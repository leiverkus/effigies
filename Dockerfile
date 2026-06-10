# Effigies — a NodeODM-compatible processing node whose engine is
# COLMAP (sparse) + OpenMVS full mesh/refine/texture (dense).
#
# Build:  docker build -t effigies .
# Run:    docker run -p 3001:3000 --gpus all effigies
# Then add http://<host>:3001 as a Processing Node in WebODM.
#
# Base carries CUDA so COLMAP and OpenMVS can use the GPU.
FROM nvidia/cuda:12.4.1-devel-ubuntu22.04 AS engine

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      colmap \
      openmvs-tools \
      python3 python3-numpy python3-pip \
      pdal \
      nodejs npm \
      git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
# NOTE: distro packages for colmap/openmvs vary in freshness. For production,
# build OpenMVS and COLMAP from source pinned to a known-good tag — the whole
# point of this node is using OpenMVS' ReconstructMesh/RefineMesh, so verify
# those binaries exist:  RUN which DensifyPointCloud ReconstructMesh RefineMesh TextureMesh

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
