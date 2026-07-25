#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Patrick Leiverkus
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for helpers/optionsToJson.py — the NodeODM options translation.

Locks the contract with NodeODM's libs/odmInfo.js: options must be a dict keyed by
"--flag" with argparse-style descriptors, and every non-enum domain must match
NodeODM's checkDomain grammar (else task submission fails validation).

Run:  python3 tests/test_options.py
"""
import os
import re
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "helpers"))
import optionsToJson as o2j  # noqa: E402

OPTIONS_JSON = os.path.join(HERE, "..", "options.json")

# Mirror of NodeODM libs/odmInfo.js domainChecks regexes (the only domains it accepts).
NODEODM_DOMAIN_REGEXES = [
    r"^(positive |negative )?(integer|float)$",
    r"^percent$",
    r"^(float|integer): ([\-\+\.\d]+) <= x <= ([\-\+\.\d]+)$",
    r"^(float|integer) (>=|>|<|<=) ([\-\+\.\d]+)$",
    r"^(json)$",
    r"^(string|path)$",
]


def _load():
    with open(OPTIONS_JSON) as f:
        return json.load(f)


def test_keys_are_double_dash_flags():
    out = o2j.to_nodeodm(_load())
    assert out, "no options produced"
    for k in out:
        assert k.startswith("--"), f"option key not a flag: {k}"
    print(f"ok  {len(out)} options keyed as --flags")


def test_type_mapping_matches_nodeodm_expectations():
    src = {opt["name"]: opt for opt in _load()}
    out = o2j.to_nodeodm(_load())
    for name, opt in src.items():
        d = out["--" + name]
        t = opt["type"]
        if t == "enum":
            assert d["type"] == "<class 'str'>" and "choices" in d, name
            # choices repr must parse the NodeODM way (' -> " then JSON)
            parsed = json.loads(d["choices"].replace("'", '"'))
            assert parsed == opt["domain"], f"choices mismatch for {name}"
        elif t == "int":
            assert d["type"] == "<class 'int'>", name
        elif t == "float":
            assert d["type"] == "<class 'float'>", name
        elif t == "bool":
            assert d["default"] in ("True", "False") and "type" not in d, name
        else:
            assert d["type"] == "<class 'str'>", name
    print("ok  type mapping matches NodeODM expectations")


def test_all_metavar_domains_are_nodeodm_valid():
    """Every emitted metavar (== NodeODM domain for non-enum) must match its grammar."""
    out = o2j.to_nodeodm(_load())
    checked = 0
    for k, d in out.items():
        mv = d.get("metavar")
        if mv is None:
            continue
        assert any(re.match(rx, mv) for rx in NODEODM_DOMAIN_REGEXES), \
            f"domain '{mv}' for {k} is not accepted by NodeODM checkDomain"
        checked += 1
    print(f"ok  {checked} metavar domains valid for NodeODM")


def test_enum_default_is_a_choice():
    src = {opt["name"]: opt for opt in _load()}
    for name, opt in src.items():
        if opt["type"] == "enum":
            assert str(opt["value"]) in [str(c) for c in opt["domain"]], \
                f"default for {name} not in its choices"
    print("ok  enum defaults are within their choices")


SPARSE_COLMAP = os.path.join(HERE, "..", "pipeline", "sparse_colmap.sh")


def test_every_features_value_is_wired_in_sparse_colmap():
    """Each --features choice must have a case arm in pipeline/sparse_colmap.sh.

    The failure this guards against is silent and expensive: a value advertised in
    options.json that the pipeline does not handle reaches COLMAP as an unknown
    argument, aborts the run, and surfaces in WebODM only as "Cannot process
    dataset". 'sift' is exempt — it is the default and deliberately passes NO extra
    flags, so the pre-existing code path stays byte-identical.
    """
    src = {opt["name"]: opt for opt in _load()}
    assert "features" in src, "the features option disappeared from options.json"
    with open(SPARSE_COLMAP) as f:
        script = f.read()
    for choice in src["features"]["domain"]:
        if choice == "sift":
            continue
        assert re.search(rf"^\s*{re.escape(choice)}\)", script, re.M), \
            f"--features={choice} is advertised but has no case arm in sparse_colmap.sh"
    print("ok  every features choice is wired in sparse_colmap.sh")


def test_features_and_matcher_types_stay_paired():
    """ALIKED extraction must never be paired with a SIFT matcher, or vice versa.

    Upstream documents that mixing feature types fails and that one database.db
    cannot hold two of them. The pipeline derives both ends from a single option so
    the mismatch is unrepresentable; this test locks that property in place.
    """
    with open(SPARSE_COLMAP) as f:
        script = f.read()
    for arm, extract, match in [
        ("sift-lightglue", "SIFT", "SIFT_LIGHTGLUE"),
        ("aliked-n16rot", "ALIKED_N16ROT", "ALIKED_LIGHTGLUE"),
        ("aliked-n32", "ALIKED_N32", "ALIKED_LIGHTGLUE"),
    ]:
        body = script.split(f"{arm})", 1)[1].split(";;", 1)[0]
        assert f"--FeatureExtraction.type {extract}" in body, \
            f"{arm}: wrong or missing extractor type"
        assert f"--FeatureMatching.type {match}" in body, \
            f"{arm}: wrong or missing matcher type"
        family_e = "ALIKED" if extract.startswith("ALIKED") else "SIFT"
        family_m = "ALIKED" if match.startswith("ALIKED") else "SIFT"
        assert family_e == family_m, f"{arm}: extractor/matcher families disagree"
    print("ok  extractor and matcher families are paired for every features choice")


if __name__ == "__main__":
    test_keys_are_double_dash_flags()
    test_type_mapping_matches_nodeodm_expectations()
    test_all_metavar_domains_are_nodeodm_valid()
    test_enum_default_is_a_choice()
    test_every_features_value_is_wired_in_sparse_colmap()
    test_features_and_matcher_types_stay_paired()
    print("\nall options tests passed")
