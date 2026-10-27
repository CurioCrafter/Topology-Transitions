"""Topology Lens: live, colour-coded poles and non-quads with hover explanations.

The lens runs as a pass-through modal operator, so the mesh can be edited
normally while it is on. Every redraw re-reads the Edit Mode mesh, colours each
vertex by how many edges meet there, tints triangles and n-gons, and explains
the vertex under the cursor.
"""

from __future__ import annotations

from time import perf_counter
from typing import Any

import bmesh
import bpy
import gpu
from bpy.types import Operator, SpaceView3D
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from . import hud

MAX_VERTS = 200000
HOVER_PIXELS = 18.0

# kind -> (colour, title, explanation, tip)
KINDS = {
    "n_pole": (
        hud.AMBER,
        "3-pole (N-pole)",
        "3 edges meet: edge loops turn a corner around this vertex.",
        "Normal on box corners; keep them off areas that bend or deform.",
    ),
    "e_pole": (
        hud.MAGENTA,
        "5-pole (E-pole)",
        "5 edges meet: an extra edge loop branches off here.",
        "Put them where the shape changes, like the 5 -> 3 transition tool does.",
    ),
    "high_pole": (
        hud.CORAL,
        "6+ pole",
        "Too many edges meet: subdivision pinches and shading creases here.",
        "Split it into 3- and 5-poles, or reroute loops around it.",
    ),
    "doublet": (
        hud.CORAL,
        "Doublet (2 edges)",
        "Only 2 edges inside the surface: this vertex adds nothing.",
        "Dissolve it (Ctrl+X on the vertex) to clean the flow.",
    ),
    "border_pole": (
        hud.VIOLET,
        "Border pole",
        "An extra loop leaves the open border here.",
        "Fine on retopo sheets you will extend; check it before closing.",
    ),
    "loose": (
        hud.CORAL,
        "Loose vertex",
        "Hardly connected: a wire end or stray vertex.",
        "Run Find Holes / Check Manifold to step through these.",
    ),
    "regular": (
        hud.TEAL,
        "Regular vertex",
        "4 edges: loops pass straight through, the ideal for quads.",
        "Grids made of these subdivide smoothly.",
    ),
    "border": (
        hud.SKY,
        "Border vertex",
        "On the open edge of the mesh with a normal number of edges.",
        "Open borders are expected on retopo sheets.",
    ),
}
DRAWN = ("n_pole", "e_pole", "high_pole", "doublet", "border_pole", "loose")


def classify(valence: int, boundary: bool) -> str:
    if boundary:
        if valence <= 1:
            return "loose"
        return "border" if valence <= 3 else "border_pole"
    if valence <= 1:
        return "loose"
    return {2: "doublet", 3: "n_pole", 4: "regular", 5: "e_pole"}.get(
        valence, "high_pole"
    )


def lens_snapshot(obj: Any) -> dict[str, Any]:
    """World-space classification of the Edit Mode mesh."""

    bm = bmesh.from_edit_mesh(obj.data)
    matrix = obj.matrix_world
    normal_matrix = matrix.to_3x3().inverted_safe().transposed()
    verts = []
    counts = dict.fromkeys(KINDS, 0)
    lift = 0.0
    if bm.edges:
        sample = list(bm.edges)[:500]
        lift = sum(edge.calc_length() for edge in sample) / len(sample) * 0.04
    for vertex in bm.verts:
        if vertex.hide:
            continue
        boundary = vertex.is_boundary or vertex.is_wire or not vertex.link_faces
        kind = classify(len(vertex.link_edges), boundary)
        counts[kind] += 1
        world_normal = normal_matrix @ vertex.normal
        verts.append(
            (
                vertex.index,
                kind,
                matrix @ (vertex.co + vertex.normal * lift),
                world_normal,
                len(vertex.link_edges),
            )
        )
    tris, ngons = [], []
    face_counts = {"quads": 0, "tris": 0, "ngons": 0}
    for face in bm.faces:
        if face.hide:
            continue
        size = len(face.verts)
        if size == 4:
            face_counts["quads"] += 1
            continue
        face_counts["tris" if size == 3 else "ngons"] += 1
        points = [matrix @ (v.co + v.normal * lift) for v in face.verts]
        target = tris if size == 3 else ngons
        for index in range(1, size - 1):
            target.extend(
                (tuple(points[0]), tuple(points[index]), tuple(points[index + 1]))
            )
    return {
        "verts": verts,
        "counts": counts,
        "faces": face_counts,
        "tris": tris,
        "ngons": ngons,
    }


