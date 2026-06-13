#!/usr/bin/env python3
"""
Multi-view blended texturing — the Metashape-class texture step.

OpenMVS's TextureMesh assigns each face ONE source view; on homogeneous surfaces
(roof planes) the per-view character (exposure, sharpness, view angle) shows as
blotches between patches. This step keeps TextureMesh's atlas LAYOUT (charts,
uv mapping) and re-bakes the CONTENT: every texel is projected through its 3D
position into the best few cameras and the (harmonised, undistorted) images are
blended with geometry-derived weights:

  1. Read the undistorted PINHOLE model (dense/sparse, binary) and the mesh.
  2. Render a depth map per view from the mesh (batched z-buffer rasteriser,
     downscaled) for occlusion tests — never blend through walls.
  3. Per face: project the centroid into every view; keep views that face the
     surface, contain the point, and pass the depth test; weight by
     cos²(view angle)/distance² and keep the top K.
  4. Per atlas texel of the face (barycentric 3D position): bilinear-sample the
     K views and write the weighted mean. Texels with no valid view keep the
     TextureMesh original.

Runs AFTER TextureMesh and BEFORE seam leveling (which then only mops up the
small residual chart borders), in the LOCAL frame (before georeferencing).
Non-fatal upstream by contract.
"""
import argparse
import os
import resource
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from openmvs_mesh import find_mesh_obj          # noqa: E402
from seam_level import parse_obj_arrays         # noqa: E402
from colmap_bin import read_cameras_bin, read_images_bin  # noqa: E402

TOP_K = 4                 # views blended per face
MIN_COS = 0.15            # grazing-angle cutoff (normal · view direction)
DEPTH_SCALE = 4           # depth maps at 1/4 image resolution
DEPTH_TOL_REL = 0.01      # visibility: z <= depth * (1 + tol) + abs tol
DEPTH_TOL_ABS = 0.05
FRAME_MARGIN = 4.0        # px margin inside the image frame
WEIGHT_FLOOR = 0.15       # drop views weaker than this fraction of the best


def _log_rss(tag):
    """Peak-RSS probe, gated on EFFIGIES_BLEND_RSS (the v0.5.0 memory-ceiling
    instrument). ru_maxrss is KB on Linux, bytes on macOS — report the raw value
    and a KB-assuming MB so the slope across runs is readable. No-op when unset."""
    if not os.environ.get("EFFIGIES_BLEND_RSS"):
        return
    kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(f"[blend] peak RSS @ {tag}: {kb} ru_maxrss (~{kb / 1024:.0f} MB if KB)",
          file=sys.stderr)


