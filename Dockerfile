# SPDX-FileCopyrightText: 2026 Patrick Leiverkus
# SPDX-License-Identifier: AGPL-3.0-or-later
# Effigies — a NodeODM-compatible processing node whose engine is
# COLMAP (sparse) + OpenMVS full mesh/refine/texture (dense).
#
# Build:  docker build -t effigies .
# Run:    docker run -p 3001:3000 --gpus all effigies
# Then add http://<host>:3001 as a Processing Node in WebODM.
#
# The whole point of this node is OpenMVS' ReconstructMesh/RefineMesh. Distro
# packages of OpenMVS are frequently too old or built without those binaries, so
# we build COLMAP and OpenMVS from PINNED upstream source and then *verify* the
# binaries exist (the `which` gate below fails the build loudly if any is
# missing).
#
# This is the CUDA/production image. It builds the SAME engine, from the SAME
# pinned sources and with the SAME build recipe, as the CPU test image
# (Dockerfile.cpu) — the ONLY differences are the CUDA base and the three
# -D*CUDA* flags. Keep the two files in lock-step: bump versions in both.
#
# MULTI-STAGE (builder -> slim runtime). The `engine` stage builds the engine on
# the CUDA *devel* base (full toolchain + -dev headers); the `runtime` stage
# starts from the much smaller CUDA *runtime* base, installs only the runtime
# shared libraries, copies the built artifacts from `engine`, and EXERCISES every
# binary so a missing runtime .so fails the build, not the user. The runtime apt
# set was derived empirically (readelf -d NEEDED) and verified end-to-end on the
# CPU image (Dockerfile.cpu); keep the two files in lock-step. The CUDA binaries'
# own runtime exercise (device init) is verified on a GPU host (RTX A4000, since
# 2026-07); the loader/shared-object gate runs at build time, and
# scripts/verify-gpu-image.sh asserts device init and that the OpenMVS binaries
# actually start under --gpus.

# CUDA 13.2.1 (was 12.8.1), adopted 2026-07-26 after an A/B on the same host.
# NOT 13.3.0, although it exists: this host's driver (595.84) reports CUDA 13.2, so
# 13.2.1 is natively covered and needs no minor-version forward compatibility — the
# highest available tag is not automatically the right pin.
# Verified before building: nvcc --list-gpu-arch in the 13.2.1 base still lists
# compute_86, so Ampere survives (CUDA 13 cut below Turing, i.e. compute_70 and
# older). COLMAP 4.1.1 and OpenMVS 2.4.0 compile against it with no source change.
# What it buys: the image drops 16.6 -> 12.4 GB (-25 %), from the leaner runtime
# base, with verify-gpu-image.sh identically 13/13 green. What it does NOT buy is
# speed — a same-host A/B was indistinguishable per unit of work (densify 24.9 vs
# 25.2 us/point, refine 118.3 vs 120.8 us/face). Raw stage times ran ~4 % longer
# only because that run produced 4.7 % more dense points; no quality claim is made
# from that, one A/B pair does not carry it.
ARG CUDA_VERSION=13.2.1
# Ubuntu 24.04 (noble), exactly as the CPU image — the current LTS. Noble dropped
# PDAL from its repos, so PDAL is built from pinned source below; the only header
# vendored is CGAL >=6.0 (OpenMVS 2.4.0 requires it; released after noble froze).
ARG UBUNTU_VERSION=24.04
FROM nvidia/cuda:${CUDA_VERSION}-devel-ubuntu${UBUNTU_VERSION} AS engine

