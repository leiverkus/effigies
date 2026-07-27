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

- [x] **Source-built, pinned Dockerfiles.** Both images build COLMAP `4.0.4` (the
      pin at this release; `4.1.1` today — see the GPU-validation section) and
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
- [x] Verify `InterfaceCOLMAP` / `InterfaceOpenSfM` binary names across OpenMVS
      builds and handle the variants. **Done.** Verified against the pinned source:
      OpenMVS v2.4.0 ships `InterfaceCOLMAP` (build-verified in both Dockerfiles)
      but **no `InterfaceOpenSfM`** — that app has never existed (the apps are
      InterfaceCOLMAP / InterfaceMVSNet / InterfaceMetashape / InterfaceOpenMVG /
      InterfacePolycam; OpenSfM converts to `.mvs` via its own `export_openmvs`).
      The `--sparse-engine opensfm` path was doubly dead (OpenSfM not installed in
      the image; `InterfaceOpenSfM` nonexistent) yet advertised, so it was removed
      (see below). `InterfaceCOLMAP` is now resolved through
      `pipeline/openmvs_bin.sh` (known-alias lookup via `command -v`, fail-loud on
      mismatch) in `run.sh` and `tile.sh`, so a future OpenMVS rename fails clearly
      instead of with a raw "command not found".
- [ ] **OpenSfM sparse backend (real).** COLMAP already covers aerial/GPS sets
      (EXIF/GCP georef + GCP-constrained BA + split-merge tiling). OpenSfM's only
      distinctive win is very large GPS-only nadir/corridor missions (its
      GPS-prior incremental SfM drifts less and is CPU-native). If such datasets
      materialise, add it for real: install OpenSfM in both images and convert via
      `opensfm export_openmvs` (NOT the nonexistent `InterfaceOpenSfM`), then
      re-add `opensfm` to the `sparse-engine` option.
- [x] Slim the image with a multi-stage (devel build → runtime copy) layout.
      **Done.** Both Dockerfiles are now two-stage: an `engine` builder (full
      toolchain + `-dev` headers, compiles COLMAP/OpenMVS/PDAL/entwine/pycolmap/
      py4dgeo — plus Obj2Tiles/OpenPointClass, both images) and a slim `runtime` stage
      that installs only the **runtime** shared libraries and copies the built
      artifacts. The runtime apt set was derived **empirically** (`readelf -d`
      NEEDED over every engine binary + the pycolmap/py4dgeo extension modules;
      apt resolves the GDAL/OpenCV transitive tree), and the runtime stage
      **exercises every binary** (`--help` + Python imports) so a missing `.so`
      fails the build, not the user. The CPU image is verified end-to-end on this
      host: build gate green, full `scripts/test.sh` passes *inside* the image,
      NodeODM serves `/info` + `/options`, and the size drops **3.24 GB → 1.65 GB
      (−49 %)**. The CUDA image mirrors the structure on the `-runtime` base
      (vs `-devel`); its CUDA runtime-exercise is deferred to a GPU host (none
      here), but the loader gate + `docker build --check` pass.

## COLMAP 4 migration *(done — folded into v0.2.0)*

Both images are now on **COLMAP 4.1.1 + OpenMVS 2.4.0** (the COLMAP 4 migration
landed on 4.0.4; bumped to 4.1.1 in 2026-07 for the learned front-end), built from identical pinned
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

- [x] **GCP-constrained bundle adjustment** (`--gcp-bundle-adjust off|on|auto`,
      default `auto`). The classic GCP path drives a post-hoc Umeyama similarity on
      triangulated marker points (`georef_bridge.py`); a rigid 7-DoF similarity
      cannot absorb reconstruction **drift**, so the check-point RMSE it leaves is
      a floor. The new path (`helpers/gcp_bundle_adjust.py`, **pycolmap** / COLMAP's
      own Ceres BA) anchors the marked GCPs at their surveyed coordinates as
      constant 3D points and re-optimises cameras + tie points on the **sparse**
      model — *before* `image_undistorter`, so densify / mesh / texture / ortho all
      inherit the corrected, world-frame poses. It rewrites `sparse/0` into the
      offset-world frame and writes `georef_transform.json` as the
      identity-with-offset transform (`source=colmap-gcp-ba`), which the bridge then
      honors instead of re-solving (the *offset trick* keeps every downstream
      consumer unchanged). pycolmap is built from the pinned COLMAP source into both
      images. Check-point convention: a `gcp_list.txt` line ending in `check` is
      held out and reported as an independent CP-RMSE.
      `auto` (the **default**) runs both paths and keeps the BA only if it beats the
      post-hoc check-point RMSE (cheap sparse-model comparison, free model backed up
      and restored on a loss) — *by construction never worse than the post-hoc path*
      on the check metric, which is what justifies it as the default; a run without
      GCPs / check points falls back silently. **Absolute-accuracy validation still
      deferred** to the v0.8.0 reference-data campaign (needs a surveyed GCP +
      held-out check-point dataset) — that measures the gain; the default rests on
      the relative never-worse property, which the synthetic fixture + the in-image
      Stage-0 spike (real 70-image / 34 626-point reconstruction, BA converged in
      ~1 s) verify. Real-data tuning of BA options (gauge, intrinsics, margin) is
      the open piece.

## v0.4.0 — Quality profiles & tuning

