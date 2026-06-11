#!/usr/bin/env bash
#
# Effigies entry point.
# Invoked by the runner as:  run.sh --<opt> <val> ... <projectName>
# Mirrors ODM's run.sh contract so NodeODM-compatible nodes can call it unchanged.
#
set -euo pipefail

# ---------------------------------------------------------------------------
# 1. Parse NodeODM-style arguments
# ---------------------------------------------------------------------------
# Defaults mirror options.json
declare -A OPT=(
  [sparse-engine]=colmap
  [matcher]=exhaustive
  [mapper]=incremental
  [camera-model]=OPENCV
  [densify-resolution-level]=1
  [number-views-fuse]=3
  [reconstruct-mesh]=true
  [refine-mesh-iters]=3
  [mesh-decimate]=1.0
  [texture-resolution]=8192
  [crs]=auto
  [georeference]=auto
  [gcp]=""
  [orthophoto]=true
  [orthophoto-resolution]=auto
  [use-gpu]=true
  [project-path]=""
)

PROJECT_NAME=""
while [[ $# -gt 0 ]]; do
  if [[ "$1" == --* ]]; then
    key="${1#--}"
    # boolean flags (no following value) vs. valued options
    if [[ $# -ge 2 && "$2" != --* ]]; then
      OPT[$key]="$2"; shift 2
    else
      OPT[$key]=true; shift 1
    fi
  else
    PROJECT_NAME="$1"; shift 1
  fi
done

PROJ="${OPT[project-path]}/${PROJECT_NAME}"
IMAGES="${PROJ}/images"
WORK="${PROJ}/effigies"
mkdir -p "$WORK"

echo "[effigies] project: $PROJ"
echo "[effigies] sparse-engine=${OPT[sparse-engine]} matcher=${OPT[matcher]} mapper=${OPT[mapper]} refine-iters=${OPT[refine-mesh-iters]} crs=${OPT[crs]}"

# Resolve GPU usage. Honour --use-gpu, but fall back to CPU when no usable CUDA
# GPU is present: COLMAP's SIFT aborts hard ("Cannot use Sift GPU without CUDA or
# OpenGL support") rather than degrading, which would surface to WebODM only as
# the opaque "Cannot process dataset". A documented CPU fallback beats a cryptic
# failure (the CPU image has no CUDA at all, and a GPU image may run without one).
GPU_FLAG=0
if [[ "${OPT[use-gpu]}" == "true" ]]; then
  if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
    GPU_FLAG=1
  else
    echo "[effigies] WARN: --use-gpu requested but no usable CUDA GPU detected; falling back to CPU" >&2
  fi
fi

# ---------------------------------------------------------------------------
# 2. Sparse reconstruction  ->  produces $WORK/sparse  (+ scene.mvs)
# ---------------------------------------------------------------------------
if [[ "${OPT[sparse-engine]}" == "colmap" ]]; then
  bash "$(dirname "$0")/pipeline/sparse_colmap.sh" \
       "$IMAGES" "$WORK" "${OPT[matcher]}" "${OPT[camera-model]}" "$GPU_FLAG" "${OPT[mapper]}"
  # COLMAP -> OpenMVS scene. InterfaceCOLMAP reads the undistorted dense
  # workspace (dense/sparse model + dense/images), produced by image_undistorter
  # in sparse_colmap.sh — not the raw sparse/0 model. --image-folder is given the
  # absolute undistorted-images path; InterfaceCOLMAP records it relative to the
  # working folder ($WORK) as "dense/images/...", which the OpenMVS dense stage
  # (also run with -w $WORK) then resolves correctly. Without this it defaults to
  # "images/" and DensifyPointCloud fails to find the images under $WORK/images.
  InterfaceCOLMAP -i "$WORK/dense" --image-folder "$WORK/dense/images/" \
                  -o "$WORK/scene.mvs" -w "$WORK"
else
  bash "$(dirname "$0")/pipeline/sparse_opensfm.sh" "$IMAGES" "$WORK"
  InterfaceOpenSfM -i "$WORK/opensfm" -o "$WORK/scene.mvs" -w "$WORK"
fi

# ---------------------------------------------------------------------------
# 3. OpenMVS dense + the steps ODM skips (ReconstructMesh / RefineMesh)
# ---------------------------------------------------------------------------
bash "$(dirname "$0")/pipeline/dense_openmvs.sh" \
     "$WORK" \
     "${OPT[densify-resolution-level]}" \
     "${OPT[number-views-fuse]}" \
     "${OPT[reconstruct-mesh]}" \
     "${OPT[refine-mesh-iters]}" \
     "${OPT[mesh-decimate]}" \
     "${OPT[texture-resolution]}" \
     "$GPU_FLAG"

# ---------------------------------------------------------------------------
# 4. Georeferencing bridge  (local SfM frame -> projected CRS)
#    This is what ODM does internally and COLMAP does NOT.
# ---------------------------------------------------------------------------
# ODM convention: a gcp_list.txt in the project root is used automatically.
if [[ -z "${OPT[gcp]}" && -f "${PROJ}/gcp_list.txt" ]]; then
  OPT[gcp]="${PROJ}/gcp_list.txt"
  echo "[effigies] auto-detected GCP file: ${OPT[gcp]}"
fi

python3 "$(dirname "$0")/helpers/georef_bridge.py" \
     --work "$WORK" \
     --images "$IMAGES" \
     --sparse-engine "${OPT[sparse-engine]}" \
     --georeference "${OPT[georeference]}" \
     --crs "${OPT[crs]}" \
     --gcp "${OPT[gcp]}"

# ---------------------------------------------------------------------------
# 5. Point cloud -> georeferenced LAZ (+ EPT for the Potree viewer)
#    Applies the georef transform to scene_dense.ply and writes LAZ via PDAL.
# ---------------------------------------------------------------------------
if ! python3 "$(dirname "$0")/helpers/pointcloud_to_laz.py" --work "$WORK" --ept; then
  echo "[effigies] WARN: LAZ/EPT step failed; map_outputs will fall back to the raw PLY" >&2
fi

# ---------------------------------------------------------------------------
# 5b. Orthophoto -> georeferenced GeoTIFF (nadir-rasterised from the textured mesh)
#     ODM builds this from its DSM; we build a true orthophoto off the refined
#     textured mesh so it inherits the RefineMesh detail. Self-skips when the
#     result is not georeferenced (crs=local). Non-fatal: a failure here must not
#     lose the 3D model + cloud that already succeeded.
# ---------------------------------------------------------------------------
if [[ "${OPT[orthophoto]}" == "true" ]]; then
  if ! python3 "$(dirname "$0")/helpers/orthophoto.py" \
       --work "$WORK" --resolution "${OPT[orthophoto-resolution]}"; then
    echo "[effigies] WARN: orthophoto step failed; continuing without it" >&2
  fi
fi

# ---------------------------------------------------------------------------
# 6. Map outputs onto the WebODM asset contract
# ---------------------------------------------------------------------------
python3 "$(dirname "$0")/helpers/map_outputs.py" --proj "$PROJ" --work "$WORK"

echo "[effigies] done."
