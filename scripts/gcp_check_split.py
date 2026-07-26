#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Patrick Leiverkus
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Mark a reproducible subset of an ODM gcp_list.txt as held-out CHECK points.

Effigies reads a trailing ``check`` token as "measured but excluded from the georef
solve / bundle adjustment" (helpers/georef_bridge.py: parse_gcp_list), which is what
makes an independent check-point RMSE possible: the transform never sees these
points, so their residual is a genuine out-of-sample error rather than a fit
residual.

Selection rule — deterministic, no RNG, no hand-picking:

  1. Project the target centroids onto the block's principal horizontal axis (PCA
     over world XY), and sort along it. Trench blocks are elongated, so this axis
     carries the spread that matters.
  2. Never hold out the two extreme points. They anchor the similarity; removing
     them would force the transform to be *extrapolated* to the block edges and the
     resulting RMSE would report that extrapolation instead of the georeferencing.
  3. From the interior, take evenly spaced points to reach ~30 % held out, and
     require at least 4 control points to remain (one more than the 3 a similarity
     needs, so the fit keeps some redundancy).

Hand-picking check points after seeing residuals is the classic way to make an
accuracy figure look good; a fixed rule stated up front is the point of this script.

Usage:
  gcp_check_split.py <gcp_list.txt> [-o out.txt] [--fraction 0.3] [--dry-run]
"""
import argparse
import os
import sys
from collections import OrderedDict


def parse(path):
    """-> (crs_header, OrderedDict[(x,y,z)] -> [line, ...], other_lines)"""
    with open(path) as f:
        raw = [l.rstrip("\n") for l in f]
    lines = [l for l in raw if l.strip() and not l.startswith("#")]
    if not lines:
        raise SystemExit(f"{path}: empty")
    crs, body = lines[0].strip(), lines[1:]
    pts = OrderedDict()
    for l in body:
        p = l.split()
        if len(p) < 6:
            continue
        key = (float(p[0]), float(p[1]), float(p[2]))
        pts.setdefault(key, []).append(l)
    return crs, pts


def principal_order(keys):
    """Indices of keys sorted along the dominant horizontal axis (PCA, numpy-free)."""
    n = len(keys)
    cx = sum(k[0] for k in keys) / n
    cy = sum(k[1] for k in keys) / n
    # 2x2 covariance
    sxx = sum((k[0] - cx) ** 2 for k in keys) / n
    syy = sum((k[1] - cy) ** 2 for k in keys) / n
    sxy = sum((k[0] - cx) * (k[1] - cy) for k in keys) / n
    # dominant eigenvector of [[sxx,sxy],[sxy,syy]]
    tr, det = sxx + syy, sxx * syy - sxy * sxy
    disc = max(tr * tr / 4.0 - det, 0.0) ** 0.5
    lam = tr / 2.0 + disc
    if abs(sxy) > 1e-12:
        vx, vy = lam - syy, sxy
    else:
        vx, vy = (1.0, 0.0) if sxx >= syy else (0.0, 1.0)
    norm = (vx * vx + vy * vy) ** 0.5 or 1.0
    vx, vy = vx / norm, vy / norm
    proj = [((k[0] - cx) * vx + (k[1] - cy) * vy, i) for i, k in enumerate(keys)]
    proj.sort()
    return [i for _, i in proj], (vx, vy)


def choose_check(keys, fraction, min_control=4):
    order, axis = principal_order(keys)
    n = len(keys)
    n_check = max(1, round(fraction * n))
    n_check = min(n_check, n - min_control)
    if n_check < 1:
        return [], axis, "too few points to hold any out"
    interior = order[1:-1]              # rule 2: extremes stay control
    if not interior:
        return [], axis, "no interior points"
    n_check = min(n_check, len(interior))
    # evenly spaced picks across the interior, centred
    step = len(interior) / float(n_check)
    idx = sorted({interior[min(int(step * (i + 0.5)), len(interior) - 1)]
                  for i in range(n_check)})
    return idx, axis, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gcp_list")
    ap.add_argument("-o", "--output")
    ap.add_argument("--fraction", type=float, default=0.30)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    crs, pts = parse(a.gcp_list)
    keys = list(pts.keys())
    already = sum(1 for ls in pts.values() for l in ls if l.split()[-1].lower() == "check")
    if already:
        raise SystemExit(f"{a.gcp_list}: already contains {already} check lines — refusing "
                         "to re-split (delete them or re-export first)")

    idx, axis, err = choose_check(keys, a.fraction, min_control=4)
    if err:
        raise SystemExit(f"{a.gcp_list}: {err} ({len(keys)} points)")
    check = set(idx)

    print(f"{os.path.basename(a.gcp_list)}")
    print(f"  CRS            {crs}")
    print(f"  points         {len(keys)}  ({sum(len(v) for v in pts.values())} observations)")
    print(f"  principal axis ({axis[0]:+.3f}, {axis[1]:+.3f})")
    print(f"  control        {len(keys) - len(check)}")
    print(f"  check          {len(check)}")
    obs = [len(v) for v in pts.values()]
    median_obs = sorted(obs)[len(obs) // 2]
    thin = []
    for i, k in enumerate(keys):
        role = "CHECK  " if i in check else "control"
        lbl = " ".join(pts[k][0].split()[6:]) or "-"
        print(f"    {role}  E={k[0]:.3f} N={k[1]:.3f} Z={k[2]:.3f}  {lbl}  "
              f"({len(pts[k])} obs)")
        if i in check and len(pts[k]) < median_obs:
            thin.append((lbl, len(pts[k])))
    if thin:
        # Not a reason to re-pick — choosing check points by observation count would
        # flatter the result. But a thinly-marked check point has a weakly
        # triangulated reconstruction position, so its residual carries marking noise
        # on top of the georeferencing error. Compare rms_3d against max_3d in
        # georef_transform.json to see whether one such point dominates.
        print(f"  NOTE  {len(thin)} check point(s) below the median observation count "
              f"({median_obs}): " + ", ".join(f"{l} ({c} obs)" for l, c in thin))
        print("        Their residuals include marking/triangulation noise, not just "
              "georeferencing error — read rms_3d together with max_3d.")

    out_lines = [crs]
    for i, k in enumerate(keys):
        for l in pts[k]:
            out_lines.append(l + " check" if i in check else l)

    if a.dry_run:
        print("  (dry run — nothing written)")
        return 0
    dest = a.output or a.gcp_list
    with open(dest, "w") as f:
        f.write("\n".join(out_lines) + "\n")
    print(f"  written        {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
