"""Pure lattice growth for Surface Paint: painted coverage becomes a quad grid.

The user paints coverage dabs on a high-poly surface. A square lattice is then
grown outward from the start of each stroke: each lattice node steps one cell along its
parent's tangent frame, is projected back onto the surface, and carries the
frame across by parallel transport. Nodes outside the painted coverage are not
created, so the painted shape decides where quads exist and each stroke's
direction decides which way its edge loops run. A short tangential relaxation
then evens out the cell shapes that curvature distorts.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from math import cos, floor, sin, sqrt

from .core import TransitionError

Point3 = tuple[float, float, float]
Node = tuple[int, int, int]  # (patch, i, j)
Projector = Callable[[Point3], "tuple[Point3, Point3] | None"]

STEPS: tuple[tuple[int, int], ...] = ((1, 0), (-1, 0), (0, 1), (0, -1))
MAX_NODES = 20000
MIN_NORMAL_AGREEMENT = 0.35
COLLISION_FACTOR = 0.45


def _add(a: Point3, b: Point3) -> Point3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a: Point3, b: Point3) -> Point3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _scale(a: Point3, factor: float) -> Point3:
    return (a[0] * factor, a[1] * factor, a[2] * factor)


def _dot(a: Point3, b: Point3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Point3, b: Point3) -> Point3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _length(a: Point3) -> float:
    return sqrt(_dot(a, a))


def _normalized(a: Point3) -> Point3 | None:
    length = _length(a)
    if length <= 1.0e-12:
        return None
    return _scale(a, 1.0 / length)


def _tangent(direction: Point3, normal: Point3) -> Point3 | None:
    """Remove the normal component of ``direction`` (parallel transport step)."""

    return _normalized(_sub(direction, _scale(normal, _dot(direction, normal))))


@dataclass(frozen=True)
class Dab:
    center: Point3
    radius: float
    erase: bool = False


@dataclass(frozen=True)
class Seed:
    point: Point3
    normal: Point3
    flow: Point3


@dataclass(frozen=True)
class PaintGrid:
    positions: dict[Node, Point3]
    normals: dict[Node, Point3]
    faces: tuple[tuple[Node, Node, Node, Node], ...]
    cell: float
    rejected_folds: int
    rejected_overlaps: int

    @property
    def boundary_nodes(self) -> frozenset[Node]:
        return frozenset(
            node
            for node in self.positions
            if any(
                (node[0] + di, node[1] + dj) not in self.positions for di, dj in STEPS
            )
        )


class Coverage:
    """Union of paint dabs minus erase dabs, tested in painting order."""

    def __init__(self, dabs: Sequence[Dab]) -> None:
        self.dabs = tuple(dabs)

    def contains(self, point: Point3) -> bool:
        inside = False
        for dab in self.dabs:
            if _length(_sub(point, dab.center)) <= dab.radius:
                inside = not dab.erase
        return inside


class _SpatialHash:
    def __init__(self, cell: float) -> None:
        self.cell = cell
        self.buckets: dict[tuple[int, int, int], list[Point3]] = {}

    def _key(self, point: Point3) -> tuple[int, int, int]:
        return tuple(floor(value / self.cell) for value in point)  # type: ignore

    def add(self, point: Point3) -> None:
        self.buckets.setdefault(self._key(point), []).append(point)

    def near(self, point: Point3, radius: float, ignore: Sequence[Point3]) -> bool:
        kx, ky, kz = self._key(point)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for other in self.buckets.get((kx + dx, ky + dy, kz + dz), ()):
                        if other in ignore:
                            continue
                        if _length(_sub(point, other)) < radius:
                            return True
        return False


def _shift(node: Node, di: int, dj: int) -> Node:
    return (node[0], node[1] + di, node[2] + dj)


def _frame(
    normal: Point3, flow_direction: Point3, flow_angle: float
) -> tuple[Point3, Point3]:
    u = _tangent(flow_direction, normal)
    if u is None:
        u = _tangent((1.0, 0.0, 0.0), normal) or _tangent((0.0, 1.0, 0.0), normal)
    v = _cross(normal, u)
    if flow_angle:
        u, v = (
            _add(_scale(u, cos(flow_angle)), _scale(v, sin(flow_angle))),
            _sub(_scale(v, cos(flow_angle)), _scale(u, sin(flow_angle))),
        )
    return u, v


def grow_paint_grid(
    dabs: Sequence[Dab],
    *,
    cell: float,
    seeds: Sequence[Seed],
    flow_angle: float,
    project: Projector,
    relax_iterations: int = 3,
    max_nodes: int = MAX_NODES,
) -> PaintGrid:
    """Grow surface-following square lattices inside the painted coverage.

    Every seed (one per paint stroke) starts its own lattice patch unless an
    earlier patch already reached it, so separate painted areas all fill.
    """

    if cell <= 1.0e-9:
        raise TransitionError("Surface Paint needs a positive cell size")
    coverage = Coverage(dabs)
    positions: dict[Node, Point3] = {}
    normals: dict[Node, Point3] = {}
    frames: dict[Node, tuple[Point3, Point3]] = {}
    occupied = _SpatialHash(cell)
    folds = overlaps = 0
    for patch, seed in enumerate(seeds):
        start = project(seed.point)
        if start is None:
            continue
        origin, normal = start
        normal = _normalized(normal) or _normalized(seed.normal)
        if (
            normal is None
            or not coverage.contains(origin)
            or occupied.near(origin, cell, ())
        ):
            continue
        root = (patch, 0, 0)
        positions[root] = origin
        normals[root] = normal
        frames[root] = _frame(normal, seed.flow, flow_angle)
        occupied.add(origin)
        queue: deque[Node] = deque([root])
        visited = {root}
        while queue:
            node = queue.popleft()
            base = positions[node]
            base_u, base_v = frames[node]
            for di, dj in STEPS:
                neighbor = _shift(node, di, dj)
                if neighbor in visited:
                    continue
                step = _add(_scale(base_u, di * cell), _scale(base_v, dj * cell))
                hit = project(_add(base, step))
                if hit is None:
                    continue
                point, hit_normal = hit
                hit_normal = _normalized(hit_normal)
                if not coverage.contains(point):
                    continue
                visited.add(neighbor)
                if hit_normal is None or _dot(hit_normal, normals[node]) < (
                    MIN_NORMAL_AGREEMENT
                ):
                    folds += 1
                    continue
                lattice_neighbors = [
                    positions[_shift(neighbor, a, b)]
                    for a, b in STEPS
                    if _shift(neighbor, a, b) in positions
                ]
                # Curvature squeezes or stretches cells far from the seed; such
                # a node would make a sliver quad, so the lattice stops there.
                if any(
                    not 0.5 * cell <= _length(_sub(point, other)) <= 2.0 * cell
                    for other in lattice_neighbors
                ):
                    folds += 1
                    continue
                # ponytail: where two lattice fronts meet (two patches, or one
                # wrapped around a cylinder) they stop at an open seam; Weld
                # closes it only where vertices happen to coincide.
                if occupied.near(point, cell * COLLISION_FACTOR, lattice_neighbors):
                    overlaps += 1
                    continue
                new_u = _tangent(base_u, hit_normal)
                if new_u is None:
                    folds += 1
                    continue
                positions[neighbor] = point
                normals[neighbor] = hit_normal
                frames[neighbor] = (new_u, _cross(hit_normal, new_u))
                occupied.add(point)
                queue.append(neighbor)
                if len(positions) > max_nodes:
                    raise TransitionError(
                        f"Painted area needs more than {max_nodes} vertices; "
                        "increase the cell size"
                    )
    if not positions:
        raise TransitionError("No painted stroke starts on the surface target")

    faces = tuple(
        (node, _shift(node, 1, 0), _shift(node, 1, 1), _shift(node, 0, 1))
        for node in sorted(positions)
        if _shift(node, 1, 0) in positions
        and _shift(node, 1, 1) in positions
        and _shift(node, 0, 1) in positions
    )
    # Keep only nodes that belong to a face so no wire vertices are created.
    used = {node for face in faces for node in face}
    positions = {node: positions[node] for node in sorted(used)}
    normals = {node: normals[node] for node in positions}
    grid = PaintGrid(positions, normals, faces, cell, folds, overlaps)
    if relax_iterations > 0 and faces:
        grid = relax_grid(grid, project, relax_iterations)
    return grid


def relax_grid(grid: PaintGrid, project: Projector, iterations: int) -> PaintGrid:
    """Average interior nodes toward their four lattice neighbours, on-surface."""

    positions = dict(grid.positions)
    normals = dict(grid.normals)
    boundary = grid.boundary_nodes
    interior = [node for node in positions if node not in boundary]
    for _ in range(iterations):
        updated = {}
        for node in interior:
            neighbors = [positions[_shift(node, a, b)] for a, b in STEPS]
            average = _scale(
                _add(
                    _add(neighbors[0], neighbors[1]), _add(neighbors[2], neighbors[3])
                ),
                0.25,
            )
            hit = project(_add(_scale(positions[node], 0.5), _scale(average, 0.5)))
            if hit is not None:
                updated[node] = hit
        for node, (point, normal) in updated.items():
            positions[node] = point
            normals[node] = _normalized(normal) or normals[node]
    return PaintGrid(
        positions,
        normals,
        grid.faces,
        grid.cell,
        grid.rejected_folds,
        grid.rejected_overlaps,
    )


def grid_report(grid: PaintGrid) -> dict[str, int]:
    """Counts that explain the result: grid quads only have poles at corners."""

    valence: dict[Node, int] = {}
    for face in grid.faces:
        for first, second in zip(face, (*face[1:], face[0]), strict=True):
            edge = tuple(sorted((first, second)))
            valence.setdefault(edge, 0)  # type: ignore[arg-type]
    vertex_valence: dict[Node, int] = {}
    for first, second in valence:  # type: ignore[misc]
        vertex_valence[first] = vertex_valence.get(first, 0) + 1
        vertex_valence[second] = vertex_valence.get(second, 0) + 1
    boundary = grid.boundary_nodes
    interior_poles = sum(
        1
        for node, count in vertex_valence.items()
        if count != 4 and node not in boundary
    )
    return {
        "quads": len(grid.faces),
        "vertices": len(grid.positions),
        "boundary_vertices": len(boundary & set(grid.positions)),
        "interior_poles": interior_poles,
        "rejected_folds": grid.rejected_folds,
        "rejected_overlaps": grid.rejected_overlaps,
    }
