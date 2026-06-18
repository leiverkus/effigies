#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Patrick Leiverkus
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for helpers/semantic_propagate.py — multi-epoch semantic propagation.

propagate_and_change (carry-forward + class transition + stats) is pure NumPy and
always runs; the GeoTIFF write needs GDAL and is skipped when unavailable.

Run:  python3 tests/test_semantic_propagate.py
"""
import os
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "helpers"))
import semantic_propagate as sp  # noqa: E402


def test_propagate_and_change():
    """Carry-forward fills this epoch's nodata from the reference; the change raster
    records a*10+b only where both are classified and differ; stats are area-correct."""
    #  (0,0): A=structure(3), B=ground(1)   -> change 31 (feature removed); prop = B = 1
    #  (0,1): A=ground(1),    B=ground(1)   -> no change; prop = 1
    #  (1,0): A=vegetation(2),B=nodata(0)   -> carry-forward A; prop = 2; no change
    #  (1,1): A=nodata(0),    B=ground(1)   -> prop = 1; no change (A unobserved)
    A = np.array([[3, 1], [2, 0]], dtype=np.uint8)
    B = np.array([[1, 1], [0, 1]], dtype=np.uint8)
    prop, change, stats = sp.propagate_and_change(A, B, cell_area=0.25)
    assert prop.tolist() == [[1, 1], [2, 1]], prop
    assert change.tolist() == [[31, 0], [0, 0]], change
    assert abs(stats["changed_area_m2"] - 0.25) < 1e-9, stats
    assert abs(stats["carry_forward_area_m2"] - 0.25) < 1e-9, stats
    assert "structure->ground" in stats["transitions_m2"], stats
    assert abs(stats["transitions_m2"]["structure->ground"] - 0.25) < 1e-9, stats
    print("ok  propagate_and_change: carry-forward + transition (structure->ground) + stats")


def test_write_change_roundtrip():
    try:
        from osgeo import gdal
    except Exception:
        print("skip write_change (needs GDAL — present in the Effigies image)")
        return
    geo = (400000.0, 0.05, 0.0, 5900000.0, 0.0, -0.05)
    change = np.array([[31, 0], [0, 21]], dtype=np.uint8)
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "ch.tif")
        sp._write_change(change, geo, "", out)
        ds = gdal.Open(out)
        band = ds.GetRasterBand(1)
        assert np.array_equal(band.ReadAsArray(), change), band.ReadAsArray()
        assert band.GetNoDataValue() == 0
        assert band.GetRasterColorTable() is not None
    print("ok  _write_change round-trips Byte + nodata + colour table")


if __name__ == "__main__":
    test_propagate_and_change()
    test_write_change_roundtrip()
    print("\nall semantic-propagation tests passed")
