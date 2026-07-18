"""Smoke an installed copy from a temporary Blender user scripts directory."""

from __future__ import annotations

import os
from pathlib import Path

import bmesh
import bpy
from mathutils import Vector


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


def create_connected_ribbon_fixture():
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    lanes = 3
    target_mesh = bpy.data.meshes.new("installed_ribbon_target_mesh")
    target_mesh.from_pydata(
        ((-2, -1, 0.5), (5, -1, 0.5), (5, 7, 0.5), (-2, 7, 0.5)),
        (),
        ((0, 1, 2, 3),),
    )
    target_mesh.update()
    target = bpy.data.objects.new("InstalledRibbonTarget", target_mesh)
    bpy.context.collection.objects.link(target)

    mesh = bpy.data.meshes.new("installed_ribbon_mesh")
    vertices = [
        (float(x), float(y), 0.5)
        for y in range(2)
        for x in range(lanes + 1)
    ]
    faces = [
        (x, x + 1, lanes + x + 2, lanes + x + 1)
        for x in range(lanes)
    ]
    mesh.from_pydata(vertices, (), faces)
    mesh.update()
    low = bpy.data.objects.new("InstalledRibbonLow", mesh)
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
        edge.select_set(
            all(abs(vertex.co.y - 1.0) < 1.0e-6 for vertex in edge.verts)
        )
    bm.select_flush_mode()
    bmesh.update_edit_mesh(mesh, loop_triangles=True, destructive=False)
    return low, target


def main() -> None:
    bpy.ops.preferences.addon_enable(module="topology_transitions")
    import topology_transitions
    from topology_transitions.ribbon_ops import grow_ribbon_from_stroke
    from topology_transitions.surface_ops import find_bake_cage

    expected_root = Path(os.environ["QT_EXPECTED_ADDON_ROOT"]).resolve()
    loaded = Path(topology_transitions.__file__).resolve()
    if expected_root not in loaded.parents:
        raise AssertionError(
            f"Loaded source copy {loaded} instead of installed root {expected_root}"
        )
    if not hasattr(bpy.types.Scene, "topology_transitions"):
        raise AssertionError("Installed add-on did not register scene settings")
    if topology_transitions.bl_info["version"] != (0, 6, 1):
        raise AssertionError(
            f"Installed add-on reported {topology_transitions.bl_info['version']}"
        )

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
    if (
        single_result != {"FINISHED"}
        or len(single_selected) != 31
        or any(len(face.verts) != 4 for face in single_bm.faces)
    ):
        raise AssertionError(
            f"Installed single-quad insertion failed: {single_result}"
        )
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

    low, target = create_connected_ribbon_fixture()
    ribbon_stats = grow_ribbon_from_stroke(
        bpy.context,
        [Vector((1.5, 3.0, 0.5)), Vector((1.5, 5.0, 0.5))],
        [Vector((0.0, 0.0, 1.0)), Vector((0.0, 0.0, 1.0))],
        layout="UNIFORM",
        transition="THREE_TO_ONE",
        segments=2,
        width_scale=1.0,
        pole_side="CENTER",
        mirror=False,
        pole_spacing=1.0,
        target=target,
        project_limit=1.0,
    )
    ribbon_bm = bmesh.from_edit_mesh(low.data)
    selected_output = [edge for edge in ribbon_bm.edges if edge.select]
    if (
        ribbon_stats["new_faces"] != 6
        or ribbon_stats["anchor_edges"] != 3
        or ribbon_stats["output_edges"] != 3
        or len(selected_output) != 3
        or len(ribbon_bm.faces) != 9
    ):
        raise AssertionError(f"Installed connected ribbon failed: {ribbon_stats}")
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
        f"flow_quads={strip_quads} single_quad={len(single_selected)} "
        f"example_quads=186 transitions=8 repairs=1 open_edges=4 "
        f"ribbon_quads=6 shrinkwrap=1 bake_cage=1"
    )


if __name__ == "__main__":
    main()
