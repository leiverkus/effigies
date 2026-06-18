#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Patrick Leiverkus
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Semantic orthophoto (v0) — the geometry-derived per-pixel surface-class raster.

This is the **free first increment** of the semantic field (Effigies ROADMAP v0.7.0):
it rasterises the point classes that ``classify_cloud.py`` (OpenPointClass) already
wrote into ``odm_georeferenced_model.laz`` onto the orthophoto grid, so every pixel
gets the dominant coarse class — **ground / vegetation / structure**. No trained model
is involved: it ships what the cloud classification already knows. The fine
archaeological material classes (stone / earth / paving / ceramic / mortar) are a
downstream 2D-model deliverable (Structura); this v0 is the bridge's v0.

The raster is **pixel-aligned** with ``odm_dem/dsm.tif`` (else the orthophoto) so it
overlays exactly. Output: ``odm_semantic/orthophoto_semantic.tif`` (Byte, georeferenced,
with a GDAL colour table) + ``odm_semantic/orthophoto_semantic.legend.json``.

Opt-in (``--semantic``); needs a **classified** cloud (run with ``--classify``) — it
self-skips, non-fatally, if the cloud carries no OpenPointClass classes.

Dependencies: PDAL (read the classified cloud), GDAL python (grid + write), NumPy.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

NODATA = 0
# ASPRS class (OpenPointClass) -> v0 class code. 2 ground; 3/4/5 low/med/high veg;
# 6 building, 64 human-made -> structure. 1 unclassified / 7 noise -> nodata.
ASPRS_TO_V0 = {2: 1, 3: 2, 4: 2, 5: 2, 6: 3, 64: 3}
V0_NAMES = {1: "ground", 2: "vegetation", 3: "structure"}
V0_COLOURS = {1: (138, 110, 75), 2: (110, 126, 106), 3: (154, 147, 138)}  # soil/veg/stone


def read_grid(work):
    """(geotransform, width, height, projection) of the grid to align to — the DSM
    if present (the geometry-derived surface grid), else the orthophoto. None if
    neither exists."""
    from osgeo import gdal
    for rel in ("odm_dem/dsm.tif", "odm_orthophoto/odm_orthophoto.tif"):
        p = os.path.join(work, rel)
        if os.path.exists(p):
            ds = gdal.Open(p)
            if ds is not None:
                return ds.GetGeoTransform(), ds.RasterXSize, ds.RasterYSize, ds.GetProjection()
    return None


