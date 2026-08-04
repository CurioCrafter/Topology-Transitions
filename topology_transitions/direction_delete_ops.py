"""Delete selected mesh-object faces by dominant world-axis normal."""

from __future__ import annotations

from typing import Any

import bmesh
import bpy
from bpy.props import EnumProperty
from bpy.types import Operator
from mathutils import Vector

AXIS_DIRECTIONS: dict[str, Vector] = {
    "POS_X": Vector((1.0, 0.0, 0.0)),
    "NEG_X": Vector((-1.0, 0.0, 0.0)),
    "POS_Y": Vector((0.0, 1.0, 0.0)),
    "NEG_Y": Vector((0.0, -1.0, 0.0)),
    "POS_Z": Vector((0.0, 0.0, 1.0)),
    "NEG_Z": Vector((0.0, 0.0, -1.0)),
}

AXIS_ITEMS = (
    ("POS_X", "+X", "Delete faces whose world normal points mostly toward +X"),
    ("NEG_X", "-X", "Delete faces whose world normal points mostly toward -X"),
    ("POS_Y", "+Y", "Delete faces whose world normal points mostly toward +Y"),
    ("NEG_Y", "-Y", "Delete faces whose world normal points mostly toward -Y"),
    ("POS_Z", "+Z", "Delete faces whose world normal points mostly toward +Z"),
    ("NEG_Z", "-Z", "Delete faces whose world normal points mostly toward -Z"),
)

AXIS_LABELS = {identifier: label for identifier, label, _description in AXIS_ITEMS}

DOMINANT_EPSILON = 1.0e-6


def _selected_mesh_objects(context: Any) -> list[Any]:
    objects = [
        obj
        for obj in context.selected_objects
        if obj is not None and obj.type == "MESH"
    ]
    if objects:
        return objects
    obj = context.active_object
    return [obj] if obj is not None and obj.type == "MESH" else []


def _normal_faces_axis(world_normal: Vector, axis: str) -> bool:
    if world_normal.length_squared <= 1.0e-16:
        return False
    normal = world_normal.normalized()
    direction = AXIS_DIRECTIONS[axis]
    dot = normal.dot(direction)
    if dot <= DOMINANT_EPSILON:
        return False
    axis_component = max(range(3), key=lambda index: abs(direction[index]))
    dominant = max(abs(normal.x), abs(normal.y), abs(normal.z))
    return abs(normal[axis_component]) + DOMINANT_EPSILON >= dominant


def delete_faces_by_axis(context: Any, axis: str) -> dict[str, int]:
    if context.mode != "OBJECT":
        raise RuntimeError("Directional face delete works in Object Mode")
    objects = _selected_mesh_objects(context)
    if not objects:
        raise RuntimeError("Select at least one mesh object")

    total_faces = 0
    touched_objects = 0
    made_single_user = 0
    for obj in objects:
        if obj.data.users > 1:
            obj.data = obj.data.copy()
            made_single_user += 1
        mesh = obj.data
        normal_matrix = obj.matrix_world.to_3x3().inverted_safe().transposed()
        bm = bmesh.new()
        try:
            bm.from_mesh(mesh)
            bm.faces.ensure_lookup_table()
            bm.normal_update()
            faces = [
                face
                for face in bm.faces
                if _normal_faces_axis(normal_matrix @ face.normal, axis)
            ]
            if not faces:
                continue
            bmesh.ops.delete(bm, geom=faces, context="FACES_ONLY")
            bm.to_mesh(mesh)
            mesh.update()
            total_faces += len(faces)
            touched_objects += 1
        finally:
            bm.free()
    return {
        "objects": len(objects),
        "touched_objects": touched_objects,
        "deleted_faces": total_faces,
        "made_single_user": made_single_user,
    }


class QT_OT_delete_faces_by_axis(Operator):
    bl_idname = "object.quad_transition_delete_faces_by_axis"
    bl_label = "Delete Faces by Axis"
    bl_description = (
        "Delete faces on selected mesh objects whose world normals face the chosen axis"
    )
    bl_options = {"REGISTER", "UNDO"}

    axis: EnumProperty(name="Axis Direction", items=AXIS_ITEMS, default="POS_Y")

    @classmethod
    def poll(cls, context: Any) -> bool:
        return context.mode == "OBJECT" and bool(_selected_mesh_objects(context))

    def execute(self, context: Any):
        try:
            stats = delete_faces_by_axis(context, self.axis)
        except RuntimeError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        label = AXIS_LABELS[self.axis]
        if stats["deleted_faces"] == 0:
            self.report(
                {"WARNING"},
                f"No selected mesh faces were mostly facing {label}",
            )
            return {"CANCELLED"}
        note = (
            f"; made {stats['made_single_user']} linked mesh copy/copies first"
            if stats["made_single_user"]
            else ""
        )
        self.report(
            {"INFO"},
            f"Deleted {stats['deleted_faces']} {label}-facing face(s) "
            f"from {stats['touched_objects']} selected object(s){note}",
        )
        return {"FINISHED"}


CLASSES = (QT_OT_delete_faces_by_axis,)


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
