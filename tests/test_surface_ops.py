"""Blender-only smoke coverage for the surface and bake workflow.

Normal CPython discovery skips this file because ``bpy`` is unavailable.  Run
the real checks with Blender's Python:

    blender --background --factory-startup --python tests/test_surface_ops.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

try:
    import bpy
except ModuleNotFoundError:  # pragma: no cover - exercised by CPython discovery
    bpy = None


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if bpy is not None:
    from topology_transitions import surface_ops


@unittest.skipIf(bpy is None, "requires Blender's bpy module")
class SurfaceWorkflowSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        surface_ops.register()

    @classmethod
    def tearDownClass(cls) -> None:
        surface_ops.unregister()

    def setUp(self) -> None:
        if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)

        self.low = self._mesh_object("Low", 0.0)
        self.high = self._mesh_object("High", 0.08)
        self.low.data.uv_layers.new(name="UVMap")
        material = bpy.data.materials.new("BakeMaterial")
        material.use_nodes = True
        image_node = material.node_tree.nodes.new("ShaderNodeTexImage")
        image_node.image = bpy.data.images.new("BakeTarget", width=16, height=16)
        self.image_name = image_node.image.name
        material.node_tree.nodes.active = image_node
        self.low.data.materials.append(material)

        bpy.ops.object.select_all(action="DESELECT")
        self.low.select_set(True)
        self.high.select_set(True)
        bpy.context.view_layer.objects.active = self.low

    @staticmethod
    def _mesh_object(name: str, z: float):
        mesh = bpy.data.meshes.new(f"{name}Mesh")
        mesh.from_pydata(
            ((0.0, 0.0, z), (1.0, 0.0, z), (1.0, 1.0, z), (0.0, 1.0, z)),
            (),
            ((0, 1, 2, 3),),
        )
        mesh.update()
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.collection.objects.link(obj)
        return obj

    def test_surface_and_bake_pipeline(self) -> None:
        shrinkwrap = surface_ops.ensure_shrinkwrap_modifier(
            self.low,
            self.high,
            wrap_method="TARGET_PROJECT",
            offset=0.001,
            project_limit=0.25,
        )
        self.assertEqual(shrinkwrap["modifier"].target, self.high)
        self.assertTrue(shrinkwrap["modifier"].show_in_editmode)
        self.assertTrue(shrinkwrap["modifier"].show_on_cage)
        directional = surface_ops.ensure_shrinkwrap_modifier(
            self.low,
            self.high,
            wrap_method="PROJECT",
            project_limit=0.25,
        )
        self.assertEqual(directional["resolved_method"], "PROJECT")
        self.assertTrue(directional["modifier"].use_project_z)
        self.assertFalse(directional["modifier"].use_project_x)
        self.assertFalse(directional["modifier"].use_project_y)

        cage_result = surface_ops.toggle_bake_cage(
            bpy.context, self.low, distance=0.05
        )
        cage = cage_result["cage"]
        inspection = surface_ops.inspect_bake_cage(self.low, cage)
        self.assertTrue(inspection.topology_matches)
        self.assertEqual(
            surface_ops.mesh_topology_signature(self.low.data),
            surface_ops.mesh_topology_signature(cage.data),
        )
        self.assertTrue(cage.show_in_front)
        self.assertEqual(cage.display_type, "WIRE")

        readiness = surface_ops.inspect_bake_readiness(
            bpy.context, use_cage=True, cage=cage
        )
        self.assertTrue(readiness.ready, readiness.summary)
        self.assertEqual(readiness.high_objects, (self.high.name,))
        self.assertEqual(readiness.image_names, (self.image_name,))

        configured = surface_ops.configure_selected_to_active_bake(
            bpy.context,
            bake_type="NORMAL",
            margin=8,
            use_cage=True,
            cage=cage,
        )
        bake = bpy.context.scene.render.bake
        self.assertEqual(bpy.context.scene.render.engine, "CYCLES")
        self.assertTrue(bake.use_selected_to_active)
        self.assertTrue(bake.use_cage)
        self.assertEqual(bake.max_ray_distance, 0.0)
        self.assertEqual(configured["bake_started"], False)
        self.assertIn(configured["resolved_type"], {"NORMAL", "NORMALS"})

        ray_configured = surface_ops.configure_selected_to_active_bake(
            bpy.context,
            bake_type="NORMAL",
            margin=4,
            max_ray_distance=0.125,
            use_cage=False,
        )
        self.assertFalse(bake.use_cage)
        self.assertAlmostEqual(bake.max_ray_distance, 0.125)
        self.assertEqual(ray_configured["mode"], "max ray distance")

    def test_stale_cage_is_detected_and_rebuilt(self) -> None:
        cage = surface_ops.toggle_bake_cage(
            bpy.context, self.low, distance=0.03
        )["cage"]
        self.low.data.vertices.add(1)
        self.low.data.update()
        stale = surface_ops.inspect_bake_cage(self.low, cage)
        self.assertEqual(stale.state, "STALE")
        self.assertFalse(stale.topology_matches)

        rebuilt = surface_ops.toggle_bake_cage(
            bpy.context, self.low, distance=0.03
        )
        self.assertEqual(rebuilt["state"], "rebuilt")
        self.assertTrue(
            surface_ops.inspect_bake_cage(self.low, rebuilt["cage"]).topology_matches
        )

        self.low.data.vertices[0].co.x += 0.2
        self.low.data.update()
        moved = surface_ops.inspect_bake_cage(self.low, rebuilt["cage"])
        self.assertEqual(moved.state, "STALE_GEOMETRY")
        self.assertFalse(moved.ready)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(SurfaceWorkflowSmoke)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
    print("QT_SURFACE_WORKFLOW_PASS tests=2")
