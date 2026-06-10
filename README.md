# Effigies

> *effigies* (lat.) — „das plastische Abbild, die geformte Nachbildung".
> Der Node formt aus flachen Bildern wieder Körper: die dichte, photometrisch
> verfeinerte Oberfläche, die ODM ausspart.

Ein **NodeODM-kompatibler Processing Node**, dessen Engine die Lücke schließt,
wegen der WebODMs 3D-Rekonstruktion hinter kommerziellen Tools zurückbleibt:
die von ODM ausgelassenen OpenMVS-Schritte **ReconstructMesh** und **RefineMesh**.

```
WebODM ──HTTP──> NodeODM REST layer ──run.sh──> [ Effigies engine ]
                                                  │
   COLMAP (sparse, robust close-range)            │
        └─ InterfaceCOLMAP ─> scene.mvs           │
   OpenMVS                                         │
        ├─ DensifyPointCloud                       │
        ├─ ReconstructMesh   ← ODM überspringt das │
        ├─ RefineMesh ×N     ← Haupt-Qualitätshebel│
        └─ TextureMesh                             │
   georef_bridge.py  (lokales SfM-Frame -> UTM)    │
   map_outputs.py    (-> WebODM-Asset-Struktur)    ┘
```

## Warum das funktioniert, ohne WebODM anzufassen

WebODM spricht nie mit einem Photogrammetrie-Binary. Es spricht über die
NodeODM-REST-API mit einem *Processing Node*. Eine „Engine" muss nur drei
Verträge erfüllen, die dieser Node liefert:

1. **Engine-Aufruf** — NodeODM ruft `run.sh` im `ODM_PATH` auf und reicht alle
   Optionen als `--name value` durch. `ENGINE` meldet den Namen (`effigies`).
2. **Options-Advertising** — `helpers/optionsToJson.py` liefert `options.json`;
   WebODM baut daraus automatisch das Task-Options-UI (inkl. `refine-mesh-iters`,
   `number-views-fuse`, `crs`, …).
3. **Output-Vertrag** — `map_outputs.py` schreibt die Ergebnisse in die von
   WebODM erwarteten Pfade (`odm_texturing/`, `odm_georeferencing/`, Punktwolke).

## Quickstart

```bash
./scripts/setup.sh            # build the Docker image (effigies:dev)
docker run -p 3001:3000 --gpus all effigies:dev
# then in WebODM: Processing Nodes -> Add -> http://<host>:3001
```

Run the test suite (no Docker / GPU needed):
```bash
./scripts/test.sh
```

## Repository layout

```
ENGINE                 engine name reported to WebODM
options.json           task options advertised to the WebODM UI
run.sh                 entry point: parses args, drives the pipeline
pipeline/              COLMAP / OpenSfM sparse + OpenMVS dense stages
helpers/               georef bridge, output mapping, options shim
tests/                 unit tests (synthetic COLMAP fixtures)
scripts/               setup.sh (build), test.sh (CI mirror)
Dockerfile             COLMAP + OpenMVS + NodeODM REST layer
CLAUDE.md              project context + hard constraints for Claude Code
```

## Integration

```bash
docker build -t effigies .
docker run -p 3001:3000 --gpus all effigies
```
Dann in WebODM unter **Processing Nodes** `http://<host>:3001` hinzufügen.
Der Node erscheint neben ODM/MicMac/LGT, mit eigenem Optionssatz.

## Dateien

| Datei | Zweck |
|---|---|
| `ENGINE` | Engine-Name für `getEngine()` |
| `options.json` | An WebODM gemeldete Parameter |
| `run.sh` | Entry-Point, parst Args, orchestriert die Kette |
| `pipeline/sparse_colmap.sh` | COLMAP feature/match/mapper |
| `pipeline/sparse_opensfm.sh` | Alternative Sparse-Stufe (optional) |
| `pipeline/dense_openmvs.sh` | Densify → ReconstructMesh → RefineMesh → TextureMesh |
| `helpers/georef_bridge.py` | Helmert/Umeyama-Georeferenzierung |
| `helpers/map_outputs.py` | Mapping auf WebODM-Asset-Pfade |
| `helpers/optionsToJson.py` | Options-Shim |

## Georeferencing (`--georeference`)

Implemented in `helpers/georef_bridge.py`:

- **`auto`** (default): use a GCP file if present (project-root `gcp_list.txt` is
  auto-detected, ODM convention), else fall back to EXIF-GPS, else keep a
  metrically-scaled local frame.
- **`gcp`**: require `gcp_list.txt`. World coords come from the file; each GCP's
  local position is recovered from COLMAP by matching its marked pixel to the
  nearest observed sparse point. Needs >=3 localizable GCPs.
- **`exif`**: pair COLMAP camera centers with EXIF-GPS reprojected into the target
  CRS (UTM auto-derived if `crs=auto`). Needs >=3 well-distributed fixes; collinear
  flight lines degrade the solve. Requires `Pillow` + `pyproj`.
- **`none`**: skip georeferencing, keep the local object-centric frame.
  **Recommended for turntable / close-range finds** — the model stays metrically
  consistent, only absolute world placement is omitted.

The solve is a Umeyama 3D similarity (scale + rotation + translation). The textured
OBJ is rewritten in place with an offset subtracted (offset stored in
`georef_transform.json`) so large projected coordinates stay within float precision.

## Offene Punkte vor Produktiveinsatz (ehrlich)

Dies ist ein **lauffähiges Gerüst**, kein fertiges Produkt. Was noch echte
Verdrahtung/Härtung braucht:

- **GCP-Lokalisierung** nutzt den nächstgelegenen beobachteten Sparse-Punkt zum
  markierten Pixel. Das ist robust, wenn der Marker auf rekonstruierter Oberfläche
  sitzt; für Subpixel-Genauigkeit wäre echte Mehrbild-Triangulation der markierten
  Pixel besser.
- **EXIF-Pfad** ignoriert Linsenverzerrung bei der Pixel-Projektion (für
  Kamerazentren-Korrespondenz irrelevant) und braucht `Pillow`+`pyproj` im Image.
- **OpenMVS/COLMAP aus Source bauen** (siehe Dockerfile-Hinweis): Distro-Pakete
  sind oft veraltet; `ReconstructMesh`/`RefineMesh` müssen vorhanden sein.
- **Punktwolke nach LAZ**: `map_outputs.py` reicht `.ply` durch; für Potree/EPT
  mit PDAL nach `.laz` konvertieren.
- **RefineMesh-Parameter** für Fundobjekt vs. Architektur kalibrieren.

## Empfehlung

Für close-range Fundobjekte/Architektur: COLMAP-Sparse + Refine ×3 als Start.
Für GPS-getaggte Luftbilder weiter den Standard-ODM-Node nutzen — beide Nodes
laufen in WebODM parallel.
