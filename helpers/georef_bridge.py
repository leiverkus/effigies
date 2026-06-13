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
import math
import os
import sys
import glob
import numpy as np

# Shared OpenMVS mesh-name lookup (kept in one place so the georef bridge and the
# output mapper can never disagree on which OBJ to act on). Resolve relative to
# this file so the import works both as a script and when imported by the tests.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from openmvs_mesh import find_mesh_obj  # noqa: E402


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
    COLMAP images.txt stores world-to-cam (R, t); center C = -R^T t.

    Delegates to _read_images_full's robust pose/points2D pairing. (The earlier
    stride-2 reader filtered out blank lines, but an image registered with NO
    observed 3D points has an EMPTY points2D line — dropping it desynced the
    two-line stride and silently lost cameras. On real drone / GLOMAP
    reconstructions that pushed the EXIF-GPS fix count below 3 and produced a
    spurious local-only georef instead of using the GPS.)"""
    centers = {}
    for name, im in _read_images_full(model_dir).items():
        R = np.asarray(im["R"]); t = np.asarray(im["t"])
        centers[name] = -R.T @ t
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

    Check-point convention: a line whose trailing token (in the ODM ``[extra]``
    field) is ``check`` (case-insensitive) marks a **held-out check point** —
    measured but excluded from the georef solve / bundle adjustment, so an honest
    independent CP-RMSE can be reported (see helpers/gcp_bundle_adjust.py). Such an
    entry carries ``check=True``; all others ``check=False``.

    Returns (crs_header, [ {world:(x,y,z), px:(u,v), image:name, check:bool}, ... ])."""
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
            "check": len(p) > 6 and p[-1].lower() == "check",
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


def _split_intrinsics(model, params):
    """COLMAP camera params -> (fx, fy, cx, cy, [distortion...])."""
    if model in ("SIMPLE_PINHOLE", "SIMPLE_RADIAL", "RADIAL"):
        return params[0], params[0], params[1], params[2], list(params[3:])
    # PINHOLE / OPENCV / FULL_OPENCV / OPENCV_FISHEYE: fx fy cx cy [dist...]
    return params[0], params[1], params[2], params[3], list(params[4:])


def _distort_normalized(model, dist, x, y):
    """Apply the model's lens distortion to normalized camera coords (x, y)."""
    if not dist or model in ("SIMPLE_PINHOLE", "PINHOLE"):
        return x, y
    if model == "SIMPLE_RADIAL":
        r2 = x*x + y*y
        f = 1 + dist[0]*r2
        return x*f, y*f
    if model == "RADIAL":
        r2 = x*x + y*y
        f = 1 + r2*(dist[0] + r2*dist[1])
        return x*f, y*f
    if model in ("OPENCV", "FULL_OPENCV"):
        k1, k2, p1, p2 = dist[0:4]
        k3, k4, k5, k6 = (dist + [0.0]*4)[4:8]
        r2 = x*x + y*y
        rad = (1 + r2*(k1 + r2*(k2 + r2*k3))) / (1 + r2*(k4 + r2*(k5 + r2*k6)))
        return (x*rad + 2*p1*x*y + p2*(r2 + 2*x*x),
                y*rad + p1*(r2 + 2*y*y) + 2*p2*x*y)
    if model == "OPENCV_FISHEYE":
        k1, k2, k3, k4 = dist[0:4]
        r = math.sqrt(x*x + y*y)
        if r < 1e-12:
            return x, y
        th = math.atan(r)
        th2 = th*th
        sc = th*(1 + th2*(k1 + th2*(k2 + th2*(k3 + th2*k4)))) / r
        return x*sc, y*sc
    raise RuntimeError(f"unsupported camera model for undistortion: {model}")


def _undistort_pixel(model, params, u, v, iters=100, tol=1e-12):
    """Marked pixel (u, v) in the ORIGINAL (distorted) image -> normalized camera
    coords (x, y) with the lens distortion removed (fixed-point iteration), so the
    viewing ray [x, y, 1] is geometrically exact."""
    fx, fy, cx, cy, dist = _split_intrinsics(model, params)
    x0, y0 = (u - cx) / fx, (v - cy) / fy
    x, y = x0, y0
    for _ in range(iters):
        xd, yd = _distort_normalized(model, dist, x, y)
        dx, dy = x0 - xd, y0 - yd
        x, y = x + dx, y + dy
        if dx*dx + dy*dy < tol*tol:
            break
    return x, y


# Smallest eigenvalue of the summed ray projectors below which the rays are
# treated as parallax-free (~0.6 deg between two rays) and triangulation refused.
_MIN_RAY_EIG = 1e-4


