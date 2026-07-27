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

### RESULT — run `expA-closeholes500` (2026-07-25)

Single variable vs `gpu-base-01`: `mesh-close-holes 30 → 500`. Metrics from
`scripts/experiment_metrics.py`, identical code for both runs.

| Measure | baseline | close-holes 500 | Δ |
|---|---|---|---|
| **Boundary edges** | 12 147 | **5 590** | **−54.0 %** |
| **Interior ortho nodata** | 0.746 % | **0.176 %** | **−76.4 %** |
| Interior hole count | 28 | **7** | −75 % |
| Faces | 10 608 471 | 10 640 998 | +0.3 % |
| Non-manifold edges | 0 | 0 | — |
| **Total runtime** | 1 h 00 m 41 s | **1 h 00 m 45 s** | **+0.1 %** |
| └ ReconstructMesh | 3 m 55 s | 3 m 52 s | −1.3 % |

**Confirmed, and it is free.** Aggressive hole-closing halves the open-edge count
and removes three quarters of the interior ortho holes at **no runtime cost** —
the +4 s total is far inside run-to-run variation. The prediction that
"close-holes is cheap" holds precisely.

**It does not close everything**: 5 590 boundary edges remain. That is the
expected shape of the result rather than an under-setting to fix by raising the
ladder to 800 — `close-holes` bridges holes up to a size threshold, and what
survives on a purely nadir block are the *large* wall voids, which are exactly the
surfaces the capture never observed. Raising the threshold further would start
fabricating those walls wholesale, which is the trade-off this experiment exists
to make explicit, not a knob to max out.

**Caveat carried from the baseline:** the +0.3 % face count and the −1.3 %
ReconstructMesh time are **below the noise floor** (see the note below) and carry
no meaning. The two headline effects (−54 %, −76 %) are far above it.

> **Noise floor, measured for free.** `close-holes` cannot affect anything before
> `ReconstructMesh`, so baseline↔A up to that point is a repeat run at identical
> settings. It is not bit-identical: sparse points 91 666 → 91 092 (−0.63 %),
> dense points 17 881 137 → 17 606 192 (−1.54 %), mean reprojection error 0.7979 →
> 0.7987 px. COLMAP's mapper (RANSAC seeds) and OpenMVS' parallel CUDA PatchMatch
> are both non-deterministic. **Treat any delta below ~1.5 % as noise.** A dedicated
> repeat run would tighten this estimate; this one came at zero cost.

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

### RESULT — run `expB-densify1` (2026-07-25)

Single variable vs `gpu-base-01`: `densify-resolution-level 0 → 1`.

| Stage | baseline | res-1 | Δ |
|---|---|---|---|
| DensifyPointCloud | 6 m 20 s | 3 m 52 s | −39 % |
| ReconstructMesh | 3 m 55 s | 2 m 43 s | −31 % |
| RefineMesh | 28 m 01 s | **6 m 38 s** | **−76 %** |
| TextureMesh | 17 m 44 s | **3 m 36 s** | **−80 %** |
| **Total** | **1 h 00 m 41 s** | **19 m 38 s** | **−67.6 %** |

| Output | baseline | res-1 | Δ |
|---|---|---|---|
| Dense points | 17 881 137 | 11 522 519 | −35.6 % |
| Faces after ReconstructMesh | 21 017 255 | 14 102 455 | −32.9 % |
| **Faces after RefineMesh** | 10 608 471 | **2 283 164** | **−78.5 %** |
| Textured OBJ | 1.39 GB | 288 MB | −79 % |
| Interior ortho nodata | 0.746 % | 0.732 % | −1.9 % (noise) |
| Ortho GSD | 4.71 cm | 4.69 cm | unchanged |

**Runtime: confirmed, and then some** — −67.6 %, not the predicted ~40 %. The
saving comes overwhelmingly from the cascade, exactly as the GPU baseline's stage
shares implied: RefineMesh and TextureMesh together drop from 45 m 45 s to
10 m 14 s. Densify itself contributes only 2 m 28 s of the 41 m saved.

