#!/usr/bin/env python3
"""
Per-image exposure/colour harmonisation BEFORE texturing.

OpenMVS 2.4.0's seam leveling (its own colour harmonisation) corrupts texture
patches on this build (interiors clamp to black, borders saturate), so Effigies
textures with leveling OFF — which leaves visible exposure differences between
patches ("fleckig"). This step fixes the cause instead: it estimates one RGB gain
per image and applies it to the undistorted images BEFORE TextureMesh, so the
photos already agree photometrically when the atlas is assembled.

Method (classic exposure compensation):
  Every sparse 3D point is observed in several images. Model the observed colour
  of point p in image i as  c_ip ≈ g_i · a_p  (per-image gain × point albedo).
  In log space this is a bilinear system solved by alternating least squares:
      log a_p = mean_i(log c_ip − log g_i),   log g_i = mean_p(log c_ip − log a_p)
  per RGB channel, sampling each observation from the ORIGINAL images (the
  observation pixel coords in images.txt refer to the distorted originals).
  Gains are normalised (geometric mean = 1), clamped to [0.5, 2.0], and applied
  as 1/g_i to the UNDISTORTED images in dense/images (what TextureMesh reads).

Pure numpy + Pillow; reuses georef_bridge's COLMAP TXT readers.
"""
import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import georef_bridge as gb  # noqa: E402

MAX_OBS_PER_IMAGE = 1500     # sampling cap per image (deterministic)
MIN_OBS_PER_IMAGE = 20       # below this an image keeps gain 1.0
CLIP_LO, CLIP_HI = 8, 247    # ignore (near-)clipped pixels in the estimation
GAIN_MIN, GAIN_MAX = 0.5, 2.0


def sample_observations(model_dir, images_dir):
    """-> (obs_img_idx[], obs_pt_idx[], obs_rgb[N,3] float, image_names[])
    One row per sampled (image, point) observation; colours are 3x3 means."""
    from PIL import Image
    images = gb._read_images_full(model_dir)
    names = sorted(images.keys())
    name_to_idx = {n: i for i, n in enumerate(names)}
    pt_ids = {}
    oi, op, oc = [], [], []
    rng = np.random.default_rng(0)
    for name in names:
        cands = [os.path.join(images_dir, name),
                 os.path.join(images_dir, os.path.basename(name))]
        path = next((c for c in cands if os.path.exists(c)), None)
        obs = images[name]["obs"]
        if not path or not obs:
            continue
        try:
            arr = np.asarray(Image.open(path).convert("RGB"), dtype=np.float32)
        except Exception:
            continue
        H, W = arr.shape[:2]
        obs = [obs[k] for k in rng.permutation(len(obs))[:MAX_OBS_PER_IMAGE]]
        for u, v, pid in obs:
            x, y = int(round(u)), int(round(v))
            if not (1 <= x < W - 1 and 1 <= y < H - 1):
                continue
            patch = arr[y - 1:y + 2, x - 1:x + 2].reshape(-1, 3)
            c = patch.mean(0)
            if c.min() < CLIP_LO or c.max() > CLIP_HI:
                continue
            if pid not in pt_ids:
                pt_ids[pid] = len(pt_ids)
            oi.append(name_to_idx[name]); op.append(pt_ids[pid]); oc.append(c)
    return (np.asarray(oi), np.asarray(op),
            np.asarray(oc, dtype=np.float64), names)


def solve_gains(oi, op, oc, n_img, iters=25):
    """Alternating least squares in log space -> per-image RGB gains [n_img,3]."""
    n_pt = int(op.max()) + 1 if len(op) else 0
    logc = np.log(np.maximum(oc, 1.0))
    logg = np.zeros((n_img, 3))
    loga = np.zeros((n_pt, 3))
    img_cnt = np.maximum(np.bincount(oi, minlength=n_img), 1)[:, None]
    pt_cnt = np.maximum(np.bincount(op, minlength=n_pt), 1)[:, None]
    for _ in range(iters):
        # albedo given gains
        loga = np.zeros((n_pt, 3))
        np.add.at(loga, op, logc - logg[oi])
        loga /= pt_cnt
        # gains given albedo
        logg = np.zeros((n_img, 3))
        np.add.at(logg, oi, logc - loga[op])
        logg /= img_cnt
    # images with too few samples keep gain 1; normalise geometric mean to 1
    weak = (np.bincount(oi, minlength=n_img) < MIN_OBS_PER_IMAGE)
    logg[weak] = 0.0
    logg -= logg[~weak].mean(0) if (~weak).any() else 0.0
    return np.clip(np.exp(logg), GAIN_MIN, GAIN_MAX)


def apply_gains(undist_dir, names, gains):
    from PIL import Image
    n = 0
    for i, name in enumerate(names):
        g = gains[i]
        if np.allclose(g, 1.0, atol=1e-3):
            continue
        cands = [os.path.join(undist_dir, name),
                 os.path.join(undist_dir, os.path.basename(name))]
        path = next((c for c in cands if os.path.exists(c)), None)
        if not path:
            continue
        im = Image.open(path).convert("RGB")
        arr = np.asarray(im, dtype=np.float32) / g  # corrected = observed / gain
        out = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
        out.save(path, quality=95)
        n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True, help="OpenMVS workdir")
    ap.add_argument("--images", default=None,
                    help="original images dir (default: <work>/../images)")
    args = ap.parse_args()
    images_dir = args.images or os.path.join(os.path.dirname(
        os.path.abspath(args.work.rstrip("/"))), "images")
    undist_dir = os.path.join(args.work, "dense", "images")
    model_dir = gb._find_colmap_model(args.work)
    if model_dir is None or not os.path.isdir(undist_dir):
        print("[harmonize] no COLMAP model or undistorted images; skipping",
              file=sys.stderr)
        return

    oi, op, oc, names = sample_observations(model_dir, images_dir)
    if len(oi) < 100:
        print(f"[harmonize] too few usable observations ({len(oi)}); skipping",
              file=sys.stderr)
        return
    gains = solve_gains(oi, op, oc, len(names))
    n = apply_gains(undist_dir, names, gains)
    lum = gains.mean(1)
    print(f"[harmonize] equalised exposure across {len(names)} images "
          f"({len(oi)} samples): gain range {lum.min():.3f}..{lum.max():.3f}, "
          f"{n} images adjusted")


if __name__ == "__main__":
    main()
