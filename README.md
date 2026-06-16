# Effigies

[![CI](https://github.com/leiverkus/effigies/actions/workflows/ci.yml/badge.svg)](https://github.com/leiverkus/effigies/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/leiverkus/effigies?include_prereleases&sort=semver)](https://github.com/leiverkus/effigies/releases)
[![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)](ROADMAP.md)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.20716035.svg)](https://doi.org/10.5281/zenodo.20716035)

> *effigies* (lat.) — "the sculpted likeness, the formed replica".
> The node turns flat images back into bodies: the dense, photometrically
> refined surface that ODM leaves out.

**Effigies is a [NodeODM](https://github.com/OpenDroneMap/NodeODM)-compatible
processing node for [WebODM](https://github.com/OpenDroneMap/WebODM).** It is an
*alternative engine* that closes the gap where WebODM/ODM produces weaker 3D
reconstructions than commercial tools (Metashape, RealityCapture): ODM runs
OpenMVS only up to `DensifyPointCloud` and then meshes with Screened Poisson,
**skipping** OpenMVS' `ReconstructMesh` and `RefineMesh`.

Effigies runs the **full OpenMVS chain** (Densify → ReconstructMesh → RefineMesh
→ TextureMesh) on top of a COLMAP sparse reconstruction, then bridges the result
into the WebODM asset contract. It installs alongside the stock ODM node and
shows up in WebODM as its own engine with its own task options.

It targets **a complete, higher-quality engine across all scenarios** —
close-range / convergent capture (objects, finds, artefacts, statues,
architecture) *and* drone / aerial sets. It produces the textured 3D mesh, the
georeferenced point cloud, *and* a georeferenced **orthophoto** (nadir-rasterised
from the refined mesh). Stock ODM produces weak 3D in both regimes; Effigies aims
to beat it on every output, in a single node.

## How it works (without touching WebODM)

WebODM never talks to a photogrammetry binary directly — it talks to a NodeODM
REST service. An "engine" only has to honour three contracts, which this node
provides:

1. **Engine call** — NodeODM runs `run.sh` in `ODM_PATH`, passing options as
   `--name value`; the `ENGINE` file reports the name (`effigies`).
2. **Options advertising** — `helpers/optionsToJson.py` serves `options.json`;
   WebODM builds the task-options UI from it.
3. **Output contract** — `helpers/map_outputs.py` writes results into the paths
   WebODM expects (`odm_texturing/`, `odm_georeferencing/`, point cloud).

```
WebODM ──HTTP──> NodeODM REST layer ──run.sh──> [ Effigies engine ]
                                                  │
   COLMAP (sparse, robust close-range)            │
        └─ InterfaceCOLMAP ─> scene.mvs           │
   OpenMVS                                         │
        ├─ DensifyPointCloud                       │
        ├─ ReconstructMesh   ← ODM skips this      │
        ├─ RefineMesh ×N     ← main quality lever  │
        └─ TextureMesh                             │
   georef_bridge.py  (local SfM frame -> CRS)      │
   orthophoto.py     (-> ortho + DSM, one raster)  │
   pointcloud_to_dtm.py (-> bare-earth DTM, opt-in)│
   classify_cloud.py (-> ML classified LAZ, opt-in)│
   contours.py       (-> DXF + GPKG contours, opt-in)
   mesh_to_3d_tiles.py (-> Cesium 3D Tiles, opt-in)
   change_detect.py  (-> DoD + M3C2 change, opt-in) │
   map_outputs.py    (-> WebODM asset structure)   ┘
```

## Quickstart

```bash
git clone https://github.com/leiverkus/effigies.git
cd effigies
./scripts/setup.sh                 # build the Docker image (effigies:dev)
docker run -p 3001:3000 --gpus all effigies:dev
```

Then in WebODM: **Processing Nodes → Add → `http://<host>:3001`**. The node
appears next to ODM with its own option set.

Run the test suite (no Docker / GPU required):

```bash
./scripts/test.sh
```

## Options

Advertised in [`options.json`](options.json) and surfaced in the WebODM task UI:

| Option | Default | Purpose |
|---|---|---|
| `sparse-engine` | `colmap` | SfM backend (`colmap` for close-range, `opensfm` for aerial). |
| `matcher` | `exhaustive` | COLMAP feature matching strategy. |
| `camera-model` | `OPENCV` | COLMAP self-calibration model. |
| `densify-resolution-level` | `1` | OpenMVS densify downscale (`0` = full res). |
| `number-views-fuse` | `3` | Min. agreeing views to fuse a point. |
| `skip-reconstruct-mesh` | `false` | Skip OpenMVS `ReconstructMesh`/`RefineMesh` (the steps ODM lacks run by default). |
| `refine-mesh-iters` | `3` | `RefineMesh` iterations — the main quality lever. |
| `mesh-decimate` | `1.0` | Mesh decimation (`1.0` = full detail). |
| `texture-resolution` | `8192` | Texture atlas size in px. |
| `georeference` | `auto` | `auto` / `gcp` / `exif` / `none` (see below). |
| `crs` | `auto` | Target projected CRS (EPSG code, or `auto` UTM derivation). |
| `crs-preset` | `none` | Named regional grids filling `crs` (Israeli TM, Palestine 1923, ETRS89 UTM 32/33N = Germany's official grid, OSGB, LV95); an explicit `crs` always wins. |
| `gcp` | — | Optional path to an ODM-format `gcp_list.txt`. |
| `gcp-bundle-adjust` | `auto` | `off` / `on` / `auto`. Anchor the GCPs in a constrained bundle adjustment (pycolmap) on the sparse model before densification, instead of the post-hoc similarity — removes drift, tightens CP-RMSE. `auto` (default) keeps the BA only if it beats the post-hoc check-point RMSE, else falls back; needs a GCP file with `check` points to do anything (see below). |
| `skip-dsm` | `false` | Skip the DSM (`odm_dem/dsm.tif`), the nadir surface model emitted from the same z-buffer as the orthophoto (inherits RefineMesh detail). |
| `dtm` | `false` | Generate the bare-earth DTM (`odm_dem/dtm.tif`) by PDAL SMRF ground classification of the dense cloud (opt-in; costs ground-filter time, needs open ground). |
| `classify` | `false` | ML multi-class point classification (OpenPointClass) → ASPRS classes in the LAZ + `odm_dem/{buildings,canopy}.tif` (opt-in; needs georeferencing). |
| `ortho-fill-holes` | `0.25` | Max hole area (m²) filled in the orthophoto by nearest-valid colour; only small interior holes close, large voids + the edge stay nodata (`0` disables). DSM/DTM/cloud are never modified. |
| `contours-interval` | `0` | Vector contour spacing (m; `0` = off) → `odm_dem/contours.{gpkg,dxf}`, from the DTM if present else the DSM. |
| `3d-tiles` | `false` | Export an OGC 3D Tiles LOD tileset (`odm_3d_tiles/`) of the textured mesh for Cesium/web streaming, via Obj2Tiles (opt-in; needs georeferencing). |
| `align-to` | — | Multi-epoch change detection: path to a prior epoch's reference cloud (e.g. another task's `odm_georeferencing/odm_georeferenced_model.laz`). Co-registers this epoch to it (PDAL ICP) and writes a DEM-of-Difference (`odm_dem/dem_difference.tif`, with cut/fill volumes) + an M3C2 change cloud (`odm_change/m3c2.laz`) + `odm_report/change_detection.json` (opt-in; needs georeferencing; py4dgeo absent → DoD-only). Additive analysis — this epoch's own assets are unchanged. |
| `no-gpu` | `false` | Force CPU even when CUDA is available. |
| `no-auto-scale` | `false` | Disable count-based adaptation of matcher/mapper/densify for large image sets (see below). |

## Georeferencing (`--georeference`)

Implemented in [`helpers/georef_bridge.py`](helpers/georef_bridge.py) as a
Umeyama 3D similarity (scale + rotation + translation) on ≥3 non-collinear
correspondences:

- **`auto`** (default) — use a GCP file if present (project-root `gcp_list.txt`
  is auto-detected, ODM convention), else fall back to EXIF-GPS, else keep a
  metrically-scaled local frame.
- **`gcp`** — require `gcp_list.txt`. World coordinates come from the file; each
  GCP's local position is **triangulated** from its marked pixels: every marking
  is undistorted (full COLMAP lens model) into a viewing ray and the rays of all
  images the GCP is marked in are intersected in least squares. GCPs marked in a
  single image only fall back to the nearest observed sparse point (heuristic).
  Needs ≥3 localizable GCPs; mark each GCP in **2+ images** for full accuracy.
- **`exif`** — pair COLMAP camera centers with EXIF-GPS reprojected into the
  target CRS (UTM auto-derived when `crs=auto`). Needs ≥3 well-distributed fixes;
  collinear flight lines degrade the solve. Requires `Pillow` + `pyproj`.
- **`none`** — skip georeferencing, keep the local object-centric frame.
  **Recommended for turntable / close-range captures** — the model stays
  metrically consistent, only absolute world placement is omitted.

The target CRS is any projected **EPSG** code, or `auto` to derive the UTM zone
from the data (`crs-preset` offers common regional grids by name). The textured
OBJ is rewritten with a projected offset subtracted (offset stored in
`georef_transform.json`) so large coordinates stay within float precision.

Every solve reports its quality: `georef_transform.json` carries a `residuals`
block (RMS 3D / horizontal / vertical, max, correspondence count — for GCP
solves also the per-method localization counts), echoed in the task log and the
quality-report PDF. GCP residuals reflect marking + reconstruction quality;
EXIF residuals are dominated by consumer-GPS noise.

### GCP-constrained bundle adjustment (`--gcp-bundle-adjust off|on|auto`)

The default GCP path is **post-hoc and rigid** — COLMAP reconstructs freely and a
single 7-DoF Umeyama similarity maps the block to the surveyed world. A rigid
similarity cannot absorb reconstruction **drift** (bending / non-uniform scale
across the block), so the check-point RMSE it leaves is a floor.

The bundle-adjustment path anchors the marked GCPs at their surveyed coordinates as
constant 3D points and re-optimises the cameras + tie points to be consistent with
them ([`helpers/gcp_bundle_adjust.py`](helpers/gcp_bundle_adjust.py), via
**pycolmap** / COLMAP's own Ceres bundle adjustment). It runs on the **sparse**
model *before* undistortion, so the dense cloud, mesh, texture and orthophoto are
all built from the corrected, world-frame poses — not patched afterwards. The model
is rewritten into the projected offset-world frame and `georef_transform.json`
becomes the identity-with-offset transform (`source=colmap-gcp-ba`), so the LAZ
still lands in full UTM and the OBJ/ortho/DSM in the offset frame — no downstream
change.

- **`auto`** (default) — run **both** and keep whichever gives the lower independent
  check-point RMSE. The comparison is a cheap sparse-model metric (no double dense
  run): the free model is backed up, the BA runs, and the BA is kept only if it
  beats the post-hoc path by a real margin (10 % relative **and** 1 mm absolute,
  `EFFIGIES_GCP_BA_MARGIN` / `EFFIGIES_GCP_BA_MIN_GAIN_M`); otherwise the free model
  is restored and the post-hoc path runs. **By construction never worse than `off`
  on the check metric** — which is why it is the default. The decision and both
  RMSEs are written to `gcp_ba_arbitration.json` (and folded into
  `georef_transform.json`) for audit. `auto` needs `check`-flagged GCPs; with none
  (or no GCP file at all) it simply falls back to the post-hoc / EXIF / local path,
  silently — so it never changes behaviour for runs that have nothing to arbitrate.
- **`on`** — always the bundle adjustment.
- **`off`** — always the post-hoc similarity. Well-tested, rigid, and forgiving of
  imperfect GCPs (a bad marker inflates residuals but a least-squares fit does not
  let it bend the geometry). Use this to force the pre-`auto` behaviour.

**Check points:** a `gcp_list.txt` line whose trailing token is `check` (the ODM
`[extra]` field) is **held out** of the solve and reported as an independent
CP-RMSE (`residuals.check_rms_3d/…`) — the honest accuracy estimate, and the metric
`auto` arbitrates on. Mark each GCP in **2+ images** (single-view GCPs still inform
the initial alignment but cannot be BA anchors).

## Large image sets (auto-scaling)

Single-machine reconstruction has two walls as the image count grows: the
`exhaustive` matcher is O(n²) (fatal past a few hundred images) and memory (dense
cloud + the `ReconstructMesh` Delaunay). Because the NodeODM `/options` contract
is static — WebODM cannot ask the engine to adapt the options dialog to the image
count — Effigies adapts **in the engine** ([`pipeline/autoscale.sh`](pipeline/autoscale.sh)),
at runtime, for options you did not set explicitly:

- **> ~150 images** — matcher `exhaustive` → `vocab_tree` (baked FAISS retrieval
  tree, O(n·k)). A profile's already-scalable `spatial` is left as is.
- **> ~500 images** — also mapper → `global` (GLOMAP), and full-res densify
  bounded (`densify-resolution-level 0 → 1`).

Every change is logged with its override flag; an explicit choice always wins (an
explicit `--matcher exhaustive` is kept, with a warning). `--no-auto-scale` turns
the whole mechanism off. Thresholds are env-tunable (`EFFIGIES_AUTOSCALE_MATCH`,
`EFFIGIES_AUTOSCALE_LARGE`). Beyond the single-machine memory wall, split-merge
tiling is the path (ROADMAP v0.5.0).

## Project status

This is **alpha — a working scaffold, not a finished product.** The pipeline
contract, the georeferencing bridge and the WebODM output mapping are in place and
unit-tested, but several parts still need hardening before production use. The
honest list lives in [ROADMAP.md](ROADMAP.md); the short version:

- The Dockerfile now builds COLMAP and OpenMVS from **pinned source** with a
  build-time `which` gate that fails loudly if `RefineMesh` (or any other required
  binary) is missing. It has **not yet been built/run on real hardware** — the gate
  is the safety net until an end-to-end run confirms it.
- VCGlib (an OpenMVS build dependency) is pinned to a verified commit via the
  `VCG_REF` build arg (`658ba36`), like every other component — no floating branch.
- GCP localization triangulates each GCP across all images it is marked in (marked
  pixels undistorted into viewing rays, intersected in least squares); the
  nearest-observed-sparse-point heuristic is now only the single-view fallback.
  Shipped in v0.3.0.

See [CHANGELOG.md](CHANGELOG.md) for the release history.

## Repository layout

```
ENGINE                 engine name reported to WebODM
options.json           task options advertised to the WebODM UI
run.sh                 entry point: parses args, drives the pipeline
pipeline/              COLMAP / OpenSfM sparse + OpenMVS dense stages
helpers/               georef bridge, point-cloud -> LAZ/EPT, output mapping, options shim
tests/                 unit tests (synthetic COLMAP fixtures)
scripts/               setup.sh (build), test.sh (CI mirror)
Dockerfile             COLMAP + OpenMVS + NodeODM REST layer
ROADMAP.md             planned milestones
CHANGELOG.md           release history
CLAUDE.md              project context + hard constraints
```

## Contributing

Branch from `develop`, conventional commits, run `./scripts/test.sh` before a PR.
See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Effigies' own source is licensed under the **[MIT License](LICENSE)**.

Effigies orchestrates third-party tools (COLMAP, OpenMVS, NodeODM/ODM, OpenSfM)
as separate programs; the Docker image bundles them as an aggregation. Those
components keep their own licenses — notably **AGPL-3.0** for NodeODM/ODM/OpenMVS.
See [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md) for what that means when
you redistribute a build.
