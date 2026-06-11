#!/usr/bin/env bash
# Benchmark harness for Effigies vs. other photogrammetry engines.
#
# Scaffold for ROADMAP v0.4.0 ("Benchmark suite comparing Effigies output against
# stock ODM / Metashape / RealityCapture on shared datasets"). Two modes:
#
#   benchmark.sh run <images_dir> [work_dir] [gpu_flag]
#       Run the Effigies pipeline with PER-STAGE timing, then emit a JSON report
#       of timings + output stats. Timings are derived from run.sh's own stage
#       markers (the pipeline is NOT modified — we just timestamp its output).
#
#   benchmark.sh stats <mesh.obj | cloud.las|.laz|.ply> [out.json]
#                       [--no-roughness] [--rough-k K] [--rough-sample N]
#       Compute comparable quality metrics on ANY engine's output. Point it at a
#       Metashape / RealityCapture export to get numbers on the same scale.
#       Includes surface ROUGHNESS — the local plane-fit residual (CloudCompare-
#       style: per point, distance to the best-fit plane of its k nearest
#       neighbours, reported as mean/std/rms/p95/max). This is the detail-vs-noise
#       signal behind H2 (recovered detail) and H5 (flat-region noise). Roughness
#       needs scipy (in the Effigies image); without it the field reports a skip.
#       Tune with --rough-k (neighbours, default 16) and --rough-sample (query
#       points, default 50000); --no-roughness turns it off.
#
#   benchmark.sh compare <output_cloud> <reference_cloud> [out.json] [--no-icp] [--sample N] [--eps E]
#       Cloud-to-reference distance — the accuracy core of the comparison
#       literature (CloudCompare C2C vs. TLS in Gabara & Sawicki 2023, Cutugno
#       2022; see docs/benchmark-literature.md). ICP-aligns the output to the
#       reference (PDAL filters.icp), then nearest-neighbour distance (scipy
#       cKDTree) → {mean, std, rms, p95, max} plus completeness. Clouds are
#       decimated to ~N points (default 1e6) for tractability — reported, not
#       silent. Units follow the data (georeferenced = metres).
#
#   benchmark.sh cprmse <pairs.csv> [out.json]
#       Check-point RMSE — surveyed control points vs. their modelled position.
#       CSV rows hold the 6 coordinates world_x,world_y,world_z,model_x,model_y,
#       model_z (an optional leading id/label column is ignored). Reports RMSE
#       per axis and 3D, mirroring the ChP-RMSE tables in the literature.
#
# Output is JSON (one object) so several runs can be diffed into a comparison
# table. Runtime is NOT comparable across machines — only collect it per host.
#
# Deps: bash, python3 + numpy, pdal, Pillow (all in the Effigies image). The
# `compare` mode additionally needs python3-scipy + the pdal CLI.
#
# NOT yet measured here (documented future work): photometric / reprojection
# error (needs per-image reprojection) and mesh-to-reference distance for OBJ
# meshes (would sample the mesh to points first). See ROADMAP v0.4.0.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"

usage() {
  cat >&2 <<EOF
usage:
  $0 run     <images_dir> [work_dir] [gpu_flag]            # time the Effigies pipeline + stats
  $0 stats   <mesh|cloud> [out.json] [--no-roughness] [--rough-k K] [--rough-sample N]
  $0 compare <output_cloud> <reference_cloud> [out.json] [--no-icp] [--sample N] [--eps E]
  $0 cprmse  <pairs.csv> [out.json]                        # check-point RMSE (world vs model)
EOF
  exit 2
}

