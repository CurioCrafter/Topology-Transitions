"""Direction-aware merge-by-distance operators for selected mesh vertices."""

from __future__ import annotations

from typing import Any

import bmesh
import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty
from bpy.types import Operator
from mathutils import Vector
from mathutils.kdtree import KDTree

STRATEGY_ITEMS = (
    (
        "RADIAL_OUTER",
        "Center: Keep Outer",
        "Keep the vertex farther from the object's world-space center",
    ),
    (
        "RADIAL_INNER",
        "Center: Keep Inner",
        "Keep the vertex nearer the object's world-space center",
    ),
    (
        "NORMAL_OUTER",
        "Face Normals: Keep Outside",
        "Keep the vertex farther along the surrounding oriented face normals",
    ),
    (
        "NORMAL_INNER",
        "Face Normals: Keep Inside",
        "Keep the vertex farther opposite the surrounding oriented face normals",
    ),
    (
        "VIEW_TOWARD",
        "View: Keep Toward You",
        "Keep the vertex closest to the current 3D viewpoint",
    ),
    (
        "VIEW_AWAY",
        "View: Keep Away From You",
        "Keep the deeper vertex along the current viewing direction",
    ),
)

SCOPE_ITEMS = (
    (
        "BOUNDARY",
        "Selected Boundary / Loose",
        "Use selected vertices on open boundaries, wire edges, or isolated points",
    ),
    (
        "ALL_SELECTED",
        "All Selected",
        "Use every selected vertex within the distance",
    ),
)


class DirectionalMergeError(ValueError):
    pass


def _is_boundary_or_loose(vertex: Any) -> bool:
    return not vertex.link_edges or any(
        len(edge.link_faces) < 2 for edge in vertex.link_edges
    )


def _world_normal(obj: Any, normal: Vector) -> Vector:
    matrix = obj.matrix_world.to_3x3().inverted_safe().transposed()
    value = matrix @ normal
    if value.length_squared > 1.0e-16:
        value.normalize()
    return value


def _face_direction(obj: Any, vertices: list[Any]) -> Vector:
    direction = Vector((0.0, 0.0, 0.0))
    for vertex in vertices:
        for face in vertex.link_faces:
            direction += _world_normal(obj, face.normal)
    if direction.length_squared > 1.0e-16:
        direction.normalize()
    return direction


def _choose_anchor(
    obj: Any,
    vertices: list[Any],
    strategy: str,
    view_matrix: Any | None = None,
) -> Any:
    world_positions = {vertex: obj.matrix_world @ vertex.co for vertex in vertices}
    if strategy.startswith("RADIAL"):
        center = obj.matrix_world.translation
        scores = {
            vertex: (position - center).length_squared
            for vertex, position in world_positions.items()
        }
    elif strategy.startswith("NORMAL"):
        center = sum(world_positions.values(), Vector()) / len(vertices)
        direction = _face_direction(obj, vertices)
        if direction.length_squared <= 1.0e-16:
            center = obj.matrix_world.translation
            scores = {
                vertex: (position - center).length_squared
                for vertex, position in world_positions.items()
            }
        else:
            scores = {
                vertex: (position - center).dot(direction)
                for vertex, position in world_positions.items()
            }
    else:
        if view_matrix is None:
            raise DirectionalMergeError(
                "Run View merge strategies from an active 3D View"
            )
        scores = {
            vertex: (view_matrix @ position).z
            for vertex, position in world_positions.items()
        }
    if strategy in {"RADIAL_OUTER", "NORMAL_OUTER", "VIEW_TOWARD"}:
        return max(vertices, key=lambda vertex: (scores[vertex], -vertex.index))
    return min(vertices, key=lambda vertex: (scores[vertex], vertex.index))


