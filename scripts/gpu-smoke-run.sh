#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Patrick Leiverkus
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# End-to-end GPU smoke run: drive run.sh directly in the CUDA image on a small
# image set, while sampling the GPU from the HOST, then assert the run really
# used the GPU.
#
# The trap this guards against: run.sh:176 falls back to CPU with a warning when
# its probe fails, and the run then SUCCEEDS. A green run is therefore not
# evidence of a working GPU path. This script requires positive evidence —
# engine processes resident on the device — and fails if it only finds fallback.
#
# NodeODM is not involved (no REST, no WebODM, no exposed port): the engine is
# invoked through its documented contract, run.sh --<opt> <val> ... <name>.
#
# Usage: ./scripts/gpu-smoke-run.sh <image-dir> [image-tag]
#
set -euo pipefail

SRC="${1:?usage: gpu-smoke-run.sh <image-dir> [image-tag]}"
IMG="${2:-effigies:gpu}"
NAME="gpu-smoke"

[[ -d "$SRC" ]] || { echo "not a directory: $SRC" >&2; exit 1; }
command -v nvidia-smi >/dev/null 2>&1 || { echo "nvidia-smi missing on the host" >&2; exit 1; }

# Validate the sampler's query fields UP FRONT. With no compute apps running the
# query returns empty but exits 0, so this checks the field names without needing
# a load. Without this check a wrong field name would make the sampler silently
# collect nothing and the run would report a false FAIL.
nvidia-smi --query-compute-apps=process_name,used_gpu_memory --format=csv,noheader >/dev/null 2>&1 \
  || { echo "nvidia-smi does not accept the compute-apps query fields used here" >&2; exit 1; }

N_IMG=$(find "$SRC" -maxdepth 1 -type f \
          \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' -o -iname '*.tif' \) | wc -l | tr -d ' ')
[[ "$N_IMG" -ge 5 ]] || { echo "found only $N_IMG images in $SRC (need >= 5)" >&2; exit 1; }

# --- Stage the ODM project layout: <root>/<name>/images/ ---------------------
ROOT="$(mktemp -d "${TMPDIR:-/tmp}/effigies-smoke.XXXXXX")"

# The engine runs as root inside the container, so every output it writes into the
# bind mount is root-owned and a plain `rm -rf` as the invoking user fails with
# "Permission denied" — leaving ~700 MB of results behind on each run. Delete the
# tree from inside a throwaway container (as root) first, then remove the husk.
cleanup() {
  [ -n "${ROOT:-}" ] || return 0
  if ! rm -rf "$ROOT" 2>/dev/null; then
    docker run --rm -v "$ROOT:/wipe" --entrypoint sh "$IMG" \
      -c 'rm -rf /wipe/* /wipe/.[!.]* 2>/dev/null || true' >/dev/null 2>&1 || true
    rm -rf "$ROOT" 2>/dev/null || echo "NOTE: could not remove $ROOT — remove it manually" >&2
  fi
}
trap cleanup EXIT
mkdir -p "$ROOT/$NAME/images"
find "$SRC" -maxdepth 1 -type f \
     \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' -o -iname '*.tif' \) \
     -exec cp {} "$ROOT/$NAME/images/" \;

LOG="$ROOT/run.log"
SAMPLES="$ROOT/gpu-samples.log"

echo "== GPU smoke run =="
echo "  image tag : $IMG"
echo "  images    : $N_IMG (from $SRC)"
echo "  workdir   : $ROOT"
nvidia-smi --query-gpu=name,memory.total,compute_cap --format=csv,noheader | sed 's/^/  gpu       : /'

# --- Sample the device from the HOST ----------------------------------------
# Queried on the host, not in the container: inside a PID namespace the driver
# cannot resolve process names for compute apps, so an in-container query would
# report blanks and prove nothing.
sample_gpu() {
  while true; do
    nvidia-smi --query-compute-apps=process_name,used_gpu_memory \
               --format=csv,noheader 2>/dev/null >> "$SAMPLES" || true
    nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader 2>/dev/null \
      | sed 's/^/util /' >> "$SAMPLES" || true
    sleep 5
  done
}
sample_gpu & SAMPLER=$!
trap 'kill "$SAMPLER" 2>/dev/null || true; cleanup' EXIT

# --- Run the engine ---------------------------------------------------------
# Deliberately cheap settings, following DEPLOYMENT.md's "good first run", plus
# --georeference none (a non-negotiable contract per CLAUDE.md) so the run does
# not depend on EXIF GPS or a GCP file being present.
set +e
docker run --rm --gpus all --init \
  --entrypoint bash \
  -v "$ROOT:/data" \
  "$IMG" -lc "/opt/effigies/run.sh \
      --project-path /data \
      --sparse-engine colmap \
      --refine-mesh-iters 1 \
      --densify-resolution-level 2 \
      --texture-resolution 4096 \
      --georeference none \
      $NAME" 2>&1 | tee "$LOG"
RC=${PIPESTATUS[0]}
set -e
kill "$SAMPLER" 2>/dev/null || true

# --- Assertions -------------------------------------------------------------
echo
echo "== assertions =="
fail=0
ok()  { echo "  ok    $*"; }
bad() { echo "  FAIL  $*"; fail=$((fail+1)); }

if [[ $RC -eq 0 ]]; then ok "run.sh exited 0"; else bad "run.sh exited $RC"; fi

# The decisive one: the CPU-fallback warning must be absent.
if grep -q "no usable CUDA GPU detected" "$LOG"; then
  bad "engine fell back to CPU (run.sh:176 warning present) — this is NOT a GPU result"
else
  ok "no CPU-fallback warning — the engine took the GPU path"
fi

# Positive evidence: engine processes were resident on the device.
SEEN=$(grep -oE 'DensifyPointCloud|RefineMesh|ReconstructMesh|TextureMesh|colmap' "$SAMPLES" 2>/dev/null \
       | sort -u | paste -sd, -)
if [[ -n "$SEEN" ]]; then
  ok "engine processes observed on the device: $SEEN"
else
  bad "no engine process ever observed on the device (sampled every 5 s)"
fi

MAXUTIL=$(grep '^util' "$SAMPLES" 2>/dev/null | grep -oE '[0-9]+' | sort -n | tail -1)
if [[ -n "${MAXUTIL:-}" && "$MAXUTIL" -gt 0 ]]; then
  ok "peak GPU utilisation ${MAXUTIL} %"
else
  bad "GPU utilisation never rose above 0 %"
fi

# The output that justifies this node existing at all. map_outputs.py writes this
# exact name unconditionally (a WebODM legacy convention), also for
# --georeference none, so one path is the whole assertion.
OBJ="odm_texturing/odm_textured_model_geo.obj"
if [[ -s "$ROOT/$NAME/$OBJ" ]]; then
  ok "produced $OBJ ($(du -h "$ROOT/$NAME/$OBJ" | cut -f1))"
else
  bad "no (or empty) $OBJ — the textured mesh is the point of this node"
fi

# Keep the log and samples out of the temp dir before the trap wipes it.
OUT="./gpu-smoke-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$OUT" && cp "$LOG" "$SAMPLES" "$OUT/" 2>/dev/null || true
echo
echo "  log + GPU samples kept in: $OUT"

echo
if [[ $fail -eq 0 ]]; then
  echo "PASS — Effigies runs on the GPU end to end."
else
  echo "FAIL — $fail assertion(s) failed; see $OUT/run.log"
  exit 1
fi
