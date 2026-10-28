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
4. Click **Surface Paint** and paint over the target with LMB.
   Ctrl+LMB erases, `[` and `]` change the brush, `-` and `=` change the cell,
   R / Shift+R rotate the edge flow by 15 degrees, X toggles Mirror X, J toggles
   Join Patches, Backspace removes the last stroke, Enter builds, Esc or
   right-click cancels.

The green preview is the exact grid that will be built. The coach card counts
quads, vertices, poles and joins, and reports cells that stopped because the
surface folded back or two patches met.

![Surface Paint across the brow ridge, with the live coach card](images/09-surface-paint-live.png)

![Painted coverage filled with a surface-following quad grid](images/06-surface-paint.png)

## How the grid is built

The algorithm lives in `topology_transitions/paint_grid.py` and has no Blender
dependency, so it is unit-tested on analytic planes, spheres and a sharp ridge.

1. **Coverage.** Every dab covers a disc-shaped column along the surface normal
   under the brush (1.5 brush radii deep), so the floor of a socket or crease
   the brush passes over is painted too. Erase dabs subtract in painting order.
2. **Surface walking.** Every step of the grid walks one cell over the surface
   in four sub-steps. After each sub-step the point is projected back to the
   surface and the heading is re-aimed along the surface actually travelled,
   so the walk turns with a crease such as a brow ridge instead of cutting
   through it. The tangent frame is carried along by parallel transport.
3. **Spine and ribs.** Each stroke starts its own patch (unless an earlier
   patch already reached it). A spine walks from the stroke's first dab along
   the stroke direction in both directions, and every spine vertex sends ribs
   out at right angles. Each vertex therefore has exactly one walk defining
   it, so curvature cannot make two paths disagree about where it belongs.
   Plain outward growth then fills any painted area the ribs did not reach.
4. **Guards.** A vertex is rejected when the surface turns back on itself (the
   far side of a thin part), when it would sit closer than 0.35 or further than
   2 cells from a grid neighbour, or when it lands on another part of the grid.
   The patch stops there instead of producing slivers or overlaps.
5. **Fill the gaps.** A grid cannot walk across a gap in the surface, such as
   an eyeball sitting in its socket or a separate shell. Spines, ribs and
   spurs that will not survive cleanup are removed first, then the painted
   area is sampled across every brush disc and through its depth, and each
   sample that is still empty starts a new patch, oriented like the nearest
   stroke. Pieces smaller than six quads are dropped at the end.
6. **Cleanup.** Every grid square with four corners becomes a quad. One-cell
   spurs with three open sides are removed, and where two cells touch only at
   a corner the smaller fan is dropped, so no vertex is non-manifold.
7. **Joins.** With **Join Patches** on, facing borders of different patches
   (within 4.5 cells) are connected. Equal edge counts get a plain strip of
   quads; different counts use the add-on's own transition templates: as many
   n+2 -> n sections as needed, plus one 2 -> 1 cell for an odd difference.
8. **Relax.** Inner vertices move halfway toward their neighbours and border
   vertices a third of the way toward their two border neighbours, both
   projected back onto the surface. Border smoothing rounds off the staircase a
   square grid leaves along a painted outline. A move that would fold a quad is
   skipped, and a join that still folds after relaxing is removed.
9. **Mirror.** With **Mirror X** the grid also grows into the reflected paint,
   is clipped at the retopo object's local YZ plane, the seam vertices are
   snapped onto the plane, and the reflected half shares them.
10. **Weld.** With **Weld to Open Edges** on, border vertices merge onto open
   boundary vertices of the existing mesh within about a third of a cell. If
   welding would break the mesh, the patch is built unwelded and a warning is
   shown.

### Why this teaches good topology

On a regular grid every inner vertex has exactly four edges, so a single
painted patch introduces no poles; only joins between patches add them, and
the coach card counts them. Poles (3- or 5-edge vertices) are where edge flow
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

1. Let cell size follow source curvature so tight areas get smaller quads.
2. Join strongly diagonal patches, which are currently left open when the
   join would fold.
3. Expand the ray diagnostic to face-area sampling, backface/dual-hit classes,
   cage self-intersection warnings, and a material preview of the baked image.
4. Transfer or interpolate UV, color, crease, and other custom data into newly
   painted topology instead of initializing those layers with defaults.
