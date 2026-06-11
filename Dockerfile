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
# missing). Eigen 3.4, CGAL, Boost and OpenCV come from Ubuntu 22.04 packages —
# they are recent enough for the pinned OpenMVS/COLMAP releases.
#
# NOTE: this is a single-stage image on the CUDA *devel* base, so every build and
# runtime library is present and the result is more likely to actually run. A
# slimmer multi-stage (devel build -> runtime copy) image is a later optimization
# (see ROADMAP.md); correctness and verifiability come first.

ARG CUDA_VERSION=12.4.1
ARG UBUNTU_VERSION=22.04
FROM nvidia/cuda:${CUDA_VERSION}-devel-ubuntu${UBUNTU_VERSION} AS engine

# --- Pinned upstream versions (bump here; the which-gate guards regressions) ---
ARG COLMAP_VERSION=3.11.1
ARG OPENMVS_VERSION=v2.3.0
# VCGlib has no release tags aligned to OpenMVS. The CPU image (Dockerfile.cpu)
# is pinned to the commit it was validated against; this production image still
# tracks a moving branch and is left so deliberately — its VCG SHA is locked
# together with the pending OpenMVS 2.4.0 bump (see ROADMAP), once the CUDA build
# is exercised on GPU hardware. Must be locked before tagging a release image.
ARG VCG_REF=master
# GPU architectures to compile for. 'all-major' covers common cards; narrow it
# (e.g. "75;86;89") to speed up the build for known hardware.
ARG CUDA_ARCH=all-major

ENV DEBIAN_FRONTEND=noninteractive

# --- Build + runtime dependencies (shared by COLMAP and OpenMVS) ---
RUN apt-get update && apt-get install -y --no-install-recommends \
      git cmake ninja-build build-essential ca-certificates \
      libeigen3-dev libcgal-dev libgmp-dev libmpfr-dev \
      libboost-program-options-dev libboost-graph-dev libboost-system-dev \
      libboost-iostreams-dev libboost-serialization-dev \
      libflann-dev libfreeimage-dev libmetis-dev libsqlite3-dev \
      libgoogle-glog-dev libgtest-dev libceres-dev libcurl4-openssl-dev \
      libglew-dev libglfw3-dev libglu1-mesa-dev \
      qtbase5-dev libqt5opengl5-dev \
      libopencv-dev libpng-dev libjpeg-dev libtiff-dev \
      python3 python3-dev python3-numpy python3-scipy python3-pip \
      python3-pil python3-pyproj \
      pdal \
      nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# --- COLMAP from pinned source ---
RUN git clone --depth 1 --branch ${COLMAP_VERSION} https://github.com/colmap/colmap.git /opt/colmap && \
    cmake -S /opt/colmap -B /opt/colmap/build -GNinja \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_CUDA_ARCHITECTURES=${CUDA_ARCH} \
      -DCMAKE_INSTALL_PREFIX=/usr/local && \
    ninja -C /opt/colmap/build install && \
    rm -rf /opt/colmap

# --- OpenMVS from pinned source (VCGlib is a build-time header dependency) ---
RUN git clone https://github.com/cdcseacave/VCG.git /opt/vcglib && \
    git -C /opt/vcglib checkout ${VCG_REF} && \
    git clone --depth 1 --branch ${OPENMVS_VERSION} https://github.com/cdcseacave/openMVS.git /opt/openMVS && \
    cmake -S /opt/openMVS -B /opt/openMVS_build \
      -DCMAKE_BUILD_TYPE=Release \
      -DVCG_ROOT=/opt/vcglib \
      -DOpenMVS_USE_CUDA=ON \
      -DCMAKE_CUDA_ARCHITECTURES=${CUDA_ARCH} \
      -DCMAKE_LIBRARY_PATH=/usr/local/cuda/lib64/stubs/ \
      -DEIGEN3_INCLUDE_DIR=/usr/include/eigen3 \
      -DCMAKE_INSTALL_PREFIX=/usr/local && \
    cmake --build /opt/openMVS_build -j"$(nproc)" --target install && \
    rm -rf /opt/openMVS /opt/openMVS_build /opt/vcglib

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

# --- NodeODM REST layer (unmodified upstream) ---
WORKDIR /opt
RUN git clone --depth 1 https://github.com/OpenDroneMap/NodeODM.git
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
