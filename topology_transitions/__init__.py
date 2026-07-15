"""Topology Transitions Blender add-on."""

from __future__ import annotations

bl_info = {
    "name": "Topology Transitions",
    "author": "CurioCrafter",
    "version": (0, 3, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > Quad Transition",
    "description": "Build quad transitions and inspect complete edge-flow strips",
    "category": "Mesh",
    "doc_url": "https://github.com/CurioCrafter/Topology-Transitions",
    "tracker_url": "https://github.com/CurioCrafter/Topology-Transitions/issues",
}


def register() -> None:
    from . import examples, flow_ops, operators, properties, ui

    properties.register()
    operators.register()
    examples.register()
    flow_ops.register()
    ui.register()


def unregister() -> None:
    from . import examples, flow_ops, operators, properties, ui

    ui.unregister()
    flow_ops.unregister()
    examples.unregister()
    operators.unregister()
    properties.unregister()


if __name__ == "__main__":
    register()
