#!/usr/bin/env bash
# OpenMVS dense reconstruction + the steps ODM skips.
# args: WORK RES_LEVEL VIEWS_FUSE RECONSTRUCT_MESH REFINE_ITERS DECIMATE TEX_RES GPU_FLAG
set -euo pipefail
WORK="$1"; RES_LEVEL="$2"; VIEWS_FUSE="$3"; RECONSTRUCT_MESH="$4"
REFINE_ITERS="$5"; DECIMATE="$6"; TEX_RES="$7"; GPU="$8"

cd "$WORK"

# CUDA device selection (-1 = first GPU, -2 = CPU only). The --cuda-device option
# only exists in a CUDA-enabled OpenMVS build; the CPU image is built with
# OpenMVS_USE_CUDA=OFF and rejects it ("unrecognised option '--cuda-device'").
# Probe the binary so we pass the flag only where it is understood: on a CUDA
# build honour GPU (-1) vs. forced-CPU fallback (-2); on a CPU build omit it.
CUDA_ARGS=()
if DensifyPointCloud --help 2>&1 | grep -q -- '--cuda-device'; then
  if [[ "$GPU" == "1" ]]; then CUDA_ARGS=(--cuda-device -1); else CUDA_ARGS=(--cuda-device -2); fi
fi

echo "[openmvs] DensifyPointCloud"
DensifyPointCloud scene.mvs \
  --resolution-level "$RES_LEVEL" \
  --number-views-fuse "$VIEWS_FUSE" \
  --archive-type 3 \
  "${CUDA_ARGS[@]}" \
  -w "$WORK"

MESH_INPUT="scene_dense.mvs"

if [[ "$RECONSTRUCT_MESH" == "true" ]]; then
  echo "[openmvs] ReconstructMesh  (ODM skips this)"
  ReconstructMesh "$MESH_INPUT" \
    --decimate "$DECIMATE" \
    --archive-type 3 \
    "${CUDA_ARGS[@]}" \
    -w "$WORK"
  MESH_MVS="scene_dense_mesh.mvs"

  if [[ "${REFINE_ITERS}" != "0" ]]; then
    echo "[openmvs] RefineMesh x${REFINE_ITERS}  (ODM skips this - main quality lever)"
    RefineMesh "$MESH_MVS" \
      --max-face-area 16 \
      --scales 1 \
      --gradient-step 25.05 \
      --resolution-level "$RES_LEVEL" \
      "${CUDA_ARGS[@]}" \
      -w "$WORK"
    MESH_MVS="scene_dense_mesh_refine.mvs"
  fi

  echo "[openmvs] TextureMesh"
  TextureMesh "$MESH_MVS" \
    --export-type obj \
    --texture-size "$TEX_RES" \
    --archive-type 3 \
    "${CUDA_ARGS[@]}" \
    -w "$WORK"
else
  echo "[openmvs] reconstruct-mesh disabled; leaving dense point cloud only"
fi

echo "[openmvs] dense stage complete"