**Quality: the prediction is contradicted.** The doc argued that "RefineMesh is
photometric — it recovers geometric detail from the source images largely
independent of densify density — so final detail should be near-unchanged". The
refined mesh has **78.5 % fewer faces**. RefineMesh did *not* re-supply the
detail; its output scales with what ReconstructMesh handed it.

**The likely mechanism, and why this is not a clean single-variable test.**
`refine-max-face-area 16` is a threshold on face area *projected into the images*,
i.e. in **pixels**. At res-1 the images are half-resolution, so the same geometric
face projects to a quarter of the pixel area, far fewer faces exceed 16 px², and
subdivision largely stops. Note the ratios: baseline 21.0 M → 10.6 M faces
(÷1.98), res-1 14.1 M → 2.3 M (÷6.18). So `densify-resolution-level` and
`refine-max-face-area` are **coupled**, and changing the first silently changes the
effective refinement target. This experiment therefore measured
*densify-resolution **and** a 4× coarser refinement target* together.

**Follow-up required before any profile decision:** re-run at
`densify-resolution-level 1` with `refine-max-face-area 4` (16 ÷ 4, compensating
the pixel-area rescale) to hold the refinement target constant. Only that run
answers the question this experiment was meant to ask. Until then, **do not**
promote res-1 as "same quality, ~40 % cheaper" — on this evidence it is
"much coarser, 68 % cheaper", and part of the coarsening is an artefact of the
coupling rather than of densify density.

**Boundary-edge counts are not comparable here.** Raw counts fell 12 147 → 4 110
(−66 %), but the mesh also lost 78.5 % of its faces. As a *fraction* of all edges,
openness went **up**: 0.0763 % → 0.1199 % (+57 %). Absolute counts are only
comparable between runs of similar mesh size — as in Experiment A, where the face
count matched to 0.3 %.

**Coverage is genuinely unaffected**: interior ortho nodata 0.746 % → 0.732 % and
the GSD is unchanged, both inside the noise floor. Densify resolution does not
change *where* the reconstruction has data, only how finely it is modelled.

### RESULT — compensation run `expC-densify1-mfa4` (2026-07-25)

`densify-resolution-level 1` **+** `refine-max-face-area 16 → 4`, compensating the
pixel-area rescale so the refinement target stays geometrically constant. This is
the run that isolates densify resolution. All three side by side:

| | baseline<br>res-0 / mfa-16 | B<br>res-1 / mfa-16 | C<br>res-1 / mfa-4 |
|---|---|---|---|
| **Total** | **1 h 00 m 41 s** | **19 m 38 s** (−67.6 %) | **54 m 52 s** (−9.6 %) |
| DensifyPointCloud | 6 m 20 s | 3 m 52 s | 3 m 51 s |
| ReconstructMesh | 3 m 55 s | 2 m 43 s | 2 m 38 s |
| RefineMesh | 28 m 01 s | 6 m 38 s | 7 m 40 s |
| TextureMesh | 17 m 44 s | 3 m 36 s | **37 m 22 s** |
| Refined faces | 10 608 471 | 2 283 164 | 6 574 854 |
| Texture patches | 232 716 | 89 842 | **425 527** |
| Textured OBJ | 1.39 GB | 288 MB | 855 MB |
| Open-edge fraction | 0.0763 % | 0.1199 % | 0.0590 % |
| Interior ortho nodata | 0.746 % | 0.732 % | 0.645 % |

**1. There is no free lunch, and the doc's premise was wrong.** Compensating the
threshold restored faces from 2.28 M to 6.57 M — but not to the baseline's
10.61 M. With the refinement target held geometrically constant, res-1 still
yields **38 % fewer faces**. So densify density *does* contribute to final mesh
detail; RefineMesh does **not** recover it "largely independent of densify
density". Experiment B's collapse was *mostly*, but not wholly, the coupling.

**2. The runtime saving nearly evaporates: −67.6 % → −9.6 %.** B's headline was
bought by coarser refinement, not by cheaper densify.

