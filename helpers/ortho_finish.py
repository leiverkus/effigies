#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Patrick Leiverkus
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Radiometric finishing for the orthophoto raster.

Effigies' ortho is a single nadir rasterisation of the textured mesh
(``orthophoto.py``), so it has no stitch seams — the "seamline editing" half of
the Metashape/ODM finishing feature is structurally unnecessary, and colour
consistency between views is already handled upstream at the texture atlas
(``harmonize_exposure.py``, ``texture_blend.py``, ``seam_level.py``). What is
left is *finishing control on the final raster*: white-balance, contrast, a
manual tone curve, and — for an ortho with a genuine residual exposure gradient
— a large-scale luminance flatten.

Design notes:
  * Every operation works in float, masks to valid pixels (``alpha > 0``) and
    leaves nodata untouched, then clips back to uint8. With no options enabled
    the orchestrator is a no-op, so the default ortho is bit-for-bit unchanged.
  * ``local_flatten`` is the only step that can erase *real* albedo variation
    (on an excavation ortho, soil-colour / feature contrast is data, not noise),
    so it is off by default and gated behind an explicit strength.
  * The low-frequency luminance estimate is the ortho-space analogue of the
    per-image spatial field solved in ``harmonize_exposure.solve_photometric``;
    here we estimate it directly from the raster instead of from observations.

