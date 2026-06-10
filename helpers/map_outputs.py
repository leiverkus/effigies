#!/usr/bin/env python3
"""
Map Effigies outputs onto the WebODM asset contract.

WebODM (via NodeODM Task.js) archives a fixed set of paths into all.zip and the
frontend reads specific ones. The minimum needed for the 3D model + point cloud:

  odm_texturing/odm_textured_model_geo.obj   (+ .mtl + texture pngs)
  odm_georeferencing/odm_georeferenced_model.laz   (dense cloud)
  entwine_pointcloud/  OR  potree_pointcloud/       (web point cloud viewer)
  odm_report/           (optional stats)

We symlink/copy from the OpenMVS workdir into these locations.
"""
import argparse
import os
import shutil
import glob


def link_or_copy(src, dst):
    if not os.path.exists(src):
        print(f"[map] skip missing {src}")
        return False
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst):
        os.remove(dst)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)
    print(f"[map] {src} -> {dst}")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--proj", required=True)
    ap.add_argument("--work", required=True)
    args = ap.parse_args()
    P, W = args.proj, args.work

    # 1. textured model -> odm_texturing
    #    OpenMVS TextureMesh emits scene_dense_mesh_refine.obj/.mtl/.png
    obj = None
    for cand in ("scene_dense_mesh_refine.obj", "scene_dense_mesh.obj"):
        if os.path.exists(os.path.join(W, cand)):
            obj = cand
            break
    if obj:
        base = obj[:-4]
        link_or_copy(os.path.join(W, obj),
                     os.path.join(P, "odm_texturing", "odm_textured_model_geo.obj"))
        link_or_copy(os.path.join(W, base + ".mtl"),
                     os.path.join(P, "odm_texturing", base + ".mtl"))
        for png in glob.glob(os.path.join(W, base + "*.png")):
            link_or_copy(png, os.path.join(P, "odm_texturing", os.path.basename(png)))

    # 2. dense point cloud -> odm_georeferencing (WebODM expects .laz here)
    #    Convert scene_dense.ply -> laz with PDAL at build time; here we pass the ply
    #    through and let postprocessing build EPT.
    link_or_copy(os.path.join(W, "scene_dense.ply"),
                 os.path.join(P, "odm_georeferencing", "odm_georeferenced_model.ply"))

    # 3. report stub so the UI has a stats target
    os.makedirs(os.path.join(P, "odm_report"), exist_ok=True)

    print("[map] output mapping complete")


if __name__ == "__main__":
    main()
