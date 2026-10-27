"""X-ray overlay and navigation for detached face islands in one mesh."""

from __future__ import annotations

from typing import Any

import bmesh
import bpy
from bpy.props import FloatProperty, IntProperty
from bpy.types import Operator

from .loose_components import detached_face_components

OVERLAY_NAME = "Topology Transition Detached Face X-Ray"
MATERIAL_NAME = "Topology Transition Detached Red"
ACTIVE_MATERIAL_NAME = "Topology Transition Detached Active Red"


class DetachedFaceError(ValueError):
    pass


def _edit_mesh(context: Any) -> tuple[Any, Any]:
    obj = context.edit_object
    if obj is None or obj.type != "MESH" or obj.mode != "EDIT":
        raise DetachedFaceError("Detached Face X-Ray requires one mesh in Edit Mode")
    if len(context.objects_in_mode_unique_data) != 1:
        raise DetachedFaceError("Inspect one mesh data-block at a time")
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.verts.index_update()
    bm.faces.index_update()
    return obj, bm


def _components(bm: Any) -> tuple[tuple[int, ...], ...]:
    return detached_face_components(
        {face.index: tuple(vertex.index for vertex in face.verts) for face in bm.faces}
    )


def _remove_overlay() -> bool:
    overlay = bpy.data.objects.get(OVERLAY_NAME)
    if overlay is None:
        return False
    mesh = overlay.data
    bpy.data.objects.remove(overlay, do_unlink=True)
    if mesh.users == 0:
        bpy.data.meshes.remove(mesh)
    return True


def _red_material(name: str, opacity: float, brightness: float) -> Any:
    material = bpy.data.materials.get(name)
    if material is None:
        material = bpy.data.materials.new(name)
    color = (brightness, 0.0, 0.0, opacity)
    material.diffuse_color = color
    material.use_nodes = True
    principled = material.node_tree.nodes.get("Principled BSDF")
    if principled is not None:
        principled.inputs["Base Color"].default_value = (*color[:3], 1.0)
        principled.inputs["Alpha"].default_value = opacity
        principled.inputs["Roughness"].default_value = 0.45
    if hasattr(material, "surface_render_method"):
        material.surface_render_method = "DITHERED"
    return material


def _build_overlay(
    context: Any,
    obj: Any,
    bm: Any,
    components: tuple[tuple[int, ...], ...],
    current_index: int,
    opacity: float,
) -> Any:
    _remove_overlay()
    component_by_face = {
        face_id: component_index
        for component_index, component in enumerate(components)
        for face_id in component
    }
    error_faces = [bm.faces[index] for index in sorted(component_by_face)]
    used_vertices = sorted(
        {vertex.index for face in error_faces for vertex in face.verts}
    )
    remap = {old: new for new, old in enumerate(used_vertices)}
    bm.verts.ensure_lookup_table()
    vertices = [bm.verts[index].co.copy() for index in used_vertices]
    faces = [
        tuple(remap[vertex.index] for vertex in face.verts) for face in error_faces
    ]
    mesh = bpy.data.meshes.new(f"{OVERLAY_NAME} Mesh")
    mesh.from_pydata(vertices, (), faces)
    idle = _red_material(MATERIAL_NAME, opacity, 0.75)
    active = _red_material(ACTIVE_MATERIAL_NAME, min(0.95, opacity + 0.25), 1.0)
    mesh.materials.append(idle)
    mesh.materials.append(active)
    for polygon, source_face in zip(mesh.polygons, error_faces):
        polygon.material_index = int(
            component_by_face[source_face.index] == current_index
        )
    overlay = bpy.data.objects.new(OVERLAY_NAME, mesh)
    context.collection.objects.link(overlay)
    overlay.matrix_world = obj.matrix_world.copy()
    overlay.color = (1.0, 0.0, 0.0, opacity)
    overlay.show_in_front = True
    if hasattr(overlay, "show_transparent"):
        overlay.show_transparent = True
    overlay.hide_select = True
    overlay["topology_transitions_xray"] = True
    overlay["source_object"] = obj.name
    return overlay