Dependencies: numpy; ``scipy.ndimage`` imported lazily (already used by
``orthophoto.fill_ortho_holes``).
"""
import numpy as np

# Rec. 601 luma weights — matches how most ortho/imaging tools weight RGB.
_LUMA = np.array([0.299, 0.587, 0.114], dtype=np.float64)


def _luma(rgbf):
    """Luminance [H,W] from a float RGB array [H,W,3]."""
    return rgbf @ _LUMA


def _masked_lowpass(values, mask, gsd, scale_m):
    """Low-frequency component of ``values`` over the valid ``mask`` at a kernel
    size of ~``scale_m`` metres. Uses a normalised (NaN-aware) Gaussian blur:
    blur(values*mask) / blur(mask), so nodata pixels neither leak in zeros nor
    pull the estimate down. ``sigma`` is derived from the GSD so the smoothing
    footprint is a fixed ground distance regardless of resolution. Returns a
    float [H,W] field defined (interpolated) everywhere; callers read it only
    where ``mask`` is True."""
    from scipy import ndimage
    sigma = max(1.0, (scale_m / max(gsd, 1e-9)) / 2.0)   # ~scale_m px footprint
    m = mask.astype(np.float64)
    num = ndimage.gaussian_filter(values * m, sigma, mode="nearest")
    den = ndimage.gaussian_filter(m, sigma, mode="nearest")
    return num / np.maximum(den, 1e-6)


def tonal_variation(rgb, alpha, gsd, scale_m=2.0):
    """Diagnostic: how much broad, low-frequency tonal variation does the ortho
    carry? Returns a dict with the std of the low-frequency luminance over valid
    pixels (in 8-bit units), the total luminance std, and their ratio. A high
    ``lowfreq_std`` (and ratio) means a real exposure/brightness gradient across
    the scene — the case where ``local_flatten`` / balancing actually helps; a
    low value means the ortho is already flat and finishing is cosmetic only."""
    valid = alpha > 0
    n = int(valid.sum())
    if n == 0:
        return {"valid_px": 0, "lowfreq_std": 0.0, "total_std": 0.0, "ratio": 0.0}
    rgbf = rgb.astype(np.float64)
    lum = _luma(rgbf)
    low = _masked_lowpass(lum, valid, gsd, scale_m)
    lv = lum[valid]
    low_v = low[valid]
    total_std = float(lv.std())
    low_std = float(low_v.std())
    ratio = low_std / total_std if total_std > 1e-9 else 0.0
    return {"valid_px": n, "lowfreq_std": low_std,
            "total_std": total_std, "ratio": ratio}


def white_balance(rgb, alpha, mode="gray-world"):
    """Gray-world white balance: scale each channel by a per-channel gain so the
    three channel means over valid pixels match the overall luminance mean. This
    removes a global colour cast (e.g. a warm/cool tint) without touching spatial
    structure. Returns (rgb_uint8, gains[3]). A flat already-neutral surface
    yields gains ~1 (no-op)."""
    valid = alpha > 0
    if not valid.any():
        return rgb, [1.0, 1.0, 1.0]
    rgbf = rgb.astype(np.float64)
    px = rgbf[valid]
    chan_mean = px.mean(0)                       # [3] per-channel mean
    target = float(chan_mean @ _LUMA)            # luminance-weighted grey target
    gains = target / np.maximum(chan_mean, 1e-6)
    out = rgbf.copy()
    out[valid] = np.clip(px * gains, 0, 255)
    return out.astype(np.uint8), gains.tolist()


def auto_contrast(rgb, alpha, lo_pct=1.0, hi_pct=99.0):
    """Percentile contrast stretch on luminance. Clip luminance to its
    [lo_pct, hi_pct] percentiles over valid pixels and linearly remap that range
    to 0..255, scaling each pixel's RGB by the same factor so hue is preserved
    (a luminance stretch, not three independent channel stretches). Expands a
    flat / low-contrast ortho to the full range; nodata untouched."""
    valid = alpha > 0
    if not valid.any():
        return rgb
    rgbf = rgb.astype(np.float64)
    lum = _luma(rgbf)
    lo, hi = np.percentile(lum[valid], [lo_pct, hi_pct])
    if hi - lo < 1e-6:
        return rgb
    scale = 255.0 / (hi - lo)
    lum_v = lum[valid]
    new_lum = np.clip((lum_v - lo) * scale, 0, 255)
    factor = new_lum / np.maximum(lum_v, 1e-6)   # per-pixel luminance ratio
    out = rgbf.copy()
    out[valid] = np.clip(rgbf[valid] * factor[:, None], 0, 255)
    return out.astype(np.uint8)


def apply_tone(rgb, alpha, brightness=0.0, gamma=1.0):
    """Manual tone fine-tune over valid pixels: additive ``brightness`` (in
    normalised −1..1, i.e. ±255) followed by a ``gamma`` curve (>1 darkens
    mid-tones, <1 lifts them). Identity at brightness=0, gamma=1."""
    valid = alpha > 0
    if not valid.any() or (brightness == 0.0 and gamma == 1.0):
        return rgb
    rgbf = rgb.astype(np.float64)
    px = rgbf[valid] + brightness * 255.0
    if gamma != 1.0:
        px = 255.0 * np.clip(px / 255.0, 0, 1) ** float(gamma)
    out = rgbf.copy()
    out[valid] = np.clip(px, 0, 255)
    return out.astype(np.uint8)


def local_flatten(rgb, alpha, gsd, strength=0.0, scale_m=4.0):
    """Remove large-scale (low-frequency) luminance variation — the Metashape-
    style "radiometric balancing". Estimate the smooth luminance field over valid
    pixels, then multiply each pixel by ``(global_mean / field) ** strength`` so
    the broad gradient flattens while the global brightness is preserved.

    WARNING: on an excavation ortho a real soil-colour / feature gradient *is*
    data; flattening it destroys signal. Off by default (``strength == 0``);
    ``scale_m`` controls how broad a feature counts as "low frequency"
    (larger = only the very broadest gradients are touched)."""
    valid = alpha > 0
    if strength <= 0.0 or not valid.any():
        return rgb
    rgbf = rgb.astype(np.float64)
    lum = _luma(rgbf)
    field = _masked_lowpass(lum, valid, gsd, scale_m)
    gmean = float(lum[valid].mean())
    corr = (gmean / np.maximum(field, 1e-6)) ** float(strength)
    out = rgbf.copy()
    out[valid] = np.clip(rgbf[valid] * corr[valid][:, None], 0, 255)
    return out.astype(np.uint8)


def finish(rgb, alpha, gsd, *, color_balance="none", brightness=0.0,
           gamma=1.0, flatten=0.0, diag_scale_m=2.0):
    """Apply the enabled finishing steps to the ortho raster and return
    ``(rgb_uint8, info)``. Order: white-balance -> local-flatten -> auto-contrast
    -> manual tone. With ``color_balance == 'none'``, ``flatten == 0`` and no
    manual knobs this is a no-op and ``rgb`` is returned unchanged (the opt-in
    default). ``info`` records the steps, gains and before/after tonal variation
    for the report.

    ``color_balance``: 'none' | 'white-balance' (gray-world) | 'auto'
    (white-balance + percentile auto-contrast)."""
    info = {"steps": [], "gains": None,
            "before": tonal_variation(rgb, alpha, gsd, diag_scale_m)}
    out = rgb

    if color_balance in ("white-balance", "auto"):
        out, gains = white_balance(out, alpha)
        info["steps"].append("white-balance")
        info["gains"] = gains
    if flatten and flatten > 0.0:
        out = local_flatten(out, alpha, gsd, strength=flatten)
        info["steps"].append(f"flatten({flatten:g})")
    if color_balance == "auto":
        out = auto_contrast(out, alpha)
        info["steps"].append("auto-contrast")
    if brightness != 0.0 or gamma != 1.0:
        out = apply_tone(out, alpha, brightness=brightness, gamma=gamma)
        info["steps"].append(f"tone(b={brightness:g},g={gamma:g})")

    info["after"] = (info["before"] if not info["steps"]
                     else tonal_variation(out, alpha, gsd, diag_scale_m))
    return out, info
