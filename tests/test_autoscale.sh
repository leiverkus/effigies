#!/usr/bin/env bash
# Unit tests for pipeline/autoscale.sh — pure function over the OPT/GIVEN arrays.
# Run: bash tests/test_autoscale.sh   (exits non-zero on failure, CI-friendly)
set -euo pipefail
cd "$(dirname "$0")/.."

# small thresholds so the scenarios read clearly: match>5, large>10
export EFFIGIES_AUTOSCALE_MATCH=5
export EFFIGIES_AUTOSCALE_LARGE=10
source pipeline/autoscale.sh

fail=0
check() {  # check <label> <actual> <expected>
  if [[ "$2" == "$3" ]]; then
    echo "ok  $1"
  else
    echo "FAIL $1: got '$2', expected '$3'"; fail=1
  fi
}

# reset OPT/GIVEN to a fresh baseline (exhaustive/incremental/densify-0, on)
reset() {
  unset OPT GIVEN
  declare -gA OPT=([matcher]=exhaustive [mapper]=incremental \
                   [densify-resolution-level]=0 [no-auto-scale]=false)
  declare -gA GIVEN=()
}

# --- small set (<= match threshold): nothing changes ----------------------
reset
effigies_autoscale 3 2>/dev/null
check "small set keeps exhaustive"        "${OPT[matcher]}"                  "exhaustive"
check "small set keeps incremental"       "${OPT[mapper]}"                   "incremental"

# --- medium set (> match, <= large): matcher only -------------------------
reset
effigies_autoscale 8 2>/dev/null
check "medium set -> vocab_tree"          "${OPT[matcher]}"                  "vocab_tree"
check "medium set keeps incremental"      "${OPT[mapper]}"                   "incremental"
check "medium set keeps densify 0"        "${OPT[densify-resolution-level]}" "0"

# --- large set (> large): matcher + mapper + densify ----------------------
reset
effigies_autoscale 20 2>/dev/null
check "large set -> vocab_tree"           "${OPT[matcher]}"                  "vocab_tree"
check "large set -> global"               "${OPT[mapper]}"                   "global"
check "large set -> densify 1"            "${OPT[densify-resolution-level]}" "1"

# --- explicit choices are respected (GIVEN) -------------------------------
reset
GIVEN=([matcher]=1 [mapper]=1 [densify-resolution-level]=1)
effigies_autoscale 20 2>/dev/null
check "explicit matcher respected"        "${OPT[matcher]}"                  "exhaustive"
check "explicit mapper respected"         "${OPT[mapper]}"                   "incremental"
check "explicit densify respected"        "${OPT[densify-resolution-level]}" "0"

# --- profile-set spatial matcher is left alone (already scales) -----------
reset
OPT[matcher]=spatial; OPT[densify-resolution-level]=1
effigies_autoscale 20 2>/dev/null
check "spatial matcher untouched"         "${OPT[matcher]}"                  "spatial"
check "global mapper still applied"       "${OPT[mapper]}"                   "global"

# --- densify is only raised, never lowered --------------------------------
reset
OPT[matcher]=vocab_tree; OPT[mapper]=global; OPT[densify-resolution-level]=2
effigies_autoscale 20 2>/dev/null
check "densify 2 not lowered"             "${OPT[densify-resolution-level]}" "2"

# --- no-auto-scale disables everything ------------------------------------
reset
OPT[no-auto-scale]=true
effigies_autoscale 20 2>/dev/null
check "no-auto-scale keeps exhaustive"    "${OPT[matcher]}"                  "exhaustive"
check "no-auto-scale keeps incremental"   "${OPT[mapper]}"                   "incremental"

echo
if [[ $fail -eq 0 ]]; then echo "all autoscale tests passed"; else echo "AUTOSCALE TESTS FAILED"; exit 1; fi