def read_xyc(laz, n_target=4000000):
    """Decimated (X, Y, Classification) arrays from a classified cloud via PDAL."""
    import numpy as np
    txt = tempfile.mktemp(suffix=".csv")
    pj = tempfile.mktemp(suffix=".json")
    try:
        pipe = {"pipeline": [laz,
                {"type": "writers.text", "filename": txt, "format": "csv",
                 "order": "X,Y,Classification", "keep_unspecified": False,
                 "write_header": False}]}
        open(pj, "w").write(json.dumps(pipe))
        subprocess.check_call(["pdal", "pipeline", pj],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        a = np.loadtxt(txt, delimiter=",", ndmin=2)
        if a.size == 0:
            return None
        if len(a) > n_target:
            a = a[np.linspace(0, len(a) - 1, n_target).astype(int)]
        return a[:, 0], a[:, 1], a[:, 2].astype(np.int64)
    finally:
        for f in (txt, pj):
            try:
                os.remove(f)
            except OSError:
                pass


def build_semantic(x, y, c, geo, w, h):
    """Per-pixel **dominant** v0 class on the (geo, w, h) grid: bin the classified
    points, map ASPRS -> v0, and take the majority class per cell (nodata where no
    classified point falls). Returns an (h, w) uint8 array. Pure (NumPy)."""
    import numpy as np
    ox, dx, _, oy, _, dy = geo
    col = np.floor((x - ox) / dx).astype(np.int64)
    row = np.floor((y - oy) / dy).astype(np.int64)        # dy<0 -> north-up, row 0 = top
    v0 = np.zeros(c.shape, dtype=np.int64)
    for asprs, code in ASPRS_TO_V0.items():
        v0[c == asprs] = code
    keep = (v0 > 0) & (col >= 0) & (col < w) & (row >= 0) & (row < h)
    flat = row[keep] * w + col[keep]
    cls = v0[keep]
    counts = np.zeros((3, w * h), dtype=np.int32)
    for k in (1, 2, 3):
        np.add.at(counts[k - 1], flat[cls == k], 1)
    total = counts.sum(axis=0)
    dom = counts.argmax(axis=0) + 1                       # 1..3
    return np.where(total > 0, dom, NODATA).astype(np.uint8).reshape(h, w)


def write_raster(arr, geo, proj, out):
    """Write the class array as a Byte GeoTIFF with a colour table (nodata 0)."""
    from osgeo import gdal
    h, w = arr.shape
    ds = gdal.GetDriverByName("GTiff").Create(
        out, w, h, 1, gdal.GDT_Byte, ["COMPRESS=DEFLATE"])
    ds.SetGeoTransform(geo)
    if proj:
        ds.SetProjection(proj)
    band = ds.GetRasterBand(1)
    band.SetNoDataValue(NODATA)
    ct = gdal.ColorTable()
    ct.SetColorEntry(NODATA, (0, 0, 0, 0))
    for code, rgb in V0_COLOURS.items():
        ct.SetColorEntry(code, (rgb[0], rgb[1], rgb[2], 255))
    band.SetRasterColorTable(ct)
    band.SetRasterColorInterpretation(gdal.GCI_PaletteIndex)
    band.WriteArray(arr)
    band.FlushCache()
    ds = None


def run_semantic_ortho(work):
    """Build odm_semantic/orthophoto_semantic.tif from the classified cloud. Non-fatal,
    self-skips when the cloud is not classified or no ortho grid exists."""
    import numpy as np
    laz = os.path.join(work, "odm_georeferenced_model.laz")
    if not os.path.exists(laz):
        print(f"[semantic] no georeferenced LAZ at {laz}; skipping", file=sys.stderr)
        return False
    grid = read_grid(work)
    if grid is None:
        print("[semantic] no DSM/orthophoto grid to align to; skipping (needs a "
              "georeferenced raster result)", file=sys.stderr)
        return False
    geo, w, h, proj = grid
    xyc = read_xyc(laz)
    if xyc is None:
        print("[semantic] empty cloud; skipping", file=sys.stderr)
        return False
    x, y, c = xyc
    if not np.isin(c, list(ASPRS_TO_V0)).any():
        print("[semantic] cloud carries no OpenPointClass classes — run with "
              "--classify to enable the v0 semantic orthophoto; skipping", file=sys.stderr)
        return False

    arr = build_semantic(x, y, c, geo, w, h)
    out_dir = os.path.join(work, "odm_semantic")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "orthophoto_semantic.tif")
    write_raster(arr, geo, proj, out)

    present = {int(k): V0_NAMES[int(k)] for k in np.unique(arr) if int(k) in V0_NAMES}
    legend = {"version": "v0-geometry",
              "source": "OpenPointClass cloud classes (ground/vegetation/structure)",
              "nodata": NODATA,
              "classes": {str(code): {"name": V0_NAMES[code],
                                      "rgb": list(V0_COLOURS[code])}
                          for code in V0_NAMES}}
    json.dump(legend, open(os.path.join(out_dir, "orthophoto_semantic.legend.json"), "w"),
              indent=2)
    cov = float((arr != NODATA).mean())
    print(f"[semantic] wrote odm_semantic/orthophoto_semantic.tif "
          f"({w}x{h}, {100*cov:.0f}% classified, classes: {sorted(present.values())})")
    return True


def main():
    ap = argparse.ArgumentParser(description="v0 semantic orthophoto from cloud classes")
    ap.add_argument("--work", required=True, help="OpenMVS workdir")
    args = ap.parse_args()
    run_semantic_ortho(args.work)


if __name__ == "__main__":
    main()
