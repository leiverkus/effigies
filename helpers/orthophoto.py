#!/usr/bin/env python3
"""
Generate a georeferenced orthophoto (GeoTIFF) from the textured mesh.

WebODM expects an orthophoto at ``odm_orthophoto/odm_orthophoto.tif``. ODM builds
it from its DSM; we build a **true orthophoto by nadir-rasterising the refined
textured mesh** — the mesh is Effigies' quality lever, so the ortho inherits the
RefineMesh detail instead of being interpolated from a sparse cloud.

How it works:
  1. Read ``georef_transform.json``. The textured OBJ was already rewritten by
     georef_bridge into offset-subtracted projected coordinates (X=easting-offset,
     Y=northing-offset, Z=up), so its X/Y are metric and axis-aligned to the CRS.
     A local-only (un-georeferenced) result has no meaningful ortho -> skip.
  2. Rasterise every triangle top-down into a pixel grid at the chosen GSD,
     z-buffering on Z so the topmost surface wins (true nadir ortho), sampling the
     texture atlas via barycentric UVs.
  3. Write a 4-band (RGB + alpha) GeoTIFF, georeferenced by adding the offset back.

Dependencies: numpy, Pillow (texture), GDAL python bindings (osgeo) for the TIFF.
Pure-CPU; one Python loop over faces (fine for typical meshes, minutes for very
large ones — a later optimisation, noted in the ROADMAP).
"""
import argparse
import glob
import json
import math
import os
import sys

import numpy as np

# sibling module — resolve relative to this file so it works as a script and in tests
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from openmvs_mesh import find_mesh_obj  # noqa: E402


def _load_textured_obj(obj_path):
    """Parse an OBJ into (V[N,3], VT[M,2], faces[(vi,vti) x3], texture[H,W,3]).
    Returns None if the OBJ has no texture coordinates (cannot make an RGB ortho)."""
    from PIL import Image
    V, VT, faces = [], [], []
    mtllib = None
    with open(obj_path, "r", errors="ignore") as f:
        for line in f:
            if line.startswith("v "):
                p = line.split(); V.append((float(p[1]), float(p[2]), float(p[3])))
            elif line.startswith("vt "):
                p = line.split(); VT.append((float(p[1]), float(p[2])))
            elif line.startswith("f "):
                vi, vti = [], []
                for tok in line.split()[1:]:
                    a = tok.split("/")
                    vi.append(int(a[0]))
                    vti.append(int(a[1]) if len(a) > 1 and a[1] else 0)
                faces.append((vi, vti))
            elif line.startswith("mtllib"):
                mtllib = line.split(maxsplit=1)[1].strip()
    if not VT:
        return None
    V = np.asarray(V, dtype=np.float64)
    VT = np.asarray(VT, dtype=np.float64)
    base = os.path.dirname(obj_path)
    # texture: from the .mtl's map_Kd, else the first *map_Kd* image beside the OBJ
    tex_path = None
    mtl = os.path.join(base, mtllib) if mtllib else None
    if mtl and os.path.exists(mtl):
        for ln in open(mtl, errors="ignore"):
            if ln.strip().lower().startswith("map_kd"):
                tex_path = os.path.join(base, ln.split()[-1]); break
    if not tex_path or not os.path.exists(tex_path):
        cands = sorted(glob.glob(os.path.join(base, "*map_Kd*")))
        tex_path = cands[0] if cands else None
    if not tex_path:
        return None
    tex = np.asarray(Image.open(tex_path).convert("RGB"))
    # fan-triangulate any polygons into vertex/texcoord index triples
    tris_v, tris_vt = [], []
    nv, nvt = len(V), len(VT)
    for vi, vti in faces:
        vi = [(i - 1) if i > 0 else (nv + i) for i in vi]
        vti = [((i - 1) if i > 0 else (nvt + i)) if i != 0 else -1 for i in vti]
        for k in range(1, len(vi) - 1):
            tris_v.append((vi[0], vi[k], vi[k + 1]))
            tris_vt.append((vti[0], vti[k], vti[k + 1]))
    return V, VT, np.asarray(tris_v, np.int64), np.asarray(tris_vt, np.int64), tex


