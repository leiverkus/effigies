# Deployment

Effigies is a NodeODM-compatible engine. You build it into a container, run it,
and add it to WebODM as a **Processing Node**. There are two paths:

| Path | Image | Use |
|---|---|---|
| **GPU production** | `Dockerfile` (CUDA) | real jobs; needs a Linux host with an NVIDIA GPU |
| **CPU local test** | `Dockerfile.cpu` | validate the node + small datasets; runs anywhere, no GPU |

> **Why two images?** A machine without an NVIDIA GPU (e.g. an Apple-Silicon Mac)
> cannot run the CUDA image at all: Docker has no `nvidia` runtime to pass a GPU
> through, and the `nvidia/cuda` base is x86-64 only. Use the CPU image there to
> test the contract, and run real jobs on a GPU host — `DensifyPointCloud` is
> genuinely GPU-accelerated and CPU densification of a real dataset is slow.
>
> Note the *shape* of that speedup, though: densify is only ~10 % of a full run's
> wall clock (see **Hardware sizing** below). `RefineMesh` — the reason this node
> exists over stock ODM — links the CUDA *driver* API but was never measured holding
> device memory, so it behaves as CPU load. An earlier version of this document
> claimed RefineMesh was GPU work; the measurements do not support it.

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
docker volume create effigies_data   # once: persists NodeODM task state
docker run -d --name effigies-1 --init \
  --network webodm_default \
  --restart unless-stopped \
  -v effigies_data:/opt/NodeODM/data \
  effigies:cpu
```

> `--init` matters: NodeODM (node as PID 1) does not reap child processes. Without
> an init, a killed engine run leaves zombie processes and NodeODM may never
> notice the task died — it hangs as "running" in WebODM.
>
> **Watch the Docker VM's disk.** A full-resolution run needs roughly 10–15 GB of
> transient space (undistorted images, depth maps); image build caches eat tens of
> GB more (`docker builder prune`). A full disk kills the engine mid-densify with
> no clearer symptom than dead/zombie processes.

> The volume matters: NodeODM keeps its tasks in `/opt/NodeODM/data` **inside the
> container**. Without it, every image update / container recreate wipes the task
> store and WebODM's existing tasks fail with "`<uuid> not found`" on the node —
> they must then be restarted (re-upload) as new tasks.
>
> **Stop gracefully before recreating:** `docker stop effigies-1 && docker rm
> effigies-1`. A hard `docker rm -f` SIGKILLs NodeODM before it writes its tasks
> dump — on the next start it treats every task directory as orphaned and
> **deletes it from the volume**.

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

This build has **no CUDA**; the engine detects that and falls back to CPU on its
own (optionally check **no-gpu** to silence the warning). Keep the
dataset small (e.g. 20–40 close-range photos) — CPU `RefineMesh` is slow. A good
first run: `sparse-engine=colmap`, `refine-mesh-iters=1`, `georeference=none`.

---

## B. GPU production host (Linux + NVIDIA)

On a Linux machine with an NVIDIA GPU and the
[NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
installed (`docker info | grep -i nvidia` should list the runtime).

### Provisioning a fresh Ubuntu 24.04 host

`scripts/provision-gpu-host.sh` installs Docker CE + the NVIDIA Container Toolkit,
wires the runtime into Docker, and smoke-tests device passthrough. It is
idempotent and opens no ports. It deliberately does **not** install the GPU
driver (that needs a reboot) — if `nvidia-smi` is missing it prints
`sudo ubuntu-drivers install` and stops.

```bash
sudo ./scripts/provision-gpu-host.sh
```

It ends by printing the card's compute capability, which is what you want for the
narrowed build below.

### Build and run

```bash
# build (compiles COLMAP 4.1.1 + OpenMVS v2.4.0 with CUDA; this takes a while)
docker build -t effigies:gpu .

# narrow the GPU arch to the installed card to speed the build up considerably —
# provision-gpu-host.sh prints the value (e.g. 86 for an Ampere A4000):
# docker build --build-arg CUDA_ARCH="86" -t effigies:gpu .

