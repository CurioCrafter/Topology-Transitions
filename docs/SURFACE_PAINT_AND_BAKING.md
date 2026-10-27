# Surface Paint, Surface Conformity, and Baking

**Surface Paint Quads** lets you paint *where* topology should go on a
high-poly mesh. The painted area is filled with a regular quad grid that
follows the surface, and the direction of each stroke becomes the
direction of the edge loops.

## Paint a quad patch

1. Keep the high-resolution source visible as a separate mesh object and set it
   as **Surface Target**.
2. Select the low-poly retopology mesh (it may be empty) and enter Edit Mode.
3. Pick a **Cell Size** (quad edge length) or leave it at zero to derive it from
   your first brush dab.
4. Click **Start Surface Paint** and paint over the target with LMB.
   Ctrl+LMB erases, `[` and `]` change the brush, `-` and `=` change the cell,
   R / Shift+R rotate the edge flow by 15 degrees, Backspace removes the last
   stroke, Enter builds, Esc or right-click cancels.

The green preview is the exact grid that will be built. The overlay also counts
quads, vertices, and interior poles, and reports cells that stopped because the
surface bent too far or wrapped back onto the patch.

![Painted coverage filled with a surface-following quad grid](images/06-surface-paint.png)

## How the grid is built

The algorithm lives in `topology_transitions/paint_grid.py` and has no Blender
dependency, so it is unit-tested on analytic planes and spheres.

1. **Coverage.** Every paint dab is a sphere on the surface; erase dabs subtract
   from it in painting order.
2. **Lattice growth.** Starting at the first dab of each stroke (unless an
   earlier patch already reached it), a square lattice is grown
   breadth-first. Each new node steps one cell along its parent's tangent frame,
   is projected to the nearest surface point, and receives the frame by
   parallel transport (the old direction with its normal component removed).
   Nodes outside the coverage are never created.
3. **Guards.** A node is rejected when the surface normal flips (a fold or the
   far side of a thin part), when it would sit closer than half a cell or
   further than two cells from an existing lattice neighbour (curvature
   distortion), or when it lands on a different part of the lattice (a patch
   painted all the way around a cylinder). The patch stops there instead of
   producing slivers or overlaps.
4. **Faces.** Each lattice square whose four corners exist becomes one quad,
   wound so its normal agrees with the surface.
5. **Relax.** Interior vertices are moved halfway toward the average of their
   four neighbours and projected back onto the surface a few times, which
   evens out cells that curvature stretched.
6. **Weld.** With **Weld to Open Edges** on, border vertices merge onto open
   boundary vertices of the existing mesh within about a third of a cell. If
   welding would break the mesh, the patch is built unwelded and a warning is
   shown.

### Why this teaches good topology

On a regular grid every inner vertex has exactly four edges, so the painted
patch introduces no poles. Poles (3- or 5-edge vertices) are where edge flow
changes direction; you add them deliberately with the transition tools
(5 -> 3, 3 -> 1, and so on) rather than getting them by accident.

## Live surface conformity

Surface Paint projects every new vertex directly onto the evaluated surface
target. **Set Up Live Shrinkwrap** then adds or updates one named,
non-destructive modifier for continued editing; repeated clicks do not stack
duplicates. Blender's official
[Shrinkwrap documentation](https://docs.blender.org/manual/en/latest/modeling/modifiers/deform/shrinkwrap.html)
describes the three exposed presets:

| Preset | Practical use |
| --- | --- |
| Nearest Surface | Fast default when the retopology is already close to the source. |
| Target Normal Project | Smoother organic conformity, with higher evaluation cost. |
| Local Z Project | Bidirectional projection along the low object's local Z axis, useful where nearest-point snapping chooses the wrong sheet. |

The modifier is visible in Edit Mode and on the editable cage. A finite Project
Limit can prevent snapping across distant or overlapping shells. Surface Offset
keeps the low mesh slightly above the source. Non-uniform object scale makes a
local offset appear uneven in world space, so the bake readiness check reports
unapplied transforms.

## Bake cage and ray preview

**Toggle Bake Cage** creates an orange, in-front wire copy of the low mesh and
inflates only vertex positions along low-poly normals. It preserves vertex,
edge, polygon, loop, and face ordering. A stored topology/geometry/transform
signature marks the cage stale after later low-poly edits; rebuilding replaces
its mesh without accumulating cage objects.

**Toggle Ray Preview** samples low-poly face centers and draws an approximate
selected-to-active ray diagnostic:

- green line: a selected high-poly source was reached;
- red line: no selected source was reached inside the configured distance or
  custom-cage envelope;
- sidebar status: hit coverage, median distance, and 95th-percentile distance.

With custom-cage mode off, the diagnostic tests both low-poly normal directions
up to Max Ray Distance. With it on, rays travel inward from cage faces toward
their topology-matched low faces. The lines are a preflight visualization, not
a bit-for-bit renderer replacement; the final Cycles bake remains authoritative.

![Exact-topology cage with green hit rays and red misses](images/07-bake-ray-preview.png)

## Selected-to-active readiness

The official [Cycles baking documentation](https://docs.blender.org/manual/en/latest/render/cycles/baking.html)
requires the low-poly mesh to be active, high-poly sources to be selected, and
an image target to be active. **Inspect Readiness** checks:

- an active mesh low-poly object and at least one separate selected high mesh;
- a low-poly UV map;
- an active Image Texture node with an assigned image in each used material;
- empty/zero-area geometry, inconsistent winding, and over-connected edges;
- unapplied, negative, or non-uniform transforms;
- render-disabled high sources; and
- exact custom-cage topology parity when cage mode is enabled.

**Configure Bake** switches the scene to Cycles and prepares selected-to-active
Normal or Displacement settings. Tangent space is used for normal maps. Custom
cage and Max Ray Distance are mutually exclusive. The button deliberately does
not call Blender's bake operator, so it cannot overwrite an image merely by
inspecting or preparing the workflow.

## Next improvements

The next high-value steps are deliberately separate from what is implemented:

1. Paint several patches in one session and stitch their facing borders with
   the transition tools when their cell counts differ.
2. Let cell size follow source curvature so tight areas get smaller quads.
3. Clip painting at a symmetry plane and mirror the result.
4. Expand the ray diagnostic to face-area sampling, backface/dual-hit classes,
   cage self-intersection warnings, and a material preview of the baked image.
5. Transfer or interpolate UV, color, crease, and other custom data into newly
   painted topology instead of initializing those layers with defaults.
