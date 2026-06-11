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
    """Write a consistent synthetic COLMAP text model + gcp_list.txt + OBJ."""
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
    with open(os.path.join(model, "cameras.txt"), "w") as f:
        f.write("# cam\n")
        f.write(f"1 PINHOLE 640 480 {cam_f} {cam_f} {cx} {cy}\n")

    cam_t = np.array([0, 0, 500.])
    with open(os.path.join(model, "images.txt"), "w") as f:
        f.write("# images\n1 1 0 0 0 0 0 500 1 img1.jpg\n")
        obs = []
        for i, p in enumerate(local, 1):
            Xc = p + cam_t
            u = cam_f * Xc[0] / Xc[2] + cx
            v = cam_f * Xc[1] / Xc[2] + cy
            obs.append(f"{u} {v} {i}")
        f.write(" ".join(obs) + "\n")

    with open(os.path.join(root, "gcp_list.txt"), "w") as f:
        f.write("EPSG:32637\n")
        for i, p in enumerate(local, 1):
            Xc = p + cam_t
            u = cam_f * Xc[0] / Xc[2] + cx
            v = cam_f * Xc[1] / Xc[2] + cy
            w = world[i - 1]
            f.write(f"{w[0]} {w[1]} {w[2]} {u:.2f} {v:.2f} img1.jpg\n")

    with open(os.path.join(root, "work", "scene_dense_mesh_refine.obj"), "w") as f:
        f.write("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
    return s_true


def test_gcp_path_recovers_scale():
    with tempfile.TemporaryDirectory() as root:
        s_true = _build_synthetic_colmap(root)
        model = gb._find_colmap_model(os.path.join(root, "work"))
        _, entries = gb.parse_gcp_list(os.path.join(root, "gcp_list.txt"))
        local, world = gb.gcp_correspondences(model, entries)
        s, R, t = gb.umeyama_similarity(local, world)
        assert abs(s - s_true) < 1e-3, f"gcp scale off: {s} vs {s_true}"
        print(f"ok  gcp path recovers scale ({s:.4f} ~ {s_true})")


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
    test_none_mode_keeps_local()
    test_camera_centers_survive_empty_points2d()
    print("\nall georef tests passed")
