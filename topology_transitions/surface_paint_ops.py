"""Interactive Surface Paint: paint coverage on a target, build a quad grid."""

from __future__ import annotations

from collections import Counter
from math import cos, degrees, pi, radians, sin
from typing import Any

import bmesh
import bpy
import gpu
from bpy.props import BoolProperty, FloatProperty, IntProperty, StringProperty
from bpy.types import Object, Operator, SpaceView3D
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from . import hud
from .core import TransitionError
from .edit_mesh import restore_bmesh
from .paint_grid import (
    Dab,
    Mirror,
    PaintGrid,
    Seed,
    grid_report,
    grow_paint_grid,
)

AUTO_CELL_FACTOR = 0.4
WELD_FACTOR = 0.35


class TargetSurface:
    """Evaluated target-mesh ray and nearest-surface queries in world space."""

    def __init__(self, context: Any, target: Object) -> None:
        if target.type != "MESH":
            raise TransitionError("The paint target must be a mesh object")
        self.target = target
        self.depsgraph = context.evaluated_depsgraph_get()
        self.evaluated = target.evaluated_get(self.depsgraph)
        self.to_world = self.evaluated.matrix_world.copy()
        self.to_local = self.to_world.inverted_safe()
        self.normal_to_world = self.to_world.to_3x3().inverted_safe().transposed()

    def _world_hit(self, location: Vector, normal: Vector) -> tuple[Vector, Vector]:
        world_normal = self.normal_to_world @ normal
        if world_normal.length_squared:
            world_normal.normalize()
        return self.to_world @ location, world_normal

    def ray_cast(
        self, region: Any, region_3d: Any, coordinate: tuple[float, float]
    ) -> tuple[Vector, Vector] | None:
        origin = view3d_utils.region_2d_to_origin_3d(
            region, region_3d, coordinate, clamp=1.0e6
        )
        direction = view3d_utils.region_2d_to_vector_3d(region, region_3d, coordinate)
        direction_local = self.to_local.to_3x3() @ direction
        if direction_local.length_squared <= 1.0e-16:
            return None
        direction_local.normalize()
        success, location, normal, _index = self.evaluated.ray_cast(
            self.to_local @ origin, direction_local, distance=1.0e20
        )
        return self._world_hit(location, normal) if success else None

    def closest(
        self, coordinate_world: Vector, limit: float = 0.0
    ) -> tuple[Vector, Vector] | None:
        success, location, normal, _index = self.evaluated.closest_point_on_mesh(
            self.to_local @ coordinate_world,
            distance=limit if limit > 0.0 else 1.0e20,
        )
        return self._world_hit(location, normal) if success else None

    def projector(self, limit: float):
        def project(point):
            hit = self.closest(Vector(point), limit)
            if hit is None:
                return None
            return tuple(hit[0]), tuple(hit[1])

        return project


def _event_window_coordinate(event: Any, region: Any) -> tuple[float, float] | None:
    """Convert window-relative mouse coordinates into a VIEW_3D window region."""

    x = float(event.mouse_x - region.x)
    y = float(event.mouse_y - region.y)
    if x < 0.0 or y < 0.0 or x >= region.width or y >= region.height:
        return None
    return x, y


def _flatten(strokes: list[list[Dab]]) -> list[Dab]:
    return [dab for stroke in strokes for dab in stroke]


def _seeds(
    strokes: list[list[Dab]], normals: list[list[Vector]], fallback: Vector
) -> list[Seed]:
    """One lattice seed per paint stroke, oriented along that stroke."""

    seeds = []
    for stroke, stroke_normals in zip(strokes, normals, strict=True):
        if not stroke or stroke[0].erase:
            continue
        start = Vector(stroke[0].center)
        flow = Vector(stroke[-1].center) - start
        if flow.length <= stroke[0].radius * 0.5:
            flow = fallback
        seeds.append(Seed(tuple(start), tuple(stroke_normals[0]), tuple(flow)))
    if not seeds:
        raise TransitionError("Paint at least one stroke on the surface target")
    return seeds


def _auto_cell(strokes: list[list[Dab]]) -> float:
    first = next(stroke[0] for stroke in strokes if stroke and not stroke[0].erase)
    return first.radius * AUTO_CELL_FACTOR


