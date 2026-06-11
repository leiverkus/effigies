#!/usr/bin/env python3
"""
Georeferencing bridge for Effigies.

OpenMVS works in the local SfM coordinate frame. WebODM expects a georeferenced,
projected model plus an offset so its 3D viewer and DEM/ortho logic work. ODM does
this internally via OpenSfM; with the COLMAP path we do it here.

Strategy (selected by --georeference):
  auto : gcp-file if present  ->  else EXIF-GPS  ->  else local-only (scaled)
  gcp  : require a GCP file (gcp_list.txt, ODM format)
  exif : require EXIF-GPS on the images
  none : skip georeferencing; keep the local frame as-is. Recommended for
         turntable / close-range objects that have no meaningful world position.
         The model stays metrically consistent (COLMAP/OpenMVS scale is internally
         consistent); only absolute placement is omitted.

The transform is a 3D similarity (Helmert: scale + rotation + translation) solved
as a Umeyama fit on >=3 non-collinear correspondences. For georeferenced output we
also emit a UTM/projected offset so vertices stay within float precision; the OBJ
is rewritten in place with offset-subtracted coordinates.

Correspondences:
  * GCP path  : gcp_list.txt gives world (projected) + pixel + image. We back out
    the local 3D position of each GCP from COLMAP by triangulating the marked
    pixel across its images (here: nearest sparse point lookup; see _gcp_local).
  * EXIF path : pair each image's COLMAP camera center (local frame) with its
    EXIF-GPS position reprojected into the target CRS. Camera-center correspondence
    needs >=3 well-distributed images; collinear flight lines degrade the solve.

Dependencies: numpy (required); pyproj and piexif/Pillow used only on the EXIF path.
"""
import argparse
import json
import os
import sys
import glob
import numpy as np


# ---------------------------------------------------------------------------
# Umeyama similarity
# ---------------------------------------------------------------------------
def umeyama_similarity(src, dst):
    """Estimate s, R, t so that  dst ~= s * R @ src + t  (Umeyama 1991).
    src, dst: (N,3) arrays of corresponding points. Returns (s, R(3x3), t(3,))."""
    src = np.asarray(src, float)
    dst = np.asarray(dst, float)
    n = src.shape[0]
    if n < 3:
        raise ValueError(f"need >=3 correspondences for a similarity, got {n}")
    mu_s = src.mean(0)
    mu_d = dst.mean(0)
    sc = src - mu_s
    dc = dst - mu_d
    cov = (dc.T @ sc) / n
    U, D, Vt = np.linalg.svd(cov)
    S = np.eye(3)
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:
        S[2, 2] = -1
    R = U @ S @ Vt
    var_s = (sc ** 2).sum() / n
    s = float(np.trace(np.diag(D) @ S) / var_s)
    t = mu_d - s * R @ mu_s
    return s, R, t


# ---------------------------------------------------------------------------
# COLMAP model reading (TEXT format: cameras.txt / images.txt / points3D.txt)
# ---------------------------------------------------------------------------
def _find_colmap_model(work):
    for cand in (os.path.join(work, "sparse", "0"), os.path.join(work, "sparse")):
        if os.path.exists(os.path.join(cand, "images.txt")):
            return cand
    return None


def read_colmap_camera_centers(model_dir):
    """Return {image_name: C} where C is the camera center in the local frame.
    COLMAP images.txt stores world-to-cam (R, t); center C = -R^T t."""
    centers = {}
    images_txt = os.path.join(model_dir, "images.txt")
    with open(images_txt) as f:
        lines = [l for l in f if not l.startswith("#") and l.strip()]
    # images.txt: every image is TWO lines; the 1st has the pose, the 2nd the 2D pts
    for i in range(0, len(lines), 2):
        parts = lines[i].split()
        if len(parts) < 10:
            continue
        qw, qx, qy, qz = map(float, parts[1:5])
        tx, ty, tz = map(float, parts[5:8])
        name = parts[9]
        R = _quat_to_rot(qw, qx, qy, qz)
        t = np.array([tx, ty, tz])
        C = -R.T @ t
        centers[name] = C
    return centers


def read_colmap_points(model_dir):
    """Return (ids->xyz) for sparse 3D points (local frame)."""
    pts = {}
    p3d = os.path.join(model_dir, "points3D.txt")
    if not os.path.exists(p3d):
        return pts
    with open(p3d) as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            parts = line.split()
            pts[int(parts[0])] = np.array(list(map(float, parts[1:4])))
    return pts


