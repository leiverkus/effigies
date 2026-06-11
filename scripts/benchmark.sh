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
#       Compute comparable quality metrics on ANY engine's output. Point it at a
#       Metashape / RealityCapture export to get numbers on the same scale.
#
# Output is JSON (one object) so several runs can be diffed into a comparison
# table. Runtime is NOT comparable across machines — only collect it per host.
#
# Deps (all present in the Effigies image): bash, python3 + numpy, pdal, Pillow.
#
# NOT yet measured here (documented future work, heavier — needs per-image
# reprojection against the input photos): photometric / reprojection error, and
# cloud-to-cloud distance vs. a reference. See ROADMAP v0.4.0.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"

usage() {
  cat >&2 <<EOF
usage:
  $0 run   <images_dir> [work_dir] [gpu_flag]   # time the Effigies pipeline + stats
  $0 stats <mesh|cloud> [out.json]              # metrics on any engine's output
EOF
  exit 2
}

# --- Stats: works on any OBJ mesh or LAS/LAZ/PLY point cloud -----------------
# Emits a JSON object on stdout. Reusable across engines, which is the point.
emit_stats() {
  local target="$1"
  python3 - "$target" <<'PY'
import sys, os, json, subprocess

target = sys.argv[1]
ext = os.path.splitext(target)[1].lower()
out = {"path": os.path.abspath(target), "type": None}

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
    return {
        "type": "cloud",
        "points": int(n) if n else None,
        "bbox_dims": [dx, dy, (b.get("maxz", 0) - b.get("minz", 0))],
        "points_per_m2": (int(n) / area_xy) if (n and area_xy) else None,
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

case "${1:-}" in
  run)   shift; run_mode "$@" ;;
  stats) shift; [ $# -ge 1 ] || usage
         if [ "${2:-}" ]; then emit_stats "$1" | tee "$2"; else emit_stats "$1"; fi ;;
  *)     usage ;;
esac
