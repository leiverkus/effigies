#!/usr/bin/env bash
#
# Effigies entry point.
# Invoked by the runner as:  run.sh --<opt> <val> ... <projectName>
# Mirrors ODM's run.sh contract so NodeODM-compatible nodes can call it unchanged.
#
set -euo pipefail

# ---------------------------------------------------------------------------
# 1. Parse NodeODM-style arguments
# ---------------------------------------------------------------------------
# Defaults mirror options.json
declare -A OPT=(
  [profile]=none
  [sparse-engine]=colmap
  [matcher]=exhaustive
  [mapper]=incremental
  [camera-model]=OPENCV
  [densify-resolution-level]=1
  [number-views-fuse]=3
  [skip-reconstruct-mesh]=false
  [refine-mesh-iters]=3
  [refine-max-face-area]=16
  [refine-gradient-step]=25.05
  [free-space-support]=false
  [mesh-close-holes]=30
  [mesh-decimate]=1.0
  [texture-resolution]=8192
  [texture-seam-leveling]=false
  [skip-color-harmonize]=false
  [skip-seam-smoothing]=false
  [skip-view-blending]=false
  [cpu-threads]=4
  [cpu-match-block]=10
  [dense-max-threads]=0
  [crs]=auto
  [crs-preset]=none
  [georeference]=auto
  [gcp]=""
  [gcp-bundle-adjust]=auto
  [skip-orthophoto]=false
  [skip-dsm]=false
  [dtm]=false
  [classify]=false
  [semantic]=false
  [align-to]=""
  [orthophoto-resolution]=auto
  [ortho-fill-holes]=0.25
  [ortho-color-balance]=none
  [ortho-brightness]=0
  [ortho-gamma]=1.0
  [ortho-flatten]=0
  [contours-interval]=0
  [3d-tiles]=false
  [no-gpu]=false
  [no-auto-scale]=false
  [tiles]=off
  [tile-budget]=auto
  [keep-workdir]=false
  [project-path]=""
)

PROJECT_NAME=""
declare -A GIVEN=()           # keys the caller set explicitly (beat the profile)
while [[ $# -gt 0 ]]; do
  if [[ "$1" == --* ]]; then
    key="${1#--}"
    GIVEN[$key]=1
    # boolean flags (no following value) vs. valued options
    if [[ $# -ge 2 && "$2" != --* ]]; then
      OPT[$key]="$2"; shift 2
    else
      OPT[$key]=true; shift 1
    fi
  else
    PROJECT_NAME="$1"; shift 1
  fi
done

# ---------------------------------------------------------------------------
# 1b. Capture profile — a parameter bundle for options the caller did NOT set
#     explicitly (explicit options always win). Profiles live here, versioned
#     with the engine, instead of in WebODM's preset JSON (those are per-install
#     data keyed to ODM's option names).
# ---------------------------------------------------------------------------
profile_set() { [[ -n "${GIVEN[$1]:-}" ]] || OPT[$1]="$2"; }
case "${OPT[profile]}" in
  drone-3d)      # aerial survey: GPS neighbourhood matching, balanced resolution
    profile_set matcher spatial
    profile_set mapper incremental
    profile_set densify-resolution-level 1
    profile_set number-views-fuse 3
    profile_set refine-mesh-iters 3
    ;;
  object)        # finds / artefacts / turntable: max detail, local frame
    profile_set matcher exhaustive
    profile_set georeference none
    profile_set densify-resolution-level 0
    profile_set number-views-fuse 3
    profile_set refine-mesh-iters 3
    profile_set refine-max-face-area 8
    ;;
  architecture)  # buildings / facades: convergent sets, balanced resolution
    profile_set matcher exhaustive
    profile_set densify-resolution-level 1
    profile_set number-views-fuse 3
    profile_set refine-mesh-iters 3
    ;;
  none) ;;
  *) echo "[effigies] WARN: unknown profile '${OPT[profile]}' — using defaults" >&2 ;;
esac

