#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Patrick Leiverkus
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared OpenMVS textured-mesh output naming.

OpenMVS names the mesh by stage: ReconstructMesh writes ``scene_dense_mesh``,
RefineMesh adds ``_refine``, TextureMesh appends ``_texture``. Two helpers need
to locate that OBJ: ``georef_bridge.py`` (which rewrites its vertices in place,
offset-subtracted) and ``map_outputs.py`` (which renames it into the WebODM asset
path). They MUST agree on the exact same ordered candidate list — if it diverges
in only one of them (e.g. a future OpenMVS rename added to the mapper but not the
bridge), the bridge georeferences one OBJ while the mapper ships a different,
un-georeferenced one, and the divergence is silent. Keep the list here, once.
"""
import os

# Preference order: textured before untextured, refined before unrefined — i.e.
# the highest-quality mesh that the enabled pipeline stages actually produced.
MESH_OBJ_CANDIDATES = (
    "scene_dense_mesh_refine_texture.obj",
    "scene_dense_mesh_texture.obj",
    "scene_dense_mesh_refine.obj",
    "scene_dense_mesh.obj",
)


def find_mesh_obj(work):
    """Return the basename of the first existing OpenMVS mesh OBJ under ``work``,
    or ``None`` if none is present (mesh stages disabled or failed)."""
    for name in MESH_OBJ_CANDIDATES:
        if os.path.exists(os.path.join(work, name)):
            return name
    return None
