#!/usr/bin/env python3
"""Unit tests for helpers/orthophoto.py — the textured-mesh orthophoto.

The rasteriser (geometry + texture sampling + z-buffer) is pure numpy and always
runs. The GeoTIFF georeferencing needs GDAL python bindings (osgeo, present in the
Effigies image) and is skipped when unavailable. A flat textured quad is enough to
pin orientation, coverage and per-corner colour.

Run:  python3 tests/test_orthophoto.py
"""
import os
import sys
import json
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "helpers"))
import orthophoto as op  # noqa: E402


def _quad_scene(gsd=0.05):
    """A flat z=0 quad over [0,20]x[0,15] with a 4-quadrant texture
    (TL red, TR green, BL blue, BR yellow). Returns (rgb, alpha, xmin, ymax)."""
    V = np.array([[0, 0, 0], [20, 0, 0], [20, 15, 0], [0, 15, 0]], float)
    VT = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], float)
    TV = np.array([[0, 1, 2], [0, 2, 3]])
    TVT = np.array([[0, 1, 2], [0, 2, 3]])
    tex = np.zeros((256, 256, 3), np.uint8)
    tex[:128, :128] = (255, 0, 0); tex[:128, 128:] = (0, 255, 0)
    tex[128:, :128] = (0, 0, 255); tex[128:, 128:] = (255, 255, 0)
    return op.rasterize(V, VT, TV, TVT, tex, gsd)


def test_rasterize_orientation_and_coverage():
    rgb, alpha, xmin, ymax = _quad_scene(gsd=0.05)
    assert rgb.shape == (300, 400, 3), rgb.shape          # 20/0.05 x 15/0.05
    assert np.isclose((alpha > 0).mean(), 1.0), (alpha > 0).mean()
    H, W = alpha.shape
    corners = {"TL": tuple(rgb[2, 2]), "TR": tuple(rgb[2, W - 3]),
               "BL": tuple(rgb[H - 3, 2]), "BR": tuple(rgb[H - 3, W - 3])}
    # north-up, OBJ v bottom-up: NW=texture top-left=red, etc.
    assert corners["TL"] == (255, 0, 0), corners
    assert corners["TR"] == (0, 255, 0), corners
    assert corners["BL"] == (0, 0, 255), corners
    assert corners["BR"] == (255, 255, 0), corners
    print("ok  orthophoto rasterises the textured mesh (orientation, 100% coverage, colours)")


def _have_gdal():
    try:
        from osgeo import gdal  # noqa: F401
        return True
    except ImportError:
        return False


def test_geotiff_is_georeferenced():
    if not _have_gdal():
        print("skip geotiff (needs GDAL python bindings — present in the Effigies image)")
        return
    from osgeo import gdal
    rgb, alpha, xmin, ymax = _quad_scene(gsd=0.05)
    d = tempfile.mkdtemp()
    tif = os.path.join(d, "o.tif")
    op.write_geotiff(tif, rgb, alpha, 415700.0 + xmin, 5958560.0 + ymax, 0.05, "EPSG:32632")
    ds = gdal.Open(tif)
    gt = ds.GetGeoTransform()
    assert "32632" in ds.GetProjection(), ds.GetProjection()[:60]
    assert ds.RasterCount == 4, ds.RasterCount                       # RGB + alpha
    assert np.isclose(gt[0], 415700.0) and np.isclose(gt[3], 5958575.0), gt
    assert np.isclose(gt[1], 0.05) and np.isclose(gt[5], -0.05), gt   # north-up
    ds = None
    print("ok  orthophoto writes a georeferenced GeoTIFF (CRS, origin, north-up)")


def test_skips_when_not_georeferenced():
    """A local-frame result has no meaningful ortho — main() must skip cleanly
    (no GeoTIFF), not crash or emit a bogus raster."""
    with tempfile.TemporaryDirectory() as work:
        json.dump({"source": "local-only", "crs": "local", "offset": [0, 0, 0]},
                  open(os.path.join(work, "georef_transform.json"), "w"))
        argv = sys.argv
        try:
            sys.argv = ["orthophoto.py", "--work", work]
            op.main()
        finally:
            sys.argv = argv
        assert not os.path.exists(os.path.join(work, "odm_orthophoto.tif"))
    print("ok  orthophoto skips a local (un-georeferenced) result")


if __name__ == "__main__":
    test_rasterize_orientation_and_coverage()
    test_geotiff_is_georeferenced()
    test_skips_when_not_georeferenced()
    print("\nall orthophoto tests passed")
