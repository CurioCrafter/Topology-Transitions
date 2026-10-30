"""Edit Mode operators for repairing selected non-quad faces.

Fast local rewrites are preferred.  When an isolated non-quad cannot be paired,
the solver propagates its required edge splits through complete opposite-edge
paths in the surrounding quad mesh.  Rebuilding every touched quad keeps the
problem from being pushed into a neighbouring face.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

import bmesh
import bpy
from bpy.props import BoolProperty, IntProperty
from bpy.types import Operator
from mathutils import Vector

from .core import TransitionError
from .edit_mesh import restore_bmesh
from .manifold import ManifoldReport, analyze_manifold, boundary_components
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


@dataclass(frozen=True)
class PropagatedFace:
    face: Any
    vertices: tuple[Any, ...]
    edges: tuple[Any, ...]
    reference_normal: Vector
    material_index: int
    smooth: bool
    hidden: bool
    target: bool


MAX_PROPAGATED_FACES = 10000


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
        (coordinate[kept[0]], coordinate[kept[1]]) for coordinate in coordinates
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
    return (
        cycle,
        internal_edges,
        {edge: len(edge.link_faces) for edge in boundary_edges},
    )


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
    if any(len(edge.link_faces) > 2 for face in created for edge in face.edges):
        raise TransitionError("Generated repair contains a non-manifold edge")
    return created


def _safe_plan(faces: tuple[Any, ...]) -> RegionPlan | None:
    try:
        return _plan_region(faces)
    except TransitionError:
        return None


def _pair_plans(
    faces: list[Any], first_sides: int, second_test, *, selected_only: bool = False
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
            if (
                other.hide
                or (selected_only and other not in selected)
                or not second_test(other)
            ):
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


def _opposite_edge(face: Any, edge: Any) -> Any:
    edges = tuple(loop.edge for loop in face.loops)
    try:
        index = edges.index(edge)
    except ValueError as exc:
        raise TransitionError("Quad propagation lost an incident edge") from exc
    return edges[(index + 2) % 4]


def _propagation_faces(
    seeds: list[Any],
    *,
    maximum_faces: int = MAX_PROPAGATED_FACES,
    allow_hidden: bool = False,
) -> tuple[list[PropagatedFace], set[Any]]:
    """Trace all edge splits needed to absorb ``seeds`` into an all-quad mesh."""

    targets = set(seeds)
    marked_edges = {edge for face in seeds for edge in face.edges}
    queue = sorted(marked_edges, key=lambda edge: edge.index, reverse=True)
    touched_quads: set[Any] = set()
    visited_edges: set[Any] = set()

    while queue:
        edge = queue.pop()
        if edge in visited_edges:
            continue
        visited_edges.add(edge)
        if not edge.is_valid or len(edge.link_faces) > 2:
            raise TransitionError(
                "Propagated repair reached an invalid or non-manifold edge"
            )
        for face in tuple(edge.link_faces):
            if face in targets:
                continue
            if face.hide and not allow_hidden:
                raise TransitionError(
                    "Propagated repair reached hidden geometry; reveal it first"
                )
            sides = len(face.verts)
            if sides == 4:
                touched_quads.add(face)
                opposite = _opposite_edge(face, edge)
                if opposite not in marked_edges:
                    marked_edges.add(opposite)
                    queue.append(opposite)
            else:
                targets.add(face)
                for boundary_edge in face.edges:
                    if boundary_edge not in marked_edges:
                        marked_edges.add(boundary_edge)
                        queue.append(boundary_edge)
            if len(targets) + len(touched_quads) > maximum_faces:
                raise TransitionError(
                    f"Repair would affect more than {maximum_faces:,} faces; "
                    "select a smaller topology region"
                )

    affected = targets | touched_quads
    records = []
    for face in sorted(affected, key=lambda item: item.index):
        if not face.is_valid:
            raise TransitionError("A propagated repair face became invalid")
        loops = tuple(face.loops)
        normal = face.normal.copy()
        if normal.length_squared <= 1.0e-16:
            raise TransitionError("A propagated repair face has no stable normal")
        normal.normalize()
        records.append(
            PropagatedFace(
                face=face,
                vertices=tuple(loop.vert for loop in loops),
                edges=tuple(loop.edge for loop in loops),
                reference_normal=normal,
                material_index=face.material_index,
                smooth=face.smooth,
                hidden=face.hide,
                target=face in targets,
            )
        )

    for record in records:
        marked_count = sum(edge in marked_edges for edge in record.edges)
        if record.target and marked_count != len(record.edges):
            raise TransitionError("A target non-quad was not split on every side")
        if not record.target:
            if marked_count not in {2, 4}:
                raise TransitionError(
                    "Quad propagation produced an adjacent rather than opposite split"
                )
            if marked_count == 2:
                indices = [
                    index
                    for index, edge in enumerate(record.edges)
                    if edge in marked_edges
                ]
                if (indices[1] - indices[0]) % 4 != 2:
                    raise TransitionError(
                        "Quad propagation produced two non-opposite splits"
                    )
    return records, marked_edges


def _oriented_face_vertices(vertices: list[Any], reference_normal: Vector) -> list[Any]:
    normal = (vertices[1].co - vertices[0].co).cross(
        vertices[2].co - vertices[0].co
    ) + (vertices[2].co - vertices[0].co).cross(vertices[3].co - vertices[0].co)
    return list(reversed(vertices)) if normal.dot(reference_normal) < 0.0 else vertices


def _cycle_between(values: list[Any], start: int, end: int) -> list[Any]:
    result = [values[start]]
    index = start
    while index != end:
        index = (index + 1) % len(values)
        result.append(values[index])
    return result


def _propagated_grid(
    bm: Any,
    seeds: list[Any],
    *,
    maximum_faces: int = MAX_PROPAGATED_FACES,
    allow_hidden: bool = False,
) -> tuple[list[Any], int, int]:
    """Repair arbitrary visible non-quads by carrying splits through quad rings."""

    records, marked_edges = _propagation_faces(
        seeds,
        maximum_faces=maximum_faces,
        allow_hidden=allow_hidden,
    )
    midpoint_by_edge = {}
    for edge in sorted(marked_edges, key=lambda item: item.index):
        if not edge.is_valid:
            raise TransitionError("A propagated split edge became invalid")
        _new_edge, midpoint = bmesh.utils.edge_split(edge, edge.verts[0], 0.5)
        midpoint_by_edge[edge] = midpoint

    bmesh.ops.delete(
        bm,
        geom=[record.face for record in records],
        context="FACES_ONLY",
    )
    created = []
    created_normals = []
    for record in records:
        marked_indices = [
            index for index, edge in enumerate(record.edges) if edge in midpoint_by_edge
        ]
        face_vertex_sets: list[list[Any]]
        if record.target or len(marked_indices) == 4:
            center_coordinate = sum(
                (vertex.co for vertex in record.vertices),
                Vector((0.0, 0.0, 0.0)),
            ) / len(record.vertices)
            center = bm.verts.new(center_coordinate)
            face_vertex_sets = [
                [
                    vertex,
                    midpoint_by_edge[record.edges[index]],
                    center,
                    midpoint_by_edge[record.edges[index - 1]],
                ]
                for index, vertex in enumerate(record.vertices)
            ]
        else:
            expanded: list[Any] = []
            midpoint_positions = []
            for index, vertex in enumerate(record.vertices):
                expanded.append(vertex)
                edge = record.edges[index]
                if edge in midpoint_by_edge:
                    expanded.append(midpoint_by_edge[edge])
                    midpoint_positions.append(len(expanded) - 1)
            first, second = midpoint_positions
            face_vertex_sets = [
                _cycle_between(expanded, first, second),
                _cycle_between(expanded, second, first),
            ]

        for vertices in face_vertex_sets:
            if len(vertices) != 4:
                raise TransitionError(
                    "Propagated repair did not reconstruct a four-sided face"
                )
            quad = bm.faces.new(
                _oriented_face_vertices(vertices, record.reference_normal)
            )
            quad.material_index = record.material_index
            quad.smooth = record.smooth
            quad.hide_set(record.hidden)
            created.append(quad)
            created_normals.append((quad, record.reference_normal))

    bm.normal_update()
    if any(
        not face.is_valid
        or len(face.verts) != 4
        or face.calc_area() <= 1.0e-12
        or face.normal.dot(reference_normal) <= 1.0e-8
        for face, reference_normal in created_normals
    ):
        raise TransitionError(
            "Propagated repair generated a zero-area or non-quad face"
        )
    if any(len(edge.link_faces) > 2 for face in created for edge in face.edges):
        raise TransitionError("Propagated repair generated a non-manifold edge")
    return (
        created,
        sum(not record.target for record in records),
        len(marked_edges),
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
        (vertex.co + original_vertices[(index + 1) % len(original_vertices)].co) * 0.5
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
        ) + (vertices[2].co - vertices[0].co).cross(vertices[3].co - vertices[0].co)
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


def _analyze_bmesh(bm: Any) -> ManifoldReport:
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.verts.index_update()
    bm.edges.index_update()
    bm.faces.index_update()
    return analyze_manifold(
        {edge.index: (edge.verts[0].index, edge.verts[1].index) for edge in bm.edges},
        {edge.index: len(edge.link_faces) for edge in bm.edges},
        (vertex.index for vertex in bm.verts),
        {face.index: tuple(vertex.index for vertex in face.verts) for face in bm.faces},
    )


def _boundary_components_from_bmesh(bm: Any):
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.verts.index_update()
    bm.edges.index_update()
    edge_vertices = {
        edge.index: (edge.verts[0].index, edge.verts[1].index) for edge in bm.edges
    }
    return boundary_components(
        edge_vertices,
        (edge.index for edge in bm.edges if len(edge.link_faces) == 1),
    )


def _remove_overconnected_duplicate_faces(bm: Any) -> int:
    """Remove only exact duplicate faces that cause over-connected edges."""

    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.verts.index_update()
    bm.faces.index_update()
    groups: dict[tuple[int, ...], list[Any]] = {}
    for face in bm.faces:
        key = tuple(sorted(vertex.index for vertex in face.verts))
        groups.setdefault(key, []).append(face)

    duplicates = []
    for faces in groups.values():
        if len(faces) < 2 or not any(
            len(edge.link_faces) > 2 for face in faces for edge in face.edges
        ):
            continue
        ordered = sorted(faces, key=lambda face: face.index)
        duplicates.extend(ordered[1:])
    if duplicates:
        bmesh.ops.delete(bm, geom=duplicates, context="FACES_ONLY")
    return len(duplicates)


SEAM_WELD_FRACTION = 1.0e-6


def _seam_tolerance(bm: Any) -> float:
    points = [vertex.co for vertex in bm.verts]
    size = max(
        max(point[axis] for point in points) - min(point[axis] for point in points)
        for axis in range(3)
    )
    return max(size * SEAM_WELD_FRACTION, 1.0e-9)


def split_seam_groups(bm: Any) -> list[list[Any]]:
    """Open-boundary vertices that sit on each other, grouped by position.

    Exporters (glTF, many AI scan tools) split vertices along UV seams, so
    the mesh looks closed but every seam is a pair of open edges. Welding
    those vertices is the right repair; capping them would add faces.
    """

    boundary = {
        vertex
        for edge in bm.edges
        if len(edge.link_faces) == 1
        for vertex in edge.verts
    }
    if not boundary:
        return []
    tolerance = _seam_tolerance(bm)
    buckets: dict[tuple[int, ...], list[Any]] = {}
    for vertex in boundary:
        key = tuple(round(value / tolerance) for value in vertex.co)
        buckets.setdefault(key, []).append(vertex)
    return [group for group in buckets.values() if len(group) > 1]


def _weld_split_seams(bm: Any) -> int:
    """Weld split seams, skipping any group whose weld would make an edge
    with more than two faces (folded or doubled scan geometry)."""

    groups = split_seam_groups(bm)
    target = {vertex: group[0] for group in groups for vertex in group[1:]}
    for _attempt in range(4):
        faces = {face for vertex in target for face in vertex.link_faces}
        counts: Counter = Counter()
        for face in faces:
            for edge in face.edges:
                counts[frozenset(target.get(v, v) for v in edge.verts)] += 1
        bad = {vertex for key, count in counts.items() if count > 2 for vertex in key}
        unsafe = [
            group
            for group in groups
            if group[0] in bad or any(target.get(v) in bad for v in group[1:])
        ]
        if not unsafe:
            break
        for group in unsafe:
            for vertex in group[1:]:
                target.pop(vertex, None)
        groups = [group for group in groups if group not in unsafe]
    else:
        return 0
    if target:
        bmesh.ops.weld_verts(bm, targetmap=target)
    return len(target)


def _remove_loose_geometry(bm: Any) -> tuple[int, int]:
    wire_edges = [edge for edge in bm.edges if not edge.link_faces]
    if wire_edges:
        bmesh.ops.delete(bm, geom=wire_edges, context="EDGES")
    isolated_vertices = [vertex for vertex in bm.verts if not vertex.link_edges]
    if isolated_vertices:
        bmesh.ops.delete(bm, geom=isolated_vertices, context="VERTS")
    return len(wire_edges), len(isolated_vertices)


def _boundary_cycle_vertices(bm: Any, component: Any) -> list[Any]:
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    vertices = [bm.verts[index] for index in component.vertex_ids]
    first_edge = bm.edges[component.edge_ids[0]]
    adjacent_face = first_edge.link_faces[0]
    adjacent_loop = next(
        loop for loop in adjacent_face.loops if loop.edge == first_edge
    )
    adjacent_direction = (
        adjacent_loop.vert.index,
        adjacent_loop.link_loop_next.vert.index,
    )
    cycle_direction = (vertices[0].index, vertices[1].index)
    if cycle_direction == adjacent_direction:
        vertices.reverse()
    return vertices


def _close_boundary_component(
    bm: Any,
    component: Any,
    *,
    prefer_minimal: bool,
    maximum_faces: int,
) -> dict[str, int | str]:
    if not component.closed or len(component.vertex_ids) < 3:
        raise TransitionError("Boundary component is not one simple closed loop")
    edges = [bm.edges[index] for index in component.edge_ids]
    if any(not edge.is_valid or len(edge.link_faces) != 1 for edge in edges):
        raise TransitionError("Boundary loop changed before it could be repaired")
    neighboring_faces = {edge.link_faces[0] for edge in edges}
    if len(neighboring_faces) == 1:
        raise TransitionError(
            "A one-face open sheet cannot be capped without duplicating that face"
        )

    cycle = _boundary_cycle_vertices(bm, component)
    styles = Counter(
        (face.material_index, face.smooth)
        for face in sorted(neighboring_faces, key=lambda item: item.index)
    )
    style = min(styles, key=lambda item: (-styles[item], item[0], item[1]))
    cap = bm.faces.new(cycle)
    cap.material_index, cap.smooth = style
    # Only the cap and its rim vertices changed; a whole-mesh normal update
    # here dominated hole filling on dense scans.
    cap.normal_update()
    for vertex in cap.verts:
        vertex.normal_update()

    side_count = len(cycle)
    if prefer_minimal and side_count % 2 == 0:
        plan = _safe_plan((cap,))
        if plan is not None:
            created = _replace_region(bm, plan)
            return {
                "strategy": "minimal_fan",
                "target_quads": len(created),
                "created_quads": len(created),
                "propagated_faces": 0,
                "propagated_edges": 0,
            }

    created, propagated_faces, propagated_edges = _propagated_grid(
        bm,
        [cap],
        maximum_faces=maximum_faces,
        allow_hidden=True,
    )
    return {
        "strategy": "center_grid",
        "target_quads": side_count,
        "created_quads": len(created),
        "propagated_faces": propagated_faces,
        "propagated_edges": propagated_edges,
    }


def _select_manifold_report(bm: Any, report: ManifoldReport) -> None:
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    for face in bm.faces:
        face.select_set(False)
    for edge in bm.edges:
        edge.select_set(False)
    for vertex in bm.verts:
        vertex.select_set(False)
    for edge_id in report.issue_edge_ids:
        edge = bm.edges[edge_id]
        edge.select_set(True)
        for vertex in edge.verts:
            vertex.select_set(True)
    for vertex_id in report.issue_vertex_ids:
        bm.verts[vertex_id].select_set(True)
    bm.select_mode = {"VERT", "EDGE"}
    bm.select_flush_mode()


def _update_auto_manifold_settings(
    settings: Any, obj: Any, report: ManifoldReport
) -> None:
    settings.manifold_object_name = obj.name
    settings.manifold_component_count = len(report.components)
    settings.manifold_component_index = 0
    settings.manifold_open_edge_count = len(report.open_boundary_edges)
    settings.manifold_nonmanifold_edge_count = len(report.nonmanifold_edges)
    settings.manifold_wire_edge_count = len(report.wire_edges)
    settings.manifold_winding_edge_count = len(report.inconsistent_winding_edges)
    settings.manifold_isolated_vertex_count = len(report.isolated_vertices)
    settings.manifold_disconnected_fan_count = len(report.disconnected_fan_vertices)
    settings.manifold_issue_count = len(report.issue_edge_ids) + len(
        report.issue_vertex_ids
    )
    settings.manifold_current_kind = (
        "Auto Repair: Topologically Manifold"
        if report.clean
        else f"Auto Repair: {len(report.components)} unresolved areas"
    )


def _auto_repair_context(context: Any) -> tuple[Any, Any, Any | None, Any, bool]:
    edit_mode = context.mode == "EDIT_MESH"
    obj = context.edit_object if edit_mode else context.active_object
    if obj is None or obj.type != "MESH" or obj.mode not in {"OBJECT", "EDIT"}:
        raise TransitionError(
            "Auto Repair requires one active mesh in Object or Edit Mode"
        )
    if edit_mode and len(context.objects_in_mode_unique_data) != 1:
        raise TransitionError("Auto Repair handles one mesh data-block at a time")
    if edit_mode and obj.data.users > 1:
        raise TransitionError(
            "Linked mesh data must be repaired in Object Mode so the active "
            "object can be made single-user safely"
        )
    if obj.data.shape_keys is not None:
        raise TransitionError(
            "Meshes with shape keys are not modified because topology would change"
        )

    mesh = obj.data
    live_bm = bmesh.from_edit_mesh(mesh) if edit_mode else None
    if live_bm is not None:
        working = live_bm.copy()
    else:
        working = bmesh.new()
        working.from_mesh(mesh)
    return obj, mesh, live_bm, working, edit_mode


def solve_all_nonmanifold(
    context: Any,
    *,
    prefer_minimal: bool = True,
    remove_loose: bool = True,
    remove_duplicate_faces: bool = True,
    maximum_faces: int = MAX_PROPAGATED_FACES,
    weld_split_seams: bool = True,
) -> dict[str, Any]:
    """Repair every safely solvable manifold defect on the active mesh.

    Simple closed boundary loops are capped with the fewest clean quads possible.
    Odd loops use a center grid; a triangular hole therefore becomes exactly three
    target quads while its three parity cuts continue through neighboring flows.
    Ambiguous over-connected sheets and branched boundaries remain selected.
    """

    obj, mesh, live_bm, working, edit_mode = _auto_repair_context(context)
    stats: dict[str, Any] = {
        "boundary_areas": 0,
        "closed_boundaries": 0,
        "failed_boundaries": 0,
        "triangle_boundaries": 0,
        "minimal_fans": 0,
        "center_grids": 0,
        "target_quads": 0,
        "created_quads": 0,
        "propagated_faces": 0,
        "propagated_edges": 0,
        "duplicate_faces_removed": 0,
        "wire_edges_removed": 0,
        "isolated_vertices_removed": 0,
        "seam_vertices_welded": 0,
        "failures": [],
        "made_single_user": False,
    }
    try:
        if weld_split_seams:
            # First, so seams are welded instead of capped with extra faces.
            stats["seam_vertices_welded"] = _weld_split_seams(working)
        if remove_duplicate_faces:
            stats["duplicate_faces_removed"] = _remove_overconnected_duplicate_faces(
                working
            )
        if remove_loose:
            wire_count, isolated_count = _remove_loose_geometry(working)
            stats["wire_edges_removed"] = wire_count
            stats["isolated_vertices_removed"] = isolated_count

        initial_components = _boundary_components_from_bmesh(working)
        stats["boundary_areas"] = len(initial_components)
        # Open and branched areas cannot be capped, so they are reported
        # straight from the first scan. The boundary scan is redone only after
        # a successful fill changed the mesh, which keeps dense scans with
        # hundreds of open areas fast.
        latest = initial_components
        stale = False
        for initial in initial_components:
            if not initial.closed:
                stats["failed_boundaries"] += 1
                stats["failures"].append("branched or open boundary component")
                continue
            anchor = initial.vertex_ids[0]
            if stale:
                latest = _boundary_components_from_bmesh(working)
                stale = False
            current = next(
                (component for component in latest if anchor in component.vertex_ids),
                None,
            )
            if current is None:
                continue
            if not current.closed:
                stats["failed_boundaries"] += 1
                stats["failures"].append("branched or open boundary component")
                continue

            # A copy keeps element order, so the scan's indices hold for it.
            trial = working.copy()
            trial.verts.ensure_lookup_table()
            trial.edges.ensure_lookup_table()
            trial.verts.index_update()
            trial.edges.index_update()
            try:
                result = _close_boundary_component(
                    trial,
                    current,
                    prefer_minimal=prefer_minimal,
                    maximum_faces=maximum_faces,
                )
            except (TransitionError, RuntimeError, ValueError, StopIteration) as exc:
                trial.free()
                stats["failed_boundaries"] += 1
                stats["failures"].append(str(exc))
                continue

            working.free()
            working = trial
            stale = True
            stats["closed_boundaries"] += 1
            stats["triangle_boundaries"] += len(current.vertex_ids) == 3
            stats["minimal_fans"] += result["strategy"] == "minimal_fan"
            stats["center_grids"] += result["strategy"] == "center_grid"
            for name in (
                "target_quads",
                "created_quads",
                "propagated_faces",
                "propagated_edges",
            ):
                stats[name] += result[name]

        working.normal_update()
        report = _analyze_bmesh(working)
        _select_manifold_report(working, report)
        if edit_mode:
            context.tool_settings.mesh_select_mode = (True, True, False)
            restore_bmesh(mesh, live_bm, working)
        else:
            if mesh.users > 1:
                output_mesh = mesh.copy()
                try:
                    working.to_mesh(output_mesh)
                    output_mesh.update()
                    obj.data = output_mesh
                except Exception:
                    bpy.data.meshes.remove(output_mesh)
                    raise
                mesh = output_mesh
                stats["made_single_user"] = True
            else:
                working.to_mesh(mesh)
                mesh.update()
        _update_auto_manifold_settings(
            context.scene.topology_transitions,
            obj,
            report,
        )
        stats["report"] = report
        stats["had_custom_data"] = (
            bool(mesh.uv_layers) or len(getattr(mesh, "color_attributes", ())) > 0
        )
        return stats
    finally:
        working.free()


def solve_selected(
    context: Any, target: str, *, propagate: bool = True
) -> dict[str, Any]:
    """Repair the selected tris or n-gons; ``propagate`` allows carrying
    splits through surrounding quad rings when no local repair exists."""

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
        "propagated_grids": 0,
    }
    propagated_quad_faces = 0
    propagated_edges = 0
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
            if (
                face in used
                or not face.is_valid
                or not all(len(edge.link_faces) == 1 for edge in face.edges)
            ):
                continue
            created.extend(_boundary_grid(bm, face))
            used.add(face)
            methods["boundary_grids"] += 1

        remaining = [
            face for face in target_faces if face.is_valid and face not in used
        ]
        if remaining and propagate:
            new_faces, affected_quads, split_edges = _propagated_grid(bm, remaining)
            created.extend(new_faces)
            used.update(remaining)
            methods["propagated_grids"] += len(remaining)
            propagated_quad_faces += affected_quads
            propagated_edges += split_edges
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
        "propagated_quad_faces": propagated_quad_faces,
        "propagated_edges": propagated_edges,
        "had_uvs": bool(mesh.uv_layers),
    }


class QT_OT_solve_all_nonmanifold(Operator):
    bl_idname = "mesh.quad_transition_solve_all_nonmanifold"
    bl_label = "Auto Solve All Non-Manifold Areas"
    bl_description = (
        "Close every simple open boundary with quads, clean exact duplicate and "
        "loose geometry, and select ambiguous defects that remain"
    )
    bl_options = {"REGISTER", "UNDO"}

    prefer_minimal: BoolProperty(
        name="Prefer Quickest Quad Path",
        description=(
            "Use a minimum-face quad fan for clean even loops; odd loops use "
            "center grids with propagated parity cuts"
        ),
        default=True,
    )
    remove_loose: BoolProperty(
        name="Remove Loose Geometry",
        description=(
            "Delete wire edges and isolated vertices that cannot form a surface"
        ),
        default=True,
    )
    remove_duplicate_faces: BoolProperty(
        name="Remove Exact Duplicate Faces",
        description=(
            "Remove exact coincident duplicates only when they create "
            "over-connected edges"
        ),
        default=True,
    )
    weld_split_seams: BoolProperty(
        name="Weld Split Seams",
        description=(
            "Merge open-boundary vertices that sit exactly on each other (seams "
            "split by exporters and AI scans) before closing real holes"
        ),
        default=True,
    )
    maximum_faces: IntProperty(
        name="Maximum Propagated Faces",
        description="Safety limit for quad-flow cuts created by one boundary repair",
        min=1,
        max=100000,
        default=MAX_PROPAGATED_FACES,
    )

    @classmethod
    def poll(cls, context: Any) -> bool:
        obj = (
            context.edit_object
            if context.mode == "EDIT_MESH"
            else context.active_object
        )
        return obj is not None and obj.type == "MESH" and obj.mode in {"OBJECT", "EDIT"}

    def execute(self, context: Any):
        try:
            stats = solve_all_nonmanifold(
                context,
                prefer_minimal=self.prefer_minimal,
                remove_loose=self.remove_loose,
                remove_duplicate_faces=self.remove_duplicate_faces,
                maximum_faces=self.maximum_faces,
                weld_split_seams=self.weld_split_seams,
            )
        except TransitionError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:
            self.report(
                {"ERROR"},
                f"Auto repair failed without changing the mesh: {exc}",
            )
            return {"CANCELLED"}

        report = stats["report"]
        cleanup_count = (
            stats["duplicate_faces_removed"]
            + stats["wire_edges_removed"]
            + stats["isolated_vertices_removed"]
        )
        summary = (
            f"Closed {stats['closed_boundaries']}/{stats['boundary_areas']} areas "
            f"with {stats['target_quads']} target quads; cleaned {cleanup_count} "
            "loose/duplicate elements"
        )
        if stats["seam_vertices_welded"]:
            summary = (
                f"Welded {stats['seam_vertices_welded']} split-seam vertices; "
                + summary[0].lower()
                + summary[1:]
            )
        if stats["made_single_user"]:
            summary += "; made the active linked mesh single-user"
        if report.clean:
            self.report({"INFO"}, f"{summary}. Mesh is topologically manifold")
        else:
            self.report(
                {"WARNING"},
                f"{summary}; {len(report.components)} ambiguous areas remain selected",
            )
        if stats["triangle_boundaries"]:
            self.report(
                {"INFO"},
                f"Converted {stats['triangle_boundaries']} triangular boundaries "
                "into three-quad center flows",
            )
        if stats["had_custom_data"] and (
            stats["closed_boundaries"] or stats["duplicate_faces_removed"]
        ):
            self.report(
                {"WARNING"},
                "New topology uses default loop data; inspect UV and color attributes",
            )
        return {"FINISHED"}


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
        f"{stats['created_quads']} quads; {stats['remaining']} left selected"
        + (
            f" (carried {stats['propagated_edges']} splits through "
            f"{stats['propagated_quad_faces']} surrounding quads)"
            if stats["propagated_edges"]
            else ""
        ),
    )
    if stats["had_uvs"]:
        operator.report(
            {"WARNING"},
            "New loops use default custom-data values; unwrap repaired faces if needed",
        )
    return {"FINISHED"}


CLASSES = (
    QT_OT_solve_all_nonmanifold,
    QT_OT_solve_selected_tris,
    QT_OT_solve_selected_ngons,
)


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
