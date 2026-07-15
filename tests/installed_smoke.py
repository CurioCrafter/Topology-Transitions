"""Smoke an installed copy from a temporary Blender user scripts directory."""

from __future__ import annotations

import os
from pathlib import Path

import bmesh
import bpy


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


def main() -> None:
    bpy.ops.preferences.addon_enable(module="topology_transitions")
    import topology_transitions

    expected_root = Path(os.environ["QT_EXPECTED_ADDON_ROOT"]).resolve()
    loaded = Path(topology_transitions.__file__).resolve()
    if expected_root not in loaded.parents:
        raise AssertionError(
            f"Loaded source copy {loaded} instead of installed root {expected_root}"
        )
    if not hasattr(bpy.types.Scene, "topology_transitions"):
        raise AssertionError("Installed add-on did not register scene settings")
    if topology_transitions.bl_info["version"] != (0, 3, 0):
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
    settings = bpy.context.scene.topology_transitions
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
        or len(example.data.polygons) != 56
    ):
        raise AssertionError(
            f"Installed example plane returned {example_result} / "
            f"{0 if example is None else len(example.data.polygons)} faces"
        )
    print(
        f"QT_INSTALLED_SMOKE_PASS module={loaded} "
        f"selected_quads={len(selected)} flows={flow_count} "
        f"strip_quads={strip_quads} example_quads=56"
    )


if __name__ == "__main__":
    main()
