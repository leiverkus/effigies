#!/usr/bin/env python3
"""Unit tests for helpers/semantic_ortho.py — the v0 semantic orthophoto.

build_semantic (per-cell majority + ASPRS->v0 mapping) is pure NumPy and always runs;
the GeoTIFF write needs GDAL and is skipped when unavailable.

Run:  python3 tests/test_semantic_ortho.py
"""
import os
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "helpers"))
import semantic_ortho as so  # noqa: E402


def test_build_semantic_majority():
    """Per-cell dominant class on a 2x2 grid: ASPRS -> v0, majority wins, noise /
    unclassified are excluded (-> nodata), out-of-grid points are dropped."""
    geo = (0.0, 1.0, 0.0, 2.0, 0.0, -1.0)          # 1 m cells, north-up, origin (0, 2)
    w, h = 2, 2
    #  (0,0): 3 ground(2) + 1 building(6)   -> ground (majority)
    #  (0,1): 2 high-veg(5)                 -> vegetation
    #  (1,0): 1 building(6)                 -> structure
    #  (1,1): 1 noise(7) + 1 off-grid       -> nodata
    x = np.array([0.5, 0.5, 0.5, 0.5, 1.5, 1.5, 0.5, 1.5, 99.0])
    y = np.array([1.5, 1.5, 1.5, 1.5, 1.5, 1.5, 0.5, 0.5, 0.5])
    c = np.array([2,   2,   2,   6,   5,   5,   6,   7,   2], dtype=np.int64)
    arr = so.build_semantic(x, y, c, geo, w, h)
    assert arr.shape == (2, 2), arr
    assert arr[0, 0] == 1, arr      # ground
    assert arr[0, 1] == 2, arr      # vegetation
    assert arr[1, 0] == 3, arr      # structure
    assert arr[1, 1] == 0, arr      # nodata (noise excluded; off-grid dropped)
    assert arr.dtype == np.uint8, arr.dtype
    print("ok  build_semantic: per-cell majority + ASPRS->v0 + nodata + out-of-grid drop")


def test_asprs_mapping():
    """The v0 map folds the ASPRS classes into ground / vegetation / structure."""
    assert so.ASPRS_TO_V0[2] == 1                                  # ground
    assert {so.ASPRS_TO_V0[k] for k in (3, 4, 5)} == {2}           # all veg -> vegetation
    assert so.ASPRS_TO_V0[6] == 3 and so.ASPRS_TO_V0[64] == 3      # building/human-made
    assert 7 not in so.ASPRS_TO_V0 and 1 not in so.ASPRS_TO_V0     # noise/unclassified
    assert set(so.V0_NAMES) == {1, 2, 3} == set(so.V0_COLOURS)
    print("ok  ASPRS->v0 mapping (ground / vegetation / structure)")


def test_write_raster_roundtrip():
    try:
        from osgeo import gdal
    except Exception:
        print("skip write_raster (needs GDAL — present in the Effigies image)")
        return
    geo = (400000.0, 0.05, 0.0, 5900000.0, 0.0, -0.05)
    arr = np.array([[1, 2], [3, 0]], dtype=np.uint8)
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "s.tif")
        so.write_raster(arr, geo, "", out)
        ds = gdal.Open(out)
        band = ds.GetRasterBand(1)
        assert np.array_equal(band.ReadAsArray(), arr), band.ReadAsArray()
        assert band.GetNoDataValue() == 0
        ct = band.GetRasterColorTable()
        assert ct is not None and tuple(ct.GetColorEntry(2)[:3]) == so.V0_COLOURS[2]
    print("ok  write_raster round-trips Byte + nodata + colour table")


if __name__ == "__main__":
    test_build_semantic_majority()
    test_asprs_mapping()
    test_write_raster_roundtrip()
    print("\nall semantic-orthophoto tests passed")
