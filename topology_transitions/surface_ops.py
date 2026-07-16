"""Surface-conform and selected-to-active bake workflow operators.

The helpers in this module deliberately keep mesh construction and readiness
inspection separate from the UI.  That makes the workflow usable from the
sidebar, scripts, and Blender background smoke tests without starting a bake.
"""

from __future__ import annotations

import hashlib
import math
import struct
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    StringProperty,
)
from bpy.types import Object, Operator

SHRINKWRAP_MODIFIER_NAME = "Topology Transition Shrinkwrap"
CAGE_NAME_PREFIX = "TT Bake Cage"
CAGE_ROLE_KEY = "topology_transitions_bake_cage"
CAGE_SOURCE_NAME_KEY = "topology_transitions_cage_source"
CAGE_SOURCE_DATA_KEY = "topology_transitions_cage_source_data"
CAGE_TOPOLOGY_KEY = "topology_transitions_cage_topology"
CAGE_GEOMETRY_KEY = "topology_transitions_cage_geometry"
CAGE_DISTANCE_KEY = "topology_transitions_cage_distance"
BAKE_STATUS_KEY = "topology_transitions_bake_status"
BAKE_READY_KEY = "topology_transitions_bake_ready"


SHRINKWRAP_METHOD_ITEMS = (
    (
        "NEAREST_SURFACEPOINT",
        "Nearest Surface",
        "Move each vertex to the nearest point on the target surface",
    ),
    (
        "TARGET_PROJECT",
        "Target Normal Project",
        "Project along target normals; smoother but more expensive",
    ),
    (
        "PROJECT",
        "Local Z Project",
        "Project in both directions along the retopology object's local Z axis",
    ),
)

BAKE_TYPE_ITEMS = (
    (
        "NORMAL",
        "Tangent Normal",
        "Configure a tangent-space normal-map bake",
    ),
    (
        "DISPLACEMENT",
        "Displacement",
        "Configure a scalar displacement bake",
    ),
)


class SurfaceWorkflowError(RuntimeError):
    """Raised when a surface or bake operation would be unsafe or ambiguous."""


@dataclass(frozen=True)
class CageInspection:
    """Topology relationship between a retopology object and a bake cage."""

    state: str
    cage_name: str
    source_signature: str
    cage_signature: str
    stored_signature: str
    topology_matches: bool
    source_geometry: str
    stored_geometry: str
    geometry_matches: bool
    transform_matches: bool
    message: str

    @property
    def ready(self) -> bool:
        return (
            self.topology_matches
            and self.geometry_matches
            and self.transform_matches
        )


@dataclass(frozen=True)
class BakeReadiness:
    """Actionable selected-to-active bake preflight result."""

    ready: bool
    low_object: str
    high_objects: tuple[str, ...]
    image_names: tuple[str, ...]
    cage_name: str
    use_cage: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def summary(self) -> str:
        if self.errors:
            return f"Not ready: {'; '.join(self.errors)}"
        if self.warnings:
            return f"Ready with warnings: {'; '.join(self.warnings)}"
        return "Ready for selected-to-active bake"


def _enum_identifiers(owner: Any, property_name: str) -> tuple[str, ...]:
    """Return runtime enum identifiers without assuming a Blender minor version."""

    try:
        prop = owner.bl_rna.properties[property_name]
        return tuple(item.identifier for item in prop.enum_items)
    except (AttributeError, KeyError, TypeError):
        return ()


def _resolve_enum(
    owner: Any,
    property_name: str,
    requested: str,
    aliases: dict[str, tuple[str, ...]],
) -> str:
    supported = _enum_identifiers(owner, property_name)
    candidates = (requested, *aliases.get(requested, ()))
    for candidate in candidates:
        if not supported or candidate in supported:
            return candidate
    raise SurfaceWorkflowError(
        f"This Blender build does not support {requested!r} for {property_name}; "
        f"available values: {', '.join(supported) or 'unknown'}"
    )


