# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- **Relicensed from MIT to GNU AGPL-3.0-or-later.** `LICENSE` now carries the full
  AGPLv3 text; the badge, README §License, `CITATION.cff`, and `.zenodo.json` are
  updated accordingly. This aligns Effigies' own source with the copyleft licences
  of the engines it bundles and orchestrates (NodeODM/ODM/OpenMVS are AGPL-3.0).
  `THIRD_PARTY_LICENSES.md` is unaffected — it documents the bundled components,
  whose licences are unchanged.
- **SPDX headers in every source file.** All Python and shell sources, both
  `Dockerfile`s, and the CI workflow now carry REUSE-style
  `SPDX-FileCopyrightText` + `SPDX-License-Identifier: AGPL-3.0-or-later` headers
  (inserted after the shebang where present). `ENGINE` is intentionally left
  bare — NodeODM reads it as a literal engine name.

## [0.7.1] - 2026-06-16

### Added
- **Citation metadata for Zenodo archival.** `CITATION.cff` (CFF 1.2.0) with author,
  keywords, and upstream references (COLMAP, OpenMVS, NodeODM), and `.zenodo.json`
  so Zenodo draws release metadata from the repository. The concept DOI is filled
  into `CITATION.cff` once the first Zenodo-archived release is minted.

## [0.7.0] - 2026-06-15

### Added
- **Semantic orthophoto v0 (`helpers/semantic_ortho.py`, opt-in `--semantic`).** The
  geometry-derived first increment of the semantic field (ROADMAP v0.7.0): it
  rasterises the OpenPointClass point classes already written into the LAZ onto the
  orthophoto grid — pixel-aligned with `odm_dem/dsm.tif` — taking the per-cell
  **majority** class, folded into **ground / vegetation / structure**, as
  `odm_semantic/orthophoto_semantic.tif` (Byte GeoTIFF + colour table) plus a legend
  JSON. No trained model: it ships what the cloud classification already knows; the
  fine archaeological material classes (stone/earth/paving/ceramic/mortar) are a
  downstream 2D-model deliverable. Needs a classified cloud (enable `--classify`);
  self-skips, non-fatally, otherwise. Runs after the orthophoto/DSM so the grid exists.
  Unit-tested (majority + ASPRS→v0 mapping + GeoTIFF round-trip) and image-validated
  end-to-end. This is the bridge's v0 to Structura's vectorisation.
- **Multi-epoch semantic propagation (`helpers/semantic_propagate.py`).** Carries the
  semantic field across daily epochs (runs under `--semantic` when `--align-to` gives a
  reference epoch). Because change detection re-lands this epoch into the reference frame,
  its semantic ortho is already co-registered with the reference epoch's; the step writes
  (1) a **carry-forward** field — `odm_semantic/orthophoto_semantic_propagated.tif`, where
  this epoch's unobserved cells inherit the reference epoch's class (temporally consistent;
  honest "unobserved = unchanged" assumption) — and (2) a **semantic-change** raster —
  `odm_semantic/semantic_change.tif` with per-pixel class transitions + per-transition area
  in `odm_report/semantic_change.json` (the class complement of the DoD/M3C2 geometric
  change: e.g. structure→ground = a feature removed, vegetation→ground = clearing). The
  reference is resampled nearest-neighbour (categorical). Self-skips without both semantic
  orthos; non-fatal. Unit-tested (carry-forward + transition + stats) and end-to-end
  validated.

### Changed
- **Change detection — M3C2 level-of-detection now includes the co-registration
  residual (`change_detect.py`).** The per-point LoD previously reflected local
  roughness only; it now passes the post-ICP cloud-to-cloud residual to py4dgeo as
  `registration_error` (Lague 2013), so a cm-level alignment error is folded into the
  significance test instead of being treated as zero — small (few-cm) changes are no
  longer over-reported as significant. Recorded as `registration_error_m` in
  `odm_report/change_detection.json`. Unit-tested: a 5 cm residual lifts the LoD
  median (≈ 0.3 cm → 10 cm). Stable-area-masked ICP remains v2 (see ROADMAP).
- **Change detection — DoD now thresholded at a minimum level-of-detection
  (`change_detect.py`).** The DEM-of-Difference changed area and fill/cut volumes now
  count only cells whose |Δz| exceeds a minimum LoD (Wheaton 2010 — a robust noise
  floor of the difference distribution, floored by the co-registration residual;
  `min_lod_from_dod`), so sub-LoD noise is no longer booked as excavation / back-fill.
  A raw un-thresholded net volume is kept as a cross-check, and the minLoD is recorded
  as `min_lod_m` in `odm_report/change_detection.json`. Unit-tested.
- **Change detection — co-registration is now stable-area-masked (`change_detect.py`).**
  ICP runs in two passes: a whole-cloud fit, then a re-fit on only the *unchanged*
  ground (changed cells dropped via `stable_mask`), so a localised excavation change no
  longer biases the rigid transform, and the residual over the stable area is a clean
  *registration-only* error that now feeds the M3C2 LoD and the DoD minLoD
  (`coreg_reg_error`) instead of the conservative full-cloud C2C. Reports
  `stable_fraction` + `registration_error` in `change_detection.json`; degrades to the
  whole-cloud fit (and records why) when too little stable ground remains. Unit-tested
  (`stable_mask` separation; the two-pass ICP smoke is pdal-gated).