def plan_surface_paint(
    surface: TargetSurface,
    strokes: list[list[Dab]],
    normals: list[list[Vector]],
    *,
    cell: float,
    flow_angle: float,
    relax_iterations: int,
    fallback_flow: Vector,
    mirror: Mirror | None = None,
    bridge: bool = True,
) -> PaintGrid:
    seeds = _seeds(strokes, normals, fallback_flow)
    if cell <= 0.0:
        cell = _auto_cell(strokes)
    return grow_paint_grid(
        _flatten(strokes),
        cell=cell,
        seeds=seeds,
        flow_angle=flow_angle,
        project=surface.projector(cell * 2.0),
        relax_iterations=relax_iterations,
        mirror=mirror,
        bridge=bridge,
    )


def object_mirror_plane(obj: Object) -> Mirror:
    """The YZ plane of ``obj``: the plane a Mirror modifier on X reflects in."""

    normal = obj.matrix_world.inverted_safe().transposed().to_3x3() @ Vector(
        (1.0, 0.0, 0.0)
    )
    return Mirror(tuple(obj.matrix_world.translation), tuple(normal.normalized()))


def _boundary_verts(bm: Any) -> set[Any]:
    return {
        vertex
        for edge in bm.edges
        if len(edge.link_faces) == 1
        for vertex in edge.verts
    }


def _check_faces(faces: list[Any]) -> str | None:
    live = [face for face in faces if face.is_valid]
    if len(live) != len(faces):
        return "welding collapsed a quad"
    for face in live:
        if len(face.verts) != 4 or face.calc_area() <= 1.0e-12:
            return "a painted quad has no area"
        for edge in face.edges:
            if len(edge.link_faces) > 2:
                return "a painted edge is non-manifold"
            if len(edge.link_faces) == 2 and not edge.is_contiguous:
                return "painted faces have inconsistent winding"
    return None


def _write_grid(obj: Object, grid: PaintGrid, *, weld: bool) -> dict[str, Any]:
    mesh = obj.data
    bm = bmesh.from_edit_mesh(mesh)
    existing_boundary = _boundary_verts(bm)
    material_index = obj.active_material_index
    smooth_votes = Counter(face.smooth for face in bm.faces)
    smooth = smooth_votes.most_common(1)[0][0] if smooth_votes else True
    to_local = obj.matrix_world.inverted_safe()
    backup = bm.copy()
    try:
        vertex_map = {
            node: bm.verts.new(to_local @ Vector(position))
            for node, position in grid.positions.items()
        }
        faces = []
        for face_nodes in grid.faces:
            face = bm.faces.new([vertex_map[node] for node in face_nodes])
            face.material_index = material_index
            face.smooth = smooth
            faces.append(face)
        before = len(bm.verts)
        if weld and existing_boundary:
            new_boundary = [vertex_map[node] for node in grid.boundary_nodes]
            bmesh.ops.remove_doubles(
                bm,
                verts=new_boundary + list(existing_boundary),
                dist=grid.cell * WELD_FACTOR / max(obj.matrix_world.to_scale()),
            )
        welded = before - len(bm.verts)
        problem = _check_faces(faces)
        if problem is not None:
            raise TransitionError(problem)
        for element in (*bm.verts, *bm.edges, *bm.faces):
            element.select_set(False)
        for face in faces:
            face.select_set(True)
        bm.select_flush_mode()
        bm.normal_update()
        bmesh.update_edit_mesh(mesh, loop_triangles=True, destructive=True)
    except Exception:
        restore_bmesh(mesh, bm, backup)
        raise
    finally:
        backup.free()
    return {**grid_report(grid), "welded": welded, "had_uvs": bool(mesh.uv_layers)}


