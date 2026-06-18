#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Patrick Leiverkus
# SPDX-License-Identifier: AGPL-3.0-or-later
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


def test_m3c2_registration_error_raises_lod():
    """The co-registration residual, passed as ``registration_error``, must lift
    the per-point LoD (Lague 2013) — so a cm-level alignment error is folded into
    the significance test instead of being treated as zero. Same scene, LoD with a
    5 cm registration error must exceed the roughness-only LoD."""
    if not cd.have_py4dgeo():
        print("skip m3c2 reg-error (needs py4dgeo — present in the Effigies image)")
        return
    rng = np.random.default_rng(1)
    n = 4000
    xy = rng.uniform(-5, 5, size=(n, 2))
    z = rng.normal(0, 0.005, n)
    A = np.ascontiguousarray(np.column_stack([xy, z]), dtype=np.float64)
    B = A.copy()
    B[:, 2] += 0.04 + rng.normal(0, 0.005, n)          # small +4 cm change
    B = np.ascontiguousarray(B)
    core = np.ascontiguousarray(B[::4])
    _, lod0 = cd.run_m3c2(A, B, core, 0.5, [0.5, 1.0, 2.0], threads=1,
                          registration_error=0.0)
    _, lodR = cd.run_m3c2(A, B, core, 0.5, [0.5, 1.0, 2.0], threads=1,
                          registration_error=0.05)     # 5 cm alignment uncertainty
    m0, mR = float(np.nanmedian(lod0)), float(np.nanmedian(lodR))
    assert mR > m0 + 0.02, (m0, mR)
    print(f"ok  M3C2 registration_error raises the LoD (median {m0:.3f} -> {mR:.3f} m)")


def test_min_lod_from_dod():
    """Wheaton-2010 minLoD: pure noise -> ~z·σ; the registration residual is a floor;
    no overlap -> 0."""
    rng = np.random.default_rng(3)
    ref = np.zeros((50, 50), dtype=np.float64)
    b = ref + rng.normal(0, 0.02, ref.shape)           # 2 cm noise, no real change
    lod = cd.min_lod_from_dod(ref, b, reg_error=0.0, nodata=cd.NODATA)
    assert 0.025 < lod < 0.055, lod                    # ~1.96·0.02 ≈ 0.039
    lod_reg = cd.min_lod_from_dod(ref, b, reg_error=0.10, nodata=cd.NODATA)
    assert lod_reg >= 1.96 * 0.10 - 1e-9, lod_reg      # floored by z·reg_error
    assert cd.min_lod_from_dod(np.full((3, 3), cd.NODATA),
                               np.zeros((3, 3)), nodata=cd.NODATA) == 0.0
    print(f"ok  min_lod_from_dod (noise≈{lod:.3f} m, reg-floored≈{lod_reg:.3f} m)")


def test_dod_threshold_masks_noise():
    """A minLoD threshold keeps sub-LoD noise out of the fill/cut volumes and the
    changed area (Wheaton 2010); real change above it is counted; a raw net is kept."""
    rng = np.random.default_rng(4)
    ref = np.zeros((40, 40), dtype=np.float64)
    b = ref + rng.normal(0, 0.01, ref.shape)           # 1 cm noise everywhere
    b[10:13, 10:13] += 0.5                              # real +0.5 m over 9 cells
    cell_area = 1.0
    _, s = cd.dod_stats(ref, b, cell_area, nodata=cd.NODATA, threshold=0.04)
    assert abs(s["volume_fill_m3"] - 9 * 0.5) < 0.1, s   # ~4.5 m³, noise excluded
    assert s["volume_cut_m3"] < 0.05, s                  # no cut above LoD
    assert abs(s["changed_area_m2"] - 9.0) < 2.0, s      # ~9 cells changed
    assert "net_volume_raw_m3" in s, s                   # raw cross-check kept
    _, s0 = cd.dod_stats(ref, b, cell_area, nodata=cd.NODATA, threshold=0.0)
    assert s0["changed_area_m2"] > s["changed_area_m2"], (s0, s)  # 0-threshold books noise
    print("ok  DoD minLoD threshold masks sub-LoD noise from volumes + changed area")


def test_stable_mask():
    """Stable points (small C2C) are kept; a high tail of changed points is dropped."""
    rng = np.random.default_rng(5)
    stable = np.abs(rng.normal(0, 0.01, 9000))         # ~1 cm C2C (folded)
    changed = rng.uniform(0.3, 0.6, 1000)              # 30-60 cm change tail
    d = np.concatenate([stable, changed])
    m = cd.stable_mask(d)
    assert m[:9000].mean() > 0.95, m[:9000].mean()     # nearly all stable kept
    assert m[9000:].mean() < 0.02, m[9000:].mean()     # nearly all change dropped
    assert cd.stable_mask(np.array([])).size == 0
    print(f"ok  stable_mask keeps {100*m[:9000].mean():.0f}% stable, drops "
          f"{100*(1-m[9000:].mean()):.0f}% changed")


