"""Capture the README showcase screenshots from a real Blender window.

Open a saved .blend (this skips the splash screen) with a UI, for example:

    blender.exe --factory-startup -p 40 40 1600 1000 blank.blend \\
      --python scripts/capture_showcase.py -- paint docs/images

Shots: paint, result, mirror, lens, sidebar. The GIF modes paint-gif,
mirror-gif and lens-gif save numbered frames (<mode>_NN.png) instead; join them
with scripts/make_gifs.py. Each run saves one PNG and quits. For the
picture only, the add-on panels are shown in the sidebar's first tab.
"""

from __future__ import annotations

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
SHOT, OUT = ARGS[0], Path(ARGS[1])
GIF = SHOT.endswith("-gif")
SHOT = SHOT.removesuffix("-gif")
NAMES = {
    "paint": "09-surface-paint-live.png",
    "result": "10-surface-paint-result.png",
    "mirror": "11-surface-paint-mirror.png",
    "lens": "13-topology-lens.png",
    "sidebar": "12-sidebar.png",
}

for panel in ui.CLASSES:
    panel.bl_category = "Item"
topology_transitions.register()
for name in ("VIEW3D_PT_transform",):
    if hasattr(bpy.types, name):
        bpy.utils.unregister_class(getattr(bpy.types, name))

instances = {}


def spy(cls, key):
    original = cls.invoke

    def invoke(self, context, event):
        result = original(self, context, event)
        instances[key] = self
        return result

    cls.invoke = invoke


spy(spo.QT_OT_surface_paint, "paint")
spy(lens_ops.QT_OT_topology_lens, "lens")


def pin_cursor(cls):
    """Keep the scripted cursor: real mouse moves would replace it."""

    original = cls.modal

    def modal(self, context, event):
        result = original(self, context, event)
        if "mouse" in instances:
            self._mouse = instances["mouse"]
        return result

    cls.modal = modal


pin_cursor(spo.QT_OT_surface_paint)
pin_cursor(lens_ops.QT_OT_topology_lens)


def view():
    window = bpy.context.window_manager.windows[0]
    area = max(
        (a for a in window.screen.areas if a.type == "VIEW_3D"),
        key=lambda a: a.width * a.height,
    )
    region = next(r for r in area.regions if r.type == "WINDOW")
    return window, area, region


def material(name, color):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = color
    return mat


def head(subdivide: int):
    bpy.ops.mesh.primitive_monkey_add(size=2.0)
    obj = bpy.context.active_object
    if subdivide:
        modifier = obj.modifiers.new("smooth", "SUBSURF")
        modifier.levels = subdivide
        bpy.ops.object.modifier_apply(modifier="smooth")
    bpy.ops.object.shade_smooth()
    obj.data.materials.append(material("Clay", (0.32, 0.30, 0.28, 1.0)))
    return obj


def hit(target, x, z):
    ok, location, normal, _ = target.ray_cast(Vector((x, -5.0, z)), Vector((0, 1, 0)))
    return (location, normal) if ok else None


def strokes(target, specs):
    result, normals = [], []
    for points, radius, erase in specs:
        hits = [h for h in (hit(target, x, z) for x, z in points) if h]
        result.append([Dab(tuple(h[0]), radius, erase, tuple(h[1])) for h in hits])
        normals.append([h[1] for h in hits])
    return result, normals


def frame(window, area, region, rotation, location, distance):
    rv3d = area.spaces.active.region_3d
    rv3d.view_perspective = "PERSP"
    rv3d.view_rotation = Euler(rotation).to_quaternion()
    rv3d.view_location = Vector(location)
    rv3d.view_distance = distance
    space = area.spaces.active
    space.show_region_ui = True
    space.overlay.show_floor = False
    space.overlay.show_axis_x = space.overlay.show_axis_y = False
    space.shading.color_type = "MATERIAL"


def paint_scene(mirror: bool):
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    target = head(3)
    target.name = "HighPoly Head"
    mesh = bpy.data.meshes.new("Retopo")
    low = bpy.data.objects.new("Retopo", mesh)
    bpy.context.collection.objects.link(low)
    low.data.materials.append(material("Retopo", (0.03, 0.42, 0.86, 1.0)))
    low.show_in_front = True
    bpy.ops.object.select_all(action="DESELECT")
    low.select_set(True)
    bpy.context.view_layer.objects.active = low
    bpy.ops.object.mode_set(mode="EDIT")
    settings = bpy.context.scene.topology_transitions
    settings.surface_target = target
    settings.paint_cell_size = 0.09
    settings.paint_mirror_x = mirror
    if mirror:
        specs = [
            ([(0.12 + i * 0.07, 0.72 - i * 0.015) for i in range(9)], 0.28, False),
        ]
    else:
        specs = [
            ([(-0.85 + i * 0.1, 0.55) for i in range(18)], 0.3, False),
            ([(0.6, 0.0 - i * 0.08) for i in range(5)], 0.24, False),
        ]
    return target, strokes(target, specs)


