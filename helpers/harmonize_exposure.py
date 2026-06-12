#!/usr/bin/env python3
"""
Per-image photometric harmonisation BEFORE texturing.

OpenMVS's seam leveling (its own colour harmonisation) corrupts texture patches
on this build (verified in v2.4.0 AND master), so Effigies textures with leveling
OFF — which would leave the raw photometric differences between photos visible as
patchwork ("fleckig"). This step fixes the cause: the photos are equalised before
the atlas is assembled.

Two-part model, estimated from the sparse-point observations (every 3D point is
seen in several images; images.txt gives the pixel position of each observation):

  log c_op = log a_p + log g_i[channel] + f_i(x_o, y_o)

  * g_i  — one RGB gain per image (exposure / white balance differences)
  * f_i  — one smooth SPATIAL field per image (quadratic in normalised image
           coords, zero-mean over the image's observations): vignetting and
           sky-gradient effects. A purely global gain cannot fix these — a patch
           textured from an image corner stays darker than its neighbour from
           another image's centre, which is exactly the residual patchiness.

Solved by alternating least squares in log space (albedos <-> per-image
parameters, ridge-regularised spatial term). Estimation samples the ORIGINAL
images (observation coords refer to them); the correction is applied to the
UNDISTORTED images in dense/images (what TextureMesh reads) — the field is smooth,
so the original/undistorted coordinate difference is negligible for this purpose.

Pure numpy + Pillow; reuses georef_bridge's COLMAP TXT readers.
"""
import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import georef_bridge as gb  # noqa: E402

MAX_OBS_PER_IMAGE = 1500     # sampling cap per image (deterministic)
MIN_OBS_PER_IMAGE = 20       # below this an image keeps gain 1 / no field
MIN_OBS_SPATIAL = 120        # below this only the global gain is fitted
CLIP_LO, CLIP_HI = 8, 247    # ignore (near-)clipped pixels in the estimation
GAIN_MIN, GAIN_MAX = 0.5, 2.0
FIELD_MAX = float(np.log(1.6))   # |spatial field| cap (log domain)
RIDGE = 1e-3                 # regularisation of the spatial coefficients


def _basis(x, y):
    """Quadratic 2D basis (no constant — that lives in the global gain)."""
    return np.stack([x, y, x * x, x * y, y * y], axis=-1)


def sample_observations(model_dir, images_dir):
    """-> (oi, op, oc[N,3], oxy[N,2 normalised coords], names)
    One row per sampled (image, point) observation; colours are 3x3 means.
    Coordinates are normalised to [-1, 1] of each image's own frame."""
    from PIL import Image
    images = gb._read_images_full(model_dir)
    names = sorted(images.keys())
    name_to_idx = {n: i for i, n in enumerate(names)}
    pt_ids = {}
    oi, op, oc, oxy = [], [], [], []
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
            oxy.append((2.0 * u / W - 1.0, 2.0 * v / H - 1.0))
    return (np.asarray(oi), np.asarray(op),
            np.asarray(oc, dtype=np.float64),
            np.asarray(oxy, dtype=np.float64), names)


def solve_gains(oi, op, oc, n_img, iters=25):
    """Global-only solver (kept for API/tests): per-image RGB gains [n_img,3]."""
    n_pt = int(op.max()) + 1 if len(op) else 0
    logc = np.log(np.maximum(oc, 1.0))
    logg = np.zeros((n_img, 3))
    loga = np.zeros((n_pt, 3))
    img_cnt = np.maximum(np.bincount(oi, minlength=n_img), 1)[:, None]
    pt_cnt = np.maximum(np.bincount(op, minlength=n_pt), 1)[:, None]
    for _ in range(iters):
        loga = np.zeros((n_pt, 3))
        np.add.at(loga, op, logc - logg[oi])
        loga /= pt_cnt
        logg = np.zeros((n_img, 3))
        np.add.at(logg, oi, logc - loga[op])
        logg /= img_cnt
    weak = (np.bincount(oi, minlength=n_img) < MIN_OBS_PER_IMAGE)
    logg -= logg[~weak].mean(0) if (~weak).any() else 0.0
    logg[weak] = 0.0
    return np.clip(np.exp(logg), GAIN_MIN, GAIN_MAX)


