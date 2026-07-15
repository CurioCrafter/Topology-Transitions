"""Ready-made meshes that demonstrate topology transitions."""

from __future__ import annotations

from typing import Any

import bmesh
import bpy
from bpy.types import Operator

EXAMPLE_COLUMNS = 5
EXAMPLE_ROWS = 10
PATCH_FIRST_ROW = 4
PATCH_HEIGHT = 2


def _material(name: str, color: tuple[float, float, float, float]):
    material = bpy.data.materials.get(name)
    if material is None:
        material = bpy.data.materials.new(name)
    material.diffuse_color = color
    return material


def _remove_failed_example(obj: Any) -> None:
    mesh = obj.data
    if obj.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh.users == 0:
        bpy.data.meshes.remove(mesh)


class QT_OT_add_example_plane(Operator):
    bl_idname = "object.quad_transition_add_example_plane"
    bl_label = "Add 5 to 3 Example Plane"
    bl_description = (
        "Create a colored all-quad plane with a ready-made 5 to 3 transition"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: Any) -> bool:
        return context.mode == "OBJECT"

    def execute(self, context: Any):
        for selected in context.selected_objects:
            selected.select_set(False)

        half_width = EXAMPLE_COLUMNS * 0.5
        half_height = EXAMPLE_ROWS * 0.5
        vertices = [
            (float(x) - half_width, float(y) - half_height, 0.0)
            for y in range(EXAMPLE_ROWS + 1)
            for x in range(EXAMPLE_COLUMNS + 1)
        ]
        faces = []
        for y in range(EXAMPLE_ROWS):
            for x in range(EXAMPLE_COLUMNS):
                first = y * (EXAMPLE_COLUMNS + 1) + x
                faces.append(
                    (
                        first,
                        first + 1,
                        first + EXAMPLE_COLUMNS + 2,
                        first + EXAMPLE_COLUMNS + 1,
                    )
                )

        mesh = bpy.data.meshes.new("TopologyTransitions_Example_5to3_Mesh")
        mesh.from_pydata(vertices, [], faces)
        mesh.update()
        obj = bpy.data.objects.new("TopologyTransitions_Example_5to3", mesh)
        context.collection.objects.link(obj)
        obj.location = context.scene.cursor.location
        context.view_layer.objects.active = obj
        obj.select_set(True)
        obj["topology_transitions_example"] = "5 to 3"

        bpy.ops.object.mode_set(mode="EDIT")
        bm = bmesh.from_edit_mesh(mesh)
        bm.faces.ensure_lookup_table()
        bm.select_mode = {"FACE"}
        for face in bm.faces:
            row = face.index // EXAMPLE_COLUMNS
            face.select_set(PATCH_FIRST_ROW <= row < PATCH_FIRST_ROW + PATCH_HEIGHT)
        bm.select_flush_mode()
        target_y = float(PATCH_FIRST_ROW + PATCH_HEIGHT) - half_height
        incoming_edges = [
            edge
            for edge in bm.edges
            if edge.select
            and all(abs(vertex.co.y - target_y) < 1.0e-6 for vertex in edge.verts)
        ]
        if not incoming_edges:
            _remove_failed_example(obj)
            self.report({"ERROR"}, "Could not prepare the example transition patch")
            return {"CANCELLED"}
        bm.select_history.clear()
        bm.select_history.add(incoming_edges[len(incoming_edges) // 2])
        bmesh.update_edit_mesh(mesh, loop_triangles=True, destructive=False)

        result = bpy.ops.mesh.quad_transition_apply(
            transition="FIVE_TO_THREE",
            pole_side="CENTER",
            relax_strength=0.55,
            relax_iterations=24,
            conform_surface=True,
        )
        if result != {"FINISHED"}:
            _remove_failed_example(obj)
            self.report({"ERROR"}, "Could not build the 5 to 3 example transition")
            return {"CANCELLED"}
        bpy.ops.object.mode_set(mode="OBJECT")

        materials = (
            _material("TT Example Blue", (0.17, 0.58, 0.82, 1.0)),
            _material("TT Example Purple", (0.48, 0.31, 0.82, 1.0)),
            _material("TT Example Red", (0.9, 0.08, 0.18, 1.0)),
        )
        for material in materials:
            obj.data.materials.append(material)
        for polygon in obj.data.polygons:
            center_y = (
                sum(obj.data.vertices[index].co.y for index in polygon.vertices) / 4
            )
            if center_y > 1.5:
                polygon.material_index = 0
            elif center_y > -1.5:
                polygon.material_index = 1
            else:
                polygon.material_index = 2

        settings = context.scene.topology_transitions
        settings.flow_index = 0
        settings.flow_count = 0
        settings.flow_object_name = ""

        self.report(
            {"INFO"},
            "Added a 5 to 3 example plane; enter Edit Mode to inspect its flows",
        )
        return {"FINISHED"}


CLASSES = (QT_OT_add_example_plane,)


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
