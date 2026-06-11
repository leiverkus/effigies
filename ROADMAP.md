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

## v0.2.0 — Reproducible & verifiable build *(in progress)*

Make the image trustworthy and the output cloud web-ready.

- [x] **Source-built, pinned Dockerfile.** COLMAP `3.11.1` and OpenMVS `v2.3.0`
      built from source (versions as build `ARG`s), with a build-time gate:
      `which colmap DensifyPointCloud ReconstructMesh RefineMesh TextureMesh
      InterfaceCOLMAP pdal` — the build fails loudly if any is missing.
- [x] **Point cloud → `.laz` + EPT.** `helpers/pointcloud_to_laz.py` applies the
      georef transform, writes `odm_georeferenced_model.laz` via PDAL, and builds
      an EPT tileset (entwine/untwine) for the Potree viewer.
- [x] **No `latest` tags.** Base image and engine versions are explicit `ARG`s.
- [x] **End-to-end smoke test (CPU image).** The full chain — COLMAP sparse →
      `image_undistorter` → OpenMVS densify → reconstruct → refine → texture —
      runs to completion on the CPU/arm64 image against a real 70-image dataset,
      producing a textured OBJ. Getting there fixed a chain of CPU-path bugs (GPU
      fallback, SIFT thread/match-block caps, the undistort workspace, the
      `--cuda-device` probe) and required OpenMVS **v2.4.0** — v2.3.0 corrupts the
      heap on arm64. STILL OPEN: drive it through NodeODM/WebODM to also exercise
      the georef → LAZ/EPT → `map_outputs` tail, and the same on the CUDA image on
      GPU hardware.
- [ ] **Bump the production (CUDA) image to OpenMVS v2.4.0.** The CPU image vendors
      pinned header-only nanoflann ≥1.5 + CGAL ≥6.0 and patches out the libjxl
      requirement to build 2.4.0 on Ubuntu 22.04. For the CUDA/production image,
      decide between the same header-vendor recipe and OpenMVS' own vcpkg build
      (exact upstream dep versions, no source patches, but a much heavier build).
- [~] **Pin VCGlib to a verified commit SHA.** The CPU image is pinned to the
      `cdcseacave/VCG` commit it was built and validated against (`658ba36`); the
      production (CUDA) image still tracks `master` and is pinned together with its
      OpenMVS 2.4.0 bump. Both must be locked before tagging a release image.
- [ ] Verify `InterfaceCOLMAP` / `InterfaceOpenSfM` binary names across OpenMVS
      builds and handle the variants.
- [ ] Slim the image with a multi-stage (devel build → runtime copy) layout once
      the single-stage build is confirmed working.

## v0.2.x — COLMAP 4 + Ubuntu 24.04 base *(planned, after v0.2.0)*

Move the engine onto the COLMAP-4 generation. Sequenced as three **isolated** steps
so a toolchain break is never debugged together with a COLMAP-API break.

1. **Base image → Ubuntu 24.04 / CUDA 12.5.1, COLMAP still 3.13.** There is no
   `12.4.1-devel-ubuntu24.04`; the lowest CUDA 12.x on 24.04 is `12.5.1`. The driver
   for 24.04 is **OpenImageIO**, not CMake — COLMAP 4 needs only CMake 3.12 (the 3.28
   floor was GLOMAP's *standalone* build, which is moot once GLOMAP ships inside
   COLMAP 4). The real risk is the OpenMVS/VCGlib rebuild under GCC 13 + newer
   Boost/CGAL — prove this with COLMAP unchanged before touching COLMAP.
2. **GPU image OpenMVS `v2.3.0` → `v2.4.0`** — prerequisite for step 3; folds into
   the existing v0.2.0 item above. 2.4.0's `InterfaceCOLMAP` adds the `SIMPLE_PINHOLE`
   reader path (2.3.0 reads only `PINHOLE`). Lock the GPU VCG SHA together with this
   bump (candidate: the CPU-validated `658ba36`, to be confirmed on GPU hardware).
3. **COLMAP `3.13` → `4.0.x`.** New required apt deps: `libopenimageio-dev` (replaces
   FreeImage as COLMAP's image I/O — **keep** `libfreeimage-dev`, OpenMVS still needs
   it) and `libsuitesparse-dev` (CHOLMOD is now `REQUIRED`). GLOMAP arrives built-in
   via `colmap global_mapper` — wire it as an optional `mapper: global` choice, never
   the default (incremental is more robust on close-range / convergent sets).

**InterfaceCOLMAP ↔ COLMAP 4.0.4 — compatibility verified at the byte level.**
COLMAP 4's rig/frame refactor is *additive*: `rigs.bin` / `frames.bin` are new,
separate files that `InterfaceCOLMAP` ignores; `images.bin` is byte-identical to the
classic format (pose stays per-image as `cam_from_world`; `frame_id` is *derived* on
read, not stored in the image record). OpenMVS 2.4's `Image::ReadBIN` reads exactly
that layout (`ID, q.wxyz, t.xyz, camera_id, name\0, num_points2D, [x,y,pt3D_id]…`).
COLMAP-4's new camera models (FISHEYE / DIVISION) never reach OpenMVS because
`image_undistorter` converts to PINHOLE first. Source-checked, **not** run-checked.

