#!/usr/bin/env bash
# Engine-side auto-scaling for large image sets.
#
# When the image set is large, single-machine reconstruction hits two walls:
# COLMAP's `exhaustive` matcher is O(n^2) (fatal past a few hundred images), and
# the incremental mapper + full-resolution densify drive the memory cost. This
# module fills scale-appropriate values for options the caller did NOT set
# explicitly — mirroring the profile layering (explicit > profile > auto-scale >
# base default) and logging every decision, so the behaviour is transparent and
# always overridable. Disabled with --no-auto-scale.
#
# It operates on the global OPT[] and GIVEN[] associative arrays defined in
# run.sh and is therefore *sourced*, not executed. The image count is passed as
# the single argument so the function stays pure and unit-testable.
#
# Thresholds are env-overridable for tuning / tests.
: "${EFFIGIES_AUTOSCALE_MATCH:=150}"   # above this, exhaustive matching is O(n^2)-fatal
: "${EFFIGIES_AUTOSCALE_LARGE:=500}"   # above this, also prefer global mapper + bound densify

effigies_autoscale() {
  local n="$1"
  [[ "${OPT[no-auto-scale]:-false}" == "true" ]] && return 0
  [[ -z "$n" || "$n" -lt 1 ]] && return 0

  # Matcher: `exhaustive` is the only O(n^2) strategy; vocab_tree / spatial /
  # sequential already scale, so only intervene while matcher is exhaustive.
  # An explicit choice is respected but warned about; otherwise switch to the
  # universal scale-safe retrieval matcher (baked FAISS vocab tree). A profile
  # that picked `spatial` (drone-3d) is left untouched — it is already O(n*k).
  if [[ "$n" -gt "${EFFIGIES_AUTOSCALE_MATCH}" && "${OPT[matcher]}" == "exhaustive" ]]; then
    if [[ -n "${GIVEN[matcher]:-}" ]]; then
      echo "[effigies] WARN: ${n} images with explicit --matcher exhaustive (O(n^2)); consider vocab_tree or spatial" >&2
    else
      OPT[matcher]="vocab_tree"
      echo "[effigies] auto-scale: ${n} images > ${EFFIGIES_AUTOSCALE_MATCH} -> matcher=vocab_tree (override with --matcher)" >&2
    fi
  fi

  # Large sets: the global mapper (GLOMAP) is far faster on big, well-connected
  # blocks than registering images one-by-one. Only when still on the default.
  if [[ "$n" -gt "${EFFIGIES_AUTOSCALE_LARGE}" && "${OPT[mapper]}" == "incremental" && -z "${GIVEN[mapper]:-}" ]]; then
    OPT[mapper]="global"
    echo "[effigies] auto-scale: ${n} images > ${EFFIGIES_AUTOSCALE_LARGE} -> mapper=global (override with --mapper)" >&2
  fi

  # Large sets: full-resolution densify (level 0) explodes the point count and
  # the ReconstructMesh Delaunay RAM. Only ever RAISE the level (never reduce a
  # caller's detail intent), and only the worst case 0 -> 1.
  if [[ "$n" -gt "${EFFIGIES_AUTOSCALE_LARGE}" && "${OPT[densify-resolution-level]}" == "0" && -z "${GIVEN[densify-resolution-level]:-}" ]]; then
    OPT[densify-resolution-level]="1"
    echo "[effigies] auto-scale: ${n} images > ${EFFIGIES_AUTOSCALE_LARGE} -> densify-resolution-level=1 (override with --densify-resolution-level)" >&2
  fi
}
