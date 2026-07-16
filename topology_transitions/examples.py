"""Ready-made atlas containing every supported density transition."""

from __future__ import annotations

import json
from typing import Any

import bpy
from bpy.types import Operator

from .core import build_transition_template, preset_counts, transition_items

REGULAR_ROWS = 3
TRANSITION_HEIGHT = 2.0
EXAMPLE_HEIGHT = REGULAR_ROWS * 2 + TRANSITION_HEIGHT
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


def _example_template(preset: str):
    incoming, outgoing = preset_counts(preset)
    side_segments = (2, 2) if abs(incoming - outgoing) == 2 else (2, 1)
    return build_transition_template(
        incoming,
        outgoing,
        *side_segments,
        pole_side="CENTER",
        pole_spacing=1.15,
    )


def _create_density_transition_object(
    context: Any,
    preset: str,
    label: str,
    location: Any,
    materials: tuple[Any, Any, Any],
):
    """Build regular input/output rows around a true unequal-count template."""

    for selected in context.selected_objects:
        selected.select_set(False)
    incoming, outgoing = preset_counts(preset)
    width = float(max(incoming, outgoing))
    template = _example_template(preset)
    vertices: list[tuple[float, float, float]] = []
    key_indices: dict[str, int] = {}

    def add_vertex(x: float, y: float) -> int:
        vertices.append((x, y, 0.0))
        return len(vertices) - 1

    for key, spec in template.vertices.items():
        key_indices[key] = add_vertex(
            (spec.u - 0.5) * width,
            (spec.v - 0.5) * TRANSITION_HEIGHT,
        )

    faces: list[tuple[int, int, int, int]] = [
        tuple(key_indices[key] for key in face) for face in template.faces
    ]
    material_indices = [1] * len(faces)

    top_row = [key_indices[key] for key in template.top_keys]
    for row_index in range(1, REGULAR_ROWS + 1):
        y = TRANSITION_HEIGHT * 0.5 + row_index
        next_row = [
            add_vertex((column / incoming - 0.5) * width, y)
            for column in range(incoming + 1)
        ]
        for column in range(incoming):
            faces.append(
                (
                    next_row[column],
                    next_row[column + 1],
                    top_row[column + 1],
                    top_row[column],
                )
            )
            material_indices.append(0)
        top_row = next_row

    bottom_row = [key_indices[key] for key in template.bottom_keys]
    for row_index in range(1, REGULAR_ROWS + 1):
        y = -TRANSITION_HEIGHT * 0.5 - row_index
        next_row = [
            add_vertex((column / outgoing - 0.5) * width, y)
            for column in range(outgoing + 1)
        ]
        for column in range(outgoing):
            faces.append(
                (
                    bottom_row[column],
                    bottom_row[column + 1],
                    next_row[column + 1],
                    next_row[column],
                )
            )
            material_indices.append(2)
        bottom_row = next_row

    mesh = bpy.data.meshes.new(f"TT_Example_{preset}_Mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    for material in materials:
        mesh.materials.append(material)
    for polygon, material_index in zip(mesh.polygons, material_indices, strict=True):
        polygon.material_index = material_index

    obj = bpy.data.objects.new(f"TT_Example_{preset}", mesh)
    context.collection.objects.link(obj)
    obj.location = location
    obj["topology_transition"] = label
    obj["incoming_columns"] = incoming
    obj["outgoing_columns"] = outgoing
    obj["transition_quads"] = len(template.faces)
    obj["top_regular_quads"] = incoming * REGULAR_ROWS
    obj["bottom_regular_quads"] = outgoing * REGULAR_ROWS
    context.view_layer.objects.active = obj
    obj.select_set(True)
    return obj


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
    for polygon, material_index in zip(obj.data.polygons, polygon_targets, strict=True):
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
        "Create one labeled atlas showing real dense-to-sparse quad transitions"
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
        density_metadata = []
        try:
            for index, (preset, label, _description) in enumerate(items):
                column = index % ATLAS_COLUMNS
                row = index // ATLAS_COLUMNS
                location = base.copy()
                location.x += (column - (ATLAS_COLUMNS - 1) * 0.5) * TILE_SPACING_X
                location.y += (0.5 - row) * TILE_SPACING_Y
                obj = _create_density_transition_object(
                    context, preset, label, location, materials
                )
                density_metadata.append(
                    {
                        "preset": preset,
                        "incoming": obj["incoming_columns"],
                        "outgoing": obj["outgoing_columns"],
                        "top_regular_quads": obj["top_regular_quads"],
                        "bottom_regular_quads": obj["bottom_regular_quads"],
                        "transition_quads": obj["transition_quads"],
                    }
                )
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
            atlas["density_transitions"] = json.dumps(density_metadata)
            _consolidate_material_slots(atlas, materials)

            for index, (_preset, label, _description) in enumerate(items):
                column = index % ATLAS_COLUMNS
                row = index // ATLAS_COLUMNS
                location = base.copy()
                location.x += (column - (ATLAS_COLUMNS - 1) * 0.5) * TILE_SPACING_X
                location.y += (0.5 - row) * TILE_SPACING_Y + EXAMPLE_HEIGHT * 0.5 + 0.7
                location.z += 0.025
                labels.append(
                    _add_label(
                        context,
                        atlas,
                        label.replace(" to ", " \u2192 "),
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
            f"Added {len(items)} true density-transition examples",
        )
        return {"FINISHED"}


CLASSES = (QT_OT_add_example_plane,)


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
