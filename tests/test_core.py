from __future__ import annotations

import unittest

from topology_transitions.core import (
    TRANSITIONS,
    TransitionError,
    build_transition_template,
    choose_pole_slot,
    template_adjacency,
    template_edges,
    validate_template,
)


class TransitionTemplateTests(unittest.TestCase):
    def _build_for_rectangular_patch(
        self,
        incoming: int,
        outgoing: int,
        height: int = 2,
        extra_side: str = "LEFT",
        **kwargs,
    ):
        difference = abs(incoming - outgoing)
        if difference == 2:
            left = right = height + 1
        else:
            left = height + (1 if extra_side == "LEFT" else 0)
            right = height + (1 if extra_side == "RIGHT" else 0)
        return build_transition_template(incoming, outgoing, left, right, **kwargs)

    def test_all_declared_presets_build_as_quad_disks(self):
        for identifier, (incoming, outgoing, _label) in TRANSITIONS.items():
            with self.subTest(identifier=identifier):
                template = self._build_for_rectangular_patch(incoming, outgoing)
                validate_template(template)
                self.assertTrue(all(len(face) == 4 for face in template.faces))
                edges = template_edges(template.faces)
                self.assertTrue(all(count in {1, 2} for count in edges.values()))
                self.assertEqual(
                    len(template.vertices) - len(edges) + len(template.faces), 1
                )

    def test_minimum_one_row_patch_builds_for_every_preset(self):
        for identifier, (incoming, outgoing, _label) in TRANSITIONS.items():
            with self.subTest(identifier=identifier):
                template = self._build_for_rectangular_patch(
                    incoming, outgoing, height=1
                )
                validate_template(template)

    def test_two_loop_patterns_have_two_valence_three_poles(self):
        for incoming, outgoing in ((5, 3), (3, 5), (3, 1), (1, 3), (4, 2), (2, 4)):
            with self.subTest(transition=(incoming, outgoing)):
                template = self._build_for_rectangular_patch(incoming, outgoing)
                adjacency = template_adjacency(template)
                self.assertEqual(len(template.pole_keys), 2)
                self.assertEqual(
                    {len(adjacency[key]) for key in template.pole_keys}, {3}
                )

    def test_one_loop_patterns_have_one_valence_three_pole(self):
        for incoming, outgoing in ((1, 2), (2, 1)):
            with self.subTest(transition=(incoming, outgoing)):
                template = self._build_for_rectangular_patch(incoming, outgoing)
                adjacency = template_adjacency(template)
                self.assertEqual(len(template.pole_keys), 1)
                self.assertEqual(len(adjacency[next(iter(template.pole_keys))]), 3)

    def test_boundary_edge_count_matches_declared_sides(self):
        template = build_transition_template(5, 3, 4, 4)
        boundary_edges = sum(
            count == 1 for count in template_edges(template.faces).values()
        )
        self.assertEqual(boundary_edges, 5 + 3 + 4 + 4)
        self.assertEqual(boundary_edges, template.boundary_edge_count)

    def test_transition_face_counts_are_deterministic(self):
        self.assertEqual(len(self._build_for_rectangular_patch(5, 3).faces), 16)
        self.assertEqual(len(self._build_for_rectangular_patch(3, 5).faces), 16)
        self.assertEqual(len(self._build_for_rectangular_patch(1, 2).faces), 5)
        self.assertEqual(len(self._build_for_rectangular_patch(2, 1).faces), 5)
        self.assertEqual(
            len(self._build_for_rectangular_patch(5, 3, height=1).faces), 11
        )
        self.assertEqual(
            len(self._build_for_rectangular_patch(1, 2, height=1).faces), 3
        )

    def test_pole_slot_control_and_mirror(self):
        self.assertEqual(choose_pole_slot(5, "LEFT"), 0)
        self.assertEqual(choose_pole_slot(5, "CENTER"), 1)
        self.assertEqual(choose_pole_slot(5, "RIGHT"), 2)
        self.assertEqual(choose_pole_slot(5, "LEFT", mirror=True), 2)
        self.assertEqual(choose_pole_slot(4, "CENTER", mirror=True), 1)

    def test_invalid_side_parity_is_rejected(self):
        with self.assertRaisesRegex(TransitionError, "equal left and right"):
            build_transition_template(5, 3, 3, 4)
        with self.assertRaisesRegex(TransitionError, "compensating side edge"):
            build_transition_template(1, 2, 3, 3)

    def test_unsupported_count_difference_is_rejected(self):
        with self.assertRaisesRegex(TransitionError, "differences of one or two"):
            build_transition_template(6, 2, 3, 3)

    def test_mirrored_asymmetric_cell_requires_mirrored_boundary(self):
        with self.assertRaisesRegex(TransitionError, "boundary shoulder"):
            build_transition_template(1, 2, 3, 2, mirror=True)


if __name__ == "__main__":
    unittest.main()
