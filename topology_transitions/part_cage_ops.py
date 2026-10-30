"""Part Cages: a bake cage with its own distance for every separate part.

A game asset is often one low-poly mesh made of many loose parts (straps,
buckles, plates). One cage distance for all of them is either too small for
the bulky parts or so large that a thin part's cage swallows its neighbour,
and the bake then picks up the wrong surface. Here every loose part gets a
distance fitted to how far the high-poly surface sits outside it, the cages
are drawn live in their own colours, and faces where one part's cage runs into
another part's cage are drawn red. The result is written as the normal
topology-locked bake cage, so Configure Bake uses it unchanged.
"""

from __future__ import annotations

import json
from typing import Any

import bpy
import gpu
from bpy.types import Object, Operator, SpaceView3D
from gpu_extras.batch import batch_for_shader
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from . import hud
from .surface_ops import (
    CAGE_NAME_PREFIX,
    CAGE_ROLE_KEY,
    _configure_cage_display,
    _show_cage,
    find_bake_cage,
    mesh_topology_signature,
)

PART_DISTANCES_KEY = "topology_transitions_part_distances"
FIT_MARGIN = 1.15  # fitted distance = deepest high-poly offset x this ...
FIT_PAD = 0.01  # ... plus this fraction of the part's size
MIN_FRACTION = 0.004  # never thinner than this fraction of the part's size
STEP = 1.15  # +/- scales the active part's distance by this
PALETTE = (hud.TEAL, hud.SKY, hud.AMBER, hud.MAGENTA, hud.VIOLET, (0.9, 0.9, 0.4, 1.0))


class PartCageError(RuntimeError):
    pass


