#!/usr/bin/env bash
# COLMAP sparse stage.
# args: IMAGES WORK MATCHER CAMERA_MODEL GPU_FLAG [MAPPER]
set -euo pipefail
IMAGES="$1"; WORK="$2"; MATCHER="$3"; CAMERA_MODEL="$4"; GPU="$5"; MAPPER="${6:-incremental}"

DB="$WORK/database.db"
mkdir -p "$WORK/sparse"

# COLMAP CLI option naming differs between the two pinned images. COLMAP 3.13
# renamed the generic feature options SiftExtraction/SiftMatching.{use_gpu,
# num_threads} -> Feature{Extraction,Matching}.* (the SIFT-*algorithm* options
# keep the Sift* prefix). The GPU/production image is COLMAP 3.11.1 (old names);
# the CPU image is 4.0.4 (new names). Probe the actual binary's help so one
# script drives both: passing an option the installed COLMAP does not know aborts
# the run, and that surfaces upstream only as the opaque NodeODM "Cannot process
# dataset". (See ROADMAP "v0.2.x — COLMAP 4".)
if colmap feature_extractor --help 2>&1 | grep -q -- '--FeatureExtraction.use_gpu'; then
  FEAT_EXTRACT=FeatureExtraction
  FEAT_MATCH=FeatureMatching
else
  FEAT_EXTRACT=SiftExtraction
  FEAT_MATCH=SiftMatching
fi

# CPU memory guards. Two distinct failure modes appear only on the CPU path and
# both surface upstream as the opaque NodeODM "Cannot process dataset":
#
#  1. SIFT extraction OOM. COLMAP's CPU extractor doubles each image (first_octave
#     -1) and fans out across all cores, so on a many-core host the simultaneous
#     scale-space buffers spike RAM and the OOM killer takes the process down
#     before a single image finishes. -> cap worker threads (EFFIGIES_CPU_THREADS).
#  2. Matcher segfault. The CPU descriptor matcher (FLANN) crashes when a whole
#     matching block of images is held in memory at once (default block_size 50);
#     a smaller block matches all pairs cleanly. -> cap the exhaustive block size
#     (EFFIGIES_CPU_MATCH_BLOCK).
#
# Both affect peak memory and speed only, not the reconstruction. Neither applies
# on GPU, where SIFT and matching run in device memory.
EXTRACT_THREADS=()
MATCH_THREADS=()
EXHAUSTIVE_CPU=()
if [[ "$GPU" != "1" ]]; then
  CPU_THREADS="${EFFIGIES_CPU_THREADS:-4}"
  # num_threads lives under the same renamed prefix detected above;
  # ExhaustiveMatching.block_size is unchanged across versions.
  EXTRACT_THREADS=(--${FEAT_EXTRACT}.num_threads "$CPU_THREADS")
  MATCH_THREADS=(--${FEAT_MATCH}.num_threads "$CPU_THREADS")
  EXHAUSTIVE_CPU=(--ExhaustiveMatching.block_size "${EFFIGIES_CPU_MATCH_BLOCK:-10}")
fi

echo "[colmap] feature_extractor"
colmap feature_extractor \
  --database_path "$DB" \
  --image_path "$IMAGES" \
  --ImageReader.camera_model "$CAMERA_MODEL" \
  --ImageReader.single_camera_per_folder 1 \
  --${FEAT_EXTRACT}.use_gpu "$GPU" \
  "${EXTRACT_THREADS[@]}"

echo "[colmap] ${MATCHER}_matcher"
case "$MATCHER" in
  exhaustive) colmap exhaustive_matcher --database_path "$DB" --${FEAT_MATCH}.use_gpu "$GPU" "${MATCH_THREADS[@]}" "${EXHAUSTIVE_CPU[@]}" ;;
  sequential) colmap sequential_matcher --database_path "$DB" --${FEAT_MATCH}.use_gpu "$GPU" "${MATCH_THREADS[@]}" ;;
  spatial)    colmap spatial_matcher    --database_path "$DB" --${FEAT_MATCH}.use_gpu "$GPU" "${MATCH_THREADS[@]}" ;;
  vocab_tree) colmap vocab_tree_matcher --database_path "$DB" --${FEAT_MATCH}.use_gpu "$GPU" "${MATCH_THREADS[@]}" ;;
  *) echo "[colmap] unknown matcher $MATCHER" >&2; exit 1 ;;
