#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Patrick Leiverkus
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Serve Effigies' task options to NodeODM.

NodeODM (libs/odmInfo.js) does NOT consume a flat list of options. It expects the
SAME shape ODM's own `odmOptionsToJson.py` produces: a JSON OBJECT keyed by the
argparse flag (e.g. "--refine-mesh-iters"), each value an argparse-style
descriptor. NodeODM then derives the {name, type, value, domain, help} it serves
to WebODM from those descriptors:

  * type    <- "<class 'int'>" / "<class 'float'>" ; default "True"/"False" => bool
  * value   <- "default"
  * domain  <- "metavar"  (or "choices" -> enum)
  * help    <- "help"

So we author options in a friendly flat list (options.json) and translate it here
into the descriptor object NodeODM wants. Domains must match NodeODM's checkDomain
grammar (e.g. "positive integer", "float: 0 <= x <= 1", or an enum choices list),
or task submission fails validation.
"""
import json
import os
import sys


def _choices_repr(domain):
    """NodeODM parses choices by replacing ' with " then JSON.parse, i.e. it wants
    a Python-list-repr string like "['a', 'b']"."""
    return "[" + ", ".join("'%s'" % c for c in domain) + "]"


def to_nodeodm(options):
    """Translate our flat options list into NodeODM's argparse-style descriptor dict."""
    out = {}
    for opt in options:
        key = "--" + opt["name"]
        t = opt.get("type", "string")
        val = opt.get("value", "")
        desc = {"help": opt.get("help", "")}

        if t == "enum":
            desc["type"] = "<class 'str'>"
            desc["default"] = str(val)
            desc["choices"] = _choices_repr(opt.get("domain", []))
        elif t == "int":
            desc["type"] = "<class 'int'>"
            desc["default"] = str(val)
            if opt.get("domain"):
                desc["metavar"] = str(opt["domain"])
        elif t == "float":
            desc["type"] = "<class 'float'>"
            desc["default"] = str(val)
            if opt.get("domain"):
                desc["metavar"] = str(opt["domain"])
        elif t == "bool":
            # NodeODM keys bool off default == "True"/"False"; no metavar/domain.
            desc["default"] = "True" if (val is True or str(val).lower() == "true") else "False"
        else:  # string / path
            desc["type"] = "<class 'str'>"
            desc["default"] = str(val)
            if opt.get("domain"):
                desc["metavar"] = str(opt["domain"])

        out[key] = desc
    return out


def _options_file():
    # NodeODM invokes this via a symlink in /opt/NodeODM/helpers/, so __file__ may
    # be that symlink. Prefer ODM_PATH (NodeODM sets it to the engine dir) and resolve
    # the real script location (realpath follows the symlink) as the fallback — never
    # use abspath, which would look next to the symlink, not the engine.
    odm_path = os.environ.get("ODM_PATH")
    if odm_path:
        return os.path.join(odm_path, "options.json")
    here = os.path.dirname(os.path.realpath(__file__))
    return os.path.join(here, "..", "options.json")


def main():
    with open(_options_file()) as f:
        options = json.load(f)

    out = json.dumps(to_nodeodm(options))
    dest = os.environ.get("ODM_OPTIONS_TMP_FILE")
    if dest:
        with open(dest, "w") as f:
            f.write(out)
    else:
        sys.stdout.write(out)


if __name__ == "__main__":
    main()
