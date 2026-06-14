#!/usr/bin/env python3
"""
Multi-epoch change detection — co-register a new epoch to a prior reference and
emit difference products (DoD + M3C2).

Multi-campaign excavation documentation needs to *measure* change between seasons:
how much earth was removed, where, and by how much. ODM does this with ``--align``
(co-register a dataset onto a reference frame); Effigies has no such step. This
module is the engine-side contract for it.

Given a **reference cloud** (a prior epoch's
``odm_georeferencing/odm_georeferenced_model.laz``, passed as a node-filesystem
path via the ``align-to`` option) and *this* epoch's georeferenced LAZ, it:

  1. **Co-registers** epoch B onto the reference with PDAL ``filters.icp`` (the same
     recipe ``scripts/benchmark.sh compare`` uses): ICP reports a rigid 4x4, which
     we apply to a *working copy* of epoch B's cloud (``odm_change/aligned.laz``) —
     epoch B's delivered assets are NOT touched (v1 is additive analysis). The
     co-registration **residual** (ICP fitness + cloud-to-cloud mean/RMS distance
     before vs. after) lands in the report so a bad alignment is visible.
  2. **DoD** (DEM of Difference) — rasterises the reference and the aligned epoch B
     to DSMs on a *shared grid* (PDAL ``writers.gdal`` max-Z), subtracts them
     (``odm_dem/dem_difference.tif`` = B - reference), and computes vertical-change
     stats incl. **cut/fill volume** (Σ Δz·cell-area). This is the workhorse product
     for vertical excavation change.
  3. **M3C2** (Lague et al. 2013, via ``py4dgeo``) — signed distance along the local
     surface normal plus a level-of-detection per core point
     (``odm_change/m3c2.laz`` with extra dims ``m3c2_distance`` / ``m3c2_lod`` /
     ``significant``). The real 3D change signal (handles overhangs / steep faces
     that a 2.5-D DoD cannot). Optional: if ``py4dgeo`` is unavailable the step is
     skipped and the DoD products still stand (DoD-only fallback).

All stats are written to ``odm_report/change_detection.json``. The whole module is
**non-fatal and opt-in** (only runs when ``align-to`` is set): a failure here must
never lose epoch B's own reconstruction.

**v1 limitation (honest):** ICP runs on the *whole* cloud, which assumes the scene
is mostly stable between epochs — a least-squares fit will absorb a fraction of a
large, one-sided change into the rigid transform (e.g. a +Δ change over fraction
*f* of the area biases the alignment by ≈ *f·Δ*). For the usual case (localised
excavation change against a stable surround) this is negligible; **stable-area-masked
ICP** (co-register on unchanged ground only) is the v2 fix. ICP is run in a frame
centred on the reference because georeferenced eastings/northings (~1e6) wreck both
the rotation conditioning and float precision otherwise (see ``_decenter_transform``).

Sign convention (DoD and M3C2): positive = surface raised (deposition / back-fill),
negative = surface lowered (excavation / erosion).

Dependencies: PDAL (ICP, transform, rasterise — same binary the LAZ step needs),
NumPy + SciPy (residual KD-tree, also already required by ``benchmark.sh compare``),
GDAL python bindings (read the DSMs, write the difference raster), and optionally
``py4dgeo`` for M3C2.
"""
import argparse
import json
import math
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pointcloud_to_dtm import _resolve_gsd, _cloud_bounds  # noqa: E402

NODATA = -9999.0