def build_surface_paint(
    context: Any,
    strokes: list[list[Dab]],
    normals: list[list[Vector]],
    *,
    target: Object,
    cell: float = 0.0,
    flow_angle: float = 0.0,
    relax_iterations: int = 3,
    weld: bool = True,
    fallback_flow: Vector | None = None,
    mirror_x: bool = False,
    bridge: bool = True,
) -> dict[str, Any]:
    """Build the painted quad grid into the active Edit Mode mesh."""

    obj = context.edit_object
    if obj is None or obj.type != "MESH" or obj.mode != "EDIT":
        raise TransitionError("Surface Paint requires a mesh in Edit Mode")
    if target is obj:
        raise TransitionError(
            "Paint on a separate high-poly target, not the retopo mesh"
        )
    if obj.data.shape_keys is not None:
        raise TransitionError("Meshes with shape keys cannot receive new topology")
    grid = plan_surface_paint(
        TargetSurface(context, target),
        strokes,
        normals,
        cell=cell,
        flow_angle=flow_angle,
        relax_iterations=relax_iterations,
        fallback_flow=fallback_flow or Vector((1.0, 0.0, 0.0)),
        mirror=object_mirror_plane(obj) if mirror_x else None,
        bridge=bridge,
    )
    if not grid.faces:
        raise TransitionError(
            "The painted area is smaller than one cell; paint wider or shrink the cell"
        )
    try:
        return _write_grid(obj, grid, weld=weld)
    except TransitionError as exc:
        if not weld:
            raise
        stats = _write_grid(obj, grid, weld=False)
        stats["weld_skipped"] = str(exc)
        return stats


def _circle(center: Vector, normal: Vector, radius: float) -> list[tuple]:
    axis = normal.orthogonal().normalized()
    other = normal.cross(axis)
    points = [
        center + (axis * cos(angle) + other * sin(angle)) * radius
        for angle in (index * 2.0 * pi / 24 for index in range(24))
    ]
    return [
        tuple(point)
        for first, second in zip(points, points[1:] + points[:1], strict=True)
        for point in (first, second)
    ]


