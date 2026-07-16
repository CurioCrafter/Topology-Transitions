"""Pure topology and curve sampling for connected multi-strip drawing."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import sqrt

from .core import (
    TransitionError,
    build_transition_template,
    preset_counts,
    template_edges,
)

Point3 = tuple[float, float, float]


@dataclass(frozen=True)
class RibbonVertex:
    key: str
    u: float
    s: float


@dataclass(frozen=True)
class RibbonPlan:
    vertices: Mapping[str, RibbonVertex]
    faces: tuple[tuple[str, str, str, str], ...]
    anchor_keys: tuple[str, ...]
    output_keys: tuple[str, ...]
    pole_keys: frozenset[str]
    input_count: int
    output_count: int
    layout: str


def ordered_open_edge_chain(
    edge_vertices: Mapping[int, tuple[int, int]],
    selected_edge_ids: Sequence[int],
) -> tuple[int, ...]:
    """Return selected edges as one deterministic, non-branching open path."""

    selected = tuple(dict.fromkeys(selected_edge_ids))
    if not selected:
        raise TransitionError("Select at least one boundary edge to grow from")
    unknown = set(selected) - set(edge_vertices)
    if unknown:
        raise TransitionError(f"Unknown selected edges: {sorted(unknown)}")
    adjacency: dict[int, list[int]] = {}
    for edge_id in selected:
        first, second = edge_vertices[edge_id]
        if first == second:
            raise TransitionError("The selected chain contains a zero-length edge")
        adjacency.setdefault(first, []).append(second)
        adjacency.setdefault(second, []).append(first)
    if any(len(neighbors) > 2 for neighbors in adjacency.values()):
        raise TransitionError("The selected boundary chain branches")
    pending = [next(iter(adjacency))]
    connected = set()
    while pending:
        vertex_id = pending.pop()
        if vertex_id in connected:
            continue
        connected.add(vertex_id)
        pending.extend(adjacency[vertex_id])
    if connected != set(adjacency):
        raise TransitionError("The selected boundary edges are disconnected")
    endpoints = sorted(
        vertex_id for vertex_id, neighbors in adjacency.items() if len(neighbors) == 1
    )
    if len(endpoints) != 2:
        raise TransitionError("Select one open boundary chain, not a closed loop")

    ordered = [endpoints[0]]
    previous = None
    current = endpoints[0]
    while True:
        following = [
            neighbor for neighbor in adjacency[current] if neighbor != previous
        ]
        if not following:
            break
        candidate = following[0]
        if candidate in ordered:
            raise TransitionError("The selected boundary chain loops back on itself")
        ordered.append(candidate)
        previous, current = current, candidate
    if len(ordered) != len(adjacency):
        raise TransitionError("The selected boundary chain loops back on itself")
    return tuple(ordered)


def _validate_plan(plan: RibbonPlan) -> None:
    if len(plan.anchor_keys) != plan.input_count + 1:
        raise TransitionError("Ribbon input boundary count is inconsistent")
    if len(plan.output_keys) != plan.output_count + 1:
        raise TransitionError("Ribbon output boundary count is inconsistent")
    keys = set(plan.vertices)
    if not plan.faces:
        raise TransitionError("Ribbon plan contains no quads")
    for face in plan.faces:
        if len(face) != 4 or len(set(face)) != 4:
            raise TransitionError(f"Ribbon contains an invalid quad: {face}")
        if set(face) - keys:
            raise TransitionError("Ribbon face references an unknown vertex")
    if any(count not in {1, 2} for count in template_edges(plan.faces).values()):
        raise TransitionError("Ribbon topology contains a non-manifold edge")
    if any(not 0.0 <= vertex.u <= 1.0 for vertex in plan.vertices.values()):
        raise TransitionError("Ribbon lateral coordinates leave the unit interval")
    if any(not 0.0 <= vertex.s <= 1.0 for vertex in plan.vertices.values()):
        raise TransitionError("Ribbon longitudinal coordinates leave the stroke")


def build_uniform_ribbon(lanes: int, segments: int) -> RibbonPlan:
    """Build a connected grid where every anchor edge grows as one lane."""

    if lanes < 1:
        raise TransitionError("A ribbon needs at least one selected boundary edge")
    if segments < 1:
        raise TransitionError("A ribbon needs at least one longitudinal segment")
    vertices = {}
    rows = []
    for row in range(segments + 1):
        keys = []
        for column in range(lanes + 1):
            key = f"row:{row}:{column}"
            vertices[key] = RibbonVertex(
                key=key,
                u=column / lanes,
                s=row / segments,
            )
            keys.append(key)
        rows.append(tuple(keys))
    faces = tuple(
        (
            rows[row][column],
            rows[row][column + 1],
            rows[row + 1][column + 1],
            rows[row + 1][column],
        )
        for row in range(segments)
        for column in range(lanes)
    )
    plan = RibbonPlan(
        vertices=vertices,
        faces=faces,
        anchor_keys=rows[0],
        output_keys=rows[-1],
        pole_keys=frozenset(),
        input_count=lanes,
        output_count=lanes,
        layout="UNIFORM",
    )
    _validate_plan(plan)
    return plan


def build_transition_ribbon(
    transition: str,
    segments: int,
    *,
    pole_side: str = "CENTER",
    mirror: bool = False,
    pole_spacing: float = 1.0,
) -> RibbonPlan:
    """Map an existing transition template from an anchor row along a stroke."""

    input_count, output_count = preset_counts(transition)
    if segments < 2:
        raise TransitionError("A drawn transition needs at least two segments")
    difference = abs(input_count - output_count)
    core_mirror = mirror
    if difference == 2:
        left_segments = right_segments = segments
    else:
        extra_side = pole_side if pole_side in {"LEFT", "RIGHT"} else "RIGHT"
        if mirror:
            extra_side = "LEFT" if extra_side == "RIGHT" else "RIGHT"
        left_segments = segments + (extra_side == "LEFT")
        right_segments = segments + (extra_side == "RIGHT")
        core_mirror = False
    template = build_transition_template(
        input_count,
        output_count,
        left_segments,
        right_segments,
        pole_side=pole_side,
        mirror=core_mirror,
        pole_spacing=pole_spacing,
    )
    vertices = {
        key: RibbonVertex(key=key, u=spec.u, s=1.0 - spec.v)
        for key, spec in template.vertices.items()
    }
    plan = RibbonPlan(
        vertices=vertices,
        faces=tuple(template.faces),
        anchor_keys=tuple(template.top_keys),
        output_keys=tuple(template.bottom_keys),
        pole_keys=frozenset(template.pole_keys),
        input_count=input_count,
        output_count=output_count,
        layout="TRANSITION",
    )
    _validate_plan(plan)
    return plan


def build_ribbon_plan(
    layout: str,
    lanes: int,
    segments: int,
    *,
    transition: str = "FIVE_TO_THREE",
    pole_side: str = "CENTER",
    mirror: bool = False,
    pole_spacing: float = 1.0,
) -> RibbonPlan:
    if layout == "UNIFORM":
        return build_uniform_ribbon(lanes, segments)
    if layout == "TRANSITION":
        plan = build_transition_ribbon(
            transition,
            segments,
            pole_side=pole_side,
            mirror=mirror,
            pole_spacing=pole_spacing,
        )
        if lanes != plan.input_count:
            raise TransitionError(
                f"{transition.replace('_', ' ').title()} needs "
                f"{plan.input_count} selected boundary edges, found {lanes}"
            )
        return plan
    raise TransitionError(f"Unknown ribbon layout: {layout}")


def _distance(first: Point3, second: Point3) -> float:
    return sqrt(sum((a - b) ** 2 for a, b in zip(first, second, strict=True)))


def _lerp(first: Point3, second: Point3, factor: float) -> Point3:
    return tuple(
        a + (b - a) * factor for a, b in zip(first, second, strict=True)
    )  # type: ignore[return-value]


def sample_polyline(points: Sequence[Point3], factor: float) -> Point3:
    """Sample a polyline by normalized arc length."""

    if not points:
        raise TransitionError("A stroke needs at least one surface point")
    if len(points) == 1:
        return points[0]
    lengths = [_distance(first, second) for first, second in zip(points, points[1:])]
    total = sum(lengths)
    if total <= 1.0e-12:
        raise TransitionError("The drawn stroke has no measurable length")
    target = max(0.0, min(1.0, factor)) * total
    travelled = 0.0
    for index, length in enumerate(lengths):
        if travelled + length >= target or index == len(lengths) - 1:
            local = 0.0 if length <= 1.0e-12 else (target - travelled) / length
            return _lerp(points[index], points[index + 1], local)
        travelled += length
    return points[-1]


def resample_polyline(points: Sequence[Point3], count: int) -> tuple[Point3, ...]:
    if count < 2:
        raise TransitionError("Polyline resampling needs at least two points")
    return tuple(sample_polyline(points, index / (count - 1)) for index in range(count))
