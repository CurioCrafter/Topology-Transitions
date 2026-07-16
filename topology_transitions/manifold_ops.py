"""Blender operators that select and focus exact manifold issue components."""

from __future__ import annotations

from typing import Any

import bmesh
import bpy
from bpy.props import IntProperty
from bpy.types import Operator

from .manifold import ManifoldReport, analyze_manifold


class ManifoldCheckError(ValueError):
    pass


def _report_from_context(context: Any) -> tuple[Any, Any, Any, ManifoldReport]:
    obj = context.edit_object
    if obj is None or obj.type != "MESH" or obj.mode != "EDIT":
        raise ManifoldCheckError("Manifold Check requires a mesh in Edit Mode")
    if len(context.objects_in_mode_unique_data) != 1:
        raise ManifoldCheckError("Check one mesh data-block at a time")
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.verts.index_update()
    bm.edges.index_update()
    report = analyze_manifold(
        {edge.index: (edge.verts[0].index, edge.verts[1].index) for edge in bm.edges},
        {edge.index: len(edge.link_faces) for edge in bm.edges},
        (vertex.index for vertex in bm.verts),
    )
    return obj, obj.data, bm, report


def _update_settings(settings: Any, obj: Any, report: ManifoldReport) -> None:
    settings.manifold_object_name = obj.name
    settings.manifold_component_count = len(report.components)
    settings.manifold_open_edge_count = len(report.open_boundary_edges)
    settings.manifold_nonmanifold_edge_count = len(report.nonmanifold_edges)
    settings.manifold_wire_edge_count = len(report.wire_edges)
    settings.manifold_isolated_vertex_count = len(report.isolated_vertices)
    settings.manifold_issue_count = len(report.issue_edge_ids) + len(
        report.isolated_vertices
    )


def _select_issue(
    context: Any,
    mesh: Any,
    bm: Any,
    edge_ids: set[int],
    vertex_ids: set[int],
) -> None:
    for face in bm.faces:
        face.select_set(False)
    for edge in bm.edges:
        edge.select_set(False)
    for vertex in bm.verts:
        vertex.select_set(False)
    for edge_id in edge_ids:
        edge = bm.edges[edge_id]
        edge.select_set(True)
        for vertex in edge.verts:
            vertex.select_set(True)
    for vertex_id in vertex_ids:
        bm.verts[vertex_id].select_set(True)
    bm.select_mode = {"VERT", "EDGE"}
    context.tool_settings.mesh_select_mode = (True, True, False)
    bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)


def _focus_selected(context: Any) -> None:
    if context.area is not None and context.area.type == "VIEW_3D":
        try:
            bpy.ops.view3d.view_selected(use_all_regions=False)
        except RuntimeError:
            pass


class QT_OT_check_manifold(Operator):
    bl_idname = "mesh.quad_transition_check_manifold"
    bl_label = "Check Manifold"
    bl_description = "Find and select every exact open or non-manifold mesh element"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: Any) -> bool:
        return (
            context.edit_object is not None
            and context.edit_object.type == "MESH"
            and context.edit_object.mode == "EDIT"
        )

    def execute(self, context: Any):
        try:
            obj, mesh, bm, report = _report_from_context(context)
        except ManifoldCheckError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        settings = context.scene.topology_transitions
        _update_settings(settings, obj, report)
        settings.manifold_component_index = 0
        if report.clean:
            settings.manifold_current_kind = "Manifold: no issues"
            self.report({"INFO"}, "Mesh is manifold: no open or over-connected edges")
            return {"FINISHED"}
        _select_issue(
            context,
            mesh,
            bm,
            set(report.issue_edge_ids),
            set(report.isolated_vertices),
        )
        settings.manifold_current_kind = "All Issues"
        _focus_selected(context)
        self.report(
            {"WARNING"},
            f"Found {len(report.components)} issue areas: "
            f"{len(report.open_boundary_edges)} open, "
            f"{len(report.nonmanifold_edges)} over-connected edges",
        )
        return {"FINISHED"}


class QT_OT_manifold_step(Operator):
    bl_idname = "mesh.quad_transition_manifold_step"
    bl_label = "Step Manifold Issue"
    bl_description = "Select and focus one connected manifold issue area"
    bl_options = {"REGISTER", "UNDO"}

    direction: IntProperty(name="Direction", min=-1, max=1, default=1)

    @classmethod
    def poll(cls, context: Any) -> bool:
        return QT_OT_check_manifold.poll(context)

    def execute(self, context: Any):
        try:
            obj, mesh, bm, report = _report_from_context(context)
        except ManifoldCheckError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        settings = context.scene.topology_transitions
        same_object = settings.manifold_object_name == obj.name
        _update_settings(settings, obj, report)
        if report.clean:
            settings.manifold_current_kind = "Manifold: no issues"
            self.report({"INFO"}, "Mesh is manifold")
            return {"FINISHED"}
        if same_object:
            current = min(settings.manifold_component_index, len(report.components) - 1)
        else:
            current = -1 if self.direction >= 0 else 0
        index = (current + self.direction) % len(report.components)
        component = report.components[index]
        settings.manifold_component_index = index
        settings.manifold_current_kind = component.kind
        _select_issue(
            context,
            mesh,
            bm,
            set(component.edge_ids),
            set(component.vertex_ids),
        )
        _focus_selected(context)
        self.report(
            {"WARNING"},
            f"Issue {index + 1}/{len(report.components)}: {component.kind}, "
            f"{len(component.edge_ids)} edges",
        )
        return {"FINISHED"}


CLASSES = (QT_OT_check_manifold, QT_OT_manifold_step)


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
