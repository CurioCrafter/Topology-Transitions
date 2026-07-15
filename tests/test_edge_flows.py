from __future__ import annotations

import unittest

from topology_transitions.edge_flows import (
    MeshFlowTopology,
    build_continuations,
    discover_edge_flows,
    neighboring_flows,
)


def rectangular_grid(width: int, height: int) -> MeshFlowTopology:
    positions = {
        y * (width + 1) + x: (float(x), float(y), 0.0)
        for y in range(height + 1)
        for x in range(width + 1)
    }
    edge_vertices: dict[int, tuple[int, int]] = {}
    horizontal: dict[tuple[int, int], int] = {}
    vertical: dict[tuple[int, int], int] = {}
    next_edge = 0
    for y in range(height + 1):
        for x in range(width):
            horizontal[(x, y)] = next_edge
            edge_vertices[next_edge] = (
                y * (width + 1) + x,
                y * (width + 1) + x + 1,
            )
            next_edge += 1
    for y in range(height):
        for x in range(width + 1):
            vertical[(x, y)] = next_edge
            edge_vertices[next_edge] = (
                y * (width + 1) + x,
                (y + 1) * (width + 1) + x,
            )
            next_edge += 1

    face_edges: dict[int, tuple[int, ...]] = {}
    edge_faces = {edge_id: set() for edge_id in edge_vertices}
    face_id = 0
    for y in range(height):
        for x in range(width):
            edges = (
                horizontal[(x, y)],
                vertical[(x + 1, y)],
                horizontal[(x, y + 1)],
                vertical[(x, y)],
            )
            face_edges[face_id] = edges
            for edge_id in edges:
                edge_faces[edge_id].add(face_id)
            face_id += 1
    vertex_edges = {vertex_id: [] for vertex_id in positions}
    for edge_id, vertices in edge_vertices.items():
        for vertex_id in vertices:
            vertex_edges[vertex_id].append(edge_id)
    return MeshFlowTopology(
        edge_vertices=edge_vertices,
        vertex_edges=vertex_edges,
        edge_faces={edge_id: frozenset(faces) for edge_id, faces in edge_faces.items()},
        face_edges=face_edges,
        positions=positions,
    )


class EdgeFlowTests(unittest.TestCase):
    def test_regular_grid_finds_interior_rows_and_columns(self):
        topology = rectangular_grid(4, 3)
        flows = discover_edge_flows(topology, mode="TOPOLOGY", minimum_edges=2)
        self.assertEqual(len(flows), 5)
        self.assertEqual(sorted(flow.edge_count for flow in flows), [3, 3, 3, 4, 4])
        self.assertTrue(all(not flow.closed for flow in flows))
        self.assertTrue(all(flow.alignment == 1.0 for flow in flows))
        self.assertTrue(all(flow.start.label.startswith("Boundary") for flow in flows))

    def test_minimum_edge_filter_keeps_singletons_optional(self):
        topology = rectangular_grid(2, 2)
        all_flows = discover_edge_flows(topology, minimum_edges=1)
        long_flows = discover_edge_flows(topology, minimum_edges=2)
        self.assertGreater(len(all_flows), len(long_flows))
        self.assertTrue(all(flow.edge_count >= 2 for flow in long_flows))

    def test_selected_edge_scope_breaks_flow_at_scope_boundary(self):
        topology = rectangular_grid(4, 3)
        full = discover_edge_flows(topology, minimum_edges=2)
        selected = set(full[0].edge_ids[:2])
        scoped = discover_edge_flows(topology, eligible_edges=selected, minimum_edges=1)
        self.assertEqual(len(scoped), 1)
        self.assertEqual(set(scoped[0].edge_ids), selected)
        self.assertEqual(scoped[0].edge_count, 2)

    def test_geometric_mode_pairs_through_extraordinary_vertex(self):
        topology = MeshFlowTopology(
            edge_vertices={
                0: (0, 1),
                1: (0, 2),
                2: (0, 3),
                3: (0, 4),
                4: (0, 5),
            },
            vertex_edges={
                0: (0, 1, 2, 3, 4),
                1: (0,),
                2: (1,),
                3: (2,),
                4: (3,),
                5: (4,),
            },
            edge_faces={edge_id: frozenset() for edge_id in range(5)},
            face_edges={},
            positions={
                0: (0.0, 0.0, 0.0),
                1: (-1.0, 0.0, 0.0),
                2: (1.0, 0.0, 0.0),
                3: (0.0, -1.0, 0.0),
                4: (0.0, 1.0, 0.0),
                5: (0.7, 0.7, 0.0),
            },
        )
        topological = build_continuations(topology, mode="TOPOLOGY")
        geometric = build_continuations(topology, mode="GEOMETRIC")
        self.assertNotIn((0, 0), topological)
        self.assertEqual(geometric[(0, 0)], 1)
        self.assertEqual(geometric[(0, 2)], 3)
        flows = discover_edge_flows(topology, mode="GEOMETRIC", minimum_edges=1)
        self.assertEqual(sorted(flow.edge_count for flow in flows), [1, 2, 2])

    def test_topology_mode_does_not_pair_through_non_quad_faces(self):
        topology = MeshFlowTopology(
            edge_vertices={
                0: (0, 1),
                1: (0, 2),
                2: (0, 3),
                3: (0, 4),
                4: (1, 2),
                5: (2, 3),
                6: (3, 4),
                7: (4, 1),
            },
            vertex_edges={
                0: (0, 1, 2, 3),
                1: (0, 4, 7),
                2: (1, 4, 5),
                3: (2, 5, 6),
                4: (3, 6, 7),
            },
            edge_faces={
                0: frozenset((0, 3)),
                1: frozenset((0, 1)),
                2: frozenset((1, 2)),
                3: frozenset((2, 3)),
                4: frozenset((0,)),
                5: frozenset((1,)),
                6: frozenset((2,)),
                7: frozenset((3,)),
            },
            face_edges={
                0: (0, 1, 4),
                1: (1, 2, 5),
                2: (2, 3, 6),
                3: (3, 0, 7),
            },
            positions={
                0: (0.0, 0.0, 0.0),
                1: (-1.0, 0.0, 0.0),
                2: (0.0, 1.0, 0.0),
                3: (1.0, 0.0, 0.0),
                4: (0.0, -1.0, 0.0),
            },
        )
        continuations = build_continuations(topology, mode="TOPOLOGY")
        self.assertFalse(any(vertex_id == 0 for vertex_id, _edge_id in continuations))

    def test_neighboring_flows_share_faces(self):
        topology = rectangular_grid(4, 3)
        flows = discover_edge_flows(topology, minimum_edges=2)
        neighbors = neighboring_flows(flows, topology.face_edges)
        self.assertTrue(all(neighbors[index] for index in neighbors))
        self.assertTrue(
            all(
                index in neighbors[other]
                for index, others in neighbors.items()
                for other in others
            )
        )


if __name__ == "__main__":
    unittest.main()