- **Change detection — `--align-to` now re-lands the deliverables into the reference
  frame by default (`change_detect.py`), ODM `--align` parity.** The recovered ICP
  transform is applied in place to the delivered mesh OBJ (offset-aware, `transform_obj`)
  and the LAZ (PDAL; EPT rebuilt); because re-landing runs before the raster stages, the
  DSM/DTM, orthophoto, contours, glTF and 3D Tiles inherit the reference frame natively
  (no re-warp). Previously the alignment was additive-only (deliverables untouched) —
  pass `--no-reland` to keep that. `report["relanded"]` lists what moved; non-fatal per
  asset. Camera assets (`shots.geojson`) are a known re-land gap (v2). Offset-exact
  transform unit-tested; the full raster re-derivation is Docker-validated.
- **Change detection — `--align-to` now accepts a DEM GeoTIFF as the reference, not
  only a point cloud (`change_detect.py`).** A reference `.tif` (a prior DSM/DEM) is
  read as cell-centre points for the ICP co-registration and M3C2, and used directly
  (resampled onto the shared grid) as the reference DSM for the DEM-of-Difference —
  so a prior epoch's DSM, or any external reference DEM, can drive the comparison.
  `is_dem` / `dem_to_xyz` / `resample_dem`; unit-tested.
- **Change detection — camera assets are re-landed with the rest (`camera_exports.py`).**
  When an `--align-to` run re-landed this epoch into the reference frame, `shots.geojson`
  camera centres and orientations are now transformed by the recorded re-land transform
  (read from `odm_report/change_detection.json`, gated on the `relanded` marker), so the
  camera positions stay consistent with the re-landed mesh / cloud / orthophoto instead
  of sitting in the old frame. `cameras.json` is unchanged (intrinsics are
  frame-independent). The re-land gate is unit-tested; the pyproj WGS84 path is
  Docker-validated. Closes the last v2 gap of the change-detection item.
- **Change detection — fix: M3C2 no longer misses deep changes (`change_detect.py`).**
  py4dgeo's M3C2 `max_distance` (the cylinder search depth along the normal) defaulted
  to 0, which on the auto-scaled small cylinder of a dense cloud is too shallow — a deep
  excavation (a change larger than the cylinder scale) had no matching surface in range
  and came back NaN / not-significant, even where the DoD clearly measured it. It is now
  set generously (`max(30·cyl_radius, 3 m)`). Found by the new end-to-end smoke
  (`scripts/smoke_change_detect.py`), which the fix takes from M3C2 0 %→significant on a
  0.4 m block.

## [0.6.0] - 2026-06-14

### Added
- **Orthomosaic finishing — radiometric colour balancing (`helpers/ortho_finish.py`).**
  Closes the colour-balance half of the v0.6.0 ortho-finishing item. The single-mesh
  ortho has no stitch seams (nothing to seamline-edit) and inter-view colour is
  already harmonised upstream at the texture atlas, so what was missing was
  *finishing control on the final raster*. New opt-in pass on the rasterised ortho
  (after hole-fill, before GeoTIFF write), masked to valid pixels and nodata-safe:
  `ortho-color-balance` = `white-balance` (gray-world cast removal) or `auto`
  (white-balance + 1–99 percentile luminance contrast stretch); manual
  `ortho-brightness` / `ortho-gamma`; and `ortho-flatten` (0..1), a large-scale
  luminance flatten for a genuine residual exposure gradient — **off by default and
  warned**, because on an excavation ortho a broad soil-colour / feature gradient is
  *data*, not noise. A residual tonal-variation metric (low-frequency luminance std)
  is **always** computed and written to `odm_report/orthophoto_finishing.json`
  (before/after + applied gains/steps), logged on the `[ortho]` line and surfaced in
  the PDF report — directly answering the roadmap's "*if* real orthos show residual
  tonal variation" gate. Default (no options) leaves the ortho bit-for-bit unchanged.
  `tests/test_ortho_finish.py` verifies cast removal, contrast stretch, the
  gradient-removed-but-albedo-preserved flatten (archaeology-safety), the diagnostic,
  and the identity default.
- **Multi-epoch change detection (`helpers/change_detect.py`).** Closes the v0.6.0
  gap to ODM's `--align` / Metashape markers — co-register datasets from different
  seasons onto a shared frame and *measure* the change. Opt-in via the `align-to`
  path option (a node-filesystem path to a prior epoch's
  `odm_georeferencing/odm_georeferenced_model.laz`, mirroring how ODM's `--align`
  takes a reference). The step: **(1)** co-registers this epoch onto the reference
  with PDAL `filters.icp` (the same recipe `scripts/benchmark.sh compare` uses),
  applies the rigid 4×4 to a *working copy* (`odm_change/aligned.laz`), and reports
  the residual (ICP fitness + cloud-to-cloud mean/RMS before vs. after); **(2)**
  **DoD** — rasterises the reference and aligned epoch B to DSMs on a shared grid
  and subtracts them → `odm_dem/dem_difference.tif` with mean/max change, changed
  area, and **cut/fill volumes** (Σ Δz·cell-area); **(3)** **M3C2** (Lague et al.
  2013, via **py4dgeo**) — signed normal-direction distance + per-point
  level-of-detection → `odm_change/m3c2.laz` (extra dims `m3c2_distance` /
  `m3c2_lod` / `significant`). All stats land in `odm_report/change_detection.json`
  (+ rows in the quality-report PDF). Sign convention: positive = surface raised
  (deposition), negative = lowered (excavation). **v1 is additive analysis** —
  epoch B's own cloud/mesh/orthophoto are untouched (re-landing them in the
  reference frame is v2). Non-fatal and self-gating (needs the reference, this
  epoch's LAZ, and PDAL); **py4dgeo absent → DoD-only fallback** (DoD already meets
  the archaeology need). py4dgeo is **built from pinned source** (`1.1.0`) in the
  image — PyPI has no manylinux aarch64 wheel; its multithreaded path segfaults on
  arm64, so the M3C2 call pins `set_num_threads(1)`. New `tests/test_change_detect.py`
  (pure: ICP-metadata parse, DoD/volume math, gate logic; py4dgeo-gated: M3C2
  recovers a known vertical shift). Option count 40 → 41.
