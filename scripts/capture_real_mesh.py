"""Capture before/after screenshots of the add-on on a real mesh file.

Open a saved .blend (this skips the splash screen) with a UI, for example:

    blender.exe --factory-startup -p 40 40 1600 1000 blank.blend \\
      --python scripts/capture_real_mesh.py -- mesh.fbx holes-before out.png

Shots:
- holes-before: every open or over-connected edge selected (orange).
- holes-after: the faces Auto Solve All Non-Manifold Areas added, selected.
  Add -zoom to either to frame the biggest hole instead of the whole mesh.
- lens: the Topology Lens with its card on a front-facing 5-pole.
- lens-gif: one frame per pole kind hovered (<out stem>_NN.png).
- paint: a Surface Paint stroke across the front of the mesh.

Each run imports the file, joins its meshes, saves one PNG and quits. The
view looks at the mesh from the front (-Y) and slightly from the side.
"""

from __future__ import annotations

import json
import sys
from math import radians
from pathlib import Path

import bpy
from mathutils import Euler, Vector

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import topology_transitions  # noqa: E402
from topology_transitions import lens_ops, ui  # noqa: E402
from topology_transitions import surface_paint_ops as spo  # noqa: E402
from topology_transitions.paint_grid import Dab  # noqa: E402

ARGS = sys.argv[sys.argv.index("--") + 1 :]
MESH, SHOT, OUT = Path(ARGS[0]), ARGS[1], Path(ARGS[2])
INFO: dict = {"mesh": MESH.name, "shot": SHOT}

topology_transitions.register()
ui.set_tab("Item")  # the screenshot shows the panels in the first tab
# Animated view moves (Auto Solve focuses its result) would undo the framing.
bpy.context.preferences.view.smooth_view = 0
instances = {}


def spy(cls, key):
    original_invoke, original_modal = cls.invoke, cls.modal

    def invoke(self, context, event):
        result = original_invoke(self, context, event)
        instances[key] = self
        return result

    def modal(self, context, event):
        result = original_modal(self, context, event)
        if "mouse" in instances:
            self._mouse = instances["mouse"]
        return result

    cls.invoke, cls.modal = invoke, modal


spy(lens_ops.QT_OT_topology_lens, "lens")


def view():
    window = bpy.context.window_manager.windows[0]
    area = max(
        (a for a in window.screen.areas if a.type == "VIEW_3D"),
        key=lambda a: a.width * a.height,
    )
    region = next(r for r in area.regions if r.type == "WINDOW")
    return window, area, region


def import_mesh():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    suffix = MESH.suffix.lower()
    if suffix in {".glb", ".gltf"}:
        bpy.ops.import_scene.gltf(filepath=str(MESH))
    elif suffix == ".fbx":
        bpy.ops.import_scene.fbx(filepath=str(MESH))
    elif suffix == ".obj":
        bpy.ops.wm.obj_import(filepath=str(MESH))
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    for obj in bpy.context.scene.objects:
        obj.select_set(obj in meshes)
        if obj.type != "MESH":
            obj.hide_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    if len(meshes) > 1:
        bpy.ops.object.join()
    obj = bpy.context.view_layer.objects.active
    for modifier in list(obj.modifiers):
        obj.modifiers.remove(modifier)
    obj.shape_key_clear()
    # Armature parents and import scale would move the mesh while editing.
    matrix = obj.matrix_world.copy()
    obj.parent = None
    obj.matrix_world = matrix
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    obj.name = MESH.stem
    obj.data.materials.clear()
    clay = bpy.data.materials.new("Clay")
    clay.diffuse_color = (0.62, 0.6, 0.57, 1.0)
    obj.data.materials.append(clay)
    INFO["faces"] = len(obj.data.polygons)
    return obj


def frame(area, region, obj, fill: float):
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    low = Vector(map(min, *corners))
    high = Vector(map(max, *corners))
    rv3d = area.spaces.active.region_3d
    rv3d.view_perspective = "PERSP"
    rv3d.view_rotation = Euler((radians(78), 0, radians(-28))).to_quaternion()
    rv3d.view_location = (low + high) / 2
    rv3d.view_distance = (high - low).length * fill
    space = area.spaces.active
    space.show_region_ui = True
    space.overlay.show_floor = False
    space.overlay.show_axis_x = space.overlay.show_axis_y = False
    space.shading.color_type = "MATERIAL"
    overlay = space.overlay
    overlay.show_edge_sharp = overlay.show_edge_seams = False


