#!/usr/bin/env bash
# COLMAP sparse stage.
# args: IMAGES WORK MATCHER CAMERA_MODEL GPU_FLAG
set -euo pipefail
IMAGES="$1"; WORK="$2"; MATCHER="$3"; CAMERA_MODEL="$4"; GPU="$5"

DB="$WORK/database.db"
mkdir -p "$WORK/sparse"

# CPU SIFT memory guard. COLMAP's CPU extractor doubles each image (first_octave
# -1) and fans out across all cores, so on a many-core host the simultaneous
# scale-space buffers spike RAM and the OOM killer takes the process down before
# a single image finishes (manifesting upstream as the opaque NodeODM "Cannot
# process dataset"). Cap the worker threads on the CPU path; this affects speed
# and peak memory only, not the reconstruction. No cap on GPU, where SIFT runs on
# device memory. Override with EFFIGIES_CPU_THREADS.
EXTRACT_THREADS=()
MATCH_THREADS=()
if [[ "$GPU" != "1" ]]; then
  CPU_THREADS="${EFFIGIES_CPU_THREADS:-4}"
  EXTRACT_THREADS=(--SiftExtraction.num_threads "$CPU_THREADS")
  MATCH_THREADS=(--SiftMatching.num_threads "$CPU_THREADS")
fi

echo "[colmap] feature_extractor"
colmap feature_extractor \
  --database_path "$DB" \
  --image_path "$IMAGES" \
  --ImageReader.camera_model "$CAMERA_MODEL" \
  --ImageReader.single_camera_per_folder 1 \
  --SiftExtraction.use_gpu "$GPU" \
  "${EXTRACT_THREADS[@]}"

echo "[colmap] ${MATCHER}_matcher"
case "$MATCHER" in
  exhaustive) colmap exhaustive_matcher --database_path "$DB" --SiftMatching.use_gpu "$GPU" "${MATCH_THREADS[@]}" ;;
  sequential) colmap sequential_matcher --database_path "$DB" --SiftMatching.use_gpu "$GPU" "${MATCH_THREADS[@]}" ;;
  spatial)    colmap spatial_matcher    --database_path "$DB" --SiftMatching.use_gpu "$GPU" "${MATCH_THREADS[@]}" ;;
  vocab_tree) colmap vocab_tree_matcher --database_path "$DB" --SiftMatching.use_gpu "$GPU" "${MATCH_THREADS[@]}" ;;
  *) echo "[colmap] unknown matcher $MATCHER" >&2; exit 1 ;;
esac

echo "[colmap] mapper (incremental SfM)"
colmap mapper \
  --database_path "$DB" \
  --image_path "$IMAGES" \
  --output_path "$WORK/sparse"

# COLMAP writes model(s) under sparse/0, sparse/1, ... ; downstream uses sparse/0
if [[ ! -d "$WORK/sparse/0" ]]; then
  echo "[colmap] reconstruction failed: no model produced" >&2
  exit 1
fi
echo "[colmap] sparse model at $WORK/sparse/0"