# ---------------------------------------------------------------------------
# 1c. Named CRS presets — fill crs only when the caller did not set it
#     explicitly (an explicit --crs always wins over the preset).
# ---------------------------------------------------------------------------
if [[ "${OPT[crs-preset]}" != "none" && -z "${GIVEN[crs]:-}" ]]; then
  case "${OPT[crs-preset]}" in
    israeli-tm)     OPT[crs]="EPSG:6991"  ;;   # Israeli TM Grid
    palestine-1923) OPT[crs]="EPSG:28191" ;;   # Palestine 1923 Grid
    etrs89-utm32n)  OPT[crs]="EPSG:25832" ;;   # German official UTM 32N
    etrs89-utm33n)  OPT[crs]="EPSG:25833" ;;   # German official UTM 33N
    british-ng)     OPT[crs]="EPSG:27700" ;;   # OSGB National Grid
    swiss-lv95)     OPT[crs]="EPSG:2056"  ;;   # Swiss LV95
    *) echo "[effigies] WARN: unknown crs-preset '${OPT[crs-preset]}' — ignored" >&2 ;;
  esac
fi

PROJ="${OPT[project-path]}/${PROJECT_NAME}"
IMAGES="${PROJ}/images"
WORK="${PROJ}/effigies"
mkdir -p "$WORK"

# ---------------------------------------------------------------------------
# 1d. Auto-scaling for large image sets — fill scale-appropriate matcher/mapper/
#     densify for options the caller did not set explicitly (explicit + profile
#     choices win where already scale-safe). Logs every decision; --no-auto-scale
#     disables it. See pipeline/autoscale.sh.
# ---------------------------------------------------------------------------
source "$(dirname "$0")/pipeline/autoscale.sh"
N_IMAGES=$(find "$IMAGES" -maxdepth 1 -type f 2>/dev/null | wc -l | tr -d ' ')
effigies_autoscale "$N_IMAGES"

# WebODM progress bar (UDP to NodeODM); see pipeline/progress.sh
export EFFIGIES_TASK_UUID="$PROJECT_NAME"
source "$(dirname "$0")/pipeline/progress.sh"
# OpenMVS binary-name resolver (handles InterfaceCOLMAP naming variants).
source "$(dirname "$0")/pipeline/openmvs_bin.sh"
progress 1

echo "[effigies] project: $PROJ ($N_IMAGES images)"
echo "[effigies] sparse-engine=${OPT[sparse-engine]} matcher=${OPT[matcher]} mapper=${OPT[mapper]} refine-iters=${OPT[refine-mesh-iters]} crs=${OPT[crs]}"

# Resolve GPU usage. GPU is used by default when present (--no-gpu forces CPU);
# we still probe and fall back to CPU when no usable CUDA
# GPU is present: COLMAP's SIFT aborts hard ("Cannot use Sift GPU without CUDA or
# OpenGL support") rather than degrading, which would surface to WebODM only as
# the opaque "Cannot process dataset". A documented CPU fallback beats a cryptic
# failure (the CPU image has no CUDA at all, and a GPU image may run without one).
# CPU tuning caps as task options; an explicitly set env var still wins (ops override)
export EFFIGIES_CPU_THREADS="${EFFIGIES_CPU_THREADS:-${OPT[cpu-threads]}}"
export EFFIGIES_CPU_MATCH_BLOCK="${EFFIGIES_CPU_MATCH_BLOCK:-${OPT[cpu-match-block]}}"
# OpenMVS dense worker-thread cap (0 = all cores = unchanged). Exported so the
# dense stage AND per-tile subprocesses (pipeline/tile.sh -> dense_openmvs.sh)
# inherit it. Bounds the densify/refine peak on many-core, RAM-constrained hosts;
# does not touch the ReconstructMesh Delaunay wall (use --tiles / resolution).
export EFFIGIES_DENSE_THREADS="${EFFIGIES_DENSE_THREADS:-${OPT[dense-max-threads]}}"

GPU_FLAG=0
if [[ "${OPT[no-gpu]}" != "true" ]]; then
  if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
    GPU_FLAG=1
  else
    echo "[effigies] WARN: no usable CUDA GPU detected; falling back to CPU (use --no-gpu to silence)" >&2
  fi