# --- Pinned upstream versions (identical to Dockerfile.cpu; bump both together) ---
# OpenMVS 2.4.0 swaps the FLANN-based nearest-neighbour code for nanoflann and
# brings dense-stage stability fixes over 2.3.0.
#
# COLMAP 4.1.1 (was 4.0.4): 4.1.0 is the *stable* 4.1 the ROADMAP's learned
# SfM front-end waited for — ALIKED extraction + LightGlue matching built in
# natively via ONNX (see ONNX_ENABLED below), plus the Caspar GPU bundle-adjustment
# backend and spherical camera models. 4.1.1 is taken over 4.1.0 for one reason
# above all: it fixes a ~4-6x feature-matching slowdown caused by a process-global
# OpenMP critical section in RANSAC/LORANSAC. The only breaking change across the
# whole jump is the pycolmap enum GPSTransfrom->GPSTransformEllipsoid, which this
# engine does not use; CLI option names are unchanged (and sparse_colmap.sh probes
# them anyway). The which-gate below guards regressions.
ARG COLMAP_VERSION=4.1.1
ARG OPENMVS_VERSION=v2.4.0
# VCGlib has no release tags aligned to OpenMVS. Pinned to the cdcseacave/VCG
# commit the 2.4.0 engine was built and validated end-to-end against.
ARG VCG_REF=658ba36d0a5666650da6e066b4794efc5a463407
# CGAL 6 is the one header-only dep newer than noble (5.6): OpenMVS 2.4.0
# includes CGAL/AABB_traits_3.h, added in CGAL 6.0.
ARG CGAL_VERSION=6.0.1
# PDAL from pinned source — noble dropped it from the repos.
ARG PDAL_VERSION=2.10.1
# GPU architectures to compile for. 'all-major' covers common cards; narrow it
# (e.g. "75;86;89") to speed up the build for known hardware.
ARG CUDA_ARCH=all-major

ENV DEBIAN_FRONTEND=noninteractive

# --- Build + runtime dependencies (identical to Dockerfile.cpu) ---
# No Qt: COLMAP is built CLI-only (GUI_ENABLED=OFF). CUDA comes from the base image.
# libnanoflann-dev (1.5.x in noble) satisfies OpenMVS directly — no header overlay.
# libgdal-dev + liblaszip-dev are PDAL build deps (PDAL itself is built below).
RUN apt-get update && apt-get install -y --no-install-recommends \
      git cmake ninja-build build-essential ca-certificates \
      libeigen3-dev libcgal-dev libnanoflann-dev libgmp-dev libmpfr-dev \
      libboost-program-options-dev libboost-graph-dev libboost-system-dev \
      libboost-iostreams-dev libboost-serialization-dev \
      libflann-dev libfreeimage-dev libmetis-dev libsqlite3-dev \
      libopenimageio-dev openimageio-tools libsuitesparse-dev \
      libgoogle-glog-dev libgtest-dev libceres-dev libcurl4-openssl-dev \
      libglew-dev libglfw3-dev libglu1-mesa-dev \
      libopencv-dev libpng-dev libjpeg-dev libtiff-dev \
      libgdal-dev liblaszip-dev \
      python3 python3-dev python3-numpy python3-scipy python3-pip \
      python3-pil python3-pyproj python3-gdal python3-reportlab \
      nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# --- PDAL from pinned source (noble dropped the distro package) ---
RUN git clone --depth 1 --branch ${PDAL_VERSION} https://github.com/PDAL/PDAL.git /opt/pdal && \
    cmake -S /opt/pdal -B /opt/pdal/build -GNinja \
      -DCMAKE_BUILD_TYPE=Release \
      -DWITH_TESTS=OFF \
      -DCMAKE_INSTALL_PREFIX=/usr/local && \
    ninja -C /opt/pdal/build install && ldconfig && \
    rm -rf /opt/pdal && \
    pdal --version

# --- COLMAP from pinned source, CUDA- and ONNX-enabled, no GUI, no MVS ---
# ONNX_ENABLED=ON (was OFF) is what actually unlocks ALIKED extraction and
# LightGlue matching — the version bump alone does not. Upstream defaults
# ONNX_ENABLED and FETCH_ONNX to ON, so ONNX Runtime is pulled in by FetchContent
# during the build; no extra apt dependency. The models themselves are baked in
# below (offline requirement), and the ALIKED_*/LIGHTGLUE CLI types are not wired
# into pipeline/sparse_colmap.sh yet — that is the ROADMAP's separate step.
#
# MVS_ENABLED=OFF (new option in 4.1): Effigies never calls COLMAP's dense stack
# (patch_match_stereo / stereo_fusion / *_mesher) — DensifyPointCloud onward is
# OpenMVS' job, which is the whole point of this node. Compiling COLMAP's MVS
# module would only add build time and image size.
#
# After installing libcolmap, build the matching pycolmap from the SAME tree:
# helpers/gcp_bundle_adjust.py drives COLMAP's own Ceres BA through pycolmap for
# GCP-constrained bundle adjustment. It is built from source against the
# just-installed COLMAP (find_package(colmap) resolves under /usr/local); pycolmap
# builds from the repo ROOT (top-level pyproject.toml, scikit-build-core), build
# deps fetched by pip build isolation. Must run BEFORE `rm -rf /opt/colmap`.
# (Same addition as Dockerfile.cpu; the GPU image is validation-parked but kept in
# lockstep.)
RUN git clone --depth 1 --branch ${COLMAP_VERSION} https://github.com/colmap/colmap.git /opt/colmap && \
    cmake -S /opt/colmap -B /opt/colmap/build -GNinja \
      -DCMAKE_BUILD_TYPE=Release \
      -DCUDA_ENABLED=ON \
      -DCMAKE_CUDA_ARCHITECTURES=${CUDA_ARCH} \
      -DGUI_ENABLED=OFF \
      -DONNX_ENABLED=ON \
      -DMVS_ENABLED=OFF \
      -DTESTS_ENABLED=OFF \
      -DCMAKE_INSTALL_PREFIX=/usr/local && \
    ninja -C /opt/colmap/build install && \
    pip install --break-system-packages --no-cache-dir /opt/colmap && \
    python3 -c "import pycolmap; print('[effigies] pycolmap', pycolmap.__version__)" && \
    rm -rf /opt/colmap

