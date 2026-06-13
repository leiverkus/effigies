#!/usr/bin/env python3
"""
GCP-constrained bundle adjustment for Effigies (pycolmap / COLMAP's own Ceres BA).

The default georeferencing path (helpers/georef_bridge.py) is **post-hoc and
rigid**: COLMAP reconstructs freely, each GCP's local position is triangulated and
one 7-DoF Umeyama similarity (scale + rotation + translation) maps the block to the
surveyed world. A rigid similarity cannot absorb reconstruction **drift** (bending
/ non-uniform scale across the block), so the check-point RMSE it leaves is a floor.

This module anchors the marked GCPs at their surveyed world coordinates and
re-optimises the cameras + tie points to be consistent with them, removing drift.
It runs on the **sparse** model *before* ``image_undistorter`` (so densify / mesh /
texture / ortho all inherit the corrected, world-frame poses), and is opt-in via
``--gcp-bundle-adjust`` (default off; the safe post-hoc Umeyama stays the default).

The offset trick (keeps every downstream consumer unchanged)
------------------------------------------------------------
The BA rewrites the sparse model into an **offset-subtracted world frame**
(``offset = georef_bridge._xy_offset(world)``; Z absolute, matching the existing
convention) and writes ``georef_transform.json`` as the identity-with-offset
``{s:1, R:I, t:offset, offset:offset, crs}``. Then every existing consumer just
works: ``pointcloud_to_laz`` does ``s·R·v+t = v+offset`` (full UTM); the OBJ rewrite
does ``v+offset−offset = v`` (identity, OBJ stays offset-world); ortho/DSM origins
add ``offset`` back; ``coords.txt`` carries ``offset``.

Pipeline placement: ``pipeline/sparse_colmap.sh``, after ``model_converter`` and
before ``image_undistorter``. Non-fatal: on any failure the free sparse model is
kept and georef_bridge.py's post-hoc Umeyama still runs.

Dependencies: pycolmap (built into the Effigies image; imported lazily so the rest
of the engine and the test-suite gate cleanly when it is absent) + numpy. Reuses
georef_bridge for GCP parsing, multi-view localization and the offset/Umeyama math.
"""
import argparse
import os
import sys
import json
import shutil
import subprocess

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import georef_bridge as gb  # noqa: E402


def _import_pycolmap():
    """Import pycolmap or raise a clear, actionable error (non-fatal upstream)."""
    try:
        import pycolmap  # noqa: F401
        return pycolmap
    except ImportError as e:
        raise RuntimeError(
            "pycolmap is required for --gcp-bundle-adjust but is not importable. "
            "The Effigies image bakes it in (built from the pinned COLMAP source); "
            "if you see this, the build did not install pycolmap.") from e


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def split_control_check(entries):
    """Split parsed gcp_list entries into (control, check) by the ``check`` flag.

    Control GCPs drive the solve (initial alignment + BA anchors); check GCPs are
    held out and used only to report an independent CP-RMSE."""
    control = [e for e in entries if not e.get("check")]
    check = [e for e in entries if e.get("check")]
    return control, check


# 'auto' mode: the BA must beat the post-hoc Umeyama by a real margin to win — a
# negligible gain does not justify the BA's extra cost, its hard-constraint warp
# risk on imperfect GCPs, or its slightly weaker reproducibility. Both a RELATIVE
# margin (default 10 %) and a MINIMUM ABSOLUTE gain (default 1 mm) must be cleared,
# so a consistent block (both RMSEs ~0) deterministically keeps the safe path.
_DEFAULT_MARGIN = float(os.environ.get("EFFIGIES_GCP_BA_MARGIN", "0.10"))
_MIN_ABS_GAIN_M = float(os.environ.get("EFFIGIES_GCP_BA_MIN_GAIN_M", "0.001"))


def _arbitrate_decision(cp_umeyama, cp_ba, n_check,
                        margin=_DEFAULT_MARGIN, min_abs_gain=_MIN_ABS_GAIN_M):
    """Decide the winning georeferencing path from the two held-out check-point
    RMSEs (metres). Returns 'ba', 'umeyama', or 'fallback' (no usable comparison).

    The bundle adjustment wins only if it lowers the independent CP-RMSE by both the
    relative margin AND the absolute floor — otherwise the conservative post-hoc
    similarity is kept. With no check points there is no honest metric (the BA
    drives its CONTROL residuals to ~0 by construction), so we fall back."""
    if not n_check or cp_umeyama is None or cp_ba is None:
        return "fallback"
    gain = cp_umeyama - cp_ba
    if gain > margin * cp_umeyama and gain > min_abs_gain:
        return "ba"
    return "umeyama"


