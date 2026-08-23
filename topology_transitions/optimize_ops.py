"""Feature-aware, all-quad simplification of repetitive remesher edge flows."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from statistics import median
from typing import Any

import bmesh
import bpy
from bpy.types import Operator
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from .edit_mesh import restore_bmesh
from .manifold import boundary_components
from .quad_optimize import LoopChoice, choose_loop_batch

MAX_ERROR_SAMPLES = 50000
MAX_BATCH_PARTITION_ATTEMPTS = 64
SCOPE_LAYER_NAME = "__topology_optimize_scope__"

SENSITIVITY_PRESETS = {
    "CONSERVATIVE": {
        "label": "Conservative",
        "max_face_angle": 8.0,
        "feature_angle": 30.0,
        "max_error_percent": 2.5,
        "branch_guard_rings": 1,
        "min_loop_edges": 4,
        "preserve_boundaries": True,
    },
    "BALANCED": {
        "label": "Balanced",
        "max_face_angle": 18.0,
        "feature_angle": 45.0,
        "max_error_percent": 5.0,
        "branch_guard_rings": 0,
        "min_loop_edges": 3,
        "preserve_boundaries": False,
    },
    "AGGRESSIVE": {
        "label": "Aggressive",
        "max_face_angle": 35.0,
        "feature_angle": 65.0,
        "max_error_percent": 10.0,
        "branch_guard_rings": 0,
        "min_loop_edges": 2,
        "preserve_boundaries": False,
    },
}

REJECTION_LABELS = {
    "too_short": "shorter than Minimum Flow Length",
    "interrupted": "interrupted by poles or irregular branches",
    "boundary_protected": "ending on protected open boundaries",
    "branch_guard": "inside Branch Guard Rings",
    "unsafe_topology": "hidden, non-manifold, or non-quad",
    "feature_flow": "protected feature flows",
    "outside_selection": "outside the selected-face scope",
    "feature_crossing": "crossing mismatched feature lines",
    "surface_error": "over the Surface Error limit",
    "curvature": "over the Surface Angle limit",
}


class QuadOptimizeError(ValueError):
    pass


@dataclass(frozen=True)
class OptimizeOptions:
    mode: str
    label: str
    cycles: int
    max_face_angle: float
    feature_angle: float
    max_error_ratio: float
    branch_guard_rings: int
    min_loop_edges: int
    preserve_boundaries: bool
    preserve_seams: bool
    preserve_sharp: bool
    preserve_creases: bool
    preserve_materials: bool
    selected_faces_only: bool


@dataclass
class LoopCandidate:
    identifier: int
    edges: tuple[Any, ...]
    vertices: tuple[Any, ...]
    closed: bool
    face_ids: frozenset[int]
    error_ratio: float
    curvature: float
    stable_index: int
    conflict_ids: set[int]

    @property
    def key(self) -> tuple[int, ...]:
        return tuple(edge.index for edge in self.edges)


@dataclass(frozen=True)
class BridgeFeature:
    first: Any
    second: Any
    seam: bool
    smooth: bool
    float_values: tuple[tuple[Any, float], ...]


@dataclass(frozen=True)
class DetectionResult:
    options: OptimizeOptions
    candidates: tuple[LoopCandidate, ...]
    selected_ids: frozenset[int]
    diagnostics: Counter[str]
    attempted_labels: tuple[str, ...]


def _options_from_settings(
    settings: Any,
    mode: str | None = None,
) -> OptimizeOptions:
    resolved_mode = mode or settings.optimize_sensitivity
    if resolved_mode == "AUTO":
        resolved_mode = "CONSERVATIVE"
    preset = SENSITIVITY_PRESETS.get(resolved_mode)
    if preset is None:
        preset = {
            "label": "Custom",
            "max_face_angle": settings.optimize_max_face_angle,
            "feature_angle": settings.optimize_feature_angle,
            "max_error_percent": settings.optimize_max_error_percent,
            "branch_guard_rings": settings.optimize_branch_guard_rings,
            "min_loop_edges": settings.optimize_min_loop_edges,
            "preserve_boundaries": settings.optimize_preserve_boundaries,
        }
    return OptimizeOptions(
        mode=resolved_mode,
        label=str(preset["label"]),
        cycles=settings.optimize_cycles,
        max_face_angle=math.radians(float(preset["max_face_angle"])),
        feature_angle=math.radians(float(preset["feature_angle"])),
        max_error_ratio=float(preset["max_error_percent"]) / 100.0,
        branch_guard_rings=int(preset["branch_guard_rings"]),
        min_loop_edges=int(preset["min_loop_edges"]),
        preserve_boundaries=bool(preset["preserve_boundaries"]),
        preserve_seams=settings.optimize_preserve_seams,
        preserve_sharp=settings.optimize_preserve_sharp,
        preserve_creases=settings.optimize_preserve_creases,
        preserve_materials=settings.optimize_preserve_materials,
        selected_faces_only=settings.optimize_scope == "SELECTED",
    )


def _option_portfolio_from_settings(settings: Any) -> tuple[OptimizeOptions, ...]:
    if settings.optimize_sensitivity == "AUTO":
        return tuple(
            _options_from_settings(settings, mode)
            for mode in ("CONSERVATIVE", "BALANCED", "AGGRESSIVE")
        )
    return (_options_from_settings(settings),)


def _edge_angle(edge: Any) -> float:
    if len(edge.link_faces) != 2:
        return 0.0
    dot = max(-1.0, min(1.0, edge.link_faces[0].normal.dot(edge.link_faces[1].normal)))
    return math.acos(dot)


def _edge_float_layers(bm: Any) -> tuple[Any, ...]:
    layers = []
    for name in ("crease_edge", "crease", "bevel_weight_edge", "bevel_weight"):
        layer = bm.edges.layers.float.get(name)
        if layer is not None and layer not in layers:
            layers.append(layer)
    return tuple(layers)


def _edge_feature_signature(
    edge: Any,
    options: OptimizeOptions,
    float_layers: tuple[Any, ...],
) -> tuple[bool, ...]:
    signature = []
    if options.preserve_seams:
        signature.append(bool(edge.seam))
    if options.preserve_sharp:
        signature.append(not edge.smooth)
    if options.preserve_creases:
        signature.append(
            _edge_angle(edge) >= options.feature_angle
            or any(abs(float(edge[layer])) > 1.0e-8 for layer in float_layers)
        )
    if options.preserve_materials:
        signature.append(
            len(edge.link_faces) == 2
            and edge.link_faces[0].material_index
            != edge.link_faces[1].material_index
        )
    return tuple(signature)


def _is_feature_edge(
    edge: Any,
    options: OptimizeOptions,
    float_layers: tuple[Any, ...],
) -> bool:
    return any(_edge_feature_signature(edge, options, float_layers))


def _other_vertex(edge: Any, vertex: Any) -> Any:
    return edge.verts[1] if edge.verts[0] == vertex else edge.verts[0]


def _chord_error_ratio(vertex: Any, first_edge: Any, second_edge: Any) -> float:
    first = _other_vertex(first_edge, vertex).co
    second = _other_vertex(second_edge, vertex).co
    span = second - first
    if span.length_squared <= 1.0e-20:
        return math.inf
    factor = max(0.0, min(1.0, (vertex.co - first).dot(span) / span.length_squared))
    distance = (vertex.co - (first + span * factor)).length
    scale = 0.5 * ((vertex.co - first).length + (vertex.co - second).length)
    return distance / max(scale, 1.0e-12)


def _regular_opposite(edge: Any, vertex: Any) -> Any | None:
    if len(vertex.link_edges) != 4 or any(
        len(face.verts) != 4 for face in vertex.link_faces
    ):
        return None
    linked_faces = set(edge.link_faces)
    candidates = [
        candidate
        for candidate in vertex.link_edges
        if candidate != edge and not (set(candidate.link_faces) & linked_faces)
    ]
    return candidates[0] if len(candidates) == 1 else None


def _loop_components(bm: Any) -> tuple[tuple[Any, ...], ...]:
    neighbors: dict[Any, set[Any]] = {
        edge: set() for edge in bm.edges if len(edge.link_faces) == 2
    }
    for vertex in bm.verts:
        if len(vertex.link_edges) != 4 or any(
            len(face.verts) != 4 for face in vertex.link_faces
        ):
            continue
        for edge in vertex.link_edges:
            if edge not in neighbors:
                continue
            opposite = _regular_opposite(edge, vertex)
            if opposite is not None and opposite in neighbors:
                neighbors[edge].add(opposite)
                neighbors[opposite].add(edge)

    remaining = set(neighbors)
    components = []
    while remaining:
        seed = min(remaining, key=lambda edge: edge.index)
        pending = [seed]
        component = set()
        while pending:
            edge = pending.pop()
            if edge in component:
                continue
            component.add(edge)
            pending.extend(neighbors[edge] - component)
        remaining -= component
        components.append(tuple(sorted(component, key=lambda edge: edge.index)))
    return tuple(components)


def _protected_vertices(
    bm: Any,
    options: OptimizeOptions,
    float_layers: tuple[Any, ...],
) -> set[Any]:
    seeds = set()
    for vertex in bm.verts:
        boundary_regular = (
            not options.preserve_boundaries
            and vertex.is_boundary
            and len(vertex.link_edges) == 3
            and all(len(face.verts) == 4 for face in vertex.link_faces)
        )
        if not boundary_regular and (
            len(vertex.link_edges) != 4
            or any(len(face.verts) != 4 for face in vertex.link_faces)
        ):
            seeds.add(vertex)
            continue
        feature_edges = [
            edge
            for edge in vertex.link_edges
            if _is_feature_edge(edge, options, float_layers)
        ]
        if len(feature_edges) not in {0, 2}:
            seeds.add(vertex)
        elif len(feature_edges) == 2 and _chord_error_ratio(
            vertex, feature_edges[0], feature_edges[1]
        ) > options.max_error_ratio:
            seeds.add(vertex)

    protected = set(seeds)
    frontier = set(seeds)
    for _ring in range(options.branch_guard_rings):
        following = {
            _other_vertex(edge, vertex)
            for vertex in frontier
            for edge in vertex.link_edges
        } - protected
        protected.update(following)
        frontier = following
        if not frontier:
            break
    return protected


def _candidate_from_component(
    identifier: int,
    component: tuple[Any, ...],
    options: OptimizeOptions,
    protected: set[Any],
    float_layers: tuple[Any, ...],
    scope_layer: Any | None,
) -> tuple[LoopCandidate | None, str | None]:
    if len(component) < options.min_loop_edges:
        return None, "too_short"
    component_set = set(component)
    vertices = {vertex for edge in component for vertex in edge.verts}
    incident_counts = Counter(
        vertex for edge in component for vertex in edge.verts
    )
    endpoints = [vertex for vertex, count in incident_counts.items() if count == 1]
    closed = not endpoints and all(count == 2 for count in incident_counts.values())
    open_boundary = (
        len(endpoints) == 2
        and all(vertex.is_boundary for vertex in endpoints)
        and all(count in {1, 2} for count in incident_counts.values())
    )
    if not closed and not open_boundary:
        return None, "interrupted"
    if open_boundary and options.preserve_boundaries:
        return None, "boundary_protected"
    if any(vertex in protected for vertex in vertices):
        return None, "branch_guard"
    if any(
        len(edge.link_faces) != 2
        or edge.hide
        or any(face.hide or len(face.verts) != 4 for face in edge.link_faces)
        for edge in component
    ):
        return None, "unsafe_topology"
    if any(_is_feature_edge(edge, options, float_layers) for edge in component):
        return None, "feature_flow"

    linked_faces = {face for edge in component for face in edge.link_faces}
    if options.selected_faces_only:
        if any(
            not face.select
            and (scope_layer is None or not face[scope_layer])
            for face in linked_faces
        ):
            return None, "outside_selection"

    chord_errors = []
    for vertex in vertices:
        crossing = [edge for edge in vertex.link_edges if edge not in component_set]
        if len(crossing) != 2:
            return None, "interrupted"
        first_signature = _edge_feature_signature(
            crossing[0], options, float_layers
        )
        second_signature = _edge_feature_signature(
            crossing[1], options, float_layers
        )
        if first_signature != second_signature:
            return None, "feature_crossing"
        chord_errors.append(_chord_error_ratio(vertex, crossing[0], crossing[1]))
    error_ratio = max(chord_errors, default=0.0)
    curvature = max((_edge_angle(edge) for edge in component), default=0.0)
    if error_ratio > options.max_error_ratio:
        return None, "surface_error"
    if curvature > options.max_face_angle:
        return None, "curvature"

    return (
        LoopCandidate(
            identifier=identifier,
            edges=component,
            vertices=tuple(sorted(vertices, key=lambda vertex: vertex.index)),
            closed=closed,
            face_ids=frozenset(face.index for face in linked_faces),
            error_ratio=error_ratio,
            curvature=curvature,
            stable_index=min(edge.index for edge in component),
            conflict_ids=set(),
        ),
        None,
    )


def discover_loop_candidates(
    bm: Any,
    options: OptimizeOptions,
    *,
    diagnostics: Counter[str] | None = None,
) -> tuple[LoopCandidate, ...]:
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.verts.index_update()
    bm.edges.index_update()
    bm.faces.index_update()
    bm.normal_update()
    if any(len(face.verts) != 4 for face in bm.faces):
        raise QuadOptimizeError(
            "Quad Flow Optimization requires an all-quad mesh; repair non-quads first"
        )
    if any(len(edge.link_faces) > 2 for edge in bm.edges):
        raise QuadOptimizeError(
            "Repair over-connected non-manifold edges before optimizing quad flow"
        )

    float_layers = _edge_float_layers(bm)
    scope_layer = bm.faces.layers.int.get(SCOPE_LAYER_NAME)
    protected = _protected_vertices(bm, options, float_layers)
    candidates = []
    components = _loop_components(bm)
    if diagnostics is not None:
        diagnostics["total"] += len(components)
    for identifier, component in enumerate(components):
        candidate, rejection = _candidate_from_component(
            identifier,
            component,
            options,
            protected,
            float_layers,
            scope_layer,
        )
        if candidate is not None:
            candidates.append(candidate)
            if diagnostics is not None:
                diagnostics["eligible"] += 1
        elif diagnostics is not None and rejection is not None:
            diagnostics[rejection] += 1

    edge_to_candidate = {
        edge: candidate.identifier
        for candidate in candidates
        for edge in candidate.edges
    }
    by_identifier = {candidate.identifier: candidate for candidate in candidates}
    for face in bm.faces:
        edges = tuple(loop.edge for loop in face.loops)
        for first, second in ((edges[0], edges[2]), (edges[1], edges[3])):
            first_id = edge_to_candidate.get(first)
            second_id = edge_to_candidate.get(second)
            if first_id is None or second_id is None or first_id == second_id:
                continue
            by_identifier[first_id].conflict_ids.add(second_id)
            by_identifier[second_id].conflict_ids.add(first_id)
    return tuple(sorted(candidates, key=lambda candidate: candidate.identifier))


def choose_candidates(candidates: tuple[LoopCandidate, ...]) -> tuple[int, ...]:
    choices = tuple(
        LoopChoice(
            identifier=candidate.identifier,
            conflict_ids=frozenset(candidate.conflict_ids),
            error_ratio=candidate.error_ratio,
            curvature=candidate.curvature,
            edge_count=len(candidate.edges),
            stable_index=candidate.stable_index,
        )
        for candidate in candidates
    )
    return choose_loop_batch(choices)


def _detect_with_portfolio(
    bm: Any,
    portfolio: tuple[OptimizeOptions, ...],
) -> DetectionResult:
    attempted = []
    last_result = None
    for options in portfolio:
        diagnostics: Counter[str] = Counter()
        candidates = discover_loop_candidates(
            bm,
            options,
            diagnostics=diagnostics,
        )
        selected_ids = frozenset(choose_candidates(candidates))
        attempted.append(options.label)
        last_result = DetectionResult(
            options=options,
            candidates=candidates,
            selected_ids=selected_ids,
            diagnostics=diagnostics,
            attempted_labels=tuple(attempted),
        )
        if selected_ids:
            return last_result
    if last_result is None:
        raise QuadOptimizeError("No optimization sensitivity modes are configured")
    return last_result


def _diagnostic_summary(diagnostics: Counter[str]) -> str:
    blockers = [
        (count, REJECTION_LABELS[reason])
        for reason, count in diagnostics.items()
        if count and reason in REJECTION_LABELS
    ]
    blockers.sort(key=lambda item: (-item[0], item[1]))
    if not blockers:
        return (
            "No internal quad flows were found; the mesh may be too small or its "
            "flows may end immediately at poles"
        )
    return "Main blockers: " + "; ".join(
        f"{count} {label}" for count, label in blockers[:3]
    )


def _topology_signature(bm: Any) -> tuple[int, int, int, int, int]:
    edge_vertices = {
        edge.index: (edge.verts[0].index, edge.verts[1].index) for edge in bm.edges
    }
    open_edges = [edge.index for edge in bm.edges if len(edge.link_faces) == 1]
    boundary_count = len(boundary_components(edge_vertices, open_edges))
    return (
        len(bm.verts) - len(bm.edges) + len(bm.faces),
        boundary_count,
        sum(len(edge.link_faces) > 2 for edge in bm.edges),
        sum(not edge.link_faces for edge in bm.edges),
        sum(not vertex.link_edges for vertex in bm.verts),
    )


def _topology_change_message(
    before: tuple[int, int, int, int, int],
    after: tuple[int, int, int, int, int],
) -> str:
    labels = (
        "Euler characteristic",
        "boundary components",
        "over-connected edges",
        "wire edges",
        "isolated vertices",
    )
    changes = [
        f"{label} {old}->{new}"
        for label, old, new in zip(labels, before, after, strict=True)
        if old != new
    ]
    detail = ", ".join(changes) if changes else "unknown signature difference"
    return f"Candidate batch changed protected topology ({detail})"


def _capture_bridge_features(
    bm: Any, selected_edges: set[Any]
) -> tuple[BridgeFeature, ...]:
    float_layers = _edge_float_layers(bm)
    vertices = {vertex for edge in selected_edges for vertex in edge.verts}
    records = []
    for vertex in vertices:
        crossing = [edge for edge in vertex.link_edges if edge not in selected_edges]
        if len(crossing) != 2:
            continue
        first, second = crossing
        records.append(
            BridgeFeature(
                first=_other_vertex(first, vertex),
                second=_other_vertex(second, vertex),
                seam=first.seam or second.seam,
                smooth=first.smooth and second.smooth,
                float_values=tuple(
                    (layer, max(float(first[layer]), float(second[layer])))
                    for layer in float_layers
                ),
            )
        )
    return tuple(records)


def _restore_bridge_features(bm: Any, records: tuple[BridgeFeature, ...]) -> None:
    for record in records:
        if not record.first.is_valid or not record.second.is_valid:
            continue
        edge = bm.edges.get((record.first, record.second))
        if edge is None:
            continue
        edge.seam = record.seam
        edge.smooth = record.smooth
        for layer, value in record.float_values:
            edge[layer] = value


def _error_samples(faces: set[Any]) -> tuple[Vector, ...]:
    coordinates = []
    seen = set()
    for face in sorted(faces, key=lambda item: item.index):
        for coordinate in (
            *(vertex.co for vertex in face.verts),
            face.calc_center_median(),
        ):
            key = tuple(round(float(value), 12) for value in coordinate)
            if key in seen:
                continue
            seen.add(key)
            coordinates.append(coordinate.copy())
    if len(coordinates) <= MAX_ERROR_SAMPLES:
        return tuple(coordinates)
    step = math.ceil(len(coordinates) / MAX_ERROR_SAMPLES)
    return tuple(coordinates[::step])


def _surface_error(bm: Any, samples: tuple[Vector, ...]) -> tuple[float, float]:
    tree = BVHTree.FromBMesh(bm)
    distances = []
    for coordinate in samples:
        nearest = tree.find_nearest(coordinate)
        if nearest is None:
            raise QuadOptimizeError("Optimized surface could not be sampled")
        distances.append(nearest[3])
    if not distances:
        return 0.0, 0.0
    maximum = max(distances)
    rms = math.sqrt(sum(distance * distance for distance in distances) / len(distances))
    return maximum, rms


def _apply_candidate_batch(
    bm: Any,
    candidates: tuple[LoopCandidate, ...],
    selected_ids: set[int],
    options: OptimizeOptions,
) -> dict[str, float | int]:
    selected = [
        candidate for candidate in candidates if candidate.identifier in selected_ids
    ]
    if not selected:
        raise QuadOptimizeError("No compatible edge-flow batch was selected")
    selected_edges = {edge for candidate in selected for edge in candidate.edges}
    affected_faces = {face for edge in selected_edges for face in edge.link_faces}
    samples = _error_samples(affected_faces)
    local_lengths = [edge.calc_length() for edge in selected_edges]
    scale = median(local_lengths) if local_lengths else 0.0
    allowed_error = max(scale * options.max_error_ratio, scale * 1.0e-8, 1.0e-10)
    before_faces = len(bm.faces)
    before_vertices = len(bm.verts)
    before_signature = _topology_signature(bm)
    bridge_features = _capture_bridge_features(bm, selected_edges)

    bmesh.ops.dissolve_edges(
        bm,
        edges=sorted(selected_edges, key=lambda edge: edge.index),
        use_verts=True,
        use_face_split=False,
    )
    _restore_bridge_features(bm, bridge_features)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.verts.index_update()
    bm.edges.index_update()
    bm.faces.index_update()
    bm.normal_update()

    if len(bm.faces) >= before_faces:
        raise QuadOptimizeError("Candidate flows did not reduce the face count")
    if any(
        len(face.verts) != 4 or face.calc_area() <= 1.0e-12 for face in bm.faces
    ):
        raise QuadOptimizeError(
            "Candidate batch did not remain valid all-quad topology"
        )
    after_signature = _topology_signature(bm)
    if after_signature != before_signature:
        raise QuadOptimizeError(
            _topology_change_message(before_signature, after_signature)
        )
    maximum_error, rms_error = _surface_error(bm, samples)
    if maximum_error > allowed_error:
        raise QuadOptimizeError(
            f"Surface error {maximum_error:.6g} exceeded {allowed_error:.6g}"
        )
    return {
        "removed_faces": before_faces - len(bm.faces),
        "removed_vertices": before_vertices - len(bm.verts),
        "loops": len(selected),
        "max_error": maximum_error,
        "rms_error": rms_error,
    }


def _try_candidate_keys(
    working: Any,
    options: OptimizeOptions,
    keys: tuple[tuple[int, ...], ...],
) -> tuple[tuple[Any, dict[str, float | int]] | None, str]:
    trial = working.copy()
    try:
        trial_candidates = discover_loop_candidates(trial, options)
        trial_by_key = {
            candidate.key: candidate for candidate in trial_candidates
        }
        missing = [key for key in keys if key not in trial_by_key]
        if missing:
            raise QuadOptimizeError(
                f"{len(missing)} candidate flows could not be remapped safely"
            )
        trial_selected = {
            trial_by_key[key].identifier
            for key in keys
        }
        result = _apply_candidate_batch(
            trial,
            trial_candidates,
            trial_selected,
            options,
        )
        return (trial, result), ""
    except (QuadOptimizeError, RuntimeError, ValueError) as exc:
        trial.free()
        return None, str(exc)


def _apply_partitioned_candidate_batch(
    working: Any,
    candidates: tuple[LoopCandidate, ...],
    selected_ids: set[int],
    options: OptimizeOptions,
) -> tuple[Any, dict[str, float | int], int, int, str]:
    selected = sorted(
        (
            candidate
            for candidate in candidates
            if candidate.identifier in selected_ids
        ),
        key=lambda candidate: (
            candidate.error_ratio,
            candidate.curvature,
            -len(candidate.edges),
            candidate.stable_index,
        ),
    )
    selected_keys = tuple(candidate.key for candidate in selected)
    if not selected_keys:
        raise QuadOptimizeError("No compatible edge-flow batch was selected")

    accepted, failure = _try_candidate_keys(working, options, selected_keys)
    attempts = 1
    if accepted is not None:
        trial, result = accepted
        return trial, result, attempts, 0, ""

    midpoint = len(selected_keys) // 2
    queue = [selected_keys[:midpoint], selected_keys[midpoint:]]
    queue = [keys for keys in queue if keys]
    safe_keys: set[tuple[int, ...]] = set()
    best: tuple[Any, dict[str, float | int], tuple[tuple[int, ...], ...]] | None = None
    failures = [failure]

    while queue and attempts < MAX_BATCH_PARTITION_ATTEMPTS:
        keys = queue.pop(0)
        accepted, subset_failure = _try_candidate_keys(working, options, keys)
        attempts += 1
        if accepted is not None:
            trial, result = accepted
            safe_keys.update(keys)
            if best is None or len(keys) > len(best[2]):
                if best is not None:
                    best[0].free()
                best = (trial, result, keys)
            else:
                trial.free()
            continue
        failures.append(subset_failure)
        if len(keys) > 1:
            midpoint = len(keys) // 2
            queue.extend((keys[:midpoint], keys[midpoint:]))

    combined_keys = tuple(key for key in selected_keys if key in safe_keys)
    if (
        best is not None
        and len(combined_keys) > len(best[2])
        and attempts < MAX_BATCH_PARTITION_ATTEMPTS
    ):
        combined, combined_failure = _try_candidate_keys(
            working,
            options,
            combined_keys,
        )
        attempts += 1
        if combined is not None:
            best[0].free()
            trial, result = combined
            deferred = len(selected_keys) - len(combined_keys)
            return trial, result, attempts, deferred, failure
        failures.append(combined_failure)

    if best is None:
        final_failure = next(
            (message for message in reversed(failures) if message),
            "Every isolated candidate batch was rejected",
        )
        raise QuadOptimizeError(
            f"{final_failure}; isolated {attempts} safe-batch attempts"
        )

    deferred = len(selected_keys) - len(best[2])
    trial, result, _keys = best
    return trial, result, attempts, deferred, failure


def _validate_context(context: Any, options: OptimizeOptions):
    edit_mode = context.mode == "EDIT_MESH"
    obj = context.edit_object if edit_mode else context.active_object
    if obj is None or obj.type != "MESH" or obj.mode not in {"OBJECT", "EDIT"}:
        raise QuadOptimizeError(
            "Quad Flow Optimization requires one active mesh in Object or Edit Mode"
        )
    if edit_mode and len(context.objects_in_mode_unique_data) != 1:
        raise QuadOptimizeError("Optimize one mesh data-block at a time")
    if edit_mode and obj.data.users > 1:
        raise QuadOptimizeError(
            "Linked mesh data must be optimized in Object Mode so only the active "
            "object is made single-user"
        )
    if options.selected_faces_only and not edit_mode:
        raise QuadOptimizeError("Selected Faces scope requires Edit Mode")
    if obj.data.shape_keys is not None:
        raise QuadOptimizeError(
            "Meshes with shape keys are not optimized because topology would change"
        )
    mesh = obj.data
    live_bm = bmesh.from_edit_mesh(mesh) if edit_mode else None
    working = live_bm.copy() if live_bm is not None else bmesh.new()
    if live_bm is None:
        working.from_mesh(mesh)
    return obj, mesh, live_bm, working, edit_mode


def optimize_quad_flows(
    context: Any,
    portfolio: tuple[OptimizeOptions, ...],
) -> dict[str, Any]:
    if not portfolio:
        raise QuadOptimizeError("No optimization sensitivity modes are configured")
    base_options = portfolio[0]
    obj, mesh, live_bm, working, edit_mode = _validate_context(
        context,
        base_options,
    )
    stats: dict[str, Any] = {
        "passes": 0,
        "candidate_loops": 0,
        "dissolved_loops": 0,
        "removed_faces": 0,
        "removed_vertices": 0,
        "max_error": 0.0,
        "rms_error": 0.0,
        "batch_attempts": 0,
        "deferred_loops": 0,
        "made_single_user": False,
        "effective_mode": "None",
        "diagnostic": "Not analyzed",
        "status": "No removable complete quad flows",
    }
    try:
        if base_options.selected_faces_only:
            scope_layer = working.faces.layers.int.get(SCOPE_LAYER_NAME)
            if scope_layer is None:
                scope_layer = working.faces.layers.int.new(SCOPE_LAYER_NAME)
            for face in working.faces:
                face[scope_layer] = int(face.select)
        for _cycle in range(base_options.cycles):
            detection = _detect_with_portfolio(working, portfolio)
            options = detection.options
            candidates = detection.candidates
            stats["candidate_loops"] += len(candidates)
            selected_ids = set(detection.selected_ids)
            if not selected_ids:
                attempted = " -> ".join(detection.attempted_labels)
                if not stats["passes"]:
                    stats["effective_mode"] = detection.options.label
                    stats["diagnostic"] = _diagnostic_summary(
                        detection.diagnostics
                    )
                    stats["status"] = f"No safe flows after {attempted} detection"
                else:
                    stats["diagnostic"] += "; no additional safe flows remain"
                break

            try:
                trial, result, batch_attempts, deferred, failure = (
                    _apply_partitioned_candidate_batch(
                        working,
                        candidates,
                        selected_ids,
                        options,
                    )
                )
            except QuadOptimizeError as exc:
                stats["status"] = "No individually safe candidate subset"
                stats["diagnostic"] = str(exc)
                break
            working.free()
            working = trial
            stats["passes"] += 1
            stats["batch_attempts"] += batch_attempts
            stats["deferred_loops"] += deferred
            stats["dissolved_loops"] += result["loops"]
            stats["removed_faces"] += result["removed_faces"]
            stats["removed_vertices"] += result["removed_vertices"]
            stats["max_error"] = max(stats["max_error"], result["max_error"])
            stats["rms_error"] = max(stats["rms_error"], result["rms_error"])
            stats["effective_mode"] = options.label
            partition_note = (
                f"; isolated the safe subset in {batch_attempts} trials and "
                f"deferred {deferred} conflicting flows"
                if batch_attempts > 1
                else ""
            )
            stats["diagnostic"] = (
                f"{len(candidates)} eligible complete flows; "
                f"{result['loops']} safely dissolved this cycle{partition_note}"
            )
            stats["status"] = (
                f"Optimization completed using {options.label} detection"
                + (" with batch isolation" if deferred else "")
            )
            if failure and deferred:
                stats["diagnostic"] += f"; protected because {failure}"

        if stats["passes"] == 0:
            return stats

        scope_layer = working.faces.layers.int.get(SCOPE_LAYER_NAME)
        if scope_layer is not None:
            working.faces.layers.int.remove(scope_layer)

        if edit_mode:
            restore_bmesh(mesh, live_bm, working)
        elif mesh.users > 1:
            output_mesh = mesh.copy()
            try:
                working.to_mesh(output_mesh)
                output_mesh.update()
                obj.data = output_mesh
            except Exception:
                bpy.data.meshes.remove(output_mesh)
                raise
            stats["made_single_user"] = True
        else:
            working.to_mesh(mesh)
            mesh.update()
        return stats
    finally:
        working.free()


def _update_settings(settings: Any, obj: Any, stats: dict[str, Any]) -> None:
    settings.optimize_object_name = obj.name
    settings.optimize_candidate_loops = stats["candidate_loops"]
    settings.optimize_dissolved_loops = stats["dissolved_loops"]
    settings.optimize_completed_cycles = stats["passes"]
    settings.optimize_removed_faces = stats["removed_faces"]
    settings.optimize_max_measured_error = stats["max_error"]
    settings.optimize_effective_mode = stats.get("effective_mode", "None")
    settings.optimize_diagnostic = stats.get("diagnostic", "Not analyzed")
    settings.optimize_status = stats["status"]


class QT_OT_analyze_quad_optimization(Operator):
    bl_idname = "mesh.quad_transition_analyze_optimization"
    bl_label = "Analyze Removable Quad Flows"
    bl_description = "Select the alternating low-curvature edge flows eligible next"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: Any) -> bool:
        return (
            context.edit_object is not None
            and context.edit_object.type == "MESH"
            and context.edit_object.mode == "EDIT"
        )

    def execute(self, context: Any):
        settings = context.scene.topology_transitions
        portfolio = _option_portfolio_from_settings(settings)
        try:
            obj = context.edit_object
            bm = bmesh.from_edit_mesh(obj.data)
            detection = _detect_with_portfolio(bm, portfolio)
            options = detection.options
            candidates = detection.candidates
            selected_ids = set(detection.selected_ids)
        except QuadOptimizeError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        selected_edges = {
            edge
            for candidate in candidates
            if candidate.identifier in selected_ids
            for edge in candidate.edges
        }
        if selected_edges and not options.selected_faces_only:
            for face in bm.faces:
                face.select_set(False)
            for edge in bm.edges:
                edge.select_set(False)
            for vertex in bm.verts:
                vertex.select_set(False)
            for edge in selected_edges:
                edge.select_set(True)
                for vertex in edge.verts:
                    vertex.select_set(True)
            bm.select_mode = {"EDGE"}
            context.tool_settings.mesh_select_mode = (False, True, False)
            bmesh.update_edit_mesh(
                obj.data,
                loop_triangles=False,
                destructive=False,
            )
        stats = {
            "candidate_loops": len(candidates),
            "dissolved_loops": len(selected_ids),
            "passes": 0,
            "removed_faces": 0,
            "max_error": 0.0,
            "effective_mode": options.label,
            "diagnostic": _diagnostic_summary(detection.diagnostics),
            "status": (
                (
                    f"{len(selected_ids)} removable flows found in selected faces"
                    if options.selected_faces_only
                    else f"{len(selected_ids)} flows selected for the next pass"
                )
                if selected_ids
                else "No safe complete flows at the selected sensitivity"
            ),
        }
        _update_settings(settings, obj, stats)
        if not selected_ids:
            self.report(
                {"WARNING"},
                f"{stats['status']}. {stats['diagnostic']}",
            )
            return {"FINISHED"}
        verb = "Found" if options.selected_faces_only else "Selected"
        self.report(
            {"INFO"},
            f"{verb} {len(selected_ids)} of {len(candidates)} compatible quad flows",
        )
        return {"FINISHED"}


class QT_OT_optimize_quad_flows(Operator):
    bl_idname = "mesh.quad_transition_optimize_flows"
    bl_label = "Optimize Repetitive Quad Flow"
    bl_description = (
        "Dissolve alternating low-curvature edge flows while preserving all-quads, "
        "topology class, features, and the configured surface-error budget"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: Any) -> bool:
        obj = (
            context.edit_object
            if context.mode == "EDIT_MESH"
            else context.active_object
        )
        return (
            obj is not None
            and obj.type == "MESH"
            and obj.mode in {"OBJECT", "EDIT"}
        )

    def execute(self, context: Any):
        settings = context.scene.topology_transitions
        portfolio = _option_portfolio_from_settings(settings)
        obj = (
            context.edit_object
            if context.mode == "EDIT_MESH"
            else context.active_object
        )
        try:
            stats = optimize_quad_flows(context, portfolio)
        except QuadOptimizeError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:
            self.report(
                {"ERROR"},
                f"Optimization failed without changing the mesh: {exc}",
            )
            return {"CANCELLED"}

        _update_settings(settings, obj, stats)
        if not stats["passes"]:
            self.report(
                {"WARNING"},
                f"{stats['status']}. {stats['diagnostic']}",
            )
            return {"FINISHED"}
        suffix = (
            f"; deferred {stats['deferred_loops']} conflicting flows"
            if stats["deferred_loops"]
            else ""
        )
        if stats["made_single_user"]:
            suffix += "; active mesh made single-user"
        self.report(
            {"INFO"},
            f"Removed {stats['removed_faces']} faces and "
            f"{stats['removed_vertices']} vertices across {stats['passes']} cycles; "
            f"max error {stats['max_error']:.6g}; "
            f"{stats['effective_mode']} detection{suffix}",
        )
        return {"FINISHED"}


CLASSES = (QT_OT_analyze_quad_optimization, QT_OT_optimize_quad_flows)


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
