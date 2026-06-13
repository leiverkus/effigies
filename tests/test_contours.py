#!/usr/bin/env python3
"""Unit tests for helpers/contours.py — DEM -> vector contours (GPKG + DXF).

Needs the GDAL python bindings (osgeo, present in the Effigies image); skipped
when unavailable. The DEM-source/skip guards are exercised; the geometry test
builds a small synthetic DEM and checks the generated contour lines.

Run:  python3 tests/test_contours.py
"""
import os
import sys
import io
import json
import tempfile
import contextlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "helpers"))
import contours as ct  # noqa: E402


def _have_gdal():
    try:
        from osgeo import gdal  # noqa: F401
        return True
    except ImportError:
        return False


def _write_dem(path, crs="EPSG:32632"):
    """A 60x60 Float32 DEM: elevation ramps 100..110 m west->east, with a 5-px
    nodata (-9999) border. origin in UTM 32N, 1 m/px, north-up."""
    from osgeo import gdal, osr
    import numpy as np
    H = W = 60
    a = np.full((H, W), -9999.0, np.float32)
    cols = np.linspace(0.0, 1.0, W, dtype=np.float32)
    ramp = 100.0 + cols * 10.0                       # 100..110
    a[5:H-5, 5:W-5] = np.tile(ramp, (H - 10, 1))[:, 5:W-5]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ds = gdal.GetDriverByName("GTiff").Create(path, W, H, 1, gdal.GDT_Float32)
    ds.SetGeoTransform([500000.0, 1.0, 0.0, 5000060.0, 0.0, -1.0])
    srs = osr.SpatialReference(); srs.SetFromUserInput(crs)
    ds.SetProjection(srs.ExportToWkt())
    b = ds.GetRasterBand(1); b.SetNoDataValue(-9999.0); b.WriteArray(a)
    ds.FlushCache(); ds = None


def _setup(work, dem_names=("dtm.tif",), crs="EPSG:32632"):
    json.dump({"source": "colmap-exif", "crs": crs},
              open(os.path.join(work, "georef_transform.json"), "w"))
    for n in dem_names:
        _write_dem(os.path.join(work, "odm_dem", n), crs)


def test_contours_geometry():
    if not _have_gdal():
        print("skip contours-geometry (needs GDAL — present in the Effigies image)")
        return
    from osgeo import ogr
    with tempfile.TemporaryDirectory() as work:
        _setup(work, ("dtm.tif",))
        assert ct.run_contours(work, 2.0) is True
        gpkg = os.path.join(work, "odm_dem", "contours.gpkg")
        dxf = os.path.join(work, "odm_dem", "contours.dxf")
        assert os.path.exists(gpkg) and os.path.exists(dxf)

        ds = ogr.Open(gpkg)
        lyr = ds.GetLayer(0)
        assert "32632" in lyr.GetSpatialRef().ExportToWkt(), "CRS not preserved"
        elevs, xs = [], []
        for feat in lyr:
            elevs.append(round(feat.GetField("elev"), 3))
            g = feat.GetGeometryRef()
            env = g.GetEnvelope()                    # (minx, maxx, miny, maxy)
            xs += [env[0], env[1]]
        assert len(elevs) > 0, "no contour lines produced"
        assert all(abs(e % 2.0) < 1e-6 for e in elevs), elevs       # multiples of interval
        assert all(100.0 <= e <= 110.0 for e in elevs), elevs       # within the DEM range
        # nodata excluded: no contour reaches into the 5-px (5 m) border
        assert min(xs) >= 500005.0 - 1.0 and max(xs) <= 500055.0 + 1.0, (min(xs), max(xs))
        ds = None
    print("ok  contours: GPKG+DXF, elev multiples of interval, CRS kept, nodata border excluded")


def test_source_selection():
    if not _have_gdal():
        print("skip contours-source (needs GDAL)")
        return
    # only a DSM present -> falls back to DSM and says so
    with tempfile.TemporaryDirectory() as work:
        _setup(work, ("dsm.tif",))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert ct.run_contours(work, 2.0) is True
        assert "source DSM" in buf.getvalue(), buf.getvalue()
    # both present -> prefers the DTM
    with tempfile.TemporaryDirectory() as work:
        _setup(work, ("dtm.tif", "dsm.tif"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert ct.run_contours(work, 2.0) is True
        assert "source DTM" in buf.getvalue(), buf.getvalue()
    print("ok  contours: prefers DTM, falls back to DSM (logged)")


def test_guards():
    if not _have_gdal():
        print("skip contours-guards (needs GDAL)")
        return
    with tempfile.TemporaryDirectory() as work:
        _setup(work, ("dtm.tif",))
        assert ct.run_contours(work, 0) is False                    # interval off
        assert not os.path.exists(os.path.join(work, "odm_dem", "contours.gpkg"))
    with tempfile.TemporaryDirectory() as work:                     # local frame
        json.dump({"source": "local-only", "crs": "local"},
                  open(os.path.join(work, "georef_transform.json"), "w"))
        os.makedirs(os.path.join(work, "odm_dem"))
        assert ct.run_contours(work, 2.0) is False
    with tempfile.TemporaryDirectory() as work:                     # no DEM
        json.dump({"source": "colmap-exif", "crs": "EPSG:32632"},
                  open(os.path.join(work, "georef_transform.json"), "w"))
        assert ct.run_contours(work, 2.0) is False
    print("ok  contours: skips on interval<=0, local frame, and missing DEM")


if __name__ == "__main__":
    test_contours_geometry()
    test_source_selection()
    test_guards()
    print("\nall contours tests passed")