def _triangulate_rays(rays):
    """Least-squares 3D point nearest all rays [(center, unit_dir), ...].
    Returns None when the rays carry no usable parallax or the intersection
    lies behind a camera (a bogus solve)."""
    A = np.zeros((3, 3))
    b = np.zeros(3)
    for C, d in rays:
        P = np.eye(3) - np.outer(d, d)
        A += P
        b += P @ C
    if np.linalg.eigvalsh(A)[0] < _MIN_RAY_EIG:
        return None
    X = np.linalg.solve(A, b)
    for C, d in rays:
        if (X - C) @ d <= 0:
            return None
    return X


def gcp_correspondences(model_dir, gcp_entries, min_points=3):
    """Build (local, world) correspondences from GCPs.

    A GCP's WORLD position comes from gcp_list.txt directly. Its LOCAL position is
    triangulated from the marked pixels: every marking is undistorted (full lens
    model) into a viewing ray from its camera center, and the rays of all images
    the GCP is marked in are intersected in least squares. GCPs marked in a single
    image only (or whose rays carry no parallax) fall back to the previous
    heuristic — the observed sparse 3D point whose pixel is nearest the marking,
    averaged over markings. Returns (local (N,3), world (N,3), info dict with the
    per-method counts).

    ``min_points`` is the minimum number of localizable GCPs required (default 3 —
    a similarity solve needs ≥3 non-collinear correspondences). Held-out
    check-point residual reporting passes ``min_points=1`` to localize whatever it
    can without demanding a solvable set."""
    points = read_colmap_points(model_dir)
    cams = _read_cameras(model_dir)
    images = _read_images_full(model_dir)
    if not images:
        raise RuntimeError("COLMAP images.txt empty/missing; cannot localize GCPs")

    groups = {}
    for e in gcp_entries:
        groups.setdefault(tuple(np.round(e["world"], 4)), []).append(e)

    local_list, world_list = [], []
    n_tri = n_near = 0
    for key, es in groups.items():
        marked = [(e, images.get(e["image"]) or images.get(os.path.basename(e["image"])))
                  for e in es]
        marked = [(e, img) for e, img in marked if img is not None]

        rays = []
        for e, img in marked:
            model, _, _, params = cams[img["cam_id"]]
            x, y = _undistort_pixel(model, params, e["px"][0], e["px"][1])
            d = img["R"].T @ np.array([x, y, 1.0])
            rays.append((-img["R"].T @ img["t"], d / np.linalg.norm(d)))
        X = _triangulate_rays(rays) if len(rays) >= 2 else None
        if X is not None:
            local_list.append(X)
            world_list.append(np.array(key))
            n_tri += 1
            continue

        # Fallback heuristic: nearest observed sparse point to the marked pixel
        # (both in distorted image coords, so directly comparable).
        locs = []
        for e, img in marked:
            best, best_d = None, float("inf")
            for (u, v, pid) in img["obs"]:
                if pid not in points:
                    continue
                d2 = (u - e["px"][0])**2 + (v - e["px"][1])**2
                if d2 < best_d:
                    best_d, best = d2, points[pid]
            if best is not None:
                locs.append(best)
        if locs:
            local_list.append(np.mean(locs, axis=0))
            world_list.append(np.array(key))
            n_near += 1

    if len(local_list) < min_points:
        raise RuntimeError(
            f"only {len(local_list)} GCPs could be localized in COLMAP "
            f"(need >={min_points} with matching observations)")
    print(f"[georef] GCP localization: {n_tri} triangulated, "
          f"{n_near} nearest-point fallback")
    return (np.array(local_list), np.array(world_list),
            {"triangulated": n_tri, "nearest_point": n_near})


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
        try:                       # one malformed image must not sink the whole solve
            g = _gps(ip)
        except Exception:
            g = None
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


def solve_residuals(s, R, t, local, world):
    """Residuals of the similarity solve in target-CRS units (metres for any
    projected CRS): how far each correspondence lands from its surveyed/GPS
    position after the transform. GCP residuals reflect marking + reconstruction
    quality; EXIF residuals are dominated by consumer-GPS noise — both belong in
    georef_transform.json so the solve quality is visible, not guessed."""
    pred = (s * (np.asarray(R) @ np.asarray(local, float).T).T) + np.asarray(t, float)
    res = pred - np.asarray(world, float)
    d3 = np.linalg.norm(res, axis=1)
    out = {
        "count": int(d3.size),
        "rms_3d": float(np.sqrt(np.mean(d3 ** 2))),
        "rms_horizontal": float(np.sqrt(np.mean(np.sum(res[:, :2] ** 2, axis=1)))),
        "rms_vertical": float(np.sqrt(np.mean(res[:, 2] ** 2))),
        "max_3d": float(d3.max()),
    }
    print(f"[georef] solve residuals: RMS 3D {out['rms_3d']:.3f} "
          f"(horiz {out['rms_horizontal']:.3f}, vert {out['rms_vertical']:.3f}), "
          f"max {out['max_3d']:.3f} over {out['count']} correspondences")
    return out


