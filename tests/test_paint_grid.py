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
    Mirror,
    Seed,
    _bad_face,
    clean_faces,
    edge_counts,
    grid_report,
    grow_paint_grid,
)


def plane(point):
    return (point[0], point[1], 0.0), (0.0, 0.0, 1.0)


def sphere(point):
    length = sqrt(sum(value * value for value in point))
    unit = tuple(value / length for value in point)
    return unit, unit


def roof(point):
    """A sharp 90-degree ridge along Y: z = -|x|."""

    x, y, z = point
    best = None
    for sign in (1.0, -1.0):
        normal = (sign * -0.7071067811865476, 0.0, 0.7071067811865476)
        offset = x * normal[0] + z * normal[2]
        foot = (x - offset * normal[0], y, z - offset * normal[2])
        if sign * foot[0] < 0.0:
            foot = (0.0, y, 0.0)
        distance = sqrt(sum((a - b) ** 2 for a, b in zip(point, foot)))
        if best is None or distance < best[0]:
            best = (distance, foot, normal if foot[0] else (0.0, 0.0, 1.0))
    return best[1], best[2]


def manifold(faces):
    return all(count <= 2 for count in edge_counts(faces).values())


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
        # Cells need all four corners inside the disk: about pi * (r - 0.7c)^2.
        self.assertAlmostEqual(len(grid.faces), pi * 9.3**2, delta=25)
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

    def test_grid_walks_over_a_sharp_crease_as_one_patch(self) -> None:
        grid = grow_paint_grid(
            [Dab((0.0, 0.0, 0.0), 0.8)],
            cell=0.1,
            seeds=[Seed((-0.3, 0.0, -0.3), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0))],
            flow_angle=0.0,
            project=roof,
            bridge=False,
        )
        left = sum(1 for p in grid.positions.values() if p[0] < -0.05)
        right = sum(1 for p in grid.positions.values() if p[0] > 0.05)
        self.assertGreater(right, left * 0.7)
        self.assertTrue(manifold(grid.faces))
        self.assertLess(grid.rejected_folds, 10)

    def test_clean_faces_removes_spur_and_corner_contact(self) -> None:
        block = [
            ((0, i, j), (0, i + 1, j), (0, i + 1, j + 1), (0, i, j + 1))
            for i in range(3)
            for j in range(3)
        ]
        spur = ((0, 3, 1), (0, 4, 1), (0, 4, 2), (0, 3, 2))
        corner = ((0, 3, 3), (0, 4, 3), (0, 4, 4), (0, 3, 4))
        cleaned, pruned = clean_faces(block + [spur, corner])
        self.assertEqual(pruned, 2)
        self.assertEqual(sorted(cleaned), sorted(block))

    def test_neighbouring_patches_are_bridged_with_transitions(self) -> None:
        grid = grow_paint_grid(
            [Dab((0.0, 0.0, 0.0), 0.5), Dab((1.25, 0.0, 0.0), 0.6)],
            cell=0.1,
            seeds=[
                Seed((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)),
                Seed((1.25, 0.03, 0.0), (0.0, 0.0, 1.0), (1.0, 0.3, 0.0)),
            ],
            flow_angle=0.0,
            project=plane,
        )
        self.assertEqual(grid.bridges, 1)
        self.assertGreater(grid_report(grid)["interior_poles"], 0)
        self.assertTrue(manifold(grid.faces))
        self.assertFalse(
            any(
                _bad_face(face, grid.positions, grid.normals, 0.1)
                for face in grid.faces
            )
        )

    def test_mirror_reflects_and_shares_the_seam(self) -> None:
        grid = grow_paint_grid(
            [Dab((0.3, 0.0, 0.0), 0.5)],
            cell=0.1,
            seeds=[Seed((0.3, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, 1.0, 0.0))],
            flow_angle=0.0,
            project=plane,
            mirror=Mirror((0.0, 0.0, 0.0), (1.0, 0.0, 0.0)),
        )
        xs = [p[0] for p in grid.positions.values()]
        self.assertAlmostEqual(min(xs), -max(xs), places=6)
        seam = [x for x in xs if abs(x) < 1.0e-9]
        self.assertGreater(len(seam), 3)
        self.assertTrue(manifold(grid.faces))
        counts = edge_counts(grid.faces)
        on_seam = [
            edge
            for edge in counts
            if all(abs(grid.positions[n][0]) < 1.0e-9 for n in edge)
        ]
        self.assertTrue(on_seam)

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