def _quat_to_rot(qw, qx, qy, qz):
    n = np.sqrt(qw*qw + qx*qx + qy*qy + qz*qz)
    qw, qx, qy, qz = qw/n, qx/n, qy/n, qz/n
    return np.array([
        [1-2*(qy*qy+qz*qz), 2*(qx*qy-qz*qw),   2*(qx*qz+qy*qw)],
        [2*(qx*qy+qz*qw),   1-2*(qx*qx+qz*qz), 2*(qy*qz-qx*qw)],
        [2*(qx*qz-qy*qw),   2*(qy*qz+qx*qw),   1-2*(qx*qx+qy*qy)],
    ])


# ---------------------------------------------------------------------------
# GCP path
# ---------------------------------------------------------------------------
def parse_gcp_list(gcp_path):
    """Parse an ODM-style gcp_list.txt.
    Line 1: a CRS / proj string (e.g. 'EPSG:32637' or a +proj string).
    Following lines: geo_x geo_y geo_z im_x im_y image_name [extra...]
    Returns (crs_header, [ {world:(x,y,z), px:(u,v), image:name}, ... ])."""
    entries = []
    with open(gcp_path) as f:
        raw = [l.rstrip("\n") for l in f if l.strip() and not l.startswith("#")]
    if not raw:
        return None, entries
    crs_header = raw[0].strip()
    for line in raw[1:]:
        p = line.split()
        if len(p) < 6:
            continue
        entries.append({
            "world": np.array(list(map(float, p[0:3]))),
            "px": np.array(list(map(float, p[3:5]))),
            "image": p[5],
        })
    return crs_header, entries


def _read_cameras(model_dir):
    """Return {camera_id: (model, w, h, params[])} from cameras.txt."""
    cams = {}
    p = os.path.join(model_dir, "cameras.txt")
    with open(p) as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            t = line.split()
            cams[int(t[0])] = (t[1], int(t[2]), int(t[3]), list(map(float, t[4:])))
    return cams


def _read_images_full(model_dir):
    """Return {image_name: dict(R, t, cam_id, obs=[(u,v,point3D_id), ...])}."""
    out = {}
    p = os.path.join(model_dir, "images.txt")
    with open(p) as f:
        lines = [l for l in f if not l.startswith("#")]
    # drop blank-only leading lines but keep pairing: pose line then obs line
    lines = [l.rstrip("\n") for l in lines]
    i = 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        p1 = lines[i].split()
        if len(p1) < 10:
            i += 1
            continue
        qw, qx, qy, qz = map(float, p1[1:5])
        tx, ty, tz = map(float, p1[5:8])
        cam_id = int(p1[8])
        name = p1[9]
        obs = []
        obs_line = lines[i+1] if i+1 < len(lines) else ""
        toks = obs_line.split()
        for j in range(0, len(toks) - 2, 3):
            u, v, pid = float(toks[j]), float(toks[j+1]), int(toks[j+2])
            if pid != -1:
                obs.append((u, v, pid))
        out[name] = {"R": _quat_to_rot(qw, qx, qy, qz),
                     "t": np.array([tx, ty, tz]), "cam_id": cam_id, "obs": obs}
        i += 2
    return out


def gcp_correspondences(model_dir, gcp_entries):
    """Build (local, world) correspondences from GCPs.

    A GCP's WORLD position comes from gcp_list.txt directly. Its LOCAL position is
    recovered from COLMAP: for the image the GCP is marked in, we take the observed
    sparse 3D point whose reprojected pixel is nearest the marked (px, py). That
    point's 3D coordinate (already in the local frame) is the local correspondent.
    Multiple markings of the same physical GCP are averaged.
    """
    points = read_colmap_points(model_dir)
    if not points:
        raise RuntimeError("COLMAP points3D.txt empty/missing; cannot localize GCPs")
    cams = _read_cameras(model_dir)
    images = _read_images_full(model_dir)

    def project(img, X):
        """Pinhole project local point X into image pixels (SIMPLE/RADIAL/OPENCV
        share fx, cx, cy as first params; distortion ignored for nearest-match)."""
        Xc = img["R"] @ X + img["t"]
        if Xc[2] <= 0:
            return None
        x, y = Xc[0]/Xc[2], Xc[1]/Xc[2]
        model, w, h, params = cams[img["cam_id"]]
        f = params[0]
        # cx, cy position depends on model; use principal point if present else center
        if model in ("SIMPLE_PINHOLE", "SIMPLE_RADIAL", "RADIAL"):
            cx, cy = params[1], params[2]
        else:  # PINHOLE / OPENCV / FULL_OPENCV: fx, fy, cx, cy, ...
            cx, cy = params[2], params[3]
        return np.array([f*x + cx, f*y + cy])

    groups = {}
    for e in gcp_entries:
        groups.setdefault(tuple(np.round(e["world"], 4)), []).append(e)

    local_list, world_list = [], []
    for key, es in groups.items():
        locs = []
        for e in es:
            img = images.get(e["image"]) or images.get(os.path.basename(e["image"]))
            if img is None or not img["obs"]:
                continue
            best, best_d = None, float("inf")
            for (u, v, pid) in img["obs"]:
                if pid not in points:
                    continue
                # nearest observation pixel to the marked pixel
                d = (u - e["px"][0])**2 + (v - e["px"][1])**2
                if d < best_d:
                    best_d, best = d, points[pid]
            if best is not None:
                locs.append(best)
        if locs:
            local_list.append(np.mean(locs, axis=0))
            world_list.append(np.array(key))
    if len(local_list) < 3:
        raise RuntimeError(
            f"only {len(local_list)} GCPs could be localized in COLMAP "
            f"(need >=3 with matching observations)")
    return np.array(local_list), np.array(world_list)


