# Roadmap

This roadmap is intentionally honest about what is *done*, what is *scaffolded*,
and what is *not built yet*. Versions follow [SemVer](https://semver.org/); dates
are targets, not promises. Items move to [CHANGELOG.md](CHANGELOG.md) as they ship.

## Guiding principles

- **The refine step is the point.** `ReconstructMesh` / `RefineMesh` must never be
  silently dropped — that is the entire reason this node exists over stock ODM.
- **Keep the NodeODM contract intact.** Engine call, options advertising and the
  WebODM output paths stay compatible with upstream.
- **Region-agnostic by default.** `crs=auto` and `georeference=none` must work for
  any dataset, anywhere, with no GPS/GCP required.
- **No fabricated behaviour.** A step that cannot run fails loudly or falls back as
  documented — never an identity transform claiming success.

---

## v0.1.0 — Scaffold *(released)*

Working NodeODM-compatible engine: COLMAP sparse → full OpenMVS chain → georef
bridge → WebODM asset mapping, with unit tests and CI. See the changelog.

## v0.2.0 — Reproducible & verifiable build *(released — 2026-06-11)*

Made the image trustworthy and the output cloud web-ready.

- [x] **Source-built, pinned Dockerfiles.** Both images build COLMAP `4.0.4` and
      OpenMVS `v2.4.0` from source (versions as build `ARG`s) from identical pinned
      sources — the production `Dockerfile` differs from `Dockerfile.cpu` only in
      the CUDA base and the `-D*CUDA*` flags. A build-time gate
      (`which colmap DensifyPointCloud ReconstructMesh RefineMesh TextureMesh
      InterfaceCOLMAP pdal`) fails the build loudly if any binary is missing.
- [x] **No `latest` tags.** Base image and engine versions are explicit `ARG`s;
      VCGlib pinned to the validated commit (`658ba36`).
- [x] **Point cloud → `.laz` + EPT.** `helpers/pointcloud_to_laz.py` applies the
      georef transform, writes `odm_georeferenced_model.laz` via PDAL, and builds
      an EPT tileset (entwine/untwine) for the Potree viewer.
- [x] **`matcher=vocab_tree` + `mapper=global`.** Working image-retrieval matching
      (baked-in FAISS vocab tree) and the built-in GLOMAP global mapper as opt-in
      COLMAP-4 choices, never the default.
- [x] **End-to-end run (CPU image).** The full chain — COLMAP sparse →
      `image_undistorter` → OpenMVS densify → reconstruct → refine → texture →
      georef → LAZ — runs to completion on the CPU/arm64 image against a real
      70-image dataset, producing a textured georeferenced OBJ + LAZ. Getting there
      fixed a chain of CPU-path bugs (GPU fallback, SIFT thread/match-block caps,
      the undistort workspace, the `--cuda-device` probe).

**Carried forward** (v0.2.0 shipped without these; tracked for a later release):

- [x] ~~OpenMVS bump to fix seam leveling~~ — **tested and rejected**: master
      (incl. the 2026-02 "global seam leveling corner case" fix) corrupts texture
      patches exactly like v2.4.0 on this arm64/CPU build (24–47% near-black atlas
      vs 0.4–2.9% with leveling off, same scene). Colour consistency stays our
      job: per-image exposure harmonisation (`harmonize_exposure.py`).
- [ ] **Drop the NodeODM `shQuote` hotfix** once upstream fixes the PR #268
      regression (numeric option values crash `s.replace()`); then bump
      `NODEODM_REF` to the fixed commit and remove the build-time sed. The repo
      policy stays "no NodeODM patches" — this is a pinned, documented exception
      for an upstream crash.
- [ ] Verify `InterfaceCOLMAP` / `InterfaceOpenSfM` binary names across OpenMVS
      builds and handle the variants.
- [ ] Slim the image with a multi-stage (devel build → runtime copy) layout.

## COLMAP 4 migration *(done — folded into v0.2.0)*

Both images are now on **COLMAP 4.0.4 + OpenMVS 2.4.0**, built from identical pinned
sources and run-verified end-to-end on the CPU image. The originally-planned
three-step sequence (24.04 base → GPU OpenMVS 2.4.0 → COLMAP 3.13 → 4.0.x) collapsed:
**the base bump to 24.04 was not blocking** — COLMAP 4 already built on Ubuntu 22.04 once
`libopenimageio-dev openimageio-tools libsuitesparse-dev` are added, so **PDAL is
kept** (24.04 dropped it). Facts worth keeping:

- **InterfaceCOLMAP ↔ COLMAP 4 is byte-compatible.** COLMAP 4's rig/frame refactor
  is additive — `rigs.bin` / `frames.bin` are new files `InterfaceCOLMAP` ignores;
  `images.bin` is byte-identical (pose stays per-image as `cam_from_world`). New
  camera models never reach OpenMVS because `image_undistorter` converts to PINHOLE
  first. Source- and run-checked.
- **Generic CLI options renamed** in COLMAP 3.13: `SiftExtraction/SiftMatching.*`
  → `Feature{Extraction,Matching}.*` (the SIFT-*algorithm* options keep `Sift*`).
  `sparse_colmap.sh` probes the binary and falls back to the legacy names, so a
  pre-4 `COLMAP_VERSION` override still works.
- **arm64 CPU matcher**: COLMAP's FLANN matcher segfaults at the default
  `block_size` regardless of version; `cpu_brute_force_matcher` is correct but ~40×
  slower. The CPU path stays **FLANN + capped block size**
  (`EFFIGIES_CPU_MATCH_BLOCK`). A GPU-vs-arm64 runtime issue the GPU image won't hit.
- **OpenMVS 2.4.0 is a runtime fix, not just a feature**: 2.3.0's `DensifyPointCloud`
  heap-corrupts on arm64; 2.4.0 (FLANN → nanoflann) runs the full dense+mesh chain.

## v0.3.0 — Georeferencing accuracy *(released — 2026-06-13)*

- [x] **Multi-view GCP triangulation.** Marked pixels are undistorted (full COLMAP
      lens model, fixed-point inversion) into viewing rays and intersected in least
      squares across all images the GCP is marked in (parallax + cheirality
      checked). Single-view GCPs fall back to the nearest-sparse-point heuristic,
      reported per method. Synthetic scene: ~2e-7 m vs ~1e-3 heuristically.
- [x] **Lens-distortion-aware marked-pixel rays** — folded into the triangulation
      (the distortion matters where pixels become geometry). The EXIF path pairs
      camera *centers* with GPS; centers are distortion-independent, so there was
      nothing to gain there.
- [x] **Reprojection-error reporting** in `georef_transform.json` (`residuals`:
      count, RMS 3D/horizontal/vertical, max), echoed in the log and the
      quality-report PDF.
- [x] Named CRS presets (`crs-preset`): Israeli TM, Palestine 1923, ETRS89 UTM
      32N/33N, OSGB, Swiss LV95 — presets, not defaults; explicit `crs` wins.

## v0.3.x — Deeper georeferencing rigor *(when the paper's accuracy claims need it)*

- [x] **GCP-constrained bundle adjustment** (`--gcp-bundle-adjust off|on|auto`,
      default `auto`). The classic GCP path drives a post-hoc Umeyama similarity on
      triangulated marker points (`georef_bridge.py`); a rigid 7-DoF similarity
      cannot absorb reconstruction **drift**, so the check-point RMSE it leaves is
      a floor. The new path (`helpers/gcp_bundle_adjust.py`, **pycolmap** / COLMAP's
      own Ceres BA) anchors the marked GCPs at their surveyed coordinates as
      constant 3D points and re-optimises cameras + tie points on the **sparse**
      model — *before* `image_undistorter`, so densify / mesh / texture / ortho all
      inherit the corrected, world-frame poses. It rewrites `sparse/0` into the
      offset-world frame and writes `georef_transform.json` as the
      identity-with-offset transform (`source=colmap-gcp-ba`), which the bridge then
      honors instead of re-solving (the *offset trick* keeps every downstream
      consumer unchanged). pycolmap is built from the pinned COLMAP source into both
      images. Check-point convention: a `gcp_list.txt` line ending in `check` is
      held out and reported as an independent CP-RMSE.
      `auto` (the **default**) runs both paths and keeps the BA only if it beats the
      post-hoc check-point RMSE (cheap sparse-model comparison, free model backed up
      and restored on a loss) — *by construction never worse than the post-hoc path*
      on the check metric, which is what justifies it as the default; a run without
      GCPs / check points falls back silently. **Absolute-accuracy validation still
      deferred** to the v0.7.0 reference-data campaign (needs a surveyed GCP +
      held-out check-point dataset) — that measures the gain; the default rests on
      the relative never-worse property, which the synthetic fixture + the in-image
      Stage-0 spike (real 70-image / 34 626-point reconstruction, BA converged in
      ~1 s) verify. Real-data tuning of BA options (gauge, intrinsics, margin) is
      the open piece.

## v0.4.0 — Quality profiles & tuning

- [x] **Capture profiles** as an engine option (`profile`: `drone-3d` / `object` /
      `architecture`): versioned parameter bundles applied for options the user
      did not set explicitly (explicit choices win). Lives in the engine instead
      of WebODM's preset JSON — those are per-install data keyed to ODM's option
      names and useless for Effigies. The bundle *values* are currently reasoned
      defaults; **empirically calibrating them** (esp. `RefineMesh`) per profile
      against benchmark runs is deferred to **v0.7.0** (it needs the benchmark
      campaign).
- [x] Expose key OpenMVS refine parameters as task options with documented
      effects: `refine-max-face-area`, `refine-gradient-step`, and
      `refine-mesh-iters` now genuinely driving `RefineMesh --scales` (it was
      hardcoded to 1 — the advertised option did nothing). The CPU stability caps
      (`cpu-threads`, `cpu-match-block`) are options too; env vars still override.
- [x] **Multi-view blended texturing** (`texture_blend.py`): every texel
      re-baked as a depth-tested, angle/distance-weighted blend of its top-4
      views — removes the per-view blotches on homogeneous surfaces (roof std
      23.6 -> 16.9 cumulatively with harmonisation). Possible refinements:
      sharpness-aware weights, multi-band blending (low frequencies blended,
      high frequencies from the best view).
- [x] **Orthophoto from the textured mesh.** `helpers/orthophoto.py` nadir-
      rasterises the refined textured mesh into a georeferenced GeoTIFF
      (`odm_orthophoto/odm_orthophoto.tif`), so the ortho inherits RefineMesh
      detail. Rasteriser is batch-vectorised (small-triangle size classes in one
      numpy pass each, z-buffer conflicts via lexsort; ~10x vs the per-face loop,
      pixel-identical). DSM/DTM and true-ortho hardening landed as their own
      items below (the gap to ODM's mature raster outputs is closed).
- [x] **DSM (digital surface model).** `orthophoto.py` already rasterises the
      refined mesh nadir with a z-buffer (z-winner lexsort) — the per-pixel
      surface height it computes *is* the DSM. That height grid is now emitted as
      a georeferenced single-band Float32 GeoTIFF (`odm_dem/dsm.tif`, nodata
      −9999) from the same rasterisation pass, so it inherits RefineMesh detail at
      no extra cost; on by default, `skip-dsm` to disable, auto-skipped for
      local-frame results. Terminology: **DEM** is the umbrella; this z-winner top
      surface is specifically the **DSM** (roofs/vegetation included); the
      bare-earth **DTM** is the separate item below. ODM's `odm_dem/` folder holds
      both — same path.
- [x] **DTM (digital terrain model — bare earth).** `helpers/pointcloud_to_dtm.py`
      runs a PDAL pipeline over the georeferenced LAZ (already built for the cloud
      output): statistical outlier removal → SMRF ground classification (the same
      filter ODM uses) → keep ground → `writers.gdal` IDW raster → `odm_dem/dtm.tif`
      (single-band Float32, nodata −9999). No new dependency. **Opt-in** (`dtm`,
      default off): the ground filter costs time and a bare-earth model is
      meaningless without open ground. Verified on real data — strips ~3.7 m of
      building tops vs the DSM. Completes the `odm_dem/` pair with the DSM.
- [x] **True-ortho hardening.** The foundation was already true-ortho —
      rasterising the real 3D mesh with the z-buffer gives occlusion-correct
      coverage (no building lean), unlike a DSM-only ortho. Hardening added a
      bounded interior hole-fill (`fill_ortho_holes` in `orthophoto.py`, scipy
      `ndimage`): only small INTERIOR nodata holes below `ortho-fill-holes` m²
      (default 0.25, 0=off) are closed with the nearest valid colour; large voids
      (missing walls) and the outer boundary stay honest nodata, and the DSM /
      DTM / cloud are never touched (verified byte-identical with fill on/off).
- [x] **Benchmark tooling.** `scripts/benchmark.sh` computes the full accuracy
      core: `compare` (cloud-to-reference **and** mesh-to-reference distance — an
      OBJ is area-weighted surface-sampled first — via PDAL ICP + scipy KD-tree,
      plus completeness), `cprmse` (check-point RMSE), and `stats` surface
      roughness (local plane-fit residual, detail-vs-noise); with a prior-art
      review in [docs/benchmark-literature.md](docs/benchmark-literature.md)
      (BibTeX in `docs/references.bib`). The actual comparison **runs** against
      stock ODM / Metashape / RealityScan are the **v0.7.0** campaign below.

## v0.5.0 — Scaling to large image sets (split-merge tiling)

Single-machine reconstruction has two hard walls as image count grows toward
300 / 600 / 900+: the COLMAP matcher (`exhaustive` is O(n²) — dead above ~150)
and, more fundamentally, **memory** — the dense cloud and the `ReconstructMesh`
Delaunay tetrahedralization (70 images → 15.5 M points → 87 M tetrahedra; 900
would be tens of GB). The time cost grows on CPU, but the RAM wall is the real
limit and it is **GPU-independent**, so it bites this (no-NVIDIA) setup
regardless. The commercial tools all solve it the same way: spatial
partitioning — Metashape **chunks** + tiled model + network processing,
RealityScan out-of-core **components**, and ODM's own **split-merge**
(`--split` / `--split-overlap` submodels merged via GPS/GCP).

- [x] **Auto-scaling for the ≤~300 path (`pipeline/autoscale.sh`).** `run.sh`
      counts the images and, for options not set explicitly, adapts: > ~150
      images switches `exhaustive` → `vocab_tree`; > ~500 also prefers
      `mapper=global` and bounds full-res densify (0→1). Logged, overridable,
      `--no-auto-scale` to disable, thresholds env-tunable. The honest WebODM-side
      mechanism: `/options` is static, so the engine adapts at runtime, not the
      (un-modifiable) dialog. The deeper levers (`number-views-fuse`, tiling)
      remain manual / below.
- [x] **Blend streaming refactor (precondition).** `helpers/texture_blend.py`
      had three image-count-scaling memory consumers (dense `[faces×views]`
      weight matrix ~29 GB, all source images in RAM ~32 GB, all depth maps held
      at once) — a wall of our own making at 900 images. Fixed in two steps:
      **streaming top-K view selection** (depth maps rendered on the fly, running
      top-K instead of the matrix — bit-for-bit identical selection) and a
      **view-major bake** (each page rasterised into a per-(face,texel) table and
      sampled one image at a time — preserves the two-level accumulation, atol-1
      identical). Peak RSS is now governed by mesh + atlas size only.
      `seam_level.py` was *not* affected (scales with atlas + mesh, not image
      count). Design in [docs/blend-streaming-plan.md](docs/blend-streaming-plan.md);
      `tests/test_blend.py` proves equivalence; `EFFIGIES_BLEND_RSS` probes peak
      RSS. The large reduced-res high-count RSS confirmation run is a deferred
      manual step (a toy-scene RSS assertion can't prove N-independence). Landed
      before tiling, as required.
- [x] **Split-merge tiling** (`tiles=off|auto|N`, `tile-budget`; opt-in, default
      off). SfM runs once on the whole set; the cameras are partitioned spatially
      **in that one shared sparse frame** (no GPS, no per-tile alignment), only the
      dense→mesh→texture chain runs per tile within a memory budget, and the
      per-tile meshes + clouds are merged into one set of assets — alignment is free
      because every tile inherited the same poses. `helpers/tiling.py` (pure grid
      partition + manifest + pycolmap/struct subset writer), `pipeline/tile.sh`
      (per-tile `InterfaceCOLMAP` + the **unchanged** `dense_openmvs.sh` on a tile
      workdir that symlinks the shared, once-harmonised undistorted images),
      `helpers/tile_merge.py` (crop-to-core mesh+cloud concat with atlas
      namespacing). The merge runs upstream so the entire existing downstream
      (georef → LAZ → ortho/DSM → glTF → report → map_outputs) runs **once on the
      merged `$WORK`, byte-identical to the non-tiled path**; below the budget
      threshold the run is byte-identical to today (zero overhead). Phases 1–4
      (partition, per-tile orchestration, merge, gating/wiring) landed and unit-
      tested; **Phase 0** (single tile reconstructs correctly from the global
      sparse) and **Phase 5** (tiled ≈ single-machine + bounded per-tile RAM) need a
      real large run and are deferred to the reference-data campaign. v1 mesh-seam
      limitation at tile borders documented (Metashape/ODM share it). Architecture:
      [docs/split-merge-tiling-plan.md](docs/split-merge-tiling-plan.md).
- [x] ~~Optional: out-of-core / cache-to-disk for the dense + Delaunay stages~~
      **— superseded; not feasible as stated.** Investigated against the OpenMVS
      2.4.0 binaries: `ReconstructMesh`'s Delaunay tetrahedralization is **strictly
      in-core** (no `--max-memory`, no block/chunk processing, no disk-cache), so a
      true out-of-core Delaunay would need patching OpenMVS internals (out of scope).
      The memory wall is only movable by fewer points (`densify-resolution-level` /
      `number-views-fuse` — already options) or splitting (**split-merge tiling —
      shipped**), which already meets the item's goal. The one residual worth keeping
      landed: **`dense-max-threads`** (OpenMVS `--max-threads`, default 0 = all cores)
      bounds the densify/refine **peak** RAM on many-core, RAM-constrained hosts —
      the same rationale as `cpu-threads` for COLMAP SIFT — but explicitly does *not*
      touch the Delaunay wall.

## v0.6.0 — Capability parity *(buildable gaps vs ODM / Metashape / RealityCapture)*

Real capability gaps surfaced by a head-to-head review against the competition —
features they ship and we don't (yet), but that are buildable. Distinct from the
deliberate non-goals (multispectral / thermal / multi-camera rigs; GUI — that is
WebODM's role) and from the GPU/maturity gaps tracked elsewhere.

- [x] **Contours / iso-lines (DXF + GeoPackage).** `helpers/contours.py` runs the
      GDAL contour API (no new dependency, no subprocess) over the DTM if present
      (bare-earth terrain contours), else the DSM, at a configurable
      `contours-interval` (m; 0 = off) → `odm_dem/contours.gpkg` (3D LineString +
      `elev` attribute, for GIS) and `odm_dem/contours.dxf` (lines at their
      elevation, for CAD). Self-skips for non-georeferenced results. Verified on
      real data (1805 terrain lines at 0.5 m from the DTM).
- [x] **3D Tiles / Cesium streaming.** `helpers/mesh_to_3d_tiles.py` runs
      OpenDroneMap's **Obj2Tiles** (the same tool ODM uses; a pinned, self-contained
      arm64/x64 binary baked into the image — no .NET runtime) over the textured
      OBJ to build an OGC 3D Tiles LOD tileset (`odm_3d_tiles/tileset.json` +
      `*.b3dm`) for web/Cesium streaming of large scenes. Placement from the georef
      offset (pyproj → WGS84 lat/lon, mean-Z altitude, Z-localised OBJ — ODM's
      reference_lla contract). Opt-in (`3d-tiles`); needs a georeferenced result.
- [x] **Point classification beyond ground.** `helpers/classify_cloud.py` runs
      OpenDroneMap's **OpenPointClass** (the ML classifier ODM uses; built from
      source for arm64 + a pinned model baked into the image) over the
      georeferenced LAZ → ASPRS classes (ground, low/med/high vegetation, building,
      vehicle) written into the cloud, the EPT rebuilt so Potree colours by class,
      and class-filtered surface rasters (`odm_dem/buildings.tif`, `canopy.tif`).
      The DTM reuses the ML ground (class 2) instead of re-running SMRF. Opt-in
      (`classify`); needs a georeferenced result.
- [x] **Multi-epoch / change detection / co-registration.** `helpers/change_detect.py`
      co-registers this epoch onto a prior epoch's reference cloud (PDAL `filters.icp`,
      the same recipe `scripts/benchmark.sh compare` uses) and emits difference
      products: a **DEM-of-Difference** (`odm_dem/dem_difference.tif`) with mean/max
      change, changed area, and **cut/fill volumes** (Σ Δz·cell-area on a shared grid),
      plus an **M3C2** change cloud (`odm_change/m3c2.laz`, signed normal-direction
      distance + per-point level-of-detection, via **py4dgeo** built from source —
      there is no manylinux aarch64 wheel) and a `odm_report/change_detection.json`
      with the co-registration residual (ICP fitness + C2C before/after) and all
      stats. Opt-in via the `align-to` path option (mirrors ODM's `--align`); needs a
      georeferenced result; py4dgeo absent → DoD-only fallback. **v1 is additive
      analysis** — epoch B's own cloud/mesh/ortho stay in their georef frame. v2:
      re-land the mesh + ortho in the reference frame (full `--align` parity),
      DEM-as-reference, and stable-area-masked ICP. Verified: M3C2 recovers a known
      vertical shift, DoD volume math unit-tested.
- [ ] **Orthomosaic finishing.** Seamline editing + radiometric colour balancing
      (Metashape/ODM). Our single-mesh ortho needs no seamlines but also offers no
      such control; expose colour-balance / blending knobs if real orthos show
      residual tonal variation.

## v0.7.0 — Benchmark campaign & profile calibration *(needs reference data)*

The empirical work behind the paper, split out from v0.4.0 (the *tooling* is
done; the *runs* are here). Gated on a dataset with **reference data** — a TLS
scan and/or surveyed check points — for absolute accuracy; relative metrics
(roughness, detail, completeness, runtime) can proceed without it.

- [ ] **Comparison runs.** Process shared datasets through Effigies, stock ODM,
      Metashape and (where available) RealityScan, and compute the
      `scripts/benchmark.sh` metrics — cloud/mesh-to-reference distance,
      check-point RMSE, surface roughness, completeness, runtime. No prior study
      benchmarks COLMAP + OpenMVS *with RefineMesh* against the commercial tools,
      so this is a publishable contribution, not just an internal check. The
      honest headline is narrow-but-deep: refined-mesh surface detail (see the
      ODM comparison in the v0.4.0 notes — ODM leads on ortho maturity / DSM-DTM
      breadth / scaling, Effigies on RefineMesh geometry).
- [ ] **Two queued single-variable experiments** specified against a shared
      baseline run in [docs/planned-experiments.md](docs/planned-experiments.md):
      watertightness (`mesh-close-holes`) and densify-resolution vs. runtime —
      both double as profile-calibration data points.
- [ ] **Profile calibration.** Sweep the key levers (esp. `RefineMesh`
      iterations / `max-face-area` / `gradient-step`, `densify-resolution-level`,
      `number-views-fuse`) per capture type against the benchmark metrics, find
      the quality/cost knee, and bake the measured-optimal values into the
      `drone-3d` / `object` / `architecture` bundles — replacing today's reasoned
      defaults with calibrated ones.

## v1.0.0 — Production

- [ ] A reproducible, source-pinned image with verified binaries as the default.
- [ ] Documented, stable option set; no breaking changes without a major bump.
- [ ] End-to-end coverage and a published reference dataset.
- [ ] Installation / operations guide for adding Effigies to an existing WebODM.

---

## Parked (no hardware)

- **CUDA/production image GPU build + run.** The image is built from the same
  pinned sources as the validated CPU image and passes `docker build --check`, but
  there is no NVIDIA machine available to compile and run it. Parked indefinitely;
  revisit only if GPU hardware appears.

---

## Out of scope

- **Modifying WebODM or NodeODM.** Effigies is an engine behind the existing
  NodeODM REST contract; it must not require patches to either.

Have a use case or a dataset that breaks an assumption here? Open an issue.
