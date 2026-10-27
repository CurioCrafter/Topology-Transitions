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


def _draw_mesh_integrity(layout, settings, *, edit_mode: bool) -> None:
    integrity = layout.box()
    integrity.label(text="Mesh Integrity - Start Here")
    integrity.label(text="Find holes and topological manifold defects")
    integrity.operator(
        "mesh.quad_transition_check_manifold",
        text="Find Holes / Check Manifold",
        icon="SHADING_WIRE",
    )
    if not edit_mode:
        integrity.label(text="Enter Edit Mode to inspect", icon="INFO")
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
        integrity.label(
            text=f"Winding {settings.manifold_winding_edge_count} | "
            f"Vertex fans {settings.manifold_disconnected_fan_count}"
        )
        integrity.label(
            text=f"Wire {settings.manifold_wire_edge_count} | "
            f"Isolated {settings.manifold_isolated_vertex_count}"
        )
        integrity.label(text=settings.manifold_current_kind)

    integrity.separator()
    integrity.label(text="Optional repair - changes topology")
    integrity.operator(
        "mesh.quad_transition_solve_all_nonmanifold",
        text="Auto Solve All Non-Manifold Areas",
        icon="MOD_REMESH",
    )
    integrity.label(text="Even holes use the quickest quad fan")
    integrity.label(text="Triangular holes become three quad flows")


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

        _draw_mesh_integrity(
            layout,
            settings,
            edit_mode=context.mode == "EDIT_MESH",
        )

        surface = layout.box()
        surface.label(text="Surface Conform & Bake Preview")
        surface.prop(settings, "shrinkwrap_method", text="")
        surface.prop(settings, "shrinkwrap_offset")
        shrinkwrap = surface.operator(
            "object.quad_transition_setup_shrinkwrap",
            text="Set Up Live Shrinkwrap",
            icon="MOD_SHRINKWRAP",
        )
        shrinkwrap.target_name = (
            settings.surface_target.name if settings.surface_target else ""
        )
        shrinkwrap.wrap_method = settings.shrinkwrap_method
        shrinkwrap.offset = settings.shrinkwrap_offset
        shrinkwrap.project_limit = settings.draw_project_limit

        surface.separator()
        surface.label(text="Select high source(s); keep low mesh active")
        row = surface.row(align=True)
        row.prop(settings, "bake_type", text="")
        row.prop(settings, "bake_margin")
        surface.prop(settings, "bake_use_cage")
        if settings.bake_use_cage:
            surface.prop(settings, "bake_cage_distance")
            row = surface.row(align=True)
            cage = row.operator(
                "object.quad_transition_toggle_bake_cage",
                text="Toggle Bake Cage",
                icon="SHADING_WIRE",
            )
            cage.distance = settings.bake_cage_distance
            rebuild = row.operator(
                "object.quad_transition_toggle_bake_cage",
                text="Rebuild",
                icon="FILE_REFRESH",
            )
            rebuild.distance = settings.bake_cage_distance
            rebuild.force_rebuild = True
        else:
            surface.prop(settings, "bake_max_ray_distance")
        surface.prop(settings, "bake_ray_samples")
        row = surface.row(align=True)
        rays = row.operator(
            "object.quad_transition_toggle_bake_rays",
            text="Toggle Ray Preview",
            icon="HIDE_OFF",
        )
        rays.max_ray_distance = (
            settings.bake_cage_distance
            if settings.bake_use_cage
            else settings.bake_max_ray_distance
        )
        rays.use_cage = settings.bake_use_cage
        rays.sample_limit = settings.bake_ray_samples
        refresh_rays = row.operator(
            "object.quad_transition_toggle_bake_rays",
            text="Refresh",
            icon="FILE_REFRESH",
        )
        refresh_rays.max_ray_distance = rays.max_ray_distance
        refresh_rays.use_cage = settings.bake_use_cage
        refresh_rays.sample_limit = settings.bake_ray_samples
        refresh_rays.force_rebuild = True
        row = surface.row(align=True)
        inspect = row.operator(
            "object.quad_transition_inspect_bake",
            text="Inspect Readiness",
            icon="VIEWZOOM",
        )
        inspect.use_cage = settings.bake_use_cage
        configure = row.operator(
            "object.quad_transition_configure_bake",
            text="Configure Bake",
            icon="RENDER_STILL",
        )
        configure.bake_type = settings.bake_type
        configure.margin = settings.bake_margin
        configure.max_ray_distance = settings.bake_max_ray_distance
        configure.use_cage = settings.bake_use_cage
        bake_status = context.scene.get(
            "topology_transitions_bake_status", "Bake readiness not inspected"
        )
        surface.label(text=str(bake_status), icon="INFO")
        surface.label(text="Green = sampled hit; red = miss (diagnostic)")
        surface.label(text="Configuration never starts or overwrites a bake")

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

        optimize = layout.box()
        optimize.label(text="Quad Flow Optimization")
        optimize.label(text="Remove repetitive remesher edge loops")
        optimize.prop(settings, "optimize_sensitivity", text="Detection")
        optimize.prop(settings, "optimize_scope", text="")
        optimize.prop(settings, "optimize_cycles")
        preset_summary = {
            "AUTO": "Auto tries Conservative, Balanced, then Aggressive",
            "CONSERVATIVE": "8 deg bend | 2.5% error | boundaries locked",
            "BALANCED": "18 deg bend | 5% error | open flows allowed",
            "AGGRESSIVE": "35 deg bend | 10% error | short flows allowed",
        }
        if settings.optimize_sensitivity == "CUSTOM":
            row = optimize.row(align=True)
            row.prop(settings, "optimize_branch_guard_rings")
            row.prop(settings, "optimize_min_loop_edges")
            row = optimize.row(align=True)
            row.prop(settings, "optimize_max_face_angle")
            row.prop(settings, "optimize_feature_angle")
            optimize.prop(settings, "optimize_max_error_percent")
            optimize.prop(settings, "optimize_preserve_boundaries")
        else:
            optimize.label(
                text=preset_summary[settings.optimize_sensitivity],
                icon="INFO",
            )
        grid = optimize.grid_flow(row_major=True, columns=2, align=True)
        grid.prop(settings, "optimize_preserve_seams")
        grid.prop(settings, "optimize_preserve_sharp")
        grid.prop(settings, "optimize_preserve_creases")
        grid.prop(settings, "optimize_preserve_materials")
        row = optimize.row(align=True)
        row.operator(
            "mesh.quad_transition_analyze_optimization",
            text="Analyze / Select Flows",
            icon="VIEWZOOM",
        )
        row.operator(
            "mesh.quad_transition_optimize_flows",
            text=(
                "Auto Optimize"
                if settings.optimize_sensitivity == "AUTO"
                else "Optimize Quad Flow"
            ),
            icon="MOD_DECIM",
        )
        optimize.prop(
            settings,
            "optimize_show_help",
            text="Explain Every Control",
            toggle=True,
        )
        if settings.optimize_show_help:
            guide = optimize.box()
            guide.label(text="Detection: how sensitive flow finding is")
            guide.label(text="Scope: whole mesh, or only selected faces")
            guide.label(text="Cycles: repeated density-halving passes")
            guide.label(text="Surface Angle: bend a removed flow may cross")
            guide.label(text="Crease Angle: geometric edge treated as a crease")
            guide.label(text="Surface Error: allowed distance from old shape")
            guide.label(text="Guard Rings: dense rings retained around poles")
            guide.label(text="Min Flow: shortest complete loop considered")
            guide.label(text="Open Boundaries: lock density along open edges")
            guide.label(text="UV Seams: retain texture-island borders")
            guide.label(text="Sharp/Creases: retain shading and weighted edges")
            guide.label(text="Materials: retain borders between materials")
            guide.label(text="Analyze: previews the next removable batch")
        if settings.optimize_object_name:
            optimize.label(
                text=f"{settings.optimize_removed_faces} faces removed | "
                f"{settings.optimize_completed_cycles} cycles"
            )
            optimize.label(
                text=f"{settings.optimize_candidate_loops} candidates | "
                f"error {settings.optimize_max_measured_error:.6g}"
            )
            optimize.label(
                text=f"Mode used: {settings.optimize_effective_mode}",
            )
            optimize.label(text=settings.optimize_status, icon="INFO")
            optimize.label(text=settings.optimize_diagnostic)

        detached = layout.box()
        detached.label(text="Detached Face X-Ray")
        detached.label(text="Edit Mode: checks islands inside one mesh")
        detached.prop(settings, "loose_xray_opacity", slider=True)
        inspect_detached = detached.operator(
            "mesh.quad_transition_inspect_detached_faces",
            text="Inspect / Refresh Red X-Ray",
            icon="HIDE_OFF",
        )
        inspect_detached.opacity = settings.loose_xray_opacity
        row = detached.row(align=True)
        previous_detached = row.operator(
            "mesh.quad_transition_step_detached_faces",
            text="Previous",
            icon="TRIA_LEFT",
        )
        previous_detached.direction = -1
        previous_detached.opacity = settings.loose_xray_opacity
        next_detached = row.operator(
            "mesh.quad_transition_step_detached_faces",
            text="Next",
            icon="TRIA_RIGHT",
        )
        next_detached.direction = 1
        next_detached.opacity = settings.loose_xray_opacity
        detached.operator(
            "mesh.quad_transition_clear_detached_xray",
            text="Clear X-Ray",
            icon="X",
        )
        if settings.loose_xray_object_name:
            detached.label(
                text=f"{settings.loose_xray_component_count} detached regions | "
                f"{settings.loose_xray_face_count} faces"
            )
        if context.mode != "EDIT_MESH":
            detached.label(text="Enter Edit Mode to inspect", icon="INFO")

        merge = layout.box()
        merge.label(text="Directional Merge")
        merge.label(text="Edit Mode: select close vertices")
        merge.prop(settings, "directional_merge_distance", slider=True)
        merge.prop(settings, "directional_merge_scope", text="")
        merge.prop(settings, "directional_merge_skip_connected")
        merge.prop(settings, "directional_merge_force_pair")
        merge.prop(settings, "directional_merge_protect_uvs")
        merge.prop(settings, "directional_merge_strategy", text="")
        directional_merge = merge.operator(
            "mesh.quad_transition_directional_merge",
            text="Merge Close Vertices",
            icon="AUTOMERGE_OFF",
        )
        directional_merge.distance = settings.directional_merge_distance
        directional_merge.strategy = settings.directional_merge_strategy
        directional_merge.scope = settings.directional_merge_scope
        directional_merge.skip_connected = (
            settings.directional_merge_skip_connected
        )
        directional_merge.force_selected_pair = settings.directional_merge_force_pair
        directional_merge.protect_uvs = settings.directional_merge_protect_uvs
        row = merge.row(align=True)
        keep_outer = row.operator(
            "mesh.quad_transition_directional_merge",
            text="Keep Outer",
            icon="TRIA_UP",
        )
        keep_outer.distance = settings.directional_merge_distance
        keep_outer.strategy = "RADIAL_OUTER"
        keep_outer.scope = settings.directional_merge_scope
        keep_outer.skip_connected = settings.directional_merge_skip_connected
        keep_outer.force_selected_pair = settings.directional_merge_force_pair
        keep_outer.protect_uvs = settings.directional_merge_protect_uvs
        keep_inner = row.operator(
            "mesh.quad_transition_directional_merge",
            text="Keep Inner",
            icon="TRIA_DOWN",
        )
        keep_inner.distance = settings.directional_merge_distance
        keep_inner.strategy = "RADIAL_INNER"
        keep_inner.scope = settings.directional_merge_scope
        keep_inner.skip_connected = settings.directional_merge_skip_connected
        keep_inner.force_selected_pair = settings.directional_merge_force_pair
        keep_inner.protect_uvs = settings.directional_merge_protect_uvs
        row = merge.row(align=True)
        keep_toward = row.operator(
            "mesh.quad_transition_directional_merge",
            text="Toward View",
            icon="VIEW_CAMERA",
        )
        keep_toward.distance = settings.directional_merge_distance
        keep_toward.strategy = "VIEW_TOWARD"
        keep_toward.scope = settings.directional_merge_scope
        keep_toward.skip_connected = settings.directional_merge_skip_connected
        keep_toward.force_selected_pair = settings.directional_merge_force_pair
        keep_toward.protect_uvs = settings.directional_merge_protect_uvs
        keep_away = row.operator(
            "mesh.quad_transition_directional_merge",
            text="Away From View",
            icon="VIEW_CAMERA",
        )
        keep_away.distance = settings.directional_merge_distance
        keep_away.strategy = "VIEW_AWAY"
        keep_away.scope = settings.directional_merge_scope
        keep_away.skip_connected = settings.directional_merge_skip_connected
        keep_away.force_selected_pair = settings.directional_merge_force_pair
        keep_away.protect_uvs = settings.directional_merge_protect_uvs
        if context.mode != "EDIT_MESH":
            merge.label(text="Enter Edit Mode to merge", icon="INFO")

        directional = layout.box()
        directional.label(text="Directional Face Delete")
        directional.label(text="Object Mode: selected mesh objects")
        row = directional.row(align=True)
        for axis, text in (("POS_X", "+X"), ("NEG_X", "-X"), ("POS_Y", "+Y")):
            operator = row.operator(
                "object.quad_transition_delete_faces_by_axis",
                text=text,
                icon="FACESEL",
            )
            operator.axis = axis
        row = directional.row(align=True)
        for axis, text in (("NEG_Y", "-Y"), ("POS_Z", "+Z"), ("NEG_Z", "-Z")):
            operator = row.operator(
                "object.quad_transition_delete_faces_by_axis",
                text=text,
                icon="FACESEL",
            )
            operator.axis = axis
        if context.mode != "OBJECT":
            directional.label(
                text="Switch to Object Mode to delete by axis", icon="INFO"
            )

        connector = layout.box()
        connector.label(text="Target Mesh Connector")
        connector.label(text="Select contact faces on the active mesh")
        connector.label(text="Object Mode: select it plus one target")
        connector.prop(settings, "target_connect_distance", slider=True)
        connector.prop(settings, "target_connect_opacity", slider=True)
        row = connector.row(align=True)
        preview_connection = row.operator(
            "object.quad_transition_preview_target_connection",
            text="Preview Red",
            icon="HIDE_OFF",
        )
        preview_connection.distance = settings.target_connect_distance
        preview_connection.opacity = settings.target_connect_opacity
        row.operator(
            "object.quad_transition_clear_target_connection_preview",
            text="Clear",
            icon="X",
        )
        build_connection = connector.operator(
            "object.quad_transition_build_target_connection",
            text="Build Triangulated Connection",
            icon="MOD_TRIANGULATE",
        )
        build_connection.distance = settings.target_connect_distance
        if context.mode != "OBJECT":
            connector.label(text="Return to Object Mode to preview/build", icon="INFO")

        selection = layout.box()
        selection.label(text="1. Select one quad, a patch, or its boundary")
        selection.label(text="One quad places only the transition core")
        selection.label(text="Larger patches use the true four-sided boundary")
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

        paint_shell = layout.box()
        header = paint_shell.row()
        header.prop(
            settings,
            "show_surface_paint",
            text="Surface Paint Quads",
            icon="TRIA_DOWN" if settings.show_surface_paint else "TRIA_RIGHT",
            emboss=False,
        )
        if settings.show_surface_paint:
            paint = paint_shell.column(align=True)
            paint.label(text="Paint where quads should go;", icon="BRUSH_DATA")
            paint.label(text="stroke direction = edge loops.")
            paint.prop(settings, "surface_target", text="Target")
            row = paint.row(align=True)
            row.prop(settings, "paint_cell_size", text="Cell")
            row.prop(settings, "paint_brush_pixels", text="Brush")
            row = paint.row(align=True)
            row.prop(settings, "paint_flow_angle", text="Flow")
            row.prop(settings, "paint_relax")
            paint.prop(settings, "paint_weld")
            operator = paint.operator(
                "mesh.quad_transition_surface_paint",
                text="Start Surface Paint",
                icon="BRUSH_DATA",
            )
            operator.target_name = (
                settings.surface_target.name if settings.surface_target else ""
            )
            operator.cell_size = settings.paint_cell_size
            operator.brush_pixels = settings.paint_brush_pixels
            operator.flow_angle = settings.paint_flow_angle
            operator.relax_iterations = settings.paint_relax
            operator.weld = settings.paint_weld
            paint.label(text="Ctrl erase | Enter build | Esc", icon="INFO")

        note = layout.column(align=True)
        note.label(text="One-quad core placement leaves a remesh gap.")
        note.label(text="Larger patch boundary vertices are pinned.")
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