fi

# ODM convention: a gcp_list.txt in the project root is used automatically.
# Resolved BEFORE the sparse stage so a GCP-constrained bundle adjustment
# (--gcp-bundle-adjust) can run inside sparse_colmap.sh; the post-hoc georef
# bridge (step 4) reuses the same resolved path.
if [[ -z "${OPT[gcp]}" && -f "${PROJ}/gcp_list.txt" ]]; then
  OPT[gcp]="${PROJ}/gcp_list.txt"
  echo "[effigies] auto-detected GCP file: ${OPT[gcp]}"
fi

# GCP-constrained bundle adjustment is a GCP georeferencing method: it only does
# anything when georeferencing consumes GCPs (auto / gcp) AND a GCP file is present.
# Mode: off (post-hoc similarity) / on (always BA) / auto (default: keep the BA only
# if it beats the post-hoc check-RMSE; no-op without check points). Tolerate legacy
# bool spellings (true/false -> on/off). The default is auto, so the "not applicable"
# cases (no GCP file, or georeference=exif/none) must stay SILENT — only warn when
# the user EXPLICITLY set the option (GIVEN), otherwise it is just the normal
# no-GCP run falling through to the default georeferencing.
GCP_BA_OPT="${OPT[gcp-bundle-adjust]}"
case "$GCP_BA_OPT" in true) GCP_BA_OPT=on ;; false) GCP_BA_OPT=off ;; esac
explicit_ba="${GIVEN[gcp-bundle-adjust]:-}"
GCP_BA=off
if [[ "$GCP_BA_OPT" != "off" ]]; then
  if [[ "${OPT[georeference]}" != "auto" && "${OPT[georeference]}" != "gcp" ]]; then
    [[ -n "$explicit_ba" ]] && echo "[effigies] WARN: --gcp-bundle-adjust=$GCP_BA_OPT ignored with --georeference ${OPT[georeference]} (needs auto or gcp)" >&2
  elif [[ -z "${OPT[gcp]}" || ! -f "${OPT[gcp]}" ]]; then
    [[ -n "$explicit_ba" ]] && echo "[effigies] WARN: --gcp-bundle-adjust=$GCP_BA_OPT but no GCP file; using post-hoc georeferencing" >&2
  else
    GCP_BA="$GCP_BA_OPT"
  fi
fi

# ---------------------------------------------------------------------------
# 2. Sparse reconstruction  ->  produces $WORK/sparse  (+ scene.mvs)
# ---------------------------------------------------------------------------
# COLMAP is the only supported SfM front-end. OpenSfM was advertised but never
# worked (OpenMVS ships no InterfaceOpenSfM, and OpenSfM itself is not in the
# image); a real OpenSfM path would go via `opensfm export_openmvs` — parked on
# the ROADMAP. Fail loudly rather than fabricate if an unknown engine is forced.
# The split-merge tiling decision (large sets exceed the single-machine RAM wall
# in Densify/ReconstructMesh) needs the global dense/sparse, so it runs after
# sparse_colmap.sh.
if [[ "${OPT[sparse-engine]}" != "colmap" ]]; then
  echo "[effigies] FATAL: sparse-engine='${OPT[sparse-engine]}' is not supported; only 'colmap' is available" >&2
  exit 1
fi
TILE_N=0
bash "$(dirname "$0")/pipeline/sparse_colmap.sh" \
     "$IMAGES" "$WORK" "${OPT[matcher]}" "${OPT[camera-model]}" "$GPU_FLAG" "${OPT[mapper]}" \
     "${OPT[gcp]}" "${OPT[crs]}" "$GCP_BA"
TILE_N=$(python3 "$(dirname "$0")/helpers/tiling.py" --decide --work "$WORK" \
           --tiles "${OPT[tiles]}" --budget "${OPT[tile-budget]}" \
           --res-level "${OPT[densify-resolution-level]}" 2>/dev/null || echo 0)
