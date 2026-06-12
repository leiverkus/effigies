#!/usr/bin/env python3
"""Minimal reader for COLMAP's BINARY model format (cameras.bin / images.bin).

The undistorted workspace (``dense/sparse``) that ``image_undistorter`` writes is
binary-only and holds the PINHOLE cameras the undistorted images correspond to —
exactly what view-projection needs (georef_bridge reads the TEXT model of the
original, distorted cameras instead).

Format reference: COLMAP src/colmap/scene/reconstruction_io.cc.
"""
import os
import struct

import numpy as np

# model_id -> (name, #params); only the models image_undistorter can emit matter
CAMERA_MODELS = {0: ("SIMPLE_PINHOLE", 3), 1: ("PINHOLE", 4)}


def read_cameras_bin(path):
    """-> {camera_id: (model_name, width, height, params[np.array])}"""
    cams = {}
    with open(path, "rb") as f:
        (n,) = struct.unpack("<Q", f.read(8))
        for _ in range(n):
            cid, model_id = struct.unpack("<ii", f.read(8))
            w, h = struct.unpack("<QQ", f.read(16))
            name, np_ = CAMERA_MODELS.get(model_id, (None, None))
            if name is None:
                raise ValueError(f"unsupported camera model id {model_id} "
                                 f"(expected pinhole after undistortion)")
            params = np.frombuffer(f.read(8 * np_), dtype="<f8").copy()
            cams[cid] = (name, int(w), int(h), params)
    return cams


def read_images_bin(path):
    """-> {name: dict(R[3,3], t[3], camera_id)} — poses are world-to-cam."""
    out = {}
    with open(path, "rb") as f:
        (n,) = struct.unpack("<Q", f.read(8))
        for _ in range(n):
            _img_id = struct.unpack("<i", f.read(4))[0]
            qw, qx, qy, qz, tx, ty, tz = struct.unpack("<7d", f.read(56))
            cam_id = struct.unpack("<i", f.read(4))[0]
            name = b""
            while True:
                c = f.read(1)
                if c == b"\x00":
                    break
                name += c
            (npts,) = struct.unpack("<Q", f.read(8))
            f.seek(npts * 24, os.SEEK_CUR)        # skip points2D (x, y, pt3d_id)
            # quaternion -> rotation matrix
            q = np.array([qw, qx, qy, qz], dtype=np.float64)
            q /= np.linalg.norm(q)
            w_, x, y, z = q
            R = np.array([
                [1 - 2 * (y * y + z * z), 2 * (x * y - w_ * z), 2 * (x * z + w_ * y)],
                [2 * (x * y + w_ * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w_ * x)],
                [2 * (x * z - w_ * y), 2 * (y * z + w_ * x), 1 - 2 * (x * x + y * y)],
            ])
            out[name.decode("utf-8", "ignore")] = {
                "R": R, "t": np.array([tx, ty, tz]), "camera_id": cam_id}
    return out
