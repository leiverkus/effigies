#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Patrick Leiverkus
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for helpers/camera_exports.py — cameras.json + shots.geojson.

cameras.json (intrinsics, OpenSfM-normalised) is pure numpy and always runs.
shots.geojson needs pyproj (UTM -> WGS84) and is skipped when unavailable.

Run:  python3 tests/test_camera_exports.py
"""
import os
import sys
import json
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "helpers"))
import camera_exports as ce  # noqa: E402


def test_cameras_json_normalised():
    cams = {1: ("OPENCV", 2048, 1536,
                [1419.92, 1419.92, 1024.0, 768.0, -0.01, 0.002, 0.0001, -0.0001])}
    cj = ce.build_cameras_json(cams)
    assert len(cj) == 1, cj
    e = cj[list(cj)[0]]
    assert e["projection_type"] == "brown", e
    assert e["width"] == 2048 and e["height"] == 1536, e
    assert np.isclose(e["focal_x"], 1419.92 / 2048.0), e            # normalised by max(w,h)
    assert np.isclose(e["c_x"], 0.0) and np.isclose(e["c_y"], 0.0), e  # cx=1024=w/2 -> 0
    assert e["k1"] == -0.01 and e["p2"] == -0.0001, e
    print("ok  cameras.json normalises intrinsics + carries distortion")


def _have_pyproj():
    try:
        import pyproj  # noqa: F401
        return True
    except ImportError:
        return False


def _synth_model(d):
    os.makedirs(os.path.join(d, "sparse", "0"))
    m = os.path.join(d, "sparse", "0")
    open(os.path.join(m, "cameras.txt"), "w").write(
        "1 OPENCV 2048 1536 1419.92 1419.92 1024 768 -0.01 0.002 0.0001 -0.0001\n")
    centers = {"DJI_0001.JPG": (0, 0, 0), "DJI_0002.JPG": (10, 0, 0), "DJI_0003.JPG": (0, 10, 2)}
    with open(os.path.join(m, "images.txt"), "w") as f:
        for i, (n, C) in enumerate(centers.items(), 1):
            f.write(f"{i} 1 0 0 0 {-C[0]} {-C[1]} {-C[2]} 1 {n}\n100 200 -1\n")
    open(os.path.join(m, "points3D.txt"), "w").write("")
    return m


def test_shots_geojson_wgs84():
    if not _have_pyproj():
        print("skip shots.geojson (needs pyproj — present in the Effigies image)")
        return
    with tempfile.TemporaryDirectory() as d:
        m = _synth_model(d)
        cams = ce.gb._read_cameras(m)
        tr = {"source": "colmap-exif", "s": 1.0, "R": np.eye(3).tolist(),
              "t": [415700.0, 5958560.0, 0.0], "crs": "EPSG:32632"}
        gj = ce.build_shots_geojson(m, cams, tr)
    assert gj["type"] == "FeatureCollection" and len(gj["features"]) == 3, gj
    f0 = gj["features"][0]
    lon, lat, _ = f0["geometry"]["coordinates"]
    assert 7.7 < lon < 7.75 and 53.75 < lat < 53.78, (lon, lat)   # UTM32N -> WGS84
    for key in ("filename", "camera", "focal", "width", "height",
                "capture_time", "translation", "rotation"):
        assert key in f0["properties"], (key, f0["properties"])
    print("ok  shots.geojson writes WGS84 camera positions with ODM properties")


def test_shots_skips_local_frame():
    if not _have_pyproj():
        print("skip shots-local (needs pyproj)")
        return
    with tempfile.TemporaryDirectory() as d:
        m = _synth_model(d)
        cams = ce.gb._read_cameras(m)
        gj = ce.build_shots_geojson(m, cams, {"crs": "local"})
    assert gj is None
    print("ok  shots.geojson skips a local (un-georeferenced) result")


def test_reland_transform_gate():
    """_reland_transform returns the 4x4 only when a re-land actually happened."""
    T = np.eye(4); T[:3, 3] = [1.0, 2.0, 3.0]
    flat = T.reshape(-1).tolist()
    with tempfile.TemporaryDirectory() as d:
        rd = os.path.join(d, "odm_report"); os.makedirs(rd)
        cdj = os.path.join(rd, "change_detection.json")
        json.dump({"relanded": {"mesh": "m.obj", "cloud": "c.laz"},
                   "coregistration": {"transform": flat}}, open(cdj, "w"))
        got = ce._reland_transform(d)
        assert got is not None and np.allclose(got, T), got
        json.dump({"relanded": {"error": "x"},
                   "coregistration": {"transform": flat}}, open(cdj, "w"))
        assert ce._reland_transform(d) is None                 # failed re-land
        json.dump({"coregistration": {"transform": flat}}, open(cdj, "w"))
        assert ce._reland_transform(d) is None                 # additive (no marker)
        no = os.path.join(d, "empty")
    assert ce._reland_transform(no) is None                    # no file
    print("ok  _reland_transform gates on the relanded marker")


def test_shots_reland_shifts_positions():
    if not _have_pyproj():
        print("skip shots-reland (needs pyproj)")
        return
    with tempfile.TemporaryDirectory() as d:
        m = _synth_model(d)
        cams = ce.gb._read_cameras(m)
        tr = {"s": 1.0, "R": np.eye(3).tolist(),
              "t": [415700.0, 5958560.0, 0.0], "crs": "EPSG:32632"}
        base = ce.build_shots_geojson(m, cams, tr)
        T = np.eye(4); T[:3, 3] = [0.05, -0.03, 0.02]          # 5/3/2 cm re-land
        rel = ce.build_shots_geojson(m, cams, tr, reland=T)
    for fb, fr in zip(base["features"], rel["features"]):
        shift = np.asarray(fr["properties"]["translation"]) - \
                np.asarray(fb["properties"]["translation"])
        assert np.allclose(shift, [0.05, -0.03, 0.02], atol=1e-6), shift
    print("ok  shots.geojson re-land shifts camera positions by the transform")


if __name__ == "__main__":
    test_cameras_json_normalised()
    test_shots_geojson_wgs84()
    test_shots_skips_local_frame()
    test_reland_transform_gate()
    test_shots_reland_shifts_positions()
    print("\nall camera-export tests passed")
