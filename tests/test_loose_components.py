from __future__ import annotations

import unittest

from topology_transitions.loose_components import (
    detached_face_components,
    find_face_components,
)


class DetachedFaceComponentTests(unittest.TestCase):
    def test_shared_edges_form_one_component(self) -> None:
        faces = {
            0: (0, 1, 4, 3),
            1: (1, 2, 5, 4),
            2: (3, 4, 7, 6),
            3: (4, 5, 8, 7),
        }
        self.assertEqual(find_face_components(faces), ((0, 1, 2, 3),))
        self.assertEqual(detached_face_components(faces), ())

    def test_vertex_touch_does_not_count_as_proper_connection(self) -> None:
        faces = {
            0: (0, 1, 2, 3),
            1: (3, 4, 5),
        }
        self.assertEqual(find_face_components(faces), ((0,), (1,)))

    def test_largest_region_is_main_and_small_regions_come_first(self) -> None:
        faces = {
            0: (0, 1, 4, 3),
            1: (1, 2, 5, 4),
            2: (3, 4, 7, 6),
            3: (4, 5, 8, 7),
            10: (20, 21, 22),
            20: (30, 31, 34, 33),
            21: (31, 32, 35, 34),
        }
        self.assertEqual(
            detached_face_components(faces),
            ((10,), (20, 21)),
        )

    def test_equal_size_main_region_is_deterministic(self) -> None:
        faces = {
            5: (0, 1, 2),
            9: (3, 4, 5),
        }
        self.assertEqual(detached_face_components(faces), ((9,),))


if __name__ == "__main__":
    unittest.main()
