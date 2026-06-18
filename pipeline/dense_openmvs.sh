#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Patrick Leiverkus
# SPDX-License-Identifier: AGPL-3.0-or-later
# OpenMVS dense reconstruction + the steps ODM skips.
# args: WORK RES_LEVEL VIEWS_FUSE RECONSTRUCT_MESH REFINE_ITERS DECIMATE TEX_RES GPU_FLAG
#       [MAX_FACE_AREA] [GRADIENT_STEP] [SEAM_LEVELING] [HARMONIZE] [SEAM_SMOOTH]
#       [VIEW_BLEND]
set -euo pipefail
WORK="$1"; RES_LEVEL="$2"; VIEWS_FUSE="$3"; RECONSTRUCT_MESH="$4"
REFINE_ITERS="$5"; DECIMATE="$6"; TEX_RES="$7"; GPU="$8"
MAX_FACE_AREA="${9:-16}"; GRADIENT_STEP="${10:-25.05}"; SEAM_LEVELING="${11:-false}"
HARMONIZE="${12:-true}"; SEAM_SMOOTH="${13:-true}"; VIEW_BLEND="${14:-true}"
FREE_SPACE="${15:-false}"; CLOSE_HOLES="${16:-30}"
HELPERS="$(cd "$(dirname "$0")/../helpers" && pwd)"

cd "$WORK"
source "$(dirname "$0")/progress.sh"   # WebODM progress bar (no-op outside NodeODM)

# CUDA device selection (-1 = first GPU, -2 = CPU only). The --cuda-device option
# only exists in a CUDA-enabled OpenMVS build; the CPU image is built with
# OpenMVS_USE_CUDA=OFF and rejects it ("unrecognised option '--cuda-device'").
# Probe the binary so we pass the flag only where it is understood: on a CUDA
# build honour GPU (-1) vs. forced-CPU fallback (-2); on a CPU build omit it.
CUDA_ARGS=()
if DensifyPointCloud --help 2>&1 | grep -q -- '--cuda-device'; then
  if [[ "$GPU" == "1" ]]; then CUDA_ARGS=(--cuda-device -1); else CUDA_ARGS=(--cuda-device -2); fi
fi

# Optional cap on OpenMVS worker threads (EFFIGIES_DENSE_THREADS, 0 = all cores =
# OpenMVS default). Each densify/refine thread holds image pyramids + working
# buffers, so on many-core, RAM-constrained hosts the per-thread peak can OOM
# (the same reason COLMAP's CPU SIFT is capped via cpu-threads). NOTE: this bounds
# the Densify/Refine peak only — it does NOT reduce the ReconstructMesh Delaunay
# tetrahedralization (strictly in-core); use --tiles or a higher
# densify-resolution-level for that wall. 0 -> flag omitted -> unchanged.
THREADS_ARG=()
DENSE_THREADS="${EFFIGIES_DENSE_THREADS:-0}"
[[ "$DENSE_THREADS" != "0" ]] && THREADS_ARG=(--max-threads "$DENSE_THREADS")

echo "[openmvs] DensifyPointCloud"
DensifyPointCloud scene.mvs \
  --resolution-level "$RES_LEVEL" \
  --number-views-fuse "$VIEWS_FUSE" \
  --archive-type 3 \
  "${CUDA_ARGS[@]}" "${THREADS_ARG[@]}" \
  -w "$WORK"

progress 62
MESH_INPUT="scene_dense.mvs"

