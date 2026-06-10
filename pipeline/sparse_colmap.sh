#!/usr/bin/env bash
# COLMAP sparse stage.
# args: IMAGES WORK MATCHER CAMERA_MODEL GPU_FLAG
set -euo pipefail
IMAGES="$1"; WORK="$2"; MATCHER="$3"; CAMERA_MODEL="$4"; GPU="$5"

DB="$WORK/database.db"
mkdir -p "$WORK/sparse"

echo "[colmap] feature_extractor"
colmap feature_extractor \
  --database_path "$DB" \
  --image_path "$IMAGES" \
  --ImageReader.camera_model "$CAMERA_MODEL" \
  --ImageReader.single_camera_per_folder 1 \
  --SiftExtraction.use_gpu "$GPU"

echo "[colmap] ${MATCHER}_matcher"
case "$MATCHER" in
  exhaustive) colmap exhaustive_matcher --database_path "$DB" --SiftMatching.use_gpu "$GPU" ;;
  sequential) colmap sequential_matcher --database_path "$DB" --SiftMatching.use_gpu "$GPU" ;;
  spatial)    colmap spatial_matcher    --database_path "$DB" --SiftMatching.use_gpu "$GPU" ;;
  vocab_tree) colmap vocab_tree_matcher --database_path "$DB" --SiftMatching.use_gpu "$GPU" ;;
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
