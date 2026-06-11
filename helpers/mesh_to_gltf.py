#!/usr/bin/env python3
"""
Export the textured mesh as a binary glTF (``.glb``) — WebODM's
"Struktur-Modell (glTF)" download (``odm_texturing/odm_textured_model_geo.glb``).

Self-contained writer: no node/obj2gltf (the image's node is too old for current
obj2gltf), no extra Python deps. Reads the textured OBJ (the same one georef
rewrote into offset-subtracted projected coordinates, so the .glb matches the .obj
asset), de-indexes the OBJ's separate vertex/texcoord indices into glTF vertices,
and embeds the texture atlas inside the .glb.
"""
import argparse
import glob
import json
import os
import struct
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from openmvs_mesh import find_mesh_obj  # noqa: E402


def _parse_obj(path):
    """-> (V[N,3], VT[M,2], faces[(vidx,vtidx) per face], texture_path) or None."""
    V, VT, faces, mtllib = [], [], [], None
    with open(path, "r", errors="ignore") as f:
        for line in f:
            if line.startswith("v "):
                p = line.split(); V.append((float(p[1]), float(p[2]), float(p[3])))
            elif line.startswith("vt "):
                p = line.split(); VT.append((float(p[1]), float(p[2])))
            elif line.startswith("f "):
                vi, vti = [], []
                for tok in line.split()[1:]:
                    a = tok.split("/")
                    vi.append(int(a[0]))
                    vti.append(int(a[1]) if len(a) > 1 and a[1] else 0)
                faces.append((vi, vti))
            elif line.startswith("mtllib"):
                mtllib = line.split(maxsplit=1)[1].strip()
    if not VT:
        return None
    base = os.path.dirname(path)
    tex = None
    mtl = os.path.join(base, mtllib) if mtllib else None
    if mtl and os.path.exists(mtl):
        for ln in open(mtl, errors="ignore"):
            if ln.strip().lower().startswith("map_kd"):
                tex = os.path.join(base, ln.split()[-1]); break
    if not tex or not os.path.exists(tex):
        c = sorted(glob.glob(os.path.join(base, "*map_Kd*")))
        tex = c[0] if c else None
    if not tex:
        return None
    return (np.asarray(V, np.float64), np.asarray(VT, np.float64), faces, tex)


def _deindex(V, VT, faces):
    """OBJ has independent vertex/texcoord indices; glTF needs one index stream.
    Build unique (vertex, texcoord) pairs and fan-triangulate."""
    pos, uv, idx, seen = [], [], [], {}
    nv, nvt = len(V), len(VT)
    for vi, vti in faces:
        vi = [(i - 1) if i > 0 else (nv + i) for i in vi]
        vti = [((i - 1) if i > 0 else (nvt + i)) if i != 0 else -1 for i in vti]
        tri = []
        for k in range(len(vi)):
            key = (vi[k], vti[k])
            if key not in seen:
                seen[key] = len(pos)
                pos.append(V[vi[k]])
                u, v = (VT[vti[k]] if vti[k] >= 0 else (0.0, 0.0))
                uv.append((u, 1.0 - v))          # glTF texcoord origin is top-left
            tri.append(seen[key])
        for k in range(1, len(tri) - 1):          # fan-triangulate
            idx += [tri[0], tri[k], tri[k + 1]]
    return (np.asarray(pos, np.float32), np.asarray(uv, np.float32),
            np.asarray(idx, np.uint32))


def _pad(b, fill=b"\x00"):
    return b + fill * ((4 - len(b) % 4) % 4)


def write_glb(out_path, pos, uv, idx, tex_path):
    pos_b, uv_b, idx_b = pos.tobytes(), uv.tobytes(), idx.tobytes()
    img_b = open(tex_path, "rb").read()
    mime = "image/png" if tex_path.lower().endswith(".png") else "image/jpeg"
    parts = [_pad(pos_b), _pad(uv_b), _pad(idx_b), _pad(img_b)]
    off = np.cumsum([0] + [len(p) for p in parts])
    buf = b"".join(parts)

    gltf = {
        "asset": {"version": "2.0", "generator": "effigies"},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "TEXCOORD_0": 1},
                                    "indices": 2, "material": 0}]}],
        "materials": [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0},
                       "metallicFactor": 0.0, "roughnessFactor": 1.0},
                       "doubleSided": True}],
        "textures": [{"source": 0, "sampler": 0}],
        "images": [{"bufferView": 3, "mimeType": mime}],
        "samplers": [{}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": int(len(pos)),
             "type": "VEC3", "min": pos.min(0).tolist(), "max": pos.max(0).tolist()},
            {"bufferView": 1, "componentType": 5126, "count": int(len(pos)), "type": "VEC2"},
            {"bufferView": 2, "componentType": 5125, "count": int(len(idx)), "type": "SCALAR"},
        ],
        "bufferViews": [
            {"buffer": 0, "byteOffset": int(off[0]), "byteLength": len(pos_b), "target": 34962},
            {"buffer": 0, "byteOffset": int(off[1]), "byteLength": len(uv_b), "target": 34962},
            {"buffer": 0, "byteOffset": int(off[2]), "byteLength": len(idx_b), "target": 34963},
            {"buffer": 0, "byteOffset": int(off[3]), "byteLength": len(img_b)},
        ],
        "buffers": [{"byteLength": len(buf)}],
    }
    js = _pad(json.dumps(gltf, separators=(",", ":")).encode(), b" ")
    glb = (b"glTF" + struct.pack("<II", 2, 12 + 8 + len(js) + 8 + len(buf))
           + struct.pack("<I", len(js)) + b"JSON" + js
           + struct.pack("<I", len(buf)) + b"BIN\x00" + buf)
    with open(out_path, "wb") as f:
        f.write(glb)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True, help="OpenMVS workdir")
    args = ap.parse_args()
    name = find_mesh_obj(args.work)
    if not name or "texture" not in name:
        print("[gltf] no textured OBJ found; skipping glTF", file=sys.stderr)
        return
    parsed = _parse_obj(os.path.join(args.work, name))
    if parsed is None:
        print("[gltf] OBJ has no texture; skipping glTF", file=sys.stderr)
        return
    V, VT, faces, tex = parsed
    pos, uv, idx = _deindex(V, VT, faces)
    out = os.path.join(args.work, "odm_textured_model_geo.glb")
    write_glb(out, pos, uv, idx, tex)
    print(f"[gltf] wrote {os.path.basename(out)} "
          f"({len(pos)} verts, {len(idx)//3} tris, texture {os.path.basename(tex)})")


if __name__ == "__main__":
    main()