# ---------------------------------------------------------------------------
# Pure helpers (no PDAL/py4dgeo) — unit-tested directly.
# ---------------------------------------------------------------------------
def parse_icp_transform(metadata):
    """Extract the rigid 4x4 transform from PDAL ``filters.icp`` ``--metadata``.

    ``filters.icp`` reports the registration of the moving (2nd) cloud onto the
    fixed (1st) one. We search the (nested) metadata for the ICP node and read its
    ``composed`` (preferred) or ``transform`` field — 16 whitespace/comma-separated
    floats in row-major order. Returns a ``(4, 4)`` ``numpy`` array, or ``None`` when
    no transform is present. Mirrors ``benchmark.sh compare``'s ``find_icp`` so the
    two stay byte-compatible. Pure; takes a parsed dict, no I/O."""
    import numpy as np

    def find_icp(o):
        if isinstance(o, dict):
            if "composed" in o or "transform" in o or "converged" in o:
                return o
            for v in o.values():
                r = find_icp(v)
                if r:
                    return r
        elif isinstance(o, list):
            for v in o:
                r = find_icp(v)
                if r:
                    return r
        return None

    st = find_icp(metadata) or {}
    tstr = st.get("composed") or st.get("transform")
    if not tstr:
        return None
    vals = [float(x) for x in str(tstr).replace(",", " ").split()]
    if len(vals) != 16:
        return None
    return np.asarray(vals, dtype=np.float64).reshape(4, 4)


