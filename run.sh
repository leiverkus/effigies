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
  [profile]=none
  [sparse-engine]=colmap
  [matcher]=exhaustive
  [mapper]=incremental
  [camera-model]=OPENCV
  [densify-resolution-level]=1
  [number-views-fuse]=3
  [skip-reconstruct-mesh]=false
  [refine-mesh-iters]=3
  [refine-max-face-area]=16
  [refine-gradient-step]=25.05
  [mesh-decimate]=1.0
  [texture-resolution]=8192
  [texture-seam-leveling]=false
  [skip-color-harmonize]=false
  [cpu-threads]=4
  [cpu-match-block]=10
  [crs]=auto
  [georeference]=auto
  [gcp]=""
  [skip-orthophoto]=false
  [orthophoto-resolution]=auto
  [no-gpu]=false
  [project-path]=""
)

PROJECT_NAME=""
declare -A GIVEN=()           # keys the caller set explicitly (beat the profile)
while [[ $# -gt 0 ]]; do
  if [[ "$1" == --* ]]; then
    key="${1#--}"
    GIVEN[$key]=1
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

# ---------------------------------------------------------------------------
# 1b. Capture profile — a parameter bundle for options the caller did NOT set
#     explicitly (explicit options always win). Profiles live here, versioned
#     with the engine, instead of in WebODM's preset JSON (those are per-install
#     data keyed to ODM's option names).
# ---------------------------------------------------------------------------
profile_set() { [[ -n "${GIVEN[$1]:-}" ]] || OPT[$1]="$2"; }
case "${OPT[profile]}" in
  drone-3d)      # aerial survey: GPS neighbourhood matching, balanced resolution
    profile_set matcher spatial
    profile_set mapper incremental
    profile_set densify-resolution-level 1
    profile_set number-views-fuse 3
    profile_set refine-mesh-iters 3
    ;;
  object)        # finds / artefacts / turntable: max detail, local frame
    profile_set matcher exhaustive
    profile_set georeference none
    profile_set densify-resolution-level 0
    profile_set number-views-fuse 3
    profile_set refine-mesh-iters 3
    profile_set refine-max-face-area 8
    ;;
  architecture)  # buildings / facades: convergent sets, balanced resolution
    profile_set matcher exhaustive
    profile_set densify-resolution-level 1
    profile_set number-views-fuse 3
    profile_set refine-mesh-iters 3
    ;;
  none) ;;
  *) echo "[effigies] WARN: unknown profile '${OPT[profile]}' — using defaults" >&2 ;;
esac

PROJ="${OPT[project-path]}/${PROJECT_NAME}"
IMAGES="${PROJ}/images"
WORK="${PROJ}/effigies"
mkdir -p "$WORK"

echo "[effigies] project: $PROJ"
echo "[effigies] sparse-engine=${OPT[sparse-engine]} matcher=${OPT[matcher]} mapper=${OPT[mapper]} refine-iters=${OPT[refine-mesh-iters]} crs=${OPT[crs]}"

# Resolve GPU usage. GPU is used by default when present (--no-gpu forces CPU);
# we still probe and fall back to CPU when no usable CUDA
# GPU is present: COLMAP's SIFT aborts hard ("Cannot use Sift GPU without CUDA or
# OpenGL support") rather than degrading, which would surface to WebODM only as
# the opaque "Cannot process dataset". A documented CPU fallback beats a cryptic
# failure (the CPU image has no CUDA at all, and a GPU image may run without one).
# CPU tuning caps as task options; an explicitly set env var still wins (ops override)
export EFFIGIES_CPU_THREADS="${EFFIGIES_CPU_THREADS:-${OPT[cpu-threads]}}"
export EFFIGIES_CPU_MATCH_BLOCK="${EFFIGIES_CPU_MATCH_BLOCK:-${OPT[cpu-match-block]}}"

GPU_FLAG=0
if [[ "${OPT[no-gpu]}" != "true" ]]; then
  if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
    GPU_FLAG=1
  else
    echo "[effigies] WARN: no usable CUDA GPU detected; falling back to CPU (use --no-gpu to silence)" >&2
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
     "$([[ "${OPT[skip-reconstruct-mesh]}" == "true" ]] && echo false || echo true)" \
     "${OPT[refine-mesh-iters]}" \
     "${OPT[mesh-decimate]}" \
     "${OPT[texture-resolution]}" \
     "$GPU_FLAG" \
     "${OPT[refine-max-face-area]}" \
     "${OPT[refine-gradient-step]}" \
     "${OPT[texture-seam-leveling]}" \
     "$([[ "${OPT[skip-color-harmonize]}" == "true" ]] && echo false || echo true)"

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
if [[ "${OPT[skip-orthophoto]}" != "true" ]]; then
  if ! python3 "$(dirname "$0")/helpers/orthophoto.py" \
       --work "$WORK" --resolution "${OPT[orthophoto-resolution]}"; then
    echo "[effigies] WARN: orthophoto step failed; continuing without it" >&2
  fi
fi

# ---------------------------------------------------------------------------
# 5c. Camera assets — cameras.json (intrinsics) + shots.geojson (camera positions
#     on the map). Matches the ODM downloadable assets. Non-fatal.
# ---------------------------------------------------------------------------
if ! python3 "$(dirname "$0")/helpers/camera_exports.py" --work "$WORK"; then
  echo "[effigies] WARN: camera export step failed; continuing without it" >&2
fi

# ---------------------------------------------------------------------------
# 5d. glTF model — WebODM's "Struktur-Modell (glTF)" (odm_textured_model_geo.glb),
#     a self-contained .glb of the same textured mesh. Non-fatal.
# ---------------------------------------------------------------------------
if ! python3 "$(dirname "$0")/helpers/mesh_to_gltf.py" --work "$WORK"; then
  echo "[effigies] WARN: glTF export failed; continuing without it" >&2
fi

# ---------------------------------------------------------------------------
# 5e. Quality report PDF — WebODM's "Qualitätsbericht" (odm_report/report.pdf).
#     Stats table + orthophoto thumbnail. Non-fatal.
# ---------------------------------------------------------------------------
if ! python3 "$(dirname "$0")/helpers/report.py" --work "$WORK" --name "$PROJECT_NAME" \
     --sparse-engine "${OPT[sparse-engine]}" --matcher "${OPT[matcher]}" \
     --mapper "${OPT[mapper]}" --refine-iters "${OPT[refine-mesh-iters]}"; then
  echo "[effigies] WARN: report step failed; continuing without it" >&2
fi

# ---------------------------------------------------------------------------
# 6. Map outputs onto the WebODM asset contract
# ---------------------------------------------------------------------------
python3 "$(dirname "$0")/helpers/map_outputs.py" --proj "$PROJ" --work "$WORK"

echo "[effigies] done."