if [[ "$RECONSTRUCT_MESH" == "true" ]]; then
  echo "[openmvs] ReconstructMesh  (ODM skips this)"
  FSS=0; [[ "$FREE_SPACE" == "true" ]] && FSS=1
  ReconstructMesh "$MESH_INPUT" \
    --decimate "$DECIMATE" \
    --free-space-support "$FSS" \
    --close-holes "$CLOSE_HOLES" \
    --archive-type 3 \
    "${CUDA_ARGS[@]}" "${THREADS_ARG[@]}" \
    -w "$WORK"
  MESH_MVS="scene_dense_mesh.mvs"
  progress 68

  if [[ "${REFINE_ITERS}" != "0" ]]; then
    # OpenMVS 2.4 has no --max-iters; its "iterations" lever IS --scales ("how many
    # iterations to run mesh optimization on multi-scale images"). Previously this
    # was hardcoded to 1 and refine-mesh-iters only gated the stage — now it drives it.
    echo "[openmvs] RefineMesh (scales=${REFINE_ITERS})  (ODM skips this - main quality lever)"
    RefineMesh "$MESH_MVS" \
      --max-face-area "$MAX_FACE_AREA" \
      --scales "$REFINE_ITERS" \
      --gradient-step "$GRADIENT_STEP" \
      --resolution-level "$RES_LEVEL" \
      "${CUDA_ARGS[@]}" "${THREADS_ARG[@]}" \
      -w "$WORK"
    MESH_MVS="scene_dense_mesh_refine.mvs"
    progress 74
  fi

  echo "[openmvs] TextureMesh"
  # Texture at FULL image resolution: RefineMesh (run with a resolution-level)
  # saves its scene with downscaled image references, so texturing the refine
  # output .mvs would sample half-res images. Instead texture the full-res
  # pre-refine scene and inject the refined geometry via --mesh-file; -o keeps
  # the canonical scene_dense_mesh_refine_texture.* names.
  #
  # Seam leveling DEFAULTS OFF: OpenMVS 2.4.0's global+local seam leveling
  # corrupts texture patches on this (arm64/CPU) build — patch interiors clamp
  # to black, borders to saturated colors — which wrecks both the model texture
  # and the orthophoto. Re-enable via --texture-seam-leveling once a build
  # without the defect is validated.
  # Equalise per-image exposure BEFORE assembling the atlas (the harmonisation
  # OpenMVS's broken seam leveling was supposed to provide): per-image RGB gains
  # from the sparse-point observations, applied to the undistorted images.
  if [[ "$HARMONIZE" == "true" ]]; then
    if ! python3 "$HELPERS/harmonize_exposure.py" --work "$WORK"; then
      echo "[openmvs] WARN: exposure harmonisation failed; texturing unadjusted images" >&2
    fi
  fi
  SEAM=0; [[ "$SEAM_LEVELING" == "true" ]] && SEAM=1
  TEX_IN="$MESH_MVS"; TEX_ARGS=()
  if [[ "$MESH_MVS" == "scene_dense_mesh_refine.mvs" ]]; then
    TEX_IN="scene_dense_mesh.mvs"
    TEX_ARGS=(--mesh-file scene_dense_mesh_refine.ply -o scene_dense_mesh_refine_texture.obj)
  fi
  TextureMesh "$TEX_IN" \
    "${TEX_ARGS[@]}" \
    --export-type obj \
    --texture-size "$TEX_RES" \
    --global-seam-leveling "$SEAM" \
    --local-seam-leveling "$SEAM" \
    --archive-type 3 \
    "${CUDA_ARGS[@]}" "${THREADS_ARG[@]}" \
    -w "$WORK"
  # Multi-view blended texturing (Metashape-class): keep TextureMesh's atlas
  # layout, re-bake every texel as a weighted blend of its best views (angle/
  # distance weights, depth-tested) — removes the per-view exposure/sharpness
  # blotches a single-view texture shows on homogeneous surfaces. Non-fatal.
  if [[ "$VIEW_BLEND" == "true" ]]; then
    if ! python3 "$HELPERS/texture_blend.py" --work "$WORK"; then
      echo "[openmvs] WARN: multi-view blend failed; keeping single-view texture" >&2
    fi
  fi
  # Our own seam leveling (OpenMVS's is corrupted): equalise colours across
  # texture-patch seams and diffuse the adjustment into the patch interiors —
  # measured to halve the median seam colour difference. Non-fatal.
  if [[ "$SEAM_SMOOTH" == "true" ]]; then
    if ! python3 "$HELPERS/seam_level.py" --work "$WORK"; then
      echo "[openmvs] WARN: seam leveling failed; keeping unlevelled texture" >&2
    fi
  fi
else
  echo "[openmvs] reconstruct-mesh disabled; leaving dense point cloud only"
fi

progress 78
echo "[openmvs] dense stage complete"