def loose_parts(mesh: Any) -> list[list[int]]:
    """Vertex indices of every edge-connected part, largest first."""

    parent = list(range(len(mesh.vertices)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for edge in mesh.edges:
        first, second = (find(index) for index in edge.vertices)
        if first != second:
            parent[first] = second
    groups: dict[int, list[int]] = {}
    for index in range(len(mesh.vertices)):
        groups.setdefault(find(index), []).append(index)
    return sorted(groups.values(), key=len, reverse=True)


def _size(points: list[Vector]) -> float:
    low = Vector(map(min, *points))
    high = Vector(map(max, *points))
    return max((high - low).length, 1.0e-6)


def high_poly_tree(context: Any, highs: list[Object]) -> BVHTree | None:
    """One world-space BVH over every high-poly object."""

    depsgraph = context.evaluated_depsgraph_get()
    points: list[Vector] = []
    polygons: list[tuple[int, ...]] = []
    for obj in highs:
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        offset = len(points)
        points += [obj.matrix_world @ vertex.co for vertex in mesh.vertices]
        polygons += [
            tuple(offset + index for index in polygon.vertices)
            for polygon in mesh.polygons
        ]
        evaluated.to_mesh_clear()
    return BVHTree.FromPolygons(points, polygons) if polygons else None


class PartCagePlan:
    """World-space parts of a low-poly mesh and one cage distance per part."""

    def __init__(self, low: Object, default_distance: float) -> None:
        mesh = low.data
        self.low = low
        matrix = low.matrix_world
        normal_matrix = matrix.to_3x3().inverted_safe().transposed()
        self.points = [matrix @ v.co for v in mesh.vertices]
        self.normals = []
        for vertex in mesh.vertices:
            normal = normal_matrix @ vertex.normal
            self.normals.append(normal.normalized() if normal.length else normal)
        self.parts = loose_parts(mesh)
        if not self.parts:
            raise PartCageError("The low-poly mesh has no vertices")
        self.part_of = [0] * len(mesh.vertices)
        for part, indices in enumerate(self.parts):
            for index in indices:
                self.part_of[index] = part
        self.sizes = [_size([self.points[i] for i in part]) for part in self.parts]
        self.polygons = [tuple(polygon.vertices) for polygon in mesh.polygons]
        self.part_polygons: list[list[int]] = [[] for _ in self.parts]
        for index, polygon in enumerate(self.polygons):
            self.part_polygons[self.part_of[polygon[0]]].append(index)
        self.distances = [default_distance] * len(self.parts)
        self.overlapping: set[int] = set()  # polygon indices drawn red
        self.clashes: list[tuple[int, int]] = []  # (part, part) pairs

    def fit(self, tree: BVHTree | None) -> None:
        """Distance per part from how far the high-poly lies outside it."""

        if tree is None:
            return
        for part, indices in enumerate(self.parts):
            size = self.sizes[part]
            deepest = 0.0
            for index in indices:
                # How far along the cage direction the high-poly still lies
                # outside: on a box corner the normal is diagonal, so the
                # nearest high-poly point would underestimate it.
                point, normal = self.points[index], self.normals[index]
                if normal.length_squared == 0.0:
                    continue
                location, _n, _i, distance = tree.ray_cast(point, normal, size * 0.5)
                if location is None:
                    continue
                # A flipped normal sends the ray through the part to its far
                # side; no real offset is more than twice the nearest one.
                near = tree.find_nearest(point, distance)[3]
                if near is not None:
                    distance = min(distance, 2.0 * near)
                deepest = max(deepest, distance)
            self.distances[part] = max(
                deepest * FIT_MARGIN + size * FIT_PAD, size * MIN_FRACTION
            )

    def cage_points(self) -> list[Vector]:
        return [
            point + normal * self.distances[self.part_of[index]]
            for index, (point, normal) in enumerate(
                zip(self.points, self.normals, strict=True)
            )
        ]

    def _local(self, part: int) -> tuple[list[int], list[list[int]]]:
        faces = [self.polygons[index] for index in self.part_polygons[part]]
        used = sorted({v for face in faces for v in face}) or [0]
        remap = {v: i for i, v in enumerate(used)}
        return used, [[remap[v] for v in face] for face in faces]

    def _cage(self, part: int):
        """Cage BVH and box of one part, rebuilt only when its distance changed."""

        distance = self.distances[part]
        cached = self._cages.get(part)
        if cached is not None and cached[0] == distance:
            return cached[1], cached[2]
        used, local = self._local(part)
        points = [self.points[v] + self.normals[v] * distance for v in used]
        tree = BVHTree.FromPolygons(points, local)
        box = (Vector(map(min, *points)), Vector(map(max, *points)))
        self._cages[part] = (distance, tree, box)
        return tree, box

    def _bare(self, part: int) -> BVHTree:
        if part not in self._bares:
            used, local = self._local(part)
            self._bares[part] = BVHTree.FromPolygons(
                [self.points[v] for v in used], local
            )
        return self._bares[part]

    def check_overlaps(self) -> None:
        """Faces of one part's cage that cut into another part's cage, where
        the low-poly parts themselves do not already touch. Results are kept
        per pair of parts, so resizing one part only rechecks its pairs."""

        if not hasattr(self, "_cages"):
            self._cages, self._bares, self._pairs = {}, {}, {}
        cages = [self._cage(part) for part in range(len(self.parts))]
        self.overlapping, self.clashes = set(), []
        for a in range(len(self.parts)):
            tree_a, (low_a, high_a) = cages[a]
            for b in range(a + 1, len(self.parts)):
                tree_b, (low_b, high_b) = cages[b]
                if any(
                    low_a[axis] > high_b[axis] or low_b[axis] > high_a[axis]
                    for axis in range(3)
                ):
                    continue
                key = (self.distances[a], self.distances[b])
                cached = self._pairs.get((a, b))
                if cached is None or cached[0] != key:
                    pairs = tree_a.overlap(tree_b)
                    if pairs:
                        touching = set(self._bare(a).overlap(self._bare(b)))
                        pairs = [pair for pair in pairs if pair not in touching]
                    cached = (key, pairs)
                    self._pairs[(a, b)] = cached
                if not cached[1]:
                    continue
                self.clashes.append((a, b))
                for face_a, face_b in cached[1]:
                    self.overlapping.add(self.part_polygons[a][face_a])
                    self.overlapping.add(self.part_polygons[b][face_b])


def write_part_cage(context: Any, plan: PartCagePlan) -> Object:
    """Write the plan as the low mesh's tagged, topology-locked bake cage."""

    low = plan.low
    inverse = low.matrix_world.inverted_safe()
    mesh = low.data.copy()
    mesh.name = f"{low.data.name}_BakeCage"
    for vertex, point in zip(mesh.vertices, plan.cage_points(), strict=True):
        vertex.co = inverse @ point
    mesh.update()
    if mesh_topology_signature(mesh) != mesh_topology_signature(low.data):
        bpy.data.meshes.remove(mesh)
        raise PartCageError("Cage construction changed mesh topology ordering")
    cage = find_bake_cage(low)
    if cage is None:
        cage = bpy.data.objects.new(f"{CAGE_NAME_PREFIX} - {low.name}", mesh)
        collections = tuple(low.users_collection)
        (collections[0] if collections else context.collection).objects.link(cage)
    else:
        previous = cage.data
        cage.data = mesh
        if previous.users == 0:
            bpy.data.meshes.remove(previous)
    # The largest distance is the one a later uniform rebuild would compare.
    _configure_cage_display(cage, low, max(plan.distances))
    cage[PART_DISTANCES_KEY] = json.dumps([round(d, 6) for d in plan.distances])
    _show_cage(cage, True)
    return cage


class QT_OT_part_cages(Operator):
    bl_idname = "object.quad_transition_part_cages"
    bl_label = "Part Cages"
    bl_description = (
        "Live bake-cage editor: every loose part of the active low-poly mesh gets "
        "its own cage distance, fitted to the selected high-poly objects (or the "
        "Surface Target); cages "
        "that run into another part are drawn red"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: Any) -> bool:
        obj = context.active_object
        return (
            context.mode == "OBJECT"
            and obj is not None
            and obj.type == "MESH"
            and not obj.get(CAGE_ROLE_KEY, False)
        )

    def invoke(self, context: Any, _event: Any):
        low = context.active_object
        highs = [
            obj
            for obj in context.selected_objects
            if obj is not low and obj.type == "MESH" and not obj.get(CAGE_ROLE_KEY)
        ]
        settings = context.scene.topology_transitions
        target = settings.surface_target
        if not highs and target is not None and target is not low:
            highs = [target]
        try:
            self._plan = PartCagePlan(low, settings.bake_cage_distance)
            self._tree = high_poly_tree(context, highs)
            self._plan.fit(self._tree)
            self._plan.check_overlaps()
        except (PartCageError, ValueError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self._highs = len(highs)
        self._active = 0
        self._area = context.area
        self._region = next(r for r in context.area.regions if r.type == "WINDOW")
        self._view = SpaceView3D.draw_handler_add(
            self._draw_view, (), "WINDOW", "POST_VIEW"
        )
        self._hud = SpaceView3D.draw_handler_add(
            self._draw_hud, (), "WINDOW", "POST_PIXEL"
        )
        context.window_manager.modal_handler_add(self)
        self._area.tag_redraw()
        return {"RUNNING_MODAL"}

    def _cleanup(self) -> None:
        SpaceView3D.draw_handler_remove(self._view, "WINDOW")
        SpaceView3D.draw_handler_remove(self._hud, "WINDOW")
        self._area.tag_redraw()

    def _changed(self) -> None:
        self._plan.check_overlaps()
        self._area.tag_redraw()

    def modal(self, context: Any, event: Any):
        if event.value != "PRESS":
            return {"PASS_THROUGH"}
        plan = self._plan
        count = len(plan.parts)
        if event.type == "TAB":
            self._active = (self._active + (-1 if event.shift else 1)) % count
            self._area.tag_redraw()
            return {"RUNNING_MODAL"}
        if event.type in {"EQUAL", "NUMPAD_PLUS", "MINUS", "NUMPAD_MINUS"}:
            factor = STEP if event.type in {"EQUAL", "NUMPAD_PLUS"} else 1.0 / STEP
            targets = range(count) if event.shift else (self._active,)
            for part in targets:
                plan.distances[part] *= factor
            self._changed()
            return {"RUNNING_MODAL"}
        if event.type == "A":
            plan.fit(self._tree)
            self._changed()
            return {"RUNNING_MODAL"}
        if event.type in {"RET", "NUMPAD_ENTER"}:
            self._cleanup()
            try:
                cage = write_part_cage(context, plan)
            except PartCageError as exc:
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}
            self.report(
                {"INFO"},
                f"Wrote {cage.name}: {count} parts, "
                f"{len(plan.clashes)} clashing pairs left",
            )
            return {"FINISHED"}
        if event.type in {"ESC", "RIGHTMOUSE"}:
            self._cleanup()
            return {"CANCELLED"}
        return {"PASS_THROUGH"}

    def _draw_view(self) -> None:
        if bpy.context.area != self._area:
            return
        plan = self._plan
        cage = plan.cage_points()
        lines: dict[int, list] = {}
        for part, polygons in enumerate(plan.part_polygons):
            segments = lines.setdefault(part, [])
            for index in polygons:
                face = plan.polygons[index]
                for a, b in zip(face, face[1:] + face[:1], strict=True):
                    segments += (cage[a], cage[b])
        red = [
            cage[v]
            for index in plan.overlapping
            for i in range(1, len(plan.polygons[index]) - 1)
            for v in (
                plan.polygons[index][0],
                plan.polygons[index][i],
                plan.polygons[index][i + 1],
            )
        ]
        viewport = gpu.state.viewport_get()
        lines_shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
        fill = gpu.shader.from_builtin("UNIFORM_COLOR")
        gpu.state.blend_set("ALPHA")
        gpu.state.depth_test_set("LESS_EQUAL")
        try:
            if red:
                fill.bind()
                fill.uniform_float("color", (*hud.CORAL[:3], 0.55))
                batch_for_shader(fill, "TRIS", {"pos": red}).draw(fill)
            lines_shader.bind()
            lines_shader.uniform_float("viewportSize", (viewport[2], viewport[3]))
            for part, segments in lines.items():
                if not segments:
                    continue
                active = part == self._active
                color = PALETTE[part % len(PALETTE)]
                lines_shader.uniform_float("lineWidth", 2.5 if active else 1.0)
                lines_shader.uniform_float(
                    "color", (*color[:3], 0.95 if active else 0.45)
                )
                batch_for_shader(lines_shader, "LINES", {"pos": segments}).draw(
                    lines_shader
                )
        finally:
            gpu.state.depth_test_set("NONE")
            gpu.state.blend_set("NONE")

    def _draw_hud(self) -> None:
        if bpy.context.area != self._area:
            return
        plan = self._plan
        part = self._active
        clashes = [pair for pair in plan.clashes if part in pair]
        color = PALETTE[part % len(PALETTE)]
        rows = [
            [
                ("chip", hud.SKY, f"{len(plan.parts)} parts"),
                (
                    "chip",
                    hud.CORAL if plan.clashes else hud.TEAL,
                    f"{len(plan.clashes)} clashing pairs",
                ),
                (
                    "chip",
                    hud.TEAL if self._highs else hud.MUTED,
                    f"fitted to {self._highs} high-poly"
                    if self._highs
                    else "no high-poly selected",
                ),
            ],
            [
                ("chip", color, f"part {part + 1}"),
                (f"{len(plan.part_polygons[part])} faces", hud.INK),
                ("chip", hud.VIOLET, f"cage {plan.distances[part]:.4g}"),
                (
                    "chip",
                    hud.CORAL if clashes else hud.TEAL,
                    "runs into part "
                    + ", ".join(
                        str((a if b == part else b) + 1) for a, b in clashes[:5]
                    )
                    + (f" +{len(clashes) - 5} more" if len(clashes) > 5 else "")
                    if clashes
                    else "clear",
                ),
            ],
            [("Red faces: this cage runs into another part's cage.", hud.MUTED)],
            [
                ("key", "Tab"),
                ("next part", hud.MUTED),
                ("key", "- ="),
                ("cage size", hud.MUTED),
                ("key", "Shift"),
                ("all parts", hud.MUTED),
                ("key", "A"),
                ("auto-fit", hud.MUTED),
                ("key", "Enter"),
                ("write cage", hud.MUTED),
                ("key", "Esc"),
                ("cancel", hud.MUTED),
            ],
        ]
        hud.draw_corner_card(self._area, self._region, "Part Cages", hud.AMBER, rows)


CLASSES = (QT_OT_part_cages,)


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
