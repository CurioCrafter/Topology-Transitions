from __future__ import annotations

import unittest

from topology_transitions.quad_flows import (
    MeshQuadTopology,
    discover_quad_flows,
    parallel_neighboring_quad_flows,
)


def rectangular_grid(width: int, height: int) -> MeshQuadTopology:
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

    face_edges = {}
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
    return MeshQuadTopology(
        edge_vertices=edge_vertices,
        edge_faces={edge_id: frozenset(faces) for edge_id, faces in edge_faces.items()},
        face_edges=face_edges,
        positions=positions,
    )


class QuadFlowTests(unittest.TestCase):
    def test_grid_discovers_face_rows_and_columns(self) -> None:
        topology = rectangular_grid(4, 3)
        flows = discover_quad_flows(topology, sort="INDEX")
        self.assertEqual(len(flows), 7)
        self.assertEqual(
            sorted(flow.quad_count for flow in flows), [3, 3, 3, 3, 4, 4, 4]
        )
        self.assertTrue(
            all(len(set(flow.face_ids)) == flow.quad_count for flow in flows)
        )
        self.assertTrue(all(not flow.closed for flow in flows))

    def test_each_quad_belongs_to_two_perpendicular_flows(self) -> None:
        topology = rectangular_grid(3, 2)
        flows = discover_quad_flows(topology)
        memberships = {face_id: 0 for face_id in topology.face_edges}
        for flow in flows:
            for face_id in flow.face_ids:
                memberships[face_id] += 1
        self.assertEqual(set(memberships.values()), {2})

    def test_selected_face_scope_clips_the_band(self) -> None:
        topology = rectangular_grid(4, 3)
        first_row = {0, 1, 2, 3}
        flows = discover_quad_flows(
            topology,
            eligible_faces=first_row,
            minimum_quads=2,
            sort="INDEX",
        )
        self.assertEqual(len(flows), 1)
        self.assertEqual(set(flows[0].face_ids), first_row)
        self.assertIn("Boundary", flows[0].start_label)

    def test_side_to_side_finishes_each_parallel_family(self) -> None:
        topology = rectangular_grid(4, 3)
        flows = discover_quad_flows(topology, sort="SIDE_TO_SIDE")
        neighbors = parallel_neighboring_quad_flows(flows, topology)
        family_breaks = [
            index
            for index in range(len(flows) - 1)
            if index + 1 not in neighbors[index]
        ]
        self.assertEqual(family_breaks, [3])
        self.assertEqual([flow.quad_count for flow in flows], [3, 3, 3, 3, 4, 4, 4])

    def test_minimum_quads_filters_short_bands(self) -> None:
        topology = rectangular_grid(4, 2)
        flows = discover_quad_flows(topology, minimum_quads=3)
        self.assertEqual(len(flows), 2)
        self.assertEqual({flow.quad_count for flow in flows}, {4})


if __name__ == "__main__":
    unittest.main()
