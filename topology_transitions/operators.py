"""Blender operators for applying and validating transition patches."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import Any

import bmesh
import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    StringProperty,
)
from bpy.types import Object, Operator
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from .core import (
    TransitionError,
    build_transition_template,
    frame_transition_for_single_quad,
    preset_counts,
    template_adjacency,
    transition_items,
)
from .edit_mesh import restore_bmesh
from .mesh_ops import analyze_selected_patch, analyze_single_quad, bind_boundary

PREVIEW_MODIFIER_NAME = "Topology Transition Preview"
GEOMETRY_EPSILON = 1.0e-8


AXIS_ITEMS = (
    ("AUTO", "Auto / Active Edge", "Use the active boundary edge when possible"),
    ("ALTERNATE", "Alternate Axis", "Use the other valid axis on a square patch"),
)
POLE_SIDE_ITEMS = (
    ("LEFT", "Left", "Move the pole pattern toward the left"),
    ("CENTER", "Center", "Center the pole pattern where possible"),
    ("RIGHT", "Right", "Move the pole pattern toward the right"),
)


def _shoulders(
    input_count: int, output_count: int, pole_side: str, mirror: bool
) -> tuple[int, int, bool]:
    difference = abs(input_count - output_count)
    if difference == 2:
        return 1, 1, mirror
    side = pole_side if pole_side in {"LEFT", "RIGHT"} else "RIGHT"
    if mirror:
        side = "RIGHT" if side == "LEFT" else "LEFT"
    return (1, 0, False) if side == "LEFT" else (0, 1, False)


def _bilinear(
    u: float,
    v: float,
    top_left: Vector,
    top_right: Vector,
    bottom_left: Vector,
    bottom_right: Vector,
) -> Vector:
    top = top_left.lerp(top_right, u)
    bottom = bottom_left.lerp(bottom_right, u)
    return bottom.lerp(top, v)


def _patch_bvh(faces: list[Any]) -> BVHTree:
    vertices = list({vertex for face in faces for vertex in face.verts})
    indices = {vertex: index for index, vertex in enumerate(vertices)}
    polygons = [[indices[vertex] for vertex in face.verts] for face in faces]
    return BVHTree.FromPolygons(
        [vertex.co.copy() for vertex in vertices], polygons, all_triangles=False
    )


def _average_normal(faces: list[Any]) -> Vector:
    normal = Vector((0.0, 0.0, 0.0))
    for face in faces:
        area = max(face.calc_area(), 1.0e-12)
        normal += face.normal * area
    if normal.length_squared < 1.0e-16:
        raise TransitionError("The selected patch has no stable surface normal")
    return normal.normalized()


def _quad_normal(points: list[Vector]) -> Vector:
    return (points[1] - points[0]).cross(points[2] - points[0]) + (
        points[2] - points[0]
    ).cross(points[3] - points[0])


def _projection_axes(normal: Vector) -> tuple[Vector, Vector]:
    fallback = (
        Vector((0.0, 1.0, 0.0))
        if abs(normal.dot(Vector((1.0, 0.0, 0.0)))) > 0.9
        else Vector((1.0, 0.0, 0.0))
    )
    x_axis = fallback.cross(normal)
    if x_axis.length_squared < 1.0e-16:
        raise TransitionError("The fitted patch has no stable tangent basis")
    x_axis.normalize()
    y_axis = normal.cross(x_axis).normalized()
    return x_axis, y_axis


def _signed_area_2d(points: list[tuple[float, float]]) -> float:
    return 0.5 * sum(
        first[0] * second[1] - second[0] * first[1]
        for first, second in zip(points, points[1:] + points[:1], strict=True)
    )


def _orientation(
    first: tuple[float, float],
    second: tuple[float, float],
    third: tuple[float, float],
) -> float:
    return (second[0] - first[0]) * (third[1] - first[1]) - (
        second[1] - first[1]
    ) * (third[0] - first[0])


def _segments_cross(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    d: tuple[float, float],
) -> bool:
    first = _orientation(a, b, c)
    second = _orientation(a, b, d)
    third = _orientation(c, d, a)
    fourth = _orientation(c, d, b)
    return (
        first * second < -(GEOMETRY_EPSILON * GEOMETRY_EPSILON)
        and third * fourth < -(GEOMETRY_EPSILON * GEOMETRY_EPSILON)
    )


def _validate_fitted_faces(
    faces: list[tuple[str, str, str, str]],
    coordinates: dict[str, Vector],
    reference_normal: Vector,
) -> None:
    """Reject fitted quads that fold before mutating the edit mesh."""

    edge_uses: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for face in faces:
        for index, first in enumerate(face):
            second = face[(index + 1) % 4]
            edge_uses.setdefault(tuple(sorted((first, second))), []).append(
                (first, second)
            )
    for edge, uses in edge_uses.items():
        if len(uses) > 2:
            raise TransitionError(
                "The fitted transition would create a non-manifold edge"
            )
        if len(uses) == 2 and uses[0] != (uses[1][1], uses[1][0]):
            raise TransitionError(
                "The fitted transition folds over itself; reduce pole spacing, "
                "choose a different pole side, or use a larger patch"
            )

    x_axis, y_axis = _projection_axes(reference_normal)
    projected = {
        key: (coordinate.dot(x_axis), coordinate.dot(y_axis))
        for key, coordinate in coordinates.items()
    }
    for face in faces:
        face_points = [projected[key] for key in face]
        if abs(_signed_area_2d(face_points)) <= GEOMETRY_EPSILON:
            raise TransitionError(
                "The fitted transition contains a collapsed quad in projection"
            )

    segments: list[
        tuple[tuple[str, str], tuple[float, float], tuple[float, float]]
    ] = []
    for edge in edge_uses:
        first, second = edge
        segments.append((edge, projected[first], projected[second]))
    for index, (first_edge, first_start, first_end) in enumerate(segments):
        first_keys = set(first_edge)
        for second_edge, second_start, second_end in segments[index + 1 :]:
            if first_keys & set(second_edge):
                continue
            if _segments_cross(first_start, first_end, second_start, second_end):
                raise TransitionError(
                    "The fitted transition self-intersects; use a less concave "
                    "selection or a larger transition patch"
                )


def _projection_function(
    context: Any,
    active_object: Object,
    source_bvh: BVHTree,
    projection_target: Object | None,
) -> Callable[[Vector], Vector]:
    if projection_target is None:

        def project_source(coordinate: Vector) -> Vector:
            location, _normal, _index, _distance = source_bvh.find_nearest(coordinate)
            return location.copy() if location is not None else coordinate

        return project_source

    if projection_target is active_object:
        raise TransitionError(
            "Use the built-in original-patch projection instead of selecting "
            "the edited object"
        )
    if projection_target.type != "MESH":
        raise TransitionError("Projection target must be a mesh object")

    depsgraph = context.evaluated_depsgraph_get()
    evaluated = projection_target.evaluated_get(depsgraph)
    active_to_world = active_object.matrix_world
    world_to_active = active_to_world.inverted_safe()
    world_to_target = evaluated.matrix_world.inverted_safe()
    target_to_world = evaluated.matrix_world

    def project_target(coordinate: Vector) -> Vector:
        world = active_to_world @ coordinate
        target_local = world_to_target @ world
        success, location, _normal, _index = evaluated.closest_point_on_mesh(
            target_local, distance=1.0e20
        )
        if not success:
            return coordinate
        return world_to_active @ (target_to_world @ location)

    return project_target


def _make_template_and_layout(
    bm: Any,
    transition: str,
    axis_mode: str,
    flip_flow: bool,
    pole_side: str,
    mirror: bool,
    pole_spacing: float,
):
    input_count, output_count = preset_counts(transition)
    shoulder_left, shoulder_right, core_mirror = _shoulders(
        input_count, output_count, pole_side, mirror
    )
    layout = analyze_single_quad(
        bm,
        axis_mode=axis_mode,
        flip_flow=flip_flow,
    )
    if layout is not None:
        inner = build_transition_template(
            input_count,
            output_count,
            1 + shoulder_left,
            1 + shoulder_right,
            pole_side=pole_side,
            mirror=core_mirror,
            pole_spacing=pole_spacing,
        )
        template = frame_transition_for_single_quad(inner)
    else:
        layout = analyze_selected_patch(
            bm,
            wide_count=max(input_count, output_count),
            input_count=input_count,
            output_count=output_count,
            shoulder_left=shoulder_left,
            shoulder_right=shoulder_right,
            axis_mode=axis_mode,
            flip_flow=flip_flow,
        )
        template = build_transition_template(
            input_count,
            output_count,
            layout.left_segments,
            layout.right_segments,
            pole_side=pole_side,
            mirror=core_mirror,
            pole_spacing=pole_spacing,
        )
    return input_count, output_count, layout, template


def apply_transition(
    context: Any,
    *,
    transition: str,
    axis_mode: str,
    flip_flow: bool,
    pole_side: str,
    mirror: bool,
    pole_spacing: float,
    relax_strength: float,
    relax_iterations: int,
    conform_surface: bool,
    projection_target: Object | None,
) -> dict[str, Any]:
    obj = context.edit_object
    if obj is None or obj.type != "MESH" or obj.mode != "EDIT":
        raise TransitionError(
            "Topology Transitions requires an active mesh in Edit Mode"
        )
    if len(context.objects_in_mode_unique_data) != 1:
        raise TransitionError(
            "Multi-object Edit Mode is not supported; edit one mesh at a time"
        )
    if obj.data.shape_keys is not None:
        raise TransitionError(
            "Meshes with shape keys are not modified because new-key "
            "interpolation is ambiguous"
        )

    mesh = obj.data
    bm = bmesh.from_edit_mesh(mesh)
    bm.normal_update()
    input_count, output_count, layout, template = _make_template_and_layout(
        bm,
        transition,
        axis_mode,
        flip_flow,
        pole_side,
        mirror,
        pole_spacing,
    )
    binding = bind_boundary(template, layout)

    boundary_coordinates = {
        vertex: vertex.co.copy() for vertex in layout.boundary_vertices
    }
    boundary_face_counts = {
        edge: len(edge.link_faces) for edge in layout.boundary_edges
    }
    reference_normal = _average_normal(layout.selected_faces)
    source_bvh = _patch_bvh(layout.selected_faces)
    project = _projection_function(context, obj, source_bvh, projection_target)
    material_index = Counter(
        face.material_index for face in layout.selected_faces
    ).most_common(1)[0][0]
    smooth = sum(face.smooth for face in layout.selected_faces) * 2 >= len(
        layout.selected_faces
    )

    top_left, top_right, bottom_left, bottom_right = (
        vertex.co.copy() for vertex in layout.physical_corners
    )
    coordinates: dict[str, Vector] = {
        key: vertex.co.copy() for key, vertex in binding.items()
    }
    anchors: dict[str, Vector] = {}
    for key in template.interior_keys:
        spec = template.vertices[key]
        coordinate = _bilinear(
            spec.u,
            spec.v,
            top_left,
            top_right,
            bottom_left,
            bottom_right,
        )
        coordinates[key] = coordinate
        anchors[key] = coordinate.copy()

    adjacency = template_adjacency(template)
    relaxable_keys = template.interior_keys - template.relax_locked_keys
    for _iteration in range(relax_iterations):
        next_coordinates: dict[str, Vector] = {}
        for key in relaxable_keys:
            neighbors = adjacency[key]
            average = sum(
                (coordinates[neighbor] for neighbor in neighbors),
                Vector((0.0, 0.0, 0.0)),
            ) / len(neighbors)
            relaxed = coordinates[key].lerp(average, relax_strength)
            if key in template.pole_keys:
                relaxed = relaxed.lerp(anchors[key], 0.15 * relax_strength)
            next_coordinates[key] = relaxed
        coordinates.update(next_coordinates)

    if conform_surface:
        for key in template.interior_keys:
            coordinates[key] = project(coordinates[key])

    oriented_faces: list[tuple[str, str, str, str]] = []
    for face in template.faces:
        points = [coordinates[key] for key in face]
        normal = _quad_normal(points)
        if normal.length_squared < 1.0e-16:
            raise TransitionError(
                "The fitted patch contains a zero-area quad; increase patch "
                "length or reduce relaxation"
            )
        oriented_faces.append(
            tuple(reversed(face)) if normal.dot(reference_normal) < 0 else face
        )
    _validate_fitted_faces(oriented_faces, coordinates, reference_normal)

    backup = bm.copy()
    created_vertices: list[Any] = []
    created_faces: list[Any] = []
    vertex_map = dict(binding)
    try:
        for key in template.interior_keys:
            vertex = bm.verts.new(coordinates[key])
            vertex_map[key] = vertex
            created_vertices.append(vertex)
        bm.verts.index_update()
        bm.verts.ensure_lookup_table()

        for face_keys in oriented_faces:
            face = bm.faces.new([vertex_map[key] for key in face_keys])
            face.material_index = material_index
            face.smooth = smooth
            created_faces.append(face)

        bmesh.ops.delete(bm, geom=layout.selected_faces, context="FACES_ONLY")
        dead_edges = [
            edge for edge in layout.patch_edges if edge.is_valid and not edge.link_faces
        ]
        if dead_edges:
            bmesh.ops.delete(bm, geom=dead_edges, context="EDGES")
        dead_vertices = [
            vertex
            for vertex in layout.patch_vertices
            if vertex.is_valid
            and vertex not in boundary_coordinates
            and not vertex.link_edges
        ]
        if dead_vertices:
            bmesh.ops.delete(bm, geom=dead_vertices, context="VERTS")

        bm.normal_update()
        if any(
            not vertex.is_valid or (vertex.co - coordinate).length > 1.0e-8
            for vertex, coordinate in boundary_coordinates.items()
        ):
            raise TransitionError("Boundary preservation check failed")
        if any(
            not edge.is_valid or len(edge.link_faces) != count
            for edge, count in boundary_face_counts.items()
        ):
            raise TransitionError("Outside boundary connectivity changed unexpectedly")
        affected_edges = {
            edge for face in created_faces if face.is_valid for edge in face.edges
        }
        if any(len(edge.link_faces) > 2 for edge in affected_edges):
            raise TransitionError("Generated patch contains a non-manifold edge")
        if any(
            not face.is_valid or len(face.verts) != 4 or face.calc_area() <= 1.0e-12
            for face in created_faces
        ):
            raise TransitionError("Generated patch failed the all-quad area check")
        if any(len(vertex_map[key].link_edges) != 3 for key in template.pole_keys):
            raise TransitionError("Generated N-pole valence check failed")

        for face in bm.faces:
            face.select_set(False)
        for face in created_faces:
            face.select_set(True)
        bm.select_flush_mode()
        bmesh.update_edit_mesh(mesh, loop_triangles=True, destructive=True)
    except Exception:
        restore_bmesh(mesh, bm, backup)
        raise
    finally:
        backup.free()

    return {
        "input_count": input_count,
        "output_count": output_count,
        "old_faces": len(layout.selected_faces),
        "new_faces": len(created_faces),
        "new_vertices": len(created_vertices),
        "poles": len(template.pole_keys),
        "width": layout.width,
        "height": layout.height,
        "active_side_used": layout.active_side_used,
        "single_quad_insertion": layout.single_quad_insertion,
        "had_uvs": bool(mesh.uv_layers),
    }


class QT_OT_apply_transition(Operator):
    bl_idname = "mesh.quad_transition_apply"
    bl_label = "Apply Quad Transition"
    bl_description = (
        "Replace a compatible patch, or insert a framed transition into one quad"
    )
    bl_options = {"REGISTER", "UNDO"}

    transition: EnumProperty(name="Transition", items=transition_items())
    axis_mode: EnumProperty(name="Patch Axis", items=AXIS_ITEMS, default="AUTO")
    flip_flow: BoolProperty(name="Reverse Flow", default=False)
    pole_side: EnumProperty(name="Pole Side", items=POLE_SIDE_ITEMS, default="CENTER")
    mirror: BoolProperty(name="Mirror", default=False)
    pole_spacing: FloatProperty(name="Pole Spacing", min=0.5, max=2.0, default=1.0)
    relax_strength: FloatProperty(
        name="Relax Strength", min=0.0, max=1.0, default=0.55, subtype="FACTOR"
    )
    relax_iterations: IntProperty(name="Relax Iterations", min=0, max=100, default=24)
    conform_surface: BoolProperty(name="Conform to Surface", default=True)
    projection_target_name: StringProperty(
        name="Projection Target",
        description="Name of the optional projection target mesh",
        default="",
    )

    @classmethod
    def poll(cls, context: Any) -> bool:
        return (
            context.edit_object is not None
            and context.edit_object.type == "MESH"
            and context.edit_object.mode == "EDIT"
        )

    def execute(self, context: Any):
        try:
            stats = apply_transition(
                context,
                transition=self.transition,
                axis_mode=self.axis_mode,
                flip_flow=self.flip_flow,
                pole_side=self.pole_side,
                mirror=self.mirror,
                pole_spacing=self.pole_spacing,
                relax_strength=self.relax_strength,
                relax_iterations=self.relax_iterations,
                conform_surface=self.conform_surface,
                projection_target=(
                    bpy.data.objects.get(self.projection_target_name)
                    if self.projection_target_name
                    else None
                ),
            )
        except TransitionError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:
            self.report({"ERROR"}, f"Transition failed and was rolled back: {exc}")
            return {"CANCELLED"}

        placement = (
            "inserted inside one quad"
            if stats["single_quad_insertion"]
            else "patch boundary preserved"
        )
        self.report(
            {"INFO"},
            f"{stats['input_count']} to {stats['output_count']}: "
            f"{stats['new_faces']} quads, {stats['poles']} transition N-poles; "
            f"{placement}",
        )
        if stats["had_uvs"]:
            self.report(
                {"WARNING"},
                "New loops use default custom-data values; unwrap the rebuilt "
                "patch if UVs matter",
            )
        return {"FINISHED"}


class QT_OT_validate_patch(Operator):
    bl_idname = "mesh.quad_transition_validate"
    bl_label = "Validate Selected Patch"
    bl_description = (
        "Check the selection and all-quad template without changing the mesh"
    )
    bl_options = {"REGISTER"}

    transition: EnumProperty(name="Transition", items=transition_items())
    axis_mode: EnumProperty(name="Patch Axis", items=AXIS_ITEMS, default="AUTO")
    flip_flow: BoolProperty(name="Reverse Flow", default=False)
    pole_side: EnumProperty(name="Pole Side", items=POLE_SIDE_ITEMS, default="CENTER")
    mirror: BoolProperty(name="Mirror", default=False)
    pole_spacing: FloatProperty(name="Pole Spacing", min=0.5, max=2.0, default=1.0)

    @classmethod
    def poll(cls, context: Any) -> bool:
        return QT_OT_apply_transition.poll(context)

    def execute(self, context: Any):
        bm = bmesh.from_edit_mesh(context.edit_object.data)
        try:
            incoming, outgoing, layout, template = _make_template_and_layout(
                bm,
                self.transition,
                self.axis_mode,
                self.flip_flow,
                self.pole_side,
                self.mirror,
                self.pole_spacing,
            )
            bind_boundary(template, layout)
        except TransitionError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        selection_label = (
            "single-quad insertion"
            if layout.single_quad_insertion
            else f"{layout.width} x {layout.height} patch"
        )
        self.report(
            {"INFO"},
            f"Valid {selection_label} for "
            f"{incoming} to {outgoing}: "
            f"{len(template.faces)} quads, {len(template.pole_keys)} N-poles",
        )
        return {"FINISHED"}


class QT_OT_toggle_subdivision_preview(Operator):
    bl_idname = "object.quad_transition_toggle_subdivision"
    bl_label = "Toggle Subdivision Preview"
    bl_description = "Create or toggle a non-destructive Catmull-Clark preview modifier"
    bl_options = {"REGISTER", "UNDO"}

    levels: IntProperty(name="Viewport Levels", min=1, max=4, default=2)

    @classmethod
    def poll(cls, context: Any) -> bool:
        return (
            context.active_object is not None and context.active_object.type == "MESH"
        )

    def execute(self, context: Any):
        obj = context.active_object
        modifier = obj.modifiers.get(PREVIEW_MODIFIER_NAME)
        if modifier is None:
            modifier = obj.modifiers.new(PREVIEW_MODIFIER_NAME, "SUBSURF")
            modifier.levels = self.levels
            modifier.render_levels = self.levels
            modifier.show_viewport = True
            state = "enabled"
        elif modifier.show_viewport:
            modifier.show_viewport = False
            state = "disabled"
        else:
            modifier.levels = self.levels
            modifier.render_levels = self.levels
            modifier.show_viewport = True
            state = "enabled"
        self.report({"INFO"}, f"Subdivision preview {state}")
        return {"FINISHED"}


CLASSES = (
    QT_OT_apply_transition,
    QT_OT_validate_patch,
    QT_OT_toggle_subdivision_preview,
)


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
