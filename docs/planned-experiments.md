# Planned engine experiments — deferred for the benchmark paper

Two single-variable experiments queued for the **v0.4.0 benchmark** (ROADMAP),
to be run later, not under time pressure. Both are measured against one shared
baseline run so each isolates exactly one parameter. They feed the paper's
*detail vs. watertightness* and *quality vs. runtime* arguments respectively.

## Shared baseline — GPU, run `gpu-base-01` (2026-07-25)

**The reference all deltas are measured against.** Supersedes the CPU/arm64 run
`8d2d31de` (kept below for its qualitative findings only — see the note there).

110 nadir drone images, DJI Phantom 3 (FC300X), 4000×3000 (12 MP), one camera
model. Selected from the Zionsberg 2015–2017 aerial set by gimbal pitch ≤ −80°
(the full set is 278 images: 111 nadir, 104 steep, 63 oblique, mean relative
altitude 27.9 m). **Purely nadir by construction**, which is exactly the premise
Experiment A rests on — no oblique observations of vertical surfaces exist in the
block, so absent walls are a property of the capture, not of the engine.

Host: RTX A4000 (GA104, sm_86, 16 GB), driver 595.84, i9-13900KS (8 P-cores /
16 threads), 125 GB RAM, Ubuntu 24.04.4. Image `effigies:gpu` built from
`CUDA_ARCH=86`, COLMAP 4.1.1 + OpenMVS v2.4.0.

**Settings:** `profile=drone-3d` (→ matcher `spatial`, mapper `incremental`),
`features=sift` (the default front-end — ALIKED would be a second variable),
`number-views-fuse=2`, `densify-resolution-level=0`, `free-space-support=true`,
`mesh-close-holes=30`, `refine-mesh-iters=3`, `refine-max-face-area=16`,
`refine-gradient-step=25.05`, `texture-resolution=8192`, `georeference=auto`.

**Sparse (COLMAP):** 110/110 images registered, 1 camera, 91 666 points,
567 995 observations, mean track length 6.20, **mean reprojection error 0.798 px**.

**Measured runtime — 1 h 00 min 41 s** (OpenMVS stages, 17:35:37 → 18:36:18):

| Stage | Duration | Share |
|---|---|---|
| DensifyPointCloud (res-0) → 17 881 137 points | 6 m 20 s | 10 % |
| ReconstructMesh → 10 526 203 verts / 21 017 255 faces | 3 m 55 s | 6 % |
| ├ Delaunay tetrahedralization (82.4 M cells, 164.9 M faces) | 51 s | |
| ├ tetrahedra weighting | 1 m 20 s | |
| └ graph-cut (flow 2.68e9) | ~1 m 36 s | |
| RefineMesh (scales 3) → 5 310 411 verts / 10 608 471 faces | **28 m 01 s** | **46 %** |
| TextureMesh (8192, 4 atlas pages, 232 716 patches) | 17 m 44 s | 29 % |
| └ best-view assignment | 7 m 53 s | |
| Post-processing (harmonise, blend, seams, ortho, DSM, LAZ, EPT, report) | ~4 m | 7 % |

RAM peak ≈ 53 GB of 125 GB — the `ReconstructMesh` Delaunay (82 M cells) is the
peak, as `docs/DEPLOYMENT.md` predicts, and it cleared in 51 s. GPU peak 100 %
during densify, ~1.2 GB VRAM of 16 GB: **VRAM was never the constraint at this
scale**, which is a data point for the sizing table's 16 GB row.

**Georeferencing:** `colmap-exif` (no GCP file), CRS `EPSG:32636` auto-derived,
scale 11.6196, residuals over 110 camera positions — RMS 3D **1.78 m**,
horizontal 0.78 m, vertical 1.60 m, max 3D 7.27 m. That is consumer-drone GPS
accuracy (Phantom 3, no RTK), so this baseline carries **no absolute-accuracy
claim**. It does not need one: both experiments measure *relative* quantities
(boundary edges, ortho nodata fraction, roof brightness std, runtime).

