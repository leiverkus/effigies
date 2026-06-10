# Contributing to Effigies

## Branch model
- `main` — released / stable
- `develop` — integration branch; branch your work from here
- feature branches: `feat/<short-name>`, fixes: `fix/<short-name>`

## Commits
Conventional commits: `type(scope): description`
Examples:
- `feat(georef): add EXIF-GPS fallback`
- `fix(openmvs): correct RefineMesh resolution-level passthrough`
- `docs(readme): document georeference modes`

## Before opening a PR
Run the local checks (same as CI):
```bash
./scripts/test.sh
```
All must pass. If you touch the pipeline, also do a real run against a small
dataset and confirm the WebODM output paths are populated.

## Hard rules
See `CLAUDE.md` → "Non-negotiable constraints". In short: keep the NodeODM
contract intact, never silently drop ReconstructMesh/RefineMesh, always support
`--georeference none`, pin container dependencies, and never commit data or
secrets (see `.gitignore`).

## Scope
Effigies is a focused photogrammetry node. Aerial/GPS mapping is already served
by the stock ODM node — contributions should target the close-range / refine
quality gap, not duplicate ODM.
