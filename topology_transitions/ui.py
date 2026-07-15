"""3D View sidebar UI for Topology Transitions."""

from __future__ import annotations

import bpy
from bpy.types import Panel


def _copy_operator_settings(
    operator, settings, *, include_geometry: bool = True
) -> None:
    for name in (
        "transition",
        "axis_mode",
        "flip_flow",
        "pole_side",
        "mirror",
        "pole_spacing",
    ):
        setattr(operator, name, getattr(settings, name))
    if include_geometry:
        for name in (
            "relax_strength",
            "relax_iterations",
            "conform_surface",
        ):
            setattr(operator, name, getattr(settings, name))
        operator.projection_target_name = (
            settings.projection_target.name if settings.projection_target else ""
        )


class QT_PT_sidebar(Panel):
    bl_label = "Topology Transitions"
    bl_idname = "QT_PT_sidebar"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Quad Transition"

    @classmethod
    def poll(cls, context):
        return (
            context.active_object is not None and context.active_object.type == "MESH"
        )

    def draw(self, context):
        layout = self.layout
        settings = context.scene.topology_transitions

        selection = layout.box()
        selection.label(text="1. Select a rectangular quad patch")
        selection.label(text="Width must equal the larger loop count")
        selection.label(text="Make an incoming boundary edge active if needed")

        pattern = layout.box()
        pattern.label(text="2. Choose the transition")
        pattern.prop(settings, "transition")
        row = pattern.row(align=True)
        row.prop(settings, "axis_mode", text="")
        row.prop(settings, "flip_flow", text="Reverse")
        row = pattern.row(align=True)
        row.prop(settings, "pole_side", text="Pole")
        row.prop(settings, "mirror")
        pattern.prop(settings, "pole_spacing")

        fit = layout.box()
        fit.label(text="3. Fit the replacement")
        fit.prop(settings, "relax_strength")
        fit.prop(settings, "relax_iterations")
        fit.prop(settings, "conform_surface")
        if settings.conform_surface:
            fit.prop(settings, "projection_target")

        row = layout.row(align=True)
        validate = row.operator(
            "mesh.quad_transition_validate", text="Validate", icon="CHECKMARK"
        )
        _copy_operator_settings(validate, settings, include_geometry=False)
        apply = row.operator(
            "mesh.quad_transition_apply", text="Apply Transition", icon="MESH_GRID"
        )
        _copy_operator_settings(apply, settings)

        preview = layout.box()
        preview.label(text="Subdivision Preview")
        preview.prop(settings, "subdivision_levels")
        toggle = preview.operator(
            "object.quad_transition_toggle_subdivision",
            text="Toggle Catmull-Clark",
            icon="MOD_SUBSURF",
        )
        toggle.levels = settings.subdivision_levels

        note = layout.column(align=True)
        note.label(text="Boundary vertices are always pinned.")
        note.label(text="Triangles and n-gons are never inserted.")


CLASSES = (QT_PT_sidebar,)


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