if [[ "$TILE_N" -le 1 ]]; then
  # Single-machine path: build the global OpenMVS scene. InterfaceCOLMAP reads
  # the undistorted dense workspace (dense/sparse + dense/images) from
  # image_undistorter — not the raw sparse/0. --image-folder is recorded relative
  # to -w ($WORK) as "dense/images/...", which the dense stage (also -w $WORK)
  # resolves; without it DensifyPointCloud looks under $WORK/images and fails.
  IFACE_COLMAP="$(resolve_openmvs_bin InterfaceCOLMAP InterfaceColmap)"
  "$IFACE_COLMAP" -i "$WORK/dense" --image-folder "$WORK/dense/images/" \
                  -o "$WORK/scene.mvs" -w "$WORK"
fi
progress 44

# ---------------------------------------------------------------------------
# 3. OpenMVS dense + the steps ODM skips (ReconstructMesh / RefineMesh)
# ---------------------------------------------------------------------------
# Dense-stage args shared by the single-machine and per-tile paths (positionals
# 2..16 of dense_openmvs.sh; index 10 == HARMONIZE).
DENSE_ARGS=(
  "${OPT[densify-resolution-level]}"
  "${OPT[number-views-fuse]}"
  "$([[ "${OPT[skip-reconstruct-mesh]}" == "true" ]] && echo false || echo true)"
  "${OPT[refine-mesh-iters]}"
  "${OPT[mesh-decimate]}"
  "${OPT[texture-resolution]}"
  "$GPU_FLAG"
  "${OPT[refine-max-face-area]}"
  "${OPT[refine-gradient-step]}"
  "${OPT[texture-seam-leveling]}"
  "$([[ "${OPT[skip-color-harmonize]}" == "true" ]] && echo false || echo true)"
  "$([[ "${OPT[skip-seam-smoothing]}" == "true" ]] && echo false || echo true)"
  "$([[ "${OPT[skip-view-blending]}" == "true" ]] && echo false || echo true)"
  "${OPT[free-space-support]}"
  "${OPT[mesh-close-holes]}"
)

if [[ "$TILE_N" -gt 1 ]]; then
  # ----- split-merge tiling (helpers/tiling.py, pipeline/tile.sh, helpers/tile_merge.py) -----
  echo "[effigies] tiling: $TILE_N tiles (set exceeds the per-tile memory budget)"
  # Harmonise exposure ONCE on the shared undistorted images, before splitting, so
  # every tile (which symlinks them) textures at a consistent exposure. Sentinel in
  # dense/ so a sparse rebuild (which wipes dense/) re-harmonises, a resume skips.
  if [[ "${OPT[skip-color-harmonize]}" != "true" && ! -f "$WORK/dense/.harmonized" ]]; then
    if python3 "$(dirname "$0")/helpers/harmonize_exposure.py" --work "$WORK" --images "$IMAGES"; then
      touch "$WORK/dense/.harmonized"
    else
      echo "[effigies] WARN: global exposure harmonisation failed; tiles texture unadjusted" >&2
    fi
  fi
  python3 "$(dirname "$0")/helpers/tiling.py" --partition --work "$WORK" \
          --tiles "$TILE_N" --manifest "$WORK/tiles_manifest.json"
  TILE_ARGS=("${DENSE_ARGS[@]}"); TILE_ARGS[10]=false      # per-tile HARMONIZE off
  PENDING=$(python3 "$(dirname "$0")/helpers/tiling.py" --list-pending \
              --manifest "$WORK/tiles_manifest.json")
  TOTAL=$(printf '%s\n' "$PENDING" | grep -c . || true); DONE=0
  for TID in $PENDING; do
    if bash "$(dirname "$0")/pipeline/tile.sh" "$WORK" "$TID" "${TILE_ARGS[@]}"; then
      python3 "$(dirname "$0")/helpers/tiling.py" --mark "$TID" done   --manifest "$WORK/tiles_manifest.json"
    else
      echo "[effigies] WARN: tile $TID failed; skipping (manifest keeps it resumable)" >&2
      python3 "$(dirname "$0")/helpers/tiling.py" --mark "$TID" failed --manifest "$WORK/tiles_manifest.json"
    fi
    DONE=$((DONE + 1))
    [[ "$TOTAL" -gt 0 ]] && progress $((44 + DONE * 51 / TOTAL))
  done
  # Merge per-tile meshes + clouds into the canonical $WORK assets (shared local
  # frame); the downstream (georef -> LAZ -> ortho -> glTF -> report) then runs
  # ONCE on $WORK exactly as the single-machine path.
  python3 "$(dirname "$0")/helpers/tile_merge.py" --work "$WORK" \
          --manifest "$WORK/tiles_manifest.json"
  progress 78
