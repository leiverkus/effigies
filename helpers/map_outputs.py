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
import sys
import shutil
import glob

# Shared OpenMVS mesh-name lookup — the SAME ordered candidate list the georef
# bridge uses, so the OBJ this maps into the WebODM asset path is exactly the one
# georef_bridge transformed (see helpers/openmvs_mesh.py).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from openmvs_mesh import find_mesh_obj  # noqa: E402


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
    #    TextureMesh appends "_texture" to the input mesh name and emits the OBJ
    #    plus a .mtl and texture maps (jpg or png depending on the build).
    obj = find_mesh_obj(W)
    if obj:
        base = obj[:-4]
        # WebODM's legacy 3D model path loads odm_textured_model_geo.obj AND a
        # .mtl explicitly named odm_textured_model_geo.mtl — so the OBJ copy gets
        # its mtllib line rewritten and the .mtl is provided under that name.
        # Texture files keep their original names (the .mtl's map_Kd references).
        dst_obj = os.path.join(P, "odm_texturing", "odm_textured_model_geo.obj")
        os.makedirs(os.path.dirname(dst_obj), exist_ok=True)
        with open(os.path.join(W, obj), "r", errors="ignore") as fin, \
             open(dst_obj, "w") as fout:
            for line in fin:
                if line.startswith("mtllib"):
                    fout.write("mtllib odm_textured_model_geo.mtl\n")
                else:
                    fout.write(line)
        print(f"[map] {os.path.join(W, obj)} -> {dst_obj} (mtllib rewritten)")
        link_or_copy(os.path.join(W, base + ".mtl"),
                     os.path.join(P, "odm_texturing", "odm_textured_model_geo.mtl"))
        for ext in (".png", ".jpg", ".jpeg"):
            for tex in glob.glob(os.path.join(W, base + "*" + ext)):
                link_or_copy(tex, os.path.join(P, "odm_texturing", os.path.basename(tex)))

    # glTF model -> odm_texturing/odm_textured_model_geo.glb ("Struktur-Modell (glTF)")
    glb = os.path.join(W, "odm_textured_model_geo.glb")
    if os.path.exists(glb):
        link_or_copy(glb, os.path.join(P, "odm_texturing", "odm_textured_model_geo.glb"))

    # 2. dense point cloud -> odm_georeferencing (WebODM expects .laz here).
    #    pointcloud_to_laz.py produces the georeferenced LAZ; fall back to the raw
    #    PLY only if the LAZ step could not run (e.g. PDAL unavailable).
    laz = os.path.join(W, "odm_georeferenced_model.laz")
    if os.path.exists(laz):
        link_or_copy(laz, os.path.join(P, "odm_georeferencing", "odm_georeferenced_model.laz"))
    else:
        print("[map] no LAZ found; passing the raw PLY through as a fallback")
        link_or_copy(os.path.join(W, "scene_dense.ply"),
                     os.path.join(P, "odm_georeferencing", "odm_georeferenced_model.ply"))

    # 2b. EPT tileset -> entwine_pointcloud/ (for the Potree web viewer), if built.
    ept_src = os.path.join(W, "entwine_pointcloud")
    if os.path.isdir(ept_src):
        ept_dst = os.path.join(P, "entwine_pointcloud")
        if os.path.exists(ept_dst):
            shutil.rmtree(ept_dst)
        shutil.copytree(ept_src, ept_dst)
        print(f"[map] {ept_src} -> {ept_dst}")

    # 2c. orthophoto -> odm_orthophoto/ (WebODM's 2D map asset)
    ortho = os.path.join(W, "odm_orthophoto.tif")
    if os.path.exists(ortho):
        link_or_copy(ortho, os.path.join(P, "odm_orthophoto", "odm_orthophoto.tif"))

    # 2c1. DSM -> odm_dem/ (WebODM's digital surface model; ODM convention path)
    dsm = os.path.join(W, "odm_dem", "dsm.tif")
    if os.path.exists(dsm):
        link_or_copy(dsm, os.path.join(P, "odm_dem", "dsm.tif"))

    # 2c1a. DTM -> odm_dem/ (bare-earth model; opt-in, only present when --dtm)
    dtm = os.path.join(W, "odm_dem", "dtm.tif")
    if os.path.exists(dtm):
        link_or_copy(dtm, os.path.join(P, "odm_dem", "dtm.tif"))

    # 2c2. coords.txt -> odm_georeferencing/ (WebODM's 3D viewer reads the offset
    #      from line 2 to place the textured model next to the point cloud)
    coords = os.path.join(W, "coords.txt")
    if os.path.exists(coords):
        link_or_copy(coords, os.path.join(P, "odm_georeferencing", "coords.txt"))

    # 2d. camera assets — cameras.json (project root) + shots.geojson (odm_report/)
    cams = os.path.join(W, "cameras.json")
    if os.path.exists(cams):
        link_or_copy(cams, os.path.join(P, "cameras.json"))
    shots = os.path.join(W, "shots.geojson")
    if os.path.exists(shots):
        link_or_copy(shots, os.path.join(P, "odm_report", "shots.geojson"))

    # 3. quality report -> odm_report/report.pdf ("Qualitätsbericht")
    os.makedirs(os.path.join(P, "odm_report"), exist_ok=True)
    report = os.path.join(W, "report.pdf")
    if os.path.exists(report):
        link_or_copy(report, os.path.join(P, "odm_report", "report.pdf"))

    print("[map] output mapping complete")


if __name__ == "__main__":
    main()
