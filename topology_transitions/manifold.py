"""Pure connected-component diagnostics for mesh manifold defects."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class ManifoldIssueComponent:
    edge_ids: frozenset[int]
    vertex_ids: frozenset[int]
    kind: str


@dataclass(frozen=True)
class BoundaryComponent:
    """One connected set of open edges, optionally ordered as a closed loop."""

    edge_ids: tuple[int, ...]
    vertex_ids: tuple[int, ...]
    closed: bool


@dataclass(frozen=True)
class ManifoldReport:
    components: tuple[ManifoldIssueComponent, ...]
    open_boundary_edges: frozenset[int]
    nonmanifold_edges: frozenset[int]
    wire_edges: frozenset[int]
    inconsistent_winding_edges: frozenset[int]
    isolated_vertices: frozenset[int]
    disconnected_fan_vertices: frozenset[int]

    @property
    def clean(self) -> bool:
        return not self.components

    @property
    def issue_edge_ids(self) -> frozenset[int]:
        return (
            self.open_boundary_edges
            | self.nonmanifold_edges
            | self.wire_edges
            | self.inconsistent_winding_edges
        )

    @property
    def issue_vertex_ids(self) -> frozenset[int]:
        return self.isolated_vertices | self.disconnected_fan_vertices


def boundary_components(
    edge_vertices: Mapping[int, tuple[int, int]],
    boundary_edge_ids: Iterable[int],
) -> tuple[BoundaryComponent, ...]:
    """Return connected open-edge components with simple loops in cycle order.

    A component is marked closed only when every vertex has exactly two incident
    boundary edges.  Branched or open components remain available to diagnostics,
    but are deliberately not presented as fillable loops.
    """

    remaining = set(boundary_edge_ids)
    unknown = remaining - set(edge_vertices)
    if unknown:
        raise ValueError(f"Unknown boundary edge ids: {sorted(unknown)}")

    vertex_to_edges: dict[int, set[int]] = {}
    for edge_id in remaining:
        for vertex_id in edge_vertices[edge_id]:
            vertex_to_edges.setdefault(vertex_id, set()).add(edge_id)

    result = []
    while remaining:
        pending = [min(remaining)]
        component_edges = set()
        component_vertices = set()
        while pending:
            edge_id = pending.pop()
            if edge_id in component_edges:
                continue
            component_edges.add(edge_id)
            vertices = edge_vertices[edge_id]
            component_vertices.update(vertices)
            for vertex_id in vertices:
                pending.extend(vertex_to_edges[vertex_id] - component_edges)
        remaining -= component_edges

        closed = bool(component_edges) and all(
            len(vertex_to_edges[vertex_id] & component_edges) == 2
            for vertex_id in component_vertices
        )
        if not closed:
            result.append(
                BoundaryComponent(
                    edge_ids=tuple(sorted(component_edges)),
                    vertex_ids=tuple(sorted(component_vertices)),
                    closed=False,
                )
            )
            continue

        start_vertex = min(component_vertices)
        current_vertex = start_vertex
        previous_edge = None
        ordered_edges = []
        ordered_vertices = []
        while True:
            candidates = sorted(
                (vertex_to_edges[current_vertex] & component_edges)
                - ({previous_edge} if previous_edge is not None else set())
            )
            edge_id = candidates[0]
            ordered_vertices.append(current_vertex)
            ordered_edges.append(edge_id)
            first, second = edge_vertices[edge_id]
            next_vertex = second if first == current_vertex else first
            previous_edge = edge_id
            current_vertex = next_vertex
            if current_vertex == start_vertex:
                break
            if len(ordered_edges) > len(component_edges):
                raise ValueError("Boundary loop traversal did not close")

        if len(ordered_edges) != len(component_edges):
            raise ValueError("Boundary component contains more than one cycle")
        result.append(
            BoundaryComponent(
                edge_ids=tuple(ordered_edges),
                vertex_ids=tuple(ordered_vertices),
                closed=True,
            )
        )

    result.sort(
        key=lambda component: (
            min(component.edge_ids),
            min(component.vertex_ids),
        )
    )
    return tuple(result)


def _face_topology_issues(
    edge_vertices: Mapping[int, tuple[int, int]],
    edge_face_counts: Mapping[int, int],
    vertex_ids: set[int],
    face_vertices: Mapping[int, tuple[int, ...]],
) -> tuple[frozenset[int], frozenset[int]]:
    """Return winding-edge and disconnected-vertex-fan defects.

    Ordered face loops carry the incidence and orientation information that
    cannot be recovered from edge face counts alone.  Two consistently wound
    faces traverse their shared edge in opposite directions.  Faces incident
    to a manifold vertex form one connected fan through the vertex's edges.
    """

    edge_by_vertices: dict[frozenset[int], int] = {}
    edge_faces: dict[int, list[int]] = {edge_id: [] for edge_id in edge_vertices}
    edge_directions: dict[int, list[int]] = {
        edge_id: [] for edge_id in edge_vertices
    }
    vertex_faces: dict[int, set[int]] = {vertex_id: set() for vertex_id in vertex_ids}

    for edge_id, vertices in edge_vertices.items():
        if len(set(vertices)) != 2:
            raise ValueError(f"Edge {edge_id} must connect two distinct vertices")
        unknown = set(vertices) - vertex_ids
        if unknown:
            raise ValueError(
                f"Edge {edge_id} uses unknown vertex ids: {sorted(unknown)}"
            )
        key = frozenset(vertices)
        if key in edge_by_vertices:
            raise ValueError(
                f"Edges {edge_by_vertices[key]} and {edge_id} share both vertices"
            )
        edge_by_vertices[key] = edge_id

    for face_id, supplied_vertices in face_vertices.items():
        vertices = tuple(supplied_vertices)
        if len(vertices) < 3 or len(set(vertices)) != len(vertices):
            raise ValueError(f"Face {face_id} needs at least three distinct vertices")
        unknown = set(vertices) - vertex_ids
        if unknown:
            raise ValueError(
                f"Face {face_id} uses unknown vertex ids: {sorted(unknown)}"
            )
        for vertex_id in vertices:
            vertex_faces[vertex_id].add(face_id)
        for index, first in enumerate(vertices):
            second = vertices[(index + 1) % len(vertices)]
            edge_id = edge_by_vertices.get(frozenset((first, second)))
            if edge_id is None:
                raise ValueError(
                    f"Face {face_id} uses missing edge ({first}, {second})"
                )
            edge_faces[edge_id].append(face_id)
            edge_directions[edge_id].append(
                1 if edge_vertices[edge_id] == (first, second) else -1
            )

    for edge_id, expected_count in edge_face_counts.items():
        actual_count = len(edge_faces[edge_id])
        if actual_count != expected_count:
            raise ValueError(
                f"Edge {edge_id} expected {expected_count} faces, found {actual_count}"
            )

    inconsistent_winding_edges = frozenset(
        edge_id
        for edge_id, directions in edge_directions.items()
        if len(directions) == 2 and directions[0] == directions[1]
    )

    disconnected_fan_vertices = set()
    vertex_edges: dict[int, list[int]] = {vertex_id: [] for vertex_id in vertex_ids}
    for edge_id, vertices in edge_vertices.items():
        for vertex_id in vertices:
            vertex_edges[vertex_id].append(edge_id)
    for vertex_id, incident_faces in vertex_faces.items():
        if len(incident_faces) < 2:
            continue
        face_neighbors = {face_id: set() for face_id in incident_faces}
        for edge_id in vertex_edges[vertex_id]:
            linked_faces = edge_faces[edge_id]
            if len(linked_faces) < 2:
                continue
            first_face = linked_faces[0]
            for other_face in linked_faces[1:]:
                face_neighbors[first_face].add(other_face)
                face_neighbors[other_face].add(first_face)
        pending = [next(iter(incident_faces))]
        connected_faces = set()
        while pending:
            face_id = pending.pop()
            if face_id in connected_faces:
                continue
            connected_faces.add(face_id)
            pending.extend(face_neighbors[face_id] - connected_faces)
        if connected_faces != incident_faces:
            disconnected_fan_vertices.add(vertex_id)

    return inconsistent_winding_edges, frozenset(disconnected_fan_vertices)


def analyze_manifold(
    edge_vertices: Mapping[int, tuple[int, int]],
    edge_face_counts: Mapping[int, int],
    vertex_ids: Iterable[int],
    face_vertices: Mapping[int, tuple[int, ...]] | None = None,
) -> ManifoldReport:
    """Classify manifold defects from edges and optional ordered face loops.

    ``face_vertices`` must describe every face when supplied.  It enables
    detection of inconsistent winding and disconnected incident face fans;
    neither defect can be inferred from edge face counts alone.
    """

    if set(edge_vertices) != set(edge_face_counts):
        raise ValueError("Every edge needs one face count")
    all_vertex_ids = set(vertex_ids)
    inconsistent_winding_edges: frozenset[int] = frozenset()
    disconnected_fan_vertices: frozenset[int] = frozenset()
    if face_vertices is not None:
        inconsistent_winding_edges, disconnected_fan_vertices = (
            _face_topology_issues(
                edge_vertices,
                edge_face_counts,
                all_vertex_ids,
                face_vertices,
            )
        )
    open_edges = frozenset(
        edge_id for edge_id, count in edge_face_counts.items() if count == 1
    )
    wire_edges = frozenset(
        edge_id for edge_id, count in edge_face_counts.items() if count == 0
    )
    nonmanifold_edges = frozenset(
        edge_id for edge_id, count in edge_face_counts.items() if count > 2
    )
    issue_edges = (
        open_edges | wire_edges | nonmanifold_edges | inconsistent_winding_edges
    )
    vertex_to_issue_edges: dict[int, set[int]] = {}
    used_vertices = set()
    for edge_id, vertices in edge_vertices.items():
        used_vertices.update(vertices)
        if edge_id in issue_edges:
            for vertex_id in vertices:
                vertex_to_issue_edges.setdefault(vertex_id, set()).add(edge_id)

    components = []
    remaining = set(issue_edges)
    while remaining:
        pending = [min(remaining)]
        component_edges = set()
        component_vertices = set()
        while pending:
            edge_id = pending.pop()
            if edge_id in component_edges:
                continue
            component_edges.add(edge_id)
            vertices = edge_vertices[edge_id]
            component_vertices.update(vertices)
            for vertex_id in vertices:
                pending.extend(
                    vertex_to_issue_edges.get(vertex_id, ()) - component_edges
                )
        remaining -= component_edges
        kinds = set()
        if component_edges & open_edges:
            kinds.add("Open Boundary")
        if component_edges & nonmanifold_edges:
            kinds.add("Non-Manifold")
        if component_edges & wire_edges:
            kinds.add("Wire Geometry")
        if component_edges & inconsistent_winding_edges:
            kinds.add("Inconsistent Winding")
        component_fan_vertices = component_vertices & disconnected_fan_vertices
        if component_fan_vertices:
            kinds.add("Disconnected Face Fans")
        components.append(
            ManifoldIssueComponent(
                edge_ids=frozenset(component_edges),
                vertex_ids=frozenset(component_vertices),
                kind=next(iter(kinds)) if len(kinds) == 1 else "Mixed Issues",
            )
        )

    handled_fan_vertices = set().union(
        *(component.vertex_ids for component in components)
    ) & disconnected_fan_vertices
    components.extend(
        ManifoldIssueComponent(
            frozenset(), frozenset({vertex_id}), "Disconnected Face Fans"
        )
        for vertex_id in sorted(disconnected_fan_vertices - handled_fan_vertices)
    )
    isolated = frozenset(all_vertex_ids - used_vertices)
    components.extend(
        ManifoldIssueComponent(frozenset(), frozenset({vertex_id}), "Isolated Vertex")
        for vertex_id in sorted(isolated)
    )
    components.sort(
        key=lambda component: (
            min(component.edge_ids) if component.edge_ids else 10**18,
            min(component.vertex_ids),
        )
    )
    return ManifoldReport(
        components=tuple(components),
        open_boundary_edges=open_edges,
        nonmanifold_edges=nonmanifold_edges,
        wire_edges=wire_edges,
        inconsistent_winding_edges=inconsistent_winding_edges,
        isolated_vertices=isolated,
        disconnected_fan_vertices=disconnected_fan_vertices,
    )
