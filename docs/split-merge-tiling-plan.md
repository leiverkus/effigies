# Plan — Split-merge tiling (ROADMAP v0.5.0 core)

Goal: reconstruct image sets too large for one machine's memory by partitioning
the **expensive dense→mesh→texture** work into spatial tiles, processing each
within a fixed memory budget, and merging the results into one set of WebODM
assets — the open-source analogue of Metashape chunks / ODM split-merge.

## The one architectural decision: a shared global sparse frame

**Do SfM once on the whole set, then split.** The sparse reconstruction
(COLMAP feature → match → mapper) is comparatively cheap and already scales
(auto-scale picks `vocab_tree`/`global`); the dense chain is what explodes. So:

1. Run COLMAP on **all** images → one consistent set of camera poses + sparse
   points in a single frame.
2. Partition the cameras spatially into tiles **in that shared frame**.
3. Run only Densify→ReconstructMesh→RefineMesh→TextureMesh **per tile**.
4. Merge — alignment is *free* because every tile inherited poses from the same
   global sparse.

This sidesteps the hard problem entirely (independent per-tile alignment +
marker matching, the Metashape-chunk pain). It also means **tiling needs no
GPS**: we cluster camera centres in the local sparse frame; georeferencing stays
orthogonal and is solved once, globally (`georef_bridge.py`), then applied
uniformly to the merged output.

## Prerequisites (must land first)

- **Blend streaming refactor** ([blend-streaming-plan.md](blend-streaming-plan.md)) —
  each tile still textures; without it a dense tile re-introduces the memory wall
  inside our own stage.
- **Global photometric harmonisation.** `harmonize_exposure.py` currently solves
  per-image gains over the whole set. Tiles must **share that one global gain
  solution** (solve once, before splitting, apply per tile) or adjacent tiles
  will texture at visibly different exposures. This is a hard coupling, not an
  afterthought.

## Stage A — Partition *(new: `helpers/tiling.py`)*

Input: the global sparse model (poses + points3D, already read by
`georef_bridge.py` / `colmap_bin.py`). Output: a tile manifest.

- Cluster camera centres by 2D position (grid or k-means over XY in the sparse
  frame). Tile count derived from a **memory budget**: target dense-points-per-
  tile from the capacity model (≈ what 128 GB affords in ReconstructMesh's
  Delaunay; see the capacity discussion), estimated from sparse-point density ×
  expected densify multiplier.
- Each tile carries: its **core** cameras, a **halo** of neighbouring cameras
  (overlap, so reconstruction near tile borders has multi-view support), and a
  **core XY bound** (the region this tile *owns* for the merge crop).
- Manifest is JSON: per tile `{cameras[], halo_cameras[], xy_bounds}`. Pure
  function over the sparse model → unit-testable without running OpenMVS.

## Stage B — Per-tile chain *(orchestration in `run.sh` / a `pipeline/tile.sh`)*

For each tile, run the existing chain on its camera subset (core + halo) and the
sparse points within bounds+halo:

- `InterfaceCOLMAP` on the tile subset → `scene_<tile>.mvs`
- Densify → ReconstructMesh → RefineMesh → TextureMesh (reuse `dense_openmvs.sh`,
  parameterised by tile workdir + camera list)
- Apply the **global** harmonise gains; run the streamed blend + seams per tile.

Each tile outputs a textured mesh + dense cloud + (optional) tile orthophoto in
the **shared frame**. **Sequential by default** (one tile resident → bounds peak
RAM at one tile's budget); an opt-in parallel mode trades RAM for wall-clock
(and contends for the GPU when present). A tile failure is logged and skipped,
not fatal — the run delivers the rest (and the manifest makes it resumable).

## Stage C — Merge *(new: `helpers/tile_merge.py`)*, easiest first

- **Orthophoto — easy.** Per-tile nadir orthos are georeferenced rasters; crop
  each to its core XY bound and mosaic with feathering in the shared CRS (GDAL).
  Clean, no seams of consequence. (Same machinery later serves DSM/DTM.)
- **Dense cloud — easy.** Crop each tile's cloud to its core bound (halo is
  reconstruction support only, discarded at merge), concatenate → one LAZ; build
  EPT once over the merged cloud (`pointcloud_to_laz.py` unchanged downstream).
