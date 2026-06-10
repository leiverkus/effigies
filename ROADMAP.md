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
- [~] **End-to-end smoke test.** The CPU image (`Dockerfile.cpu`) now builds the
      pinned engine from source on arm64, the `which` gate passes, and the node
      runs: NodeODM reports `engine=effigies`, serves all options correctly and is
      reachable on the WebODM network. STILL OPEN: a full processing run on a real
      dataset (sparse → densify → refine → texture → LAZ) to confirm the pipeline,
      and the same on the CUDA image on GPU hardware.
- [ ] **Pin VCGlib to a verified commit SHA** (currently tracks a branch via the
      `VCG_REF` arg; lock it before tagging a release image).
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
