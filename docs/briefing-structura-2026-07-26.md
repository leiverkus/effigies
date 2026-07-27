# Briefing — Structura, Stand 26.07.2026

Übergabe aus einer Effigies-Session. Zweck: eine parallele Session kann hier
anfangen, ohne den Effigies-Verlauf zu kennen.

> Dieses Dokument ist eine **Momentaufnahme**, keine Quelle der Wahrheit. Maßgeblich
> sind `code/ROADMAP.md`, `code/docs/architecture.md` und die jeweilige `CLAUDE.md`.
> Wenn etwas hier von denen abweicht, gelten die.

---

## 0. Die GPU-Maschine — HINFÄLLIG seit 28.07.2026

> ## ⚠ Der Zugang zu `arbi-gpu1` endet am 28.07.2026.
>
> Der gesamte folgende Abschnitt war für zwei parallel arbeitende Sessions
> geschrieben und ist damit **überholt**. Er bleibt stehen, weil er die
> Rahmenbedingungen dokumentiert, unter denen die Effigies-Messwerte entstanden
> sind — aber **nichts darin ist mehr handlungsleitend**. Die Maschine wurde
> abgeräumt: Ergebnisverzeichnisse, Workdirs, Docker-Image und Build-Cache.
>
> **Was das für dich konkret heißt:**
>
> - **Der Testdatensatz `~/semtest/proj/` ist dort weg.** Er liegt jetzt auf dem
>   MacBook unter `Documents/Aktuell/effigies-host-archiv/semtest-final/` — Ortho,
>   DSM, LAZ und das Semantikraster, unverändert. Die Eigenschaft, auf die es
>   ankam, gilt weiter: Ortho, DSM und Semantik sind **pixelgleich**.
> - **Ein besserer Datensatz ist dazugekommen.** Unter
>   `effigies-host-archiv/block2-final/` liegt derselbe Tiberias-Block, aber
>   **GCP-georeferenziert in EPSG:6991** (dem CRS, das Contexta für diesen Ort
>   erwartet) mit einer unabhängigen **Check-Point-RMSE von 3,4 cm**, und mit
>   Ortho und DSM bei **0,58 cm/px** statt der 3,17 cm des semtest-Satzes. Wenn du
>   einen realen Eingang für den 2D- oder 2.5D-Track brauchst, nimm diesen.
> - **Abstimmung über Rechenzeit, Plattenplatz und `docker prune` entfällt.**
>   Es gibt keine geteilte Maschine mehr.
> - **Die Warnung zu offenen Ports** (unten) bleibt sinngemäß gültig für jede
>   künftige gemeinsam genutzte Maschine, nicht für diese.
>
> Die Effigies-Seite ist vollständig gesichert: Code und Dokumentation in neun
> Commits auf `github.com/leiverkus/effigies`, Ergebnisse und sämtliche Logs auf
> dem MacBook. Verloren gegangen ist bewusst nur, was reproduzierbar oder bereits
> als Kennzahl dokumentiert ist.

Seit 26.07.2026 arbeiteten **zwei Sessions** auf demselben Rechner. Die folgenden
Punkte vermieden, dass sie sich gegenseitig Arbeit zerstören.

**Zugang.** `ssh arbi-gpu1` (Key-Auth, Eintrag liegt in `~/.ssh/config` mit
`BatchMode`, `ConnectTimeout` und Keepalives). Benutzer `patlei`, hat `sudo` **mit**
Passwort — für alles Root-Level ist Patrick nötig. Verwaltet von Jörg Lehners
(ARBI, Uni Oldenburg).

**Die Maschine ist temporär.** Lehners: „nur temporaer fuer die naechste Zeit" —
bare metal, weil PCI-Passthrough für VMs noch zickt. Alles dort ist auf Abruf; nichts
darauf ist ein Ablageort. Ergebnisse gehören committet, nicht auf die Kiste.

**Hardware:** RTX A4000 (Ampere, sm_86, 16 GB VRAM), i9-13900KS (8 P-Cores /
16 Threads, E-Cores deaktiviert), 125 GB RAM, **eine** 228-GB-SSD, Ubuntu 24.04.4.
Treiber 595.84, unterstützt CUDA 13.2.

### Was Effigies dort belegt — nicht löschen ohne Absprache