# run, exposing the NodeODM port
docker run -d --name effigies-gpu --gpus all -p 3001:3000 \
  --restart unless-stopped effigies:gpu
```

> **On a host that is directly reachable from the internet, do not use
> `-p 3001:3000`.** NodeODM has no authentication by default: anyone who can
> reach the port can submit tasks, upload files and consume the GPU. Bind it to
> loopback (`-p 127.0.0.1:3001:3000`) and reach it through an SSH tunnel, or
> restrict the port to the WebODM host with a default-deny firewall.

### Add it to WebODM (even a WebODM running elsewhere)

WebODM only needs to reach the node over HTTP. If WebODM runs on your Mac and
Effigies on a GPU box, add the node by the GPU host's address:

- Hostname: `<gpu-host-ip-or-dns>`
- Port: `3001`

Make sure the port is reachable (firewall / security group). The GPU does **not**
need to be on the WebODM machine — only on the machine running Effigies.

---

## Hardware sizing (production)

**Measured, not reasoned.** The numbers below come from instrumented runs on an
RTX A4000 host (i9-13900KS, 8 P-cores / 16 threads, 125 GB RAM, Ubuntu 24.04) in
2026-07 — see `docs/planned-experiments.md` for the full stage tables. They replace
an earlier estimate that over-specified VRAM and did not mention CPU or disk at all.

**The headline is counter-intuitive for a "GPU photogrammetry node": the workload is
CPU- and RAM-bound.** Stage shares of the reference run (110 nadir drone images,
12 MP, `densify-resolution-level 0`, `refine-mesh-iters 3`, total 1 h 00 m 41 s):

| Stage | Share | Binding resource |
|---|---|---|
| DensifyPointCloud | **10 %** | GPU (CUDA PatchMatch) |
| ReconstructMesh | 6 % | CPU + **the RAM peak** |
| RefineMesh | **46 %** | CPU |
| TextureMesh | 29 % | CPU (atlas packing is largely serial) |
| Post-processing | 7 % | CPU |

About **90 % of wall clock is CPU work**. `RefineMesh` links the CUDA *driver* API
but was never observed holding device memory, so despite the name it behaves as CPU
load here. On large sets the sparse stage adds to that: 339- and 400-image runs
spent most of their time in `vocab_tree` matching and the incremental mapper.

### The peaks, quantified

- **System RAM → `ReconstructMesh`.** The Delaunay tetrahedralization holds the
  *entire* dense cloud at once (stock ODM skips this step and never pays the cost).
  Measured: **53 GB peak at 17.9 M dense points** (82.4 M cells, 164.9 M faces),
  cleared in 51 s. That is roughly **3 GB per million dense points** — the one number
  to size against. Host-side even in the CUDA image; VRAM cannot substitute.
- **GPU VRAM → `DensifyPointCloud` only.** Measured peak: **1.2 GB of 16 GB** at full
  resolution, `number-views-fuse 2`, 12 MP. GPU utilisation hit 100 % during densify,
  so the *compute* is used — the *memory* is not. Scaling to 16 MP with
  `number-views-fuse 3` should stay in the low single-digit GB.
- **Disk → the toolchain, not the results.** Outputs are small: **1.7–6.6 GB per
  run** depending on settings. The Docker footprint is not: **37 GB of images plus
  70 GB of BuildKit cache** on this host. Budget for the tooling, and keep the
  transient churn (undistorted images, depth maps) on NVMe.

### Recommended configuration

| | Minimum | Recommended | Large sets without tiling |
|---|---|---|---|
| **CPU** | 8 cores, high clock | **16 real cores, ≥4 GHz boost** | 32 cores |
| **System RAM** | 64 GB | **128 GB** | **256 GB** |
| **GPU VRAM** | 8 GB | **12 GB** | 12 GB — more buys nothing measurable |
| **NVMe** (OS + Docker + scratch) | 512 GB | **1 TB** | 1 TB |
| **Bulk** (datasets + results) | — | **separate, ≥ 4 TB** | separate |

Priority order, from the measurements: **CPU > RAM > disk > GPU.**

**CPU wants both axes.** `RefineMesh` and matching parallelise well, so cores pay
off directly; but `TextureMesh`'s atlas packing looked largely serial — 33 min for
425 k patches in one experiment — so single-thread speed matters too. Disabling
E-cores on a hybrid Intel part is reasonable: the scheduler anomalies cost more than
the extra threads gain.

**What the RAM ceiling means in practice.** At ~3 GB per million dense points,
125 GB tops out near **40 M dense points ≈ 250 images at 12 MP, full resolution**.
Extrapolating 400 × 16 MP at full resolution gives ~87 M points ≈ 260 GB — beyond
that box. This is exactly what split-merge tiling (`--tiles`) exists for: **tiling is
the alternative to buying RAM**, and for large sets it is a design decision rather
than an escape hatch.

**Do not over-buy the GPU.** On this evidence a card with better compute-per-euro
beats a VRAM-heavy one: densify is 10 % of wall clock and used 7 % of a 16 GB card's
memory. The reason not to go below 8 GB is *unmeasured* future load, not today's
(see caveats).

### One node or two?

Because a single task is ~90 % CPU-bound and does not saturate a mid-range GPU,
**two mid-sized nodes may deliver more throughput than one large one** at equal
budget. Two WebODM processing nodes run two tasks genuinely in parallel; doubling one
machine does not halve a single task's wall clock, because `TextureMesh` does not
scale with cores. Worth costing out before buying a single big box.

### Caveats — what these numbers do not cover

- **One host, one GPU, one dataset family.** All figures come from a single machine
  and mostly one drone block. Absolute times will differ; the *shares* and the
  RAM-per-point relationship should travel.
- **Three unmeasured GPU consumers** could raise the VRAM floor: ALIKED/LightGlue
  ONNX inference (in the image since 2026-07 but not profiled), the planned
  fine-class semantic model, and py4dgeo/M3C2 change detection on large multi-epoch
  sets. Hence 8 GB as a floor despite a 1.2 GB measurement.
- **Nothing beyond 400 images or 16 MP was tested**, and the effect of `--tiles` on
  the RAM curve is unmeasured — the tiling ceiling above is an extrapolation.
- **RAM peak was read with `free`**, which includes page cache, so 53 GB is an upper
  bound on the true resident peak.

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

### Verifying a **GPU** build

The `which` gate above passes on the CPU image too — it says nothing about CUDA.
Two scripts supply the discriminating evidence.

```bash
./scripts/verify-gpu-image.sh effigies:gpu        # static, seconds, no dataset
./scripts/gpu-smoke-run.sh /path/to/images effigies:gpu   # end-to-end run
```

`verify-gpu-image.sh` asserts that `DensifyPointCloud` accepts `--cuda-device`
(the same probe `pipeline/dense_openmvs.sh` uses — a CPU build rejects it), that
the binaries link the CUDA runtime, and that the engine's own GPU probe
(`nvidia-smi -L`, `run.sh`) succeeds inside the container. It includes a
**negative control**: the probe must *fail* without `--gpus`, otherwise the
positive check proves nothing about passthrough.

`gpu-smoke-run.sh` drives `run.sh` directly (no NodeODM, no exposed port) on a
small image set and samples the device from the host while it runs. This matters
because **a successful run is not evidence of GPU use**: when the probe fails the
engine logs a warning and completes on CPU. The script therefore requires
positive evidence — engine processes resident on the device, non-zero utilisation,
and the absence of the fallback warning — and fails otherwise.

## Logs / troubleshooting

```bash
docker logs -f effigies-1            # NodeODM REST + task output
docker exec -it effigies-1 bash      # poke around the engine
```

If the node shows red in WebODM, it's almost always name/port/network: confirm the
container is on `webodm_default` (`docker inspect effigies-1 -f '{{json .NetworkSettings.Networks}}'`)
and that you used port `3000` (the in-container NodeODM port), not the published one.
