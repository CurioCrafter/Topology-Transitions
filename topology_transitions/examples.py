"""Ready-made atlas containing every supported topology transition."""

from __future__ import annotations

from typing import Any

import bmesh
import bpy
from bpy.types import Operator

from .core import preset_counts, transition_items

EXAMPLE_ROWS = 8
PATCH_FIRST_ROW = 3
PATCH_HEIGHT = 2
ATLAS_COLUMNS = 4
TILE_SPACING_X = 7.5
TILE_SPACING_Y = 10.0


def _material(name: str, color: tuple[float, float, float, float]):
    material = bpy.data.materials.get(name)
    if material is None:
        material = bpy.data.materials.new(name)
    material.diffuse_color = color
    return material


def _remove_objects(objects: list[Any]) -> None:
    for obj in objects:
        if obj.name not in bpy.data.objects:
            continue
        object_type = obj.type
        data = obj.data if object_type in {"MESH", "FONT"} else None
        bpy.data.objects.remove(obj, do_unlink=True)
        if data is not None and data.users == 0:
            if object_type == "MESH":
                bpy.data.meshes.remove(data)
            else:
                bpy.data.curves.remove(data)


def _create_grid_object(context: Any, preset: str, label: str, location: Any):
    for selected in context.selected_objects:
        selected.select_set(False)
    incoming, outgoing = preset_counts(preset)
    columns = max(incoming, outgoing)
    half_width = columns * 0.5
    half_height = EXAMPLE_ROWS * 0.5
    vertices = [
        (float(x) - half_width, float(y) - half_height, 0.0)
        for y in range(EXAMPLE_ROWS + 1)
        for x in range(columns + 1)
    ]
    faces = []
    for y in range(EXAMPLE_ROWS):
        for x in range(columns):
            first = y * (columns + 1) + x
            faces.append(
                (first, first + 1, first + columns + 2, first + columns + 1)
            )

    mesh = bpy.data.meshes.new(f"TT_Example_{preset}_Mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(f"TT_Example_{preset}", mesh)
    context.collection.objects.link(obj)
    obj.location = location
    obj["topology_transition"] = label
    context.view_layer.objects.active = obj
    obj.select_set(True)

    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(mesh)
    bm.faces.ensure_lookup_table()
    bm.select_mode = {"FACE"}
    for face in bm.faces:
        row = face.index // columns
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
        raise RuntimeError(f"Could not prepare the {label} example patch")
    bm.select_history.clear()
    bm.select_history.add(incoming_edges[len(incoming_edges) // 2])
    bmesh.update_edit_mesh(mesh, loop_triangles=True, destructive=False)
    result = bpy.ops.mesh.quad_transition_apply(
        transition=preset,
        pole_side="CENTER",
        relax_strength=0.55,
        relax_iterations=16,
        conform_surface=True,
    )
    if result != {"FINISHED"}:
        raise RuntimeError(f"Could not build the {label} example transition")
    bpy.ops.object.mode_set(mode="OBJECT")
    return obj


def _assign_bands(obj: Any, materials: tuple[Any, Any, Any]) -> None:
    for material in materials:
        obj.data.materials.append(material)
    for polygon in obj.data.polygons:
        center_y = sum(
            obj.data.vertices[index].co.y for index in polygon.vertices
        ) / len(polygon.vertices)
        if center_y > 1.0:
            polygon.material_index = 0
        elif center_y > -1.0:
            polygon.material_index = 1
        else:
            polygon.material_index = 2


def _consolidate_material_slots(obj: Any, materials: tuple[Any, Any, Any]) -> None:
    target_index = {material.name: index for index, material in enumerate(materials)}
    slot_names = [material.name if material else "" for material in obj.data.materials]
    polygon_targets = [
        target_index[slot_names[polygon.material_index]]
        for polygon in obj.data.polygons
    ]
    obj.data.materials.clear()
    for material in materials:
        obj.data.materials.append(material)
    for polygon, material_index in zip(
        obj.data.polygons, polygon_targets, strict=True
    ):
        polygon.material_index = material_index


def _add_label(context: Any, atlas: Any, text: str, location: Any, material: Any):
    curve = bpy.data.curves.new(f"TT_Label_{text}", "FONT")
    curve.body = text
    curve.align_x = "CENTER"
    curve.align_y = "CENTER"
    curve.size = 0.58
    curve.extrude = 0.008
    curve.materials.append(material)
    label = bpy.data.objects.new(f"TT_Label_{text}", curve)
    context.collection.objects.link(label)
    label.location = location
    label.show_in_front = True
    label.parent = atlas
    label.matrix_parent_inverse = atlas.matrix_world.inverted_safe()
    return label


class QT_OT_add_example_plane(Operator):
    bl_idname = "object.quad_transition_add_example_plane"
    bl_label = "Add All Transition Examples"
    bl_description = (
        "Create one labeled atlas mesh containing every supported transition"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: Any) -> bool:
        return context.mode == "OBJECT"

    def execute(self, context: Any):
        for selected in context.selected_objects:
            selected.select_set(False)
        materials = (
            _material("TT Example Blue", (0.17, 0.58, 0.82, 1.0)),
            _material("TT Example Purple", (0.48, 0.31, 0.82, 1.0)),
            _material("TT Example Red", (0.9, 0.08, 0.18, 1.0)),
        )
        label_material = _material("TT Example Labels", (0.04, 0.65, 1.0, 1.0))
        base = context.scene.cursor.location.copy()
        tiles = []
        labels = []
        items = transition_items()
        try:
            for index, (preset, label, _description) in enumerate(items):
                column = index % ATLAS_COLUMNS
                row = index // ATLAS_COLUMNS
                offset_x = (column - (ATLAS_COLUMNS - 1) * 0.5) * TILE_SPACING_X
                offset_y = (0.5 - row) * TILE_SPACING_Y
                location = base.copy()
                location.x += offset_x
                location.y += offset_y
                obj = _create_grid_object(context, preset, label, location)
                _assign_bands(obj, materials)
                tiles.append(obj)

            for tile in tiles:
                tile.select_set(True)
            context.view_layer.objects.active = tiles[0]
            if bpy.ops.object.join() != {"FINISHED"}:
                raise RuntimeError("Could not combine the transition atlas tiles")
            atlas = context.active_object
            atlas.name = "TopologyTransitions_Example_Atlas"
            atlas.data.name = "TopologyTransitions_Example_Atlas_Mesh"
            atlas["topology_transitions_example"] = "all supported transitions"
            atlas["transition_count"] = len(items)
            _consolidate_material_slots(atlas, materials)

            for index, (_preset, label, _description) in enumerate(items):
                column = index % ATLAS_COLUMNS
                row = index // ATLAS_COLUMNS
                location = base.copy()
                location.x += (column - (ATLAS_COLUMNS - 1) * 0.5) * TILE_SPACING_X
                location.y += (0.5 - row) * TILE_SPACING_Y + EXAMPLE_ROWS * 0.5 + 0.7
                location.z += 0.025
                labels.append(
                    _add_label(
                        context,
                        atlas,
                        label.replace(" to ", " → "),
                        location,
                        label_material,
                    )
                )
            for label in labels:
                label.select_set(False)
            atlas.select_set(True)
            context.view_layer.objects.active = atlas
        except Exception as exc:
            if context.object is not None and context.object.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            failed = [
                obj
                for obj in bpy.data.objects
                if obj.name.startswith("TT_Example_")
                or obj.name.startswith("TT_Label_")
                or obj.name.startswith("TopologyTransitions_Example_Atlas")
            ]
            _remove_objects([*tiles, *labels, *failed])
            self.report({"ERROR"}, f"Could not build the transition atlas: {exc}")
            return {"CANCELLED"}

        settings = context.scene.topology_transitions
        settings.flow_index = 0
        settings.flow_count = 0
        settings.flow_object_name = ""
        self.report(
            {"INFO"},
            f"Added {len(items)} transition examples in one quad-flow atlas",
        )
        return {"FINISHED"}


CLASSES = (QT_OT_add_example_plane,)


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
