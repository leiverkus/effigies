#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Patrick Leiverkus
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for helpers/tile_merge.py — the split-merge stitch.

Pure parts (face crop, PDAL crop-pipeline structure) always run. The mesh merge is
PIL-gated; the cloud merge is PDAL-gated (like test_dtm / test_orthophoto).

Run:  python3 tests/test_tile_merge.py
"""
import os
import sys
import json
import shutil
import tempfile
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "helpers"))
import tile_merge as tm          # noqa: E402
import seam_level                # noqa: E402
import mesh_to_gltf              # noqa: E402


def _have_pil():
    try:
        from PIL import Image  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Pure
# ---------------------------------------------------------------------------
def test_crop_faces_to_bounds():
    V = np.array([[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0],
                  [100, 100, 0], [110, 100, 0], [110, 110, 0]], float)
    FV = np.array([[0, 1, 2],          # centroid ~ (6.7, 3.3) -> inside
                   [0, 2, 3],          # centroid ~ (3.3, 6.7) -> inside
                   [4, 5, 6]])         # centroid ~ (106, 103) -> outside
    mask = tm.crop_faces_to_bounds(V, FV, (0, 0, 10, 10))
    assert mask.tolist() == [True, True, False], mask
    assert tm.crop_faces_to_bounds(np.zeros((0, 3)), np.zeros((0, 3), int),
                                   (0, 0, 1, 1)).shape == (0,)
    print("ok  crop_faces_to_bounds: centroid ownership (in/out)")


def test_build_crop_pipeline():
    p = tm.build_crop_pipeline("/in.ply", "/out.ply", (1.0, 2.0, 3.0, 4.0))["pipeline"]
    assert [s["type"] for s in p] == ["readers.ply", "filters.crop", "writers.ply"]
    assert p[1]["bounds"] == "([1.0,3.0],[2.0,4.0])", p[1]["bounds"]   # XY only
    print("ok  build_crop_pipeline: readers->crop(XY)->writers")


# ---------------------------------------------------------------------------
# Mesh merge (PIL-gated)
# ---------------------------------------------------------------------------
def _write_tile_obj(tdir, x0, color, atlas="model_material_0000_map_Kd.png"):
    """A 10x10 textured quad (2 triangles) at x in [x0, x0+10]. The atlas filename
    is INTENTIONALLY the same across tiles (OpenMVS's default) to exercise the
    merge's namespacing against the parser's sorted-glob collision fallback."""
    from PIL import Image
    os.makedirs(tdir, exist_ok=True)
    obj = os.path.join(tdir, "scene_dense_mesh_refine_texture.obj")
    with open(obj, "w") as f:
        f.write("mtllib scene_dense_mesh_refine_texture.mtl\n")
        f.write(f"v {x0} 0 0\nv {x0 + 10} 0 0\nv {x0 + 10} 10 0\nv {x0} 10 0\n")
        f.write("vt 0 0\nvt 1 0\nvt 1 1\nvt 0 1\n")
        f.write("usemtl mat0\n")
        f.write("f 1/1 2/2 3/3\nf 1/1 3/3 4/4\n")
    with open(os.path.join(tdir, "scene_dense_mesh_refine_texture.mtl"), "w") as f:
        f.write(f"newmtl mat0\nmap_Kd {atlas}\n")
    Image.fromarray(np.full((32, 32, 3), color, np.uint8)).save(
        os.path.join(tdir, atlas))
    return obj


def test_merge_meshes():
    if not _have_pil():
        print("skip merge-meshes (needs PIL — present in the Effigies image)")
        return
    with tempfile.TemporaryDirectory() as root:
        objA = _write_tile_obj(os.path.join(root, "tiles", "tile_A"), 0, (200, 0, 0))
        objB = _write_tile_obj(os.path.join(root, "tiles", "tile_B"), 10, (0, 0, 200))
        work = os.path.join(root, "work")
        os.makedirs(work)
        out_obj = os.path.join(work, "scene_dense_mesh_refine_texture.obj")
        out_mtl = os.path.join(work, "scene_dense_mesh_refine_texture.mtl")
        nv, nf = tm.merge_meshes(
            [("tile_A", objA), ("tile_B", objB)],
            {"tile_A": (0, 0, 10, 10), "tile_B": (10, 0, 20, 10)},
            out_obj, out_mtl, work)
        assert (nv, nf) == (8, 4), (nv, nf)            # 4+4 verts, 2+2 faces

        # the two colliding atlas names are namespaced to distinct files
        atlases = sorted(f for f in os.listdir(work) if f.endswith(".png"))
        assert len(atlases) == 2 and atlases[0] != atlases[1], atlases

        # merged OBJ parses into 2 DISTINCT, RESOLVABLE atlas pages (proves the
        # namespacing defeats the sorted-*map_Kd*-glob fallback collision)
        V, VT, FV, FVT, FM, tex_paths = seam_level.parse_obj_arrays(out_obj)
        assert len(FV) == 4 and len(V) == 8, (len(FV), len(V))
        assert len(tex_paths) == 2, tex_paths
        assert all(p and os.path.exists(p) for p in tex_paths), tex_paths
        assert len(set(tex_paths)) == 2, "atlas pages must be distinct files"
        assert set(FM.tolist()) == {0, 1}, FM

        # the merged OBJ is consumable by the glTF exporter's parser (N atlas pages)
        gV, gVT, gfaces, gtex = mesh_to_gltf._parse_obj(out_obj)
        assert len(gtex) == 2 and len(gV) == 8, (len(gtex), len(gV))
    print("ok  merge_meshes: namespaced multi-atlas OBJ, consumable downstream")


# ---------------------------------------------------------------------------
# Cloud merge (PDAL-gated)
# ---------------------------------------------------------------------------
def _faux_ply(path, bounds, count=500):
    import subprocess
    xmin, ymin, xmax, ymax = bounds
    pipe = {"pipeline": [
        {"type": "readers.faux", "mode": "random", "count": count,
         "bounds": f"([{xmin},{xmax}],[{ymin},{ymax}],[0,1])"},
        {"type": "writers.ply", "filename": path}]}
    r = subprocess.run(["pdal", "pipeline", "--stdin"], input=json.dumps(pipe),
                       text=True, capture_output=True)
    assert r.returncode == 0, r.stderr


def _ply_count(path):
    import subprocess
    r = subprocess.run(["pdal", "info", "--summary", path],
                       text=True, capture_output=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)["summary"]["num_points"]


def test_merge_clouds():
    if shutil.which("pdal") is None:
        print("skip merge-clouds (needs pdal — present in the Effigies image)")
        return
    with tempfile.TemporaryDirectory() as root:
        pA = os.path.join(root, "tiles", "tile_A", "scene_dense.ply")
        pB = os.path.join(root, "tiles", "tile_B", "scene_dense.ply")
        os.makedirs(os.path.dirname(pA)); os.makedirs(os.path.dirname(pB))
        # each tile's cloud spills past its core bound (halo); crop must clip it
        _faux_ply(pA, (0, 0, 14, 10), count=600)        # core A = [0,10]
        _faux_ply(pB, (6, 0, 20, 10), count=600)        # core B = [10,20]
        out = os.path.join(root, "scene_dense.ply")
        tm.merge_clouds([("tile_A", pA), ("tile_B", pB)],
                        {"tile_A": (0, 0, 10, 10), "tile_B": (10, 0, 20, 10)}, out)
        assert os.path.exists(out)
        n = _ply_count(out)
        # merged count is bounded by the inputs (cropping removed the halo spill)
        assert 0 < n <= 1200, n
    print(f"ok  merge_clouds: cropped tiles concatenated into one PLY")


if __name__ == "__main__":
    test_crop_faces_to_bounds()
    test_build_crop_pipeline()
    test_merge_meshes()
    test_merge_clouds()
    print("\nall tile-merge tests passed")
