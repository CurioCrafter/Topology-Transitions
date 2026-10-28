"""Pure lattice growth for Surface Paint: painted coverage becomes a quad grid.

The user paints coverage dabs on a high-poly surface. A square lattice is then
grown outward from the start of each stroke. Every lattice step walks one cell
across the surface in small sub-steps, re-projecting and re-aiming after each,
so the lattice bends over creases instead of cutting through them; the tangent
frame is carried along by parallel transport. Nodes outside the painted
coverage are not created, so the painted shape decides where quads exist and
each stroke's direction decides which way its edge loops run.

After growth the result is cleaned (spur cells and corner-only contacts are
removed), neighbouring patches are joined with the add-on's own loop-count
transition templates, every vertex is relaxed on the surface (border vertices
along the border), and the patch is optionally mirrored across a plane.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass
from math import cos, floor, pi, sin, sqrt

from .core import TransitionError, build_transition_template

Point3 = tuple[float, float, float]
Node = Hashable  # lattice nodes are (patch, i, j); bridge/mirror nodes are tagged
Face = tuple[Node, Node, Node, Node]
Projector = Callable[[Point3], "tuple[Point3, Point3] | None"]

STEPS: tuple[tuple[int, int], ...] = ((1, 0), (-1, 0), (0, 1), (0, -1))
MAX_NODES = 20000
SUBSTEPS = 4
FOLD_LIMIT = -0.2
COLLISION_FACTOR = 0.45
MIN_STRETCH = 0.35
DAB_DEPTH = 1.5
MAX_STRETCH = 2.0
BRIDGE_REACH = 4.5
FILL_GAP = 1.5
MIN_PIECE_FACES = 6
GROWING_PIECE_FACES = 4
INTERIOR_RELAX = 0.5
BORDER_RELAX = 0.35


def _add(a: Point3, b: Point3) -> Point3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a: Point3, b: Point3) -> Point3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _scale(a: Point3, factor: float) -> Point3:
    return (a[0] * factor, a[1] * factor, a[2] * factor)


def _lerp(a: Point3, b: Point3, t: float) -> Point3:
    return _add(a, _scale(_sub(b, a), t))


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


def _distance(a: Point3, b: Point3) -> float:
    return _length(_sub(a, b))


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
    """One brush dab. With a ``normal`` it covers a disc-shaped column along
    that normal (what the brush circle sweeps over, including the floor of a
    socket or crease); without one it covers a sphere."""

    center: Point3
    radius: float
    erase: bool = False
    normal: Point3 | None = None

    def covers(self, point: Point3) -> bool:
        offset = _sub(point, self.center)
        if self.normal is None:
            return _length(offset) <= self.radius
        depth = _dot(offset, self.normal)
        if abs(depth) > DAB_DEPTH * self.radius:
            return False
        return _length(_sub(offset, _scale(self.normal, depth))) <= self.radius


@dataclass(frozen=True)
class Seed:
    point: Point3
    normal: Point3
    flow: Point3


@dataclass(frozen=True)
class Mirror:
    """Mirror plane; geometry on the negative side is replaced by a reflection."""

    origin: Point3
    normal: Point3


@dataclass(frozen=True)
class PaintGrid:
    positions: dict[Node, Point3]
    normals: dict[Node, Point3]
    faces: tuple[Face, ...]
    cell: float
    rejected_folds: int = 0
    rejected_overlaps: int = 0
    bridges: int = 0
    pruned_faces: int = 0

    @property
    def boundary_nodes(self) -> frozenset[Node]:
        return frozenset(
            node
            for edge, count in edge_counts(self.faces).items()
            if count == 1
            for node in edge
        )


def _edge(first: Node, second: Node) -> tuple[Node, Node]:
    return (first, second) if repr(first) <= repr(second) else (second, first)


def face_edges(face: Face) -> tuple[tuple[Node, Node], ...]:
    return tuple(
        _edge(first, second)
        for first, second in zip(face, (*face[1:], face[0]), strict=True)
    )


def edge_counts(faces: Sequence[Face]) -> dict[tuple[Node, Node], int]:
    counts: dict[tuple[Node, Node], int] = {}
    for face in faces:
        for edge in face_edges(face):
            counts[edge] = counts.get(edge, 0) + 1
    return counts


def _neighbors(faces: Sequence[Face]) -> dict[Node, set[Node]]:
    result: dict[Node, set[Node]] = {}
    for first, second in edge_counts(faces):
        result.setdefault(first, set()).add(second)
        result.setdefault(second, set()).add(first)
    return result


class Coverage:
    """Union of paint dabs minus erase dabs, tested in painting order."""

    def __init__(self, dabs: Sequence[Dab]) -> None:
        self.dabs = tuple(dabs)

    def contains(self, point: Point3) -> bool:
        inside = False
        for dab in self.dabs:
            if dab.covers(point):
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

    def remove(self, point: Point3) -> None:
        self.buckets[self._key(point)].remove(point)

    def near(self, point: Point3, radius: float, ignore: Sequence[Point3]) -> bool:
        kx, ky, kz = self._key(point)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for other in self.buckets.get((kx + dx, ky + dy, kz + dz), ()):
                        if other not in ignore and _distance(point, other) < radius:
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


def _walk(
    start: Point3,
    normal: Point3,
    heading: Point3,
    frame_u: Point3,
    cell: float,
    project: Projector,
) -> tuple[Point3, Point3, Point3] | str | None:
    """Walk one cell over the surface; returns (point, normal, transported u).

    Each sub-step moves a fraction of the cell along the current heading,
    projects back to the surface, then re-aims the heading along the surface
    actually travelled. Over a crease the walk turns with the surface, which a
    single straight step would cut through. ``"fold"`` means the surface turned
    back on itself (the far side of a thin part).
    """

    point, current_normal, current_u = start, normal, frame_u
    for _ in range(SUBSTEPS):
        hit = project(_add(point, _scale(heading, cell / SUBSTEPS)))
        if hit is None:
            return None
        next_point, next_normal = hit
        next_normal = _normalized(next_normal)
        if next_normal is None or _dot(next_normal, current_normal) < FOLD_LIMIT:
            return "fold"
        travelled = _sub(next_point, point)
        heading = _tangent(travelled, next_normal) or _tangent(heading, next_normal)
        current_u = _tangent(current_u, next_normal)
        if heading is None or current_u is None:
            return "fold"
        point, current_normal = next_point, next_normal
    return point, current_normal, current_u


def _grow(
    coverage: Coverage,
    seeds: Sequence[Seed],
    cell: float,
    flow_angle: float,
    project: Projector,
    max_nodes: int,
) -> tuple[dict, dict, int, int]:
    """Spine-and-rib lattice per seed, then outward fill of what ribs missed.

    The spine walks across the surface along the flow direction from the seed;
    every spine node sends ribs out at right angles. Each node therefore has
    exactly one walk that defines it, so curvature cannot make two paths
    disagree about where a node is (the weakness of plain outward growth,
    which only fills the leftovers here).
    """

    positions: dict[Node, Point3] = {}
    normals: dict[Node, Point3] = {}
    frames: dict[Node, tuple[Point3, Point3]] = {}
    occupied = _SpatialHash(cell)
    stats = {"folds": 0, "overlaps": 0}
    visited: set[Node] = set()

    def place(node: Node, di: int, dj: int) -> Node | None:
        neighbor = _shift(node, di, dj)
        if neighbor in visited:
            return None
        base_u, base_v = frames[node]
        heading = _add(_scale(base_u, di), _scale(base_v, dj))
        walked = _walk(positions[node], normals[node], heading, base_u, cell, project)
        if walked is None:
            return None
        if walked == "fold":
            visited.add(neighbor)
            stats["folds"] += 1
            return None
        point, hit_normal, new_u = walked
        if not coverage.contains(point):
            return None
        visited.add(neighbor)
        lattice_neighbors = [
            positions[_shift(neighbor, a, b)]
            for a, b in STEPS
            if _shift(neighbor, a, b) in positions
        ]
        # Strong curvature squeezes or stretches cells; such a node would make
        # a sliver quad, so the lattice stops there.
        if any(
            not MIN_STRETCH * cell <= _distance(point, other) <= MAX_STRETCH * cell
            for other in lattice_neighbors
        ):
            stats["folds"] += 1
            return None
        # Where two lattice fronts meet (two patches, or one wrapped around a
        # cylinder) they stop; bridging joins patch fronts afterwards.
        if occupied.near(point, cell * COLLISION_FACTOR, lattice_neighbors):
            stats["overlaps"] += 1
            return None
        positions[neighbor] = point
        normals[neighbor] = hit_normal
        frames[neighbor] = (new_u, _cross(hit_normal, new_u))
        occupied.add(point)
        if len(positions) > max_nodes:
            raise TransitionError(
                f"Painted area needs more than {max_nodes} vertices; "
                "increase the cell size"
            )
        return neighbor

    def grow_from(patch: int, seed: Seed) -> bool:
        start = project(seed.point)
        if start is None:
            return False
        origin, normal = start
        normal = _normalized(normal) or _normalized(seed.normal)
        if (
            normal is None
            or not coverage.contains(origin)
            or occupied.near(origin, cell, ())
        ):
            return False
        root = (patch, 0, 0)
        positions[root] = origin
        normals[root] = normal
        frames[root] = _frame(normal, seed.flow, flow_angle)
        occupied.add(origin)
        visited.add(root)
        spine = [root]
        for di in (1, -1):
            node = root
            while (node := place(node, di, 0)) is not None:
                spine.append(node)
        for spine_node in spine:
            for dj in (1, -1):
                node = spine_node
                while (node := place(node, 0, dj)) is not None:
                    pass
        queue: deque[Node] = deque(node for node in positions if node[0] == patch)
        while queue:
            node = queue.popleft()
            for di, dj in STEPS:
                placed = place(node, di, dj)
                if placed is not None:
                    queue.append(placed)
        return True

    def drop_strings(patches: set[int]) -> None:
        # Spines, ribs and spurs that will not survive cleanup would block the
        # fill pass from starting patches where they lie. Patches never share
        # nodes, so only the given ones need cleaning.
        nodes = {node: point for node, point in positions.items() if node[0] in patches}
        faced = _faced_nodes(nodes)
        for node, point in nodes.items():
            if node not in faced:
                del positions[node], normals[node], frames[node]
                occupied.remove(point)

    for patch, seed in enumerate(seeds):
        grow_from(patch, seed)
    drop_strings(set(range(len(seeds))))
    # The coach card explains where the strokes' own lattices stopped; the
    # fill pass below tries many starts, which would drown that number.
    folds, overlaps = stats["folds"], stats["overlaps"]
    # A lattice cannot walk across a gap in the surface (an eyeball inside its
    # socket, a separate shell), so painted surface the stroke seeds never
    # reached gets its own patch, oriented like the nearest stroke; joining
    # connects the patches afterwards.
    patch = len(seeds)
    for dab in coverage.dabs:
        if dab.erase:
            continue
        for point in _dab_samples(dab, cell):
            if occupied.near(point, cell * FILL_GAP, ()):
                continue
            nearest = min(seeds, key=lambda seed: _distance(seed.point, point))
            normal = dab.normal or nearest.normal
            if grow_from(patch, Seed(point, normal, nearest.flow)):
                drop_strings({patch})
                patch += 1
    return positions, normals, folds, overlaps


def _lattice_faces(positions: dict[Node, Point3]) -> list[Face]:
    return [
        (node, _shift(node, 1, 0), _shift(node, 1, 1), _shift(node, 0, 1))
        for node in sorted(positions)
        if _shift(node, 1, 0) in positions
        and _shift(node, 1, 1) in positions
        and _shift(node, 0, 1) in positions
    ]


def _faced_nodes(positions: dict[Node, Point3]) -> set[Node]:
    """Nodes that survive cleanup. Repeated until stable: the corners left by
    one cleanup can form new lattice cells that need cleaning again."""

    nodes = set(positions)
    while True:
        faces, _pruned = clean_faces(_lattice_faces(dict.fromkeys(nodes)))
        # Growing pieces keep a lower bar than the final result: a small piece
        # may still be joined to its neighbours.
        faces, _small = drop_small_pieces(faces, GROWING_PIECE_FACES)
        kept = {node for face in faces for node in face}
        if kept == nodes:
            return kept
        nodes = kept


def _dab_samples(dab: Dab, cell: float) -> list[Point3]:
    """The dab centre plus rings about one cell apart across the brush disc,
    repeated through the dab's column (nearest layers first) so a socket
    floor or a bulge in front of the stroke is sampled too."""

    if dab.normal is None:
        return [dab.center]
    u, v = _frame(dab.normal, (1.0, 0.0, 0.0), 0.0)
    disc = [(0.0, 0.0, 0.0)]
    rings = max(1, int(dab.radius / cell))
    for ring in range(1, rings + 1):
        distance = dab.radius * ring / (rings + 0.25)
        count = max(6, int(2.0 * pi * distance / cell))
        for index in range(count):
            angle = 2.0 * pi * index / count
            offset = _add(_scale(u, cos(angle)), _scale(v, sin(angle)))
            disc.append(_scale(offset, distance))
    samples = []
    layers = max(1, int(DAB_DEPTH * dab.radius / cell))
    for layer in sorted(range(-layers, layers + 1), key=abs):
        depth = DAB_DEPTH * dab.radius * layer / layers
        level = _sub(dab.center, _scale(dab.normal, depth))
        samples.extend(_add(level, offset) for offset in disc)
    return samples


def drop_small_pieces(
    faces: Sequence[Face], minimum: int = MIN_PIECE_FACES
) -> tuple[list[Face], int]:
    """Remove edge-connected pieces too small to be useful retopology."""

    by_edge: dict[tuple[Node, Node], list[int]] = {}
    for index, face in enumerate(faces):
        for edge in face_edges(face):
            by_edge.setdefault(edge, []).append(index)
    piece = [-1] * len(faces)
    kept = []
    for start in range(len(faces)):
        if piece[start] >= 0:
            continue
        members, queue = [start], deque([start])
        piece[start] = start
        while queue:
            for edge in face_edges(faces[queue.popleft()]):
                for other in by_edge[edge]:
                    if piece[other] < 0:
                        piece[other] = start
                        members.append(other)
                        queue.append(other)
        if len(members) >= minimum:
            kept.extend(members)
    kept.sort()
    return [faces[index] for index in kept], len(faces) - len(kept)


def _fans(node: Node, faces: Sequence[Face]) -> list[list[Face]]:
    """Group the faces around ``node`` into edge-connected fans."""

    remaining = list(faces)
    fans = []
    while remaining:
        fan = [remaining.pop()]
        grew = True
        while grew:
            grew = False
            fan_edges = {
                edge for face in fan for edge in face_edges(face) if node in edge
            }
            for face in list(remaining):
                if fan_edges.intersection(
                    edge for edge in face_edges(face) if node in edge
                ):
                    fan.append(face)
                    remaining.remove(face)
                    grew = True
        fans.append(fan)
    return fans


def clean_faces(faces: Sequence[Face]) -> tuple[list[Face], int]:
    """Remove one-cell spurs and make every vertex a single face fan.

    A spur is a cell with three open sides sticking out of a larger patch; it
    is what makes painted borders look ragged. Two cells that touch only at a
    corner form a non-manifold vertex, which the add-on's own integrity check
    would report, so the smaller fan around such a vertex is dropped.
    """

    faces = list(faces)
    start = len(faces)
    counts = edge_counts(faces)
    if len(faces) > 4:
        faces = [
            face
            for face in faces
            if sum(counts[edge] == 1 for edge in face_edges(face)) < 3
        ]
    changed = True
    while changed:
        changed = False
        around: dict[Node, list[Face]] = {}
        for face in faces:
            for node in face:
                around.setdefault(node, []).append(face)
        for node, incident in around.items():
            fans = _fans(node, incident)
            if len(fans) > 1:
                fans.sort(key=len, reverse=True)
                drop = {id(face) for fan in fans[1:] for face in fan}
                faces = [face for face in faces if id(face) not in drop]
                changed = True
                break
    return faces, start - len(faces)


def _boundary_loops(faces: Sequence[Face]) -> list[list[Node]]:
    """Ordered boundary loops; faces must already be fan-clean."""

    adjacency: dict[Node, list[Node]] = {}
    for (first, second), count in edge_counts(faces).items():
        if count == 1:
            adjacency.setdefault(first, []).append(second)
            adjacency.setdefault(second, []).append(first)
    loops, seen = [], set()
    for start in adjacency:
        if start in seen or len(adjacency[start]) != 2:
            continue
        loop, previous, current = [start], None, start
        seen.add(start)
        while True:
            options = [n for n in adjacency[current] if n != previous]
            if not options or options[0] == start or len(adjacency[options[0]]) != 2:
                break
            previous, current = current, options[0]
            if current in seen:
                break
            loop.append(current)
            seen.add(current)
        loops.append(loop)
    return loops


def _nearest_run(loop: list[Node], near: set[Node]) -> list[Node]:
    """Longest cyclic run of consecutive ``loop`` nodes that are in ``near``."""

    if not near or len(near) == len(loop):
        return []
    start = next(index for index, node in enumerate(loop) if node not in near)
    order = loop[start:] + loop[:start]
    best, current = [], []
    for node in order + [order[0]]:
        if node in near:
            current.append(node)
        else:
            best = max(best, current, key=len)
            current = []
    return best


def _section_counts(wide: int, narrow: int) -> list[tuple[int, int]] | None:
    """Split a wide->narrow edge-count change into template-sized sections.

    Two-loop drops (n+2 -> n) take any width; a one-loop drop only exists as
    the 2 -> 1 cell, which goes last because its far side is one edge longer.
    """

    difference = wide - narrow
    if difference == 0:
        return [(wide, narrow)]
    odd = difference % 2
    pairs = difference // 2
    spare = narrow - odd
    if spare < pairs or (pairs == 0 and spare < 0):
        return None
    sections = []
    if pairs:
        base, extra = divmod(spare, pairs)
        if base < 1:
            return None
        sections = [(base + (i < extra) + 2, base + (i < extra)) for i in range(pairs)]
    elif spare:
        sections = [(spare, spare)]
    if odd:
        sections.append((2, 1))
    return sections


def _bridge_template(top: int, bottom: int, rows: int, last: bool):
    """Return (vertices{key:(u,v)}, faces, top, bottom, left, right) keys."""

    if top == bottom:
        keys = {
            (i, r): (i / top, 1.0 - r / rows)
            for i in range(top + 1)
            for r in range(rows + 1)
        }
        faces = [
            ((i, r), (i + 1, r), (i + 1, r + 1), (i, r + 1))
            for i in range(top)
            for r in range(rows)
        ]
        return (
            keys,
            faces,
            [(i, 0) for i in range(top + 1)],
            [(i, rows) for i in range(top + 1)],
            [(0, r) for r in range(rows + 1)],
            [(top, r) for r in range(rows + 1)],
        )
    right = rows + (abs(top - bottom) == 1)
    if not last and right != rows:
        raise TransitionError("A one-loop section must be the last section")
    template = build_transition_template(top, bottom, rows, right)
    return (
        {key: (spec.u, spec.v) for key, spec in template.vertices.items()},
        list(template.faces),
        list(template.top_keys),
        list(template.bottom_keys),
        list(template.left_keys),
        list(template.right_keys),
    )


def _bridge_pair(
    run_a: list[Node],
    run_b: list[Node],
    positions: dict[Node, Point3],
    cell: float,
    tag: int,
) -> tuple[dict[Node, Point3], list[Face]] | None:
    """Quads (with transitions when counts differ) between two boundary runs."""

    if len(run_a) - 1 < len(run_b) - 1:
        run_a, run_b = run_b, run_a
    a_edges, b_edges = len(run_a) - 1, len(run_b) - 1
    if b_edges < 1:
        return None
    gap = sum(
        min(_distance(positions[a], positions[b]) for b in run_b) for a in run_a
    ) / len(run_a)
    if gap < 0.3 * cell:
        return None
    rows = max(1 if a_edges == b_edges else 2, round(gap / cell))
    counts = _section_counts(a_edges, b_edges)
    if counts is None:
        return None
    new_positions: dict[Node, Point3] = {}
    faces: list[Face] = []
    a_start = b_start = 0
    previous_right: list[Node] | None = None
    for index, (top, bottom) in enumerate(counts):
        keys, local_faces, tops, bottoms, lefts, rights = _bridge_template(
            top, bottom, rows, index == len(counts) - 1
        )
        a_chain = run_a[a_start : a_start + top + 1]
        b_chain = run_b[b_start : b_start + bottom + 1]
        mapping: dict = {}
        for key, node in zip(tops, a_chain, strict=True):
            mapping[key] = node
        for key, node in zip(bottoms, b_chain, strict=True):
            mapping[key] = node
        if previous_right is not None:
            for key, node in zip(lefts, previous_right, strict=True):
                mapping.setdefault(key, node)

        def along(chain: list[Node], u: float) -> Point3:
            scaled = u * (len(chain) - 1)
            low = min(int(scaled), len(chain) - 2)
            return _lerp(positions[chain[low]], positions[chain[low + 1]], scaled - low)

        for key, (u, v) in keys.items():
            if key in mapping:
                continue
            node = ("bridge", tag, index, key)
            mapping[key] = node
            new_positions[node] = _lerp(along(b_chain, u), along(a_chain, u), v)
        faces.extend(tuple(mapping[key] for key in face) for face in local_faces)
        previous_right = [mapping[key] for key in rights]
        a_start += top
        b_start += bottom
    return new_positions, faces


def bridge_patches(
    positions: dict[Node, Point3],
    faces: list[Face],
    cell: float,
) -> tuple[dict[Node, Point3], list[Face], int]:
    """Join facing borders of different patches with quads and transitions."""

    loops = _boundary_loops(faces)
    patch_of = {node: node[0] for node in positions if isinstance(node, tuple) and node}
    used: set[Node] = set()
    bridges = 0
    positions = dict(positions)
    for index, loop_a in enumerate(loops):
        for loop_b in loops[index + 1 :]:
            if patch_of.get(loop_a[0]) == patch_of.get(loop_b[0]):
                continue
            reach = BRIDGE_REACH * cell
            near_a = {
                a
                for a in loop_a
                if a not in used
                and any(_distance(positions[a], positions[b]) <= reach for b in loop_b)
            }
            near_b = {
                b
                for b in loop_b
                if b not in used
                and any(_distance(positions[b], positions[a]) <= reach for a in loop_a)
            }
            run_a, run_b = _nearest_run(loop_a, near_a), _nearest_run(loop_b, near_b)
            if len(run_a) < 2 or len(run_b) < 2:
                continue
            same = _distance(positions[run_a[0]], positions[run_b[0]]) + _distance(
                positions[run_a[-1]], positions[run_b[-1]]
            )
            crossed = _distance(positions[run_a[0]], positions[run_b[-1]]) + _distance(
                positions[run_a[-1]], positions[run_b[0]]
            )
            if crossed < same:
                run_b = list(reversed(run_b))
            try:
                result = _bridge_pair(run_a, run_b, positions, cell, bridges)
            except TransitionError:
                result = None
            if result is None:
                continue
            new_positions, new_faces = result
            trial = faces + new_faces
            if any(count > 2 for count in edge_counts(trial).values()):
                continue
            positions.update(new_positions)
            faces = trial
            used.update(run_a)
            used.update(run_b)
            bridges += 1
    return positions, faces, bridges


def _orient(face: Face, positions: dict[Node, Point3], normal: Point3) -> Face:
    a, b, c, d = (positions[node] for node in face)
    winding = _add(_cross(_sub(b, a), _sub(c, a)), _cross(_sub(c, a), _sub(d, a)))
    return face if _dot(winding, normal) >= 0.0 else tuple(reversed(face))


def _bad_face(face: Face, positions: dict, normals: dict, cell: float) -> bool:
    """True when either triangle of the quad is flipped or nearly flat."""

    normal = _sum(normals[node] for node in face)
    a, b, c, d = (positions[node] for node in face)
    minimum = 0.02 * cell * cell
    return (
        _dot(_cross(_sub(b, a), _sub(c, a)), normal) <= minimum
        or _dot(_cross(_sub(c, a), _sub(d, a)), normal) <= minimum
        or _dot(_cross(_sub(c, b), _sub(d, b)), normal) <= minimum
        or _dot(_cross(_sub(d, b), _sub(a, b)), normal) <= minimum
    )


def _bridge_tag(node: Node):
    if isinstance(node, tuple) and node and node[0] == "bridge":
        return node[1]
    return None


def orient_consistently(
    faces: Sequence[Face], positions: dict, normals: dict
) -> list[Face]:
    """Wind every face the same way across shared edges.

    Each connected piece starts from its largest face, oriented to the surface
    normal, and spreads across shared edges, so a single doubtful face (a thin
    join quad on a curved surface) cannot flip against its neighbours.
    """

    def area(face: Face) -> float:
        a, b, c, d = (positions[node] for node in face)
        return _length(
            _add(_cross(_sub(b, a), _sub(c, a)), _cross(_sub(c, a), _sub(d, a)))
        )

    by_edge: dict[tuple[Node, Node], list[int]] = {}
    for index, face in enumerate(faces):
        for edge in face_edges(face):
            by_edge.setdefault(edge, []).append(index)
    result: list[Face | None] = [None] * len(faces)
    for start in sorted(range(len(faces)), key=lambda i: -area(faces[i])):
        if result[start] is not None:
            continue
        face = faces[start]
        result[start] = _orient(face, positions, _sum(normals[n] for n in face))
        queue = deque([start])
        while queue:
            current = result[queue.popleft()]
            directed = set(zip(current, (*current[1:], current[0]), strict=True))
            for edge in face_edges(current):
                for other in by_edge[edge]:
                    if result[other] is not None:
                        continue
                    candidate = faces[other]
                    pairs = set(
                        zip(candidate, (*candidate[1:], candidate[0]), strict=True)
                    )
                    result[other] = (
                        tuple(reversed(candidate)) if pairs & directed else candidate
                    )
                    queue.append(other)
    return [face for face in result if face is not None]


def relax(
    positions: dict[Node, Point3],
    normals: dict[Node, Point3],
    faces: Sequence[Face],
    project: Projector,
    iterations: int,
) -> None:
    """Smooth on the surface: inner vertices toward their neighbours, border
    vertices toward their two border neighbours, which rounds off the
    staircase a square lattice leaves along a painted outline."""

    neighbors = _neighbors(faces)
    incident: dict[Node, list[Face]] = {}
    for face in faces:
        for node in face:
            incident.setdefault(node, []).append(face)
    cell = min(
        _distance(positions[first], positions[second])
        for first, second in edge_counts(faces)
    )
    border: dict[Node, list[Node]] = {}
    for (first, second), count in edge_counts(faces).items():
        if count == 1:
            border.setdefault(first, []).append(second)
            border.setdefault(second, []).append(first)
    for _ in range(iterations):
        updated = {}
        for node, around in neighbors.items():
            if node in border:
                if len(border[node]) != 2:
                    continue
                around, factor = border[node], BORDER_RELAX
            else:
                factor = INTERIOR_RELAX
            average = _scale(
                (
                    sum(positions[n][0] for n in around),
                    sum(positions[n][1] for n in around),
                    sum(positions[n][2] for n in around),
                ),
                1.0 / len(around),
            )
            hit = project(_lerp(positions[node], average, factor))
            if hit is not None:
                updated[node] = hit
        for node, (point, normal) in updated.items():
            before = positions[node]
            positions[node] = point
            # Never let smoothing fold a quad: keep the old spot instead.
            if any(
                _bad_face(face, positions, normals, cell)
                and not _bad_face(face, {**positions, node: before}, normals, cell)
                for face in incident[node]
            ):
                positions[node] = before
                continue
            normals[node] = _normalized(normal) or normals.get(node, normal)


def mirror_faces(
    positions: dict[Node, Point3],
    normals: dict[Node, Point3],
    faces: Sequence[Face],
    plane: Mirror,
    cell: float,
    keep_side: Point3,
) -> list[Face]:
    """Clip at the plane, snap the seam onto it, and add the reflected half."""

    normal = _normalized(plane.normal)
    if normal is None:
        raise TransitionError("The mirror plane has no direction")
    if _dot(_sub(keep_side, plane.origin), normal) < 0.0:
        normal = _scale(normal, -1.0)

    def side(point: Point3) -> float:
        return _dot(_sub(point, plane.origin), normal)

    kept = [
        face
        for face in faces
        if side(_scale(_sum(positions[node] for node in face), 0.25)) > 0.0
    ]
    border = {
        node for edge, count in edge_counts(kept).items() if count == 1 for node in edge
    }
    on_plane = set()
    for node in {node for face in kept for node in face}:
        distance = side(positions[node])
        if distance < 0.0 or (node in border and distance < 0.5 * cell):
            positions[node] = _sub(positions[node], _scale(normal, distance))
            on_plane.add(node)
    kept = [
        face
        for face in kept
        if len({positions[node] for node in face}) == 4
        and sum(node in on_plane for node in face) < 3
    ]
    reflected = []
    for face in kept:
        mirrored = []
        for node in face:
            if node in on_plane:
                mirrored.append(node)
                continue
            twin = ("mirror", node)
            point = positions[node]
            positions[twin] = _sub(point, _scale(normal, 2.0 * side(point)))
            n = normals[node]
            normals[twin] = _sub(n, _scale(normal, 2.0 * _dot(n, normal)))
            mirrored.append(twin)
        reflected.append(tuple(reversed(mirrored)))
    return kept + reflected


def _reflect_dab(dab: Dab, plane: Mirror) -> Dab:
    normal = _normalized(plane.normal) or (1.0, 0.0, 0.0)
    offset = _dot(_sub(dab.center, plane.origin), normal)
    reflected_normal = None
    if dab.normal is not None:
        along = _dot(dab.normal, normal)
        reflected_normal = _sub(dab.normal, _scale(normal, 2.0 * along))
    return Dab(
        _sub(dab.center, _scale(normal, 2.0 * offset)),
        dab.radius,
        dab.erase,
        reflected_normal,
    )


def _sum(points) -> Point3:
    total = (0.0, 0.0, 0.0)
    for point in points:
        total = _add(total, point)
    return total


def grow_paint_grid(
    dabs: Sequence[Dab],
    *,
    cell: float,
    seeds: Sequence[Seed],
    flow_angle: float,
    project: Projector,
    relax_iterations: int = 3,
    max_nodes: int = MAX_NODES,
    bridge: bool = True,
    mirror: Mirror | None = None,
) -> PaintGrid:
    """Grow, clean, bridge, relax and mirror the painted quad patches."""

    if cell <= 1.0e-9:
        raise TransitionError("Surface Paint needs a positive cell size")
    if mirror is not None:
        # Let the lattice cross the plane so clipping always leaves a seam.
        dabs = [*dabs, *(_reflect_dab(dab, mirror) for dab in dabs)]
    positions, normals, folds, overlaps = _grow(
        Coverage(dabs), seeds, cell, flow_angle, project, max_nodes
    )
    if not positions:
        raise TransitionError("No painted stroke starts on the surface target")
    faces, pruned = clean_faces(_lattice_faces(positions))
    bridges = 0
    if bridge and faces:
        positions, faces, bridges = bridge_patches(positions, faces, cell)
        for node, point in positions.items():
            if node not in normals:
                hit = project(point)
                if hit is not None:
                    positions[node], normals[node] = hit[0], _normalized(hit[1])
                else:
                    normals[node] = (0.0, 0.0, 1.0)
    used = {node for face in faces for node in face}
    positions = {node: positions[node] for node in used}
    normals = {node: normals[node] for node in used}
    faces = orient_consistently(faces, positions, normals)
    if relax_iterations > 0 and faces:
        relax(positions, normals, faces, project, relax_iterations)
    if bridges:
        # A join between badly offset loops can fold even after relaxing; such
        # a join is dropped and the two patches stay open to each other.
        failed = {
            tag
            for face in faces
            if _bad_face(face, positions, normals, cell)
            for tag in map(_bridge_tag, face)
            if tag is not None
        }
        if failed:
            faces = [
                face
                for face in faces
                if not any(_bridge_tag(node) in failed for node in face)
            ]
            bridges -= len(failed)
    # Last, so a dropped join cannot leave a scrap behind.
    faces, small = drop_small_pieces(faces)
    pruned += small
    used = {node for face in faces for node in face}
    positions = {node: positions[node] for node in used}
    normals = {node: normals[node] for node in used}
    if mirror is not None and faces:
        faces = mirror_faces(positions, normals, faces, mirror, cell, seeds[0].point)
        used = {node for face in faces for node in face}
        positions = {node: positions[node] for node in used}
        normals = {node: normals[node] for node in used}
    return PaintGrid(
        positions,
        normals,
        tuple(faces),
        cell,
        folds,
        overlaps,
        bridges,
        pruned,
    )


def grid_report(grid: PaintGrid) -> dict[str, int]:
    """Counts that explain the result: where poles are and why."""

    neighbors = _neighbors(grid.faces)
    boundary = grid.boundary_nodes
    poles = [
        node
        for node, around in neighbors.items()
        if node not in boundary and len(around) != 4
    ]
    return {
        "quads": len(grid.faces),
        "vertices": len(grid.positions),
        "boundary_vertices": len(boundary),
        "interior_poles": len(poles),
        "rejected_folds": grid.rejected_folds,
        "rejected_overlaps": grid.rejected_overlaps,
        "bridges": grid.bridges,
        "pruned_faces": grid.pruned_faces,
    }
