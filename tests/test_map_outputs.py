#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Patrick Leiverkus
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit test for helpers/map_outputs.py — georef_transform.json asset mapping.

The georef bridge writes georef_transform.json (solved similarity scale + GCP/EXIF
residuals) into the workdir. map_outputs must expose it as a downloadable WebODM
asset under odm_report/ so consumers (e.g. the Mensura scale report) can read it.

Run:  python3 tests/test_map_outputs.py
"""
import os
import sys
import json
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "helpers"))
import map_outputs as mo  # noqa: E402


def _run(work, proj):
    argv = sys.argv
    sys.argv = ["map_outputs.py", "--proj", proj, "--work", work]
    try:
        mo.main()
    finally:
        sys.argv = argv


def test_georef_transform_mapped():
    with tempfile.TemporaryDirectory() as d:
        work, proj = os.path.join(d, "work"), os.path.join(d, "proj")
        os.makedirs(work)
        tr = {"source": "colmap-gcp", "s": 1.0234,
              "R": [[1, 0, 0], [0, 1, 0], [0, 0, 1]], "t": [0, 0, 0],
              "offset": [0, 0, 0], "crs": "EPSG:32633",
              "residuals": {"count": 4, "rms_3d": 0.0021, "max_3d": 0.004}}
        json.dump(tr, open(os.path.join(work, "georef_transform.json"), "w"))
        _run(work, proj)
        dst = os.path.join(proj, "odm_report", "georef_transform.json")
        assert os.path.exists(dst), "georef_transform.json was not mapped to odm_report/"
        got = json.load(open(dst))
        assert got["s"] == 1.0234 and got["residuals"]["rms_3d"] == 0.0021, got
    print("ok  map_outputs exposes georef_transform.json as an odm_report asset")


def test_absent_is_silent():
    with tempfile.TemporaryDirectory() as d:
        work, proj = os.path.join(d, "work"), os.path.join(d, "proj")
        os.makedirs(work)
        _run(work, proj)  # no georef_transform.json present
        assert not os.path.exists(os.path.join(proj, "odm_report", "georef_transform.json"))
    print("ok  map_outputs skips georef_transform.json when it is absent")


if __name__ == "__main__":
    test_georef_transform_mapped()
    test_absent_is_silent()
    print("\nall map_outputs tests passed")
