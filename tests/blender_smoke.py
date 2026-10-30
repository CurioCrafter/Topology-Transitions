"""Headless Blender integration smoke for Topology Transitions.

Run with:
    blender --background --factory-startup --python tests/blender_smoke.py
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import bmesh
import bpy
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import topology_transitions  # noqa: E402
from topology_transitions import optimize_ops  # noqa: E402
from topology_transitions.directional_merge_ops import (  # noqa: E402
    DirectionalMergeError,
    merge_selected_by_direction,
)
from topology_transitions.flow_ops import build_flow_session  # noqa: E402
from topology_transitions.lens_ops import lens_snapshot  # noqa: E402
from topology_transitions.operators import _make_template_and_layout  # noqa: E402
from topology_transitions.paint_grid import Dab  # noqa: E402
from topology_transitions.surface_paint_ops import (  # noqa: E402
    _event_window_coordinate,
    build_surface_paint,
)

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
TRANSITION_ROLE_LAYER = "topology_transition_role"
TRANSITION_ROLE_ADAPTER = 1
TRANSITION_ROLE_CORE = 2


def clear_scene() -> None:
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for obj in bpy.context.scene.objects:
        obj.hide_set(False)
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def create_paint_fixture(name: str, lanes: int):
    """Create a one-row retopo base plus a separate planar paint target."""

    clear_scene()
    target_mesh = bpy.data.meshes.new(f"{name}_target_mesh")
    target_mesh.from_pydata(
        (
            (-4.0, -2.0, 0.5),
            (lanes + 4.0, -2.0, 0.5),
            (lanes + 4.0, 10.0, 0.5),
            (-4.0, 10.0, 0.5),
        ),
        (),
        ((0, 1, 2, 3),),
    )
    target_mesh.update()
    target = bpy.data.objects.new(f"{name}_target", target_mesh)
    bpy.context.collection.objects.link(target)

    vertices = [
        (float(x), float(y), 0.5)
        for y in range(2)
        for x in range(lanes + 1)
    ]
    faces = []
    for x in range(lanes):
        lower = x
        upper = lanes + 1 + x
        faces.append((lower, lower + 1, upper + 1, upper))
    mesh = bpy.data.meshes.new(f"{name}_retopo_mesh")
    mesh.from_pydata(vertices, (), faces)
    mesh.update()
    retopo = bpy.data.objects.new(f"{name}_retopo", mesh)
    bpy.context.collection.objects.link(retopo)
    bpy.context.view_layer.objects.active = retopo
    retopo.select_set(True)
    target.select_set(False)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.context.tool_settings.mesh_select_mode = (False, True, False)
    bm = bmesh.from_edit_mesh(mesh)
    bm.select_mode = {"EDGE"}
    for face in bm.faces:
        face.select_set(False)
    for edge in bm.edges:
        edge.select_set(
            all(abs(vertex.co.y - 1.0) < 1.0e-6 for vertex in edge.verts)
        )
    bm.select_flush_mode()
    bmesh.update_edit_mesh(mesh, loop_triangles=True, destructive=False)
    return retopo, target


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
        "boundary_edge_coordinates": {
            frozenset(tuple(vertex.co) for vertex in edge.verts)
            for edge in boundary_edges
        },
        "boundary_face_counts": sorted(len(edge.link_faces) for edge in boundary_edges),
        "bvh": source_bvh,
    }


def assert_selected_faces_connected(selected_faces, preset: str) -> None:
    selected_set = set(selected_faces)
    pending = [selected_faces[0]] if selected_faces else []
    visited = set()
    while pending:
        face = pending.pop()
        if face in visited:
            continue
        visited.add(face)
        for edge in face.edges:
            pending.extend(
                linked
                for linked in edge.link_faces
                if linked in selected_set and linked not in visited
            )
    if visited != selected_set:
        raise AssertionError(f"{preset}: inserted faces are disconnected")


def assert_internal_loop_directions(selected_faces, preset: str) -> None:
    selected_set = set(selected_faces)
    for edge in {edge for face in selected_faces for edge in face.edges}:
        linked = [face for face in edge.link_faces if face in selected_set]
        if len(linked) != 2:
            continue
        uses = []
        for loop in edge.link_loops:
            if loop.face in selected_set:
                uses.append((loop.vert, loop.link_loop_next.vert))
        if len(uses) != 2 or uses[0] != (uses[1][1], uses[1][0]):
            raise AssertionError(f"{preset}: folded shared edge direction detected")


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


def assert_single_quad_transition(preset: str, *, padding: int = 1) -> None:
    clear_scene()
    obj, columns, rows = create_grid(
        f"single_{preset.lower()}",
        1,
        1,
        padding=padding,
    )
    original_total_faces = columns * rows
    bm = bmesh.from_edit_mesh(obj.data)
    _incoming, _outgoing, layout, template = _make_template_and_layout(
        bm,
        preset,
        "AUTO",
        False,
        "CENTER",
        False,
        1.0,
    )
    if not layout.single_quad_insertion:
        raise AssertionError(f"{preset}: did not choose the single-quad path")

    validation = bpy.ops.mesh.quad_transition_validate(
        transition=preset,
        pole_side="CENTER",
    )
    if validation != {"FINISHED"}:
        raise AssertionError(f"{preset}: single-quad validation returned {validation}")
    result = bpy.ops.mesh.quad_transition_apply(
        transition=preset,
        pole_side="CENTER",
        relax_strength=0.55,
        relax_iterations=24,
        conform_surface=True,
    )
    if result != {"FINISHED"}:
        raise AssertionError(f"{preset}: single-quad apply returned {result}")

    bm = bmesh.from_edit_mesh(obj.data)
    selected_faces = [face for face in bm.faces if face.select]
    role_layer = bm.faces.layers.int.get(TRANSITION_ROLE_LAYER)
    inserted_faces = [
        face
        for face in bm.faces
        if role_layer is not None
        and face[role_layer] in {TRANSITION_ROLE_ADAPTER, TRANSITION_ROLE_CORE}
    ]
    assert_selected_faces_connected(selected_faces, preset)
    assert_internal_loop_directions(selected_faces, preset)
    if len(inserted_faces) != len(template.faces):
        raise AssertionError(
            f"{preset}: expected {len(template.faces)} inserted quads, "
            f"found {len(inserted_faces)}"
        )
    if len(selected_faces) != len(template.faces) or any(
        role_layer is None or face[role_layer] != TRANSITION_ROLE_CORE
        for face in selected_faces
    ):
        raise AssertionError(
            f"{preset}: selected {len(selected_faces)} faces instead of the "
            f"{len(template.faces)} transition-core faces"
        )
    expected_total = original_total_faces - 1 + len(template.faces)
    if len(bm.faces) != expected_total:
        raise AssertionError(
            f"{preset}: expected {expected_total} total faces, found {len(bm.faces)}"
        )
    if any(
        len(face.verts) != 4 or face.calc_area() <= 1.0e-12 for face in bm.faces
    ):
        raise AssertionError(f"{preset}: generated a zero-area or non-quad face")
    if any(len(edge.link_faces) > 2 for edge in bm.edges):
        raise AssertionError(f"{preset}: generated a non-manifold edge")

    inserted_vertices = {
        vertex for face in selected_faces for vertex in face.verts
    }
    if any(abs(vertex.co.z) > 1.0e-7 for vertex in inserted_vertices):
        raise AssertionError(f"{preset}: planar insertion moved away from the source")

    print(
        f"QT_SINGLE_QUAD_PASS preset={preset} inserted={len(inserted_faces)} "
        f"core={len(selected_faces)} "
        f"adapter={len(inserted_faces) - len(selected_faces)} "
        f"embedded={bool(padding)}"
    )


def assert_single_quad_edge_loop_transition() -> None:
    clear_scene()
    obj, _columns, _rows = create_grid("single_edge_loop", 1, 1, padding=0)
    bm = bmesh.from_edit_mesh(obj.data)
    for face in bm.faces:
        face.select_set(False)
    for vertex in bm.verts:
        vertex.select_set(False)
    for edge in bm.edges:
        edge.select_set(True)
    bm.select_mode = {"EDGE"}
    bpy.context.tool_settings.mesh_select_mode = (False, True, False)
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    result = bpy.ops.mesh.quad_transition_apply(
        transition="FIVE_TO_THREE",
        relax_iterations=8,
        conform_surface=True,
    )
    if result != {"FINISHED"}:
        raise AssertionError(f"Single-quad edge-loop apply returned {result}")
    bm = bmesh.from_edit_mesh(obj.data)
    selected_faces = [face for face in bm.faces if face.select]
    role_layer = bm.faces.layers.int.get(TRANSITION_ROLE_LAYER)
    inserted_faces = [
        face
        for face in bm.faces
        if role_layer is not None
        and face[role_layer] in {TRANSITION_ROLE_ADAPTER, TRANSITION_ROLE_CORE}
    ]
    if (
        len(inserted_faces) != 11
        or len(selected_faces) != 11
        or any(len(face.verts) != 4 for face in bm.faces)
        or any(
            role_layer is None or face[role_layer] != TRANSITION_ROLE_CORE
            for face in selected_faces
        )
    ):
        raise AssertionError("Single-quad edge loop did not mark the core correctly")
    assert_selected_faces_connected(selected_faces, "single_edge_loop")
    assert_internal_loop_directions(selected_faces, "single_edge_loop")
    print("QT_SINGLE_QUAD_EDGE_LOOP_PASS preset=FIVE_TO_THREE quads=11 core=11")


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


def assert_edge_flow_step_browser() -> None:
    clear_scene()
    obj, _columns, _rows = create_grid("edge_flow_grid", 4, 3)
    settings = bpy.context.scene.topology_transitions
    settings.flow_mode = "FACE_STRIPS"
    settings.flow_scope = "ALL"
    settings.flow_sort = "LONGEST"
    settings.flow_min_edges = 2
    result = bpy.ops.mesh.quad_transition_edge_flow_step(
        direction=0, select_current=True
    )
    if result != {"FINISHED"}:
        raise AssertionError(f"Edge flow refresh returned {result}")
    if settings.flow_count != 7 or settings.flow_quad_count != 4:
        raise AssertionError(
            f"Expected seven face flows led by four quads, got "
            f"{settings.flow_count} / {settings.flow_quad_count}"
        )
    bm = bmesh.from_edit_mesh(obj.data)
    selected_faces = [face for face in bm.faces if face.select]
    if len(selected_faces) != settings.flow_quad_count or len(selected_faces) != 4:
        raise AssertionError(
            f"Expected current flow to select four quad faces, found "
            f"{len(selected_faces)}"
        )
    result = bpy.ops.mesh.quad_transition_edge_flow_step(
        direction=1, select_current=True
    )
    if result != {"FINISHED"} or settings.flow_index != 1:
        raise AssertionError("Edge flow next step did not advance to index one")

    all_session = build_flow_session(bpy.context)
    for face in bm.faces:
        face.select_set(False)
    for edge in bm.edges:
        edge.select_set(False)
    for vertex in bm.verts:
        vertex.select_set(False)
    bm.select_mode = {"FACE"}
    bpy.context.tool_settings.mesh_select_mode = (False, False, True)
    for face_id in all_session.flows[settings.flow_index].face_ids:
        bm.faces[face_id].select_set(True)
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    settings.flow_scope = "SELECTED"
    settings.flow_min_edges = 2
    result = bpy.ops.mesh.quad_transition_edge_flow_step(
        direction=0, select_current=False
    )
    if result != {"FINISHED"} or settings.flow_count != 1:
        raise AssertionError("Selected scope did not isolate the current flow")
    print("QT_QUAD_FLOW_STEP_PASS all_flows=7 selected_scope=1 flow_quads=4")


def assert_side_to_side_strip_order() -> None:
    clear_scene()
    create_grid("side_to_side_grid", 4, 3)
    settings = bpy.context.scene.topology_transitions
    settings.flow_mode = "FACE_STRIPS"
    settings.flow_scope = "ALL"
    settings.flow_sort = "SIDE_TO_SIDE"
    settings.flow_min_edges = 2
    session = build_flow_session(bpy.context)
    quad_counts = [flow.quad_count for flow in session.flows]
    breaks = [
        index
        for index in range(len(session.flows) - 1)
        if index + 1 not in session.neighbors[index]
    ]
    if quad_counts != [3, 3, 3, 3, 4, 4, 4] or breaks != [3]:
        raise AssertionError(
            f"Unexpected face-band traversal: quads={quad_counts}, breaks={breaks}"
        )
    print("QT_QUAD_FLOW_SIDE_PASS order=3,3,3,3,4,4,4 family_breaks=1")


def assert_example_plane() -> None:
    clear_scene()
    result = bpy.ops.object.quad_transition_add_example_plane()
    if result != {"FINISHED"}:
        raise AssertionError(f"Example plane returned {result}")
    obj = bpy.context.active_object
    if obj is None or obj.name != "TopologyTransitions_Example_Atlas":
        raise AssertionError("Example atlas did not become the active object")
    density_metadata = json.loads(obj.get("density_transitions", "[]"))
    expected_quads = sum(
        item["top_regular_quads"]
        + item["transition_quads"]
        + item["bottom_regular_quads"]
        for item in density_metadata
    )
    if len(density_metadata) != 8 or any(
        item["incoming"] == item["outgoing"] for item in density_metadata
    ):
        raise AssertionError("Example atlas does not record eight unequal densities")
    if len(obj.data.polygons) != expected_quads or any(
        len(polygon.vertices) != 4 for polygon in obj.data.polygons
    ):
        raise AssertionError(
            f"Expected {expected_quads} density-atlas quads, "
            f"found {len(obj.data.polygons)} polygons"
        )
    if len(obj.data.materials) != 3:
        raise AssertionError("Example plane did not receive three reference bands")
    if obj.get("transition_count") != 8:
        raise AssertionError("Example atlas does not advertise all eight transitions")
    labels = [child for child in obj.children if child.type == "FONT"]
    if len(labels) != 8:
        raise AssertionError(f"Expected eight atlas labels, found {len(labels)}")
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(obj.data)
    for face in bm.faces:
        face.select_set(False)
    for edge in bm.edges:
        edge.select_set(False)
    for vertex in bm.verts:
        vertex.select_set(False)
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    settings = bpy.context.scene.topology_transitions
    settings.flow_mode = "REGIONS"
    settings.flow_scope = "ALL"
    settings.flow_sort = "SIDE_TO_SIDE"
    settings.flow_min_edges = 1
    session = build_flow_session(bpy.context)
    memberships = sum(flow.quad_count for flow in session.flows)
    if len(session.flows) <= 8 or memberships != len(bm.faces):
        raise AssertionError(
            f"Atlas region membership was {memberships}, expected {len(bm.faces)}; "
            f"regions={len(session.flows)}"
        )
    print(
        f"QT_EXAMPLE_ATLAS_PASS transitions=8 quads={expected_quads} labels=8 "
        f"regions={len(session.flows)} materials=3 unequal_density=8"
    )


def assert_quad_flow_on_transition() -> None:
    clear_scene()
    create_grid("edge_flow_transition", 5, 2)
    result = bpy.ops.mesh.quad_transition_apply(
        transition="FIVE_TO_THREE",
        relax_iterations=8,
        conform_surface=True,
    )
    if result != {"FINISHED"}:
        raise AssertionError(f"Transition setup returned {result}")
    settings = bpy.context.scene.topology_transitions
    settings.flow_mode = "REGIONS"
    settings.flow_scope = "ALL"
    settings.flow_sort = "LONGEST"
    settings.flow_min_edges = 1
    session = build_flow_session(bpy.context)
    memberships = sum(flow.quad_count for flow in session.flows)
    bm = bmesh.from_edit_mesh(bpy.context.edit_object.data)
    if memberships != len(bm.faces) or len(session.flows) <= 1:
        raise AssertionError(
            f"Transition regions did not cover each quad once: {memberships}; "
            f"regions={len(session.flows)}"
        )
    labels = {flow.start_label for flow in session.flows} | {
        flow.end_label for flow in session.flows
    }
    if "Pole-Separated Region" not in labels:
        raise AssertionError(
            f"Transition regions did not identify pole separation: {labels}"
        )
    print(
        f"QT_QUAD_FLOW_TRANSITION_PASS regions={len(session.flows)} "
        f"covered_quads={memberships}"
    )


def create_custom_mesh(name, vertices, faces):
    clear_scene()
    mesh = bpy.data.meshes.new(f"{name}_mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(mesh)
    bm.select_mode = {"FACE"}
    for face in bm.faces:
        face.select_set(True)
    bm.select_flush_mode()
    bmesh.update_edit_mesh(mesh, loop_triangles=True, destructive=False)
    return obj


def create_radial_nonquad_ring(name: str, sides: int):
    vertices = []
    for radius in (1.0, 2.0):
        vertices.extend(
            (
                radius * math.cos((index / sides) * math.tau),
                radius * math.sin((index / sides) * math.tau),
                0.0,
            )
            for index in range(sides)
        )
    faces = [tuple(range(sides))]
    faces.extend(
        (
            index,
            (index + 1) % sides,
            sides + ((index + 1) % sides),
            sides + index,
        )
        for index in range(sides)
    )
    obj = create_custom_mesh(name, vertices, faces)
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    for face in bm.faces:
        face.select_set(False)
    bm.faces[0].select_set(True)
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=False)
    return obj


def assert_repair_operators() -> None:
    obj = create_custom_mesh(
        "tri_pair",
        ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)),
        ((0, 1, 2), (0, 2, 3)),
    )
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    bm.faces[1].select_set(False)
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=False)
    try:
        result = bpy.ops.mesh.quad_transition_solve_selected_tris()
    except RuntimeError:
        result = {"CANCELLED"}
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    if result != {"FINISHED"} or len(bm.faces) != 1 or len(bm.faces[0].verts) != 4:
        raise AssertionError(f"Triangle pair repair failed: {result} / {len(bm.faces)}")

    obj = create_custom_mesh(
        "boundary_tri",
        ((0, 0, 0), (2, 0, 0), (0.5, 1.5, 0)),
        ((0, 1, 2),),
    )
    result = bpy.ops.mesh.quad_transition_solve_selected_tris()
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    if (
        result != {"FINISHED"}
        or len(bm.faces) != 3
        or any(len(face.verts) != 4 for face in bm.faces)
    ):
        raise AssertionError(
            "Boundary triangle did not become a three-quad center grid"
        )

    hexagon = (
        (0, 0, 0),
        (1, -0.25, 0),
        (2, 0, 0),
        (2, 1, 0),
        (1, 1.25, 0),
        (0, 1, 0),
    )
    obj = create_custom_mesh("even_ngon", hexagon, ((0, 1, 2, 3, 4, 5),))
    result = bpy.ops.mesh.quad_transition_solve_selected_ngons()
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    if (
        result != {"FINISHED"}
        or len(bm.faces) != 2
        or any(len(face.verts) != 4 for face in bm.faces)
    ):
        raise AssertionError("Even n-gon did not become a two-quad fan")

    obj = create_custom_mesh(
        "mixed_pair",
        hexagon,
        ((0, 1, 2), (0, 2, 3, 4, 5)),
    )
    result = bpy.ops.mesh.quad_transition_solve_selected_ngons()
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    if (
        result != {"FINISHED"}
        or len(bm.faces) != 2
        or any(len(face.verts) != 4 for face in bm.faces)
    ):
        raise AssertionError("Triangle plus odd n-gon did not become two quads")

    obj = create_radial_nonquad_ring("embedded_tri", 3)
    result = bpy.ops.mesh.quad_transition_solve_selected_tris()
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    if (
        result != {"FINISHED"}
        or len(bm.faces) != 9
        or any(len(face.verts) != 4 for face in bm.faces)
    ):
        raise AssertionError(
            f"Embedded triangle propagation failed: {result} / {len(bm.faces)}"
        )

    obj = create_radial_nonquad_ring("embedded_pentagon", 5)
    result = bpy.ops.mesh.quad_transition_solve_selected_ngons()
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    if (
        result != {"FINISHED"}
        or len(bm.faces) != 15
        or any(len(face.verts) != 4 for face in bm.faces)
    ):
        raise AssertionError(
            f"Embedded pentagon propagation failed: {result} / {len(bm.faces)}"
        )
    print(
        "QT_REPAIR_PASS tri_pair=1 boundary_tri=3 even_ngon=2 mixed_pair=2 "
        "embedded_tri=9 embedded_pentagon=15"
    )


def assert_mixed_and_edge_boundary_transition_inputs() -> None:
    clear_scene()
    obj, _columns, _rows = create_grid("mixed_input", 3, 2)
    bm = bmesh.from_edit_mesh(obj.data)
    source = bm.faces[0]
    result_faces = bmesh.ops.triangulate(bm, faces=[source])["faces"]
    for face in bm.faces:
        face.select_set(False)
    for face in result_faces:
        face.select_set(True)
    for face in bm.faces:
        face.select_set(True)
    bm.select_mode = {"FACE"}
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=True)
    result = bpy.ops.mesh.quad_transition_apply(
        transition="THREE_TO_ONE",
        relax_iterations=8,
        conform_surface=True,
    )
    bm = bmesh.from_edit_mesh(obj.data)
    if (
        result != {"FINISHED"}
        or len(bm.faces) != 10
        or any(len(face.verts) != 4 for face in bm.faces)
    ):
        raise AssertionError("Mixed-face rectangular patch was not replaced")

    clear_scene()
    obj, _columns, _rows = create_grid("edge_boundary_input", 3, 2)
    bm = bmesh.from_edit_mesh(obj.data)
    for vertex in bm.verts:
        vertex.select_set(False)
    for edge in bm.edges:
        edge.select_set(False)
    for face in bm.faces:
        face.select_set(False)
    boundary = [edge for edge in bm.edges if len(edge.link_faces) == 1]
    for edge in boundary:
        edge.select_set(True)
    bm.select_mode = {"EDGE"}
    bpy.context.tool_settings.mesh_select_mode = (False, True, False)
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    result = bpy.ops.mesh.quad_transition_apply(
        transition="THREE_TO_ONE",
        relax_iterations=8,
        conform_surface=True,
    )
    bm = bmesh.from_edit_mesh(obj.data)
    if (
        result != {"FINISHED"}
        or len(bm.faces) != 10
        or any(len(face.verts) != 4 for face in bm.faces)
    ):
        raise AssertionError("Closed selected edge boundary was not replaced")
    print("QT_TRANSITION_INPUT_PASS mixed_faces=1 closed_edge_boundary=1")


def assert_manifold_diagnostics() -> None:
    obj = create_custom_mesh(
        "open_plane",
        ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)),
        ((0, 1, 2, 3),),
    )
    result = bpy.ops.mesh.quad_transition_check_manifold()
    settings = bpy.context.scene.topology_transitions
    bm = bmesh.from_edit_mesh(obj.data)
    selected_edges = [edge for edge in bm.edges if edge.select]
    if (
        result != {"FINISHED"}
        or settings.manifold_open_edge_count != 4
        or settings.manifold_component_count != 1
        or len(selected_edges) != 4
    ):
        raise AssertionError(
            "Open-plane diagnostic did not select its exact four-edge boundary"
        )
    result = bpy.ops.mesh.quad_transition_manifold_step(direction=0)
    if result != {"FINISHED"} or settings.manifold_current_kind != "Open Boundary":
        raise AssertionError("Manifold issue stepping did not identify open boundary")

    create_custom_mesh(
        "over_connected",
        (
            (0, 0, 0),
            (1, 0, 0),
            (0, 1, 0),
            (0, -1, 0),
            (0.5, 0, 1),
        ),
        ((0, 1, 2), (1, 0, 3), (0, 1, 4)),
    )
    result = bpy.ops.mesh.quad_transition_check_manifold()
    if result != {"FINISHED"} or settings.manifold_nonmanifold_edge_count != 1:
        raise AssertionError("Three-face shared edge was not pinpointed")

    tetra_vertices = (
        (0, 0, 0),
        (1, 0, 0),
        (0, 1, 0),
        (0, 0, 1),
    )
    create_custom_mesh(
        "inconsistent_winding",
        tetra_vertices,
        (
            (0, 2, 1),
            (3, 1, 0),
            (1, 2, 3),
            (2, 0, 3),
        ),
    )
    result = bpy.ops.mesh.quad_transition_check_manifold()
    bm = bmesh.from_edit_mesh(bpy.context.edit_object.data)
    selected_edges = [edge for edge in bm.edges if edge.select]
    if (
        result != {"FINISHED"}
        or settings.manifold_winding_edge_count != 3
        or settings.manifold_open_edge_count != 0
        or len(selected_edges) != 3
    ):
        raise AssertionError("Reversed tetra face did not expose three winding edges")

    create_custom_mesh(
        "touching_closed_shells",
        (
            (0, 0, 0),
            (1, 0, 0),
            (0, 1, 0),
            (0, 0, 1),
            (-1, 0, 0),
            (0, -1, 0),
            (0, 0, -1),
        ),
        (
            (0, 2, 1),
            (0, 1, 3),
            (1, 2, 3),
            (2, 0, 3),
            (0, 5, 4),
            (0, 4, 6),
            (4, 5, 6),
            (5, 0, 6),
        ),
    )
    result = bpy.ops.mesh.quad_transition_check_manifold()
    bm = bmesh.from_edit_mesh(bpy.context.edit_object.data)
    selected_vertices = [vertex.index for vertex in bm.verts if vertex.select]
    if (
        result != {"FINISHED"}
        or settings.manifold_disconnected_fan_count != 1
        or settings.manifold_issue_count != 1
        or selected_vertices != [0]
    ):
        raise AssertionError(
            "Touching closed shells did not expose their shared vertex"
        )

    clear_scene()
    bpy.ops.mesh.primitive_cube_add()
    bpy.ops.object.mode_set(mode="EDIT")
    result = bpy.ops.mesh.quad_transition_check_manifold()
    if result != {"FINISHED"} or settings.manifold_issue_count != 0:
        raise AssertionError("Closed cube did not report as manifold")
    print(
        "QT_MANIFOLD_PASS open_plane_edges=4 open_components=1 "
        "over_connected_edges=1 winding_edges=3 disconnected_fans=1 "
        "closed_cube_issues=0"
    )


def assert_auto_nonmanifold_solver() -> None:
    clear_scene()
    bpy.ops.mesh.primitive_cube_add(size=2.0)
    cube = bpy.context.active_object
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(cube.data)
    top = max(bm.faces, key=lambda face: face.calc_center_median().z)
    bmesh.ops.delete(bm, geom=[top], context="FACES_ONLY")
    bmesh.update_edit_mesh(cube.data, loop_triangles=True, destructive=True)
    result = bpy.ops.mesh.quad_transition_solve_all_nonmanifold()
    bm = bmesh.from_edit_mesh(cube.data)
    if (
        result != {"FINISHED"}
        or len(bm.faces) != 6
        or any(len(face.verts) != 4 for face in bm.faces)
        or any(len(edge.link_faces) != 2 for edge in bm.edges)
    ):
        raise AssertionError("Four-edge cube hole did not take the one-quad path")
    bpy.ops.object.mode_set(mode="OBJECT")

    tetra = create_custom_mesh(
        "triangular_hole",
        ((0, 0, 0), (2, 0, 0), (0, 2, 0), (0, 0, 2)),
        ((0, 3, 1), (1, 3, 2), (2, 3, 0)),
    )
    bpy.ops.object.mode_set(mode="OBJECT")
    result = bpy.ops.mesh.quad_transition_solve_all_nonmanifold()
    bm = bmesh.new()
    bm.from_mesh(tetra.data)
    try:
        hole_center = min(
            bm.verts,
            key=lambda vertex: (vertex.co - Vector((2 / 3, 2 / 3, 0))).length,
        )
        center_faces = list(hole_center.link_faces)
        if (
            result != {"FINISHED"}
            or len(bm.faces) != 12
            or len(center_faces) != 3
            or any(len(face.verts) != 4 for face in bm.faces)
            or any(len(edge.link_faces) != 2 for edge in bm.edges)
        ):
            raise AssertionError(
                "Triangular hole did not become three conforming quad flows"
            )
    finally:
        bm.free()

    duplicate = create_custom_mesh(
        "duplicate_cube_face",
        (
            (-1, -1, -1),
            (1, -1, -1),
            (1, 1, -1),
            (-1, 1, -1),
            (-1, -1, 1),
            (1, -1, 1),
            (1, 1, 1),
            (-1, 1, 1),
        ),
        (
            (0, 3, 2, 1),
            (4, 5, 6, 7),
            (0, 1, 5, 4),
            (1, 2, 6, 5),
            (2, 3, 7, 6),
            (3, 0, 4, 7),
            (4, 5, 6, 7),
        ),
    )
    bpy.ops.object.mode_set(mode="OBJECT")
    result = bpy.ops.mesh.quad_transition_solve_all_nonmanifold()
    bm = bmesh.new()
    bm.from_mesh(duplicate.data)
    try:
        if (
            result != {"FINISHED"}
            or len(bm.faces) != 6
            or any(len(edge.link_faces) != 2 for edge in bm.edges)
        ):
            raise AssertionError("Over-connected exact duplicate face was not removed")
    finally:
        bm.free()

    clear_scene()
    bpy.ops.mesh.primitive_cube_add(size=2.0)
    linked_source = bpy.context.active_object
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(linked_source.data)
    top = max(bm.faces, key=lambda face: face.calc_center_median().z)
    bmesh.ops.delete(bm, geom=[top], context="FACES_ONLY")
    bmesh.update_edit_mesh(linked_source.data, loop_triangles=True, destructive=True)
    bpy.ops.object.mode_set(mode="OBJECT")
    linked_copy = bpy.data.objects.new("LinkedUnchanged", linked_source.data)
    bpy.context.collection.objects.link(linked_copy)
    linked_copy.select_set(False)
    bpy.context.view_layer.objects.active = linked_source
    result = bpy.ops.mesh.quad_transition_solve_all_nonmanifold()
    if (
        result != {"FINISHED"}
        or linked_source.data == linked_copy.data
        or len(linked_source.data.polygons) != 6
        or len(linked_copy.data.polygons) != 5
    ):
        raise AssertionError("Object Mode repair changed an unselected linked mesh")

    clear_scene()
    bpy.ops.mesh.primitive_cube_add(size=2.0)
    loose = bpy.context.active_object
    bm = bmesh.new()
    bm.from_mesh(loose.data)
    first = bm.verts.new((3.0, 0.0, 0.0))
    second = bm.verts.new((4.0, 0.0, 0.0))
    bm.edges.new((first, second))
    bm.verts.new((5.0, 0.0, 0.0))
    bm.to_mesh(loose.data)
    bm.free()
    result = bpy.ops.mesh.quad_transition_solve_all_nonmanifold()
    if (
        result != {"FINISHED"}
        or len(loose.data.vertices) != 8
        or len(loose.data.edges) != 12
        or len(loose.data.polygons) != 6
    ):
        raise AssertionError("Loose wire and isolated vertex cleanup failed")

    unresolved = create_custom_mesh(
        "ambiguous_over_connected",
        (
            (0, 0, 0),
            (1, 0, 0),
            (0, 1, 0),
            (0, -1, 0),
            (0.5, 0, 1),
        ),
        ((0, 1, 2), (1, 0, 3), (0, 1, 4)),
    )
    result = bpy.ops.mesh.quad_transition_solve_all_nonmanifold()
    settings = bpy.context.scene.topology_transitions
    bm = bmesh.from_edit_mesh(unresolved.data)
    if (
        result != {"FINISHED"}
        or settings.manifold_nonmanifold_edge_count != 1
        or not any(edge.select for edge in bm.edges if len(edge.link_faces) > 2)
    ):
        raise AssertionError("Ambiguous over-connected sheet was not retained/selected")
    bpy.ops.object.mode_set(mode="OBJECT")

    # The top face has its own copies of its four corners: a split seam, as
    # glTF exports and AI scans have. It must be welded, not capped.
    corners = [(x, y, z) for z in (0, 1) for y in (0, 1) for x in (0, 1)]
    seamed = create_custom_mesh(
        "split_seam_cube",
        (*corners, (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)),
        (
            (0, 2, 3, 1),
            (0, 1, 5, 4),
            (1, 3, 7, 5),
            (3, 2, 6, 7),
            (2, 0, 4, 6),
            (8, 9, 10, 11),
        ),
    )
    result = bpy.ops.mesh.quad_transition_solve_all_nonmanifold()
    bm = bmesh.from_edit_mesh(seamed.data)
    if (
        result != {"FINISHED"}
        or len(bm.verts) != 8
        or len(bm.faces) != 6
        or any(len(edge.link_faces) != 2 for edge in bm.edges)
    ):
        raise AssertionError("Split seam was not welded into a closed cube")
    print(
        "QT_AUTO_MANIFOLD_PASS cube_hole_quads=1 triangle_hole_quads=3 "
        "tetra_total_quads=12 duplicate_removed=1 linked_safe=1 "
        "loose_removed=3 ambiguous_selected=1 seam_welded=4"
    )


def assert_quad_flow_optimization() -> None:
    clear_scene()
    auto_obj, _columns, _rows = create_grid("optimize_auto", 8, 8)
    settings = bpy.context.scene.topology_transitions
    settings.optimize_sensitivity = "CONSERVATIVE"
    settings.optimize_scope = "ALL"
    settings.optimize_cycles = 1
    conservative = bpy.ops.mesh.quad_transition_analyze_optimization()
    conservative_bm = bmesh.from_edit_mesh(auto_obj.data)
    if (
        conservative != {"FINISHED"}
        or settings.optimize_candidate_loops != 0
        or "boundar" not in settings.optimize_diagnostic.lower()
        or sum(face.select for face in conservative_bm.faces) != 64
    ):
        raise AssertionError("Conservative diagnostics did not explain blocked flows")
    settings.optimize_sensitivity = "AUTO"
    automatic = bpy.ops.mesh.quad_transition_optimize_flows()
    auto_bm = bmesh.from_edit_mesh(auto_obj.data)
    if (
        automatic != {"FINISHED"}
        or len(auto_bm.faces) != 16
        or settings.optimize_effective_mode != "Balanced"
        or any(len(face.verts) != 4 for face in auto_bm.faces)
    ):
        raise AssertionError("Automatic sensitivity did not relax to Balanced")

    clear_scene()
    obj, _columns, _rows = create_grid("optimize_regular", 8, 8)
    bm = bmesh.from_edit_mesh(obj.data)
    crease_layer = bm.edges.layers.float.get("crease_edge")
    if crease_layer is None:
        crease_layer = bm.edges.layers.float.new("crease_edge")
    uv_layer = bm.loops.layers.uv.get("UVMap")
    if uv_layer is None:
        uv_layer = bm.loops.layers.uv.new("UVMap")
    for face in bm.faces:
        for loop in face.loops:
            loop[uv_layer].uv = (loop.vert.co.x / 8.0, loop.vert.co.y / 8.0)
    feature_edges = [
        edge
        for edge in bm.edges
        if all(abs(vertex.co.y - 4.0) < 1.0e-6 for vertex in edge.verts)
    ]
    for edge in feature_edges:
        edge.seam = True
        edge.smooth = False
        edge[crease_layer] = 0.75
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)

    settings.optimize_sensitivity = "CUSTOM"
    settings.optimize_scope = "SELECTED"
    settings.optimize_cycles = 2
    settings.optimize_max_face_angle = 1.0
    settings.optimize_feature_angle = 20.0
    settings.optimize_max_error_percent = 0.1
    settings.optimize_branch_guard_rings = 0
    settings.optimize_min_loop_edges = 4
    settings.optimize_preserve_boundaries = False
    settings.optimize_preserve_seams = True
    settings.optimize_preserve_sharp = True
    settings.optimize_preserve_creases = True
    settings.optimize_preserve_materials = True

    analyze = bpy.ops.mesh.quad_transition_analyze_optimization()
    if (
        analyze != {"FINISHED"}
        or settings.optimize_candidate_loops < 1
        or settings.optimize_dissolved_loops < 1
    ):
        raise AssertionError("Quad optimization analysis found no regular flows")

    result = bpy.ops.mesh.quad_transition_optimize_flows()
    bm = bmesh.from_edit_mesh(obj.data)
    crease_layer = bm.edges.layers.float.get("crease_edge")
    uv_layer = bm.loops.layers.uv.get("UVMap")
    preserved = [
        edge
        for edge in bm.edges
        if edge.seam
        and not edge.smooth
        and crease_layer is not None
        and edge[crease_layer] >= 0.74
    ]
    uv_error = max(
        (
            loop[uv_layer].uv
            - Vector((loop.vert.co.x / 8.0, loop.vert.co.y / 8.0))
        ).length
        for face in bm.faces
        for loop in face.loops
    )
    if (
        result != {"FINISHED"}
        or len(bm.faces) != 4
        or any(len(face.verts) != 4 for face in bm.faces)
        or len(preserved) != 2
        or settings.optimize_completed_cycles != 2
        or settings.optimize_removed_faces != 60
        or settings.optimize_max_measured_error > 1.0e-6
        or uv_error > 1.0e-6
        or bm.faces.layers.int.get("__topology_optimize_scope__") is not None
    ):
        raise AssertionError(
            "Two-cycle feature-aware optimization did not reduce 64 quads to 4"
        )

    vertices = []
    faces = []
    for region in range(2):
        offset = len(vertices)
        for y in range(5):
            for x in range(5):
                z = (
                    0.0
                    if region == 0
                    else 0.35 * ((x - 2.0) ** 2 + (y - 2.0) ** 2)
                )
                vertices.append((float(x + region * 8), float(y), z))
        for y in range(4):
            for x in range(4):
                first = offset + y * 5 + x
                faces.append((first, first + 1, first + 6, first + 5))
    mixed = create_custom_mesh("optimize_flat_and_curved", vertices, faces)
    settings.optimize_scope = "ALL"
    settings.optimize_cycles = 1
    settings.optimize_max_face_angle = 0.1
    settings.optimize_max_error_percent = 0.01
    result = bpy.ops.mesh.quad_transition_optimize_flows()
    bm = bmesh.from_edit_mesh(mixed.data)
    curved_faces = [face for face in bm.faces if face.calc_center_median().x > 6.0]
    if (
        result != {"FINISHED"}
        or len(bm.faces) != 20
        or len(curved_faces) != 16
        or any(len(face.verts) != 4 for face in bm.faces)
    ):
        raise AssertionError(
            "Optimizer did not simplify the flat region while retaining curvature"
        )

    clear_scene()
    bpy.ops.mesh.primitive_cube_add(size=2.0)
    closed = bpy.context.active_object
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(closed.data)
    original_corners = {
        tuple(round(value, 6) for value in vertex.co) for vertex in bm.verts
    }
    bmesh.ops.subdivide_edges(
        bm,
        edges=list(bm.edges),
        cuts=7,
        use_grid_fill=True,
    )
    bmesh.update_edit_mesh(closed.data, loop_triangles=True, destructive=True)
    dense_faces = len(bm.faces)
    settings.optimize_cycles = 1
    settings.optimize_max_face_angle = 1.0
    settings.optimize_feature_angle = 20.0
    settings.optimize_max_error_percent = 0.1
    settings.optimize_branch_guard_rings = 1
    settings.optimize_preserve_boundaries = True
    result = bpy.ops.mesh.quad_transition_optimize_flows()
    bm = bmesh.from_edit_mesh(closed.data)
    remaining_coordinates = {
        tuple(round(value, 6) for value in vertex.co) for vertex in bm.verts
    }
    if (
        result != {"FINISHED"}
        or len(bm.faces) >= dense_faces
        or any(len(face.verts) != 4 for face in bm.faces)
        or any(len(edge.link_faces) != 2 for edge in bm.edges)
        or not original_corners <= remaining_coordinates
    ):
        raise AssertionError(
            "Closed optimization did not preserve branch corners and manifold quads"
        )
    closed_faces_after = len(bm.faces)

    clear_scene()
    dense, _columns, _rows = create_grid("optimize_dense", 64, 64)
    settings.optimize_scope = "ALL"
    settings.optimize_cycles = 1
    settings.optimize_branch_guard_rings = 0
    settings.optimize_preserve_boundaries = False
    result = bpy.ops.mesh.quad_transition_optimize_flows()
    bm = bmesh.from_edit_mesh(dense.data)
    if (
        result != {"FINISHED"}
        or len(bm.faces) != 1024
        or any(len(face.verts) != 4 for face in bm.faces)
    ):
        raise AssertionError("Dense 4,096-quad optimization did not reach 1,024")

    clear_scene()
    isolated, _columns, _rows = create_grid("optimize_batch_isolation", 8, 8)
    original_apply_batch = optimize_ops._apply_candidate_batch

    def reject_large_batch(test_bm, candidates, selected_ids, options):
        if len(selected_ids) > 2:
            raise optimize_ops.QuadOptimizeError(
                "Candidate batch changed protected topology "
                "(boundary components 1->2)"
            )
        return original_apply_batch(test_bm, candidates, selected_ids, options)

    optimize_ops._apply_candidate_batch = reject_large_batch
    try:
        result = bpy.ops.mesh.quad_transition_optimize_flows()
    finally:
        optimize_ops._apply_candidate_batch = original_apply_batch
    bm = bmesh.from_edit_mesh(isolated.data)
    if (
        result != {"FINISHED"}
        or not 0 < len(bm.faces) < 64
        or any(len(face.verts) != 4 for face in bm.faces)
        or "batch isolation" not in settings.optimize_status.lower()
        or "boundary components 1->2" not in settings.optimize_diagnostic
    ):
        raise AssertionError("Rejected candidate batch was not safely isolated")

    clear_scene()
    linked_source, _columns, _rows = create_grid("optimize_linked", 4, 4)
    bpy.ops.object.mode_set(mode="OBJECT")
    linked_copy = bpy.data.objects.new("OptimizeLinkedUnchanged", linked_source.data)
    bpy.context.collection.objects.link(linked_copy)
    linked_copy.select_set(False)
    bpy.context.view_layer.objects.active = linked_source
    result = bpy.ops.mesh.quad_transition_optimize_flows()
    if (
        result != {"FINISHED"}
        or linked_source.data == linked_copy.data
        or len(linked_source.data.polygons) != 4
        or len(linked_copy.data.polygons) != 16
    ):
        raise AssertionError("Object Mode optimization changed linked mesh data")

    clear_scene()
    shape_key_obj, _columns, _rows = create_grid("optimize_shape_key", 4, 4)
    bpy.ops.object.mode_set(mode="OBJECT")
    shape_key_obj.shape_key_add(name="Basis")
    try:
        result = bpy.ops.mesh.quad_transition_optimize_flows()
    except RuntimeError as exc:
        if "shape keys" not in str(exc):
            raise
        result = {"CANCELLED"}
    if result != {"CANCELLED"} or len(shape_key_obj.data.polygons) != 16:
        raise AssertionError("Shape-key optimization was not rejected unchanged")
    print(
        "QT_QUAD_OPTIMIZE_PASS auto=Balanced auto_faces=64->16 diagnostics=boundary "
        "cycles=2 faces=64->4 feature_edges=8->2 "
        f"flat_region=16->4 curved_region=16->16 "
        f"closed={dense_faces}->{closed_faces_after} dense=4096->1024 "
        f"uv_error={uv_error:.3g} batch_isolation=1 linked_safe=1 "
        "shape_key_reject=1 all_quad=1"
    )


def assert_surface_paint() -> None:
    lanes = 5
    coordinate = _event_window_coordinate(
        SimpleNamespace(mouse_x=350, mouse_y=250),
        SimpleNamespace(x=100, y=50, width=800, height=600),
    )
    outside = _event_window_coordinate(
        SimpleNamespace(mouse_x=99, mouse_y=250),
        SimpleNamespace(x=100, y=50, width=800, height=600),
    )
    if coordinate != (250.0, 200.0) or outside is not None:
        raise AssertionError("Modal viewport coordinates failed")
    results = {}
    for weld in (True, False):
        retopo, target = create_paint_fixture(f"paint_weld_{weld}", lanes)
        original_faces = len(retopo.data.polygons)
        stroke = [Dab((x * 0.5, 1.5, 0.5), 0.6) for x in range(1, 10)]
        stats = build_surface_paint(
            bpy.context,
            [stroke],
            [[Vector((0.0, 0.0, 1.0))] * len(stroke)],
            target=target,
            cell=0.5,
            weld=weld,
        )
        bm = bmesh.from_edit_mesh(retopo.data)
        selected = [face for face in bm.faces if face.select]
        if (
            stats["quads"] < 8
            or len(selected) != stats["quads"]
            or len(bm.faces) != original_faces + stats["quads"]
            or any(len(face.verts) != 4 for face in bm.faces)
            or any(len(edge.link_faces) > 2 for edge in bm.edges)
            or any(abs(vertex.co.z - 0.5) > 1.0e-5 for vertex in bm.verts)
            or stats["interior_poles"] != 0
        ):
            raise AssertionError(f"Surface paint grid failed checks: {stats}")
        results[weld] = stats
    if results[True]["welded"] < 3 or results[False]["welded"] != 0:
        raise AssertionError(f"Surface paint weld failed: {results}")

    retopo, target = create_paint_fixture("paint_mirror", lanes)
    stroke = [Dab((0.6 + x * 0.3, 4.0, 0.5), 0.7) for x in range(5)]
    mirrored = build_surface_paint(
        bpy.context,
        [stroke],
        [[Vector((0.0, 0.0, 1.0))] * len(stroke)],
        target=target,
        cell=0.25,
        mirror_x=True,
    )
    bm = bmesh.from_edit_mesh(retopo.data)
    new = [face for face in bm.faces if face.select]
    left = sum(1 for face in new if face.calc_center_median().x < 0.0)
    right = len(new) - left
    if (
        not new
        or left != right
        or any(len(edge.link_faces) > 2 for edge in bm.edges)
        or not any(abs(vertex.co.x) < 1.0e-6 for vertex in bm.verts if vertex.select)
    ):
        raise AssertionError(f"Surface paint mirror failed: {mirrored} {left}/{right}")

    clear_scene()
    bpy.ops.mesh.primitive_cube_add()
    bpy.ops.object.mode_set(mode="EDIT")
    snapshot = lens_snapshot(bpy.context.edit_object)
    if snapshot["counts"]["n_pole"] != 8 or snapshot["faces"]["quads"] != 6:
        raise AssertionError(f"Topology Lens cube counts failed: {snapshot['counts']}")
    print(
        f"QT_SURFACE_PAINT_PASS quads={results[True]['quads']} "
        f"welded={results[True]['welded']} unwelded=0 "
        f"mirrored={mirrored['quads']} lens_cube_poles=8"
    )


def _world_normal_faces_axis(obj, polygon, axis: Vector) -> bool:
    normal_matrix = obj.matrix_world.to_3x3().inverted_safe().transposed()
    normal = normal_matrix @ polygon.normal
    if normal.length_squared <= 1.0e-16:
        return False
    normal.normalize()
    dot = normal.dot(axis)
    if dot <= 1.0e-6:
        return False
    component = max(range(3), key=lambda index: abs(axis[index]))
    return abs(normal[component]) + 1.0e-6 >= max(
        abs(normal.x), abs(normal.y), abs(normal.z)
    )


def assert_directional_face_delete() -> None:
    axes = {
        "POS_X": Vector((1.0, 0.0, 0.0)),
        "NEG_X": Vector((-1.0, 0.0, 0.0)),
        "POS_Y": Vector((0.0, 1.0, 0.0)),
        "NEG_Y": Vector((0.0, -1.0, 0.0)),
        "POS_Z": Vector((0.0, 0.0, 1.0)),
        "NEG_Z": Vector((0.0, 0.0, -1.0)),
    }
    for axis, vector in axes.items():
        clear_scene()
        bpy.ops.mesh.primitive_cube_add(size=2.0)
        obj = bpy.context.object
        if obj is None:
            raise AssertionError("Cube was not created for directional delete")
        result = bpy.ops.object.quad_transition_delete_faces_by_axis(axis=axis)
        if result != {"FINISHED"} or len(obj.data.polygons) != 5:
            raise AssertionError(
                f"Directional delete {axis} failed: {result}, "
                f"{len(obj.data.polygons)} faces"
            )
        if any(
            _world_normal_faces_axis(obj, polygon, vector)
            for polygon in obj.data.polygons
        ):
            raise AssertionError(f"Directional delete left a {axis} face behind")

    clear_scene()
    mesh = bpy.data.meshes.new("linked_direction_delete_mesh")
    mesh.from_pydata(
        (
            (-1.0, -1.0, 0.0),
            (1.0, -1.0, 0.0),
            (1.0, 1.0, 0.0),
            (-1.0, 1.0, 0.0),
        ),
        (),
        ((0, 1, 2, 3),),
    )
    mesh.update()
    selected = bpy.data.objects.new("linked_direction_delete_selected", mesh)
    duplicate = bpy.data.objects.new("linked_direction_delete_duplicate", mesh)
    bpy.context.collection.objects.link(selected)
    bpy.context.collection.objects.link(duplicate)
    bpy.context.view_layer.objects.active = selected
    selected.select_set(True)
    duplicate.select_set(False)
    result = bpy.ops.object.quad_transition_delete_faces_by_axis(axis="POS_Z")
    if (
        result != {"FINISHED"}
        or selected.data is duplicate.data
        or len(selected.data.polygons) != 0
        or len(duplicate.data.polygons) != 1
    ):
        raise AssertionError("Directional delete did not protect linked mesh data")
    print("QT_DIRECTIONAL_DELETE_PASS axes=6 linked_single_user=1")


def assert_target_mesh_connector() -> None:
    clear_scene()
    source_mesh = bpy.data.meshes.new("connector_source_mesh")
    source_mesh.from_pydata(
        ((-0.5, -0.5, 0.05), (0.5, -0.5, 0.05),
         (0.5, 0.5, 0.05), (-0.5, 0.5, 0.05)),
        (),
        ((0, 1, 2, 3),),
    )
    source_mesh.polygons[0].select = True
    source = bpy.data.objects.new("ConnectorSource", source_mesh)
    bpy.context.collection.objects.link(source)

    target_mesh = bpy.data.meshes.new("connector_target_mesh")
    target_mesh.from_pydata(
        (
            (-1.5, -0.5, 0.0), (-0.5, -0.5, 0.0),
            (0.5, -0.5, 0.0), (1.5, -0.5, 0.0),
            (-1.5, 0.5, 0.0), (-0.5, 0.5, 0.0),
            (0.5, 0.5, 0.0), (1.5, 0.5, 0.0),
        ),
        (),
        ((0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3)),
    )
    target = bpy.data.objects.new("ConnectorTarget", target_mesh)
    bpy.context.collection.objects.link(target)
    source.select_set(True)
    target.select_set(True)
    bpy.context.view_layer.objects.active = source

    preview_result = bpy.ops.object.quad_transition_preview_target_connection(
        distance=0.06, opacity=0.42
    )
    preview = bpy.data.objects.get("Topology Transition Target Delete Preview")
    if (
        preview_result != {"FINISHED"}
        or preview is None
        or len(preview.data.polygons) != 1
        or abs(preview.color[3] - 0.42) > 1.0e-5
    ):
        raise AssertionError("Target connector did not create its red face preview")

    wide_preview_result = bpy.ops.object.quad_transition_preview_target_connection(
        distance=0.51, opacity=0.42
    )
    wide_preview = bpy.data.objects.get("Topology Transition Target Delete Preview")
    if (
        wide_preview_result != {"FINISHED"}
        or wide_preview is None
        or len(wide_preview.data.polygons) != 3
    ):
        raise AssertionError("Nearby Delete slider did not expand the red preview")

    result = bpy.ops.object.quad_transition_build_target_connection(distance=0.06)
    connected = bpy.context.active_object
    if (
        result != {"FINISHED"}
        or connected is None
        or connected in {source, target}
        or len(connected.data.polygons) != 10
        or sum(len(face.vertices) == 3 for face in connected.data.polygons) != 8
        or sum(len(face.vertices) == 4 for face in connected.data.polygons) != 2
        or not source.hide_get()
        or not target.hide_get()
        or bpy.data.objects.get("Topology Transition Target Delete Preview") is not None
    ):
        raise AssertionError(
            "Target connector did not create the expected triangle bridge"
        )
    print(
        "QT_TARGET_CONNECTOR_PASS preview_faces=1 expanded_preview=3 opacity=0.42 "
        "deleted_source=1 deleted_target=1 bridge_triangles=8 originals=hidden"
    )

    clear_scene()
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, -0.5))
    solid_target = bpy.context.object
    solid_target.name = "ConnectorSolidTarget"
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, 0.55))
    solid_source = bpy.context.object
    solid_source.name = "ConnectorSolidSource"
    for polygon in solid_source.data.polygons:
        polygon.select = polygon.center.z < -0.49
    solid_target.select_set(True)
    solid_source.select_set(True)
    bpy.context.view_layer.objects.active = solid_source
    solid_result = bpy.ops.object.quad_transition_build_target_connection(
        distance=0.06
    )
    solid = bpy.context.active_object
    solid_bm = bmesh.new()
    try:
        solid_bm.from_mesh(solid.data)
        manifold = all(len(edge.link_faces) == 2 for edge in solid_bm.edges)
    finally:
        solid_bm.free()
    if (
        solid_result != {"FINISHED"}
        or len(solid.data.polygons) != 18
        or sum(len(face.vertices) == 3 for face in solid.data.polygons) != 8
        or not manifold
    ):
        raise AssertionError("Solid target connector result was not manifold")
    print("QT_TARGET_CONNECTOR_MANIFOLD_PASS faces=18 triangles=8 edge_faces=2")


def assert_detached_face_xray() -> None:
    clear_scene()
    vertices = [
        (float(x), float(y), 0.0) for y in range(3) for x in range(3)
    ]
    faces = [
        (0, 1, 4, 3),
        (1, 2, 5, 4),
        (3, 4, 7, 6),
        (4, 5, 8, 7),
    ]
    vertices.extend(
        ((0.8, 0.8, 0.04), (1.2, 0.8, 0.04), (1.0, 1.2, 0.04))
    )
    faces.append((9, 10, 11))
    vertices.extend(
        (
            (0.0, 0.2, 0.03),
            (1.0, 0.2, 0.03),
            (2.0, 0.2, 0.03),
            (0.0, 1.2, 0.03),
            (1.0, 1.2, 0.03),
            (2.0, 1.2, 0.03),
        )
    )
    faces.extend(((12, 13, 16, 15), (13, 14, 17, 16)))
    mesh = bpy.data.meshes.new("detached_xray_mesh")
    mesh.from_pydata(vertices, (), faces)
    mesh.update()
    obj = bpy.data.objects.new("DetachedXRay", mesh)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")

    inspect = bpy.ops.mesh.quad_transition_inspect_detached_faces(opacity=0.27)
    settings = bpy.context.scene.topology_transitions
    overlay = bpy.data.objects.get("Topology Transition Detached Face X-Ray")
    bm = bmesh.from_edit_mesh(mesh)
    if (
        inspect != {"FINISHED"}
        or settings.loose_xray_component_count != 2
        or settings.loose_xray_face_count != 3
        or overlay is None
        or len(overlay.data.polygons) != 3
        or not overlay.show_in_front
        or len([face for face in bm.faces if face.select]) != 1
    ):
        raise AssertionError("Detached Face X-Ray did not identify both error regions")

    step = bpy.ops.mesh.quad_transition_step_detached_faces(
        direction=1, opacity=0.27
    )
    bm = bmesh.from_edit_mesh(mesh)
    selected = [face for face in bm.faces if face.select]
    if (
        step != {"FINISHED"}
        or settings.loose_xray_component_index != 1
        or len(selected) != 2
    ):
        raise AssertionError("Detached Face X-Ray did not step to the next region")
    bmesh.ops.delete(bm, geom=selected, context="FACES")
    bmesh.update_edit_mesh(mesh, loop_triangles=True, destructive=True)
    bpy.ops.mesh.quad_transition_inspect_detached_faces(opacity=0.27)
    bm = bmesh.from_edit_mesh(mesh)
    if (
        settings.loose_xray_component_count != 1
        or settings.loose_xray_face_count != 1
        or len([face for face in bm.faces if face.select]) != 1
    ):
        raise AssertionError("Detached Face X-Ray did not refresh after a repair")
    bmesh.ops.delete(
        bm,
        geom=[face for face in bm.faces if face.select],
        context="FACES",
    )
    bmesh.update_edit_mesh(mesh, loop_triangles=True, destructive=True)
    clean = bpy.ops.mesh.quad_transition_inspect_detached_faces(opacity=0.27)
    if (
        clean != {"FINISHED"}
        or settings.loose_xray_component_count != 0
        or settings.loose_xray_face_count != 0
        or bpy.data.objects.get("Topology Transition Detached Face X-Ray")
        is not None
    ):
        raise AssertionError("Detached Face X-Ray did not reach a clean state")
    print(
        "QT_DETACHED_XRAY_PASS regions=2 faces=3 opacity=0.27 "
        "step_faces=2 refresh_regions=1 clean=1"
    )


def _directional_merge_pair(name: str, first_x: float, second_x: float):
    clear_scene()
    mesh = bpy.data.meshes.new(f"{name}_mesh")
    mesh.from_pydata(((first_x, 0.0, 0.0), (second_x, 0.0, 0.0)), (), ())
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    return obj


def _directional_merge_view_pair(name: str, first_z: float, second_z: float):
    clear_scene()
    mesh = bpy.data.meshes.new(f"{name}_mesh")
    mesh.from_pydata(((0.0, 0.0, first_z), (0.0, 0.0, second_z)), (), ())
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    return obj


def _directional_merge_normal_fixture(name: str):
    clear_scene()
    mesh = bpy.data.meshes.new(f"{name}_mesh")
    mesh.from_pydata(
        (
            (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0),
            (0.0, 0.0, 0.02), (1.0, 0.0, 0.02), (0.0, 1.0, 0.02),
        ),
        (),
        ((0, 1, 2), (3, 4, 5)),
    )
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(mesh)
    bm.verts.ensure_lookup_table()
    for vertex in bm.verts:
        vertex.select_set(vertex.index in {0, 3})
    bm.select_mode = {"VERT"}
    bpy.context.tool_settings.mesh_select_mode = (True, False, False)
    bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
    return obj


def _directional_merge_uv_fixture(name: str):
    clear_scene()
    mesh = bpy.data.meshes.new(f"{name}_mesh")
    mesh.from_pydata(
        (
            (0.00, 0.00, 0.00),
            (0.01, 0.00, 0.00),
            (1.00, 0.00, 0.00),
            (1.00, 1.00, 0.00),
            (0.00, 1.00, 0.00),
            (-1.00, 1.00, 0.00),
            (-1.00, 0.00, 0.00),
        ),
        (),
        (
            (0, 1, 3, 4),
            (1, 2, 3),
            (0, 4, 5, 6),
        ),
    )
    mesh.uv_layers.new(name="UVMap")
    mesh.uv_layers.new(name="DetailUV")
    mesh.uv_layers.active_index = 1
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")

    bm = bmesh.from_edit_mesh(mesh)
    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    face_id_name = "qt_uv_guard_test_face"
    bm.faces.layers.int.new(face_id_name)
    face_id_layer = bm.faces.layers.int.get(face_id_name)
    uv_layers = {
        name: bm.loops.layers.uv.get(name) for name in ("UVMap", "DetailUV")
    }
    primary_values = (
        ((0.10, 0.10), (0.90, 0.90), (0.80, 0.80), (0.20, 0.20)),
        ((0.91, 0.91), (0.70, 0.90), (0.70, 0.70)),
        ((0.11, 0.11), (0.30, 0.10), (0.30, 0.30), (0.10, 0.30)),
    )
    expected: dict[int, dict[tuple[float, float, float], dict[str, tuple]]] = {}
    loop_number = 0
    for face_index, face in enumerate(bm.faces):
        face_id = face_index + 100
        face[face_id_layer] = face_id
        expected_groups: dict[
            tuple[float, float, float], list[tuple[int, dict[str, tuple]]]
        ] = {}
        for ordinal, loop in enumerate(face.loops):
            primary = primary_values[face_index][ordinal]
            detail = (primary[0] * 0.37 + 0.13, primary[1] * 0.41 + 0.07)
            states: dict[str, tuple] = {}
            for layer_index, (uv_name, uv_layer) in enumerate(uv_layers.items()):
                loop_uv = loop[uv_layer]
                loop_uv.uv = primary if layer_index == 0 else detail
                if hasattr(loop_uv, "select"):
                    loop_uv.select = (loop_number + layer_index) % 2 == 0
                if hasattr(loop_uv, "select_edge"):
                    loop_uv.select_edge = (loop_number + layer_index) % 3 == 0
                loop_uv.pin_uv = (loop_number + layer_index) % 4 == 0
                states[uv_name] = (
                    tuple(loop_uv.uv),
                    (
                        bool(loop_uv.select)
                        if hasattr(loop_uv, "select")
                        else None
                    ),
                    (
                        bool(loop_uv.select_edge)
                        if hasattr(loop_uv, "select_edge")
                        else None
                    ),
                    bool(loop_uv.pin_uv),
                )
            original_index = loop.vert.index
            final_index = 0 if original_index == 1 else original_index
            final_coordinate = tuple(
                float(value) for value in bm.verts[final_index].co
            )
            expected_groups.setdefault(final_coordinate, []).append(
                (original_index, states)
            )
            loop_number += 1
        expected[face_id] = {}
        for coordinate, entries in expected_groups.items():
            anchor = next((entry for entry in entries if entry[0] == 0), None)
            expected[face_id][coordinate] = (anchor or entries[0])[1]

    for vertex in bm.verts:
        vertex.select_set(vertex.index in {0, 1})
    bm.select_mode = {"VERT"}
    bpy.context.tool_settings.mesh_select_mode = (True, False, False)
    bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
    return obj, face_id_name, expected


def _uv_loop_state(loop, uv_layers) -> dict[str, tuple]:
    result = {}
    for name, layer in uv_layers.items():
        loop_uv = loop[layer]
        result[name] = (
            tuple(loop_uv.uv),
            bool(loop_uv.select) if hasattr(loop_uv, "select") else None,
            (
                bool(loop_uv.select_edge)
                if hasattr(loop_uv, "select_edge")
                else None
            ),
            bool(loop_uv.pin_uv),
        )
    return result


def assert_directional_merge() -> None:
    explicit = _directional_merge_pair("DirectionalExplicitPair", 0.5, 1.5)
    explicit_result = bpy.ops.mesh.quad_transition_directional_merge(
        distance=0.0001,
        strategy="RADIAL_OUTER",
    )
    explicit_bm = bmesh.from_edit_mesh(explicit.data)
    explicit_bm.verts.ensure_lookup_table()
    if (
        explicit_result != {"FINISHED"}
        or len(explicit_bm.verts) != 1
        or abs(explicit_bm.verts[0].co.x - 1.5) > 1.0e-6
    ):
        raise AssertionError("Exact selected pair did not override Merge Distance")

    outer = _directional_merge_pair("DirectionalOuter", 0.95, 1.0)
    outer_result = bpy.ops.mesh.quad_transition_directional_merge(
        distance=0.1,
        strategy="RADIAL_OUTER",
        scope="BOUNDARY",
        skip_connected=True,
    )
    outer_bm = bmesh.from_edit_mesh(outer.data)
    outer_bm.verts.ensure_lookup_table()
    if (
        outer_result != {"FINISHED"}
        or len(outer_bm.verts) != 1
        or abs(outer_bm.verts[0].co.x - 1.0) > 1.0e-6
    ):
        raise AssertionError("Directional radial outer merge kept the wrong vertex")

    inner = _directional_merge_pair("DirectionalInner", 0.95, 1.0)
    inner_result = bpy.ops.mesh.quad_transition_directional_merge(
        distance=0.1,
        strategy="RADIAL_INNER",
        scope="ALL_SELECTED",
        skip_connected=True,
    )
    inner_bm = bmesh.from_edit_mesh(inner.data)
    inner_bm.verts.ensure_lookup_table()
    if (
        inner_result != {"FINISHED"}
        or len(inner_bm.verts) != 1
        or abs(inner_bm.verts[0].co.x - 0.95) > 1.0e-6
    ):
        raise AssertionError("Directional radial inner merge kept the wrong vertex")

    normal_outer = _directional_merge_normal_fixture("DirectionalNormalOuter")
    normal_outer_result = bpy.ops.mesh.quad_transition_directional_merge(
        distance=0.03,
        strategy="NORMAL_OUTER",
        scope="ALL_SELECTED",
        skip_connected=True,
    )
    normal_outer_bm = bmesh.from_edit_mesh(normal_outer.data)
    joined_outer = [
        vertex for vertex in normal_outer_bm.verts if len(vertex.link_faces) == 2
    ]
    if (
        normal_outer_result != {"FINISHED"}
        or len(joined_outer) != 1
        or abs(joined_outer[0].co.z - 0.02) > 1.0e-6
    ):
        raise AssertionError("Face-normal outside merge kept the wrong vertex")

    normal_inner = _directional_merge_normal_fixture("DirectionalNormalInner")
    normal_inner_result = bpy.ops.mesh.quad_transition_directional_merge(
        distance=0.03,
        strategy="NORMAL_INNER",
        scope="ALL_SELECTED",
        skip_connected=True,
    )
    normal_inner_bm = bmesh.from_edit_mesh(normal_inner.data)
    joined_inner = [
        vertex for vertex in normal_inner_bm.verts if len(vertex.link_faces) == 2
    ]
    if (
        normal_inner_result != {"FINISHED"}
        or len(joined_inner) != 1
        or abs(joined_inner[0].co.z) > 1.0e-6
    ):
        raise AssertionError("Face-normal inside merge kept the wrong vertex")

    protected = _directional_merge_pair("DirectionalProtected", 0.0, 0.01)
    protected_bm = bmesh.from_edit_mesh(protected.data)
    protected_bm.verts.ensure_lookup_table()
    protected_bm.edges.new((protected_bm.verts[0], protected_bm.verts[1]))
    bmesh.update_edit_mesh(protected.data, loop_triangles=False, destructive=True)
    protected_result = bpy.ops.mesh.quad_transition_directional_merge(
        distance=0.02,
        strategy="RADIAL_OUTER",
        scope="BOUNDARY",
        skip_connected=True,
    )
    if protected_result != {"CANCELLED"} or len(protected_bm.verts) != 2:
        raise AssertionError("Protect Existing Edges did not prevent edge collapse")
    collapse_result = bpy.ops.mesh.quad_transition_directional_merge(
        distance=0.02,
        strategy="RADIAL_OUTER",
        scope="BOUNDARY",
        skip_connected=False,
    )
    protected_bm = bmesh.from_edit_mesh(protected.data)
    if collapse_result != {"FINISHED"} or len(protected_bm.verts) != 1:
        raise AssertionError("Directional merge could not allow an edge collapse")

    uv_obj, face_id_name, expected_uvs = _directional_merge_uv_fixture(
        "DirectionalProtectedUVs"
    )
    uv_stats = merge_selected_by_direction(
        bpy.context,
        distance=0.1,
        strategy="RADIAL_INNER",
        scope="ALL_SELECTED",
        skip_connected=False,
        protect_uvs=True,
    )
    uv_bm = bmesh.from_edit_mesh(uv_obj.data)
    face_id_layer = uv_bm.faces.layers.int.get(face_id_name)
    uv_layers = {
        name: uv_bm.loops.layers.uv.get(name) for name in ("UVMap", "DetailUV")
    }
    actual_uvs = {}
    for face in uv_bm.faces:
        face_id = int(face[face_id_layer])
        actual_uvs[face_id] = {
            tuple(float(value) for value in loop.vert.co): _uv_loop_state(
                loop, uv_layers
            )
            for loop in face.loops
        }
    leaked_layers = [
        name
        for name in uv_bm.loops.layers.int.keys()
        if name.startswith("_qt_directional_merge_uv_guard")
    ]
    if (
        actual_uvs != expected_uvs
        or uv_stats["uv_layers_protected"] != 2
        or uv_stats["uv_loops_restored"] != 20
        or uv_stats["uv_loops_removed"] != 2
        or leaked_layers
        or tuple(uv_obj.data.uv_layers.keys()) != ("UVMap", "DetailUV")
        or uv_obj.data.uv_layers.active_index != 1
    ):
        raise AssertionError(
            "Protect UVs changed an attached face corner or leaked its guard layer: "
            f"stats={uv_stats} leaked={leaked_layers} "
            f"expected={expected_uvs} actual={actual_uvs}"
        )

    if not bpy.context.scene.topology_transitions.directional_merge_protect_uvs:
        raise AssertionError("Protect UVs scene setting is not default-on")
    merge_rna = bpy.ops.mesh.quad_transition_directional_merge.get_rna_type()
    protect_rna = merge_rna.properties["protect_uvs"]
    if not protect_rna.default:
        raise AssertionError("Protect UVs operator setting is not default-on")

    transitive = _directional_merge_pair("DirectionalTransitive", 0.0, 0.005)
    transitive_bm = bmesh.from_edit_mesh(transitive.data)
    extra = transitive_bm.verts.new((0.01, 0.0, 0.0))
    extra.select_set(True)
    transitive_bm.verts.ensure_lookup_table()
    transitive_bm.edges.new((transitive_bm.verts[0], extra))
    bmesh.update_edit_mesh(transitive.data, loop_triangles=False, destructive=True)
    transitive_result = bpy.ops.mesh.quad_transition_directional_merge(
        distance=0.02,
        strategy="RADIAL_OUTER",
        scope="BOUNDARY",
        skip_connected=True,
    )
    transitive_bm = bmesh.from_edit_mesh(transitive.data)
    if (
        transitive_result != {"FINISHED"}
        or len(transitive_bm.verts) != 2
        or len(transitive_bm.edges) != 1
    ):
        raise AssertionError("Protected merge collapsed an edge through a cluster")

    toward = _directional_merge_view_pair("DirectionalViewToward", -1.0, -1.05)
    toward_stats = merge_selected_by_direction(
        bpy.context,
        distance=0.1,
        strategy="VIEW_TOWARD",
        scope="BOUNDARY",
        skip_connected=True,
        view_matrix=Matrix.Identity(4),
    )
    toward_bm = bmesh.from_edit_mesh(toward.data)
    toward_bm.verts.ensure_lookup_table()
    if (
        toward_stats["merged_vertices"] != 1
        or len(toward_bm.verts) != 1
        or abs(toward_bm.verts[0].co.z + 1.0) > 1.0e-6
    ):
        raise AssertionError("View Toward merge did not keep the shallower vertex")

    away = _directional_merge_view_pair("DirectionalViewAway", -1.0, -1.05)
    away_stats = merge_selected_by_direction(
        bpy.context,
        distance=0.1,
        strategy="VIEW_AWAY",
        scope="BOUNDARY",
        skip_connected=True,
        view_matrix=Matrix.Identity(4),
    )
    away_bm = bmesh.from_edit_mesh(away.data)
    away_bm.verts.ensure_lookup_table()
    if (
        away_stats["merged_vertices"] != 1
        or len(away_bm.verts) != 1
        or abs(away_bm.verts[0].co.z + 1.05) > 1.0e-6
    ):
        raise AssertionError("View Away merge did not keep the deeper vertex")

    viewport = _directional_merge_view_pair(
        "DirectionalViewOperator", -1.0, -1.05
    )
    screen = bpy.context.screen
    area = next(area for area in screen.areas if area.type == "VIEW_3D")
    region = next(region for region in area.regions if region.type == "WINDOW")
    view_matrix = area.spaces.active.region_3d.view_matrix.copy()
    viewport_bm = bmesh.from_edit_mesh(viewport.data)
    expected = max(
        viewport_bm.verts,
        key=lambda vertex: (
            view_matrix @ (viewport.matrix_world @ vertex.co)
        ).z,
    ).co.copy()
    with bpy.context.temp_override(area=area, region=region):
        viewport_result = bpy.ops.mesh.quad_transition_directional_merge(
            distance=0.1,
            strategy="VIEW_TOWARD",
            scope="BOUNDARY",
            skip_connected=True,
        )
    viewport_bm = bmesh.from_edit_mesh(viewport.data)
    viewport_bm.verts.ensure_lookup_table()
    if (
        viewport_result != {"FINISHED"}
        or len(viewport_bm.verts) != 1
        or (viewport_bm.verts[0].co - expected).length > 1.0e-6
    ):
        raise AssertionError("View merge operator did not use the active viewport")

    diagnostic = _directional_merge_pair("DirectionalDiagnostic", 0.0, 0.05)
    diagnostic_bm = bmesh.from_edit_mesh(diagnostic.data)
    third = diagnostic_bm.verts.new((0.1, 0.0, 0.0))
    third.select_set(True)
    bmesh.update_edit_mesh(diagnostic.data, loop_triangles=False, destructive=True)
    try:
        merge_selected_by_direction(
            bpy.context,
            distance=0.001,
            strategy="RADIAL_OUTER",
            scope="ALL_SELECTED",
            skip_connected=False,
            force_selected_pair=False,
        )
    except DirectionalMergeError as exc:
        if "Nearest eligible pair is" not in str(exc):
            raise AssertionError("Directional merge gave an unhelpful warning") from exc
    else:
        raise AssertionError("Directional merge diagnostic fixture unexpectedly merged")
    print(
        "QT_DIRECTIONAL_MERGE_PASS explicit_pair=1 radial_outer=1 radial_inner=1 "
        "normal_outer=1 normal_inner=1 protected_edge=1 allowed_collapse=1 "
        "protected_uv_maps=2 protected_uv_values=20 transitive_edge=1 "
        "view_toward=1 view_away=1 view_operator=1 "
        "nearest_diagnostic=1"
    )


def assert_part_cages() -> dict:
    from topology_transitions.part_cage_ops import (
        PART_DISTANCES_KEY,
        PartCagePlan,
        high_poly_tree,
        write_part_cage,
    )
    from topology_transitions.surface_ops import find_bake_cage, inspect_bake_cage

    clear_scene()
    # Two low-poly boxes 0.3 apart; the right one's high-poly bulges far more.
    for x, scale, name in ((0.0, 1.04, "HighA"), (1.3, 1.5, "HighB")):
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(x, 0, 0))
        bpy.context.active_object.scale = (scale, scale, scale)
        bpy.context.active_object.name = name
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0, 0, 0))
    first = bpy.context.active_object
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(1.3, 0, 0))
    second = bpy.context.active_object
    bpy.ops.object.select_all(action="DESELECT")
    first.select_set(True)
    second.select_set(True)
    bpy.context.view_layer.objects.active = first
    bpy.ops.object.join()
    low = bpy.context.active_object
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    highs = [bpy.data.objects["HighA"], bpy.data.objects["HighB"]]
    plan = PartCagePlan(low, 0.05)
    plan.fit(high_poly_tree(bpy.context, highs))
    plan.check_overlaps()
    left, right = sorted(range(2), key=lambda p: plan.points[plan.parts[p][0]].x)
    if (
        len(plan.parts) != 2
        or not plan.distances[right] > 3 * plan.distances[left]
        or (min(left, right), max(left, right)) not in plan.clashes
        or not plan.overlapping
    ):
        raise AssertionError(
            f"Part cages did not fit per part or flag the clash: "
            f"{plan.distances} {plan.clashes}"
        )
    fitted = list(plan.distances)
    plan.distances[right] = plan.distances[left]
    plan.check_overlaps()
    if plan.clashes:
        raise AssertionError("Equal small part cages should not clash")
    cage = write_part_cage(bpy.context, plan)
    if (
        find_bake_cage(low) is not cage
        or not inspect_bake_cage(low, cage).ready
        or len(json.loads(cage[PART_DISTANCES_KEY])) != 2
    ):
        raise AssertionError("Part cage was not written as a ready bake cage")
    return {"fitted": [round(d, 3) for d in fitted]}


def assert_live_boolean_cleanup() -> dict:
    from topology_transitions.boolean_ops import MODIFIER_NAME, clean_cut, face_counts

    clear_scene()
    bpy.ops.mesh.primitive_cube_add(size=2.0)
    target = bpy.context.active_object
    subdivide = target.modifiers.new("grid", "SUBSURF")
    subdivide.subdivision_type = "SIMPLE"
    subdivide.levels = 2
    bpy.ops.object.modifier_apply(modifier="grid")
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=8, radius=0.35, depth=3.0, rotation=(0.0, 1.5708, 0.0)
    )
    cutter = bpy.context.active_object
    boolean = target.modifiers.new(MODIFIER_NAME, "BOOLEAN")
    boolean.object = cutter
    boolean.operation = "DIFFERENCE"
    bpy.ops.object.select_all(action="DESELECT")
    target.select_set(True)
    bpy.context.view_layer.objects.active = target
    bpy.ops.object.modifier_apply(modifier=MODIFIER_NAME)
    cut = face_counts(target.data)
    cleaned = clean_cut(bpy.context, target, cutter)
    final = face_counts(target.data)
    bm = bmesh.new()
    bm.from_mesh(target.data)
    over = sum(1 for edge in bm.edges if len(edge.link_faces) > 2)
    bm.free()
    if (
        cut["tris"] + cut["ngons"] == 0
        or cleaned["solved"] == 0
        or final["tris"] + final["ngons"] >= cut["tris"] + cut["ngons"]
        or over
    ):
        raise AssertionError(f"Live Boolean cleanup failed: {cut} {cleaned} {final}")
    return {
        "cut_nonquads": cut["tris"] + cut["ngons"],
        "left": final["tris"] + final["ngons"],
    }


def main() -> None:
    topology_transitions.register()
    try:
        for preset, width, height, faces, poles in PRESETS:
            assert_transition(preset, width, height, faces, poles)
        for preset, _width, _height, _faces, _poles in PRESETS:
            assert_single_quad_transition(preset)
        assert_single_quad_transition("FIVE_TO_THREE", padding=0)
        assert_single_quad_edge_loop_transition()
        assert_transition("FIVE_TO_THREE", 5, 2, 16, 2, padding=1, curved=True)
        assert_transition("ONE_TO_TWO", 2, 2, 5, 1, mirror=True)
        assert_transition("FIVE_TO_THREE", 5, 1, 11, 2)
        assert_transition("ONE_TO_TWO", 2, 1, 3, 1)
        assert_edge_flow_step_browser()
        assert_side_to_side_strip_order()
        assert_quad_flow_on_transition()
        assert_example_plane()
        assert_repair_operators()
        assert_mixed_and_edge_boundary_transition_inputs()
        assert_manifold_diagnostics()
        assert_auto_nonmanifold_solver()
        assert_quad_flow_optimization()
        assert_directional_face_delete()
        assert_target_mesh_connector()
        assert_detached_face_xray()
        assert_directional_merge()
        assert_surface_paint()
        assert_subdivision_preview()
        assert_external_projection_target()
        assert_invalid_selection_is_unchanged()
        assert_shape_key_is_unchanged()
        cages = assert_part_cages()
        cut = assert_live_boolean_cleanup()
        print(f"QT_NEW_TOOLS_PASS part_cages={cages} live_boolean={cut}")
        print(
            "QT_BLENDER_SMOKE_PASS patterns=13 single_quad=10 rejection_cases=2 "
            "preview=1 external_projection=1 quad_flow=3 example_atlas=1 "
            "repair=6 boundary_input=2 manifold=5 auto_manifold=6 optimize=9 "
            "directional_delete=7 "
            "target_connector=2 detached_xray=1 directional_merge=12 "
            "surface_paint=2 part_cages=1 live_boolean=1"
        )
    finally:
        clear_scene()
        topology_transitions.unregister()


if __name__ == "__main__":
    main()
