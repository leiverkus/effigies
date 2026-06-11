# Benchmark literature — prior art for the v0.4.0 engine comparison

Grounding for the ROADMAP **v0.4.0** item ("Benchmark suite comparing Effigies
output against stock ODM / Metashape / RealityCapture"). This note records how
the existing comparison literature measures photogrammetry pipelines, so
[`scripts/benchmark.sh`](../scripts/benchmark.sh) reports the *same* quantities
and the result is publishable rather than merely internal. BibTeX in
[`references.bib`](references.bib).

## What the literature actually measures

Across the close-range / cultural-heritage comparison studies, the recurring,
comparable metrics are:

- **Control/check-point error** — RMSE on GCPs (used to scale/register) and on
  independent check points (CPs). The standard accuracy figure. *Cutugno2022*,
  *GabaraSawicki2023*.
- **Image residuals** (reprojection error, px) of the bundle adjustment.
  *Cutugno2022* report 0.770 px (MicMac) vs. 0.735 px (Metashape).
- **Cloud-to-cloud / mesh-to-reference distance** — deviation of the dense cloud
  or mesh against a ground truth (TLS/structured-light or an averaged-merged
  cloud), computed in CloudCompare. *RahamanChampion2019*, *Qureshi2022*,
  *Cutugno2022*.
- **Completeness** and **noise** — qualitative + quantitative coverage and
  surface-noise assessment. *SolemNau2020* (RC more detailed but noisier than
  Metashape), *Croce2024*.
- **Processing time** — wall-clock per pipeline. *SolemNau2020* (RC ≫ faster
  than Metashape).

`benchmark.sh` currently covers the **runtime** axis (per stage) and **output
geometry** (vertices, faces, surface area, density, texture megapixels; point
count + density). The two metrics it does **not** yet compute, and which the
literature treats as the accuracy core, are:

1. **GCP/CP RMSE** — needs marked control points; already a v0.3.0 georef item.
2. **Cloud-to-cloud / mesh-to-reference distance** — needs a reference scan and
   a registration step (PDAL `filters.icp` + a distance filter, or CloudCompare
   `-c2c`). This is the natural next addition to `benchmark.sh`.

## Findings relevant to Effigies' expected positioning

- **Open-source reaches comparable geometric accuracy** to Metashape on good
  input — MicMac vs. Metashape came out "comparable" on both residuals and 3D
  control-point error (*Cutugno2022*); FOSS gave "promising, significant
  accuracy" for heritage visualisation (*RahamanChampion2019*).
- **Commercial leads on speed and on the detail/noise frontier.**
  RealityCapture is markedly faster than Metashape and produces more detailed
  meshes, but with more surface noise in places; accuracy between them was
  inconclusive (*SolemNau2020*). This matches the speed analysis in
  [DEPLOYMENT.md](DEPLOYMENT.md).
- **Metric scale is the open-stack weak point.** SfM models without scale
  information are "not fully suitable for archiving" (*Barszcz2021*) — i.e. the
  georeferencing / GCP maturity gap, tracked as ROADMAP v0.3.0.

## The gap Effigies can fill

The open side of these comparisons is almost always **MicMac, GRAPHOS, or
AliceVision/Meshroom** (*Griwodz2021*). **No located study benchmarks the exact
Effigies stack — COLMAP + OpenMVS with the `ReconstructMesh`/`RefineMesh`
photometric refinement — head-to-head against Metashape/RealityCapture.** That
refinement step is the node's *raison d'être* and is largely absent from the
comparison literature. A careful Effigies-vs-commercial benchmark on a shared
close-range dataset is therefore a genuine (publishable) contribution, not just
an internal sanity check.

## Suggested benchmark protocol (from the literature)

1. Shared dataset with surveyed control points (or a TLS/structured-light
   reference) — cf. the *CRBeDaSet* design (*GabaraSawicki2023*).
2. Run each engine; record per-stage runtime and the geometry stats
   (`benchmark.sh`).
3. Register each output to the reference and report **C2C/mesh distance**
   (mean, std, RMS) — the metric to add to `benchmark.sh` next.
4. Report **CP RMSE** once GCP support lands (v0.3.0).
5. Report mesh **detail vs. noise** (triangle density + local roughness), the
   axis where RefineMesh is expected to differentiate.
