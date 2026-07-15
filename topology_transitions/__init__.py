"""Topology Transitions Blender add-on."""

from __future__ import annotations

bl_info = {
    "name": "Topology Transitions",
    "author": "CurioCrafter",
    "version": (0, 2, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > Quad Transition",
    "description": "Rebuild rectangular quad patches with guided edge-loop transitions",
    "category": "Mesh",
    "doc_url": "https://github.com/CurioCrafter/Topology-Transitions",
    "tracker_url": "https://github.com/CurioCrafter/Topology-Transitions/issues",
}


def register() -> None:
    from . import flow_ops, operators, properties, ui

    properties.register()
    operators.register()
    flow_ops.register()
    ui.register()


def unregister() -> None:
    from . import flow_ops, operators, properties, ui

    ui.unregister()
    flow_ops.unregister()
    operators.unregister()
    properties.unregister()


if __name__ == "__main__":
    register()
