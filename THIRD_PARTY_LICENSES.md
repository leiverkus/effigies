# Third-party licenses

Effigies' own source code (this repository) is licensed under the **GNU Affero
General Public License v3.0 or later (AGPL-3.0-or-later)** (see
[LICENSE](LICENSE)).

Effigies does **not** vendor or statically link any of the projects below. It
orchestrates them as separate programs:

- The pipeline scripts call the COLMAP / OpenMVS / OpenSfM command-line tools.
  COLMAP and OpenSfM are built from pinned upstream source **unmodified**; OpenMVS
  carries two **build-compatibility** source patches (the libjxl `REQUIRED` flag
  dropped, and `cv::IMWRITE_JPEGXL_QUALITY` mapped to the JPEG constant) so it
  compiles against the image's OpenCV — neither touches reconstruction geometry.
- The Docker image clones **pinned** upstream NodeODM (`NODEODM_REF`) at build time
  and runs it as the REST layer, applying **one documented one-line type-safety
  hotfix** (`s.replace(…)` → `String(s).replace(…)`, working around the upstream
  PR #268 numeric-option crash) — to be dropped once upstream fixes the regression.

Effigies' own AGPL-3.0-or-later license applies to this repository's source
code. The bundled or orchestrated components below keep their own licenses. When
you **distribute a build that bundles them** (e.g. the Docker image), each
component's license governs that component.

| Component      | Role                                   | License      | Upstream |
|----------------|----------------------------------------|--------------|----------|
| NodeODM        | REST layer that drives the engine      | AGPL-3.0     | https://github.com/OpenDroneMap/NodeODM |
| ODM            | Reference engine / asset contract      | AGPL-3.0     | https://github.com/OpenDroneMap/ODM |
| OpenMVS        | Densify / ReconstructMesh / RefineMesh / TextureMesh | AGPL-3.0 | https://github.com/cdcseacave/openMVS |
| Obj2Tiles      | Optional OGC 3D Tiles export           | AGPL-3.0     | https://github.com/OpenDroneMap/Obj2Tiles |
| OpenPointClass | Optional point-cloud classification    | AGPL-3.0     | https://github.com/uav4geo/OpenPointClass |
| VCGlib         | OpenMVS mesh-processing build dependency | GPL-3.0    | https://github.com/cdcseacave/VCG |
| CGAL           | OpenMVS geometry build dependency      | GPL-3.0-or-later / LGPL-3.0-or-later (file-dependent) | https://github.com/CGAL/cgal |
| Entwine        | EPT point-cloud export                 | LGPL-2.1     | https://github.com/OpenDroneMap/entwine |
| COLMAP         | Sparse SfM (default sparse engine)     | BSD-3-Clause | https://github.com/colmap/colmap |
| OpenSfM        | Sparse SfM (optional, aerial sets)     | BSD-2-Clause | https://github.com/mapillary/OpenSfM |
| PDAL           | Point-cloud conversion and raster pipelines | BSD-3-Clause | https://github.com/PDAL/PDAL |
| LightGBM       | OpenPointClass gradient-boosting dependency | MIT      | https://github.com/microsoft/LightGBM |
| py4dgeo        | M3C2 change detection (opt-in `align-to`) | MIT       | https://github.com/3dgeo-heidelberg/py4dgeo |
| NumPy          | Georef solver dependency               | BSD-3-Clause | https://github.com/numpy/numpy |
| Pillow         | EXIF parsing (optional path)           | MIT-CMU / HPND | https://github.com/python-pillow/Pillow |
| pyproj         | CRS reprojection (optional path)       | MIT          | https://github.com/pyproj4/pyproj |

## AGPL-3.0 note

The strongest obligation comes from the AGPL-3.0 components (NodeODM, ODM,
OpenMVS, Obj2Tiles, OpenPointClass): if you run a **modified** version of them
as a network service, you must offer that modified source to the users of the
service.

- **ODM** is shipped unmodified — the upstream repository linked above satisfies
  the offer.
- **NodeODM** carries one documented one-line type-safety hotfix (see above).
  Because it is both the modified component *and* the network-facing service, the
  AGPL source-offer applies — and it is satisfied here: the modification lives in
  this repository (the build step is public, the patch a single reproducible `sed`
  in the Dockerfiles).
- **OpenMVS** carries two build-compatibility source patches (libjxl `REQUIRED`
  dropped; `IMWRITE_JPEGXL_QUALITY` → JPEG). They only let it compile against the
  image's OpenCV and do not change reconstruction behaviour; the patched source is
  likewise public in this repository's Dockerfiles.
- **Obj2Tiles** and **OpenPointClass** are used unmodified as separate command-line
  tools in optional export/classification paths.
- **VCGlib**, **CGAL**, and **Entwine** are GPL/LGPL-family dependencies used by
  the image build. They are compatible with Effigies' AGPL-3.0-or-later licensing,
  but their license notices and corresponding source availability still need to be
  preserved when redistributing an image.

If you fork and further modify any of them inside your own image, you take on that
obligation for your fork.

This file is informational and not legal advice. When in doubt, consult the
upstream license texts directly.
