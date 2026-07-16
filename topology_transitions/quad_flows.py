"""Discover broad quad-flow regions and optional one-quad-wide face bands.

A quad flow is a maximal sequence of faces reached by entering a quad through
one edge and leaving through the opposite edge.  Each quad participates in two
perpendicular flow directions.  Edges define the route, but faces are the
primary discovered, highlighted, and selected elements.

The default region view traces separatrices from interior extraordinary
vertices (the poles that redirect retopology) and flood-fills the quad patches
between them. This produces broad coloured flow zones rather than mistaking
every edge loop for a complete topology region.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from math import sqrt

Vector3 = tuple[float, float, float]
FlowNode = tuple[int, int]


@dataclass(frozen=True)
class MeshQuadTopology:
    edge_vertices: Mapping[int, tuple[int, int]]
    edge_faces: Mapping[int, frozenset[int]]
    face_edges: Mapping[int, Sequence[int]]
    positions: Mapping[int, Vector3]


@dataclass(frozen=True)
class QuadFlow:
    face_ids: tuple[int, ...]
    face_axes: tuple[int, ...]
    crossed_edge_ids: tuple[int, ...]
    endpoint_edge_ids: tuple[int, ...]
    closed: bool
    length: float
    alignment: float
    start_label: str
    end_label: str

    @property
    def quad_count(self) -> int:
        return len(self.face_ids)

    @property
    def nodes(self) -> tuple[FlowNode, ...]:
        return tuple(zip(self.face_ids, self.face_axes, strict=True))


def _subtract(first: Vector3, second: Vector3) -> Vector3:
    return tuple(a - b for a, b in zip(first, second, strict=True))  # type: ignore[return-value]


def _length(vector: Vector3) -> float:
    return sqrt(sum(component * component for component in vector))


def _normalized(vector: Vector3) -> Vector3:
    magnitude = _length(vector)
    if magnitude <= 1.0e-12:
        return (0.0, 0.0, 0.0)
    return tuple(component / magnitude for component in vector)  # type: ignore[return-value]


def _dot(first: Vector3, second: Vector3) -> float:
    return sum(a * b for a, b in zip(first, second, strict=True))


def _cross_edges(topology: MeshQuadTopology, node: FlowNode) -> tuple[int, int]:
    face_id, axis = node
    edges = topology.face_edges[face_id]
    return edges[axis], edges[axis + 2]


def _side_edges(topology: MeshQuadTopology, node: FlowNode) -> tuple[int, int]:
    face_id, axis = node
    edges = topology.face_edges[face_id]
    return edges[1 - axis], edges[3 - axis]


def _neighbor_across(
    topology: MeshQuadTopology,
    node: FlowNode,
    edge_id: int,
    eligible_faces: set[int],
) -> FlowNode | None:
    face_id, _axis = node
    linked = topology.edge_faces[edge_id]
    if len(linked) > 2:
        return None
    candidates = [
        other
        for other in linked
        if other != face_id
        and other in eligible_faces
        and len(topology.face_edges[other]) == 4
    ]
    if len(candidates) != 1:
        return None
    other = candidates[0]
    try:
        other_edge_index = topology.face_edges[other].index(edge_id)
    except ValueError as exc:
        raise ValueError(
            f"Face {other} does not contain its linked edge {edge_id}"
        ) from exc
    return other, other_edge_index % 2


def _build_graph(
    topology: MeshQuadTopology, eligible_faces: set[int]
) -> tuple[dict[FlowNode, set[FlowNode]], dict[tuple[FlowNode, int], FlowNode]]:
    nodes = {
        (face_id, axis)
        for face_id in eligible_faces
        if len(topology.face_edges[face_id]) == 4
        for axis in (0, 1)
    }
    adjacency = {node: set() for node in nodes}
    transitions: dict[tuple[FlowNode, int], FlowNode] = {}
    for node in nodes:
        for edge_id in _cross_edges(topology, node):
            neighbor = _neighbor_across(topology, node, edge_id, eligible_faces)
            if neighbor is None or neighbor not in nodes:
                continue
            transitions[(node, edge_id)] = neighbor
            adjacency[node].add(neighbor)
    if any(len(neighbors) > 2 for neighbors in adjacency.values()):
        raise ValueError("Quad-flow graph branches unexpectedly")
    return adjacency, transitions


def _components(adjacency: Mapping[FlowNode, set[FlowNode]]) -> list[set[FlowNode]]:
    remaining = set(adjacency)
    result = []
    while remaining:
        pending = [min(remaining)]
        component: set[FlowNode] = set()
        while pending:
            node = pending.pop()
            if node in component:
                continue
            component.add(node)
            pending.extend(adjacency[node] - component)
        remaining -= component
        result.append(component)
    return result


def _ordered_component(
    component: set[FlowNode], adjacency: Mapping[FlowNode, set[FlowNode]]
) -> tuple[list[FlowNode], bool]:
    endpoints = sorted(node for node in component if len(adjacency[node]) < 2)
    closed = not endpoints and len(component) > 1
    current = endpoints[0] if endpoints else min(component)
    previous: FlowNode | None = None
    ordered = []
    while current not in ordered:
        ordered.append(current)
        following = sorted(adjacency[current] - ({previous} if previous else set()))
        if not following:
            break
        candidate = following[0]
        if candidate == ordered[0]:
            break
        previous, current = current, candidate
    if len(ordered) != len(component):
        raise ValueError("Quad-flow component could not be ordered without branching")
    if len({face_id for face_id, _axis in ordered}) != len(ordered):
        raise ValueError("A quad flow crosses the same face in two directions")
    return ordered, closed


def _shared_cross_edge(
    topology: MeshQuadTopology, first: FlowNode, second: FlowNode
) -> int:
    shared = set(_cross_edges(topology, first)) & set(_cross_edges(topology, second))
    if len(shared) != 1:
        raise ValueError(f"Adjacent flow states {first} and {second} lack one edge")
    return shared.pop()


def _terminal_edges(
    topology: MeshQuadTopology,
    ordered: Sequence[FlowNode],
    closed: bool,
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    if closed:
        crossed = tuple(
            _shared_cross_edge(
                topology,
                ordered[index],
                ordered[(index + 1) % len(ordered)],
            )
            for index in range(len(ordered))
        )
        return crossed, ()
    if len(ordered) == 1:
        terminals = tuple(sorted(_cross_edges(topology, ordered[0])))
        return terminals, terminals
    internal = tuple(
        _shared_cross_edge(topology, first, second)
        for first, second in zip(ordered, ordered[1:])
    )
    start_candidates = set(_cross_edges(topology, ordered[0])) - {internal[0]}
    end_candidates = set(_cross_edges(topology, ordered[-1])) - {internal[-1]}
    if len(start_candidates) != 1 or len(end_candidates) != 1:
        raise ValueError("Open quad flow lacks two unambiguous terminal edges")
    endpoints = (start_candidates.pop(), end_candidates.pop())
    return (endpoints[0], *internal, endpoints[1]), endpoints


def _face_vertices(topology: MeshQuadTopology, face_id: int) -> set[int]:
    return {
        vertex_id
        for edge_id in topology.face_edges[face_id]
        for vertex_id in topology.edge_vertices[edge_id]
    }


def _average(points: Iterable[Vector3]) -> Vector3:
    values = list(points)
    return tuple(  # type: ignore[return-value]
        sum(point[axis] for point in values) / len(values) for axis in range(3)
    )


def _face_center(topology: MeshQuadTopology, face_id: int) -> Vector3:
    return _average(
        topology.positions[vertex_id] for vertex_id in _face_vertices(topology, face_id)
    )


def _edge_center(topology: MeshQuadTopology, edge_id: int) -> Vector3:
    return _average(
        topology.positions[vertex_id] for vertex_id in topology.edge_vertices[edge_id]
    )


def _flow_points(
    topology: MeshQuadTopology,
    ordered: Sequence[FlowNode],
    endpoint_edges: Sequence[int],
) -> list[Vector3]:
    centers = [_face_center(topology, face_id) for face_id, _axis in ordered]
    if endpoint_edges:
        return [
            _edge_center(topology, endpoint_edges[0]),
            *centers,
            _edge_center(topology, endpoint_edges[-1]),
        ]
    return centers


def _polyline_metrics(points: Sequence[Vector3], closed: bool) -> tuple[float, float]:
    if not points:
        return 0.0, 1.0
    pairs = list(zip(points, points[1:]))
    if closed and len(points) > 1:
        pairs.append((points[-1], points[0]))
    length = sum(_length(_subtract(second, first)) for first, second in pairs)
    if len(points) < 3:
        return length, 1.0
    scores = []
    indices = range(len(points)) if closed else range(1, len(points) - 1)
    for index in indices:
        previous = points[index - 1]
        current = points[index]
        following = points[(index + 1) % len(points)]
        incoming = _normalized(_subtract(current, previous))
        outgoing = _normalized(_subtract(following, current))
        scores.append(max(0.0, min(1.0, _dot(incoming, outgoing))))
    return length, sum(scores) / len(scores) if scores else 1.0


def _endpoint_label(
    topology: MeshQuadTopology,
    edge_id: int,
    endpoint_face: int,
    eligible_faces: set[int],
) -> str:
    linked = topology.edge_faces[edge_id]
    if len(linked) <= 1:
        return "Mesh Boundary"
    if len(linked) > 2:
        return "Non-manifold Edge"
    others = linked - {endpoint_face}
    if not others:
        return "Mesh Boundary"
    other = next(iter(others))
    side_count = len(topology.face_edges[other])
    if side_count != 4:
        if side_count == 3:
            return "Triangle Boundary"
        return f"N-gon Boundary ({side_count})"
    if other not in eligible_faces:
        return "Scope Boundary"
    return "Open Junction"


def _make_flow(
    topology: MeshQuadTopology,
    ordered: Sequence[FlowNode],
    closed: bool,
    eligible_faces: set[int],
) -> QuadFlow:
    crossed, endpoint_edges = _terminal_edges(topology, ordered, closed)
    points = _flow_points(topology, ordered, endpoint_edges)
    length, alignment = _polyline_metrics(points, closed)
    if endpoint_edges:
        start_label = _endpoint_label(
            topology, endpoint_edges[0], ordered[0][0], eligible_faces
        )
        end_label = _endpoint_label(
            topology, endpoint_edges[-1], ordered[-1][0], eligible_faces
        )
    else:
        start_label = end_label = "Closed"
    return QuadFlow(
        face_ids=tuple(face_id for face_id, _axis in ordered),
        face_axes=tuple(axis for _face_id, axis in ordered),
        crossed_edge_ids=crossed,
        endpoint_edge_ids=endpoint_edges,
        closed=closed,
        length=length,
        alignment=alignment,
        start_label=start_label,
        end_label=end_label,
    )


def parallel_neighboring_quad_flows(
    flows: Sequence[QuadFlow], topology: MeshQuadTopology
) -> dict[int, set[int]]:
    """Return face bands directly beside each flow across their side edges."""

    node_to_flow = {
        node: flow_index for flow_index, flow in enumerate(flows) for node in flow.nodes
    }
    neighbors = {index: set() for index in range(len(flows))}
    eligible = {face_id for flow in flows for face_id in flow.face_ids}
    for flow_index, flow in enumerate(flows):
        for node in flow.nodes:
            face_id, _axis = node
            for edge_id in _side_edges(topology, node):
                linked = topology.edge_faces[edge_id]
                if len(linked) != 2:
                    continue
                other_faces = linked - {face_id}
                if not other_faces:
                    continue
                other_face = next(iter(other_faces))
                if (
                    other_face not in eligible
                    or len(topology.face_edges[other_face]) != 4
                ):
                    continue
                other_edge_index = topology.face_edges[other_face].index(edge_id)
                other_node = (other_face, 1 - (other_edge_index % 2))
                other_flow = node_to_flow.get(other_node)
                if other_flow is not None and other_flow != flow_index:
                    neighbors[flow_index].add(other_flow)
                    neighbors[other_flow].add(flow_index)
    return neighbors


def _graph_component(
    start: int, neighbors: Mapping[int, set[int]], remaining: set[int]
) -> set[int]:
    pending = [start]
    component = set()
    while pending:
        current = pending.pop()
        if current in component:
            continue
        component.add(current)
        pending.extend(neighbors[current] - component)
    remaining -= component
    return component


def _order_side_to_side(
    flows: Sequence[QuadFlow], topology: MeshQuadTopology
) -> list[QuadFlow]:
    neighbors = parallel_neighboring_quad_flows(flows, topology)

    def flow_key(index: int) -> tuple[int, int]:
        return min(flows[index].nodes)

    remaining = set(range(len(flows)))
    families = []
    while remaining:
        families.append(
            _graph_component(min(remaining, key=flow_key), neighbors, remaining)
        )
    families.sort(
        key=lambda family: (-len(family), min(flow_key(index) for index in family))
    )

    ordered_indices = []
    for family in families:
        unvisited = set(family)
        endpoints = [index for index in family if len(neighbors[index] & family) <= 1]
        current = min(endpoints or family, key=flow_key)
        while unvisited:
            ordered_indices.append(current)
            unvisited.remove(current)
            adjacent = neighbors[current] & unvisited
            if adjacent:
                current = min(adjacent, key=flow_key)
                continue
            if not unvisited:
                break
            pending = deque([(current, 0)])
            seen = {current}
            candidates = []
            while pending:
                node, distance = pending.popleft()
                if node in unvisited:
                    candidates.append((distance, flow_key(node), node))
                    continue
                for following in sorted(neighbors[node] & family, key=flow_key):
                    if following not in seen:
                        seen.add(following)
                        pending.append((following, distance + 1))
            current = min(candidates)[2] if candidates else min(unvisited, key=flow_key)
    return [flows[index] for index in ordered_indices]


def discover_quad_flows(
    topology: MeshQuadTopology,
    *,
    eligible_faces: Iterable[int] | None = None,
    minimum_quads: int = 1,
    sort: str = "SIDE_TO_SIDE",
) -> list[QuadFlow]:
    """Discover maximal one-quad-wide face bands in both mesh directions."""

    if minimum_quads < 1:
        raise ValueError("minimum_quads must be at least one")
    eligible = (
        set(topology.face_edges) if eligible_faces is None else set(eligible_faces)
    )
    unknown = eligible - set(topology.face_edges)
    if unknown:
        raise ValueError(f"Unknown eligible faces: {sorted(unknown)}")
    adjacency, _transitions = _build_graph(topology, eligible)
    flows = []
    for component in _components(adjacency):
        ordered, closed = _ordered_component(component, adjacency)
        if len(ordered) >= minimum_quads:
            flows.append(_make_flow(topology, ordered, closed, eligible))

    if sort == "SIDE_TO_SIDE":
        flows = _order_side_to_side(flows, topology)
    elif sort in {"LARGEST", "LONGEST"}:
        flows.sort(key=lambda flow: (-flow.quad_count, -flow.length, min(flow.nodes)))
    elif sort == "SMOOTHEST":
        flows.sort(
            key=lambda flow: (
                -flow.alignment,
                -flow.quad_count,
                min(flow.nodes),
            )
        )
    elif sort == "INDEX":
        flows.sort(key=lambda flow: min(flow.nodes))
    else:
        raise ValueError(f"Unknown quad-flow sort: {sort}")
    return flows


def _incident_edges(topology: MeshQuadTopology) -> dict[int, set[int]]:
    result = {vertex_id: set() for vertex_id in topology.positions}
    for edge_id, vertices in topology.edge_vertices.items():
        for vertex_id in vertices:
            result.setdefault(vertex_id, set()).add(edge_id)
    return result


def _other_vertex(topology: MeshQuadTopology, edge_id: int, vertex_id: int) -> int:
    first, second = topology.edge_vertices[edge_id]
    if first == vertex_id:
        return second
    if second == vertex_id:
        return first
    raise ValueError(f"Vertex {vertex_id} is not on edge {edge_id}")


def quad_region_barriers(
    topology: MeshQuadTopology,
    *,
    eligible_faces: Iterable[int] | None = None,
) -> tuple[set[int], set[int], set[int]]:
    """Return barrier edges, separatrices, and interior extraordinary vertices."""

    eligible = (
        {face_id for face_id, edges in topology.face_edges.items() if len(edges) == 4}
        if eligible_faces is None
        else {
            face_id
            for face_id in eligible_faces
            if len(topology.face_edges.get(face_id, ())) == 4
        }
    )
    incident = _incident_edges(topology)
    boundary_vertices = {
        vertex_id
        for vertex_id, edge_ids in incident.items()
        if any(len(topology.edge_faces[edge_id]) != 2 for edge_id in edge_ids)
    }
    nonquad_faces = {
        face_id for face_id, edges in topology.face_edges.items() if len(edges) != 4
    }
    nonquad_vertices = {
        vertex_id
        for edge_id, linked in topology.edge_faces.items()
        if linked & nonquad_faces
        for vertex_id in topology.edge_vertices[edge_id]
    }
    extraordinary = {
        vertex_id
        for vertex_id, edge_ids in incident.items()
        if vertex_id not in boundary_vertices
        and vertex_id not in nonquad_vertices
        and len(edge_ids) != 4
    }

    barriers = {
        edge_id
        for edge_id, linked in topology.edge_faces.items()
        if len(linked) != 2
        or bool(linked & nonquad_faces)
        or len(linked & eligible) != len(linked)
    }
    separatrices: set[int] = set()
    for pole in sorted(extraordinary):
        for initial_edge in sorted(incident[pole]):
            current_vertex = _other_vertex(topology, initial_edge, pole)
            incoming_edge = initial_edge
            seen = set()
            while True:
                state = (current_vertex, incoming_edge)
                if state in seen:
                    break
                seen.add(state)
                separatrices.add(incoming_edge)
                if (
                    current_vertex in boundary_vertices
                    or current_vertex in extraordinary
                    or current_vertex in nonquad_vertices
                ):
                    break
                candidates = [
                    edge_id
                    for edge_id in incident[current_vertex]
                    if edge_id != incoming_edge
                    and not (
                        topology.edge_faces[edge_id]
                        & topology.edge_faces[incoming_edge]
                    )
                ]
                if len(candidates) != 1:
                    break
                outgoing_edge = candidates[0]
                current_vertex = _other_vertex(topology, outgoing_edge, current_vertex)
                incoming_edge = outgoing_edge
    barriers.update(separatrices)
    return barriers, separatrices, extraordinary


def discover_quad_regions(
    topology: MeshQuadTopology,
    *,
    eligible_faces: Iterable[int] | None = None,
    minimum_quads: int = 1,
    sort: str = "LARGEST",
) -> list[QuadFlow]:
    """Discover complete quad patches bounded by poles and their separatrices."""

    if minimum_quads < 1:
        raise ValueError("minimum_quads must be at least one")
    eligible = (
        {face_id for face_id, edges in topology.face_edges.items() if len(edges) == 4}
        if eligible_faces is None
        else {
            face_id
            for face_id in eligible_faces
            if len(topology.face_edges.get(face_id, ())) == 4
        }
    )
    unknown = eligible - set(topology.face_edges)
    if unknown:
        raise ValueError(f"Unknown eligible faces: {sorted(unknown)}")
    barriers, separatrices, _extraordinary = quad_region_barriers(
        topology, eligible_faces=eligible
    )
    remaining = set(eligible)
    region_faces = []
    while remaining:
        pending = [min(remaining)]
        region = set()
        while pending:
            face_id = pending.pop()
            if face_id in region:
                continue
            region.add(face_id)
            for edge_id in topology.face_edges[face_id]:
                if edge_id in barriers:
                    continue
                pending.extend(
                    other
                    for other in topology.edge_faces[edge_id]
                    if other in eligible and other not in region
                )
        remaining -= region
        if len(region) >= minimum_quads:
            region_faces.append(region)

    regions = []
    for faces in region_faces:
        boundary = {
            edge_id
            for face_id in faces
            for edge_id in topology.face_edges[face_id]
            if edge_id in barriers
            or any(
                linked_face not in faces for linked_face in topology.edge_faces[edge_id]
            )
            or len(topology.edge_faces[edge_id]) < 2
        }
        boundary_length = sum(
            _length(
                _subtract(
                    topology.positions[topology.edge_vertices[edge_id][1]],
                    topology.positions[topology.edge_vertices[edge_id][0]],
                )
            )
            for edge_id in boundary
        )
        pole_boundary = bool(boundary & separatrices)
        ordered = tuple(sorted(faces))
        regions.append(
            QuadFlow(
                face_ids=ordered,
                face_axes=(0,) * len(ordered),
                crossed_edge_ids=tuple(sorted(boundary)),
                endpoint_edge_ids=tuple(sorted(boundary & separatrices)),
                closed=False,
                length=boundary_length,
                alignment=1.0,
                start_label=(
                    "Pole-Separated Region" if pole_boundary else "Regular Quad Region"
                ),
                end_label=f"{len(boundary)} boundary edges",
            )
        )

    if sort in {"LARGEST", "LONGEST"}:
        regions.sort(key=lambda region: (-region.quad_count, min(region.face_ids)))
    elif sort in {"INDEX", "SIDE_TO_SIDE"}:
        regions.sort(key=lambda region: min(region.face_ids))
    elif sort == "SMOOTHEST":
        regions.sort(key=lambda region: (-region.quad_count, min(region.face_ids)))
    else:
        raise ValueError(f"Unknown quad-region sort: {sort}")
    return regions


def neighboring_quad_regions(
    regions: Sequence[QuadFlow], topology: MeshQuadTopology
) -> dict[int, set[int]]:
    """Return regions that meet across at least one boundary edge."""

    face_to_region = {
        face_id: index
        for index, region in enumerate(regions)
        for face_id in region.face_ids
    }
    neighbors = {index: set() for index in range(len(regions))}
    for linked in topology.edge_faces.values():
        indices = {
            face_to_region[face_id] for face_id in linked if face_id in face_to_region
        }
        if len(indices) == 2:
            first, second = indices
            neighbors[first].add(second)
            neighbors[second].add(first)
    return neighbors
