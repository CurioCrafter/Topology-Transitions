"""Face Loops: hover a quad and scroll through the face loops of a mesh.

A face loop is the ring of quads you cross by walking out of a quad through
one edge and in through the opposite edge of the next quad, until a non-quad,
an open border, or the start is reached. Hovering picks the loop that runs
through the edge nearest the mouse; the wheel steps to the parallel loop on
either side, so you can sweep across a whole region loop by loop. R turns the
loop the other way through the same quad, F flips the face orientation of the
loop, and clicks select loops.
"""

from __future__ import annotations

from typing import Any

import bmesh
import bpy
import gpu
from bpy.types import Operator, SpaceView3D
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader
from mathutils.bvhtree import BVHTree

from . import hud


def _quad_edges(face) -> list:
    """The face's edges in winding order."""

    return [loop.edge for loop in face.loops]


def _opposite(face, edge):
    edges = _quad_edges(face)
    return edges[(edges.index(edge) + 2) % 4]


def _beside(face, edge) -> tuple:
    """The two edges of a quad that touch ``edge``: the loop's sides."""

    edges = _quad_edges(face)
    index = edges.index(edge)
    return edges[(index + 1) % 4], edges[(index + 3) % 4]


def _across(face, edge):
    if len(edge.link_faces) != 2:
        return None
    other = edge.link_faces[0] if edge.link_faces[1] == face else edge.link_faces[1]
    return other if len(other.verts) == 4 else None


def face_loop(face, edge) -> tuple[list, bool]:
    """Quads of the loop through ``face`` that crosses ``edge``, in order, and
    whether the loop closes on itself."""

    if len(face.verts) != 4 or edge not in face.edges:
        return [face], False
    forward, current, through = [], face, edge
    while True:
        nxt = _across(current, through)
        if nxt is None:
            break
        if nxt == face:
            return [face, *forward], True
        if nxt in forward:
            break
        forward.append(nxt)
        through, current = _opposite(nxt, through), nxt
    backward, current, through = [], face, _opposite(face, edge)
    while True:
        nxt = _across(current, through)
        if nxt is None or nxt in forward or nxt in backward or nxt == face:
            break
        backward.append(nxt)
        through, current = _opposite(nxt, through), nxt
    return [*reversed(backward), face, *forward], False


def next_loop(face, edge, side):
    """Step from the loop through ``face`` (crossing ``edge``) to the parallel
    loop across ``side``. Returns the new (face, edge, side) or None."""

    neighbor = _across(face, side)
    if neighbor is None:
        return None
    # In the neighbour the loop crosses the edges that touch the shared side;
    # the next step continues through the side opposite the one we came over.
    crossing = _beside(neighbor, side)[0]
    return neighbor, crossing, _opposite(neighbor, side)