# ---------------------------------------------------------------------------
# EXIF-GPS path
# ---------------------------------------------------------------------------
def exif_correspondences(model_dir, images_dir, target_crs):
    """Pair COLMAP camera centers (local) with EXIF-GPS (reprojected to target)."""
    try:
        from PIL import Image
        from PIL.ExifTags import GPSTAGS, TAGS
    except Exception as e:
        raise RuntimeError(f"Pillow required for EXIF path: {e}")
    try:
        from pyproj import Transformer
    except Exception as e:
        raise RuntimeError(f"pyproj required for EXIF path: {e}")

    centers = read_colmap_camera_centers(model_dir)
    if not centers:
        raise RuntimeError("no COLMAP camera centers found")

    def _gps(img_path):
        img = Image.open(img_path)
        exif = img._getexif() or {}
        gps = {}
        for k, v in exif.items():
            if TAGS.get(k) == "GPSInfo":
                for gk, gv in v.items():
                    gps[GPSTAGS.get(gk, gk)] = gv
        if not gps or "GPSLatitude" not in gps or "GPSLongitude" not in gps:
            return None
        def dms(x):
            d, m, s = x
            return float(d) + float(m)/60 + float(s)/3600
        lat = dms(gps["GPSLatitude"])
        lon = dms(gps["GPSLongitude"])
        if gps.get("GPSLatitudeRef") == "S":
            lat = -lat
        if gps.get("GPSLongitudeRef") == "W":
            lon = -lon
        alt = float(gps.get("GPSAltitude", 0))
        return lat, lon, alt

    # Determine target CRS: explicit, else UTM derived from first fix
    first = None
    fixes = {}
    for name in centers:
        candidates = [os.path.join(images_dir, name), os.path.join(images_dir, os.path.basename(name))]
        ip = next((c for c in candidates if os.path.exists(c)), None)
        if not ip:
            continue
        g = _gps(ip)
        if g:
            fixes[name] = g
            first = first or g
    if len(fixes) < 3:
        raise RuntimeError(f"need >=3 EXIF-GPS fixes, found {len(fixes)}")

    if target_crs and target_crs not in ("auto", "local"):
        epsg = target_crs
    else:
        lat, lon, _ = first
        zone = int((lon + 180) / 6) + 1
        epsg = f"EPSG:{32600 + zone if lat >= 0 else 32700 + zone}"

    tf = Transformer.from_crs("EPSG:4326", epsg, always_xy=True)
    local_list, world_list = [], []
    for name, (lat, lon, alt) in fixes.items():
        x, y = tf.transform(lon, lat)
        world_list.append([x, y, alt])
        local_list.append(centers[name])
    return np.array(local_list), np.array(world_list), epsg