- [x] **Capture profiles** as an engine option (`profile`: `drone-3d` / `object` /
      `architecture`): versioned parameter bundles applied for options the user
      did not set explicitly (explicit choices win). Lives in the engine instead
      of WebODM's preset JSON — those are per-install data keyed to ODM's option
      names and useless for Effigies. The bundle *values* are currently reasoned
      defaults; **empirically calibrating them** (esp. `RefineMesh`) per profile
      against benchmark runs is deferred to **v0.8.0** (it needs the benchmark
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
      stock ODM / Metashape / RealityScan are the **v0.8.0** campaign below.

## v0.5.0 — Scaling to large image sets (split-merge tiling) *(released — 2026-06-14)*

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
- [x] **Blend streaming refactor (precondition).** `helpers/texture_blend.py`
      had three image-count-scaling memory consumers (dense `[faces×views]`
      weight matrix ~29 GB, all source images in RAM ~32 GB, all depth maps held
      at once) — a wall of our own making at 900 images. Fixed in two steps:
      **streaming top-K view selection** (depth maps rendered on the fly, running
      top-K instead of the matrix — bit-for-bit identical selection) and a
      **view-major bake** (each page rasterised into a per-(face,texel) table and
      sampled one image at a time — preserves the two-level accumulation, atol-1
      identical). Peak RSS is now governed by mesh + atlas size only.
      `seam_level.py` was *not* affected (scales with atlas + mesh, not image
      count). Design in [docs/blend-streaming-plan.md](docs/blend-streaming-plan.md);
      `tests/test_blend.py` proves equivalence; `EFFIGIES_BLEND_RSS` probes peak
      RSS. The large reduced-res high-count RSS confirmation run is a deferred
      manual step (a toy-scene RSS assertion can't prove N-independence). Landed
      before tiling, as required.
- [x] **Split-merge tiling** (`tiles=off|auto|N`, `tile-budget`; opt-in, default
      off). SfM runs once on the whole set; the cameras are partitioned spatially
      **in that one shared sparse frame** (no GPS, no per-tile alignment), only the
      dense→mesh→texture chain runs per tile within a memory budget, and the
      per-tile meshes + clouds are merged into one set of assets — alignment is free
      because every tile inherited the same poses. `helpers/tiling.py` (pure grid
      partition + manifest + pycolmap/struct subset writer), `pipeline/tile.sh`
      (per-tile `InterfaceCOLMAP` + the **unchanged** `dense_openmvs.sh` on a tile
      workdir that symlinks the shared, once-harmonised undistorted images),
      `helpers/tile_merge.py` (crop-to-core mesh+cloud concat with atlas
      namespacing). The merge runs upstream so the entire existing downstream
      (georef → LAZ → ortho/DSM → glTF → report → map_outputs) runs **once on the
      merged `$WORK`, byte-identical to the non-tiled path**; below the budget
      threshold the run is byte-identical to today (zero overhead). Phases 1–4
      (partition, per-tile orchestration, merge, gating/wiring) landed and unit-
      tested; **Phase 0** (single tile reconstructs correctly from the global
      sparse) and **Phase 5** (tiled ≈ single-machine + bounded per-tile RAM) need a
      real large run and are deferred to the reference-data campaign. v1 mesh-seam
      limitation at tile borders documented (Metashape/ODM share it). Architecture:
      [docs/split-merge-tiling-plan.md](docs/split-merge-tiling-plan.md).
- [x] ~~Optional: out-of-core / cache-to-disk for the dense + Delaunay stages~~
      **— superseded; not feasible as stated.** Investigated against the OpenMVS
      2.4.0 binaries: `ReconstructMesh`'s Delaunay tetrahedralization is **strictly
      in-core** (no `--max-memory`, no block/chunk processing, no disk-cache), so a
      true out-of-core Delaunay would need patching OpenMVS internals (out of scope).
      The memory wall is only movable by fewer points (`densify-resolution-level` /
      `number-views-fuse` — already options) or splitting (**split-merge tiling —
      shipped**), which already meets the item's goal. The one residual worth keeping
      landed: **`dense-max-threads`** (OpenMVS `--max-threads`, default 0 = all cores)
      bounds the densify/refine **peak** RAM on many-core, RAM-constrained hosts —
      the same rationale as `cpu-threads` for COLMAP SIFT — but explicitly does *not*
      touch the Delaunay wall.

## v0.6.0 — Capability parity *(released — 2026-06-14; buildable gaps vs ODM / Metashape / RealityCapture)*

Real capability gaps surfaced by a head-to-head review against the competition —
features they ship and we don't (yet), but that are buildable. Distinct from the
deliberate non-goals (multispectral / thermal / multi-camera rigs; GUI — that is
WebODM's role) and from the GPU/maturity gaps tracked elsewhere.

- [x] **Contours / iso-lines (DXF + GeoPackage).** `helpers/contours.py` runs the
      GDAL contour API (no new dependency, no subprocess) over the DTM if present
      (bare-earth terrain contours), else the DSM, at a configurable
      `contours-interval` (m; 0 = off) → `odm_dem/contours.gpkg` (3D LineString +
      `elev` attribute, for GIS) and `odm_dem/contours.dxf` (lines at their
      elevation, for CAD). Self-skips for non-georeferenced results. Verified on
      real data (1805 terrain lines at 0.5 m from the DTM).
- [x] **3D Tiles / Cesium streaming.** `helpers/mesh_to_3d_tiles.py` runs
      OpenDroneMap's **Obj2Tiles** (the same tool ODM uses; a pinned, self-contained
      arm64/x64 binary baked into the image — no .NET runtime) over the textured
      OBJ to build an OGC 3D Tiles LOD tileset (`odm_3d_tiles/tileset.json` +
      `*.b3dm`) for web/Cesium streaming of large scenes. Placement from the georef
      offset (pyproj → WGS84 lat/lon, mean-Z altitude, Z-localised OBJ — ODM's
      reference_lla contract). Opt-in (`3d-tiles`); needs a georeferenced result.
- [x] **Point classification beyond ground.** `helpers/classify_cloud.py` runs
      OpenDroneMap's **OpenPointClass** (the ML classifier ODM uses; built from
      source for arm64 + a pinned model baked into the image) over the
      georeferenced LAZ → ASPRS classes (ground, low/med/high vegetation, building,
      vehicle) written into the cloud, the EPT rebuilt so Potree colours by class,
      and class-filtered surface rasters (`odm_dem/buildings.tif`, `canopy.tif`).
      The DTM reuses the ML ground (class 2) instead of re-running SMRF. Opt-in
      (`classify`); needs a georeferenced result.
- [x] **Multi-epoch / change detection / co-registration.** `helpers/change_detect.py`
      co-registers this epoch onto a prior epoch's reference cloud (PDAL `filters.icp`,
      the same recipe `scripts/benchmark.sh compare` uses) and emits difference
      products: a **DEM-of-Difference** (`odm_dem/dem_difference.tif`) with mean/max
      change, changed area, and **cut/fill volumes** (Σ Δz·cell-area on a shared grid),
      plus an **M3C2** change cloud (`odm_change/m3c2.laz`, signed normal-direction
      distance + per-point level-of-detection, via **py4dgeo** built from source —
      there is no manylinux aarch64 wheel) and a `odm_report/change_detection.json`
      with the co-registration residual (ICP fitness + C2C before/after) and all
      stats. Opt-in via the `align-to` path option (mirrors ODM's `--align`); needs a
      georeferenced result; py4dgeo absent → DoD-only fallback. **v1 is additive
      analysis** — epoch B's own cloud/mesh/ortho stay in their georef frame.
      **Detectability vs small errors:** ICP removes the *rigid* relative georef
      offset first, so the floor is the post-ICP residual + roughness, not the
      absolute georef stddev. The **M3C2 LoD now folds in the co-registration
      residual** (post-ICP C2C → py4dgeo `registration_error`, Lague 2013), so a
      cm-level alignment error is not read as real change (done; unit-tested that a
      5 cm residual lifts the LoD). The **DoD is now thresholded at a minimum LoD**
      too (Wheaton 2010 — robust noise floor of the difference, floored by the
      co-registration residual; `min_lod_from_dod`), so sub-LoD noise no longer
      inflates the changed area or the fill/cut volumes (a raw net is kept as a
      cross-check). **Co-registration is now stable-area-masked** (two-pass: a
      whole-cloud ICP, then a re-fit on only the unchanged ground via `stable_mask`),
      so a localised change no longer biases the rigid transform and M3C2/DoD get a
      clean *registration-only* error instead of the conservative full-cloud C2C
      (`coreg_reg_error`; degrades to the whole-cloud fit — and says so — when too
      little stable ground remains). **Re-landing is now the default** (`--align`
      parity): `reland_assets` applies the recovered transform to the delivered mesh +
      cloud in place (offset-aware OBJ via `transform_obj`, LAZ via PDAL, EPT rebuilt),
      and because it runs *before* the raster stages the DSM/DTM/ortho/contours/glTF/
      3D-Tiles inherit the reference frame natively (`--no-reland` keeps additive-only).
      **DEM-as-reference** is now supported too: an `--align-to` that is a DEM GeoTIFF
      (a prior DSM/DEM) is read as cell-centre points for ICP/M3C2 and used directly as
      the reference DSM for the DoD (`is_dem` / `dem_to_xyz` / `resample_dem`). The
      **camera assets** are re-landed too — `camera_exports` transforms the
      `shots.geojson` camera centres + orientations by the recorded re-land transform
      (gated on the `relanded` marker), so the whole asset set is frame-consistent.
      **The v2 list for this item is now complete.** Residual risk neither LoD catches:
      non-rigid SfM doming (James 2020) — mitigated by GCP/BA, not by the LoD. The
      stable mask itself still assumes a mostly-stable scene. Verified: M3C2 recovers a
      known vertical shift, registration_error raises the LoD, DoD minLoD masks sub-LoD
      noise, stable_mask separates change from stable ground, transform_obj is
      offset-exact, dem_to_xyz loads a DEM as cell-centre points, the camera re-land
      gate is unit-tested, volume math unit-tested; the full re-land pipeline (raster
      re-derivation, py4dgeo, pyproj) is Docker-validated end-to-end by
      `scripts/smoke_change_detect.py` (synthesises a two-epoch case — known rigid
      offset + excavation block — runs the real CLIs and asserts the products).
- [x] **Orthomosaic finishing.** Seamline editing + radiometric colour balancing
      (Metashape/ODM). Our single-mesh ortho needs no seamlines but also offers no
      such control; expose colour-balance / blending knobs if real orthos show
      residual tonal variation. **Done (colour-balance half; seamlines N/A):**
      `helpers/ortho_finish.py` adds an opt-in finishing pass on the rasterised ortho
      — `ortho-color-balance` (gray-world white-balance / `auto` = + percentile
      contrast), manual `ortho-brightness` / `ortho-gamma`, and an off-by-default,
      explicitly-warned `ortho-flatten` (large-scale luminance flatten; can erase
      real soil/feature albedo). A residual tonal-variation metric is always measured
      and written to `odm_report/orthophoto_finishing.json` + the report, so the
      "if real orthos show residual tonal variation" question is answered per dataset
      rather than guessed. Default output is bit-for-bit unchanged; nodata-safe;
      unit-tested incl. a gradient-removed-but-albedo-preserved flatten check.

## v0.7.0 — Semantic field v0 + change-detection accuracy *(released — 2026-06-15; ships the v0 geometry field + multi-epoch propagation + change-detection LoD/re-land hardening; the full mesh-rasterise, fine-class model and contract carry forward)*

The bridge to **Structura**, the downstream vectorisation project (orthophoto/DEM
→ georeferenced excavation vectors in PostGIS). The division of labour is
**field vs object**, not raster vs vector — a boundary that survives the move into
3D: **Effigies owns the semantic *field* in geometry-space** (per-point / -vertex /
-pixel class, multi-view- and multi-epoch-consistent); **Structura owns the
semantic *objects* in vector/DB-space** (instances, topology, stratigraphic
attribution). Effigies ships only the **mechanism** — classify → rasterise →
propagate — and **never bakes an archaeological-material model into the shipped image**.

> **Released in v0.7.0 (2026-06-15):** the v0 geometry-derived field, multi-epoch
> propagation (the two `[x]` items below), and the post-v0.6.0 change-detection accuracy
> hardening — M3C2 level-of-detection with the co-registration residual, DoD min-LoD
> (Wheaton 2010), stable-area-masked ICP, `--align`-parity re-landing of the deliverables,
> DEM-as-reference, and camera-asset re-land. **Carried forward (post-v0.7.0):** the three
> unchecked items below — the full mesh-classify → z-buffer `--semantic` path, the
> fine-class material model (Structura's deliverable, itself data-blocked), and contract
> finalisation. **Of those, the mesh z-buffer `--semantic` path shipped 2026-07-26**
> (see the first item); the fine-class model and the contract remain open.

- [x] **`--semantic` from a 3D class field via the ortho z-buffer — shipped 2026-07-26.**
      Cloud classes are transferred to the mesh **vertices** by 3D nearest neighbour
      (`scipy.cKDTree`; 3D, not plan-view — a 2D match assigns the wrong class wherever
      the surface folds back under an eave or baulk overhang), reduced to a per-triangle
      class (majority, ties to the lowest code so the result never depends on OBJ vertex
      order), and read off the triangle that **won the orthophoto's z-buffer** at each
      pixel.

      The mechanism is the point: `rasterize()` gained an optional `tri_out` that records
      the winning triangle index in the pass it already performs, so the class raster is
      **pixel-identical** to the RGB ortho and the DSM — same grid, same geometry, same
      occlusion decisions. A second rasterisation could drift on any of the three. Cost
      is one scatter-write per winning pixel and nothing when unrequested (verified by
      test: the ortho and DSM outputs are bit-identical with and without `tri_out`).
      Gains over the v0 cloud path: **occlusion-correct** (only the nadir-visible surface
      contributes) and it **inherits the RefineMesh geometry**, so class edges land on the
      refined surface instead of a per-cell majority of scattered points.
      `orthophoto.py --semantic-cloud` + `semantic_ortho.run_semantic_mesh`; legend
      version `v1-mesh`. The cloud-majority v0 stays as the fallback for when there is a
      classified cloud but no rasterised grid. Self-skips for local-frame results,
      non-fatal throughout.

      **Validated on real data 2026-07-26** — Tiberias 2023, 400 oblique Anafi images
      at 15 m AGL (~5 mm GSD), deliberately not a nadir block so the occlusion logic is
      exercised. Grid identity holds across *separate invocations*: RGB ortho, DSM and
      class raster all 3116×2659 at 3.173 cm, geotransform identical to nine decimals.
      Field is differentiated: ground 17.7 %, vegetation 48.6 %, structure 33.6 % of the
      classified 53 %. Two findings kept in CHANGELOG: before the coordinate-frame fix
      the same run yielded ONE class over 100 % of the classified area (a plausible-
      looking uniform raster), and area share inverts against point share (10.4 M
      `building` points → 33.6 % of area; 2.15 M vegetation points → 48.6 %) because
      masonry is surface-rich per unit plan area. OpenPointClass on an excavation
      separates masonry / soil / plants usefully even though the class *names* stay
      domain-foreign — it does not remove the need for the fine-class model.
- [x] **v0 is free from the existing point classification — shipped.**
      `helpers/semantic_ortho.py` (opt-in `--semantic`) rasterises the OpenPointClass
      cloud classes onto the orthophoto grid (pixel-aligned with `odm_dem/dsm.tif`),
      per-cell **majority** class → ground / vegetation / structure, as
      `odm_semantic/orthophoto_semantic.tif` (Byte + colour table) + a legend JSON. No
      model cost — needs only `--classify`; self-skips otherwise. Unit-tested
      (majority + ASPRS→v0 + write round-trip) and image-validated end-to-end. The fine
      material classes below remain the trained-model step.
- [ ] **Fine archaeological classes = bring-your-own model (Structura's
      deliverable).** Stone / earth / ceramic / mortar is a trained **2D
      image** semantic model (labels are cheap in 2D; foundation-model leverage),
      run per-view and **fused onto the mesh via the existing multi-view blend**
      (`texture_blend.py`) to give one class per 3D point — multi-view-consistent,
      and it sees the **vertical / occluded surfaces (profiles)** the nadir ortho
      loses. The model is a **versioned weights asset** loaded like the vocab tree /
      OpenPointClass model, never baked in (likely non-commercial research weights —
      same opt-in pattern as the SuperPoint / MASt3R items). **Paving is not a field
      class** — it is the same *material* as a single stone and differs only in
      *arrangement*; Structura derives it in the object layer (a configuration of
      stone instances), so the field stays material-only.
- [x] **Multi-epoch propagation (the temporal kicker) — shipped (v0).** Because
      change detection re-lands this epoch into the reference frame, this epoch's
      semantic ortho is already co-registered with the reference epoch's, so
      `helpers/semantic_propagate.py` (runs under `--semantic` when `--align-to` is
      given) carries the class field across epochs: a **carry-forward** field
      (`orthophoto_semantic_propagated.tif` — unobserved cells inherit the reference
      class, honest "no-change-where-unobserved" assumption) **and** a **semantic-change**
      raster (`semantic_change.tif` + per-transition area in `odm_report/semantic_change.json`
      — the class complement of the DoD/M3C2: e.g. structure→ground = a feature removed).
      Reference resampled nearest (categorical). Unit-tested + end-to-end validated.
      Effigies carries the **class field**; Structura carries **object / Befund
      identity** in PostGIS — two temporal mechanisms, each where its information
      lives, so **Effigies never reads the DB**.
- [ ] **Cross-project contract.** Runtime flow stays one-directional
      (Effigies → Structura); the only backflow is the trained model **as a build
      artifact** (produced / retrained in Structura's research, dropped into
      Effigies). Validation of the semantic ortho's archaeological usefulness is
      Structura's evaluation, not Effigies'. See the Structura research plan.
      Handoff notes for a parallel session: [`docs/briefing-structura-2026-07-26.md`](docs/briefing-structura-2026-07-26.md).
      **Downstream decided 2026-07-26 (Structura ADR-0001, *Proposed*):** Structura
      hands off a **GeoPackage file**; Contexta imports it through a management
      command via the ORM. `PostGISSink` is withdrawn — so the line above ("Structura
      carries object identity *in PostGIS*") describes where the identity ends up,
      not who writes it. Nothing for Effigies to change: the flow stays
      one-directional and Effigies still never reads the DB. One consequence does
      land here, though: Structura's `FileSink` deliberately **never reprojects**,
      and Contexta's target CRS is **per site** (`Site.srid`; 6991 / 28191) rather
      than global. That makes Effigies' `--crs` the place where the site's grid is
      fixed, and makes the explicit-CRS constraint (#4) load-bearing for the whole
      chain, not just for our own outputs.

## v0.8.0 — Benchmark campaign & profile calibration *(needs reference data)*

The empirical work behind the paper, split out from v0.4.0 (the *tooling* is
done; the *runs* are here). Gated on a dataset with **reference data** — a TLS
scan and/or surveyed check points — for absolute accuracy; relative metrics
(roughness, detail, completeness, runtime) can proceed without it.

**The surveyed-check-point half of that gate is now open.**

- [x] **Check-point RMSE measured — 3.6 cm 3D, out of sample** (2026-07-26).
      First absolute-accuracy figure for this engine that is *not* a fit residual.
      Zionsberg 2023 trench 6-2, 339 close-range iPhone images, GCPs from
      total-station survey via `metashape_gcp_export.py`, control/check split by
      `scripts/gcp_check_split.py` (7 control / 3 check, rule fixed before any
      number existed).

      | | |
      |---|---|
      | **CP-RMSE, post-hoc similarity** | **0.0361 m** |
      | CP-RMSE, GCP-constrained BA | 0.0397 m |
      | Fit residual over all 10 GCPs | 0.0368 m (horiz 0.0185 / vert 0.0318) |
      | Worst single point | 0.0796 m |
      | GCP localization | 10/10 triangulated, 0 nearest-point fallbacks |

      Two things worth keeping. **Fit residual and out-of-sample error agree**
      (3.68 vs 3.61 cm), so the similarity generalises — no sign of overfitting to
      the control points. And **`--gcp-bundle-adjust auto` rejected the bundle
      adjustment**: 0.0397 m vs 0.0361 m for the post-hoc similarity, so it
      restored the free sparse model. That arbitration is impossible without check
      points, so this run is also the first functional proof of that mode.

      Caveat, flagged by the split tool before the run: the worst point is 2.2× the
      RMS, and with only 3 check points a single target can dominate. The prime
      suspect is `target 4`, which carries 4 observations against a median of 8 —
      a weakly triangulated position whose residual mixes marking noise into the
      georeferencing error. Read `rms_3d` with `max_3d`, and prefer projects with
      ≥ 10 well-observed targets when this is repeated. 31 further `gcp_list.txt`
      files are available from the same campaign for exactly that.

      Still missing for the *other* half of the gate: an independent reference
      surface (TLS scan) for cloud/mesh-to-reference distance. Check points bound
      the georeferencing, not the geometry.

- [ ] **Comparison runs.** **Started 2026-07-27: first Metashape run measured** on
      block 2 (382 images), both sides emitting the full product set at a pinned
      orthophoto GSD — full table in
      [docs/planned-experiments.md](docs/planned-experiments.md). Headline
      **2 h 37 m 30 vs 56 m 06 (2.8x)**, but **71 of our 157 minutes are stages
      Metashape does not run** (RefineMesh 28 m 41, multi-view blend 42 m 24) — more
      than its entire run; without them the factor is 1.5. Comparable stages split
      three ways: sparse **3.0x slower** (the mapper is already fixed by GLOMAP; the
      rest is `image_undistorter`, which Metashape needs no equivalent of), texture
      atlas **4.9x slower** (OpenMVS' packing, and it compounds with `mfa-8`), dense
      **12 % faster per point**, orthophoto **at parity** (238 Mpx in 5 m 59 vs 290 Mpx
      in 6 m 06). Structural advantage that survives the runtime loss: our DSM is the
      z-buffer of the ortho's own rasterisation, so the two share a grid byte for byte,
      while Metashape's DEM and ortho land ~5 px apart even when pinned to one GSD.
      Still missing for the *quality* half — this measured runtime and products only:
      cloud/mesh-to-reference distance, roughness, completeness. The workdir of the
      Effigies run is kept (`--keep-workdir true`) so those can be computed without
      re-running the 2.5 h chain.
      Remaining engines and the original scope:
      Process shared datasets through Effigies, stock ODM,
      Metashape and (where available) RealityScan, and compute the
      `scripts/benchmark.sh` metrics — cloud/mesh-to-reference distance,
      check-point RMSE, surface roughness, completeness, runtime. No prior study
      benchmarks COLMAP + OpenMVS *with RefineMesh* against the commercial tools,
      so this is a publishable contribution, not just an internal check. The
      honest headline is narrow-but-deep: refined-mesh surface detail (see the
      ODM comparison in the v0.4.0 notes — ODM leads on ortho maturity / DSM-DTM
      breadth / scaling, Effigies on RefineMesh geometry).
- [x] **Two queued single-variable experiments** — **both run 2026-07-25** against
      `gpu-base-01`; full results in
      [docs/planned-experiments.md](docs/planned-experiments.md).
      **A (watertightness, `mesh-close-holes` 30→500):** confirmed and free —
      boundary edges −54 %, interior ortho nodata −76 %, runtime +0.1 %. It does
      not close everything (5 590 open edges remain), which is the honest shape of
      the result on a nadir-only block, not an under-setting.
      **B (`densify-resolution-level` 0→1):** runtime −67.6 % (1 h 00 m 41 s →
      19 m 38 s), well beyond the predicted ~40 %, with the saving coming from the
      cascade into RefineMesh/TextureMesh rather than densify itself. **But the
      quality premise was contradicted** — the refined mesh lost 78.5 % of its
      faces, so RefineMesh does *not* re-supply detail independently of densify
      density. **And the test was not actually single-variable:**
      `refine-max-face-area` is a *pixel*-area threshold, so halving image
      resolution quarters it and silently coarsens the refinement target too.
- [x] **Compensation run: res-1 with `refine-max-face-area 4`** (`expC`, 2026-07-25)
      — the run that isolates densify resolution by holding the refinement target
      geometrically constant. **There is no free lunch.** Compensating restored
      faces 2.28 M → 6.57 M but not to the baseline's 10.61 M, so res-1 still costs
      **38 % of the face density** even with the threshold corrected: densify
      density *does* feed final detail, contrary to the premise. And the runtime
      saving collapses from −67.6 % to **−9.6 %**, because TextureMesh **more than
      doubles** (17 m 44 s → 37 m 22 s). Cause is patch count, not faces: 62 % of
      the baseline's faces but **1.83× its texture patches** (fine subdivision on
      half-resolution imagery fragments view selection), and atlas packing is
      superlinear. `res-1` + `mfa-4` is the worst of the three combinations.
- [x] **`mfa-8` run (`expD`, 2026-07-26) — the knee is located.** It contradicted the
      expectation stated after run C (that the patch curve left no useful
      intermediate). At res-1, `refine-max-face-area 8` gives **27 m 35 s (−54.6 %)**
      for **3 876 353 faces** — +69.8 % geometry over the `mfa-16` default for +40.5 %
      runtime, whereas the next identical step (`mfa 8→4`) buys +69.6 % for +98.9 %.
      Marginal cost doubles across `mfa-8`. Its atlas stage (7 m 51 s, 212 576
      patches) is still **cheaper than the full-res baseline's** (9 m 51 s, 232 716)
      — the blow-up starts past this point, not at it. Interior ortho nodata is the
      **lowest of all four runs**. Reasoning from the curve's shape without the
      middle measurement produced the wrong call.
- [x] **Second-block confirmation (2026-07-27) — the knee holds.** Three runs
      (`mfa` 16/8/4, res-1, everything else the `gpu-base-01` recipe) on **block 2**:
      Tiberias 20230309, Parrot Anafi, 382 images at 16 MP, 379 registered. The
      marginal-cost doubling reproduces and is **sharper**: 16→8 buys +88.0 % faces
      for **+22.8 %** OpenMVS time, 8→4 buys +92.3 % for **+105.6 %** — cost per unit
      face gain 0.26 → 1.14 (4.4×, against 2.4× on block 1). Patches-per-face climbs
      monotonically as before, atlas cost superlinear in it.
      **Not a controlled replication:** site, camera, sensor, image count *and*
      capture geometry all differ — block 2 has **zero nadir images** (median gimbal
      pitch −70.0°) where block 1 was nadir by construction. No second nadir block
      exists in the available data. A knee that *held* across two blocks this
      different is the strong outcome; a moved knee would have been unattributable.
      **One block-1 sub-claim is refuted:** `mfa-8` is *not* a coverage optimum.
      Interior ortho nodata is flat on block 2 (1.990 / 1.966 / 1.956 % across a 3.6×
      face range — noise), so run D's "coverage is best here" was a dataset artefact
      and is withdrawn. The recommendation survives on runtime and geometry.
      Built-in controls passed: densify points and ReconstructMesh faces flat to
      1.2 % / 0.5 % across the three runs, under the noise floor.
- [ ] **Adopt `refine-max-face-area 8` in the `drone-3d` profile** (from 16), keeping
      `densify-resolution-level 1`. Recommendation and both four-/three-point tables in
      [docs/planned-experiments.md](docs/planned-experiments.md). `res-0` stays the
      max-detail option; `mfa-4` must not be adopted. **The second-block gate above is
      now met** — what remains is the decision to change a shipped default, plus the
      standing caveat that each setting was run once per block and both blocks are
      Southern-Levant archaeology at 12–16 MP.

      **DECIDED 2026-07-27: ship it as a bundle, not on its own.** On its own this
      change makes every drone run slower — +17 min OpenMVS on block 2 (1 h 15 m 30 →
      1 h 32 m 41) — and it also costs **+33 % RAM peak** (38.9 → 51.6 GB, which lowers
      the maximum set size against the documented 125 GB ceiling) and **doubles the
      textured OBJ** (751 MB → 1.4 GB per task in WebODM storage). Those two are not
      bought back by any speedup; they belong in the CHANGELOG entry when it ships.
      Two measured savings are queued against it, both pure waste rather than quality:
      **GLOMAP** (`mapper global`, −5 min sparse *and* better on every sparse metric —
      382/382 images registered vs 379, 1.137 px vs 1.164) and **`texture_blend`
      phase 1**, which the 2026-07-27 instrumented run shows to be essentially the
      whole ~45 min, with two memory-neutral attack points (frustum-cull in
      `render_depth`; reuse of the visibility OpenMVS' `TextureMesh` already computes).
      Ship `mfa-8` together with those, so the user-visible net is *more quality at
      equal or better runtime* instead of *slower for more faces*. Blocking on: the
      GLOMAP end-to-end verification (15 % fewer sparse points → densify?) and at
      least one of the two blend fixes.
      Note: `mfa-8` is **already live in the `object` profile** (run.sh) — this item is
      only about the aerial path.
- [ ] **COLMAP's GPU bundle adjustment is built but never switched on.** Surfaced
      2026-07-27 while starting the Metashape comparison, and it looks like the single
      largest cheap win in the pipeline. Measured on block 2 (382 images, 16 MP), the
      sparse stage splits as `spatial_matcher` **1 m 12** + `mapper` **11 m 12** ≈
      14 m 30. Metashape 2.3.1 on an M3 Max does the same work in **3 m 13**
      (`matchPhotos` 2 m 07 + `alignCameras` 1 m 06) — i.e. **our matching is already
      faster**, and the entire gap is the incremental mapper, a factor of ~10.
      Meanwhile COLMAP 4.1.1 — which we adopted *today*, and whose CHANGELOG entry in
      this repo even names the "**Caspar** GPU bundle-adjustment backend (1–2 orders of
      magnitude faster than the Ceres CUDA backend)" — exposes
      `--Mapper.ba_use_gpu (=0)`, `--Mapper.ba_local_backend (=CERES)` and
      `--Mapper.ba_global_backend (=CERES)`. `pipeline/sparse_colmap.sh` passes **none
      of them**, so we build the GPU backend and then bundle-adjust on the CPU by
      default.
      Not yet a proven win: the mapper does more than bundle adjustment, and BA's share
      of those 11 m is unmeasured. But it is single-variable testable and cheap — same
      block, `--Mapper.ba_use_gpu 1`. First step is establishing the accepted
      `ba_*_backend` values (COLMAP prints no enumeration on an invalid value).
      Cross-reference: the hardware differs (A4000 host vs M3 Max laptop), so the
      factor of 10 is indicative, not a clean benchmark — but the *split* between
      matching and mapping is measured on our side alone and stands on its own.
- [ ] **`texture_blend.py` is serial and it is the largest post-processing cost.**
      **MEASURED 2026-07-27 — and the split refutes the plan written below.** The
      instrumented markers on an 11.3 M-face / 382-view / 11-page run:
      `entry -> after view selection` **21 m 19**, `after view selection -> exit`
      **21 m 04**, total **42 m 24**. That is **50.3 % / 49.7 %** — not the
      phase-1-dominated profile assumed here. Consequences: page-parallel baking,
      dismissed below as "nearly useless", addresses **half** the cost and is the
      memory-cheap option (one atlas page is resident at a time anyway); the
      frustum-cull and the OpenMVS-visibility reuse address the other half. Do both,
      cheapest first, rather than either on its own. Peak RSS at the phase boundary is
      **11 GB** before any worker is added — that is the budget any parallel version
      starts from. Note the phase-1 figure still includes parsing the 1.4 GB textured
      OBJ, so `select_views` alone is *less* than 21 m; do not over-attribute it
      (an earlier offline attempt to measure this failed outright: `run.sh` rewrites
      the OBJ into the georeferenced frame in place while the COLMAP poses stay local,
      so a rebuilt workdir yields 2.9 % valid views instead of 99.5 %).

      On block 2's `mfa-4` run it took **~60 minutes single-threaded at 100 % of one
      core**, against 4 minutes for the entire post-processing block on the 110-image
      reference. Cause is structural, not accidental: `select_views` streams the views
      in a Python loop by design (the *blend streaming refactor* traded parallelism for
      memory flat in view count), and the inner work is numpy elementwise / fancy
      indexing plus an unbuffered `np.minimum.at` scatter-reduce — none of which numpy
      threads. `NLWP` is 1; not even a BLAS pool is created. Cost scales as
      **views × faces**, so it grows fastest exactly where the node is most useful.
      The view loop is embarrassingly parallel (each view yields an independent weight
      vector); the obstacle is the running top-K reduction, which is fixable by
      per-worker top-K plus an associative pairwise merge. The price is memory —
      `W × (nF·K)`, ~350 MB per worker of indices alone at 22 M faces — i.e. a genuine
      conflict with the RAM ceiling, which is why the serial version exists. Decide
      deliberately; do not "just parallelise it".
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

## v1.1.0 — MASt3R sparse-engine *(post-1.0 experiment — not production-ready)*

**Explicitly post-1.0 and experimental.** MASt3R is **not production-ready** — a
non-commercial licence plus research-grade maturity (both below) — so it sits *after*
v1.0.0 as an opt-in experiment, **not** in the shipping path: the engine reaches 1.0
without it.

COLMAP — including the deferred learned front-end (LightGlue, below) — is still **correspondence-based**
SfM: it needs enough matchable points across enough overlap. That breaks down on
small, low-overlap, textureless/glossy object sets — exactly **artefacts,
ceramics, statues, fine architectural detail**. **MASt3R** (Naver, the DUSt3R
line) regresses dense pointmaps directly from image pairs and reconstructs poses +
a sparse model *without* keypoint matching, robust where correspondence SfM has no
signal at all. **Not a replacement** — an additional `--sparse-engine` value for
the **low-overlap / textureless** regime where it wins. This is a different architectural
layer from LightGlue: LightGlue lifts the SIFT *matcher* feeding `colmap mapper`;
MASt3R replaces the whole SfM *front-end*.

- [ ] **`--sparse-engine mast3r`, re-activating the multi-valued option.**
      `sparse-engine` is currently colmap-only (`domain: ["colmap"]`); this
      re-introduces real alternatives. MASt3R-SfM runs, then **exports a
      COLMAP-format sparse model into `$WORK/sparse/0`** — the exact seam the parked
      OpenSfM backend would use — so `image_undistorter` + the entire OpenMVS chain
      run **unchanged**. Shares its integration contract with the OpenSfM park item.
- [ ] **Sparse stage only — RefineMesh stays the point.** MASt3R also yields dense
      pointmaps, but short-circuiting OpenMVS would drop ReconstructMesh/RefineMesh
      — the entire reason this node exists. MASt3R supplies **poses + sparse cloud
      only**; the unchanged Densify → Reconstruct → Refine → Texture chain consumes
      it. Scale stays the existing georef job (MASt3R is up-to-scale; the GCP/EXIF +
      offset/Umeyama machinery applies unchanged).
- [ ] **Cost, not a scaling wall (corrected 2026-06-14).** The earlier "quadratic,
      small-N-only" framing is **outdated**: MASt3R-**SfM** (arXiv 2409.19152) uses
      foundation-model **image retrieval** to bring the scene graph to **~linear** and
      handles up to **~1000 images**. The real limit is **cost**, not image count — it
      is GPU-heavy and slow (≈ 200 images / 27 min on GPU vs COLMAP), and those are
      benchmark conditions, not robustness on arbitrary excavation data. So `mast3r` is
      gated by cost + the blockers below (licence, maturity), **not** by N;
      `autoscale.sh` / split-merge tiling stay COLMAP's domain regardless.
- [ ] **Licensing — the hard blocker (verified 2026-06-14).** DUSt3R/MASt3R code
      **and** weights (Naver) are **CC BY-NC-SA 4.0 (non-commercial)**, and there is
      **no public commercial-licence option** — commercial use needs a direct agreement
      with Naver. The **weights are even more encumbered than the code**: using a
      checkpoint also means agreeing to the licences of every training dataset and base
      checkpoint, and the **mapfree dataset licence in particular is very restrictive**.
      So: never bakeable into the shipped image, never a default — opt-in with user-provided
      weights + licence acknowledgment only; `THIRD_PARTY_LICENSES.md` unaffected because
      nothing ships. This (with maturity) is the real reason `mast3r` stays opt-in —
      not scaling.
- [ ] **GPU-only validation; maturity risk.** ViT backbone, ~2 GB weights, CPU
      impractical → validation parked like the CUDA image. MASt3R-SfM (Sept 2024) is
      recent research and the line is moving fast (e.g. the feed-forward **Light3R-SfM**,
      Jan 2025); robustness / reproducibility vs COLMAP on production object sets is
      unproven, and a heavy, evolving neural model sits in tension with Effigies'
      reproducible-reference identity. Higher-risk, opt-in experiment, to be quantified on
      artefact / ceramic / statue datasets in the v0.8.0 campaign.

## Learned SfM front-end (LightGlue) — *gate OPEN: stable 4.1 shipped 2026-06-26; build side landed, wiring open*

The learned detector + matcher (**ALIKED** features + **LightGlue**) is the single
most visible quality lever for the hard surfaces archaeological documentation lives
on — **low-texture earth / planum**, **section profiles**, **repetitive stone
settings** — where SIFT (`exhaustive` / `vocab_tree`) is structurally weak. It was
**deferred to the COLMAP 4.1 release rather than given an Effigies version**, because
the clean way to get it is upstream and the trigger was a date we do not control.

**That trigger has fired.** COLMAP **4.1.0** released 2026-06-26 — one day after this
repo's last commit, which is why the pin sat at 4.0.4 for a month — and **4.1.1** on
2026-07-17. Both images are now pinned to **4.1.1** with `ONNX_ENABLED=ON` and the
ALIKED/LightGlue ONNX models baked in, so the capability is *present in the image*.
What remains is exposing it through the engine.

- [x] **Build side — native via the COLMAP 4.1 bump.** Done: `COLMAP_VERSION=4.1.1`
      in both Dockerfiles, `ONNX_ENABLED=OFF → ON` (the version bump alone does not
      suffice — that flag was the real gate), `MVS_ENABLED=OFF` (COLMAP's dense stack
      is unused; OpenMVS does that work). Models baked in and SHA256-pinned from the
      upstream `3.13.0` release assets, including the **ALIKED-specific retrieval
      trees** — SIFT vocab trees cannot serve ALIKED retrieval. No hloc/torch
      pipeline, no manual `database.db` import.
- [x] **Wiring — exposed through the engine as `--features`.** Done: one option
      selects extractor *and* matcher (`sift` | `sift-lightglue` | `aliked-n16rot` |
      `aliked-n32`), so the documented mismatch — SIFT descriptors with an ALIKED
      matcher, or two feature types in one `database.db` — is unrepresentable rather
      than merely warned about; two tests lock that. `features=sift` passes no new
      flags, keeping the default path byte-identical. Model paths resolve to the
      baked-in `EFFIGIES_MODEL_DIR` (COLMAP's defaults are URLs it would fetch at
      first use), a missing model fails loudly, `vocab_tree` switches to the matching
      `_aliked_*` retrieval tree, and availability is probed on the binary rather
      than inferred from `COLMAP_VERSION`.

      Verified on the A4000: `aliked-n16rot` on 67 × 12 MP images →
      **67/67 registered**, 54 214 points, 241 311 observations, mean track length
      4.45, mean reprojection error **1.363 px**, 109 MB textured OBJ.
      Required one more build fix: the CUDA **runtime** base had to become the
      `-cudnn-` variant, because ONNX Runtime's CUDA provider dlopens
      `libcudnn.so.9` and the plain base ships none — a *lazy* load, so the image
      built green and only aborted at inference.
- [ ] **Quantify it.** No SIFT-vs-ALIKED claim is made from the run above: one
      dataset at one setting is not evidence. Registered-image count, sparse-point
      count and downstream completeness on planum / profile / stone-setting sets
      belong to the v0.8.0 campaign — where this now *can* run, on the same host.
      **ONNX (not torch)** is a far lighter dependency, and ONNX Runtime has CPU **and**
      GPU execution providers (CUDA / CoreML) → softens the GPU requirement.
      License-clean: COLMAP took **ALIKED + LightGlue** (both permissive), **not**
      SuperPoint. The repo pins known-good and forbids `latest`/dev, so this **waits for
      the 4.1 release**. (4.1 also rides along division/fisheye camera models, model
      clustering, QEM mesh decimation, EXIF auto-rotate, ~10–15 % faster BA.)
- [ ] **Fallback (only if 4.1 slips badly) — hloc-style import.** Extract ALIKED
      keypoints + LightGlue matches, **import into the existing `database.db`**, then the
      unchanged `colmap mapper` writes `sparse/0` (downstream untouched). Pulls in
      **torch** + a GPU and a `--features` option in `options.json` — kept off the lean
      CPU image's default path. Only worth building if the 4.1 release is far out.
- [ ] **Retrieval still required for large sets.** LightGlue is pairwise; >~150 images
      need a retrieval stage — *composes with* `vocab_tree`/global descriptors, does
      **not** replace it. `autoscale.sh` picks a retrieval strategy for `features≠sift`.
- [ ] **Licensing — ALIKED is the default, not SuperPoint.** ALIKED + LightGlue are
      permissive (ship-able); **SuperPoint/SuperGlue (Magic Leap) are non-commercial** —
      opt-in only, never baked in. `THIRD_PARTY_LICENSES.md` updated for whatever weights ship.
- [ ] **Validation folds into the benchmark campaign (v0.8.0).** SIFT vs ALIKED+LightGlue
      on planum / profile / stone-setting datasets: registered-image count, sparse-point
      count, downstream completeness. Needs a **GPU** — available since 2026-07-25
      (see the GPU-validation section), so this is no longer hardware-blocked.

## GPU validation *(unparked 2026-07-25 — build + run VERIFIED on an RTX A4000)*

- [x] **CUDA/production image GPU build + run.** The image is built from the same
      pinned sources as the validated CPU image and passes `docker build --check`,
      but had never been compiled or executed for want of an NVIDIA machine. A
      temporary bare-metal test host (Intel i9-13xxx, P-cores only, 128 GB RAM,
      NVIDIA RTX 4000 / 16 GB VRAM, Ubuntu 24.04) is now available — which matches
      the *recommended production box* in `docs/DEPLOYMENT.md`'s sizing table.
      Scope for now is deliberately narrow: **does the engine work on a GPU at
      all** — not benchmarking (that is v0.8.0 and still gated on reference data).
      Tooling is in place and reproducible, since the host is temporary:
      `scripts/provision-gpu-host.sh` (Docker + NVIDIA Container Toolkit),
      `scripts/verify-gpu-image.sh` (static CUDA assertions incl. a negative
      control), `scripts/gpu-smoke-run.sh` (end-to-end run with host-side device
      sampling — a green run alone is *not* evidence, `run.sh` falls back to CPU
      and still exits 0).

      **DONE — 2026-07-25. The CUDA image builds and Effigies runs on a GPU.**
      Host: RTX A4000 (GA104, sm_86, 16 GB), driver 595.84, i9-13900KS (8 P-cores /
      16 threads), 125 GB RAM, Ubuntu 24.04.4, built with `CUDA_ARCH=86`.
      `verify-gpu-image.sh` 13/13; `gpu-smoke-run.sh` PASS on 67 × 12 MP iPhone
      images (Zionsberg 2023, trench 8-1) at `densify-resolution-level 2`,
      `refine-mesh-iters 1`, `--georeference none`:

      | Stage | Result |
      |---|---|
      | COLMAP sparse | SIFT **GPU** extractor + matcher (log-confirmed) |
      | DensifyPointCloud | 67 depth maps in **11 s**, 1 216 201 points, 29.7 s total |
      | ReconstructMesh | 655 673 vertices / 1 310 870 faces, 16.8 s |
      | RefineMesh | 459 405 vertices / 917 675 faces, 1 m 51.6 s |
      | Peak GPU utilisation | **98 %** |
      | Output | `odm_textured_model_geo.obj`, 108 MB |

      End-to-end ≈ 10 min. No CPU-fallback warning; OpenMVS logged
      `CUDA device 0 initialized: NVIDIA RTX A4000`. Two real defects surfaced,
      both fixed: (1) COLMAP 4.1.1 links GLEW/OpenGL even with `GUI_ENABLED=OFF`
      (`OPENGL_ENABLED` defaults ON), so the slim runtime stage needed
      `libglew2.2 libopengl0` — it failed as pycolmap's opaque "Cannot import the
      C++ backend"; (2) the runtime gate asserted the OpenMVS binaries *start*
      during `docker build`, which they never can: they link `libcuda.so.1`, the
      NVIDIA **driver** library, injected only at `docker run --gpus`. That was a
      latent bug that could only surface once this image was actually built for the
      first time; the gate now checks library resolution with `ldd` (stronger — it
      sees the whole unresolved set, not just the first miss) and
      `verify-gpu-image.sh` asserts the binaries really start under `--gpus`.

      Open detail: `RefineMesh` links the CUDA **driver** API (`libcuda.so.1`, not
      `libcudart`) and was **not** observed holding device memory in the 5 s
      sampling, unlike Densify/ReconstructMesh/TextureMesh. So
      `docs/DEPLOYMENT.md`'s "GPU VRAM → DensifyPointCloud **and** RefineMesh" may
      overstate RefineMesh's GPU role at `scales=1`; worth measuring before the
      sizing table is quoted in the paper.
- [x] **A GPU baseline run.** Done — `gpu-base-01` (2026-07-25), recorded in full in
      `docs/planned-experiments.md`, which now marks the CPU/arm64 run `8d2d31de`
      as superseded. 110 purely nadir drone images (Zionsberg 2015–2017, gimbal
      pitch ≤ −80°, DJI Phantom 3, 12 MP) at the full documented settings:
      **1 h 00 min 41 s**, 17.9 M dense points, 10.6 M refined faces,
      110/110 registered, mean reprojection error 0.798 px, 4.71 cm GSD ortho/DSM.

      The stage shares change the picture the CPU run painted: **RefineMesh 46 %,
      TextureMesh 29 %, densify only 10 %** (CPU run: densify 34 %). Experiment B's
      expected saving therefore has to be re-derived — at res-0 densify is not the
      bottleneck it appeared to be. RAM peaked at 53 of 125 GB on the Delaunay
      (82 M cells, cleared in 51 s); **VRAM peaked at ~1.2 GB of 16 GB**, so the
      GPU was never the constraint at this scale.

      Chosen deliberately over the close-range Zionsberg trench data: both queued
      experiments measure roof brightness, wall closure and building-core ortho
      nodata — quantities that do not exist in a 5 × 3 m excavation trench, so that
      baseline would have anchored nothing. Two other drone sets were rejected on
      inspection (Beyenburg: gimbal 0°, 2.2 m relative altitude — not a survey
      flight; Bethlehem: no orientation metadata).

---

## Pinned-stack currency *(audited 2026-07-26, re-audited the same day)*

Every `ARG` in both Dockerfiles checked against upstream. **The first pass got two
items wrong**, both from querying GitHub before reading the URL the Dockerfile
actually uses — recorded here because the errors are more instructive than the
findings:

- **`entwine` is not "diverged" and needs no decision.** The Dockerfile clones
  **`OpenDroneMap/entwine`** and the comment says so (*"Same fork + commit ODM
  pins"*). The first pass compared against `connormanning/entwine` — upstream, which
  a fork diverges from by definition. Against the fork the pin sits **21 commits
  ahead of its master** with nothing newer: current, deliberate, documented.
- **`Obj2Tiles` was never a white spot.** The first pass queried
  `DroneDB/Obj2Tiles`; the Dockerfile uses **`OpenDroneMap/Obj2Tiles`**, which has
  tags up to v1.6.2. Real state was two minors behind, now bumped.

**Current after this pass:** COLMAP 4.1.1, CUDA base 13.2.1, OpenMVS 2.4.0
(upstream's newest tag), VCGlib and NodeODM identical to upstream HEAD, entwine at
the ODM fork pin — and, newly bumped and version-verified *inside the built image*:

| | from | to |
|---|---|---|
| PDAL | 2.10.1 | **2.10.2** |
| py4dgeo | 1.1.0 | **1.2.0** |
| Obj2Tiles | v1.4.0 | **v1.6.2** (per-arch SHA256 re-pinned) |

Bumped together on purpose: each lives in its **own build stage**, so a failure
localises itself. Verified `13/13` plus a smoke run.

**Deliberately not bumped** — recorded at the pin in both Dockerfiles as
`DELIBERATELY NOT bumped`, so the next audit does not chase them:

- [ ] **CGAL 6.0.1 → 6.2.** The pin exists only because noble ships 5.6 and OpenMVS
      2.4.0 needs ≥ 6.0 for `CGAL/AABB_traits_3.h`; 6.0.1 satisfies that. OpenMVS
      2.4.0 is validated against 6.0.x and CGAL feeds its Delaunay/AABB code, so two
      minors risk a compile break or a subtle behaviour change for no named benefit.
      Bump when something needs it.
- [ ] **OpenPointClass, 2 commits.** Opt-in feature; its weights are pinned
      separately (model v1.1.3, upstream v1.1.7). Near-zero benefit against a real
      binary/model format-mismatch risk — and the mesh `--semantic` path was
      validated against exactly this model, so changing it would invalidate that
      evidence. Bump source **and** model together, with a re-run.

### Build cache architecture *(done 2026-07-26)*

`PDAL` was built at engine stage 3, **before** COLMAP and OpenMVS, which do not
depend on it. So a PDAL *patch* bump invalidated the cache for the entire engine —
the cheapest change in the list was the most expensive to apply. The author already
solved exactly this for entwine (*"Placed after the engine layers to keep their build
cache"*); PDAL was missed.

> **Correction, same day.** The item above (and the CHANGELOG, and the Structura
> briefing) put the cost of a full engine rebuild at *"~50 minutes"*. That figure was
> never measured; it was an estimate carried from the first cold build. Measured
> wall-clock of the three full builds on the A4000 host: **13, 12 and 11 minutes**.
> The saving from this change is therefore **~12 min → ~4.4 min per PDAL bump**, not
> ~45 minutes. The change is still right — it just buys about a sixth of what was
> claimed, and the build cache is far less precious than that number implied.

- [x] **PDAL stage moved after the COLMAP/OpenMVS layers**, together with
      OpenPointClass (its only consumer above entwine). Nothing above the new
      `PDAL` banner references PDAL, so the order was free. Both Dockerfiles moved
      in lock-step.

**The layer move alone would not have worked.** Probing the assumption first turned
up the real mechanism: *an `ARG`'s declaration site is part of the cache key of every
layer below it, whether or not that layer references the variable.* Measured with a
controlled probe — two `RUN`s, an `ARG` only the second one uses:

| ARG declared | change the ARG value | first (unrelated) RUN |
|---|---|---|
| above both RUNs | `--build-arg BAR=2` | **re-ran** (`DONE 2.2s`) |
| between the RUNs | `--build-arg BAR=2` | **`CACHED`** |

Control (same ARG value, no change) kept it `CACHED` in both layouts, so the probe
was not simply cache-less.

Consequence: the tidy "pinned versions" block at the top of both Dockerfiles was
itself the defect. Every version ARG has therefore moved next to the stage that
consumes it — `OPENMVS_VERSION`, `VCG_REF`, `CGAL_VERSION`, `PDAL_VERSION`. Only
`COLMAP_VERSION` and `CUDA_ARCH` remain on top, because COLMAP is the first heavy
stage and `CUDA_ARCH` feeds both COLMAP and OpenMVS. This also fixes cases nobody had
noticed: before, bumping **CGAL** or **OpenMVS** rebuilt COLMAP too.

**Verified by build, not by argument.** With the reordered Dockerfile built once,
a `--build-arg PDAL_VERSION=2.10.1` rebuild reported:

| engine stage | before | after |
|---|---|---|
| 3/19 COLMAP | rebuilt | **CACHED** |
| 9/19 OpenMVS | rebuilt | **CACHED** |
| 10/19 PDAL | rebuilt | rebuilt (63.3 s) |
| 11/19 OpenPointClass | rebuilt | rebuilt (44.2 s) |

Engine stages 2–9 all came back `CACHED`; only PDAL and what genuinely depends on it
re-ran. **~12 min → ~4.4 min** for a PDAL patch bump. CGAL, OpenMVS and VCG bumps get
the same treatment. The rule is written into both Dockerfiles at the top of the pin
block so the next tidy-up does not silently undo it.

## Out of scope

- **Modifying WebODM or NodeODM.** Effigies is an engine behind the existing
  NodeODM REST contract; it must not require patches to either.

Have a use case or a dataset that breaks an assumption here? Open an issue.
