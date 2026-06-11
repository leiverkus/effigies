# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Full ODM output parity.** Effigies now fills every WebODM download slot the
  stock ODM nodes do — orthophoto, point cloud, textured model, **glTF model**,
  **camera parameters** (`cameras.json`), **camera shots** (`shots.geojson`), and a
  **quality report** (`odm_report/report.pdf`, stats table + orthophoto thumbnail
  via reportlab). One node, every product.
- **glTF model (`helpers/mesh_to_gltf.py`) — "Struktur-Modell (glTF)".** A
  self-contained binary glTF (`odm_texturing/odm_textured_model_geo.glb`) of the
  refined textured mesh, with the texture atlas embedded. Written by a dependency-
  free Python GLB writer (the image's node is too old for current obj2gltf), so
  the .glb matches the .obj asset.
- **Camera assets (`helpers/camera_exports.py`) — ODM-parity downloads.**
  `cameras.json` (intrinsics, OpenSfM-normalised, in the project root) and
  `odm_report/shots.geojson` (one WGS84 point per image — camera positions on the
  WebODM map, with filename / camera / focal / pose properties), derived from the
  COLMAP model + the georef similarity. `shots.geojson` is skipped for a local-only
  result; `cameras.json` is always written.
- **Orthophoto output (`helpers/orthophoto.py`).** Effigies now produces a
  georeferenced orthophoto (`odm_orthophoto/odm_orthophoto.tif`, RGB + alpha),
  nadir-rasterised from the refined textured mesh — so it inherits the RefineMesh
  detail instead of being interpolated from a sparse DSM. z-buffered (topmost
  surface wins), texture-sampled, written via GDAL in the model's CRS. New options
  `orthophoto` (on by default) and `orthophoto-resolution` (cm/px, `auto` ≈ 4k px
  wide). Skipped automatically for local-frame / un-georeferenced results. Effigies
  is now a complete engine — 3D mesh, point cloud, AND orthophoto — in one node;
  no need to also run stock ODM for the 2D product. (Adds the `python3-gdal`
  dependency.)
- **Mesh-to-reference distance in `benchmark.sh`.** `compare` now accepts an OBJ
  mesh on either side: it area-weighted surface-samples the mesh to a point cloud
  (deterministic) before the existing ICP + nearest-neighbour distance, so a
  textured mesh can be measured against a reference scan directly — completing the
  benchmark accuracy core (cloud-to-reference, mesh-to-reference, check-point RMSE).

### Fixed
- **EXIF-GPS georeferencing silently dropped on real reconstructions.**
  `read_colmap_camera_centers` filtered blank lines out of `images.txt`, but a
  COLMAP image registered with no observed 3D points has an *empty* points2D line;
  dropping it desynced the two-line stride and silently lost cameras. On drone /
  GLOMAP runs this pushed the EXIF-GPS fix count below the required 3, so
  `georeference=auto` fell back to a local (un-georeferenced) frame even though the
  images carried GPS. The center reader now delegates to the robust pose/points2D
  pairing used by the GCP path, and the EXIF loop tolerates a single malformed
  image instead of failing the whole solve. (The EXIF path had no test coverage;
  added a regression test for the empty-points2D case.)

### Added
- **First full end-to-end run on the CPU image.** The complete chain — COLMAP
  sparse → `image_undistorter` → OpenMVS `DensifyPointCloud` → `ReconstructMesh`
  → `RefineMesh` → `TextureMesh` — now runs to completion on the CPU/arm64 image
  against a real 70-image dataset, producing a textured OBJ. This closes the main
  open 0.2.0 validation item (the engine had never been run through on a dataset).
- **Both images unified on COLMAP 4.0.4 + OpenMVS v2.4.0.** The CUDA/production
  `Dockerfile` and the CPU `Dockerfile.cpu` now build the identical engine from
  the identical pinned sources and recipe — the only differences are the CUDA
  base image and the three `-D*CUDA*` flags. No image builds COLMAP 3.x any more.
- **OpenMVS bumped to v2.4.0 (both images).** v2.3.0's `DensifyPointCloud`
  corrupts the heap and aborts on arm64 (it falls back off SSE); v2.4.0 swaps the
  FLANN nearest-neighbour code for nanoflann and runs the full dense+mesh chain
  cleanly. 2.4.0 needs two libs newer than Ubuntu 22.04 ships — nanoflann ≥1.5
  and CGAL ≥6.0 — both header-only, so both Dockerfiles vendor pinned releases
  (`NANOFLANN_VERSION`, `CGAL_VERSION`) rather than bumping the base off 22.04
  (which would lose PDAL, dropped from 24.04). Two small source patches keep it
  building against jammy's OpenCV (disable the hard libjxl requirement; map the
  one OpenCV-4.7-only JXL write constant to the JPEG one — we emit no JPEG-XL).
- **`matcher=vocab_tree` now works.** Image-retrieval matching for large sets:
  each image bakes in a SHA256-pinned vocabulary tree in the format its COLMAP
  expects (FAISS for COLMAP 4). Previously the option was selectable but always
  aborted with the opaque "Cannot process dataset".

### Fixed
- **CPU pipeline could not start: opaque "Cannot process dataset".** A chain of
  failures, each masking the next, all surfacing only as NodeODM's generic error:
  - `run.sh` applied the default `use-gpu=true` even on the CUDA-less CPU image,
    so COLMAP's SIFT aborted ("Cannot use Sift GPU without CUDA or OpenGL"). It
    now probes for a usable GPU (`nvidia-smi -L`) and falls back to CPU with a
    loud warning when none is present.
  - COLMAP's CPU SIFT extractor OOM-killed itself fanning out over all cores on
    large images; the CPU SIFT/match thread count is now capped
    (`EFFIGIES_CPU_THREADS`, default 4).
  - COLMAP's CPU FLANN matcher segfaulted holding a full match block in memory;
    the exhaustive block size is capped on the CPU path
    (`EFFIGIES_CPU_MATCH_BLOCK`, default 10).
  - `InterfaceCOLMAP` was fed the raw `sparse/0` model instead of an undistorted
    workspace; `sparse_colmap.sh` now runs `colmap image_undistorter` and
    `InterfaceCOLMAP` reads `$WORK/dense` with the correct `--image-folder`.
  - `dense_openmvs.sh` passed `--cuda-device` unconditionally; the CPU OpenMVS
    build rejects it. The flag is now probed and passed only on CUDA builds.
- **Source-built, pinned Dockerfile.** COLMAP (`4.0.4`) and OpenMVS (`v2.4.0`)
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

[Unreleased]: https://github.com/leiverkus/effigies/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/leiverkus/effigies/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/leiverkus/effigies/releases/tag/v0.1.0