def solve_photometric(oi, op, oc, oxy, n_img, iters=25):
    """Full solver: per-image RGB log-gains [n,3], spatial coefficients [n,5]
    and per-image basis means [n,5] (the field is zero-mean over each image's
    observations, so the constant part stays in the gain)."""
    n_pt = int(op.max()) + 1 if len(op) else 0
    logc = np.log(np.maximum(oc, 1.0))
    B = _basis(oxy[:, 0], oxy[:, 1])                       # [N,5]
    logg = np.zeros((n_img, 3))
    coef = np.zeros((n_img, 5))
    bmean = np.zeros((n_img, 5))
    loga = np.zeros((n_pt, 3))
    pt_cnt = np.maximum(np.bincount(op, minlength=n_pt), 1)[:, None]
    img_obs = [np.nonzero(oi == i)[0] for i in range(n_img)]
    n_obs = np.array([len(s) for s in img_obs])
    for i in range(n_img):
        if n_obs[i]:
            bmean[i] = B[img_obs[i]].mean(0)

    eye = np.eye(5) * RIDGE
    for _ in range(iters):
        # field value per observation (zero-mean per image)
        f = ((B - bmean[oi]) * coef[oi]).sum(1)
        # albedos given image parameters
        loga = np.zeros((n_pt, 3))
        np.add.at(loga, op, logc - logg[oi] - f[:, None])
        loga /= pt_cnt
        # image parameters given albedos
        for i in range(n_img):
            sel = img_obs[i]
            if len(sel) < MIN_OBS_PER_IMAGE:
                continue
            E = logc[sel] - loga[op[sel]]                  # [m,3]
            if len(sel) >= MIN_OBS_SPATIAL:
                Bc = B[sel] - bmean[i]
                y = E.mean(1) - E.mean()                   # luminance, centred
                coef[i] = np.linalg.solve(Bc.T @ Bc + eye * len(sel), Bc.T @ y)
                fi = Bc @ coef[i]
            else:
                coef[i] = 0.0
                fi = np.zeros(len(sel))
            logg[i] = (E - fi[:, None]).mean(0)
    weak = n_obs < MIN_OBS_PER_IMAGE
    logg -= logg[~weak].mean(0) if (~weak).any() else 0.0
    logg[weak] = 0.0
    coef[weak] = 0.0
    logg = np.clip(logg, np.log(GAIN_MIN), np.log(GAIN_MAX))
    return logg, coef, bmean


def apply_correction(undist_dir, names, logg, coef, bmean):
    """Divide each undistorted image by its full correction field
    exp(logg_c + f(x, y)); the field is evaluated on a coarse grid and
    upsampled (it is quadratic — exact up to interpolation)."""
    from PIL import Image
    n = 0
    for i, name in enumerate(names):
        if np.allclose(logg[i], 0, atol=1e-3) and np.allclose(coef[i], 0, atol=1e-4):
            continue
        cands = [os.path.join(undist_dir, name),
                 os.path.join(undist_dir, os.path.basename(name))]
        path = next((c for c in cands if os.path.exists(c)), None)
        if not path:
            continue
        im = Image.open(path).convert("RGB")
        W, H = im.size
        # coarse field grid (quadratic surface -> bilinear upsample is plenty)
        gw, gh = max(2, W // 64), max(2, H // 64)
        gx = np.linspace(-1, 1, gw)
        gy = np.linspace(-1, 1, gh)
        GX, GY = np.meshgrid(gx, gy)
        f = (_basis(GX.ravel(), GY.ravel()) - bmean[i]) @ coef[i]
        f = np.clip(f, -FIELD_MAX, FIELD_MAX).reshape(gh, gw)
        field = np.asarray(Image.fromarray(f.astype(np.float32))
                           .resize((W, H), Image.BILINEAR))
        corr = np.exp(logg[i][None, None, :] + field[:, :, None])
        corr = np.clip(corr, 0.4, 2.5)
        arr = np.asarray(im, dtype=np.float32) / corr
        Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).save(path, quality=95)
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

    oi, op, oc, oxy, names = sample_observations(model_dir, images_dir)
    if len(oi) < 100:
        print(f"[harmonize] too few usable observations ({len(oi)}); skipping",
              file=sys.stderr)
        return
    logg, coef, bmean = solve_photometric(oi, op, oc, oxy, len(names))
    n = apply_correction(undist_dir, names, logg, coef, bmean)
    lum = np.exp(logg).mean(1)
    # field strength = max |field| over the frame corners (worst case)
    corners = _basis(np.array([-1, -1, 1, 1, 0.0]), np.array([-1, 1, -1, 1, 0.0]))
    fmax = np.abs((corners[None, :, :] - bmean[:, None, :]) @ coef[..., None]).max()
    print(f"[harmonize] equalised exposure across {len(names)} images "
          f"({len(oi)} samples): gain range {lum.min():.3f}..{lum.max():.3f}, "
          f"spatial field up to ±{(np.exp(min(fmax, FIELD_MAX)) - 1) * 100:.0f}%, "
          f"{n} images adjusted")


if __name__ == "__main__":
    main()
