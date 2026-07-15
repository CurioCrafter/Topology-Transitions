"""Conservative Edit Mode operators for repairing selected non-quad faces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import bmesh
import bpy
from bpy.types import Operator
from mathutils import Vector

from .core import TransitionError
from .edit_mesh import restore_bmesh
from .quad_repair import Quad, best_quad_fan, quad_fan_quality


@dataclass
class RegionPlan:
    faces: tuple[Any, ...]
    cycle: tuple[Any, ...]
    quads: tuple[Quad, ...]
    internal_edges: tuple[Any, ...]
    boundary_face_counts: dict[Any, int]
    reference_normal: Vector
    material_index: int
    smooth: bool
    quality: float


def _validate_context(context: Any) -> tuple[Any, Any, Any]:
    obj = context.edit_object
    if obj is None or obj.type != "MESH" or obj.mode != "EDIT":
        raise TransitionError("Topology Repair requires a mesh in Edit Mode")
    if len(context.objects_in_mode_unique_data) != 1:
        raise TransitionError("Repair one mesh data-block at a time")
    if obj.data.shape_keys is not None:
        raise TransitionError(
            "Meshes with shape keys are not modified because topology would change"
        )
    return obj, obj.data, bmesh.from_edit_mesh(obj.data)


def _average_normal(faces: tuple[Any, ...]) -> Vector:
    normal = Vector((0.0, 0.0, 0.0))
    for face in faces:
        normal += face.normal * max(face.calc_area(), 1.0e-12)
    if normal.length_squared <= 1.0e-16:
        raise TransitionError("The selected region has no stable surface normal")
    return normal.normalized()


def _newell_normal(vertices: list[Any]) -> Vector:
    normal = Vector((0.0, 0.0, 0.0))
    for current, following in zip(vertices, (*vertices[1:], vertices[0]), strict=True):
        normal.x += (current.co.y - following.co.y) * (current.co.z + following.co.z)
        normal.y += (current.co.z - following.co.z) * (current.co.x + following.co.x)
        normal.z += (current.co.x - following.co.x) * (current.co.y + following.co.y)
    return normal


def _project_coordinates(coordinates: list[Vector], normal: Vector):
    drop_axis = max(range(3), key=lambda axis: abs(normal[axis]))
    kept = [axis for axis in range(3) if axis != drop_axis]
    return tuple(
        (coordinate[kept[0]], coordinate[kept[1]])
        for coordinate in coordinates
    )


def _region_cycle(
    faces: tuple[Any, ...],
) -> tuple[list[Any], list[Any], dict[Any, int]]:
    region = set(faces)
    boundary_edges = []
    internal_edges = []
    for edge in {edge for face in faces for edge in face.edges}:
        if len(edge.link_faces) > 2:
            raise TransitionError("Non-manifold edges are not repaired automatically")
        region_links = sum(linked in region for linked in edge.link_faces)
        if region_links == 1:
            boundary_edges.append(edge)
        elif region_links == 2:
            if len(edge.link_faces) != 2:
                raise TransitionError("The selected region touches a non-manifold edge")
            internal_edges.append(edge)
        else:
            raise TransitionError("Selected faces do not form one simple region")
    if not boundary_edges:
        raise TransitionError("Selected faces do not have an outside boundary")

    adjacency: dict[Any, list[Any]] = {}
    for edge in boundary_edges:
        first, second = edge.verts
        adjacency.setdefault(first, []).append(second)
        adjacency.setdefault(second, []).append(first)
    if any(len(neighbors) != 2 for neighbors in adjacency.values()):
        raise TransitionError("Selected faces have a branching or open boundary")

    start = min(adjacency, key=lambda vertex: vertex.index)
    cycle = [start]
    previous = None
    current = start
    while True:
        candidates = [
            neighbor for neighbor in adjacency[current] if neighbor != previous
        ]
        following = min(candidates, key=lambda vertex: vertex.index)
        if following == start:
            break
        if following in cycle:
            raise TransitionError("Selected faces have more than one boundary cycle")
        cycle.append(following)
        previous, current = current, following
    if len(cycle) != len(adjacency):
        raise TransitionError("Selected faces contain a hole or disconnected boundary")
    return cycle, internal_edges, {
        edge: len(edge.link_faces) for edge in boundary_edges
    }


def _plan_region(faces: tuple[Any, ...]) -> RegionPlan:
    if not faces or any(not face.is_valid for face in faces):
        raise TransitionError("The candidate repair region is no longer valid")
    if len({face.material_index for face in faces}) != 1:
        raise TransitionError("Adjacent repair faces must use the same material")
    if len({face.smooth for face in faces}) != 1:
        raise TransitionError("Adjacent repair faces must use the same smooth setting")
    reference_normal = _average_normal(faces)
    cycle, internal_edges, boundary_counts = _region_cycle(faces)
    if _newell_normal(cycle).dot(reference_normal) < 0.0:
        cycle.reverse()
    points = _project_coordinates([vertex.co for vertex in cycle], reference_normal)
    quads = best_quad_fan(points)
    if quads is None:
        raise TransitionError(
            "The region boundary cannot be split into clean convex quads"
        )
    return RegionPlan(
        faces=faces,
        cycle=tuple(cycle),
        quads=quads,
        internal_edges=tuple(internal_edges),
        boundary_face_counts=boundary_counts,
        reference_normal=reference_normal,
        material_index=faces[0].material_index,
        smooth=faces[0].smooth,
        quality=quad_fan_quality(points, quads),
    )


def _replace_region(bm: Any, plan: RegionPlan) -> list[Any]:
    bmesh.ops.delete(bm, geom=list(plan.faces), context="FACES_ONLY")
    dead_edges = [
        edge for edge in plan.internal_edges if edge.is_valid and not edge.link_faces
    ]
    if dead_edges:
        bmesh.ops.delete(bm, geom=dead_edges, context="EDGES")

    created = []
    for indices in plan.quads:
        face = bm.faces.new([plan.cycle[index] for index in indices])
        face.material_index = plan.material_index
        face.smooth = plan.smooth
        created.append(face)
    bm.normal_update()
    if any(
        not edge.is_valid or len(edge.link_faces) != count
        for edge, count in plan.boundary_face_counts.items()
    ):
        raise TransitionError("Repair changed connectivity outside the selected region")
    if any(
        not face.is_valid
        or len(face.verts) != 4
        or face.calc_area() <= 1.0e-12
        or face.normal.dot(plan.reference_normal) <= 1.0e-8
        for face in created
    ):
        raise TransitionError("Generated quads failed the area or winding check")
    if any(
        len(edge.link_faces) > 2
        for face in created
        for edge in face.edges
    ):
        raise TransitionError("Generated repair contains a non-manifold edge")
    return created


def _safe_plan(faces: tuple[Any, ...]) -> RegionPlan | None:
    try:
        return _plan_region(faces)
    except TransitionError:
        return None


def _pair_plans(
    faces: list[Any], first_sides: int, second_test
) -> list[RegionPlan]:
    selected = set(faces)
    plans = {}
    for face in faces:
        if len(face.verts) != first_sides:
            continue
        for edge in face.edges:
            if len(edge.link_faces) != 2:
                continue
            other = next(linked for linked in edge.link_faces if linked != face)
            if other not in selected or not second_test(other):
                continue
            key = tuple(sorted((face.index, other.index)))
            if key not in plans:
                plan = _safe_plan((face, other))
                if plan is not None:
                    plans[key] = plan
    return sorted(
        plans.values(),
        key=lambda plan: (-plan.quality, min(face.index for face in plan.faces)),
    )


def _apply_disjoint_plans(
    bm: Any, plans: list[RegionPlan], used: set[Any]
) -> tuple[list[Any], int]:
    created = []
    applied = 0
    for plan in plans:
        if any(face in used or not face.is_valid for face in plan.faces):
            continue
        created.extend(_replace_region(bm, plan))
        used.update(plan.faces)
        applied += 1
    return created, applied


def _boundary_grid(bm: Any, face: Any) -> list[Any]:
    if any(len(edge.link_faces) != 1 for edge in face.edges):
        raise TransitionError("Center-grid fallback is limited to mesh-boundary faces")
    loops = list(face.loops)
    original_vertices = [loop.vert for loop in loops]
    original_edges = [loop.edge for loop in loops]
    reference_normal = face.normal.copy()
    if reference_normal.length_squared <= 1.0e-16:
        raise TransitionError("Boundary face has no stable normal")
    reference_normal.normalize()
    center_coordinate = sum(
        (vertex.co for vertex in original_vertices), Vector((0.0, 0.0, 0.0))
    ) / len(original_vertices)
    midpoint_coordinates = [
        (vertex.co + original_vertices[(index + 1) % len(original_vertices)].co)
        * 0.5
        for index, vertex in enumerate(original_vertices)
    ]
    projected = _project_coordinates(
        [
            coordinate
            for index, vertex in enumerate(original_vertices)
            for coordinate in (
                vertex.co,
                midpoint_coordinates[index],
                center_coordinate,
                midpoint_coordinates[index - 1],
            )
        ],
        reference_normal,
    )
    for offset in range(0, len(projected), 4):
        if best_quad_fan(projected[offset : offset + 4]) is None:
            raise TransitionError(
                "Boundary face is too concave for the center-grid fallback"
            )

    material_index = face.material_index
    smooth = face.smooth
    midpoints = []
    for edge, origin in zip(original_edges, original_vertices, strict=True):
        _new_edge, midpoint = bmesh.utils.edge_split(edge, origin, 0.5)
        midpoints.append(midpoint)
    bmesh.ops.delete(bm, geom=[face], context="FACES_ONLY")
    center = bm.verts.new(center_coordinate)
    created = []
    for index, vertex in enumerate(original_vertices):
        vertices = [vertex, midpoints[index], center, midpoints[index - 1]]
        normal = (vertices[1].co - vertices[0].co).cross(
            vertices[2].co - vertices[0].co
        ) + (vertices[2].co - vertices[0].co).cross(
            vertices[3].co - vertices[0].co
        )
        if normal.dot(reference_normal) < 0.0:
            vertices.reverse()
        quad = bm.faces.new(vertices)
        quad.material_index = material_index
        quad.smooth = smooth
        created.append(quad)
    bm.normal_update()
    if any(
        face.calc_area() <= 1.0e-12 or face.normal.dot(reference_normal) <= 1.0e-8
        for face in created
    ):
        raise TransitionError("Boundary center-grid fallback generated a bad quad")
    return created


def solve_selected(context: Any, target: str) -> dict[str, Any]:
    _obj, mesh, bm = _validate_context(context)
    bm.faces.ensure_lookup_table()
    bm.faces.index_update()
    bm.normal_update()
    selected = [face for face in bm.faces if face.select and not face.hide]
    predicate = (
        (lambda face: len(face.verts) == 3)
        if target == "TRIS"
        else (lambda face: len(face.verts) > 4)
    )
    target_faces = [face for face in selected if predicate(face)]
    if not target_faces:
        noun = "triangles" if target == "TRIS" else "n-gons"
        raise TransitionError(f"Select at least one {noun[:-1]} face to repair")

    backup = bm.copy()
    created: list[Any] = []
    used: set[Any] = set()
    methods = {
        "triangle_pairs": 0,
        "even_ngons": 0,
        "mixed_pairs": 0,
        "boundary_grids": 0,
    }
    try:
        if target == "TRIS":
            pair_plans = _pair_plans(selected, 3, lambda face: len(face.verts) == 3)
            new_faces, count = _apply_disjoint_plans(bm, pair_plans, used)
            created.extend(new_faces)
            methods["triangle_pairs"] += count
        else:
            for face in list(target_faces):
                if not face.is_valid or len(face.verts) % 2:
                    continue
                plan = _safe_plan((face,))
                if plan is not None:
                    created.extend(_replace_region(bm, plan))
                    used.add(face)
                    methods["even_ngons"] += 1

        remaining_selected = [
            face for face in selected if face.is_valid and face not in used
        ]
        mixed_plans = _pair_plans(
            remaining_selected,
            3,
            lambda face: len(face.verts) > 4 and len(face.verts) % 2 == 1,
        )
        new_faces, count = _apply_disjoint_plans(bm, mixed_plans, used)
        created.extend(new_faces)
        methods["mixed_pairs"] += count

        for face in target_faces:
            if face in used or not face.is_valid or not all(
                len(edge.link_faces) == 1 for edge in face.edges
            ):
                continue
            created.extend(_boundary_grid(bm, face))
            used.add(face)
            methods["boundary_grids"] += 1

        remaining = [
            face for face in target_faces if face.is_valid and face not in used
        ]
        solved_target = len(target_faces) - len(remaining)
        if solved_target == 0:
            noun = "triangles" if target == "TRIS" else "n-gons"
            raise TransitionError(
                f"No selected {noun} could be repaired safely; select an adjacent "
                "compatible face pair or an isolated mesh-boundary face"
            )

        for vertex in bm.verts:
            vertex.select_set(False)
        for edge in bm.edges:
            edge.select_set(False)
        for face in bm.faces:
            face.select_set(False)
        for face in remaining or created:
            if face.is_valid:
                face.select_set(True)
        bm.select_mode = {"FACE"}
        context.tool_settings.mesh_select_mode = (False, False, True)
        bm.select_flush_mode()
        bmesh.update_edit_mesh(mesh, loop_triangles=True, destructive=True)
    except Exception:
        restore_bmesh(mesh, bm, backup)
        raise
    finally:
        backup.free()

    return {
        "target": target,
        "selected": len(target_faces),
        "solved": solved_target,
        "remaining": len(remaining),
        "created_quads": len(created),
        "methods": methods,
        "had_uvs": bool(mesh.uv_layers),
    }


class QT_OT_solve_selected_tris(Operator):
    bl_idname = "mesh.quad_transition_solve_selected_tris"
    bl_label = "Solve Selected Tris"
    bl_description = (
        "Try adjacent-triangle, triangle-plus-odd-n-gon, and boundary-grid repairs"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: Any) -> bool:
        return (
            context.edit_object is not None
            and context.edit_object.type == "MESH"
            and context.edit_object.mode == "EDIT"
        )

    def execute(self, context: Any):
        return _execute_repair(self, context, "TRIS")


class QT_OT_solve_selected_ngons(Operator):
    bl_idname = "mesh.quad_transition_solve_selected_ngons"
    bl_label = "Solve Selected N-gons"
    bl_description = (
        "Try even quad fans, odd-n-gon-plus-triangle, and boundary-grid repairs"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: Any) -> bool:
        return QT_OT_solve_selected_tris.poll(context)

    def execute(self, context: Any):
        return _execute_repair(self, context, "NGONS")


def _execute_repair(operator: Operator, context: Any, target: str):
    try:
        stats = solve_selected(context, target)
    except TransitionError as exc:
        operator.report({"ERROR"}, str(exc))
        return {"CANCELLED"}
    except Exception as exc:
        operator.report({"ERROR"}, f"Repair failed and was rolled back: {exc}")
        return {"CANCELLED"}

    noun = "triangles" if target == "TRIS" else "n-gons"
    level = {"WARNING"} if stats["remaining"] else {"INFO"}
    operator.report(
        level,
        f"Solved {stats['solved']}/{stats['selected']} selected {noun} into "
        f"{stats['created_quads']} quads; {stats['remaining']} left selected",
    )
    if stats["had_uvs"]:
        operator.report(
            {"WARNING"},
            "New loops use default custom-data values; unwrap repaired faces if needed",
        )
    return {"FINISHED"}


CLASSES = (QT_OT_solve_selected_tris, QT_OT_solve_selected_ngons)


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
