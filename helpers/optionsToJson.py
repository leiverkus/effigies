#!/usr/bin/env python3
"""Emit Effigies options to the path NodeODM expects (ODM_OPTIONS_TMP_FILE),
mirroring odmOptionsToJson.py but reading our static options.json instead of
introspecting ODM's argparse config."""
import json
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
options_file = os.path.join(here, "..", "options.json")

with open(options_file) as f:
    options = json.load(f)

dest = os.environ.get("ODM_OPTIONS_TMP_FILE")
out = json.dumps(options)
if dest:
    with open(dest, "w") as f:
        f.write(out)
else:
    sys.stdout.write(out)
