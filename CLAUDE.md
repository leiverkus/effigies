# CLAUDE.md — Effigies

Context for Claude Code when working in this repository.

## What this is

**Effigies** is a NodeODM-compatible photogrammetry processing node for WebODM.
Its purpose is to close the gap where WebODM/ODM produces weaker 3D reconstructions
than commercial tools (Metashape, RealityCapture): ODM runs OpenMVS only up to
`DensifyPointCloud` and then meshes with Screened Poisson, **skipping** OpenMVS'
`ReconstructMesh` and `RefineMesh`. Effigies runs the full OpenMVS chain
(Densify → ReconstructMesh → RefineMesh → TextureMesh) on top of a COLMAP sparse
reconstruction, and bridges the result into the WebODM asset contract.

Target use: a **complete, higher-quality engine across the board** — close-range /
convergent capture (objects, finds, artefacts, architecture) AND drone / aerial
sets. It produces the textured 3D mesh, the georeferenced point cloud, AND the
orthophoto (nadir-rasterised from the refined textured mesh, so the ortho inherits
the RefineMesh detail rather than being interpolated from a sparse cloud). Stock
ODM produces weak 3D in both regimes; Effigies aims to beat it on **every** output,
in one node — not a close-range niche. The node is region-agnostic; it was
originally built for archaeological documentation in the Southern Levant, but
nothing in the engine is specific to that region.

> *effigies* (lat.) — „das plastische Abbild, die geformte Nachbildung".

## Architecture (do not break the contract)

```
WebODM ──HTTP──> NodeODM REST layer ──run.sh──> [ Effigies engine ]
   COLMAP (sparse) ─ InterfaceCOLMAP ─> scene.mvs
   OpenMVS: DensifyPointCloud → ReconstructMesh → RefineMesh → TextureMesh
   georef_bridge.py   (local frame -> projected CRS, or local-only)
   pointcloud_to_laz.py (dense cloud -> georeferenced LAZ + EPT)
   orthophoto.py      (textured mesh -> georeferenced GeoTIFF orthophoto)
   camera_exports.py  (-> cameras.json + odm_report/shots.geojson)
   mesh_to_gltf.py    (textured OBJ -> odm_textured_model_geo.glb)
   map_outputs.py     (-> WebODM asset paths)
```

WebODM never talks to a binary directly. It talks to a NodeODM REST service. An
"engine" only has to honour three contracts:
1. **Engine call** — NodeODM runs `run.sh` in `ODM_PATH`, passing options as
   `--name value`. `ENGINE` reports the name (`effigies`).
2. **Options advertising** — `helpers/optionsToJson.py` serves `options.json`;
   WebODM builds the task-options UI from it.
3. **Output contract** — `helpers/map_outputs.py` writes results into the paths
   WebODM expects (`odm_texturing/`, `odm_georeferencing/`, point cloud).

## Non-negotiable constraints

Do not break these without an explicit instruction to do so.

1. **Keep the NodeODM contract intact.** `run.sh` argument parsing, the `ENGINE`
   file, the `options.json` schema, and the output paths in `map_outputs.py` must
   stay compatible with what NodeODM/WebODM expect. Verify against upstream
   NodeODM `libs/Task.js` before changing output paths.
2. **The RefineMesh step is the point.** Never silently drop `ReconstructMesh` /
   `RefineMesh` — that is the entire reason this node exists over stock ODM.
3. **Georeferencing must support `none`.** Object / turntable captures have no
   meaningful world position. `--georeference none` must always keep a metrically
   consistent local frame and must never fail for lack of GPS/GCP.
4. **CRS handling is explicit and general.** The default is `auto` (UTM zone
   derived from GPS/GCP). Never hard-code a single CRS; honour the `--crs` option
   for any projected EPSG code. Regional grids (e.g. **EPSG:6991** Israeli TM Grid,
   **EPSG:28191** Palestine 1923, **EPSG:32637** UTM 37N for the Southern Levant)
   are valid `--crs` *values*, not defaults.
5. **Float precision via offset.** Any georeferenced OBJ must be written with the
   projected offset subtracted (offset recorded in `georef_transform.json`).
   Never write raw UTM coordinates into vertex positions.
6. **No fabricated behaviour.** If a step (EXIF parse, GCP localize, mesh) cannot
   run, fail loudly or fall back as documented — do not emit an identity transform
   while claiming success. Mark genuine heuristics as such in comments.
7. **No `latest` container tags.** Pin COLMAP/OpenMVS to known-good versions; the
   whole value of the node depends on the presence of working
   `ReconstructMesh`/`RefineMesh` binaries (verify with `which` in the build).
8. **No destructive ops without confirmation.** No force-push, no history rewrite,
   no deleting a user's project data.

## Code conventions

- **Bash**: `set -euo pipefail`; quote paths; keep each pipeline stage in its own
  script under `pipeline/`.
- **Python**: 3.10+, type hints where it helps, docstrings in English. `numpy` is
  the only hard dependency of `georef_bridge.py`; `Pillow`/`pyproj` are used only
  on the EXIF path and imported lazily.
- **Comments in English, prose/commit discussion in German** (Patrick's default).
- Add missing imports; verify they appear in the final file. Do not reformat
  existing code without instruction. Do not remove existing tests.

## Git

- **Single trunk: `main`.** Solo developer — commit directly to `main`. No feature
  branches, no per-change PRs, no develop/main split. Tag releases on `main`. Work
  fast; don't stop for a commit-approval prompt on routine work (still confirm
  genuinely destructive or outward-facing one-way actions).
- Conventional commits: `type(scope): description`
  (e.g. `feat(georef): add EXIF-GPS fallback`).
- **Docs travel with the change.** Update CHANGELOG / ROADMAP / README in the *same*
  commit as the work they describe — immediately, never piecemeal afterwards.

## Testing

`georef_bridge.py` has no QGIS/ODM dependency and is unit-testable with synthetic
COLMAP fixtures (see `tests/`). The Umeyama solver must recover a known similarity
to ~1e-9; the GCP path must recover a known scale on a consistent synthetic scene.
Run `./scripts/test.sh`.

## Open points (state, not TODO-as-done)

- GCP localization uses the nearest observed sparse point to the marked pixel;
  multi-view triangulation of the marked pixel would be more precise.
- COLMAP/OpenMVS should be built from pinned source in the Dockerfile; distro
  packages may lack `RefineMesh`.
- Point cloud is passed through as `.ply`; convert to `.laz` + EPT (PDAL) for the
  Potree viewer.
- `InterfaceCOLMAP`/`InterfaceOpenSfM` binary names vary by OpenMVS build — verify.
