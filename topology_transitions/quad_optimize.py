"""Pure planning helpers for deterministic quad-flow simplification."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LoopChoice:
    """One removable edge flow and the parallel flows it cannot share a pass with."""

    identifier: int
    conflict_ids: frozenset[int]
    error_ratio: float
    curvature: float
    edge_count: int
    stable_index: int


def choose_loop_batch(candidates: tuple[LoopChoice, ...]) -> tuple[int, ...]:
    """Choose a deterministic maximal-benefit, non-adjacent set of edge flows.

    Perpendicular flows are intentionally compatible.  Parallel neighboring
    flows conflict because dissolving both sides of the same one-quad strip
    would remove the strip instead of combining a regular 2x2 pattern.

    Regular quad grids produce path or cycle conflict graphs.  Those groups
    are solved exactly with dynamic programming so a large flat grid does not
    retain a strip merely because of mesh index order.  Unusual branching
    conflict graphs use the conservative low-error greedy fallback.
    """

    identifiers = {candidate.identifier for candidate in candidates}
    if len(identifiers) != len(candidates):
        raise ValueError("Loop identifiers must be unique")
    if any(
        candidate.identifier in candidate.conflict_ids
        or not candidate.conflict_ids <= identifiers
        for candidate in candidates
    ):
        raise ValueError("Loop conflicts must reference other candidate identifiers")

    by_identifier = {candidate.identifier: candidate for candidate in candidates}
    if any(
        candidate.identifier not in by_identifier[conflict].conflict_ids
        for candidate in candidates
        for conflict in candidate.conflict_ids
    ):
        raise ValueError("Loop conflicts must be symmetric")

    def stable_key(identifier: int) -> tuple[int, int]:
        candidate = by_identifier[identifier]
        return candidate.stable_index, identifier

    def better(first: tuple[int, ...], second: tuple[int, ...]) -> tuple[int, ...]:
        def metrics(plan: tuple[int, ...]) -> tuple[int, float, float, int]:
            members = [by_identifier[identifier] for identifier in plan]
            return (
                sum(member.edge_count for member in members),
                sum(member.error_ratio * member.edge_count for member in members),
                sum(member.curvature * member.edge_count for member in members),
                len(members),
            )

        first_metrics = metrics(first)
        second_metrics = metrics(second)
        if first_metrics[0] != second_metrics[0]:
            return first if first_metrics[0] > second_metrics[0] else second
        if first_metrics[1] != second_metrics[1]:
            return first if first_metrics[1] < second_metrics[1] else second
        if first_metrics[2] != second_metrics[2]:
            return first if first_metrics[2] < second_metrics[2] else second
        if first_metrics[3] != second_metrics[3]:
            return first if first_metrics[3] > second_metrics[3] else second
        first_order = tuple(sorted(first, key=stable_key))
        second_order = tuple(sorted(second, key=stable_key))
        return first if first_order <= second_order else second

    def solve_path(path: tuple[int, ...]) -> tuple[int, ...]:
        excluded: tuple[int, ...] = ()
        included: tuple[int, ...] | None = None
        for identifier in path:
            next_included = (*excluded, identifier)
            next_excluded = (
                excluded if included is None else better(excluded, included)
            )
            excluded, included = next_excluded, next_included
        return excluded if included is None else better(excluded, included)

    def ordered_component(component: set[int]) -> tuple[tuple[int, ...], bool]:
        endpoints = [
            identifier
            for identifier in component
            if len(by_identifier[identifier].conflict_ids & component) <= 1
        ]
        is_cycle = not endpoints and len(component) > 1
        start = min(endpoints or component, key=stable_key)
        ordered = []
        previous = None
        current = start
        while current is not None and current not in ordered:
            ordered.append(current)
            following = sorted(
                (
                    identifier
                    for identifier in by_identifier[current].conflict_ids & component
                    if identifier != previous
                ),
                key=stable_key,
            )
            previous, current = current, following[0] if following else None
        return tuple(ordered), is_cycle

    pending = set(identifiers)
    selected: list[int] = []
    while pending:
        seed = min(pending, key=stable_key)
        component = {seed}
        frontier = [seed]
        while frontier:
            current = frontier.pop()
            neighbors = by_identifier[current].conflict_ids - component
            component.update(neighbors)
            frontier.extend(neighbors)
        pending.difference_update(component)

        if max(
            (
                len(by_identifier[identifier].conflict_ids & component)
                for identifier in component
            ),
            default=0,
        ) <= 2:
            ordered, is_cycle = ordered_component(component)
            if is_cycle:
                without_first = solve_path(ordered[1:])
                with_first = (ordered[0], *solve_path(ordered[2:-1]))
                selected.extend(better(without_first, with_first))
            else:
                selected.extend(solve_path(ordered))
            continue

        ordered = sorted(
            (by_identifier[identifier] for identifier in component),
            key=lambda candidate: (
                candidate.error_ratio,
                candidate.curvature,
                -candidate.edge_count,
                candidate.stable_index,
                candidate.identifier,
            ),
        )
        blocked = set()
        for candidate in ordered:
            if candidate.identifier in blocked:
                continue
            selected.append(candidate.identifier)
            blocked.update(candidate.conflict_ids)

    if any(
        first in by_identifier[second].conflict_ids
        for index, first in enumerate(selected)
        for second in selected[index + 1 :]
    ):
        raise RuntimeError("Quad-flow planner selected conflicting loops")
    return tuple(sorted(selected))
