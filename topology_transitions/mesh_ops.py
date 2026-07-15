"""Selection analysis for rectangular Edit Mode quad patches."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import bmesh

from .core import TransitionError


@dataclass
class PatchLayout:
    """A selected rectangular patch partitioned for a transition template."""

    selected_faces: list[Any]
    patch_edges: set[Any]
    patch_vertices: set[Any]
    boundary_edges: set[Any]
    boundary_vertices: list[Any]
    top: list[Any]
    bottom: list[Any]
    left: list[Any]
    right: list[Any]
    physical_corners: tuple[Any, Any, Any, Any]
    width: int
    height: int
    active_side_used: bool

    @property
    def left_segments(self) -> int:
        return len(self.left) - 1

    @property
    def right_segments(self) -> int:
        return len(self.right) - 1


def _selected_face_count(vertex: Any, selected: set[Any]) -> int:
    return sum(face in selected for face in vertex.link_faces)


def _face_component(faces: set[Any]) -> set[Any]:
    if not faces:
        return set()
    pending = [next(iter(faces))]
    visited: set[Any] = set()
    while pending:
        face = pending.pop()
        if face in visited:
            continue
        visited.add(face)
        for edge in face.edges:
            pending.extend(
                linked
                for linked in edge.link_faces
                if linked in faces and linked not in visited
            )
    return visited


def _ordered_boundary(boundary_edges: set[Any]) -> tuple[list[Any], list[Any]]:
    neighbors: dict[Any, list[tuple[Any, Any]]] = {}
    for edge in boundary_edges:
        first, second = edge.verts
        neighbors.setdefault(first, []).append((second, edge))
        neighbors.setdefault(second, []).append((first, edge))
    invalid = [vertex for vertex, linked in neighbors.items() if len(linked) != 2]
    if invalid:
        raise TransitionError(
            "The selection boundary branches or terminates; select one rectangular disk"
        )

    start = min(neighbors, key=lambda vertex: vertex.index)
    first_neighbor, first_edge = min(neighbors[start], key=lambda item: item[0].index)
    vertices = [start]
    edges: list[Any] = []
    previous = start
    current = first_neighbor
    edge = first_edge

    while True:
        edges.append(edge)
        if current is start:
            break
        if current in vertices:
            raise TransitionError("The selection boundary self-intersects")
        vertices.append(current)
        choices = [item for item in neighbors[current] if item[0] is not previous]
        if len(choices) != 1:
            raise TransitionError("The selection boundary is not a single loop")
        previous, (current, edge) = current, choices[0]
        if len(edges) > len(boundary_edges):
            raise TransitionError("The selection boundary could not be ordered")

    if set(edges) != boundary_edges or len(vertices) != len(boundary_edges):
        raise TransitionError("The selection contains more than one boundary loop")
    return vertices, edges


def _path_between(cycle: list[Any], start: int, end: int) -> list[Any]:
    if start <= end:
        return cycle[start : end + 1]
    return cycle[start:] + cycle[: end + 1]


def _edge_in_path(edge: Any, path: list[Any]) -> bool:
    return any(
        edge.verts[0] in {path[i], path[i + 1]}
        and edge.verts[1] in {path[i], path[i + 1]}
        for i in range(len(path) - 1)
    )


def _active_path_index(bm: Any, paths: list[list[Any]]) -> int | None:
    active = bm.select_history.active
    if isinstance(active, bmesh.types.BMEdge):
        for index, path in enumerate(paths):
            if _edge_in_path(active, path):
                return index
    elif isinstance(active, bmesh.types.BMVert):
        matches = [index for index, path in enumerate(paths) if active in path[1:-1]]
        if len(matches) == 1:
            return matches[0]
    return None


def _validate_structured_rectangle(
    selected_faces: set[Any],
    patch_vertices: set[Any],
    boundary_vertices: list[Any],
    paths: list[list[Any]],
) -> None:
    boundary_set = set(boundary_vertices)
    for vertex in boundary_vertices:
        count = _selected_face_count(vertex, selected_faces)
        expected = 1 if vertex in {path[0] for path in paths} else 2
        if count != expected:
            raise TransitionError(
                "Boundary valence does not match a rectangular quad grid"
            )
    for vertex in patch_vertices - boundary_set:
        if _selected_face_count(vertex, selected_faces) != 4:
            raise TransitionError(
                "Interior valence does not match a rectangular quad grid"
            )

    counts = [len(path) - 1 for path in paths]
    if counts[0] != counts[2] or counts[1] != counts[3]:
        raise TransitionError(
            "Opposite sides of the selected patch have different lengths"
        )
    if len(selected_faces) != counts[0] * counts[1]:
        raise TransitionError(
            "The selected faces are not a complete rectangular quad grid"
        )


def analyze_selected_patch(
    bm: Any,
    *,
    wide_count: int,
    input_count: int,
    output_count: int,
    shoulder_left: int,
    shoulder_right: int,
    axis_mode: str = "AUTO",
    flip_flow: bool = False,
) -> PatchLayout:
    """Validate and partition the currently selected faces.

    The selected rectangle must be ``wide_count`` faces across.  Shoulder
    edges on the narrow side are reassigned to the side boundaries, preserving
    every outside boundary edge while satisfying quad parity.
    """

    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    selected_faces = [face for face in bm.faces if face.select and not face.hide]
    if not selected_faces:
        raise TransitionError("Select a rectangular strip of quad faces")
    if any(len(face.verts) != 4 for face in selected_faces):
        raise TransitionError("The selected patch must contain quads only")
    selected_set = set(selected_faces)
    if _face_component(selected_set) != selected_set:
        raise TransitionError("The selected patch must be one connected region")

    patch_edges = {edge for face in selected_faces for edge in face.edges}
    patch_vertices = {vertex for face in selected_faces for vertex in face.verts}
    if any(len(edge.link_faces) > 2 for edge in patch_edges):
        raise TransitionError("The selected patch touches a non-manifold edge")
    boundary_edges = {
        edge
        for edge in patch_edges
        if sum(face in selected_set for face in edge.link_faces) == 1
    }
    boundary_vertices, _ordered_edges = _ordered_boundary(boundary_edges)

    corners = [
        vertex
        for vertex in boundary_vertices
        if _selected_face_count(vertex, selected_set) == 1
    ]
    if len(corners) != 4:
        raise TransitionError(f"Expected four patch corners, found {len(corners)}")
    corner_indices = sorted(boundary_vertices.index(vertex) for vertex in corners)
    paths = [
        _path_between(
            boundary_vertices,
            corner_indices[index],
            corner_indices[(index + 1) % 4],
        )
        for index in range(4)
    ]
    _validate_structured_rectangle(
        selected_set, patch_vertices, boundary_vertices, paths
    )

    path_counts = [len(path) - 1 for path in paths]
    candidates = [
        axis
        for axis in (0, 1)
        if path_counts[axis] == wide_count and path_counts[axis + 2] == wide_count
    ]
    if not candidates:
        dims = f"{path_counts[0]} x {path_counts[1]}"
        raise TransitionError(
            f"This transition needs a {wide_count}-face-wide strip; selection is {dims}"
        )

    active_path = _active_path_index(bm, paths)
    auto_axis = (
        active_path % 2
        if active_path is not None and active_path % 2 in candidates
        else candidates[0]
    )
    if axis_mode == "ALTERNATE":
        alternatives = [axis for axis in candidates if axis != auto_axis]
        if not alternatives:
            raise TransitionError(
                "The alternate axis does not have the required strip width"
            )
        axis = alternatives[0]
    elif axis_mode == "AUTO":
        axis = auto_axis
    else:
        raise TransitionError(f"Unknown axis mode: {axis_mode}")

    start = axis
    active_side_used = False
    if active_path in {axis, axis + 2}:
        start = active_path
        active_side_used = True
    if flip_flow:
        start = (start + 2) % 4

    full_top = list(paths[start])
    right_vertical = list(paths[(start + 1) % 4])
    opposite = list(paths[(start + 2) % 4])
    left_vertical = list(paths[(start + 3) % 4])
    if shoulder_left + shoulder_right != wide_count - min(input_count, output_count):
        raise TransitionError("Internal shoulder distribution is inconsistent")

    reducing = input_count > output_count
    if reducing:
        segment_end = len(opposite) - shoulder_left
        narrow_segment = opposite[shoulder_right:segment_end]
        top = full_top
        bottom = list(reversed(narrow_segment))
        right = right_vertical + opposite[1 : shoulder_right + 1]
        left = list(reversed(left_vertical))
        if shoulder_left:
            left.extend(reversed(opposite[-(shoulder_left + 1) : -1]))
    else:
        top_end = len(full_top) - shoulder_right
        top = full_top[shoulder_left:top_end]
        bottom = list(reversed(opposite))
        left = list(reversed(full_top[: shoulder_left + 1]))
        left.extend(list(reversed(left_vertical))[1:])
        right_start = len(full_top) - shoulder_right - 1
        right = full_top[right_start:] + right_vertical[1:]

    if len(top) != input_count + 1 or len(bottom) != output_count + 1:
        raise TransitionError("Boundary partition produced the wrong loop counts")
    if left[0] is not top[0] or left[-1] is not bottom[0]:
        raise TransitionError("Left boundary partition is inconsistent")
    if right[0] is not top[-1] or right[-1] is not bottom[-1]:
        raise TransitionError("Right boundary partition is inconsistent")

    return PatchLayout(
        selected_faces=selected_faces,
        patch_edges=patch_edges,
        patch_vertices=patch_vertices,
        boundary_edges=boundary_edges,
        boundary_vertices=boundary_vertices,
        top=top,
        bottom=bottom,
        left=left,
        right=right,
        physical_corners=(
            full_top[0],
            full_top[-1],
            opposite[-1],
            opposite[0],
        ),
        width=wide_count,
        height=path_counts[(axis + 1) % 2],
        active_side_used=active_side_used,
    )


def bind_boundary(template: Any, layout: PatchLayout) -> dict[str, Any]:
    """Bind template boundary keys to existing BMesh vertices."""

    binding: dict[str, Any] = {}
    for keys, vertices, name in (
        (template.top_keys, layout.top, "top"),
        (template.bottom_keys, layout.bottom, "bottom"),
        (template.left_keys, layout.left, "left"),
        (template.right_keys, layout.right, "right"),
    ):
        if len(keys) != len(vertices):
            raise TransitionError(
                f"Template {name} boundary has {len(keys) - 1} edges, "
                f"selection has {len(vertices) - 1}"
            )
        for key, vertex in zip(keys, vertices):
            existing = binding.get(key)
            if existing is not None and existing is not vertex:
                raise TransitionError(f"Template corner binding conflict at {key}")
            binding[key] = vertex
    return binding