**Outputs:** orthophoto + DSM 2907 × 2887 px at **4.71 cm GSD**, 136.9 × 135.9 m;
textured model 2.2 GB; georeferenced cloud 71 MB.

> **Caveats, so later readings stay honest.** (1) 110 of the 111 nadir images —
> one filename could not be resolved during staging. (2) Run **without**
> `--keep-workdir`, so the pre-refinement mesh (`scene_dense_mesh.ply`) is not
> retained; the "do walls exist before RefineMesh?" check in Experiment A needs a
> targeted re-run. (3) Roof brightness std and boundary-edge counts are **not yet
> computed** for this baseline — they need `scripts/benchmark.sh stats` plus a roof
> crop against the delivered assets.

## Superseded baseline — run `8d2d31de` (2026-06-12, CPU/arm64)

**Do not use for deltas.** 70 drone images (~12 MP), nadir-dominant, CPU/arm64
(no GPU), canonical image `effigies:cpu` (`feada36f`). Every runtime figure below
is non-comparable to the GPU baseline above — different platform *and* a different
dataset — and COLMAP has since moved 4.0.4 → 4.1.1. Kept because its **qualitative**
findings still stand and motivate both experiments: the floating roof, the voids
under the eaves, and the free-space-support trade-off.

**Settings:** `profile=drone-3d` (matcher `spatial`, mapper `incremental`),
`number-views-fuse=2`, `densify-resolution-level=0`, `free-space-support=on`,
`mesh-close-holes=30`, `refine-mesh-iters=3`, `refine-max-face-area=16`,
`refine-gradient-step=25.05`, `texture-resolution=8192`, `cpu-threads=12`.

**Measured runtime ≈ 50 min:**

| Stage | Duration | Share |
|---|---|---|
| COLMAP sparse + undistort | ~8 min | 16 % |
| DensifyPointCloud (res-level 0, 15.5 M pts) | ~17 min | 34 % |
| ReconstructMesh (Delaunay + graph-cut) | ~7 min | 14 % |
| RefineMesh (scales 3, max-face-area 16, 4.0 M→8.0 M faces) | ~9 min | 18 % |
| TextureMesh (8192, 4 atlas pages) | ~2.5 min | 5 % |
| Harmonize + Blend + Seams + Exports | ~8 min | 16 % |

**Measured quality:** roof texture excellent — homogeneous, no patchiness
(roof brightness std ≈ 20.0 vs 23.9 single-view, same scene). **Walls absent:**
the main house roof "floats", large voids under the eaves (see the screenshot
discussion). `mesh-close-holes` was at its default `30` here, so only tiny gaps
were bridged. `free-space-support` recovered ≈ 20 m² of facade vs an earlier
run without it (367 → 387 m² of >70°-steep faces) but also raised boundary
edges +23 % and ortho nodata in the building core to ≈ 1.5 %.

---

## Experiment A — Watertightness (`mesh-close-holes`)

**Question:** Does aggressive hole-closing approach Metashape's "watertight"
look on nadir capture, and at what cost?

