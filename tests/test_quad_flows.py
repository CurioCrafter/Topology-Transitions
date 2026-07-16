from __future__ import annotations

import unittest

from topology_transitions.core import build_transition_template
from topology_transitions.quad_flows import (
    MeshQuadTopology,
    discover_quad_flows,
    discover_quad_regions,
    neighboring_quad_regions,
    parallel_neighboring_quad_flows,
    quad_region_barriers,
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


def transition_topology() -> MeshQuadTopology:
    template = build_transition_template(5, 3, 2, 2)
    vertex_indices = {key: index for index, key in enumerate(template.vertices)}
    edge_indices: dict[tuple[int, int], int] = {}
    edge_faces: dict[int, set[int]] = {}
    face_edges = {}
    for face_id, face in enumerate(template.faces):
        edges = []
        for first, second in zip(face, (*face[1:], face[0]), strict=True):
            pair = tuple(sorted((vertex_indices[first], vertex_indices[second])))
            edge_id = edge_indices.setdefault(pair, len(edge_indices))
            edge_faces.setdefault(edge_id, set()).add(face_id)
            edges.append(edge_id)
        face_edges[face_id] = tuple(edges)
    return MeshQuadTopology(
        edge_vertices={pair_id: pair for pair, pair_id in edge_indices.items()},
        edge_faces={edge_id: frozenset(faces) for edge_id, faces in edge_faces.items()},
        face_edges=face_edges,
        positions={
            vertex_indices[key]: (spec.u, spec.v, 0.0)
            for key, spec in template.vertices.items()
        },
    )


class QuadFlowTests(unittest.TestCase):
    def test_regular_grid_is_one_whole_quad_region(self) -> None:
        topology = rectangular_grid(4, 3)
        regions = discover_quad_regions(topology)
        self.assertEqual(len(regions), 1)
        self.assertEqual(set(regions[0].face_ids), set(topology.face_edges))

    def test_poles_split_transition_into_complete_regions(self) -> None:
        topology = transition_topology()
        barriers, separatrices, poles = quad_region_barriers(topology)
        regions = discover_quad_regions(topology)
        self.assertEqual(len(poles), 2)
        self.assertTrue(separatrices)
        self.assertTrue(separatrices <= barriers)
        self.assertGreater(len(regions), 1)
        memberships = [face_id for region in regions for face_id in region.face_ids]
        self.assertEqual(sorted(memberships), sorted(topology.face_edges))
        self.assertEqual(len(memberships), len(set(memberships)))
        neighbors = neighboring_quad_regions(regions, topology)
        self.assertTrue(any(neighbors.values()))

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
