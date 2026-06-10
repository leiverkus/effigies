#!/usr/bin/env bash
# OpenSfM sparse stage (alternative to COLMAP, for GPS-tagged aerial sets).
# args: IMAGES WORK
# Produces $WORK/opensfm with a geo-aligned reconstruction that InterfaceOpenSfM reads.
set -euo pipefail
IMAGES="$1"; WORK="$2"
OSFM="$WORK/opensfm"
mkdir -p "$OSFM/images"

# OpenSfM expects images under the dataset dir
cp -al "$IMAGES/." "$OSFM/images/" 2>/dev/null || cp -r "$IMAGES/." "$OSFM/images/"

cat > "$OSFM/config.yaml" <<'YAML'
feature_type: SIFT
matching_gps_neighbors: 8
matching_gps_distance: 0
triangulation_type: ROBUST
bundle_outlier_filtering_type: AUTO
align_method: auto
YAML

echo "[opensfm] extract_metadata"
opensfm extract_metadata "$OSFM"
echo "[opensfm] detect_features"
opensfm detect_features "$OSFM"
echo "[opensfm] match_features"
opensfm match_features "$OSFM"
echo "[opensfm] create_tracks"
opensfm create_tracks "$OSFM"
echo "[opensfm] reconstruct"
opensfm reconstruct "$OSFM"
echo "[opensfm] reconstruction at $OSFM"
