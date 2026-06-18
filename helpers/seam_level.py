#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Patrick Leiverkus
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Seam leveling for the textured mesh — our own, since OpenMVS's is corrupted.

Even after per-image photometric harmonisation, adjacent texture patches can
differ slightly (view-dependent reflectance, residual vignetting): on homogeneous
surfaces (roof planes!) every remaining step between patches is visible. This
step equalises the colours ACROSS patch seams and diffuses the adjustment
smoothly into the patch interiors — the mvs-texturing "seam leveling" idea,
implemented on our own data structures:

  1. Patches = texture-connected face components (faces sharing a (v, vt) edge).
     A 3D vertex used by several patches lies ON a seam.
  2. At each seam vertex, sample the atlas colour per patch (small disc around
     its uv). Target = mean over the patches -> per-(vertex, patch) correction.
  3. Propagate: solve a screened-Poisson system over the (vertex, patch) graph
     (data term at seam vertices, smoothness along mesh edges) with scipy CG —
     corrections fade smoothly into the interior instead of stopping at the seam.
  4. Bake: rasterise each face's barycentrically-interpolated vertex corrections
     into its atlas page (additive, per RGB channel, clamped; pages processed
     sequentially to bound memory).

Everything index-heavy is numpy (packed integer keys + np.unique / sorting) —
a 2M-face mesh must fit comfortably in RAM. In-place on
scene_dense_mesh_refine_texture.* (OBJ + atlas pages). Non-fatal upstream.
"""
import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from openmvs_mesh import find_mesh_obj  # noqa: E402
from orthophoto import _parse_mtl       # noqa: E402

SAMPLE_RADIUS = 1        # px disc around the inset sample point
SMOOTH_LAMBDA = 0.15     # data weight at seams vs smoothness; the interior is
                         # harmonic infill either way (no data there), so seams
                         # get matched and corrections blend across the patch
MAX_CORRECTION = 60.0    # clamp per-channel additive correction (8-bit units)
CG_TOL = 1e-3


def parse_obj_arrays(path):
    """-> V[N,3], VT[M,2], FV[F,3], FVT[F,3], FM[F], tex_paths (triangles only)."""
    import glob as _glob
    V, VT, fv, fvt, fm, mtllib = [], [], [], [], [], None
    cur_mat, mat_ids = 0, {}
    with open(path, "r", errors="ignore") as f:
        for line in f:
            if line.startswith("v "):
                p = line.split(); V.append((float(p[1]), float(p[2]), float(p[3])))
            elif line.startswith("vt "):
                p = line.split(); VT.append((float(p[1]), float(p[2])))
            elif line.startswith("usemtl"):
                cur_mat = mat_ids.setdefault(line.split(maxsplit=1)[1].strip(),
                                             len(mat_ids))
            elif line.startswith("f "):
                toks = line.split()[1:]
                if len(toks) != 3:
                    continue                      # OpenMVS emits pure triangles
                a = [t.split("/") for t in toks]
                fv.append([int(x[0]) - 1 for x in a])
                fvt.append([int(x[1]) - 1 if len(x) > 1 and x[1] else -1 for x in a])
                fm.append(cur_mat)
            elif line.startswith("mtllib"):
                mtllib = line.split(maxsplit=1)[1].strip()
    base = os.path.dirname(path)
    mtl = os.path.join(base, mtllib) if mtllib else None
    mat_tex = _parse_mtl(mtl) if (mtl and os.path.exists(mtl)) else {}
    fallback = sorted(_glob.glob(os.path.join(base, "*map_Kd*")))
    if not mat_ids:
        mat_ids = {"__default__": 0}
    tex_paths = [None] * len(mat_ids)
    for name, slot in mat_ids.items():
        p = mat_tex.get(name)
        if (not p or not os.path.exists(p)) and slot < len(fallback):
            p = fallback[slot]
        tex_paths[slot] = p
    return (np.asarray(V, np.float64), np.asarray(VT, np.float64),
            np.asarray(fv, np.int64), np.asarray(fvt, np.int64),
            np.asarray(fm, np.int64), tex_paths)


def texture_patches(FV, FVT, VT, FM):
    """Texture-connected components, fully vectorised union-find. OpenMVS does
    NOT share vt indices between faces, so identity must be by VALUE: two faces
    are texture-connected when they share a mesh edge whose corners map to the
    same atlas position (same page, uv equal after quantisation finer than an
    atlas pixel)."""
    F = len(FV)
    Q = 1 << 14                                       # finer than 8192-px atlas
    uv = VT[np.maximum(FVT, 0)]                       # [F,3,2]
    qu = np.clip((uv[:, :, 0] * Q).round().astype(np.int64), 0, Q)
    qv = np.clip((uv[:, :, 1] * Q).round().astype(np.int64), 0, Q)
    n_mats = int(FM.max()) + 1
    corner = (((FV * (Q + 1) + qu) * (Q + 1) + qv) * n_mats
              + FM[:, None])                          # packed (v, uv, page)
    e_a = corner
    e_b = np.roll(corner, -1, axis=1)
    lo = np.minimum(e_a, e_b).ravel()
    hi = np.maximum(e_a, e_b).ravel()
    face_of = np.repeat(np.arange(F), 3)
    order = np.lexsort((hi, lo))
    lo, hi, face_of = lo[order], hi[order], face_of[order]
    same = (lo[1:] == lo[:-1]) & (hi[1:] == hi[:-1])  # consecutive = shared edge

    parent = np.arange(F)

    def find(a):
        root = a
        while parent[root] != root:
            root = parent[root]
        while parent[a] != root:
            parent[a], a = root, parent[a]
        return root

    for i in np.nonzero(same)[0]:
        ra, rb = find(face_of[i]), find(face_of[i + 1])
        if ra != rb:
            parent[rb] = ra
    roots = np.fromiter((find(i) for i in range(F)), np.int64, F)
    _, patch = np.unique(roots, return_inverse=True)
    return patch


def solve_and_bake(work_obj):
    from PIL import Image
    from scipy.sparse import coo_matrix, identity
    from scipy.sparse.linalg import cg

    V, VT, FV, FVT, FM, tex_paths = parse_obj_arrays(work_obj)
    if len(VT) == 0 or any(p is None for p in tex_paths) or len(FV) == 0:
        print("[seams] no texture coordinates/atlas; skipping", file=sys.stderr)
        return False
    patch = texture_patches(FV, FVT, VT, FM)
    n_patches = int(patch.max()) + 1

    # (vertex, patch) nodes — packed keys + np.unique, no python dicts
    pk = FV * n_patches + patch[:, None]              # [F,3] packed node keys
    uniq, corner_node = np.unique(pk.ravel(), return_inverse=True)
    corner_node = corner_node.reshape(-1, 3)          # node id per face corner
    n_nodes = len(uniq)
    node_v = uniq // n_patches

    # representative uv + page per node (first corner occurrence)
    flatc = corner_node.ravel()
    first = np.full(n_nodes, np.iinfo(np.int64).max, np.int64)
    np.minimum.at(first, flatc, np.arange(len(flatc)))
    fidx, cidx = first // 3, first % 3
    node_vt = FVT[fidx, cidx]
    node_pg = FM[fidx]
    has_uv = node_vt >= 0
    node_uv = np.zeros((n_nodes, 2))
    node_uv[has_uv] = VT[node_vt[has_uv]]
    # sample INSET toward the face centroid: seam vertices sit on the chart
    # border in atlas space, where a centred disc hits gutter/background pixels
    # (OpenMVS pads charts with a solid fill colour) and poisons the data term.
    cent_uv = np.zeros((n_nodes, 2))
    ok3 = (FVT[fidx] >= 0).all(1)
    cent_uv[ok3] = VT[FVT[fidx[ok3]]].mean(1)
    inset = 0.35
    node_suv = node_uv.copy()
    node_suv[ok3] = (1 - inset) * node_uv[ok3] + inset * cent_uv[ok3]

    # seam vertices: 3D vertex with >1 node
    v_counts = np.bincount(node_v, minlength=len(V))
    seam_nodes = np.nonzero((v_counts[node_v] > 1) & has_uv)[0]
    if not len(seam_nodes):
        print("[seams] no seams found; nothing to level")
        return False

    # sample atlas colours at seam nodes, page by page (uint8 textures, no copy)
    node_col = np.zeros((n_nodes, 3), np.float32)
    tex_imgs = [None] * len(tex_paths)
    for m, tp in enumerate(tex_paths):
        sel = seam_nodes[node_pg[seam_nodes] == m]
        if not len(sel):
            continue
        tex = np.asarray(Image.open(tp).convert("RGB"))
        tex_imgs[m] = tex.shape[:2]
        H, W = tex.shape[:2]
        xs = np.clip((node_suv[sel, 0] * (W - 1)).round().astype(np.int64), 0, W - 1)
        ys = np.clip(((1.0 - node_suv[sel, 1]) * (H - 1)).round().astype(np.int64), 0, H - 1)
        r = SAMPLE_RADIUS
        acc = np.zeros((len(sel), 3), np.float64); cnt = 0
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                acc += tex[np.clip(ys + dy, 0, H - 1), np.clip(xs + dx, 0, W - 1)]
                cnt += 1
        node_col[sel] = (acc / cnt).astype(np.float32)
        del tex

    # per-seam-vertex target = mean over its nodes -> data term
    sv = node_v[seam_nodes]
    order = np.argsort(sv, kind="stable")
    sn, sv_sorted = seam_nodes[order], sv[order]
    grp_start = np.r_[0, np.nonzero(sv_sorted[1:] != sv_sorted[:-1])[0] + 1]
    grp_end = np.r_[grp_start[1:], len(sn)]
    sums = np.add.reduceat(node_col[sn].astype(np.float64), grp_start, axis=0)
    cnts = (grp_end - grp_start)[:, None]
    target = sums / cnts
    tgt_per_node = np.repeat(target, (grp_end - grp_start), axis=0)
    data_idx = sn
    data_val = tgt_per_node - node_col[sn]

    # smoothness: mesh edges between nodes (same patch by construction)
    e_r = corner_node.ravel()
    e_c = np.roll(corner_node, -1, axis=1).ravel()
    A = coo_matrix((np.ones(len(e_r), np.float32), (e_r, e_c)),
                   shape=(n_nodes, n_nodes))
    A = (A + A.T).tocsr(); A.data[:] = 1.0
    deg = np.asarray(A.sum(1)).ravel()
    L = identity(n_nodes, format="csr", dtype=np.float32)
    L.setdiag(deg)
    L = L - A
    D = identity(n_nodes, format="csr", dtype=np.float32)
    d = np.zeros(n_nodes, np.float32); d[data_idx] = 1.0
    D.setdiag(d)
    M = (D + SMOOTH_LAMBDA * L).tocsr()

    def _cg(Mm, bb):
        try:                                   # scipy >= 1.12
            return cg(Mm, bb, rtol=CG_TOL, maxiter=400)
        except TypeError:                      # scipy <= 1.11 names it tol
            return cg(Mm, bb, tol=CG_TOL, maxiter=400)

    corr = np.zeros((n_nodes, 3), np.float32)
    for ch in range(3):
        b = np.zeros(n_nodes, np.float64)
        np.add.at(b, data_idx, data_val[:, ch])
        x, info = _cg(M, b)
        if info != 0:
            print(f"[seams] CG channel {ch} not fully converged (info={info})",
                  file=sys.stderr)
        corr[:, ch] = x
    corr = np.clip(corr, -MAX_CORRECTION, MAX_CORRECTION)

    # bake page by page: additive accumulation (np.add.at), batched per bbox size
    for m, tp in enumerate(tex_paths):
        fsel = np.nonzero((FM == m) & (FVT >= 0).all(1))[0]
        if not len(fsel):
            continue
        tex = np.asarray(Image.open(tp).convert("RGB"), dtype=np.float32)
        H, W = tex.shape[:2]
        uv = VT[FVT[fsel]]                              # [B,3,2]
        xs = uv[:, :, 0] * (W - 1)
        ys = (1.0 - uv[:, :, 1]) * (H - 1)
        C0 = np.maximum(np.floor(xs.min(1)).astype(np.int64), 0)
        C1 = np.minimum(np.ceil(xs.max(1)).astype(np.int64) + 1, W)
        R0 = np.maximum(np.floor(ys.min(1)).astype(np.int64), 0)
        R1 = np.minimum(np.ceil(ys.max(1)).astype(np.int64) + 1, H)
        AREA = ((xs[:, 1] - xs[:, 0]) * (ys[:, 2] - ys[:, 0])
                - (xs[:, 2] - xs[:, 0]) * (ys[:, 1] - ys[:, 0]))
        ok = (C1 > C0) & (R1 > R0) & (np.abs(AREA) > 1e-9)
        ccorr = corr[corner_node[fsel]]                 # [B,3corners,3ch]
        acc = np.zeros((H * W, 3), np.float32)
        wgt = np.zeros(H * W, np.float32)
        BW, BH = C1 - C0, R1 - R0
        done = np.zeros(len(fsel), bool)
        for k in (2, 4, 8, 16, 32, 64):
            grp = np.nonzero(ok & ~done & (BW <= k) & (BH <= k))[0]
            done[grp] = True
            if not len(grp):
                continue
            chunk = max(2000, int(2.5e8 // (40 * k * k)))
            dr = np.arange(k)[None, :, None]
            dc = np.arange(k)[None, None, :]
            for s in range(0, len(grp), chunk):
                g = grp[s:s + chunk]
                r0 = R0[g][:, None, None]; c0 = C0[g][:, None, None]
                rr = r0 + dr; cc2 = c0 + dc
                inwin = (rr < R1[g][:, None, None]) & (cc2 < C1[g][:, None, None])
                gx = cc2 + 0.0; gy = rr + 0.0
                x0 = xs[g, 0][:, None, None]; x1 = xs[g, 1][:, None, None]; x2 = xs[g, 2][:, None, None]
                y0 = ys[g, 0][:, None, None]; y1 = ys[g, 1][:, None, None]; y2 = ys[g, 2][:, None, None]
                ar = AREA[g][:, None, None]
                l0 = ((x1 - gx) * (y2 - gy) - (x2 - gx) * (y1 - gy)) / ar
                l1 = ((x2 - gx) * (y0 - gy) - (x0 - gx) * (y2 - gy)) / ar
                l2 = 1.0 - l0 - l1
                eps = -0.02                              # small bleed across edges
                cand = inwin & (l0 >= eps) & (l1 >= eps) & (l2 >= eps)
                if not cand.any():
                    continue
                pix = (rr * W + cc2)[cand]
                for ch in range(3):
                    val = (l0 * ccorr[g, 0, ch][:, None, None]
                           + l1 * ccorr[g, 1, ch][:, None, None]
                           + l2 * ccorr[g, 2, ch][:, None, None])
                    np.add.at(acc[:, ch], pix, val[cand].astype(np.float32))
                np.add.at(wgt, pix, 1.0)
        # faces with bbox > 64 px are vanishingly rare; left unbaked deliberately
        wgt = np.maximum(wgt, 1.0)
        out = tex + (acc / wgt[:, None]).reshape(H, W, 3)
        Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).save(tp, quality=95)
        del tex, acc, wgt

    print(f"[seams] levelled {len(grp_start)} seam vertices across {n_patches} "
          f"patches ({len(tex_paths)} atlas page(s)); max correction "
          f"{np.abs(corr).max():.1f}/255")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True, help="OpenMVS workdir")
    args = ap.parse_args()
    name = find_mesh_obj(args.work)
    if not name or "texture" not in name:
        print("[seams] no textured OBJ; skipping", file=sys.stderr)
        return
    solve_and_bake(os.path.join(args.work, name))


if __name__ == "__main__":
    main()