| Pfad / Objekt | Größe | was es ist |
|---|---|---|
| `~/effigies/` | 4,5 MB | rsync-Ziel des Effigies-Repos |
| Docker-Image `effigies:gpu` | 12,4 GB | der kanonische Build (CUDA 13.2.1) |
| Docker **Build-Cache** | 62,9 GB (98 Layer) | spart einen Effigies-Neubau — der aber nur ~12 min dauert, s. u. |
| `~/baseline`, `~/expA`–`~/expD` | ~18 GB | die fünf Benchmark-Läufe hinter `docs/planned-experiments.md` |
| `~/cprmse`, `~/semtest` | ~13 GB | Validierungsläufe (Check-Point-RMSE, Semantik-Pfad) |
| `~/data`, `~/gpu-smoke-*` | ~0,7 GB | Smoke-Datensatz und GPU-Sampling-Logs |

> **Korrektur 17:00.** Dieser Absatz stand hier zuerst mit „kostet eine Stunde
> Neubau". Diese Zahl war nie gemessen, sondern von meinem ersten Kaltbau
> geschätzt. Inzwischen liegen drei gemessene Voll-Builds vor: **13, 12 und 11
> Minuten**. Der Cache ist also deutlich weniger kostbar, als ich geschrieben
> hatte — die Empfehlung unten ist entsprechend entschärft.

**`docker builder prune` gern, `docker system prune -a` bitte kurz abstimmen.**
Ersteres kostet im schlimmsten Fall ~12 min Neubau — wenn du die 62,9 GB
brauchst, nimm sie. Letzteres wirft zusätzlich das Image `effigies:gpu` weg;
das ist auch kein Drama, aber sag mir vorher Bescheid, damit ich nicht mitten
in einem Lauf ohne Image dastehe. Platte am 26.07.: **74 von 228 GB frei**.
Billigster Weg zuerst: `docker builder prune` **ohne** `-a` holt nur die
unreferenzierten Layer und lässt die aktiven stehen. Die Ergebnis-Verzeichnisse
erst danach, und die nur nach Rückfrage — die sind im Gegensatz zum Cache nicht
in Minuten reproduzierbar.

### Messungen: die eigentliche Kollisionsgefahr

Der Effigies-Teil dieser Arbeit ist **messend** — Stufenlaufzeiten, eine Rauschgrenze
von ~1,5 %, ein Qualitäts-Kosten-Knick, ein CUDA-A/B. Diese Zahlen gelten nur, weil
die Maschine dabei ruhig war; es wurde zweimal absichtlich gewartet statt parallel
gerechnet.

**Wenn eine Session rechnet, sind Laufzeitmessungen der anderen ungültig.** Bei
rechenintensiven Läufen also kurz abstimmen. Gemessene Größenordnungen: ein
Effigies-Vollbau **11–13 min** (drei Messungen), ein Benchmark-Lauf 20–60 min, ein
Semantik-Testlauf über 400 Bilder ~1 h 45. Der Build ist dabei der harmloseste
Posten — die langen Läufe sind das, was sich lohnt abzustimmen.

**GPU-Konkurrenz betrifft Structura direkt.** Der 2D-Track hat `sam` (samgeo) und
`cellpose` als wählbare Backends — beide GPU-Inferenz. Der Default `classical` ist
GPU-frei. Bei 16 GB VRAM ist Platz für einiges, aber nicht für gleichzeitiges
OpenMVS-Densify und SAM. Messbar relevant ist ohnehin eher die **CPU**: der
Effigies-Workload ist zu ~90 % CPU-gebunden, zwei Stufen davon rein seriell.

**Was NICHT kollidiert:** Effigies' Ergebnisse sind alle committet und gepusht. Es
gibt keine unveröffentlichte Messung, die verloren gehen könnte.

### Was für dich brauchbar ist: ein echter Effigies-Output liegt schon da

`~/semtest/proj/` ist ein vollständiger, ODM-vertragskonformer Ergebnissatz aus
einem realen Drohnenblock (Tiberias 2023, 400 Bilder) — also genau das, was
Structura als Eingang erwartet, statt synthetischer Raster:

