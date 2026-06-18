#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Patrick Leiverkus
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Split-merge tiling — merge half: stitch per-tile dense outputs into one set of
assets in the shared local frame, then the existing downstream (georef, LAZ,
ortho, glTF, report, map_outputs) runs ONCE on $WORK exactly as the non-tiled path.

Two merges, both in the shared (offset-free, local SfM) frame the tiles produced:

  * Mesh — crop each tile's textured OBJ to its CORE xy bound (drop halo-only
    faces; centroid ownership ⇒ exactly one tile per face, no double count),
    concatenate vertices/UVs/faces with running index offsets, and **namespace
    every tile's atlas pages + materials** so the merged OBJ resolves each page
    explicitly via the MTL — never via the OBJ parsers' sorted-`*map_Kd*`-glob
    fallback, which would collide on OpenMVS's default page names. The downstream
    OBJ consumers (orthophoto, mesh_to_gltf, seam_level) already handle multi-page
    atlases.
  * Cloud — crop each tile's scene_dense.ply to its core bound (PDAL, XY only) and
    concatenate into one scene_dense.ply; pointcloud_to_laz then runs once.

The mesh seam at tile borders (border vertices don't coincide) is the documented
v1 limitation — Metashape/ODM share it; halo overlap + the ortho hole-fill mitigate
the 2-D symptom.

Dependencies: numpy + PIL (mesh), PDAL binary (cloud). Reuses
seam_level.parse_obj_arrays and openmvs_mesh.find_mesh_obj.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from collections import OrderedDict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from seam_level import parse_obj_arrays          # noqa: E402
from openmvs_mesh import find_mesh_obj            # noqa: E402
import tiling                                     # noqa: E402


# ---------------------------------------------------------------------------
# Mesh
# ---------------------------------------------------------------------------
def crop_faces_to_bounds(V, FV, xy_bounds):
    """Boolean face mask: keep faces whose centroid XY is inside ``xy_bounds``
    (xmin, ymin, xmax, ymax). Centroid ownership + exact cell tiling ⇒ each face
    is owned by exactly one tile. Pure."""
    if len(FV) == 0:
        return np.zeros(0, bool)
    c = V[FV].mean(axis=1)
    xmin, ymin, xmax, ymax = xy_bounds
    return ((c[:, 0] >= xmin) & (c[:, 0] <= xmax)
            & (c[:, 1] >= ymin) & (c[:, 1] <= ymax))


def merge_meshes(tile_objs, tile_bounds, out_obj, out_mtl, atlas_dir):
    """Crop + concatenate per-tile textured OBJs into one merged OBJ+MTL.

    tile_objs:   [(tile_id, obj_path), ...]
    tile_bounds: {tile_id: (xmin, ymin, xmax, ymax)}  (the tile's CORE bound)
    Atlas pages are copied into atlas_dir under tile-namespaced filenames and the
    MTL references them explicitly. Returns the merged (n_vertices, n_faces)."""
    verts, uvs, faces = [], [], []        # faces: (material_name, [(vi, ti|None)x3])
    mtl_entries = []                      # (material_name, atlas_basename)
    v_off = vt_off = 0

    for tile_id, obj_path in tile_objs:
        if not obj_path or not os.path.exists(obj_path):
            continue
        V, VT, FV, FVT, FM, tex_paths = parse_obj_arrays(obj_path)
        if len(FV) == 0:
            continue
        mask = crop_faces_to_bounds(V, FV, tile_bounds[tile_id])
        if not mask.any():
            continue
        fv, fvt, fm = FV[mask], FVT[mask], FM[mask]

        used_v = np.unique(fv)
        vmap = {int(o): k for k, o in enumerate(used_v)}
        verts.extend(V[o] for o in used_v)
        has_vt = fvt >= 0
        used_vt = np.unique(fvt[has_vt]) if has_vt.any() else np.empty(0, int)
        vtmap = {int(o): k for k, o in enumerate(used_vt)}
        uvs.extend(VT[o] for o in used_vt)

        # namespace each used material's atlas page + material name
        slot_mat = {}
        for s in np.unique(fm):
            s = int(s)
            ap = tex_paths[s] if s < len(tex_paths) else None
            if not ap or not os.path.exists(ap):
                continue                  # untextured material -> drop its faces
            base = f"{tile_id}_{os.path.basename(ap)}"
            shutil.copy(ap, os.path.join(atlas_dir, base))
            mat = f"{tile_id}_mat{s}"
            slot_mat[s] = mat
            mtl_entries.append((mat, base))

        for k in range(len(fv)):
            s = int(fm[k])
            if s not in slot_mat:
                continue
            tri = []
            for c in range(3):
                vi = v_off + vmap[int(fv[k, c])] + 1            # 1-based
                ti = vt_off + vtmap[int(fvt[k, c])] + 1 if fvt[k, c] >= 0 else None
                tri.append((vi, ti))
            faces.append((slot_mat[s], tri))
        v_off, vt_off = len(verts), len(uvs)

    if not faces:
        raise RuntimeError("merge_meshes: no faces survived cropping across tiles")

    with open(out_obj, "w") as f:
        f.write(f"mtllib {os.path.basename(out_mtl)}\n")
        for v in verts:
            f.write(f"v {v[0]:.6f} {v[1]:.6f} {v[2]:.6f}\n")
        for t in uvs:
            f.write(f"vt {t[0]:.6f} {t[1]:.6f}\n")
        groups = OrderedDict()
        for mat, tri in faces:
            groups.setdefault(mat, []).append(tri)
        for mat, tris in groups.items():
            f.write(f"usemtl {mat}\n")
            for tri in tris:
                f.write("f " + " ".join(f"{vi}/{ti}" if ti else f"{vi}"
                                        for vi, ti in tri) + "\n")
    with open(out_mtl, "w") as f:
        for mat, base in mtl_entries:
            f.write(f"newmtl {mat}\nmap_Kd {base}\n")
    print(f"[tile-merge] merged mesh -> {os.path.basename(out_obj)} "
          f"({len(verts)} verts, {len(faces)} faces, {len(mtl_entries)} atlas pages)")
    return len(verts), len(faces)


# ---------------------------------------------------------------------------
# Cloud
# ---------------------------------------------------------------------------
def build_crop_pipeline(ply_in, ply_out, xy_bounds):
    """PDAL pipeline dict: crop a PLY to ``xy_bounds`` (XY only, full Z). Pure —
    unit-testable without PDAL (mirrors pointcloud_to_dtm.build_dtm_pipeline)."""
    xmin, ymin, xmax, ymax = xy_bounds
    return {"pipeline": [
        {"type": "readers.ply", "filename": ply_in},
        {"type": "filters.crop", "bounds": f"([{xmin},{xmax}],[{ymin},{ymax}])"},
        {"type": "writers.ply", "filename": ply_out},
    ]}


def _run_pdal(pipeline):
    proc = subprocess.run(["pdal", "pipeline", "--stdin"],
                          input=json.dumps(pipeline), text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"pdal pipeline failed:\n{proc.stderr.strip()}")


def merge_clouds(tile_plys, tile_bounds, out_ply):
    """Crop each tile cloud to its core bound and concatenate into one PLY.
    tile_plys: [(tile_id, ply_path), ...]. Returns the merged PLY path."""
    cropped = []
    for tile_id, ply in tile_plys:
        if not os.path.exists(ply):
            print(f"[tile-merge] WARN: tile {tile_id} cloud missing ({ply}); skipping",
                  file=sys.stderr)
            continue
        tmp = ply + ".crop.ply"
        _run_pdal(build_crop_pipeline(ply, tmp, tile_bounds[tile_id]))
        cropped.append(tmp)
    if not cropped:
        raise RuntimeError("merge_clouds: no tile clouds to merge")
    stages = [{"type": "readers.ply", "filename": c} for c in cropped]
    stages += [{"type": "filters.merge"},
               {"type": "writers.ply", "filename": out_ply}]
    _run_pdal({"pipeline": stages})
    for c in cropped:
        try:
            os.remove(c)
        except OSError:
            pass
    print(f"[tile-merge] merged cloud -> {os.path.basename(out_ply)} "
          f"({len(cropped)} tiles)")
    return out_ply


# ---------------------------------------------------------------------------
# CLI — merge all done tiles in a manifest into the canonical $WORK assets
# ---------------------------------------------------------------------------
def merge_from_manifest(work, manifest_path):
    man = tiling.read_manifest(manifest_path)
    done = [t for t in man["tiles"] if t.get("status") == "done"]
    if not done:
        raise RuntimeError("no completed tiles to merge")
    bounds = {t["id"]: tuple(t["xy_bounds"]) for t in done}

    tile_objs, tile_plys = [], []
    for t in done:
        tdir = os.path.join(work, "tiles", t["id"])
        obj = find_mesh_obj(tdir)
        tile_objs.append((t["id"], os.path.join(tdir, obj) if obj else None))
        tile_plys.append((t["id"], os.path.join(tdir, "scene_dense.ply")))

    out_obj = os.path.join(work, "scene_dense_mesh_refine_texture.obj")
    out_mtl = os.path.join(work, "scene_dense_mesh_refine_texture.mtl")
    merge_meshes(tile_objs, bounds, out_obj, out_mtl, work)
    try:
        merge_clouds(tile_plys, bounds, os.path.join(work, "scene_dense.ply"))
    except RuntimeError as e:
        print(f"[tile-merge] WARN: cloud merge failed ({e}); "
              f"continuing with mesh only", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description="merge per-tile dense outputs")
    ap.add_argument("--work", required=True)
    ap.add_argument("--manifest", required=True)
    args = ap.parse_args()
    merge_from_manifest(args.work, args.manifest)


if __name__ == "__main__":
    main()
