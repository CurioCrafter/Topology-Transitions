"""Smoke an installed copy from a temporary Blender user scripts directory."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector

TRANSITION_ROLE_LAYER = "topology_transition_role"
TRANSITION_ROLE_ADAPTER = 1
TRANSITION_ROLE_CORE = 2


def create_patch():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    vertices = [(float(x), float(y), 0.0) for y in range(2) for x in range(4)]
    faces = [
        (0, 1, 5, 4),
        (1, 2, 6, 5),
        (2, 3, 7, 6),
    ]
    mesh = bpy.data.meshes.new("installed_smoke_mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new("InstalledSmoke", mesh)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(mesh)
    bm.select_mode = {"FACE"}
    bpy.context.tool_settings.mesh_select_mode = (False, False, True)
    for face in bm.faces:
        face.select_set(True)
    bm.select_flush_mode()
    top_edges = [
        edge
        for edge in bm.edges
        if all(abs(vertex.co.y - 1.0) < 1.0e-6 for vertex in edge.verts)
    ]
    bm.select_history.clear()
    bm.select_history.add(top_edges[1])
    bmesh.update_edit_mesh(mesh, loop_triangles=True, destructive=False)
    return obj


def create_triangle_pair():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    mesh = bpy.data.meshes.new("installed_tri_pair_mesh")
    mesh.from_pydata(
        ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)),
        [],
        ((0, 1, 2), (0, 2, 3)),
    )
    mesh.update()
    obj = bpy.data.objects.new("InstalledTriPair", mesh)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(mesh)
    bm.select_mode = {"FACE"}
    bpy.context.tool_settings.mesh_select_mode = (False, False, True)
    for face in bm.faces:
        face.select_set(True)
    bm.select_flush_mode()
    bmesh.update_edit_mesh(mesh, loop_triangles=True, destructive=False)
    return obj


def create_single_quad():
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    mesh = bpy.data.meshes.new("installed_single_quad_mesh")
    mesh.from_pydata(
        ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)),
        [],
        ((0, 1, 2, 3),),
    )
    mesh.update()
    obj = bpy.data.objects.new("InstalledSingleQuad", mesh)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(mesh)
    bm.select_mode = {"FACE"}
    bpy.context.tool_settings.mesh_select_mode = (False, False, True)
    for face in bm.faces:
        face.select_set(True)
    bm.select_flush_mode()
    bmesh.update_edit_mesh(mesh, loop_triangles=True, destructive=False)
    return obj


def create_surface_paint_fixture():
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    lanes = 3
    target_mesh = bpy.data.meshes.new("installed_paint_target_mesh")
    target_mesh.from_pydata(
        ((-2, -1, 0.5), (5, -1, 0.5), (5, 7, 0.5), (-2, 7, 0.5)),
        (),
        ((0, 1, 2, 3),),
    )
    target_mesh.update()
    target = bpy.data.objects.new("InstalledPaintTarget", target_mesh)
    bpy.context.collection.objects.link(target)

    mesh = bpy.data.meshes.new("installed_paint_mesh")
    vertices = [(float(x), float(y), 0.5) for y in range(2) for x in range(lanes + 1)]
    faces = [(x, x + 1, lanes + x + 2, lanes + x + 1) for x in range(lanes)]
    mesh.from_pydata(vertices, (), faces)
    mesh.update()
    low = bpy.data.objects.new("InstalledPaintLow", mesh)
    bpy.context.collection.objects.link(low)
    bpy.context.view_layer.objects.active = low
    low.select_set(True)
    target.select_set(False)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.context.tool_settings.mesh_select_mode = (False, True, False)
    bm = bmesh.from_edit_mesh(mesh)
    bm.select_mode = {"EDGE"}
    for face in bm.faces:
        face.select_set(False)
    for edge in bm.edges:
        edge.select_set(all(abs(vertex.co.y - 1.0) < 1.0e-6 for vertex in edge.verts))
    bm.select_flush_mode()
    bmesh.update_edit_mesh(mesh, loop_triangles=True, destructive=False)
    return low, target


def create_connector_fixture():
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    source_mesh = bpy.data.meshes.new("installed_connector_source_mesh")
    source_mesh.from_pydata(
        ((-0.5, -0.5, 0.04), (0.5, -0.5, 0.04), (0.5, 0.5, 0.04), (-0.5, 0.5, 0.04)),
        (),
        ((0, 1, 2, 3),),
    )
    source_mesh.polygons[0].select = True
    source = bpy.data.objects.new("InstalledConnectorSource", source_mesh)
    bpy.context.collection.objects.link(source)
    target_mesh = bpy.data.meshes.new("installed_connector_target_mesh")
    target_mesh.from_pydata(
        ((-0.5, -0.5, 0.0), (0.5, -0.5, 0.0), (0.5, 0.5, 0.0), (-0.5, 0.5, 0.0)),
        (),
        ((0, 3, 2, 1),),
    )
    target = bpy.data.objects.new("InstalledConnectorTarget", target_mesh)
    bpy.context.collection.objects.link(target)
    source.select_set(True)
    target.select_set(True)
    bpy.context.view_layer.objects.active = source
    return source, target


def create_detached_xray_fixture():
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    mesh = bpy.data.meshes.new("installed_detached_xray_mesh")
    mesh.from_pydata(
        (
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (2.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
            (1.0, 1.0, 0.0),
            (2.0, 1.0, 0.0),
            (0.8, 0.3, 0.03),
            (1.2, 0.3, 0.03),
            (1.0, 0.7, 0.03),
        ),
        (),
        ((0, 1, 4, 3), (1, 2, 5, 4), (6, 7, 8)),
    )
    mesh.update()
    obj = bpy.data.objects.new("InstalledDetachedXRay", mesh)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    return obj


def create_directional_merge_fixture():
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    mesh = bpy.data.meshes.new("installed_directional_merge_mesh")
    mesh.from_pydata(((0.95, 0.0, 0.0), (1.0, 0.0, 0.0)), (), ())
    mesh.update()
    obj = bpy.data.objects.new("InstalledDirectionalMerge", mesh)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    return obj


def create_directional_merge_uv_fixture():
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    mesh = bpy.data.meshes.new("installed_directional_merge_uv_mesh")
    mesh.from_pydata(
        (
            (0.0, 0.0, 0.0),
            (0.01, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
        ),
        (),
        ((0, 1, 2, 3),),
    )
    mesh.uv_layers.new(name="UVMap")
    mesh.update()
    obj = bpy.data.objects.new("InstalledDirectionalMergeUV", mesh)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(mesh)
    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    uv_layer = bm.loops.layers.uv.get("UVMap")
    values = ((0.1, 0.2), (0.9, 0.8), (0.7, 0.8), (0.2, 0.7))
    for loop, value in zip(bm.faces[0].loops, values, strict=True):
        loop_uv = loop[uv_layer]
        loop_uv.uv = value
        loop_uv.pin_uv = loop.vert.index == 0
    for vertex in bm.verts:
        vertex.select_set(vertex.index in {0, 1})
    bm.select_mode = {"VERT"}
    bpy.context.tool_settings.mesh_select_mode = (True, False, False)
    return obj


def main() -> None:
    bpy.ops.preferences.addon_enable(module="topology_transitions")
    import topology_transitions
    from topology_transitions.directional_merge_ops import (
        merge_selected_by_direction,
    )
    from topology_transitions.paint_grid import Dab
    from topology_transitions.surface_ops import find_bake_cage
    from topology_transitions.surface_paint_ops import build_surface_paint

    expected_root = Path(os.environ["QT_EXPECTED_ADDON_ROOT"]).resolve()
    loaded = Path(topology_transitions.__file__).resolve()
    if expected_root not in loaded.parents:
        raise AssertionError(
            f"Loaded source copy {loaded} instead of installed root {expected_root}"
        )
    if not hasattr(bpy.types.Scene, "topology_transitions"):
        raise AssertionError("Installed add-on did not register scene settings")
    manifest = tomllib.loads(
        (expected_root / "blender_manifest.toml").read_text(encoding="utf-8")
    )
    expected_version = tuple(int(part) for part in manifest["version"].split("."))
    if topology_transitions.bl_info["version"] != expected_version:
        raise AssertionError(
            f"Installed add-on reported {topology_transitions.bl_info['version']}; "
            f"manifest expects {expected_version}"
        )

    from topology_transitions import ui

    addon_prefs = bpy.context.preferences.addons["topology_transitions"].preferences
    addon_prefs.sidebar_tab = "Tool"
    addon_prefs.show_paint = False
    if (
        ui.QT_PT_inspect.bl_category != "Tool"
        or not ui.QT_PT_inspect.is_registered
        or ui.QT_PT_paint.poll(bpy.context)
    ):
        raise AssertionError("Add-on preferences did not move or hide the panels")
    addon_prefs.sidebar_tab = "Quad Transition"
    addon_prefs.show_paint = True

    obj = create_patch()
    validation = bpy.ops.mesh.quad_transition_validate(transition="THREE_TO_ONE")
    if validation != {"FINISHED"}:
        raise AssertionError(f"Installed validation returned {validation}")
    result = bpy.ops.mesh.quad_transition_apply(
        transition="THREE_TO_ONE",
        relax_iterations=8,
        conform_surface=True,
    )
    if result != {"FINISHED"}:
        raise AssertionError(f"Installed apply returned {result}")
    bm = bmesh.from_edit_mesh(obj.data)
    selected = [face for face in bm.faces if face.select]
    if len(selected) != 7 or any(len(face.verts) != 4 for face in selected):
        raise AssertionError("Installed copy did not generate the expected seven quads")
    single = create_single_quad()
    single_result = bpy.ops.mesh.quad_transition_apply(
        transition="FIVE_TO_THREE",
        relax_iterations=8,
        conform_surface=True,
    )
    single_bm = bmesh.from_edit_mesh(single.data)
    single_selected = [face for face in single_bm.faces if face.select]
    role_layer = single_bm.faces.layers.int.get(TRANSITION_ROLE_LAYER)
    single_inserted = [
        face
        for face in single_bm.faces
        if role_layer is not None
        and face[role_layer] in {TRANSITION_ROLE_ADAPTER, TRANSITION_ROLE_CORE}
    ]
    if (
        single_result != {"FINISHED"}
        or len(single_inserted) != 11
        or len(single_selected) != 11
        or any(len(face.verts) != 4 for face in single_bm.faces)
        or any(
            role_layer is None or face[role_layer] != TRANSITION_ROLE_CORE
            for face in single_selected
        )
    ):
        raise AssertionError(f"Installed single-quad insertion failed: {single_result}")
    bpy.ops.object.mode_set(mode="OBJECT")
    single.select_set(False)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    settings = bpy.context.scene.topology_transitions
    settings.flow_mode = "REGIONS"
    settings.flow_scope = "ALL"
    settings.flow_min_edges = 1
    flow_result = bpy.ops.mesh.quad_transition_edge_flow_step(
        direction=0, select_current=False
    )
    if flow_result != {"FINISHED"} or settings.flow_count < 1:
        raise AssertionError(
            f"Installed edge-flow browser returned {flow_result} / "
            f"{settings.flow_count} flows"
        )
    flow_count = settings.flow_count
    strip_quads = settings.flow_quad_count
    bpy.ops.object.mode_set(mode="OBJECT")
    example_result = bpy.ops.object.quad_transition_add_example_plane()
    example = bpy.context.active_object
    if (
        example_result != {"FINISHED"}
        or example is None
        or len(example.data.polygons) != 186
        or example.get("transition_count") != 8
    ):
        raise AssertionError(
            f"Installed example plane returned {example_result} / "
            f"{0 if example is None else len(example.data.polygons)} faces"
        )
    repair_obj = create_triangle_pair()
    repair_bm = bmesh.from_edit_mesh(repair_obj.data)
    repair_bm.faces.ensure_lookup_table()
    for face in repair_bm.faces:
        face.select_set(False)
    repair_bm.faces[0].select_set(True)
    repair_bm.select_flush_mode()
    bmesh.update_edit_mesh(repair_obj.data, loop_triangles=True, destructive=False)
    repair_result = bpy.ops.mesh.quad_transition_solve_selected_tris()
    repaired = bmesh.from_edit_mesh(repair_obj.data)
    if repair_result != {"FINISHED"} or len(repaired.faces) != 1:
        raise AssertionError(f"Installed triangle repair returned {repair_result}")
    manifold_result = bpy.ops.mesh.quad_transition_check_manifold()
    if manifold_result != {"FINISHED"} or settings.manifold_open_edge_count != 4:
        raise AssertionError("Installed manifold diagnostic missed quad boundary")

    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    optimize_mesh = bpy.data.meshes.new("installed_optimize_mesh")
    optimize_vertices = [(float(x), float(y), 0.0) for y in range(5) for x in range(5)]
    optimize_faces = []
    for y in range(4):
        for x in range(4):
            first = y * 5 + x
            optimize_faces.append((first, first + 1, first + 6, first + 5))
    optimize_mesh.from_pydata(optimize_vertices, (), optimize_faces)
    optimize_mesh.update()
    optimize_obj = bpy.data.objects.new("InstalledOptimize", optimize_mesh)
    bpy.context.collection.objects.link(optimize_obj)
    bpy.context.view_layer.objects.active = optimize_obj
    optimize_obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    settings.optimize_scope = "ALL"
    settings.optimize_sensitivity = "AUTO"
    settings.optimize_cycles = 1
    settings.optimize_max_face_angle = 1.0
    settings.optimize_feature_angle = 20.0
    settings.optimize_max_error_percent = 0.1
    settings.optimize_branch_guard_rings = 0
    settings.optimize_min_loop_edges = 4
    settings.optimize_preserve_boundaries = False
    optimize_result = bpy.ops.mesh.quad_transition_optimize_flows()
    optimized_bm = bmesh.from_edit_mesh(optimize_mesh)
    if (
        optimize_result != {"FINISHED"}
        or len(optimized_bm.faces) != 4
        or settings.optimize_effective_mode != "Balanced"
        or any(len(face.verts) != 4 for face in optimized_bm.faces)
    ):
        raise AssertionError(f"Installed quad optimization failed: {optimize_result}")

    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_cube_add(size=2.0)
    cube = bpy.context.object
    delete_result = bpy.ops.object.quad_transition_delete_faces_by_axis(axis="POS_Y")
    if (
        delete_result != {"FINISHED"}
        or cube is None
        or len(cube.data.polygons) != 5
        or any(polygon.normal.y > 0.9 for polygon in cube.data.polygons)
    ):
        raise AssertionError(f"Installed directional delete failed: {delete_result}")
    auto_repair_result = bpy.ops.mesh.quad_transition_solve_all_nonmanifold()
    auto_bm = bmesh.new()
    auto_bm.from_mesh(cube.data)
    try:
        if (
            auto_repair_result != {"FINISHED"}
            or len(auto_bm.faces) != 6
            or any(len(face.verts) != 4 for face in auto_bm.faces)
            or any(len(edge.link_faces) != 2 for edge in auto_bm.edges)
        ):
            raise AssertionError(
                f"Installed auto manifold repair failed: {auto_repair_result}"
            )
    finally:
        auto_bm.free()

    source, target = create_connector_fixture()
    connector_preview = bpy.ops.object.quad_transition_preview_target_connection(
        distance=0.05, opacity=0.4
    )
    connector_result = bpy.ops.object.quad_transition_build_target_connection(
        distance=0.05
    )
    connected = bpy.context.active_object
    if (
        connector_preview != {"FINISHED"}
        or connector_result != {"FINISHED"}
        or connected is None
        or connected in {source, target}
        or len(connected.data.polygons) != 8
        or any(len(face.vertices) != 3 for face in connected.data.polygons)
    ):
        raise AssertionError("Installed target connector failed")

    detached = create_detached_xray_fixture()
    detached_result = bpy.ops.mesh.quad_transition_inspect_detached_faces(opacity=0.3)
    detached_overlay = bpy.data.objects.get("Topology Transition Detached Face X-Ray")
    if (
        detached_result != {"FINISHED"}
        or settings.loose_xray_component_count != 1
        or settings.loose_xray_face_count != 1
        or detached_overlay is None
        or len(detached_overlay.data.polygons) != 1
        or not detached_overlay.show_in_front
    ):
        raise AssertionError("Installed Detached Face X-Ray failed")
    bpy.ops.mesh.quad_transition_clear_detached_xray()
    bpy.ops.object.mode_set(mode="OBJECT")
    detached.hide_set(True)

    merge_obj = create_directional_merge_fixture()
    directional_merge_result = bpy.ops.mesh.quad_transition_directional_merge(
        distance=0.0001,
        strategy="RADIAL_OUTER",
    )
    merge_bm = bmesh.from_edit_mesh(merge_obj.data)
    merge_bm.verts.ensure_lookup_table()
    if (
        directional_merge_result != {"FINISHED"}
        or len(merge_bm.verts) != 1
        or abs(merge_bm.verts[0].co.x - 1.0) > 1.0e-6
    ):
        raise AssertionError("Installed Directional Merge failed")
    bpy.ops.object.mode_set(mode="OBJECT")
    merge_obj.hide_set(True)

    uv_merge = create_directional_merge_uv_fixture()
    uv_merge_result = bpy.ops.mesh.quad_transition_directional_merge(
        distance=0.1,
        strategy="RADIAL_INNER",
        scope="ALL_SELECTED",
        skip_connected=False,
    )
    uv_bm = bmesh.from_edit_mesh(uv_merge.data)
    uv_layer = uv_bm.loops.layers.uv.get("UVMap")
    anchor_loop = next(
        loop
        for face in uv_bm.faces
        for loop in face.loops
        if loop.vert.co.length < 1.0e-8
    )
    anchor_uv = anchor_loop[uv_layer]
    leaked_uv_guards = [
        name
        for name in uv_bm.loops.layers.int.keys()
        if name.startswith("_qt_directional_merge_uv_guard")
    ]
    merge_rna = bpy.ops.mesh.quad_transition_directional_merge.get_rna_type()
    if (
        uv_merge_result != {"FINISHED"}
        or (anchor_uv.uv - Vector((0.1, 0.2))).length > 1.0e-6
        or not anchor_uv.pin_uv
        or leaked_uv_guards
        or not settings.directional_merge_protect_uvs
        or not merge_rna.properties["protect_uvs"].default
    ):
        raise AssertionError(
            "Installed Protect UVs failed: "
            f"result={uv_merge_result} uv={tuple(anchor_uv.uv)} "
            f"pin={anchor_uv.pin_uv} leaked={leaked_uv_guards}"
        )
    bpy.ops.object.mode_set(mode="OBJECT")
    uv_merge.hide_set(True)

    view_merge = create_directional_merge_fixture()
    view_bm = bmesh.from_edit_mesh(view_merge.data)
    view_bm.verts.ensure_lookup_table()
    view_bm.verts[0].co = (0.0, 0.0, -1.0)
    view_bm.verts[1].co = (0.0, 0.0, -1.05)
    bmesh.update_edit_mesh(view_merge.data, loop_triangles=False, destructive=False)
    view_stats = merge_selected_by_direction(
        bpy.context,
        distance=0.1,
        strategy="VIEW_TOWARD",
        scope="BOUNDARY",
        skip_connected=True,
        view_matrix=Matrix.Identity(4),
    )
    view_bm = bmesh.from_edit_mesh(view_merge.data)
    view_bm.verts.ensure_lookup_table()
    if (
        view_stats["merged_vertices"] != 1
        or len(view_bm.verts) != 1
        or abs(view_bm.verts[0].co.z + 1.0) > 1.0e-6
    ):
        raise AssertionError("Installed View Toward merge failed")
    bpy.ops.object.mode_set(mode="OBJECT")
    view_merge.hide_set(True)

    low, target = create_surface_paint_fixture()
    stroke = [Dab((x * 0.5, 1.5, 0.5), 0.6) for x in range(1, 6)]
    paint_stats = build_surface_paint(
        bpy.context,
        [stroke],
        [[Vector((0.0, 0.0, 1.0))] * len(stroke)],
        target=target,
        cell=0.5,
    )
    paint_bm = bmesh.from_edit_mesh(low.data)
    if (
        paint_stats["quads"] < 4
        or paint_stats["welded"] < 2
        or len(paint_bm.faces) != 3 + paint_stats["quads"]
        or any(len(face.verts) != 4 for face in paint_bm.faces)
    ):
        raise AssertionError(f"Installed surface paint failed: {paint_stats}")
    bpy.ops.object.mode_set(mode="OBJECT")
    shrinkwrap_result = bpy.ops.object.quad_transition_setup_shrinkwrap(
        target_name=target.name,
        wrap_method="NEAREST_SURFACEPOINT",
        offset=0.001,
        project_limit=1.0,
    )
    cage_result = bpy.ops.object.quad_transition_toggle_bake_cage(distance=0.05)
    if (
        shrinkwrap_result != {"FINISHED"}
        or cage_result != {"FINISHED"}
        or low.modifiers.get("Topology Transition Shrinkwrap") is None
        or find_bake_cage(low) is None
    ):
        raise AssertionError(
            f"Installed surface workflow failed: {shrinkwrap_result} / {cage_result}"
        )
    print(
        f"QT_INSTALLED_SMOKE_PASS module={loaded} "
        f"selected_quads={len(selected)} flows={flow_count} "
        f"flow_quads={strip_quads} single_quad={len(single_inserted)} "
        f"single_core={len(single_selected)} "
        f"example_quads=186 transitions=8 repairs=1 open_edges=4 "
        f"directional_delete=1 auto_manifold=1 quad_optimize=auto_balanced "
        f"target_connector=1 detached_xray=1 "
        "directional_merge_pair_override=1 protected_uv=1 view_merge=1 "
        "surface_paint=1 shrinkwrap=1 "
        "bake_cage=1"
    )


if __name__ == "__main__":
    main()
