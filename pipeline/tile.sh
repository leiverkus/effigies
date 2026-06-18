#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Patrick Leiverkus
# SPDX-License-Identifier: AGPL-3.0-or-later
# Per-tile dense chain for split-merge tiling.
# args: WORK TILE_ID <dense_openmvs.sh args 2..16, with HARMONIZE forced false>
#
# Builds the tile's subset COLMAP model + a symlink to the SHARED (already
# harmonised) undistorted images, runs InterfaceCOLMAP on the subset, then the
# existing dense_openmvs.sh on the tile workdir — unchanged. Global exposure
# harmonisation already ran once on the shared images, so the caller passes
# HARMONIZE=false; per-tile harmonise would mis-resolve a tile workdir anyway.
set -euo pipefail
WORK="$1"; TILE_ID="$2"; shift 2
HELPERS="$(cd "$(dirname "$0")/../helpers" && pwd)"
TILE="$WORK/tiles/$TILE_ID"
# OpenMVS binary-name resolver (InterfaceCOLMAP naming variants); fail loudly.
source "$(dirname "$0")/openmvs_bin.sh"
IFACE_COLMAP="$(resolve_openmvs_bin InterfaceCOLMAP InterfaceColmap)"

mkdir -p "$TILE/dense/sparse"
# symlink the shared undistorted images (one global undistort; tiles never copy)
ln -sfn "$WORK/dense/images" "$TILE/dense/images"

echo "[tile] $TILE_ID: writing subset COLMAP model"
python3 "$HELPERS/tiling.py" --subset --work "$WORK" --tile "$TILE_ID" \
        --manifest "$WORK/tiles_manifest.json" --out "$TILE/dense/sparse"

echo "[tile] $TILE_ID: $IFACE_COLMAP (subset model, shared image folder)"
"$IFACE_COLMAP" -i "$TILE/dense" --image-folder "$TILE/dense/images/" \
                -o "$TILE/scene.mvs" -w "$TILE"
if [[ ! -f "$TILE/scene.mvs" ]]; then
  echo "[tile] $TILE_ID: $IFACE_COLMAP produced no scene.mvs" >&2
  exit 1
fi

echo "[tile] $TILE_ID: dense chain (Densify -> ReconstructMesh -> RefineMesh -> TextureMesh)"
bash "$(dirname "$0")/dense_openmvs.sh" "$TILE" "$@"
echo "[tile] $TILE_ID: done"
