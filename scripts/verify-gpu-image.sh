#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Patrick Leiverkus
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Assert that a built image is genuinely CUDA-enabled, and that the engine's own
# GPU probes resolve to the GPU path. No dataset and no long run needed.
#
# Why this exists: DEPLOYMENT.md's "Verifying a build" only checks that the
# binaries EXIST (a `which` gate). That passes on the CPU image too. These checks
# are the discriminating ones — they fail on Dockerfile.cpu and pass only on a
# working CUDA build, which had never been compiled before this host existed.
#
# Usage: ./scripts/verify-gpu-image.sh [image-tag]     (default: effigies:gpu)
#
set -euo pipefail

IMG="${1:-effigies:gpu}"
pass=0; fail=0

ok()   { echo "  ok    $*"; pass=$((pass+1)); }
bad()  { echo "  FAIL  $*"; fail=$((fail+1)); }
head_() { echo; echo "== $* =="; }

# Run a command inside the image. The image's CMD starts NodeODM, so override it.
in_img()     { docker run --rm --entrypoint bash "$IMG" -lc "$1"; }
in_img_gpu() { docker run --rm --gpus all --entrypoint bash "$IMG" -lc "$1"; }

docker image inspect "$IMG" >/dev/null 2>&1 || { echo "no such image: $IMG" >&2; exit 1; }
echo "verifying image: $IMG"

# --- 1. Binaries present (same gate as DEPLOYMENT.md) --------------------------
head_ "engine binaries"
BINS="colmap DensifyPointCloud ReconstructMesh RefineMesh TextureMesh InterfaceCOLMAP pdal"
if in_img "which $BINS" >/dev/null 2>&1; then
  ok "all engine binaries on PATH"
else
  bad "an engine binary is missing"
fi

# --- 2. OpenMVS was built with CUDA ------------------------------------------
# This is the exact probe pipeline/dense_openmvs.sh:25 uses to decide whether to
# pass --cuda-device. A CPU build (OpenMVS_USE_CUDA=OFF) rejects the option, so
# its presence proves the CUDA build flag took effect.
#
# MUST run with --gpus: these binaries link libcuda.so.1, the NVIDIA *driver*
# library, which the container runtime injects only for --gpus. Without it they
# abort at load time and this check would fail for the wrong reason.
head_ "OpenMVS CUDA build"
if in_img_gpu "DensifyPointCloud --help 2>&1 | grep -q -- '--cuda-device'"; then
  ok "DensifyPointCloud accepts --cuda-device (OpenMVS_USE_CUDA=ON)"
else
  bad "DensifyPointCloud has no --cuda-device — this is a CPU OpenMVS build"
fi

# --- 3. Binaries actually link CUDA, and it resolves -------------------------
# Independent of any --help string. Two CUDA APIs are in play and the binaries do
# NOT use the same one — checking only for libcudart wrongly fails RefineMesh:
#   libcudart.so.12  CUDA *runtime* API, ships in the image (DensifyPointCloud, colmap)
#   libcuda.so.1     CUDA *driver* API, injected by the container runtime (RefineMesh)
# So accept either, and require that it RESOLVES rather than merely being listed.
# Runs with --gpus, without which the driver-API link could never resolve.
head_ "CUDA linkage"
for b in DensifyPointCloud RefineMesh colmap; do
  out="$(in_img_gpu "ldd \$(which $b) 2>/dev/null | grep -E 'libcudart|libcuda\.so'" 2>/dev/null || true)"
  libs="$(printf '%s' "$out" | grep -oE 'libcuda(rt)?\.so[.0-9]*' | sort -u | paste -sd, -)"
  if [ -n "$libs" ] && ! printf '%s' "$out" | grep -q 'not found'; then
    ok "$b links CUDA and it resolves ($libs)"
  else
    bad "$b has no resolved CUDA linkage (${libs:-none found})"
  fi
done

# --- 3b. The OpenMVS binaries actually START once the driver is injected -----
# The build-time gate can only check that libraries RESOLVE (libcuda.so.1 is absent
# during docker build by design). This is where "they really run" is established —
# the same loader-failure match the Dockerfile's exercise() uses.
head_ "OpenMVS binaries start with the driver present"
for b in DensifyPointCloud ReconstructMesh RefineMesh TextureMesh InterfaceCOLMAP; do
  out="$(in_img_gpu "$b --help 2>&1 | head -5" 2>/dev/null || true)"
  case "$out" in
    *"loading shared libraries"*|*"cannot open shared object"*|*"symbol lookup error"*)
      bad "$b still fails to load under --gpus: $(echo "$out" | head -1)" ;;
    *)
      ok "$b starts under --gpus" ;;
  esac
done

# --- 4. The engine's own GPU probe resolves to GPU ---------------------------
# run.sh:173 gates the whole GPU path on `nvidia-smi -L` succeeding INSIDE the
# container. If this fails, every run silently degrades to CPU.
head_ "engine GPU probe (run.sh:173)"
if in_img_gpu "command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L" >/dev/null 2>&1; then
  ok "nvidia-smi -L succeeds inside the container with --gpus all"
  in_img_gpu "nvidia-smi -L" 2>/dev/null | sed 's/^/        /'
else
  bad "nvidia-smi -L fails inside the container — run.sh would fall back to CPU"
fi

# --- 5. Negative control -----------------------------------------------------
# Without --gpus the probe MUST fail. If it "succeeds" here, check 4 proves
# nothing about passthrough and the whole verification is meaningless.
head_ "negative control (no --gpus)"
if in_img "command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L" >/dev/null 2>&1; then
  bad "probe succeeds WITHOUT --gpus — check 4 is not discriminating"
else
  ok "probe correctly fails without --gpus (so check 4 is meaningful)"
fi

# --- 6. CUDA device init from inside the container ---------------------------
# The Dockerfile's own build-time self-test only checks that binaries LOAD;
# device initialisation happens at runtime (see the note at Dockerfile:330).
head_ "CUDA device initialisation"
if in_img_gpu "nvidia-smi --query-gpu=name,memory.total,compute_cap --format=csv,noheader" 2>/dev/null \
     | sed 's/^/        /'; then
  ok "device reports name/VRAM/compute capability at runtime"
else
  bad "device query failed at runtime"
fi

echo
echo "-------- $pass passed, $fail failed --------"
if [[ $fail -eq 0 ]]; then
  echo "Image is CUDA-enabled and the engine's GPU path is live."
  echo "Next: ./scripts/gpu-smoke-run.sh <image-dir> $IMG"
else
  echo "Do NOT trust a run from this image as a GPU result."
  exit 1
fi