class QT_OT_face_loops(Operator):
    bl_idname = "mesh.quad_transition_face_loops"
    bl_label = "Face Loops"
    bl_description = (
        "Hover a quad to see its face loop, scroll to step through the parallel "
        "loops, R to turn the loop the other way, F to flip its faces, click to "
        "select"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: Any) -> bool:
        obj = context.edit_object
        return obj is not None and obj.type == "MESH"

    def invoke(self, context: Any, _event: Any):
        self._obj = context.edit_object
        self._bm = bmesh.from_edit_mesh(self._obj.data)
        self._bm.faces.ensure_lookup_table()
        self._saved = {face.index for face in self._bm.faces if face.select}
        context.tool_settings.mesh_select_mode = (False, False, True)
        self._tree = BVHTree.FromBMesh(self._bm)
        self._area = context.area
        self._region = next(r for r in context.area.regions if r.type == "WINDOW")
        self._rv3d = context.region_data
        self._hovered = None
        self._loop = None  # (face, crossing edge, forward side)
        self._faces: list = []
        self._closed = False
        self._steps = 0
        self._view = SpaceView3D.draw_handler_add(
            self._draw_view, (), "WINDOW", "POST_VIEW"
        )
        self._hud = SpaceView3D.draw_handler_add(
            self._draw_hud, (), "WINDOW", "POST_PIXEL"
        )
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _cleanup(self) -> None:
        SpaceView3D.draw_handler_remove(self._view, "WINDOW")
        SpaceView3D.draw_handler_remove(self._hud, "WINDOW")
        self._area.tag_redraw()

    def _pick(self, event: Any) -> None:
        coordinate = (event.mouse_region_x, event.mouse_region_y)
        origin = view3d_utils.region_2d_to_origin_3d(
            self._region, self._rv3d, coordinate
        )
        direction = view3d_utils.region_2d_to_vector_3d(
            self._region, self._rv3d, coordinate
        )
        inverse = self._obj.matrix_world.inverted_safe()
        local_origin = inverse @ origin
        local_direction = (inverse.to_3x3() @ direction).normalized()
        location, _normal, index, _distance = self._tree.ray_cast(
            local_origin, local_direction
        )
        if index is None or index >= len(self._bm.faces):
            return
        face = self._bm.faces[index]
        if face == self._hovered:
            return
        self._hovered = face
        if len(face.verts) != 4:
            self._set(face, None, None)
            return
        nearest = min(
            face.edges,
            key=lambda edge: (
                (edge.verts[0].co + edge.verts[1].co) / 2 - location
            ).length,
        )
        # The loop runs along the nearest edge, so it crosses the edges beside it.
        crossing = _beside(face, nearest)[0]
        self._set(face, crossing, nearest)

    def _set(self, face, crossing, side) -> None:
        if crossing is None:
            self._loop, self._faces, self._closed = None, [face], False
        else:
            self._loop = (face, crossing, side)
            self._faces, self._closed = face_loop(face, crossing)
        self._area.tag_redraw()

    def _step(self, forward: bool) -> None:
        if self._loop is None:
            return
        face, crossing, side = self._loop
        if not forward:
            side = _opposite(face, side)
        stepped = next_loop(face, crossing, side)
        if stepped is None:
            return
        new_face, new_crossing, new_side = stepped
        if not forward:
            new_side = _opposite(new_face, new_side)
        self._steps += 1 if forward else -1
        self._set(new_face, new_crossing, new_side)

    def _turn(self) -> None:
        if self._loop is None:
            return
        face, crossing, _side = self._loop
        self._set(face, _beside(face, crossing)[0], crossing)

    def _select(self, extend: bool) -> None:
        if not extend:
            for face in self._bm.faces:
                face.select_set(False)
        for face in self._faces:
            face.select_set(True)
        self._bm.select_flush_mode()
        bmesh.update_edit_mesh(self._obj.data)

    def _flip(self) -> None:
        bmesh.ops.reverse_faces(self._bm, faces=list(self._faces))
        bmesh.update_edit_mesh(self._obj.data)
        self._area.tag_redraw()

    def modal(self, context: Any, event: Any):
        if event.type == "MOUSEMOVE":
            self._pick(event)
            return {"PASS_THROUGH"}
        if event.type in {"WHEELUPMOUSE", "WHEELDOWNMOUSE"} and not event.ctrl:
            self._step(event.type == "WHEELUPMOUSE")
            return {"RUNNING_MODAL"}
        if event.value != "PRESS":
            return {"PASS_THROUGH"}
        if event.type in {"UP_ARROW", "DOWN_ARROW"}:
            self._step(event.type == "UP_ARROW")
            return {"RUNNING_MODAL"}
        if event.type == "R":
            self._turn()
            return {"RUNNING_MODAL"}
        if event.type == "F" and self._faces:
            self._flip()
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and self._faces:
            self._select(extend=event.shift)
            return {"RUNNING_MODAL"}
        if event.type in {"RET", "NUMPAD_ENTER"}:
            self._cleanup()
            return {"FINISHED"}
        if event.type in {"ESC", "RIGHTMOUSE"}:
            for face in self._bm.faces:
                face.select_set(face.index in self._saved)
            self._bm.select_flush_mode()
            bmesh.update_edit_mesh(self._obj.data)
            self._cleanup()
            return {"CANCELLED"}
        return {"PASS_THROUGH"}

    def _draw_view(self) -> None:
        if bpy.context.area != self._area or not self._faces:
            return
        matrix = self._obj.matrix_world
        try:
            triangles = []
            for face in self._faces:
                points = [matrix @ v.co for v in face.verts]
                for i in range(1, len(points) - 1):
                    triangles += (points[0], points[i], points[i + 1])
            anchor = self._loop[0] if self._loop else self._faces[0]
            outline = [matrix @ v.co for e in anchor.edges for v in e.verts]
        except ReferenceError:
            return
        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        gpu.state.blend_set("ALPHA")
        gpu.state.depth_test_set("LESS_EQUAL")
        try:
            shader.bind()
            shader.uniform_float("color", (*hud.TEAL[:3], 0.45))
            batch_for_shader(shader, "TRIS", {"pos": triangles}).draw(shader)
            shader.uniform_float("color", (1.0, 1.0, 1.0, 0.9))
            batch_for_shader(shader, "LINES", {"pos": outline}).draw(shader)
        finally:
            gpu.state.depth_test_set("NONE")
            gpu.state.blend_set("NONE")

    def _draw_hud(self) -> None:
        if bpy.context.area != self._area:
            return
        if self._loop is None and self._faces:
            sides = len(self._faces[0].verts) if self._faces[0].is_valid else 0
            status = [
                ("chip", hud.AMBER, "triangle" if sides == 3 else f"{sides}-gon"),
                ("Face loops run through quads only.", hud.INK),
            ]
        elif self._loop is None:
            status = [("Hover a quad to see its face loop.", hud.INK)]
        else:
            status = [
                ("chip", hud.TEAL, f"{len(self._faces)} quads"),
                (
                    "chip",
                    hud.SKY if self._closed else hud.AMBER,
                    "closed loop" if self._closed else "open: ends at a pole or border",
                ),
                ("chip", hud.VIOLET, f"step {self._steps:+d}"),
            ]
        rows = [
            status,
            [
                (
                    "The wheel steps to the next parallel loop; zoom with "
                    "Ctrl+MMB or Numpad +/-.",
                    hud.MUTED,
                )
            ],
            [
                ("key", "Wheel"),
                ("next loop", hud.MUTED),
                ("key", "R"),
                ("other direction", hud.MUTED),
                ("key", "F"),
                ("flip faces", hud.MUTED),
            ],
            [
                ("key", "Click"),
                ("select", hud.MUTED),
                ("key", "Shift"),
                ("add", hud.MUTED),
                ("key", "Enter"),
                ("done", hud.MUTED),
                ("key", "Esc"),
                ("cancel", hud.MUTED),
            ],
        ]
        hud.draw_corner_card(self._area, self._region, "Face Loops", hud.TEAL, rows)


CLASSES = (QT_OT_face_loops,)


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