| Datei | | |
|---|---|---|
| `odm_orthophoto/odm_orthophoto.tif` | 12 MB, 4 Bänder | 2D-Track-Eingang |
| `odm_dem/dsm.tif` | 11 MB, 1 Band | 2.5D-Track-Eingang |
| `odm_georeferencing/odm_georeferenced_model.laz` | 71 MB | klassifizierte dichte Wolke |
| `effigies/odm_semantic/orthophoto_semantic.tif` | 106 KB, Byte | das Semantik-Feld aus §4 |
| `effigies/odm_semantic/orthophoto_semantic.legend.json` | | Klassencodes → Name + RGB |

Alle drei Raster sind **pixelgleich**: 3116 × 2659, **EPSG:6991**, GSD 3,17 cm,
identische Geotransformation. Das Semantik-Raster überlagert Ortho und DSM also
ohne Resampling — wenn du die v0.8-Brücke ausprobieren willst, ist genau hier der
Testfall, an dem sie sich zeigen muss.

Erwartungsdämpfer: das Feld hat **drei Klassen** (`1 ground`, `2 vegetation`,
`3 structure`, `0` = nodata) aus dem generischen OpenPointClass-Modell — die
domänenfremden Klassen aus §4. Als Maske („vektorisiere nur, wo `3`") taugt es,
als Befundklassifikation nicht.

Die Dateien sind **root-owned** (Container-Ausgaben), aber lesbar. Zum Arbeiten
kopieren, nicht in place ändern — sie sind das Beweisstück hinter den
dokumentierten Messwerten.

### Netz: keine Ports veröffentlichen

Die Kiste hängt **direkt am Internet, alle Ports erreichbar**, ohne Firewall davor.
Lehners dazu wörtlich: „Auf Sicherheit der Software-Installation und ggf. Einrichtung
einer Firewall oder so, selbst achten."

Effigies hält das so: nichts lauscht nach außen. Alle Läufe gehen direkt über
`run.sh` in einem Container, NodeODM wurde nie gestartet, das Provisioning-Skript
öffnet keinen Port. Grund: NodeODM hat **keine Authentifizierung** — wer den Port
erreicht, kann Tasks einstellen und Ergebnisse abholen.

Für Structura relevant, sobald ein **PostGIS-Sink** ins Spiel kommt: einen
Postgres-Container dort nicht mit `-p 5432:5432` starten. Wenn ein Port gebraucht
wird, an Loopback binden (`-p 127.0.0.1:5432:5432`) und per SSH-Tunnel
(`ssh -L 5432:127.0.0.1:5432 arbi-gpu1`) rangehen. Dasselbe gilt für Jupyter,
MLflow, Vorschau-Server und alles andere mit Web-UI.

---

## 1. Wo du bist

```
Structura/                     ← KEIN Git-Repo, nur Klammer
├── code/    → github.com/leiverkus/structura   (Python-Pipeline)
└── paper/   → GitLab, institutsintern          (Forschungs-Wiki + Manuskript)
```

**Zuerst klären, in welchem Teilprojekt die Aufgabe liegt.** `paper/` hat eine
eigene `CLAUDE.md` mit Vorrang (research-superpowers-Workflow). Git-Operationen
immer im Unterordner, nie an der Wurzel.

Für Code-Arbeit:

```bash
cd code
python -m venv .venv && source .venv/bin/activate
pip install -e ".[geo,dev]"
structura run --dry-run
ruff check . && mypy src && pytest      # vor jedem Commit
```

Python ≥ 3.11. Lizenz: AGPL (seit `26bd163`).

---

## 2. Stand der Codebase

| | |
|---|---|
| Version | **v0.4.1** (Tags v0.1.0 … v0.4.1) |
| Commits | 22, alle Juni 2026 |
| Umfang | 1.372 Zeilen `src/` (26 Dateien), 706 Zeilen `tests/` (15 Dateien) |
| CI | `.github/workflows/ci.yml`, grün |
| Working Tree | **eine uncommittete Änderung** — siehe §6 |

**Was end-to-end läuft:**

- **2D-Track** — Orthophoto → geschlossene Polygone (Steine, Flächen). Drei
  wählbare Backends: `classical` (Otsu-Marker-Watershed, Default, GPU-frei),
  `sam` (via samgeo), `cellpose` (Cellpose-SAM v4). Auswahl über
  `STRUCTURA_2D_BACKEND` / `make_segmenter`.
- **2.5D-Track** — DEM → Polylinien. Selbst implementierte Relief-Ableitungen
  (`dem/relief.py`: hillshade, slope, curvature, local relief model) speisen
  `WallTracer` (multiskalige Relief-Grate → `WALL`, mit Lückenüberbrückung für
  unterbrochene Mauern) und `EdgeTracer` (Hangdiskontinuitäten → `EDGE`).
- **Ausgabe heute**: `FileSink` → `./data/output/features.gpkg`, direkt in QGIS
  öffenbar. Plus Geometriemetriken in `structura.metrics`
  (Über-/Untersegmentierungsrate, a/b-Achsenfehler).

**Was Stub ist:** Profil-Track, Temporal-Track, `PostGISSink`, `DjangoApiSink`.

---

## 3. Meilensteine

| | Status |
|---|---|
| v0.1.0 Scaffolding | ✅ |
| v0.2.0 Walking skeleton | ✅ |
| v0.3.0 2D-Track (Sub-Studie A) | ✅ |
| v0.4.0 2.5D-Track (Sub-Studie B) | ✅ |
| v0.5.0 Persistenz | offen — **hängt an der DB-Entscheidung** |
| v0.6.0 Temporal / 4D (C) | offen |
| v0.7.0 Profil-Track (D) | offen |
| v0.8.0 **Semantik-Feld-Brücke (E)** | offen — **siehe §4, hat sich geändert** |
| v0.9.0 Evaluations-Harness | ⛔ braucht echte Grabungsdaten |
| v1.0.0 Produktion | ⛔ braucht echte Grabungsdaten |

**Leitprinzip des Projekts** (aus `ROADMAP.md`): *decouple code from the data
blocker*. Fast alles ist auf synthetischen Rastern baubar und in CI testbar; nur
die vergleichende Bewertung und die Qualitätsurteile (H_A–H_C) brauchen einen
echten Schnitt, und die sind in v0.9/v1.0 isoliert. Der Datenengpass blockiert
also das *Urteil*, nicht die *Entwicklung*.

Die Track-Buchstaben A–E verweisen auf die Sub-Studien im Forschungsplan unter
`paper/` — die beiden Repos bleiben so ausgerichtet.

---

## 4. Was sich upstream geändert hat (der Grund für dieses Briefing)

**Effigies liefert seit 26.07.2026 das Semantik-Feld, das v0.8.0 konsumieren will.**

Datei: `odm_semantic/orthophoto_semantic.tif` — Byte-GeoTIFF mit Farbtabelle,
plus `orthophoto_semantic.legend.json`. Eigenschaften, auf die man sich verlassen
kann:

- **Legende `version: "v1-mesh"`** — der Mesh-Pfad. (`v0-geometry` wäre der
  ältere Wolken-Rückfall; beide Formate sind identisch, nur die Herkunft
  unterscheidet sich.)
- **Pixelidentisch** mit `odm_orthophoto/odm_orthophoto.tif` und `odm_dem/dsm.tif`
  — gleiches Gitter, gleiche Geometrie, gleiche Okklusionsentscheidungen. Das ist
  garantiert, weil das Klassenraster aus dem Dreiecks-Puffer *desselben*
  Rasterisierungs-Passes gelesen wird, nicht aus einer zweiten Rasterisierung.
- **Okklusionskorrekt** und erbt die RefineMesh-Geometrie: Klassenkanten liegen
  auf der verfeinerten Oberfläche, nicht auf einer Zellenmehrheit gestreuter Punkte.
- Klassencodes: `0` nodata, `1` ground, `2` vegetation, `3` structure.

**Der Vorbehalt, der die Priorisierung bestimmt.** Das Feld entsteht heute aus
OpenPointClass mit dem eingebackenen Modell `vehicles-vegetation-buildings` —
trainiert für Luftbild- und Stadtszenen. Auf einer Ausgrabung sagt
`ground/vegetation/structure` inhaltlich fast nichts. **Der Mechanismus ist
fertig, der Inhalt nicht.**

Die feinen Materialklassen (Stein / Erde / Keramik / Mörtel) sind laut
Effigies-Roadmap ausdrücklich **Structuras Deliverable**: ein trainiertes
2D-Bildmodell, pro Ansicht gerechnet und über Effigies' Multi-View-Blend
(`texture_blend.py`) auf das Mesh fusioniert. Das dreht die Richtung um — heute
3D→2D, geplant 2D→3D — und nur so sieht die Klassifikation die **vertikalen
Flächen (Profile)**, die ein Nadir-Ortho grundsätzlich verliert.

Nützliche Vorarbeit, die dafür schon existiert: Effigies hat seit 26.07. **ONNX
Runtime im Image** (CPU- und CUDA-Provider) und das Muster für versionierte,
SHA256-gepinnte Modellgewichte in `$EFFIGIES_MODEL_DIR` — beides kam mit
ALIKED/LightGlue herein und ist genau der Auslieferungsweg, den das
Feinklassenmodell laut Roadmap braucht.

---

## 5. Der Vertrag zwischen den Projekten — nicht brechen

- **Feld gegen Objekt.** Effigies besitzt das semantische *Feld* im Geometrieraum
  (Klasse pro Punkt / Vertex / Pixel, multi-view- und mehrepochen-konsistent).
  Structura besitzt die semantischen *Objekte* in Vektor/DB-Raum (Instanzen,
  Topologie, stratigraphische Zuordnung).
- **Laufzeitfluss ist einseitig**: Effigies → Structura. **Effigies liest die
  Datenbank nie.** Der einzige Rückfluss ist das trainierte Modell als
  *Build-Artefakt*.
- **Pflasterung ist keine Feldklasse.** Sie ist dasselbe *Material* wie ein
  einzelner Stein und unterscheidet sich nur in der *Anordnung* — Structura leitet
  sie im Objektlayer ab (eine Konfiguration von Stein-Instanzen). Das ist der
  klarste Testfall für die Grenze: was Anordnung ist, gehört nicht ins Feld.
- **Die Validierung** der archäologischen Nützlichkeit des Semantik-Ortho ist
  Structuras Evaluation, nicht Effigies'.

---

## 6. Was du sofort wissen musst

> **Nachtrag 15:45, beim Commit.** Dieser Abschnitt war beim Schreiben schon
> teilweise überholt — die parallele Session hat schneller gearbeitet als das
> Briefing gealtert ist. Korrigiert statt gelöscht, damit man sieht, was sich
> bewegt hat.

**~~Uncommittete Änderung im Working Tree~~ — erledigt.** Die
Untertitel-Umformulierung in `code/README.md` ist weg, der Working Tree ist
sauber. Aktueller Stand: Branch `fix/sam-checkpoint-none`, HEAD `da1cfff`
(*„SamSegmenter crashed on every run — checkpoint=None"*). Beachte: Structura
arbeitet mit **Feature-Branches und PRs** — nicht mit Effigies'
Direkt-auf-`main`-Konvention. Nicht das eine Muster auf das andere anwenden.

**Von drei offenen Architekturentscheidungen ist die erste entschieden.**
Dokumentiert in `code/docs/architecture.md#open-decisions`:

1. ~~**DB-Senke** — direkt PostGIS oder über die Django-API?~~ → **entschieden in
   `code/docs/adr/0001-*.md`** (Status *Proposed*, via PR #9). Und zwar *keine*
   der beiden angebotenen Optionen: **Datei-Übergabe**. `FileSink` (GeoPackage)
   ist der unterstützte Weg, Contexta importiert per Management-Command über das
   ORM. `PostGISSink` ist **zurückgezogen**, nicht verschoben; `DjangoApiSink`
   bleibt Stub, bis Contexta überhaupt eine API hat (heute: `djangorestframework`
   in den Requirements, aber nicht in `INSTALLED_APPS`, keine Serializer,
   keine Viewsets).
2. **Standard-2D-Modell** — entscheidet die v0.9-Evaluation, also datenblockiert.
3. **Intake-Layout-Heuristik**.

Die ADR-Begründung ist stärker als die Frage, die sie beantwortet: Contexta
erzwingt Invarianten in *Anwendungscode* (`Context.save()` vergibt
`context_number` über `NumberSequence.next()` mit `SELECT … FOR UPDATE`,
`Relation.save()` versöhnt die Gegenkante transaktional) — direktes SQL sieht
davon nichts. Das ist ein Argument, das Effigies' Vertragsseite in §5 spiegelt.

---

## 7. Die Frage, die auf dem Tisch liegt

**Sollte v0.8.0 (Semantik-Feld-Brücke) vor v0.5–v0.7 gezogen werden?**

Dafür:
- Das Feld existiert jetzt real und ist nicht mehr hypothetisch.
- Es macht den bestehenden 2D-Track *zielgerichtet* statt blind — „vektorisiere
  nur, wo das Feld Stein sagt" — und verbessert damit rückwirkend v0.3.
- Die Brücke ist mechanisch testbar (synthetische Raster, CI), also passt sie
  zum Leitprinzip.
- Der ROADMAP sagt selbst: *„Milestone scope and ordering may shift."*

Dagegen:
- Der *Inhalt* des Feldes ist heute domänenfremd (§4). Ein Prior aus
  `ground/vegetation/structure` bringt einem Grabungsplan wenig — man baut die
  Verrohrung, ohne den Nutzen zu sehen.
- v0.5 (Persistenz) blockiert alles, was Objektidentität und Stratigraphie
  braucht — und ohne die entsteht aus Vektoren keine Zeichnung. Das ist der
  eigentliche kritische Pfad zum Produkt.

Meine Einschätzung war: **v0.5 zuerst.** Die Objekt- und Stratenschicht ist der
Engpass zwischen „Vektoren in QGIS" und „automatisierter Befundplan", und v0.8
ohne Feinklassen ist Verrohrung ohne Durchfluss.

> **Nachtrag 15:45 — meine Empfehlung trägt so nicht mehr.** ADR-0001 hat sie
> beim Commit widerlegt, nicht abgeschwächt. Mein Argument war „v0.5 ist der
> kritische Pfad, also v0.5 zuerst". Die ADR zeigt: **v0.5.0 kann in Structura
> allein gar nicht fertig werden.** Der Import-Command liegt in Contexta, und
> Contexta hat heute keinen Landeplatz für kontextlose Geometrie — jedes
> Geometriemodell dort verlangt einen nicht-nullbaren `Context`-Fremdschlüssel,
> während Structura bewusst anonyme Vektoren emittiert (`Feature.stratum is
> None`). Die ADR sagt es selbst: *„Until then v0.5.0 cannot complete regardless
> of sink."*
>
> Damit dreht sich die Abwägung: „v0.5 zuerst" heißt jetzt „warten auf ein
> anderes Repo". Der Teil von v0.5, der in Structura liegt (GeoPackage-Ausgabe),
> ist ohnehin schon implementiert. Das macht die Zeit bis zum Contexta-Landeplatz
> zu genau dem Fenster, in dem v0.8 sinnvoll gezogen werden kann — der Einwand
> gegen v0.8 („Verrohrung ohne Durchfluss") bleibt richtig, ist aber kein
> Ausschlussgrund mehr, wenn die Alternative Warten ist.
>
> Zusätzlich hängt jetzt eine CRS-Frage im Raum, die vorher nicht sichtbar war:
> Structura erbt den CRS des Quellrasters (Fixtures: EPSG:32636), Contexta pinnt
> global auf `settings.SRID` = 6991, `Site.srid` ist aber pro Site (Tall Ziraʿa:
> 28191). Die ADR schiebt das korrekt zu Contexta und hält fest, dass `FileSink`
> **nicht** reprojizieren soll. Für Effigies ist das dieselbe Frage wie in
> `contexta-legacy-crs` — die Zielprojektion ist pro Site, nicht global.

Die Entscheidung liegt beim Projekt; ich liefere die Abwägung, nicht das Urteil.

---

## 8. Einstiegspunkte

| | |
|---|---|
| Architektur + offene Entscheidungen | `code/docs/architecture.md` |
| Meilensteine, Status | `code/ROADMAP.md` |
| Konventionen, Stack | `code/README.md`, Wurzel-`CLAUDE.md` |
| Forschungsplan, Sub-Studien A–E | `paper/` (eigene `CLAUDE.md`, Vorrang!) |
| Upstream-Vertrag im Detail | Effigies `code/ROADMAP.md`, Abschnitt v0.7.0 |
| Das neue Semantik-Feld | Effigies `code/helpers/semantic_ortho.py` |
| Die geteilte Maschine | §0 · `ssh arbi-gpu1` · Effigies `code/docs/DEPLOYMENT.md` |
| Echter Testdatensatz dort | `~/semtest/proj/` (Ortho + DSM + LAZ + Semantik, EPSG:6991) |
