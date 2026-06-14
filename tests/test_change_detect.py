#!/usr/bin/env python3
"""Unit tests for helpers/change_detect.py — multi-epoch change detection.

The pure parts (ICP-metadata transform parse, the DoD subtraction + cut/fill
volume math, the gate logic) always run. The M3C2 leg needs py4dgeo (built into
the Effigies image; no manylinux aarch64 wheel) and is skipped when unavailable.

Run:  python3 tests/test_change_detect.py
"""
import os
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "helpers"))
import change_detect as cd  # noqa: E402


# --- ICP metadata transform parse ------------------------------------------
def test_parse_icp_transform():
    # a pure translation (+5 in X, +2 in Z), nested as PDAL emits it under stages
    meta = {"stages": {"filters.icp": {
        "converged": True, "fitness": 0.012,
        "composed": "1 0 0 5  0 1 0 0  0 0 1 2  0 0 0 1"}}}
    T = cd.parse_icp_transform(meta)
    assert T is not None and T.shape == (4, 4), T
    assert T[0, 3] == 5.0 and T[2, 3] == 2.0 and T[1, 3] == 0.0, T
    assert np.allclose(T[:3, :3], np.eye(3)), T
    print("ok  ICP transform parse (composed 4x4, translation recovered)")


def test_parse_icp_transform_missing():
    assert cd.parse_icp_transform({"stages": {"filters.icp": {"converged": False}}}) is None
    assert cd.parse_icp_transform({}) is None
    # wrong element count must not raise — returns None
    assert cd.parse_icp_transform({"transform": "1 2 3"}) is None
    print("ok  ICP transform parse returns None when no valid 4x4 is present")


# --- de-centring the ICP transform -----------------------------------------
def test_decenter_transform():
    """T_orig applied to x must equal T_centered applied to (x - offset), + offset
    — i.e. the two describe the same rigid motion in different origins. A tiny
    rotation about a far origin (the bug this fixes) must NOT introduce a vertical
    bias once de-centred."""
    offset = np.array([300000.0, 5000000.0, 100.0])
    # a small rotation (1e-4 rad about Z) + small translation, as ICP would report
    th = 1e-4
    R = np.array([[np.cos(th), -np.sin(th), 0],
                  [np.sin(th), np.cos(th), 0], [0, 0, 1]])
    T_c = np.eye(4)
    T_c[:3, :3] = R
    T_c[:3, 3] = [0.01, -0.02, 0.005]
    T_orig = cd._decenter_transform(T_c, offset)
    pts = np.array([[300020.0, 5000020.0, 100.3], [299980.0, 4999990.0, 99.7]])
    via_orig = pts @ T_orig[:3, :3].T + T_orig[:3, 3]
    via_centred = (pts - offset) @ T_c[:3, :3].T + T_c[:3, 3] + offset
    assert np.allclose(via_orig, via_centred, atol=1e-9), (via_orig - via_centred)
    # the centred transform's own translation is the true cm-level motion (no 1e6 sweep)
    assert np.linalg.norm(T_c[:3, 3]) < 1.0, T_c[:3, 3]
    print("ok  de-centred ICP transform reproduces the centred rigid motion (no far-origin bias)")


# --- DoD subtraction + volume math -----------------------------------------
def test_dod_volume_fill():
    """A 3x3 block raised by +0.5 m on a 10x10 grid at 0.5 m/px (cell 0.25 m²)."""
    ref = np.zeros((10, 10), dtype=np.float64)
    b = ref.copy()
    b[2:5, 2:5] = 0.5                                   # +0.5 m over 9 cells
    cell_area = 0.5 * 0.5
    diff, s = cd.dod_stats(ref, b, cell_area, nodata=cd.NODATA, threshold=0.0)
    assert s["valid_cells"] == 100, s
    assert abs(s["volume_fill_m3"] - 9 * 0.5 * cell_area) < 1e-9, s
    assert s["volume_cut_m3"] == 0.0, s
    assert abs(s["net_volume_m3"] - 9 * 0.5 * cell_area) < 1e-9, s
    assert abs(s["changed_area_m2"] - 9 * cell_area) < 1e-9, s
    assert s["max_raise_m"] == 0.5 and s["max_lower_m"] == 0.0, s
    assert abs(diff[3, 3] - 0.5) < 1e-9 and diff[0, 0] == 0.0, diff[3, 3]
    print("ok  DoD volume (fill = 9 cells x 0.5 m x 0.25 m² = 1.125 m³)")


