#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Patrick Leiverkus
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit test for helpers/harmonize_exposure.py — the exposure solver.

Synthetic ground truth: random point albedos, known per-image gains, observations
c_ip = g_i * a_p (+ noise). The alternating-least-squares solver must recover the
gains (up to the geometric-mean normalisation). Pure numpy.

Run:  python3 tests/test_harmonize.py
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "helpers"))
import harmonize_exposure as hx  # noqa: E402


def test_solver_recovers_known_gains():
    rng = np.random.default_rng(7)
    n_img, n_pt = 8, 400
    true_g = np.exp(rng.normal(0, 0.25, (n_img, 1)))          # ~0.6..1.6 spread
    true_g /= np.exp(np.log(true_g).mean())                    # geo-mean = 1
    albedo = rng.uniform(30, 200, (n_pt, 3))
    oi, op, oc = [], [], []
    for p in range(n_pt):
        for i in rng.choice(n_img, size=4, replace=False):     # each point in 4 images
            c = true_g[i] * albedo[p] * np.exp(rng.normal(0, 0.02, 3))
            if c.min() < hx.CLIP_LO or c.max() > hx.CLIP_HI:
                continue
            oi.append(i); op.append(p); oc.append(c)
    gains = hx.solve_gains(np.asarray(oi), np.asarray(op),
                           np.asarray(oc, dtype=np.float64), n_img)
    err = np.abs(gains.mean(1) - true_g[:, 0])
    assert err.max() < 0.03, (gains.mean(1), true_g[:, 0])
    print(f"ok  exposure solver recovers known per-image gains "
          f"(max err {err.max():.4f}, spread {true_g.min():.2f}..{true_g.max():.2f})")


def test_weak_images_keep_unit_gain():
    """An image with fewer than MIN_OBS samples must keep gain 1 (no wild guess)."""
    rng = np.random.default_rng(1)
    n_img, n_pt = 3, 120
    oi, op, oc = [], [], []
    for p in range(n_pt):                       # images 0 and 1 share all points
        for i in (0, 1):
            oi.append(i); op.append(p)
            oc.append([100.0 * (1.5 if i else 1.0)] * 3)
    oi.append(2); op.append(0); oc.append([80.0] * 3)   # image 2: a single sample
    gains = hx.solve_gains(np.asarray(oi), np.asarray(op),
                           np.asarray(oc, dtype=np.float64), n_img)
    assert np.allclose(gains[2], 1.0), gains[2]
    assert gains[1].mean() > gains[0].mean(), gains
    print("ok  under-sampled images keep gain 1.0")


def test_spatial_solver_recovers_vignetting():
    """Synthetic vignetting: each image darkens quadratically toward the frame
    edge (c = g_i * a_p * exp(v_i * r²), v_i < 0). The photometric solver must
    recover both the global gains and the spatial field so that the corrected
    observations of one point agree across images."""
    rng = np.random.default_rng(3)
    n_img, n_pt = 8, 500
    true_g = np.exp(rng.normal(0, 0.2, n_img))
    true_g /= np.exp(np.log(true_g).mean())
    true_v = rng.uniform(-0.45, -0.15, n_img)             # vignetting strength
    albedo = rng.uniform(40, 180, n_pt)
    oi, op, oc, oxy = [], [], [], []
    for p in range(n_pt):
        for i in rng.choice(n_img, size=4, replace=False):
            x, y = rng.uniform(-1, 1), rng.uniform(-1, 1)
            c = true_g[i] * albedo[p] * np.exp(true_v[i] * (x * x + y * y))
            c *= np.exp(rng.normal(0, 0.01))
            if not (hx.CLIP_LO < c < hx.CLIP_HI):
                continue
            oi.append(i); op.append(p); oc.append([c, c, c]); oxy.append((x, y))
    oi, op = np.asarray(oi), np.asarray(op)
    oc, oxy = np.asarray(oc, float), np.asarray(oxy, float)
    logg, coef, bmean = hx.solve_photometric(oi, op, oc, oxy, n_img)
    # corrected observations: residual spread per point must collapse
    B = hx._basis(oxy[:, 0], oxy[:, 1])
    f = ((B - bmean[oi]) * coef[oi]).sum(1)
    corrected = np.log(oc[:, 0]) - logg[oi, 0] - f
    raw = np.log(oc[:, 0]) - np.log(np.exp(logg[oi, 0]))  # global-only correction
    def spread(vals):
        s = np.zeros(int(op.max()) + 1); c = np.zeros_like(s)
        np.add.at(s, op, vals); np.add.at(c, op, 1)
        mean = s / np.maximum(c, 1)
        return float(np.sqrt(np.mean((vals - mean[op]) ** 2)))
    sp_corr, sp_raw = spread(corrected), spread(raw)
    assert sp_corr < 0.03, sp_corr                         # near-perfect agreement
    assert sp_corr < 0.5 * sp_raw, (sp_corr, sp_raw)       # clearly beats global-only
    print(f"ok  spatial solver removes synthetic vignetting "
          f"(residual {sp_corr:.4f} vs global-only {sp_raw:.4f})")


def test_spatial_field_zero_on_flat_data():
    """Without any spatial effect the field coefficients must stay ~0 (the ridge
    keeps the solver from inventing vignetting)."""
    rng = np.random.default_rng(4)
    n_img, n_pt = 5, 300
    albedo = rng.uniform(40, 180, n_pt)
    oi, op, oc, oxy = [], [], [], []
    for p in range(n_pt):
        for i in rng.choice(n_img, size=3, replace=False):
            c = 1.2 * albedo[p] if i == 0 else albedo[p]
            oi.append(i); op.append(p); oc.append([c] * 3)
            oxy.append((rng.uniform(-1, 1), rng.uniform(-1, 1)))
    logg, coef, bmean = hx.solve_photometric(
        np.asarray(oi), np.asarray(op), np.asarray(oc, float),
        np.asarray(oxy, float), n_img)
    assert np.abs(coef).max() < 0.02, np.abs(coef).max()
    assert np.exp(logg[0, 0]) > 1.1, np.exp(logg[0, 0])    # global gain still found
    print("ok  spatial field stays ~0 on flat data (no invented vignetting)")


if __name__ == "__main__":
    test_solver_recovers_known_gains()
    test_weak_images_keep_unit_gain()
    test_spatial_solver_recovers_vignetting()
    test_spatial_field_zero_on_flat_data()
    print("\nall harmonize tests passed")
