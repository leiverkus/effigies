#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Patrick Leiverkus
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Split-merge tiling — spatial partition of a shared global sparse model (v0.5.0).

Large image sets blow the single-machine RAM wall in the dense chain (Densify +
ReconstructMesh's Delaunay). The fix (see docs/split-merge-tiling-plan.md): run SfM
ONCE on all images, then partition the cameras spatially **in that one shared frame**
and run the dense→mesh→texture chain per tile within a memory budget. Because every
tile inherits poses from the same global sparse, merge alignment is free and no GPS
is needed.

This module is the partition half:
  * a PURE-numpy partition over camera centres (grid over XY) — unit-testable
    without OpenMVS/pycolmap: tile assignment, halo membership, core bounds
    (which tile a tile, and the merge, then crop to), the memory-budget tile-count
    heuristic, and a connectivity guard;
  * a tile manifest (JSON, same style as georef_transform.json) that makes the run
    resumable (per-tile status);
  * a subset writer that emits a per-tile COLMAP binary model (cameras/images/
    points3D) from the global undistorted model — via pycolmap (already baked in,
    used by gcp_bundle_adjust.py), with a pycolmap-free struct fallback.

The per-tile core bound is the grid cell rectangle; cells tile the global camera
bbox exactly (disjoint, union == bbox), so at merge each face is owned by exactly
one tile (centroid-in-cell) — no double counting. Faces whose centroid lands in an
empty cell (no core camera anywhere near) are marginal halo spill and dropped.

Dependencies: numpy (required); pycolmap only for the subset writer (gated).
"""
import argparse
import json
import math
import os
import struct
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import colmap_bin  # noqa: E402

MANIFEST_VERSION = 1
# Memory-budget heuristic constants (PLACEHOLDERS — calibrate against the
# EFFIGIES_BLEND_RSS RAM slope on real runs; see docs/split-merge-tiling-plan.md).
DEFAULT_BUDGET_POINTS = int(os.environ.get("EFFIGIES_TILE_BUDGET", "80000000"))
# dense points produced per sparse point, by densify-resolution-level (0=full res).
_DENSIFY_MULT = {0: 400.0, 1: 100.0, 2: 25.0, 3: 8.0}
# halo radius as a multiple of the median camera spacing.
_HALO_MULT = float(os.environ.get("EFFIGIES_TILE_HALO", "1.5"))


# ---------------------------------------------------------------------------
# Reading camera centres from the (undistorted, binary) global model
# ---------------------------------------------------------------------------
def camera_centers_xy(dense_sparse_dir):
    """{image_name: np.array([x, y])} — camera centres (C = -R^T t) projected to
    the XY plane of the shared local frame, read from <dir>/images.bin."""
    poses = colmap_bin.read_images_bin(os.path.join(dense_sparse_dir, "images.bin"))
    out = {}
    for name, im in poses.items():
        R = np.asarray(im["R"]); t = np.asarray(im["t"])
        C = -R.T @ t
        out[name] = C[:2].astype(float)
    return out


def n_sparse_points(dense_sparse_dir):
    return int(len(colmap_bin.read_points3D_bin(
        os.path.join(dense_sparse_dir, "points3D.bin"))))


# ---------------------------------------------------------------------------
# Pure partition math (no pycolmap / OpenMVS)
# ---------------------------------------------------------------------------
def _bbox(centers_xy):
    pts = np.asarray(list(centers_xy.values()), float)
    return (float(pts[:, 0].min()), float(pts[:, 1].min()),
            float(pts[:, 0].max()), float(pts[:, 1].max()))


def median_spacing(centers_xy):
    """Median nearest-neighbour distance between camera centres (sets the halo
    radius scale). Returns a positive float; 0-safe for a single camera."""
    pts = np.asarray(list(centers_xy.values()), float)
    if len(pts) < 2:
        return 0.0
    nn = []
    for i in range(len(pts)):
        d = np.linalg.norm(pts - pts[i], axis=1)
        d[i] = np.inf
        nn.append(d.min())
    return float(np.median(nn))


def grid_dims(n_tiles, w, h):
    """Near-square grid (cols, rows) with cols*rows >= n_tiles, biased to the
    camera-extent aspect ratio so cells stay roughly square in world units."""
    n_tiles = max(1, int(n_tiles))
    if n_tiles == 1:
        return 1, 1
    aspect = (w / h) if h > 1e-9 else 1.0
    cols = max(1, int(round(math.sqrt(n_tiles * aspect))))
    rows = max(1, math.ceil(n_tiles / cols))
    return cols, rows


def cell_bounds(bbox, cols, rows):
    """Exact uniform tiling of bbox into cols*rows rectangles. Returns
    {(i, j): (xmin, ymin, xmax, ymax)}; the union is bbox and interiors are
    disjoint (the coverage invariant the merge crop relies on)."""
    xmin, ymin, xmax, ymax = bbox
    dx = (xmax - xmin) / cols if cols else 0.0
    dy = (ymax - ymin) / rows if rows else 0.0
    out = {}
    for i in range(cols):
        for j in range(rows):
            x0 = xmin + i * dx
            y0 = ymin + j * dy
            x1 = xmax if i == cols - 1 else xmin + (i + 1) * dx
            y1 = ymax if j == rows - 1 else ymin + (j + 1) * dy
            out[(i, j)] = (x0, y0, x1, y1)
    return out


def _cell_of(xy, bbox, cols, rows):
    xmin, ymin, xmax, ymax = bbox
    dx = (xmax - xmin) or 1.0
    dy = (ymax - ymin) or 1.0
    i = min(cols - 1, max(0, int((xy[0] - xmin) / dx * cols)))
    j = min(rows - 1, max(0, int((xy[1] - ymin) / dy * rows)))
    return i, j


def _in_bounds(xy, b):
    return b[0] <= xy[0] <= b[2] and b[1] <= xy[1] <= b[3]


def partition(centers_xy, n_tiles, halo_radius=None):
    """Partition camera centres into tiles on a uniform grid.

    Returns (tiles, bbox) where tiles is a list of dicts
    {id, cameras[core], halo_cameras[], xy_bounds}. Only non-empty cells become
    tiles. Each tile's core cameras are those whose centre falls in its cell; its
    halo is the cameras within ``halo_radius`` of the (expanded) cell rectangle but
    not in the core — reconstruction support near borders, dropped at merge."""
    bbox = _bbox(centers_xy)
    cols, rows = grid_dims(n_tiles, bbox[2] - bbox[0], bbox[3] - bbox[1])
    bounds = cell_bounds(bbox, cols, rows)
    if halo_radius is None:
        halo_radius = _HALO_MULT * median_spacing(centers_xy)

    core = {cell: [] for cell in bounds}
    for name, xy in centers_xy.items():
        core[_cell_of(xy, bbox, cols, rows)].append(name)

    tiles = []
    for (i, j), b in bounds.items():
        if not core[(i, j)]:
            continue                                   # empty cell -> no tile
        core_set = set(core[(i, j)])
        ex = (b[0] - halo_radius, b[1] - halo_radius,
              b[2] + halo_radius, b[3] + halo_radius)
        halo = [n for n, xy in centers_xy.items()
                if n not in core_set and _in_bounds(xy, ex)]
        tiles.append({
            "id": f"tile_{i:02d}_{j:02d}",
            "cameras": sorted(core[(i, j)]),
            "halo_cameras": sorted(halo),
            "xy_bounds": [b[0], b[1], b[2], b[3]],
        })
    return tiles, list(bbox)


def is_connected(centers_xy, radius):
    """True if the camera-centre proximity graph (edge when centres are within
    ``radius``) is a single connected component — the precondition for tiles to
    share one frame. A disconnected set means COLMAP likely produced multiple
    components and tiling would mis-stitch unrelated blocks."""
    names = list(centers_xy)
    if len(names) < 2:
        return True
    pts = np.asarray([centers_xy[n] for n in names], float)
    seen = np.zeros(len(names), bool)
    stack = [0]
    seen[0] = True
    count = 1
    while stack:
        k = stack.pop()
        d = np.linalg.norm(pts - pts[k], axis=1)
        nbr = np.nonzero((d <= radius) & (~seen))[0]
        for m in nbr:
            seen[m] = True
            count += 1
            stack.append(int(m))
    return count == len(names)


def estimate_tile_count(n_points, budget_points=DEFAULT_BUDGET_POINTS,
                        densify_mult=_DENSIFY_MULT[1]):
    """Tile count from the memory budget: estimated dense points
    (n_sparse_points x densify_mult) divided by the per-tile budget, >= 1."""
    if budget_points <= 0:
        return 1
    est_dense = max(0.0, float(n_points)) * float(densify_mult)
    return max(1, math.ceil(est_dense / float(budget_points)))


def densify_mult_for(res_level):
    try:
        return _DENSIFY_MULT.get(int(res_level), _DENSIFY_MULT[1])
    except (TypeError, ValueError):
        return _DENSIFY_MULT[1]


# ---------------------------------------------------------------------------
# Manifest (JSON; mirrors georef_transform.json write/read style)
# ---------------------------------------------------------------------------
def build_manifest(centers_xy, n_points, n_tiles, method="grid"):
    tiles, bbox = partition(centers_xy, n_tiles)
    for t in tiles:
        t["status"] = "pending"
    return {
        "version": MANIFEST_VERSION,
        "method": method,
        "n_images": len(centers_xy),
        "n_sparse_points": int(n_points),
        "global_bbox": bbox,
        "tiles": tiles,
    }


def write_manifest(path, manifest):
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"[tiling] wrote {path} ({len(manifest['tiles'])} tiles)")


def read_manifest(path):
    with open(path) as f:
        return json.load(f)


def _find_tile(manifest, tile_id):
    for t in manifest["tiles"]:
        if t["id"] == tile_id:
            return t
    raise KeyError(f"tile {tile_id} not in manifest")


# ---------------------------------------------------------------------------
# Per-tile subset COLMAP model writer
# ---------------------------------------------------------------------------
def write_tile_subset(global_dense_sparse, tile_image_names, out_dir):
    """Write a per-tile COLMAP binary model (cameras/images/points3D) holding only
    ``tile_image_names`` (core + halo) and the points they observe.

    Tries pycolmap (keeps the proper sparse points), then VERIFIES the written
    model contains exactly the kept images and falls back to a struct packer
    otherwise — robust to pycolmap API drift across COLMAP builds. The struct
    fallback writes an empty points3D.bin (Densify recomputes structure from depth
    maps and does not consume the sparse points)."""
    os.makedirs(out_dir, exist_ok=True)
    keep = set(tile_image_names)
    if _try_pycolmap_subset(global_dense_sparse, keep, out_dir):
        try:
            got = set(colmap_bin.read_images_bin(os.path.join(out_dir, "images.bin")))
        except (OSError, struct.error):
            got = None
        if got == keep:
            print(f"[tiling] subset model -> {out_dir} ({len(keep)} images, pycolmap)")
            return len(keep)
        print("[tiling] pycolmap subset did not match the requested images; "
              "using struct fallback", file=sys.stderr)
    return _write_tile_subset_struct(global_dense_sparse, keep, out_dir)


def _try_pycolmap_subset(global_dense_sparse, keep, out_dir):
    """Best-effort pycolmap subset (rig/frame model: deregister the frames of
    non-kept images, drop now-empty points, write). Returns True if it wrote
    something, False on missing pycolmap or any API error (caller then verifies /
    falls back)."""
    try:
        import pycolmap
    except ImportError:
        return False
    try:
        rec = pycolmap.Reconstruction(global_dense_sparse)
        drop = {rec.images[iid].frame_id for iid in rec.reg_image_ids()
                if rec.images[iid].name not in keep}
        for fid in drop:
            rec.deregister_frame(fid)
        for pid in list(rec.points3D):
            if rec.points3D[pid].track.length() == 0:
                rec.delete_point3D(pid)
        rec.write(out_dir)
        return True
    except Exception as e:                       # noqa: BLE001 — any API drift -> fallback
        print(f"[tiling] pycolmap subset failed ({e}); struct fallback",
              file=sys.stderr)
        return False


def _write_tile_subset_struct(global_dense_sparse, keep, out_dir):
    """pycolmap-free fallback: re-pack the subset cameras.bin + images.bin (PINHOLE
    binary, the format colmap_bin reads) and an EMPTY points3D.bin."""
    cams = colmap_bin.read_cameras_bin(os.path.join(global_dense_sparse, "cameras.bin"))
    imgs = colmap_bin.read_images_bin(os.path.join(global_dense_sparse, "images.bin"))
    sub = {n: im for n, im in imgs.items() if n in keep}
    cam_ids = {im["camera_id"] for im in sub.values()}
    model_id = {"SIMPLE_PINHOLE": 0, "PINHOLE": 1}
    with open(os.path.join(out_dir, "cameras.bin"), "wb") as f:
        f.write(struct.pack("<Q", len(cam_ids)))
        for cid in sorted(cam_ids):
            name, w, h, params = cams[cid]
            f.write(struct.pack("<ii", cid, model_id[name]))
            f.write(struct.pack("<QQ", int(w), int(h)))
            f.write(struct.pack(f"<{len(params)}d", *map(float, params)))
    with open(os.path.join(out_dir, "images.bin"), "wb") as f:
        f.write(struct.pack("<Q", len(sub)))
        for iid, (name, im) in enumerate(sorted(sub.items()), 1):
            R = np.asarray(im["R"]); t = np.asarray(im["t"])
            q = _rot_to_quat(R)
            f.write(struct.pack("<i", iid))
            f.write(struct.pack("<7d", q[0], q[1], q[2], q[3], t[0], t[1], t[2]))
            f.write(struct.pack("<i", int(im["camera_id"])))
            f.write(name.encode("utf-8") + b"\x00")
            f.write(struct.pack("<Q", 0))                 # no points2D
    with open(os.path.join(out_dir, "points3D.bin"), "wb") as f:
        f.write(struct.pack("<Q", 0))                     # empty (Densify rebuilds)
    print(f"[tiling] subset model -> {out_dir} ({len(sub)} images, struct fallback)")
    return len(sub)


def _rot_to_quat(R):
    """3x3 rotation -> (qw, qx, qy, qz), COLMAP's images.bin order."""
    tr = np.trace(R)
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        qw = 0.25 * s
        qx = (R[2, 1] - R[1, 2]) / s
        qy = (R[0, 2] - R[2, 0]) / s
        qz = (R[1, 0] - R[0, 1]) / s
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = math.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2]) * 2
        qw = (R[2, 1] - R[1, 2]) / s
        qx = 0.25 * s
        qy = (R[0, 1] + R[1, 0]) / s
        qz = (R[0, 2] + R[2, 0]) / s
    elif R[1, 1] > R[2, 2]:
        s = math.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2]) * 2
        qw = (R[0, 2] - R[2, 0]) / s
        qx = (R[0, 1] + R[1, 0]) / s
        qy = 0.25 * s
        qz = (R[1, 2] + R[2, 1]) / s
    else:
        s = math.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1]) * 2
        qw = (R[1, 0] - R[0, 1]) / s
        qx = (R[0, 2] + R[2, 0]) / s
        qy = (R[1, 2] + R[2, 1]) / s
        qz = 0.25 * s
    q = np.array([qw, qx, qy, qz], float)
    return q / np.linalg.norm(q)


