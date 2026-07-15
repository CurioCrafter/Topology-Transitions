"""Blender-independent edge-flow discovery and metrics.

An edge flow is represented as a maximal path of edges.  At regular valence-4
quad vertices, continuation follows the topologically opposite edge.  The
optional geometric mode instead pairs the straightest incident edges and can
therefore visualize likely continuation through extraordinary vertices.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations
from math import sqrt

Vector3 = tuple[float, float, float]


@dataclass(frozen=True)
class FlowEndpoint:
    vertex_id: int
    valence: int
    boundary: bool
    label: str


@dataclass(frozen=True)
class EdgeFlow:
    edge_ids: tuple[int, ...]
    vertex_ids: tuple[int, ...]
    closed: bool
    length: float
    alignment: float
    start: FlowEndpoint | None
    end: FlowEndpoint | None

    @property
    def edge_count(self) -> int:
        return len(self.edge_ids)


@dataclass(frozen=True)
class MeshFlowTopology:
    edge_vertices: Mapping[int, tuple[int, int]]
    vertex_edges: Mapping[int, Sequence[int]]
    edge_faces: Mapping[int, frozenset[int]]
    face_edges: Mapping[int, Sequence[int]]
    positions: Mapping[int, Vector3]


def _subtract(first: Vector3, second: Vector3) -> Vector3:
    return (
        first[0] - second[0],
        first[1] - second[1],
        first[2] - second[2],
    )


def _dot(first: Vector3, second: Vector3) -> float:
    return sum(a * b for a, b in zip(first, second))


def _length(vector: Vector3) -> float:
    return sqrt(_dot(vector, vector))


def _normalized(vector: Vector3) -> Vector3:
    magnitude = _length(vector)
    if magnitude <= 1.0e-12:
        return (0.0, 0.0, 0.0)
    return tuple(component / magnitude for component in vector)  # type: ignore[return-value]


def _other_vertex(topology: MeshFlowTopology, edge_id: int, vertex_id: int) -> int:
    first, second = topology.edge_vertices[edge_id]
    if first == vertex_id:
        return second
    if second == vertex_id:
        return first
    raise ValueError(f"Edge {edge_id} is not incident to vertex {vertex_id}")


def _straightness(
    topology: MeshFlowTopology, vertex_id: int, first_edge: int, second_edge: int
) -> float:
    center = topology.positions[vertex_id]
    first = _normalized(
        _subtract(
            topology.positions[_other_vertex(topology, first_edge, vertex_id)], center
        )
    )
    second = _normalized(
        _subtract(
            topology.positions[_other_vertex(topology, second_edge, vertex_id)], center
        )
    )
    return max(-1.0, min(1.0, -_dot(first, second)))


def _geometry_pairs(
    topology: MeshFlowTopology,
    vertex_id: int,
    incident: Sequence[int],
    minimum_alignment: float,
) -> list[tuple[int, int]]:
    candidates = sorted(
        (
            (_straightness(topology, vertex_id, first, second), first, second)
            for first, second in combinations(incident, 2)
        ),
        reverse=True,
    )
    used: set[int] = set()
    pairs: list[tuple[int, int]] = []
    for score, first, second in candidates:
        if score < minimum_alignment:
            break
        if first in used or second in used:
            continue
        used.update((first, second))
        pairs.append((first, second))
    return pairs


def _topology_pairs(
    topology: MeshFlowTopology,
    vertex_id: int,
    incident: Sequence[int],
    minimum_alignment: float,
) -> list[tuple[int, int]]:
    if len(incident) == 2:
        if _straightness(topology, vertex_id, incident[0], incident[1]) >= (
            minimum_alignment
        ):
            return [(incident[0], incident[1])]
        return []
    if len(incident) != 4:
        return []
    linked_faces = {
        face_id for edge_id in incident for face_id in topology.edge_faces[edge_id]
    }
    if not linked_faces or any(
        len(topology.face_edges[face_id]) != 4 for face_id in linked_faces
    ):
        return []

    pairs: set[tuple[int, int]] = set()
    for edge_id in incident:
        candidates = [
            other
            for other in incident
            if other != edge_id
            and topology.edge_faces[edge_id].isdisjoint(topology.edge_faces[other])
        ]
        if len(candidates) != 1:
            return []
        pairs.add(tuple(sorted((edge_id, candidates[0]))))
    return sorted(pairs) if len(pairs) == 2 else []


def build_continuations(
    topology: MeshFlowTopology,
    *,
    mode: str = "TOPOLOGY",
    minimum_alignment: float = 0.15,
) -> dict[tuple[int, int], int]:
    """Map ``(vertex, incoming edge)`` to its paired outgoing edge."""

    if mode not in {"TOPOLOGY", "GEOMETRIC"}:
        raise ValueError(f"Unknown edge-flow mode: {mode}")
    continuations: dict[tuple[int, int], int] = {}
    for vertex_id, incident_value in topology.vertex_edges.items():
        incident = list(incident_value)
        pairs = (
            _topology_pairs(topology, vertex_id, incident, minimum_alignment)
            if mode == "TOPOLOGY"
            else _geometry_pairs(topology, vertex_id, incident, minimum_alignment)
        )
        for first, second in pairs:
            continuations[(vertex_id, first)] = second
            continuations[(vertex_id, second)] = first
    return continuations


def _edge_adjacency(
    topology: MeshFlowTopology,
    continuations: Mapping[tuple[int, int], int],
    eligible_edges: set[int],
) -> dict[int, set[int]]:
    adjacency = {edge_id: set() for edge_id in eligible_edges}
    for (vertex_id, edge_id), other in continuations.items():
        if edge_id not in eligible_edges or other not in eligible_edges:
            continue
        adjacency[edge_id].add(other)
        adjacency[other].add(edge_id)
        if len(adjacency[edge_id]) > 2 or len(adjacency[other]) > 2:
            raise ValueError(f"Continuation graph branches at vertex {vertex_id}")
    return adjacency


def _components(adjacency: Mapping[int, set[int]]) -> list[set[int]]:
    remaining = set(adjacency)
    result: list[set[int]] = []
    while remaining:
        start = min(remaining)
        pending = [start]
        component: set[int] = set()
        while pending:
            edge_id = pending.pop()
            if edge_id in component:
                continue
            component.add(edge_id)
            pending.extend(adjacency[edge_id] - component)
        remaining -= component
        result.append(component)
    return result


def _ordered_component(
    topology: MeshFlowTopology,
    component: set[int],
    continuations: Mapping[tuple[int, int], int],
    adjacency: Mapping[int, set[int]],
) -> tuple[list[int], list[int], bool]:
    endpoint_edges = sorted(
        edge_id for edge_id in component if len(adjacency[edge_id]) < 2
    )
    first_edge = endpoint_edges[0] if endpoint_edges else min(component)
    first_vertices = topology.edge_vertices[first_edge]
    start_candidates = [
        vertex_id
        for vertex_id in first_vertices
        if continuations.get((vertex_id, first_edge)) not in component
    ]
    current_vertex = min(start_candidates) if start_candidates else min(first_vertices)
    current_edge = first_edge
    edge_order: list[int] = []
    vertex_order = [current_vertex]

    while current_edge not in edge_order:
        edge_order.append(current_edge)
        next_vertex = _other_vertex(topology, current_edge, current_vertex)
        vertex_order.append(next_vertex)
        next_edge = continuations.get((next_vertex, current_edge))
        if next_edge not in component:
            return edge_order, vertex_order, False
        if next_edge == first_edge:
            return edge_order, vertex_order, len(edge_order) == len(component)
        current_vertex = next_vertex
        current_edge = next_edge

    return edge_order, vertex_order, False


def _endpoint(topology: MeshFlowTopology, vertex_id: int) -> FlowEndpoint:
    incident = topology.vertex_edges[vertex_id]
    valence = len(incident)
    boundary = any(len(topology.edge_faces[edge_id]) < 2 for edge_id in incident)
    if boundary:
        label = f"Boundary (v{valence})"
    elif valence == 3:
        label = "N-pole (v3)"
    elif valence == 5:
        label = "E-pole (v5)"
    elif valence == 4:
        label = "Regular (v4)"
    else:
        label = f"Pole (v{valence})"
    return FlowEndpoint(vertex_id, valence, boundary, label)


def _flow_length(topology: MeshFlowTopology, edge_ids: Iterable[int]) -> float:
    total = 0.0
    for edge_id in edge_ids:
        first, second = topology.edge_vertices[edge_id]
        total += _length(
            _subtract(topology.positions[second], topology.positions[first])
        )
    return total


def _flow_alignment(
    topology: MeshFlowTopology, vertex_ids: Sequence[int], closed: bool
) -> float:
    if len(vertex_ids) < 3:
        return 1.0
    points = [topology.positions[vertex_id] for vertex_id in vertex_ids]
    if closed and points[0] == points[-1]:
        points = points[:-1]
    scores: list[float] = []
    if closed:
        indices = range(len(points))
    else:
        indices = range(1, len(points) - 1)
    for index in indices:
        previous = points[index - 1]
        current = points[index]
        following = points[(index + 1) % len(points)]
        incoming = _normalized(_subtract(current, previous))
        outgoing = _normalized(_subtract(following, current))
        scores.append(max(0.0, min(1.0, _dot(incoming, outgoing))))
    return sum(scores) / len(scores) if scores else 1.0


def discover_edge_flows(
    topology: MeshFlowTopology,
    *,
    eligible_edges: Iterable[int] | None = None,
    mode: str = "TOPOLOGY",
    minimum_edges: int = 1,
    minimum_alignment: float = 0.15,
    sort: str = "SIDE_TO_SIDE",
) -> list[EdgeFlow]:
    """Return every maximal flow that passes the requested minimum size."""

    if minimum_edges < 1:
        raise ValueError("minimum_edges must be at least one")
    eligible = (
        set(topology.edge_vertices) if eligible_edges is None else set(eligible_edges)
    )
    unknown = eligible - set(topology.edge_vertices)
    if unknown:
        raise ValueError(f"Unknown eligible edges: {sorted(unknown)}")
    continuations = build_continuations(
        topology, mode=mode, minimum_alignment=minimum_alignment
    )
    adjacency = _edge_adjacency(topology, continuations, eligible)
    flows: list[EdgeFlow] = []
    for component in _components(adjacency):
        if len(component) < minimum_edges:
            continue
        edge_ids, vertex_ids, closed = _ordered_component(
            topology, component, continuations, adjacency
        )
        start = None if closed else _endpoint(topology, vertex_ids[0])
        end = None if closed else _endpoint(topology, vertex_ids[-1])
        flows.append(
            EdgeFlow(
                edge_ids=tuple(edge_ids),
                vertex_ids=tuple(vertex_ids),
                closed=closed,
                length=_flow_length(topology, edge_ids),
                alignment=_flow_alignment(topology, vertex_ids, closed),
                start=start,
                end=end,
            )
        )

    if sort == "SIDE_TO_SIDE":
        flows = order_flows_side_to_side(flows, topology.face_edges)
    elif sort == "LONGEST":
        flows.sort(
            key=lambda flow: (
                -flow.edge_count,
                -flow.length,
                min(flow.edge_ids),
            )
        )
    elif sort == "SMOOTHEST":
        flows.sort(
            key=lambda flow: (
                -flow.alignment,
                -flow.edge_count,
                min(flow.edge_ids),
            )
        )
    elif sort == "INDEX":
        flows.sort(key=lambda flow: min(flow.edge_ids))
    else:
        raise ValueError(f"Unknown edge-flow sort: {sort}")
    return flows


def quad_strip_faces(
    flows: Sequence[EdgeFlow], face_edges: Mapping[int, Sequence[int]]
) -> dict[int, set[int]]:
    """Return the quad faces directly adjoining each edge flow."""

    edge_to_flow = {
        edge_id: flow_index
        for flow_index, flow in enumerate(flows)
        for edge_id in flow.edge_ids
    }
    strips = {index: set() for index in range(len(flows))}
    for face_id, edge_ids in face_edges.items():
        if len(edge_ids) != 4:
            continue
        for flow_index in {
            edge_to_flow[edge_id] for edge_id in edge_ids if edge_id in edge_to_flow
        }:
            strips[flow_index].add(face_id)
    return strips


def parallel_neighboring_flows(
    flows: Sequence[EdgeFlow], face_edges: Mapping[int, Sequence[int]]
) -> dict[int, set[int]]:
    """Find flows separated by one quad, using opposite face edges."""

    edge_to_flow = {
        edge_id: flow_index
        for flow_index, flow in enumerate(flows)
        for edge_id in flow.edge_ids
    }
    neighbors = {index: set() for index in range(len(flows))}
    for edge_ids in face_edges.values():
        if len(edge_ids) != 4:
            continue
        for first_edge, second_edge in (
            (edge_ids[0], edge_ids[2]),
            (edge_ids[1], edge_ids[3]),
        ):
            first = edge_to_flow.get(first_edge)
            second = edge_to_flow.get(second_edge)
            if first is None or second is None or first == second:
                continue
            neighbors[first].add(second)
            neighbors[second].add(first)
    return neighbors


def _graph_component(
    start: int, neighbors: Mapping[int, set[int]], remaining: set[int]
) -> set[int]:
    pending = [start]
    component: set[int] = set()
    while pending:
        current = pending.pop()
        if current in component:
            continue
        component.add(current)
        pending.extend(neighbors[current] - component)
    remaining -= component
    return component


def _nearest_unvisited(
    start: int,
    unvisited: set[int],
    component: set[int],
    neighbors: Mapping[int, set[int]],
    flow_key,
) -> int:
    pending = deque([(start, 0)])
    seen = {start}
    candidates: list[tuple[int, tuple[int, int], int]] = []
    while pending:
        current, distance = pending.popleft()
        if current in unvisited:
            candidates.append((distance, flow_key(current), current))
            continue
        for following in sorted(neighbors[current] & component, key=flow_key):
            if following not in seen:
                seen.add(following)
                pending.append((following, distance + 1))
    if not candidates:
        return min(unvisited, key=flow_key)
    return min(candidates)[2]


def order_flows_side_to_side(
    flows: Sequence[EdgeFlow], face_edges: Mapping[int, Sequence[int]]
) -> list[EdgeFlow]:
    """Group parallel flow families and walk each from one side to the other."""

    if not flows:
        return []
    neighbors = parallel_neighboring_flows(flows, face_edges)

    def flow_key(index: int) -> tuple[int, int]:
        return (min(flows[index].edge_ids), index)

    remaining = set(range(len(flows)))
    components: list[set[int]] = []
    while remaining:
        components.append(
            _graph_component(min(remaining, key=flow_key), neighbors, remaining)
        )
    components.sort(
        key=lambda component: (
            -len(component),
            min(flow_key(index) for index in component),
        )
    )

    ordered_indices: list[int] = []
    for component in components:
        endpoints = [
            index for index in component if len(neighbors[index] & component) <= 1
        ]
        current = min(endpoints or component, key=flow_key)
        unvisited = set(component)
        while unvisited:
            ordered_indices.append(current)
            unvisited.remove(current)
            adjacent = neighbors[current] & unvisited
            if adjacent:
                current = min(adjacent, key=flow_key)
            elif unvisited:
                current = _nearest_unvisited(
                    current,
                    unvisited,
                    component,
                    neighbors,
                    flow_key,
                )
    return [flows[index] for index in ordered_indices]


def neighboring_flows(
    flows: Sequence[EdgeFlow], face_edges: Mapping[int, Sequence[int]]
) -> dict[int, set[int]]:
    """Find flows that run through at least one common face."""

    edge_to_flow = {
        edge_id: flow_index
        for flow_index, flow in enumerate(flows)
        for edge_id in flow.edge_ids
    }
    neighbors = {index: set() for index in range(len(flows))}
    for edge_ids in face_edges.values():
        present = {
            edge_to_flow[edge_id] for edge_id in edge_ids if edge_id in edge_to_flow
        }
        for first, second in combinations(sorted(present), 2):
            neighbors[first].add(second)
            neighbors[second].add(first)
    return neighbors
