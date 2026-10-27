"""Preview and build a triangulated connection between two mesh objects."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Any

import bmesh
import bpy
from bpy.props import FloatProperty
from bpy.types import Operator
from mathutils import Vector
from mathutils.geometry import closest_point_on_tri

PREVIEW_NAME = "Topology Transition Target Delete Preview"
PREVIEW_MATERIAL_NAME = "Topology Transition Delete Preview Red"
DISTANCE_EPSILON = 1.0e-6


def _connection_objects(context: Any) -> tuple[Any, Any]:
    source = context.active_object
    meshes = [obj for obj in context.selected_objects if obj.type == "MESH"]
    if source is None or source.type != "MESH" or source not in meshes:
        raise RuntimeError(
            "Make the contact mesh active and select the target mesh too"
        )
    targets = [obj for obj in meshes if obj != source]
    if len(targets) != 1 or len(meshes) != 2:
        raise RuntimeError(
            "Select exactly two meshes; the active one is the contact mesh"
        )
    if source.data.shape_keys or targets[0].data.shape_keys:
        raise RuntimeError("Shape-key meshes are not supported by the connector")
    return source, targets[0]


def _selected_source_faces(source: Any) -> set[int]:
    selected = {polygon.index for polygon in source.data.polygons if polygon.select}
    if not selected:
        raise RuntimeError(
            "Select the contact face region on the active mesh in Edit Mode, "
            "then return to Object Mode"
        )
    return selected


def _world_triangles(obj: Any, face_indices: set[int]) -> list[tuple[Vector, ...]]:
    mesh = obj.data
    mesh.calc_loop_triangles()
    matrix = obj.matrix_world
    return [
        tuple(matrix @ mesh.vertices[index].co for index in triangle.vertices)
        for triangle in mesh.loop_triangles
        if triangle.polygon_index in face_indices
    ]


def _point_triangle_distance(point: Vector, triangle: tuple[Vector, ...]) -> float:
    nearest = closest_point_on_tri(point, *triangle)
    return (point - nearest).length


def nearby_target_faces(source: Any, target: Any, distance: float) -> set[int]:
    """Return target polygons within world-space distance of selected source faces."""
    selected = _selected_source_faces(source)
    triangles = _world_triangles(source, selected)
    if not triangles:
        raise RuntimeError("The selected source region has no usable triangles")
    limit = max(float(distance), 0.0) + DISTANCE_EPSILON
    source_centers = [
        source.matrix_world @ source.data.polygons[index].center for index in selected
    ]
    target.data.calc_loop_triangles()
    target_triangles: dict[int, list[tuple[Vector, ...]]] = defaultdict(list)
    for triangle in target.data.loop_triangles:
        target_triangles[triangle.polygon_index].append(
            tuple(
                target.matrix_world @ target.data.vertices[index].co
                for index in triangle.vertices
            )
        )
    nearby: set[int] = set()
    for polygon in target.data.polygons:
        target_center = target.matrix_world @ polygon.center
        center_near_source = any(
            _point_triangle_distance(target_center, triangle) <= limit
            for triangle in triangles
        )
        source_center_near_target = any(
            _point_triangle_distance(center, triangle) <= limit
            for center in source_centers
            for triangle in target_triangles[polygon.index]
        )
        if center_near_source or source_center_near_target:
            nearby.add(polygon.index)
    if not nearby:
        raise RuntimeError(
            "No target faces are inside the delete distance; increase Nearby Delete"
        )
    return nearby


def _region_boundary(mesh: Any, removed_faces: set[int], label: str) -> list[int]:
    edge_counts: dict[tuple[int, int], int] = defaultdict(int)
    for face_index in removed_faces:
        vertices = list(mesh.polygons[face_index].vertices)
        for first, second in zip(vertices, vertices[1:] + vertices[:1]):
            edge_counts[tuple(sorted((first, second)))] += 1
    edges = [edge for edge, count in edge_counts.items() if count == 1]
    adjacency: dict[int, list[int]] = defaultdict(list)
    for first, second in edges:
        adjacency[first].append(second)
        adjacency[second].append(first)
    if not adjacency or any(len(neighbors) != 2 for neighbors in adjacency.values()):
        raise RuntimeError(
            f"The {label} contact region must have one closed boundary loop"
        )
    start = min(adjacency)
    ordered = [start]
    previous = None
    current = start
    while True:
        candidates = [value for value in adjacency[current] if value != previous]
        next_vertex = candidates[0]
        if next_vertex == start:
            break
        if next_vertex in ordered:
            raise RuntimeError(f"The {label} contact region has a branching boundary")
        ordered.append(next_vertex)
        previous, current = current, next_vertex
    if len(ordered) != len(adjacency):
        raise RuntimeError(
            f"The {label} contact region creates multiple boundary loops"
        )
    return ordered


def _rotate(values: list[int], index: int) -> list[int]:
    return values[index:] + values[:index]


def _align_loops(
    target_loop: list[int],
    source_loop: list[int],
    vertices: list[Vector],
) -> tuple[list[int], list[int]]:
    target_index, source_index = min(
        ((i, j) for i in range(len(target_loop)) for j in range(len(source_loop))),
        key=lambda pair: (
            vertices[target_loop[pair[0]]] - vertices[source_loop[pair[1]]]
        ).length_squared,
    )
    target = _rotate(target_loop, target_index)
    source = _rotate(source_loop, source_index)
    forward = (vertices[target[1]] - vertices[source[1]]).length_squared
    reverse = (vertices[target[1]] - vertices[source[-1]]).length_squared
    if reverse < forward:
        source = [source[0], *reversed(source[1:])]
    return target, source


def _triangulate_loops(
    first: list[int], second: list[int], vertices: list[Vector]
) -> list[tuple[int, int, int]]:
    triangles: list[tuple[int, int, int]] = []
    i = j = 0
    while i < len(first) or j < len(second):
        if i == len(first):
            advance_first = False
        elif j == len(second):
            advance_first = True
        else:
            first_cost = (
                vertices[first[(i + 1) % len(first)]]
                - vertices[second[j % len(second)]]
            ).length_squared
            second_cost = (
                vertices[first[i % len(first)]]
                - vertices[second[(j + 1) % len(second)]]
            ).length_squared
            advance_first = first_cost <= second_cost
        if advance_first:
            triangle = (
                first[i % len(first)],
                first[(i + 1) % len(first)],
                second[j % len(second)],
            )
            i += 1
        else:
            triangle = (
                first[i % len(first)],
                second[(j + 1) % len(second)],
                second[j % len(second)],
            )
            j += 1
        if len(set(triangle)) == 3:
            triangles.append(triangle)
    return triangles


def _append_object_geometry(
    obj: Any,
    removed: set[int],
    destination_matrix: Any,
    vertices: list[Vector],
    faces: list[tuple[int, ...]],
    material_indices: list[int],
    material_offset: int,
) -> dict[int, int]:
    kept = [face for face in obj.data.polygons if face.index not in removed]
    used = {index for face in kept for index in face.vertices}
    used.update(index for index in _region_boundary(obj.data, removed, obj.name))
    remap: dict[int, int] = {}
    transform = destination_matrix @ obj.matrix_world
    for old_index in sorted(used):
        remap[old_index] = len(vertices)
        vertices.append(transform @ obj.data.vertices[old_index].co)
    for face in kept:
        faces.append(tuple(remap[index] for index in face.vertices))
        material_indices.append(material_offset + face.material_index)
    return remap


def build_connected_result(
    context: Any, source: Any, target: Any, target_faces: set[int]
) -> tuple[Any, dict[str, int]]:
    source_faces = _selected_source_faces(source)
    source_boundary = _region_boundary(source.data, source_faces, "source")
    target_boundary = _region_boundary(target.data, target_faces, "target")
    vertices: list[Vector] = []
    faces: list[tuple[int, ...]] = []
    material_indices: list[int] = []
    target_inverse = target.matrix_world.inverted_safe()
    target_remap = _append_object_geometry(
        target, target_faces, target_inverse, vertices, faces, material_indices, 0
    )
    target_material_count = len(target.data.materials)
    source_remap = _append_object_geometry(
        source,
        source_faces,
        target_inverse,
        vertices,
        faces,
        material_indices,
        target_material_count,
    )
    first = [target_remap[index] for index in target_boundary]
    second = [source_remap[index] for index in source_boundary]
    first, second = _align_loops(first, second, vertices)
    bridge = _triangulate_loops(first, second, vertices)
    if not bridge:
        raise RuntimeError("The two contact loops could not produce a triangle bridge")
    faces.extend(bridge)
    material_indices.extend([0] * len(bridge))

    mesh = bpy.data.meshes.new(f"{target.data.name}_Connected")
    mesh.from_pydata(vertices, (), faces)
    for material in target.data.materials:
        mesh.materials.append(material)
    for material in source.data.materials:
        mesh.materials.append(material)
    for polygon, material_index in zip(mesh.polygons, material_indices):
        polygon.material_index = material_index
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
        bm.to_mesh(mesh)
    finally:
        bm.free()
    mesh.update()
    result = bpy.data.objects.new(f"{target.name}_Connected", mesh)
    context.collection.objects.link(result)
    result.matrix_world = target.matrix_world.copy()
    for obj in context.selected_objects:
        obj.select_set(False)
    source.hide_set(True)
    target.hide_set(True)
    result.hide_set(False)
    result.select_set(True)
    context.view_layer.objects.active = result
    return result, {
        "source_faces": len(source_faces),
        "target_faces": len(target_faces),
        "bridge_triangles": len(bridge),
    }


def _remove_preview() -> bool:
    preview = bpy.data.objects.get(PREVIEW_NAME)
    if preview is None:
        return False
    mesh = preview.data
    bpy.data.objects.remove(preview, do_unlink=True)
    if mesh.users == 0:
        bpy.data.meshes.remove(mesh)
    return True


def _preview_material(opacity: float) -> Any:
    material = bpy.data.materials.get(PREVIEW_MATERIAL_NAME)
    if material is None:
        material = bpy.data.materials.new(PREVIEW_MATERIAL_NAME)
    material.diffuse_color = (1.0, 0.01, 0.01, opacity)
    material.use_nodes = True
    principled = material.node_tree.nodes.get("Principled BSDF")
    if principled is not None:
        principled.inputs["Base Color"].default_value = (1.0, 0.01, 0.01, 1.0)
        principled.inputs["Alpha"].default_value = opacity
    if hasattr(material, "surface_render_method"):
        material.surface_render_method = "DITHERED"
    return material


def create_delete_preview(
    context: Any, target: Any, face_indices: Iterable[int], opacity: float
) -> Any:
    _remove_preview()
    indices = set(face_indices)
    used = sorted(
        {
            index
            for face in target.data.polygons
            if face.index in indices
            for index in face.vertices
        }
    )
    remap = {old: new for new, old in enumerate(used)}
    vertices = [target.matrix_world @ target.data.vertices[index].co for index in used]
    faces = [
        tuple(remap[index] for index in face.vertices)
        for face in target.data.polygons
        if face.index in indices
    ]
    mesh = bpy.data.meshes.new(f"{PREVIEW_NAME} Mesh")
    mesh.from_pydata(vertices, (), faces)
    mesh.materials.append(_preview_material(opacity))
    preview = bpy.data.objects.new(PREVIEW_NAME, mesh)
    context.collection.objects.link(preview)
    preview.color = (1.0, 0.01, 0.01, opacity)
    preview.show_in_front = True
    if hasattr(preview, "show_transparent"):
        preview.show_transparent = True
    preview.hide_select = True
    preview["topology_transitions_preview"] = True
    return preview


class QT_OT_preview_target_connection(Operator):
    bl_idname = "object.quad_transition_preview_target_connection"
    bl_label = "Preview Target Deletion"
    bl_description = (
        "Show target faces that will be removed as a translucent red overlay"
    )
    bl_options = {"REGISTER", "UNDO"}

    distance: FloatProperty(
        name="Nearby Delete",
        min=0.0,
        soft_max=10.0,
        default=0.1,
        subtype="DISTANCE",
    )
    opacity: FloatProperty(name="Red Opacity", min=0.05, max=0.9, default=0.35)

    @classmethod
    def poll(cls, context: Any) -> bool:
        return context.mode == "OBJECT" and len(context.selected_objects) >= 2

    def execute(self, context: Any):
        try:
            source, target = _connection_objects(context)
            faces = nearby_target_faces(source, target, self.distance)
            create_delete_preview(context, target, faces, self.opacity)
        except RuntimeError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Red preview shows {len(faces)} target face(s)")
        return {"FINISHED"}


class QT_OT_clear_target_connection_preview(Operator):
    bl_idname = "object.quad_transition_clear_target_connection_preview"
    bl_label = "Clear Connection Preview"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, _context: Any):
        _remove_preview()
        return {"FINISHED"}


class QT_OT_build_target_connection(Operator):
    bl_idname = "object.quad_transition_build_target_connection"
    bl_label = "Build Triangulated Connection"
    bl_description = (
        "Create a connected copy, remove the chosen contact regions, and bridge "
        "their boundary loops with triangles"
    )
    bl_options = {"REGISTER", "UNDO"}

    distance: FloatProperty(
        name="Nearby Delete",
        min=0.0,
        soft_max=10.0,
        default=0.1,
        subtype="DISTANCE",
    )

    @classmethod
    def poll(cls, context: Any) -> bool:
        return context.mode == "OBJECT" and len(context.selected_objects) >= 2

    def execute(self, context: Any):
        try:
            source, target = _connection_objects(context)
            faces = nearby_target_faces(source, target, self.distance)
            _result, stats = build_connected_result(context, source, target, faces)
            _remove_preview()
        except RuntimeError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report(
            {"INFO"},
            f"Removed {stats['source_faces']} + {stats['target_faces']} "
            "contact face(s); "
            f"built {stats['bridge_triangles']} bridge triangles",
        )
        return {"FINISHED"}


CLASSES = (
    QT_OT_preview_target_connection,
    QT_OT_clear_target_connection_preview,
    QT_OT_build_target_connection,
)


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    _remove_preview()
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
