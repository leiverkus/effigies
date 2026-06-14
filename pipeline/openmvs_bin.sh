#!/usr/bin/env bash
# Resolve OpenMVS binary names that vary across builds/forks.
#
# OpenMVS' interface tools have been renamed/re-cased between releases and forks
# (e.g. the COLMAP reader has appeared as InterfaceCOLMAP and InterfaceColmap;
# OpenMVS never shipped an "InterfaceOpenSfM" at all — OpenSfM exports to .mvs via
# its own `opensfm export_openmvs`). To keep the engine robust to a future OpenMVS
# bump, the runtime resolves the binary name from a known-alias list instead of
# hard-coding one, and FAILS LOUDLY if none is on PATH (constraint #7: the whole
# node depends on these binaries actually being present).
#
# Usage:  source this file, then
#           IFACE_COLMAP="$(resolve_openmvs_bin InterfaceCOLMAP InterfaceColmap)"
#         The first alias found on PATH is echoed; if none, a FATAL line goes to
#         stderr and the function returns non-zero (under `set -e` the caller's
#         command substitution then aborts the run).

resolve_openmvs_bin() {  # resolve_openmvs_bin <alias1> [alias2 ...] -> echoes first on PATH
  local b
  for b in "$@"; do
    if command -v "$b" >/dev/null 2>&1; then
      echo "$b"
      return 0
    fi
  done
  echo "FATAL: no OpenMVS binary found among [$*]; OpenMVS build/name mismatch" >&2
  return 1
}
