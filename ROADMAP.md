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

- [ ] **GCP-constrained bundle adjustment.** Today GCPs drive a post-hoc Umeyama
      similarity on triangulated marker points (the bridge in `georef_bridge.py`).
      ODM's stronger path puts GCPs *into* the bundle adjustment, so marker
      residuals shape the reconstruction itself. COLMAP supports pose / position
      priors (`Mapper.use_prior_position` for GPS); pulling GCP observations into
      the BA is more involved but achievable, and would tighten the CP-RMSE that
      the benchmark reports. Real work — schedule it only if the accuracy claims
      in the paper demand it, not speculatively.

## v0.4.0 — Quality profiles & tuning

- [x] **Capture profiles** as an engine option (`profile`: `drone-3d` / `object` /
      `architecture`): versioned parameter bundles applied for options the user
      did not set explicitly (explicit choices win). Lives in the engine instead
      of WebODM's preset JSON — those are per-install data keyed to ODM's option
      names and useless for Effigies. The bundle *values* are currently reasoned
      defaults; **empirically calibrating them** (esp. `RefineMesh`) per profile
      against benchmark runs is deferred to **v0.6.0** (it needs the benchmark
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
      stock ODM / Metashape / RealityScan are the **v0.6.0** campaign below.

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
- [ ] **Blend streaming refactor (precondition).** `helpers/texture_blend.py`
      has three image-count-scaling memory consumers (dense `[faces×views]`
      weight matrix ~29 GB, all source images in RAM ~32 GB, all depth maps held
      at once) — a wall of our own making at 900 images. Fix: streaming top-K
      view selection (depth maps rendered on the fly) + a view-major bake (one
      image resident at a time). `seam_level.py` is *not* affected (it scales with
      atlas + mesh, not image count). Full design with measured slopes and a
      phased plan in [docs/blend-streaming-plan.md](docs/blend-streaming-plan.md).
      Must land before tiling, or our own texture-quality stage becomes the wall.
- [ ] **Split-merge tiling.** Run SfM once on the whole set, partition the
      cameras spatially **in that shared sparse frame** (no GPS required, no
      per-tile alignment), run the dense→mesh→texture chain per tile within a
      memory budget, and merge cloud / mesh / orthophoto. The open-source
      analogue of Metashape chunks; the only clean path past the single-machine
      memory wall. Full architecture (shared-sparse anchor, global-harmonise
      coupling, easiest-merge-first phasing, honest seam risks) in
      [docs/split-merge-tiling-plan.md](docs/split-merge-tiling-plan.md).
- [ ] Optional: out-of-core / cache-to-disk for the dense + Delaunay stages
      (the RealityScan approach) as an alternative to tiling for mid-size sets.

## v0.6.0 — Benchmark campaign & profile calibration *(needs reference data)*

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