def test_dod_cut_sign_and_nodata():
    """Excavation (lowered surface) is a negative diff -> reported as cut volume;
    cells nodata in either raster are excluded from every statistic."""
    ref = np.zeros((4, 4), dtype=np.float64)
    b = ref.copy()
    b[1, 1] = -0.8                                      # dug down 0.8 m
    b[0, 0] = cd.NODATA                                 # B invalid here
    ref[3, 3] = cd.NODATA                               # ref invalid here
    cell_area = 1.0
    diff, s = cd.dod_stats(ref, b, cell_area, nodata=cd.NODATA)
    assert s["valid_cells"] == 14, s                    # 16 - 2 nodata
    assert abs(s["volume_cut_m3"] - 0.8) < 1e-9, s      # positive, |Δz|·area
    assert s["volume_fill_m3"] == 0.0, s
    assert abs(s["net_volume_m3"] + 0.8) < 1e-9, s      # net is negative
    assert s["max_lower_m"] == -0.8, s
    assert diff[0, 0] == cd.NODATA and diff[3, 3] == cd.NODATA, "nodata propagated"
    print("ok  DoD cut sign + nodata exclusion (excavation -> positive cut volume)")


def test_dod_no_overlap():
    ref = np.full((3, 3), cd.NODATA)
    b = np.zeros((3, 3))
    _, s = cd.dod_stats(ref, b, 1.0)
    assert s["valid_cells"] == 0, s
    print("ok  DoD reports cleanly when the rasters do not overlap")


# --- gate logic ------------------------------------------------------------
def test_gate():
    with tempfile.TemporaryDirectory() as d:
        ref = os.path.join(d, "ref.laz")
        cloud = os.path.join(d, "b.laz")
        # no reference path -> skip
        ok, why = cd.gate("", cloud, True)
        assert not ok and "no align-to" in why, why
        # reference path given but file absent -> skip
        ok, why = cd.gate(ref, cloud, True)
        assert not ok and "not found" in why, why
        open(ref, "w").close()
        # reference present, epoch-B cloud absent -> skip
        ok, why = cd.gate(ref, cloud, True)
        assert not ok and "epoch-B" in why, why
        open(cloud, "w").close()
        # both present but no pdal -> skip
        ok, why = cd.gate(ref, cloud, False)
        assert not ok and "pdal" in why, why
        # all conditions met -> run
        ok, why = cd.gate(ref, cloud, True)
        assert ok and why == "ok", why
    print("ok  gate skips on no-reference / missing-reference / missing-LAZ / no-pdal")


# --- M3C2 known-shift recovery (py4dgeo-gated) -----------------------------
def test_m3c2_known_shift():
    if not cd.have_py4dgeo():
        print("skip m3c2 (needs py4dgeo — present in the Effigies image)")
        return
    rng = np.random.default_rng(0)
    n = 4000
    xy = rng.uniform(-5, 5, size=(n, 2))
    z = 0.02 * xy[:, 0] + rng.normal(0, 0.005, n)      # gentle slope + noise
    A = np.ascontiguousarray(np.column_stack([xy, z]), dtype=np.float64)
    DZ = 0.20
    B = A.copy()
    B[:, 2] += DZ + rng.normal(0, 0.005, n)            # lifted by a known +0.20 m
    B = np.ascontiguousarray(B)
    core = np.ascontiguousarray(B[::4])                # decimated epoch B
    distances, lod = cd.run_m3c2(A, B, core, cyl_radius=0.5,
                                 normal_radii=[0.5, 1.0, 2.0], threads=1)
    d = distances[np.isfinite(distances)]
    assert d.size > 0, "no finite M3C2 distances"
    med = float(np.median(d))
    assert abs(med - DZ) < 0.02, f"median {med} should recover the +{DZ} shift"
    sig = np.abs(distances) > lod                      # change well beyond LoD
    assert np.nanmean(sig) > 0.9, np.nanmean(sig)
    print(f"ok  M3C2 recovers the known +{DZ} m shift (median {med:.4f}, "
          f"{100*np.nanmean(sig):.0f}% significant)")


if __name__ == "__main__":
    test_parse_icp_transform()
    test_parse_icp_transform_missing()
    test_decenter_transform()
    test_dod_volume_fill()
    test_dod_cut_sign_and_nodata()
    test_dod_no_overlap()
    test_gate()
    test_m3c2_known_shift()
    print("\nall change-detection tests passed")
