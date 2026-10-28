"""Topology Transitions Blender add-on."""

from __future__ import annotations

bl_info = {
    "name": "Topology Transitions",
    "author": "CurioCrafter",
    "version": (0, 11, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > Quad Transition",
    "description": "Find manifold issues, paint and repair guided quad topology",
    "category": "Mesh",
    "doc_url": "https://github.com/CurioCrafter/Topology-Transitions",
    "tracker_url": "https://github.com/CurioCrafter/Topology-Transitions/issues",
}


def register() -> None:
    from . import (
        bake_preview_ops,
        direction_delete_ops,
        directional_merge_ops,
        examples,
        flow_ops,
        lens_ops,
        loose_xray_ops,
        manifold_ops,
        operators,
        optimize_ops,
        properties,
        repair_ops,
        surface_ops,
        surface_paint_ops,
        target_connect_ops,
        ui,
    )

    properties.register()
    operators.register()
    repair_ops.register()
    optimize_ops.register()
    manifold_ops.register()
    loose_xray_ops.register()
    examples.register()
    flow_ops.register()
    lens_ops.register()
    surface_ops.register()
    surface_paint_ops.register()
    bake_preview_ops.register()
    direction_delete_ops.register()
    directional_merge_ops.register()
    target_connect_ops.register()
    ui.register()


def unregister() -> None:
    from . import (
        bake_preview_ops,
        direction_delete_ops,
        directional_merge_ops,
        examples,
        flow_ops,
        lens_ops,
        loose_xray_ops,
        manifold_ops,
        operators,
        optimize_ops,
        properties,
        repair_ops,
        surface_ops,
        surface_paint_ops,
        target_connect_ops,
        ui,
    )

    ui.unregister()
    directional_merge_ops.unregister()
    loose_xray_ops.unregister()
    target_connect_ops.unregister()
    direction_delete_ops.unregister()
    bake_preview_ops.unregister()
    surface_paint_ops.unregister()
    surface_ops.unregister()
    lens_ops.unregister()
    flow_ops.unregister()
    examples.unregister()
    manifold_ops.unregister()
    optimize_ops.unregister()
    repair_ops.unregister()
    operators.unregister()
    properties.unregister()


if __name__ == "__main__":
    register()