def _xy_offset(world):
    """2D (x, y, 0) float-precision offset — ODM's convention. Only easting and
    northing are large enough to break float32 vertex precision; Z stays ABSOLUTE
    in the OBJ/glTF so the model aligns vertically with the (full-coordinate)
    point cloud in viewers that translate by x/y only (WebODM's ModelView)."""
    off = world.mean(0).astype(float)
    off[2] = 0.0
    return off


def write_coords_txt(work, offset, crs):
    """ODM-compatible odm_georeferencing/coords.txt: line 1 a CRS description,
    line 2 'easting northing'. WebODM's 3D viewer reads line 2 to place the
    (offset-subtracted) textured model next to the full-coordinate point cloud."""
    desc = str(crs)
    m = str(crs).upper().replace("EPSG:", "")
    if m.isdigit() and (m.startswith("326") or m.startswith("327")):
        desc = f"WGS84 UTM {int(m[3:])}{'N' if m.startswith('326') else 'S'}"
    p = os.path.join(work, "coords.txt")
    with open(p, "w") as f:
        f.write(f"{desc}\n{offset[0]:.6f} {offset[1]:.6f}\n")
    print(f"[georef] wrote coords.txt ({desc}, offset {offset[0]:.1f} {offset[1]:.1f})")


# ---------------------------------------------------------------------------
# Apply transform to the textured OBJ (offset-subtracted to keep float precision)
# ---------------------------------------------------------------------------
def apply_to_obj(work, s, R, t, offset):
    name = find_mesh_obj(work)
    if not name:
        print("[georef] no OBJ to transform (mesh disabled?)", file=sys.stderr)
        return
    obj = os.path.join(work, name)
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

    # ---- honor an upstream GCP-constrained bundle adjustment --------------
    # If sparse_colmap.sh already ran gcp_bundle_adjust.py, the sparse model was
    # rewritten into the offset-world frame and georef_transform.json is the
    # identity-with-offset transform (s=1, R=I, t=offset). Apply that to the OBJ
    # (-> identity, the mesh stays offset-world, matching the cloud) and write
    # coords.txt — do NOT re-solve a post-hoc Umeyama, which would fight the
    # already-corrected reconstruction. Keep the colmap-gcp-ba transform in place
    # so pointcloud_to_laz/orthophoto consume its offset.
    if mode in ("gcp", "auto") and os.path.exists(transform_path):
        try:
            existing = json.load(open(transform_path))
        except (ValueError, OSError):
            existing = None
        if existing and existing.get("source") == "colmap-gcp-ba":
            print("[georef] honoring upstream GCP-constrained BA "
                  "(source=colmap-gcp-ba); applying identity-with-offset to OBJ")
            s = float(existing.get("s", 1.0))
            R = np.asarray(existing.get("R", np.eye(3).tolist()))
            t = np.asarray(existing.get("t", [0.0, 0.0, 0.0]))
            offset = np.asarray(existing.get("offset", [0.0, 0.0, 0.0]))
            apply_to_obj(args.work, s, R, t, offset)
            write_coords_txt(args.work, offset, existing.get("crs", args.crs))
            return

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
                local, world, gcp_info = gcp_correspondences(model_dir, entries)
                s, R, t = umeyama_similarity(local, world)
                residuals = solve_residuals(s, R, t, local, world)
                residuals["gcp_localization"] = gcp_info
                offset = _xy_offset(world)
                crs = args.crs if args.crs not in ("auto", "") else crs_header
                apply_to_obj(args.work, s, R, t, offset)
                write({"source": "colmap-gcp", "s": s, "R": R.tolist(),
                       "t": t.tolist(), "offset": offset.tolist(), "crs": crs,
                       "residuals": residuals})
                write_coords_txt(args.work, offset, crs)
                return
            else:  # exif
                local, world, epsg = exif_correspondences(model_dir, args.images, args.crs)
                s, R, t = umeyama_similarity(local, world)
                residuals = solve_residuals(s, R, t, local, world)
                offset = _xy_offset(world)
                apply_to_obj(args.work, s, R, t, offset)
                write({"source": "colmap-exif", "s": s, "R": R.tolist(),
                       "t": t.tolist(), "offset": offset.tolist(), "crs": epsg,
                       "residuals": residuals})
                write_coords_txt(args.work, offset, epsg)
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
