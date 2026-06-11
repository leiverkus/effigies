#!/usr/bin/env python3
"""Unit test for helpers/report.py — the quality-report PDF.

Needs reportlab (present in the Effigies image); skipped otherwise. Builds a
minimal workdir (COLMAP model + dense ply + georef) and checks a valid PDF is
written. The orthophoto thumbnail path is exercised by the in-image end-to-end
checks, not required here.

Run:  python3 tests/test_report.py
"""
import os
import sys
import json
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "helpers"))
import report as rp  # noqa: E402


def _have_reportlab():
    try:
        import reportlab  # noqa: F401
        return True
    except ImportError:
        return False


def test_report_pdf():
    if not _have_reportlab():
        print("skip report (needs reportlab — present in the Effigies image)")
        return
    with tempfile.TemporaryDirectory() as work:
        m = os.path.join(work, "sparse", "0")
        os.makedirs(m)
        open(os.path.join(m, "cameras.txt"), "w").write(
            "1 OPENCV 2048 1536 1419 1419 1024 768 0 0 0 0\n")
        with open(os.path.join(m, "images.txt"), "w") as f:
            for i in range(1, 4):
                f.write(f"{i} 1 0 0 0 0 0 0 1 img{i}.jpg\n100 200 -1\n")
        open(os.path.join(m, "points3D.txt"), "w").write("")
        open(os.path.join(work, "scene_dense.ply"), "w").write(
            "ply\nformat ascii 1.0\nelement vertex 5000\nproperty float x\nend_header\n")
        json.dump({"source": "colmap-exif", "crs": "EPSG:32632", "offset": [0, 0, 0]},
                  open(os.path.join(work, "georef_transform.json"), "w"))
        argv = sys.argv
        try:
            sys.argv = ["report.py", "--work", work, "--name", "T", "--matcher", "exhaustive"]
            rp.main()
        finally:
            sys.argv = argv
        pdf = os.path.join(work, "report.pdf")
        assert os.path.exists(pdf), "no report.pdf written"
        assert open(pdf, "rb").read(5) == b"%PDF-", "not a PDF"
    print("ok  report.py writes a valid quality-report PDF")


if __name__ == "__main__":
    test_report_pdf()
    print("\nall report tests passed")
