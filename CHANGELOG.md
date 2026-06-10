# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Source-built, pinned Dockerfile.** COLMAP (`3.11.1`) and OpenMVS (`v2.3.0`)
  are now compiled from upstream source with CUDA, replacing the distro packages;
  Eigen/CGAL/Boost/OpenCV come from Ubuntu 22.04. Versions are declared as build
  `ARG`s and a build-time `which` gate fails the build loudly if `colmap`,
  `DensifyPointCloud`, `ReconstructMesh`, `RefineMesh`, `TextureMesh`,
  `InterfaceCOLMAP` or `pdal` is missing.
- **Georeferenced point cloud output.** New `helpers/pointcloud_to_laz.py` applies
  the georef similarity to `scene_dense.ply` and writes
  `odm_georeferenced_model.laz` via PDAL (full projected coordinates, LAS
  scale/offset for precision), and optionally builds an EPT tileset
  (`entwine_pointcloud/`) for the Potree viewer when `entwine`/`untwine` is
  present. `map_outputs.py` maps the LAZ + EPT into the WebODM paths, with the raw
  PLY kept as a documented fallback.
- **CPU test image** (`Dockerfile.cpu`) — same pinned engine built without CUDA,
  for local integration testing on machines without an NVIDIA GPU (e.g. Apple
  Silicon). Plus `docs/DEPLOYMENT.md` (local CPU + GPU host recipes, WebODM node
  wiring) and a `.dockerignore`.
- Unit tests for the point-cloud transform matrix (`tests/test_pointcloud.py`) and
  the NodeODM options translation (`tests/test_options.py`); the local runner and
  CI now execute every `tests/test_*.py`.

### Fixed
- **Options were incompatible with NodeODM.** `options.json` was a flat list, but
  NodeODM (`libs/odmInfo.js`) expects an argparse-style descriptor object keyed by
  `--flag`; it was serving every option as `name="0".."12"` with the wrong types.
  `helpers/optionsToJson.py` now translates our list into NodeODM's schema (enum
  choices, `<class 'int'>`/`float`, bool via `default`, valid `metavar` domains),
  so WebODM builds the correct task UI. (Found by actually running the node.)
- **Options shim could not find `options.json`.** It resolved the path with
  `abspath(__file__)`, which does not follow the NodeODM symlink — it looked in
  `/opt/NodeODM`. Now prefers `ODM_PATH` and falls back to `realpath`.
- `mesh-decimate` domain changed to `float: 0 <= x <= 1` so it passes NodeODM's
  `checkDomain` validation on task submission.

### Notes
- NodeODM hard-skips a `--gcp` UI option (WebODM handles GCP upload natively), so
  the `gcp` option is intentionally not shown; `run.sh` still auto-detects
  `gcp_list.txt`. The node is verified to build, run, serve all options and be
  reachable on the WebODM network; a full processing run on a dataset is the
  remaining 0.2.0 validation.

### Planned
See [ROADMAP.md](ROADMAP.md). Still open for 0.2.0: a full end-to-end processing
run on a real dataset, a verified VCGlib commit pin, and confirming the
`InterfaceCOLMAP`/`InterfaceOpenSfM` binary names across OpenMVS builds. Beyond
that: multi-view GCP triangulation (0.3.0).

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
