from __future__ import annotations

import unittest

from topology_transitions.core import TRANSITIONS, TransitionError, template_adjacency
from topology_transitions.ribbon import (
    build_ribbon_plan,
    build_transition_ribbon,
    build_uniform_ribbon,
    ordered_open_edge_chain,
    resample_polyline,
)


class RibbonTests(unittest.TestCase):
    def test_orders_shuffled_open_boundary_chain(self) -> None:
        edge_vertices = {8: (2, 3), 2: (0, 1), 4: (1, 2)}
        self.assertEqual(
            ordered_open_edge_chain(edge_vertices, (8, 2, 4)),
            (0, 1, 2, 3),
        )

    def test_rejects_branch_loop_and_disconnected_edges(self) -> None:
        with self.assertRaisesRegex(TransitionError, "branches"):
            ordered_open_edge_chain({0: (0, 1), 1: (1, 2), 2: (1, 3)}, (0, 1, 2))
        with self.assertRaisesRegex(TransitionError, "closed loop"):
            ordered_open_edge_chain({0: (0, 1), 1: (1, 2), 2: (2, 0)}, (0, 1, 2))
        with self.assertRaisesRegex(TransitionError, "disconnected"):
            ordered_open_edge_chain({0: (0, 1), 1: (2, 3)}, (0, 1))

    def test_uniform_plan_grows_every_lane_as_one_sheet(self) -> None:
        plan = build_uniform_ribbon(4, 3)
        self.assertEqual((plan.input_count, plan.output_count), (4, 4))
        self.assertEqual(len(plan.anchor_keys), 5)
        self.assertEqual(len(plan.output_keys), 5)
        self.assertEqual(len(plan.vertices), 20)
        self.assertEqual(len(plan.faces), 12)
        self.assertTrue(all(len(face) == 4 for face in plan.faces))

    def test_transition_ribbon_has_real_poles_and_unequal_end(self) -> None:
        plan = build_transition_ribbon("FIVE_TO_THREE", 4)
        self.assertEqual((plan.input_count, plan.output_count), (5, 3))
        self.assertEqual(len(plan.anchor_keys), 6)
        self.assertEqual(len(plan.output_keys), 4)
        self.assertEqual(len(plan.pole_keys), 2)
        adjacency = template_adjacency(
            type("Template", (), {"vertices": plan.vertices, "faces": plan.faces})()
        )
        self.assertEqual({len(adjacency[key]) for key in plan.pole_keys}, {3})

    def test_every_transition_can_be_drawn_from_its_input_edge_count(self) -> None:
        for identifier, (incoming, outgoing, _label) in TRANSITIONS.items():
            with self.subTest(identifier=identifier):
                plan = build_ribbon_plan(
                    "TRANSITION",
                    incoming,
                    3,
                    transition=identifier,
                )
                self.assertEqual(plan.input_count, incoming)
                self.assertEqual(plan.output_count, outgoing)
                self.assertTrue(all(len(face) == 4 for face in plan.faces))

    def test_transition_requires_matching_anchor_lane_count(self) -> None:
        with self.assertRaisesRegex(TransitionError, "5 selected boundary edges"):
            build_ribbon_plan(
                "TRANSITION",
                4,
                3,
                transition="FIVE_TO_THREE",
            )

    def test_resampling_uses_arc_length(self) -> None:
        samples = resample_polyline(((0, 0, 0), (1, 0, 0), (3, 0, 0)), 4)
        self.assertEqual(
            samples,
            (
                (0.0, 0.0, 0.0),
                (1.0, 0.0, 0.0),
                (2.0, 0.0, 0.0),
                (3.0, 0.0, 0.0),
            ),
        )


if __name__ == "__main__":
    unittest.main()