# ---------------------------------------------------------------------------
# Apply transform to the textured OBJ (offset-subtracted to keep float precision)
# ---------------------------------------------------------------------------
def apply_to_obj(work, s, R, t, offset):
    obj = None
    # TextureMesh appends "_texture" to the input mesh name; prefer the textured
    # OBJ (refined first), falling back to the untextured mesh names.
    for cand in ("scene_dense_mesh_refine_texture.obj", "scene_dense_mesh_texture.obj",
                 "scene_dense_mesh_refine.obj", "scene_dense_mesh.obj"):
        p = os.path.join(work, cand)
        if os.path.exists(p):
            obj = p
            break
    if not obj:
        print("[georef] no OBJ to transform (mesh disabled?)", file=sys.stderr)
        return
    tmp = obj + ".tmp"
    R = np.asarray(R); t = np.asarray(t); offset = np.asarray(offset)
    with open(obj) as fin, open(tmp, "w") as fout:
        for line in fin:
            if line.startswith("v "):
                _, x, y, z = line.split()[:4]
                v = np.array([float(x), float(y), float(z)])
                w = s * R @ v + t - offset
                fout.write(f"v {w[0]:.6f} {w[1]:.6f} {w[2]:.6f}\n")
            else:
                fout.write(line)
    os.replace(tmp, obj)
    print(f"[georef] rewrote {os.path.basename(obj)} (offset={offset.tolist()})")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--images", required=True)
    ap.add_argument("--sparse-engine", required=True)
    ap.add_argument("--georeference", default="auto",
                    choices=["auto", "gcp", "exif", "none"])
    ap.add_argument("--crs", default="auto")
    ap.add_argument("--gcp", default="")
    args = ap.parse_args()

    transform_path = os.path.join(args.work, "georef_transform.json")
    mode = args.georeference

    def write(tr):
        with open(transform_path, "w") as f:
            json.dump(tr, f, indent=2)
        print(f"[georef] wrote {transform_path} (source={tr['source']})")

    # ---- explicit 'none': object-centric, keep local frame -----------------
    if mode == "none":
        print("[georef] mode=none: keeping local (object-centric) frame, no georeferencing")
        write({"source": "none", "s": 1.0, "R": np.eye(3).tolist(),
               "t": [0, 0, 0], "offset": [0, 0, 0], "crs": "local"})
        return

    # ---- OpenSfM already geo-aligned --------------------------------------
    if args.sparse_engine == "opensfm" and mode in ("auto", "exif", "gcp"):
        print("[georef] OpenSfM path: reconstruction already geo-aligned")
        write({"source": "opensfm", "s": 1.0, "R": np.eye(3).tolist(),
               "t": [0, 0, 0], "offset": [0, 0, 0], "crs": args.crs})
        return

    model_dir = _find_colmap_model(args.work)
    if model_dir is None:
        print("[georef] no COLMAP text model found; cannot georeference.", file=sys.stderr)
        if mode in ("gcp", "exif"):
            sys.exit(2)
        write({"source": "local-only", "s": 1.0, "R": np.eye(3).tolist(),
               "t": [0, 0, 0], "offset": [0, 0, 0], "crs": "local"})
        return

    have_gcp = bool(args.gcp) and os.path.exists(args.gcp)

    # ---- resolve order per mode -------------------------------------------
    order = []
    if mode == "gcp":
        order = ["gcp"]
    elif mode == "exif":
        order = ["exif"]
    else:  # auto
        order = (["gcp"] if have_gcp else []) + ["exif"]

    last_err = None
    for attempt in order:
        try:
            if attempt == "gcp":
                if not have_gcp:
                    raise RuntimeError(f"GCP mode requested but file not found: {args.gcp}")
                crs_header, entries = parse_gcp_list(args.gcp)
                local, world = gcp_correspondences(model_dir, entries)
                s, R, t = umeyama_similarity(local, world)
                offset = world.mean(0)
                crs = args.crs if args.crs not in ("auto", "") else crs_header
                apply_to_obj(args.work, s, R, t, offset)
                write({"source": "colmap-gcp", "s": s, "R": R.tolist(),
                       "t": t.tolist(), "offset": offset.tolist(), "crs": crs})
                return
            else:  # exif
                local, world, epsg = exif_correspondences(model_dir, args.images, args.crs)
                s, R, t = umeyama_similarity(local, world)
                offset = world.mean(0)
                apply_to_obj(args.work, s, R, t, offset)
                write({"source": "colmap-exif", "s": s, "R": R.tolist(),
                       "t": t.tolist(), "offset": offset.tolist(), "crs": epsg})
                return
        except Exception as e:
            last_err = e
            print(f"[georef] {attempt} path failed: {e}", file=sys.stderr)

    # ---- fallback ----------------------------------------------------------
    if mode == "auto":
        print(f"[georef] auto: all georeferencing failed ({last_err}); "
              f"falling back to local frame.", file=sys.stderr)
        write({"source": "local-only", "s": 1.0, "R": np.eye(3).tolist(),
               "t": [0, 0, 0], "offset": [0, 0, 0], "crs": "local"})
    else:
        print(f"[georef] {mode} mode failed and no fallback permitted.", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
