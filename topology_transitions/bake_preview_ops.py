"""Non-destructive viewport geometry for selected-to-active bake rays."""

from __future__ import annotations

import math
from statistics import median
from typing import Any

import bpy
from bpy.props import BoolProperty, FloatProperty, IntProperty
from bpy.types import Object, Operator
from mathutils import Vector

from .surface_ops import (
    BAKE_STATUS_KEY,
    CAGE_ROLE_KEY,
    SurfaceWorkflowError,
    find_bake_cage,
    inspect_bake_cage,
    mesh_topology_signature,
)

PREVIEW_ROLE_KEY = "topology_transitions_bake_ray_preview"
PREVIEW_SOURCE_KEY = "topology_transitions_bake_ray_source"
PREVIEW_SIGNATURE_KEY = "topology_transitions_bake_ray_signature"
PREVIEW_SUMMARY_KEY = "topology_transitions_bake_ray_summary"
PREVIEW_CAGE_KEY = "topology_transitions_bake_ray_use_cage"
PREVIEW_DISTANCE_KEY = "topology_transitions_bake_ray_distance"
PREVIEW_SAMPLES_KEY = "topology_transitions_bake_ray_samples"
PREVIEW_HIGHS_KEY = "topology_transitions_bake_ray_highs"
PREVIEW_PREFIX = "TT Bake Rays"


def _selected_high_objects(context: Any, low: Object) -> tuple[Object, ...]:
    return tuple(
        obj
        for obj in (getattr(context, "selected_objects", ()) or ())
        if obj != low
        and obj.type == "MESH"
        and not bool(obj.get(CAGE_ROLE_KEY, False))
        and not bool(obj.get(PREVIEW_ROLE_KEY, False))
    )


def _sample_indices(count: int, limit: int) -> tuple[int, ...]:
    if count <= 0 or limit <= 0:
        return ()
    if count <= limit:
        return tuple(range(count))
    return tuple(
        min(count - 1, math.floor(index * count / limit))
        for index in range(limit)
    )


def _face_center(mesh: Any, polygon: Any) -> Vector:
    return sum((mesh.vertices[index].co for index in polygon.vertices), Vector()) / len(
        polygon.vertices
    )


def _world_ray_hit(
    target_data: tuple[Any, Any, Any],
    origin_world: Vector,
    direction_world: Vector,
    max_distance: float,
) -> tuple[Vector, float] | None:
    if direction_world.length_squared <= 1.0e-18:
        return None
    direction_world = direction_world.normalized()
    evaluated, to_world, to_local = target_data
    origin_local = to_local @ origin_world
    direction_local = to_local.to_3x3() @ direction_world
    if direction_local.length_squared <= 1.0e-18:
        return None
    direction_local.normalize()
    hit, location, _normal, _face = evaluated.ray_cast(
        origin_local,
        direction_local,
        distance=1.0e20,
    )
    if not hit:
        return None
    location_world = to_world @ location
    delta = location_world - origin_world
    distance = delta.length
    if (
        distance > max_distance + 1.0e-7
        or delta.dot(direction_world) < -1.0e-7
    ):
        return None
    return location_world, distance


def _nearest_ray_hit(
    targets: tuple[tuple[Any, Any, Any], ...],
    origin: Vector,
    directions: tuple[Vector, ...],
    max_distance: float,
) -> tuple[Vector, float] | None:
    candidates = [
        result
        for target in targets
        for direction in directions
        if (
            result := _world_ray_hit(
                target,
                origin,
                direction,
                max_distance,
            )
        )
        is not None
    ]
    return min(candidates, key=lambda item: item[1]) if candidates else None


def _preview_objects(source: Object) -> list[Object]:
    return sorted(
        (
            obj
            for obj in bpy.data.objects
            if bool(obj.get(PREVIEW_ROLE_KEY, False))
            and obj.get(PREVIEW_SOURCE_KEY, "") == source.name
        ),
        key=lambda obj: obj.name,
    )


def _set_preview_visible(objects: list[Object], visible: bool) -> None:
    for obj in objects:
        obj.hide_viewport = False
        try:
            obj.hide_set(not visible)
        except (RuntimeError, TypeError):
            obj.hide_viewport = not visible


def _is_preview_visible(objects: list[Object]) -> bool:
    for obj in objects:
        try:
            if not obj.hide_get() and not obj.hide_viewport:
                return True
        except (RuntimeError, TypeError):
            if not obj.hide_viewport:
                return True
    return False


def _remove_preview(objects: list[Object]) -> None:
    for obj in objects:
        data = obj.data
        object_type = obj.type
        bpy.data.objects.remove(obj, do_unlink=True)
        if data.users:
            continue
        if object_type == "MESH":
            bpy.data.meshes.remove(data)
        elif object_type == "CURVE":
            bpy.data.curves.remove(data)


