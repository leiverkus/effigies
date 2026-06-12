#!/usr/bin/env python3
"""Unit tests for helpers/georef_bridge.py — no ODM/QGIS dependency.

Run:  python3 tests/test_georef.py
Exits non-zero on failure (CI-friendly).
"""
import os
import sys
import json
import tempfile
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "helpers"))
import georef_bridge as gb  # noqa: E402


def test_umeyama_recovers_known_similarity():
    np.random.seed(0)
    local = np.random.randn(8, 3) * 5
    s_true, ang = 2.5, 0.4
    R_true = np.array([[np.cos(ang), -np.sin(ang), 0],
                       [np.sin(ang),  np.cos(ang), 0],
                       [0, 0, 1]])
    t_true = np.array([100., 200., 30.])
    world = (s_true * (R_true @ local.T).T) + t_true
    s, R, t = gb.umeyama_similarity(local, world)
    rec = (s * (R @ local.T).T) + t
    assert abs(s - s_true) < 1e-6, f"scale off: {s}"
    assert np.abs(rec - world).max() < 1e-9, "residual too large"
    print("ok  umeyama recovers known similarity")


def test_quat_identity():
    assert np.allclose(gb._quat_to_rot(1, 0, 0, 0), np.eye(3))
    print("ok  quaternion identity")


