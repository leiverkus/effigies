#!/usr/bin/env python3
"""Unit tests for helpers/pointcloud_to_dtm.py — the bare-earth DTM.

The pipeline-builder is pure and always runs. The end-to-end test needs the PDAL
binary (present in the Effigies image) and GDAL python bindings, and is skipped
when either is unavailable.

Run:  python3 tests/test_dtm.py
"""
import os
import sys
import json
import shutil
import tempfile
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "helpers"))
import pointcloud_to_dtm as dtm  # noqa: E402


def test_pipeline_structure():
    p = dtm.build_dtm_pipeline("/in/cloud.laz", "/out/odm_dem/dtm.tif", 0.05)["pipeline"]
    types = [s.get("type") for s in p]
    assert types == ["readers.las", "filters.outlier", "filters.smrf",
                     "filters.range", "writers.gdal"], types
    smrf = next(s for s in p if s["type"] == "filters.smrf")
    assert smrf["ignore"] == "Classification[7:7]", smrf
    rng = next(s for s in p if s["type"] == "filters.range")
    assert rng["limits"] == "Classification[2:2]", rng          # keep ground only
    w = p[-1]
    assert w["filename"].endswith("odm_dem/dtm.tif")
    assert w["output_type"] == "idw" and w["nodata"] == -9999.0, w
    assert w["resolution"] == 0.05 and w["data_type"] == "float32", w
    assert w["dimension"] == "Z", w
    print("ok  DTM pipeline (outlier -> smrf -> ground-only -> gdal idw, nodata -9999)")


def test_skips_local_frame():
    """A local-frame result has no CRS — run_dtm must skip, not write a file."""
    with tempfile.TemporaryDirectory() as work:
        json.dump({"source": "local-only", "crs": "local"},
                  open(os.path.join(work, "georef_transform.json"), "w"))
        assert dtm.run_dtm(work) is False
        assert not os.path.exists(os.path.join(work, "odm_dem", "dtm.tif"))
    print("ok  DTM skips a local (un-georeferenced) result")


def test_skips_missing_laz():
    with tempfile.TemporaryDirectory() as work:
        json.dump({"source": "colmap-exif", "crs": "EPSG:32632"},
                  open(os.path.join(work, "georef_transform.json"), "w"))
        assert dtm.run_dtm(work) is False                       # no LAZ present
        assert not os.path.exists(os.path.join(work, "odm_dem", "dtm.tif"))
    print("ok  DTM skips cleanly when the georeferenced LAZ is missing")


def _have(bin_or_mod):
    if bin_or_mod == "gdal":
        try:
            from osgeo import gdal  # noqa: F401
            return True
        except ImportError:
            return False
    return shutil.which(bin_or_mod) is not None


def test_dtm_end_to_end():
    if not (_have("pdal") and _have("gdal")):
        print("skip dtm-e2e (needs pdal + GDAL — present in the Effigies image)")
        return
    from osgeo import gdal
    with tempfile.TemporaryDirectory() as work:
        # a rough ground over a 50x50 m patch, written as the georeferenced LAZ
        laz = os.path.join(work, "odm_georeferenced_model.laz")
        faux = {"pipeline": [
            {"type": "readers.faux", "mode": "random", "count": 8000,
             "bounds": "([300000,300050],[5000000,5000050],[100,101])"},
            {"type": "writers.las", "filename": laz, "a_srs": "EPSG:32632"}]}
        r = subprocess.run(["pdal", "pipeline", "--stdin"], input=json.dumps(faux),
                          text=True, capture_output=True)
        assert r.returncode == 0, r.stderr
        json.dump({"source": "colmap-exif", "crs": "EPSG:32632"},
                  open(os.path.join(work, "georef_transform.json"), "w"))

        assert dtm.run_dtm(work, resolution="100") is True       # 1 m/px over 50 m
        out = os.path.join(work, "odm_dem", "dtm.tif")
        assert os.path.exists(out)
        ds = gdal.Open(out)
        band = ds.GetRasterBand(1)
        assert ds.RasterCount == 1, ds.RasterCount
        assert band.DataType == gdal.GDT_Float32, gdal.GetDataTypeName(band.DataType)
        assert band.GetNoDataValue() == -9999.0, band.GetNoDataValue()
        assert "32632" in ds.GetProjection(), ds.GetProjection()[:60]
        import numpy as np
        a = band.ReadAsArray()
        valid = a[a != -9999.0]
        assert valid.size > 0 and 99.0 < valid.mean() < 102.0, (valid.size, valid.mean())
        ds = None
    print("ok  DTM end-to-end (SMRF ground -> single-band Float32 GeoTIFF, nodata -9999)")


if __name__ == "__main__":
    test_pipeline_structure()
    test_skips_local_frame()
    test_skips_missing_laz()
    test_dtm_end_to_end()
    print("\nall DTM tests passed")