**Background (the paper's core finding):** Nadir imagery contains almost no
observations of vertical walls — *neither* engine can *measure* them. OpenMVS'
graph-cut (`ReconstructMesh`) carves away unsupported surface → honest holes.
Metashape (and, ironically, ODM's Screened-Poisson) produce a *closed* surface
by construction → "watertight", but the wall geometry is **interpolated, not
measured**, with roof/ground texture projected (smeared) onto it. This is the
genuine, publishable trade-off: **detail (graph-cut, open) vs. watertightness
(interpolation, closed)** — not a defect on either side. Note also that
`RefineMesh` ("remove unconnected vertices", 1.0 M → 0.82 M verts in the
baseline) appears to additionally carve marginal wall fragments — worth
checking whether walls exist in `scene_dense_mesh.ply` *before* refinement and
are removed by it (run with `--keep-workdir` to inspect).

**Delta from baseline (single variable):** `mesh-close-holes 30 → 500`
(consider a ladder 300 / 500 / 800 if 500 under-closes). Everything else
identical, including `free-space-support=on`.

**Measure:**
- Boundary edges (open-hole indicator) — **not yet measured on the GPU baseline**;
  the CPU run's 6 777 belongs to a different dataset and cannot be the reference.
- Ortho nodata in the building core — **not yet measured on the GPU baseline**
  (CPU run: ≈ 1.5 %, different dataset). Expect → near 0 after close-holes.
- Visual wall closure + honest assessment of texture smearing on the bridged
  "skirts" (the interpolated geometry carries no real wall texture).
- Runtime impact (close-holes is cheap; expect negligible).

**Expected:** Closer to the Metashape *look* (voids filled), but the bridged
walls are interpolated + texture-smeared, not reconstructed. For *scientific*
documentation this is a fabrication-vs-honest-hole choice to state explicitly,
not a quality win to claim silently.

**Real walls** would require oblique imagery in the capture set — no algorithm
recovers faithful walls from nadir-only. If the set contains obliques, verify
they registered (camera pitch distribution from the COLMAP poses).

---

## Experiment B — Densify resolution vs. runtime (`densify-resolution-level`)

**Question:** How much runtime drops at `densify-resolution-level 1`, and
whether roof / final detail holds.

**Background:** Densify at `resolution-level 0` (full res) is the single most
expensive stage (~17 min, 15.5 M points) and it **cascades**: more dense points
→ larger Delaunay (ReconstructMesh) → more faces for RefineMesh → larger atlas
for Texture/Blend/Seams. Level 1 (¼ the pixels) makes densify ≈ 4× faster *and*
shrinks the entire back half. Crucially, `RefineMesh` is **photometric** — it
recovers geometric detail from the source images largely independent of densify
density — so final detail should be near-unchanged. Since walls are not
recoverable from nadir regardless, res-0 mainly buys roof/ground point density
that RefineMesh re-supplies anyway.

**Delta from baseline (single variable):** `densify-resolution-level 0 → 1`
(this is the `drone-3d` profile's own default; the baseline overrode it to 0).
Everything else identical.

**Measure:**
- Per-stage timings + total. GPU baseline: **1 h 00 min 41 s**, of which RefineMesh
  is 46 % and TextureMesh 29 % — densify itself is only **10 %**. So the saving
  from res-1 must come almost entirely from the *cascade* into those two stages,
  not from densify. That reframes the experiment: at res-0 densify is not the
  bottleneck the CPU run suggested (34 % there), and the expected ≈ 40 % total
  saving should be re-derived rather than carried over.
- Roof brightness std — **not yet measured on the GPU baseline** (CPU run: ≈ 20.0,
  different dataset). Expect ≈ unchanged under a densify-resolution delta.
- Dense point count, final mesh face count, visual detail on roof/ground —
  expect modest point-count drop, near-identical refined detail.

**Expected:** ~40 % less wall-clock at effectively unchanged final quality —
i.e. res-0 is mostly wasted cost here. If confirmed, `drone-3d`'s default
(level 1) is the right production setting and res-0 is a niche max-density
option.

---

## Notes

- Both are **one-variable** tests vs the GPU baseline `gpu-base-01`; do not combine parameters
  in a single run or the attribution is lost. A later **combined** run
  (`densify-level 1` + `close-holes 500`) is worth doing once each is
  understood, as the likely production profile.
- Capture per-stage timings (`task_output.txt`), the mesh stats, and the roof
  crop for each — the same quantities the baseline above records — so the paper
  tables are populated directly.
- These belong to ROADMAP **v0.4.0** (Quality profiles & tuning / Benchmark
  suite); calibrating the `drone-3d` bundle against Experiment B is exactly the
  open "calibrate the bundles per profile against benchmark runs" item.
