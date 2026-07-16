"""3D View sidebar UI for Topology Transitions."""

from __future__ import annotations

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
        for name in ("relax_strength", "relax_iterations", "conform_surface"):
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
    def poll(cls, _context):
        return True

    def draw(self, context):
        layout = self.layout
        settings = context.scene.topology_transitions

        example = layout.box()
        example.label(text="True Density Examples")
        example.operator(
            "object.quad_transition_add_example_plane",
            text="Add All Transition Examples",
            icon="MESH_GRID",
        )
        if context.mode != "OBJECT":
            example.label(text="Switch to Object Mode to add it", icon="INFO")

        repair = layout.box()
        repair.label(text="Topology Repair")
        repair.label(text="Select non-quad faces in Edit Mode")
        repair.operator(
            "mesh.quad_transition_solve_selected_tris",
            text="Solve Selected Tris",
            icon="FACESEL",
        )
        repair.operator(
            "mesh.quad_transition_solve_selected_ngons",
            text="Solve Selected N-gons",
            icon="FACESEL",
        )
        repair.label(text="Local pairs are preferred automatically", icon="INFO")
        repair.label(text="Isolated faces carry splits through quad rings")

        integrity = layout.box()
        integrity.label(text="Mesh Integrity")
        integrity.operator(
            "mesh.quad_transition_check_manifold",
            text="Check & Select Manifold Issues",
            icon="SHADING_WIRE",
        )
        row = integrity.row(align=True)
        previous_issue = row.operator(
            "mesh.quad_transition_manifold_step",
            text="Previous Issue",
            icon="TRIA_LEFT",
        )
        previous_issue.direction = -1
        next_issue = row.operator(
            "mesh.quad_transition_manifold_step",
            text="Next Issue",
            icon="TRIA_RIGHT",
        )
        next_issue.direction = 1
        if settings.manifold_object_name:
            integrity.label(
                text=f"{settings.manifold_component_count} areas, "
                f"{settings.manifold_issue_count} issue elements"
            )
            integrity.label(
                text=f"Open {settings.manifold_open_edge_count} | "
                f"Over-connected {settings.manifold_nonmanifold_edge_count}"
            )
            integrity.label(text=settings.manifold_current_kind)

        selection = layout.box()
        selection.label(text="1. Select faces or a closed boundary loop")
        selection.label(text="Interior faces may be quads, tris, or n-gons")
        selection.label(text="Boundary needs four sides and the required width")
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

        flow = layout.box()
        flow.label(text="Quad Flow Scroll")
        flow.label(text="Browse whole pole-bounded quad regions")
        flow.prop(settings, "flow_mode", text="")
        row = flow.row(align=True)
        row.prop(settings, "flow_scope", text="")
        row.prop(settings, "flow_min_edges")
        flow.prop(settings, "flow_sort", text="")
        row = flow.row(align=True)
        row.prop(settings, "flow_focus_view")
        if settings.flow_mode == "REGIONS":
            row.prop(settings, "flow_show_full_map")
        else:
            row.prop(settings, "flow_show_neighbors")

        row = flow.row(align=True)
        previous = row.operator(
            "mesh.quad_transition_edge_flow_step", text="Previous", icon="TRIA_LEFT"
        )
        previous.direction = -1
        previous.select_current = True
        refresh = row.operator("mesh.quad_transition_edge_flow_step", text="Refresh")
        refresh.direction = 0
        refresh.select_current = False
        following = row.operator(
            "mesh.quad_transition_edge_flow_step", text="Next", icon="TRIA_RIGHT"
        )
        following.direction = 1
        following.select_current = True
        flow.operator(
            "mesh.quad_transition_edge_flow_scroll",
            text="Start Quad Flow Inspector",
        )

        if settings.flow_count:
            metrics = flow.column(align=True)
            noun = "Region" if settings.flow_mode == "REGIONS" else "Band"
            metrics.label(
                text=f"{noun} {settings.flow_index + 1} / "
                f"{settings.flow_count} | {settings.flow_quad_count} quads"
            )
            metrics.label(text=f"Boundary/flow length {settings.flow_length:.3f}")
            metrics.label(
                text=f"{settings.flow_start_label} -> {settings.flow_end_label}"
            )
            metrics.label(text=f"{settings.flow_neighbor_count} adjacent flows")

        note = layout.column(align=True)
        note.label(text="Boundary vertices are always pinned.")
        note.label(text="Transition output remains all-quad.")


CLASSES = (QT_PT_sidebar,)


def register() -> None:
    from bpy.utils import register_class

    for cls in CLASSES:
        register_class(cls)


def unregister() -> None:
    from bpy.utils import unregister_class

    for cls in reversed(CLASSES):
        unregister_class(cls)
