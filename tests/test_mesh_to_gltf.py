#!/usr/bin/env python3
"""Unit test for helpers/mesh_to_gltf.py — the self-contained .glb writer.

Builds a small textured OBJ and checks the produced GLB is a well-formed binary
glTF: header, JSON + BIN chunks, accessor counts, an embedded texture. Parses the
GLB by hand so no glTF library is required (pure numpy).

Run:  python3 tests/test_mesh_to_gltf.py
"""
import os
import sys
import json
import struct
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "helpers"))
import mesh_to_gltf as mg  # noqa: E402


def _textured_obj(work, side=6):
    """A side×side textured grid named like OpenMVS' refined textured output."""
    base = "scene_dense_mesh_refine_texture"
    open(os.path.join(work, base + "_material_00_map_Kd.png"), "wb").write(
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)        # bytes only need to embed, not decode
    open(os.path.join(work, base + ".mtl"), "w").write(
        f"newmtl m\nmap_Kd {base}_material_00_map_Kd.png\n")
    lines = [f"mtllib {base}.mtl"]
    g = [i * 2.0 for i in range(side)]
    for y in g:
        for x in g:
            lines.append(f"v {x} {y} 0")
    for y in g:
        for x in g:
            lines.append(f"vt {x/10} {y/10}")
    ix = lambda r, c: r * side + c + 1
    lines.append("usemtl m")
    for r in range(side - 1):
        for c in range(side - 1):
            a, b, cc, d = ix(r, c), ix(r, c + 1), ix(r + 1, c + 1), ix(r + 1, c)
            lines.append(f"f {a}/{a} {b}/{b} {cc}/{cc}")
            lines.append(f"f {a}/{a} {cc}/{cc} {d}/{d}")
    open(os.path.join(work, base + ".obj"), "w").write("\n".join(lines) + "\n")
    return side * side


def _read_glb(path):
    b = open(path, "rb").read()
    assert b[:4] == b"glTF", b[:4]
    ver, total = struct.unpack("<II", b[4:12])
    assert ver == 2 and total == len(b), (ver, total, len(b))
    jlen, = struct.unpack("<I", b[12:16])
    assert b[16:20] == b"JSON", b[16:20]
    js = json.loads(b[20:20 + jlen])
    o = 20 + jlen
    blen, = struct.unpack("<I", b[o:o + 4])
    assert b[o + 4:o + 8] == b"BIN\x00", b[o + 4:o + 8]
    return js, blen


def test_writes_valid_glb():
    with tempfile.TemporaryDirectory() as work:
        nverts = _textured_obj(work, side=6)          # 36 grid vertices
        argv = sys.argv
        try:
            sys.argv = ["mesh_to_gltf.py", "--work", work]
            mg.main()
        finally:
            sys.argv = argv
        glb = os.path.join(work, "odm_textured_model_geo.glb")
        assert os.path.exists(glb), "no glb written"
        js, blen = _read_glb(glb)
    assert js["asset"]["version"] == "2.0", js["asset"]
    assert len(js["meshes"]) == 1 and len(js["accessors"]) == 3, js
    prim = js["meshes"][0]["primitives"][0]
    assert "POSITION" in prim["attributes"] and "TEXCOORD_0" in prim["attributes"], prim
    assert "indices" in prim and prim.get("material") == 0, prim
    # every grid vertex carries exactly one uv here -> POSITION count == grid verts
    assert js["accessors"][0]["count"] == nverts, (js["accessors"][0]["count"], nverts)
    assert "min" in js["accessors"][0] and "max" in js["accessors"][0], js["accessors"][0]
    assert js["images"][0]["mimeType"] == "image/png", js["images"]
    assert js["buffers"][0]["byteLength"] == blen, (js["buffers"][0]["byteLength"], blen)
    print("ok  mesh_to_gltf writes a well-formed binary glTF with an embedded texture")


if __name__ == "__main__":
    test_writes_valid_glb()
    print("\nall mesh_to_gltf tests passed")
