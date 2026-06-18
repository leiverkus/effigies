#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Patrick Leiverkus
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Multi-epoch propagation of the semantic field (Effigies ROADMAP v0.7.0).

Daily capture produces, per epoch, a v0 semantic orthophoto (``semantic_ortho.py``).
Because change detection re-lands this epoch's deliverables into the reference frame,
this epoch's semantic ortho is already in the *same* georeferenced frame as the
reference epoch's. This step carries the class field **across epochs**:

  * **Carry-forward** — a propagated field where this epoch's *unobserved* cells
    (nodata, no classified points) inherit the reference epoch's class, giving a
    temporally-consistent field (``orthophoto_semantic_propagated.tif``). Honest v0
    assumption: an unobserved cell did not change since the reference epoch.
  * **Semantic change** — the per-pixel *class transition* where both epochs are
    classified and disagree (``semantic_change.tif`` + per-transition area in
    ``odm_report/semantic_change.json``). The semantic complement of the DoD/M3C2
    (which carry the *geometric* change): e.g. structure→ground = a feature removed,
    vegetation→ground = clearing.

The reference epoch's semantic ortho is resampled onto this epoch's grid with
**nearest-neighbour** (the field is categorical). Opt-in (runs under ``--semantic``
when an ``--align-to`` reference is given); self-skips, non-fatally, when this epoch
or the reference has no semantic ortho.

Dependencies: GDAL python (read / warp / write), NumPy. Imports the v0 class
names/colours + the Byte writer from ``semantic_ortho``.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from semantic_ortho import V0_NAMES, write_raster, NODATA  # noqa: E402


def _read(tif):
    """(array, geotransform, projection) of a single-band raster, or (None, ...)."""
    from osgeo import gdal
    ds = gdal.Open(tif)
    if ds is None:
        return None, None, None
    return (ds.GetRasterBand(1).ReadAsArray(), ds.GetGeoTransform(), ds.GetProjection())


def resample_to_grid(src, geo, w, h, out):
    """Resample a categorical raster onto the (geo, w, h) grid with nearest-neighbour
    (preserves class codes), nodata 0. Returns the resampled array."""
    from osgeo import gdal
    ox, dx, _, oy, _, dy = geo
    gdal.Warp(out, src, format="GTiff",
              outputBounds=(ox, oy + h * dy, ox + w * dx, oy),
              xRes=abs(dx), yRes=abs(dy), dstNodata=NODATA,
              resampleAlg="near", outputType=gdal.GDT_Byte)
    return _read(out)[0]


def propagate_and_change(a, b, cell_area):
    """Carry-forward + semantic change from two grid-aligned v0-class arrays
    (0 = nodata). ``a`` = reference epoch, ``b`` = this epoch. Returns
    ``(propagated, change_code, stats)``: ``propagated`` = b where classified else a;
    ``change_code`` = ``a*10 + b`` where both classified and differ, else 0. Pure."""
    import numpy as np
    a = np.asarray(a); b = np.asarray(b)
    prop = np.where(b != NODATA, b, a).astype(np.uint8)
    changed = (b != NODATA) & (a != NODATA) & (b != a)
    change = np.where(changed,
                      a.astype(np.int32) * 10 + b.astype(np.int32), 0).astype(np.uint8)
    trans = {}
    for code in (int(c) for c in np.unique(change) if c != 0):
        af, bt = code // 10, code % 10
        name = f"{V0_NAMES.get(af, af)}->{V0_NAMES.get(bt, bt)}"
        trans[name] = float(int((change == code).sum()) * cell_area)
    stats = {
        "changed_area_m2": float(int(changed.sum()) * cell_area),
        "transitions_m2": trans,
        "carry_forward_area_m2": float(int(((b == NODATA) & (a != NODATA)).sum()) * cell_area),
    }
    return prop, change, stats


# transition colour table for semantic_change.tif: removals (->ground) flagged warm.
_CHANGE_COLOURS = {21: (181, 86, 63), 31: (143, 63, 44),     # veg/structure -> ground
                   12: (110, 126, 106), 13: (154, 147, 138),  # ground -> veg/structure
                   23: (154, 147, 138), 32: (110, 126, 106)}


def _write_change(change, geo, proj, out):
    from osgeo import gdal
    import numpy as np
    h, w = change.shape
    ds = gdal.GetDriverByName("GTiff").Create(out, w, h, 1, gdal.GDT_Byte,
                                              ["COMPRESS=DEFLATE"])
    ds.SetGeoTransform(geo)
    if proj:
        ds.SetProjection(proj)
    band = ds.GetRasterBand(1)
    band.SetNoDataValue(0)
    ct = gdal.ColorTable()
    ct.SetColorEntry(0, (0, 0, 0, 0))
    for code in (int(c) for c in np.unique(change) if c != 0):
        r, g, b = _CHANGE_COLOURS.get(code, (200, 60, 60))
        ct.SetColorEntry(code, (r, g, b, 255))
    band.SetRasterColorTable(ct)
    band.SetRasterColorInterpretation(gdal.GCI_PaletteIndex)
    band.WriteArray(change)
    band.FlushCache()
    ds = None


def run_semantic_propagate(work, reference_semantic):
    """Carry the semantic field across epochs: write the propagated field + the
    semantic-change raster + stats. Non-fatal; self-skips without both semantic orthos."""
    sem_dir = os.path.join(work, "odm_semantic")
    b_tif = os.path.join(sem_dir, "orthophoto_semantic.tif")
    if not os.path.exists(b_tif):
        print("[semantic] no semantic ortho for this epoch; skipping propagation",
              file=sys.stderr)
        return False
    if not reference_semantic or not os.path.exists(reference_semantic):
        print(f"[semantic] reference epoch has no semantic ortho "
              f"({reference_semantic}); skipping propagation", file=sys.stderr)
        return False

    b, geo, proj = _read(b_tif)
    if b is None:
        return False
    h, w = b.shape
    tmp = os.path.join(sem_dir, "_ref_semantic_resampled.tif")
    try:
        a = resample_to_grid(reference_semantic, geo, w, h, tmp)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    if a is None:
        print("[semantic] could not resample the reference semantic ortho; skipping",
              file=sys.stderr)
        return False

    cell_area = abs(geo[1] * geo[5])
    prop, change, stats = propagate_and_change(a, b, cell_area)
    write_raster(prop, geo, proj, os.path.join(sem_dir, "orthophoto_semantic_propagated.tif"))
    _write_change(change, geo, proj, os.path.join(sem_dir, "semantic_change.tif"))

    stats["resolution_m"] = float(abs(geo[1]))
    json.dump(stats, open(os.path.join(work, "odm_report", "semantic_change.json"), "w"),
              indent=2)
    print(f"[semantic] propagated field + change: changed "
          f"{stats['changed_area_m2']:.1f} m², carry-forward "
          f"{stats['carry_forward_area_m2']:.1f} m², transitions {stats['transitions_m2']}")
    return True


def main():
    ap = argparse.ArgumentParser(description="multi-epoch semantic field propagation")
    ap.add_argument("--work", required=True, help="OpenMVS workdir")
    ap.add_argument("--reference-semantic", required=True,
                    help="prior epoch's odm_semantic/orthophoto_semantic.tif")
    args = ap.parse_args()
    os.makedirs(os.path.join(args.work, "odm_report"), exist_ok=True)
    run_semantic_propagate(args.work, args.reference_semantic)


if __name__ == "__main__":
    main()
