#!/usr/bin/env bash
# Build the Effigies node image and print integration instructions.
set -euo pipefail
cd "$(dirname "$0")/.."

TAG="${1:-effigies:dev}"

echo "Building $TAG ..."
docker build -t "$TAG" .

cat <<EOF

Built $TAG.

Run it (GPU recommended):
  docker run -p 3001:3000 --gpus all $TAG

Then in WebODM:
  Processing Nodes -> Add -> http://<host>:3001

The node will appear as engine "effigies" with its own task options
(sparse-engine, refine-mesh-iters, georeference, crs, ...).
EOF