# --- COLMAP vocabulary tree (for matcher=vocab_tree) ---
# Image-retrieval matching for large sets: each image queries this pre-trained
# tree for its most similar images instead of matching all O(n^2) pairs. Baked in
# so the matcher works offline (COLMAP would otherwise auto-download it on first
# use — ~140 s, to an ephemeral cache, and needs runtime network).
#
# FORMAT MATTERS: COLMAP 3.12+ replaced FLANN with FAISS for retrieval, so this
# COLMAP-4 image needs the *FAISS* tree (vocab_tree_faiss_*). The classic
# FLANN-format trees from demuc.de make COLMAP 4 abort with std::invalid_argument
# in IndexImages(). This is exactly the tree COLMAP 4 auto-downloads when no path
# is given; we pin it here. (The "3.11.1" in the URL is the GitHub *release tag*
# that hosts the asset, not the COLMAP version we build.) Flickr100K, 256K words
# (~72 MB). Override with --build-arg VOCAB_TREE_URL=/VOCAB_TREE_SHA256=.
ARG VOCAB_TREE_URL=https://github.com/colmap/colmap/releases/download/3.11.1/vocab_tree_faiss_flickr100K_words256K.bin
ARG VOCAB_TREE_SHA256=96ca8ec8ea60b1f73465aaf2c401fd3b3ca75cdba2d3c50d6a2f6f760f275ddc
RUN mkdir -p /usr/local/share/effigies && \
    python3 -c "import urllib.request; urllib.request.urlretrieve('${VOCAB_TREE_URL}', '/usr/local/share/effigies/vocab_tree.bin')" && \
    echo "${VOCAB_TREE_SHA256}  /usr/local/share/effigies/vocab_tree.bin" | sha256sum -c - && \
    echo "[effigies] COLMAP vocab tree baked in (FAISS, Flickr100K 256K words)"
ENV EFFIGIES_VOCAB_TREE=/usr/local/share/effigies/vocab_tree.bin

# --- ALIKED + LightGlue ONNX models (learned SfM front-end, COLMAP 4.1) ---
# Same offline rationale as the vocab tree above: COLMAP accepts a URL in
# --AlikedExtraction.*_model_path and downloads+caches it on first use, which needs
# runtime network and an ephemeral cache. Baked in and SHA256-pinned instead.
#
# Upstream hosts these as assets on the 3.13.0 *release tag* (the tag is where the
# assets live, not the COLMAP version we build — same convention as the vocab tree).
#
# RETRIEVAL DESCRIPTORS MUST MATCH THE FEATURE TYPE. The SIFT tree above cannot
# serve ALIKED retrieval, so the matching vocab_tree_*_aliked_* trees come along:
# LightGlue is pairwise, and sets beyond ~150 images need a retrieval stage on top
# (see ROADMAP). Both ALIKED variants are shipped: n16rot is faster and trained for
# some viewpoint invariance (the better default for convergent close-range work),
# n32 is more expensive without explicit viewpoint training.
#
# sift-lightglue.onnx is included too: it allows SIFT_LIGHTGLUE — keep SIFT
# features, gain the learned matcher — whose integration 4.1.0 fixed. Total ~136 MB.
ARG COLMAP_MODEL_TAG=3.13.0
RUN set -eu; \
    D=/usr/local/share/effigies/models; mkdir -p "$D"; \
    B="https://github.com/colmap/colmap/releases/download/${COLMAP_MODEL_TAG}"; \
    while read -r name sha; do \
      [ -n "$name" ] || continue; \
      python3 -c "import urllib.request,sys; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])" \
        "$B/$name" "$D/$name"; \
      echo "$sha  $D/$name" | sha256sum -c - >/dev/null; \
    done <<'MODELS'
