"""Blender-only smoke checks for the visible bake-ray diagnostic."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

try:
    import bpy
except ModuleNotFoundError:  # pragma: no cover - normal CPython discovery
    bpy = None

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if bpy is not None:
    from topology_transitions import bake_preview_ops, surface_ops


@unittest.skipIf(bpy is None, "requires Blender's bpy module")
class BakeRayPreviewSmoke(unittest.TestCase):
    def setUp(self) -> None:
        if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)
        self.low = self._plane("PreviewLow", 0.0)
        self.high = self._plane("PreviewHigh", 0.08)
        bpy.ops.object.select_all(action="DESELECT")
        self.low.select_set(True)
        self.high.select_set(True)
        bpy.context.view_layer.objects.active = self.low

    @staticmethod
    def _plane(name: str, height: float):
        mesh = bpy.data.meshes.new(f"{name}Mesh")
        mesh.from_pydata(
            (
                (-1.0, -1.0, height),
                (1.0, -1.0, height),
                (1.0, 1.0, height),
                (-1.0, 1.0, height),
            ),
            (),
            ((0, 1, 2, 3),),
        )
        mesh.update()
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.collection.objects.link(obj)
        return obj

    def test_hit_miss_statistics_and_visibility_toggle(self) -> None:
        hit = bake_preview_ops.build_bake_ray_preview(
            bpy.context,
            self.low,
            max_ray_distance=0.2,
            use_cage=False,
            sample_limit=10,
        )
        self.assertEqual((hit["hits"], hit["misses"]), (1, 0))
        self.assertEqual(hit["coverage"], 1.0)
        self.assertAlmostEqual(hit["median_distance"], 0.08, places=5)
        self.assertTrue(all(obj.show_in_front for obj in hit["objects"]))

        hidden = bake_preview_ops.toggle_bake_ray_preview(
            bpy.context,
            self.low,
            max_ray_distance=0.2,
            use_cage=False,
            sample_limit=10,
        )
        self.assertEqual(hidden["state"], "hidden")
        shown = bake_preview_ops.toggle_bake_ray_preview(
            bpy.context,
            self.low,
            max_ray_distance=0.2,
            use_cage=False,
            sample_limit=10,
        )
        self.assertEqual(shown["state"], "shown")

        missed = bake_preview_ops.toggle_bake_ray_preview(
            bpy.context,
            self.low,
            max_ray_distance=0.02,
            use_cage=False,
            sample_limit=10,
            force_rebuild=True,
        )
        self.assertEqual((missed["hits"], missed["misses"]), (0, 1))

    def test_custom_cage_rays_travel_inward(self) -> None:
        cage = surface_ops.toggle_bake_cage(
            bpy.context,
            self.low,
            distance=0.12,
        )["cage"]
        result = bake_preview_ops.build_bake_ray_preview(
            bpy.context,
            self.low,
            max_ray_distance=0.12,
            use_cage=True,
            sample_limit=10,
        )
        self.assertEqual((result["hits"], result["misses"]), (1, 0))
        self.assertAlmostEqual(result["median_distance"], 0.04, places=5)
        self.assertTrue(surface_ops.inspect_bake_cage(self.low, cage).topology_matches)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(BakeRayPreviewSmoke)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
    print("QT_BAKE_RAY_PREVIEW_PASS tests=2")