else
  bash "$(dirname "$0")/pipeline/dense_openmvs.sh" "$WORK" "${DENSE_ARGS[@]}"
fi

# ---------------------------------------------------------------------------
# 4. Georeferencing bridge  (local SfM frame -> projected CRS)
#    This is what ODM does internally and COLMAP does NOT. When a GCP-constrained
#    bundle adjustment already ran (--gcp-bundle-adjust), georef_bridge honors its
#    colmap-gcp-ba transform instead of re-solving a post-hoc Umeyama.
# ---------------------------------------------------------------------------
progress 80
python3 "$(dirname "$0")/helpers/georef_bridge.py" \
     --work "$WORK" \
     --images "$IMAGES" \
     --sparse-engine "${OPT[sparse-engine]}" \
     --georeference "${OPT[georeference]}" \
     --crs "${OPT[crs]}" \
     --gcp "${OPT[gcp]}"

# ---------------------------------------------------------------------------
# 5. Point cloud -> georeferenced LAZ (+ EPT for the Potree viewer)
#    Applies the georef transform to scene_dense.ply and writes LAZ via PDAL.
# ---------------------------------------------------------------------------
progress 83
# When classifying, skip the EPT here — the classify step rebuilds it from the
# classified cloud so the Potree viewer can colour by class.
if [[ "${OPT[classify]}" == "true" ]]; then EPT_FLAG=""; else EPT_FLAG="--ept"; fi
if ! python3 "$(dirname "$0")/helpers/pointcloud_to_laz.py" --work "$WORK" $EPT_FLAG; then
  echo "[effigies] WARN: LAZ/EPT step failed; map_outputs will fall back to the raw PLY" >&2
fi

# ---------------------------------------------------------------------------
# 5a0. Multi-epoch change detection -> odm_dem/dem_difference.tif + odm_change/
#      m3c2.laz + odm_report/change_detection.json. Opt-in (--align-to <ref cloud>):
#      co-registers this epoch to a prior epoch's reference cloud (PDAL ICP) and
#      writes DoD + M3C2 difference products. Additive analysis — epoch B's own
#      deliverables are untouched. Self-gates (needs the reference, this epoch's
#      LAZ, and PDAL). Non-fatal. py4dgeo absent -> DoD-only.
# ---------------------------------------------------------------------------
if [[ -n "${OPT[align-to]}" ]]; then
  progress 85
  if ! python3 "$(dirname "$0")/helpers/change_detect.py" \
       --work "$WORK" --reference "${OPT[align-to]}" \
       --resolution "${OPT[orthophoto-resolution]}"; then
    echo "[effigies] WARN: change-detection step failed; continuing without it" >&2
  fi
fi

# ---------------------------------------------------------------------------
# 5a1. Multi-class classification (OpenPointClass). Opt-in (--classify): tags the
#      cloud with ground/vegetation/building/vehicle classes (in place), rebuilds
#      the EPT, and writes class rasters (odm_dem/buildings.tif, canopy.tif).
#      Self-skips when not georeferenced. Non-fatal.
# ---------------------------------------------------------------------------
if [[ "${OPT[classify]}" == "true" ]]; then
  progress 86
  if ! python3 "$(dirname "$0")/helpers/classify_cloud.py" \
       --work "$WORK" --resolution "${OPT[orthophoto-resolution]}"; then
    echo "[effigies] WARN: classification step failed; continuing without it" >&2
  fi
fi
progress 88