def _select_component(context: Any, obj: Any, bm: Any, face_ids: set[int]) -> None:
    for face in bm.faces:
        face.select_set(face.index in face_ids)
    bm.select_mode = {"FACE"}
    context.tool_settings.mesh_select_mode = (False, False, True)
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    if context.area is not None and context.area.type == "VIEW_3D":
        try:
            bpy.ops.view3d.view_selected(use_all_regions=False)
        except RuntimeError:
            pass


def _update_state(
    settings: Any,
    obj: Any,
    components: tuple[tuple[int, ...], ...],
    index: int,
) -> None:
    settings.loose_xray_object_name = obj.name
    settings.loose_xray_component_count = len(components)
    settings.loose_xray_face_count = sum(len(component) for component in components)
    settings.loose_xray_component_index = index if components else 0


def inspect_detached_faces(
    context: Any, *, direction: int = 0, opacity: float = 0.35
) -> dict[str, int]:
    obj, bm = _edit_mesh(context)
    components = _components(bm)
    settings = context.scene.topology_transitions
    if not components:
        _remove_overlay()
        _update_state(settings, obj, components, 0)
        return {"components": 0, "faces": 0, "current_faces": 0}

    same_object = settings.loose_xray_object_name == obj.name
    previous = settings.loose_xray_component_index if same_object else 0
    index = (min(previous, len(components) - 1) + direction) % len(components)
    _update_state(settings, obj, components, index)
    _select_component(context, obj, bm, set(components[index]))
    _build_overlay(context, obj, bm, components, index, opacity)
    return {
        "components": len(components),
        "faces": sum(len(component) for component in components),
        "current_faces": len(components[index]),
    }


class QT_OT_inspect_detached_faces(Operator):
    bl_idname = "mesh.quad_transition_inspect_detached_faces"
    bl_label = "Inspect Detached Faces"
    bl_description = (
        "Show face islands not edge-connected to the mesh's largest region in red"
    )
    bl_options = {"REGISTER", "UNDO"}

    opacity: FloatProperty(name="Red X-Ray Opacity", min=0.05, max=0.9, default=0.35)

    @classmethod
    def poll(cls, context: Any) -> bool:
        return context.edit_object is not None and context.edit_object.type == "MESH"

    def execute(self, context: Any):
        try:
            stats = inspect_detached_faces(context, opacity=self.opacity)
        except DetachedFaceError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        if stats["components"] == 0:
            self.report({"INFO"}, "No detached face islands remain")
        else:
            self.report(
                {"WARNING"},
                f"Found {stats['components']} detached region(s), "
                f"{stats['faces']} face(s); showing region 1",
            )
        return {"FINISHED"}


class QT_OT_step_detached_faces(Operator):
    bl_idname = "mesh.quad_transition_step_detached_faces"
    bl_label = "Step Detached Face Region"
    bl_description = "Select, highlight, and frame one detached face region"
    bl_options = {"REGISTER", "UNDO"}

    direction: IntProperty(name="Direction", min=-1, max=1, default=1)
    opacity: FloatProperty(name="Red X-Ray Opacity", min=0.05, max=0.9, default=0.35)

    @classmethod
    def poll(cls, context: Any) -> bool:
        return QT_OT_inspect_detached_faces.poll(context)

    def execute(self, context: Any):
        try:
            stats = inspect_detached_faces(
                context, direction=self.direction, opacity=self.opacity
            )
        except DetachedFaceError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        settings = context.scene.topology_transitions
        if stats["components"] == 0:
            self.report({"INFO"}, "No detached face islands remain")
        else:
            self.report(
                {"WARNING"},
                f"Detached region {settings.loose_xray_component_index + 1}/"
                f"{stats['components']}: {stats['current_faces']} face(s)",
            )
        return {"FINISHED"}


class QT_OT_clear_detached_xray(Operator):
    bl_idname = "mesh.quad_transition_clear_detached_xray"
    bl_label = "Clear Detached X-Ray"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, _context: Any):
        _remove_overlay()
        return {"FINISHED"}


CLASSES = (
    QT_OT_inspect_detached_faces,
    QT_OT_step_detached_faces,
    QT_OT_clear_detached_xray,
)


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    _remove_overlay()
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