# ---------------------------------------------------------------------------
# CLI (driven by run.sh / pipeline/tile.sh)
# ---------------------------------------------------------------------------
def _dense_sparse(work):
    return os.path.join(work, "dense", "sparse")


def main():
    ap = argparse.ArgumentParser(description="split-merge tiling: partition / subset")
    ap.add_argument("--decide", action="store_true",
                    help="print the chosen tile count (0/1 = no tiling)")
    ap.add_argument("--partition", action="store_true",
                    help="write the tile manifest")
    ap.add_argument("--subset", action="store_true",
                    help="write one tile's subset COLMAP model")
    ap.add_argument("--list-pending", action="store_true")
    ap.add_argument("--mark", nargs=2, metavar=("TILE", "STATUS"))
    ap.add_argument("--work")
    ap.add_argument("--manifest")
    ap.add_argument("--out")
    ap.add_argument("--tile")
    ap.add_argument("--tiles", default="auto", help="off | auto | <N>")
    ap.add_argument("--budget", default="auto")
    ap.add_argument("--res-level", default="1")
    args = ap.parse_args()

    if args.decide:
        if str(args.tiles) == "off":
            print(0); return
        if str(args.tiles).isdigit():
            print(int(args.tiles)); return
        budget = (DEFAULT_BUDGET_POINTS if args.budget in ("auto", "", None)
                  else int(args.budget))
        npts = n_sparse_points(_dense_sparse(args.work))
        print(estimate_tile_count(npts, budget, densify_mult_for(args.res_level)))
        return

    if args.partition:
        centers = camera_centers_xy(_dense_sparse(args.work))
        n_tiles = int(args.tiles) if str(args.tiles).isdigit() else \
            estimate_tile_count(n_sparse_points(_dense_sparse(args.work)))
        radius = _HALO_MULT * median_spacing(centers)
        if not is_connected(centers, radius):
            print("[tiling] WARN: camera graph is not single-connected; COLMAP may "
                  "have produced multiple components — tiling a disconnected block "
                  "is unsafe. Proceeding on all cameras (largest-component handling "
                  "is a future item).", file=sys.stderr)
        man = build_manifest(centers, n_sparse_points(_dense_sparse(args.work)), n_tiles)
        write_manifest(args.manifest or os.path.join(args.work, "tiles_manifest.json"), man)
        return

    if args.subset:
        man = read_manifest(args.manifest or os.path.join(args.work, "tiles_manifest.json"))
        t = _find_tile(man, args.tile)
        names = list(t["cameras"]) + list(t["halo_cameras"])
        write_tile_subset(_dense_sparse(args.work), names, args.out)
        return

    if args.list_pending:
        man = read_manifest(args.manifest)
        print("\n".join(t["id"] for t in man["tiles"] if t.get("status") != "done"))
        return

    if args.mark:
        path = args.manifest
        man = read_manifest(path)
        _find_tile(man, args.mark[0])["status"] = args.mark[1]
        with open(path, "w") as f:
            json.dump(man, f, indent=2)
        return

    ap.error("no action given")


if __name__ == "__main__":
    main()