class QT_OT_surface_paint(Operator):
    bl_idname = "mesh.quad_transition_surface_paint"
    bl_label = "Surface Paint Quads"
    bl_description = (
        "Paint where you want topology on a high-poly target; a surface-following "
        "quad grid fills the painted area and is added to this mesh"
    )
    bl_options = {"REGISTER", "UNDO", "BLOCKING"}

    target_name: StringProperty(name="Surface Target", default="")
    cell_size: FloatProperty(
        name="Cell Size",
        description="Quad edge length; zero picks it from the first brush dab",
        min=0.0,
        default=0.0,
        subtype="DISTANCE",
    )
    brush_pixels: IntProperty(name="Brush Size", min=4, max=500, default=40)
    flow_angle: FloatProperty(name="Flow Rotation", default=0.0, subtype="ANGLE")
    relax_iterations: IntProperty(name="Relax", min=0, max=20, default=3)
    weld: BoolProperty(
        name="Weld to Open Edges",
        description="Merge painted border vertices onto nearby open edges of this mesh",
        default=True,
    )
    mirror_x: BoolProperty(
        name="Mirror X",
        description="Paint one half; the other half is mirrored across local X",
        default=False,
    )
    bridge: BoolProperty(
        name="Join Patches",
        description=(
            "Join facing borders of separate strokes with quads, using 5-3 style "
            "transitions where their loop counts differ"
        ),
        default=True,
    )

    @classmethod
    def poll(cls, context: Any) -> bool:
        return (
            context.edit_object is not None
            and context.edit_object.type == "MESH"
            and context.area is not None
            and context.area.type == "VIEW_3D"
        )

    def _world_radius(self, point: Vector) -> float:
        center = view3d_utils.location_3d_to_region_2d(
            self._region, self._region_3d, point
        )
        if center is None:
            return 0.0
        edge = view3d_utils.region_2d_to_location_3d(
            self._region,
            self._region_3d,
            center + Vector((self.brush_pixels, 0)),
            point,
        )
        return (edge - point).length

    def _dab(self, event: Any) -> None:
        coordinate = _event_window_coordinate(event, self._region)
        if coordinate is None:
            return
        self._mouse = coordinate
        hit = self._surface.ray_cast(self._region, self._region_3d, coordinate)
        if hit is None:
            return
        point, normal = hit
        radius = self._world_radius(point)
        if radius <= 0.0:
            return
        stroke = self._strokes[-1]
        if stroke and (Vector(stroke[-1].center) - point).length < radius * 0.3:
            return
        stroke.append(Dab(tuple(point), radius, self._erasing, tuple(normal)))
        self._normals[-1].append(normal)

    def _cell(self) -> float:
        if self.cell_size > 0.0:
            return self.cell_size
        try:
            return _auto_cell(self._strokes)
        except StopIteration:
            return 0.0

    def _replan(self) -> None:
        self._grid = None
        self._error = ""
        if not any(stroke for stroke in self._strokes):
            return
        try:
            self._grid = plan_surface_paint(
                self._surface,
                self._strokes,
                self._normals,
                cell=self.cell_size,
                flow_angle=self.flow_angle,
                relax_iterations=self.relax_iterations,
                fallback_flow=self._view_up,
                mirror=self._mirror_plane if self.mirror_x else None,
                bridge=self.bridge,
            )
        except TransitionError as exc:
            self._error = str(exc)

    def _draw_view(self) -> None:
        if bpy.context.area != self._area:
            return
        line_shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
        fill_shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        viewport = gpu.state.viewport_get()
        # Lift overlays off the surface so the depth test does not dash them.
        paths, erase = [], []
        for stroke, normals in zip(self._strokes, self._normals, strict=True):
            if not stroke:
                continue
            lifted = [
                Vector(dab.center) + normal * dab.radius * 0.04
                for dab, normal in zip(stroke, normals, strict=True)
            ]
            if stroke[0].erase:
                for dab, normal, point in zip(stroke, normals, lifted, strict=True):
                    erase.extend(_circle(point, normal, dab.radius))
                continue
            paths.extend(_circle(lifted[0], normals[0], stroke[0].radius * 0.15))
            for first, second in zip(lifted, lifted[1:]):
                paths.extend((tuple(first), tuple(second)))
        grid_lines, grid_fill = [], []
        if self._grid is not None:
            lift = self._grid.cell * 0.06
            positions = {
                node: tuple(Vector(point) + Vector(self._grid.normals[node]) * lift)
                for node, point in self._grid.positions.items()
            }
            for face in self._grid.faces:
                points = [positions[node] for node in face]
                grid_fill.extend((points[0], points[1], points[2]))
                grid_fill.extend((points[0], points[2], points[3]))
                for first, second in zip(points, points[1:] + points[:1], strict=True):
                    grid_lines.extend((first, second))
        try:
            gpu.state.blend_set("ALPHA")
            gpu.state.depth_test_set("LESS_EQUAL")
            if grid_fill:
                fill_shader.bind()
                fill_shader.uniform_float("color", (0.15, 0.75, 0.45, 0.25))
                batch_for_shader(fill_shader, "TRIS", {"pos": grid_fill}).draw(
                    fill_shader
                )
            line_shader.bind()
            line_shader.uniform_float("viewportSize", (viewport[2], viewport[3]))
            for lines, color, width in (
                (grid_lines, (0.2, 1.0, 0.55, 0.95), 1.5),
                (paths, (0.15, 0.6, 1.0, 0.8), 4.0),
                (erase, (1.0, 0.25, 0.25, 0.8), 2.0),
            ):
                if lines:
                    line_shader.uniform_float("color", color)
                    line_shader.uniform_float("lineWidth", width)
                    batch_for_shader(line_shader, "LINES", {"pos": lines}).draw(
                        line_shader
                    )
        finally:
            gpu.state.depth_test_set("NONE")
            gpu.state.blend_set("NONE")

    def _draw_hud(self) -> None:
        if bpy.context.area != self._area:
            return
        cell = self._cell()

        def toggle(label: str, on: bool):
            state = "on" if on else "off"
            return ("chip", hud.TEAL if on else hud.MUTED, f"{label} {state}")

        rows = [
            [
                ("chip", hud.SKY, f"brush {self.brush_pixels}px"),
                ("chip", hud.VIOLET, f"cell {cell:.3g}" if cell else "cell auto"),
                ("chip", hud.AMBER, f"flow {degrees(self.flow_angle):+.0f}\u00b0"),
                toggle("mirror", self.mirror_x),
                toggle("join", self.bridge),
            ]
        ]
        if self._grid is not None:
            report = grid_report(self._grid)
            poles = report["interior_poles"]
            rows.append(
                [
                    ("chip", hud.TEAL, f"{report['quads']} quads"),
                    ("chip", hud.SKY, f"{report['vertices']} verts"),
                    ("chip", hud.MAGENTA if poles else hud.TEAL, f"{poles} poles"),
                    ("chip", hud.VIOLET, f"{report['bridges']} joins"),
                ]
            )
            stopped = report["rejected_folds"] + report["rejected_overlaps"]
            if stopped:
                rows.append(
                    [
                        ("chip", hud.CORAL, f"{stopped} stopped"),
                        ("where the surface folds back or patches meet", hud.MUTED),
                    ]
                )
        elif self._error:
            rows.append([("chip", hud.CORAL, "!"), (self._error, hud.INK)])
        else:
            rows.append([("Paint over the target; the grid appears live.", hud.INK)])
        rows.extend(
            (
                [("Each stroke's direction becomes its edge-loop direction.", hud.INK)],
                [
                    ("Grid vertices have 4 edges (no poles);", hud.MUTED),
                    ("joins", hud.VIOLET),
                    ("add", hud.MUTED),
                    ("poles", hud.MAGENTA),
                    ("on purpose.", hud.MUTED),
                ],
                [
                    ("key", "LMB"),
                    ("paint", hud.MUTED),
                    ("key", "Ctrl"),
                    ("erase", hud.MUTED),
                    ("key", "[ ]"),
                    ("brush", hud.MUTED),
                    ("key", "- ="),
                    ("cell", hud.MUTED),
                    ("key", "R"),
                    ("flow", hud.MUTED),
                ],
                [
                    ("key", "X"),
                    ("mirror", hud.MUTED),
                    ("key", "J"),
                    ("join", hud.MUTED),
                    ("key", "Bksp"),
                    ("undo", hud.MUTED),
                    ("key", "Enter"),
                    ("build", hud.MUTED),
                    ("key", "Esc"),
                    ("cancel", hud.MUTED),
                ],
            )
        )
        hud.draw_corner_card(self._area, self._region, "Surface Paint", hud.TEAL, rows)
        if self._mouse is not None:
            shader = gpu.shader.from_builtin("UNIFORM_COLOR")
            x, y = self._mouse
            ring = [
                (
                    x + cos(index * pi / 16) * self.brush_pixels,
                    y + sin(index * pi / 16) * self.brush_pixels,
                )
                for index in range(32)
            ]
            shader.bind()
            shader.uniform_float(
                "color", (1.0, 0.3, 0.3, 0.8) if self._erasing else (1, 1, 1, 0.6)
            )
            batch_for_shader(shader, "LINE_LOOP", {"pos": ring}).draw(shader)

    def _cleanup(self, context: Any) -> None:
        for handle in (self._view_handle, self._hud_handle):
            if handle is not None:
                SpaceView3D.draw_handler_remove(handle, "WINDOW")
        self._view_handle = self._hud_handle = None
        if context.workspace is not None:
            context.workspace.status_text_set(None)
        self._area.tag_redraw()

    def invoke(self, context: Any, _event: Any):
        target = bpy.data.objects.get(self.target_name) if self.target_name else None
        if target is None or target.type != "MESH":
            self.report({"ERROR"}, "Choose a Surface Target mesh before painting")
            return {"CANCELLED"}
        if target is context.edit_object:
            self.report({"ERROR"}, "Choose a separate high-poly surface target")
            return {"CANCELLED"}
        self._area = context.area
        self._region = next(
            (region for region in context.area.regions if region.type == "WINDOW"),
            None,
        )
        if self._region is None:
            self.report({"ERROR"}, "The 3D View has no drawable window region")
            return {"CANCELLED"}
        self._region_3d = context.space_data.region_3d
        self._view_up = self._region_3d.view_rotation @ Vector((0.0, 1.0, 0.0))
        self._surface = TargetSurface(context, target)
        self._target = target
        self._object_name = context.edit_object.name
        self._mirror_plane = object_mirror_plane(context.edit_object)
        self._strokes: list[list[Dab]] = []
        self._normals: list[list[Vector]] = []
        self._painting = self._erasing = False
        self._grid: PaintGrid | None = None
        self._error = ""
        self._mouse = None
        self._view_handle = SpaceView3D.draw_handler_add(
            self._draw_view, (), "WINDOW", "POST_VIEW"
        )
        self._hud_handle = SpaceView3D.draw_handler_add(
            self._draw_hud, (), "WINDOW", "POST_PIXEL"
        )
        context.window_manager.modal_handler_add(self)
        context.workspace.status_text_set(
            "Surface Paint: LMB paint, Ctrl+LMB erase, [ ] brush, - = cell, "
            "R flow, X mirror, J join, Backspace undo, Enter build, Esc cancel"
        )
        self._area.tag_redraw()
        return {"RUNNING_MODAL"}

    def _finish(self, context: Any):
        try:
            stats = build_surface_paint(
                context,
                self._strokes,
                self._normals,
                target=self._target,
                cell=self.cell_size,
                flow_angle=self.flow_angle,
                relax_iterations=self.relax_iterations,
                weld=self.weld,
                fallback_flow=self._view_up,
                mirror_x=self.mirror_x,
                bridge=self.bridge,
            )
        except TransitionError as exc:
            self.report({"ERROR"}, str(exc))
            return {"RUNNING_MODAL"}
        self._cleanup(context)
        self.report(
            {"INFO"},
            f"Painted {stats['quads']} quads, {stats['welded']} border vertices welded",
        )
        if "weld_skipped" in stats:
            self.report({"WARNING"}, f"Built without welding: {stats['weld_skipped']}")
        if stats["had_uvs"]:
            self.report({"WARNING"}, "Unwrap the new quads before baking")
        return {"FINISHED"}

    def modal(self, context: Any, event: Any):
        if context.edit_object is None or context.edit_object.name != self._object_name:
            self._cleanup(context)
            self.report({"WARNING"}, "Active retopo mesh changed; painting cancelled")
            return {"CANCELLED"}
        self._area.tag_redraw()
        pressed = event.value == "PRESS"
        if event.type in {"ESC", "RIGHTMOUSE"} and pressed:
            self._cleanup(context)
            return {"CANCELLED"}
        if event.type in {"RET", "NUMPAD_ENTER"} and pressed:
            return self._finish(context)
        if event.type == "LEFTMOUSE":
            if pressed:
                self._painting = True
                self._erasing = event.ctrl
                self._strokes.append([])
                self._normals.append([])
                self._dab(event)
            elif self._painting:
                self._painting = False
                if not self._strokes[-1]:
                    self._strokes.pop()
                    self._normals.pop()
                self._replan()
            return {"RUNNING_MODAL"}
        if event.type == "MOUSEMOVE":
            if self._painting:
                self._dab(event)
            else:
                self._mouse = _event_window_coordinate(event, self._region)
                self._erasing = event.ctrl
            return {"RUNNING_MODAL"}
        if not pressed:
            return (
                {"PASS_THROUGH"}
                if event.type.startswith(("WHEEL", "MIDDLEMOUSE", "NDOF", "TRACKPAD"))
                else {"RUNNING_MODAL"}
            )
        if event.type in {"LEFT_BRACKET", "RIGHT_BRACKET"}:
            factor = 1.15 if event.type == "RIGHT_BRACKET" else 1.0 / 1.15
            self.brush_pixels = max(4, min(500, round(self.brush_pixels * factor)))
            return {"RUNNING_MODAL"}
        if event.type in {"MINUS", "EQUAL"}:
            if self._strokes or self.cell_size > 0.0:
                factor = 1.1 if event.type == "EQUAL" else 1.0 / 1.1
                self.cell_size = self._cell() * factor
                self._replan()
            return {"RUNNING_MODAL"}
        if event.type == "X":
            self.mirror_x = not self.mirror_x
            self._replan()
            return {"RUNNING_MODAL"}
        if event.type == "J":
            self.bridge = not self.bridge
            self._replan()
            return {"RUNNING_MODAL"}
        if event.type == "R":
            self.flow_angle += radians(-15.0 if event.shift else 15.0)
            self._replan()
            return {"RUNNING_MODAL"}
        if event.type == "BACK_SPACE" and self._strokes:
            self._strokes.pop()
            self._normals.pop()
            self._replan()
            return {"RUNNING_MODAL"}
        if event.type.startswith(("WHEEL", "MIDDLEMOUSE", "NDOF", "TRACKPAD")) or (
            event.type.startswith("NUMPAD")
        ):
            return {"PASS_THROUGH"}
        return {"RUNNING_MODAL"}

    def cancel(self, context: Any) -> None:
        self._cleanup(context)


CLASSES = (QT_OT_surface_paint,)


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
