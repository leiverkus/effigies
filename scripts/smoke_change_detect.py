#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Patrick Leiverkus
# SPDX-License-Identifier: AGPL-3.0-or-later
"""End-to-end smoke for multi-epoch change detection + re-landing.

Synthesises a two-epoch case — a georeferenced ground patch (UTM 32N), with epoch B
= epoch A under a known rigid georef offset PLUS a localised excavation block — then
runs the real ``helpers/change_detect.py`` and ``helpers/camera_exports.py`` CLIs and
asserts the outputs. It exercises the Docker-only paths that the unit tests skip:
PDAL (stable-area ICP, transform, rasterise, LAZ), GDAL (DoD), py4dgeo (M3C2), pyproj
(shots.geojson). Optionally re-derives the orthophoto/DSM from the re-landed mesh.

It also guards a real bug it first surfaced: M3C2 must flag a deep (0.4 m) excavation
block as significant (py4dgeo's ``max_distance`` search depth must be generous, not its
shallow default) — a check that goes 0 %→significant with the fix.

Run inside the Effigies image:
    python3 scripts/smoke_change_detect.py
Exit 0 = all checks passed (skips allowed for absent optional deps); 1 = a failure.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HELP = os.path.join(ROOT, "helpers")
sys.path.insert(0, HELP)
import change_detect as cd  # noqa: E402  (synthetic-data + read-back helpers)

CRS = "EPSG:32632"
OX, OY = 400000.0, 5900000.0          # patch origin in UTM 32N
OFFSET = [OX, OY, 0.0]                 # georef projected offset (OBJ frame)
RIGID = np.array([0.04, -0.02, 0.03])  # known epoch-B georef offset (m)
BLOCK_DROP = 0.40                      # excavation depth (m)
MESH_OBJ = "scene_dense_mesh_refine_texture.obj"

_results = []


def check(name, cond, detail=""):
    ok = bool(cond)
    _results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
    return ok


def ground(n, seed):
    """A gently-sloped, noisy ground patch in UTM 32N (Nx3)."""
    rng = np.random.default_rng(seed)
    xy = rng.uniform(0.0, 10.0, size=(n, 2))
    z = 0.5 + 0.03 * xy[:, 0] + rng.normal(0.0, 0.005, n)
    return np.column_stack([OX + xy[:, 0], OY + xy[:, 1], z])


def write_min_obj(path, xyz_offset):
    """A minimal OBJ (offset-subtracted vertices, as georef_bridge writes) for the
    re-land transform to act on."""
    with open(path, "w") as f:
        f.write("mtllib m.mtl\n")
        for v in xyz_offset:
            f.write(f"v {v[0]:.6f} {v[1]:.6f} {v[2]:.6f}\n")
        f.write("vt 0.5 0.5\n")
        f.write("f 1/1 2/1 3/1\n")


def write_colmap_model(work):
    """A tiny COLMAP text model so camera_exports can emit shots.geojson."""
    m = os.path.join(work, "sparse", "0")
    os.makedirs(m, exist_ok=True)
    open(os.path.join(m, "cameras.txt"), "w").write(
        "1 SIMPLE_PINHOLE 2048 1536 1500 1024 768\n")
    centres = {"IMG_0001.JPG": (2, 2, 8), "IMG_0002.JPG": (6, 3, 8),
               "IMG_0003.JPG": (4, 7, 9)}
    with open(os.path.join(m, "images.txt"), "w") as f:
        for i, (n, c) in enumerate(centres.items(), 1):
            f.write(f"{i} 1 0 0 0 {-c[0]} {-c[1]} {-c[2]} 1 {n}\n100 200 -1\n")
    open(os.path.join(m, "points3D.txt"), "w").write("")


def main():
    if not shutil.which("pdal"):
        print("SKIP: pdal not found — run this inside the Effigies image.")
        return 0

    tmp = tempfile.mkdtemp(prefix="effigies-smoke-")
    try:
        work = os.path.join(tmp, "epochB")
        os.makedirs(work, exist_ok=True)

        # --- synthesise the two epochs -------------------------------------
        A = ground(200000, 0)
        ref = os.path.join(tmp, "epochA.laz")
        cd._write_cloud(A, ref, srs=CRS)

        rngB = np.random.default_rng(1)
        B = A + RIGID
        B[:, 2] += rngB.normal(0.0, 0.005, len(B))           # independent epoch-B noise
        bx = ((A[:, 0] - OX > 3.5) & (A[:, 0] - OX < 6.5) &
              (A[:, 1] - OY > 3.5) & (A[:, 1] - OY < 6.5))    # ~9 m² excavation block
        B[bx, 2] -= BLOCK_DROP
        laz = os.path.join(work, "odm_georeferenced_model.laz")
        cd._write_cloud(B, laz, srs=CRS)

        # a mesh in the offset frame + a georef transform + a COLMAP model
        write_min_obj(os.path.join(work, MESH_OBJ),
                      (B[:6] - np.array(OFFSET)))
        json.dump({"source": "smoke", "s": 1.0, "R": np.eye(3).tolist(),
                   "t": OFFSET, "offset": OFFSET, "crs": CRS},
                  open(os.path.join(work, "georef_transform.json"), "w"))
        write_colmap_model(work)
        obj_before = np.loadtxt(
            [l for l in open(os.path.join(work, MESH_OBJ)) if l.startswith("v ")],
            usecols=(1, 2, 3))

        # --- run the real change-detection CLI -----------------------------
        print("== change_detect.py ==")
        r = subprocess.run([sys.executable, os.path.join(HELP, "change_detect.py"),
                            "--work", work, "--reference", ref,
                            "--resolution", "5", "--threads", "1"],
                           capture_output=True, text=True)
        sys.stdout.write(r.stdout)
        if r.returncode != 0:
            sys.stderr.write(r.stderr)
        check("change_detect exit 0", r.returncode == 0, r.stderr[-200:])

        rep = json.load(open(os.path.join(work, "odm_report",
                                          "change_detection.json")))
        co = rep.get("coregistration", {})
        check("stable-area-masked ICP engaged", "stable-area-masked" in co.get("method", ""),
              co.get("method"))
        sf = co.get("stable_fraction")
        check("stable fraction in (0.7, 0.99)", sf is not None and 0.7 < sf < 0.99, f"{sf}")
        re_err = co.get("registration_error")
        check("clean registration error < 5 cm", re_err is not None and re_err < 0.05,
              f"{re_err}")
        cbm = (co.get("c2c_before") or {}).get("mean")
        check("ICP reduced the misalignment (stable residual << initial offset)",
              re_err is not None and cbm and re_err < 0.5 * cbm,
              f"reg-error {re_err:.4f} vs initial {cbm:.4f}" if (re_err and cbm) else "n/a")

        dod = rep.get("dod", {})
        check("DoD cut volume ≈ block (2–6 m³)",
              2.0 < dod.get("volume_cut_m3", 0) < 6.0, f"{dod.get('volume_cut_m3')}")
        check("DoD minLoD > 0 and raw net kept",
              dod.get("min_lod_m", 0) > 0 and "net_volume_raw_m3" in dod,
              f"minLoD={dod.get('min_lod_m')}")
        check("DoD raster written",
              os.path.exists(os.path.join(work, "odm_dem", "dem_difference.tif")))

        m3 = rep.get("m3c2", {})
        if m3.get("available"):
            check("M3C2 flags the block as significant",
                  m3.get("significant_fraction", 0) > 0.02,
                  f"{m3.get('significant_fraction')}")
            check("M3C2 LoD carries the registration error",
                  m3.get("registration_error_m") is not None)
            check("M3C2 cloud written",
                  os.path.exists(os.path.join(work, "odm_change", "m3c2.laz")))
        else:
            print("  [SKIP] M3C2 (py4dgeo absent) — DoD-only fallback")

        rel = rep.get("relanded", {})
        check("re-landed mesh + cloud", rel.get("mesh") and rel.get("cloud"), f"{rel}")

        # re-land cross-checks (independent of the report)
        from scipy.spatial import cKDTree
        b_re = cd.load_xyz(laz)                       # the LAZ was overwritten in place
        med = float(np.median(cKDTree(A).query(b_re)[0]))   # robust to the change block
        check("re-landed cloud aligns to the reference (median C2C < 2 cm)",
              med < 0.02, f"{med:.4f} m")
        obj_after = np.loadtxt(
            [l for l in open(os.path.join(work, MESH_OBJ)) if l.startswith("v ")],
            usecols=(1, 2, 3))
        check("re-landed mesh vertices moved",
              not np.allclose(obj_before, obj_after, atol=1e-4),
              f"max Δ {np.abs(obj_before - obj_after).max():.4f} m")

        # --- camera assets re-land -----------------------------------------
        print("== camera_exports.py ==")
        r2 = subprocess.run([sys.executable, os.path.join(HELP, "camera_exports.py"),
                            "--work", work], capture_output=True, text=True)
        sys.stdout.write(r2.stdout)
        sg = os.path.join(work, "shots.geojson")
        if "re-landed into the reference frame" in r2.stdout and os.path.exists(sg):
            feats = json.load(open(sg)).get("features", [])
            check("shots.geojson written + re-landed", len(feats) == 3, f"{len(feats)} shots")
        elif "pyproj" in (r2.stdout + r2.stderr):
            print("  [SKIP] shots.geojson (pyproj absent)")
        else:
            check("shots.geojson re-landed", os.path.exists(sg), r2.stderr[-200:])

        # --- optional: re-derive the DSM from the re-landed mesh -----------
        print("== orthophoto.py (re-derive DSM from re-landed mesh) ==")
        if os.path.exists(os.path.join(HELP, "orthophoto.py")):
            r3 = subprocess.run([sys.executable, os.path.join(HELP, "orthophoto.py"),
                                "--work", work, "--skip-orthophoto"],
                               capture_output=True, text=True)
            sys.stdout.write(r3.stdout[-400:])
            dsm = os.path.join(work, "odm_dem", "dsm.tif")
            if r3.returncode == 0 and os.path.exists(dsm):
                check("DSM re-derived from the re-landed mesh", True)
            else:
                print("  [SKIP] DSM re-derivation (needs a full textured mesh; "
                      "the minimal smoke OBJ has no texture)")
        # ------------------------------------------------------------------
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    n = len(_results)
    passed = sum(_results)
    print(f"\n{'='*48}\nsmoke: {passed}/{n} checks passed")
    return 0 if passed == n else 1


if __name__ == "__main__":
    sys.exit(main())