def _write_arbitration(work, record):
    """Drop the arbitration sidecar (read back by georef_bridge for audit)."""
    with open(os.path.join(work, "gcp_ba_arbitration.json"), "w") as f:
        json.dump(record, f, indent=2)


def _backup_model(model_dir):
    """Copy the free sparse model aside so a losing BA can be rolled back. The
    sparse model (cameras/images/points3D, bin+txt) is small — a few MB."""
    bdir = model_dir.rstrip("/") + ".free_backup"
    if os.path.exists(bdir):
        shutil.rmtree(bdir)
    shutil.copytree(model_dir, bdir)
    return bdir


def _restore_model(model_dir, bdir):
    shutil.rmtree(model_dir)
    shutil.move(bdir, model_dir)


def _discard_backup(bdir):
    shutil.rmtree(bdir, ignore_errors=True)


def _rmat_to_quat_xyzw(R):
    """3x3 rotation matrix -> unit quaternion [x, y, z, w] (pycolmap's order)."""
    R = np.asarray(R, float)
    tr = np.trace(R)
    if tr > 0:
        s = np.sqrt(tr + 1.0) * 2
        w = 0.25 * s
        x = (R[2, 1] - R[1, 2]) / s
        y = (R[0, 2] - R[2, 0]) / s
        z = (R[1, 0] - R[0, 1]) / s
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = np.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2]) * 2
        w = (R[2, 1] - R[1, 2]) / s
        x = 0.25 * s
        y = (R[0, 1] + R[1, 0]) / s
        z = (R[0, 2] + R[2, 0]) / s
    elif R[1, 1] > R[2, 2]:
        s = np.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2]) * 2
        w = (R[0, 2] - R[2, 0]) / s
        x = (R[0, 1] + R[1, 0]) / s
        y = 0.25 * s
        z = (R[1, 2] + R[2, 1]) / s
    else:
        s = np.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1]) * 2
        w = (R[1, 0] - R[0, 1]) / s
        x = (R[0, 2] + R[2, 0]) / s
        y = (R[1, 2] + R[2, 1]) / s
        z = 0.25 * s
    q = np.array([x, y, z, w], float)
    return q / np.linalg.norm(q)


def _sim3d(pc, s, R, t):
    """Build a pycolmap.Sim3d(scale, Rotation3d, translation) from s, R(3x3), t(3)."""
    rot = pc.Rotation3d(_rmat_to_quat_xyzw(R))
    return pc.Sim3d(float(s), rot, np.asarray(t, float))


def _images_by_name(rec):
    """{name: image, basename(name): image} for the registered images."""
    out = {}
    for img in rec.images.values():
        out[img.name] = img
        out[os.path.basename(img.name)] = img
    return out


