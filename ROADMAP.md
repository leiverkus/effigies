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

Both images are now on **COLMAP 4.0.4 + OpenMVS 2.4.0**, built from identical pinned
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

## v0.7.0 — Semantic field (`--semantic`) *(planned — mechanism only; the fine-class model is a Structura deliverable; pushes the benchmark campaign to v0.8.0)*

The bridge to **Structura**, the downstream vectorisation project (orthophoto/DEM
→ georeferenced excavation vectors in PostGIS). The division of labour is
**field vs object**, not raster vs vector — a boundary that survives the move into
3D: **Effigies owns the semantic *field* in geometry-space** (per-point / -vertex /
-pixel class, multi-view- and multi-epoch-consistent); **Structura owns the
semantic *objects* in vector/DB-space** (instances, topology, stratigraphic
attribution). Effigies ships only the **mechanism** — classify → rasterise →
propagate — and **never bakes an archaeological-material model into the MIT image**.

- [ ] **`--semantic`: a per-pixel class ortho rasterised from a 3D class field.**
      Classify the mesh / cloud, then nadir-rasterise through the existing
      true-ortho z-buffer pass into `odm_semantic/orthophoto_semantic.tif` —
      occlusion-correct, inheriting RefineMesh geometry, georeferenced like the RGB
      ortho. Same machinery as the existing class rasters (`classify_cloud.py`
      `buildings.tif` / `canopy.tif`). Self-skips for local-frame results.
- [x] **v0 is free from the existing point classification — shipped.**
      `helpers/semantic_ortho.py` (opt-in `--semantic`) rasterises the OpenPointClass
      cloud classes onto the orthophoto grid (pixel-aligned with `odm_dem/dsm.tif`),
      per-cell **majority** class → ground / vegetation / structure, as
      `odm_semantic/orthophoto_semantic.tif` (Byte + colour table) + a legend JSON. No
      model cost — needs only `--classify`; self-skips otherwise. Unit-tested
      (majority + ASPRS→v0 + write round-trip) and image-validated end-to-end. The fine
      material classes below remain the trained-model step.
- [ ] **Fine archaeological classes = bring-your-own model (Structura's
      deliverable).** Stone / earth / paving / ceramic / mortar is a trained **2D
      image** semantic model (labels are cheap in 2D; foundation-model leverage),
      run per-view and **fused onto the mesh via the existing multi-view blend**
      (`texture_blend.py`) to give one class per 3D point — multi-view-consistent,
      and it sees the **vertical / occluded surfaces (profiles)** the nadir ortho
      loses. The model is a **versioned weights asset** loaded like the vocab tree /
      OpenPointClass model, never baked in (likely non-commercial research weights —
      same opt-in pattern as the SuperPoint / MASt3R items).
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

## v0.8.0 — Benchmark campaign & profile calibration *(needs reference data)*

The empirical work behind the paper, split out from v0.4.0 (the *tooling* is
done; the *runs* are here). Gated on a dataset with **reference data** — a TLS
scan and/or surveyed check points — for absolute accuracy; relative metrics
(roughness, detail, completeness, runtime) can proceed without it.

- [ ] **Comparison runs.** Process shared datasets through Effigies, stock ODM,
      Metashape and (where available) RealityScan, and compute the
      `scripts/benchmark.sh` metrics — cloud/mesh-to-reference distance,
      check-point RMSE, surface roughness, completeness, runtime. No prior study
      benchmarks COLMAP + OpenMVS *with RefineMesh* against the commercial tools,
      so this is a publishable contribution, not just an internal check. The
      honest headline is narrow-but-deep: refined-mesh surface detail (see the
      ODM comparison in the v0.4.0 notes — ODM leads on ortho maturity / DSM-DTM
      breadth / scaling, Effigies on RefineMesh geometry).
- [ ] **Two queued single-variable experiments** specified against a shared
      baseline run in [docs/planned-experiments.md](docs/planned-experiments.md):
      watertightness (`mesh-close-holes`) and densify-resolution vs. runtime —
      both double as profile-calibration data points.
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
      So: never bakeable into the MIT image, never a default — opt-in with user-provided
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

## Learned SfM front-end (LightGlue) — *deferred to the COLMAP 4.1 stable release (no Effigies version)*

The learned detector + matcher (**ALIKED** features + **LightGlue**) is the single
most visible quality lever for the hard surfaces archaeological documentation lives
on — **low-texture earth / planum**, **section profiles**, **repetitive stone
settings** — where SIFT (`exhaustive` / `vocab_tree`) is structurally weak. It is
**deferred to the COLMAP 4.1 release rather than given an Effigies version**, because
the clean way to get it is now upstream and the trigger is a date we do not control;
the numbered items above are pulled ahead of it.

- [ ] **Plan — native via a COLMAP 4.1 bump (gated on *stable* 4.1).** COLMAP **4.1**
      (dev `4.1.0.dev0`, ~2026-03) builds **ALIKED extraction + LightGlue matching
      natively via ONNX** (SIFT *and* ALIKED) + Python bindings. That collapses the work
      to a **version bump (4.0.4 → stable 4.1) + wiring the native flags** in
      `sparse_colmap.sh` — no hloc/torch pipeline, no manual `database.db` import.
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
      count, downstream completeness. Needs a **GPU** (none on this host).

## Parked (no hardware)

- **CUDA/production image GPU build + run.** The image is built from the same
  pinned sources as the validated CPU image and passes `docker build --check`, but
  there is no NVIDIA machine available to compile and run it. Parked indefinitely;
  revisit only if GPU hardware appears.

---

## Out of scope

- **Modifying WebODM or NodeODM.** Effigies is an engine behind the existing
  NodeODM REST contract; it must not require patches to either.

Have a use case or a dataset that breaks an assumption here? Open an issue.