esac

if [[ "$MAPPER" == "global" ]]; then
  # GLOMAP global SfM, built into COLMAP 4 (colmap global_mapper). Optional, never
  # the default: incremental is more robust on close-range / convergent sets;
  # global is much faster on large, well-connected (e.g. aerial) blocks. Same
  # database/output contract as the incremental mapper (writes sparse/0).
  #
  # GLOMAP ships built into COLMAP 4 only; COLMAP 3.x (the current GPU/production
  # image) has no global_mapper subcommand. Probe for it and fail clearly rather
  # than letting COLMAP abort with an opaque "command not recognized" that
  # surfaces as the generic NodeODM "Cannot process dataset".
  if ! colmap global_mapper --help >/dev/null 2>&1; then
    echo "[colmap] mapper=global requires COLMAP 4 (built-in GLOMAP); this image's COLMAP has no global_mapper subcommand. Use the default incremental mapper, or build the COLMAP 4 image." >&2
    exit 1
  fi
  echo "[colmap] global_mapper (GLOMAP global SfM)"
  GLOBAL_THREADS=()
  [[ "$GPU" != "1" ]] && GLOBAL_THREADS=(--GlobalMapper.num_threads "${EFFIGIES_CPU_THREADS:-4}")
  colmap global_mapper \
    --database_path "$DB" \
    --image_path "$IMAGES" \
    --output_path "$WORK/sparse" \
    "${GLOBAL_THREADS[@]}"
else
  echo "[colmap] mapper (incremental SfM)"
  colmap mapper \
    --database_path "$DB" \
    --image_path "$IMAGES" \
    --output_path "$WORK/sparse"
fi

# COLMAP writes model(s) under sparse/0, sparse/1, ... ; downstream uses sparse/0
if [[ ! -d "$WORK/sparse/0" ]]; then
  echo "[colmap] reconstruction failed: no model produced" >&2
  exit 1
fi
echo "[colmap] sparse model at $WORK/sparse/0"

# Export a TEXT copy of the model alongside the binary one. The mapper writes
# binary (cameras.bin/...), but georef_bridge.py reads the TEXT format
# (cameras.txt/images.txt/points3D.txt); without this it finds no model and
# silently degrades --georeference auto/exif/gcp to local-only.
echo "[colmap] model_converter -> TXT (for georef_bridge)"
colmap model_converter \
  --input_path "$WORK/sparse/0" \
  --output_path "$WORK/sparse/0" \
  --output_type TXT
if [[ ! -f "$WORK/sparse/0/images.txt" ]]; then
  echo "[colmap] model_converter failed: no text model produced" >&2
  exit 1
fi

# Undistort into the workspace layout OpenMVS' InterfaceCOLMAP expects:
# <dense>/sparse/{cameras,images,points3D}.bin + <dense>/images/ (undistorted).
# This is also required for correctness, not just layout: OpenMVS densifies on
# pinhole (undistorted) images, so the raw distorted sparse/0 must not be fed in.
echo "[colmap] image_undistorter -> dense workspace for OpenMVS"
rm -rf "$WORK/dense"
colmap image_undistorter \
  --image_path "$IMAGES" \
  --input_path "$WORK/sparse/0" \
  --output_path "$WORK/dense" \
  --output_type COLMAP
if [[ ! -f "$WORK/dense/sparse/cameras.bin" ]]; then
  echo "[colmap] undistortion failed: no dense/sparse model produced" >&2
  exit 1
fi
echo "[colmap] dense workspace at $WORK/dense"
