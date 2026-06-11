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

- [ ] Verify `InterfaceCOLMAP` / `InterfaceOpenSfM` binary names across OpenMVS
      builds and handle the variants.
- [ ] Slim the image with a multi-stage (devel build → runtime copy) layout.

## COLMAP 4 migration *(done — folded into v0.2.0)*

Both images are now on **COLMAP 4.0.4 + OpenMVS 2.4.0**, built from identical pinned
sources and run-verified end-to-end on the CPU image. The originally-planned
three-step sequence (24.04 base → GPU OpenMVS 2.4.0 → COLMAP 3.13 → 4.0.x) collapsed:
**the base bump to 24.04 was not needed** — COLMAP 4 builds on Ubuntu 22.04 once
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
      `docs/references.bib`). `benchmark.sh` computes the full accuracy core:
      `compare` (cloud-to-reference **and** mesh-to-reference distance — an OBJ is
      area-weighted surface-sampled first — via PDAL ICP + scipy KD-tree, plus
      completeness), `cprmse` (check-point RMSE), and `stats` surface roughness
      (local plane-fit residual, detail-vs-noise). Remaining: run the actual
      comparison against stock ODM / Metashape / RealityCapture on shared datasets.
      No prior study benchmarks COLMAP + OpenMVS *with RefineMesh* against the
      commercial tools, so this is a publishable contribution, not just an internal
      check.

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

- **Aerial / GPS mapping.** Already served well by the stock ODM node; Effigies
  targets the close-range refine-quality gap and should not duplicate ODM.
- **Modifying WebODM or NodeODM.** Effigies is an engine behind the existing
  NodeODM REST contract; it must not require patches to either.

Have a use case or a dataset that breaks an assumption here? Open an issue.