# --- Stats: works on any OBJ mesh or LAS/LAZ/PLY point cloud -----------------
# Emits a JSON object on stdout. Reusable across engines, which is the point.
emit_stats() {
  local target="$1"
  ROUGH="${ROUGH:-1}" ROUGH_K="${ROUGH_K:-16}" ROUGH_SAMPLE="${ROUGH_SAMPLE:-50000}" \
  python3 - "$target" <<'PY'
import sys, os, json, subprocess, tempfile

target = sys.argv[1]
ext = os.path.splitext(target)[1].lower()
out = {"path": os.path.abspath(target), "type": None}

# --- Surface roughness: local plane-fit residual (CloudCompare-style) --------
# For each (sampled) point, fit a plane to its k nearest neighbours via PCA and
# take the point's distance to that plane. The residual distribution is the
# surface-noise / detail signal behind H2 (detail) and H5 (flat-region noise).
# The neighbour search needs scipy; if it is missing we report a skip rather
# than failing the whole stats call.
def roughness_xyz(xyz):
    import numpy as np
    if os.environ.get("ROUGH", "1") != "1":
        return None
    try:
        from scipy.spatial import cKDTree
    except ImportError:
        return {"skipped": "needs scipy (present in the Effigies image)"}
    xyz = np.asarray(xyz, dtype=np.float64)
    n = len(xyz)
    k = max(3, int(os.environ.get("ROUGH_K", "16")))
    if n < k + 1:
        return {"error": f"too few points ({n}) for k={k} plane fit"}
    # Build the tree on ALL points (so neighbours are real), but only evaluate
    # the residual on a deterministic sample of query points for tractability.
    tree = cKDTree(xyz)
    sample = int(os.environ.get("ROUGH_SAMPLE", "50000"))
    if 0 < sample < n:
        step = n // sample
        qidx = np.arange(0, n, step)
    else:
        qidx = np.arange(n)
    q = xyz[qidx]
    # k+1 because the nearest neighbour of a point is itself.
    _, nn = tree.query(q, k=k + 1)
    res = np.empty(len(q), dtype=np.float64)
    for i in range(len(q)):
        nb = xyz[nn[i]]
        c = nb.mean(axis=0)
        # smallest-eigenvector of the neighbourhood covariance = plane normal
        _, _, vt = np.linalg.svd(nb - c, full_matrices=False)
        normal = vt[-1]
        res[i] = abs(np.dot(q[i] - c, normal))
    return {
        "unit": "data units (metres if georeferenced)",
        "k": k, "evaluated_points": int(len(q)), "total_points": int(n),
        "mean": float(res.mean()), "std": float(res.std()),
        "rms": float(np.sqrt(np.mean(res ** 2))),
        "p95": float(np.percentile(res, 95)), "max": float(res.max()),
    }

def _load_cloud_xyz(path):
    """Decimate a LAS/LAZ/PLY to ~ROUGH_SAMPLE points and load XYZ for roughness."""
    import numpy as np
    j = json.loads(subprocess.check_output(["pdal", "info", "--summary", path], text=True))
    s = j.get("summary", j)
    c = int(s.get("num_points") or s.get("count") or 0)
    target_n = int(os.environ.get("ROUGH_SAMPLE", "50000"))
    step = max(1, c // max(1, target_n)) if c else 1
    txt = tempfile.mktemp(suffix=".csv"); pj = tempfile.mktemp(suffix=".json")
    pipe = {"pipeline": [path,
            {"type": "filters.decimation", "step": step},
            {"type": "writers.text", "filename": txt, "format": "csv",
             "order": "X,Y,Z", "keep_unspecified": False, "write_header": False}]}
    open(pj, "w").write(json.dumps(pipe))
    try:
        subprocess.check_call(["pdal", "pipeline", pj],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        arr = np.loadtxt(txt, delimiter=",")
        if arr.ndim == 1:
            arr = arr.reshape(1, -1)
        return arr[:, :3]
    finally:
        for f in (txt, pj):
            try: os.remove(f)
            except OSError: pass

def mesh_obj(path):
    import numpy as np
    vs = []
    faces = []
    with open(path, "r", errors="ignore") as f:
        for line in f:
            if line.startswith("v "):
                p = line.split()
                vs.append((float(p[1]), float(p[2]), float(p[3])))
            elif line.startswith("f "):
                # face indices may be v, v/vt, v/vt/vn — take the vertex index
                idx = [int(tok.split("/")[0]) for tok in line.split()[1:]]
                faces.append(idx)
    V = np.asarray(vs, dtype=np.float64)
    # Fan-triangulate polygons; OBJ indices are 1-based (and may be negative).
    n = len(V)
    tris = []
    for fa in faces:
        fa = [(i - 1) if i > 0 else (n + i) for i in fa]
        for k in range(1, len(fa) - 1):
            tris.append((fa[0], fa[k], fa[k + 1]))
    T = np.asarray(tris, dtype=np.int64)
    area = 0.0
    if len(T):
        a = V[T[:, 0]]; b = V[T[:, 1]]; c = V[T[:, 2]]
        area = float(0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1).sum())
    bbox = (V.max(0) - V.min(0)).tolist() if n else [0, 0, 0]
    return {
        "type": "mesh",
        "vertices": n,
        "faces": int(len(T)),
        "surface_area": area,
        "bbox_dims": bbox,
        # density independent of bbox: triangles per unit area
        "faces_per_area": (len(T) / area) if area else None,
        # surface-noise / detail signal on the mesh vertices (H2/H5)
        "roughness": (roughness_xyz(V) if n else None),
    }

def texture_obj(path):
    """If the OBJ references material maps, sum their megapixels."""
    try:
        from PIL import Image
    except Exception:
        return None
    base = os.path.dirname(path)
    maps, mp = 0, 0.0
    # collect map_Kd files referenced by sibling .mtl(s)
    for mtl in [f for f in os.listdir(base or ".") if f.endswith(".mtl")]:
        for line in open(os.path.join(base, mtl), errors="ignore"):
            if line.strip().lower().startswith("map_kd"):
                img = os.path.join(base, line.split()[-1])
                if os.path.exists(img):
                    try:
                        w, h = Image.open(img).size
                        maps += 1; mp += (w * h) / 1e6
                    except Exception:
                        pass
    return {"texture_maps": maps, "texture_megapixels": round(mp, 1)} if maps else None

def cloud(path):
    # pdal reads las/laz/ply; pull count + bbox from its summary.
    j = json.loads(subprocess.check_output(
        ["pdal", "info", "--summary", path], text=True))
    s = j.get("summary", j)
    n = s.get("num_points") or s.get("count")
    b = s.get("bounds", {})
    dx = (b.get("maxx", 0) - b.get("minx", 0))
    dy = (b.get("maxy", 0) - b.get("miny", 0))
    area_xy = dx * dy
    rough = None
    if n:
        try:
            rough = roughness_xyz(_load_cloud_xyz(path))
        except Exception as e:
            rough = {"error": str(e)}
    return {
        "type": "cloud",
        "points": int(n) if n else None,
        "bbox_dims": [dx, dy, (b.get("maxz", 0) - b.get("minz", 0))],
        "points_per_m2": (int(n) / area_xy) if (n and area_xy) else None,
        # surface-noise / detail signal (H2/H5)
        "roughness": rough,
    }

if ext == ".obj":
    out.update(mesh_obj(target))
    tex = texture_obj(target)
    if tex:
        out.update(tex)
elif ext in (".las", ".laz", ".ply"):
    out.update(cloud(target))
else:
    out = {"path": target, "error": f"unsupported extension '{ext}'"}

out["file_bytes"] = os.path.getsize(target) if os.path.exists(target) else None
print(json.dumps(out, indent=2))
PY
}

# --- Run: time the Effigies pipeline, then stat its outputs ------------------
run_mode() {
  local images="${1:?images_dir required}"
  local work="${2:-$(mktemp -d)/effigies}"
  local gpu="${3:-0}"
  local proj; proj="$(dirname "$work")"
  local name; name="$(basename "$work")"
  local timed; timed="$(mktemp)"

  mkdir -p "$proj/$name/images"
  # run.sh expects <project-path>/<name>/images; mirror the dataset in.
  cp -n "$images"/* "$proj/$name/images/" 2>/dev/null || true

  echo "[bench] running Effigies pipeline (gpu=$gpu) — this can take a while..." >&2
  # Timestamp every line of the engine's output without touching the pipeline.
  ( "$REPO/run.sh" --project-path "$proj" --use-gpu "$([ "$gpu" = 1 ] && echo true || echo false)" "$name" 2>&1 \
      | while IFS= read -r line; do printf '%s %s\n' "$(date +%s.%N)" "$line"; done ) \
      | tee "$timed" >&2 || true

  # Derive per-stage durations from consecutive stage markers, then add stats.
  python3 - "$timed" "$proj/$name" "$images" "$REPO/scripts/benchmark.sh" <<'PY'
import sys, os, re, json, glob, subprocess

timed, work, images, self_path = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
marker = re.compile(r'^(\d+\.\d+)\s+(\[(?:effigies|colmap|openmvs|georef|map)\].*)$')
events = []
for line in open(timed, errors="ignore"):
    m = marker.match(line.rstrip("\n"))
    if m:
        events.append((float(m.group(1)), m.group(2).strip()))

stages = []
for i in range(len(events) - 1):
    t0, label = events[i]
    t1, _ = events[i + 1]
    stages.append({"stage": label, "seconds": round(t1 - t0, 1)})
total = round(events[-1][0] - events[0][0], 1) if len(events) >= 2 else None

def stats_of(path):
    try:
        return json.loads(subprocess.check_output(
            [self_path, "stats", path], text=True))
    except Exception as e:
        return {"path": path, "error": str(e)}

# locate the canonical WebODM outputs
obj = glob.glob(os.path.join(work, "odm_texturing", "*_geo.obj")) or \
      glob.glob(os.path.join(work, "odm_texturing", "*.obj"))
laz = glob.glob(os.path.join(work, "odm_georeferencing", "*.laz")) or \
      glob.glob(os.path.join(work, "odm_georeferencing", "*.las"))

report = {
    "engine": "effigies",
    "images": len(glob.glob(os.path.join(images, "*"))),
    "total_seconds": total,
    "stages": stages,
    "mesh": stats_of(obj[0]) if obj else None,
    "cloud": stats_of(laz[0]) if laz else None,
}
print(json.dumps(report, indent=2))
PY
  rm -f "$timed"
}

# --- Compare: cloud-to-reference distance (ICP + nearest-neighbour) ----------
# Engine-agnostic: point it at any LAS/LAZ/PLY output and a reference cloud.
compare_mode() {
  local out="${1:?output cloud required}" ref="${2:?reference cloud required}"
  shift 2
  local outjson="" icp=1 sample=1000000 eps=0.01
  while [ $# -gt 0 ]; do
    case "$1" in
      --no-icp)  icp=0; shift ;;
      --sample)  sample="$2"; shift 2 ;;
      --eps)     eps="$2"; shift 2 ;;
      *)         outjson="$1"; shift ;;
    esac
  done
  local json
  json="$(ICP="$icp" SAMPLE="$sample" EPS="$eps" python3 - "$out" "$ref" <<'PY'
import sys, os, json, subprocess, tempfile
import numpy as np
try:
    from scipy.spatial import cKDTree
except ImportError:
    print(json.dumps({"error": "compare needs scipy (apt install python3-scipy; "
                               "present in the Effigies image)"})); sys.exit(1)

out_path, ref_path = sys.argv[1], sys.argv[2]
do_icp = os.environ.get("ICP", "1") == "1"
sample = int(os.environ.get("SAMPLE", "1000000"))
eps    = float(os.environ.get("EPS", "0.01"))
tmp = []

def count(p):
    j = json.loads(subprocess.check_output(["pdal", "info", "--summary", p], text=True))
    s = j.get("summary", j)
    return int(s.get("num_points") or s.get("count") or 0)

def load_xyz(path, n_target):
    """Decimate to ~n_target points (reported) and load XYZ into numpy."""
    c = count(path)
    step = max(1, c // max(1, n_target))
    txt = tempfile.mktemp(suffix=".csv"); pj = tempfile.mktemp(suffix=".json")
    tmp.extend([txt, pj])
    pipe = {"pipeline": [path,
            {"type": "filters.decimation", "step": step},
            {"type": "writers.text", "filename": txt, "format": "csv",
             "order": "X,Y,Z", "keep_unspecified": False, "write_header": False}]}
    open(pj, "w").write(json.dumps(pipe))
    subprocess.check_call(["pdal", "pipeline", pj],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    arr = np.loadtxt(txt, delimiter=",")
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    return arr[:, :3], c, step

icp_meta = None
aligned = out_path
if do_icp:
    al = tempfile.mktemp(suffix=".las"); pj = tempfile.mktemp(suffix=".json")
    mj = tempfile.mktemp(suffix=".json"); tmp.extend([al, pj, mj])
    # filters.icp registers the moving (2nd) cloud onto the fixed (1st) cloud.
    pipe = {"pipeline": [ref_path, out_path,
            {"type": "filters.icp"},
            {"type": "writers.las", "filename": al}]}
    open(pj, "w").write(json.dumps(pipe))
    try:
        subprocess.check_call(["pdal", "pipeline", pj, "--metadata", mj],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        aligned = al
        try:
            m = json.loads(open(mj).read())
            def find_icp(o):
                if isinstance(o, dict):
                    if "converged" in o or "fitness" in o:
                        return {"converged": o.get("converged"), "fitness": o.get("fitness")}
                    for v in o.values():
                        r = find_icp(v)
                        if r: return r
                elif isinstance(o, list):
                    for v in o:
                        r = find_icp(v)
                        if r: return r
                return None
            icp_meta = find_icp(m)
        except Exception:
            icp_meta = {"converged": None}
    except subprocess.CalledProcessError:
        icp_meta = {"error": "icp failed; using raw (unaligned) distance"}
        aligned = out_path

out_xyz, out_n, out_step = load_xyz(aligned, sample)
ref_xyz, ref_n, ref_step = load_xyz(ref_path, sample)

# nearest-neighbour distance: each output point to the reference cloud
d, _ = cKDTree(ref_xyz).query(out_xyz, k=1)
# completeness: fraction of reference points within eps of an output point
dr, _ = cKDTree(out_xyz).query(ref_xyz, k=1)

rms = float(np.sqrt(np.mean(d ** 2)))
report = {
    "type": "cloud-to-reference",
    "output": os.path.abspath(out_path),
    "reference": os.path.abspath(ref_path),
    "icp": (icp_meta if do_icp else "skipped"),
    "sampled": {"output_points": int(len(out_xyz)), "output_total": out_n,
                "output_step": out_step, "reference_points": int(len(ref_xyz)),
                "reference_total": ref_n, "reference_step": ref_step,
                "note": "clouds decimated for tractability; stats are on the sample"},
    "distance": {"unit": "data units (metres if georeferenced)",
                 "mean": float(d.mean()), "std": float(d.std()), "rms": rms,
                 "p95": float(np.percentile(d, 95)), "max": float(d.max())},
    "completeness": {"eps": eps, "fraction": float((dr <= eps).mean())},
}
for f in tmp:
    try: os.remove(f)
    except OSError: pass
print(json.dumps(report, indent=2))
PY
)"
  if [ -n "$outjson" ]; then printf '%s\n' "$json" | tee "$outjson"; else printf '%s\n' "$json"; fi
}

# --- Check-point RMSE: surveyed control points vs. modelled position ---------
cprmse_mode() {
  local pairs="${1:?pairs.csv required}" outjson="${2:-}"
  local json
  json="$(python3 - "$pairs" <<'PY'
import sys, json
import numpy as np

rows = []
for line in open(sys.argv[1], encoding="utf-8"):
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    nums = []
    for tok in line.replace(";", ",").split(","):
        try:
            nums.append(float(tok))
        except ValueError:
            pass  # ignore id / label columns
    if len(nums) >= 6:
        rows.append(nums[-6:])  # world_x,y,z, model_x,y,z (last 6 numeric)

if not rows:
    print(json.dumps({"error": "no rows with 6 numeric coords "
                               "(world_x,world_y,world_z,model_x,model_y,model_z)"}))
    sys.exit(1)

A = np.asarray(rows)
d = A[:, 0:3] - A[:, 3:6]
rmse = lambda v: float(np.sqrt(np.mean(v ** 2)))
print(json.dumps({
    "type": "cp-rmse",
    "n_points": int(len(A)),
    "rmse": {"unit": "data units (metres if georeferenced)",
             "x": rmse(d[:, 0]), "y": rmse(d[:, 1]), "z": rmse(d[:, 2]),
             "xyz": float(np.sqrt(np.mean(np.sum(d ** 2, axis=1))))},
}, indent=2))
PY
)"
  if [ -n "$outjson" ]; then printf '%s\n' "$json" | tee "$outjson"; else printf '%s\n' "$json"; fi
}

case "${1:-}" in
  run)     shift; run_mode "$@" ;;
  stats)   shift; [ $# -ge 1 ] || usage
           target="$1"; shift; outjson=""
           while [ $# -gt 0 ]; do case "$1" in
             --no-roughness) export ROUGH=0; shift ;;
             --rough-k)      export ROUGH_K="$2"; shift 2 ;;
             --rough-sample) export ROUGH_SAMPLE="$2"; shift 2 ;;
             *)              outjson="$1"; shift ;;
           esac; done
           if [ -n "$outjson" ]; then emit_stats "$target" | tee "$outjson"; else emit_stats "$target"; fi ;;
  compare) shift; [ $# -ge 2 ] || usage; compare_mode "$@" ;;
  cprmse)  shift; [ $# -ge 1 ] || usage; cprmse_mode "$@" ;;
  *)       usage ;;
esac