def _finite_nonnegative(value: float, label: str) -> float:
    value = float(value)
    if not math.isfinite(value) or value < 0.0:
        raise SurfaceWorkflowError(f"{label} must be a finite non-negative value")
    return value


def _require_mesh_object(obj: Object | None, label: str) -> Object:
    if obj is None or obj.type != "MESH":
        raise SurfaceWorkflowError(f"{label} must be a mesh object")
    return obj


def resolve_shrinkwrap_method(modifier: Any, requested: str) -> str:
    """Resolve method aliases across Blender 4.2 through 5.2."""

    aliases = {
        "NEAREST_SURFACEPOINT": ("NEAREST_VERTEX",),
        "TARGET_PROJECT": ("PROJECT", "NEAREST_SURFACEPOINT", "NEAREST_VERTEX"),
        "PROJECT": ("TARGET_PROJECT", "NEAREST_SURFACEPOINT", "NEAREST_VERTEX"),
    }
    return _resolve_enum(modifier, "wrap_method", requested, aliases)


def ensure_shrinkwrap_modifier(
    source: Object,
    target: Object,
    *,
    wrap_method: str = "NEAREST_SURFACEPOINT",
    offset: float = 0.002,
    project_limit: float = 0.0,
    vertex_group: str = "",
) -> dict[str, Any]:
    """Create or update the add-on's non-destructive Shrinkwrap modifier."""

    source = _require_mesh_object(source, "Retopology source")
    target = _require_mesh_object(target, "Shrinkwrap target")
    if source == target:
        raise SurfaceWorkflowError(
            "Shrinkwrap target must be separate from the retopo mesh"
        )
    offset = float(offset)
    if not math.isfinite(offset):
        raise SurfaceWorkflowError("Shrinkwrap offset must be finite")
    project_limit = _finite_nonnegative(project_limit, "Project limit")
    if vertex_group and source.vertex_groups.get(vertex_group) is None:
        raise SurfaceWorkflowError(
            f"Vertex group {vertex_group!r} does not exist on {source.name!r}"
        )

    modifier = source.modifiers.get(SHRINKWRAP_MODIFIER_NAME)
    created = modifier is None
    if modifier is not None and modifier.type != "SHRINKWRAP":
        raise SurfaceWorkflowError(
            f"A non-Shrinkwrap modifier already uses {SHRINKWRAP_MODIFIER_NAME!r}"
        )
    if modifier is None:
        modifier = source.modifiers.new(SHRINKWRAP_MODIFIER_NAME, "SHRINKWRAP")
    try:
        resolved_method = resolve_shrinkwrap_method(modifier, wrap_method)
        modifier.target = target
        modifier.wrap_method = resolved_method
        modifier.offset = offset
        if hasattr(modifier, "project_limit"):
            modifier.project_limit = project_limit
        if hasattr(modifier, "show_in_editmode"):
            modifier.show_in_editmode = True
        if hasattr(modifier, "show_on_cage"):
            modifier.show_on_cage = True
        if hasattr(modifier, "show_viewport"):
            modifier.show_viewport = True

        if resolved_method == "PROJECT":
            for name in ("use_positive_direction", "use_negative_direction"):
                if hasattr(modifier, name):
                    setattr(modifier, name, True)
            for name, enabled in (
                ("use_project_x", False),
                ("use_project_y", False),
                ("use_project_z", True),
            ):
                if hasattr(modifier, name):
                    setattr(modifier, name, enabled)

        assigned_group = ""
        if vertex_group and hasattr(modifier, "vertex_group"):
            modifier.vertex_group = vertex_group
            assigned_group = vertex_group
        elif hasattr(modifier, "vertex_group"):
            modifier.vertex_group = ""
    except Exception as exc:
        if created and source.modifiers.get(modifier.name) == modifier:
            source.modifiers.remove(modifier)
        if isinstance(exc, SurfaceWorkflowError):
            raise
        raise SurfaceWorkflowError(f"Could not configure Shrinkwrap: {exc}") from exc

    return {
        "modifier": modifier,
        "created": created,
        "requested_method": wrap_method,
        "resolved_method": resolved_method,
        "used_fallback": resolved_method != wrap_method,
        "vertex_group": assigned_group,
    }