aliked-n16rot.onnx 39c423d0a6f03d39ec89d3d1d61853765c2fb6a8b8381376c703e5758778a547
aliked-n32.onnx a077728a02d2de1a775c66df6de8cfeb7c6b51ca57572c64c680131c988c8b3c
aliked-lightglue.onnx b9a5de7204648b18a8cf5dcac819f9d30de1a5961ef03756803c8b86c2dceb8d
sift-lightglue.onnx e0500228472b43f92b3d36881a09b3310d3b058b56187b246cc7b9ab6429096e
bruteforce-matcher.onnx 3c1282f96d83f5ffc861a873298d08bbe5219f59af59223f5ceab5c41a182a47
vocab_tree_faiss_flickr100K_words64K_aliked_n16rot.bin 8b2f9bdc44ca7204d8543bb3adab4c03ba9336c84ef41220b5007991036f075e
vocab_tree_faiss_flickr100K_words64K_aliked_n32.bin 65619481045b8f933268f10c31ad180eb1ee7881182873efe0f5753972ef6a20
MODELS
RUN echo "[effigies] ALIKED/LightGlue ONNX models baked in:" && \
    ls -1sh /usr/local/share/effigies/models
ENV EFFIGIES_MODEL_DIR=/usr/local/share/effigies/models

# --- Obj2Tiles (OpenDroneMap) for the 3D Tiles export (opt-in --3d-tiles) ---
# Self-contained single-file binary (bundles its own .NET runtime — no runtime to
# install); the SAME tool + version ODM uses for OGC 3D Tiles. Asset picked by
# build arch (this CUDA image is x86_64; the CPU image is arm64). Pin per-arch sha.
ARG OBJ2TILES_VERSION=v1.4.0
ARG OBJ2TILES_SHA256_ARM64=1310d44c10eb3b149d2b5b07b8c2379a15262f64a34ef9d479c13de911e7508b
ARG OBJ2TILES_SHA256_X64=ff09c26ba32fe6122dfd6e60adf258ca942fb1574a75a34927344d8ceedccc4a
RUN set -eux; \
    case "$(uname -m)" in \
      aarch64) A=LinuxArm64; S="${OBJ2TILES_SHA256_ARM64}" ;; \
      x86_64)  A=Linux64;    S="${OBJ2TILES_SHA256_X64}"   ;; \
      *) echo "unsupported arch $(uname -m) for Obj2Tiles" >&2; exit 1 ;; \
    esac; \
    U="https://github.com/OpenDroneMap/Obj2Tiles/releases/download/${OBJ2TILES_VERSION}/Obj2Tiles-${A}.zip"; \
    python3 -c "import urllib.request; urllib.request.urlretrieve('${U}', '/tmp/o2t.zip')"; \
    echo "${S}  /tmp/o2t.zip" | sha256sum -c -; \
    python3 -m zipfile -e /tmp/o2t.zip /tmp/o2t/; \
    install -m755 /tmp/o2t/Obj2Tiles /usr/local/bin/Obj2Tiles; \
    rm -rf /tmp/o2t /tmp/o2t.zip; \
    Obj2Tiles --help >/dev/null 2>&1 || { rc=$?; [ "$rc" = 1 ] || { echo "Obj2Tiles exec failed (rc=$rc)"; exit 1; }; }; \
    echo "[effigies] Obj2Tiles ${OBJ2TILES_VERSION} (${A}) baked in"

