#!/usr/bin/env python3
"""Unit test for helpers/seam_level.py — our texture seam leveling.

Synthetic case: two quads sharing a mesh edge, textured from two DIFFERENT
regions of one atlas with a deliberate brightness step (one side bright, one
dark — uniform content, the homogeneous-surface worst case). After leveling,
the colour difference across the seam must collapse and the patch interiors
must move toward each other.

Needs scipy (present in the Effigies image); skipped without it.

Run:  python3 tests/test_seam_level.py
"""
import os
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "helpers"))
import seam_level as sl  # noqa: E402


def _have_scipy():
    try:
        import scipy.sparse  # noqa: F401
        return True
    except ImportError:
        return False


def _two_patch_scene(work):
    """Two 4x4-subdivided quads sharing the edge x=1 (3D), mapped to the left /
    right half of a 256x128 atlas. Left half value 180, right half 120."""
    from PIL import Image
    n = 5                                     # 5x5 vertices per quad
    vs, vts, faces = [], [], []
    # quad A: x in [0,1]; quad B: x in [1,2] — shared 3D vertices on x=1
    grid_id = {}

    def vid(x, y):
        key = (round(x, 4), round(y, 4))
        if key not in grid_id:
            grid_id[key] = len(vs)
            vs.append((x, y, 0.0))
        return grid_id[key]

    def add_quad(x0, u0, u1):
        for j in range(n - 1):
            for i in range(n - 1):
                xa = x0 + i / (n - 1); xb = x0 + (i + 1) / (n - 1)
                ya = j / (n - 1); yb = (j + 1) / (n - 1)
                ids = [vid(xa, ya), vid(xb, ya), vid(xb, yb), vid(xa, yb)]
                uvs = []
                for (xx, yy) in ((xa, ya), (xb, ya), (xb, yb), (xa, yb)):
                    u = u0 + (u1 - u0) * (xx - x0)
                    vts.append((u, yy)); uvs.append(len(vts) - 1)
                faces.append((ids[:3], uvs[:3]))
                faces.append(([ids[0], ids[2], ids[3]], [uvs[0], uvs[2], uvs[3]]))

    add_quad(0.0, 0.05, 0.45)                 # patch A -> left atlas half
    add_quad(1.0, 0.55, 0.95)                 # patch B -> right atlas half
    tex = np.zeros((128, 256, 3), np.uint8)
    tex[:, :128] = 180
    tex[:, 128:] = 120
    Image.fromarray(tex).save(os.path.join(work, "t_map_Kd.png"))
    with open(os.path.join(work, "scene_dense_mesh_refine_texture.mtl"), "w") as f:
        f.write("newmtl m\nmap_Kd t_map_Kd.png\n")
    with open(os.path.join(work, "scene_dense_mesh_refine_texture.obj"), "w") as f:
        f.write("mtllib scene_dense_mesh_refine_texture.mtl\n")
        for v in vs:
            f.write(f"v {v[0]} {v[1]} {v[2]}\n")
        for t in vts:
            f.write(f"vt {t[0]} {t[1]}\n")
        f.write("usemtl m\n")
        for vi, vti in faces:
            f.write("f " + " ".join(f"{a+1}/{b+1}" for a, b in zip(vi, vti)) + "\n")


def test_seam_step_collapses():
    if not _have_scipy():
        print("skip seam-level (needs scipy — present in the Effigies image)")
        return
    from PIL import Image
    with tempfile.TemporaryDirectory() as work:
        _two_patch_scene(work)
        ok = sl.solve_and_bake(
            os.path.join(work, "scene_dense_mesh_refine_texture.obj"))
        assert ok, "seam leveling reported nothing to do"
        tex = np.asarray(Image.open(os.path.join(work, "t_map_Kd.png"))
                         .convert("RGB"), float)
    # sample the two chart interiors near the shared 3D edge (atlas u≈0.45 / 0.55)
    left = tex[40:90, int(0.40 * 256):int(0.44 * 256)].mean()
    right = tex[40:90, int(0.56 * 256):int(0.60 * 256)].mean()
    step_after = abs(left - right)
    assert step_after < 25, f"seam step still {step_after:.1f} (was 60)"
    assert left < 180 - 5 and right > 120 + 5, (left, right)  # both sides moved
    print(f"ok  seam step 60 -> {step_after:.1f}; both patches moved toward each other")


if __name__ == "__main__":
    test_seam_step_collapses()
    print("\nall seam-level tests passed")