- [ ] **Acceptance gate: end-to-end smoke test against COLMAP 4.0.4 output.** Drive a
      real dataset through `sparse_colmap.sh` (COLMAP 4 `image_undistorter`) →
      `InterfaceCOLMAP` → `dense_openmvs.sh` and assert `scene.mvs` is produced
      (the compat boundary) and a textured OBJ comes out. This is the only remaining
      unknown — it lives in execution, not in the format. Script: `scripts/smoke_e2e.sh`.

**Runtime findings from the CPU/arm64 end-to-end validation (this branch).** These
are *run-checked* on a 70-image dataset and refine the source-level plan above:

- **PDAL is gone from Ubuntu 24.04's repos** (present in 22.04). The "base → 24.04"
  step must solve PDAL (PPA / source / vendored) before moving, or LAZ/EPT breaks.
  This is why the CPU image stays on 22.04 and vendors header-only nanoflann + CGAL.
- **COLMAP 3.13 renamed the generic feature CLI options**: `SiftExtraction.use_gpu`
  / `SiftMatching.use_gpu` (+ `num_threads`) → `FeatureExtraction.*` /
  `FeatureMatching.*`; the SIFT-algorithm options (and `SiftMatching.cpu_brute_force_matcher`)
  keep the `Sift*` prefix. `sparse_colmap.sh` was updated accordingly — a code change
  the format/dep analysis did not surface, and one that recurs for the 4.0 step.
- **The arm64 CPU matcher crash is NOT a version bug — 3.13's FLANN matcher still
  segfaults** at default `block_size`, exactly as 3.11.1 did. Three options measured:
  default FLANN → segfault; `cpu_brute_force_matcher` (new in 3.13) → correct but
  **~40× too slow** (≈5 h for this set vs. 7.5 min); FLANN at `block_size 10` →
  completes cleanly. So the CPU path stays **FLANN + capped block size**, not
  brute-force; this is a GPU-vs-arm64 runtime issue the GPU production image will not
  hit. The acceptance gate's "it lives in execution" is correct — confirmed here.
- **OpenMVS 2.4.0 is also a *runtime* fix, not only a feature add**: v2.3.0's
  `DensifyPointCloud` heap-corrupts and aborts on arm64; 2.4.0 (FLANN → nanoflann)
  runs the full dense+mesh chain. Step 2's GPU bump should treat this as load-bearing.

## v0.3.0 — Georeferencing accuracy

- [ ] **Multi-view GCP triangulation.** Replace the nearest-sparse-point heuristic
      with proper triangulation of the marked pixel across its images.
- [ ] **Lens-distortion-aware EXIF projection** where it improves the camera-center
      correspondence.
- [ ] **Reprojection-error reporting** in `georef_transform.json` (RMS residual,
      number of correspondences used) so the solve quality is visible.
- [ ] Optional named CRS presets selectable in the UI (regional grids included as
      presets, not as defaults).

## v0.4.0 — Quality profiles & tuning

- [ ] **Capture profiles** that preset sensible parameters: *object/turntable*,
      *architecture*, *macro/find*. Calibrate `RefineMesh` parameters per profile.
- [ ] Expose key OpenMVS refine parameters (`--max-face-area`, `--scales`,
      `--gradient-step`) as task options with documented effects.
- [~] **Benchmark suite** comparing Effigies output against stock ODM /
      Metashape / RealityCapture on shared datasets (mesh density, photometric
      error, runtime). Scaffolded: `scripts/benchmark.sh` (per-stage runtime +
      mesh/cloud stats) and a prior-art review in
      [docs/benchmark-literature.md](docs/benchmark-literature.md) (BibTeX in
      `docs/references.bib`). Open: cloud-to-cloud / mesh-to-reference distance
      and CP RMSE — the accuracy metrics the literature uses. No prior study
      benchmarks COLMAP + OpenMVS *with RefineMesh* against the commercial
      tools, so this is a publishable contribution, not just an internal check.

## v1.0.0 — Production

- [ ] A reproducible, source-pinned image with verified binaries as the default.
- [ ] Documented, stable option set; no breaking changes without a major bump.
- [ ] End-to-end coverage and a published reference dataset.
- [ ] Installation / operations guide for adding Effigies to an existing WebODM.

---

## Out of scope

- **Aerial / GPS mapping.** Already served well by the stock ODM node; Effigies
  targets the close-range refine-quality gap and should not duplicate ODM.
- **Modifying WebODM or NodeODM.** Effigies is an engine behind the existing
  NodeODM REST contract; it must not require patches to either.

Have a use case or a dataset that breaks an assumption here? Open an issue.
