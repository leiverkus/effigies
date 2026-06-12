#!/usr/bin/env python3
"""
Convert the OpenMVS dense point cloud into the georeferenced WebODM assets.

OpenMVS' DensifyPointCloud emits ``scene_dense.ply`` in the local SfM frame.
WebODM expects a georeferenced LAS/LAZ at
``odm_georeferencing/odm_georeferenced_model.laz`` and, for the Potree viewer,
an EPT tileset under ``entwine_pointcloud/``.

This step:
  1. reads the similarity transform produced by ``georef_bridge.py``
     (``georef_transform.json``: scale ``s``, rotation ``R``, translation ``t``,
     ``offset``, ``crs``),
  2. applies it to the dense cloud and writes a LAZ via PDAL,
  3. optionally builds an EPT tileset (entwine) if present.

Coordinate convention (matches ODM):
  * The **point cloud** is written in FULL projected coordinates
    ``world = s * R @ v + t``. LAS stores coordinates as scaled int32 with a
    header offset, so large projected values keep millimetre precision — no need
    to subtract the float-precision offset that the textured OBJ uses.
  * For ``none`` / ``local-only`` / ``opensfm`` transforms this reduces to the
    identity, so the cloud is written in its existing (local or already-aligned)
    frame — still as a valid LAZ.

External tools are invoked as separate programs; if PDAL is missing the step
fails loudly (the LAZ is a required asset). EPT is best-effort: if no builder is
found the LAZ is still produced and the missing EPT is reported, not hidden.

Dependencies: numpy (required); PDAL + entwine are external binaries.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

import numpy as np


def build_transform_matrix(transform):
    """Return a 16-element row-major 4x4 matrix for PDAL ``filters.transformation``.

    Applies ``world = s * R @ v + t`` (the projected-coordinate convention; the
    OBJ-only ``offset`` is intentionally NOT subtracted here — see module docstring).
    ``transform`` is the parsed ``georef_transform.json`` dict.
    """
    s = float(transform.get("s", 1.0))
    R = np.asarray(transform.get("R", np.eye(3).tolist()), dtype=float)
    t = np.asarray(transform.get("t", [0.0, 0.0, 0.0]), dtype=float)
    if R.shape != (3, 3):
        raise ValueError(f"R must be 3x3, got {R.shape}")
    A = s * R
    M = np.eye(4)
    M[:3, :3] = A
    M[:3, 3] = t
    return M


def _matrix_to_pdal_string(M):
    """PDAL wants the 16 matrix entries as a single space-separated, row-major string."""
    return " ".join(f"{v:.12g}" for v in np.asarray(M, dtype=float).reshape(-1))


def ply_to_laz(ply_path, laz_path, transform, srs=None):
    """Run PDAL to transform ``ply_path`` and write a compressed ``laz_path``.

    Raises RuntimeError if PDAL is unavailable or the conversion fails — the LAZ
    is a required WebODM asset, so we do not silently skip it.
    """
    if shutil.which("pdal") is None:
        raise RuntimeError("pdal not found on PATH; cannot write the required LAZ asset")

    M = build_transform_matrix(transform)
    stages = [
        {"type": "readers.ply", "filename": ply_path},
        {"type": "filters.transformation", "matrix": _matrix_to_pdal_string(M)},
    ]
    writer = {
        "type": "writers.las",
        "filename": laz_path,
        "compression": "laszip",
        # auto header offset + mm scale keeps full projected coordinates precise
        "offset_x": "auto", "offset_y": "auto", "offset_z": "auto",
        "scale_x": 0.001, "scale_y": 0.001, "scale_z": 0.001,
    }
    if srs and srs not in ("local", "auto", ""):
        writer["a_srs"] = srs
    stages.append(writer)

    pipeline = json.dumps({"pipeline": stages})
    os.makedirs(os.path.dirname(laz_path), exist_ok=True)
    proc = subprocess.run(
        ["pdal", "pipeline", "--stdin"],
        input=pipeline, text=True, capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"pdal pipeline failed:\n{proc.stderr.strip()}")
    print(f"[pointcloud] wrote {os.path.basename(laz_path)} "
          f"(srs={srs or 'local'})")


def build_ept(laz_path, ept_dir):
    """Best-effort EPT build for the Potree viewer. Returns True on success.

    Needs ``entwine``; if absent, reports and returns
    False (the LAZ alone is still a usable asset)."""
    # entwine only: untwine is NOT a substitute — since 1.x it writes a single
    # COPC file and cannot produce the EPT directory WebODM's viewer reads
    # (entwine_pointcloud/ept.json).
    if not shutil.which("entwine"):
        print("[pointcloud] entwine not found; "
              "skipping entwine_pointcloud (LAZ still written)", file=sys.stderr)
        return False
    cmd = ["entwine", "build", "-i", laz_path, "-o", ept_dir]
    os.makedirs(ept_dir, exist_ok=True)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"[pointcloud] EPT build failed (non-fatal):\n{proc.stderr.strip()}",
              file=sys.stderr)
        return False
    print(f"[pointcloud] built EPT tileset at {os.path.basename(ept_dir)}")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True, help="OpenMVS workdir")
    ap.add_argument("--ply", default=None, help="dense cloud (default: <work>/scene_dense.ply)")
    ap.add_argument("--ept", action="store_true", help="also build an EPT tileset")
    args = ap.parse_args()

    ply = args.ply or os.path.join(args.work, "scene_dense.ply")
    if not os.path.exists(ply):
        print(f"[pointcloud] no dense cloud at {ply}; nothing to convert", file=sys.stderr)
        return

    tr_path = os.path.join(args.work, "georef_transform.json")
    if os.path.exists(tr_path):
        with open(tr_path) as f:
            transform = json.load(f)
    else:
        print("[pointcloud] no georef_transform.json; writing cloud in local frame",
              file=sys.stderr)
        transform = {"s": 1.0, "R": np.eye(3).tolist(), "t": [0, 0, 0], "crs": "local"}

    laz = os.path.join(args.work, "odm_georeferenced_model.laz")
    ply_to_laz(ply, laz, transform, srs=transform.get("crs"))

    if args.ept:
        build_ept(laz, os.path.join(args.work, "entwine_pointcloud"))


if __name__ == "__main__":
    main()