class QT_OT_topology_lens(Operator):
    bl_idname = "mesh.quad_transition_topology_lens"
    bl_label = "Topology Lens"
    bl_description = (
        "Toggle a live overlay that colours poles, triangles and n-gons and "
        "explains the vertex under the cursor; keep editing while it is on"
    )

    running = False
    _stop = False

    @classmethod
    def poll(cls, context: Any) -> bool:
        return (
            context.edit_object is not None
            and context.edit_object.type == "MESH"
            and context.area is not None
            and context.area.type == "VIEW_3D"
        )

    def invoke(self, context: Any, _event: Any):
        cls = type(self)
        if cls.running:
            cls._stop = True
            context.area.tag_redraw()
            return {"FINISHED"}
        cls.running, cls._stop = True, False
        self._area = context.area
        self._region = next(r for r in context.area.regions if r.type == "WINDOW")
        self._region_3d = context.space_data.region_3d
        self._object_name = context.edit_object.name
        self._mouse: tuple[float, float] | None = None
        self._snapshot: dict[str, Any] | None = None
        self._refreshed_at = 0.0
        self._refresh_interval = 0.05
        self._hover: tuple | None = None
        self._hover_key: tuple | None = None
        self._view_handle = SpaceView3D.draw_handler_add(
            self._draw_view, (), "WINDOW", "POST_VIEW"
        )
        self._hud_handle = SpaceView3D.draw_handler_add(
            self._draw_hud, (), "WINDOW", "POST_PIXEL"
        )
        context.window_manager.modal_handler_add(self)
        self._area.tag_redraw()
        return {"RUNNING_MODAL"}

    def _finish(self, context: Any):
        for handle in (self._view_handle, self._hud_handle):
            SpaceView3D.draw_handler_remove(handle, "WINDOW")
        cls = type(self)
        cls.running = cls._stop = False
        self._area.tag_redraw()
        return {"FINISHED"}

    def modal(self, context: Any, event: Any):
        obj = context.edit_object
        if type(self)._stop or obj is None or obj.name != self._object_name:
            return self._finish(context)
        if event.type == "ESC" and event.value == "PRESS":
            self._finish(context)
            return {"PASS_THROUGH"}
        if event.type == "MOUSEMOVE" and context.area == self._area:
            self._mouse = (
                float(event.mouse_x - self._region.x),
                float(event.mouse_y - self._region.y),
            )
        self._area.tag_redraw()
        return {"PASS_THROUGH"}

    def cancel(self, context: Any) -> None:
        self._finish(context)

    def _refresh(self) -> dict[str, Any] | None:
        obj = bpy.context.edit_object
        if obj is None or obj.name != self._object_name:
            return None
        if len(obj.data.vertices) > MAX_VERTS:
            return None
        # Re-read the mesh at most a few times a second on heavy meshes: the
        # interval adapts to how long one read takes.
        now = perf_counter()
        if self._snapshot is not None and now - self._refreshed_at < (
            self._refresh_interval
        ):
            return self._snapshot
        try:
            self._snapshot = lens_snapshot(obj)
        except (ReferenceError, ValueError):
            self._snapshot = None
        cost = perf_counter() - now
        self._refreshed_at = perf_counter()
        self._refresh_interval = min(1.0, max(0.05, cost * 4.0))
        self._hover_key = None
        return self._snapshot

    def _hovered(self) -> tuple | None:
        if self._mouse is None or self._snapshot is None:
            return None
        if self._hover_key == self._mouse:
            return self._hover
        self._hover_key = self._mouse
        self._hover = self._nearest()
        return self._hover

    def _nearest(self) -> tuple | None:
        best, best_distance = None, HOVER_PIXELS
        mouse = Vector(self._mouse)
        view = self._region_3d.view_matrix.inverted()
        eye, forward = view.translation, view.to_3x3() @ Vector((0.0, 0.0, -1.0))
        for entry in self._snapshot["verts"]:
            # Only vertices facing the viewer can be the one under the cursor.
            sight = entry[2] - eye if self._region_3d.is_perspective else forward
            if sight.dot(entry[3]) > 0.0:
                continue
            screen = view3d_utils.location_3d_to_region_2d(
                self._region, self._region_3d, entry[2]
            )
            if screen is None:
                continue
            distance = (screen - mouse).length
            if distance < best_distance:
                best, best_distance = entry, distance
        return best

    def _draw_view(self) -> None:
        if bpy.context.area != self._area:
            return
        snapshot = self._refresh()
        if snapshot is None:
            return
        fill = gpu.shader.from_builtin("UNIFORM_COLOR")
        points = gpu.shader.from_builtin("POINT_FLAT_COLOR")
        positions, colors = [], []
        for _index, kind, point, _normal, _valence in snapshot["verts"]:
            if kind in DRAWN:
                positions.append(tuple(point))
                colors.append(KINDS[kind][0])
        try:
            gpu.state.blend_set("ALPHA")
            gpu.state.depth_test_set("LESS_EQUAL")
            for tris, color in (
                (snapshot["tris"], (*hud.AMBER[:3], 0.3)),
                (snapshot["ngons"], (*hud.CORAL[:3], 0.35)),
            ):
                if tris:
                    fill.bind()
                    fill.uniform_float("color", color)
                    batch_for_shader(fill, "TRIS", {"pos": tris}).draw(fill)
            if positions:
                gpu.state.point_size_set(11.0)
                batch_for_shader(
                    points, "POINTS", {"pos": positions, "color": colors}
                ).draw(points)
            hovered = self._hovered()
            if hovered is not None:
                gpu.state.depth_test_set("NONE")
                gpu.state.point_size_set(18.0)
                batch_for_shader(
                    points,
                    "POINTS",
                    {"pos": [tuple(hovered[2])], "color": [KINDS[hovered[1]][0]]},
                ).draw(points)
                self._draw_spokes(hovered[0])
        finally:
            gpu.state.point_size_set(1.0)
            gpu.state.depth_test_set("NONE")
            gpu.state.blend_set("NONE")

    def _draw_spokes(self, index: int) -> None:
        obj = bpy.context.edit_object
        try:
            bm = bmesh.from_edit_mesh(obj.data)
            bm.verts.ensure_lookup_table()
            if index >= len(bm.verts):
                return
            lines = [
                tuple(obj.matrix_world @ v.co)
                for edge in bm.verts[index].link_edges
                for v in edge.verts
            ]
        except (ReferenceError, ValueError):
            return
        if not lines:
            return
        shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
        viewport = gpu.state.viewport_get()
        shader.bind()
        shader.uniform_float("viewportSize", (viewport[2], viewport[3]))
        shader.uniform_float("lineWidth", 4.0)
        shader.uniform_float("color", (1.0, 1.0, 1.0, 0.9))
        batch_for_shader(shader, "LINES", {"pos": lines}).draw(shader)

    def _draw_hud(self) -> None:
        if bpy.context.area != self._area:
            return
        obj = bpy.context.edit_object
        if obj is not None and len(obj.data.vertices) > MAX_VERTS:
            hud.draw_card(
                hud.left_margin(self._area),
                24.0,
                "Topology Lens",
                hud.CORAL,
                [[(f"Paused: more than {MAX_VERTS} vertices.", hud.INK)]],
            )
            return
        snapshot = self._snapshot
        if snapshot is None:
            return
        counts, faces = snapshot["counts"], snapshot["faces"]
        rows = [
            [
                ("chip", hud.TEAL, f"{faces['quads']} quads"),
                ("chip", hud.AMBER, f"{faces['tris']} tris"),
                ("chip", hud.CORAL, f"{faces['ngons']} n-gons"),
            ],
            [
                ("chip", hud.AMBER, f"{counts['n_pole']} 3-poles"),
                ("chip", hud.MAGENTA, f"{counts['e_pole']} 5-poles"),
                ("chip", hud.CORAL, f"{counts['high_pole'] + counts['doublet']} bad"),
                ("chip", hud.VIOLET, f"{counts['border_pole']} border"),
            ],
            [("Hover a dot to learn what it does to the edge flow.", hud.MUTED)],
            [
                ("key", "Esc"),
                ("or the Lens button turns it off; keep editing meanwhile", hud.MUTED),
            ],
        ]
        hud.draw_card(hud.left_margin(self._area), 24.0, "Topology Lens", hud.SKY, rows)
        hovered = self._hovered()
        if hovered is None or self._mouse is None:
            return
        color, title, explanation, tip = KINDS[hovered[1]]
        valence = hovered[4]
        rows = [
            [("chip", color, f"{valence} edges"), (explanation, hud.INK)],
            [(tip, hud.MUTED)],
        ]
        width, height = hud.measure_card(title, rows)
        x, y = self._mouse
        x = (
            x + 22.0
            if x + 22.0 + width < self._region.width - 8.0
            else x - 22.0 - width
        )
        y = min(y + 14.0, self._region.height - height - 8.0)
        hud.draw_card(max(8.0, x), max(8.0, y), title, color, rows)


CLASSES = (QT_OT_topology_lens,)


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    QT_OT_topology_lens.running = False
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
