"""Topology Transitions Blender add-on."""

from __future__ import annotations

bl_info = {
    "name": "Topology Transitions",
    "author": "CurioCrafter",
    "version": (0, 4, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > Quad Transition",
    "description": "Build, repair, and inspect guided quad topology flows",
    "category": "Mesh",
    "doc_url": "https://github.com/CurioCrafter/Topology-Transitions",
    "tracker_url": "https://github.com/CurioCrafter/Topology-Transitions/issues",
}


def register() -> None:
    from . import examples, flow_ops, operators, properties, repair_ops, ui

    properties.register()
    operators.register()
    repair_ops.register()
    examples.register()
    flow_ops.register()
    ui.register()


def unregister() -> None:
    from . import examples, flow_ops, operators, properties, repair_ops, ui

    ui.unregister()
    flow_ops.unregister()
    examples.unregister()
    repair_ops.unregister()
    operators.unregister()
    properties.unregister()


if __name__ == "__main__":
    register()
