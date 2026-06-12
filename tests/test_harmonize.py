#!/usr/bin/env python3
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


if __name__ == "__main__":
    test_solver_recovers_known_gains()
    test_weak_images_keep_unit_gain()
    print("\nall harmonize tests passed")