def _proximity_clusters(
    obj: Any,
    vertices: list[Any],
    distance: float,
    skip_connected: bool,
) -> list[list[Any]]:
    if len(vertices) < 2:
        return []
    tree = KDTree(len(vertices))
    for index, vertex in enumerate(vertices):
        tree.insert(obj.matrix_world @ vertex.co, index)
    tree.balance()
    parents = list(range(len(vertices)))
    members = [{index} for index in range(len(vertices))]
    vertex_indices = {vertex: index for index, vertex in enumerate(vertices)}
    connected_pairs = {
        frozenset((index, vertex_indices[edge.other_vert(vertex)]))
        for index, vertex in enumerate(vertices)
        for edge in vertex.link_edges
        if edge.other_vert(vertex) in vertex_indices
    }

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(first: int, second: int) -> None:
        first_root = find(first)
        second_root = find(second)
        if first_root == second_root:
            return
        if skip_connected and any(
            frozenset((first_member, second_member)) in connected_pairs
            for first_member in members[first_root]
            for second_member in members[second_root]
        ):
            return
        parents[second_root] = first_root
        members[first_root].update(members[second_root])
        members[second_root].clear()

    for index, vertex in enumerate(vertices):
        position = obj.matrix_world @ vertex.co
        for _coordinate, other_index, _distance in tree.find_range(position, distance):
            if other_index <= index:
                continue
            union(index, other_index)

    grouped: dict[int, list[Any]] = {}
    for index, vertex in enumerate(vertices):
        grouped.setdefault(find(index), []).append(vertex)
    return [
        sorted(cluster, key=lambda vertex: vertex.index)
        for cluster in grouped.values()
        if len(cluster) > 1
    ]


def _minimum_world_distance(obj: Any, vertices: list[Any]) -> float:
    tree = KDTree(len(vertices))
    positions = [obj.matrix_world @ vertex.co for vertex in vertices]
    for index, position in enumerate(positions):
        tree.insert(position, index)
    tree.balance()
    nearest = float("inf")
    for index, position in enumerate(positions):
        for _coordinate, other_index, distance in tree.find_n(position, 2):
            if other_index != index:
                nearest = min(nearest, distance)
    return nearest


def merge_selected_by_direction(
    context: Any,
    *,
    distance: float,
    strategy: str,
    scope: str,
    skip_connected: bool,
    view_matrix: Any | None = None,
    force_selected_pair: bool = True,
) -> dict[str, int]:
    obj = context.edit_object
    if obj is None or obj.type != "MESH" or obj.mode != "EDIT":
        raise DirectionalMergeError("Directional Merge requires one mesh in Edit Mode")
    if len(context.objects_in_mode_unique_data) != 1:
        raise DirectionalMergeError("Merge one mesh data-block at a time")
    if obj.data.shape_keys:
        raise DirectionalMergeError(
            "Shape-key meshes are not merged because key interpolation is ambiguous"
        )
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.verts.index_update()
    bm.normal_update()
    all_selected = [vertex for vertex in bm.verts if vertex.select]
    selected = [
        vertex
        for vertex in all_selected
        if scope == "ALL_SELECTED" or _is_boundary_or_loose(vertex)
    ]
    if len(selected) < 2:
        if len(all_selected) >= 2 and scope == "BOUNDARY":
            raise DirectionalMergeError(
                f"{len(all_selected)} vertices are selected, but fewer than two "
                "are boundary/loose vertices; change Scope to All Selected"
            )
        raise DirectionalMergeError(
            "Select at least two eligible vertices; change Scope if needed"
        )
    if len(selected) == 2 and force_selected_pair:
        connected = any(
            edge.other_vert(selected[0]) is selected[1]
            for edge in selected[0].link_edges
        )
        if skip_connected and connected:
            raise DirectionalMergeError(
                "The selected pair already shares an edge; turn off Protect "
                "Existing Edges to merge it"
            )
        clusters = [selected]
    else:
        clusters = _proximity_clusters(
            obj, selected, max(distance, 0.0), skip_connected
        )
    if not clusters:
        if skip_connected and _proximity_clusters(
            obj, selected, max(distance, 0.0), False
        ):
            raise DirectionalMergeError(
                "Close pairs were blocked by Protect Existing Edges; turn it off "
                "to allow an edge collapse"
            )
        nearest = _minimum_world_distance(obj, selected)
        raise DirectionalMergeError(
            f"Nearest eligible pair is {nearest:.6g} away; increase Merge Distance "
            f"above {distance:.6g}"
        )
    targetmap: dict[Any, Any] = {}
    anchors: list[Any] = []
    for cluster in clusters:
        anchor = _choose_anchor(obj, cluster, strategy, view_matrix)
        anchors.append(anchor)
        targetmap.update(
            {vertex: anchor for vertex in cluster if vertex is not anchor}
        )
    bmesh.ops.weld_verts(bm, targetmap=targetmap)
    for vertex in bm.verts:
        vertex.select_set(False)
    for anchor in anchors:
        if anchor.is_valid:
            anchor.select_set(True)
    bm.select_mode = {"VERT"}
    context.tool_settings.mesh_select_mode = (True, False, False)
    bm.normal_update()
    bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=True)
    return {
        "candidates": len(selected),
        "clusters": len(clusters),
        "merged_vertices": len(targetmap),
        "survivors": len(anchors),
    }


