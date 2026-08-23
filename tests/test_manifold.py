from __future__ import annotations

import unittest

from topology_transitions.manifold import analyze_manifold, boundary_components


class ManifoldTests(unittest.TestCase):
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

    def test_wire_and_isolated_vertex_are_separate_exact_issues(self) -> None:
        report = analyze_manifold({0: (0, 1)}, {0: 0}, range(3))
        self.assertEqual(report.wire_edges, frozenset({0}))
        self.assertEqual(report.isolated_vertices, frozenset({2}))
        self.assertEqual(len(report.components), 2)


if __name__ == "__main__":
    unittest.main()
