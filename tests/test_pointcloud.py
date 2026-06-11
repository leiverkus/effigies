#!/usr/bin/env python3
"""Unit tests for helpers/pointcloud_to_laz.py — the transform-matrix builder.

Only the pure-numpy matrix construction is tested here; the PDAL / entwine calls
are external binaries exercised by an end-to-end run, not by unit tests.

Run:  python3 tests/test_pointcloud.py
"""
import os
import sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "helpers"))
import pointcloud_to_laz as pc  # noqa: E402


def test_identity_for_local_frame():
    """none / local-only transforms must yield the 4x4 identity (cloud unchanged)."""
    M = pc.build_transform_matrix(
        {"s": 1.0, "R": np.eye(3).tolist(), "t": [0, 0, 0]})
    assert np.allclose(M, np.eye(4)), M
    print("ok  identity matrix for local frame")


def test_matrix_matches_obj_convention():
    """build_transform_matrix must apply world = s*R@v + t (the projected convention,
    i.e. the OBJ formula WITHOUT the float-precision offset subtracted)."""
    s = 2.5
    ang = 0.4
    R = np.array([[np.cos(ang), -np.sin(ang), 0],
                  [np.sin(ang),  np.cos(ang), 0],
                  [0, 0, 1]])
    t = np.array([690000.0, 3540000.0, 100.0])
    M = pc.build_transform_matrix({"s": s, "R": R.tolist(), "t": t.tolist()})

    rng = np.random.default_rng(0)
    pts = rng.standard_normal((20, 3)) * 5.0
    expected = (s * (R @ pts.T).T) + t
    homog = np.hstack([pts, np.ones((pts.shape[0], 1))])
    got = (M @ homog.T).T[:, :3]
    assert np.abs(got - expected).max() < 1e-9, "matrix does not match s*R@v + t"
    print("ok  matrix matches s*R@v + t convention")


def test_pdal_string_is_16_row_major():
    M = np.arange(16, dtype=float).reshape(4, 4)
    s = pc._matrix_to_pdal_string(M)
    vals = [float(x) for x in s.split()]
    assert len(vals) == 16, f"expected 16 values, got {len(vals)}"
    assert vals == list(range(16)), "row-major order not preserved"
    print("ok  pdal matrix string is 16-value row-major")


if __name__ == "__main__":
    test_identity_for_local_frame()
    test_matrix_matches_obj_convention()
    test_pdal_string_is_16_row_major()
    print("\nall pointcloud tests passed")