def test_stable_area_icp():
    """Two-pass stable-area ICP recovers a known shift while a localised change block
    is masked out: masked=True, stable_fraction<1, clean cm-level reg error."""
    import shutil
    if not shutil.which("pdal"):
        print("skip stable-area-icp (needs pdal — present in the Effigies image)")
        return
    rng = np.random.default_rng(6)
    n = 20000
    xy = rng.uniform(-5, 5, size=(n, 2))
    z = rng.normal(0, 0.005, n)
    ref = np.ascontiguousarray(np.column_stack([xy, z]), dtype=np.float64)
    b = ref.copy()
    b[:, 2] += 0.03                                    # known +3 cm shift
    block = (np.abs(b[:, 0]) < 1.5) & (np.abs(b[:, 1]) < 1.5)   # ~9% of area
    b[block, 2] += 0.40                                # +0.4 m localised change
    b = np.ascontiguousarray(b)
    with tempfile.TemporaryDirectory() as d:
        ref_c = os.path.join(d, "ref.laz")
        b_c = os.path.join(d, "b.laz")
        cd._write_cloud(ref, ref_c)
        cd._write_cloud(b, b_c)
        T, fit, conv, info = cd.stable_area_icp(ref_c, b_c, ref, b, d)
    assert info.get("masked") is True, info
    assert 0.5 < info["stable_fraction"] < 0.99, info
    assert info["registration_error"] < 0.05, info     # clean, not inflated by 0.4 m
    print(f"ok  stable_area_icp: masked, stable {info['stable_fraction']:.2f}, "
          f"reg-error {info['registration_error']:.4f} m")


def test_transform_obj():
    """transform_obj rigid-transforms the v-lines in the offset frame
    (v' = R·v + (R·offset + t − offset)) and passes vt / f / vertex-colour through."""
    R = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]], dtype=float)   # +90° about Z
    t = np.array([0.10, -0.20, 0.05])
    offset = np.array([300000.0, 5000000.0, 100.0])
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "m.obj")
        out = os.path.join(d, "m2.obj")
        with open(src, "w") as f:
            f.write("mtllib m.mtl\n")
            f.write("v 1.0 2.0 3.0\n")
            f.write("vt 0.5 0.5\n")
            f.write("v 4.0 5.0 6.0 0.1 0.2 0.3\n")    # vertex with colour
            f.write("f 1/1 2/1 1/1\n")
        cd.transform_obj(src, out, R, t, offset)
        lines = open(out).read().splitlines()
    assert lines[0] == "mtllib m.mtl" and lines[2] == "vt 0.5 0.5", lines
    assert lines[4].startswith("f "), lines
    t_eff = R @ offset + t - offset
    for lineno, v in [(1, [1, 2, 3]), (3, [4, 5, 6])]:
        exp = R @ np.array(v, float) + t_eff
        got = np.array([float(x) for x in lines[lineno].split()[1:4]])
        assert np.allclose(got, exp, atol=1e-5), (lineno, got, exp)
    assert lines[3].split()[4:] == ["0.1", "0.2", "0.3"], lines[3]
    print("ok  transform_obj rigid-transforms v-lines (offset-aware), keeps vt/f/colour")


def test_dem_to_xyz():
    """is_dem distinguishes raster vs cloud; a DEM GeoTIFF loads as cell-centre XYZ
    points with nodata skipped (so a DEM can stand in as the reference)."""
    assert cd.is_dem("ref.tif") and cd.is_dem("a.TIFF") and not cd.is_dem("ref.laz")
    try:
        from osgeo import gdal
    except Exception:
        print("skip dem_to_xyz raster (needs GDAL — present in the Effigies image)")
        return
    with tempfile.TemporaryDirectory() as d:
        tif = os.path.join(d, "dem.tif")
        ds = gdal.GetDriverByName("GTiff").Create(tif, 3, 2, 1, gdal.GDT_Float32)
        ds.SetGeoTransform((100.0, 1.0, 0.0, 200.0, 0.0, -1.0))   # 1 m cells
        band = ds.GetRasterBand(1)
        band.SetNoDataValue(cd.NODATA)
        band.WriteArray(np.array([[10.0, 11.0, cd.NODATA],
                                  [12.0, 13.0, 14.0]], dtype=np.float32))
        ds.FlushCache(); ds = None
        xyz = cd.dem_to_xyz(tif)
    assert xyz.shape == (5, 3), xyz                     # 6 cells − 1 nodata
    rows = {(round(x, 1), round(y, 1)): round(z, 1) for x, y, z in xyz}
    assert rows[(100.5, 199.5)] == 10.0, rows           # row0,col0 cell centre
    assert rows[(102.5, 198.5)] == 14.0, rows           # row1,col2
    assert (102.5, 199.5) not in rows, rows             # the nodata cell excluded
    print(f"ok  dem_to_xyz: {xyz.shape[0]} cell-centre points, nodata skipped")


if __name__ == "__main__":
    test_transform_obj()
    test_dem_to_xyz()
    test_parse_icp_transform()
    test_parse_icp_transform_missing()
    test_decenter_transform()
    test_dod_volume_fill()
    test_dod_cut_sign_and_nodata()
    test_dod_no_overlap()
    test_min_lod_from_dod()
    test_dod_threshold_masks_noise()
    test_stable_mask()
    test_stable_area_icp()
    test_gate()
    test_m3c2_known_shift()
    test_m3c2_registration_error_raises_lod()
    print("\nall change-detection tests passed")
