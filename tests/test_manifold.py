from __future__ import annotations

import unittest

from topology_transitions.manifold import analyze_manifold, boundary_components


class ManifoldTests(unittest.TestCase):
    TETRA_EDGES = {
        0: (0, 1),
        1: (1, 2),
        2: (2, 0),
        3: (0, 3),
        4: (1, 3),
        5: (2, 3),
    }
    TETRA_FACES = {
        0: (0, 2, 1),
        1: (0, 1, 3),
        2: (1, 2, 3),
        3: (2, 0, 3),
    }

    def test_boundary_components_orders_closed_loops(self) -> None:
        components = boundary_components(
            {
                0: (0, 1),
                1: (1, 2),
                2: (2, 3),
                3: (3, 0),
                4: (10, 11),
                5: (11, 12),
            },
            range(6),
        )
        self.assertEqual(len(components), 2)
        self.assertTrue(components[0].closed)
        self.assertEqual(components[0].vertex_ids, (0, 1, 2, 3))
        self.assertEqual(components[0].edge_ids, (0, 1, 2, 3))
        self.assertFalse(components[1].closed)
        self.assertEqual(components[1].edge_ids, (4, 5))

    def test_boundary_components_marks_branches_unfillable(self) -> None:
        component = boundary_components(
            {0: (0, 1), 1: (1, 2), 2: (1, 3)},
            (0, 1, 2),
        )[0]
        self.assertFalse(component.closed)
        self.assertEqual(component.vertex_ids, (0, 1, 2, 3))

    def test_boundary_components_rejects_unknown_edges(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown boundary edge"):
            boundary_components({0: (0, 1)}, (0, 99))

    def test_open_plane_pinpoints_one_boundary_loop(self) -> None:
        report = analyze_manifold(
            {0: (0, 1), 1: (1, 2), 2: (2, 3), 3: (3, 0)},
            {0: 1, 1: 1, 2: 1, 3: 1},
            range(4),
        )
        self.assertEqual(report.open_boundary_edges, frozenset(range(4)))
        self.assertEqual(len(report.components), 1)
        self.assertEqual(report.components[0].kind, "Open Boundary")

    def test_three_faces_on_one_edge_is_nonmanifold(self) -> None:
        report = analyze_manifold(
            {0: (0, 1), 1: (1, 2), 2: (2, 0)},
            {0: 3, 1: 2, 2: 2},
            range(3),
        )
        self.assertEqual(report.nonmanifold_edges, frozenset({0}))
        self.assertEqual(report.components[0].edge_ids, frozenset({0}))

    def test_closed_cube_style_counts_are_clean(self) -> None:
        report = analyze_manifold(
            {0: (0, 1), 1: (1, 2), 2: (2, 3), 3: (3, 0)},
            {0: 2, 1: 2, 2: 2, 3: 2},
            range(4),
        )
        self.assertTrue(report.clean)

    def test_consistently_wound_closed_surface_is_clean(self) -> None:
        report = analyze_manifold(
            self.TETRA_EDGES,
            {edge_id: 2 for edge_id in self.TETRA_EDGES},
            range(4),
            self.TETRA_FACES,
        )
        self.assertTrue(report.clean)
        self.assertEqual(report.inconsistent_winding_edges, frozenset())
        self.assertEqual(report.disconnected_fan_vertices, frozenset())

    def test_two_face_edges_with_inconsistent_winding_are_issues(self) -> None:
        faces = dict(self.TETRA_FACES)
        faces[1] = tuple(reversed(faces[1]))
        report = analyze_manifold(
            self.TETRA_EDGES,
            {edge_id: 2 for edge_id in self.TETRA_EDGES},
            range(4),
            faces,
        )
        self.assertFalse(report.clean)
        self.assertEqual(report.inconsistent_winding_edges, frozenset({0, 3, 4}))
        self.assertEqual(report.issue_edge_ids, frozenset({0, 3, 4}))
        self.assertEqual(report.components[0].kind, "Inconsistent Winding")

    def test_closed_shells_touching_at_one_vertex_have_disconnected_fans(self) -> None:
        second_edges = {
            6: (0, 4),
            7: (4, 5),
            8: (5, 0),
            9: (0, 6),
            10: (4, 6),
            11: (5, 6),
        }
        second_faces = {
            4: (0, 5, 4),
            5: (0, 4, 6),
            6: (4, 5, 6),
            7: (5, 0, 6),
        }
        edges = self.TETRA_EDGES | second_edges
        report = analyze_manifold(
            edges,
            {edge_id: 2 for edge_id in edges},
            range(7),
            self.TETRA_FACES | second_faces,
        )
        self.assertFalse(report.clean)
        self.assertEqual(report.inconsistent_winding_edges, frozenset())
        self.assertEqual(report.disconnected_fan_vertices, frozenset({0}))
        self.assertEqual(report.issue_edge_ids, frozenset())
        self.assertEqual(report.issue_vertex_ids, frozenset({0}))
        self.assertEqual(report.components[0].edge_ids, frozenset())
        self.assertEqual(report.components[0].vertex_ids, frozenset({0}))
        self.assertEqual(report.components[0].kind, "Disconnected Face Fans")

    def test_wire_and_isolated_vertex_are_separate_exact_issues(self) -> None:
        report = analyze_manifold({0: (0, 1)}, {0: 0}, range(3))
        self.assertEqual(report.wire_edges, frozenset({0}))
        self.assertEqual(report.isolated_vertices, frozenset({2}))
        self.assertEqual(len(report.components), 2)


if __name__ == "__main__":
    unittest.main()
