"""Headless Blender integration smoke for Topology Transitions.

Run with:
    blender --background --factory-startup --python tests/blender_smoke.py
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils.bvhtree import BVHTree

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import topology_transitions  # noqa: E402
from topology_transitions.operators import _make_template_and_layout  # noqa: E402

PRESETS = (
    ("FIVE_TO_THREE", 5, 2, 16, 2),
    ("THREE_TO_FIVE", 5, 2, 16, 2),
    ("THREE_TO_ONE", 3, 2, 10, 2),
    ("ONE_TO_THREE", 3, 2, 10, 2),
    ("FOUR_TO_TWO", 4, 2, 13, 2),
    ("TWO_TO_FOUR", 4, 2, 13, 2),
    ("ONE_TO_TWO", 2, 2, 5, 1),
    ("TWO_TO_ONE", 2, 2, 5, 1),
)


def clear_scene() -> None:
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def create_grid(
    name: str,
    patch_width: int,
    patch_height: int,
    *,
    padding: int = 0,
    curved: bool = False,
):
    columns = patch_width + (padding * 2)
    rows = patch_height + (padding * 2)
    vertices = []
    for y in range(rows + 1):
        for x in range(columns + 1):
            z = (
                0.12
                * math.sin((x / max(columns, 1)) * math.pi)
                * math.sin((y / max(rows, 1)) * math.pi)
                if curved
                else 0.0
            )
            vertices.append((float(x), float(y), z))
    faces = []
    for y in range(rows):
        for x in range(columns):
            first = y * (columns + 1) + x
            faces.append(
                (
                    first,
                    first + 1,
                    first + columns + 2,
                    first + columns + 1,
                )
            )

    mesh = bpy.data.meshes.new(f"{name}_mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")

    bm = bmesh.from_edit_mesh(mesh)
    bm.faces.ensure_lookup_table()
    bm.select_mode = {"FACE"}
    for face in bm.faces:
        center = face.calc_center_median()
        selected = (
            padding < center.x < padding + patch_width
            and padding < center.y < padding + patch_height
        )
        face.select_set(selected)
    bm.select_flush_mode()

    target_y = float(padding + patch_height)
    candidate_edges = [
        edge
        for edge in bm.edges
        if edge.select
        and all(abs(vertex.co.y - target_y) < 1.0e-6 for vertex in edge.verts)
        and all(
            padding - 1.0e-6 <= vertex.co.x <= padding + patch_width + 1.0e-6
            for vertex in edge.verts
        )
    ]
    if not candidate_edges:
        raise AssertionError("Failed to find active incoming boundary edge")
    bm.select_history.clear()
    bm.select_history.add(candidate_edges[len(candidate_edges) // 2])
    bmesh.update_edit_mesh(mesh, loop_triangles=True, destructive=False)
    return obj, columns, rows


def selection_snapshot(obj):
    bm = bmesh.from_edit_mesh(obj.data)
    selected = [face for face in bm.faces if face.select]
    selected_set = set(selected)
    boundary_edges = {
        edge
        for face in selected
        for edge in face.edges
        if sum(linked in selected_set for linked in edge.link_faces) == 1
    }
    boundary_vertices = {vertex for edge in boundary_edges for vertex in edge.verts}
    source_vertices = list({vertex for face in selected for vertex in face.verts})
    source_indices = {vertex: index for index, vertex in enumerate(source_vertices)}
    source_bvh = BVHTree.FromPolygons(
        [vertex.co.copy() for vertex in source_vertices],
        [[source_indices[vertex] for vertex in face.verts] for face in selected],
        all_triangles=False,
    )
    return {
        "face_count": len(selected),
        "boundary_coordinates": {tuple(vertex.co) for vertex in boundary_vertices},
        "boundary_face_counts": sorted(len(edge.link_faces) for edge in boundary_edges),
        "bvh": source_bvh,
    }


def assert_transition(
    preset: str,
    width: int,
    height: int,
    expected_faces: int,
    expected_poles: int,
    *,
    padding: int = 0,
    curved: bool = False,
    mirror: bool = False,
) -> None:
    clear_scene()
    obj, columns, rows = create_grid(
        preset,
        width,
        height,
        padding=padding,
        curved=curved,
    )
    before = selection_snapshot(obj)
    original_total_faces = columns * rows
    debug_bm = bmesh.from_edit_mesh(obj.data)
    _incoming, _outgoing, debug_layout, debug_template = _make_template_and_layout(
        debug_bm,
        preset,
        "AUTO",
        False,
        "CENTER",
        mirror,
        1.15,
    )
    print(
        f"QT_TEMPLATE preset={preset} width={debug_layout.width} "
        f"height={debug_layout.height} left={debug_layout.left_segments} "
        f"right={debug_layout.right_segments} faces={len(debug_template.faces)}"
    )
    validation = bpy.ops.mesh.quad_transition_validate(
        transition=preset,
        axis_mode="AUTO",
        flip_flow=False,
        pole_side="CENTER",
        mirror=mirror,
        pole_spacing=1.15,
    )
    if validation != {"FINISHED"}:
        raise AssertionError(f"{preset}: validation operator returned {validation}")
    result = bpy.ops.mesh.quad_transition_apply(
        transition=preset,
        axis_mode="AUTO",
        flip_flow=False,
        pole_side="CENTER",
        mirror=mirror,
        pole_spacing=1.15,
        relax_strength=0.55,
        relax_iterations=24,
        conform_surface=True,
    )
    if result != {"FINISHED"}:
        raise AssertionError(f"{preset} returned {result}")

    bm = bmesh.from_edit_mesh(obj.data)
    selected_faces = [face for face in bm.faces if face.select]
    if len(selected_faces) != expected_faces:
        raise AssertionError(
            f"{preset}: expected {expected_faces} selected quads, "
            f"found {len(selected_faces)}"
        )
    if any(len(face.verts) != 4 for face in bm.faces):
        raise AssertionError(f"{preset}: non-quad face generated")
    if any(len(edge.link_faces) > 2 for edge in bm.edges):
        raise AssertionError(f"{preset}: non-manifold edge generated")
    expected_total = original_total_faces - before["face_count"] + expected_faces
    if len(bm.faces) != expected_total:
        raise AssertionError(
            f"{preset}: expected {expected_total} total faces, found {len(bm.faces)}"
        )

    current_coordinates = {tuple(vertex.co) for vertex in bm.verts}
    if not before["boundary_coordinates"].issubset(current_coordinates):
        raise AssertionError(f"{preset}: a boundary coordinate moved or disappeared")

    selected_vertices = {vertex for face in selected_faces for vertex in face.verts}
    new_vertices = [
        vertex
        for vertex in selected_vertices
        if tuple(vertex.co) not in before["boundary_coordinates"]
    ]
    poles = [vertex for vertex in new_vertices if len(vertex.link_edges) == 3]
    if len(poles) != expected_poles:
        raise AssertionError(
            f"{preset}: expected {expected_poles} new N-poles, found {len(poles)}"
        )
    if curved:
        distances = [
            before["bvh"].find_nearest(vertex.co)[3] for vertex in new_vertices
        ]
        if max(distances, default=0.0) > 1.0e-5:
            raise AssertionError(
                f"{preset}: conformed vertex drifted {max(distances):.6g} from source"
            )
    else:
        if any(abs(vertex.co.z) > 1.0e-7 for vertex in new_vertices):
            raise AssertionError(f"{preset}: planar conformity changed Z")

    print(
        f"QT_PATTERN_PASS preset={preset} selected_quads={len(selected_faces)} "
        f"poles={len(poles)} padding={padding} curved={curved}"
    )


def assert_invalid_selection_is_unchanged() -> None:
    clear_scene()
    obj, _columns, _rows = create_grid("invalid_l", 3, 2)
    bm = bmesh.from_edit_mesh(obj.data)
    selected = [face for face in bm.faces if face.select]
    selected[-1].select_set(False)
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=False)
    before_faces = len(bm.faces)
    before_vertices = len(bm.verts)
    try:
        result = bpy.ops.mesh.quad_transition_apply(
            transition="THREE_TO_ONE",
            relax_iterations=0,
        )
    except RuntimeError as exc:
        if "Expected four patch corners" not in str(exc):
            raise
        result = {"CANCELLED"}
    if result != {"CANCELLED"}:
        raise AssertionError(f"Invalid L selection unexpectedly returned {result}")
    bm = bmesh.from_edit_mesh(obj.data)
    if len(bm.faces) != before_faces or len(bm.verts) != before_vertices:
        raise AssertionError("Invalid selection changed mesh data")
    print("QT_REJECTION_PASS case=l_shaped_selection")


def assert_shape_key_is_unchanged() -> None:
    clear_scene()
    obj, _columns, _rows = create_grid("shape_key", 3, 2)
    bpy.ops.object.mode_set(mode="OBJECT")
    obj.shape_key_add(name="Basis")
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(obj.data)
    before_faces = len(bm.faces)
    try:
        result = bpy.ops.mesh.quad_transition_apply(transition="THREE_TO_ONE")
    except RuntimeError as exc:
        if "Meshes with shape keys" not in str(exc):
            raise
        result = {"CANCELLED"}
    if result != {"CANCELLED"}:
        raise AssertionError(f"Shape-key safety check returned {result}")
    if len(bmesh.from_edit_mesh(obj.data).faces) != before_faces:
        raise AssertionError("Shape-key rejection changed the mesh")
    print("QT_REJECTION_PASS case=shape_keys")


def assert_external_projection_target() -> None:
    clear_scene()
    target_mesh = bpy.data.meshes.new("projection_target_mesh")
    target_mesh.from_pydata(
        [
            (-10.0, -10.0, 0.75),
            (10.0, -10.0, 0.75),
            (10.0, 10.0, 0.75),
            (-10.0, 10.0, 0.75),
        ],
        [],
        [(0, 1, 2, 3)],
    )
    target_mesh.update()
    target = bpy.data.objects.new("ProjectionTarget", target_mesh)
    bpy.context.collection.objects.link(target)

    obj, _columns, _rows = create_grid("external_projection", 3, 2)
    before = selection_snapshot(obj)
    result = bpy.ops.mesh.quad_transition_apply(
        transition="THREE_TO_ONE",
        pole_side="CENTER",
        relax_iterations=8,
        conform_surface=True,
        projection_target_name=target.name,
    )
    if result != {"FINISHED"}:
        raise AssertionError(f"External projection returned {result}")
    bm = bmesh.from_edit_mesh(obj.data)
    selected_vertices = {
        vertex for face in bm.faces if face.select for vertex in face.verts
    }
    new_vertices = [
        vertex
        for vertex in selected_vertices
        if tuple(vertex.co) not in before["boundary_coordinates"]
    ]
    if not new_vertices or any(
        abs(vertex.co.z - 0.75) > 1.0e-5 for vertex in new_vertices
    ):
        raise AssertionError("New interior vertices did not reach the external target")
    print(f"QT_EXTERNAL_PROJECTION_PASS vertices={len(new_vertices)} target_z=0.75")


def assert_subdivision_preview() -> None:
    obj = bpy.context.edit_object
    bpy.ops.object.mode_set(mode="OBJECT")
    result = bpy.ops.object.quad_transition_toggle_subdivision(levels=2)
    if result != {"FINISHED"}:
        raise AssertionError(f"Preview creation returned {result}")
    modifier = obj.modifiers.get("Topology Transition Preview")
    if modifier is None or modifier.type != "SUBSURF" or not modifier.show_viewport:
        raise AssertionError("Subdivision preview modifier was not enabled")
    result = bpy.ops.object.quad_transition_toggle_subdivision(levels=2)
    if result != {"FINISHED"} or modifier.show_viewport:
        raise AssertionError("Subdivision preview modifier did not toggle off")
    print("QT_PREVIEW_PASS levels=2")


def main() -> None:
    topology_transitions.register()
    try:
        for preset, width, height, faces, poles in PRESETS:
            assert_transition(preset, width, height, faces, poles)
        assert_transition("FIVE_TO_THREE", 5, 2, 16, 2, padding=1, curved=True)
        assert_transition("ONE_TO_TWO", 2, 2, 5, 1, mirror=True)
        assert_transition("FIVE_TO_THREE", 5, 1, 11, 2)
        assert_transition("ONE_TO_TWO", 2, 1, 3, 1)
        assert_subdivision_preview()
        assert_external_projection_target()
        assert_invalid_selection_is_unchanged()
        assert_shape_key_is_unchanged()
        print(
            "QT_BLENDER_SMOKE_PASS patterns=13 rejection_cases=2 "
            "preview=1 external_projection=1"
        )
    finally:
        clear_scene()
        topology_transitions.unregister()


if __name__ == "__main__":
    main()