# ---------------------------------------------------------------------------
# 5a2. DTM (bare earth) -> odm_dem/dtm.tif. Opt-in (--dtm): PDAL SMRF ground
#      classification of the georeferenced LAZ, then rasterise the ground returns.
#      When --classify ran, reuse the ML ground class instead of re-running SMRF.
#      Off by default. Self-skips when not georeferenced. Non-fatal.
# ---------------------------------------------------------------------------
if [[ "${OPT[dtm]}" == "true" ]]; then
  PRECLASS=""; [[ "${OPT[classify]}" == "true" ]] && PRECLASS="--pre-classified"
  if ! python3 "$(dirname "$0")/helpers/pointcloud_to_dtm.py" \
       --work "$WORK" --resolution "${OPT[orthophoto-resolution]}" $PRECLASS; then
    echo "[effigies] WARN: DTM step failed; continuing without it" >&2
  fi
fi

# ---------------------------------------------------------------------------
# 5b. Orthophoto + DSM -> georeferenced GeoTIFFs (one nadir rasterisation of the
#     textured mesh). ODM builds the ortho from its DSM; we build a true ortho off
#     the refined textured mesh so both inherit the RefineMesh detail. The DSM is
#     the z-buffer that rasterisation already computes (odm_dem/dsm.tif). Both
#     self-skip when not georeferenced (crs=local). Non-fatal: a failure here must
#     not lose the 3D model + cloud that already succeeded.
# ---------------------------------------------------------------------------
if [[ "${OPT[skip-orthophoto]}" != "true" || "${OPT[skip-dsm]}" != "true" ]]; then
  if ! python3 "$(dirname "$0")/helpers/orthophoto.py" \
       --work "$WORK" --resolution "${OPT[orthophoto-resolution]}" \
       --fill-holes "${OPT[ortho-fill-holes]}" \
       --color-balance "${OPT[ortho-color-balance]}" \
       --ortho-brightness "${OPT[ortho-brightness]}" \
       --ortho-gamma "${OPT[ortho-gamma]}" \
       --ortho-flatten "${OPT[ortho-flatten]}" \
       $([[ "${OPT[skip-orthophoto]}" == "true" ]] && echo --skip-orthophoto) \
       $([[ "${OPT[skip-dsm]}" == "true" ]] && echo --skip-dsm); then
    echo "[effigies] WARN: orthophoto/DSM step failed; continuing without it" >&2
  fi
fi

# ---------------------------------------------------------------------------
# 5b2. Contour lines -> odm_dem/contours.{gpkg,dxf}. Opt-in (contours-interval>0):
#      GDAL contours from the DTM if present (bare earth), else the DSM. Runs after
#      both DEMs exist. Self-skips when not georeferenced. Non-fatal.
# ---------------------------------------------------------------------------
if [[ "$(awk "BEGIN{print (${OPT[contours-interval]}>0)?1:0}")" == "1" ]]; then
  if ! python3 "$(dirname "$0")/helpers/contours.py" \
       --work "$WORK" --interval "${OPT[contours-interval]}"; then
    echo "[effigies] WARN: contours step failed; continuing without it" >&2
  fi
fi

# ---------------------------------------------------------------------------
# 5b3. Semantic orthophoto v0 -> odm_semantic/orthophoto_semantic.tif. Opt-in
#      (--semantic): rasterises the OpenPointClass cloud classes (ground/vegetation/
#      structure) onto the ortho grid (pixel-aligned with the DSM). Needs a classified
#      cloud (--classify) + a raster grid; self-skips otherwise. Non-fatal. The bridge
#      to Structura's vectorisation; fine material classes are a downstream model.
# ---------------------------------------------------------------------------
if [[ "${OPT[semantic]}" == "true" ]]; then
  if ! python3 "$(dirname "$0")/helpers/semantic_ortho.py" --work "$WORK"; then
    echo "[effigies] WARN: semantic-orthophoto step failed; continuing without it" >&2
  fi
fi