def run():
    window, area, region = view()
    if SHOT == "lens":
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete()
        low = head(0)
        low.name = "Low Poly Head"
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="DESELECT")
        frame(window, area, region, (radians(72), 0, radians(-24)), (0, 0, 0.1), 4.2)
        with bpy.context.temp_override(window=window, area=area, region=region):
            bpy.ops.mesh.quad_transition_topology_lens("INVOKE_DEFAULT")
    else:
        target, (paint, normals) = paint_scene(SHOT == "mirror")
        frame(window, area, region, (radians(70), 0, radians(-26)), (0.1, 0, 0.3), 4.3)
        if SHOT == "sidebar":
            pass
        elif SHOT == "result":
            spo.build_surface_paint(
                bpy.context, paint, normals, target=target, cell=0.09
            )
        else:
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.mesh.quad_transition_surface_paint(
                    "INVOKE_DEFAULT",
                    target_name=target.name,
                    cell_size=0.09,
                    mirror_x=SHOT == "mirror",
                )
            operator = instances["paint"]
            operator._strokes, operator._normals = paint, normals
            operator._replan()
            operator._mouse = (region.width * 0.6, region.height * 0.42)
            instances["mouse"] = operator._mouse
    area.tag_redraw()
    bpy.app.timers.register(start_gif if GIF else capture, first_interval=1.0)


FRAMES: list = []


def screen_of(area, region, point):
    from bpy_extras import view3d_utils

    rv3d = area.spaces.active.region_3d
    return view3d_utils.location_3d_to_region_2d(region, rv3d, Vector(point))


def start_gif():
    """Build the list of frame setters, then shoot them one by one."""

    window, area, region = view()
    if SHOT == "lens":
        lens = instances["lens"]
        lens._refresh()
        eye = area.spaces.active.region_3d.view_matrix.inverted().translation
        picks = {}
        for entry in lens._snapshot["verts"]:
            facing = (entry[2] - eye).normalized().dot(entry[3].normalized())
            if facing > -0.55 or entry[1] in picks:
                continue
            screen = screen_of(area, region, entry[2])
            if screen is not None and 0.2 < screen.x / region.width < 0.62:
                picks[entry[1]] = (screen.x, screen.y)
        for kind in ("e_pole", "n_pole", "regular", "high_pole", "doublet"):
            if kind in picks:
                FRAMES.append(lambda point=picks[kind]: instances.update(mouse=point))
    else:
        operator = instances["paint"]
        strokes, normals = operator._strokes, operator._normals
        steps = [(s, d) for s, stroke in enumerate(strokes) for d in range(len(stroke))]
        for s, d in steps[1::2] + [steps[-1]]:

            def setter(s=s, d=d):
                operator._strokes = [*strokes[:s], strokes[s][: d + 1]]
                operator._normals = [*normals[:s], normals[s][: d + 1]]
                operator._replan()
                point = screen_of(area, region, strokes[s][d].center)
                if point is not None:
                    instances["mouse"] = (point.x, point.y)

            FRAMES.append(setter)
    shoot_frame.index = 0
    next_frame()


def next_frame():
    window, area, region = view()
    if shoot_frame.index >= len(FRAMES):
        print("SHOWCASE_OK", SHOT, "frames", len(FRAMES))
        bpy.app.timers.register(quit_blender, first_interval=0.3)
        return
    FRAMES[shoot_frame.index]()
    for key in ("lens", "paint"):
        if key in instances and "mouse" in instances:
            instances[key]._mouse = instances["mouse"]
            instances[key]._hover_key = None
    area.tag_redraw()
    bpy.app.timers.register(shoot_frame, first_interval=0.35)


def shoot_frame():
    window, area, region = view()
    path = OUT / f"{SHOT}-gif_{shoot_frame.index:02d}.png"
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.screen.screenshot(filepath=str(path))
    shoot_frame.index += 1
    bpy.app.timers.register(next_frame, first_interval=0.05)


def aim_lens(area, region):
    """Put the scripted cursor on a front-facing 5-pole (after a redraw, so
    the view matrix matches the framed camera)."""

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
        if screen is not None:
            visible.append(screen)
    screen = min(visible, key=lambda point: abs(point.x - region.width * 0.42))
    instances["mouse"] = (screen.x, screen.y)


def capture():
    window, area, region = view()
    if SHOT == "lens":
        aim_lens(area, region)
    if SHOT == "sidebar":
        sidebar = next(r for r in area.regions if r.type == "UI")
        with bpy.context.temp_override(window=window, area=area, region=sidebar):
            for _ in range(75):
                bpy.ops.view2d.scroll_down(deltay=200)
    # Real mouse moves during the wait would replace the scripted cursor.
    for key in ("lens", "paint"):
        if key in instances and "mouse" in instances:
            instances[key]._mouse = instances["mouse"]
    area.tag_redraw()
    bpy.app.timers.register(shoot, first_interval=0.3)


def shoot():
    window, area, region = view()
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.screen.screenshot(filepath=str(OUT / NAMES[SHOT]))
    print("SHOWCASE_OK", SHOT)
    bpy.app.timers.register(quit_blender, first_interval=0.3)


def quit_blender():
    bpy.ops.wm.quit_blender()


bpy.app.timers.register(run, first_interval=0.8)