**3. And the residual saving is eaten by a texture-stage blow-up.** The front half
(densify + reconstruct + refine) does drop hard, 38 m 16 s → 14 m 09 s (−63 %),
but TextureMesh **more than doubles** against the baseline, 17 m 44 s → 37 m 22 s.
The cause is patch count, not face count: C has 62 % of the baseline's faces but
**1.83× its texture patches** (425 527 vs 232 716), and atlas packing scales
superlinearly — 3.4× the time for 1.83× the patches. A patch is a run of adjacent
faces sharing the same best view; a finely subdivided mesh on *half-resolution*
imagery fragments view selection badly. **`res-1` + `mfa-4` is the worst of the
three combinations** — it pays full price for texturing and still delivers 38 %
fewer faces.

**4. Geometry quality otherwise holds up.** C is proportionally the *least* open of
the three (open-edge fraction 0.0590 % vs the baseline's 0.0763 %), and interior
ortho nodata 0.645 % is at or below the baseline. Coverage and watertightness are
not what res-1 costs; face density is.

**Conclusion for profile calibration.** The `drone-3d` default (`res-1`,
`mfa-16` = run B) is genuinely fast and genuinely coarser — a defensible
production default, but it must be described that way, not as "same quality,
cheaper". Compensating through `refine-max-face-area` is **counterproductive**:
it recovers only part of the detail and triggers the patch explosion. If baseline
detail is wanted, res-0 is what buys it, and the 28 m of RefineMesh against
full-resolution imagery is the price of the photometric detail this node exists
for — not waste.

### RESULT — `expD-densify1-mfa8` (2026-07-26): the knee, and a wrong prediction

After run C we expected no useful intermediate, on the grounds that C's patch-count
curve made texture cost rise faster than detail. **That expectation was wrong.**
`mfa-8` at res-1 is a genuine sweet spot. Four points, one variable pair:

| | baseline<br>res-0 / mfa-16 | B<br>res-1 / mfa-16 | **D<br>res-1 / mfa-8** | C<br>res-1 / mfa-4 |
|---|---|---|---|---|
| **Total** | 1 h 00 m 41 s | 19 m 38 s | **27 m 35 s** | 54 m 52 s |
| vs baseline | — | −67.6 % | **−54.6 %** | −9.6 % |
| DensifyPointCloud | 6 m 20 s | 3 m 52 s | 3 m 49 s | 3 m 51 s |
| ReconstructMesh | 3 m 55 s | 2 m 43 s | 2 m 39 s | 2 m 38 s |
| RefineMesh | 28 m 01 s | 6 m 38 s | 6 m 58 s | 7 m 40 s |
| TextureMesh | 17 m 44 s | 3 m 36 s | **11 m 10 s** | 37 m 22 s |
| └ atlas generation | 9 m 51 s | 1 m 37 s | 7 m 51 s | 33 m 17 s |
| Refined faces | 10 608 471 | 2 283 164 | **3 876 353** | 6 574 854 |
| Texture patches (atlas) | 232 716 | 89 842 | 212 576 | 425 527 |
| Patches per face | 0.0219 | 0.0393 | 0.0548 | 0.0647 |
| Textured OBJ | 1.39 GB | 288 MB | 497 MB | 855 MB |
| Open-edge fraction | 0.0763 % | 0.1199 % | 0.0866 % | 0.0590 % |
| Interior ortho nodata | 0.746 % | 0.732 % | **0.613 %** | 0.645 % |

**The knee is at `mfa-8`.** The marginal cost of face density roughly doubles across
it:

| step | faces | wall clock |
|---|---|---|
| B → D (`mfa` 16→8) | **+69.8 %** | +40.5 % |
| D → C (`mfa` 8→4) | +69.6 % | **+98.9 %** |

Identical face-density gain, at 2.4× the time cost on the second step. `mfa-8` buys
70 % more geometry than the `drone-3d` default for 40 % more runtime; going one
step further buys the same again for a doubling. That is the quality/cost knee the
v0.8.0 profile-calibration item asks for, located.

**The fragmentation mechanism was right, the conclusion drawn from it was not.**
Patches per face does climb monotonically as subdivision gets finer
(0.0219 → 0.0393 → 0.0548 → 0.0647), and atlas cost is superlinear in patches —
fitting the three res-1 points gives an exponent of ≈ 1.8 (B→D) to ≈ 2.1 (D→C).
But at `mfa-8` the *absolute* patch count (212 576) is still below the baseline's
(232 716), so the atlas stage costs 7 m 51 s — **less than the baseline's 9 m 51 s**.
The explosion at `mfa-4` is real but starts beyond this point; D sits on the cheap
side of it. Reasoning from the curve's shape alone, without the middle
measurement, produced the wrong call.

**Coverage is best here, not worst.** D has the **lowest interior ortho nodata of
all four runs** (0.613 %) and an open-edge fraction between the baseline and the
default. Nothing about the intermediate setting degrades watertightness or coverage.

### Recommendation for the `drone-3d` profile

Change `refine-max-face-area` from **16 to 8**, keeping
`densify-resolution-level 1`. Measured against the current default: +70 % refined
faces, +40 % runtime (19 m 38 s → 27 m 35 s), lower interior ortho nodata, and an
atlas stage still cheaper than the full-resolution baseline's. `res-0` remains the
max-detail option — its 28 m of RefineMesh against full-resolution imagery is the
price of the photometric detail this node exists for, and 2.7× the faces of D.
Do **not** go to `mfa-4`: same detail step, double the cost, texture-atlas blow-up.

> One caveat on all four runs: they share a single dataset (110 nadir images, one
> site) and each setting was run once. The face-density and runtime effects are far
> above the ~1.5 % noise floor, but the *location* of the knee may shift with scene
> content and image count. Before baking this into the shipped profile, it is worth
> one confirmation on a second drone block.
> **→ That confirmation was run 2026-07-27; see the next section. The knee holds;
> one sub-claim of this section does not.**

---

## Knee confirmation — block 2, run `block2-mfa{16,8,4}` (2026-07-27)

The confirmation the caveat above asks for. **`refine-max-face-area` is the only
variable**; every other setting is the `gpu-base-01` recipe at
`densify-resolution-level 1`, i.e. the res-1 family B/D/C.

### The second block, and what it does *not* control for

**Tiberias 20230309** — 382 images, **Parrot Anafi**, 4608×3456 (16 MP), one camera
model, 379 registered. Selected from a 400-image flight by
`drone-parrot:CameraPitchDegree ≤ −60°`; the 18 discarded frames sit at ~0°
(take-off / landing). Same pitch-filter methodology as block 1.

**This is a replication, not a controlled one.** Against block 1 the following change
*simultaneously*: site, camera, sensor format, image count (110 → 382) and — the one
that matters most — **capture geometry**. Block 1 was *purely nadir by construction*
(pitch ≤ −80°). This block has **zero nadir images**: median pitch −70.0°, flown at a
fixed gimbal angle. There is no second nadir drone block in the available data, so a
tighter control was not on offer.

Consequence for reading the result: a knee that *stays* at `mfa-8` across two such
different blocks is a **strong** result. A knee that had *moved* would have been
ambiguous — no single cause could have been assigned.

### Result

| | mfa-16 | **mfa-8** | mfa-4 |
|---|---|---|---|
| DensifyPointCloud (res-1) | 49 821 451 pts / 18 m 06 | 50 192 336 pts / 17 m 40 | 49 602 660 pts / 17 m 11 |
| ReconstructMesh | 43 828 511 faces / 9 m 44 | 43 610 362 faces / 9 m 44 | 43 598 179 faces / 9 m 28 |
| RefineMesh | 6 071 497 faces / 27 m 04 | **11 415 736 faces / 28 m 20** | 21 952 834 faces / 31 m 45 |
| TextureMesh | 10 m 45 | 26 m 41 | **2 h 01 m 16** |
| └ atlas generation | 3 m 52 | 13 m 54 | **1 h 36 m 48** |
| **OpenMVS span** | **1 h 15 m 30** | **1 h 32 m 41** | **3 h 10 m 34** |
| Wall clock (whole run) | 2 h 08 m 32 | 2 h 37 m 59 | 4 h 39 m 51 |
| Texture patches (atlas) | 93 497 | 218 045 | 589 221 |
| Patches per face | 0.01540 | 0.01910 | 0.02684 |
| Open-edge fraction | 0.1332 % | 0.0994 % | 0.0596 % |
| Non-manifold edges | 0 | 0 | 0 |
| Interior ortho nodata | 1.990 % | 1.966 % | 1.956 % |
| Textured OBJ | 751 MB | 1.4 GB | 2.7 GB |
| RAM peak (VmPeak) | 38.9 GB | 51.6 GB | 73.9 GB |

**Two built-in controls pass.** Densify points (49.60–50.19 M, spread 1.2 %) and
ReconstructMesh faces (43.60–43.83 M, spread 0.5 %) are flat across the three runs —
which is exactly required, since `refine-max-face-area` first acts in RefineMesh. Both
spreads sit under the measured ~1.5 % noise floor, so the runs really are
single-variable.

### The knee holds, and is sharper here

| step | faces | OpenMVS time | time per unit face gain | block 1 |
|---|---|---|---|---|
| 16 → 8 | +88.0 % | **+22.8 %** | **0.26** | 0.58 |
| 8 → 4 | +92.3 % | **+105.6 %** | **1.14** | 1.42 |

Both blocks: the second step buys the same face-density gain as the first at a
multiple of the cost — **2.4× on block 1, 4.4× on block 2**. On this block `mfa-8` is
the better bargain of the two datasets: +88 % geometry for +23 % runtime. The
fragmentation mechanism replicates too — patches per face climbs monotonically and
atlas cost is superlinear in patches (fitted exponent ≈ 1.5 for 16→8, ≈ 2.0 for 8→4,
against ≈ 1.8–2.1 on block 1).

### One sub-claim of run D is refuted

Run D reported **"Coverage is best here, not worst"** — `mfa-8` had the lowest
interior ortho nodata of all four block-1 runs (0.613 %), better than both the
baseline and its neighbours. **That does not replicate.** On block 2 the metric is
flat: 1.990 / 1.966 / 1.956 % across a 3.6× range of face counts — a spread of 0.03
percentage points, which is noise, not an effect. So `mfa-8` is *not* a coverage
optimum; block 1's dip was a dataset artefact. The recommendation survives on runtime
and geometry, but the coverage argument is withdrawn.

The higher absolute level (≈ 1.97 % vs 0.61 %) is expected and not a defect: an
obliquely flown strip covering ~50 % of its raster rectangle has more genuine holes
than a compact nadir block. Open-edge fraction does fall monotonically
(0.1332 → 0.0994 → 0.0596 %), as on block 1 — finer subdivision closes rims rather
than opening them — and no run produced a single non-manifold edge.

### Status of the profile gate

The `v0.8.0` gate *"worth one confirmation on a second drone block"* is **met**:
`refine-max-face-area 16 → 8` for `drone-3d` is confirmed on two drone blocks that
share almost nothing but being drone blocks. The shipped default is **not yet
changed** — that is a user-facing profile change and its own decision.

Caveat that remains: each setting was still run **once per block**, and both blocks
are Southern-Levant archaeology at 12–16 MP. Nothing here speaks to other scene types.

---

## Metashape comparison — block 2, run `cmp-effigies` (2026-07-27)

First measurement against the v0.8.0 *Comparison runs* item. Same 382 images, both
sides producing the **full product set** (sparse, dense, mesh, texture, DEM/DSM,
orthophoto) at a **pinned, comparable orthophoto GSD**.

### Two false starts worth recording

The first attempt was invalid in ways that were not obvious:

1. **The Metashape run built no orthophoto and no DEM.** Its stages stopped at
   `buildTexture`. Comparing its 48 m 30 against an Effigies run that also emits
   ortho, DSM, LAZ, EPT, glTF and a report was not a comparison at all.
2. **The Effigies orthophoto was at 3.1 cm/px** — 1/28 of the pixel count the imagery
   supports (see the `auto`-GSD fix in the CHANGELOG). The one product both sides
   shared was a thumbnail on our side.

Both are fixed here: Metashape's `buildDem` and `buildOrthomosaic` were added and
**both pinned to 0.588 cm/px** (Effigies' own estimate for this block), and the
Effigies run used the data-driven GSD.

### Configuration

| | Effigies | Metashape 2.3.1 |
|---|---|---|
| Host | RTX A4000, i9-13900KS (8 P-cores), 125 GB | Apple M3 Max, 69 GB |
| Sparse | COLMAP 4.1.1, SIFT, `spatial`, **GLOMAP** | `matchPhotos(downscale=1)` + `alignCameras` |
| Dense | OpenMVS, `densify-resolution-level 1` | `buildDepthMaps(downscale=2)` + `buildPointCloud` |
| Mesh | ReconstructMesh + **RefineMesh** `mfa-8` | `buildModel(DepthMapsData, HighFaceCount)` |
| Texture | TextureMesh 8192 + **multi-view blend** | `buildUV` + `buildTexture(Mosaic, 8192)` |

`densify-resolution-level 1` and `downscale=2` both mean half image resolution — that
equivalence is what makes the dense stage comparable at all.

### Result

| Stage | Effigies | Metashape | |
|---|---|---|---|
| Sparse | 9 m 34 | **3 m 12** | 3.0x slower |
| Dense | **17 m 24** · 51.3 M pts | 32 m 12 · 83.1 M pts | 12 % faster *per point* |
| Mesh | 9 m 33 · 44.5 M faces | **7 m 42** · 13.9 M faces | 1.2x slower |
| **RefineMesh** | **28 m 41** → 11.3 M faces | — | no counterpart |
| Texture | 26 m 10 | **5 m 18** | 4.9x slower |
| **`texture_blend`** | **42 m 24** | — | no counterpart |
| Ortho + DEM/DSM | **5 m 59** · 238 Mpx | 7 m 36 · 290 Mpx | **parity** |
| LAZ, EPT, glTF, report | ~7 m | — | no counterpart |
| **Total** | **2 h 37 m 30** | **56 m 06** | **2.8x** |

### Where the gap actually is

**71 of our 157 minutes are stages Metashape does not run at all** — RefineMesh
(28 m 41) and the multi-view texel blend (42 m 24). That is *more than Metashape's
entire run*. Subtracting them leaves **86 min vs 56 min, a factor of 1.5**.

Within the comparable stages the picture is mixed, not uniformly bad:

- **Sparse (3.0x slower)** is the one clear structural deficit, and it is not the
  matching — `spatial_matcher` takes 1 m 12 against Metashape's 2 m 07 for detection
  *and* matching. GLOMAP already cut the mapper from 7 m 57 to 2 m 59; the remaining
  gap is largely `image_undistorter` (3 m 15), which Metashape needs no equivalent of.
- **Dense: we are ahead per unit of output.** Metashape produced 62 % more points, so
  raw wall clock understates us; normalised, 20.4 s/Mpt against 23.3.
- **Texture (4.9x slower)** is OpenMVS' atlas packing — 13 m 20 for 215 106 patches.
  This is the second real deficit and it compounds with `mfa-8`, which raises patch
  count by design.
- **Orthophoto: parity.** 238 Mpx in 5 m 59 against 290 Mpx in 6 m 06. The stage that
  was expected to be our worst, after the resolution fix multiplied its work by 28.9,
  turned out to be the one where we match. Predicted 15–30 min; measured 5 m 59.

### What Metashape does not give you

Effigies' DSM is the **z-buffer of the orthophoto's own rasterisation**, so the two are
byte-identical in grid: same size, same origin to the last decimal. Metashape builds
its DEM from the point cloud and its ortho from the model; even *pinned to the same
GSD* they came out 18319x15874 vs 18265x15882 with a 28 mm origin offset — about five
pixels apart. Overlaying them requires resampling; ours do not.

Metashape's DEM default is also **4x coarser than its ortho** (19.893 vs 4.973 mm on
the reference project), and the factor is exactly its `BuildDepthMaps/downscale = 4`:
the DEM inherits the point-cloud spacing while the ortho inherits the image
resolution. That is honest on Metashape's part — it does not interpolate a DEM finer
than the depth maps support — but it means DEM-at-ortho-resolution costs a 16x more
expensive depth-map pass there, and is free here.

### Quality comparison — run, and it did NOT answer the question

Both results were brought into EPSG:6991 (Metashape's exports reprojected with proj;
Metashape itself refuses WGS 84 → Israeli Grid, "Unsupported datum transformation"),
the Effigies OBJ given its offset back so the two are co-located, then
`benchmark.sh stats` on all four products and `benchmark.sh compare` mesh-to-mesh.

**Surface roughness** (local plane residual over k=16 neighbours, 50 000 sample
points):

| | elements | mean | rms | p95 |
|---|---|---|---|---|
| Effigies mesh | 11 283 119 faces | **0.47 cm** | 0.69 cm | 1.49 cm |
| Metashape mesh | 13 926 780 faces | 0.57 cm | 0.80 cm | 1.62 cm |
| Effigies cloud | 51 282 498 pts | **4.15 cm** | 7.54 cm | 14.49 cm |
| Metashape cloud | 83 063 231 pts | 8.47 cm | 12.32 cm | 23.55 cm |

**Mesh-to-mesh distance** (ICP-aligned, converged, 1 M points sampled per side):
mean **9.96 cm**, rms 11.46 cm, p95 20.96 cm, max 1.27 m; completeness 38.6 % within
an auto threshold of 9.18 cm.

#### Why that mean is not an answer

The question was whether RefineMesh yields measurably more surface detail. A 10 cm
mean on a reconstruction with 0.58 cm GSD measures something else.

1. **The measurement cannot resolve the effect.** `compare` decimates both sides to
   1 M points; the reference's median nearest-neighbour spacing is then **4.59 cm**.
   Two independent samples of the *same* surface already sit ~2 cm apart at that
   density. Sub-centimetre detail — exactly what RefineMesh produces — is below the
   floor of this measurement. The `--sample 1e6` default is too coarse for this
   question, not wrong in general.
2. **Vegetation almost certainly dominates the mean.** Large parts of the block are
   overgrown (visible in the orthophoto), and two reconstructions of moving foliage
   differ by decimetres — consistent with the 1.27 m maximum, the long tail, and
   cloud roughness of 4–8 cm, far above what a stone surface produces. The mean mixes
   the excavation surface, where the answer matters, with the scrub, where it does
   not, and the scrub wins.
3. **Completeness inherits the same problem**: its 9.18 cm threshold is derived from
   the sample spacing, not from a meaningful tolerance.

What would answer it: sampling 20–50 M points instead of 1 M, and splitting the
distance **by terrain class**. The tool for the second part already exists — the
semantic raster separates `ground` / `vegetation` / `structure`, so the distance can
be computed on stone surfaces alone. That is the shape the v0.8.0 quality gate should
take; the aggregate number should not be quoted as an accuracy figure.

Not obtained: the cloud-to-cloud comparison. Its ICP ran **>40 minutes single-core**
without converging (against ~7 minutes for the mesh pair) and was cut off when access
to the host ended. That `filters.icp` is serial and can behave this badly on a noisy
pair is itself worth knowing before planning a benchmark campaign around it.

### Caveats — read before quoting any of this

- **Different hardware.** Directions are meaningful, magnitudes indicative.
- **The dense stages did not do the same amount of work** (83.1 M vs 51.3 M points).
  Only the per-point figure is comparable.
- **Effigies' configuration is the *candidate* bundle** (GLOMAP + `mfa-8`), not
  today's shipped default.
- **Our GSD estimate reads ~17 % high** against Metashape's 0.497 cm/px, so the ortho
  rasters are not at identical resolution (0.582 vs 0.588 pinned).
- **One run each.** No repetition, no second block, no quality comparison — this
  measures *runtime and products*, not accuracy. Cloud-to-cloud and mesh-to-mesh
  distance against these same outputs is the next step, and the absolute-accuracy half
  still waits on a TLS scan.

---

## Notes

- **Both experiments are RUN (2026-07-25).** Results are recorded inline above.
  Headline: A confirmed and free; B's runtime saving confirmed and larger than
  predicted, but its *quality* premise was contradicted and the test turned out
  not to be single-variable (`refine-max-face-area` is pixel-based and couples to
  densify resolution). The queued follow-up is the res-1 + `max-face-area 4` run.
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
