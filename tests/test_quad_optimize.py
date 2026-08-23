"""Tests for deterministic quad-flow optimization planning."""

from __future__ import annotations

import unittest

from topology_transitions.quad_optimize import LoopChoice, choose_loop_batch


def choice(
    identifier: int,
    conflicts: set[int],
    *,
    error: float = 0.0,
    curvature: float = 0.0,
) -> LoopChoice:
    return LoopChoice(
        identifier=identifier,
        conflict_ids=frozenset(conflicts),
        error_ratio=error,
        curvature=curvature,
        edge_count=8,
        stable_index=identifier,
    )


class QuadOptimizeTests(unittest.TestCase):
    def test_alternating_parallel_flows_are_selected(self) -> None:
        candidates = (
            choice(0, {1}),
            choice(1, {0, 2}),
            choice(2, {1, 3}),
            choice(3, {2}),
        )
        self.assertEqual(choose_loop_batch(candidates), (0, 2))

    def test_perpendicular_nonconflicting_flows_share_a_pass(self) -> None:
        candidates = (choice(0, set()), choice(1, set()), choice(2, set()))
        self.assertEqual(choose_loop_batch(candidates), (0, 1, 2))

    def test_lower_error_flow_wins_a_conflict(self) -> None:
        candidates = (
            choice(0, {1}, error=0.2),
            choice(1, {0}, error=0.01),
        )
        self.assertEqual(choose_loop_batch(candidates), (1,))

    def test_cycle_uses_maximum_alternating_set(self) -> None:
        candidates = (
            choice(0, {1, 3}),
            choice(1, {0, 2}),
            choice(2, {1, 3}),
            choice(3, {0, 2}),
        )
        self.assertEqual(choose_loop_batch(candidates), (0, 2))

    def test_invalid_conflict_reference_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "conflicts"):
            choose_loop_batch((choice(0, {9}),))

    def test_asymmetric_conflicts_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "symmetric"):
            choose_loop_batch((choice(0, {1}), choice(1, set())))


if __name__ == "__main__":
    unittest.main()
