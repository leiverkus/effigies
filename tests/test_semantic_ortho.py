#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Patrick Leiverkus
# SPDX-License-Identifier: AGPL-3.0-or-later
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


def test_triangle_classes_majority_and_ties():
    """Per-triangle class: majority of the three corners, ties to the LOWEST code.

    The tie rule matters because OBJ vertex order is arbitrary — without it the same
    geometry could get different classes on a re-run, and the semantic ortho would
    flicker between epochs for no physical reason.
    """
    import numpy as np
    vc = np.array([0, 1, 1, 2, 2, 3], np.uint8)     # vertex classes by index
    TV = np.array([
        [1, 2, 3],    # 1,1,2 -> majority 1
        [3, 4, 5],    # 2,2,3 -> majority 2
        [1, 3, 5],    # 1,2,3 -> three-way tie -> lowest = 1
        [0, 0, 0],    # all unclassified -> nodata 0
        [0, 0, 5],    # single classified corner still carries the triangle -> 3
        [1, 3, 0],    # 1,2,unclassified -> tie between 1 and 2 -> lowest = 1
    ])
    got = so.triangle_classes(vc, TV)
    assert list(got) == [1, 2, 1, 0, 3, 1], list(got)
    print("ok  triangle classes (majority, ties to lowest, nodata only if all three)")


def test_tribuf_to_class_raster():
    """tribuf -> class raster: -1 becomes nodata, everything else its triangle class."""
    import numpy as np
    tri_class = np.array([1, 3, 2], np.uint8)
    tribuf = np.array([[-1, 0, 1],
                       [2, -1, 0]], np.int32)
    arr = np.where(tribuf >= 0, tri_class[np.clip(tribuf, 0, None)], so.NODATA).astype(np.uint8)
    assert arr.tolist() == [[0, 1, 3], [2, 0, 1]], arr.tolist()
    # nodata must never be a real class code
    assert so.NODATA not in so.V0_NAMES
    print("ok  triangle buffer maps to the class raster (nodata preserved)")


if __name__ == "__main__":
    test_build_semantic_majority()
    test_asprs_mapping()
    test_write_raster_roundtrip()
    test_triangle_classes_majority_and_ties()
    test_tribuf_to_class_raster()
    print("\nall semantic-orthophoto tests passed")