# --- OpenPointClass for ML point classification (opt-in --classify) ---
# ODM's classifier; no prebuilt binary, so build pcclassify from pinned source
# (links our installed PDAL; LightGBM is fetched+built by its cmake). AGPL, invoked
# as a separate process (mere aggregation, as with OpenMVS). Pinned model baked in.
ARG OPC_REF=dd6a560a1d43cb709f7b220b19a436e25a889e3e
ARG OPC_MODEL_URL=https://github.com/uav4geo/OpenPointClass/releases/download/v1.1.3/vehicles-vegetation-buildings.zip
ARG OPC_MODEL_SHA256=258f67f02a9d2c329c61726a227281f3ac0af9dd4c274c5c893975beb9dc191a
RUN apt-get update && apt-get install -y --no-install-recommends libtbb-dev libeigen3-dev && \
    rm -rf /var/lib/apt/lists/* && \
    git clone https://github.com/uav4geo/OpenPointClass.git /opt/opc && \
    git -C /opt/opc checkout ${OPC_REF} && \
    cmake -S /opt/opc -B /opt/opc/build -DCMAKE_BUILD_TYPE=Release \
      -DWITH_GBT=ON -DBUILD_PCTRAIN=OFF -DPDAL_DIR=/usr/local/lib/cmake/PDAL && \
    cmake --build /opt/opc/build -j"$(nproc)" --target pcclassify && \
    install -m755 /opt/opc/build/pcclassify /usr/local/bin/pcclassify && \
    rm -rf /opt/opc && \
    pcclassify </dev/null >/dev/null 2>&1; rc=$?; [ "$rc" -lt 126 ] || { echo "pcclassify exec failed (rc=$rc)"; exit 1; }; \
    echo "[effigies] OpenPointClass pcclassify (${OPC_REF}) baked in"
RUN mkdir -p /usr/local/share/effigies && \
    python3 -c "import urllib.request; urllib.request.urlretrieve('${OPC_MODEL_URL}', '/tmp/opc_model.zip')" && \
    echo "${OPC_MODEL_SHA256}  /tmp/opc_model.zip" | sha256sum -c - && \
    python3 -m zipfile -e /tmp/opc_model.zip /tmp/opc_model/ && \
    install -m644 /tmp/opc_model/model.bin /usr/local/share/effigies/opc_model.bin && \
    rm -rf /tmp/opc_model /tmp/opc_model.zip && \
    echo "[effigies] OpenPointClass model (vehicles-vegetation-buildings v1.1.3) baked in"
ENV EFFIGIES_OPC_MODEL=/usr/local/share/effigies/opc_model.bin

# --- CGAL 6 (header-only, pinned; used via -DCGAL_DIR below) ---
# The one dep newer than noble: OpenMVS 2.4.0 includes CGAL/AABB_traits_3.h
# (CGAL >=6.0); noble ships 5.6.
RUN python3 -c "import urllib.request; urllib.request.urlretrieve('https://github.com/CGAL/cgal/releases/download/v${CGAL_VERSION}/CGAL-${CGAL_VERSION}-library.tar.xz', '/tmp/cgal.tar.xz')" && \
    tar -xf /tmp/cgal.tar.xz -C /opt && rm /tmp/cgal.tar.xz

# --- OpenMVS from pinned source, CUDA-enabled ---
# Two source patches keep 2.4.0 building against noble's OpenCV (4.6) — identical
# to the CPU image:
#   1. libs/IO: the JXL pkg-config check is hard-REQUIRED via a macro; we do not
#      install libjxl (enabling it would compile a write path that needs OpenCV
#      >=4.7). Drop REQUIRED so JXL support self-disables (the surrounding code
#      already guards on JPEGXL_FOUND); we never emit JPEG-XL.
#   2. Types.inl references cv::IMWRITE_JPEGXL_QUALITY (OpenCV >= 4.7 only) on the
#      .jxl write path we never take; map it to the JPEG constant so it compiles.
# CUDA differences vs. the CPU image: OpenMVS_USE_CUDA=ON, the CUDA arch, and the
# CUDA stubs library path (so the link finds libcuda at build time).
RUN git clone https://github.com/cdcseacave/VCG.git /opt/vcglib && \
    git -C /opt/vcglib checkout ${VCG_REF} && \
    git clone --depth 1 --branch ${OPENMVS_VERSION} https://github.com/cdcseacave/openMVS.git /opt/openMVS && \
    sed -i 's/pkg_check_modules(${PREFIX} REQUIRED IMPORTED_TARGET/pkg_check_modules(${PREFIX} IMPORTED_TARGET/' /opt/openMVS/libs/IO/CMakeLists.txt && \
    sed -i 's/cv::IMWRITE_JPEGXL_QUALITY/cv::IMWRITE_JPEG_QUALITY/' /opt/openMVS/libs/Common/Types.inl && \
    cmake -S /opt/openMVS -B /opt/openMVS_build \
      -DCMAKE_BUILD_TYPE=Release \
      -DVCG_ROOT=/opt/vcglib \
      -DCGAL_DIR=/opt/CGAL-${CGAL_VERSION} \
      -DOpenMVS_USE_CUDA=ON \
      -DCMAKE_CUDA_ARCHITECTURES=${CUDA_ARCH} \
      -DCMAKE_LIBRARY_PATH=/usr/local/cuda/lib64/stubs/ \
      -DOpenMVS_BUILD_VIEWER=OFF \
      -DOpenMVS_USE_PYTHON=OFF \
      -DOpenMVS_USE_BREAKPAD=OFF \
      -DOpenMVS_ENABLE_TESTS=OFF \
      -DEIGEN3_INCLUDE_DIR=/usr/include/eigen3 \
      -DCMAKE_INSTALL_PREFIX=/usr/local && \
    cmake --build /opt/openMVS_build -j"$(nproc)" --target install && \
    rm -rf /opt/openMVS /opt/openMVS_build /opt/vcglib /opt/CGAL-${CGAL_VERSION}

# OpenMVS installs its tools under <prefix>/bin/OpenMVS — put them on PATH.
ENV PATH="/usr/local/bin/OpenMVS:${PATH}"

# --- Verify the binaries that justify this node's existence (fail loudly) ---
RUN set -eux; \
    command -v colmap; \
    for b in DensifyPointCloud ReconstructMesh RefineMesh TextureMesh InterfaceCOLMAP; do \
      command -v "$b" || { echo "FATAL: required OpenMVS binary '$b' missing after build" >&2; exit 1; }; \
    done; \
    command -v pdal; \
    echo "[effigies] all required engine binaries present"

# --- Entwine (EPT tileset builder for the Potree web viewer) ---
# Builds entwine_pointcloud/ NODE-side (ODM parity) so WebODM does not have to
# regenerate the viewer tileset from the LAZ in its own post-processing.
# pointcloud_to_laz.py --ept picks it up automatically. Same fork + commit ODM
# pins (untwine is no alternative: since 1.x it emits COPC only, no EPT).
# WITH_ZSTD=OFF because our PDAL is built without zstd; CURL off as in ODM.
# Placed after the engine layers to keep their build cache.
ARG ENTWINE_REF=0cf957432f291e841ff1385085dadad933dcba8d
RUN git clone https://github.com/OpenDroneMap/entwine.git /opt/entwine && \
    git -C /opt/entwine checkout ${ENTWINE_REF} && \
    cmake -S /opt/entwine -B /opt/entwine/build -GNinja \
      -DCMAKE_BUILD_TYPE=Release \
      -DWITH_CURL=OFF -DWITH_ZSTD=OFF -DWITH_TESTS=OFF \
      -DCMAKE_INSTALL_PREFIX=/usr/local && \
    ninja -C /opt/entwine/build install && ldconfig && \
    rm -rf /opt/entwine && \
    command -v entwine

# --- py4dgeo (M3C2 change detection) from pinned source ---
# Multi-epoch change detection (helpers/change_detect.py, opt-in via --align-to)
# uses py4dgeo's M3C2. PyPI ships wheels for macOS arm64 and Linux x86_64 only —
# there is NO manylinux aarch64 wheel — so on linux/arm64 pip builds it from the
# sdist (C++17; Eigen is already present from libeigen3-dev). Build deps
# (scikit-build-core, pybind11, …) and runtime deps (laspy, …) are fetched by pip
# build isolation. Pinned for reproducibility (same policy as every other
# component). NOTE: the multithreaded path segfaults on arm64, so change_detect.py
# pins py4dgeo.set_num_threads(1) before run() — do not remove that.
ARG PY4DGEO_VERSION=1.1.0
RUN pip install --break-system-packages --no-cache-dir py4dgeo==${PY4DGEO_VERSION} && \
    python3 -c "import py4dgeo; print('[effigies] py4dgeo', py4dgeo.__version__)"

# --- NodeODM REST layer (pinned upstream + one type-safety hotfix) ---
# Pinned for reproducibility (same policy as every other component). The sed is a
# minimal hotfix for an upstream regression introduced by NodeODM PR #268
# ("more-quotes", 2026-04-30): shQuote() calls s.replace() on every option value,
# but numeric options (e.g. our cpu-threads) arrive as JS numbers after NodeODM's
# own type cast -> "TypeError: s.replace is not a function" crashes the node when
# a task starts. String(s) makes it type-safe; behaviour is otherwise unchanged.
ARG NODEODM_REF=8ad3e30dc0006d59fd552c1e884614b53daa19e3
WORKDIR /opt
RUN git clone https://github.com/OpenDroneMap/NodeODM.git && \
    git -C NodeODM checkout ${NODEODM_REF} && \
    sed -i 's|s = s.replace(/"/g, "")|s = String(s).replace(/"/g, "")|' NodeODM/libs/odmRunner.js && \
    grep -q 'String(s).replace' NodeODM/libs/odmRunner.js
WORKDIR /opt/NodeODM
RUN npm install --production

# ===========================================================================
# Runtime stage — slim image on the CUDA *runtime* base (not devel). Drops the
# build toolchain + every -dev header; the big win vs the single-stage image is
# the devel->runtime CUDA base. Keep this lock-step with Dockerfile.cpu's runtime
# stage (the runtime apt set is verified there end-to-end); the only delta is the
# CUDA base.
#
# The *-cudnn-* variant is required, not cosmetic: ONNX Runtime's CUDA execution
# provider — which COLMAP 4.1 uses for ALIKED/LightGlue whenever use_gpu is on —
# dlopens libonnxruntime_providers_cuda.so, which needs libcudnn.so.9. The plain
# `-runtime` base does not ship cuDNN, and the failure appears only at INFERENCE
# time, not at build time: the provider is loaded lazily, so the build succeeds and
# then `colmap feature_extractor --FeatureExtraction.type ALIKED_N16ROT` aborts with
# "Failed to load library ... libcudnn.so.9". Costs roughly +0.7 GB compressed.
# The builder stage deliberately stays on plain `-devel`: cuDNN is only needed to
# RUN inference, never to compile, and that stage is discarded anyway.
# ===========================================================================
FROM nvidia/cuda:${CUDA_VERSION}-cudnn-runtime-ubuntu${UBUNTU_VERSION} AS runtime
ENV DEBIAN_FRONTEND=noninteractive

# Runtime shared libraries (same noble package names as the CPU image; the CUDA
# runtime libs come from the base). Derived empirically via readelf -d NEEDED.
# libtbb12 (OpenPointClass) and libicu74 (Obj2Tiles' bundled .NET) are
# belt-and-braces; the exercise gate below catches any miss.
#
# libglew2.2 + libopengl0 arrived with COLMAP 4.1.1: upstream defaults
# OPENGL_ENABLED to ON, so libcolmap (and therefore pycolmap._core) links GLEW and
# OpenGL even though we build GUI_ENABLED=OFF. Without them the gate below fails
# with pycolmap's generic "Cannot import the C++ backend" — the real cause is only
# visible by importing pycolmap._core directly. OpenGL is also COLMAP's non-CUDA
# SIFT-GPU fallback path, so keep it rather than building -DOPENGL_ENABLED=OFF.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates \
      libblas3 liblapack3 libgomp1 \
      libglew2.2 libopengl0 \
      libboost-iostreams1.83.0 libboost-program-options1.83.0 libboost-serialization1.83.0 \
      libceres4t64 libcholmod5 libmetis5 libgoogle-glog0v6t64 \
      libgmp10 \
      libgdal34t64 libgeotiff5 libproj25 \
      libopencv-calib3d406t64 libopencv-core406t64 libopencv-imgcodecs406t64 libopencv-imgproc406t64 \
      libopenimageio2.4t64 \
      libjpeg-turbo8 libpng16-16t64 libtiff6 \
      libsqlite3-0 libssl3t64 libxml2 zlib1g libcurl4t64 \
      libtbb12 libicu74 \
      python3 python3-numpy python3-scipy python3-pil python3-pyproj python3-gdal python3-reportlab \
      python3-six python3-requests python3-setuptools \
      nodejs \
    && rm -rf /var/lib/apt/lists/*

# Built artifacts: binaries (/usr/local/bin incl. OpenMVS/, colmap, pdal, entwine,
# pcclassify, Obj2Tiles), our shared libs, the pip Python packages (pycolmap,
# py4dgeo + deps), and baked-in data (vocab tree, OPC model).
COPY --from=engine /usr/local /usr/local
COPY --from=engine /opt/NodeODM /opt/NodeODM
# Drop build-only leftovers (static archives, headers, cmake/pkgconfig metadata).
RUN rm -rf /usr/local/include /usr/local/lib/cmake /usr/local/lib/pkgconfig \
           /usr/local/lib/*.a /usr/local/lib/python3.12/dist-packages/**/*.a 2>/dev/null; \
    ldconfig