def _line_object(
    context: Any,
    source: Object,
    label: str,
    lines: list[tuple[Vector, Vector]],
    color: tuple[float, float, float, float],
    signature: str,
    summary: str,
) -> Object | None:
    if not lines:
        return None
    curve = bpy.data.curves.new(f"{PREVIEW_PREFIX} {label} Curves", "CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = 1
    curve.bevel_resolution = 0
    curve.bevel_depth = max(
        max((float(component) for component in source.dimensions), default=1.0)
        * 0.0025,
        1.0e-5,
    )
    for start, end in lines:
        spline = curve.splines.new("POLY")
        spline.points.add(1)
        spline.points[0].co = (*start, 1.0)
        spline.points[1].co = (*end, 1.0)
    material_name = f"{PREVIEW_PREFIX} {label} Material"
    material = bpy.data.materials.get(material_name)
    if material is None:
        material = bpy.data.materials.new(material_name)
    material.diffuse_color = color
    curve.materials.append(material)
    obj = bpy.data.objects.new(f"{PREVIEW_PREFIX} {label} - {source.name}", curve)
    collections = tuple(source.users_collection)
    (collections[0] if collections else context.collection).objects.link(obj)
    obj.show_in_front = True
    obj.hide_render = True
    obj.color = color
    obj[PREVIEW_ROLE_KEY] = True
    obj[PREVIEW_SOURCE_KEY] = source.name
    obj[PREVIEW_SIGNATURE_KEY] = signature
    obj[PREVIEW_SUMMARY_KEY] = summary
    return obj


def build_bake_ray_preview(
    context: Any,
    source: Object,
    *,
    max_ray_distance: float,
    use_cage: bool,
    sample_limit: int,
) -> dict[str, Any]:
    """Build line geometry that approximates Blender's bake-ray envelope."""

    if source is None or source.type != "MESH":
        raise SurfaceWorkflowError("The active low-poly object must be a mesh")
    if bool(source.get(CAGE_ROLE_KEY, False)):
        raise SurfaceWorkflowError("Activate the low-poly mesh, not its bake cage")
    if source.mode == "EDIT" and hasattr(source, "update_from_editmode"):
        source.update_from_editmode()
    source.data.update()
    if not math.isfinite(max_ray_distance) or max_ray_distance <= 0.0:
        raise SurfaceWorkflowError("Ray preview distance must be greater than zero")
    sample_limit = int(sample_limit)
    if sample_limit < 1:
        raise SurfaceWorkflowError("Ray preview needs at least one sample")
    targets = _selected_high_objects(context, source)
    if not targets:
        raise SurfaceWorkflowError(
            "Select at least one high-poly mesh and keep the low mesh active"
        )

    cage = find_bake_cage(source) if use_cage else None
    if use_cage:
        inspection = inspect_bake_cage(source, cage)
        if not inspection.ready:
            raise SurfaceWorkflowError(inspection.message)

    depsgraph = context.evaluated_depsgraph_get()
    ray_targets = tuple(
        (
            evaluated,
            evaluated.matrix_world.copy(),
            evaluated.matrix_world.inverted_safe(),
        )
        for target in targets
        for evaluated in (target.evaluated_get(depsgraph),)
    )
    normal_matrix = source.matrix_world.to_3x3().inverted_safe().transposed()
    sample_indices = _sample_indices(len(source.data.polygons), sample_limit)
    hit_lines: list[tuple[Vector, Vector]] = []
    miss_lines: list[tuple[Vector, Vector]] = []
    distances: list[float] = []
    for polygon_index in sample_indices:
        polygon = source.data.polygons[polygon_index]
        low_center = source.matrix_world @ _face_center(source.data, polygon)
        normal = normal_matrix @ polygon.normal
        if normal.length_squared <= 1.0e-18:
            continue
        normal.normalize()
        if use_cage:
            cage_polygon = cage.data.polygons[polygon_index]
            origin = cage.matrix_world @ _face_center(cage.data, cage_polygon)
            inward = low_center - origin
            reach = max(inward.length, 1.0e-7)
            directions = (inward,)
            miss_end = low_center
        else:
            origin = low_center
            reach = max_ray_distance
            directions = (normal, -normal)
            miss_end = origin + normal * reach
        result = _nearest_ray_hit(ray_targets, origin, directions, reach)
        if result is None:
            miss_start = origin if use_cage else origin - normal * reach
            miss_lines.append((miss_start, miss_end))
        else:
            location, distance = result
            hit_lines.append((origin, location))
            distances.append(distance)

    total = len(hit_lines) + len(miss_lines)
    if total == 0:
        raise SurfaceWorkflowError("The low mesh has no valid face samples")
    coverage = len(hit_lines) / total
    ordered_distances = sorted(distances)
    p95_index = max(0, math.ceil(len(ordered_distances) * 0.95) - 1)
    median_distance = median(ordered_distances) if ordered_distances else 0.0
    p95_distance = ordered_distances[p95_index] if ordered_distances else 0.0
    summary = (
        f"Bake rays {len(hit_lines)}/{total} hit ({coverage:.0%}); "
        f"median {median_distance:.4g}, p95 {p95_distance:.4g}"
    )
    signature = mesh_topology_signature(source.data)
    objects = tuple(
        obj
        for obj in (
            _line_object(
                context,
                source,
                "Hits",
                hit_lines,
                (0.05, 1.0, 0.12, 1.0),
                signature,
                summary,
            ),
            _line_object(
                context,
                source,
                "Misses",
                miss_lines,
                (1.0, 0.03, 0.02, 1.0),
                signature,
                summary,
            ),
        )
        if obj is not None
    )
    high_names = "\n".join(sorted(target.name for target in targets))
    for obj in objects:
        obj[PREVIEW_CAGE_KEY] = use_cage
        obj[PREVIEW_DISTANCE_KEY] = max_ray_distance
        obj[PREVIEW_SAMPLES_KEY] = sample_limit
        obj[PREVIEW_HIGHS_KEY] = high_names
    context.scene[BAKE_STATUS_KEY] = summary
    return {
        "objects": objects,
        "hits": len(hit_lines),
        "misses": len(miss_lines),
        "samples": total,
        "coverage": coverage,
        "median_distance": median_distance,
        "p95_distance": p95_distance,
        "summary": summary,
        "approximate": True,
    }


def toggle_bake_ray_preview(
    context: Any,
    source: Object,
    *,
    max_ray_distance: float,
    use_cage: bool,
    sample_limit: int,
    force_rebuild: bool = False,
) -> dict[str, Any]:
    existing = _preview_objects(source)
    signature = mesh_topology_signature(source.data)
    stale = any(
        obj.get(PREVIEW_SIGNATURE_KEY, "") != signature for obj in existing
    )
    current_high_names = "\n".join(
        sorted(obj.name for obj in _selected_high_objects(context, source))
    )
    config_matches = bool(existing) and all(
        bool(obj.get(PREVIEW_CAGE_KEY, False)) == use_cage
        and math.isclose(
            float(obj.get(PREVIEW_DISTANCE_KEY, -1.0)),
            max_ray_distance,
            rel_tol=0.0,
            abs_tol=1.0e-9,
        )
        and int(obj.get(PREVIEW_SAMPLES_KEY, -1)) == sample_limit
        and (
            not current_high_names
            or str(obj.get(PREVIEW_HIGHS_KEY, "")) == current_high_names
        )
        for obj in existing
    )
    if existing and not force_rebuild and not stale and config_matches:
        visible = _is_preview_visible(existing)
        _set_preview_visible(existing, not visible)
        summary = str(existing[0].get(PREVIEW_SUMMARY_KEY, "Bake-ray preview"))
        context.scene[BAKE_STATUS_KEY] = summary
        return {
            "state": "hidden" if visible else "shown",
            "objects": tuple(existing),
            "summary": summary,
        }
    _remove_preview(existing)
    result = build_bake_ray_preview(
        context,
        source,
        max_ray_distance=max_ray_distance,
        use_cage=use_cage,
        sample_limit=sample_limit,
    )
    result["state"] = "rebuilt" if existing else "created"
    return result


class QT_OT_toggle_bake_ray_preview(Operator):
    bl_idname = "object.quad_transition_toggle_bake_rays"
    bl_label = "Toggle Bake-Ray Preview"
    bl_description = (
        "Show sampled green source hits and red misses without starting a bake"
    )
    bl_options = {"REGISTER", "UNDO"}

    max_ray_distance: FloatProperty(
        name="Max Ray Distance",
        min=0.000001,
        default=0.02,
    )
    use_cage: BoolProperty(name="Use Custom Cage", default=False)
    sample_limit: IntProperty(name="Samples", min=1, max=5000, default=500)
    force_rebuild: BoolProperty(name="Refresh", default=False)

    @classmethod
    def poll(cls, context: Any) -> bool:
        return (
            context.active_object is not None and context.active_object.type == "MESH"
        )

    def execute(self, context: Any):
        try:
            result = toggle_bake_ray_preview(
                context,
                context.active_object,
                max_ray_distance=self.max_ray_distance,
                use_cage=self.use_cage,
                sample_limit=self.sample_limit,
                force_rebuild=self.force_rebuild,
            )
        except SurfaceWorkflowError as exc:
            context.scene[BAKE_STATUS_KEY] = str(exc)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Ray preview {result['state']}: {result['summary']}")
        return {"FINISHED"}


CLASSES = (QT_OT_toggle_bake_ray_preview,)


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
