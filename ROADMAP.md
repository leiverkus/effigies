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
- [ ] **Benchmark suite** comparing Effigies output against stock ODM on shared
      datasets (mesh density, photometric error, runtime).

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
