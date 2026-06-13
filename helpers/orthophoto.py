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
Pure-CPU; small triangles (the vast majority) are rasterised in batched numpy
passes per size class, large ones in a fallback loop.
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


def _parse_mtl(mtl_path):
    """-> {material_name: map_Kd path} from an .mtl file."""
    mats, cur = {}, None
    base = os.path.dirname(mtl_path)
    for ln in open(mtl_path, errors="ignore"):
        t = ln.strip()
        if t.lower().startswith("newmtl"):
            cur = t.split(maxsplit=1)[1].strip()
        elif t.lower().startswith("map_kd") and cur:
            mats[cur] = os.path.join(base, t.split()[-1])
    return mats


def _load_textured_obj(obj_path):
    """Parse an OBJ into (V[N,3], VT[M,2], TV, TVT, TM[per-tri material idx],
    textures[list of H,W,3 arrays]). MULTI-MATERIAL: large inputs make OpenMVS
    split the atlas into several pages (material_00, material_01, ...), each face
    group bound via `usemtl` — sampling everything from page 0 scrambles the
    output. Returns None if the OBJ has no texture coordinates."""
    from PIL import Image
    V, VT, faces = [], [], []
    mtllib = None
    cur_mat = 0
    mat_ids = {}                      # usemtl name -> texture slot
    with open(obj_path, "r", errors="ignore") as f:
        for line in f:
            if line.startswith("v "):
                p = line.split(); V.append((float(p[1]), float(p[2]), float(p[3])))
            elif line.startswith("vt "):
                p = line.split(); VT.append((float(p[1]), float(p[2])))
            elif line.startswith("usemtl"):
                name = line.split(maxsplit=1)[1].strip()
                cur_mat = mat_ids.setdefault(name, len(mat_ids))
            elif line.startswith("f "):
                vi, vti = [], []
                for tok in line.split()[1:]:
                    a = tok.split("/")
                    vi.append(int(a[0]))
                    vti.append(int(a[1]) if len(a) > 1 and a[1] else 0)
                faces.append((vi, vti, cur_mat))
            elif line.startswith("mtllib"):
                mtllib = line.split(maxsplit=1)[1].strip()
    if not VT:
        return None
    V = np.asarray(V, dtype=np.float64)
    VT = np.asarray(VT, dtype=np.float64)
    base = os.path.dirname(obj_path)
    # resolve each material's atlas page from the .mtl; fall back to the sorted
    # *map_Kd* files beside the OBJ (slot order == material_XX order)
    mtl = os.path.join(base, mtllib) if mtllib else None
    mat_tex = _parse_mtl(mtl) if (mtl and os.path.exists(mtl)) else {}
    fallback = sorted(glob.glob(os.path.join(base, "*map_Kd*")))
    if not mat_ids:                    # OBJ without usemtl: single implicit slot
        mat_ids = {"__default__": 0}
    textures = [None] * len(mat_ids)
    for name, slot in mat_ids.items():
        path = mat_tex.get(name)
        if (not path or not os.path.exists(path)) and slot < len(fallback):
            path = fallback[slot]
        if not path or not os.path.exists(path):
            return None
        textures[slot] = np.asarray(Image.open(path).convert("RGB"))
    # fan-triangulate any polygons into vertex/texcoord index triples
    tris_v, tris_vt, tris_m = [], [], []
    nv, nvt = len(V), len(VT)
    for vi, vti, m in faces:
        vi = [(i - 1) if i > 0 else (nv + i) for i in vi]
        vti = [((i - 1) if i > 0 else (nvt + i)) if i != 0 else -1 for i in vti]
        for k in range(1, len(vi) - 1):
            tris_v.append((vi[0], vi[k], vi[k + 1]))
            tris_vt.append((vti[0], vti[k], vti[k + 1]))
            tris_m.append(m)
    return (V, VT, np.asarray(tris_v, np.int64), np.asarray(tris_vt, np.int64),
            np.asarray(tris_m, np.int64), textures)


