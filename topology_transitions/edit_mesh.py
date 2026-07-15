"""Shared Edit Mode BMesh transaction helpers."""

from __future__ import annotations

from typing import Any

import bmesh
import bpy


def restore_bmesh(mesh: Any, bm: Any, backup: Any) -> None:
    """Restore an Edit Mode BMesh from a copy without leaving Edit Mode."""

    temporary = bpy.data.meshes.new("__topology_transition_restore__")
    try:
        backup.to_mesh(temporary)
        bm.clear()
        bm.from_mesh(temporary)
        bmesh.update_edit_mesh(mesh, loop_triangles=True, destructive=True)
    finally:
        bpy.data.meshes.remove(temporary)
