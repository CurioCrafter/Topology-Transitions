"""Interactive edge-flow browser for Blender's 3D View."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import blf
import bmesh
import bpy
import gpu
from bpy.props import BoolProperty, IntProperty
from bpy.types import Operator, SpaceView3D
from gpu_extras.batch import batch_for_shader

from .edge_flows import (
    EdgeFlow,
    MeshFlowTopology,
    discover_edge_flows,
    neighboring_flows,
)


class EdgeFlowError(ValueError):
    """Raised when the active mesh cannot provide a requested flow view."""


@dataclass
class FlowSession:
    topology: MeshFlowTopology
    flows: list[EdgeFlow]
    neighbors: dict[int, set[int]]
    world_positions: dict[int, tuple[float, float, float]]
    mesh_signature: tuple[int, int, int]


def _mesh_signature(bm: Any) -> tuple[int, int, int]:
    return (len(bm.verts), len(bm.edges), len(bm.faces))


def _topology_from_bmesh(
    bm: Any, obj: Any
) -> tuple[MeshFlowTopology, dict[int, tuple[float, float, float]]]:
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.verts.index_update()
    bm.edges.index_update()
    bm.faces.index_update()

    world_positions = {
        vertex.index: tuple(obj.matrix_world @ vertex.co) for vertex in bm.verts
    }
    topology = MeshFlowTopology(
        edge_vertices={
            edge.index: (edge.verts[0].index, edge.verts[1].index) for edge in bm.edges
        },
        vertex_edges={
            vertex.index: tuple(edge.index for edge in vertex.link_edges)
            for vertex in bm.verts
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
    return topology, world_positions


def build_flow_session(context: Any) -> FlowSession:
    obj = context.edit_object
    if obj is None or obj.type != "MESH" or obj.mode != "EDIT":
        raise EdgeFlowError("Edge Flow Scroll requires a mesh in Edit Mode")
    if len(context.objects_in_mode_unique_data) != 1:
        raise EdgeFlowError("Inspect one mesh data-block at a time")
    settings = context.scene.topology_transitions
    bm = bmesh.from_edit_mesh(obj.data)
    topology, world_positions = _topology_from_bmesh(bm, obj)
    visible_edges = {edge.index for edge in bm.edges if not edge.hide}
    if settings.flow_scope == "SELECTED":
        eligible = {edge.index for edge in bm.edges if edge.select and not edge.hide}
        if not eligible:
            raise EdgeFlowError("Selected scope needs at least one selected edge")
    else:
        eligible = visible_edges
    flows = discover_edge_flows(
        topology,
        eligible_edges=eligible,
        mode=settings.flow_mode,
        minimum_edges=settings.flow_min_edges,
        minimum_alignment=settings.flow_min_alignment,
        sort=settings.flow_sort,
    )
    if not flows:
        raise EdgeFlowError("No flows match this scope and minimum edge count")
    return FlowSession(
        topology=topology,
        flows=flows,
        neighbors=neighboring_flows(flows, topology.face_edges),
        world_positions=world_positions,
        mesh_signature=_mesh_signature(bm),
    )


def update_flow_metrics(settings: Any, session: FlowSession, index: int) -> int:
    index %= len(session.flows)
    flow = session.flows[index]
    settings.flow_index = index
    settings.flow_count = len(session.flows)
    settings.flow_edge_count = flow.edge_count
    settings.flow_length = flow.length
    settings.flow_alignment = flow.alignment
    settings.flow_closed = flow.closed
    settings.flow_neighbor_count = len(session.neighbors[index])
    settings.flow_start_label = "Closed" if flow.start is None else flow.start.label
    settings.flow_end_label = "Closed" if flow.end is None else flow.end.label
    return index


def select_flow(context: Any, flow: EdgeFlow) -> None:
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
    context.tool_settings.mesh_select_mode = (False, True, False)
    bm.select_mode = {"EDGE"}
    for edge_id in flow.edge_ids:
        bm.edges[edge_id].select_set(True)
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)


class QT_OT_edge_flow_step(Operator):
    bl_idname = "mesh.quad_transition_edge_flow_step"
    bl_label = "Step Edge Flow"
    bl_description = "Move to another detected edge flow and optionally select it"
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
        except (EdgeFlowError, ValueError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        if settings.flow_count:
            current = min(settings.flow_index, len(session.flows) - 1)
        else:
            current = -1 if self.direction > 0 else 0
        index = update_flow_metrics(settings, session, current + self.direction)
        if self.select_current:
            select_flow(context, session.flows[index])
        flow = session.flows[index]
        self.report(
            {"INFO"},
            f"Flow {index + 1}/{len(session.flows)}: {flow.edge_count} edges, "
            f"{flow.alignment:.0%} aligned",
        )
        return {"FINISHED"}


class QT_OT_edge_flow_scroll(Operator):
    bl_idname = "mesh.quad_transition_edge_flow_scroll"
    bl_label = "Start Edge Flow Scroll"
    bl_description = (
        "Browse every detected edge flow with the mouse wheel and viewport overlay"
    )
    bl_options = {"REGISTER", "UNDO", "BLOCKING"}

    _area = None
    _draw_view_handle = None
    _draw_hud_handle = None
    _session: FlowSession | None = None
    _index = 0
    _object_name = ""
    _original_selection: dict[str, set[int]] | None = None
    _original_select_mode: tuple[bool, bool, bool] | None = None

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
            if settings.flow_show_neighbors:
                neighbor_edges = {
                    edge_id
                    for neighbor in self._session.neighbors[self._index]
                    for edge_id in self._session.flows[neighbor].edge_ids
                }
                self._draw_lines(
                    self._line_positions(neighbor_edges),
                    (0.1, 0.75, 1.0, 0.38),
                    2.0,
                )
            self._draw_lines(
                self._line_positions(flow.edge_ids),
                (1.0, 0.24, 0.04, 1.0),
                5.0,
            )
            if not flow.closed:
                endpoint_positions = [
                    self._session.world_positions[flow.vertex_ids[0]],
                    self._session.world_positions[flow.vertex_ids[-1]],
                ]
                shader = gpu.shader.from_builtin("UNIFORM_COLOR")
                batch = batch_for_shader(shader, "POINTS", {"pos": endpoint_positions})
                gpu.state.point_size_set(10.0)
                shader.bind()
                shader.uniform_float("color", (1.0, 0.04, 0.35, 1.0))
                batch.draw(shader)
        finally:
            gpu.state.point_size_set(1.0)
            gpu.state.depth_test_set("NONE")
            gpu.state.blend_set("NONE")

    def _draw_hud(self) -> None:
        if self._session is None or bpy.context.area != self._area:
            return
        flow = self._session.flows[self._index]
        settings = bpy.context.scene.topology_transitions
        lines = [
            f"EDGE FLOW {self._index + 1} / {len(self._session.flows)}",
            f"{flow.edge_count} edges   {flow.length:.3f} length   "
            f"{flow.alignment:.0%} aligned",
            "Closed loop"
            if flow.closed
            else f"{flow.start.label}  ->  {flow.end.label}",
            f"{settings.flow_mode.title()} mode   "
            f"{len(self._session.neighbors[self._index])} neighboring flows",
            "Wheel/Arrows browse | Enter select | S select & stay | "
            "N neighbors | Esc cancel",
        ]
        font_id = 0
        blf.size(font_id, 16.0)
        blf.enable(font_id, blf.SHADOW)
        blf.shadow(font_id, 3, 0.0, 0.0, 0.0, 0.9)
        blf.color(font_id, 1.0, 1.0, 1.0, 1.0)
        for line_index, line in enumerate(lines):
            blf.position(font_id, 56.0, 128.0 - line_index * 21.0, 0.0)
            blf.draw(font_id, line)
        blf.disable(font_id, blf.SHADOW)

    def _redraw(self) -> None:
        if self._area is not None:
            self._area.tag_redraw()

    def _set_index(self, context: Any, index: int) -> None:
        self._index = update_flow_metrics(
            context.scene.topology_transitions, self._session, index
        )
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
            self.report({"ERROR"}, "Start Edge Flow Scroll from a 3D View")
            return {"CANCELLED"}
        try:
            self._session = build_flow_session(context)
        except (EdgeFlowError, ValueError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self._area = context.area
        self._object_name = context.edit_object.name
        self._capture_selection(context)
        settings = context.scene.topology_transitions
        self._index = min(settings.flow_index, len(self._session.flows) - 1)
        update_flow_metrics(settings, self._session, self._index)
        self._draw_view_handle = SpaceView3D.draw_handler_add(
            self._draw_view, (), "WINDOW", "POST_VIEW"
        )
        self._draw_hud_handle = SpaceView3D.draw_handler_add(
            self._draw_hud, (), "WINDOW", "POST_PIXEL"
        )
        context.window_manager.modal_handler_add(self)
        context.workspace.status_text_set(
            "Edge Flow Scroll: Wheel/Arrows browse, Enter selects, "
            "S selects and stays, N toggles neighbors, Esc cancels"
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
            select_flow(context, self._session.flows[self._index])
            self._cleanup(context)
            return {"FINISHED"}
        if event.type == "S" and event.value == "PRESS":
            select_flow(context, self._session.flows[self._index])
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
