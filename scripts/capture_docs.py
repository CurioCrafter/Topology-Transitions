"""Capture reproducible Topology Transitions screenshots from Blender.

Run Blender with a real UI, for example:

    blender.exe --factory-startup -p 40 40 1600 1000 \
      --python scripts/capture_docs.py -- --shot flow

Shots are saved to the Windows default Screenshots directory unless
``--output-dir`` is provided.
"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

import bmesh
import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import topology_transitions  # noqa: E402
from topology_transitions import ui as ui_module  # noqa: E402
from topology_transitions.bake_preview_ops import (  # noqa: E402
    build_bake_ray_preview,
)
from topology_transitions.flow_ops import build_flow_session  # noqa: E402
from topology_transitions.ribbon_ops import grow_ribbon_from_stroke  # noqa: E402
from topology_transitions.surface_ops import (  # noqa: E402
    inspect_bake_readiness,
    toggle_bake_cage,
)


def parse_args() -> argparse.Namespace:
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--shot",
        choices=("before", "after", "flow", "pole", "example", "ribbon", "bake"),
        required=True,
    )
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args(arguments)


ARGS = parse_args()
OUTPUT_DIR = (
    ARGS.output_dir.resolve()
    if ARGS.output_dir
    else Path.home() / "Pictures" / "Screenshots"
)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_NAMES = {
    "before": "01-select-patch.png",
    "after": "02-five-to-three-result.png",
    "flow": "03-edge-flow-scroll.png",
    "pole": "04-flow-termination.png",
    "example": "05-example-plane-strip.png",
    "ribbon": "06-connected-multi-strip.png",
    "bake": "07-bake-ray-preview.png",
}


def clear_scene() -> None:
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for data in (bpy.data.meshes, bpy.data.curves, bpy.data.materials):
        for block in list(data):
            if block.users == 0:
                data.remove(block)


def create_text(body: str, location, size: float = 0.32) -> None:
    bpy.ops.object.text_add(location=location)
    text = bpy.context.object
    text.data.body = body
    text.data.align_x = "CENTER"
    text.data.align_y = "CENTER"
    text.data.size = size
    text.data.extrude = 0.008
    text.show_in_front = True
    material = bpy.data.materials.new(f"Text_{len(bpy.data.materials)}")
    material.diffuse_color = (0.04, 0.55, 1.0, 1.0)
    text.data.materials.append(material)


def create_grid(name: str, width: int, height: int):
    vertices = [
        (x - width / 2, y - height / 2, 0.0)
        for y in range(height + 1)
        for x in range(width + 1)
    ]
    faces = []
    for y in range(height):
        for x in range(width):
            first = y * (width + 1) + x
            faces.append(
                (
                    first,
                    first + 1,
                    first + width + 2,
                    first + width + 1,
                )
            )
    mesh = bpy.data.meshes.new(f"{name}_mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    bpy.ops.object.select_all(action="DESELECT")
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(mesh)
    bm.select_mode = {"FACE"}
    for face in bm.faces:
        face.select_set(True)
    bm.select_flush_mode()
    top_edges = [
        edge
        for edge in bm.edges
        if all(abs(vertex.co.y - height / 2) < 1.0e-6 for vertex in edge.verts)
    ]
    bm.select_history.clear()
    bm.select_history.add(top_edges[len(top_edges) // 2])
    bmesh.update_edit_mesh(mesh, loop_triangles=True, destructive=False)
    return obj


def view_context():
    window = bpy.context.window_manager.windows[0]
    screen = window.screen
    area = max(
        (area for area in screen.areas if area.type == "VIEW_3D"),
        key=lambda candidate: candidate.width * candidate.height,
    )
    region = next(region for region in area.regions if region.type == "WINDOW")
    return window, screen, area, region


def maximize_view() -> None:
    window, screen, area, region = view_context()
    if len(screen.areas) > 1:
        with bpy.context.temp_override(
            window=window, screen=screen, area=area, region=region
        ):
            bpy.ops.screen.screen_full_area(use_hide_panels=False)


def configure_view(*, top: bool) -> tuple:
    window, screen, area, region = view_context()
    space = area.spaces.active
    space.show_region_ui = True
    space.overlay.show_floor = False
    space.overlay.show_axis_x = False
    space.overlay.show_axis_y = False
    space.overlay.show_relationship_lines = False
    space.shading.type = "SOLID"
    space.shading.light = "STUDIO"
    space.shading.color_type = "MATERIAL"
    with bpy.context.temp_override(
        window=window, screen=screen, area=area, region=region
    ):
        if top:
            bpy.ops.view3d.view_axis(type="TOP", align_active=False)
        bpy.ops.view3d.view_all(center=True)
        if not top:
            bpy.ops.view3d.view_orbit(angle=0.45, type="ORBITUP")
            bpy.ops.view3d.view_orbit(angle=0.55, type="ORBITLEFT")
    space.region_3d.view_distance *= 1.12
    area.tag_redraw()
    return window, screen, area, region


def setup_transition(
    *,
    apply: bool,
    title: str | None = None,
    subtitle: str | None = None,
) -> None:
    clear_scene()
    title = title or (
        "5 TO 3 TRANSITION  •  TWO GUIDED N-POLES"
        if apply
        else "SELECT A 5-FACE-WIDE QUAD PATCH"
    )
    subtitle = subtitle or "BOUNDARY STAYS PINNED  •  ALL QUADS"
    create_text(title, (0.0, 1.75, 0.04), 0.22 if len(title) > 32 else 0.34)
    create_text(
        subtitle,
        (0.0, -1.75, 0.04),
        0.18 if len(subtitle) > 36 else 0.25,
    )
    create_grid("FiveToThree", 5, 2)
    settings = bpy.context.scene.topology_transitions
    settings.transition = "FIVE_TO_THREE"
    settings.pole_side = "CENTER"
    settings.relax_strength = 0.55
    settings.relax_iterations = 24
    settings.conform_surface = True
    if not apply:
        return
    result = bpy.ops.mesh.quad_transition_apply(
        transition="FIVE_TO_THREE",
        pole_side="CENTER",
        relax_strength=0.55,
        relax_iterations=24,
        conform_surface=True,
    )
    if result != {"FINISHED"}:
        raise RuntimeError(f"Transition screenshot setup returned {result}")
    bm = bmesh.from_edit_mesh(bpy.context.edit_object.data)
    for face in bm.faces:
        face.select_set(False)
    for edge in bm.edges:
        edge.select_set(False)
    for vertex in bm.verts:
        boundary = any(len(edge.link_faces) < 2 for edge in vertex.link_edges)
        vertex.select_set(len(vertex.link_edges) == 3 and not boundary)
    bm.select_mode = {"VERT"}
    bpy.context.tool_settings.mesh_select_mode = (True, False, False)
    bmesh.update_edit_mesh(
        bpy.context.edit_object.data,
        loop_triangles=False,
        destructive=False,
    )


def setup_torus_flow() -> None:
    clear_scene()
    bpy.ops.mesh.primitive_torus_add(
        align="WORLD",
        major_segments=24,
        minor_segments=8,
        location=(0.0, 0.0, 0.0),
        major_radius=3.0,
        minor_radius=1.05,
    )
    obj = bpy.context.object
    obj.name = "EdgeFlowTorus"
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(obj.data)
    for vertex in bm.verts:
        vertex.select_set(False)
    for edge in bm.edges:
        edge.select_set(False)
    for face in bm.faces:
        face.select_set(False)
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    settings = bpy.context.scene.topology_transitions
    settings.flow_mode = "FACE_STRIPS"
    settings.flow_scope = "ALL"
    settings.flow_sort = "SIDE_TO_SIDE"
    settings.flow_min_edges = 4
    settings.flow_show_neighbors = True
    settings.flow_focus_view = True
    session = build_flow_session(bpy.context)
    settings.flow_index = max(
        range(len(session.flows)),
        key=lambda index: session.flows[index].quad_count,
    )


def setup_pole_flow() -> None:
    setup_transition(
        apply=True,
        title="WHOLE QUAD FLOW REGIONS THROUGH A TRANSITION",
        subtitle="POLE-BOUNDED PATCHES  |  COMPLETE COLOURED FLOW MAP",
    )
    bm = bmesh.from_edit_mesh(bpy.context.edit_object.data)
    for vertex in bm.verts:
        vertex.select_set(False)
    for edge in bm.edges:
        edge.select_set(False)
    for face in bm.faces:
        face.select_set(False)
    bmesh.update_edit_mesh(
        bpy.context.edit_object.data,
        loop_triangles=False,
        destructive=False,
    )
    settings = bpy.context.scene.topology_transitions
    settings.flow_mode = "REGIONS"
    settings.flow_scope = "ALL"
    settings.flow_sort = "LARGEST"
    settings.flow_min_edges = 1
    settings.flow_show_full_map = True
    settings.flow_focus_view = True
    session = build_flow_session(bpy.context)
    settings.flow_index = max(
        range(len(session.flows)),
        key=lambda index: session.flows[index].quad_count,
    )


def setup_example_flow() -> None:
    clear_scene()
    result = bpy.ops.object.quad_transition_add_example_plane()
    if result != {"FINISHED"}:
        raise RuntimeError(f"Example plane screenshot setup returned {result}")
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(bpy.context.edit_object.data)
    for vertex in bm.verts:
        vertex.select_set(False)
    for edge in bm.edges:
        edge.select_set(False)
    for face in bm.faces:
        face.select_set(False)
    bmesh.update_edit_mesh(
        bpy.context.edit_object.data,
        loop_triangles=False,
        destructive=False,
    )


def create_material(name: str, color: tuple[float, float, float, float]):
    material = bpy.data.materials.new(name)
    material.diffuse_color = color
    return material


def setup_connected_ribbon() -> None:
    clear_scene()
    create_text(
        "ONE WELDED 5-LANE SHEET  ->  5 TO 3 TRANSITION",
        (2.5, 11.8, 0.06),
        0.26,
    )
    create_text(
        "EXACT BOTTOM BOUNDARY  |  OUTPUT CHAIN READY TO CONTINUE",
        (2.5, -1.15, 0.06),
        0.18,
    )
    target_mesh = bpy.data.meshes.new("RibbonSurface_mesh")
    target_mesh.from_pydata(
        ((-3, -2, -0.03), (8, -2, -0.03), (8, 13, -0.03), (-3, 13, -0.03)),
        (),
        ((0, 1, 2, 3),),
    )
    target_mesh.update()
    target = bpy.data.objects.new("Ribbon Surface Target", target_mesh)
    bpy.context.collection.objects.link(target)
    target.data.materials.append(
        create_material("RibbonTarget", (0.035, 0.045, 0.065, 1.0))
    )

    lanes = 5
    vertices = [
        (float(x), float(y), 0.0)
        for y in range(2)
        for x in range(lanes + 1)
    ]
    faces = [
        (x, x + 1, lanes + x + 2, lanes + x + 1)
        for x in range(lanes)
    ]
    mesh = bpy.data.meshes.new("ConnectedRibbon_mesh")
    mesh.from_pydata(vertices, (), faces)
    mesh.update()
    low = bpy.data.objects.new("Connected Ribbon", mesh)
    bpy.context.collection.objects.link(low)
    low.data.materials.append(
        create_material("ConnectedRibbon", (0.03, 0.42, 0.86, 1.0))
    )
    low.show_in_front = True
    bpy.ops.object.select_all(action="DESELECT")
    low.select_set(True)
    bpy.context.view_layer.objects.active = low
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.context.tool_settings.mesh_select_mode = (False, True, False)
    bm = bmesh.from_edit_mesh(mesh)
    bm.select_mode = {"EDGE"}
    for edge in bm.edges:
        edge.select_set(
            all(abs(vertex.co.y - 1.0) < 1.0e-6 for vertex in edge.verts)
        )
    bm.select_flush_mode()
    bmesh.update_edit_mesh(mesh, loop_triangles=True, destructive=False)

    grow_ribbon_from_stroke(
        bpy.context,
        [
            Vector((2.5, 2.7, -0.03)),
            Vector((2.75, 4.7, -0.03)),
            Vector((2.55, 6.8, -0.03)),
        ],
        [Vector((0, 0, 1))] * 3,
        layout="UNIFORM",
        transition="FIVE_TO_THREE",
        segments=6,
        width_scale=1.0,
        pole_side="CENTER",
        mirror=False,
        pole_spacing=1.0,
        target=target,
        project_limit=1.0,
    )
    grow_ribbon_from_stroke(
        bpy.context,
        [Vector((2.55, 8.6, -0.03)), Vector((2.65, 10.5, -0.03))],
        [Vector((0, 0, 1))] * 2,
        layout="TRANSITION",
        transition="FIVE_TO_THREE",
        segments=3,
        width_scale=0.9,
        pole_side="CENTER",
        mirror=False,
        pole_spacing=1.0,
        target=target,
        project_limit=1.0,
    )
    settings = bpy.context.scene.topology_transitions
    settings.surface_target = target
    settings.draw_layout = "TRANSITION"
    settings.transition = "FIVE_TO_THREE"
    settings.draw_segments = 3
    settings.draw_width_scale = 0.9


def setup_bake_preview() -> None:
    clear_scene()
    create_text(
        "BAKE ENVELOPE + SAMPLED SOURCE RAYS",
        (0.0, 3.9, 0.6),
        0.3,
    )
    low = create_grid("BakePreviewLow", 5, 6)
    bpy.ops.object.mode_set(mode="OBJECT")
    low.data.uv_layers.new(name="UVMap")
    low.color = (0.03, 0.35, 0.95, 1.0)
    low.data.materials.append(
        create_material("BakeLow", (0.03, 0.35, 0.95, 1.0))
    )
    bake_material = low.data.materials[0]
    bake_material.use_nodes = True
    image_node = bake_material.node_tree.nodes.new("ShaderNodeTexImage")
    image_node.image = bpy.data.images.new("Bake Preview Target", 64, 64)
    bake_material.node_tree.nodes.active = image_node

    high_mesh = bpy.data.meshes.new("BakeHigh_mesh")
    high_mesh.from_pydata(
        (
            (-1.25, -2.6, 0.22),
            (1.25, -2.6, 0.22),
            (1.25, 2.6, 0.22),
            (-1.25, 2.6, 0.22),
        ),
        (),
        ((0, 1, 2, 3),),
    )
    high_mesh.update()
    high = bpy.data.objects.new("Selected High Source", high_mesh)
    bpy.context.collection.objects.link(high)
    high.color = (0.12, 0.15, 0.2, 1.0)
    high.data.materials.append(
        create_material("BakeHigh", (0.12, 0.15, 0.2, 1.0))
    )
    bpy.ops.object.select_all(action="DESELECT")
    low.select_set(True)
    high.select_set(True)
    bpy.context.view_layer.objects.active = low
    cage = toggle_bake_cage(bpy.context, low, distance=0.45)["cage"]
    cage.color = (1.0, 0.16, 0.02, 1.0)
    build_bake_ray_preview(
        bpy.context,
        low,
        max_ray_distance=0.45,
        use_cage=True,
        sample_limit=100,
    )
    inspect_bake_readiness(bpy.context, use_cage=True, cage=cage)
    settings = bpy.context.scene.topology_transitions
    settings.bake_use_cage = True
    settings.bake_cage_distance = 0.45
    settings.bake_ray_samples = 100


def invoke_flow(area, region) -> None:
    window = bpy.context.window_manager.windows[0]
    with bpy.context.temp_override(
        window=window,
        screen=window.screen,
        area=area,
        region=region,
    ):
        result = bpy.ops.mesh.quad_transition_edge_flow_scroll(
            "INVOKE_DEFAULT",
            start_index=bpy.context.scene.topology_transitions.flow_index,
        )
    if result != {"RUNNING_MODAL"}:
        raise RuntimeError(f"Flow screenshot setup returned {result}")


def capture() -> None:
    window, screen, area, region = view_context()
    area.tag_redraw()
    output = OUTPUT_DIR / OUTPUT_NAMES[ARGS.shot]
    with bpy.context.temp_override(
        window=window, screen=screen, area=area, region=region
    ):
        bpy.ops.screen.screenshot(filepath=str(output))
    print(f"QT_SCREENSHOT_PASS shot={ARGS.shot} path={output}")

    def quit_blender():
        bpy.ops.wm.quit_blender()

    bpy.app.timers.register(quit_blender, first_interval=0.35)


def guarded(callback):
    try:
        return callback()
    except Exception:
        error_path = OUTPUT_DIR / "TopologyTransitions-capture-error.txt"
        details = traceback.format_exc()
        error_path.write_text(details, encoding="utf-8")
        print(f"QT_SCREENSHOT_FAIL path={error_path}\n{details}", flush=True)

        def quit_after_error():
            bpy.ops.wm.quit_blender()

        bpy.app.timers.register(quit_after_error, first_interval=0.25)
        return None


def setup() -> float:
    ui_module.QT_PT_sidebar.bl_category = "Item"
    topology_transitions.register()
    maximize_view()
    if ARGS.shot == "before":
        setup_transition(apply=False)
        configure_view(top=True)
    elif ARGS.shot == "after":
        setup_transition(apply=True)
        configure_view(top=True)
    elif ARGS.shot == "flow":
        setup_torus_flow()
        _window, _screen, area, region = configure_view(top=False)
        invoke_flow(area, region)
    elif ARGS.shot == "pole":
        setup_pole_flow()
        _window, _screen, area, region = configure_view(top=True)
        invoke_flow(area, region)
    elif ARGS.shot == "example":
        setup_example_flow()
        configure_view(top=True)
    elif ARGS.shot == "ribbon":
        setup_connected_ribbon()
        configure_view(top=True)
    else:
        setup_bake_preview()
        _window, _screen, area, _region = configure_view(top=False)
        area.spaces.active.shading.color_type = "OBJECT"
    bpy.app.timers.register(lambda: guarded(capture), first_interval=0.8)
    return None


bpy.app.timers.register(lambda: guarded(setup), first_interval=0.8)