ENV PATH="/usr/local/bin/OpenMVS:${PATH}"
ENV EFFIGIES_VOCAB_TREE=/usr/local/share/effigies/vocab_tree.bin
ENV EFFIGIES_OPC_MODEL=/usr/local/share/effigies/opc_model.bin
# ENV does not cross build stages — the files arrive via COPY --from=engine
# /usr/local above, but every EFFIGIES_* path must be re-declared here or it is
# empty at runtime.
ENV EFFIGIES_MODEL_DIR=/usr/local/share/effigies/models

# --- our engine code (last layer, so source edits never bust the heavy ones) ---
COPY . /opt/effigies
ENV ODM_PATH=/opt/effigies
RUN ln -sf /opt/effigies/helpers/optionsToJson.py /opt/NodeODM/helpers/odmOptionsToJson.py || true

# --- Exercise every engine binary: a missing runtime .so fails the BUILD. ---
#     The OpenMVS binaries CANNOT be started here, and that is by design, not a
#     defect: they link libcuda.so.1, which is the NVIDIA *driver* library. It is
#     not part of the CUDA toolkit and not in any base image — the container
#     runtime injects it at `docker run --gpus`. So `--help` aborts at load time
#     during `docker build` on every host, GPU or not. (The earlier claim here that
#     "device init happens only at real use, so this passes on a GPU-less builder"
#     was wrong: init is deferred, but LOADING the NEEDED libcuda is not. It went
#     unnoticed because this CUDA image had never actually been built.)
#
#     They are therefore checked with ldd, ignoring libcuda.so.1. That is strictly
#     STRONGER than the --help probe for these binaries: the loader aborts on the
#     first missing library it hits, whereas ldd reports the whole unresolved set —
#     so a second missing .so can no longer hide behind libcuda.
RUN set -eu; \
    fail=0; \
    exercise() { \
      out="$("$@" --help 2>&1 || true)"; \
      case "$out" in \
        *"cannot open shared object"*|*"loading shared libraries"*|*"symbol lookup error"*) \
          echo "FATAL: $1 fails to load: $out" >&2; fail=1 ;; \
      esac; \
    }; \
    ldd_check() { \
      miss="$(ldd "$(command -v "$1")" 2>/dev/null | grep "not found" | grep -v "libcuda\.so\.1" || true)"; \
      [ -z "$miss" ] || { echo "FATAL: $1 has unresolved libraries: $miss" >&2; fail=1; }; \
    }; \
    command -v colmap >/dev/null || { echo "FATAL: colmap missing"; fail=1; }; \
    out="$(colmap -h 2>&1 || true)"; case "$out" in *"shared object"*|*"loading shared libraries"*) echo "FATAL: colmap loader: $out"; fail=1;; esac; \
    for b in DensifyPointCloud ReconstructMesh RefineMesh TextureMesh InterfaceCOLMAP TransformScene; do \
      command -v "$b" >/dev/null || { echo "FATAL: OpenMVS $b missing"; fail=1; continue; }; \
      ldd_check "$b"; \
    done; \
    exercise pdal; exercise entwine; \
    out="$(pcclassify 2>&1 </dev/null || true)"; case "$out" in *"shared object"*|*"loading shared libraries"*) echo "FATAL: pcclassify loader: $out"; fail=1;; esac; \
    out="$(Obj2Tiles --help 2>&1 || true)"; case "$out" in *"shared object"*|*"loading shared libraries"*|*"libicu"*) echo "FATAL: Obj2Tiles loader: $out"; fail=1;; esac; \
    python3 -c "import pycolmap, py4dgeo, numpy, scipy, osgeo.gdal, pyproj, PIL, reportlab; print('[effigies] python extension imports OK')" || fail=1; \
    node -e "process.exit(0)" || { echo "FATAL: node broken"; fail=1; }; \
    [ "$fail" = 0 ] || { echo "FATAL: runtime binary verification failed" >&2; exit 1; }; \
    echo "[effigies] runtime image: libraries resolve for every engine binary (CUDA slim; the OpenMVS binaries' libcuda.so.1 arrives with --gpus at run time — scripts/verify-gpu-image.sh asserts they actually start there)"

# NodeODM reads config-default.json relative to its working dir — run from there.
WORKDIR /opt/NodeODM
EXPOSE 3000
CMD ["node", "/opt/NodeODM/index.js", "--odm_path", "/opt/effigies"]
