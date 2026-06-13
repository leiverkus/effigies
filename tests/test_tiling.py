#!/usr/bin/env python3
"""Unit tests for helpers/tiling.py — the split-merge spatial partition.

The partition math is pure numpy and always runs. The subset-model writer is
tested via its pycolmap-free struct fallback unconditionally, and via pycolmap
when available (gated like test_gcp_ba).

Run:  python3 tests/test_tiling.py
"""
import os
import sys
import struct
import tempfile
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "helpers"))
import tiling as tl          # noqa: E402
import colmap_bin            # noqa: E402


# ---------------------------------------------------------------------------
# Pure partition
# ---------------------------------------------------------------------------
def test_grid_dims():
    for n in (1, 2, 4, 9, 13):
        c, r = tl.grid_dims(n, 100.0, 100.0)
        assert c * r >= n, (n, c, r)
    assert tl.grid_dims(1, 10, 10) == (1, 1)
    # aspect bias: wide extent -> more columns than rows
    c, r = tl.grid_dims(4, 400.0, 100.0)
    assert c >= r, (c, r)
    print("ok  grid_dims: cols*rows>=n, aspect-biased")


def test_cell_bounds_coverage():
    """Cells must tile the bbox exactly: disjoint, union == bbox (the invariant
    the merge crop relies on for no-double-count, no-gap ownership)."""
    bbox = (10.0, 20.0, 110.0, 70.0)            # 100 x 50
    for cols, rows in [(1, 1), (2, 3), (4, 4)]:
        bounds = tl.cell_bounds(bbox, cols, rows)
        assert len(bounds) == cols * rows
        area = sum((b[2] - b[0]) * (b[3] - b[1]) for b in bounds.values())
        assert abs(area - 100.0 * 50.0) < 1e-6, (cols, rows, area)
        for b in bounds.values():               # every cell inside bbox
            assert bbox[0] - 1e-9 <= b[0] < b[2] <= bbox[2] + 1e-9
            assert bbox[1] - 1e-9 <= b[1] < b[3] <= bbox[3] + 1e-9
    print("ok  cell_bounds: exact disjoint tiling of the bbox (coverage invariant)")


def _grid_centers(nx, ny, step=10.0):
    """nx*ny camera centres on a regular lattice."""
    return {f"img_{i}_{j}.jpg": np.array([i * step, j * step], float)
            for i in range(nx) for j in range(ny)}


def test_partition_assignment_and_halo():
    centers = _grid_centers(4, 4, step=10.0)     # 16 cameras on a 4x4 lattice
    tiles, bbox = tl.partition(centers, n_tiles=4, halo_radius=5.0)
    # 4 tiles (2x2 grid over the lattice), each owning a quadrant of 4 cameras
    assert len(tiles) == 4, len(tiles)
    for t in tiles:
        assert len(t["cameras"]) == 4, t["id"]
        # every core camera lies within the tile's xy_bounds
        b = t["xy_bounds"]
        for n in t["cameras"]:
            xy = centers[n]
            assert b[0] - 1e-9 <= xy[0] <= b[2] + 1e-9 and \
                   b[1] - 1e-9 <= xy[1] <= b[3] + 1e-9
        # halo excludes core and is non-empty (neighbours within radius exist)
        assert not (set(t["cameras"]) & set(t["halo_cameras"]))
        assert len(t["halo_cameras"]) > 0, t["id"]
    # core sets partition all cameras exactly (disjoint, complete)
    allcore = [n for t in tiles for n in t["cameras"]]
    assert sorted(allcore) == sorted(centers), "cores must partition all cameras"
    print("ok  partition: cores partition cameras, halos overlap neighbours")


def test_halo_radius_membership():
    centers = _grid_centers(6, 1, step=10.0)     # a 1-D line of 6 cameras
    tiles, _ = tl.partition(centers, n_tiles=2, halo_radius=12.0)
    # a camera 1 step (10) outside a tile's core band is within halo_radius 12
    for t in tiles:
        b = t["xy_bounds"]
        for n, xy in centers.items():
            if n in t["cameras"]:
                continue
            near = (b[0] - 12.0 <= xy[0] <= b[2] + 12.0 and
                    b[1] - 12.0 <= xy[1] <= b[3] + 12.0)
            assert (n in t["halo_cameras"]) == near, (t["id"], n)
    print("ok  halo membership matches the radius test exactly")


def test_estimate_tile_count():
    b = 1_000_000
    assert tl.estimate_tile_count(0, b, 100.0) == 1
    assert tl.estimate_tile_count(5_000, b, 100.0) == 1            # 0.5M < 1M
    assert tl.estimate_tile_count(20_000, b, 100.0) == 2           # 2.0M -> 2
    # monotonic non-decreasing in point count
    prev = 0
    for npts in (0, 10_000, 50_000, 200_000):
        c = tl.estimate_tile_count(npts, b, 100.0)
        assert c >= prev
        prev = c
    print("ok  estimate_tile_count: budget-driven, monotonic")


def test_is_connected():
    near = _grid_centers(3, 3, step=10.0)
    assert tl.is_connected(near, radius=15.0)
    # two clusters far apart -> disconnected at a small radius
    split = {**{f"a{i}.jpg": np.array([i * 5.0, 0.0]) for i in range(3)},
             **{f"b{i}.jpg": np.array([1000.0 + i * 5.0, 0.0]) for i in range(3)}}
    assert not tl.is_connected(split, radius=15.0)
    assert tl.is_connected(split, radius=2000.0)
    print("ok  is_connected: single vs multi-component camera graph")


