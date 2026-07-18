"""Topology Transitions Blender add-on."""

from __future__ import annotations

bl_info = {
    "name": "Topology Transitions",
    "author": "CurioCrafter",
    "version": (0, 6, 1),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > Quad Transition",
    "description": "Build, repair, and inspect guided quad topology flows",
    "category": "Mesh",
    "doc_url": "https://github.com/CurioCrafter/Topology-Transitions",
    "tracker_url": "https://github.com/CurioCrafter/Topology-Transitions/issues",
}


def register() -> None:
    from . import (
        bake_preview_ops,
        examples,
        flow_ops,
        manifold_ops,
        operators,
        properties,
        repair_ops,
        ribbon_ops,
        surface_ops,
        ui,
    )

    properties.register()
    operators.register()
    repair_ops.register()
    manifold_ops.register()
    examples.register()
    flow_ops.register()
    ribbon_ops.register()
    surface_ops.register()
    bake_preview_ops.register()
    ui.register()


def unregister() -> None:
    from . import (
        bake_preview_ops,
        examples,
        flow_ops,
        manifold_ops,
        operators,
        properties,
        repair_ops,
        ribbon_ops,
        surface_ops,
        ui,
    )

    ui.unregister()
    bake_preview_ops.unregister()
    surface_ops.unregister()
    ribbon_ops.unregister()
    flow_ops.unregister()
    examples.unregister()
    manifold_ops.unregister()
    repair_ops.unregister()
    operators.unregister()
    properties.unregister()


if __name__ == "__main__":
    register()
