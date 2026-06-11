#!/usr/bin/env python3
"""
Export the camera assets WebODM/ODM expose for download:

  * ``cameras.json``            (project root)  — camera intrinsics, OpenSfM-style
  * ``odm_report/shots.geojson`` — one WGS84 point per image (camera positions on
    the map), with filename / camera / focal / pose properties

Both are derived from the COLMAP text model (cameras.txt + images.txt) and the
similarity in ``georef_transform.json``. ``shots.geojson`` needs a projected CRS,
so it is skipped for a local-only (un-georeferenced) result; ``cameras.json`` is
written regardless (intrinsics are frame-independent).
"""
import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import georef_bridge as gb  # noqa: E402  (reuse the COLMAP readers)


def _intrinsics(model, p):
    """COLMAP (model, params) -> (fx, fy, cx, cy, dist{}, projection_type)."""
    if model == "SIMPLE_PINHOLE":            # f, cx, cy
        return p[0], p[0], p[1], p[2], {}, "perspective"
    if model == "PINHOLE":                   # fx, fy, cx, cy
        return p[0], p[1], p[2], p[3], {}, "perspective"
    if model == "SIMPLE_RADIAL":             # f, cx, cy, k1
        return p[0], p[0], p[1], p[2], {"k1": p[3], "k2": 0.0}, "perspective"
    if model == "RADIAL":                    # f, cx, cy, k1, k2
        return p[0], p[0], p[1], p[2], {"k1": p[3], "k2": p[4]}, "perspective"
    if model in ("OPENCV", "FULL_OPENCV"):   # fx, fy, cx, cy, k1, k2, p1, p2[, k3..]
        d = {"k1": p[4], "k2": p[5], "p1": p[6], "p2": p[7]}
        if model == "FULL_OPENCV" and len(p) > 8:
            d["k3"] = p[8]
        return p[0], p[1], p[2], p[3], d, "brown"
    if model == "OPENCV_FISHEYE":            # fx, fy, cx, cy, k1, k2, k3, k4
        return p[0], p[1], p[2], p[3], {"k1": p[4], "k2": p[5], "k3": p[6], "k4": p[7]}, "fisheye"
    # unknown: assume the first four are fx, fy, cx, cy
    return p[0], p[1], p[2], p[3], {}, "perspective"


def _cam_key(cam_id, model, w, h):
    return f"effigies {model.lower()} {w} {h} cam{cam_id}"


def build_cameras_json(cams):
    """OpenSfM-style cameras.json: focal/principal point normalised by max(w,h)."""
    out = {}
    for cid, (model, w, h, params) in cams.items():
        fx, fy, cx, cy, dist, proj = _intrinsics(model, params)
        norm = float(max(w, h))
        entry = {
            "projection_type": proj,
            "width": int(w), "height": int(h),
            "focal_x": fx / norm, "focal_y": fy / norm,
            "c_x": (cx - w / 2.0) / norm, "c_y": (cy - h / 2.0) / norm,
        }
        entry.update({k: float(v) for k, v in dist.items()})
        out[_cam_key(cid, model, w, h)] = entry
    return out


def _rodrigues(R):
    """Rotation matrix -> axis-angle 3-vector (ODM shots.geojson 'rotation')."""
    R = np.asarray(R, dtype=np.float64)
    ang = np.arccos(np.clip((np.trace(R) - 1.0) / 2.0, -1.0, 1.0))
    if ang < 1e-8:
        return [0.0, 0.0, 0.0]
    axis = np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]])
    axis = axis / (2.0 * np.sin(ang))
    return (axis * ang).tolist()


def build_shots_geojson(model_dir, cams, transform):
    """One WGS84 Point feature per image (camera centre), or None if not
    georeferenced (no projected CRS to invert to lon/lat)."""
    crs = transform.get("crs")
    if not crs or str(crs).lower() == "local":
        return None
    try:
        from pyproj import Transformer
    except ImportError as e:
        print(f"[cameras] pyproj missing, cannot write shots.geojson: {e}", file=sys.stderr)
        return None

    s = float(transform.get("s", 1.0))
    Rg = np.asarray(transform.get("R", np.eye(3).tolist()), dtype=np.float64)
    tg = np.asarray(transform.get("t", [0, 0, 0]), dtype=np.float64)
    to_wgs = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)

    images = gb._read_images_full(model_dir)
    feats = []
    for name, im in images.items():
        R = np.asarray(im["R"]); t = np.asarray(im["t"])
        C = -R.T @ t                              # camera centre, local frame
        world = s * (Rg @ C) + tg                 # -> projected CRS (full coords)
        lon, lat, alt = to_wgs.transform(world[0], world[1], world[2])
        cid = im["cam_id"]
        model, w, h, params = cams[cid]
        fx = _intrinsics(model, params)[0]
        feats.append({
            "type": "Feature",
            "properties": {
                "filename": name, "camera": _cam_key(cid, model, w, h),
                "focal": float(fx), "width": int(w), "height": int(h),
                "capture_time": 0,
                "translation": world.tolist(),
                "rotation": _rodrigues(R @ Rg.T),  # orientation in the projected frame
            },
            "geometry": {"type": "Point", "coordinates": [lon, lat, alt]},
        })
    return {"type": "FeatureCollection", "features": feats}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True, help="OpenMVS workdir")
    args = ap.parse_args()

    model_dir = gb._find_colmap_model(args.work)
    if model_dir is None:
        print("[cameras] no COLMAP text model; skipping camera exports", file=sys.stderr)
        return
    cams = gb._read_cameras(model_dir)

    cameras_json = build_cameras_json(cams)
    with open(os.path.join(args.work, "cameras.json"), "w") as f:
        json.dump(cameras_json, f, indent=2)
    print(f"[cameras] wrote cameras.json ({len(cameras_json)} camera(s))")

    tr_path = os.path.join(args.work, "georef_transform.json")
    transform = json.load(open(tr_path)) if os.path.exists(tr_path) else {"crs": "local"}
    shots = build_shots_geojson(model_dir, cams, transform)
    if shots is None:
        print("[cameras] not georeferenced; skipping shots.geojson", file=sys.stderr)
        return
    with open(os.path.join(args.work, "shots.geojson"), "w") as f:
        json.dump(shots, f)
    print(f"[cameras] wrote shots.geojson ({len(shots['features'])} shot(s), WGS84)")


if __name__ == "__main__":
    main()