# ---------------------------------------------------------------------------
# 5c. Camera assets — cameras.json (intrinsics) + shots.geojson (camera positions
#     on the map). Matches the ODM downloadable assets. Non-fatal.
# ---------------------------------------------------------------------------
progress 92
if ! python3 "$(dirname "$0")/helpers/camera_exports.py" --work "$WORK"; then
  echo "[effigies] WARN: camera export step failed; continuing without it" >&2
fi

# ---------------------------------------------------------------------------
# 5d. glTF model — WebODM's "Struktur-Modell (glTF)" (odm_textured_model_geo.glb),
#     a self-contained .glb of the same textured mesh. Non-fatal.
# ---------------------------------------------------------------------------
progress 93
if ! python3 "$(dirname "$0")/helpers/mesh_to_gltf.py" --work "$WORK"; then
  echo "[effigies] WARN: glTF export failed; continuing without it" >&2
fi

# ---------------------------------------------------------------------------
# 5d2. 3D Tiles -> odm_3d_tiles/ (Cesium/OGC LOD streaming tileset of the textured
#      mesh, via Obj2Tiles). Opt-in (--3d-tiles); needs a georeferenced result.
#      Non-fatal.
# ---------------------------------------------------------------------------
if [[ "${OPT[3d-tiles]}" == "true" ]]; then
  progress 94
  if ! python3 "$(dirname "$0")/helpers/mesh_to_3d_tiles.py" --work "$WORK"; then
    echo "[effigies] WARN: 3D Tiles export failed; continuing without it" >&2
  fi
fi

# ---------------------------------------------------------------------------
# 5e. Quality report PDF — WebODM's "Qualitätsbericht" (odm_report/report.pdf).
#     Stats table + orthophoto thumbnail. Non-fatal.
# ---------------------------------------------------------------------------
if ! python3 "$(dirname "$0")/helpers/report.py" --work "$WORK" --name "$PROJECT_NAME" \
     --sparse-engine "${OPT[sparse-engine]}" --matcher "${OPT[matcher]}" \
     --mapper "${OPT[mapper]}" --refine-iters "${OPT[refine-mesh-iters]}"; then
  echo "[effigies] WARN: report step failed; continuing without it" >&2
fi

# ---------------------------------------------------------------------------
# 6. Map outputs onto the WebODM asset contract
# ---------------------------------------------------------------------------
progress 98
python3 "$(dirname "$0")/helpers/map_outputs.py" --proj "$PROJ" --work "$WORK"
progress 99

# ---------------------------------------------------------------------------
# 7. Clean the heavy intermediates. A full-res run leaves ~6-8 GB of depth maps,
#    undistorted images and mesh snapshots in $WORK; with a persistent task
#    volume that exhausts the disk after a handful of runs (observed twice).
#    The mapped assets are hard links/copies under $PROJ and stay intact. Kept:
#    the small text outputs (georef_transform.json, coords.txt, sparse/0 text
#    model) for diagnostics/benchmarks. --keep-workdir disables the cleanup.
# ---------------------------------------------------------------------------
if [[ "${OPT[keep-workdir]}" != "true" ]]; then
  echo "[effigies] cleaning intermediate workdir data (use --keep-workdir to keep)"
  # per-tile workdirs first (their dense/images are symlinks to $WORK/dense — rm
  # removes the links, not the shared target, which is cleaned next)
  rm -rf "$WORK/tiles" 2>/dev/null || true
  rm -rf "$WORK/dense" "$WORK/entwine_pointcloud_tmp" 2>/dev/null || true
  rm -f "$WORK"/depth*.dmap "$WORK"/depth*.dmap.tmp "$WORK"/*.log         "$WORK"/scene.mvs "$WORK"/scene_dense.mvs "$WORK"/scene_dense.ply         "$WORK"/scene_dense_mesh.mvs "$WORK"/scene_dense_mesh.ply         "$WORK"/scene_dense_mesh_refine.mvs "$WORK"/scene_dense_mesh_refine.ply         "$WORK"/database.db "$WORK"/database.db-shm "$WORK"/database.db-wal         2>/dev/null || true
fi

echo "[effigies] done."
