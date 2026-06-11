#!/usr/bin/env bash
# COLMAP sparse stage.
# args: IMAGES WORK MATCHER CAMERA_MODEL GPU_FLAG
set -euo pipefail
IMAGES="$1"; WORK="$2"; MATCHER="$3"; CAMERA_MODEL="$4"; GPU="$5"

DB="$WORK/database.db"
mkdir -p "$WORK/sparse"

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
  EXTRACT_THREADS=(--SiftExtraction.num_threads "$CPU_THREADS")
  MATCH_THREADS=(--SiftMatching.num_threads "$CPU_THREADS")
  EXHAUSTIVE_CPU=(--ExhaustiveMatching.block_size "${EFFIGIES_CPU_MATCH_BLOCK:-10}")
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
  exhaustive) colmap exhaustive_matcher --database_path "$DB" --SiftMatching.use_gpu "$GPU" "${MATCH_THREADS[@]}" "${EXHAUSTIVE_CPU[@]}" ;;
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
