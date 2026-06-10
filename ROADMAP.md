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

## v0.2.0 — Reproducible & verifiable build *(next)*

Make the image trustworthy and the output cloud web-ready.

- [ ] **Source-built, pinned Dockerfile.** Build COLMAP and OpenMVS from known-good
      tags instead of distro packages. Add a build-time gate:
      `RUN which DensifyPointCloud ReconstructMesh RefineMesh TextureMesh`.
- [ ] **Point cloud → `.laz` + EPT.** Convert `scene_dense.ply` with PDAL and emit
      an Entwine/EPT tileset so the WebODM Potree viewer works.
- [ ] **End-to-end smoke test** against a small public close-range dataset (run
      behind a manual / self-hosted CI job, not the GPU-less default runner).
- [ ] **Pin and document image tags**; no `latest` anywhere.
- [ ] Verify `InterfaceCOLMAP` / `InterfaceOpenSfM` binary names across OpenMVS
      builds and handle the variants.

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