def edit(obj):
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_mode(type="EDGE")
    bpy.ops.mesh.select_all(action="DESELECT")


def biggest_hole():
    """Centre and size of the largest area Find Holes reports, in world space."""

    from topology_transitions.manifold_ops import _report_from_context

    obj, _mesh, bm, report = _report_from_context(bpy.context)
    INFO["problem_edges"] = len(report.issue_edge_ids)
    INFO["problem_areas"] = len(report.components)
    if not report.components:
        return None
    component = max(report.components, key=lambda c: len(c.edge_ids))
    points = [
        obj.matrix_world @ vert.co
        for edge_id in component.edge_ids
        for vert in bm.edges[edge_id].verts
    ]
    center = sum(points, Vector()) / len(points)
    return center, max((p - center).length for p in points)


def select_problems():
    """Select exactly the edges Find Holes reports, all at once."""

    import bmesh

    from topology_transitions.manifold_ops import _report_from_context

    _obj, mesh, bm, report = _report_from_context(bpy.context)
    issues = set(report.issue_edge_ids)
    for edge in bm.edges:
        edge.select_set(edge.index in issues)
    bm.select_flush_mode()
    bmesh.update_edit_mesh(mesh)


def select_new_faces(first_new: int):
    import bmesh

    mesh = bpy.context.edit_object.data
    bm = bmesh.from_edit_mesh(mesh)
    bpy.ops.mesh.select_mode(type="FACE")
    for face in bm.faces:
        face.select_set(face.index >= first_new)
    bmesh.update_edit_mesh(mesh)


def look_at(area, center, size):
    rv3d = area.spaces.active.region_3d
    rv3d.view_location = center
    rv3d.view_distance = max(size * 6.0, 0.05)


def paint(obj):
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    low = Vector(map(min, *corners))
    high = Vector(map(max, *corners))
    size = high - low
    depsgraph = bpy.context.evaluated_depsgraph_get()
    hits = []
    for index in range(16):
        x = low.x + size.x * (0.22 + 0.56 * index / 15)
        z = low.z + size.z * 0.58
        ok, location, normal, *_ = bpy.context.scene.ray_cast(
            depsgraph, Vector((x, low.y - size.length, z)), Vector((0, 1, 0))
        )
        if ok:
            hits.append((location, normal))
    mesh = bpy.data.meshes.new("Retopo")
    retopo = bpy.data.objects.new("Retopo", mesh)
    bpy.context.collection.objects.link(retopo)
    blue = bpy.data.materials.new("Retopo")
    blue.diffuse_color = (0.03, 0.42, 0.86, 1.0)
    mesh.materials.append(blue)
    retopo.show_in_front = True
    bpy.ops.object.select_all(action="DESELECT")
    edit(retopo)
    radius = size.length * 0.07
    stroke = [Dab(tuple(p), radius, False, tuple(n)) for p, n in hits]
    stats = spo.build_surface_paint(
        bpy.context,
        [stroke],
        [[n for _p, n in hits]],
        target=obj,
        cell=size.length / 70,
    )
    bpy.ops.mesh.select_all(action="SELECT")
    INFO["quads"] = stats["quads"]


def aim_lens(area, region):
    from bpy_extras import view3d_utils

    lens = instances["lens"]
    lens._refresh()
    rv3d = area.spaces.active.region_3d
    eye = rv3d.view_matrix.inverted().translation
    visible = []
    for entry in lens._snapshot["verts"]:
        facing = (entry[2] - eye).normalized().dot(entry[3].normalized())
        if entry[1] != "e_pole" or facing > -0.5:
            continue
        screen = view3d_utils.location_3d_to_region_2d(region, rv3d, entry[2])
        if screen is not None and 0.3 < screen.x / region.width < 0.6:
            visible.append(screen)
    target = Vector((region.width * 0.45, region.height * 0.55))
    screen = min(visible, key=lambda point: (point - target).length)
    instances["mouse"] = (screen.x, screen.y)
    lens._mouse = instances["mouse"]
    lens._hover_key = None


