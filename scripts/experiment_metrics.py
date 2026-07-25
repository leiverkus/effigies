#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Patrick Leiverkus
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Metrics for the single-variable engine experiments (docs/planned-experiments.md).

Computes the quantities the experiments compare, from the DELIVERED assets only —
no engine internals — so the baseline and every delta run are measured identically:

  boundary_edges      Edges used by exactly one face in the textured mesh. The
                      open-hole indicator: a watertight surface has none. This is
                      Experiment A's primary measure.
  interior_nodata     Fraction of orthophoto nodata that is NOT connected to the
                      image border, i.e. genuine holes punched through coverage
                      rather than the ragged outer boundary. Used instead of a
                      hand-drawn "building core" crop, which is not reproducible
                      across runs and would invite unconscious re-framing.
  mesh/cloud counts   Vertices, faces, dense points — Experiment B's cascade.

Usage:  experiment_metrics.py <project_dir> [--label NAME] [--json out.json]
"""
import argparse
import json
import os
import sys


def boundary_edges(obj_path):
    """Count edges referenced by exactly one face, streaming the OBJ.

    An OBJ this size (10^7 faces) does not fit in a naive Python dict of tuples
    without a lot of memory, so edges are folded into a single int64 key and
    counted with numpy. Only the parity matters, so a set-toggle would also work,
    but the counter also lets us report non-manifold edges (>2 faces) separately.
    """
    import numpy as np

    # Accumulate folded edge keys in numpy chunks. A Python list of ~3*10^7 tuples
    # would cost several GB and dominate the runtime; chunking keeps it flat.
    CHUNK = 4_000_000
    buf = np.empty(CHUNK, dtype=np.int64)
    n_buf = 0
    chunks = []

    def flush():
        nonlocal n_buf
        if n_buf:
            chunks.append(buf[:n_buf].copy())
            n_buf = 0

    with open(obj_path, "r", errors="ignore") as f:
        for line in f:
            if not line.startswith("f "):
                continue
            # face verts may be v, v/vt, v//vn, v/vt/vn — take the vertex index
            idx = [int(tok.split("/", 1)[0]) for tok in line.split()[1:]]
            n = len(idx)
            for i in range(n):
                a, b = idx[i], idx[(i + 1) % n]
                if a > b:
                    a, b = b, a
                if n_buf == CHUNK:
                    flush()
                buf[n_buf] = (a << 32) | b
                n_buf += 1
    flush()

    if not chunks:
        return {"boundary_edges": None, "note": "no faces parsed"}
    key = np.concatenate(chunks)
    del chunks
    key.sort()
    # run lengths of identical keys
    change = np.empty(key.shape[0], dtype=bool)
    change[0] = True
    np.not_equal(key[1:], key[:-1], out=change[1:])
    starts = np.flatnonzero(change)
    counts = np.diff(np.append(starts, key.shape[0]))
    return {
        "total_edges": int(starts.size),
        "boundary_edges": int((counts == 1).sum()),
        "manifold_edges": int((counts == 2).sum()),
        "nonmanifold_edges": int((counts > 2).sum()),
    }


def interior_nodata(ortho_path):
    """Fraction of the ortho that is nodata, split into border-connected vs interior.

    Interior nodata = holes fully enclosed by valid pixels. Flood-filling the
    invalid mask from the image border separates the two without any hand-placed
    crop, so the number means the same thing in every run.
    """
    import numpy as np
    from osgeo import gdal
    from scipy import ndimage

    ds = gdal.Open(ortho_path)
    if ds is None:
        return {"error": f"cannot open {ortho_path}"}
    n = ds.RasterCount
    # prefer the alpha band when present (ODM/Effigies orthos are RGBA)
    if n >= 4:
        a = ds.GetRasterBand(n).ReadAsArray()
        invalid = a == 0
    else:
        b = ds.GetRasterBand(1)
        nd = b.GetNoDataValue()
        arr = b.ReadAsArray()
        invalid = (arr == nd) if nd is not None else np.zeros(arr.shape, bool)

    total = invalid.size
    lbl, _ = ndimage.label(invalid)
    border = set(lbl[0, :]) | set(lbl[-1, :]) | set(lbl[:, 0]) | set(lbl[:, -1])
    border.discard(0)
    border_mask = np.isin(lbl, list(border)) if border else np.zeros(lbl.shape, bool)
    interior = invalid & ~border_mask
    return {
        "raster_px": int(total),
        "nodata_px": int(invalid.sum()),
        "nodata_frac": round(float(invalid.sum()) / total, 6),
        "interior_nodata_px": int(interior.sum()),
        "interior_nodata_frac": round(float(interior.sum()) / total, 6),
        "interior_hole_count": int(len(set(np.unique(lbl[interior])) - {0})),
    }


def mesh_counts(obj_path):
    v = f = 0
    with open(obj_path, "r", errors="ignore") as fh:
        for line in fh:
            if line.startswith("v "):
                v += 1
            elif line.startswith("f "):
                f += 1
    return {"vertices": v, "faces": f}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--label", default=None)
    ap.add_argument("--json", default=None)
    a = ap.parse_args()

    P = a.project
    obj = os.path.join(P, "odm_texturing", "odm_textured_model_geo.obj")
    ortho = os.path.join(P, "odm_orthophoto", "odm_orthophoto.tif")

    out = {"label": a.label or os.path.basename(P.rstrip("/"))}
    if os.path.isfile(obj):
        out["mesh"] = mesh_counts(obj)
        out["edges"] = boundary_edges(obj)
        out["obj_bytes"] = os.path.getsize(obj)
    else:
        out["mesh"] = {"error": f"missing {obj}"}
    if os.path.isfile(ortho):
        out["ortho"] = interior_nodata(ortho)
    else:
        out["ortho"] = {"error": f"missing {ortho}"}

    txt = json.dumps(out, indent=2)
    print(txt)
    if a.json:
        with open(a.json, "w") as f:
            f.write(txt + "\n")


if __name__ == "__main__":
    sys.exit(main())
