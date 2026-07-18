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
from mathutils import Vector
from mathutils.bvhtree import BVHTree

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import topology_transitions  # noqa: E402
from topology_transitions.flow_ops import build_flow_session  # noqa: E402
from topology_transitions.operators import _make_template_and_layout  # noqa: E402
from topology_transitions.ribbon import build_transition_ribbon  # noqa: E402
from topology_transitions.ribbon_ops import (  # noqa: E402
    QT_OT_draw_multi_strip,
    TargetSurface,
    _anchor_snapshot,
    _event_window_coordinate,
    grow_ribbon_from_stroke,
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


def clear_scene() -> None:
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def create_ribbon_fixture(name: str, lanes: int):
    """Create a one-row retopo base plus a separate planar draw target."""

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
    before = selection_snapshot(obj)
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
    if not layout.single_quad_insertion or template.boundary_edge_count != 4:
        raise AssertionError(f"{preset}: did not choose the single-quad insertion")

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
    assert_selected_faces_connected(selected_faces, preset)
    assert_internal_loop_directions(selected_faces, preset)
    if len(selected_faces) != len(template.faces):
        raise AssertionError(
            f"{preset}: expected {len(template.faces)} inserted quads, "
            f"found {len(selected_faces)}"
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

    original_boundary = before["boundary_coordinates"]
    outer_edges = [
        edge
        for edge in bm.edges
        if all(tuple(vertex.co) in original_boundary for vertex in edge.verts)
    ]
    if len(outer_edges) != 4:
        raise AssertionError(f"{preset}: selected quad boundary was not preserved")
    outer_edge_coordinates = {
        frozenset(tuple(vertex.co) for vertex in edge.verts) for edge in outer_edges
    }
    if outer_edge_coordinates != before["boundary_edge_coordinates"]:
        raise AssertionError(f"{preset}: selected quad boundary endpoints changed")
    if sorted(len(edge.link_faces) for edge in outer_edges) != before[
        "boundary_face_counts"
    ]:
        raise AssertionError(f"{preset}: outside face connectivity changed")
    inserted_vertices = {
        vertex for face in selected_faces for vertex in face.verts
    }
    if any(abs(vertex.co.z) > 1.0e-7 for vertex in inserted_vertices):
        raise AssertionError(f"{preset}: planar insertion moved away from the source")

    print(
        f"QT_SINGLE_QUAD_PASS preset={preset} inserted={len(selected_faces)} "
        f"embedded={bool(padding)} boundary_links="
        f"{sorted(len(edge.link_faces) for edge in outer_edges)}"
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
    if len(selected_faces) != 31 or any(len(face.verts) != 4 for face in bm.faces):
        raise AssertionError("Single-quad edge loop did not become 31 quads")
    assert_selected_faces_connected(selected_faces, "single_edge_loop")
    assert_internal_loop_directions(selected_faces, "single_edge_loop")
    print("QT_SINGLE_QUAD_EDGE_LOOP_PASS preset=FIVE_TO_THREE quads=31")


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

    clear_scene()
    bpy.ops.mesh.primitive_cube_add()
    bpy.ops.object.mode_set(mode="EDIT")
    result = bpy.ops.mesh.quad_transition_check_manifold()
    if result != {"FINISHED"} or settings.manifold_issue_count != 0:
        raise AssertionError("Closed cube did not report as manifold")
    print(
        "QT_MANIFOLD_PASS open_plane_edges=4 open_components=1 "
        "over_connected_edges=1 closed_cube_issues=0"
    )


def assert_connected_ribbon_growth() -> None:
    lanes = 5
    segments = 4
    retopo, target = create_ribbon_fixture("uniform_ribbon", lanes)
    original_vertex_count = len(retopo.data.vertices)
    coordinate = _event_window_coordinate(
        SimpleNamespace(mouse_x=350, mouse_y=250),
        SimpleNamespace(x=100, y=50, width=800, height=600),
    )
    outside = _event_window_coordinate(
        SimpleNamespace(mouse_x=99, mouse_y=250),
        SimpleNamespace(x=100, y=50, width=800, height=600),
    )
    preview = SimpleNamespace(
        _stroke_points=[
            Vector((lanes * 0.5, 3.0, 0.5)),
            Vector((lanes * 0.5 + 0.35, 6.0, 0.5)),
        ],
        _stroke_normals=[Vector((0.0, 0.0, 1.0))] * 2,
        _anchor=_anchor_snapshot(bpy.context),
        _surface=TargetSurface(bpy.context, target),
        layout="UNIFORM",
        transition="FIVE_TO_THREE",
        segments=segments,
        width_scale=1.0,
        pole_side="CENTER",
        mirror=False,
        pole_spacing=1.0,
        project_limit=1.0,
    )
    preview_lines, preview_fill, preview_poles = (
        QT_OT_draw_multi_strip._preview_geometry(preview)
    )
    if (
        coordinate != (250.0, 200.0)
        or outside is not None
        or not preview_lines
        or not preview_fill
        or preview_poles
    ):
        raise AssertionError("Modal viewport coordinates or quad preview failed")
    stats = grow_ribbon_from_stroke(
        bpy.context,
        [
            Vector((lanes * 0.5, 3.0, 0.5)),
            Vector((lanes * 0.5 + 0.35, 6.0, 0.5)),
        ],
        [Vector((0.0, 0.0, 1.0)), Vector((0.0, 0.0, 1.0))],
        layout="UNIFORM",
        transition="FIVE_TO_THREE",
        segments=segments,
        width_scale=1.0,
        pole_side="CENTER",
        mirror=False,
        pole_spacing=1.0,
        target=target,
        project_limit=1.0,
    )
    bm = bmesh.from_edit_mesh(retopo.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    selected_edges = [edge for edge in bm.edges if edge.select]
    anchor_vertices = bm.verts[lanes + 1 : (lanes + 1) * 2]
    anchor_edges = [
        bm.edges.get((first, second))
        for first, second in zip(anchor_vertices, anchor_vertices[1:])
    ]
    if (
        stats["new_faces"] != lanes * segments
        or stats["anchor_edges"] != lanes
        or stats["input_count"] != lanes
        or stats["output_count"] != lanes
        or len(bm.faces) != lanes + lanes * segments
        or stats["output_edges"] != lanes
        or len(selected_edges) != lanes
        or any(len(face.verts) != 4 for face in bm.faces)
        or any(edge is None or len(edge.link_faces) != 2 for edge in anchor_edges)
        or any(len(edge.link_faces) > 2 for edge in bm.edges)
        or any(
            abs(vertex.co.z - 0.5) > 1.0e-5
            for vertex in bm.verts[original_vertex_count:]
        )
    ):
        raise AssertionError(f"Uniform ribbon failed welded-sheet checks: {stats}")

    transition_plan = build_transition_ribbon("FIVE_TO_THREE", segments)
    retopo, target = create_ribbon_fixture("transition_ribbon", lanes)
    transition_stats = grow_ribbon_from_stroke(
        bpy.context,
        [Vector((lanes * 0.5, 3.0, 0.5)), Vector((lanes * 0.5, 6.0, 0.5))],
        [Vector((0.0, 0.0, 1.0)), Vector((0.0, 0.0, 1.0))],
        layout="TRANSITION",
        transition="FIVE_TO_THREE",
        segments=segments,
        width_scale=1.0,
        pole_side="CENTER",
        mirror=False,
        pole_spacing=1.0,
        target=target,
        project_limit=1.0,
    )
    bm = bmesh.from_edit_mesh(retopo.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    anchor_vertices = bm.verts[lanes + 1 : (lanes + 1) * 2]
    anchor_edges = [
        bm.edges.get((first, second))
        for first, second in zip(anchor_vertices, anchor_vertices[1:])
    ]
    pole_count = sum(
        1
        for vertex in bm.verts
        if len(vertex.link_edges) == 3
        and not any(len(edge.link_faces) == 1 for edge in vertex.link_edges)
    )
    selected_output_edges = [edge for edge in bm.edges if edge.select]
    if (
        transition_stats["new_faces"] != len(transition_plan.faces)
        or transition_stats["input_count"] != 5
        or transition_stats["output_count"] != 3
        or transition_stats["poles"] != 2
        or transition_stats["output_edges"] != 3
        or len(selected_output_edges) != 3
        or any(len(face.verts) != 4 for face in bm.faces)
        or any(edge is None or len(edge.link_faces) != 2 for edge in anchor_edges)
        or pole_count < 2
    ):
        raise AssertionError(
            f"Transition ribbon failed density-change checks: {transition_stats}"
        )
    print(
        "QT_CONNECTED_RIBBON_PASS lanes=5 welded=5 uniform_quads=20 "
        f"transition_quads={transition_stats['new_faces']} output_lanes=3 "
        "poles=2 modal_preview=1"
    )


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
        assert_connected_ribbon_growth()
        assert_subdivision_preview()
        assert_external_projection_target()
        assert_invalid_selection_is_unchanged()
        assert_shape_key_is_unchanged()
        print(
            "QT_BLENDER_SMOKE_PASS patterns=13 single_quad=10 rejection_cases=2 "
            "preview=1 external_projection=1 quad_flow=3 example_atlas=1 "
            "repair=6 boundary_input=2 manifold=3 connected_ribbon=2"
        )
    finally:
        clear_scene()
        topology_transitions.unregister()


if __name__ == "__main__":
    main()
