# Planned engine experiments — deferred for the benchmark paper

Two single-variable experiments queued for the **v0.4.0 benchmark** (ROADMAP),
to be run later, not under time pressure. Both are measured against one shared
baseline run so each isolates exactly one parameter. They feed the paper's
*detail vs. watertightness* and *quality vs. runtime* arguments respectively.

## Shared baseline — run `8d2d31de` (2026-06-12)

The reference all deltas are measured against. 70 drone images (~12 MP),
nadir-dominant, CPU/arm64 (no GPU), canonical image `effigies:cpu` (`feada36f`).

**Settings:** `profile=drone-3d` (matcher `spatial`, mapper `incremental`),
`number-views-fuse=2`, `densify-resolution-level=0`, `free-space-support=on`,
`mesh-close-holes=30`, `refine-mesh-iters=3`, `refine-max-face-area=16`,
`refine-gradient-step=25.05`, `texture-resolution=8192`, `cpu-threads=12`.

**Measured runtime ≈ 50 min:**

| Stage | Duration | Share |
|---|---|---|
| COLMAP sparse + undistort | ~8 min | 16 % |
| DensifyPointCloud (res-level 0, 15.5 M pts) | ~17 min | 34 % |
| ReconstructMesh (Delaunay + graph-cut) | ~7 min | 14 % |
| RefineMesh (scales 3, max-face-area 16, 4.0 M→8.0 M faces) | ~9 min | 18 % |
| TextureMesh (8192, 4 atlas pages) | ~2.5 min | 5 % |
| Harmonize + Blend + Seams + Exports | ~8 min | 16 % |

**Measured quality:** roof texture excellent — homogeneous, no patchiness
(roof brightness std ≈ 20.0 vs 23.9 single-view, same scene). **Walls absent:**
the main house roof "floats", large voids under the eaves (see the screenshot
discussion). `mesh-close-holes` was at its default `30` here, so only tiny gaps
were bridged. `free-space-support` recovered ≈ 20 m² of facade vs an earlier
run without it (367 → 387 m² of >70°-steep faces) but also raised boundary
edges +23 % and ortho nodata in the building core to ≈ 1.5 %.

---

## Experiment A — Watertightness (`mesh-close-holes`)

**Question:** Does aggressive hole-closing approach Metashape's "watertight"
look on nadir capture, and at what cost?

**Background (the paper's core finding):** Nadir imagery contains almost no
observations of vertical walls — *neither* engine can *measure* them. OpenMVS'
graph-cut (`ReconstructMesh`) carves away unsupported surface → honest holes.
Metashape (and, ironically, ODM's Screened-Poisson) produce a *closed* surface
by construction → "watertight", but the wall geometry is **interpolated, not
measured**, with roof/ground texture projected (smeared) onto it. This is the
genuine, publishable trade-off: **detail (graph-cut, open) vs. watertightness
(interpolation, closed)** — not a defect on either side. Note also that
`RefineMesh` ("remove unconnected vertices", 1.0 M → 0.82 M verts in the
baseline) appears to additionally carve marginal wall fragments — worth
checking whether walls exist in `scene_dense_mesh.ply` *before* refinement and
are removed by it (run with `--keep-workdir` to inspect).

**Delta from baseline (single variable):** `mesh-close-holes 30 → 500`
(consider a ladder 300 / 500 / 800 if 500 under-closes). Everything else
identical, including `free-space-support=on`.

**Measure:**
- Boundary edges (open-hole indicator) vs baseline 6 777 — expect a sharp drop.
- Ortho nodata in the building core (baseline ≈ 1.5 %) — expect → near 0.
- Visual wall closure + honest assessment of texture smearing on the bridged
  "skirts" (the interpolated geometry carries no real wall texture).
- Runtime impact (close-holes is cheap; expect negligible).

**Expected:** Closer to the Metashape *look* (voids filled), but the bridged
walls are interpolated + texture-smeared, not reconstructed. For *scientific*
documentation this is a fabrication-vs-honest-hole choice to state explicitly,
not a quality win to claim silently.

**Real walls** would require oblique imagery in the capture set — no algorithm
recovers faithful walls from nadir-only. If the set contains obliques, verify
they registered (camera pitch distribution from the COLMAP poses).

---

## Experiment B — Densify resolution vs. runtime (`densify-resolution-level`)

**Question:** How much runtime drops at `densify-resolution-level 1`, and
whether roof / final detail holds.

**Background:** Densify at `resolution-level 0` (full res) is the single most
expensive stage (~17 min, 15.5 M points) and it **cascades**: more dense points
→ larger Delaunay (ReconstructMesh) → more faces for RefineMesh → larger atlas
for Texture/Blend/Seams. Level 1 (¼ the pixels) makes densify ≈ 4× faster *and*
shrinks the entire back half. Crucially, `RefineMesh` is **photometric** — it
recovers geometric detail from the source images largely independent of densify
density — so final detail should be near-unchanged. Since walls are not
recoverable from nadir regardless, res-0 mainly buys roof/ground point density
that RefineMesh re-supplies anyway.

**Delta from baseline (single variable):** `densify-resolution-level 0 → 1`
(this is the `drone-3d` profile's own default; the baseline overrode it to 0).
Everything else identical.

**Measure:**
- Per-stage timings + total (baseline ≈ 50 min) — expect ≈ 30 min, the saving
  compounding through reconstruct / refine / texture / blend.
- Roof brightness std (baseline ≈ 20.0) — expect ≈ unchanged.
- Dense point count, final mesh face count, visual detail on roof/ground —
  expect modest point-count drop, near-identical refined detail.

**Expected:** ~40 % less wall-clock at effectively unchanged final quality —
i.e. res-0 is mostly wasted cost here. If confirmed, `drone-3d`'s default
(level 1) is the right production setting and res-0 is a niche max-density
option.

---

## Notes

- Both are **one-variable** tests vs run `8d2d31de`; do not combine parameters
  in a single run or the attribution is lost. A later **combined** run
  (`densify-level 1` + `close-holes 500`) is worth doing once each is
  understood, as the likely production profile.
- Capture per-stage timings (`task_output.txt`), the mesh stats, and the roof
  crop for each — the same quantities the baseline above records — so the paper
  tables are populated directly.
- These belong to ROADMAP **v0.4.0** (Quality profiles & tuning / Benchmark
  suite); calibrating the `drone-3d` bundle against Experiment B is exactly the
  open "calibrate the bundles per profile against benchmark runs" item.
