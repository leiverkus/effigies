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
echo "[effigies] sparse-engine=${OPT[sparse-engine]} matcher=${OPT[matcher]} refine-iters=${OPT[refine-mesh-iters]} crs=${OPT[crs]}"

GPU_FLAG=0
[[ "${OPT[use-gpu]}" == "true" ]] && GPU_FLAG=1

# ---------------------------------------------------------------------------
# 2. Sparse reconstruction  ->  produces $WORK/sparse  (+ scene.mvs)
# ---------------------------------------------------------------------------
if [[ "${OPT[sparse-engine]}" == "colmap" ]]; then
  bash "$(dirname "$0")/pipeline/sparse_colmap.sh" \
       "$IMAGES" "$WORK" "${OPT[matcher]}" "${OPT[camera-model]}" "$GPU_FLAG"
  # COLMAP -> OpenMVS scene
  InterfaceCOLMAP -i "$WORK/sparse/0" -o "$WORK/scene.mvs" -w "$WORK"
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
# 5. Map outputs onto the WebODM asset contract
# ---------------------------------------------------------------------------
python3 "$(dirname "$0")/helpers/map_outputs.py" --proj "$PROJ" --work "$WORK"

echo "[effigies] done."