def _ceres_cost(summary, attr):
    """Pull a cost (``initial_cost`` / ``final_cost``) from a pycolmap
    CeresBundleAdjustmentSummary. The costs live on the wrapped pyceres summary
    (``summary.ceres_summary``, mirroring ceres::Solver::Summary); fall back to a
    direct attribute for other binding variants. Returns None if unavailable (the
    cost fields are diagnostic, not load-bearing)."""
    for obj in (getattr(summary, "ceres_summary", None), summary):
        v = getattr(obj, attr, None) if obj is not None else None
        if v is not None:
            return float(v)
    return None


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------
def run_gcp_bundle_adjust(work, gcp_path, crs="auto", refine_intrinsics=False,
                          write_transform=True):
    """Run a GCP-constrained bundle adjustment on ``<work>/sparse/0``.

    Steps:
      1. Parse gcp_list.txt; split control / check points.
      2. Initial alignment: multi-view-triangulate the control GCPs in the local
         frame and fit a Umeyama similarity to their world coords; transform the
         reconstruction into the offset-world frame (Sim3d(s, R, t-offset)).
      3. Anchor each control GCP with ≥2 marked observations as a CONSTANT 3D point
         at ``world - offset`` (the raw gcp_list pixel is the correct 2D obs — the
         COLMAP camera carries the distortion model).
      4. Bundle-adjust all registered images (GCP points constant; intrinsics fixed
         unless ``refine_intrinsics``); the constant control GCPs define the datum.
      5. Write back sparse/0 (binary + text) in the offset-world frame and
         georef_transform.json (source=colmap-gcp-ba, identity-with-offset), with a
         residuals block including the held-out check-point CP-RMSE.

    Returns the transform dict. Raises on any failure (callers treat it as
    non-fatal and fall back to the post-hoc Umeyama path)."""
    pc = _import_pycolmap()

    model_dir = gb._find_colmap_model(work)
    if model_dir is None:
        raise RuntimeError(f"no COLMAP text model under {work}/sparse; run the "
                           f"sparse stage + model_converter first")

    crs_header, entries = gb.parse_gcp_list(gcp_path)
    if not entries:
        raise RuntimeError(f"no GCP entries parsed from {gcp_path}")
    control, check = split_control_check(entries)
    if not control:
        raise RuntimeError("all GCPs are flagged 'check'; no control points to "
                           "constrain the bundle adjustment")

    # --- 1. initial alignment on the control GCPs (reuse the post-hoc math) ---
    local, world, info = gb.gcp_correspondences(model_dir, control)
    s, R, t = gb.umeyama_similarity(local, world)
    offset = gb._xy_offset(world)
    print(f"[gcp-ba] initial Umeyama: scale={s:.6g}, offset="
          f"{offset[0]:.1f} {offset[1]:.1f}; control={len(world)} check={len(check)}")

    # --- 2. load the sparse model and move it into the offset-world frame ---
    rec = pc.Reconstruction(model_dir)
    rec.transform(_sim3d(pc, s, R, t - offset))

    by_name = _images_by_name(rec)

    # --- 3. anchor each control GCP as a constant 3D point at world-offset ---
    # Group by world coord (a GCP is marked once per image it appears in).
    groups = {}
    for e in control:
        groups.setdefault(tuple(np.round(e["world"], 4)), []).append(e)

    gcp_ids = []
    n_anchored = n_skipped = 0
    for key, es in groups.items():
        marked = [(e, by_name.get(e["image"]) or by_name.get(os.path.basename(e["image"])))
                  for e in es]
        marked = [(e, img) for e, img in marked if img is not None]
        if len(marked) < 2:
            # Single-view GCPs cannot be BA points (Ceres needs track length > 1);
            # they still informed the initial alignment. (Consistent with the
            # post-hoc path's single-view nearest-point fallback.)
            n_skipped += 1
            continue
        track = pc.Track()
        for e, img in marked:
            idx = len(img.points2D)
            img.points2D.append(pc.Point2D(np.asarray(e["px"], float)))
            track.add_element(img.image_id, idx)
        xyz = np.asarray(key, float) - offset
        gcp_ids.append(rec.add_point3D(xyz, track))
        n_anchored += 1
    if n_anchored < 2:
        raise RuntimeError(
            f"only {n_anchored} control GCP(s) had ≥2 marked images; need ≥2 "
            f"multi-view control GCPs to anchor a bundle adjustment")
    print(f"[gcp-ba] anchored {n_anchored} control GCPs as constant points "
          f"({n_skipped} single-view skipped)")

    # --- 4. configure + solve the bundle adjustment ---
    cfg = pc.BundleAdjustmentConfig()
    for iid in rec.reg_image_ids():
        cfg.add_image(iid)
    for gid in gcp_ids:
        cfg.add_constant_point(gid)
    # The constant control GCPs define the datum: ≥3 non-collinear constant points
    # remove the full 7-DoF gauge freedom, so every camera and tie point is free to
    # move toward the surveyed frame. Do NOT fix two camera poses
    # (TWO_CAMS_FROM_WORLD) — that would nail down the very cameras the GCPs should
    # be allowed to correct. UNSPECIFIED leaves the gauge to the constant points.
    try:
        cfg.fix_gauge(pc.BundleAdjustmentGauge.UNSPECIFIED)
    except Exception:
        pass
    if not refine_intrinsics:
        for cam_id in rec.cameras:
            cfg.set_constant_cam_intrinsics(cam_id)

    ba = pc.create_default_bundle_adjuster(pc.BundleAdjustmentOptions(), cfg, rec)
    summary = ba.solve()
    cost_before = _ceres_cost(summary, "initial_cost")
    cost_after = _ceres_cost(summary, "final_cost")
    print(f"[gcp-ba] BA converged: cost {cost_before} -> {cost_after}")

    # --- 5. write back the corrected model (binary + text) ---
    rec.write(model_dir)            # binary cameras.bin/images.bin/points3D.bin
    try:
        rec.write_text(model_dir)   # TXT for georef_bridge / diagnostics
    except (AttributeError, RuntimeError):
        subprocess.run(["colmap", "model_converter", "--input_path", model_dir,
                        "--output_path", model_dir, "--output_type", "TXT"],
                       check=True)
    if not os.path.exists(os.path.join(model_dir, "images.txt")):
        raise RuntimeError("BA write-back produced no text model (images.txt)")

    # --- residuals: re-triangulate from the corrected cameras (offset-world),
    # add the offset back, compare to the surveyed world. Control points show how
    # consistent the block now is; the held-out CHECK points are the independent
    # CP-RMSE (the headline metric vs the post-hoc Umeyama). ---
    residuals = {
        "n_control": len(world),
        "n_check": len({tuple(np.round(e["world"], 4)) for e in check}),
        "n_anchored": n_anchored,
        "ba_cost_before": cost_before,
        "ba_cost_after": cost_after,
        "gcp_localization": info,
    }
    lc, wc, _ = gb.gcp_correspondences(model_dir, control, min_points=1)
    ctrl = gb.solve_residuals(1.0, np.eye(3), offset, lc, wc)
    residuals["control"] = ctrl
    residuals["control_rms_3d"] = ctrl["rms_3d"]
    residuals["control_rms_horizontal"] = ctrl["rms_horizontal"]
    residuals["control_rms_vertical"] = ctrl["rms_vertical"]
    if check:
        try:
            lk, wk, _ = gb.gcp_correspondences(model_dir, check, min_points=1)
            chk = gb.solve_residuals(1.0, np.eye(3), offset, lk, wk)
            residuals["check"] = chk
            residuals["check_rms_3d"] = chk["rms_3d"]
            residuals["check_rms_horizontal"] = chk["rms_horizontal"]
            residuals["check_rms_vertical"] = chk["rms_vertical"]
            print(f"[gcp-ba] independent check-point RMSE: 3D {chk['rms_3d']:.3f} m "
                  f"(horiz {chk['rms_horizontal']:.3f}, vert {chk['rms_vertical']:.3f}) "
                  f"over {chk['count']} check points")
        except RuntimeError as e:
            residuals["check"] = None
            print(f"[gcp-ba] WARN: could not localize check points: {e}",
                  file=sys.stderr)

    # Flat top-level residual keys (rms_3d / rms_horizontal / rms_vertical / count)
    # so the quality-report PDF and any generic consumer read GCP-BA results the
    # same as a post-hoc solve. Prefer the HELD-OUT check points — the honest
    # independent CP-RMSE — and fall back to control when none are marked.
    flat = residuals.get("check") or residuals.get("control")
    for k in ("rms_3d", "rms_horizontal", "rms_vertical", "max_3d", "count"):
        residuals[k] = flat[k]

    resolved_crs = crs if crs not in ("auto", "", "local") else crs_header
    transform = {
        "source": "colmap-gcp-ba",
        "s": 1.0,
        "R": np.eye(3).tolist(),
        "t": offset.tolist(),
        "offset": offset.tolist(),
        "crs": resolved_crs,
        "residuals": residuals,
    }
    # The sparse model on disk is always rewritten (the corrected, world-frame poses
    # are what downstream consumes). Writing georef_transform.json is optional: in
    # 'auto' mode the arbiter defers it until the BA has won the check-RMSE contest.
    if write_transform:
        tr_path = os.path.join(work, "georef_transform.json")
        with open(tr_path, "w") as f:
            json.dump(transform, f, indent=2)
        print(f"[gcp-ba] wrote {tr_path} (source=colmap-gcp-ba, "
              f"t=offset={offset[:2].tolist()}, crs={resolved_crs})")
    return transform


