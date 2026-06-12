# Plan — Blend streaming refactor (ROADMAP v0.5.0 precondition)

Goal: make `helpers/texture_blend.py` memory-bounded **independent of the image
count**, so large sets (and, later, per-tile texturing under split-merge) do not
OOM in our own texture-quality stage. Target: peak RSS governed by mesh + atlas
size only, with at most one source image resident at a time.

`helpers/seam_level.py` is **out of scope** by inspection: it reads the atlas
pages (`tex_paths`, bounded by `texture-resolution`) and the mesh (V/VT/FV,
bounded by reconstruction detail) — it never touches the source images, so it
has no image-count scaling. Its only growth axis is mesh size; a sanity check at
high face counts is enough (see Phase 3).

## The three image-count-scaling consumers in `blend()` (measured from the code)

Grounded in the current code and the last real run (8 M faces refined mesh,
12 MP images, 4 atlas pages). The walls, worst first:

1. **`Wt = np.zeros((nF, nV))` in `select_views`** (texture_blend.py:107) — a
   **dense [faces × views] weight matrix**, even though each face keeps only the
   top-4 views. **O(nF·nV)**: 8 M faces × 900 views × 4 B = **~29 GB**. The
   biggest and least obvious offender; almost all of it is zeros.
2. **`imgs = [...]` all undistorted images in RAM** (texture_blend.py:199) —
   **O(nV·pixels)**: 900 × (12 MP × 3 B) = **~32 GB**. The one we already
   flagged.
3. **`depths = [...]` all depth maps held at once** (texture_blend.py:187) for
   `select_views`. **O(nV·pixels/16)** (1/4-res): 900 × ~0.77 MB ≈ **0.7 GB**.
   Modest, but held simultaneously and trivially avoidable.

Everything else (per-page `acc`/`wgt`, the bbox-class rasteriser) is bounded by
atlas size, not image count, and stays as is.

## Phase 0 — Instrument & baseline *(half-day, do first)*

- Add an optional peak-RSS probe to the blend log (`resource.getrusage`
  `ru_maxrss` at entry/exit, gated on an env flag) so the model is *measured*,
  not assumed. This doubles as the v0.5.0 memory-ceiling instrument.
- Capture a baseline on a reduced-resolution, high-count run (e.g. 300 images
  @ ~1600 px) to confirm the per-consumer slopes before changing anything.
- Add `tests/test_blend.py` if absent: a tiny synthetic scene (2–3 PINHOLE
  views, a few faces, a small atlas) whose blended output is the **golden
  reference** all later phases must reproduce within tolerance.

## Phase 1 — Streaming top-K view selection *(kills walls #1 and #3)*

Replace the dense `Wt` matrix with a **running top-K per face** and fold the
depth-map rendering into the same single pass:

- Keep two `[nF, K]` arrays: `top_idx` (int32, init -1) and `top_w` (float32,
  init 0). Memory **O(nF·K)** — 8 M × 4 × 8 B ≈ 256 MB, *flat in view count*.
- Loop views once. For each view `vi`: render its depth map **on the fly**, use
  it for the visibility test, compute the per-face weight vector `w[nF]` exactly
  as now (cos²/d², frame margin, depth visibility), then **discard the depth
  map**. No `depths` list.
- Streaming insertion (vectorised): `cmin = top_w.min(1); amin = top_w.argmin(1);
  repl = w > cmin; top_w[repl, amin[repl]] = w[repl]; top_idx[...] = vi`. Each
  face retains its 4 strongest views regardless of arrival order.
- Final normalisation unchanged: `WEIGHT_FLOOR` relative to the best, renormalise
  to sum 1.

Result is the **same top-4 set and weights** as the matrix path (top-K is
order-independent), so Phase 1 must reproduce the golden reference bit-for-bit.
Slightly more arithmetic per view than one big `argsort`, but bounded and likely
a wash (the old `argsort` over `nV` was itself O(nF·nV·log nV)).

## Phase 2 — View-major bake *(kills wall #2; the bulk of the work)*

Restructure the per-page bake (texture_blend.py:203–288) from
"all-images-resident, texel-major" into **rasterise → view-major sample**:

- **Rasterise pass (per page):** run the existing bbox-size-class rasteriser, but
  instead of sampling immediately, emit a compact per-page texel table: for each
  covered texel its atlas pixel index, its 3D position `P`, and its face's top-K
  `(vid, weight)`. Bounded by **page size** (≤ atlas texels), not image count.
- **Sample pass (per page):** group texel→view references by `vid`, then
  `for vid in views_used_on_this_page:` load that image, sample all texels that
  reference it (the existing project-and-`bilinear` step), accumulate into the
  page `acc`/`wgt`, **discard the image**. One image resident at a time.

Memory: **O(atlas page + one image)**. I/O cost: each image read at most once
per page (≤ 4× for a 4-page atlas) — the RAM-for-I/O trade; an optional small
LRU across pages can recover it later if profiling says it matters.

Numerical note: view-major changes the float accumulation order, so the weighted
mean differs by rounding only. Phase 2's test asserts **near-equality**
(e.g. ≤ 1 LSB / atol 1) against the golden reference, not bit-equality.

## Phase 3 — Validate, wire, confirm

- `test_blend.py`: golden-reference equality (Phase 1 exact, Phase 2 atol) +
  a **memory-ceiling assertion** — peak RSS on a synthetic N-view scene must not
  grow with N (run at N and 4N, assert flat).
- Reduced-res large-count run (300–600 @ ~1600 px): confirm flat blend RSS and
  unchanged roof quality (brightness std) vs the baseline.
- Seam sanity: confirm `seam_level.py` peak is bounded by atlas + mesh at a high
  face count (no code change expected).
- No behavioural change otherwise: skip conditions, coverage %, log lines, output
  files all identical.

## Effort & sequencing

Phase 1 is the high-value, self-contained win (one function, exact test). Phase 2
is the larger restructure (the bake), gated on Phase 1's selection output. Roughly
a focused day end-to-end. Lands **before** any large-set or tiling work — it is
the v0.5.0 precondition, not a follow-up.
