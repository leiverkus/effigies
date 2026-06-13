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
# NOTE: single-stage image on the CUDA *devel* base, so every build and runtime
# library is present and the result is more likely to actually run. A slimmer
# multi-stage (devel build -> runtime copy) image is a later optimization (see
# ROADMAP.md); correctness and verifiability come first.

ARG CUDA_VERSION=12.8.1
# Ubuntu 24.04 (noble), exactly as the CPU image — the current LTS. Noble dropped
# PDAL from its repos, so PDAL is built from pinned source below; the only header
# vendored is CGAL >=6.0 (OpenMVS 2.4.0 requires it; released after noble froze).
ARG UBUNTU_VERSION=24.04
FROM nvidia/cuda:${CUDA_VERSION}-devel-ubuntu${UBUNTU_VERSION} AS engine

# --- Pinned upstream versions (identical to Dockerfile.cpu; bump both together) ---
# OpenMVS 2.4.0 swaps the FLANN-based nearest-neighbour code for nanoflann and
# brings dense-stage stability fixes over 2.3.0; COLMAP 4.0.4 is the validated
# baseline (CLI option names, built-in GLOMAP, FAISS retrieval). The which-gate
# below guards regressions.
ARG COLMAP_VERSION=4.0.4
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

# --- COLMAP from pinned source, CUDA-enabled, no GUI ---
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
      -DONNX_ENABLED=OFF \
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

# --- our engine ---
COPY . /opt/effigies
# Point NodeODM at our engine dir: it reads ENGINE, run.sh and the options helper here.
ENV ODM_PATH=/opt/effigies

# NodeODM calls helpers/odmOptionsToJson.py inside ODM_PATH-adjacent path; we expose
# our optionsToJson via a shim named to match what odmRunner invokes.
RUN ln -sf /opt/effigies/helpers/optionsToJson.py /opt/NodeODM/helpers/odmOptionsToJson.py || true

EXPOSE 3000
CMD ["node", "/opt/NodeODM/index.js", "--odm_path", "/opt/effigies"]
