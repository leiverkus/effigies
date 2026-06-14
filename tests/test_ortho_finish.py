#!/usr/bin/env python3
"""Unit tests for helpers/ortho_finish.py — orthophoto radiometric finishing.

All tests build synthetic RGB+alpha rasters and check that each finishing step
does what it claims while leaving nodata (alpha==0) pixels untouched. The key
archaeology-safety test (`local_flatten`) verifies that a broad low-frequency
brightness gradient is removed while a high-frequency albedo pattern survives —
the ortho-space analogue of test_harmonize's vignetting test.

Run:  python3 tests/test_ortho_finish.py
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "helpers"))
import ortho_finish as of  # noqa: E402


def _full_alpha(h, w):
    return np.full((h, w), 255, np.uint8)


def test_white_balance_removes_cast():
    """A uniform grey surface tinted by a known per-channel cast must come back
    to near-neutral (channel means equal)."""
    h, w = 40, 40
    base = np.full((h, w, 3), 120.0)
    cast = base * np.array([1.3, 1.0, 0.7])               # warm cast
    rgb = np.clip(cast, 0, 255).astype(np.uint8)
    out, gains = of.white_balance(rgb, _full_alpha(h, w))
    means = out.reshape(-1, 3).mean(0)
    assert means.max() - means.min() < 2.0, means
    assert gains[0] < 1.0 < gains[2], gains                # undoes the cast
    print(f"ok  white-balance removes cast (channel spread {means.max()-means.min():.2f})")


def test_white_balance_neutral_is_noop():
    """An already-neutral surface must keep gains ~1 and stay put."""
    rgb = np.full((20, 20, 3), 100, np.uint8)
    out, gains = of.white_balance(rgb, _full_alpha(20, 20))
    assert np.allclose(gains, 1.0, atol=1e-3), gains
    assert np.array_equal(out, rgb)
    print("ok  white-balance is a no-op on neutral input")


def test_auto_contrast_stretches_and_keeps_nodata():
    """A low-contrast raster expands toward full range; nodata stays nodata."""
    h, w = 30, 30
    lum = np.linspace(100, 150, w)[None, :].repeat(h, 0)   # narrow 100..150 band
    rgb = np.repeat(lum[:, :, None], 3, axis=2).astype(np.uint8)
    alpha = _full_alpha(h, w)
    alpha[:, :5] = 0                                       # a nodata strip
    rgb[:, :5] = 0
    out = of.auto_contrast(rgb, alpha)
    valid = alpha > 0
    vlum = out[..., 0][valid].astype(float)
    assert vlum.min() < 20 and vlum.max() > 235, (vlum.min(), vlum.max())
    assert np.array_equal(out[~valid], rgb[~valid])        # nodata untouched
    print(f"ok  auto-contrast stretches {vlum.min():.0f}..{vlum.max():.0f}, nodata kept")


def test_local_flatten_keeps_albedo_drops_gradient():
    """Synthetic ortho = high-frequency checkerboard albedo (the DATA) under a
    broad left-to-right brightness ramp (the residual exposure gradient). After
    flatten the ramp must collapse while the checkerboard contrast survives."""
    h, w = 80, 80
    yy, xx = np.mgrid[0:h, 0:w]
    checker = np.where(((xx // 4) + (yy // 4)) % 2 == 0, 90.0, 150.0)  # albedo
    ramp = np.linspace(0.6, 1.4, w)[None, :]                            # gradient
    lum = checker * ramp
    rgb = np.clip(np.repeat(lum[:, :, None], 3, axis=2), 0, 255).astype(np.uint8)
    alpha = _full_alpha(h, w)
    gsd = 0.02                                             # 2 cm/px

    before = of.tonal_variation(rgb, alpha, gsd, scale_m=0.4)["lowfreq_std"]
    out = of.local_flatten(rgb, alpha, gsd, strength=1.0, scale_m=0.4)
    after = of.tonal_variation(out, alpha, gsd, scale_m=0.4)["lowfreq_std"]

    # broad gradient strongly reduced
    assert after < 0.5 * before, (before, after)
    # checkerboard contrast preserved: per-4px-block std should stay high
    block_std_in = checker.std()
    block_std_out = out[..., 0].astype(float).std()
    assert block_std_out > 0.6 * block_std_in, (block_std_in, block_std_out)
    print(f"ok  local-flatten drops gradient ({before:.1f}->{after:.1f}) "
          f"and keeps albedo (std {block_std_out:.0f})")


def test_tonal_variation_high_on_gradient_low_on_flat():
    h, w = 60, 60
    flat = np.full((h, w, 3), 120, np.uint8)
    ramp_l = np.linspace(60, 200, w)[None, :].repeat(h, 0)
    ramp = np.repeat(ramp_l[:, :, None], 3, axis=2).astype(np.uint8)
    alpha = _full_alpha(h, w)
    v_flat = of.tonal_variation(flat, alpha, 0.05)["lowfreq_std"]
    v_ramp = of.tonal_variation(ramp, alpha, 0.05)["lowfreq_std"]
    assert v_flat < 1.0, v_flat
    assert v_ramp > 20.0, v_ramp
    print(f"ok  tonal-variation flat {v_flat:.2f} << ramp {v_ramp:.1f}")


def test_finish_default_is_identity():
    """color_balance=none + no knobs => raster returned unchanged (opt-in guard)."""
    rng = np.random.default_rng(0)
    rgb = rng.integers(0, 256, (40, 40, 3), dtype=np.uint8)
    alpha = _full_alpha(40, 40)
    alpha[:10, :10] = 0
    out, info = of.finish(rgb, alpha, 0.05)
    assert np.array_equal(out, rgb), "default finish must not alter the raster"
    assert info["steps"] == [], info["steps"]
    assert info["after"] == info["before"]
    print("ok  finish() with no options is bit-for-bit identity")


def test_finish_records_steps_and_metric():
    rng = np.random.default_rng(2)
    rgb = (rng.integers(0, 256, (40, 40, 3)) * np.array([1.2, 1.0, 0.8])).clip(0, 255).astype(np.uint8)
    alpha = _full_alpha(40, 40)
    out, info = of.finish(rgb, alpha, 0.05, color_balance="auto", gamma=1.1)
    assert "white-balance" in info["steps"] and "auto-contrast" in info["steps"]
    assert info["gains"] is not None
    assert "before" in info and "after" in info
    assert not np.array_equal(out, rgb)
    print(f"ok  finish() records steps {info['steps']}")


if __name__ == "__main__":
    test_white_balance_removes_cast()
    test_white_balance_neutral_is_noop()
    test_auto_contrast_stretches_and_keeps_nodata()
    test_local_flatten_keeps_albedo_drops_gradient()
    test_tonal_variation_high_on_gradient_low_on_flat()
    test_finish_default_is_identity()
    test_finish_records_steps_and_metric()
    print("\nall ortho_finish tests passed")
