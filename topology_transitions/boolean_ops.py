"""Live Boolean: cut or join with a live topology readout, then clean up.

A Boolean modifier is added to the active mesh with the other selected mesh as
the cutter. While the tool runs you keep moving, rotating and scaling the
cutter as usual; the card shows how many triangles and n-gons the cut makes,
and they are tinted on the mesh. Enter applies the cut and runs the add-on's
own triangle and n-gon repair on the faces around the cut, so the result is
as close to all-quad as the repairs can make it.
"""

from __future__ import annotations

from time import perf_counter
from typing import Any

import bmesh
import bpy
import gpu
from bpy.types import Object, Operator, SpaceView3D
from gpu_extras.batch import batch_for_shader
from mathutils.kdtree import KDTree

from . import hud
from .core import TransitionError

MODIFIER_NAME = "TT Live Boolean"
ORIGINAL_FACE = "tt_live_boolean_original"
PROPAGATE_LIMIT = 6
OPERATIONS = {"D": "DIFFERENCE", "U": "UNION", "I": "INTERSECT"}
REFRESH_SECONDS = 0.12
MAX_TINT_FACES = 20000


def face_counts(mesh: Any) -> dict[str, int]:
    counts = {"quads": 0, "tris": 0, "ngons": 0}
    for polygon in mesh.polygons:
        sides = len(polygon.vertices)
        counts["quads" if sides == 4 else "tris" if sides == 3 else "ngons"] += 1
    return counts


def mark_original_faces(obj: Object) -> KDTree:
    """Tag the mesh's own faces before the cut (faces from the cutter come
    out of the Boolean without the tag) and remember its vertex positions
    (vertices the cut creates are not among them)."""

    layer = obj.data.attributes.get(ORIGINAL_FACE)
    if layer is None:
        layer = obj.data.attributes.new(ORIGINAL_FACE, "INT", "FACE")
    layer.data.foreach_set("value", [1] * len(obj.data.polygons))
    tree = KDTree(len(obj.data.vertices))
    for index, vertex in enumerate(obj.data.vertices):
        tree.insert(vertex.co, index)
    tree.balance()
    return tree


def select_cut_faces(obj: Object, original: KDTree) -> int:
    """In Edit Mode, select the non-quad faces the cut made: faces that came
    from the cutter, and faces with a vertex the cut created."""

    bm = bmesh.from_edit_mesh(obj.data)
    layer = bm.faces.layers.int.get(ORIGINAL_FACE)
    if layer is None:
        return 0
    size = max(obj.dimensions) or 1.0
    new = {vertex for vertex in bm.verts if original.find(vertex.co)[2] > size * 1.0e-6}
    at_cut = {
        face
        for face in bm.faces
        if face[layer] == 0 or any(vertex in new for vertex in face.verts)
    }
    selected = 0
    for face in bm.faces:
        pick = face in at_cut and len(face.verts) != 4
        face.select_set(pick)
        selected += pick
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data)
    return selected


def clean_cut(context: Any, obj: Object, original: KDTree) -> dict[str, int]:
    """Quad repair of the faces the cut made; returns what it solved.

    Local repairs run first. A repair that carries splits through the
    surrounding quad rings is then kept only if it stays small (at most
    PROPAGATE_LIMIT new quads per repaired face): otherwise a handful of faces
    would be fixed by re-cutting much of the mesh.
    """

    from .edit_mesh import restore_bmesh
    from .repair_ops import solve_selected

    result = {"solved": 0, "created_quads": 0, "remaining": 0}
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        for target, propagate in (
            ("NGONS", False),
            ("TRIS", False),
            ("NGONS", False),
            ("NGONS", True),
            ("TRIS", True),
        ):
            selected = select_cut_faces(obj, original)
            if not selected:
                continue
            bm = bmesh.from_edit_mesh(obj.data)
            backup = bm.copy()
            try:
                stats = solve_selected(context, target, propagate=propagate)
                if propagate and stats["created_quads"] > PROPAGATE_LIMIT * max(
                    1, stats["solved"]
                ):
                    restore_bmesh(obj.data, bm, backup)
                    continue
            except TransitionError:
                continue
            finally:
                backup.free()
            result["solved"] += stats["solved"]
            result["created_quads"] += stats["created_quads"]
        result["remaining"] = select_cut_faces(obj, original)
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")
        if ORIGINAL_FACE in obj.data.attributes:
            obj.data.attributes.remove(obj.data.attributes[ORIGINAL_FACE])
    return result