def mesh_topology_signature(mesh: Any) -> str:
    """Hash ordered connectivity while deliberately ignoring vertex positions."""

    vertex_count = len(mesh.vertices)
    edge_count = len(mesh.edges)
    polygon_count = len(mesh.polygons)
    digest = hashlib.sha256()
    digest.update(struct.pack("<III", vertex_count, edge_count, polygon_count))
    for edge in mesh.edges:
        digest.update(struct.pack("<II", int(edge.vertices[0]), int(edge.vertices[1])))
    for polygon in mesh.polygons:
        vertices = tuple(int(index) for index in polygon.vertices)
        digest.update(struct.pack("<I", len(vertices)))
        if vertices:
            digest.update(struct.pack(f"<{len(vertices)}I", *vertices))
    return f"{vertex_count}:{edge_count}:{polygon_count}:{digest.hexdigest()[:24]}"


def mesh_geometry_signature(mesh: Any) -> str:
    """Hash source positions so a moved low-poly cage is detected as stale."""

    digest = hashlib.sha256(mesh_topology_signature(mesh).encode("ascii"))
    for vertex in mesh.vertices:
        digest.update(
            struct.pack(
                "<ddd",
                float(vertex.co.x),
                float(vertex.co.y),
                float(vertex.co.z),
            )
        )
    return digest.hexdigest()[:24]


def find_bake_cage(source: Object) -> Object | None:
    """Find the tagged cage for ``source`` without relying on its display name."""

    source = _require_mesh_object(source, "Retopology source")
    candidates = [
        obj
        for obj in bpy.data.objects
        if obj.type == "MESH"
        and bool(obj.get(CAGE_ROLE_KEY, False))
    ]
    matches = [
        obj
        for obj in candidates
        if obj.get(CAGE_SOURCE_NAME_KEY, "") == source.name
    ]
    if not matches:
        data_matches = [
            obj
            for obj in candidates
            if obj.get(CAGE_SOURCE_DATA_KEY, "") == source.data.name
        ]
        if len(data_matches) == 1:
            matches = data_matches
    if not matches:
        return None
    matches.sort(key=lambda item: item.name)
    return matches[0]


def inspect_bake_cage(source: Object, cage: Object | None = None) -> CageInspection:
    """Detect missing, foreign, and topology-stale cages."""

    source = _require_mesh_object(source, "Retopology source")
    source_signature = mesh_topology_signature(source.data)
    source_geometry = mesh_geometry_signature(source.data)
    cage = cage or find_bake_cage(source)
    if cage is None:
        return CageInspection(
            state="MISSING",
            cage_name="",
            source_signature=source_signature,
            cage_signature="",
            stored_signature="",
            topology_matches=False,
            source_geometry=source_geometry,
            stored_geometry="",
            geometry_matches=False,
            transform_matches=False,
            message="No bake cage exists for the active low-poly mesh",
        )
    if cage.type != "MESH":
        return CageInspection(
            state="INVALID",
            cage_name=cage.name,
            source_signature=source_signature,
            cage_signature="",
            stored_signature=str(cage.get(CAGE_TOPOLOGY_KEY, "")),
            topology_matches=False,
            source_geometry=source_geometry,
            stored_geometry=str(cage.get(CAGE_GEOMETRY_KEY, "")),
            geometry_matches=False,
            transform_matches=False,
            message="The configured bake cage is not a mesh",
        )

    cage_signature = mesh_topology_signature(cage.data)
    stored_signature = str(cage.get(CAGE_TOPOLOGY_KEY, ""))
    stored_geometry = str(cage.get(CAGE_GEOMETRY_KEY, ""))
    source_name = str(cage.get(CAGE_SOURCE_NAME_KEY, ""))
    source_data = str(cage.get(CAGE_SOURCE_DATA_KEY, ""))
    topology_matches = (
        cage_signature == source_signature and stored_signature == source_signature
    )
    geometry_matches = stored_geometry == source_geometry
    transform_matches = all(
        abs(cage.matrix_world[row][column] - source.matrix_world[row][column])
        <= 1.0e-7
        for row in range(4)
        for column in range(4)
    )
    same_source = source_name == source.name or source_data == source.data.name
    if not same_source:
        state = "FOREIGN"
        message = f"Cage belongs to {source_name or 'an unknown source'}"
        topology_matches = False
        geometry_matches = False
    elif not topology_matches:
        state = "STALE"
        message = "Bake cage topology no longer matches the low-poly mesh"
    elif not geometry_matches:
        state = "STALE_GEOMETRY"
        message = "Bake cage positions no longer match the edited low-poly mesh"
    elif not transform_matches:
        state = "MISALIGNED"
        message = "Bake cage transform no longer matches the low-poly mesh"
    else:
        state = "READY"
        message = "Bake cage has exact topology and vertex-order parity"
    return CageInspection(
        state=state,
        cage_name=cage.name,
        source_signature=source_signature,
        cage_signature=cage_signature,
        stored_signature=stored_signature,
        topology_matches=topology_matches,
        source_geometry=source_geometry,
        stored_geometry=stored_geometry,
        geometry_matches=geometry_matches,
        transform_matches=transform_matches,
        message=message,
    )


