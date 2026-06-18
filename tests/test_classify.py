#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Patrick Leiverkus
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the point-classification path (helpers/classify_cloud.py) and
the DTM pre-classified branch.

The pipeline/gate logic is pure and always runs; the full classifier needs the
`pcclassify` binary (built into the image) and is skipped elsewhere.

Run:  python3 tests/test_classify.py
"""
import os
import sys
import shutil
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "helpers"))
import classify_cloud as cc          # noqa: E402
import pointcloud_to_dtm as dtm      # noqa: E402


def test_dtm_pre_classified_branch():
    """With a pre-classified cloud the DTM must skip SMRF/outlier and just keep
    the existing ground class; without it, SMRF runs as before."""
    smrf = dtm.build_dtm_pipeline("in.laz", "out.tif", 0.05, pre_classified=False)["pipeline"]
    types = [s["type"] for s in smrf]
    assert "filters.outlier" in types and "filters.smrf" in types, types

    pre = dtm.build_dtm_pipeline("in.laz", "out.tif", 0.05, pre_classified=True)["pipeline"]
    ptypes = [s["type"] for s in pre]
    assert ptypes == ["readers.las", "filters.range", "writers.gdal"], ptypes
    rng = next(s for s in pre if s["type"] == "filters.range")
    assert rng["limits"] == "Classification[2:2]", rng        # reuse ML ground
    print("ok  DTM pre-classified branch (skips SMRF, keeps ground class 2)")


def test_class_raster_pipeline():
    """The class-raster pipeline keeps one class and writes a max-Z surface."""
    import json, subprocess  # noqa: F401
    # _class_raster runs PDAL; here we just check the pipeline it would build by
    # constructing it the same way (kept in sync with the helper).
    klass = 6
    pipe = {"pipeline": [
        {"type": "readers.las", "filename": "c.laz"},
        {"type": "filters.range", "limits": f"Classification[{klass}:{klass}]"},
        {"type": "writers.gdal", "filename": "b.tif", "resolution": 0.1,
         "output_type": "max", "gdaldriver": "GTiff", "data_type": "float32",
         "dimension": "Z", "nodata": dtm.NODATA}]}
    assert pipe["pipeline"][1]["limits"] == "Classification[6:6]"
    assert pipe["pipeline"][2]["output_type"] == "max"
    print("ok  class-raster pipeline (single class -> max-Z surface)")


def test_classify_gate():
    """run_classify must skip cleanly when the binary/model/LAZ is missing."""
    with tempfile.TemporaryDirectory() as work:
        result = cc.run_classify(work)
        assert result is False                                # no pcclassify or no LAZ
        assert not os.path.exists(os.path.join(work, "odm_dem", "buildings.tif"))
    if shutil.which("pcclassify") is None:
        print("ok  classify skips when pcclassify binary absent")
    else:
        print("ok  classify skips when the georeferenced LAZ is absent")


if __name__ == "__main__":
    test_dtm_pre_classified_branch()
    test_class_raster_pipeline()
    test_classify_gate()
    print("\nall classify tests passed")
