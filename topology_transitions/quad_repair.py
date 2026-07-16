"""Blender-independent planning for conservative n-gon quad fans."""

from __future__ import annotations

from collections.abc import Sequence

Point2D = tuple[float, float]
Quad = tuple[int, int, int, int]

EPSILON = 1.0e-10


def polygon_signed_area(points: Sequence[Point2D]) -> float:
    """Return twice the signed area of a 2D polygon."""

    return sum(
        first[0] * second[1] - second[0] * first[1]
        for first, second in zip(points, (*points[1:], points[0]), strict=True)
    )


def _cross(first: Point2D, second: Point2D, third: Point2D) -> float:
    return (second[0] - first[0]) * (third[1] - second[1]) - (second[1] - first[1]) * (
        third[0] - second[0]
    )


def _point_on_segment(point: Point2D, first: Point2D, second: Point2D) -> bool:
    direct_cross = (second[0] - first[0]) * (point[1] - first[1]) - (
        second[1] - first[1]
    ) * (point[0] - first[0])
    if abs(direct_cross) > EPSILON:
        return False
    return (
        min(first[0], second[0]) - EPSILON
        <= point[0]
        <= max(first[0], second[0]) + EPSILON
        and min(first[1], second[1]) - EPSILON
        <= point[1]
        <= max(first[1], second[1]) + EPSILON
    )


def _segments_intersect(
    first_a: Point2D,
    first_b: Point2D,
    second_a: Point2D,
    second_b: Point2D,
) -> bool:
    first_side_a = _cross(first_a, first_b, second_a)
    first_side_b = _cross(first_a, first_b, second_b)
    second_side_a = _cross(second_a, second_b, first_a)
    second_side_b = _cross(second_a, second_b, first_b)
    if (
        first_side_a * first_side_b < -EPSILON
        and second_side_a * second_side_b < -EPSILON
    ):
        return True
    return any(
        (abs(cross) <= EPSILON and _point_on_segment(point, segment_a, segment_b))
        for cross, point, segment_a, segment_b in (
            (first_side_a, second_a, first_a, first_b),
            (first_side_b, second_b, first_a, first_b),
            (second_side_a, first_a, second_a, second_b),
            (second_side_b, first_b, second_a, second_b),
        )
    )


def _point_inside_polygon(point: Point2D, polygon: Sequence[Point2D]) -> bool:
    if any(
        _point_on_segment(point, first, second)
        for first, second in zip(polygon, (*polygon[1:], polygon[0]), strict=True)
    ):
        return False

    inside = False
    previous = polygon[-1]
    for current in polygon:
        crosses_y = (current[1] > point[1]) != (previous[1] > point[1])
        if crosses_y:
            intersection_x = (previous[0] - current[0]) * (point[1] - current[1]) / (
                previous[1] - current[1]
            ) + current[0]
            if point[0] < intersection_x:
                inside = not inside
        previous = current
    return inside


def _diagonal_is_internal(
    polygon: Sequence[Point2D], first_index: int, second_index: int
) -> bool:
    first = polygon[first_index]
    second = polygon[second_index]
    for edge_index in range(len(polygon)):
        next_index = (edge_index + 1) % len(polygon)
        if first_index in {edge_index, next_index} or second_index in {
            edge_index,
            next_index,
        }:
            continue
        if _segments_intersect(
            first,
            second,
            polygon[edge_index],
            polygon[next_index],
        ):
            return False
    midpoint = ((first[0] + second[0]) * 0.5, (first[1] + second[1]) * 0.5)
    return _point_inside_polygon(midpoint, polygon)


def _quad_is_strictly_convex(points: Sequence[Point2D], orientation: float) -> bool:
    if len(points) != 4:
        return False
    if polygon_signed_area(points) * orientation <= EPSILON:
        return False
    for index in range(4):
        corner = _cross(
            points[index - 1],
            points[index],
            points[(index + 1) % 4],
        )
        if corner * orientation <= EPSILON:
            return False
    return True


def quad_fan_candidates(vertex_count: int) -> tuple[tuple[Quad, ...], ...]:
    """Enumerate every rotated fan that partitions an even polygon into quads."""

    if vertex_count < 4 or vertex_count % 2:
        return ()
    candidates = []
    indices = list(range(vertex_count))
    for anchor in range(vertex_count):
        ordered = indices[anchor:] + indices[:anchor]
        candidates.append(
            tuple(
                (ordered[0], ordered[offset], ordered[offset + 1], ordered[offset + 2])
                for offset in range(1, vertex_count - 1, 2)
            )
        )
    return tuple(candidates)


def quad_fan_quality(points: Sequence[Point2D], quads: Sequence[Quad]) -> float:
    """Score a valid fan by its weakest area and corner angle."""

    areas = []
    corner_sines = []
    for quad in quads:
        quad_points = [points[index] for index in quad]
        areas.append(abs(polygon_signed_area(quad_points)))
        for index in range(4):
            previous = quad_points[index - 1]
            current = quad_points[index]
            following = quad_points[(index + 1) % 4]
            first = (previous[0] - current[0], previous[1] - current[1])
            second = (following[0] - current[0], following[1] - current[1])
            denominator = (
                (first[0] ** 2 + first[1] ** 2) * (second[0] ** 2 + second[1] ** 2)
            ) ** 0.5
            if denominator <= EPSILON:
                return 0.0
            corner_sines.append(
                abs(first[0] * second[1] - first[1] * second[0]) / denominator
            )
    if not areas:
        return 0.0
    area_balance = min(areas) / max(areas)
    return area_balance * min(corner_sines)


def best_quad_fan(points: Sequence[Point2D]) -> tuple[Quad, ...] | None:
    """Return the cleanest non-crossing, strictly convex quad fan if one exists."""

    if len(points) < 4 or len(points) % 2:
        return None
    polygon_area = polygon_signed_area(points)
    if abs(polygon_area) <= EPSILON:
        return None
    orientation = 1.0 if polygon_area > 0.0 else -1.0

    best: tuple[Quad, ...] | None = None
    best_score = -1.0
    for candidate in quad_fan_candidates(len(points)):
        if any(
            not _quad_is_strictly_convex([points[index] for index in quad], orientation)
            for quad in candidate
        ):
            continue
        internal_diagonals = {
            tuple(sorted((quad[0], quad[-1]))) for quad in candidate[:-1]
        }
        if any(
            not _diagonal_is_internal(points, first, second)
            for first, second in internal_diagonals
        ):
            continue
        score = quad_fan_quality(points, candidate)
        if score > best_score + EPSILON:
            best = candidate
            best_score = score
    return best