def _sync_edit_mesh(source: Object) -> None:
    if source.mode == "EDIT" and hasattr(source, "update_from_editmode"):
        source.update_from_editmode()
    source.data.update()


def _offset_mesh_copy(source: Object, distance: float) -> Any:
    """Copy all mesh data, then move vertices without changing connectivity order."""

    _sync_edit_mesh(source)
    mesh_copy = source.data.copy()
    mesh_copy.name = f"{source.data.name}_BakeCage"
    for source_vertex, cage_vertex in zip(
        source.data.vertices, mesh_copy.vertices, strict=True
    ):
        normal = source_vertex.normal
        if normal.length_squared > 1.0e-20:
            cage_vertex.co = source_vertex.co + normal.normalized() * distance
        else:
            cage_vertex.co = source_vertex.co
    mesh_copy.update()
    if mesh_topology_signature(mesh_copy) != mesh_topology_signature(source.data):
        raise SurfaceWorkflowError("Cage construction changed mesh topology ordering")
    return mesh_copy


def _configure_cage_display(cage: Object, source: Object, distance: float) -> None:
    signature = mesh_topology_signature(source.data)
    cage.name = f"{CAGE_NAME_PREFIX} - {source.name}"
    cage.matrix_world = source.matrix_world.copy()
    cage.display_type = "WIRE"
    cage.show_in_front = True
    cage.show_wire = True
    cage.show_all_edges = True
    cage.hide_render = True
    cage.color = (1.0, 0.18, 0.03, 1.0)
    cage[CAGE_ROLE_KEY] = True
    cage[CAGE_SOURCE_NAME_KEY] = source.name
    cage[CAGE_SOURCE_DATA_KEY] = source.data.name
    cage[CAGE_TOPOLOGY_KEY] = signature
    cage[CAGE_GEOMETRY_KEY] = mesh_geometry_signature(source.data)
    cage[CAGE_DISTANCE_KEY] = distance


def _show_cage(cage: Object, show: bool) -> None:
    cage.hide_viewport = False
    try:
        cage.hide_set(not show)
    except (RuntimeError, TypeError):
        cage.hide_viewport = not show


def _cage_hidden(cage: Object) -> bool:
    try:
        return bool(cage.hide_get()) or bool(cage.hide_viewport)
    except (RuntimeError, TypeError):
        return bool(cage.hide_viewport)


