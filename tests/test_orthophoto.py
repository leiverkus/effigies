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
    (TL red, TR green, BL blue, BR yellow). Returns (rgb, alpha, xmin, ymax, zbuf)."""
    V = np.array([[0, 0, 0], [20, 0, 0], [20, 15, 0], [0, 15, 0]], float)
    VT = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], float)
    TV = np.array([[0, 1, 2], [0, 2, 3]])
    TVT = np.array([[0, 1, 2], [0, 2, 3]])
    TM = np.array([0, 0])                     # both tris on texture page 0
    tex = np.zeros((256, 256, 3), np.uint8)
    tex[:128, :128] = (255, 0, 0); tex[:128, 128:] = (0, 255, 0)
    tex[128:, :128] = (0, 0, 255); tex[128:, 128:] = (255, 255, 0)
    return op.rasterize(V, VT, TV, TVT, TM, [tex], gsd)


def test_rasterize_orientation_and_coverage():
    rgb, alpha, xmin, ymax, _z = _quad_scene(gsd=0.05)
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
    rgb, alpha, xmin, ymax, _z = _quad_scene(gsd=0.05)
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
        assert not os.path.exists(os.path.join(work, "odm_dem", "dsm.tif"))
    print("ok  orthophoto/DSM skip a local (un-georeferenced) result")


def _ramp_scene(gsd=0.05, slope=0.5):
    """A quad over [0,20]x[0,15] tilted in X so the surface height is z = slope*x
    (0 at the west edge, slope*20 at the east). Returns (rgb, alpha, xmin, ymax, zbuf)."""
    V = np.array([[0, 0, 0.0], [20, 0, slope * 20], [20, 15, slope * 20],
                  [0, 15, 0.0]], float)
    VT = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], float)
    TV = np.array([[0, 1, 2], [0, 2, 3]])
    TVT = np.array([[0, 1, 2], [0, 2, 3]])
    TM = np.array([0, 0])
    tex = np.full((16, 16, 3), 128, np.uint8)
    return op.rasterize(V, VT, TV, TVT, TM, [tex], gsd)


def test_dsm_height_grid():
    """The z-buffer the rasteriser returns IS the DSM: per-pixel surface height.
    Flat quad -> all zero where covered; a ramp -> right range and monotone in X."""
    _, alpha, _, _, zflat = _quad_scene(gsd=0.05)
    cov = alpha > 0
    assert np.allclose(zflat[cov], 0.0), zflat[cov].max()
    assert np.isneginf(zflat[~cov]).all() if (~cov).any() else True

    _, alpha, _, _, z = _ramp_scene(gsd=0.05, slope=0.5)
    cov = alpha > 0
    h = z[cov]
    assert abs(h.min() - 0.0) < 0.1 and abs(h.max() - 10.0) < 0.1, (h.min(), h.max())
    # north-up raster, x grows with column -> height grows left->right
    zc = np.where(cov, z, np.nan)
    left = np.nanmean(zc[:, : zc.shape[1] // 4])
    right = np.nanmean(zc[:, -zc.shape[1] // 4:])
    assert right > left + 5.0, (left, right)
    print("ok  DSM height grid (flat=0, ramp range + monotone in X)")


def test_dsm_geotiff_is_single_band_float():
    if not _have_gdal():
        print("skip dsm-geotiff (needs GDAL python bindings — present in the Effigies image)")
        return
    from osgeo import gdal
    _, _, xmin, ymax, z = _ramp_scene(gsd=0.05, slope=0.5)
    d = tempfile.mkdtemp()
    tif = os.path.join(d, "dem", "dsm.tif")
    op.write_dem_geotiff(tif, z, 415700.0 + xmin, 5958560.0 + ymax, 0.05, "EPSG:32632")
    ds = gdal.Open(tif)
    band = ds.GetRasterBand(1)
    gt = ds.GetGeoTransform()
    assert ds.RasterCount == 1, ds.RasterCount
    assert band.DataType == gdal.GDT_Float32, gdal.GetDataTypeName(band.DataType)
    assert band.GetNoDataValue() == -9999.0, band.GetNoDataValue()
    assert "32632" in ds.GetProjection(), ds.GetProjection()[:60]
    assert np.isclose(gt[1], 0.05) and np.isclose(gt[5], -0.05), gt   # north-up
    arr = band.ReadAsArray()
    covered = arr != -9999.0
    assert covered.any() and abs(arr[covered].max() - 10.0) < 0.2, arr[covered].max()
    assert (arr[~covered] == -9999.0).all() if (~covered).any() else True
    ds = None
    print("ok  DSM writes a single-band Float32 GeoTIFF (nodata -9999, north-up, CRS)")


if __name__ == "__main__":
    test_rasterize_orientation_and_coverage()
    test_geotiff_is_georeferenced()
    test_skips_when_not_georeferenced()
    test_dsm_height_grid()
    test_dsm_geotiff_is_single_band_float()
    print("\nall orthophoto tests passed")
