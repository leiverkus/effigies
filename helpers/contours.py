#!/usr/bin/env python3
"""
Contour / iso-lines from the DEM — vector lines for GIS (GeoPackage) and CAD (DXF).

ODM and Metashape export vector contours; this gives Effigies the same from the
DEM it already produces. Prefers the bare-earth DTM (`odm_dem/dtm.tif`) for true
terrain contours, falling back to the DSM (`odm_dem/dsm.tif`) when no DTM was
generated. Output:
  odm_dem/contours.gpkg   (3D LineString, attribute `elev`, the DEM's CRS)
  odm_dem/contours.dxf    (same lines at their elevation, for CAD)

Pure GDAL (osgeo) — no new dependency, no subprocess. Opt-in via a positive
contour interval in metres (0 = off). Self-skips for non-georeferenced results.

Dependencies: the GDAL python bindings (osgeo), already in the Effigies image.
"""
import argparse
import json
import os
import sys

NODATA = -9999.0


def _set_z(geom, z):
    """Set every vertex's Z to `z` (recurses into multi-geometries)."""
    if geom.GetGeometryCount() > 0:
        for i in range(geom.GetGeometryCount()):
            _set_z(geom.GetGeometryRef(i), z)
        return
    geom.Set3D(True)
    for i in range(geom.GetPointCount()):
        x, y, _ = geom.GetPoint(i)
        geom.SetPoint(i, x, y, z)


def run_contours(work, interval):
    """Write odm_dem/contours.{gpkg,dxf} from the DEM. Non-fatal: returns True on
    success, False (with a reason) when skipped/failed."""
    try:
        interval = float(interval)
    except (TypeError, ValueError):
        interval = 0.0
    if interval <= 0:
        print("[contours] interval <= 0; skipping", file=sys.stderr)
        return False

    tr_path = os.path.join(work, "georef_transform.json")
    if not os.path.exists(tr_path):
        print("[contours] no georef_transform.json; skipping", file=sys.stderr)
        return False
    crs = json.load(open(tr_path)).get("crs")
    if not crs or str(crs).lower() == "local":
        print("[contours] result is not georeferenced (crs=local); skipping",
              file=sys.stderr)
        return False

    try:
        from osgeo import gdal, ogr, osr
    except ImportError:
        print("[contours] GDAL python bindings missing; skipping", file=sys.stderr)
        return False

    dem_dir = os.path.join(work, "odm_dem")
    dtm = os.path.join(dem_dir, "dtm.tif")
    dsm = os.path.join(dem_dir, "dsm.tif")
    if os.path.exists(dtm):
        dem, source = dtm, "DTM"
    elif os.path.exists(dsm):
        dem, source = dsm, "DSM"
    else:
        print("[contours] no DEM (odm_dem/dtm.tif or dsm.tif); skipping", file=sys.stderr)
        return False

    ds = gdal.Open(dem)
    if ds is None:
        print(f"[contours] cannot open {dem}; skipping", file=sys.stderr)
        return False
    band = ds.GetRasterBand(1)
    nodata = band.GetNoDataValue()
    if nodata is None:
        nodata = NODATA
    srs = osr.SpatialReference()
    srs.ImportFromWkt(ds.GetProjection())

    gpkg = os.path.join(dem_dir, "contours.gpkg")
    if os.path.exists(gpkg):
        os.remove(gpkg)
    out = ogr.GetDriverByName("GPKG").CreateDataSource(gpkg)
    layer = out.CreateLayer("contours", srs, ogr.wkbLineString25D)
    layer.CreateField(ogr.FieldDefn("elev", ogr.OFTReal))

    rc = gdal.ContourGenerateEx(band, layer, options=[
        f"LEVEL_INTERVAL={interval}",
        "ELEV_FIELD=0",
        f"NODATA={nodata}",
        "ID_FIELD=-1",
    ])
    ds = None
    if rc != 0:
        print(f"[contours] ContourGenerateEx failed (rc={rc}); skipping", file=sys.stderr)
        out = None
        return False

    # Lift each line to its elevation so the lines sit at their true height
    # (so the DXF is usable in CAD, and the GPKG is genuine 3D).
    n = 0
    layer.ResetReading()
    for feat in layer:
        geom = feat.GetGeometryRef()
        if geom is not None:
            _set_z(geom, feat.GetField("elev"))
            feat.SetGeometry(geom)
            layer.SetFeature(feat)
        n += 1
    out.FlushCache()
    out = None

    if n == 0:
        os.remove(gpkg)
        print(f"[contours] no contour lines at interval {interval:g} m "
              f"(DEM too flat / interval too large); nothing written", file=sys.stderr)
        return False

    dxf = os.path.join(dem_dir, "contours.dxf")
    if os.path.exists(dxf):
        os.remove(dxf)
    # DXF carries the lines at their Z (elevation) but cannot store the `elev`
    # attribute — GDAL warns about the dropped field; quiet that benign message
    # while still catching a genuine translate failure (d is None).
    gdal.PushErrorHandler("CPLQuietErrorHandler")
    try:
        d = gdal.VectorTranslate(dxf, gpkg, format="DXF")
    finally:
        gdal.PopErrorHandler()
    if d is None:
        print("[contours] DXF translate failed; GeoPackage still written", file=sys.stderr)
    d = None

    note = "" if source == "DTM" else " (enable --dtm for bare-earth terrain contours)"
    print(f"[contours] wrote contours.gpkg + contours.dxf "
          f"(interval {interval:g} m, source {source}, {n} lines, crs={crs}){note}")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True, help="OpenMVS workdir")
    ap.add_argument("--interval", default="0",
                    help="contour interval in metres (0 = off)")
    args = ap.parse_args()
    run_contours(args.work, args.interval)


if __name__ == "__main__":
    main()