def render_depth(V, FV, K, R, t, W, H):
    """z-buffer depth map of the mesh from one PINHOLE view (downscaled)."""
    fx, fy, cx, cy = K
    Xc = V @ R.T + t
    z = Xc[:, 2]
    u = np.where(z > 1e-6, fx * Xc[:, 0] / np.maximum(z, 1e-6) + cx, -1e9) / DEPTH_SCALE
    v = np.where(z > 1e-6, fy * Xc[:, 1] / np.maximum(z, 1e-6) + cy, -1e9) / DEPTH_SCALE
    w, h = W // DEPTH_SCALE, H // DEPTH_SCALE
    depth = np.full((h, w), np.inf, np.float32)

    X = u[FV]; Y = v[FV]; Z = z[FV]
    ok = (Z > 1e-6).all(1)
    C0 = np.maximum(np.floor(X.min(1)).astype(np.int64), 0)
    C1 = np.minimum(np.ceil(X.max(1)).astype(np.int64) + 1, w)
    R0 = np.maximum(np.floor(Y.min(1)).astype(np.int64), 0)
    R1 = np.minimum(np.ceil(Y.max(1)).astype(np.int64) + 1, h)
    AREA = ((X[:, 1] - X[:, 0]) * (Y[:, 2] - Y[:, 0])
            - (X[:, 2] - X[:, 0]) * (Y[:, 1] - Y[:, 0]))
    ok &= (C1 > C0) & (R1 > R0) & (np.abs(AREA) > 1e-12)
    dflat = depth.reshape(-1)
    BW, BH = C1 - C0, R1 - R0
    done = ~ok
    for k in (4, 8, 16, 32):
        grp = np.nonzero(~done & (BW <= k) & (BH <= k))[0]
        done[grp] = True
        if not len(grp):
            continue
        chunk = max(2000, int(2e8 // (32 * k * k)))
        dr = np.arange(k)[None, :, None]; dc = np.arange(k)[None, None, :]
        for s in range(0, len(grp), chunk):
            g = grp[s:s + chunk]
            r0 = R0[g][:, None, None]; c0 = C0[g][:, None, None]
            rr = r0 + dr; cc = c0 + dc
            inwin = (rr < R1[g][:, None, None]) & (cc < C1[g][:, None, None])
            gx = cc + 0.5; gy = rr + 0.5
            x0 = X[g, 0][:, None, None]; x1 = X[g, 1][:, None, None]; x2 = X[g, 2][:, None, None]
            y0 = Y[g, 0][:, None, None]; y1 = Y[g, 1][:, None, None]; y2 = Y[g, 2][:, None, None]
            ar = AREA[g][:, None, None]
            l0 = ((x1 - gx) * (y2 - gy) - (x2 - gx) * (y1 - gy)) / ar
            l1 = ((x2 - gx) * (y0 - gy) - (x0 - gx) * (y2 - gy)) / ar
            l2 = 1.0 - l0 - l1
            cand = inwin & (l0 >= 0) & (l1 >= 0) & (l2 >= 0)
            if not cand.any():
                continue
            zz = (l0 * Z[g, 0][:, None, None] + l1 * Z[g, 1][:, None, None]
                  + l2 * Z[g, 2][:, None, None])
            pix = (rr * w + cc)[cand]
            np.minimum.at(dflat, pix, zz[cand].astype(np.float32))
    # large faces (rare) intentionally skipped: tolerance covers small gaps
    return depth


def _topk_insert(top_idx, top_w, w, vi):
    """Streaming running-top-K update, in place. For each face, if this view's
    weight ``w[face]`` exceeds the face's current weakest kept weight, it replaces
    that slot. ``w`` is [nF] (0 where the face is not visible in view ``vi``);
    ``top_idx`` [nF,K] int32 (init -1), ``top_w`` [nF,K] float32 (init 0). Strict
    ``>`` plus the 0-init means empty slots (weight 0) are filled by any positive
    weight, and a non-visible face (w=0) never displaces anything. Replaces the
    old dense [nF,nV] weight matrix — memory is O(nF·K), flat in the view count."""
    cmin = top_w.min(1)
    repl = w > cmin
    if not repl.any():
        return
    rows = np.nonzero(repl)[0]
    cols = top_w.argmin(1)[rows]
    top_w[rows, cols] = w[rows]
    top_idx[rows, cols] = vi


def _finalize_topk(top_idx, top_w):
    """Sort each face's K kept views by descending weight (matching the old
    ``argsort`` order, so the downstream bake sums views in the same order), drop
    views weaker than WEIGHT_FLOOR·best, and renormalise to sum 1. Returns
    (top_idx [nF,K] int32, tw [nF,K] float32)."""
    order = np.argsort(-top_w, axis=1)
    top_idx = np.take_along_axis(top_idx, order, axis=1)
    top_w = np.take_along_axis(top_w, order, axis=1)
    best = top_w[:, :1]
    tw = np.where(top_w >= WEIGHT_FLOOR * np.maximum(best, 1e-12), top_w, 0.0)
    s = tw.sum(1, keepdims=True)
    tw = np.where(s > 0, tw / np.maximum(s, 1e-12), 0.0)
    return top_idx, tw.astype(np.float32)


def select_views(V, FV, names, poses, cam_of, cams):
    """Per face: top-K view indices + normalised weights ([F,K] each).

    Streams the views in a single pass: each view's depth map is rendered on the
    fly for the visibility test and immediately discarded, and a running top-K
    (``_topk_insert``) replaces the old dense [faces×views] weight matrix and the
    all-depth-maps list. Peak memory is therefore O(nF·K + one depth map), flat in
    the number of views. The result (top-K set + normalised weights) is identical
    to the old argsort path."""
    Fc = V[FV].mean(1)                                   # centroids
    e1 = V[FV[:, 1]] - V[FV[:, 0]]
    e2 = V[FV[:, 2]] - V[FV[:, 0]]
    N = np.cross(e1, e2)
    N /= np.maximum(np.linalg.norm(N, axis=1)[:, None], 1e-12)

    nF = len(FV)
    top_idx = np.full((nF, TOP_K), -1, np.int32)
    top_w = np.zeros((nF, TOP_K), np.float32)
    for vi, name in enumerate(names):
        R, t = poses[name]["R"], poses[name]["t"]
        _, Wd, Hd, K = cams[cam_of[name]]
        fx, fy, cx, cy = K
        C = -R.T @ t
        d = C[None, :] - Fc
        dist = np.linalg.norm(d, axis=1)
        dirn = d / np.maximum(dist[:, None], 1e-12)
        cos = (N * dirn).sum(1)
        Xc = Fc @ R.T + t
        z = Xc[:, 2]
        ok = (cos > MIN_COS) & (z > 1e-6)
        u = fx * Xc[:, 0] / np.maximum(z, 1e-6) + cx
        v = fy * Xc[:, 1] / np.maximum(z, 1e-6) + cy
        ok &= (u >= FRAME_MARGIN) & (u < Wd - FRAME_MARGIN) \
            & (v >= FRAME_MARGIN) & (v < Hd - FRAME_MARGIN)
        if ok.any():
            # render this view's depth map on the fly for the visibility test,
            # then discard it (no all-views `depths` list held in memory)
            dm = render_depth(V, FV, K, R, t, Wd, Hd)
            du = np.clip((u / DEPTH_SCALE).astype(np.int64), 0, dm.shape[1] - 1)
            dv = np.clip((v / DEPTH_SCALE).astype(np.int64), 0, dm.shape[0] - 1)
            vis = z <= dm[dv, du] * (1 + DEPTH_TOL_REL) + DEPTH_TOL_ABS
            ok &= vis
        w = np.zeros(nF, np.float32)
        w[ok] = (np.maximum(cos[ok], 0.0) ** 2
                 / np.maximum(dist[ok], 1e-6) ** 2)
        _topk_insert(top_idx, top_w, w, vi)

    return _finalize_topk(top_idx, top_w)


def bilinear(img, u, v):
    """Bilinear sample img[H,W,3] (uint8) at float pixel coords -> float32."""
    H, W = img.shape[:2]
    u = np.clip(u, 0.0, W - 1.001); v = np.clip(v, 0.0, H - 1.001)
    x0 = u.astype(np.int64); y0 = v.astype(np.int64)
    fx = (u - x0)[..., None]; fy = (v - y0)[..., None]
    p00 = img[y0, x0].astype(np.float32)
    p01 = img[y0, x0 + 1].astype(np.float32)
    p10 = img[y0 + 1, x0].astype(np.float32)
    p11 = img[y0 + 1, x0 + 1].astype(np.float32)
    return (p00 * (1 - fx) * (1 - fy) + p01 * fx * (1 - fy)
            + p10 * (1 - fx) * fy + p11 * fx * fy)


def blend(work):
    from PIL import Image
    _log_rss("entry")
    obj_name = find_mesh_obj(work)
    if not obj_name or "texture" not in obj_name:
        print("[blend] no textured OBJ; skipping", file=sys.stderr)
        return False
    sparse_bin = os.path.join(work, "dense", "sparse")
    img_dir = os.path.join(work, "dense", "images")
    if not (os.path.exists(os.path.join(sparse_bin, "cameras.bin"))
            and os.path.isdir(img_dir)):
        print("[blend] no undistorted workspace; skipping", file=sys.stderr)
        return False

    V, VT, FV, FVT, FM, tex_paths = parse_obj_arrays(os.path.join(work, obj_name))
    if len(FV) == 0 or any(p is None for p in tex_paths):
        print("[blend] mesh/atlas incomplete; skipping", file=sys.stderr)
        return False
    raw = read_cameras_bin(os.path.join(sparse_bin, "cameras.bin"))
    poses = read_images_bin(os.path.join(sparse_bin, "images.bin"))
    cams = {cid: (m, w, h, (p[0], p[1], p[2], p[3]) if m == "PINHOLE"
                  else (p[0], p[0], p[1], p[2]))
            for cid, (m, w, h, p) in raw.items()}
    names = sorted(n for n in poses
                   if os.path.exists(os.path.join(img_dir, n)))
    if len(names) < 2:
        print("[blend] fewer than two registered views; skipping", file=sys.stderr)
        return False
    cam_of = {n: poses[n]["camera_id"] for n in names}

    # Streaming top-K view selection (depth maps rendered on the fly inside, then
    # discarded — no all-views depth list, no dense [faces×views] weight matrix).
    print(f"[blend] selecting top-{TOP_K} views over {len(names)} cameras "
          f"(streaming, depth at 1/{DEPTH_SCALE} res) ...")
    top, tw = select_views(V, FV, names, poses, cam_of, cams)
    covered = (tw.sum(1) > 0)
    print(f"[blend] view selection: {covered.mean() * 100:.1f}% of faces have "
          f"valid views (top-{TOP_K})")
    _log_rss("after view selection")

    print(f"[blend] loading {len(names)} undistorted images ...")
    imgs = [np.asarray(Image.open(os.path.join(img_dir, n)).convert("RGB"))
            for n in names]

    # bake per page, per bbox size class (same batching as the rasterisers)
    for m, tp in enumerate(tex_paths):
        fsel = np.nonzero((FM == m) & (FVT >= 0).all(1) & covered)[0]
        if not len(fsel):
            continue
        tex = np.asarray(Image.open(tp).convert("RGB"), dtype=np.float32)
        H, W = tex.shape[:2]
        uv = VT[FVT[fsel]]
        xs = uv[:, :, 0] * (W - 1)
        ys = (1.0 - uv[:, :, 1]) * (H - 1)
        C0 = np.maximum(np.floor(xs.min(1)).astype(np.int64), 0)
        C1 = np.minimum(np.ceil(xs.max(1)).astype(np.int64) + 1, W)
        R0 = np.maximum(np.floor(ys.min(1)).astype(np.int64), 0)
        R1 = np.minimum(np.ceil(ys.max(1)).astype(np.int64) + 1, H)
        AREA = ((xs[:, 1] - xs[:, 0]) * (ys[:, 2] - ys[:, 0])
                - (xs[:, 2] - xs[:, 0]) * (ys[:, 1] - ys[:, 0]))
        ok = (C1 > C0) & (R1 > R0) & (np.abs(AREA) > 1e-9)
        acc = np.zeros((H * W, 3), np.float32)
        wgt = np.zeros(H * W, np.float32)
        Vf = V[FV[fsel]]                                  # [B,3corners,3]
        topf, twf = top[fsel], tw[fsel]
        BW, BH = C1 - C0, R1 - R0
        done = ~ok
        for k in (2, 4, 8, 16, 32, 64):
            grp = np.nonzero(~done & (BW <= k) & (BH <= k))[0]
            done[grp] = True
            if not len(grp):
                continue
            chunk = max(1000, int(2e8 // (60 * k * k)))
            dr = np.arange(k)[None, :, None]; dc = np.arange(k)[None, None, :]
            for s in range(0, len(grp), chunk):
                g = grp[s:s + chunk]
                r0 = R0[g][:, None, None]; c0 = C0[g][:, None, None]
                rr = r0 + dr; cc = c0 + dc
                inwin = (rr < R1[g][:, None, None]) & (cc < C1[g][:, None, None])
                gx = cc + 0.5; gy = rr + 0.5
                x0 = xs[g, 0][:, None, None]; x1 = xs[g, 1][:, None, None]; x2 = xs[g, 2][:, None, None]
                y0 = ys[g, 0][:, None, None]; y1 = ys[g, 1][:, None, None]; y2 = ys[g, 2][:, None, None]
                ar = AREA[g][:, None, None]
                l0 = ((x1 - gx) * (y2 - gy) - (x2 - gx) * (y1 - gy)) / ar
                l1 = ((x2 - gx) * (y0 - gy) - (x0 - gx) * (y2 - gy)) / ar
                l2 = 1.0 - l0 - l1
                eps = -0.02
                cand = inwin & (l0 >= eps) & (l1 >= eps) & (l2 >= eps)
                if not cand.any():
                    continue
                # 3D position of every candidate texel
                P = (l0[..., None] * Vf[g, None, None, 0, :]
                     + l1[..., None] * Vf[g, None, None, 1, :]
                     + l2[..., None] * Vf[g, None, None, 2, :])
                pix = (rr * W + cc)[cand]
                Pc = P[cand]                              # [T,3]
                # face index per candidate texel (within g)
                fidx = np.broadcast_to(np.arange(len(g))[:, None, None],
                                       cand.shape)[cand]
                col = np.zeros((len(Pc), 3), np.float32)
                wsum = np.zeros(len(Pc), np.float32)
                for kk in range(TOP_K):
                    vids = topf[g][fidx, kk]
                    ws = twf[g][fidx, kk]
                    active = ws > 0
                    if not active.any():
                        continue
                    for vid in np.unique(vids[active]):
                        sel = active & (vids == vid)
                        n = names[vid]
                        Rv, tv = poses[n]["R"], poses[n]["t"]
                        _, Wd, Hd, K = cams[cam_of[n]]
                        fx, fy, cx, cy = K
                        Xc = Pc[sel] @ Rv.T + tv
                        z = np.maximum(Xc[:, 2], 1e-6)
                        u = fx * Xc[:, 0] / z + cx
                        v = fy * Xc[:, 1] / z + cy
                        smp = bilinear(imgs[vid], u, v)
                        col[sel] += ws[sel, None] * smp
                        wsum[sel] += ws[sel]
                good = wsum > 1e-6
                np.add.at(acc, pix[good],
                          col[good] / wsum[good, None])
                np.add.at(wgt, pix[good], 1.0)
        covered_px = wgt > 0
        out = tex.reshape(-1, 3).copy()
        out[covered_px] = acc[covered_px] / wgt[covered_px, None]
        Image.fromarray(np.clip(out.reshape(H, W, 3), 0, 255)
                        .astype(np.uint8)).save(tp, quality=95)
        print(f"[blend] page {m}: {covered_px.mean() * 100:.0f}% of texels re-baked")
        del tex, acc, wgt
    print(f"[blend] multi-view blend complete (top-{TOP_K}, cos²/d² weights)")
    _log_rss("exit")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True, help="OpenMVS workdir")
    args = ap.parse_args()
    blend(args.work)


if __name__ == "__main__":
    main()