def run_arbitrated(work, gcp_path, crs="auto", refine_intrinsics=False,
                   margin=_DEFAULT_MARGIN):
    """'auto' mode: run BOTH the post-hoc Umeyama and the GCP-BA, keep whichever
    gives the lower INDEPENDENT check-point RMSE.

    The comparison is a cheap SPARSE-model metric (no double OpenMVS run): both
    paths triangulate the held-out check GCPs and measure them against the surveyed
    coords. The BA always rewrites sparse/0 in place, so we back it up first and
    restore the free model if the post-hoc path wins. Auditability: both RMSEs and
    the decision land in a sidecar (folded into georef_transform.json by
    georef_bridge) — on a BA win, directly in the colmap-gcp-ba transform.

    Returns the arbitration record. Raises only on hard pycolmap/IO errors (the
    caller treats GCP-BA as non-fatal and falls back to the post-hoc path)."""
    model_dir = gb._find_colmap_model(work)
    if model_dir is None:
        raise RuntimeError(f"no COLMAP text model under {work}/sparse")

    _, entries = gb.parse_gcp_list(gcp_path)
    control, check = split_control_check(entries)
    n_check_groups = len({tuple(np.round(e["world"], 4)) for e in check})

    # No held-out check points -> no honest metric to arbitrate on. Keep the safe
    # post-hoc path (the bridge solves it later) and record why.
    if n_check_groups == 0:
        rec = {"winner": "umeyama", "reason": "no check points to compare on "
               "(mark a gcp_list.txt line with a trailing 'check' to enable auto)",
               "margin": margin, "cp_umeyama": None, "cp_ba": None, "n_check": 0}
        _write_arbitration(work, rec)
        print(f"[gcp-ba] auto: {rec['reason']}; keeping post-hoc similarity",
              file=sys.stderr)
        return rec

    # (A) post-hoc Umeyama check-RMSE on the FREE model (measure only)
    cp_umeyama = gb.evaluate_umeyama_cp(model_dir, control, check)

    # (B) GCP-BA: rewrites sparse/0 -> back it up first so we can roll back
    backup = _backup_model(model_dir)
    try:
        transform = run_gcp_bundle_adjust(work, gcp_path, crs=crs,
                                          refine_intrinsics=refine_intrinsics,
                                          write_transform=False)
    except Exception:
        _restore_model(model_dir, backup)   # never leave a half-written model
        raise
    cp_ba = transform["residuals"].get("check_rms_3d")

    decision = _arbitrate_decision(cp_umeyama, cp_ba, n_check_groups, margin)
    rec = {"winner": "ba" if decision == "ba" else "umeyama",
           "decision": decision, "margin": margin,
           "cp_umeyama": cp_umeyama, "cp_ba": cp_ba, "n_check": n_check_groups}
    print(f"[gcp-ba] auto arbitration: check-RMSE umeyama={cp_umeyama:.4f} m vs "
          f"ba={cp_ba:.4f} m (margin {margin:.0%}) -> winner={rec['winner']}")

    if decision == "ba":
        transform["arbitration"] = rec
        transform["residuals"]["alternative"] = {
            "source": "colmap-gcp-umeyama", "check_rms_3d": cp_umeyama}
        with open(os.path.join(work, "georef_transform.json"), "w") as f:
            json.dump(transform, f, indent=2)
        _discard_backup(backup)
        print("[gcp-ba] auto: BA wins; kept the bundle-adjusted sparse model")
    else:
        _restore_model(model_dir, backup)   # undo the BA rewrite of sparse/0
        print("[gcp-ba] auto: post-hoc similarity wins; restored the free sparse "
              "model (the georef bridge will solve it)")
    _write_arbitration(work, rec)
    return rec


def main():
    ap = argparse.ArgumentParser(description="GCP-constrained bundle adjustment "
                                             "(pycolmap) on the COLMAP sparse model")
    ap.add_argument("--work", required=True, help="OpenMVS/COLMAP workdir (holds sparse/0)")
    ap.add_argument("--gcp", required=True, help="path to gcp_list.txt (ODM format)")
    ap.add_argument("--crs", default="auto", help="target CRS (EPSG); 'auto' uses the gcp_list header")
    ap.add_argument("--mode", default="on", choices=["on", "auto"],
                    help="'on': always bundle-adjust; 'auto': keep BA only if it beats "
                         "the post-hoc similarity on the held-out check-point RMSE")
    ap.add_argument("--refine-intrinsics", action="store_true",
                    help="also refine camera intrinsics (default: keep them fixed)")
    args = ap.parse_args()
    if args.mode == "auto":
        run_arbitrated(args.work, args.gcp, crs=args.crs,
                       refine_intrinsics=args.refine_intrinsics)
    else:
        run_gcp_bundle_adjust(args.work, args.gcp, crs=args.crs,
                              refine_intrinsics=args.refine_intrinsics)


if __name__ == "__main__":
    main()
