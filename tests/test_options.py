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


if __name__ == "__main__":
    test_keys_are_double_dash_flags()
    test_type_mapping_matches_nodeodm_expectations()
    test_all_metavar_domains_are_nodeodm_valid()
    test_enum_default_is_a_choice()
    print("\nall options tests passed")
