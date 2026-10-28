"""Run every main tool on one real mesh file and print one JSON result line.

    blender -b --factory-startup --python tests/real_mesh_stress.py -- mesh.glb

Imports the file, joins its meshes, then runs Find Holes, the Topology Lens
snapshot, the detached-face X-ray, the flow browser, Solve Selected Tris,
optimization analysis, Auto Solve Non-Manifold (on a copy) and Surface Paint,
recording status, timing, and whether each result is still manifold. This is a
manual robustness sweep over user meshes; it is not part of the smoke suite.
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

import bmesh
import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import topology_transitions  # noqa: E402
from topology_transitions.lens_ops import lens_snapshot  # noqa: E402
from topology_transitions.paint_grid import Dab  # noqa: E402
from topology_transitions.surface_paint_ops import build_surface_paint  # noqa: E402

PATH = Path(sys.argv[sys.argv.index("--") + 1])
RESULT: dict = {"file": PATH.name, "blender": bpy.app.version_string, "tests": {}}


def run(name, function):
    start = time.perf_counter()
    try:
        detail = function()
        status = "ok"
    except Exception as exc:  # noqa: BLE001 - the sweep records every failure
        detail = f"{type(exc).__name__}: {exc}".strip()
        # Operators report a clean, user-facing refusal as "Error: ...".
        if isinstance(exc, RuntimeError) and str(exc).startswith("Error:"):
            status = "refused"
        else:
            status = "error"
            RESULT.setdefault("tracebacks", {})[name] = traceback.format_exc()[-1500:]
    RESULT["tests"][name] = {
        "status": status,
        "seconds": round(time.perf_counter() - start, 2),
        "detail": detail,
    }


def nonmanifold_edges(obj) -> int:
    bm = bmesh.from_edit_mesh(obj.data)
    return sum(1 for edge in bm.edges if len(edge.link_faces) > 2)


def import_mesh():
    suffix = PATH.suffix.lower()
    if suffix in {".glb", ".gltf"}:
        bpy.ops.import_scene.gltf(filepath=str(PATH))
    elif suffix == ".fbx":
        bpy.ops.import_scene.fbx(filepath=str(PATH))
    elif suffix == ".obj":
        bpy.ops.wm.obj_import(filepath=str(PATH))
    elif suffix == ".dae":
        bpy.ops.wm.collada_import(filepath=str(PATH))
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    for obj in bpy.context.scene.objects:
        obj.select_set(obj in meshes)
    bpy.context.view_layer.objects.active = meshes[0]
    if len(meshes) > 1:
        bpy.ops.object.join()
    obj = bpy.context.view_layer.objects.active
    for modifier in list(obj.modifiers):
        obj.modifiers.remove(modifier)
    obj.shape_key_clear()
    obj.name = "Source"
    RESULT["verts"] = len(obj.data.vertices)
    RESULT["faces"] = len(obj.data.polygons)
    return obj


def edit(obj):
    bpy.context.view_layer.objects.active = obj
    if obj.mode != "OBJECT" or bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for other in bpy.context.scene.objects:
        other.select_set(other is obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="DESELECT")


def main():
    topology_transitions.register()
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    source = import_mesh()
    settings = bpy.context.scene.topology_transitions

    def manifold():
        edit(source)
        bpy.ops.mesh.quad_transition_check_manifold()
        return {
            "issues": settings.manifold_issue_count,
            "areas": settings.manifold_component_count,
            "open": settings.manifold_open_edge_count,
            "over": settings.manifold_nonmanifold_edge_count,
        }

    def lens():
        edit(source)
        snapshot = lens_snapshot(source)
        return {**snapshot["faces"], "e_poles": snapshot["counts"]["e_pole"]}

    def detached():
        edit(source)
        result = bpy.ops.mesh.quad_transition_inspect_detached_faces()
        return {
            "result": sorted(result),
            "regions": settings.loose_xray_component_count,
        }

    def flow():
        edit(source)
        settings.flow_scope = "ALL"
        result = bpy.ops.mesh.quad_transition_edge_flow_step(
            direction=0, select_current=True
        )
        return {"result": sorted(result), "flows": settings.flow_count}

    def solve_tris():
        edit(source)
        bpy.ops.object.mode_set(mode="OBJECT")
        work = source.copy()
        work.data = source.data.copy()
        bpy.context.collection.objects.link(work)
        edit(work)
        bm = bmesh.from_edit_mesh(work.data)
        tris = [face for face in bm.faces if len(face.verts) == 3][:40]
        for face in tris:
            face.select_set(True)
        bmesh.update_edit_mesh(work.data)
        if not tris:
            return "no triangles"
        before = len(bm.faces)
        result = bpy.ops.mesh.quad_transition_solve_selected_tris()
        bm = bmesh.from_edit_mesh(work.data)
        detail = {
            "result": sorted(result),
            "faces": f"{before}->{len(bm.faces)}",
            "over_edges": nonmanifold_edges(work),
        }
        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.data.objects.remove(work)
        bpy.context.view_layer.objects.active = source
        return detail

    def optimize():
        edit(source)
        result = bpy.ops.mesh.quad_transition_analyze_optimization()
        return {
            "result": sorted(result),
            "candidates": settings.optimize_candidate_loops,
        }

    def auto_solve():
        edit(source)
        bpy.ops.object.mode_set(mode="OBJECT")
        work = source.copy()
        work.data = source.data.copy()
        bpy.context.collection.objects.link(work)
        edit(work)
        result = bpy.ops.mesh.quad_transition_solve_all_nonmanifold()
        bpy.ops.mesh.quad_transition_check_manifold()
        detail = {
            "result": sorted(result),
            "issues_after": settings.manifold_issue_count,
            "over_edges": nonmanifold_edges(work),
        }
        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.data.objects.remove(work)
        bpy.context.view_layer.objects.active = source
        return detail

    def paint():
        edit(source)
        bpy.ops.object.mode_set(mode="OBJECT")
        corners = [source.matrix_world @ Vector(c) for c in source.bound_box]
        low = Vector(map(min, *corners))
        high = Vector(map(max, *corners))
        size = high - low
        diagonal = size.length
        depsgraph = bpy.context.evaluated_depsgraph_get()
        hits = []
        for index in range(7):
            x = low.x + size.x * (0.3 + 0.4 * index / 6)
            z = low.z + size.z * 0.55
            origin = Vector((x, low.y - diagonal, z))
            ok, location, normal, *_ = bpy.context.scene.ray_cast(
                depsgraph, origin, Vector((0, 1, 0))
            )
            if ok:
                hits.append((location, normal))
        if len(hits) < 2:
            return "front rays missed the mesh"
        mesh = bpy.data.meshes.new("Retopo")
        retopo = bpy.data.objects.new("Retopo", mesh)
        bpy.context.collection.objects.link(retopo)
        edit(retopo)
        radius = diagonal * 0.06
        stroke = [Dab(tuple(p), radius, False, tuple(n)) for p, n in hits]
        stats = build_surface_paint(
            bpy.context,
            [stroke],
            [[n for _p, n in hits]],
            target=source,
            cell=diagonal / 80,
            mirror_x=True,
        )
        bm = bmesh.from_edit_mesh(mesh)
        return {
            "quads": stats["quads"],
            "joins": stats["bridges"],
            "over_edges": nonmanifold_edges(retopo),
            "non_quads": sum(1 for f in bm.faces if len(f.verts) != 4),
        }

    for name, function in (
        ("manifold", manifold),
        ("lens", lens),
        ("detached", detached),
        ("flow", flow),
        ("solve_tris", solve_tris),
        ("optimize_analyze", optimize),
        ("auto_solve", auto_solve),
        ("surface_paint", paint),
    ):
        run(name, function)
    print("STRESS_RESULT " + json.dumps(RESULT))


try:
    main()
except Exception:  # noqa: BLE001
    RESULT["fatal"] = traceback.format_exc()[-1500:]
    print("STRESS_RESULT " + json.dumps(RESULT))
