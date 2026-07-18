"""Pure topology templates for selection-driven edge-loop transitions.

This module deliberately has no Blender imports.  Keeping the combinatorial
mesh builder independent makes the difficult part of the add-on testable with
ordinary Python and keeps Blender-specific state handling in one place.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field


class TransitionError(ValueError):
    """Raised when a requested all-quad transition is not topologically valid."""


TRANSITIONS: Mapping[str, tuple[int, int, str]] = {
    "FIVE_TO_THREE": (5, 3, "5 to 3"),
    "THREE_TO_FIVE": (3, 5, "3 to 5"),
    "THREE_TO_ONE": (3, 1, "3 to 1"),
    "ONE_TO_THREE": (1, 3, "1 to 3"),
    "FOUR_TO_TWO": (4, 2, "4 to 2"),
    "TWO_TO_FOUR": (2, 4, "2 to 4"),
    "ONE_TO_TWO": (1, 2, "1 to 2"),
    "TWO_TO_ONE": (2, 1, "2 to 1"),
}


@dataclass(frozen=True)
class VertexSpec:
    """A template vertex and its normalized geometric starting point."""

    key: str
    u: float
    v: float
    boundary: bool = False


@dataclass
class TransitionTemplate:
    """A quad-disk template whose boundary can be bound to an existing patch."""

    input_count: int
    output_count: int
    vertices: dict[str, VertexSpec] = field(default_factory=dict)
    faces: list[tuple[str, str, str, str]] = field(default_factory=list)
    top_keys: list[str] = field(default_factory=list)
    bottom_keys: list[str] = field(default_factory=list)
    left_keys: list[str] = field(default_factory=list)
    right_keys: list[str] = field(default_factory=list)
    pole_keys: set[str] = field(default_factory=set)
    relax_locked_keys: set[str] = field(default_factory=set)

    @property
    def boundary_keys(self) -> set[str]:
        return {
            *self.top_keys,
            *self.bottom_keys,
            *self.left_keys,
            *self.right_keys,
        }

    @property
    def interior_keys(self) -> set[str]:
        return set(self.vertices) - self.boundary_keys

    @property
    def boundary_edge_count(self) -> int:
        return (
            len(self.top_keys)
            + len(self.bottom_keys)
            + len(self.left_keys)
            + len(self.right_keys)
            - 4
        )


class _Builder:
    def __init__(
        self,
        input_count: int,
        output_count: int,
        left_segments: int,
        right_segments: int,
    ) -> None:
        self.template = TransitionTemplate(input_count, output_count)
        self._counter = 0
        self._make_boundary(left_segments, right_segments)

    def _add_vertex(
        self, key: str, u: float, v: float, *, boundary: bool = False
    ) -> str:
        existing = self.template.vertices.get(key)
        spec = VertexSpec(key, float(u), float(v), boundary)
        if existing is not None:
            if existing.boundary != boundary:
                raise TransitionError(f"Conflicting boundary state for {key}")
            return key
        self.template.vertices[key] = spec
        return key

    def _new_vertex(self, prefix: str, u: float, v: float) -> str:
        self._counter += 1
        return self._add_vertex(f"{prefix}:{self._counter}", u, v)

    def _make_boundary(self, left_segments: int, right_segments: int) -> None:
        if left_segments < 1 or right_segments < 1:
            raise TransitionError("Each side boundary needs at least one edge")

        t = self.template
        t.top_keys = [f"top:{i}" for i in range(t.input_count + 1)]
        t.bottom_keys = [f"bottom:{i}" for i in range(t.output_count + 1)]
        for i, key in enumerate(t.top_keys):
            self._add_vertex(key, i / t.input_count, 1.0, boundary=True)
        for i, key in enumerate(t.bottom_keys):
            self._add_vertex(key, i / t.output_count, 0.0, boundary=True)

        t.left_keys = [t.top_keys[0]]
        for i in range(1, left_segments):
            key = f"left:{i}"
            self._add_vertex(key, 0.0, 1.0 - (i / left_segments), boundary=True)
            t.left_keys.append(key)
        t.left_keys.append(t.bottom_keys[0])

        t.right_keys = [t.top_keys[-1]]
        for i in range(1, right_segments):
            key = f"right:{i}"
            self._add_vertex(key, 1.0, 1.0 - (i / right_segments), boundary=True)
            t.right_keys.append(key)
        t.right_keys.append(t.bottom_keys[-1])

    def make_row(
        self,
        prefix: str,
        segments: int,
        left_key: str,
        right_key: str,
        v: float,
    ) -> list[str]:
        row = [left_key]
        for i in range(1, segments):
            row.append(self._new_vertex(prefix, i / segments, v))
        row.append(right_key)
        return row

    def connect_equal_rows(self, first: Sequence[str], second: Sequence[str]) -> None:
        if len(first) != len(second):
            raise TransitionError("Regular row bridge requires equal edge counts")
        for i in range(len(first) - 1):
            self.template.faces.append(
                (first[i], first[i + 1], second[i + 1], second[i])
            )

    def add_two_loop_cell(
        self,
        wide_row: Sequence[str],
        narrow_row: Sequence[str],
        pole_slot: int,
        pole_spacing: float,
    ) -> None:
        wide = len(wide_row) - 1
        narrow = len(narrow_row) - 1
        if wide - narrow != 2:
            raise TransitionError(
                "The two-loop cell requires a width difference of two"
            )
        if not 0 <= pole_slot <= wide - 3:
            raise TransitionError(
                f"Pole slot {pole_slot} is outside the valid range 0..{wide - 3}"
            )

        wide_v = sum(self.template.vertices[key].v for key in wide_row) / len(wide_row)
        narrow_v = sum(self.template.vertices[key].v for key in narrow_row) / len(
            narrow_row
        )
        pole_v = (0.55 * wide_v) + (0.45 * narrow_v)

        base_a = (pole_slot + 1) / wide
        base_b = (pole_slot + 2) / wide
        center = (base_a + base_b) * 0.5
        half_distance = (base_b - base_a) * 0.5 * pole_spacing
        pole_a = self._new_vertex("n-pole", max(0.01, center - half_distance), pole_v)
        pole_b = self._new_vertex("n-pole", min(0.99, center + half_distance), pole_v)
        self.template.pole_keys.update((pole_a, pole_b))

        for i in range(pole_slot):
            self.template.faces.append(
                (wide_row[i], wide_row[i + 1], narrow_row[i + 1], narrow_row[i])
            )

        k = pole_slot
        self.template.faces.extend(
            [
                (wide_row[k], wide_row[k + 1], pole_a, narrow_row[k]),
                (wide_row[k + 1], wide_row[k + 2], pole_b, pole_a),
                (
                    wide_row[k + 2],
                    wide_row[k + 3],
                    narrow_row[k + 1],
                    pole_b,
                ),
                (pole_a, pole_b, narrow_row[k + 1], narrow_row[k]),
            ]
        )

        for i in range(pole_slot + 3, wide):
            self.template.faces.append(
                (
                    wide_row[i],
                    wide_row[i + 1],
                    narrow_row[i - 1],
                    narrow_row[i - 2],
                )
            )

    def add_one_loop_cell(
        self,
        narrow_row: Sequence[str],
        wide_row: Sequence[str],
        extra_key: str,
        extra_side: str,
        pole_spacing: float,
    ) -> None:
        if len(narrow_row) != 2 or len(wide_row) != 3:
            raise TransitionError(
                "The asymmetric cell only supports 1 to 2 transitions"
            )
        if extra_side not in {"LEFT", "RIGHT"}:
            raise TransitionError("The asymmetric cell needs a LEFT or RIGHT side")

        v = sum(self.template.vertices[key].v for key in (*narrow_row, *wide_row)) / 5
        shift = max(-0.18, min(0.18, (pole_spacing - 1.0) * 0.18))
        u = 0.5 + (shift if extra_side == "LEFT" else -shift)
        pole = self._new_vertex("n-pole", u, v)
        self.template.pole_keys.add(pole)

        n0, n1 = narrow_row
        w0, w1, w2 = wide_row
        if extra_side == "LEFT":
            self.template.faces.extend(
                [
                    (n0, n1, pole, extra_key),
                    (extra_key, pole, w1, w0),
                    (pole, n1, w2, w1),
                ]
            )
        else:
            self.template.faces.extend(
                [
                    (n0, n1, extra_key, pole),
                    (pole, extra_key, w2, w1),
                    (n0, pole, w1, w0),
                ]
            )


def choose_pole_slot(wide_count: int, pole_side: str, mirror: bool = False) -> int:
    """Choose where the local 3-to-1 cell sits inside a wider strip."""

    maximum = wide_count - 3
    if maximum < 0:
        raise TransitionError("A two-loop transition needs at least three wide edges")
    side = pole_side.upper()
    if side == "LEFT":
        slot = 0
    elif side == "RIGHT":
        slot = maximum
    elif side == "CENTER":
        slot = maximum // 2
    else:
        raise TransitionError(f"Unknown pole side: {pole_side}")
    return maximum - slot if mirror else slot


def build_transition_template(
    input_count: int,
    output_count: int,
    left_segments: int,
    right_segments: int,
    *,
    pole_side: str = "CENTER",
    mirror: bool = False,
    pole_spacing: float = 1.0,
) -> TransitionTemplate:
    """Build a validated all-quad transition disk.

    ``left_segments`` and ``right_segments`` are the complete top-to-bottom
    boundary lengths after the operator has assigned shoulder edges.  A
    two-loop change needs equal side lengths.  A one-loop change needs one side
    to contain exactly one extra edge; that edge is the parity compensation
    visible in the classic asymmetric 1-to-2 pattern.
    """

    if input_count < 1 or output_count < 1:
        raise TransitionError("Loop counts must be positive")
    difference = abs(input_count - output_count)
    if difference not in {1, 2}:
        raise TransitionError(
            "This release supports loop-count differences of one or two"
        )
    if not 0.5 <= pole_spacing <= 2.0:
        raise TransitionError("Pole spacing must be between 0.5 and 2.0")

    builder = _Builder(input_count, output_count, left_segments, right_segments)
    t = builder.template
    reducing = input_count > output_count
    wide = max(input_count, output_count)

    if difference == 2:
        if left_segments != right_segments:
            raise TransitionError(
                "Two-loop transitions require equal left and right side boundaries"
            )
        side_segments = left_segments
        slot = choose_pole_slot(wide, pole_side, mirror)

        if reducing:
            previous = t.top_keys
            for row_index in range(1, side_segments):
                row = builder.make_row(
                    "wide-row",
                    wide,
                    t.left_keys[row_index],
                    t.right_keys[row_index],
                    1.0 - (row_index / side_segments),
                )
                builder.connect_equal_rows(previous, row)
                previous = row
            builder.add_two_loop_cell(previous, t.bottom_keys, slot, pole_spacing)
        else:
            if side_segments == 1:
                wide_row = t.bottom_keys
            else:
                wide_row = builder.make_row(
                    "wide-row",
                    wide,
                    t.left_keys[1],
                    t.right_keys[1],
                    1.0 - (1.0 / side_segments),
                )
            builder.add_two_loop_cell(wide_row, t.top_keys, slot, pole_spacing)
            previous = wide_row
            for row_index in range(2, side_segments + 1):
                row = (
                    t.bottom_keys
                    if row_index == side_segments
                    else builder.make_row(
                        "wide-row",
                        wide,
                        t.left_keys[row_index],
                        t.right_keys[row_index],
                        1.0 - (row_index / side_segments),
                    )
                )
                builder.connect_equal_rows(previous, row)
                previous = row
    else:
        if abs(left_segments - right_segments) != 1:
            raise TransitionError(
                "A one-loop transition needs exactly one compensating side edge"
            )
        extra_side = "LEFT" if left_segments > right_segments else "RIGHT"
        if mirror:
            requested = "RIGHT" if extra_side == "LEFT" else "LEFT"
            raise TransitionError(
                f"Mirror requires the boundary shoulder on the {requested.lower()} side"
            )
        left_consumed = 2 if extra_side == "LEFT" else 1
        right_consumed = 2 if extra_side == "RIGHT" else 1
        common_left = left_segments - left_consumed
        common_right = right_segments - right_consumed
        if common_left != common_right or common_left < 0:
            raise TransitionError("The asymmetric boundary cannot be filled with quads")
        common_rows = common_left

        if reducing:
            previous = t.top_keys
            for row_index in range(1, common_rows + 1):
                row = builder.make_row(
                    "wide-row",
                    wide,
                    t.left_keys[row_index],
                    t.right_keys[row_index],
                    1.0 - (row_index / max(left_segments, right_segments)),
                )
                builder.connect_equal_rows(previous, row)
                previous = row
            extra_key = (
                t.left_keys[common_rows + 1]
                if extra_side == "LEFT"
                else t.right_keys[common_rows + 1]
            )
            builder.add_one_loop_cell(
                t.bottom_keys, previous, extra_key, extra_side, pole_spacing
            )
        else:
            left_index = left_consumed
            right_index = right_consumed
            wide_row = (
                t.bottom_keys
                if common_rows == 0
                else builder.make_row(
                    "wide-row",
                    wide,
                    t.left_keys[left_index],
                    t.right_keys[right_index],
                    1.0
                    - (
                        max(left_index, right_index)
                        / max(left_segments, right_segments)
                    ),
                )
            )
            extra_key = t.left_keys[1] if extra_side == "LEFT" else t.right_keys[1]
            builder.add_one_loop_cell(
                t.top_keys, wide_row, extra_key, extra_side, pole_spacing
            )
            previous = wide_row
            for row_index in range(1, common_rows + 1):
                left_index = left_consumed + row_index
                right_index = right_consumed + row_index
                row = (
                    t.bottom_keys
                    if row_index == common_rows
                    else builder.make_row(
                        "wide-row",
                        wide,
                        t.left_keys[left_index],
                        t.right_keys[right_index],
                        1.0
                        - (
                            max(left_index, right_index)
                            / max(left_segments, right_segments)
                        ),
                    )
                )
                builder.connect_equal_rows(previous, row)
                previous = row

    validate_template(t)
    return t


def _boundary_cycle(template: TransitionTemplate) -> list[str]:
    """Return a clockwise boundary cycle without repeated corner keys."""

    cycle = list(template.top_keys)
    cycle.extend(template.right_keys[1:])
    cycle.extend(reversed(template.bottom_keys[:-1]))
    cycle.extend(reversed(template.left_keys[1:-1]))
    return cycle


def _odd_side_partition(total: int, targets: Sequence[int]) -> tuple[int, ...]:
    """Split an even loop into four positive odd arcs near ``targets``."""

    if len(targets) != 4 or total < 4 or total % 2:
        raise TransitionError("A single-quad frame needs an even boundary loop")
    candidates: list[tuple[tuple[int, int, tuple[int, ...]], tuple[int, ...]]] = []
    for first in range(1, total - 2, 2):
        for second in range(1, total - first - 1, 2):
            for third in range(1, total - first - second, 2):
                fourth = total - first - second - third
                if fourth < 1 or fourth % 2 == 0:
                    continue
                values = (first, second, third, fourth)
                differences = tuple(
                    abs(value - target)
                    for value, target in zip(values, targets, strict=True)
                )
                score = (
                    sum(difference * difference for difference in differences),
                    max(differences),
                    values,
                )
                candidates.append((score, values))
    if not candidates:
        raise TransitionError("The transition boundary cannot fit a quad frame")
    return min(candidates, key=lambda item: item[0])[1]


def frame_transition_for_single_quad(
    inner: TransitionTemplate,
    *,
    inner_margin: float = 0.18,
    guard_margin: float = 0.07,
) -> TransitionTemplate:
    """Wrap a transition disk in an all-quad frame with a four-edge boundary.

    A preset normally needs several boundary edges, so it cannot replace one
    embedded quad directly without splitting adjacent faces.  This adapter
    keeps the selected quad's four shared edges untouched and places the real
    preset inside two connected rings.  The added extraordinary vertices are
    the topological cost of making the operation local and universally safe.
    """

    validate_template(inner)
    if not 0.0 < guard_margin < inner_margin < 0.5:
        raise TransitionError(
            "Single-quad frame margins must satisfy 0 < guard < inner < 0.5"
        )

    template = TransitionTemplate(inner.input_count, inner.output_count)
    outer = {
        "top_left": "single:outer:top_left",
        "top_right": "single:outer:top_right",
        "bottom_right": "single:outer:bottom_right",
        "bottom_left": "single:outer:bottom_left",
    }
    outer_coordinates = {
        outer["top_left"]: (0.0, 1.0),
        outer["top_right"]: (1.0, 1.0),
        outer["bottom_right"]: (1.0, 0.0),
        outer["bottom_left"]: (0.0, 0.0),
    }
    for key, (u, v) in outer_coordinates.items():
        template.vertices[key] = VertexSpec(key, u, v, boundary=True)
    template.top_keys = [outer["top_left"], outer["top_right"]]
    template.bottom_keys = [outer["bottom_left"], outer["bottom_right"]]
    template.left_keys = [outer["top_left"], outer["bottom_left"]]
    template.right_keys = [outer["top_right"], outer["bottom_right"]]

    inner_scale = 1.0 - (2.0 * inner_margin)
    inner_keys = {key: f"single:transition:{key}" for key in inner.vertices}
    for key, spec in inner.vertices.items():
        mapped = inner_keys[key]
        template.vertices[mapped] = VertexSpec(
            mapped,
            inner_margin + (inner_scale * spec.u),
            inner_margin + (inner_scale * spec.v),
        )
    template.faces.extend(
        tuple(inner_keys[key] for key in face) for face in inner.faces
    )
    template.pole_keys = {inner_keys[key] for key in inner.pole_keys}

    source_cycle = _boundary_cycle(inner)
    inner_cycle = [inner_keys[key] for key in source_cycle]
    side_counts = _odd_side_partition(
        len(inner_cycle),
        (
            len(inner.top_keys) - 1,
            len(inner.right_keys) - 1,
            len(inner.bottom_keys) - 1,
            len(inner.left_keys) - 1,
        ),
    )
    guard_keys: list[str] = []
    guard_scale = 1.0 - (2.0 * guard_margin)
    for side, segments in enumerate(side_counts):
        for _step in range(segments):
            key = f"single:guard:{len(guard_keys)}"
            source = inner.vertices[source_cycle[len(guard_keys)]]
            u = guard_margin + (guard_scale * source.u)
            v = guard_margin + (guard_scale * source.v)
            template.vertices[key] = VertexSpec(key, u, v)
            guard_keys.append(key)

    boundary_size = len(inner_cycle)
    for index, guard_key in enumerate(guard_keys):
        following = (index + 1) % boundary_size
        template.faces.append(
            (
                guard_key,
                guard_keys[following],
                inner_cycle[following],
                inner_cycle[index],
            )
        )

    outer_cycle = [
        outer["top_left"],
        outer["top_right"],
        outer["bottom_right"],
        outer["bottom_left"],
    ]
    first_index = 0
    for side, arc_length in enumerate(side_counts):
        arc = [
            guard_keys[(first_index + offset) % boundary_size]
            for offset in range(arc_length + 1)
        ]
        polygon = [
            outer_cycle[side],
            outer_cycle[(side + 1) % 4],
            *reversed(arc),
        ]
        for index in range(1, len(polygon) - 2, 2):
            template.faces.append(
                (
                    polygon[0],
                    polygon[index],
                    polygon[index + 1],
                    polygon[index + 2],
                )
            )
        first_index = (first_index + arc_length) % boundary_size

    template.relax_locked_keys.update(guard_keys)
    template.relax_locked_keys.update(inner_cycle)
    validate_template(template)
    return template


def template_edges(
    faces: Iterable[Sequence[str]],
) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    for face in faces:
        for index, first in enumerate(face):
            second = face[(index + 1) % len(face)]
            edge = tuple(sorted((first, second)))
            counts[edge] = counts.get(edge, 0) + 1
    return counts


def template_adjacency(template: TransitionTemplate) -> dict[str, set[str]]:
    adjacency = {key: set() for key in template.vertices}
    for face in template.faces:
        for index, first in enumerate(face):
            second = face[(index + 1) % 4]
            adjacency[first].add(second)
            adjacency[second].add(first)
    return adjacency


def _expected_boundary_edges(template: TransitionTemplate) -> set[tuple[str, str]]:
    cycle = _boundary_cycle(template)
    return {
        tuple(sorted((cycle[i], cycle[(i + 1) % len(cycle)])))
        for i in range(len(cycle))
    }


def validate_template(template: TransitionTemplate) -> None:
    """Prove the template is one connected quad disk with the declared poles."""

    if not template.faces:
        raise TransitionError("Template contains no faces")
    for face in template.faces:
        if len(face) != 4 or len(set(face)) != 4:
            raise TransitionError(f"Invalid quad face: {face}")
        missing = set(face) - set(template.vertices)
        if missing:
            raise TransitionError(
                f"Face references missing vertices: {sorted(missing)}"
            )

    edges = template_edges(template.faces)
    if any(count not in {1, 2} for count in edges.values()):
        raise TransitionError("Template contains a non-manifold edge")
    actual_boundary = {edge for edge, count in edges.items() if count == 1}
    expected_boundary = _expected_boundary_edges(template)
    if actual_boundary != expected_boundary:
        missing = expected_boundary - actual_boundary
        extra = actual_boundary - expected_boundary
        raise TransitionError(
            "Template boundary mismatch "
            f"(missing={sorted(missing)}, extra={sorted(extra)})"
        )

    used = {key for face in template.faces for key in face}
    if used != set(template.vertices):
        raise TransitionError("Template has isolated or unused vertices")
    if not template.relax_locked_keys <= template.interior_keys:
        raise TransitionError("Relaxation locks must reference interior vertices")

    adjacency = template_adjacency(template)
    pending = [next(iter(template.vertices))]
    visited: set[str] = set()
    while pending:
        key = pending.pop()
        if key in visited:
            continue
        visited.add(key)
        pending.extend(adjacency[key] - visited)
    if visited != set(template.vertices):
        raise TransitionError("Template is disconnected")

    euler = len(template.vertices) - len(edges) + len(template.faces)
    if euler != 1:
        raise TransitionError(f"Template is not a disk (Euler characteristic {euler})")
    for pole in template.pole_keys:
        if len(adjacency[pole]) != 3:
            raise TransitionError(f"Expected a valence-3 N-pole at {pole}")


def preset_counts(identifier: str) -> tuple[int, int]:
    try:
        input_count, output_count, _label = TRANSITIONS[identifier]
    except KeyError as exc:
        raise TransitionError(f"Unknown transition preset: {identifier}") from exc
    return input_count, output_count


def transition_items() -> list[tuple[str, str, str]]:
    return [
        (identifier, label, f"Rebuild a {label} all-quad edge-loop transition")
        for identifier, (_incoming, _outgoing, label) in TRANSITIONS.items()
    ]
