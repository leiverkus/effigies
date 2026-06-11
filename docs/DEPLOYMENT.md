# Deployment

Effigies is a NodeODM-compatible engine. You build it into a container, run it,
and add it to WebODM as a **Processing Node**. There are two paths:

| Path | Image | Use |
|---|---|---|
| **GPU production** | `Dockerfile` (CUDA) | real jobs; needs a Linux host with an NVIDIA GPU |
| **CPU local test** | `Dockerfile.cpu` | validate the node + small datasets; runs anywhere, no GPU |

> **Why two images?** OpenMVS' `RefineMesh` — the reason this node exists — is
> GPU work. A machine without an NVIDIA GPU (e.g. an Apple-Silicon Mac) cannot run
> the CUDA image: Docker has no `nvidia` runtime to pass a GPU through, and the
> `nvidia/cuda` base is x86-64 only. Use the CPU image there to test the contract,
> and run real jobs on a GPU host.

---

## A. Local CPU test image (no GPU)

Builds the same pinned COLMAP + OpenMVS from source, without CUDA. On Apple
Silicon it builds natively (arm64). The from-source build takes roughly
**30–60 min** and is memory-hungry — give Docker Desktop ≥ 8 GB RAM (Settings →
Resources). Adjust parallelism with `--build-arg JOBS=N`.

```bash
# from the repo root
docker build -f Dockerfile.cpu -t effigies:cpu .
```

### Run it on WebODM's network

WebODM reaches its processing nodes by **container name on its Docker network**
(`webodm_default`). Start Effigies there with a stable name and the NodeODM port:

```bash
docker run -d --name effigies-1 \
  --network webodm_default \
  --restart unless-stopped \
  effigies:cpu
```

Then in WebODM: **Processing Nodes → Add Node**
- Hostname: `effigies-1`
- Port: `3000`

(That mirrors how the bundled `node-odx-1` is wired.) The node should turn green
and show the Effigies options.

> If you'd rather not touch WebODM's network, publish the port instead
> (`docker run -d --name effigies-1 -p 3001:3000 effigies:cpu`) and add the node
> as `host.docker.internal` : `3001` — reachable from the WebODM containers on
> Docker Desktop.

### Running a task against the CPU node

This build has **no CUDA**, so in the task options set **Use GPU = OFF**. Keep the
dataset small (e.g. 20–40 close-range photos) — CPU `RefineMesh` is slow. A good
first run: `sparse-engine=colmap`, `refine-mesh-iters=1`, `georeference=none`.

---

## B. GPU production host (Linux + NVIDIA)

On a Linux machine with an NVIDIA GPU and the
[NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
installed (`docker info | grep -i nvidia` should list the runtime):

```bash
# build (compiles COLMAP 3.11.1 + OpenMVS v2.3.0 with CUDA; this takes a while)
docker build -t effigies:gpu .

# optionally narrow the GPU arch to speed the build, e.g. for a single card:
# docker build --build-arg CUDA_ARCH="86" -t effigies:gpu .

# run, exposing the NodeODM port
docker run -d --name effigies-gpu --gpus all -p 3001:3000 \
  --restart unless-stopped effigies:gpu
```

### Add it to WebODM (even a WebODM running elsewhere)

WebODM only needs to reach the node over HTTP. If WebODM runs on your Mac and
Effigies on a GPU box, add the node by the GPU host's address:

- Hostname: `<gpu-host-ip-or-dns>`
- Port: `3001`

Make sure the port is reachable (firewall / security group). The GPU does **not**
need to be on the WebODM machine — only on the machine running Effigies.

---

## Hardware sizing (production)

Effigies' resource profile differs from stock ODM precisely because of the steps
it adds. The two peaks to size for:

- **System RAM → `ReconstructMesh`.** The Delaunay tetrahedralization holds the
  *entire* dense cloud at once (stock ODM skips this step, so it never pays this
  cost). `TextureMesh`'s atlas and the incremental mapper's bundle adjustment are
  next. These run **host-side even in the CUDA image** — system RAM is not
  substitutable by VRAM.
- **GPU VRAM → `DensifyPointCloud` and `RefineMesh`.** Depth-map estimation
  (CUDA PatchMatch) and mesh refinement scale with *image resolution ×
  `number-views-fuse`* and mesh size respectively.

Anchored on a measured run (71 × 12 MP images → 1.3 M dense points, 890 k mesh
vertices, a 585 MB textured OBJ):

| | Minimum | Recommended | Heavy |
|---|---|---|---|
| **System RAM** | 32 GB | **64 GB** | 128 GB |
| **GPU VRAM** | 8 GB | **12–16 GB** | 24 GB |
| Fits | ≤ ~150 imgs @ 12–24 MP | object / architecture sets, full-res | 300+ imgs, hi-res architecture |
| Example GPU | RTX 3060/4060, A2000 | RTX 4070 Ti/4080, A4000/A5000 | RTX 4090, A5000/A6000 |

**Recommended production box: 64 GB RAM + 16 GB VRAM** (RTX 4080 / A4000–A5000
class) — covers full 12–24 MP densification without downscaling and the
`ReconstructMesh` peak with headroom. On 8 GB VRAM you must downscale via
`--densify-resolution-level`, which works against the detail Effigies exists to
produce. If you also run **large drone sets** for the stock-ODM benchmark (many
images, GLOMAP `mapper=global`), prefer **128 GB RAM** — global bundle adjustment
and tetrahedralization of big sets are the bottleneck there, not the GPU.

---

## Verifying a build

Both Dockerfiles end with a `which` gate that fails the build loudly if `colmap`,
`DensifyPointCloud`, `ReconstructMesh`, `RefineMesh`, `TextureMesh`,
`InterfaceCOLMAP` or `pdal` is missing. A successful `docker build` therefore
already guarantees the binaries that justify this node exist. To re-check a built
image manually:

```bash
docker run --rm effigies:cpu bash -lc \
  'which colmap DensifyPointCloud ReconstructMesh RefineMesh TextureMesh InterfaceCOLMAP pdal'
```

## Logs / troubleshooting

```bash
docker logs -f effigies-1            # NodeODM REST + task output
docker exec -it effigies-1 bash      # poke around the engine
```

If the node shows red in WebODM, it's almost always name/port/network: confirm the
container is on `webodm_default` (`docker inspect effigies-1 -f '{{json .NetworkSettings.Networks}}'`)
and that you used port `3000` (the in-container NodeODM port), not the published one.