def dod_stats(ref, b, cell_area, nodata=NODATA, threshold=0.0):
    """DEM-of-Difference statistics from two co-located DSM arrays.

    ``ref`` and ``b`` are 2-D elevation arrays on the **same grid**; ``b - ref`` is
    the signed vertical change (positive = surface raised). Only cells valid (not
    ``nodata``) in *both* rasters are compared. ``cell_area`` is the grid cell area
    in m² (gsd²); volumes are Σ Δz·cell_area. Returns ``(diff, stats)`` where
    ``diff`` is the difference array (``nodata`` where either input is invalid) and
    ``stats`` is a dict. Pure (NumPy only) — unit-tested on synthetic DSMs."""
    import numpy as np

    ref = np.asarray(ref, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    valid = (ref != nodata) & (b != nodata)
    diff = np.full(ref.shape, nodata, dtype=np.float64)
    diff[valid] = b[valid] - ref[valid]
    dz = diff[valid]
    if dz.size == 0:
        return diff, {"valid_cells": 0, "note": "no overlapping valid cells"}

    changed = np.abs(dz) > threshold
    fill = dz[dz > 0]                      # surface raised — deposition / back-fill
    cut = dz[dz < 0]                       # surface lowered — excavation / erosion
    stats = {
        "valid_cells": int(dz.size),
        "change_threshold_m": float(threshold),
        "mean_change_m": float(dz.mean()),
        "max_raise_m": float(dz.max()),
        "max_lower_m": float(dz.min()),
        "changed_area_m2": float(int(changed.sum()) * cell_area),
        "volume_fill_m3": float(fill.sum() * cell_area),
        "volume_cut_m3": float(-cut.sum() * cell_area),   # reported positive
        "net_volume_m3": float(dz.sum() * cell_area),
    }
    return diff, stats


def gate(reference, cloud, have_pdal):
    """Decide whether change detection can run. Returns ``(ok, reason)``.

    Skips (non-fatal) when no reference is given, the reference file is missing,
    epoch B's cloud is missing, or PDAL is unavailable. Pure (filesystem reads
    only) — unit-tested for each branch."""
    if not reference:
        return False, "no align-to reference given; skipping change detection"
    if not os.path.exists(reference):
        return False, f"reference cloud not found at {reference}; skipping"
    if not os.path.exists(cloud):
        return False, f"epoch-B cloud not found at {cloud}; skipping"
    if not have_pdal:
        return False, "pdal not found on PATH; skipping change detection"
    return True, "ok"


# ---------------------------------------------------------------------------
# Cloud I/O via PDAL.
# ---------------------------------------------------------------------------
def _count(path):
    j = json.loads(subprocess.check_output(["pdal", "info", "--summary", path], text=True))
    s = j.get("summary", j)
    return int(s.get("num_points") or s.get("count") or 0)


def load_xyz(path, n_target=400000):
    """Decimate a cloud to ~``n_target`` points and load XYZ into a NumPy array.
    Mirrors ``benchmark.sh compare``: ``filters.decimation`` -> text -> ``loadtxt``."""
    import numpy as np
    c = _count(path)
    step = max(1, c // max(1, n_target))
    txt = tempfile.mktemp(suffix=".csv")
    pj = tempfile.mktemp(suffix=".json")
    try:
        pipe = {"pipeline": [path,
                {"type": "filters.decimation", "step": step},
                {"type": "writers.text", "filename": txt, "format": "csv",
                 "order": "X,Y,Z", "keep_unspecified": False, "write_header": False}]}
        open(pj, "w").write(json.dumps(pipe))
        subprocess.check_call(["pdal", "pipeline", pj],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            arr = np.atleast_2d(np.loadtxt(txt, delimiter=","))
        except (ValueError, StopIteration):
            return np.empty((0, 3))
        if arr.size == 0 or arr.shape[1] < 3:
            return np.empty((0, 3))
        return np.ascontiguousarray(arr[:, :3], dtype=np.float64)
    finally:
        for f in (txt, pj):
            try:
                os.remove(f)
            except OSError:
                pass


def _write_cloud(xyz, path, srs=None):
    """Write an Nx3 NumPy array to a LAZ via PDAL (readers.text -> writers.las)."""
    import numpy as np
    csv = tempfile.mktemp(suffix=".csv")
    pj = tempfile.mktemp(suffix=".json")
    try:
        np.savetxt(csv, np.asarray(xyz, dtype=np.float64), delimiter=",",
                   header="X,Y,Z", comments="")
        writer = {"type": "writers.las", "filename": path, "compression": "true"}
        if srs:
            writer["a_srs"] = srs
        pipe = {"pipeline": [{"type": "readers.text", "filename": csv}, writer]}
        open(pj, "w").write(json.dumps(pipe))
        subprocess.check_call(["pdal", "pipeline", pj],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        for f in (csv, pj):
            try:
                os.remove(f)
            except OSError:
                pass


def _decenter_transform(T_centered, offset):
    """Express an ICP transform computed in a frame centred on ``offset`` back in
    the original (un-centred) coordinate frame.

    ICP is run on clouds with ``offset`` subtracted (UTM eastings/northings are
    ~1e6 — a tiny ICP rotation about the *origin* there becomes a metre-scale
    rigid-body sweep, and float math at that magnitude is coarse). For a centred
    transform ``x_c' = R·x_c + t_c`` with ``x_c = x - offset``, the equivalent
    original-frame transform is ``x' = R·x + (t_c + offset - R·offset)``. Pure."""
    import numpy as np
    R = T_centered[:3, :3]
    t_c = T_centered[:3, 3]
    t_orig = t_c + offset - R @ offset
    T = np.eye(4)
    T[:3, :3] = R
    T[:3, 3] = t_orig
    return T


def c2c(moving_xyz, fixed_xyz):
    """Cloud-to-cloud nearest-neighbour distance (mean / RMS), moving -> fixed.
    SciPy KD-tree, same as ``benchmark.sh``. Pure (NumPy/SciPy)."""
    import numpy as np
    from scipy.spatial import cKDTree
    if len(moving_xyz) == 0 or len(fixed_xyz) == 0:
        return None
    d, _ = cKDTree(fixed_xyz).query(moving_xyz, k=1)
    return {"mean": float(d.mean()), "rms": float(np.sqrt(np.mean(d ** 2)))}


def icp_register(reference, cloud):
    """Register ``cloud`` (moving) onto ``reference`` (fixed) via PDAL
    ``filters.icp``; return ``(T, fitness, converged)`` with ``T`` a 4x4 (or None).
    ``writers.null`` keeps the pipeline valid without emitting the merged cloud."""
    pj = tempfile.mktemp(suffix=".json")
    mj = tempfile.mktemp(suffix=".json")
    try:
        pipe = {"pipeline": [reference, cloud,
                {"type": "filters.icp"}, {"type": "writers.null"}]}
        open(pj, "w").write(json.dumps(pipe))
        subprocess.check_call(["pdal", "pipeline", pj, "--metadata", mj],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        meta = json.loads(open(mj).read())
        T = parse_icp_transform(meta)

        def find_icp(o):
            if isinstance(o, dict):
                if "composed" in o or "transform" in o or "converged" in o:
                    return o
                for v in o.values():
                    r = find_icp(v)
                    if r:
                        return r
            elif isinstance(o, list):
                for v in o:
                    r = find_icp(v)
                    if r:
                        return r
            return None
        st = find_icp(meta) or {}
        return T, st.get("fitness"), st.get("converged")
    finally:
        for f in (pj, mj):
            try:
                os.remove(f)
            except OSError:
                pass


def apply_transform(cloud, T, out):
    """Apply a 4x4 transform to ``cloud`` and write ``out`` via PDAL
    ``filters.transformation`` (matrix is 16 space-separated row-major floats)."""
    # space-separated, row-major, 12 sig-figs — matches pointcloud_to_laz.py
    matrix = " ".join(f"{v:.12g}" for v in T.reshape(-1))
    pj = tempfile.mktemp(suffix=".json")
    try:
        pipe = {"pipeline": [cloud,
                {"type": "filters.transformation", "matrix": matrix},
                {"type": "writers.las", "filename": out, "compression": "true",
                 "forward": "all"}]}
        open(pj, "w").write(json.dumps(pipe))
        subprocess.check_call(["pdal", "pipeline", pj],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        try:
            os.remove(pj)
        except OSError:
            pass


def _shared_bounds(ref, b, gsd):
    """Union bounding box of two clouds, snapped to the ``gsd`` grid, plus the
    integer (width, height). Both DSMs rasterised to this exact grid line up
    cell-for-cell so the difference is a plain array subtraction."""
    rb, bb = _cloud_bounds(ref), _cloud_bounds(b)
    if not rb or not bb:
        return None
    minx = math.floor(min(rb[0], bb[0]) / gsd) * gsd
    miny = math.floor(min(rb[1], bb[1]) / gsd) * gsd
    maxx = math.ceil(max(rb[2], bb[2]) / gsd) * gsd
    maxy = math.ceil(max(rb[3], bb[3]) / gsd) * gsd
    width = max(1, int(round((maxx - minx) / gsd)))
    height = max(1, int(round((maxy - miny) / gsd)))
    return minx, miny, maxx, maxy, width, height


def rasterize_dsm(cloud, out, gsd, bounds):
    """Rasterise ``cloud`` to a max-Z DSM GeoTIFF on the explicit shared ``bounds``
    grid (``writers.gdal``). ``bounds`` = ``(minx, miny, maxx, maxy, w, h)``; the
    grid is pinned by ``bounds`` + ``resolution`` (both snapped to the gsd, so the
    cell counts match), and the ``w``/``h`` are used only by the caller's read-back
    shape guard against a 1-px rounding mismatch."""
    minx, miny, maxx, maxy, w, h = bounds
    pj = tempfile.mktemp(suffix=".json")
    try:
        pipe = {"pipeline": [cloud,
                {"type": "writers.gdal", "filename": out,
                 "resolution": gsd, "output_type": "max", "dimension": "Z",
                 "gdaldriver": "GTiff", "data_type": "float32", "nodata": NODATA,
                 "bounds": f"([{minx},{maxx}],[{miny},{maxy}])"}]}
        open(pj, "w").write(json.dumps(pipe))
        subprocess.check_call(["pdal", "pipeline", pj],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        try:
            os.remove(pj)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# M3C2 (py4dgeo) — optional; DoD-only fallback when unavailable.
# ---------------------------------------------------------------------------
def have_py4dgeo():
    try:
        import py4dgeo  # noqa: F401
        return True
    except Exception:
        return False


def run_m3c2(ref_xyz, b_xyz, core_xyz, cyl_radius, normal_radii, threads=1):
    """M3C2 signed distance + level-of-detection of epoch B vs. the reference.

    Returns ``(distances, lodetection)`` 1-D arrays aligned with ``core_xyz``.
    ``set_num_threads`` is pinned (the default multithreaded path segfaults on the
    arm64 build); core points must be C-contiguous float64 (a strided view
    segfaults the C++ core). py4dgeo-gated — unit-tested on a known shift."""
    import numpy as np
    import py4dgeo
    py4dgeo.set_num_threads(max(1, int(threads)))
    e_ref = py4dgeo.Epoch(np.ascontiguousarray(ref_xyz, dtype=np.float64))
    e_b = py4dgeo.Epoch(np.ascontiguousarray(b_xyz, dtype=np.float64))
    core = np.ascontiguousarray(core_xyz, dtype=np.float64)
    m3c2 = py4dgeo.M3C2(epochs=(e_ref, e_b), corepoints=core,
                        cyl_radius=float(cyl_radius),
                        normal_radii=[float(r) for r in normal_radii])
    distances, unc = m3c2.run()
    return np.asarray(distances), np.asarray(unc["lodetection"])


def write_m3c2_laz(core_xyz, distances, lod, out):
    """Write the core points carrying ``m3c2_distance`` / ``m3c2_lod`` /
    ``significant`` extra dimensions to a LAZ (PDAL ``readers.text`` ->
    ``writers.las`` with ``extra_dims``)."""
    import numpy as np
    sig = (np.abs(distances) > lod).astype(np.int32)
    rows = np.column_stack([core_xyz,
                            np.nan_to_num(distances, nan=NODATA),
                            np.nan_to_num(lod, nan=NODATA), sig])
    csv = tempfile.mktemp(suffix=".csv")
    pj = tempfile.mktemp(suffix=".json")
    try:
        np.savetxt(csv, rows, delimiter=",",
                   header="X,Y,Z,m3c2_distance,m3c2_lod,significant", comments="")
        pipe = {"pipeline": [
            {"type": "readers.text", "filename": csv},
            {"type": "writers.las", "filename": out, "compression": "true",
             "extra_dims": "m3c2_distance=float,m3c2_lod=float,significant=int32"}]}
        open(pj, "w").write(json.dumps(pipe))
        subprocess.check_call(["pdal", "pipeline", pj],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        for f in (csv, pj):
            try:
                os.remove(f)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Difference raster I/O.
# ---------------------------------------------------------------------------
def _read_band(tif):
    from osgeo import gdal
    ds = gdal.Open(tif)
    if ds is None:
        return None, None, None, None
    band = ds.GetRasterBand(1)
    return (band.ReadAsArray(), band.GetNoDataValue(),
            ds.GetGeoTransform(), ds.GetProjection())


def _write_diff(diff, geo, proj, out, nodata=NODATA):
    from osgeo import gdal
    import numpy as np
    h, w = diff.shape
    drv = gdal.GetDriverByName("GTiff")
    ds = drv.Create(out, w, h, 1, gdal.GDT_Float32, ["COMPRESS=DEFLATE"])
    ds.SetGeoTransform(geo)
    if proj:
        ds.SetProjection(proj)
    band = ds.GetRasterBand(1)
    band.SetNoDataValue(nodata)
    band.WriteArray(diff.astype(np.float32))
    band.FlushCache()
    ds = None


# ---------------------------------------------------------------------------
# Orchestration.
# ---------------------------------------------------------------------------
def run_change_detection(work, reference, resolution="auto", cloud=None,
                         m3c2_core_target=200000, threads=1):
    """Full pipeline. Non-fatal: returns ``True`` on success, ``False`` (with a
    reason on stderr) when skipped. Writes the change products + JSON report."""
    import shutil
    import numpy as np

    cloud = cloud or os.path.join(work, "odm_georeferenced_model.laz")
    ok, reason = gate(reference, cloud, shutil.which("pdal") is not None)
    if not ok:
        print(f"[change] {reason}", file=sys.stderr)
        return False

    change_dir = os.path.join(work, "odm_change")
    dem_dir = os.path.join(work, "odm_dem")
    report_dir = os.path.join(work, "odm_report")
    for d in (change_dir, dem_dir, report_dir):
        os.makedirs(d, exist_ok=True)

    report = {"reference": os.path.abspath(reference),
              "epoch_b": os.path.abspath(cloud),
              "sign_convention": "epoch B minus reference; positive = surface "
                                 "raised (deposition), negative = lowered (excavation)"}

    # 1. Co-register epoch B onto the reference (ICP) ------------------------
    # ICP runs in a frame CENTRED on the reference (offset subtracted): georeferenced
    # eastings/northings are ~1e6, where a tiny ICP rotation about the origin becomes
    # a metre-scale sweep and float precision is coarse — both wreck the cm-level
    # alignment the DoD/M3C2 actually need. The transform is de-centred back to the
    # original frame before it is applied to epoch B's cloud.
    try:
        ref_xyz = load_xyz(reference)
        b_xyz = load_xyz(cloud)
        offset = np.floor(ref_xyz.mean(axis=0)) if len(ref_xyz) else np.zeros(3)
        before = c2c(b_xyz, ref_xyz)
        ref_c = os.path.join(change_dir, "_ref_centred.laz")
        b_c = os.path.join(change_dir, "_b_centred.laz")
        _write_cloud(ref_xyz - offset, ref_c)
        _write_cloud(b_xyz - offset, b_c)
        T_c, fitness, converged = icp_register(ref_c, b_c)
        for tmp in (ref_c, b_c):
            try:
                os.remove(tmp)
            except OSError:
                pass
        aligned = os.path.join(change_dir, "aligned.laz")
        coreg = {"method": "pdal filters.icp (reference-centred)",
                 "converged": converged, "fitness": fitness, "c2c_before": before}
        if T_c is not None:
            T = _decenter_transform(T_c, offset)
            apply_transform(cloud, T, aligned)
            b_xyz_aligned = b_xyz @ T[:3, :3].T + T[:3, 3]
            coreg["c2c_after"] = c2c(b_xyz_aligned, ref_xyz)
            coreg["transform"] = [float(x) for x in T.reshape(-1)]
        else:
            # No transform recovered — fall back to comparing the unaligned cloud
            # rather than fabricating an identity. Reported as such.
            shutil.copy2(cloud, aligned)
            b_xyz_aligned = b_xyz
            coreg["note"] = "no transform recovered from ICP; using unaligned cloud"
        report["coregistration"] = coreg
        print(f"[change] co-registered (fitness={fitness}, "
              f"before={before}, after={coreg.get('c2c_after')})")
    except Exception as e:
        print(f"[change] co-registration failed (non-fatal): {e}", file=sys.stderr)
        return False

    # 2. DoD — DSM difference on a shared grid -------------------------------
    try:
        gsd = _resolve_gsd(resolution, cloud)
        bounds = _shared_bounds(reference, aligned, gsd)
        if bounds is None:
            raise RuntimeError("could not resolve shared raster bounds")
        ref_dsm = os.path.join(change_dir, "ref_dsm.tif")
        b_dsm = os.path.join(change_dir, "b_dsm.tif")
        rasterize_dsm(reference, ref_dsm, gsd, bounds)
        rasterize_dsm(aligned, b_dsm, gsd, bounds)
        ra, nra, geo, proj = _read_band(ref_dsm)
        ba, nba, _, _ = _read_band(b_dsm)
        if ra is None or ba is None:
            raise RuntimeError("DSM rasterisation produced no readable raster")
        # Guard against a 1-px rounding mismatch: crop both to the common shape.
        h = min(ra.shape[0], ba.shape[0])
        w = min(ra.shape[1], ba.shape[1])
        ra, ba = ra[:h, :w], ba[:h, :w]
        diff, dod = dod_stats(ra, ba, gsd * gsd, nodata=NODATA)
        dod["resolution_m"] = float(gsd)
        diff_path = os.path.join(dem_dir, "dem_difference.tif")
        _write_diff(diff, geo, proj, diff_path)
        dod["raster"] = "odm_dem/dem_difference.tif"
        report["dod"] = dod
        for tmp in (ref_dsm, b_dsm):
            try:
                os.remove(tmp)
            except OSError:
                pass
        print(f"[change] DoD: net {dod.get('net_volume_m3', 0):.2f} m³ "
              f"(fill {dod.get('volume_fill_m3', 0):.2f}, cut {dod.get('volume_cut_m3', 0):.2f}), "
              f"changed area {dod.get('changed_area_m2', 0):.1f} m² @ {gsd*100:.1f} cm/px")
    except Exception as e:
        print(f"[change] DoD failed (non-fatal): {e}", file=sys.stderr)
        report["dod"] = {"error": str(e)}

    # 3. M3C2 (optional) -----------------------------------------------------
    if not have_py4dgeo():
        report["m3c2"] = {"available": False,
                          "reason": "py4dgeo not installed; DoD-only fallback"}
        print("[change] py4dgeo unavailable; DoD-only (no M3C2)", file=sys.stderr)
    else:
        try:
            # Scale the M3C2 search radii to the cloud's own point spacing so the
            # normals/cylinder are meaningful at any data scale (object vs. site).
            core = b_xyz_aligned
            if len(core) > m3c2_core_target:
                step = max(1, len(core) // m3c2_core_target)
                core = np.ascontiguousarray(core[::step])
            spacing = _median_spacing(ref_xyz)
            cyl_radius = max(spacing * 3.0, 1e-6)
            normal_radii = [cyl_radius, cyl_radius * 2.0, cyl_radius * 4.0]
            # Run M3C2 in the centred frame too: py4dgeo's core works in float, and
            # a northing of ~5e6 has metre-scale float resolution that would swamp a
            # cm-level change. Distances are translation-invariant, so the signed
            # change is unchanged; the LAZ is written back in the original frame.
            distances, lod = run_m3c2(ref_xyz - offset, b_xyz_aligned - offset,
                                      core - offset, cyl_radius, normal_radii,
                                      threads=threads)
            finite = distances[np.isfinite(distances)]
            sig = np.abs(distances) > lod
            report["m3c2"] = {
                "available": True,
                "core_points": int(len(core)),
                "cyl_radius_m": float(cyl_radius),
                "normal_radii_m": [float(r) for r in normal_radii],
                "median_change_m": float(np.median(finite)) if finite.size else None,
                "lod_median_m": float(np.nanmedian(lod)),
                "significant_fraction": float(np.nanmean(sig))}
            # The stats are recorded; write the cloud best-effort so a LAZ-writer
            # hiccup does not void them.
            try:
                m3c2_laz = os.path.join(change_dir, "m3c2.laz")
                write_m3c2_laz(core, distances, lod, m3c2_laz)
                report["m3c2"]["cloud"] = "odm_change/m3c2.laz"
            except Exception as e:
                print(f"[change] M3C2 cloud write failed (stats kept): {e}",
                      file=sys.stderr)
            print(f"[change] M3C2: median {report['m3c2']['median_change_m']} m, "
                  f"{100*report['m3c2']['significant_fraction']:.0f}% significant "
                  f"(LoD median {report['m3c2']['lod_median_m']:.3f} m)")
        except Exception as e:
            report["m3c2"] = {"available": False, "reason": f"M3C2 failed: {e}"}
            print(f"[change] M3C2 failed (non-fatal): {e}", file=sys.stderr)

    # 4. Report --------------------------------------------------------------
    out_json = os.path.join(report_dir, "change_detection.json")
    with open(out_json, "w") as f:
        json.dump(report, f, indent=2)
    print(f"[change] wrote {out_json}")
    return True


def _median_spacing(xyz, sample=20000):
    """Median nearest-neighbour spacing of a cloud (for radius scaling)."""
    import numpy as np
    from scipy.spatial import cKDTree
    if len(xyz) < 2:
        return 0.05
    s = xyz if len(xyz) <= sample else xyz[np.linspace(0, len(xyz) - 1, sample).astype(int)]
    d, _ = cKDTree(s).query(s, k=2)
    nz = d[:, 1][d[:, 1] > 0]
    return float(np.median(nz)) if nz.size else 0.05


def main():
    ap = argparse.ArgumentParser(description="Multi-epoch change detection "
                                             "(co-registration + DoD + M3C2)")
    ap.add_argument("--work", required=True, help="OpenMVS workdir")
    ap.add_argument("--reference", required=True,
                    help="prior epoch's reference cloud (LAZ/LAS/PLY)")
    ap.add_argument("--resolution", default="auto",
                    help="DoD ground sample distance in cm/px, or 'auto'")
    ap.add_argument("--cloud", default=None,
                    help="epoch-B cloud (default <work>/odm_georeferenced_model.laz)")
    ap.add_argument("--threads", type=int, default=1,
                    help="py4dgeo thread cap (1 avoids the arm64 multithread segfault)")
    args = ap.parse_args()
    run_change_detection(args.work, args.reference, args.resolution,
                         args.cloud, threads=args.threads)


if __name__ == "__main__":
    main()