def test_manifest_roundtrip():
    centers = _grid_centers(4, 4, step=10.0)
    man = tl.build_manifest(centers, n_points=12345, n_tiles=4)
    assert man["version"] == tl.MANIFEST_VERSION
    assert man["n_images"] == 16 and man["n_sparse_points"] == 12345
    assert all(t["status"] == "pending" for t in man["tiles"])
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "tiles_manifest.json")
        tl.write_manifest(p, man)
        back = tl.read_manifest(p)
    assert back == man, "manifest must round-trip byte-stably"
    print("ok  manifest round-trip (schema stable)")


# ---------------------------------------------------------------------------
# Subset model writer
# ---------------------------------------------------------------------------
def _write_global_model(sparse_dir, centers, n_points=20):
    """Tiny global COLMAP binary model: 1 PINHOLE camera, images at the given XY
    centres (R=I so centre C = -t), and n_points sparse points (empty tracks)."""
    os.makedirs(sparse_dir, exist_ok=True)
    with open(os.path.join(sparse_dir, "cameras.bin"), "wb") as f:
        f.write(struct.pack("<Q", 1))
        f.write(struct.pack("<ii", 1, 1))                  # cam 1, PINHOLE
        f.write(struct.pack("<QQ", 640, 480))
        f.write(struct.pack("<4d", 500.0, 500.0, 320.0, 240.0))
    with open(os.path.join(sparse_dir, "images.bin"), "wb") as f:
        f.write(struct.pack("<Q", len(centers)))
        for iid, (name, xy) in enumerate(sorted(centers.items()), 1):
            t = (-xy[0], -xy[1], -5.0)                     # R=I -> C = -t
            f.write(struct.pack("<i", iid))
            f.write(struct.pack("<7d", 1.0, 0.0, 0.0, 0.0, t[0], t[1], t[2]))
            f.write(struct.pack("<i", 1))
            f.write(name.encode("utf-8") + b"\x00")
            f.write(struct.pack("<Q", 0))                  # no points2D
    with open(os.path.join(sparse_dir, "points3D.bin"), "wb") as f:
        f.write(struct.pack("<Q", n_points))
        for pid in range(1, n_points + 1):
            f.write(struct.pack("<Q", pid))
            f.write(struct.pack("<3d", float(pid), 0.0, 0.0))
            f.write(struct.pack("<3B", 128, 128, 128))
            f.write(struct.pack("<d", 0.5))
            f.write(struct.pack("<Q", 0))                  # empty track


def test_read_points3D_and_centers():
    centers = _grid_centers(2, 2, step=10.0)
    with tempfile.TemporaryDirectory() as d:
        sp = os.path.join(d, "sparse")
        _write_global_model(sp, centers, n_points=7)
        assert tl.n_sparse_points(sp) == 7
        back = tl.camera_centers_xy(sp)
        assert set(back) == set(centers)
        for n in centers:
            assert np.allclose(back[n], centers[n]), (n, back[n], centers[n])
    print("ok  read_points3D_bin count + camera_centers_xy recover the XY lattice")


def test_subset_struct_fallback():
    """The pycolmap-free struct fallback must emit a valid subset model holding
    exactly the requested images (readable back by colmap_bin)."""
    centers = _grid_centers(3, 2, step=10.0)               # 6 images
    keep = sorted(centers)[:3]
    with tempfile.TemporaryDirectory() as d:
        sp = os.path.join(d, "sparse")
        _write_global_model(sp, centers, n_points=10)
        out = os.path.join(d, "tile_sparse")
        os.makedirs(out)
        tl._write_tile_subset_struct(sp, set(keep), out)
        imgs = colmap_bin.read_images_bin(os.path.join(out, "images.bin"))
        cams = colmap_bin.read_cameras_bin(os.path.join(out, "cameras.bin"))
        assert set(imgs) == set(keep), (set(imgs), set(keep))
        assert 1 in cams and cams[1][0] == "PINHOLE"
        assert tl.n_sparse_points(out) == 0                # empty points3D
        # centres of the kept images survive the re-pack (quat round-trip)
        back = tl.camera_centers_xy(out)
        for n in keep:
            assert np.allclose(back[n], centers[n], atol=1e-6), (n, back[n])
    print("ok  subset struct fallback: exact image subset, centres preserved")


def _have_pycolmap():
    try:
        import pycolmap  # noqa: F401
        return True
    except ImportError:
        return False


def test_subset_pycolmap():
    if not _have_pycolmap():
        print("skip subset-pycolmap (needs pycolmap — present in the Effigies image)")
        return
    centers = _grid_centers(3, 2, step=10.0)
    keep = sorted(centers)[:4]
    with tempfile.TemporaryDirectory() as d:
        sp = os.path.join(d, "sparse")
        _write_global_model(sp, centers, n_points=10)
        out = os.path.join(d, "tile_sparse")
        tl.write_tile_subset(sp, keep, out)
        imgs = colmap_bin.read_images_bin(os.path.join(out, "images.bin"))
        assert set(imgs) == set(keep), (set(imgs), set(keep))
    print("ok  subset via pycolmap: exact image subset")


if __name__ == "__main__":
    test_grid_dims()
    test_cell_bounds_coverage()
    test_partition_assignment_and_halo()
    test_halo_radius_membership()
    test_estimate_tile_count()
    test_is_connected()
    test_manifest_roundtrip()
    test_read_points3D_and_centers()
    test_subset_struct_fallback()
    test_subset_pycolmap()
    print("\nall tiling tests passed")
