#!/usr/bin/env python3
"""Unit tests for scripts/benchmark.sh — the comparison metrics.

`cprmse` (check-point RMSE) is pure numpy and is tested against fixtures with a
known offset. `compare` (cloud-to-reference distance) needs the pdal CLI and
scipy; it is exercised on two synthetic clouds when both are available and
skipped otherwise (so host CI without pdal/scipy stays green).

Run:  python3 tests/test_benchmark.py
"""
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH = os.path.join(HERE, "..", "scripts", "benchmark.sh")


def _run(*args):
    out = subprocess.check_output(["bash", BENCH, *args], text=True)
    return json.loads(out)


def test_cprmse_known_offset():
    """model = world - (0.01, 0.02, 0.03) ⇒ per-axis RMSE = the offset."""
    rows = [
        "id,world_x,world_y,world_z,model_x,model_y,model_z",
        "A,10.00,20.00,5.00,9.99,19.98,4.97",
        "B,11.00,21.00,6.00,10.99,20.98,5.97",
        "C,12.50,19.50,5.50,12.49,19.48,5.47",
    ]
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
        f.write("\n".join(rows) + "\n")
        path = f.name
    try:
        r = _run("cprmse", path)
    finally:
        os.remove(path)
    assert r["type"] == "cp-rmse", r
    assert r["n_points"] == 3, r
    rmse = r["rmse"]
    assert math.isclose(rmse["x"], 0.01, abs_tol=1e-9), rmse
    assert math.isclose(rmse["y"], 0.02, abs_tol=1e-9), rmse
    assert math.isclose(rmse["z"], 0.03, abs_tol=1e-9), rmse
    assert math.isclose(rmse["xyz"], math.sqrt(0.01**2 + 0.02**2 + 0.03**2),
                        abs_tol=1e-9), rmse
    print("ok  cprmse recovers a known per-axis offset")


def test_cprmse_ignores_label_columns():
    """A leading id and a trailing label must not break the 6-coord parse."""
    rows = ["P1,0,0,0,0.1,0,0", "P2,1,1,1,1.1,1,1"]  # residual_x = -0.1 → RMSE_x = 0.1
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
        f.write("\n".join(rows) + "\n")
        path = f.name
    try:
        r = _run("cprmse", path)
    finally:
        os.remove(path)
    assert r["n_points"] == 2, r
    assert math.isclose(r["rmse"]["x"], 0.1, abs_tol=1e-9), r
    assert math.isclose(r["rmse"]["y"], 0.0, abs_tol=1e-9), r
    print("ok  cprmse ignores id/label columns")


def _have_compare_deps():
    if shutil.which("pdal") is None:
        return False
    try:
        import scipy.spatial  # noqa: F401
        return True
    except ImportError:
        return False


def _faux_cloud(path, count, shift=0.0):
    """Write a deterministic synthetic LAS via the pdal faux reader."""
    pipe = {"pipeline": [
        {"type": "readers.faux", "mode": "ramp", "count": count,
         "bounds": f"([{shift},{10 + shift}],[0,10],[0,2])"},
        {"type": "writers.las", "filename": path}]}
    pj = path + ".json"
    open(pj, "w").write(json.dumps(pipe))
    subprocess.check_call(["pdal", "pipeline", pj],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.remove(pj)


def test_compare_identical_clouds():
    """Distance of a cloud to itself (after ICP) must be ~0."""
    if not _have_compare_deps():
        print("skip compare (needs pdal + scipy — present in the Effigies image)")
        return
    d = tempfile.mkdtemp()
    ref = os.path.join(d, "ref.las")
    _faux_cloud(ref, 2000)
    try:
        r = _run("compare", ref, ref, "--sample", "2000", "--eps", "0.001")
    finally:
        shutil.rmtree(d, ignore_errors=True)
    assert r["type"] == "cloud-to-reference", r
    assert r["distance"]["rms"] < 1e-6, r
    assert r["completeness"]["fraction"] > 0.99, r
    print("ok  compare gives ~0 distance for identical clouds")


def _have_scipy():
    try:
        import scipy.spatial  # noqa: F401
        return True
    except ImportError:
        return False


def _plane_obj(path, noise=0.0, seed=0, side=60):
    """Write a flat z=0 grid (side×side vertices) as an OBJ, optionally with
    Gaussian noise of std=`noise` injected along z. Deterministic via `seed`.
    Roughness runs on the mesh vertices, so this exercises the metric with only
    scipy (no pdal needed). Faces are irrelevant to the plane-fit residual."""
    import numpy as np
    rng = np.random.default_rng(seed)
    g = np.linspace(0.0, 10.0, side)
    xx, yy = np.meshgrid(g, g)
    x = xx.ravel(); y = yy.ravel()
    z = rng.normal(0.0, noise, size=x.shape) if noise else np.zeros_like(x)
    with open(path, "w") as f:
        for xi, yi, zi in zip(x, y, z):
            f.write(f"v {xi} {yi} {zi}\n")


def test_roughness_flat_plane_is_zero():
    """A perfectly flat plane has ~0 local plane-fit residual (mesh path)."""
    if not _have_scipy():
        print("skip roughness (needs scipy — present in the Effigies image)")
        return
    d = tempfile.mkdtemp()
    p = os.path.join(d, "flat.obj")
    _plane_obj(p, noise=0.0)
    try:
        r = _run("stats", p, "--no-roughness")  # sanity: flag suppresses it
        assert r["roughness"] is None, r
        r = _run("stats", p)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    assert r["type"] == "mesh", r
    rough = r["roughness"]
    assert isinstance(rough, dict) and "rms" in rough, rough
    assert rough["rms"] < 1e-5, rough
    print("ok  roughness ~0 on a flat plane (and --no-roughness suppresses it)")


def test_roughness_scales_with_noise():
    """Roughness must rise with injected noise and scale ~linearly with its std."""
    if not _have_scipy():
        print("skip roughness (needs scipy — present in the Effigies image)")
        return
    d = tempfile.mkdtemp()
    p1 = os.path.join(d, "n1.obj")
    p2 = os.path.join(d, "n2.obj")
    _plane_obj(p1, noise=0.05, seed=1)
    _plane_obj(p2, noise=0.10, seed=1)  # 2× the noise std
    try:
        r1 = _run("stats", p1)["roughness"]["rms"]
        r2 = _run("stats", p2)["roughness"]["rms"]
    finally:
        shutil.rmtree(d, ignore_errors=True)
    assert r1 > 1e-3, r1                       # clearly non-zero
    ratio = r2 / r1
    assert 1.6 < ratio < 2.4, f"expected ~2× roughness for 2× noise, got {ratio:.2f}"
    print(f"ok  roughness scales with noise (2× noise → {ratio:.2f}× roughness)")


if __name__ == "__main__":
    test_cprmse_known_offset()
    test_cprmse_ignores_label_columns()
    test_compare_identical_clouds()
    test_roughness_flat_plane_is_zero()
    test_roughness_scales_with_noise()
    print("\nall benchmark tests passed")
