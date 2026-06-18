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
# own runtime exercise (device init) is verified on a GPU host — this machine has
# none — but the loader/shared-object gate runs at build time here too.

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
# ===========================================================================
FROM nvidia/cuda:${CUDA_VERSION}-runtime-ubuntu${UBUNTU_VERSION} AS runtime
ENV DEBIAN_FRONTEND=noninteractive

# Runtime shared libraries (same noble package names as the CPU image; the CUDA
# runtime libs come from the base). Derived empirically via readelf -d NEEDED.
# libtbb12 (OpenPointClass) and libicu74 (Obj2Tiles' bundled .NET) are
# belt-and-braces; the exercise gate below catches any miss.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates \
      libblas3 liblapack3 libgomp1 \
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

# --- our engine code (last layer, so source edits never bust the heavy ones) ---
COPY . /opt/effigies
ENV ODM_PATH=/opt/effigies
RUN ln -sf /opt/effigies/helpers/optionsToJson.py /opt/NodeODM/helpers/odmOptionsToJson.py || true

# --- Exercise every engine binary: a missing runtime .so fails the BUILD. The
#     CUDA binaries load their runtime libs here (device init happens only at
#     real use, so this passes on a GPU-less builder too). ---
RUN set -eu; \
    fail=0; \
    exercise() { \
      out="$("$@" --help 2>&1 || true)"; \
      case "$out" in \
        *"cannot open shared object"*|*"loading shared libraries"*|*"symbol lookup error"*) \
          echo "FATAL: $1 fails to load: $out" >&2; fail=1 ;; \
      esac; \
    }; \
    command -v colmap >/dev/null || { echo "FATAL: colmap missing"; fail=1; }; \
    out="$(colmap -h 2>&1 || true)"; case "$out" in *"shared object"*|*"loading shared libraries"*) echo "FATAL: colmap loader: $out"; fail=1;; esac; \
    for b in DensifyPointCloud ReconstructMesh RefineMesh TextureMesh InterfaceCOLMAP TransformScene; do \
      command -v "$b" >/dev/null || { echo "FATAL: OpenMVS $b missing"; fail=1; continue; }; \
      exercise "$b"; \
    done; \
    exercise pdal; exercise entwine; \
    out="$(pcclassify 2>&1 </dev/null || true)"; case "$out" in *"shared object"*|*"loading shared libraries"*) echo "FATAL: pcclassify loader: $out"; fail=1;; esac; \
    out="$(Obj2Tiles --help 2>&1 || true)"; case "$out" in *"shared object"*|*"loading shared libraries"*|*"libicu"*) echo "FATAL: Obj2Tiles loader: $out"; fail=1;; esac; \
    python3 -c "import pycolmap, py4dgeo, numpy, scipy, osgeo.gdal, pyproj, PIL, reportlab; print('[effigies] python extension imports OK')" || fail=1; \
    node -e "process.exit(0)" || { echo "FATAL: node broken"; fail=1; }; \
    [ "$fail" = 0 ] || { echo "FATAL: runtime binary verification failed" >&2; exit 1; }; \
    echo "[effigies] runtime image: all engine binaries load and run (CUDA slim)"

# NodeODM reads config-default.json relative to its working dir — run from there.
WORKDIR /opt/NodeODM
EXPOSE 3000
CMD ["node", "/opt/NodeODM/index.js", "--odm_path", "/opt/effigies"]
