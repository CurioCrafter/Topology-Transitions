"""Topology Transitions Blender add-on."""

from __future__ import annotations

bl_info = {
    "name": "Topology Transitions",
    "author": "CurioCrafter",
    "version": (0, 1, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > Quad Transition",
    "description": "Rebuild rectangular quad patches with guided edge-loop transitions",
    "category": "Mesh",
    "doc_url": "https://github.com/CurioCrafter/Topology-Transitions",
    "tracker_url": "https://github.com/CurioCrafter/Topology-Transitions/issues",
}


def register() -> None:
    from . import operators, properties, ui

    properties.register()
    operators.register()
    ui.register()


def unregister() -> None:
    from . import operators, properties, ui

    ui.unregister()
    operators.unregister()
    properties.unregister()


if __name__ == "__main__":
    register()