def _build_synthetic_colmap(root, s_true=0.5, ang=0.3):
    """Write a consistent synthetic COLMAP text model + gcp_list.txt + OBJ.

    Two cameras: img1 is a clean PINHOLE at (0,0,-500); img2 is a SIMPLE_RADIAL
    with strong distortion (k=0.1) translated sideways, so each GCP is marked in
    two views with real parallax — exercising both the multi-view triangulation
    and the pixel undistortion. Marked/observed pixels are written in DISTORTED
    image coords, exactly as COLMAP stores them."""
    model = os.path.join(root, "work", "sparse", "0")
    os.makedirs(model, exist_ok=True)
    os.makedirs(os.path.join(root, "images"), exist_ok=True)

    world = np.array([
        [690000, 3540000, 100], [690010, 3540000, 101], [690000, 3540010, 99],
        [690010, 3540010, 100], [690005, 3540005, 102], [690002, 3540008, 98]],
        float)
    R = np.array([[np.cos(ang), -np.sin(ang), 0],
                  [np.sin(ang),  np.cos(ang), 0],
                  [0, 0, 1]])
    t = np.array([690005., 3540005., 100.])
    local = ((R.T @ (world - t).T).T) / s_true

    with open(os.path.join(model, "points3D.txt"), "w") as f:
        f.write("# 3D points\n")
        for i, p in enumerate(local, 1):
            f.write(f"{i} {p[0]} {p[1]} {p[2]} 0 0 0 0.5\n")

    cam_f, cx, cy = 1000., 320., 240.
    f2, k2 = 900., 0.1
    with open(os.path.join(model, "cameras.txt"), "w") as f:
        f.write("# cam\n")
        f.write(f"1 PINHOLE 640 480 {cam_f} {cam_f} {cx} {cy}\n")
        f.write(f"2 SIMPLE_RADIAL 640 480 {f2} {cx} {cy} {k2}\n")

    cam1_t = np.array([0, 0, 500.])
    cam2_t = np.array([150., 0, 450.])

    def proj1(p):
        Xc = p + cam1_t
        return cam_f * Xc[0] / Xc[2] + cx, cam_f * Xc[1] / Xc[2] + cy

    def proj2(p):  # SIMPLE_RADIAL: distortion applied to normalized coords
        Xc = p + cam2_t
        x, y = Xc[0] / Xc[2], Xc[1] / Xc[2]
        fct = 1 + k2 * (x * x + y * y)
        return f2 * x * fct + cx, f2 * y * fct + cy

    with open(os.path.join(model, "images.txt"), "w") as f:
        f.write("# images\n")
        f.write(f"1 1 0 0 0 {cam1_t[0]} {cam1_t[1]} {cam1_t[2]} 1 img1.jpg\n")
        f.write(" ".join(f"{u} {v} {i}" for i, p in enumerate(local, 1)
                         for u, v in [proj1(p)]) + "\n")
        f.write(f"2 1 0 0 0 {cam2_t[0]} {cam2_t[1]} {cam2_t[2]} 2 img2.jpg\n")
        f.write(" ".join(f"{u} {v} {i}" for i, p in enumerate(local, 1)
                         for u, v in [proj2(p)]) + "\n")

    with open(os.path.join(root, "gcp_list.txt"), "w") as f:
        f.write("EPSG:32637\n")
        for i, p in enumerate(local, 1):
            w = world[i - 1]
            u1, v1 = proj1(p)
            u2, v2 = proj2(p)
            f.write(f"{w[0]} {w[1]} {w[2]} {u1:.6f} {v1:.6f} img1.jpg\n")
            f.write(f"{w[0]} {w[1]} {w[2]} {u2:.6f} {v2:.6f} img2.jpg\n")

    with open(os.path.join(root, "work", "scene_dense_mesh_refine.obj"), "w") as f:
        f.write("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
    return s_true


def test_gcp_path_recovers_scale():
    with tempfile.TemporaryDirectory() as root:
        s_true = _build_synthetic_colmap(root)
        model = gb._find_colmap_model(os.path.join(root, "work"))
        _, entries = gb.parse_gcp_list(os.path.join(root, "gcp_list.txt"))
        local, world, _ = gb.gcp_correspondences(model, entries)
        s, R, t = gb.umeyama_similarity(local, world)
        assert abs(s - s_true) < 1e-3, f"gcp scale off: {s} vs {s_true}"
        print(f"ok  gcp path recovers scale ({s:.4f} ~ {s_true})")


def test_undistort_pixel_roundtrip():
    """distort -> pixel -> _undistort_pixel must recover the normalized coords
    for every camera model the engine advertises (plus the pinhole bases)."""
    cases = [
        ("PINHOLE", [900., 920., 320., 240.]),
        ("SIMPLE_RADIAL", [900., 320., 240., 0.1]),
        ("RADIAL", [900., 320., 240., 0.1, -0.03]),
        ("OPENCV", [900., 920., 320., 240., 0.1, -0.05, 0.001, -0.002]),
        ("FULL_OPENCV", [900., 920., 320., 240., 0.1, -0.05, 0.001, -0.002,
                         0.01, 0.02, -0.01, 0.005]),
        ("OPENCV_FISHEYE", [900., 920., 320., 240., 0.05, -0.01, 0.002, -0.001]),
    ]
    for model, params in cases:
        fx, fy, cx, cy, dist = gb._split_intrinsics(model, params)
        for (x, y) in [(0.05, -0.1), (0.3, 0.2), (-0.25, 0.15), (0.0, 0.0)]:
            xd, yd = gb._distort_normalized(model, dist, x, y)
            xr, yr = gb._undistort_pixel(model, params, fx * xd + cx, fy * yd + cy)
            assert abs(xr - x) < 1e-9 and abs(yr - y) < 1e-9, \
                f"{model}: ({x},{y}) -> ({xr},{yr})"
    print("ok  pixel undistortion roundtrip (6 camera models)")


def test_gcp_triangulation_is_exact():
    """With every GCP marked in two views the marked pixels must be triangulated
    (no nearest-point fallback) and — pixels being exact, distortion included —
    recover the similarity to numerical precision, far beyond the heuristic."""
    with tempfile.TemporaryDirectory() as root:
        s_true = _build_synthetic_colmap(root)
        model = gb._find_colmap_model(os.path.join(root, "work"))
        _, entries = gb.parse_gcp_list(os.path.join(root, "gcp_list.txt"))
        local, world, info = gb.gcp_correspondences(model, entries)
        assert info["triangulated"] == len(local) == 6, info
        assert info["nearest_point"] == 0, info
        s, R, t = gb.umeyama_similarity(local, world)
        assert abs(s - s_true) < 1e-7, f"triangulated scale off: {s} vs {s_true}"
        res = gb.solve_residuals(s, R, t, local, world)
        assert res["rms_3d"] < 1e-5, res
        assert res["count"] == 6
    print(f"ok  gcp multi-view triangulation exact (rms {res['rms_3d']:.2e} m)")


def test_gcp_single_view_falls_back():
    """A GCP marked in only one image has no parallax: triangulation must refuse
    and the nearest-sparse-point heuristic must take over per GCP."""
    with tempfile.TemporaryDirectory() as root:
        s_true = _build_synthetic_colmap(root)
        model = gb._find_colmap_model(os.path.join(root, "work"))
        _, entries = gb.parse_gcp_list(os.path.join(root, "gcp_list.txt"))
        only_img1 = [e for e in entries if e["image"] == "img1.jpg"]
        local, world, info = gb.gcp_correspondences(model, only_img1)
        assert info["triangulated"] == 0 and info["nearest_point"] == 6, info
        s, _, _ = gb.umeyama_similarity(local, world)
        assert abs(s - s_true) < 1e-3, f"fallback scale off: {s} vs {s_true}"
    print("ok  single-view gcp falls back to nearest-point heuristic")


def test_residuals_written_to_transform():
    """main() in gcp mode must report the solve quality in georef_transform.json:
    residual RMS values plus the per-method GCP localization counts."""
    with tempfile.TemporaryDirectory() as root:
        _build_synthetic_colmap(root)
        work = os.path.join(root, "work")
        argv = sys.argv
        sys.argv = ["georef", "--work", work, "--images",
                    os.path.join(root, "images"), "--sparse-engine", "colmap",
                    "--georeference", "gcp", "--crs", "auto",
                    "--gcp", os.path.join(root, "gcp_list.txt")]
        try:
            gb.main()
        finally:
            sys.argv = argv
        tr = json.load(open(os.path.join(work, "georef_transform.json")))
        res = tr["residuals"]
        assert res["count"] == 6 and res["rms_3d"] < 1e-5, res
        assert {"rms_horizontal", "rms_vertical", "max_3d"} <= set(res)
        assert res["gcp_localization"] == {"triangulated": 6, "nearest_point": 0}
    print("ok  residuals + localization counts written to georef_transform.json")


def test_none_mode_keeps_local():
    with tempfile.TemporaryDirectory() as root:
        _build_synthetic_colmap(root)
        work = os.path.join(root, "work")
        # call main() via argv
        argv = sys.argv
        sys.argv = ["georef", "--work", work, "--images",
                    os.path.join(root, "images"),
                    "--sparse-engine", "colmap", "--georeference", "none",
                    "--crs", "auto"]
        try:
            gb.main()
        finally:
            sys.argv = argv
        tr = json.load(open(os.path.join(work, "georef_transform.json")))
        assert tr["source"] == "none" and tr["crs"] == "local"
        print("ok  none mode keeps local frame")


def test_xy_offset_and_coords_txt():
    """The float-precision offset is 2D (ODM convention): x/y only, Z stays
    absolute so the model aligns vertically with the full-coordinate cloud in
    WebODM's viewer (it translates by x/y only). coords.txt carries the offset
    on line 2 for the viewer."""
    world = np.array([[415700.0, 5958500.0, 210.0],
                      [415720.0, 5958540.0, 214.0]])
    off = gb._xy_offset(world)
    assert off[0] == 415710.0 and off[1] == 5958520.0, off
    assert off[2] == 0.0, f"offset must be 2D (z=0), got {off}"
    with tempfile.TemporaryDirectory() as d:
        gb.write_coords_txt(d, off, "EPSG:32632")
        lines = open(os.path.join(d, "coords.txt")).read().splitlines()
    assert lines[0] == "WGS84 UTM 32N", lines
    x, y = map(float, lines[1].split())
    assert (x, y) == (415710.0, 5958520.0), lines
    print("ok  2D offset (z=0) + ODM-compatible coords.txt")


def test_camera_centers_survive_empty_points2d():
    """Regression: an image registered with NO observed 3D points has an EMPTY
    points2D line in images.txt. read_colmap_camera_centers must not drop blank
    lines — doing so desynced the two-line stride and silently lost cameras, which
    pushed real drone / GLOMAP runs below the >=3 EXIF-GPS fixes needed and
    produced a spurious local-only georef instead of using the GPS."""
    with tempfile.TemporaryDirectory() as d:
        m = os.path.join(d, "m")
        os.makedirs(m)
        with open(os.path.join(m, "images.txt"), "w") as f:
            f.write("# Image list\n")
            f.write("1 1 0 0 0 0 0 0 1 a.jpg\n");  f.write("10 20 -1\n")
            f.write("2 1 0 0 0 -1 0 0 1 b.jpg\n"); f.write("\n")          # no observations
            f.write("3 1 0 0 0 0 -1 0 1 c.jpg\n"); f.write("11 21 -1\n")
            f.write("4 1 0 0 0 0 0 -1 1 d.jpg\n"); f.write("12 22 -1\n")
        centers = gb.read_colmap_camera_centers(m)
    assert set(centers) == {"a.jpg", "b.jpg", "c.jpg", "d.jpg"}, centers
    assert np.allclose(centers["b.jpg"], [1, 0, 0]), centers["b.jpg"]   # R=I -> C=-t
    print("ok  camera centers survive an empty points2D line (no stride desync)")


if __name__ == "__main__":
    test_umeyama_recovers_known_similarity()
    test_quat_identity()
    test_gcp_path_recovers_scale()
    test_undistort_pixel_roundtrip()
    test_gcp_triangulation_is_exact()
    test_gcp_single_view_falls_back()
    test_residuals_written_to_transform()
    test_none_mode_keeps_local()
    test_xy_offset_and_coords_txt()
    test_camera_centers_survive_empty_points2d()
    print("\nall georef tests passed")
