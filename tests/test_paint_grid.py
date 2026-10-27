from __future__ import annotations

import sys
import unittest
from math import pi, sqrt
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from topology_transitions.core import TransitionError  # noqa: E402
from topology_transitions.paint_grid import (  # noqa: E402
    Dab,
    Seed,
    grid_report,
    grow_paint_grid,
)


def plane(point):
    return (point[0], point[1], 0.0), (0.0, 0.0, 1.0)


def sphere(point):
    length = sqrt(sum(value * value for value in point))
    unit = tuple(value / length for value in point)
    return unit, unit


def edges_of(faces):
    counts = {}
    for face in faces:
        for first, second in zip(face, (*face[1:], face[0])):
            edge = tuple(sorted((first, second)))
            counts[edge] = counts.get(edge, 0) + 1
    return counts


class PaintGridTests(unittest.TestCase):
    def test_disk_on_plane_becomes_manifold_quad_grid(self) -> None:
        grid = grow_paint_grid(
            [Dab((0.0, 0.0, 0.0), 1.0)],
            cell=0.1,
            seeds=[Seed((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0))],
            flow_angle=0.0,
            project=plane,
        )
        self.assertAlmostEqual(len(grid.faces), pi * 100, delta=40)
        self.assertTrue(all(count <= 2 for count in edges_of(grid.faces).values()))
        self.assertEqual(grid_report(grid)["interior_poles"], 0)
        for x, y, z in grid.positions.values():
            self.assertAlmostEqual(z, 0.0)
            self.assertLessEqual(x * x + y * y, 1.0 + 1.0e-9)

    def test_flow_direction_orients_edge_loops(self) -> None:
        grid = grow_paint_grid(
            [Dab((0.0, 0.0, 0.0), 0.5)],
            cell=0.1,
            seeds=[Seed((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 1.0, 0.0))],
            flow_angle=0.0,
            project=plane,
            relax_iterations=0,
        )
        x, y, _ = grid.positions[(0, 1, 0)]
        self.assertAlmostEqual(x, y)

    def test_erase_dab_cuts_a_hole(self) -> None:
        kwargs = dict(
            cell=0.1,
            seeds=[Seed((-0.8, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0))],
            flow_angle=0.0,
            project=plane,
        )
        full = grow_paint_grid([Dab((0.0, 0.0, 0.0), 1.0)], **kwargs)
        holed = grow_paint_grid(
            [Dab((0.0, 0.0, 0.0), 1.0), Dab((0.0, 0.0, 0.0), 0.3, erase=True)],
            **kwargs,
        )
        self.assertLess(len(holed.faces), len(full.faces) - 20)

    def test_sphere_cap_stays_on_surface_and_faces_outward(self) -> None:
        grid = grow_paint_grid(
            [Dab((0.0, 0.0, 1.0), 0.8)],
            cell=0.08,
            seeds=[Seed((0.0, 0.0, 1.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0))],
            flow_angle=0.0,
            project=sphere,
        )
        self.assertGreater(len(grid.faces), 100)
        for position in grid.positions.values():
            self.assertAlmostEqual(sqrt(sum(v * v for v in position)), 1.0)
        for face in grid.faces:
            a, b, c, _d = (grid.positions[node] for node in face)
            ab = tuple(q - p for p, q in zip(a, b))
            ac = tuple(q - p for p, q in zip(a, c))
            normal = (
                ab[1] * ac[2] - ab[2] * ac[1],
                ab[2] * ac[0] - ab[0] * ac[2],
                ab[0] * ac[1] - ab[1] * ac[0],
            )
            self.assertGreater(sum(n * p for n, p in zip(normal, a)), 0.0)

    def test_whole_sphere_stops_at_seam_without_overlap(self) -> None:
        grid = grow_paint_grid(
            [Dab((0.0, 0.0, 0.0), 2.0)],
            cell=0.2,
            seeds=[Seed((0.0, 0.0, 1.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0))],
            flow_angle=0.0,
            project=sphere,
        )
        self.assertTrue(all(count <= 2 for count in edges_of(grid.faces).values()))
        points = list(grid.positions.values())
        for index, first in enumerate(points):
            for second in points[index + 1 :]:
                distance = sqrt(sum((a - b) ** 2 for a, b in zip(first, second)))
                self.assertGreater(distance, 0.2 * 0.3)

    def test_separate_strokes_each_get_a_patch(self) -> None:
        dabs = [Dab((0.0, 0.0, 0.0), 0.5), Dab((3.0, 0.0, 0.0), 0.5)]
        seeds = [
            Seed((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)),
            Seed((3.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 1.0, 0.0)),
            Seed((0.1, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)),
        ]
        grid = grow_paint_grid(
            dabs, cell=0.1, seeds=seeds, flow_angle=0.0, project=plane
        )
        patches = {node[0] for node in grid.positions}
        self.assertEqual(patches, {0, 1})
        x, y, _ = grid.positions[(1, 1, 0)]
        self.assertAlmostEqual(x - 3.0, y)

    def test_erased_seed_is_rejected(self) -> None:
        with self.assertRaises(TransitionError):
            grow_paint_grid(
                [Dab((0.0, 0.0, 0.0), 1.0), Dab((0.0, 0.0, 0.0), 2.0, erase=True)],
                cell=0.1,
                seeds=[Seed((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0))],
                flow_angle=0.0,
                project=plane,
            )


if __name__ == "__main__":
    unittest.main()
