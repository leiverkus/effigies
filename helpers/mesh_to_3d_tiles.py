#!/usr/bin/env python3
"""
3D Tiles export — OGC / Cesium streaming LOD tileset of the textured mesh.

ODM streams large models as 3D Tiles; a single glTF is too heavy to stream a big
scene. This runs OpenDroneMap's **Obj2Tiles** (baked into the image) over the
textured OBJ to build `odm_3d_tiles/tileset.json` + `*.b3dm` LOD tiles, placed on
the globe from the georeferencing.

Placement: the textured OBJ is XY-offset / Z-absolute (the engine convention). We
project the offset (easting, northing) back to WGS84 lat/lon, take the mean Z as
the origin altitude, write a fully-local OBJ (Z localised to that altitude, MTL +
textures reused in place) and hand Obj2Tiles `--lat/--lon/--alt` — the ODM
reference_lla contract. ENU matches UTM (X=east, Y=north, Z=up), so no axis swap.

Opt-in (`3d-tiles`); needs a georeferenced result. Non-fatal.

Dependencies: the `Obj2Tiles` binary (in the image) and pyproj.
"""
import argparse
import json
import math
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from openmvs_mesh import find_mesh_obj  # noqa: E402


def _obj_stats(obj_path):
    """One streaming pass: (mean_z, area_m2) from the `v` lines."""
    sx = sy = sz = 0.0
    minx = miny = float("inf")
    maxx = maxy = float("-inf")
    n = 0
    with open(obj_path) as f:
        for line in f:
            if line.startswith("v "):
                p = line.split()
                x, y, z = float(p[1]), float(p[2]), float(p[3])
                sz += z
                minx = min(minx, x); maxx = max(maxx, x)
                miny = min(miny, y); maxy = max(maxy, y)
                n += 1
    if n == 0:
        return None, None
    area = max(0.0, (maxx - minx)) * max(0.0, (maxy - miny))
    return sz / n, area


def _auto_divisions(area_m2):
    """ODM-style: more tiles for larger scenes. Clamped 1..4."""
    if area_m2 <= 0:
        return 1
    return int(min(4, max(1, math.ceil(math.log(max(area_m2, 1.0) / 10000.0, 4) if area_m2 > 10000 else 1))))


def _write_local_obj(src_obj, dst_obj, alt):
    """Copy the OBJ verbatim except `v x y z` -> `v x y (z-alt)` (so the mesh is
    local around its origin at `alt`). The mtllib/textures are referenced in place,
    so dst_obj must sit in the same directory as src_obj."""
    with open(src_obj) as fin, open(dst_obj, "w") as fout:
        for line in fin:
            if line.startswith("v "):
                p = line.split()
                fout.write(f"v {p[1]} {p[2]} {float(p[3]) - alt:.6f}\n")
            else:
                fout.write(line)


def run_3d_tiles(work, divisions="auto", lods=3):
    """Build odm_3d_tiles/ from the textured OBJ. Non-fatal: True on success."""
    if shutil.which("Obj2Tiles") is None:
        print("[3dtiles] Obj2Tiles not on PATH; skipping", file=sys.stderr)
        return False
    name = find_mesh_obj(work)
    if not name or "texture" not in name:
        print("[3dtiles] no textured OBJ; skipping", file=sys.stderr)
        return False
    tr_path = os.path.join(work, "georef_transform.json")
    if not os.path.exists(tr_path):
        print("[3dtiles] no georef_transform.json; skipping", file=sys.stderr)
        return False
    tr = json.load(open(tr_path))
    crs = tr.get("crs")
    if not crs or str(crs).lower() == "local":
        print("[3dtiles] result is not georeferenced (crs=local); skipping", file=sys.stderr)
        return False
    off = tr.get("offset", [0.0, 0.0, 0.0])

    try:
        from pyproj import Transformer
    except ImportError:
        print("[3dtiles] pyproj missing; skipping", file=sys.stderr)
        return False

    obj = os.path.join(work, name)
    mean_z, area = _obj_stats(obj)
    if mean_z is None:
        print("[3dtiles] OBJ has no vertices; skipping", file=sys.stderr)
        return False

    lon, lat = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform(off[0], off[1])
    alt = float(mean_z)
    div = _auto_divisions(area) if str(divisions).lower() == "auto" else int(divisions)

    out = os.path.join(work, "odm_3d_tiles")
    if os.path.isdir(out):
        shutil.rmtree(out)
    local_obj = os.path.join(work, "_3dtiles_local.obj")
    _write_local_obj(obj, local_obj, alt)
    try:
        cmd = ["Obj2Tiles", local_obj, out,
               "--divisions", str(div), "--lods", str(lods),
               "--lat", f"{lat:.9f}", "--lon", f"{lon:.9f}", "--alt", f"{alt:.3f}"]
        proc = subprocess.run(cmd, capture_output=True, text=True)
    finally:
        if os.path.exists(local_obj):
            os.remove(local_obj)

    tileset = os.path.join(out, "tileset.json")
    if proc.returncode != 0 or not os.path.exists(tileset):
        print(f"[3dtiles] Obj2Tiles failed (rc={proc.returncode}); skipping\n"
              f"{proc.stderr.strip()[:500]}", file=sys.stderr)
        return False
    n_b3dm = sum(1 for _, _, fs in os.walk(out) for fn in fs if fn.endswith(".b3dm"))
    print(f"[3dtiles] wrote odm_3d_tiles/tileset.json "
          f"({n_b3dm} b3dm tiles, divisions {div}, lods {lods}, "
          f"lat {lat:.6f} lon {lon:.6f} alt {alt:.1f} m)")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True, help="OpenMVS workdir")
    ap.add_argument("--divisions", default="auto", help="tile splits per axis, or 'auto'")
    ap.add_argument("--lods", type=int, default=3, help="levels of detail")
    args = ap.parse_args()
    run_3d_tiles(args.work, args.divisions, args.lods)


if __name__ == "__main__":
    main()