def open_edges(mesh: Any) -> int:
    users: dict[tuple[int, int], int] = {}
    for polygon in mesh.polygons:
        for key in polygon.edge_keys:
            users[key] = users.get(key, 0) + 1
    return sum(1 for count in users.values() if count != 2)


class QT_OT_live_boolean(Operator):
    bl_idname = "object.quad_transition_live_boolean"
    bl_label = "Live Boolean"
    bl_description = (
        "Cut the active mesh with the other selected mesh, live: move the cutter "
        "and watch the triangles and n-gons the cut makes, then apply with quad "
        "cleanup"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: Any) -> bool:
        obj = context.active_object
        return (
            context.mode == "OBJECT"
            and obj is not None
            and obj.type == "MESH"
            and any(
                other is not obj and other.type == "MESH"
                for other in context.selected_objects
            )
        )

    def invoke(self, context: Any, _event: Any):
        self._target = context.active_object
        self._cutter = next(
            obj
            for obj in context.selected_objects
            if obj is not self._target and obj.type == "MESH"
        )
        if self._target.data.shape_keys is not None:
            self.report({"ERROR"}, "Live Boolean does not change shape-key meshes")
            return {"CANCELLED"}
        self._before = face_counts(self._target.data)
        self._open = open_edges(self._target.data)
        modifier = self._target.modifiers.new(MODIFIER_NAME, "BOOLEAN")
        modifier.object = self._cutter
        modifier.operation = "DIFFERENCE"
        if hasattr(modifier, "solver"):
            modifier.solver = "EXACT"
        self._cutter_display = (self._cutter.display_type, self._cutter.hide_render)
        self._cutter.display_type = "WIRE"
        self._cutter.hide_render = True
        # The cutter is what the user moves while the tool runs.
        self._target.select_set(False)
        context.view_layer.objects.active = self._cutter
        self._counts = dict(self._before)
        self._tint: list = []
        self._key = None
        self._last = 0.0
        self._area = context.area
        self._region = next(r for r in context.area.regions if r.type == "WINDOW")
        self._view = SpaceView3D.draw_handler_add(
            self._draw_view, (), "WINDOW", "POST_VIEW"
        )
        self._hud = SpaceView3D.draw_handler_add(
            self._draw_hud, (), "WINDOW", "POST_PIXEL"
        )
        self._timer = context.window_manager.event_timer_add(
            REFRESH_SECONDS, window=context.window
        )
        context.window_manager.modal_handler_add(self)
        self._refresh(context)
        return {"RUNNING_MODAL"}

    @property
    def _modifier(self):
        return self._target.modifiers.get(MODIFIER_NAME)

    def _refresh(self, context: Any) -> None:
        modifier = self._modifier
        key = (
            tuple(tuple(row) for row in self._cutter.matrix_world),
            tuple(tuple(row) for row in self._target.matrix_world),
            modifier.operation if modifier else None,
        )
        if key == self._key:
            return
        self._key = key
        depsgraph = context.evaluated_depsgraph_get()
        evaluated = self._target.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        try:
            self._counts = face_counts(mesh)
            tint = []
            if len(mesh.polygons) <= MAX_TINT_FACES:
                mesh.calc_loop_triangles()
                matrix = self._target.matrix_world
                for triangle in mesh.loop_triangles:
                    sides = len(mesh.polygons[triangle.polygon_index].vertices)
                    if sides == 4:
                        continue
                    points = [matrix @ mesh.vertices[v].co for v in triangle.vertices]
                    tint.append((sides, points))
            self._tint = tint
        finally:
            evaluated.to_mesh_clear()
        self._area.tag_redraw()

    def _restore_cutter(self) -> None:
        self._cutter.display_type, self._cutter.hide_render = self._cutter_display

    def _cleanup(self, context: Any) -> None:
        context.window_manager.event_timer_remove(self._timer)
        SpaceView3D.draw_handler_remove(self._view, "WINDOW")
        SpaceView3D.draw_handler_remove(self._hud, "WINDOW")
        self._area.tag_redraw()

    def modal(self, context: Any, event: Any):
        if event.type == "TIMER":
            if perf_counter() - self._last > REFRESH_SECONDS:
                self._last = perf_counter()
                self._refresh(context)
            return {"PASS_THROUGH"}
        if event.value != "PRESS" or event.ctrl or event.alt or event.shift:
            return {"PASS_THROUGH"}
        if event.type in OPERATIONS and self._modifier is not None:
            self._modifier.operation = OPERATIONS[event.type]
            self._key = None
            self._refresh(context)
            return {"RUNNING_MODAL"}
        if event.type in {"RET", "NUMPAD_ENTER"}:
            return self._apply(context)
        if event.type == "ESC":
            self._cleanup(context)
            if self._modifier is not None:
                self._target.modifiers.remove(self._modifier)
            self._restore_cutter()
            return {"CANCELLED"}
        return {"PASS_THROUGH"}

    def _apply(self, context: Any):
        self._cleanup(context)
        target, cutter = self._target, self._cutter
        for obj in context.selected_objects:
            obj.select_set(False)
        target.select_set(True)
        context.view_layer.objects.active = target
        original = mark_original_faces(target)
        with context.temp_override(object=target, active_object=target):
            bpy.ops.object.modifier_apply(modifier=MODIFIER_NAME)
        after_cut = face_counts(target.data)
        self._restore_cutter()
        cutter.hide_set(True)
        cleaned = clean_cut(context, target, original)
        final = face_counts(target.data)
        self.report(
            {"INFO"},
            f"Cut made {after_cut['tris']} tris and {after_cut['ngons']} n-gons; "
            f"cleanup turned {cleaned['solved']} into {cleaned['created_quads']} "
            f"quads; {final['tris'] + final['ngons']} non-quads left "
            f"({cleaned['remaining']} at the cut). Cutter hidden",
        )
        return {"FINISHED"}

    def _draw_view(self) -> None:
        if bpy.context.area != self._area or not self._tint:
            return
        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        gpu.state.blend_set("ALPHA")
        gpu.state.depth_test_set("LESS_EQUAL")
        try:
            shader.bind()
            for wanted, color in ((3, hud.AMBER), (5, hud.CORAL)):
                points = [
                    p
                    for sides, tri in self._tint
                    if (sides == 3) == (wanted == 3)
                    for p in tri
                ]
                if points:
                    shader.uniform_float("color", (*color[:3], 0.5))
                    batch_for_shader(shader, "TRIS", {"pos": points}).draw(shader)
        finally:
            gpu.state.depth_test_set("NONE")
            gpu.state.blend_set("NONE")

    def _draw_hud(self) -> None:
        if bpy.context.area != self._area:
            return
        before, now = self._before, self._counts
        modifier = self._modifier
        operation = modifier.operation.title() if modifier else "-"

        def delta(key: str) -> str:
            change = now[key] - before[key]
            return f"{now[key]} {key} ({change:+d})"

        made = (now["tris"] - before["tris"]) + (now["ngons"] - before["ngons"])
        rows = [
            [
                ("chip", hud.SKY, operation),
                ("chip", hud.TEAL, delta("quads")),
                ("chip", hud.AMBER, delta("tris")),
                ("chip", hud.CORAL, delta("ngons")),
            ],
            [
                (
                    f"The cut adds {made} non-quad faces (tinted)."
                    if made > 0
                    else "The cut adds no triangles or n-gons.",
                    hud.INK,
                )
            ],
            [("Move, rotate or scale the cutter; the cut follows live.", hud.MUTED)],
            *(
                [
                    [
                        ("chip", hud.CORAL, f"{self._open} open/over edges"),
                        ("Booleans need a closed mesh: run Auto Solve first.", hud.INK),
                    ]
                ]
                if self._open
                else []
            ),
            [
                ("key", "D"),
                ("difference", hud.MUTED),
                ("key", "U"),
                ("union", hud.MUTED),
                ("key", "I"),
                ("intersect", hud.MUTED),
                ("key", "Enter"),
                ("apply + quad cleanup", hud.MUTED),
                ("key", "Esc"),
                ("cancel", hud.MUTED),
            ],
        ]
        hud.draw_corner_card(self._area, self._region, "Live Boolean", hud.SKY, rows)


CLASSES = (QT_OT_live_boolean,)


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
