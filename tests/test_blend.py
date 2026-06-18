#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Patrick Leiverkus
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for helpers/texture_blend.py — the multi-view blended texturing step.

Two layers, mirroring the streaming refactor (docs/blend-streaming-plan.md):

  * test_topk_kernel_equivalence — a pure, deterministic proof that the streaming
    running-top-K selection (_topk_insert + _finalize_topk) returns the SAME top-K
    indices and normalised weights as the old dense argsort over a full [F,V] matrix.
    numpy only; always runs.

  * test_blend_golden — an end-to-end run of blend() on a tiny hand-built synthetic
    scene (3 PINHOLE views, a 2-triangle quad sharing an edge, a small atlas),
    compared against a captured golden atlas within atol=1 (uint8). The golden is
    generated once from the reference implementation via `--capture-golden` and
    committed as tests/fixtures/blend_golden.npy. Phase 1 reproduces it bit-for-bit;
    Phase 2 (view-major bake) reproduces it up to float-summation rounding, hence
    atol=1. Needs PIL — skipped (like the scipy-gated tests) when unavailable.

Run:  python3 tests/test_blend.py
      python3 tests/test_blend.py --capture-golden   # regenerate the fixture
"""
import os
import sys
import struct
import shutil
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "helpers"))
import texture_blend as tb  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "blend_golden.npy")


def _have_pil():
    try:
        from PIL import Image  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Synthetic scene — a 2-triangle quad facing 3 PINHOLE cameras, with a small
# atlas. The two triangles share edge v0-v2 so the bake exercises multi-face
# overlap at the diagonal (the eps=-0.02 case that distinguishes Phase 2).
# ---------------------------------------------------------------------------
W = H = 64                 # image size (px)
FX = FY = 80.0
CX = CY = 32.0
ATLAS = 64                 # atlas page size (px)

# camera centres (world); R = identity, so world-to-cam t = -C, and a world point
# at z=0 projects with cam-z = +5 (in front). Five offsets give parallax and, with
# 5 > TOP_K=4 views, exercise the common "more views than slots" selection path.
_CENTERS = [(0.0, 0.0, -5.0), (1.0, 0.0, -5.0), (-1.0, 0.5, -5.0),
            (0.5, -0.8, -5.0), (-0.6, -0.4, -5.0)]
_NAMES = ["img0.png", "img1.png", "img2.png", "img3.png", "img4.png"]


def _pack_cameras_bin(path):
    with open(path, "wb") as f:
        f.write(struct.pack("<Q", 1))                       # n_cameras
        f.write(struct.pack("<ii", 1, 1))                   # camera_id=1, model_id=1 (PINHOLE)
        f.write(struct.pack("<QQ", W, H))
        f.write(struct.pack("<4d", FX, FY, CX, CY))


def _pack_images_bin(path):
    with open(path, "wb") as f:
        f.write(struct.pack("<Q", len(_NAMES)))             # n_images
        for i, (name, C) in enumerate(zip(_NAMES, _CENTERS), 1):
            t = (-C[0], -C[1], -C[2])                       # R=I -> t = -C
            f.write(struct.pack("<i", i))                   # image_id
            f.write(struct.pack("<7d", 1.0, 0.0, 0.0, 0.0,  # quat (identity)
                                t[0], t[1], t[2]))
            f.write(struct.pack("<i", 1))                   # camera_id
            f.write(name.encode("utf-8") + b"\x00")
            f.write(struct.pack("<Q", 0))                   # 0 points2D


def build_scene(work):
    """Write a full synthetic blend workspace under ``work`` and return the OBJ
    name. Deterministic — no randomness — so the golden is reproducible."""
    from PIL import Image
    sparse = os.path.join(work, "dense", "sparse")
    imdir = os.path.join(work, "dense", "images")
    os.makedirs(sparse, exist_ok=True)
    os.makedirs(imdir, exist_ok=True)
    _pack_cameras_bin(os.path.join(sparse, "cameras.bin"))
    _pack_images_bin(os.path.join(sparse, "images.bin"))

    # per-view images: a deterministic gradient, offset per view so blending the
    # views produces a non-trivial (view-dependent) result the atol test can catch.
    yy, xx = np.mgrid[0:H, 0:W]
    for vi, name in enumerate(_NAMES):
        img = np.stack([(xx * 4 + vi * 30) % 256,
                        (yy * 4 + vi * 15) % 256,
                        np.full((H, W), 100 + vi * 40)], -1).astype(np.uint8)
        Image.fromarray(img).save(os.path.join(imdir, name))

    # textured quad: corners CCW so the face normal points -z (toward the cameras
    # at z=-5). Two triangles share edge v0-v2.
    obj = os.path.join(work, "scene_dense_mesh_refine_texture.obj")
    with open(obj, "w") as f:
        f.write("mtllib model.mtl\n")
        f.write("v -1 -1 0\nv 1 -1 0\nv 1 1 0\nv -1 1 0\n")
        f.write("vt 0 0\nvt 1 0\nvt 1 1\nvt 0 1\n")
        f.write("usemtl mat0\n")
        f.write("f 1/1 3/3 2/2\n")          # v0,v2,v1  (normal -z)
        f.write("f 1/1 4/4 3/3\n")          # v0,v3,v2
    with open(os.path.join(work, "model.mtl"), "w") as f:
        f.write("newmtl mat0\nmap_Kd atlas.png\n")
    # a mid-grey atlas so re-baked texels differ clearly from the original
    Image.fromarray(np.full((ATLAS, ATLAS, 3), 128, np.uint8)).save(
        os.path.join(work, "atlas.png"))
    return obj


def _run_blend_capture_atlas(work):
    """Run blend() on a fresh copy of ``work`` and return the re-baked atlas array.
    blend() rewrites atlas.png in place, so callers pass a throwaway copy."""
    from PIL import Image
    ok = tb.blend(work)
    assert ok, "blend() returned False on the synthetic scene"
    return np.asarray(Image.open(os.path.join(work, "atlas.png")).convert("RGB"))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_topk_kernel_equivalence():
    """Streaming running-top-K (_topk_insert + _finalize_topk) must reproduce the
    dense argsort-top-K selection exactly: same normalised weights, and same view
    indices wherever a slot carries weight. Covers nV<K, nV==K, nV>K and faces with
    no valid view. Continuous random weights -> no manufactured ties."""
    rng = np.random.default_rng(0)
    nF = 300
    for nV in (2, tb.TOP_K, 7):
        cols = []
        for _ in range(nV):
            c = rng.random(nF).astype(np.float32)
            c[rng.random(nF) < 0.3] = 0.0        # ~30% of faces "not visible" here
            cols.append(c)

        # streaming
        top_idx = np.full((nF, tb.TOP_K), -1, np.int32)
        top_w = np.zeros((nF, tb.TOP_K), np.float32)
        for vi, c in enumerate(cols):
            tb._topk_insert(top_idx, top_w, c, vi)
        top_s, w_s = tb._finalize_topk(top_idx, top_w)

        # dense reference. Pad to TOP_K columns when nV < K so the reference has K
        # slots like the streaming version (the old un-padded argsort produced <K
        # columns for nV<K — a latent bug the streaming [nF,K] arrays fix; with the
        # production nV>=K it is a plain argsort).
        Wt = np.stack(cols, 1)                    # [nF, nV]
        if nV < tb.TOP_K:
            Wt = np.concatenate(
                [Wt, np.zeros((nF, tb.TOP_K - nV), np.float32)], axis=1)
        top_d = np.argsort(-Wt, axis=1)[:, :tb.TOP_K]
        tw = np.take_along_axis(Wt, top_d, axis=1)
        best = tw[:, :1]
        tw = np.where(tw >= tb.WEIGHT_FLOOR * np.maximum(best, 1e-12), tw, 0.0)
        s = tw.sum(1, keepdims=True)
        tw = np.where(s > 0, tw / np.maximum(s, 1e-12), 0.0).astype(np.float32)

        assert np.array_equal(w_s, tw), f"weights differ for nV={nV}"
        pos = w_s > 0
        assert np.array_equal(top_s[pos], top_d[pos]), f"indices differ for nV={nV}"
        # coverage agreement (which faces ended up with any valid view)
        assert np.array_equal(w_s.sum(1) > 0, tw.sum(1) > 0)
    print("ok  streaming top-K == dense argsort top-K (weights + indices, nV<K/=K/>K)")


def test_blend_golden():
    if not _have_pil():
        print("skip blend-golden (needs PIL — present in the Effigies image)")
        return
    if not os.path.exists(FIXTURE):
        print(f"skip blend-golden (no fixture at {FIXTURE}; run --capture-golden)")
        return
    golden = np.load(FIXTURE)
    with tempfile.TemporaryDirectory() as root:
        work = os.path.join(root, "work")
        os.makedirs(work)
        build_scene(work)
        out = _run_blend_capture_atlas(work)
    assert out.shape == golden.shape, (out.shape, golden.shape)
    # Phase 1 reproduces the golden exactly; Phase 2's view-major accumulation
    # reorders float summation -> atol=1 on uint8 (per docs/blend-streaming-plan.md).
    diff = np.abs(out.astype(np.int16) - golden.astype(np.int16))
    assert diff.max() <= 1, f"blend output drifted from golden (max |Δ|={diff.max()})"
    # the bake must actually have changed texels (not a no-op that trivially matches)
    assert (golden != 128).any(), "golden atlas was never re-baked (scene broken)"
    print(f"ok  blend end-to-end matches golden (max |Δ|={diff.max()}, atol=1)")


def _capture_golden():
    """Regenerate tests/fixtures/blend_golden.npy from the CURRENT implementation."""
    if not _have_pil():
        sys.exit("cannot capture golden without PIL")
    os.makedirs(os.path.dirname(FIXTURE), exist_ok=True)
    with tempfile.TemporaryDirectory() as root:
        work = os.path.join(root, "work")
        os.makedirs(work)
        build_scene(work)
        out = _run_blend_capture_atlas(work)
    np.save(FIXTURE, out)
    print(f"captured golden -> {FIXTURE} (shape {out.shape}, "
          f"{(out != 128).any()} re-baked)")


if __name__ == "__main__":
    if "--capture-golden" in sys.argv:
        _capture_golden()
        sys.exit(0)
    test_topk_kernel_equivalence()
    test_blend_golden()
    print("\nall blend tests passed")
