#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Patrick Leiverkus
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Digital Terrain Model (DTM) — bare-earth raster from the dense point cloud.

The DSM (``orthophoto.py``) captures the *top* surface (roofs, vegetation). The
DTM is the complement: the ground only, with buildings and vegetation removed.
It is derived from the **georeferenced LAZ** (``odm_georeferenced_model.laz``,
written in absolute projected coordinates by ``pointcloud_to_laz.py``) via PDAL:

  readers.las -> filters.outlier (flag noise) -> filters.smrf (classify ground)
              -> filters.range (keep ground) -> writers.gdal (rasterise to GeoTIFF)

``filters.smrf`` is the Simple Morphological Filter (Pingel et al. 2013), the
same ground classifier ODM uses. Output: ``odm_dem/dtm.tif`` (single-band
Float32, nodata -9999), in the cloud's CRS.

Opt-in: the ground classification costs real time and a bare-earth model is
meaningless for close-range / object captures with no open ground, so this runs
only when requested (the ``dtm`` task option). Skipped for non-georeferenced
(local-frame) results.

Dependencies: PDAL (external binary; the same one ``pointcloud_to_laz.py`` needs)
and the GDAL python bindings (only for the post-write validity check).
"""
import argparse
import json
import math
import os
import shutil
import subprocess
import sys

NODATA = -9999.0


def build_dtm_pipeline(laz_path, dtm_path, gsd, nodata=NODATA, pre_classified=False):
    """The PDAL pipeline dict: outlier -> SMRF ground -> keep ground -> rasterise.

    When ``pre_classified`` (the cloud already carries ML classes from
    OpenPointClass), skip the outlier+SMRF stages and use the existing ground
    class (2) directly. Pure (no I/O); returned as a dict, unit-testable."""
    ground = [
        # statistical outlier removal flags noise as Classification 7 so SMRF
        # does not anchor the ground surface to stray low points
        {"type": "filters.outlier"},
        {"type": "filters.smrf", "ignore": "Classification[7:7]"},
    ] if not pre_classified else []
    return {"pipeline": [
        {"type": "readers.las", "filename": laz_path},
        *ground,
        # keep only the ground returns (SMRF- or ML-labelled Classification 2)
        {"type": "filters.range", "limits": "Classification[2:2]"},
        {"type": "writers.gdal",
         "filename": dtm_path,
         "resolution": gsd,
         "output_type": "idw",       # inverse-distance fill across the sparse ground
         "window_size": 3,            # bridge small gaps between ground returns
         "gdaldriver": "GTiff",
         "data_type": "float32",
         "dimension": "Z",
         "nodata": nodata}],
    }


def _cloud_bounds(laz_path):
    """(minx, miny, maxx, maxy) of the cloud via ``pdal info --summary``, or None."""
    try:
        proc = subprocess.run(["pdal", "info", "--summary", laz_path],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            return None
        b = json.loads(proc.stdout)["summary"]["bounds"]
        return b["minx"], b["miny"], b["maxx"], b["maxy"]
    except Exception:
        return None


def _resolve_gsd(resolution, laz_path):
    """Ground sample distance in metres — mirrors orthophoto.py so DSM and DTM
    share a grid. Numeric ``resolution`` is cm/px; 'auto' targets ~4k px wide."""
    if str(resolution).lower() != "auto":
        return float(resolution) / 100.0
    b = _cloud_bounds(laz_path)
    if not b:
        return 0.05                                   # safe fallback: 5 cm/px
    diag = math.hypot(b[2] - b[0], b[3] - b[1])
    return min(max(diag / 4096.0, 0.01), 1.0)         # ~4k px wide, clamped 1cm..1m


def _valid_pixel_count(tif):
    """Number of non-nodata pixels in the DTM, or None if it cannot be read."""
    try:
        from osgeo import gdal
        import numpy as np
        ds = gdal.Open(tif)
        if ds is None:
            return None
        band = ds.GetRasterBand(1)
        nd = band.GetNoDataValue()
        a = band.ReadAsArray()
        return int((a != nd).sum()) if nd is not None else int(a.size)
    except Exception:
        return None


def run_dtm(work, resolution="auto", laz=None, pre_classified=False):
    """Produce ``<work>/odm_dem/dtm.tif`` from the georeferenced LAZ. Non-fatal:
    returns True on success, False (with a reason) when skipped/failed.
    ``pre_classified`` reuses the cloud's existing ML ground (class 2)."""
    tr_path = os.path.join(work, "georef_transform.json")
    if not os.path.exists(tr_path):
        print("[dtm] no georef_transform.json; skipping DTM", file=sys.stderr)
        return False
    crs = json.load(open(tr_path)).get("crs")
    if not crs or str(crs).lower() == "local":
        print("[dtm] result is not georeferenced (crs=local); skipping DTM",
              file=sys.stderr)
        return False

    laz = laz or os.path.join(work, "odm_georeferenced_model.laz")
    if not os.path.exists(laz):
        print(f"[dtm] no georeferenced LAZ at {laz}; skipping DTM", file=sys.stderr)
        return False
    if shutil.which("pdal") is None:
        print("[dtm] pdal not found on PATH; skipping DTM", file=sys.stderr)
        return False

    gsd = _resolve_gsd(resolution, laz)
    dtm = os.path.join(work, "odm_dem", "dtm.tif")
    os.makedirs(os.path.dirname(dtm), exist_ok=True)

    pipeline = json.dumps(build_dtm_pipeline(laz, dtm, gsd, pre_classified=pre_classified))
    proc = subprocess.run(["pdal", "pipeline", "--stdin"],
                          input=pipeline, text=True, capture_output=True)
    if proc.returncode != 0:
        print(f"[dtm] pdal pipeline failed (non-fatal):\n{proc.stderr.strip()}",
              file=sys.stderr)
        return False

    # Guard against emitting a bogus all-nodata raster (e.g. a capture with no
    # open ground): if SMRF classified essentially nothing, drop the file.
    valid = _valid_pixel_count(dtm)
    if valid is not None and valid == 0:
        os.remove(dtm)
        print("[dtm] no ground points classified (no open terrain?); DTM not written",
              file=sys.stderr)
        return False

    print(f"[dtm] wrote odm_dem/dtm.tif (@ {gsd*100:.1f} cm/px, "
          f"{valid if valid is not None else '?'} ground cells, crs={crs})")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True, help="OpenMVS workdir")
    ap.add_argument("--resolution", default="auto",
                    help="ground sample distance in cm/px, or 'auto' (~4k px wide)")
    ap.add_argument("--laz", default=None,
                    help="georeferenced LAZ (default: <work>/odm_georeferenced_model.laz)")
    ap.add_argument("--pre-classified", action="store_true",
                    help="reuse the cloud's existing ground class (skip SMRF)")
    args = ap.parse_args()
    run_dtm(args.work, args.resolution, args.laz, args.pre_classified)


if __name__ == "__main__":
    main()
