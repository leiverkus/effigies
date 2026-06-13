#!/usr/bin/env python3
"""Unit tests for helpers/gcp_bundle_adjust.py — GCP-constrained bundle adjustment.

The pure parts (check-point parsing, control/check split, and the georef bridge
honoring an upstream colmap-gcp-ba transform) always run. The end-to-end bundle
adjustment needs pycolmap (built into the Effigies image) and is skipped when it is
unavailable — exactly like the scipy/pdal-gated tests.

Run:  python3 tests/test_gcp_ba.py
"""
import os
import sys
import json
import tempfile
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "helpers"))
import georef_bridge as gb          # noqa: E402
import gcp_bundle_adjust as ba      # noqa: E402


# ---------------------------------------------------------------------------
# Pure tests (no pycolmap)
# ---------------------------------------------------------------------------
def test_parse_gcp_list_check_flag():
    """A trailing 'check' token (ODM [extra] field) marks a held-out check point;
    every other line is a control point. Backward compatible with plain lines."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "gcp_list.txt")
        with open(p, "w") as f:
            f.write("EPSG:32637\n")
            f.write("690000 3540000 100 320 240 img1.jpg\n")          # control (no extra)
            f.write("690010 3540000 101 400 240 img1.jpg gcp2\n")     # control (label, not 'check')
            f.write("690000 3540010 99 320 300 img2.jpg check\n")     # check
            f.write("690010 3540010 100 410 305 img2.jpg label CHECK\n")  # check (case-insensitive)
        crs, entries = gb.parse_gcp_list(p)
    assert crs == "EPSG:32637", crs
    assert [e["check"] for e in entries] == [False, False, True, True], entries
    print("ok  parse_gcp_list flags trailing 'check' tokens (case-insensitive)")


def test_split_control_check():
    entries = [
        {"world": np.array([0, 0, 0]), "check": False},
        {"world": np.array([1, 0, 0]), "check": True},
        {"world": np.array([2, 0, 0]), "check": False},
    ]
    control, check = ba.split_control_check(entries)
    assert len(control) == 2 and len(check) == 1, (control, check)
    assert check[0]["world"][0] == 1
    print("ok  split_control_check separates control from held-out check points")


def test_arbitrate_decision():
    """'auto' keeps the BA only if it beats the post-hoc similarity by BOTH the
    relative margin and the absolute floor; no check points -> fallback."""
    m = 0.10            # default margin (10 %)
    # clear win: 0.10 -> 0.05 (gain 0.05 > 0.01 and > 1 mm)
    assert ba._arbitrate_decision(0.10, 0.05, 2, margin=m) == "ba"
    # marginal gain below the relative margin -> keep post-hoc
    assert ba._arbitrate_decision(0.10, 0.095, 2, margin=m) == "umeyama"
    # sub-millimetre absolute gain on an already-tiny error -> keep post-hoc
    assert ba._arbitrate_decision(0.0011, 0.0001, 2, margin=m) == "umeyama"
    # consistent block: both ~0 -> deterministically post-hoc (no spurious flip)
    assert ba._arbitrate_decision(1e-7, 6e-8, 2, margin=m) == "umeyama"
    # no check points / no metric -> fallback
    assert ba._arbitrate_decision(0.10, 0.01, 0, margin=m) == "fallback"
    assert ba._arbitrate_decision(None, 0.01, 2, margin=m) == "fallback"
    print("ok  _arbitrate_decision: relative margin + absolute floor + fallback")


def test_rmat_to_quat_roundtrip():
    """_rmat_to_quat_xyzw must produce a unit quaternion that reconstructs R
    (via the same q->R convention georef_bridge uses), for a few rotations."""
    for ang, axis in [(0.0, "z"), (0.7, "z"), (-1.2, "z"), (0.5, "x")]:
        c, s = np.cos(ang), np.sin(ang)
        if axis == "z":
            R = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], float)
        else:  # x
            R = np.array([[1, 0, 0], [0, c, -s], [0, s, c]], float)
        x, y, z, w = ba._rmat_to_quat_xyzw(R)
        assert abs(np.linalg.norm([x, y, z, w]) - 1.0) < 1e-12
        R2 = gb._quat_to_rot(w, x, y, z)            # gb uses (qw,qx,qy,qz)
        assert np.abs(R2 - R).max() < 1e-9, (ang, axis, R2 - R)
    print("ok  _rmat_to_quat_xyzw roundtrips to the rotation matrix")


def test_georef_honors_existing_gcp_ba():
    """When sparse_colmap.sh has already written a colmap-gcp-ba transform, the
    georef bridge must honor it: apply the identity-with-offset to the OBJ (leaving
    vertices unchanged — the mesh is already in the offset-world frame), write
    coords.txt, and NOT overwrite the transform with a post-hoc Umeyama."""
    with tempfile.TemporaryDirectory() as root:
        work = os.path.join(root, "work")
        model = os.path.join(work, "sparse", "0")
        os.makedirs(model)
        # a minimal model so _find_colmap_model resolves (content is irrelevant —
        # the honor branch returns before any solve)
        open(os.path.join(model, "images.txt"), "w").write("# images\n")
        obj = os.path.join(work, "scene_dense_mesh_refine.obj")
        open(obj, "w").write("v 1.5 2.5 3.5\nv 4.0 5.0 6.0\nf 1 2 3\n")

        offset = [690005.0, 3540005.0, 0.0]
        tr = {"source": "colmap-gcp-ba", "s": 1.0, "R": np.eye(3).tolist(),
              "t": offset, "offset": offset, "crs": "EPSG:32637",
              "residuals": {"n_control": 4, "n_check": 2}}
        tr_path = os.path.join(work, "georef_transform.json")
        json.dump(tr, open(tr_path, "w"))

        argv = sys.argv
        sys.argv = ["georef", "--work", work, "--images", os.path.join(root, "images"),
                    "--sparse-engine", "colmap", "--georeference", "gcp",
                    "--crs", "auto", "--gcp", ""]
        try:
            gb.main()
        finally:
            sys.argv = argv

        # OBJ unchanged (identity-with-offset)
        verts = [list(map(float, l.split()[1:4]))
                 for l in open(obj) if l.startswith("v ")]
        assert verts == [[1.5, 2.5, 3.5], [4.0, 5.0, 6.0]], verts
        # transform NOT overwritten
        again = json.load(open(tr_path))
        assert again["source"] == "colmap-gcp-ba", again
        # coords.txt written with the offset on line 2
        lines = open(os.path.join(work, "coords.txt")).read().splitlines()
        assert tuple(map(float, lines[1].split())) == (690005.0, 3540005.0), lines
    print("ok  georef bridge honors an upstream colmap-gcp-ba transform (no re-solve)")


# ---------------------------------------------------------------------------
# End-to-end (pycolmap-gated)
# ---------------------------------------------------------------------------
def _have_pycolmap():
    try:
        import pycolmap  # noqa: F401
        return True
    except ImportError:
        return False


def _build_synthetic_colmap_model(work, s_true=0.5, ang=0.3):
    """Write a CONSISTENT synthetic COLMAP TEXT model (3 PINHOLE views with real
    parallax) + a gcp_list.txt with control and held-out check points.

    Returns (s_true, R_true, t_true, offset_expected). The scene is internally
    consistent (no drift), so the GCP-BA confirms the alignment and leaves both
    control and check residuals ~0 — proving the plumbing, the offset convention
    and the held-out CP-RMSE reporting (real drift-removal is the deferred
    real-data validation)."""
    model = os.path.join(work, "sparse", "0")
    os.makedirs(model, exist_ok=True)

    R = np.array([[np.cos(ang), -np.sin(ang), 0],
                  [np.sin(ang),  np.cos(ang), 0],
                  [0, 0, 1]], float)
    t = np.array([690005., 3540005., 100.])

    # tie points + GCP points, all in WORLD then mapped to the LOCAL frame
    rng = np.random.default_rng(0)
    tie_world = np.column_stack([690000 + rng.uniform(0, 10, 14),
                                 3540000 + rng.uniform(0, 10, 14),
                                 100 + rng.uniform(-2, 2, 14)])
    gcp_world = np.array([
        [690000, 3540000, 100], [690010, 3540000, 101], [690000, 3540010, 99],
        [690010, 3540010, 100], [690005, 3540005, 102], [690002, 3540008, 98]],
        float)

    def to_local(W):
        return ((R.T @ (W - t).T).T) / s_true

    tie_local = to_local(tie_world)
    gcp_local = to_local(gcp_world)

    cam_f, cx, cy = 1000., 320., 240.
    cam_t = [np.array([0., 0., 500.]),
             np.array([150., 0., 470.]),
             np.array([-120., 80., 520.])]   # 3 well-separated centers (R=I poses)

    def proj(p, ti):                          # PINHOLE, world-to-cam = I*p + cam_t
        Xc = p + cam_t[ti]
        return cam_f * Xc[0] / Xc[2] + cx, cam_f * Xc[1] / Xc[2] + cy

    with open(os.path.join(model, "cameras.txt"), "w") as f:
        f.write("# cam\n")
        f.write(f"1 PINHOLE 640 480 {cam_f} {cam_f} {cx} {cy}\n")

    # images.txt: pose line + observation line (X Y POINT3D_ID) for every tie point
    with open(os.path.join(model, "images.txt"), "w") as f:
        f.write("# images\n")
        for ti in range(3):
            c = cam_t[ti]
            f.write(f"{ti+1} 1 0 0 0 {c[0]} {c[1]} {c[2]} 1 img{ti+1}.jpg\n")
            obs = []
            for k, p in enumerate(tie_local, 1):
                u, v = proj(p, ti)
                obs.append(f"{u} {v} {k}")
            f.write(" ".join(obs) + "\n")

    # points3D.txt: each tie point tracked in all 3 images at its row index
    with open(os.path.join(model, "points3D.txt"), "w") as f:
        f.write("# 3D points\n")
        for k, p in enumerate(tie_local, 1):
            idx = k - 1
            track = f"1 {idx} 2 {idx} 3 {idx}"
            f.write(f"{k} {p[0]} {p[1]} {p[2]} 128 128 128 0.5 {track}\n")

    # gcp_list.txt: first 4 control, last 2 held-out check
    with open(os.path.join(work, "gcp_list.txt"), "w") as f:
        f.write("EPSG:32637\n")
        for gi, (W, p) in enumerate(zip(gcp_world, gcp_local)):
            tag = " check" if gi >= 4 else ""
            for ti in range(3):
                u, v = proj(p, ti)
                f.write(f"{W[0]} {W[1]} {W[2]} {u:.6f} {v:.6f} img{ti+1}.jpg{tag}\n")

    offset = gb._xy_offset(gcp_world[:4])     # offset is from CONTROL world coords
    return s_true, R, t, offset


def test_gcp_ba_end_to_end():
    if not _have_pycolmap():
        print("skip gcp-ba-e2e (needs pycolmap — present in the Effigies image)")
        return
    with tempfile.TemporaryDirectory() as work:
        _, _, _, offset = _build_synthetic_colmap_model(work)
        gcp = os.path.join(work, "gcp_list.txt")

        tr = ba.run_gcp_bundle_adjust(work, gcp, crs="auto")

        # offset-with-identity convention (so every downstream consumer just works)
        assert tr["source"] == "colmap-gcp-ba", tr
        assert tr["s"] == 1.0 and tr["R"] == np.eye(3).tolist(), tr
        assert np.allclose(tr["t"], offset) and np.allclose(tr["offset"], offset), \
            (tr["t"], offset)
        assert tr["crs"] == "EPSG:32637", tr["crs"]

        res = tr["residuals"]
        assert res["n_control"] == 4 and res["n_check"] == 2, res
        # consistent scene: control GCPs land on their surveyed coords after BA
        assert res["control_rms_3d"] < 1e-2, res["control_rms_3d"]
        # the held-out check points are reported as an INDEPENDENT CP-RMSE
        assert res["check"] is not None and res["check_rms_3d"] < 1e-2, res

        # sparse/0 rewritten in the offset-world frame (text model present)
        assert os.path.exists(os.path.join(work, "sparse", "0", "images.txt"))
        # a downstream cloud would land in full UTM: s*R*v + t == v + offset
        v = np.array([1.0, 2.0, 3.0])
        full = float(tr["s"]) * (np.asarray(tr["R"]) @ v) + np.asarray(tr["t"])
        assert np.allclose(full, v + np.asarray(offset)), full
    print("ok  gcp-ba end-to-end (BA converges, offset convention, held-out CP-RMSE)")


def test_arbitration_consistent_keeps_umeyama():
    """'auto' on a consistent block: BA and post-hoc both land the check points at
    ~0, so neither clears the margin -> the safe post-hoc path is kept, sparse/0 is
    RESTORED to the free model, no colmap-gcp-ba transform is written, and the
    decision (both RMSEs) is recorded in the sidecar for audit."""
    if not _have_pycolmap():
        print("skip arbitration-consistent (needs pycolmap)")
        return
    with tempfile.TemporaryDirectory() as work:
        _build_synthetic_colmap_model(work)
        gcp = os.path.join(work, "gcp_list.txt")
        free_images = open(os.path.join(work, "sparse", "0", "images.txt")).read()

        rec = ba.run_arbitrated(work, gcp, crs="auto")

        assert rec["winner"] == "umeyama", rec
        assert rec["n_check"] == 2 and rec["cp_ba"] is not None, rec
        # no colmap-gcp-ba transform committed (the bridge solves the post-hoc later)
        assert not os.path.exists(os.path.join(work, "georef_transform.json"))
        # sparse/0 restored to the free model (BA rewrite rolled back, backup gone)
        assert open(os.path.join(work, "sparse", "0", "images.txt")).read() == free_images
        assert not os.path.exists(os.path.join(work, "sparse", "0.free_backup"))
        # sidecar records both RMSEs for audit
        side = json.load(open(os.path.join(work, "gcp_ba_arbitration.json")))
        assert side["winner"] == "umeyama" and "cp_umeyama" in side and "cp_ba" in side
    print("ok  auto arbitration keeps post-hoc on a consistent block (sparse restored)")


def test_arbitration_no_check_falls_back():
    """'auto' with no check points has no honest metric -> fall back to post-hoc,
    leave sparse/0 untouched, write no transform, record the reason."""
    if not _have_pycolmap():
        print("skip arbitration-no-check (needs pycolmap)")
        return
    with tempfile.TemporaryDirectory() as work:
        _build_synthetic_colmap_model(work)
        gcp = os.path.join(work, "gcp_list.txt")
        # strip the trailing 'check' tokens -> every GCP becomes control
        stripped = [l.rsplit(" check", 1)[0].rstrip() + "\n" if l.rstrip().lower().endswith("check")
                    else l for l in open(gcp)]
        open(gcp, "w").writelines(stripped)
        _, entries = gb.parse_gcp_list(gcp)
        assert not any(e["check"] for e in entries)             # sanity: no check left

        rec = ba.run_arbitrated(work, gcp, crs="auto")
        assert rec["winner"] == "umeyama" and rec["n_check"] == 0, rec
        assert "check" in rec["reason"].lower(), rec
        assert not os.path.exists(os.path.join(work, "georef_transform.json"))
        side = json.load(open(os.path.join(work, "gcp_ba_arbitration.json")))
        assert side["n_check"] == 0
    print("ok  auto arbitration falls back to post-hoc when no check points marked")


if __name__ == "__main__":
    test_parse_gcp_list_check_flag()
    test_split_control_check()
    test_arbitrate_decision()
    test_rmat_to_quat_roundtrip()
    test_georef_honors_existing_gcp_ba()
    test_gcp_ba_end_to_end()
    test_arbitration_consistent_keeps_umeyama()
    test_arbitration_no_check_falls_back()
    print("\nall gcp-ba tests passed")
