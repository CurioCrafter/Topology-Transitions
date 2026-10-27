"""Pure topology helpers for disconnected face-island diagnostics."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence


def find_face_components(
    face_vertices: Mapping[int, Sequence[int]],
) -> tuple[tuple[int, ...], ...]:
    """Group faces by shared full edges, not by coincident points or vertices."""
    edge_faces: dict[tuple[int, int], list[int]] = defaultdict(list)
    for face_id, vertices in face_vertices.items():
        values = tuple(vertices)
        for first, second in zip(values, values[1:] + values[:1]):
            edge_faces[tuple(sorted((first, second)))].append(face_id)

    adjacency: dict[int, set[int]] = {face_id: set() for face_id in face_vertices}
    for linked_faces in edge_faces.values():
        for face_id in linked_faces:
            adjacency[face_id].update(
                other for other in linked_faces if other != face_id
            )

    pending = set(face_vertices)
    components: list[tuple[int, ...]] = []
    while pending:
        seed = min(pending)
        stack = [seed]
        found: set[int] = set()
        while stack:
            face_id = stack.pop()
            if face_id in found:
                continue
            found.add(face_id)
            stack.extend(adjacency[face_id] - found)
        pending.difference_update(found)
        components.append(tuple(sorted(found)))
    return tuple(sorted(components, key=lambda item: (-len(item), item[0])))


def detached_face_components(
    face_vertices: Mapping[int, Sequence[int]],
) -> tuple[tuple[int, ...], ...]:
    """Return every component except the deterministic largest main body."""
    components = find_face_components(face_vertices)
    if len(components) <= 1:
        return ()
    return tuple(sorted(components[1:], key=lambda item: (len(item), item[0])))
