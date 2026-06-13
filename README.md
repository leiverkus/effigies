# Effigies

[![CI](https://github.com/leiverkus/effigies/actions/workflows/ci.yml/badge.svg)](https://github.com/leiverkus/effigies/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/leiverkus/effigies?include_prereleases&sort=semver)](https://github.com/leiverkus/effigies/releases)
[![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)](ROADMAP.md)

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
| `skip-dsm` | `false` | Skip the DSM (`odm_dem/dsm.tif`), the nadir surface model emitted from the same z-buffer as the orthophoto (inherits RefineMesh detail). |
| `dtm` | `false` | Generate the bare-earth DTM (`odm_dem/dtm.tif`) by PDAL SMRF ground classification of the dense cloud (opt-in; costs ground-filter time, needs open ground). |
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
- VCGlib (an OpenMVS build dependency) still tracks a branch via the `VCG_REF`
  build arg; it must be pinned to a verified commit before a release image.
- GCP localization uses the nearest observed sparse point to the marked pixel;
  multi-view triangulation would be more precise.

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