def toggle_bake_cage(
    context: Any,
    source: Object,
    *,
    distance: float = 0.03,
    force_rebuild: bool = False,
) -> dict[str, Any]:
    """Create, rebuild, or visibility-toggle a topology-locked wire cage."""

    source = _require_mesh_object(source, "Retopology source")
    if bool(source.get(CAGE_ROLE_KEY, False)):
        raise SurfaceWorkflowError("Select the low-poly retopology mesh, not its cage")
    distance = _finite_nonnegative(distance, "Cage distance")
    _sync_edit_mesh(source)
    cage = find_bake_cage(source)
    inspection = inspect_bake_cage(source, cage)
    stored_distance = (
        float(cage.get(CAGE_DISTANCE_KEY, -1.0)) if cage is not None else -1.0
    )
    needs_rebuild = (
        force_rebuild
        or cage is None
        or not inspection.topology_matches
        or not inspection.geometry_matches
        or not math.isclose(stored_distance, distance, rel_tol=0.0, abs_tol=1.0e-9)
    )

    if needs_rebuild:
        cage_mesh = _offset_mesh_copy(source, distance)
        if cage is None:
            cage = bpy.data.objects.new(
                f"{CAGE_NAME_PREFIX} - {source.name}", cage_mesh
            )
            collections = tuple(source.users_collection)
            collection = collections[0] if collections else context.collection
            collection.objects.link(cage)
            state = "created"
        else:
            previous_mesh = cage.data
            cage.data = cage_mesh
            if previous_mesh.users == 0:
                bpy.data.meshes.remove(previous_mesh)
            state = "rebuilt"
        _configure_cage_display(cage, source, distance)
        _show_cage(cage, True)
    else:
        _configure_cage_display(cage, source, distance)
        show = _cage_hidden(cage)
        _show_cage(cage, show)
        state = "shown" if show else "hidden"

    final_inspection = inspect_bake_cage(source, cage)
    if not final_inspection.ready:
        raise SurfaceWorkflowError(final_inspection.message)
    return {
        "cage": cage,
        "state": state,
        "distance": distance,
        "signature": final_inspection.source_signature,
        "topology_matches": True,
    }


def _material_bake_images(low: Object) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    image_names: list[str] = []
    used_slots = sorted({int(polygon.material_index) for polygon in low.data.polygons})
    if not used_slots:
        used_slots = [int(low.active_material_index)]
    for slot_index in used_slots:
        material = (
            low.material_slots[slot_index].material
            if 0 <= slot_index < len(low.material_slots)
            else None
        )
        slot_label = f"material slot {slot_index + 1}"
        if material is None:
            errors.append(f"{slot_label} has no material")
            continue
        if not material.use_nodes or material.node_tree is None:
            errors.append(f"{material.name!r} does not use nodes")
            continue
        node = material.node_tree.nodes.active
        if node is None or node.type != "TEX_IMAGE" or node.image is None:
            errors.append(
                f"{material.name!r} needs an active Image Texture with an image"
            )
            continue
        image_names.append(node.image.name)
    return image_names, errors


def _object_transform_warnings(obj: Object) -> list[str]:
    warnings: list[str] = []
    _location, rotation, scale = obj.matrix_basis.decompose()
    if any(abs(component - 1.0) > 1.0e-5 for component in scale):
        warnings.append(
            f"{obj.name!r} has unapplied scale "
            f"({scale.x:.3g}, {scale.y:.3g}, {scale.z:.3g})"
        )
    if abs(rotation.angle) > 1.0e-5:
        warnings.append(f"{obj.name!r} has unapplied rotation")
    if obj.matrix_world.to_3x3().determinant() < 0.0:
        warnings.append(f"{obj.name!r} has a negative transform that flips normals")
    return warnings


def _mesh_normal_warnings(obj: Object) -> list[str]:
    warnings: list[str] = []
    mesh = obj.data
    zero_area = sum(polygon.area <= 1.0e-14 for polygon in mesh.polygons)
    if zero_area:
        warnings.append(f"{obj.name!r} has {zero_area} zero-area face(s)")

    edge_directions: dict[tuple[int, int], list[int]] = defaultdict(list)
    for polygon in mesh.polygons:
        vertices = tuple(int(index) for index in polygon.vertices)
        for index, first in enumerate(vertices):
            second = vertices[(index + 1) % len(vertices)]
            key = (min(first, second), max(first, second))
            edge_directions[key].append(1 if first < second else -1)
    inconsistent = sum(
        len(directions) == 2 and directions[0] == directions[1]
        for directions in edge_directions.values()
    )
    nonmanifold = sum(len(directions) > 2 for directions in edge_directions.values())
    if inconsistent:
        warnings.append(
            f"{obj.name!r} has {inconsistent} shared edge(s) with inconsistent winding"
        )
    if nonmanifold:
        warnings.append(
            f"{obj.name!r} has {nonmanifold} edge(s) used by over two faces"
        )
    return warnings