def rasterize(V, VT, TV, TVT, TM, textures, gsd):
    """Nadir-rasterise the textured mesh at ground sample distance ``gsd`` (metres
    per pixel). ``TM`` holds the per-triangle texture slot into ``textures``
    (multi-page atlas). Returns (rgb[H,W,3] uint8, alpha[H,W] uint8, xmin, ymax)."""
    xmin, ymin = V[:, 0].min(), V[:, 1].min()
    xmax, ymax = V[:, 0].max(), V[:, 1].max()
    W = max(1, int(math.ceil((xmax - xmin) / gsd)))
    H = max(1, int(math.ceil((ymax - ymin) / gsd)))
    rgb = np.zeros((H, W, 3), np.uint8)
    alpha = np.zeros((H, W), np.uint8)
    zbuf = np.full((H, W), -np.inf, np.float64)

    # vertex -> pixel-centre coordinates (north up: row grows southward)
    px = (V[:, 0] - xmin) / gsd
    py = (ymax - V[:, 1]) / gsd

    # Per-triangle precomputation, fully vectorised: corner coords, bboxes,
    # signed areas, validity cull. The per-triangle loop then runs only over
    # surviving triangles and reads plain Python floats (lists) — numpy scalar
    # indexing per iteration is what made the previous version ~3x slower.
    X = px[TV]; Y = py[TV]                       # [T,3] pixel coords
    Zt = V[:, 2][TV]                             # [T,3] heights
    C0 = np.maximum(np.floor(X.min(1)).astype(np.int64), 0)
    C1 = np.minimum(np.ceil(X.max(1)).astype(np.int64), W)
    R0 = np.maximum(np.floor(Y.min(1)).astype(np.int64), 0)
    R1 = np.minimum(np.ceil(Y.max(1)).astype(np.int64), H)
    AREA = ((X[:, 1] - X[:, 0]) * (Y[:, 2] - Y[:, 0])
            - (X[:, 2] - X[:, 0]) * (Y[:, 1] - Y[:, 0]))
    valid = (C1 > C0) & (R1 > R0) & (np.abs(AREA) > 1e-12) & (TVT.min(1) >= 0)
    survivors = np.nonzero(valid)[0]

    # --- batched path for small triangles (the vast majority at any sane GSD) --
    # Process all triangles whose bbox fits k x k in ONE vectorised pass per
    # (texture page, size class): barycentrics for the whole batch, candidate
    # pixels flattened, and the z-buffer conflict inside a batch resolved by a
    # lexsort picking, per pixel, the max z (ties: lowest triangle index — the
    # same winner the sequential loop produces with its strict z > test).
    Usafe = VT[:, 0][np.where(TVT >= 0, TVT, 0)]
    Vsafe = VT[:, 1][np.where(TVT >= 0, TVT, 0)]
    BW = C1 - C0; BH = R1 - R0
    zflat = zbuf.reshape(-1); rgbflat = rgb.reshape(-1, 3); aflat = alpha.reshape(-1)
    done = np.zeros(len(TV), bool)
    for k in (2, 4, 8, 16, 32):
        chunk = max(2000, int(3e8 // (48 * k * k)))      # ~300 MB working set
        sel_k = survivors[(BW[survivors] <= k) & (BH[survivors] <= k)
                          & ~done[survivors]]
        done[sel_k] = True
        dr = np.arange(k)[None, :, None]                  # [1,k,1] row offsets
        dc = np.arange(k)[None, None, :]                  # [1,1,k] col offsets
        for m in range(len(textures)):
            sel_m = sel_k[TM[sel_k] == m]
            tex = textures[m]; Ht, Wt = tex.shape[:2]
            for s in range(0, len(sel_m), chunk):
                tt = sel_m[s:s + chunk]
                if not len(tt):
                    continue
                r0 = R0[tt][:, None, None]; c0 = C0[tt][:, None, None]
                rr = r0 + dr; cc = c0 + dc                # [B,k,k]
                inwin = (rr < R1[tt][:, None, None]) & (cc < C1[tt][:, None, None])
                gx = cc + 0.5; gy = rr + 0.5
                x0 = X[tt, 0][:, None, None]; x1 = X[tt, 1][:, None, None]; x2 = X[tt, 2][:, None, None]
                y0 = Y[tt, 0][:, None, None]; y1 = Y[tt, 1][:, None, None]; y2 = Y[tt, 2][:, None, None]
                ar = AREA[tt][:, None, None]
                l0 = ((x1 - gx) * (y2 - gy) - (x2 - gx) * (y1 - gy)) / ar
                l1 = ((x2 - gx) * (y0 - gy) - (x0 - gx) * (y2 - gy)) / ar
                l2 = 1.0 - l0 - l1
                cand = inwin & (l0 >= 0) & (l1 >= 0) & (l2 >= 0)
                if not cand.any():
                    continue
                z = (l0 * Zt[tt, 0][:, None, None] + l1 * Zt[tt, 1][:, None, None]
                     + l2 * Zt[tt, 2][:, None, None])
                u = (l0 * Usafe[tt, 0][:, None, None] + l1 * Usafe[tt, 1][:, None, None]
                     + l2 * Usafe[tt, 2][:, None, None])
                v = (l0 * Vsafe[tt, 0][:, None, None] + l1 * Vsafe[tt, 1][:, None, None]
                     + l2 * Vsafe[tt, 2][:, None, None])
                pix = (rr * W + cc)[cand]
                zc = z[cand]
                tric = np.broadcast_to(np.arange(len(tt))[:, None, None], cand.shape)[cand]
                # per-pixel winner: sort by (pix, z asc, tri desc) -> last of each
                # pix run = max z, ties resolved to the LOWEST triangle index
                order = np.lexsort((-tric, zc, pix))
                pix_o = pix[order]
                last = np.r_[pix_o[1:] != pix_o[:-1], True]
                wsel = order[last]
                beats = zc[wsel] > zflat[pix[wsel]]
                wsel = wsel[beats]
                if not len(wsel):
                    continue
                p = pix[wsel]
                tx = np.clip((u[cand][wsel] * (Wt - 1)).astype(np.int64), 0, Wt - 1)
                ty = np.clip(((1.0 - v[cand][wsel]) * (Ht - 1)).astype(np.int64), 0, Ht - 1)
                zflat[p] = zc[wsel]
                rgbflat[p] = tex[ty, tx]
                aflat[p] = 255
    survivors = survivors[~done[survivors]]               # big triangles -> loop
    # texcoords per corner (rows for invalid tris contain garbage; never read)
    Usafe = VT[:, 0][np.where(TVT >= 0, TVT, 0)]
    Vsafe = VT[:, 1][np.where(TVT >= 0, TVT, 0)]
    Xl, Yl, Zl = X.tolist(), Y.tolist(), Zt.tolist()
    Ul, Vl = Usafe.tolist(), Vsafe.tolist()
    C0l, C1l, R0l, R1l = C0.tolist(), C1.tolist(), R0.tolist(), R1.tolist()
    AREAl, TMl = AREA.tolist(), TM.tolist()
    # pixel-centre coordinate axes, sliced per bbox instead of meshgrid-per-tri
    colc = np.arange(W, dtype=np.float64) + 0.5
    rowc = np.arange(H, dtype=np.float64) + 0.5
    tex_dims = [(t.shape[0], t.shape[1]) for t in textures]

    for t in survivors.tolist():
        x0, x1, x2 = Xl[t]; y0, y1, y2 = Yl[t]
        c0, c1, r0, r1 = C0l[t], C1l[t], R0l[t], R1l[t]
        area = AREAl[t]
        gx = colc[c0:c1][None, :]                # [1,w] broadcasts against [h,1]
        gy = rowc[r0:r1][:, None]
        l0 = ((x1 - gx) * (y2 - gy) - (x2 - gx) * (y1 - gy)) / area
        l1 = ((x2 - gx) * (y0 - gy) - (x0 - gx) * (y2 - gy)) / area
        l2 = 1.0 - l0 - l1
        inside = (l0 >= 0) & (l1 >= 0) & (l2 >= 0)
        if not inside.any():
            continue
        z0, z1, z2 = Zl[t]
        z = l0 * z0 + l1 * z1 + l2 * z2
        sub = zbuf[r0:r1, c0:c1]
        win = inside & (z > sub)
        if not win.any():
            continue
        tex = textures[TMl[t]]                   # this triangle's atlas page
        Ht, Wt = tex_dims[TMl[t]]
        u0, u1, u2 = Ul[t]; v0, v1, v2 = Vl[t]
        u = l0 * u0 + l1 * u1 + l2 * u2
        v = l0 * v0 + l1 * v1 + l2 * v2
        tx = np.clip((u * (Wt - 1)).astype(np.int64), 0, Wt - 1)
        ty = np.clip(((1.0 - v) * (Ht - 1)).astype(np.int64), 0, Ht - 1)  # OBJ v bottom-up
        rr, cc = np.nonzero(win)
        rgb[r0 + rr, c0 + cc] = tex[ty[win], tx[win]]
        alpha[r0 + rr, c0 + cc] = 255
        sub[win] = z[win]
    return rgb, alpha, xmin, ymax, zbuf


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


def write_dem_geotiff(path, zbuf, originx, originy, gsd, crs, nodata=-9999.0):
    """Write the per-pixel surface-height grid (`zbuf`, absolute elevations) as a
    single-band Float32 DSM GeoTIFF — same geotransform/CRS as the orthophoto.
    Uncovered pixels (zbuf == -inf) become `nodata`."""
    from osgeo import gdal, osr
    H, W = zbuf.shape
    dem = np.where(np.isneginf(zbuf), nodata, zbuf).astype(np.float32)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    drv = gdal.GetDriverByName("GTiff")
    ds = drv.Create(path, W, H, 1, gdal.GDT_Float32,
                    options=["COMPRESS=DEFLATE", "TILED=YES"])
    ds.SetGeoTransform([originx, gsd, 0.0, originy, 0.0, -gsd])
    srs = osr.SpatialReference()
    srs.SetFromUserInput(crs)
    ds.SetProjection(srs.ExportToWkt())
    band = ds.GetRasterBand(1)
    band.SetNoDataValue(nodata)
    band.WriteArray(dem)
    ds.FlushCache(); ds = None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True, help="OpenMVS workdir")
    ap.add_argument("--resolution", default="auto",
                    help="ground sample distance in cm/px, or 'auto' (~4k px wide)")
    ap.add_argument("--skip-orthophoto", action="store_true",
                    help="do not write the RGB orthophoto")
    ap.add_argument("--skip-dsm", action="store_true",
                    help="do not write the DSM (digital surface model)")
    args = ap.parse_args()

    if args.skip_orthophoto and args.skip_dsm:
        return

    tr_path = os.path.join(args.work, "georef_transform.json")
    if not os.path.exists(tr_path):
        print("[ortho] no georef_transform.json; skipping orthophoto/DSM", file=sys.stderr)
        return
    tr = json.load(open(tr_path))
    crs = tr.get("crs")
    if not crs or str(crs).lower() == "local":
        print("[ortho] result is not georeferenced (crs=local); skipping orthophoto/DSM",
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
    V, VT, TV, TVT, TM, textures = parsed

    # np.ptp as a function — the ndarray .ptp() METHOD was removed in numpy 2.x
    ext_x = float(np.ptp(V[:, 0])); ext_y = float(np.ptp(V[:, 1]))
    diag = math.hypot(ext_x, ext_y)
    if str(args.resolution).lower() == "auto":
        gsd = min(max(diag / 4096.0, 0.01), 1.0)     # ~4k px wide, clamped 1cm..1m
    else:
        gsd = float(args.resolution) / 100.0          # cm/px -> m/px
    # cap raster size so a bad GSD cannot blow memory
    while (ext_x / gsd) * (ext_y / gsd) > 16000 * 16000:
        gsd *= 2.0

    rgb, alpha, xmin, ymax, zbuf = rasterize(V, VT, TV, TVT, TM, textures, gsd)
    ox, oy = offset[0] + xmin, offset[1] + ymax
    cov = 100.0 * (alpha > 0).mean()

    if not args.skip_orthophoto:
        out = os.path.join(args.work, "odm_orthophoto.tif")
        write_geotiff(out, rgb, alpha, ox, oy, gsd, crs)
        print(f"[ortho] wrote {os.path.basename(out)} "
              f"({rgb.shape[1]}x{rgb.shape[0]} px @ {gsd*100:.1f} cm/px, "
              f"{cov:.0f}% covered, crs={crs})")

    if not args.skip_dsm:
        # The z-buffer the rasteriser already computed IS the DSM: per-pixel
        # topmost surface elevation (absolute, inherits RefineMesh detail).
        dsm_out = os.path.join(args.work, "odm_dem", "dsm.tif")
        write_dem_geotiff(dsm_out, zbuf, ox, oy, gsd, crs)
        finite = zbuf[np.isfinite(zbuf)]
        rng = (f"elev {finite.min():.1f}..{finite.max():.1f} m"
               if finite.size else "no covered pixels")
        print(f"[ortho] wrote odm_dem/dsm.tif "
              f"({zbuf.shape[1]}x{zbuf.shape[0]} px @ {gsd*100:.1f} cm/px, {rng}, crs={crs})")


if __name__ == "__main__":
    main()
