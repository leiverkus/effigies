#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Patrick Leiverkus
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Multi-class point classification — ground / vegetation / building / vehicles.

Today the DTM step does PDAL SMRF ground-only. This runs OpenDroneMap's
**OpenPointClass** (`pcclassify`, an ML classifier; the same tool + default model
ODM uses) over the georeferenced LAZ to tag every point with an ASPRS class
(2 ground, 3/4/5 low/med/high vegetation, 6 building, 64 human-made/vehicles,
7 noise), writing the classification back into `odm_georeferenced_model.laz`.

It then rebuilds the EPT (so the Potree viewer can colour by class) and rasterises
a couple of class-filtered surfaces (`odm_dem/buildings.tif`, `odm_dem/canopy.tif`).
The DTM step, when a classified cloud exists, reuses the ML ground (class 2) instead
of re-running SMRF.

Opt-in (`classify`); needs a georeferenced result. Non-fatal.

Dependencies: the `pcclassify` binary + model (baked into the image), PDAL, entwine.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pointcloud_to_laz import build_ept          # noqa: E402
from pointcloud_to_dtm import _resolve_gsd, _valid_pixel_count, NODATA  # noqa: E402

MODEL = os.environ.get("EFFIGIES_OPC_MODEL", "/usr/local/share/effigies/opc_model.bin")
# ASPRS classes we surface as rasters: (code, basename, writers.gdal output_type)
CLASS_RASTERS = [(6, "buildings", "max"), (5, "canopy", "max")]
CLASS_NAMES = {2: "ground", 3: "low-veg", 4: "med-veg", 5: "high-veg",
               6: "building", 7: "noise", 64: "human-made", 1: "unclassified"}


def _find_counts(obj):
    """Recursively find the Classification `counts` list in PDAL metadata."""
    if isinstance(obj, dict):
        if obj.get("name") == "Classification" and "counts" in obj:
            return obj["counts"]
        for v in obj.values():
            r = _find_counts(v)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_counts(v)
            if r:
                return r
    return None


def _class_histogram(laz):
    """{class_code: count} via PDAL filters.stats(count=Classification). PDAL emits
    `counts` as 'value/count' strings; parse them. Best-effort -> None on failure."""
    try:
        pipe = json.dumps({"pipeline": [laz, {"type": "filters.stats",
                                              "count": "Classification"}]})
        proc = subprocess.run(["pdal", "pipeline", "--stdin", "--metadata", "/dev/stdout"],
                              input=pipe, text=True, capture_output=True)
        if proc.returncode != 0:
            return None
        counts = _find_counts(json.loads(proc.stdout)) or []
        out = {}
        for c in counts:
            v, n = (c.split("/") if isinstance(c, str) else (c["value"], c["count"]))
            out[int(round(float(v)))] = int(n)
        return out or None
    except Exception:
        return None


def _class_raster(laz, out_path, klass, output_type, gsd, crs):
    """Rasterise one class (max-Z surface) via writers.gdal; keep only if non-empty."""
    pipeline = json.dumps({"pipeline": [
        {"type": "readers.las", "filename": laz},
        {"type": "filters.range", "limits": f"Classification[{klass}:{klass}]"},
        {"type": "writers.gdal", "filename": out_path, "resolution": gsd,
         "output_type": output_type, "gdaldriver": "GTiff", "data_type": "float32",
         "dimension": "Z", "nodata": NODATA},
    ]})
    proc = subprocess.run(["pdal", "pipeline", "--stdin"], input=pipeline,
                          text=True, capture_output=True)
    if proc.returncode != 0 or not os.path.exists(out_path):
        return False
    if _valid_pixel_count(out_path) == 0:
        os.remove(out_path)
        return False
    return True


def run_classify(work, resolution="auto"):
    """Classify the georeferenced LAZ in place + class rasters + EPT. Non-fatal."""
    if shutil.which("pcclassify") is None:
        print("[classify] pcclassify not on PATH; skipping", file=sys.stderr)
        return False
    if not os.path.exists(MODEL):
        print(f"[classify] OpenPointClass model not found at {MODEL}; skipping", file=sys.stderr)
        return False
    laz = os.path.join(work, "odm_georeferenced_model.laz")
    if not os.path.exists(laz):
        print(f"[classify] no georeferenced LAZ at {laz}; skipping", file=sys.stderr)
        return False

    tmp = laz + ".classified.laz"
    # Classify every point (default local_smooth regularisation). NB: -u only
    # touches points already labelled "unclassified" — but our dense cloud is all
    # ASPRS class 1, which pcclassify treats as already-labelled, so -u would
    # classify nothing; and -s SKIPS classes (ODM passes -s 2,64 because it does
    # its own SMRF ground). We want all classes, so no flags.
    proc = subprocess.run(["pcclassify", laz, tmp, MODEL],
                          capture_output=True, text=True)
    if proc.returncode != 0 or not os.path.exists(tmp):
        print(f"[classify] pcclassify failed (rc={proc.returncode}); skipping\n"
              f"{proc.stderr.strip()[:500]}", file=sys.stderr)
        if os.path.exists(tmp):
            os.remove(tmp)
        return False
    os.replace(tmp, laz)                              # atomic: classified cloud is the asset

    hist = _class_histogram(laz)
    if hist:
        summary = ", ".join(f"{CLASS_NAMES.get(k, k)}:{v:,}"
                            for k, v in sorted(hist.items()) if v)
        print(f"[classify] classified {os.path.basename(laz)} — {summary}")
    else:
        print(f"[classify] classified {os.path.basename(laz)} (histogram unavailable)")

    # rebuild the EPT from the classified cloud so Potree colours by class
    ept = os.path.join(work, "entwine_pointcloud")
    if os.path.isdir(ept):
        shutil.rmtree(ept, ignore_errors=True)
    build_ept(laz, ept)

    # class-filtered surface rasters
    crs = None
    trp = os.path.join(work, "georef_transform.json")
    if os.path.exists(trp):
        crs = json.load(open(trp)).get("crs")
    gsd = _resolve_gsd(resolution, laz)
    os.makedirs(os.path.join(work, "odm_dem"), exist_ok=True)
    for code, base, otype in CLASS_RASTERS:
        out = os.path.join(work, "odm_dem", f"{base}.tif")
        if _class_raster(laz, out, code, otype, gsd, crs):
            print(f"[classify] wrote odm_dem/{base}.tif (class {code} {CLASS_NAMES.get(code,'')})")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True, help="OpenMVS workdir")
    ap.add_argument("--resolution", default="auto", help="class-raster GSD cm/px or 'auto'")
    args = ap.parse_args()
    run_classify(args.work, args.resolution)


if __name__ == "__main__":
    main()
