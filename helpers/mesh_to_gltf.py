#!/usr/bin/env python3
"""
Export the textured mesh as a binary glTF (``.glb``) — WebODM's
"Struktur-Modell (glTF)" download (``odm_texturing/odm_textured_model_geo.glb``).

Self-contained writer: no node/obj2gltf (the image's node is too old for current
obj2gltf), no extra Python deps. Reads the textured OBJ (the same one georef
rewrote into offset-subtracted projected coordinates, so the .glb matches the .obj
asset), de-indexes the OBJ's separate vertex/texcoord indices into glTF vertices,
and embeds the texture atlas pages inside the .glb.

MULTI-MATERIAL: large inputs make OpenMVS split the texture atlas into several
pages (material_00, material_01, ...; faces bound via ``usemtl``). The glb mirrors
that as one primitive per page, each with its own embedded texture — flattening
everything onto page 0 scrambles the model's colours.

For georeferenced results the glb carries the ``CESIUM_RTC`` extension (center =
the 2D vertex offset): WebODM's ModelView translates the scene by it to place the
model next to the full-coordinate point cloud — without it the model sits at the
UTM origin, kilometres out of view.
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


def _parse_mtl(mtl_path):
    """-> {material_name: map_Kd path} from an .mtl file."""
    mats, cur = {}, None
    base = os.path.dirname(mtl_path)
    for ln in open(mtl_path, errors="ignore"):
        t = ln.strip()
        if t.lower().startswith("newmtl"):
            cur = t.split(maxsplit=1)[1].strip()
        elif t.lower().startswith("map_kd") and cur:
            mats[cur] = os.path.join(base, t.split()[-1])
    return mats


def _parse_obj(path):
    """-> (V[N,3], VT[M,2], faces[(vi, vti, mat_slot)], tex_paths[slot]) or None."""
    V, VT, faces, mtllib = [], [], [], None
    cur_mat, mat_ids = 0, {}
    with open(path, "r", errors="ignore") as f:
        for line in f:
            if line.startswith("v "):
                p = line.split(); V.append((float(p[1]), float(p[2]), float(p[3])))
            elif line.startswith("vt "):
                p = line.split(); VT.append((float(p[1]), float(p[2])))
            elif line.startswith("usemtl"):
                name = line.split(maxsplit=1)[1].strip()
                cur_mat = mat_ids.setdefault(name, len(mat_ids))
            elif line.startswith("f "):
                vi, vti = [], []
                for tok in line.split()[1:]:
                    a = tok.split("/")
                    vi.append(int(a[0]))
                    vti.append(int(a[1]) if len(a) > 1 and a[1] else 0)
                faces.append((vi, vti, cur_mat))
            elif line.startswith("mtllib"):
                mtllib = line.split(maxsplit=1)[1].strip()
    if not VT:
        return None
    base = os.path.dirname(path)
    mtl = os.path.join(base, mtllib) if mtllib else None
    mat_tex = _parse_mtl(mtl) if (mtl and os.path.exists(mtl)) else {}
    fallback = sorted(glob.glob(os.path.join(base, "*map_Kd*")))
    if not mat_ids:                    # OBJ without usemtl: single implicit slot
        mat_ids = {"__default__": 0}
    tex_paths = [None] * len(mat_ids)
    for name, slot in mat_ids.items():
        p = mat_tex.get(name)
        if (not p or not os.path.exists(p)) and slot < len(fallback):
            p = fallback[slot]
        if not p or not os.path.exists(p):
            return None
        tex_paths[slot] = p
    return (np.asarray(V, np.float64), np.asarray(VT, np.float64), faces, tex_paths)


def _deindex(V, VT, faces, n_mats):
    """OBJ has independent vertex/texcoord indices; glTF needs one index stream.
    Build unique (vertex, texcoord) pairs (shared across materials) and
    fan-triangulate into ONE index array per material slot.

    Fast path: OpenMVS emits pure triangles, so the unique-pair mapping is done
    with one np.unique over packed (vertex, texcoord) keys instead of a Python
    dict loop (the loop is kept as fallback for polygonal OBJs)."""
    nv, nvt = len(V), len(VT)
    if faces and all(len(f[0]) == 3 for f in faces):
        FV = np.asarray([f[0] for f in faces], np.int64)
        FVT = np.asarray([f[1] for f in faces], np.int64)
        FM = np.asarray([f[2] for f in faces], np.int64)
        FV = np.where(FV > 0, FV - 1, nv + FV)
        FVT = np.where(FVT > 0, FVT - 1, np.where(FVT == 0, -1, nvt + FVT))
        keys = FV * (nvt + 1) + (FVT + 1)         # unique (vi, vti) pairing
        uniq, inv = np.unique(keys.reshape(-1), return_inverse=True)
        uvi = uniq // (nvt + 1)
        uvt = uniq % (nvt + 1) - 1
        pos = V[uvi].astype(np.float32)
        uv = np.zeros((len(uniq), 2), np.float32)
        has = uvt >= 0
        uv[has, 0] = VT[uvt[has], 0]
        uv[has, 1] = 1.0 - VT[uvt[has], 1]        # glTF texcoord origin top-left
        uv[~has, 1] = 1.0                          # matches the (0, 1-0) fallback
        tri_idx = inv.reshape(-1, 3).astype(np.uint32)
        return pos, uv, [tri_idx[FM == m].reshape(-1) for m in range(n_mats)]
    # fallback: arbitrary polygons, dict-based
    pos, uv, seen = [], [], {}
    idx = [[] for _ in range(n_mats)]
    for vi, vti, m in faces:
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
            idx[m] += [tri[0], tri[k], tri[k + 1]]
    return (np.asarray(pos, np.float32), np.asarray(uv, np.float32),
            [np.asarray(ix, np.uint32) for ix in idx])


def _pad(b, fill=b"\x00"):
    return b + fill * ((4 - len(b) % 4) % 4)


def _rtc_center(work):
    """CESIUM_RTC center from georef_transform.json — the (x, y) offset the OBJ
    vertices were shifted by. WebODM's ModelView translates the glTF scene by this
    center to place the model next to the full-coordinate point cloud (exactly how
    ODM's glb behaves). None for local-frame results."""
    p = os.path.join(work, "georef_transform.json")
    if not os.path.exists(p):
        return None
    tr = json.load(open(p))
    crs = tr.get("crs")
    if not crs or str(crs).lower() == "local":
        return None
    off = tr.get("offset", [0, 0, 0])
    return [float(off[0]), float(off[1]), 0.0]


def write_glb(out_path, pos, uv, idx_list, tex_paths, rtc_center=None):
    """One primitive (+ material + embedded texture) per atlas page; POSITION and
    TEXCOORD_0 accessors are shared across primitives."""
    n = len(idx_list)
    pos_b, uv_b = pos.tobytes(), uv.tobytes()
    idx_bs = [ix.tobytes() for ix in idx_list]
    img_bs = [open(p, "rb").read() for p in tex_paths]
    mimes = ["image/png" if p.lower().endswith(".png") else "image/jpeg"
             for p in tex_paths]
    parts = [_pad(pos_b), _pad(uv_b)] + [_pad(b) for b in idx_bs] + [_pad(b) for b in img_bs]
    off = np.cumsum([0] + [len(p) for p in parts])
    buf = b"".join(parts)

    buffer_views = [
        {"buffer": 0, "byteOffset": int(off[0]), "byteLength": len(pos_b), "target": 34962},
        {"buffer": 0, "byteOffset": int(off[1]), "byteLength": len(uv_b), "target": 34962},
    ]
    accessors = [
        {"bufferView": 0, "componentType": 5126, "count": int(len(pos)),
         "type": "VEC3", "min": pos.min(0).tolist(), "max": pos.max(0).tolist()},
        {"bufferView": 1, "componentType": 5126, "count": int(len(pos)), "type": "VEC2"},
    ]
    # bufferView layout: [0]=POSITION, [1]=TEXCOORD, [2..2+n)=indices per
    # material, [2+n..2+2n)=embedded images per material
    buffer_views += [{"buffer": 0, "byteOffset": int(off[2 + i]),
                      "byteLength": len(idx_bs[i]), "target": 34963}
                     for i in range(n)]
    buffer_views += [{"buffer": 0, "byteOffset": int(off[2 + n + i]),
                      "byteLength": len(img_bs[i])} for i in range(n)]
    primitives, materials, textures, images = [], [], [], []
    for i in range(n):
        accessors.append({"bufferView": 2 + i, "componentType": 5125,
                          "count": int(len(idx_list[i])), "type": "SCALAR"})
        primitives.append({"attributes": {"POSITION": 0, "TEXCOORD_0": 1},
                           "indices": 2 + i, "material": i})
        materials.append({"pbrMetallicRoughness": {"baseColorTexture": {"index": i},
                          "metallicFactor": 0.0, "roughnessFactor": 1.0},
                          "doubleSided": True})
        textures.append({"source": i, "sampler": 0})
        images.append({"bufferView": 2 + n + i, "mimeType": mimes[i]})

    gltf = {
        "asset": {"version": "2.0", "generator": "effigies"},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
        **({"extensionsUsed": ["CESIUM_RTC"],
            "extensions": {"CESIUM_RTC": {"center": rtc_center}}}
           if rtc_center else {}),
        "meshes": [{"primitives": primitives}],
        "materials": materials,
        "textures": textures,
        "images": images,
        "samplers": [{}],
        "accessors": accessors,
        "bufferViews": buffer_views,
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
    V, VT, faces, tex_paths = parsed
    pos, uv, idx_list = _deindex(V, VT, faces, len(tex_paths))
    rtc = _rtc_center(args.work)
    out = os.path.join(args.work, "odm_textured_model_geo.glb")
    write_glb(out, pos, uv, idx_list, tex_paths, rtc_center=rtc)
    ntris = sum(len(ix) for ix in idx_list) // 3
    print(f"[gltf] wrote {os.path.basename(out)} "
          f"({len(pos)} verts, {ntris} tris, {len(tex_paths)} texture page(s)"
          f"{', CESIUM_RTC ' + str([round(c, 1) for c in rtc]) if rtc else ''})")


if __name__ == "__main__":
    main()