def rasterize(V, VT, TV, TVT, tex, gsd):
    """Nadir-rasterise the textured mesh at ground sample distance ``gsd`` (metres
    per pixel). Returns (rgb[H,W,3] uint8, alpha[H,W] uint8, xmin, ymax)."""
    xmin, ymin = V[:, 0].min(), V[:, 1].min()
    xmax, ymax = V[:, 0].max(), V[:, 1].max()
    W = max(1, int(math.ceil((xmax - xmin) / gsd)))
    H = max(1, int(math.ceil((ymax - ymin) / gsd)))
    rgb = np.zeros((H, W, 3), np.uint8)
    alpha = np.zeros((H, W), np.uint8)
    zbuf = np.full((H, W), -np.inf, np.float64)
    Ht, Wt = tex.shape[:2]

    # vertex -> pixel-centre coordinates (north up: row grows southward)
    px = (V[:, 0] - xmin) / gsd
    py = (ymax - V[:, 1]) / gsd
    for t in range(len(TV)):
        a, b, c = TV[t]
        x0, y0 = px[a], py[a]; x1, y1 = px[b], py[b]; x2, y2 = px[c], py[c]
        c0, c1 = int(math.floor(min(x0, x1, x2))), int(math.ceil(max(x0, x1, x2)))
        r0, r1 = int(math.floor(min(y0, y1, y2))), int(math.ceil(max(y0, y1, y2)))
        c0 = max(c0, 0); r0 = max(r0, 0); c1 = min(c1, W); r1 = min(r1, H)
        if c1 <= c0 or r1 <= r0:
            continue
        area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
        if abs(area) < 1e-12:
            continue
        gx, gy = np.meshgrid(np.arange(c0, c1) + 0.5, np.arange(r0, r1) + 0.5)
        l0 = ((x1 - gx) * (y2 - gy) - (x2 - gx) * (y1 - gy)) / area
        l1 = ((x2 - gx) * (y0 - gy) - (x0 - gx) * (y2 - gy)) / area
        l2 = 1.0 - l0 - l1
        inside = (l0 >= 0) & (l1 >= 0) & (l2 >= 0)
        if not inside.any():
            continue
        z = l0 * V[a, 2] + l1 * V[b, 2] + l2 * V[c, 2]
        sub = zbuf[r0:r1, c0:c1]
        win = inside & (z > sub)
        if not win.any():
            continue
        tva, tvb, tvc = TVT[t]
        if tva < 0 or tvb < 0 or tvc < 0:
            continue
        u = l0 * VT[tva, 0] + l1 * VT[tvb, 0] + l2 * VT[tvc, 0]
        v = l0 * VT[tva, 1] + l1 * VT[tvb, 1] + l2 * VT[tvc, 1]
        tx = np.clip((u * (Wt - 1)).astype(np.int64), 0, Wt - 1)
        ty = np.clip(((1.0 - v) * (Ht - 1)).astype(np.int64), 0, Ht - 1)  # OBJ v is bottom-up
        rr, cc = np.nonzero(win)
        rgb[r0 + rr, c0 + cc] = tex[ty[win], tx[win]]
        alpha[r0 + rr, c0 + cc] = 255
        sub[win] = z[win]
    return rgb, alpha, xmin, ymax


def write_geotiff(path, rgb, alpha, originx, originy, gsd, crs):
    from osgeo import gdal, osr
    H, W = alpha.shape
    drv = gdal.GetDriverByName("GTiff")
    ds = drv.Create(path, W, H, 4, gdal.GDT_Byte,
                    options=["COMPRESS=DEFLATE", "TILED=YES", "PHOTOMETRIC=RGB", "ALPHA=YES"])
    ds.SetGeoTransform([originx, gsd, 0.0, originy, 0.0, -gsd])
    srs = osr.SpatialReference()
    srs.SetFromUserInput(crs)
    ds.SetProjection(srs.ExportToWkt())
    for b in range(3):
        ds.GetRasterBand(b + 1).WriteArray(rgb[:, :, b])
    ds.GetRasterBand(4).WriteArray(alpha)
    ds.FlushCache(); ds = None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True, help="OpenMVS workdir")
    ap.add_argument("--resolution", default="auto",
                    help="ground sample distance in cm/px, or 'auto' (~4k px wide)")
    args = ap.parse_args()

    tr_path = os.path.join(args.work, "georef_transform.json")
    if not os.path.exists(tr_path):
        print("[ortho] no georef_transform.json; skipping orthophoto", file=sys.stderr)
        return
    tr = json.load(open(tr_path))
    crs = tr.get("crs")
    if not crs or str(crs).lower() == "local":
        print("[ortho] result is not georeferenced (crs=local); skipping orthophoto",
              file=sys.stderr)
        return
    offset = np.asarray(tr.get("offset", [0, 0, 0]), dtype=np.float64)

    name = find_mesh_obj(args.work)
    if not name or "texture" not in name:
        print("[ortho] no textured OBJ found; skipping orthophoto", file=sys.stderr)
        return
    parsed = _load_textured_obj(os.path.join(args.work, name))
    if parsed is None:
        print("[ortho] OBJ has no texture coordinates; skipping orthophoto", file=sys.stderr)
        return
    V, VT, TV, TVT, tex = parsed

    diag = math.hypot(V[:, 0].ptp(), V[:, 1].ptp())
    if str(args.resolution).lower() == "auto":
        gsd = min(max(diag / 4096.0, 0.01), 1.0)     # ~4k px wide, clamped 1cm..1m
    else:
        gsd = float(args.resolution) / 100.0          # cm/px -> m/px
    # cap raster size so a bad GSD cannot blow memory
    while (V[:, 0].ptp() / gsd) * (V[:, 1].ptp() / gsd) > 16000 * 16000:
        gsd *= 2.0

    rgb, alpha, xmin, ymax = rasterize(V, VT, TV, TVT, tex, gsd)
    out = os.path.join(args.work, "odm_orthophoto.tif")
    write_geotiff(out, rgb, alpha, offset[0] + xmin, offset[1] + ymax, gsd, crs)
    cov = 100.0 * (alpha > 0).mean()
    print(f"[ortho] wrote {os.path.basename(out)} "
          f"({rgb.shape[1]}x{rgb.shape[0]} px @ {gsd*100:.1f} cm/px, "
          f"{cov:.0f}% covered, crs={crs})")


if __name__ == "__main__":
    main()
