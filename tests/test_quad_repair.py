"""Tests for Blender-independent quad-repair planning."""

from __future__ import annotations

import unittest

from topology_transitions.quad_repair import (
    best_quad_fan,
    polygon_signed_area,
    quad_fan_candidates,
)


class QuadRepairTests(unittest.TestCase):
    def test_even_polygon_fans_have_only_quads(self) -> None:
        candidates = quad_fan_candidates(8)
        self.assertEqual(len(candidates), 8)
        self.assertTrue(all(len(candidate) == 3 for candidate in candidates))
        self.assertTrue(
            all(len(quad) == 4 for candidate in candidates for quad in candidate)
        )

    def test_odd_polygon_has_no_boundary_preserving_fan(self) -> None:
        self.assertEqual(quad_fan_candidates(5), ())
        self.assertIsNone(
            best_quad_fan(((0.0, 0.0), (2.0, 0.0), (2.0, 1.0), (1.0, 2.0), (0.0, 1.0)))
        )

    def test_convex_hexagon_becomes_two_quads(self) -> None:
        points = (
            (0.0, 0.0),
            (1.0, -0.25),
            (2.0, 0.0),
            (2.0, 1.0),
            (1.0, 1.25),
            (0.0, 1.0),
        )
        fan = best_quad_fan(points)
        self.assertIsNotNone(fan)
        self.assertEqual(len(fan), 2)
        self.assertTrue(
            all(
                polygon_signed_area([points[index] for index in quad]) > 0
                for quad in fan
            )
        )

    def test_concave_even_polygon_uses_only_valid_diagonals(self) -> None:
        points = (
            (0.0, 0.0),
            (3.0, 0.0),
            (3.0, 3.0),
            (2.0, 2.5),
            (1.0, 3.0),
            (0.0, 3.0),
        )
        fan = best_quad_fan(points)
        self.assertIsNotNone(fan)
        self.assertEqual(len(fan), 2)

    def test_self_intersecting_polygon_is_rejected(self) -> None:
        bow_tie = ((0.0, 0.0), (2.0, 2.0), (0.0, 2.0), (2.0, 0.0))
        self.assertIsNone(best_quad_fan(bow_tie))


if __name__ == "__main__":
    unittest.main()