def run():
    window, area, region = view()
    obj = import_mesh()
    frame(area, region, obj, 1.0 if SHOT.startswith("holes") else 1.25)
    rv3d = area.spaces.active.region_3d
    framed = (rv3d.view_location.copy(), rv3d.view_distance, rv3d.view_rotation.copy())
    edit(obj)
    hole = biggest_hole() if SHOT.startswith("holes") else None
    if SHOT.startswith("holes"):
        if SHOT.startswith("holes-after"):
            first_new = len(obj.data.polygons)
            bpy.ops.mesh.quad_transition_solve_all_nonmanifold()
            select_new_faces(first_new)
            INFO["after"] = biggest_hole() is None
        else:
            select_problems()
    elif SHOT.startswith("lens"):
        with bpy.context.temp_override(window=window, area=area, region=region):
            bpy.ops.mesh.quad_transition_topology_lens("INVOKE_DEFAULT")
    elif SHOT == "paint":
        bpy.ops.object.mode_set(mode="OBJECT")
        paint(obj)
    # Again last: Auto Solve focuses the view on what it leaves selected.
    rv3d.view_location, rv3d.view_distance, rv3d.view_rotation = framed
    if hole is not None and SHOT.endswith("-zoom"):
        look_at(area, *hole)
    area.tag_redraw()
    bpy.app.timers.register(capture, first_interval=1.5)


def lens_picks(area, region):
    """Screen points of one front-facing vertex per Lens kind."""

    from bpy_extras import view3d_utils

    lens = instances["lens"]
    lens._refresh()
    rv3d = area.spaces.active.region_3d
    eye = rv3d.view_matrix.inverted().translation
    target = Vector((region.width * 0.45, region.height * 0.55))
    best = {}
    for entry in lens._snapshot["verts"]:
        facing = (entry[2] - eye).normalized().dot(entry[3].normalized())
        if facing > -0.5:
            continue
        screen = view3d_utils.location_3d_to_region_2d(region, rv3d, entry[2])
        if screen is None or not 0.25 < screen.x / region.width < 0.65:
            continue
        distance = (screen - target).length
        if entry[1] not in best or distance < best[entry[1]][0]:
            best[entry[1]] = (distance, (screen.x, screen.y))
    kinds = ("e_pole", "n_pole", "regular", "high_pole", "doublet", "border")
    return [best[kind][1] for kind in kinds if kind in best]


FRAMES: list = []


def capture():
    window, area, region = view()
    if SHOT == "lens-gif":
        FRAMES.extend(lens_picks(area, region))
        bpy.app.timers.register(next_frame, first_interval=0.3)
        return
    if SHOT == "lens":
        aim_lens(area, region)
    area.tag_redraw()
    bpy.app.timers.register(shoot, first_interval=0.5)


def shoot():
    window, area, region = view()
    if "lens" in instances:
        instances["lens"]._mouse = instances["mouse"]
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.screen.screenshot(filepath=str(OUT))
    print("REAL_MESH_SHOT " + json.dumps(INFO))
    bpy.app.timers.register(quit_blender, first_interval=0.3)


def next_frame():
    window, area, region = view()
    if not FRAMES:
        print("REAL_MESH_SHOT " + json.dumps(INFO))
        bpy.app.timers.register(quit_blender, first_interval=0.3)
        return
    instances["mouse"] = FRAMES.pop(0)
    lens = instances["lens"]
    lens._mouse, lens._hover_key = instances["mouse"], None
    area.tag_redraw()
    bpy.app.timers.register(shoot_frame, first_interval=0.4)


def shoot_frame():
    window, area, region = view()
    index = INFO.setdefault("frames", 0)
    path = OUT.with_name(f"{OUT.stem}_{index:02d}.png")
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.screen.screenshot(filepath=str(path))
    INFO["frames"] = index + 1
    bpy.app.timers.register(next_frame, first_interval=0.05)


def quit_blender():
    bpy.ops.wm.quit_blender()


bpy.app.timers.register(run, first_interval=0.8)