- **Cameras / shots / report — trivial.** Come from the global sparse already —
  whole-set, computed once.
- **Mesh — the hard one.** Crop each tile mesh to its core XY bound and
  concatenate. Border vertices between tiles won't coincide → potential cracks at
  tile seams. v1: crop-and-concatenate, document the seam limitation (Metashape
  and ODM have it too, mitigated by overlap). v2 (later): boundary stitching or a
  thin Poisson seam-fill across tile borders. glTF (`mesh_to_gltf.py`) and the
  georef transform apply to the merged mesh.

## Stage D — Gating, contract, progress

- **Gate:** tiling triggers automatically when the image count (or estimated
  dense-point total) exceeds a memory-budget threshold, **or** explicitly via a
  new option (`tiles=auto|N` / `tile-budget`). Below the threshold the pipeline
  runs exactly as today (zero overhead, no behavioural change).
- **Output contract unchanged.** `map_outputs.py` writes the same WebODM paths;
  WebODM sees one task with one set of assets. The merge produces those final
  files; the per-tile workdirs are intermediate (cleaned like today unless
  `--keep-workdir`).
- **Progress:** per-tile sub-ranges on the existing UDP progress bar (sparse =
  0–X %, then each tile a slice of X–95 %, merge = 95–100 %).
- **Georef:** solved once on the global sparse; the transform + offset +
  `coords.txt` are computed once and applied to the merged outputs (no per-tile
  georef).

## Phasing

- **Phase 0 — single tile from global sparse.** Prove a tile reconstructs
  correctly: run the chain on a camera subset drawn from a whole-set sparse, and
  show the result matches the same region of a full run. Validates the shared-
  frame premise before any merge code.
- **Phase 1 — partition** (`helpers/tiling.py` + unit tests on a synthetic
  sparse model: cluster counts, halo membership, bound coverage).
- **Phase 2 — per-tile orchestration** (sequential), reusing `dense_openmvs.sh`.
- **Phase 3 — merge, easy wins first:** ortho mosaic, cloud concat+EPT, cameras/
  report; then mesh crop+concat + glTF; LAZ over the merged cloud.
- **Phase 4 — gating + run.sh wiring + progress + output-contract tests.**
- **Phase 5 — validate at scale:** on a medium set that fits *both* paths, assert
  tiled output ≈ single-machine output (cloud/mesh/ortho metrics within
  tolerance); then a large set only tiling can do, confirming bounded peak RAM
  per tile.

## Honest risks

- **Mesh seams at tile borders** — the genuine hard part; v1 crops-and-
  concatenates and documents it. Not unique to us (Metashape/ODM share it).
- **Global harmonise coupling** — get it wrong and tiles texture at different
  exposures; it must be solved whole-set before splitting.
- **Tile sizing** — too large reintroduces the memory wall, too small multiplies
  border seams and overhead; the budget heuristic needs calibration against the
  instrumented RAM slope (the same measurement the blend plan's Phase 0 yields).
- **Sparse must register the whole set** — if COLMAP fails to connect the block
  into one component, tiles won't share a frame; fall back to per-component
  handling (COLMAP already splits into components, RealityScan-style).

## Effort

Substantially larger than the blend refactor — multi-day, the core v0.5.0
deliverable. Phases 0–1 are self-contained and testable without large runs;
Phase 3's merge is the bulk; Phase 5 needs the large dataset (reduced-resolution
for iteration, one full-res confirmatory run). Lands **after** the blend
streaming refactor.
