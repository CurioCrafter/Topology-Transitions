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
class ManifoldReport:
    components: tuple[ManifoldIssueComponent, ...]
    open_boundary_edges: frozenset[int]
    nonmanifold_edges: frozenset[int]
    wire_edges: frozenset[int]
    isolated_vertices: frozenset[int]

    @property
    def clean(self) -> bool:
        return not self.components

    @property
    def issue_edge_ids(self) -> frozenset[int]:
        return self.open_boundary_edges | self.nonmanifold_edges | self.wire_edges


def analyze_manifold(
    edge_vertices: Mapping[int, tuple[int, int]],
    edge_face_counts: Mapping[int, int],
    vertex_ids: Iterable[int],
) -> ManifoldReport:
    """Classify open, wire, over-connected, and isolated mesh elements."""

    if set(edge_vertices) != set(edge_face_counts):
        raise ValueError("Every edge needs one face count")
    open_edges = frozenset(
        edge_id for edge_id, count in edge_face_counts.items() if count == 1
    )
    wire_edges = frozenset(
        edge_id for edge_id, count in edge_face_counts.items() if count == 0
    )
    nonmanifold_edges = frozenset(
        edge_id for edge_id, count in edge_face_counts.items() if count > 2
    )
    issue_edges = open_edges | wire_edges | nonmanifold_edges
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
        components.append(
            ManifoldIssueComponent(
                edge_ids=frozenset(component_edges),
                vertex_ids=frozenset(component_vertices),
                kind=next(iter(kinds)) if len(kinds) == 1 else "Mixed Issues",
            )
        )

    isolated = frozenset(set(vertex_ids) - used_vertices)
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
        isolated_vertices=isolated,
    )
