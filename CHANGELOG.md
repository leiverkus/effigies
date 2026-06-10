# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Planned
See [ROADMAP.md](ROADMAP.md). Next up: source-built and binary-verified Dockerfile,
`.laz` + EPT point-cloud output, multi-view GCP triangulation.

## [0.1.0] - 2026-06-10

First public release — a working, NodeODM-compatible engine scaffold for WebODM.

### Added
- **NodeODM contract**: `ENGINE` name, `run.sh` argument parsing in NodeODM's
  `--name value` convention, and `helpers/optionsToJson.py` serving `options.json`
  so WebODM builds the task-options UI automatically.
- **Sparse stage**: `pipeline/sparse_colmap.sh` (COLMAP feature extraction →
  matching → incremental mapper) with selectable matcher and camera model;
  `pipeline/sparse_opensfm.sh` as an alternative geo-aligned backend for aerial
  sets.
- **Dense stage**: `pipeline/dense_openmvs.sh` running the full OpenMVS chain —
  `DensifyPointCloud` → `ReconstructMesh` → `RefineMesh` ×N → `TextureMesh` — the
  `ReconstructMesh`/`RefineMesh` steps that stock ODM skips.
- **Georeferencing bridge** (`helpers/georef_bridge.py`): Umeyama 3D similarity
  with four modes — `auto`, `gcp`, `exif`, `none`. Float precision preserved via a
  subtracted projected offset recorded in `georef_transform.json`.
- **Output mapping** (`helpers/map_outputs.py`): writes the textured model and
  point cloud into the WebODM asset paths (`odm_texturing/`, `odm_georeferencing/`).
- **Task options** (`options.json`): sparse engine, matcher, camera model, densify
  level, views-to-fuse, mesh reconstruct/refine/decimate, texture resolution,
  georeference mode, target CRS, GCP file, GPU toggle.
- **Docker image**: CUDA base, COLMAP + OpenMVS + PDAL, NodeODM cloned unmodified
  as the REST layer.
- **Tests**: synthetic-COLMAP unit tests for the Umeyama solver, the GCP path, and
  `none` mode (`tests/test_georef.py`); local runner `scripts/test.sh` mirroring CI.
- **CI** (`.github/workflows/ci.yml`): bash syntax, Python compile, `options.json`
  validation, shellcheck (advisory), and the georef unit tests.
- Project docs: `README.md`, `CLAUDE.md`, `CONTRIBUTING.md`, issue template.

### Licensing
- Released under the **MIT License**. Third-party components it orchestrates
  (NodeODM/ODM/OpenMVS — AGPL-3.0; COLMAP — BSD-3-Clause; OpenSfM — BSD-2-Clause)
  retain their own licenses; see `THIRD_PARTY_LICENSES.md`.

### Known limitations
- Dockerfile installs COLMAP/OpenMVS from distro packages, which may lack a working
  `RefineMesh`; a source build with binary verification is planned for 0.2.0.
- GCP localization uses the nearest observed sparse point to the marked pixel
  rather than multi-view triangulation.
- The dense point cloud is passed through as `.ply`; `.laz` + EPT conversion is
  planned.
- No end-to-end run against a real dataset is exercised in CI (no GPU runner).

[Unreleased]: https://github.com/leiverkus/effigies/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/leiverkus/effigies/releases/tag/v0.1.0