- **Multi-class point classification (`helpers/classify_cloud.py`).** Closes the
  v0.6.0 gap to Metashape/ODM — beyond SMRF ground-only. Runs OpenDroneMap's
  **OpenPointClass** (`pcclassify`, the same ML tool + default model ODM uses;
  built from source for arm64 — no prebuilt binary — and the model baked in, both
  pinned) over the georeferenced LAZ, tagging every point with an ASPRS class
  (2 ground, 3/4/5 vegetation, 6 building, 64 vehicle) **in place** so viewers
  colour by class; the EPT is rebuilt from the classified cloud, and class-filtered
  surface rasters (`odm_dem/buildings.tif`, `odm_dem/canopy.tif`) are written. The
  DTM step reuses the ML ground (class 2) instead of re-running SMRF when
  classification ran. Opt-in via `classify`; needs a georeferenced result. AGPL
  tool invoked as a separate process (mere aggregation, as with OpenMVS).
- **3D Tiles export (`helpers/mesh_to_3d_tiles.py`).** OGC 3D Tiles / Cesium
  streaming LOD tileset of the textured mesh (`odm_3d_tiles/tileset.json` +
  `*.b3dm`), closing a gap to ODM — a single glTF is too heavy to stream a large
  scene. Built with OpenDroneMap's **Obj2Tiles** (the same tool ODM uses), baked
  into the image as a pinned, self-contained binary picked by build arch
  (`LinuxArm64` / `Linux64`) — no .NET runtime installed. Globe placement comes
  from the georef offset (pyproj → WGS84 lat/lon, mean-Z altitude, Z-localised OBJ
  = ODM's reference_lla contract). Opt-in via `3d-tiles`; needs a georeferenced
  result; the tileset directory is mapped to `odm_3d_tiles/`.
- **Contours / iso-lines (`helpers/contours.py`).** Vector contour lines from the
  DEM, closing a gap to ODM/Metashape — `odm_dem/contours.gpkg` (3D LineString +
  `elev` attribute, for GIS) and `odm_dem/contours.dxf` (lines at their elevation,
  for CAD). Pure GDAL (the contour API was already in the image — no new
  dependency, no subprocess). Source: the DTM if present (true bare-earth terrain
  contours), else the DSM, logged. Opt-in via `contours-interval` (metres, 0 =
  off); self-skips for non-georeferenced results; nodata is excluded. Verified on
  real drone data (1805 terrain lines at 0.5 m from the DTM).

### Changed
- **Multi-stage Docker build (builder → slim runtime) — CPU image 3.24 GB → 1.65 GB (−49 %).**
  Both `Dockerfile` and `Dockerfile.cpu` are now two-stage. The `engine` builder is
  unchanged (full toolchain + `-dev` headers, compiles COLMAP/OpenMVS/PDAL/entwine/
  pycolmap/py4dgeo, plus Obj2Tiles/OpenPointClass in both images); a new `runtime` stage
  starts from a clean base (`ubuntu:24.04` for CPU, `nvidia/cuda:…-runtime` instead
  of `-devel` for GPU), installs **only the runtime shared libraries**, and copies
  the built artifacts (`/usr/local`, the pip Python packages, `/opt/NodeODM`). The
  runtime apt set was derived empirically with `readelf -d` NEEDED over every engine
  binary (apt then pulls the GDAL/OpenCV transitive tree); the missing-from-runtime
  pure-Python deps (`six`/`requests`/`setuptools`, apt-provided in the builder) were
  added back. The runtime stage **exercises every binary** (`--help` + Python
  imports + `node`) so a missing `.so` fails the *build*, not a user task, and sets
  `WORKDIR /opt/NodeODM` (NodeODM reads `config-default.json` relative to cwd).
  Verified on this host for the CPU image: build gate green, `scripts/test.sh` passes
  *inside* the image, NodeODM serves `/info` + `/options`. The CUDA image mirrors the
  structure (lock-step) and passes `docker build --check`; its CUDA runtime-exercise
  is deferred to a GPU host.
- **GPU production image: Obj2Tiles + OpenPointClass restored (lock-step with the CPU image).**
  The CUDA `Dockerfile` previously omitted both tools, so `--3d-tiles` (Obj2Tiles) and
  `--classify` (OpenPointClass / `pcclassify`) silently failed on the *production* image
  while the CPU test image had them. Both build blocks are now in the `engine` stage,
  with their runtime libs (`libtbb12`, `libicu74`), the baked OPC model + `EFFIGIES_OPC_MODEL`
  env, and `pcclassify` / `Obj2Tiles` added to the runtime exercise gate — matching
  `Dockerfile.cpu`. Lints clean (`docker build --check`); full CUDA build/runtime
  verification is on a GPU host.
- **`InterfaceCOLMAP` resolved through a fail-loud alias lookup (`pipeline/openmvs_bin.sh`).**
  Both `run.sh` and `pipeline/tile.sh` now resolve the OpenMVS COLMAP-interface binary
  via `resolve_openmvs_bin` (tries `InterfaceCOLMAP`, `InterfaceColmap`; aborts with a
  clear FATAL if none is on PATH) instead of hard-coding the name, so a future OpenMVS
  rename/relocation fails clearly rather than as a raw "command not found". The
  Dockerfiles already build-verify `InterfaceCOLMAP` with `command -v`.

### Removed
- **The non-functional `--sparse-engine opensfm` path.** Verifying the
  `InterfaceCOLMAP` / `InterfaceOpenSfM` binary names (ROADMAP item) revealed the
  OpenSfM path was advertised but could never run: OpenMVS **never shipped an
  `InterfaceOpenSfM`** (its v2.4.0 interface apps are InterfaceCOLMAP / MVSNet /
  Metashape / OpenMVG / Polycam; OpenSfM converts via its own `export_openmvs`),
  **and** OpenSfM itself is not installed in either image — so `run.sh` and
  `pipeline/sparse_opensfm.sh` both called missing binaries. Selecting it crashed
  with "command not found", violating the no-fabricated-behaviour rule. Removed:
  the `opensfm` value from the `sparse-engine` option, `pipeline/sparse_opensfm.sh`,
  the `run.sh` OpenSfM/`InterfaceOpenSfM` branch (now fails loudly on any non-colmap
  engine), and the unreachable OpenSfM identity-transform branch in `georef_bridge.py`
  (it wrote `source=opensfm` + an identity matrix claiming success). COLMAP is the
  only SfM front-end; it already covers aerial/GPS sets (EXIF/GCP georef +
  GCP-constrained BA + split-merge tiling). A *real* OpenSfM backend (via
  `opensfm export_openmvs`) for very large GPS-only nadir missions is ROADMAP-parked.

## [0.5.0] - 2026-06-13

### Added
- **OpenMVS dense thread cap (`dense-max-threads`, default 0 = all cores).** Caps the
  worker threads of DensifyPointCloud / ReconstructMesh / RefineMesh / TextureMesh
  (`--max-threads`) to bound the **densify/refine peak** RAM on many-core but
  RAM-constrained hosts — each thread holds image pyramids + working buffers, the
  same OOM risk that already caps COLMAP's CPU SIFT via `cpu-threads`. Wired like
  `cpu-threads` (`EFFIGIES_DENSE_THREADS` env overrides; tiles inherit it via the
  subprocess env); `0` omits the flag, so the default is byte-identical. **Honest
  scope:** it does *not* reduce the `ReconstructMesh` Delaunay tetrahedralization
  peak — that is strictly in-core in OpenMVS 2.4.0; for that memory wall use `tiles`
  (split-merge) or a higher `densify-resolution-level`. This closes the v0.5.0
  "optional out-of-core / cache-to-disk" item: a true out-of-core Delaunay was
  investigated and found infeasible without patching OpenMVS (no `--max-memory`, no
  block processing), and the wall is already addressed by split-merge tiling — so
  only this residual thread cap was kept.
- **Split-merge tiling for large image sets (`tiles=off|auto|N`, `tile-budget`).**
  The open-source analogue of Metashape chunks / ODM split-merge — the path past
  the single-machine RAM wall (Densify + ReconstructMesh's Delaunay). SfM runs once
  on all images; the cameras are partitioned spatially **in that one shared sparse
  frame**, only the dense→mesh→texture chain runs per tile within a memory budget,
  and the per-tile results are merged — alignment is free (every tile inherited the
  same poses; no GPS, no per-tile registration). New `helpers/tiling.py` (pure grid
  partition over camera centres + resumable JSON manifest + per-tile subset COLMAP
  model writer, pycolmap with a struct fallback), `pipeline/tile.sh` (per-tile
  `InterfaceCOLMAP` + the **unchanged** `dense_openmvs.sh` on a tile workdir that
  symlinks the shared undistorted images), and `helpers/tile_merge.py` (crop each
  tile's mesh + cloud to its core bound and concatenate, with atlas/material
  **namespacing** so the merged multi-page OBJ resolves explicitly — never via the
  parsers' glob fallback). Exposure is **harmonised once** on the shared images
  before splitting (sentinel-guarded), so tiles never texture at mismatched
  exposures. The geometry merge runs upstream, so the entire existing downstream
  (georef → LAZ → ortho/DSM → glTF → report → map_outputs) runs **once on the
  merged `$WORK`, byte-identical to the non-tiled path**. **Opt-in, default off**;
  `auto` engages only above the dense-point budget (below it the run is
  byte-identical to today — zero overhead); COLMAP path only. The v1 mesh has
  hairline seams at tile borders (crop-and-concatenate; Metashape/ODM share it).
  Unit-tested end-to-end (partition coverage invariant, halo membership, subset
  model, merge namespacing/anti-collision + multi-atlas consumability, PDAL cloud
  crop+merge); real-data validation (single tile correctness, tiled ≈ single-machine,
  bounded per-tile RAM) deferred to the reference-data campaign.
- **GCP-constrained bundle adjustment (`gcp-bundle-adjust`: `off` / `on` / `auto`).**
  A stronger georeferencing path than the default post-hoc Umeyama similarity: a
  rigid 7-DoF similarity cannot absorb reconstruction **drift** (bending /
  non-uniform scale across the block), leaving a check-point-RMSE floor.
  `helpers/gcp_bundle_adjust.py` (**pycolmap** / COLMAP's own Ceres BA) anchors the
  marked GCPs at their surveyed world coordinates as constant 3D points and
  re-optimises the cameras + tie points to be consistent with them. It runs on the
  **sparse** model *before* `image_undistorter` (injection point in
  `pipeline/sparse_colmap.sh`, after `model_converter`), so densify → mesh → texture
  → ortho all inherit the corrected, world-frame poses. The model is rewritten into
  the offset-world frame and `georef_transform.json` becomes the identity-with-offset
  transform (`source=colmap-gcp-ba`, `s=1, R=I, t=offset`) — the **offset trick**
  keeps every downstream consumer (`pointcloud_to_laz`, `apply_to_obj`, ortho/DSM,
  `coords.txt`) unchanged; `georef_bridge.py` honors that transform instead of
  re-solving.
  - **`auto` mode (compare-and-keep-better).** Runs *both* paths and keeps whichever
    gives the lower **independent check-point RMSE** — a cheap sparse-model
    comparison (no double OpenMVS run): the free model is backed up, the BA is run,
    and the BA is kept only if it beats the post-hoc similarity by both a relative
    margin (10 %, `EFFIGIES_GCP_BA_MARGIN`) and a 1 mm absolute floor
    (`EFFIGIES_GCP_BA_MIN_GAIN_M`); otherwise the free sparse model is restored and
    the post-hoc path runs. **Never worse than the post-hoc path on the check
    metric, by construction.** Needs `check`-flagged GCPs (no honest metric without
    them → falls back to post-hoc). Both RMSEs and the decision are recorded
    (`gcp_ba_arbitration.json`, folded into `georef_transform.json`) for audit.
  - **Check-point convention:** a `gcp_list.txt` line ending in `check` (ODM
    `[extra]` field) is held out of the solve and reported as an independent
    CP-RMSE in `georef_transform.json`.
  pycolmap is built from the pinned COLMAP source into both Dockerfiles (no wheel
  for linux/aarch64+py3.12). **Default `auto`** — chosen for its never-worse-than-
  post-hoc guarantee on the check metric: a GCP run with check points keeps the BA
  only when it measurably wins, and a run without GCPs or check points behaves
  exactly as before (post-hoc / EXIF / local), silently. `off` forces the old
  post-hoc-only behaviour. Real-data accuracy validation (surveyed GCP + held-out
  check points) is still deferred to the v0.8.0 reference-data campaign — that
  campaign measures the *absolute* gain; the default flip rests on the *relative*
  never-worse property, which the synthetic fixture and the in-image runs verify.
- **True-ortho hardening — bounded orthophoto hole-fill.** The orthophoto was
  already a true-ortho (the mesh z-buffer resolves occlusion, no building lean);
  this hardens its coverage. `fill_ortho_holes` (scipy `ndimage`) fills only
  *small interior* nodata holes — pinholes, thin triangle seams, tiny mesh gaps —
  with the nearest valid colour, while *large* voids (missing walls) and the outer
  boundary stay honest nodata: a hole is fully filled (small) or fully untouched
  (large), never smeared at the edge. Controlled by `ortho-fill-holes` (max hole
  area m², default 0.25, 0 disables); default-on but conservative. Affects only
  the visual orthophoto — the DSM, DTM and point cloud are never modified (verified
  byte-identical with fill on/off). No new dependency.
- **DTM (digital terrain model / bare earth) output (`odm_dem/dtm.tif`).** The
  complement of the DSM: `helpers/pointcloud_to_dtm.py` ground-classifies the
  georeferenced dense cloud with PDAL (statistical outlier → SMRF → keep ground)
  and rasterises the ground returns via `writers.gdal` (IDW, single-band Float32,
  nodata −9999) — the same approach ODM uses, with no new dependency (PDAL is
  already built for the LAZ). **Opt-in** (`dtm` option, default off): the ground
  filter costs real time and a bare-earth model is meaningless for close-range /
  object captures with no open ground. Self-skips for non-georeferenced results
  and emits nothing when no ground is found (no bogus all-nodata file). Verified
  on real drone data: the DTM strips ~3.7 m of building roofs vs the DSM. Mapped
  to `odm_dem/dtm.tif` and reported in the quality PDF.
- **DSM (digital surface model) output (`odm_dem/dsm.tif`).** Reaches parity with
  ODM's DEM raster, nearly for free: `helpers/orthophoto.py` already computes a
  per-pixel surface-height z-buffer to resolve occlusion for the orthophoto (the
  z-winner = topmost surface). That height grid — previously discarded — is now
  written as a georeferenced single-band Float32 GeoTIFF (nodata −9999) from the
  **same** nadir rasterisation, so it inherits the RefineMesh detail at no extra
  rasterisation cost and carries absolute elevations (georef keeps Z absolute).
  On by default for georeferenced tasks, `skip-dsm` to disable, auto-skipped for
  local-frame results; mapped to the WebODM `odm_dem/dsm.tif` asset path and
  reported in the quality PDF (px @ cm/px, elevation range). This is the
  *surface* model (buildings/vegetation included); a bare-earth DTM (PDAL ground
  filter) remains a separate future output.
- **Engine-side auto-scaling for large image sets (`pipeline/autoscale.sh`).**
  `run.sh` now counts the images at runtime and, for options the caller did not
  set explicitly, adapts to the count: above ~150 images it switches the COLMAP
  matcher off the O(n²) `exhaustive` strategy to `vocab_tree` (the baked FAISS
  retrieval tree); above ~500 it also prefers the `global` (GLOMAP) mapper and
  bounds full-resolution densify (0→1). Every decision is logged with the
  override flag, an explicit `--matcher exhaustive` is respected (with a warning),
  and a profile's already-scale-safe choice (drone-3d's `spatial`) is left
  untouched. Disable entirely with `--no-auto-scale`. This is the WebODM-side
  "intervene at large counts" mechanism done correctly: the NodeODM `/options`
  contract is static (no engine callback at form time), so the only honest place
  to adapt is the engine itself, transparently and overridably. Thresholds are
  env-tunable (`EFFIGIES_AUTOSCALE_MATCH`, `EFFIGIES_AUTOSCALE_LARGE`).

### Changed
- **Blend memory — peak RSS now independent of the image count (`texture_blend.py`).**
  The multi-view texturing step had three consumers that scaled with the number of
  views and OOM'd large sets: a dense `[faces × views]` weight matrix (~29 GB at
  8 M faces × 900 views), all source images resident (~32 GB), and all depth maps
  at once. Two changes remove all three: **(1) streaming top-K view selection** —
  views are streamed in one pass, each depth map rendered on the fly and discarded,
  a running top-K (`[faces × K]`, ~256 MB, flat in the view count) replacing the
  matrix; **(2) view-major bake** — each atlas page is rasterised into a
  per-(face,texel) row table and sampled view-by-view, so at most **one source
  image is resident at a time** (read ≤ once per page). Peak memory is now governed
  by mesh + atlas size only. Output is preserved: selection is bit-for-bit
  identical to the old argsort path; the view-major bake keeps the original
  two-level accumulation (per-(face,texel) weighted blend, then equal per-pixel
  averaging across overlapping faces) so it matches the reference to within float
  rounding (atol 1 on uint8 — verified bit-exact on the golden scene). This
  completes the ROADMAP v0.5.0 blend-streaming precondition for split-merge tiling.
  Incidentally robust to fewer-than-K views (the old path crashed the bake there).
  New `tests/test_blend.py` (kernel-equivalence + golden) and an `EFFIGIES_BLEND_RSS`
  peak-RSS probe; the large reduced-resolution high-count RSS run is a documented
  manual step. No behaviour change (skip conditions, coverage %, outputs identical).

## [0.3.0] - 2026-06-12

### Added
- **Multi-view GCP triangulation with full lens-distortion handling
  (`helpers/georef_bridge.py`).** A GCP's local position is no longer the
  nearest observed sparse point to the marked pixel (a heuristic limited by
  sparse-point density): every marking is undistorted into a viewing ray —
  supporting all advertised COLMAP camera models (SIMPLE_RADIAL, RADIAL, OPENCV,
  FULL_OPENCV, OPENCV_FISHEYE) via fixed-point inversion of the distortion —
  and the rays of all images the GCP is marked in are intersected in least
  squares, with parallax and cheirality checks. Single-view GCPs fall back to
  the previous heuristic; the per-method counts are reported. On the synthetic
  test scene the triangulated solve is exact to ~2e-7 m where the heuristic
  needed a 1e-3 tolerance.
- **Georeferencing solve-quality reporting.** `georef_transform.json` now
  carries a `residuals` block (count, RMS 3D / horizontal / vertical, max 3D —
  for GCP solves also the triangulated-vs-fallback counts), echoed in the task
  log and as a "Georef RMS error" row in the quality-report PDF. No more
  guessing whether a solve was survey-grade or GPS-noise-grade.
- **Named CRS presets (`crs-preset`).** Regional grids selectable by name in
  the task UI — Israeli TM (EPSG:6991), Palestine 1923 (EPSG:28191), ETRS89 UTM
  32N/33N (EPSG:25832/25833), OSGB (EPSG:27700), Swiss LV95 (EPSG:2056) —
  filling `crs` only when it was not set explicitly (presets, not defaults; the
  engine stays region-agnostic).
- **Exposure/colour harmonisation before texturing
  (`helpers/harmonize_exposure.py`).** With OpenMVS seam leveling disabled (it is
  corrupted on this build), texture patches showed the raw exposure differences
  between photos — a patchy ("fleckig") texture and orthophoto. Now one RGB gain
  per image is estimated from the sparse-point observations (every 3D point seen
  in several images; alternating least squares in log space) and applied to the
  undistorted images before TextureMesh, so the atlas is assembled from
  photometrically consistent photos. On the drone test set the estimated spread
  was 0.59–1.77 (≈3× brightness) across 70 images. New option
  `texture-color-harmonize` (on by default).
- **Node-side EPT point-cloud tileset (Entwine).** The node now ships the same
  Entwine fork+commit ODM pins and builds `entwine_pointcloud/` itself, so the
  Potree viewer gets its tileset directly from the node instead of WebODM
  regenerating it from the LAZ in post-processing. (untwine was evaluated and
  rejected: since 1.x it emits a single COPC file only — it cannot produce the
  `ept.json` directory layout the viewer reads. NodeODM's "PotreeConverter is not
  installed" notice refers to the legacy potree format and is irrelevant once EPT
  is present.)
- **Workdir auto-cleanup after each run (`keep-workdir` to disable).** A full-res
  run leaves ~6-8 GB of intermediates (depth maps, undistorted images, mesh
  snapshots) in the task workdir; with the persistent task volume that exhausted
  the Docker disk after a handful of runs and killed running tasks mid-densify
  (observed twice). The engine now deletes its intermediates at the end of a
  successful run; delivered assets (hard links/copies) and the small text
  diagnostics (georef transform, coords, sparse text model) are kept.
- **Facade/wall recovery levers exposed: `free-space-support` + `mesh-close-holes`.**
  Nadir flights see walls only at grazing angles; with few wall points OpenMVS'
  graph-cut carves facades away (holes), while Metashape's default interpolation
  bridges them closed. `free-space-support` (OpenMVS default off) recovers weakly
  supported surfaces via visibility rays; `mesh-close-holes` raised (e.g. 300)
  bridges remaining holes Metashape-style — interpolated geometry, documented as
  such. Recommended wall test: `number-views-fuse: 2`,
  `densify-resolution-level: 0`, `free-space-support` on.
- **Multi-view blended texturing (`helpers/texture_blend.py`) — Metashape-class
  texture.** TextureMesh's atlas LAYOUT is kept, the CONTENT is re-baked: every
  texel is projected through its 3D position into its best views (top-4, weights
  cos²(view angle)/distance², occlusion-tested against per-view depth maps
  rendered from the mesh) and the harmonised undistorted images are blended.
  This removes the per-view exposure/sharpness blotches a single-view texture
  shows on homogeneous surfaces. Includes `helpers/colmap_bin.py` (reader for
  the binary undistorted PINHOLE model; poses cross-checked against the text
  model to 1e-16). Real 12 MP validation: 99.5% of faces with valid views,
  ~4.7 min for a 1.9M-face mesh, roof-plane brightness std 19.6 -> 16.9 with no
  visible ghosting (cumulative since single-view/global-gain: 23.6 -> 16.9).
  New option `skip-view-blending`. Pipeline order: TextureMesh -> blend ->
  seam leveling -> georeferencing.
- **Own texture seam leveling (`helpers/seam_level.py`).** OpenMVS's seam
  leveling is corrupted (v2.4.0 and master), so Effigies now levels seams
  itself: texture patches are found by value-based connectivity (OpenMVS shares
  no vt indices), seam colours are sampled INSET from each side (the chart
  border pixels are gutter/background), per-(vertex, patch) corrections solve a
  screened-Poisson system (scipy CG; seams matched, harmonic infill inside) and
  are baked into the atlas pages batched/additively. Synthetic two-patch step:
  60 -> 0.3; real 12 MP drone mesh: median seam colour difference halved
  (18 -> 9.3). New option `skip-seam-smoothing` (default off = leveling on).
  Honest scope note: blotchy view-character differences WITHIN patches (exposure/
  sharpness of the source views, visible on homogeneous roofs) are not seam
  offsets — that needs multi-view blended texturing (tracked in ROADMAP).
- **Spatial photometric harmonisation (vignetting correction).** The global
  per-image gain left residual patchiness: a patch textured from an image corner
  stays darker than its neighbour from another image's centre (lens vignetting,
  sky gradients). The harmoniser now also fits one smooth spatial field per image
  (quadratic in normalised image coords, luminance-shared, ridge-regularised,
  zero-mean so the constant stays in the gain) from the same sparse-point
  observations, and divides it out of the undistorted images before texturing.
  On the 12 MP drone set the recovered fields reach the ±60% cap (strong
  vignetting); side-by-side orthos show uniform roof planes and smooth grass
  where the global-only version had visible brightness bands. Solver verified on
  synthetic vignetting (residual 0.009 vs 0.107 global-only) and against
  inventing fields on flat data.
- **WebODM progress bar is driven now.** NodeODM listens on UDP :6367 for ODM's
  `PGUP/<pid>/<uuid>/<percent>` datagrams; the engine never sent them, so tasks
  showed only a spinning "Processing". `pipeline/progress.sh` (sourced by run.sh
  and the pipeline scripts) emits stage-weighted progress — extraction 10,
  matching 32, mapper 38, undistort 42, scene 44, densify 62, mesh 68, refine 74,
  texture 78, then the asset helpers up to 99. Best-effort by design: a progress
  failure can never fail a run.
- **Capture profiles (`profile` option): `drone-3d`, `object`, `architecture`.**
  Versioned parameter bundles inside the engine — applied only for options the
  user did not set explicitly, so individual choices always win. This replaces
  WebODM "presets" as the mechanism for sensible defaults: those are per-install
  JSON keyed to ODM's option names (useless against Effigies), edited in a raw
  admin dialog. A WebODM preset for Effigies now only needs
  `[{"name": "profile", "value": "drone-3d"}]`.
- **All engine tuning parameters exposed as task options** — nothing hidden:
  `refine-max-face-area` (RefineMesh subdivision threshold, was hardcoded 16),
  `refine-gradient-step` (refinement step size, was hardcoded 25.05),
  `cpu-threads` and `cpu-match-block` (the CPU stability caps, previously
  env-only `EFFIGIES_CPU_THREADS`/`EFFIGIES_CPU_MATCH_BLOCK`; an explicitly set
  env var still wins as an ops override).
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

### Changed
- **Base image: Ubuntu 22.04 → 24.04 (noble), CUDA 12.4.1 → 12.8.1.** No legacy
  base for new software: 22.04's standard support ends in under a year and forced
  workarounds (node v12 too old for obj2gltf, nanoflann header overlay, ancient
  OpenCV). On noble the nanoflann vendoring is gone (1.5.4 in apt), node is 18,
  OIIO/ceres/suitesparse are current. Noble dropped PDAL from its repos, so PDAL
  is now **built from pinned source** (2.10.1) like COLMAP/OpenMVS — pin + verify
  instead of distro roulette. The only vendored header remains CGAL 6.0.1
  (OpenMVS 2.4.0 requires ≥6.0; CGAL 6 was released after noble froze).
- **Boolean options follow the ODM flag convention (default-false `skip-`/`no-`
  flags).** WebODM checkboxes can only *send* a flag (checked) or omit it — a
  default-true boolean is impossible to disable from the UI (the tooltip said
  "Default: true" while the box sat unchecked, and checking it could only confirm
  the default). Renamed/inverted: `reconstruct-mesh` → `skip-reconstruct-mesh`,
  `orthophoto` → `skip-orthophoto` (ODM's own name), `texture-color-harmonize` →
  `skip-color-harmonize`, `use-gpu` → `no-gpu`. Unchecked now genuinely means the
  default, checked genuinely disables. (`texture-seam-leveling`, default false,
  was already expressible.)

### Fixed
- **3D model invisible in WebODM's viewer.** Three missing pieces of the ODM
  contract, all fixed: (1) the glb now carries the **`CESIUM_RTC`** extension
  (center = the vertex offset) — WebODM's ModelView translates the glTF scene by
  it, and without it our model sat at the UTM origin, kilometres away from the
  point cloud; (2) the georef float-precision offset is now **2D (x/y only, ODM's
  convention)** so model Z stays absolute and aligns vertically with the
  full-coordinate cloud (the viewer translates by x/y only); (3) ODM-compatible
  **`odm_georeferencing/coords.txt`** is written (the viewer reads the offset from
  line 2), and the legacy OBJ path works too (`odm_textured_model_geo.mtl`
  provided under the exact name the viewer requests, OBJ `mtllib` rewritten).
- **NodeODM crashed on numeric task options ("&lt;uuid&gt; not found" in WebODM).**
  Upstream NodeODM PR #268 (2026-04-30) introduced `shQuote()`, which calls
  `s.replace()` on every option value — numeric options (e.g. `cpu-threads: 12`)
  arrive as JS numbers after NodeODM's own type cast and crash the node the moment
  a task starts; the restarted node then removes the half-created task as
  orphaned, surfacing in WebODM as "not found". NodeODM is now **pinned**
  (`8ad3e30d`, same reproducibility policy as every other component) with a
  one-line type-safety hotfix (`String(s)`) applied at image build — to be dropped
  when upstream fixes the regression.
- **Node task state survives container recreates.** NodeODM keeps its task store
  in `/opt/NodeODM/data` inside the container; the node now runs with a named
  volume (`effigies_data`), so image updates no longer wipe existing tasks.
- **Texture (and therefore orthophoto + 3D model) corrupted by OpenMVS seam
  leveling.** On the arm64/CPU build, OpenMVS 2.4.0's global+local seam leveling
  clamps texture-patch interiors to black and their borders to saturated colours —
  the model texture and the orthophoto came out as black shapes with coloured
  outlines. Seam leveling is now OFF by default (new option
  `texture-seam-leveling` to re-enable on a verified build). Diagnosed on the real
  drone task by re-texturing the same mesh: leveling on → 38% black atlas;
  off → clean photographic patches.
- **Texturing ran at half resolution.** RefineMesh (run with a resolution-level)
  saves its scene with downscaled image references, so TextureMesh sampled 0.75
  MP instead of the full 2.99 MP images. TextureMesh now textures the full-res
  pre-refine scene and injects the refined geometry via `--mesh-file` (output
  names preserved via `-o`) — refine speed kept, texture sharpness doubled.
- **`orthophoto.py` crashed under numpy 2.x** (`ndarray.ptp()` was removed);
  uses the `np.ptp()` function now.
- **`refine-mesh-iters` was advertised but did nothing.** The log claimed
  "RefineMesh x3", yet no iteration flag was passed — `--scales` was hardcoded
  to 1 and the option only gated the stage on/off. OpenMVS 2.4 has no
  `--max-iters`; its iteration lever IS `--scales` ("how many iterations to run
  mesh optimization on multi-scale images"). The option now actually drives
  `RefineMesh --scales` (default 3 = more multi-scale refinement than the old
  hardcoded 1 — quality-first default for the engine's main lever).
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

## [0.2.0] - 2026-06-12

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

[Unreleased]: https://github.com/leiverkus/effigies/compare/v0.7.0...HEAD
[0.7.0]: https://github.com/leiverkus/effigies/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/leiverkus/effigies/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/leiverkus/effigies/compare/v0.3.0...v0.5.0
[0.3.0]: https://github.com/leiverkus/effigies/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/leiverkus/effigies/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/leiverkus/effigies/releases/tag/v0.1.0
