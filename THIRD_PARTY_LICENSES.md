# Third-party licenses

Effigies' own source code (this repository) is licensed under the **MIT License**
(see [LICENSE](LICENSE)).

Effigies does **not** vendor or statically link any of the projects below. It
orchestrates them as separate programs:

- The pipeline scripts call the COLMAP / OpenMVS / OpenSfM command-line tools.
- The Docker image clones **unmodified** upstream NodeODM at build time and runs
  it as the REST layer.

Because these are independent programs invoked at arm's length, the MIT license
on Effigies' own code is unaffected by their copyleft terms (this is "mere
aggregation" under the GPL/AGPL). However, when you **distribute a build that
bundles them** (e.g. the Docker image), each component's license governs that
component.

| Component      | Role                                   | License      | Upstream |
|----------------|----------------------------------------|--------------|----------|
| NodeODM        | REST layer that drives the engine      | AGPL-3.0     | https://github.com/OpenDroneMap/NodeODM |
| ODM            | Reference engine / asset contract      | AGPL-3.0     | https://github.com/OpenDroneMap/ODM |
| OpenMVS        | Densify / ReconstructMesh / RefineMesh / TextureMesh | AGPL-3.0 | https://github.com/cdcseacave/openMVS |
| COLMAP         | Sparse SfM (default sparse engine)     | BSD-3-Clause | https://github.com/colmap/colmap |
| OpenSfM        | Sparse SfM (optional, aerial sets)     | BSD-2-Clause | https://github.com/mapillary/OpenSfM |
| PDAL           | Point-cloud conversion (planned)       | BSD-3-Clause | https://github.com/PDAL/PDAL |
| NumPy          | Georef solver dependency               | BSD-3-Clause | https://github.com/numpy/numpy |
| Pillow         | EXIF parsing (optional path)           | MIT-CMU / HPND | https://github.com/python-pillow/Pillow |
| pyproj         | CRS reprojection (optional path)       | MIT          | https://github.com/pyproj4/pyproj |

## AGPL-3.0 note

The strongest obligation comes from the AGPL-3.0 components (NodeODM, ODM,
OpenMVS): if you run a **modified** version of them as a network service, you
must offer that modified source to users of the service. Effigies ships them
**unmodified**, so this obligation is satisfied by the upstream repositories
linked above. If you fork and modify any of them inside your own image, you take
on that obligation for your fork.

This file is informational and not legal advice. When in doubt, consult the
upstream license texts directly.