class QT_OT_directional_merge(Operator):
    bl_idname = "mesh.quad_transition_directional_merge"
    bl_label = "Directional Merge by Distance"
    bl_description = (
        "Merge selected close vertices while explicitly keeping the outer or "
        "inner position"
    )
    bl_options = {"REGISTER", "UNDO"}

    distance: FloatProperty(
        name="Merge Distance",
        description="Maximum world-space distance between selected vertices",
        min=0.0,
        soft_max=0.1,
        default=0.001,
        precision=5,
        subtype="DISTANCE",
    )
    strategy: EnumProperty(
        name="Direction Strategy",
        items=STRATEGY_ITEMS,
        default="RADIAL_OUTER",
    )
    scope: EnumProperty(name="Scope", items=SCOPE_ITEMS, default="ALL_SELECTED")
    skip_connected: BoolProperty(
        name="Protect Existing Edges",
        description="Do not merge a close pair that already shares a mesh edge",
        default=False,
    )
    force_selected_pair: BoolProperty(
        name="Always Merge Exact Pair",
        description=(
            "When exactly two eligible vertices are selected, merge that explicit "
            "pair even when it exceeds Merge Distance"
        ),
        default=True,
    )

    @classmethod
    def poll(cls, context: Any) -> bool:
        return context.edit_object is not None and context.edit_object.type == "MESH"

    def execute(self, context: Any):
        try:
            view_matrix = None
            if self.strategy.startswith("VIEW"):
                region_data = getattr(context, "region_data", None)
                if region_data is None:
                    raise DirectionalMergeError(
                        "Run View merge strategies from an active 3D View"
                    )
                view_matrix = region_data.view_matrix.copy()
            stats = merge_selected_by_direction(
                context,
                distance=self.distance,
                strategy=self.strategy,
                scope=self.scope,
                skip_connected=self.skip_connected,
                view_matrix=view_matrix,
                force_selected_pair=self.force_selected_pair,
            )
        except DirectionalMergeError as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        label = dict((item[0], item[1]) for item in STRATEGY_ITEMS)[self.strategy]
        self.report(
            {"INFO"},
            f"{label}: merged {stats['merged_vertices']} vertex/vertices "
            f"across {stats['clusters']} close group(s)",
        )
        return {"FINISHED"}


def _draw_merge_menu(self: Any, _context: Any) -> None:
    self.layout.separator()
    for strategy, label in (
        ("RADIAL_OUTER", "By Distance: Keep Outer"),
        ("RADIAL_INNER", "By Distance: Keep Inner"),
        ("NORMAL_OUTER", "By Face Normals: Keep Outside"),
        ("NORMAL_INNER", "By Face Normals: Keep Inside"),
        ("VIEW_TOWARD", "By View: Keep Toward You"),
        ("VIEW_AWAY", "By View: Keep Away From You"),
    ):
        operator = self.layout.operator(
            QT_OT_directional_merge.bl_idname,
            text=label,
            icon="AUTOMERGE_OFF",
        )
        operator.strategy = strategy


CLASSES = (QT_OT_directional_merge,)


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.VIEW3D_MT_edit_mesh_merge.append(_draw_merge_menu)


def unregister() -> None:
    bpy.types.VIEW3D_MT_edit_mesh_merge.remove(_draw_merge_menu)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
