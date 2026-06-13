#!/usr/bin/env python3
"""Unit tests for helpers/mesh_to_3d_tiles.py — the 3D Tiles (Obj2Tiles) export.

The pure helpers (OBJ stats, Z-localisation, auto-divisions, lat/lon projection)
always run; the full pipeline needs the `Obj2Tiles` binary (baked into the image)
and is skipped + best-effort elsewhere.

Run:  python3 tests/test_3d_tiles.py
"""
import os
import sys
import json
import shutil
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "helpers"))
import mesh_to_3d_tiles as t3  # noqa: E402


def _write_obj(path, zs=(0.0, 5.0, 10.0)):
    with open(path, "w") as f:
        f.write("mtllib m.mtl\n")
        for i, z in enumerate(zs):
            f.write(f"v {i * 4.0} {i * 3.0} {z}\n")
        f.write("vt 0 0\nf 1 2 3\n")


def test_obj_stats_and_local():
    with tempfile.TemporaryDirectory() as d:
        obj = os.path.join(d, "scene_texture.obj")
        _write_obj(obj, (0.0, 6.0, 12.0))       # mean z = 6; bbox 8 x 6 = 48 m²
        mean_z, area = t3._obj_stats(obj)
        assert abs(mean_z - 6.0) < 1e-9, mean_z
        assert abs(area - 48.0) < 1e-9, area
        # localisation subtracts the altitude from every Z, keeps the rest verbatim
        loc = os.path.join(d, "_local.obj")
        t3._write_local_obj(obj, loc, mean_z)
        zs = [float(l.split()[3]) for l in open(loc) if l.startswith("v ")]
        assert zs == [-6.0, 0.0, 6.0], zs
        assert "mtllib m.mtl" in open(loc).read()      # mtl reference preserved
    print("ok  3d-tiles: OBJ stats (mean Z, area) + Z-localisation")


def test_auto_divisions():
    assert t3._auto_divisions(48.0) == 1                # tiny scene
    assert t3._auto_divisions(0.0) == 1
    big = t3._auto_divisions(5_000_000.0)               # ~5 km² → more tiles
    assert 1 <= big <= 4, big
    assert big > 1
    print(f"ok  3d-tiles: auto-divisions (small=1, large={big})")


def test_placement_projection():
    try:
        from pyproj import Transformer
    except ImportError:
        print("skip 3d-tiles-projection (needs pyproj — present in the Effigies image)")
        return
    # a UTM 32N easting/northing in N-Germany -> plausible WGS84 lat/lon
    lon, lat = Transformer.from_crs("EPSG:32632", "EPSG:4326", always_xy=True).transform(
        415706.7, 5958530.4)
    assert 5.0 < lon < 9.0 and 53.0 < lat < 54.0, (lon, lat)
    print(f"ok  3d-tiles: offset -> WGS84 placement (lat {lat:.3f}, lon {lon:.3f})")


def test_skips_without_binary_or_georef():
    # no Obj2Tiles on PATH -> skip (the first gate); only assert when truly absent
    if shutil.which("Obj2Tiles") is None:
        with tempfile.TemporaryDirectory() as work:
            assert t3.run_3d_tiles(work) is False
        print("ok  3d-tiles: skips when Obj2Tiles binary absent")
    else:
        # binary present (rebuilt image): a local-frame result must still skip
        with tempfile.TemporaryDirectory() as work:
            os.makedirs(work, exist_ok=True)
            open(os.path.join(work, "scene_dense_mesh_refine_texture.obj"), "w").close()
            json.dump({"crs": "local"},
                      open(os.path.join(work, "georef_transform.json"), "w"))
            assert t3.run_3d_tiles(work) is False
        print("ok  3d-tiles: skips a local (un-georeferenced) result")


if __name__ == "__main__":
    test_obj_stats_and_local()
    test_auto_divisions()
    test_placement_projection()
    test_skips_without_binary_or_georef()
    print("\nall 3d-tiles tests passed")
