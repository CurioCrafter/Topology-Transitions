"""Interactive quad-face-flow browser for Blender's 3D View."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import sqrt
from typing import Any

import blf
import bmesh
import bpy
import gpu
from bpy.props import BoolProperty, IntProperty
from bpy.types import Operator, SpaceView3D
from gpu_extras.batch import batch_for_shader

from .quad_flows import (
    MeshQuadTopology,
    QuadFlow,
    discover_quad_flows,
    parallel_neighboring_quad_flows,
)


class QuadFlowError(ValueError):
    """Raised when the active mesh cannot provide a requested face-flow view."""


@dataclass
class FlowSession:
    object_name: str
    topology: MeshQuadTopology
    flows: list[QuadFlow]
    neighbors: dict[int, set[int]]
    face_vertices: dict[int, tuple[int, ...]]
    face_normals: dict[int, tuple[float, float, float]]
    world_positions: dict[int, tuple[float, float, float]]
    overlay_offset: float
    mesh_signature: tuple[int, int, int]


def _mesh_signature(bm: Any) -> tuple[int, int, int]:
    return (len(bm.verts), len(bm.edges), len(bm.faces))


def _topology_from_bmesh(
    bm: Any, obj: Any
) -> tuple[
    MeshQuadTopology,
    dict[int, tuple[float, float, float]],
    dict[int, tuple[int, ...]],
    dict[int, tuple[float, float, float]],
    float,
]:
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.verts.index_update()
    bm.edges.index_update()
    bm.faces.index_update()
    bm.normal_update()

    world_positions = {
        vertex.index: tuple(obj.matrix_world @ vertex.co) for vertex in bm.verts
    }
    topology = MeshQuadTopology(
        edge_vertices={
            edge.index: (edge.verts[0].index, edge.verts[1].index) for edge in bm.edges
        },
        edge_faces={
            edge.index: frozenset(face.index for face in edge.link_faces)
            for edge in bm.edges
        },
        face_edges={
            face.index: tuple(edge.index for edge in face.edges) for face in bm.faces
        },
        positions=world_positions,
    )
    face_vertices = {
        face.index: tuple(vertex.index for vertex in face.verts) for face in bm.faces
    }
    normal_matrix = obj.matrix_world.to_3x3().inverted_safe().transposed()
    face_normals = {}
    for face in bm.faces:
        normal = normal_matrix @ face.normal
        if normal.length_squared:
            normal.normalize()
        face_normals[face.index] = tuple(normal)
    edge_lengths = sorted(
        sqrt(
            sum(
                (world_positions[second][axis] - world_positions[first][axis]) ** 2
                for axis in range(3)
            )
        )
        for first, second in topology.edge_vertices.values()
    )
    typical_edge_length = edge_lengths[len(edge_lengths) // 2] if edge_lengths else 0.0
    overlay_offset = max(typical_edge_length * 0.005, 1.0e-7)
    return topology, world_positions, face_vertices, face_normals, overlay_offset


def build_flow_session(context: Any) -> FlowSession:
    obj = context.edit_object
    if obj is None or obj.type != "MESH" or obj.mode != "EDIT":
        raise QuadFlowError("Quad Flow Scroll requires a mesh in Edit Mode")
    if len(context.objects_in_mode_unique_data) != 1:
        raise QuadFlowError("Inspect one mesh data-block at a time")
    settings = context.scene.topology_transitions
    bm = bmesh.from_edit_mesh(obj.data)
    topology, world_positions, face_vertices, face_normals, overlay_offset = (
        _topology_from_bmesh(bm, obj)
    )
    visible_faces = {face.index for face in bm.faces if not face.hide}
    if settings.flow_scope == "SELECTED":
        eligible = {
            face.index for face in bm.faces if face.select and not face.hide
        }
        if not eligible:
            raise QuadFlowError("Selected scope needs at least one selected face")
    else:
        eligible = visible_faces
    flows = discover_quad_flows(
        topology,
        eligible_faces=eligible,
        minimum_quads=settings.flow_min_edges,
        sort=settings.flow_sort,
    )
    if not flows:
        raise QuadFlowError("No quad flows match this scope and minimum quad count")
    return FlowSession(
        object_name=obj.name,
        topology=topology,
        flows=flows,
        neighbors=parallel_neighboring_quad_flows(flows, topology),
        face_vertices=face_vertices,
        face_normals=face_normals,
        world_positions=world_positions,
        overlay_offset=overlay_offset,
        mesh_signature=_mesh_signature(bm),
    )


def update_flow_metrics(settings: Any, session: FlowSession, index: int) -> int:
    index %= len(session.flows)
    flow = session.flows[index]
    settings.flow_index = index
    settings.flow_object_name = session.object_name
    settings.flow_count = len(session.flows)
    settings.flow_edge_count = len(
        {
            edge_id
            for face_id in flow.face_ids
            for edge_id in session.topology.face_edges[face_id]
        }
    )
    settings.flow_quad_count = flow.quad_count
    settings.flow_length = flow.length
    settings.flow_alignment = flow.alignment
    settings.flow_closed = flow.closed
    settings.flow_neighbor_count = len(session.neighbors[index])
    settings.flow_start_label = flow.start_label
    settings.flow_end_label = flow.end_label
    return index


def select_quad_flow(context: Any, face_ids: set[int]) -> None:
    obj = context.edit_object
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    for face in bm.faces:
        face.select_set(False)
    for edge in bm.edges:
        edge.select_set(False)
    for vertex in bm.verts:
        vertex.select_set(False)
    context.tool_settings.mesh_select_mode = (False, False, True)
    bm.select_mode = {"FACE"}
    for face_id in face_ids:
        bm.faces[face_id].select_set(True)
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)


def focus_flow(context: Any, session: FlowSession, index: int) -> None:
    settings = context.scene.topology_transitions
    if (
        not settings.flow_focus_view
        or context.area is None
        or context.area.type != "VIEW_3D"
        or context.space_data is None
    ):
        return
    vertex_ids = {
        vertex_id
        for face_id in session.flows[index].face_ids
        for vertex_id in session.face_vertices[face_id]
    }
    if not vertex_ids:
        return
    points = [session.world_positions[vertex_id] for vertex_id in vertex_ids]
    minimum = tuple(min(point[axis] for point in points) for axis in range(3))
    maximum = tuple(max(point[axis] for point in points) for axis in range(3))
    center = tuple((minimum[axis] + maximum[axis]) * 0.5 for axis in range(3))
    radius = max(
        sqrt(sum((point[axis] - center[axis]) ** 2 for axis in range(3)))
        for point in points
    )
    region_3d = context.space_data.region_3d
    region_3d.view_location = center
    aspect = 1.0
    window_region = next(
        (region for region in context.area.regions if region.type == "WINDOW"), None
    )
    if window_region is not None and window_region.height:
        aspect = window_region.width / window_region.height
    region_3d.view_distance = max(radius * 2.8 * max(1.0, 1.0 / aspect), 0.25)


class QT_OT_edge_flow_step(Operator):
    bl_idname = "mesh.quad_transition_edge_flow_step"
    bl_label = "Step Quad Flow"
    bl_description = "Move to another quad face band and optionally select it"
    bl_options = {"REGISTER", "UNDO"}

    direction: IntProperty(name="Direction", min=-1, max=1, default=1)
    select_current: BoolProperty(name="Select Current", default=True)

    @classmethod
    def poll(cls, context: Any) -> bool:
        return (
            context.edit_object is not None
            and context.edit_object.type == "MESH"
            and context.edit_object.mode == "EDIT"
        )

    def execute(self, context: Any):
        settings = context.scene.topology_transitions
        try:
            session = build_flow_session(context)
        except (QuadFlowError, ValueError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        if settings.flow_count and settings.flow_object_name == session.object_name:
            current = min(settings.flow_index, len(session.flows) - 1)
        else:
            current = -1 if self.direction > 0 else 0
        index = update_flow_metrics(settings, session, current + self.direction)
        if self.select_current:
            select_quad_flow(context, set(session.flows[index].face_ids))
        focus_flow(context, session, index)
        flow = session.flows[index]
        self.report(
            {"INFO"},
            f"Quad flow {index + 1}/{len(session.flows)}: "
            f"{flow.quad_count} faces, {flow.alignment:.0%} smooth",
        )
        return {"FINISHED"}


class QT_OT_edge_flow_scroll(Operator):
    bl_idname = "mesh.quad_transition_edge_flow_scroll"
    bl_label = "Start Quad Flow Scroll"
    bl_description = (
        "Browse one-quad-wide face bands with the mouse wheel and viewport overlay"
    )
    bl_options = {"REGISTER", "UNDO", "BLOCKING"}

    start_index: IntProperty(
        name="Start Index",
        description="Optional flow index used by scripted invocations",
        min=-1,
        default=-1,
        options={"HIDDEN", "SKIP_SAVE"},
    )

    _area = None
    _draw_view_handle = None
    _draw_hud_handle = None
    _session: FlowSession | None = None
    _index = 0
    _object_name = ""
    _original_selection: dict[str, set[int]] | None = None
    _original_select_mode: tuple[bool, bool, bool] | None = None
    _original_bmesh_select_mode: set[str] | None = None

    @classmethod
    def poll(cls, context: Any) -> bool:
        return QT_OT_edge_flow_step.poll(context) and context.area is not None

    def _capture_selection(self, context: Any) -> None:
        bm = bmesh.from_edit_mesh(context.edit_object.data)
        self._original_selection = {
            "verts": {vertex.index for vertex in bm.verts if vertex.select},
            "edges": {edge.index for edge in bm.edges if edge.select},
            "faces": {face.index for face in bm.faces if face.select},
        }
        self._original_select_mode = tuple(context.tool_settings.mesh_select_mode)
        self._original_bmesh_select_mode = set(bm.select_mode)

    def _restore_selection(self, context: Any) -> None:
        if self._original_selection is None or context.edit_object is None:
            return
        bm = bmesh.from_edit_mesh(context.edit_object.data)
        if _mesh_signature(bm) != self._session.mesh_signature:
            return
        for vertex in bm.verts:
            vertex.select_set(vertex.index in self._original_selection["verts"])
        for edge in bm.edges:
            edge.select_set(edge.index in self._original_selection["edges"])
        for face in bm.faces:
            face.select_set(face.index in self._original_selection["faces"])
        if self._original_select_mode is not None:
            context.tool_settings.mesh_select_mode = self._original_select_mode
        if self._original_bmesh_select_mode is not None:
            bm.select_mode = self._original_bmesh_select_mode
            bm.select_flush_mode()
        bmesh.update_edit_mesh(
            context.edit_object.data,
            loop_triangles=False,
            destructive=False,
        )

    def _line_positions(self, edge_ids: set[int] | tuple[int, ...]):
        positions = []
        for edge_id in edge_ids:
            first, second = self._session.topology.edge_vertices[edge_id]
            positions.extend(
                (
                    self._session.world_positions[first],
                    self._session.world_positions[second],
                )
            )
        return positions

    def _face_triangle_positions(self, face_ids: set[int]):
        positions = []
        for face_id in face_ids:
            vertex_ids = self._session.face_vertices[face_id]
            if len(vertex_ids) != 4:
                continue
            normal = self._session.face_normals[face_id]

            def offset_position(vertex_id):
                position = self._session.world_positions[vertex_id]
                return tuple(
                    position[axis] + normal[axis] * self._session.overlay_offset
                    for axis in range(3)
                )

            first = offset_position(vertex_ids[0])
            for offset in range(1, len(vertex_ids) - 1):
                positions.extend(
                    (
                        first,
                        offset_position(vertex_ids[offset]),
                        offset_position(vertex_ids[offset + 1]),
                    )
                )
        return positions

    def _face_edge_ids(self, face_ids: set[int]) -> set[int]:
        return {
            edge_id
            for face_id in face_ids
            for edge_id in self._session.topology.face_edges[face_id]
        }

    def _boundary_edge_ids(self, face_ids: set[int]) -> set[int]:
        counts = Counter(
            edge_id
            for face_id in face_ids
            for edge_id in self._session.topology.face_edges[face_id]
        )
        return {edge_id for edge_id, count in counts.items() if count == 1}

    def _draw_faces(self, face_ids: set[int], color) -> None:
        positions = self._face_triangle_positions(face_ids)
        if not positions:
            return
        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        batch = batch_for_shader(shader, "TRIS", {"pos": positions})
        shader.bind()
        shader.uniform_float("color", color)
        batch.draw(shader)

    @staticmethod
    def _draw_lines(positions, color, width: float) -> None:
        if not positions:
            return
        shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
        batch = batch_for_shader(shader, "LINES", {"pos": positions})
        viewport = gpu.state.viewport_get()
        shader.bind()
        shader.uniform_float("color", color)
        shader.uniform_float("lineWidth", width)
        shader.uniform_float("viewportSize", (viewport[2], viewport[3]))
        batch.draw(shader)

    def _draw_view(self) -> None:
        if self._session is None or bpy.context.area != self._area:
            return
        settings = bpy.context.scene.topology_transitions
        flow = self._session.flows[self._index]
        try:
            gpu.state.blend_set("ALPHA")
            gpu.state.depth_test_set("LESS_EQUAL")
            gpu.state.depth_mask_set(False)
            active_faces = set(flow.face_ids)
            if settings.flow_show_neighbors:
                neighbor_faces = {
                    face_id
                    for neighbor in self._session.neighbors[self._index]
                    for face_id in self._session.flows[neighbor].face_ids
                }
                self._draw_faces(neighbor_faces, (0.05, 0.62, 1.0, 0.14))
            self._draw_faces(active_faces, (1.0, 0.22, 0.03, 0.38))
            self._draw_lines(
                self._line_positions(self._face_edge_ids(active_faces)),
                (1.0, 0.34, 0.08, 0.72),
                2.0,
            )
            self._draw_lines(
                self._line_positions(self._boundary_edge_ids(active_faces)),
                (1.0, 0.2, 0.02, 1.0),
                4.0,
            )
            self._draw_lines(
                self._line_positions(set(flow.endpoint_edge_ids)),
                (1.0, 0.04, 0.35, 1.0),
                6.0,
            )
        finally:
            gpu.state.point_size_set(1.0)
            gpu.state.depth_mask_set(True)
            gpu.state.depth_test_set("NONE")
            gpu.state.blend_set("NONE")

    def _draw_hud(self) -> None:
        if self._session is None or bpy.context.area != self._area:
            return
        flow = self._session.flows[self._index]
        settings = bpy.context.scene.topology_transitions
        lines = [
            f"QUAD FLOW {self._index + 1} / {len(self._session.flows)}",
            f"{flow.quad_count} quad faces   {flow.length:.3f} centerline length",
            f"{flow.alignment:.0%} smooth   "
            f"{settings.flow_sort.replace('_', ' ').title()} order",
            "Closed loop"
            if flow.closed
            else f"{flow.start_label}  ->  {flow.end_label}",
            f"{len(self._session.neighbors[self._index])} parallel face bands",
            "Wheel/Arrows browse + focus | Enter select quad flow | "
            "S select & stay | F focus | N neighbors | Esc cancel",
        ]
        font_id = 0
        blf.size(font_id, 16.0)
        blf.enable(font_id, blf.SHADOW)
        blf.shadow(font_id, 3, 0.0, 0.0, 0.0, 0.9)
        blf.color(font_id, 1.0, 1.0, 1.0, 1.0)
        for line_index, line in enumerate(lines):
            blf.position(font_id, 56.0, 149.0 - line_index * 21.0, 0.0)
            blf.draw(font_id, line)
        blf.disable(font_id, blf.SHADOW)

    def _redraw(self) -> None:
        if self._area is not None:
            self._area.tag_redraw()

    def _set_index(self, context: Any, index: int) -> None:
        self._index = update_flow_metrics(
            context.scene.topology_transitions, self._session, index
        )
        focus_flow(context, self._session, self._index)
        self._redraw()

    def _cleanup(self, context: Any) -> None:
        if self._draw_view_handle is not None:
            SpaceView3D.draw_handler_remove(self._draw_view_handle, "WINDOW")
            self._draw_view_handle = None
        if self._draw_hud_handle is not None:
            SpaceView3D.draw_handler_remove(self._draw_hud_handle, "WINDOW")
            self._draw_hud_handle = None
        if context.workspace is not None:
            context.workspace.status_text_set(None)
        self._redraw()

    def invoke(self, context: Any, _event: Any):
        if context.area.type != "VIEW_3D":
            self.report({"ERROR"}, "Start Quad Flow Scroll from a 3D View")
            return {"CANCELLED"}
        try:
            self._session = build_flow_session(context)
        except (QuadFlowError, ValueError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self._area = context.area
        self._object_name = context.edit_object.name
        self._capture_selection(context)
        settings = context.scene.topology_transitions
        self._index = (
            0
            if self.start_index < 0
            else min(self.start_index, len(self._session.flows) - 1)
        )
        update_flow_metrics(settings, self._session, self._index)
        focus_flow(context, self._session, self._index)
        self._draw_view_handle = SpaceView3D.draw_handler_add(
            self._draw_view, (), "WINDOW", "POST_VIEW"
        )
        self._draw_hud_handle = SpaceView3D.draw_handler_add(
            self._draw_hud, (), "WINDOW", "POST_PIXEL"
        )
        context.window_manager.modal_handler_add(self)
        context.workspace.status_text_set(
            "Quad Flow Scroll: Wheel/Arrows browse and focus, Enter selects band, "
            "S selects and stays, F toggles focus, N toggles neighbors, Esc cancels"
        )
        self._redraw()
        return {"RUNNING_MODAL"}

    def modal(self, context: Any, event: Any):
        obj = context.edit_object
        if (
            obj is None
            or obj.name != self._object_name
            or _mesh_signature(bmesh.from_edit_mesh(obj.data))
            != self._session.mesh_signature
        ):
            self._cleanup(context)
            self.report({"WARNING"}, "Mesh topology changed; flow browser closed")
            return {"CANCELLED"}
        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            self._restore_selection(context)
            self._cleanup(context)
            return {"CANCELLED"}
        if event.type in {"RET", "NUMPAD_ENTER"} and event.value == "PRESS":
            select_quad_flow(context, set(self._session.flows[self._index].face_ids))
            self._cleanup(context)
            return {"FINISHED"}
        if event.type == "S" and event.value == "PRESS":
            select_quad_flow(context, set(self._session.flows[self._index].face_ids))
            self._redraw()
            return {"RUNNING_MODAL"}
        if event.type == "F" and event.value == "PRESS":
            settings = context.scene.topology_transitions
            settings.flow_focus_view = not settings.flow_focus_view
            if settings.flow_focus_view:
                focus_flow(context, self._session, self._index)
            self._redraw()
            return {"RUNNING_MODAL"}
        if event.type == "N" and event.value == "PRESS":
            settings = context.scene.topology_transitions
            settings.flow_show_neighbors = not settings.flow_show_neighbors
            self._redraw()
            return {"RUNNING_MODAL"}
        if event.type == "WHEELUPMOUSE" or (
            event.type in {"UP_ARROW", "LEFT_ARROW"} and event.value == "PRESS"
        ):
            self._set_index(context, self._index - 1)
            return {"RUNNING_MODAL"}
        if event.type == "WHEELDOWNMOUSE" or (
            event.type in {"DOWN_ARROW", "RIGHT_ARROW"} and event.value == "PRESS"
        ):
            self._set_index(context, self._index + 1)
            return {"RUNNING_MODAL"}
        if event.type == "HOME" and event.value == "PRESS":
            self._set_index(context, 0)
            return {"RUNNING_MODAL"}
        if event.type == "END" and event.value == "PRESS":
            self._set_index(context, len(self._session.flows) - 1)
            return {"RUNNING_MODAL"}
        if event.type in {"MIDDLEMOUSE", "MOUSEMOVE", "NDOF_MOTION"}:
            return {"PASS_THROUGH"}
        return {"RUNNING_MODAL"}

    def cancel(self, context: Any) -> None:
        self._restore_selection(context)
        self._cleanup(context)


CLASSES = (
    QT_OT_edge_flow_step,
    QT_OT_edge_flow_scroll,
)


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