def inspect_bake_readiness(
    context: Any,
    *,
    use_cage: bool = False,
    cage: Object | None = None,
) -> BakeReadiness:
    """Inspect the complete selected-to-active contract without mutating it."""

    errors: list[str] = []
    warnings: list[str] = []
    low = context.active_object
    if low is None or low.type != "MESH":
        return BakeReadiness(
            ready=False,
            low_object="",
            high_objects=(),
            image_names=(),
            cage_name="",
            use_cage=use_cage,
            errors=("Active low-poly object must be a mesh",),
            warnings=(),
        )
    if bool(low.get(CAGE_ROLE_KEY, False)):
        errors.append("The active object is a bake cage; activate the low-poly mesh")

    selected = tuple(getattr(context, "selected_objects", ()) or ())
    high_objects = tuple(
        obj
        for obj in selected
        if obj != low and obj.type == "MESH" and not bool(obj.get(CAGE_ROLE_KEY, False))
    )
    if not high_objects:
        errors.append(
            "Select at least one separate high-poly mesh with the low mesh active"
        )
    if len(low.data.polygons) == 0:
        errors.append("The active low-poly mesh has no faces")
    if len(low.data.uv_layers) == 0:
        errors.append("The active low-poly mesh needs a UV map")

    image_names, image_errors = _material_bake_images(low)
    errors.extend(image_errors)
    warnings.extend(_object_transform_warnings(low))
    warnings.extend(_mesh_normal_warnings(low))
    for high in high_objects:
        if len(high.data.polygons) == 0:
            errors.append(f"High-poly object {high.name!r} has no faces")
        warnings.extend(_object_transform_warnings(high))
        warnings.extend(_mesh_normal_warnings(high))
        if high.hide_render:
            warnings.append(f"High-poly object {high.name!r} is disabled for rendering")

    cage = cage or find_bake_cage(low)
    cage_inspection = inspect_bake_cage(low, cage)
    if use_cage:
        if cage_inspection.state == "MISSING":
            errors.append("Custom-cage mode requires a bake cage")
        elif not cage_inspection.ready:
            errors.append(cage_inspection.message)
    elif cage is not None and not cage_inspection.ready:
        warnings.append(cage_inspection.message)

    errors = list(dict.fromkeys(errors))
    warnings = list(dict.fromkeys(warnings))
    return BakeReadiness(
        ready=not errors,
        low_object=low.name,
        high_objects=tuple(obj.name for obj in high_objects),
        image_names=tuple(dict.fromkeys(image_names)),
        cage_name=cage.name if cage is not None else "",
        use_cage=use_cage,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def _set_bake_cage_reference(bake: Any, cage: Object | None) -> None:
    if not hasattr(bake, "cage_object"):
        if cage is not None:
            raise SurfaceWorkflowError("This Blender build has no custom cage setting")
        return
    try:
        prop = bake.bl_rna.properties["cage_object"]
        setattr(
            bake,
            "cage_object",
            cage if prop.type == "POINTER" else cage.name if cage else "",
        )
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise SurfaceWorkflowError(f"Could not assign the bake cage: {exc}") from exc


def resolve_bake_type(bake: Any, requested: str) -> str:
    """Map stable add-on bake names to the active Blender RNA identifiers."""

    aliases = {
        "NORMAL": ("NORMALS",),
        "NORMALS": ("NORMAL",),
        "DISPLACEMENT": (),
    }
    return _resolve_enum(bake, "type", requested, aliases)


def configure_selected_to_active_bake(
    context: Any,
    *,
    bake_type: str = "NORMAL",
    margin: int = 16,
    max_ray_distance: float = 0.02,
    use_cage: bool = False,
    cage: Object | None = None,
) -> dict[str, Any]:
    """Configure Cycles selected-to-active state without invoking a bake."""

    max_ray_distance = _finite_nonnegative(max_ray_distance, "Max ray distance")
    margin = int(margin)
    if margin < 0:
        raise SurfaceWorkflowError("Bake margin must be non-negative")
    readiness = inspect_bake_readiness(context, use_cage=use_cage, cage=cage)
    if not readiness.ready:
        raise SurfaceWorkflowError(readiness.summary)

    scene = context.scene
    try:
        scene.render.engine = "CYCLES"
    except (TypeError, ValueError) as exc:
        raise SurfaceWorkflowError(
            "Cycles is unavailable in this Blender installation"
        ) from exc
    bake = scene.render.bake
    resolved_type = resolve_bake_type(bake, bake_type)
    bake.type = resolved_type
    bake.use_selected_to_active = True
    bake.margin = margin
    if hasattr(bake, "target"):
        targets = _enum_identifiers(bake, "target")
        if not targets or "IMAGE_TEXTURES" in targets:
            bake.target = "IMAGE_TEXTURES"
    if bake_type in {"NORMAL", "NORMALS"} and hasattr(bake, "normal_space"):
        normal_spaces = _enum_identifiers(bake, "normal_space")
        if not normal_spaces or "TANGENT" in normal_spaces:
            bake.normal_space = "TANGENT"

    if use_cage:
        cage = cage or find_bake_cage(context.active_object)
        bake.use_cage = True
        _set_bake_cage_reference(bake, cage)
        bake.max_ray_distance = 0.0
        bake.cage_extrusion = 0.0
        ray_mode = "custom cage"
    else:
        bake.use_cage = False
        _set_bake_cage_reference(bake, None)
        bake.max_ray_distance = max_ray_distance
        bake.cage_extrusion = 0.0
        ray_mode = "max ray distance"

    return {
        "readiness": readiness,
        "requested_type": bake_type,
        "resolved_type": resolved_type,
        "margin": margin,
        "mode": ray_mode,
        "max_ray_distance": bake.max_ray_distance,
        "cage": cage if use_cage else None,
        "bake_started": False,
    }


class QT_OT_setup_shrinkwrap(Operator):
    bl_idname = "object.quad_transition_setup_shrinkwrap"
    bl_label = "Set Up Retopo Shrinkwrap"
    bl_description = (
        "Add or update a live Shrinkwrap modifier on the active retopo mesh"
    )
    bl_options = {"REGISTER", "UNDO"}

    target_name: StringProperty(
        name="Surface Target",
        description="Name of the separate high-poly surface mesh",
        default="",
    )
    wrap_method: EnumProperty(
        name="Method",
        items=SHRINKWRAP_METHOD_ITEMS,
        default="NEAREST_SURFACEPOINT",
    )
    offset: FloatProperty(name="Offset", default=0.002, precision=4)
    project_limit: FloatProperty(name="Project Limit", min=0.0, default=0.0)
    vertex_group: StringProperty(
        name="Vertex Group",
        description="Optional existing vertex group that limits the modifier",
        default="",
    )

    @classmethod
    def poll(cls, context: Any) -> bool:
        return (
            context.active_object is not None and context.active_object.type == "MESH"
        )

    def execute(self, context: Any):
        source = context.active_object
        target = bpy.data.objects.get(self.target_name) if self.target_name else None
        try:
            result = ensure_shrinkwrap_modifier(
                source,
                target,
                wrap_method=self.wrap_method,
                offset=self.offset,
                project_limit=self.project_limit,
                vertex_group=self.vertex_group,
            )
        except SurfaceWorkflowError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        action = "Created" if result["created"] else "Updated"
        message = f"{action} Shrinkwrap using {result['resolved_method']}"
        if result["used_fallback"]:
            self.report({"WARNING"}, f"{message} (fallback for {self.wrap_method})")
        else:
            self.report({"INFO"}, message)
        return {"FINISHED"}


class QT_OT_toggle_bake_cage(Operator):
    bl_idname = "object.quad_transition_toggle_bake_cage"
    bl_label = "Toggle Bake Cage"
    bl_description = "Create, rebuild, or show/hide an exact-topology wire bake cage"
    bl_options = {"REGISTER", "UNDO"}

    distance: FloatProperty(
        name="Cage Distance",
        description="Local-space offset along low-poly vertex normals",
        min=0.0,
        default=0.03,
        precision=4,
    )
    force_rebuild: BoolProperty(name="Rebuild", default=False)

    @classmethod
    def poll(cls, context: Any) -> bool:
        return (
            context.active_object is not None and context.active_object.type == "MESH"
        )

    def execute(self, context: Any):
        try:
            result = toggle_bake_cage(
                context,
                context.active_object,
                distance=self.distance,
                force_rebuild=self.force_rebuild,
            )
        except SurfaceWorkflowError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report(
            {"INFO"},
            f"Bake cage {result['state']}; topology signature {result['signature']}",
        )
        return {"FINISHED"}


class QT_OT_inspect_bake_readiness(Operator):
    bl_idname = "object.quad_transition_inspect_bake"
    bl_label = "Inspect Bake Readiness"
    bl_description = (
        "Check UVs, images, source selection, transforms, normals, and cage parity"
    )
    bl_options = {"REGISTER"}

    use_cage: BoolProperty(name="Use Custom Cage", default=False)
    cage_name: StringProperty(name="Bake Cage", default="")

    def execute(self, context: Any):
        cage = bpy.data.objects.get(self.cage_name) if self.cage_name else None
        result = inspect_bake_readiness(context, use_cage=self.use_cage, cage=cage)
        context.scene[BAKE_STATUS_KEY] = result.summary
        context.scene[BAKE_READY_KEY] = result.ready
        if result.errors:
            self.report({"ERROR"}, result.summary)
        elif result.warnings:
            self.report({"WARNING"}, result.summary)
        else:
            self.report({"INFO"}, result.summary)
        return {"FINISHED"}


class QT_OT_configure_bake(Operator):
    bl_idname = "object.quad_transition_configure_bake"
    bl_label = "Configure Selected-to-Active"
    bl_description = "Configure Cycles baking without starting the bake"
    bl_options = {"REGISTER", "UNDO"}

    bake_type: EnumProperty(name="Bake Type", items=BAKE_TYPE_ITEMS, default="NORMAL")
    margin: IntProperty(name="Margin", min=0, max=32767, default=16)
    max_ray_distance: FloatProperty(name="Max Ray Distance", min=0.0, default=0.02)
    use_cage: BoolProperty(name="Use Custom Cage", default=False)
    cage_name: StringProperty(name="Bake Cage", default="")

    def execute(self, context: Any):
        cage = bpy.data.objects.get(self.cage_name) if self.cage_name else None
        try:
            result = configure_selected_to_active_bake(
                context,
                bake_type=self.bake_type,
                margin=self.margin,
                max_ray_distance=self.max_ray_distance,
                use_cage=self.use_cage,
                cage=cage,
            )
        except SurfaceWorkflowError as exc:
            context.scene[BAKE_STATUS_KEY] = str(exc)
            context.scene[BAKE_READY_KEY] = False
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        readiness = result["readiness"]
        status = (
            f"Configured {result['resolved_type']} via {result['mode']}; "
            "bake not started"
        )
        context.scene[BAKE_STATUS_KEY] = status
        context.scene[BAKE_READY_KEY] = True
        self.report(
            {"WARNING" if readiness.warnings else "INFO"},
            status,
        )
        return {"FINISHED"}


CLASSES = (
    QT_OT_setup_shrinkwrap,
    QT_OT_toggle_bake_cage,
    QT_OT_inspect_bake_readiness,
    QT_OT_configure_bake,
)


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
