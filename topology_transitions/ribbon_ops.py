"""Interactive surface drawing for connected multi-lane quad ribbons."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

import blf
import bmesh
import bpy
import gpu
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    StringProperty,
)
from bpy.types import Object, Operator, SpaceView3D
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from .core import TransitionError, transition_items
from .edit_mesh import restore_bmesh
from .operators import POLE_SIDE_ITEMS
from .ribbon import RibbonPlan, build_ribbon_plan, ordered_open_edge_chain

RIBBON_LAYOUT_ITEMS = (
    (
        "UNIFORM",
        "Connected Multi-Strip",
        "Grow every selected boundary edge as one connected quad lane",
    ),
    (
        "TRANSITION",
        "Transition Ribbon",
        "Draw the selected 5 to 3, 3 to 1, or other transition pattern",
    ),
)


@dataclass(frozen=True)
class AnchorSnapshot:
    object_name: str
    vertex_ids: tuple[int, ...]
    world_positions: tuple[Vector, ...]
    world_normal: Vector
    width: float
    material_index: int
    smooth: bool

    @property
    def lanes(self) -> int:
        return len(self.vertex_ids) - 1

    @property
    def center(self) -> Vector:
        return sum(self.world_positions, Vector()) / len(self.world_positions)


class TargetSurface:
    """Evaluated target-mesh ray and nearest-surface queries in world space."""

    def __init__(self, context: Any, target: Object) -> None:
        if target.type != "MESH":
            raise TransitionError("The drawing target must be a mesh object")
        self.target = target
        self.depsgraph = context.evaluated_depsgraph_get()
        self.evaluated = target.evaluated_get(self.depsgraph)
        self.to_world = self.evaluated.matrix_world.copy()
        self.to_local = self.to_world.inverted_safe()
        self.normal_to_world = self.to_world.to_3x3().inverted_safe().transposed()

    def ray_cast(
        self, region: Any, region_3d: Any, coordinate: tuple[float, float]
    ) -> tuple[Vector, Vector] | None:
        origin_world = view3d_utils.region_2d_to_origin_3d(
            region, region_3d, coordinate, clamp=1.0e6
        )
        direction_world = view3d_utils.region_2d_to_vector_3d(
            region, region_3d, coordinate
        )
        origin_local = self.to_local @ origin_world
        direction_local = self.to_local.to_3x3() @ direction_world
        if direction_local.length_squared <= 1.0e-16:
            return None
        direction_local.normalize()
        success, location, normal, _face_index = self.evaluated.ray_cast(
            origin_local, direction_local, distance=1.0e20
        )
        if not success:
            return None
        world_normal = self.normal_to_world @ normal
        if world_normal.length_squared:
            world_normal.normalize()
        return self.to_world @ location, world_normal

    def closest(
        self, coordinate_world: Vector, limit: float = 0.0
    ) -> tuple[Vector, Vector] | None:
        local = self.to_local @ coordinate_world
        success, location, normal, _face_index = self.evaluated.closest_point_on_mesh(
            local, distance=limit if limit > 0.0 else 1.0e20
        )
        if not success:
            return None
        world_normal = self.normal_to_world @ normal
        if world_normal.length_squared:
            world_normal.normalize()
        return self.to_world @ location, world_normal


def _anchor_snapshot(context: Any, *, flip_width: bool = False) -> AnchorSnapshot:
    obj = context.edit_object
    if obj is None or obj.type != "MESH" or obj.mode != "EDIT":
        raise TransitionError("Multi-Strip Draw requires a mesh in Edit Mode")
    if len(context.objects_in_mode_unique_data) != 1:
        raise TransitionError("Draw on one mesh data-block at a time")
    if obj.data.shape_keys is not None:
        raise TransitionError("Meshes with shape keys cannot grow new topology")
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.verts.index_update()
    bm.edges.index_update()
    bm.normal_update()
    all_selected_edges = [edge for edge in bm.edges if edge.select]
    if any(edge.hide for edge in all_selected_edges):
        raise TransitionError("Hidden edges cannot be part of the drawing boundary")
    selected_edges = [edge for edge in all_selected_edges if not edge.hide]
    if not selected_edges:
        raise TransitionError("Select one open boundary edge chain in Edge mode")
    if any(len(edge.link_faces) != 1 for edge in selected_edges):
        raise TransitionError(
            "Every selected edge must be on the open boundary of the retopo mesh"
        )
    ordered_ids = ordered_open_edge_chain(
        {
            edge.index: (edge.verts[0].index, edge.verts[1].index)
            for edge in selected_edges
        },
        tuple(edge.index for edge in selected_edges),
    )
    if flip_width:
        ordered_ids = tuple(reversed(ordered_ids))
    ordered_vertices = [bm.verts[index] for index in ordered_ids]
    world_positions = tuple(obj.matrix_world @ vertex.co for vertex in ordered_vertices)
    width = sum(
        (second - first).length
        for first, second in zip(world_positions, world_positions[1:])
    )
    if width <= 1.0e-8:
        raise TransitionError("The selected boundary chain has no measurable width")
    linked_faces = list({face for edge in selected_edges for face in edge.link_faces})
    normal_local = sum(
        (face.normal * max(face.calc_area(), 1.0e-12) for face in linked_faces),
        Vector(),
    )
    normal_matrix = obj.matrix_world.to_3x3().inverted_safe().transposed()
    normal_world = normal_matrix @ normal_local
    if normal_world.length_squared <= 1.0e-16:
        raise TransitionError("The selected boundary has no stable surface normal")
    normal_world.normalize()
    material_index = Counter(
        face.material_index for face in linked_faces
    ).most_common(1)[0][0]
    smooth = sum(face.smooth for face in linked_faces) * 2 >= len(linked_faces)
    return AnchorSnapshot(
        object_name=obj.name,
        vertex_ids=ordered_ids,
        world_positions=world_positions,
        world_normal=normal_world,
        width=width,
        material_index=material_index,
        smooth=smooth,
    )


def _stroke_with_anchor(
    anchor: AnchorSnapshot,
    points: list[Vector],
    normals: list[Vector] | None,
) -> tuple[list[Vector], list[Vector]]:
    if not points:
        raise TransitionError("Draw a stroke away from the selected boundary")
    if len(points) > 1 and (points[-1] - anchor.center).length < (
        points[0] - anchor.center
    ).length:
        points = list(reversed(points))
        normals = list(reversed(normals)) if normals else None
    result_points = [anchor.center.copy()]
    result_normals = [anchor.world_normal.copy()]
    for index, point in enumerate(points):
        if (point - result_points[-1]).length <= 1.0e-7:
            continue
        result_points.append(point.copy())
        normal = normals[index].copy() if normals and index < len(normals) else None
        if normal is None or normal.length_squared <= 1.0e-16:
            normal = result_normals[-1].copy()
        else:
            normal.normalize()
        result_normals.append(normal)
    if len(result_points) < 2:
        raise TransitionError("The drawn stroke has no measurable length")
    return result_points, result_normals


def _path_distances(points: list[Vector]) -> tuple[list[float], float]:
    cumulative = [0.0]
    for first, second in zip(points, points[1:]):
        cumulative.append(cumulative[-1] + (second - first).length)
    if cumulative[-1] <= 1.0e-8:
        raise TransitionError("The drawn stroke has no measurable length")
    return cumulative, cumulative[-1]


def _sample_path(
    points: list[Vector],
    normals: list[Vector],
    cumulative: list[float],
    total: float,
    factor: float,
) -> tuple[Vector, Vector]:
    target = max(0.0, min(1.0, factor)) * total
    for index in range(len(points) - 1):
        start = cumulative[index]
        end = cumulative[index + 1]
        if target <= end or index == len(points) - 2:
            local = 0.0 if end <= start else (target - start) / (end - start)
            point = points[index].lerp(points[index + 1], local)
            normal = normals[index].lerp(normals[index + 1], local)
            if normal.length_squared <= 1.0e-16:
                normal = normals[index].copy()
            normal.normalize()
            return point, normal
    return points[-1].copy(), normals[-1].copy()


def _fit_world_coordinates(
    plan: RibbonPlan,
    anchor: AnchorSnapshot,
    stroke_points: list[Vector],
    stroke_normals: list[Vector] | None,
    *,
    width_scale: float,
    surface: TargetSurface | None,
    project_limit: float,
) -> dict[str, Vector]:
    points, normals = _stroke_with_anchor(anchor, stroke_points, stroke_normals)
    cumulative, total = _path_distances(points)
    unique_s = sorted({vertex.s for vertex in plan.vertices.values()})
    frames: dict[float, tuple[Vector, Vector, Vector]] = {}
    previous_lateral = None
    anchor_direction = anchor.world_positions[-1] - anchor.world_positions[0]
    for s in unique_s:
        center, normal = _sample_path(points, normals, cumulative, total, s)
        delta = min(0.02, 0.25 / max(len(unique_s), 2))
        before, _before_normal = _sample_path(
            points, normals, cumulative, total, max(0.0, s - delta)
        )
        after, _after_normal = _sample_path(
            points, normals, cumulative, total, min(1.0, s + delta)
        )
        tangent = after - before
        if tangent.length_squared <= 1.0e-16:
            tangent = points[-1] - points[0]
        tangent.normalize()
        lateral = normal.cross(tangent)
        if lateral.length_squared <= 1.0e-16:
            lateral = anchor_direction.copy()
        lateral.normalize()
        if previous_lateral is None:
            if lateral.dot(anchor_direction) < 0.0:
                lateral.negate()
        elif lateral.dot(previous_lateral) < 0.0:
            lateral.negate()
        previous_lateral = lateral.copy()
        frames[s] = (center, normal, lateral)

    coordinates = {
        key: position.copy()
        for key, position in zip(
            plan.anchor_keys, anchor.world_positions, strict=True
        )
    }
    width = anchor.width * width_scale
    for key, spec in plan.vertices.items():
        if key in coordinates:
            continue
        center, _normal, lateral = frames[spec.s]
        coordinate = center + lateral * ((spec.u - 0.5) * width)
        if surface is not None:
            nearest = surface.closest(coordinate, project_limit)
            if nearest is None:
                raise TransitionError(
                    "A ribbon vertex could not reach the drawing target; "
                    "increase Project Limit or simplify the stroke"
                )
            coordinate = nearest[0]
        coordinates[key] = coordinate
    return coordinates


def _quad_normal(points: list[Vector]) -> Vector:
    return (points[1] - points[0]).cross(points[2] - points[0]) + (
        points[2] - points[0]
    ).cross(points[3] - points[0])


def _event_window_coordinate(event: Any, region: Any) -> tuple[float, float] | None:
    """Convert window-relative mouse coordinates into a VIEW_3D window region."""

    x = float(event.mouse_x - region.x)
    y = float(event.mouse_y - region.y)
    if x < 0.0 or y < 0.0 or x >= region.width or y >= region.height:
        return None
    return x, y


def grow_ribbon_from_stroke(
    context: Any,
    stroke_world_points: list[Vector],
    stroke_world_normals: list[Vector] | None = None,
    *,
    layout: str,
    transition: str,
    segments: int,
    width_scale: float,
    pole_side: str,
    mirror: bool,
    pole_spacing: float,
    target: Object | None,
    project_limit: float = 0.0,
    flip_width: bool = False,
) -> dict[str, Any]:
    """Create one connected all-quad ribbon, welding its first row to the mesh."""

    anchor = _anchor_snapshot(context, flip_width=flip_width)
    obj = context.edit_object
    if target is obj:
        raise TransitionError("The retopo mesh cannot also be its drawing target")
    surface = TargetSurface(context, target) if target is not None else None
    plan = build_ribbon_plan(
        layout,
        anchor.lanes,
        segments,
        transition=transition,
        pole_side=pole_side,
        mirror=mirror,
        pole_spacing=pole_spacing,
    )
    coordinates_world = _fit_world_coordinates(
        plan,
        anchor,
        stroke_world_points,
        stroke_world_normals,
        width_scale=width_scale,
        surface=surface,
        project_limit=project_limit,
    )

    mesh = obj.data
    bm = bmesh.from_edit_mesh(mesh)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    anchor_vertices = [bm.verts[index] for index in anchor.vertex_ids]
    if any(not vertex.is_valid for vertex in anchor_vertices):
        raise TransitionError("The selected boundary changed while drawing")
    anchor_edges = []
    for first, second in zip(anchor_vertices, anchor_vertices[1:]):
        edge = bm.edges.get((first, second))
        if edge is None or len(edge.link_faces) != 1:
            raise TransitionError("The selected boundary is no longer open")
        anchor_edges.append(edge)

    world_to_object = obj.matrix_world.inverted_safe()
    coordinates_local = {
        key: world_to_object @ coordinate
        for key, coordinate in coordinates_world.items()
    }
    face_normals = []
    for face in plan.faces:
        points_world = [coordinates_world[key] for key in face]
        normal = _quad_normal(points_world)
        if normal.length_squared <= 1.0e-16:
            raise TransitionError(
                "The stroke folded a quad to zero area; redraw with a wider curve"
            )
        face_normals.append(normal)
    anchor_key_set = set(plan.anchor_keys)
    reference_index = next(
        (
            index
            for index, face in enumerate(plan.faces)
            if len(anchor_key_set.intersection(face)) >= 2
        ),
        0,
    )
    reverse_all = face_normals[reference_index].dot(anchor.world_normal) < 0.0
    oriented_faces = [
        tuple(reversed(face)) if reverse_all else face for face in plan.faces
    ]

    backup = bm.copy()
    created_vertices = []
    created_faces = []
    vertex_map = {
        key: vertex
        for key, vertex in zip(plan.anchor_keys, anchor_vertices, strict=True)
    }
    try:
        for key in plan.vertices:
            if key in vertex_map:
                continue
            vertex = bm.verts.new(coordinates_local[key])
            vertex_map[key] = vertex
            created_vertices.append(vertex)
        for face_keys in oriented_faces:
            face = bm.faces.new([vertex_map[key] for key in face_keys])
            face.material_index = anchor.material_index
            face.smooth = anchor.smooth
            created_faces.append(face)
        bm.normal_update()
        output_vertices = [vertex_map[key] for key in plan.output_keys]
        output_edges = [
            bm.edges.get((first, second))
            for first, second in zip(output_vertices, output_vertices[1:])
        ]
        if any(len(edge.link_faces) != 2 for edge in anchor_edges):
            raise TransitionError("The new ribbon did not weld to every anchor edge")
        if any(
            edge is None or len(edge.link_faces) != 1 for edge in output_edges
        ):
            raise TransitionError("The new ribbon did not expose its output boundary")
        if any(
            len(edge.link_faces) > 2
            for face in created_faces
            for edge in face.edges
        ):
            raise TransitionError("The new ribbon contains a non-manifold edge")
        if any(
            len(edge.link_faces) == 2 and not edge.is_contiguous
            for face in created_faces
            for edge in face.edges
        ):
            raise TransitionError("The new ribbon contains inconsistent face winding")
        if any(
            len(face.verts) != 4 or face.calc_area() <= 1.0e-12
            for face in created_faces
        ):
            raise TransitionError("The new ribbon failed the all-quad area check")
        if any(len(vertex_map[key].link_edges) != 3 for key in plan.pole_keys):
            raise TransitionError("The drawn transition pole valence is invalid")
        for face in bm.faces:
            face.select_set(False)
        for edge in bm.edges:
            edge.select_set(False)
        for vertex in bm.verts:
            vertex.select_set(False)
        for edge in output_edges:
            edge.select_set(True)
        bm.select_mode = {"EDGE"}
        context.tool_settings.mesh_select_mode = (False, True, False)
        bm.select_flush_mode()
        bmesh.update_edit_mesh(mesh, loop_triangles=True, destructive=True)
    except Exception:
        restore_bmesh(mesh, bm, backup)
        raise
    finally:
        backup.free()
    return {
        "layout": layout,
        "input_count": plan.input_count,
        "output_count": plan.output_count,
        "segments": segments,
        "new_faces": len(created_faces),
        "new_vertices": len(created_vertices),
        "poles": len(plan.pole_keys),
        "anchor_edges": len(anchor_edges),
        "output_edges": len(output_edges),
        "projected": target is not None,
        "had_uvs": bool(mesh.uv_layers),
    }


class QT_OT_draw_multi_strip(Operator):
    bl_idname = "mesh.quad_transition_draw_multi_strip"
    bl_label = "Draw Connected Multi-Strip"
    bl_description = (
        "Draw many connected quad lanes from one selected open boundary chain"
    )
    bl_options = {"REGISTER", "UNDO", "BLOCKING"}

    layout: EnumProperty(name="Ribbon Layout", items=RIBBON_LAYOUT_ITEMS)
    transition: EnumProperty(name="Transition", items=transition_items())
    segments: IntProperty(name="Segments", min=1, max=64, default=6)
    width_scale: FloatProperty(name="Width", min=0.25, max=4.0, default=1.0)
    flip_width: BoolProperty(
        name="Flip Width",
        description="Reverse which selected boundary endpoint is treated as left",
        default=False,
    )
    pole_side: EnumProperty(name="Pole Side", items=POLE_SIDE_ITEMS, default="CENTER")
    mirror: BoolProperty(name="Mirror", default=False)
    pole_spacing: FloatProperty(name="Pole Spacing", min=0.5, max=2.0, default=1.0)
    target_name: StringProperty(name="Surface Target", default="")
    project_limit: FloatProperty(name="Project Limit", min=0.0, default=0.0)

    _area = None
    _anchor: AnchorSnapshot | None = None
    _surface: TargetSurface | None = None
    _target: Object | None = None
    _drawing = False
    _stroke_points: list[Vector]
    _stroke_normals: list[Vector]
    _draw_view_handle = None
    _draw_hud_handle = None
    _window_region = None
    _region_3d = None

    @classmethod
    def poll(cls, context: Any) -> bool:
        return (
            context.edit_object is not None
            and context.edit_object.type == "MESH"
            and context.edit_object.mode == "EDIT"
            and context.area is not None
            and context.area.type == "VIEW_3D"
        )

    def _hit(self, context: Any, event: Any) -> tuple[Vector, Vector] | None:
        coordinate = _event_window_coordinate(event, self._window_region)
        if coordinate is None:
            return None
        return self._surface.ray_cast(
            self._window_region,
            self._region_3d,
            coordinate,
        )

    def _append_hit(self, hit: tuple[Vector, Vector] | None) -> None:
        if hit is None:
            return
        point, normal = hit
        minimum = max(self._anchor.width * 0.015, 1.0e-5)
        if self._stroke_points and (point - self._stroke_points[-1]).length < minimum:
            return
        self._stroke_points.append(point)
        self._stroke_normals.append(normal)

    def _preview_geometry(
        self,
    ) -> tuple[
        list[tuple[float, float, float]],
        list[tuple[float, float, float]],
        list[tuple[float, float, float]],
    ]:
        if not self._stroke_points:
            return [], [], []
        try:
            plan = build_ribbon_plan(
                self.layout,
                self._anchor.lanes,
                self.segments,
                transition=self.transition,
                pole_side=self.pole_side,
                mirror=self.mirror,
                pole_spacing=self.pole_spacing,
            )
            coordinates = _fit_world_coordinates(
                plan,
                self._anchor,
                self._stroke_points,
                self._stroke_normals,
                width_scale=self.width_scale,
                surface=self._surface,
                project_limit=self.project_limit,
            )
        except (TransitionError, ValueError):
            return [], [], []
        edges = {
            tuple(sorted((first, second)))
            for face in plan.faces
            for first, second in zip(face, (*face[1:], face[0]), strict=True)
        }
        lines = [
            tuple(coordinates[key])
            for edge in edges
            for key in edge
        ]
        fill = []
        pole_fill = []
        for face in plan.faces:
            destination = (
                pole_fill
                if any(key in plan.pole_keys for key in face)
                else fill
            )
            for first, second, third in (
                (face[0], face[1], face[2]),
                (face[0], face[2], face[3]),
            ):
                destination.extend(
                    (
                        tuple(coordinates[first]),
                        tuple(coordinates[second]),
                        tuple(coordinates[third]),
                    )
                )
        return lines, fill, pole_fill

    def _draw_view(self) -> None:
        if self._area != bpy.context.area or self._anchor is None:
            return
        lines, fill, pole_fill = self._preview_geometry()
        if not lines:
            return
        fill_shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        line_shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
        line_batch = batch_for_shader(line_shader, "LINES", {"pos": lines})
        viewport = gpu.state.viewport_get()
        try:
            gpu.state.blend_set("ALPHA")
            gpu.state.depth_test_set("LESS_EQUAL")
            if fill:
                fill_batch = batch_for_shader(fill_shader, "TRIS", {"pos": fill})
                fill_shader.bind()
                fill_shader.uniform_float("color", (0.03, 0.65, 1.0, 0.22))
                fill_batch.draw(fill_shader)
            if pole_fill:
                pole_batch = batch_for_shader(
                    fill_shader, "TRIS", {"pos": pole_fill}
                )
                fill_shader.bind()
                fill_shader.uniform_float("color", (1.0, 0.08, 0.65, 0.34))
                pole_batch.draw(fill_shader)
            line_shader.bind()
            line_shader.uniform_float("color", (0.05, 0.8, 1.0, 0.95))
            line_shader.uniform_float("lineWidth", 2.5)
            line_shader.uniform_float("viewportSize", (viewport[2], viewport[3]))
            line_batch.draw(line_shader)
        finally:
            gpu.state.depth_test_set("NONE")
            gpu.state.blend_set("NONE")

    def _draw_hud(self) -> None:
        if self._area != bpy.context.area or self._anchor is None:
            return
        layout_label = (
            f"{self.transition.replace('_', ' ').title()} transition"
            if self.layout == "TRANSITION"
            else f"{self._anchor.lanes} connected lanes"
        )
        lines = (
            "CONNECTED MULTI-STRIP DRAW",
            f"{layout_label} | {self.segments} segments | width {self.width_scale:.2f}",
            "LMB drag | Wheel rows | Shift+wheel width | Enter finish | Esc cancel",
        )
        font_id = 0
        blf.size(font_id, 16.0)
        blf.enable(font_id, blf.SHADOW)
        blf.shadow(font_id, 3, 0.0, 0.0, 0.0, 0.0, 0.9)
        for index, line in enumerate(lines):
            blf.position(font_id, 48.0, 100.0 - index * 21.0, 0.0)
            blf.draw(font_id, line)
        blf.disable(font_id, blf.SHADOW)

    def _redraw(self) -> None:
        if self._area is not None:
            self._area.tag_redraw()

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

    def _finish(self, context: Any):
        if not self._stroke_points:
            self.report({"ERROR"}, "Draw across the target surface before finishing")
            return {"RUNNING_MODAL"}
        try:
            stats = grow_ribbon_from_stroke(
                context,
                self._stroke_points,
                self._stroke_normals,
                layout=self.layout,
                transition=self.transition,
                segments=self.segments,
                width_scale=self.width_scale,
                pole_side=self.pole_side,
                mirror=self.mirror,
                pole_spacing=self.pole_spacing,
                target=self._target,
                project_limit=self.project_limit,
            )
        except TransitionError as exc:
            self.report({"ERROR"}, str(exc))
            return {"RUNNING_MODAL"}
        except Exception as exc:
            self.report({"ERROR"}, f"Ribbon draw failed and was rolled back: {exc}")
            return {"RUNNING_MODAL"}
        self._cleanup(context)
        self.report(
            {"INFO"},
            f"Drew {stats['new_faces']} connected quads from "
            f"{stats['anchor_edges']} welded boundary edges",
        )
        if stats["had_uvs"]:
            self.report({"WARNING"}, "Unwrap the new ribbon before baking")
        return {"FINISHED"}

    def invoke(self, context: Any, _event: Any):
        if context.area is None or context.area.type != "VIEW_3D":
            self.report({"ERROR"}, "Start Multi-Strip Draw from a 3D View")
            return {"CANCELLED"}
        target = bpy.data.objects.get(self.target_name) if self.target_name else None
        try:
            self._anchor = _anchor_snapshot(context, flip_width=self.flip_width)
            if target is None:
                raise TransitionError("Choose a Surface Target before drawing")
            if target is context.edit_object:
                raise TransitionError("Choose a separate high-poly surface target")
            self._surface = TargetSurface(context, target)
            build_ribbon_plan(
                self.layout,
                self._anchor.lanes,
                self.segments,
                transition=self.transition,
                pole_side=self.pole_side,
                mirror=self.mirror,
                pole_spacing=self.pole_spacing,
            )
        except TransitionError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self._target = target
        self._area = context.area
        self._window_region = next(
            (region for region in context.area.regions if region.type == "WINDOW"),
            None,
        )
        if self._window_region is None:
            self.report({"ERROR"}, "The 3D View has no drawable window region")
            return {"CANCELLED"}
        self._region_3d = context.space_data.region_3d
        self._drawing = False
        self._stroke_points = []
        self._stroke_normals = []
        self._draw_view_handle = SpaceView3D.draw_handler_add(
            self._draw_view, (), "WINDOW", "POST_VIEW"
        )
        self._draw_hud_handle = SpaceView3D.draw_handler_add(
            self._draw_hud, (), "WINDOW", "POST_PIXEL"
        )
        context.window_manager.modal_handler_add(self)
        context.workspace.status_text_set(
            "Multi-Strip: drag LMB; wheel rows; Shift+wheel width; "
            "Enter builds; Esc cancels"
        )
        self._redraw()
        return {"RUNNING_MODAL"}

    def modal(self, context: Any, event: Any):
        if (
            context.edit_object is None
            or context.edit_object.name != self._anchor.object_name
        ):
            self._cleanup(context)
            self.report({"WARNING"}, "Active retopo mesh changed; drawing cancelled")
            return {"CANCELLED"}
        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            self._cleanup(context)
            return {"CANCELLED"}
        if event.type in {"RET", "NUMPAD_ENTER"} and event.value == "PRESS":
            return self._finish(context)
        if event.type == "LEFTMOUSE":
            if event.value == "PRESS":
                self._drawing = True
                self._stroke_points.clear()
                self._stroke_normals.clear()
                self._append_hit(self._hit(context, event))
                self._redraw()
                return {"RUNNING_MODAL"}
            if event.value == "RELEASE" and self._drawing:
                self._append_hit(self._hit(context, event))
                self._drawing = False
                self._redraw()
                return {"RUNNING_MODAL"}
        if event.type == "MOUSEMOVE" and self._drawing:
            self._append_hit(self._hit(context, event))
            self._redraw()
            return {"RUNNING_MODAL"}
        if event.type == "WHEELUPMOUSE":
            if event.shift:
                self.width_scale = min(4.0, self.width_scale + 0.05)
            else:
                self.segments = min(64, self.segments + 1)
            self._redraw()
            return {"RUNNING_MODAL"}
        if event.type == "WHEELDOWNMOUSE":
            if event.shift:
                self.width_scale = max(0.25, self.width_scale - 0.05)
            else:
                minimum = 2 if self.layout == "TRANSITION" else 1
                self.segments = max(minimum, self.segments - 1)
            self._redraw()
            return {"RUNNING_MODAL"}
        if event.type in {"MIDDLEMOUSE", "NDOF_MOTION"}:
            return {"PASS_THROUGH"}
        return {"RUNNING_MODAL"}

    def cancel(self, context: Any) -> None:
        self._cleanup(context)


CLASSES = (QT_OT_draw_multi_strip,)


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
