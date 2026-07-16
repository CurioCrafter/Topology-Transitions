# Topology Transitions

Topology Transitions is a Blender add-on for building guided all-quad loop
reductions/expansions, drawing several welded quad lanes over a source surface,
repairing selected triangles and n-gons, reading broad quad-flow regions, and
preparing shrinkwrapped retopology for selected-to-active baking.

Supported transition directions are 5 -> 3, 3 -> 5, 3 -> 1, 1 -> 3,
4 -> 2, 2 -> 4, 1 -> 2, and 2 -> 1. Pole placement, mirroring, surface
conformity, relaxation, and Catmull-Clark preview remain user-controlled.

![True unequal-density transition atlas](docs/images/05-example-plane-strip.png)

## Install

1. Download `topology-transitions-<version>.zip` from GitHub Releases.
2. In Blender 4.2 or newer, open **Edit -> Preferences -> Get Extensions**.
3. Open the top-right menu, choose **Install from Disk**, and select the ZIP.
4. Open **3D View -> Sidebar -> Quad Transition**.

## Draw a connected multi-strip

This is a boundary-driven alternative to drawing several independent quad
strips:

1. Keep the high-poly source as a separate visible mesh and choose it as
   **Surface Target**.
2. On the low-poly mesh in Edit Mode, select one open boundary edge chain. Each
   selected edge is one lane.
3. Choose **Connected Multi-Strip** for a uniform sheet or **Transition Ribbon**
   for one of the eight real density patterns.
4. Click **Draw Connected Multi-Strip**, drag LMB over the target, adjust rows
   with the wheel and width with Shift+wheel, then press Enter.

The selected chain is reused as the exact first row, so the new faces weld to
the bottom quads instead of overlapping them. All lanes share their cross-row
vertices and commit as one connected all-quad mesh. The far boundary is selected
afterward for immediate continuation. Cyan filled preview cells show the entire
sheet; magenta cells mark transition poles.

The draw is transactional and target-specific. It cancels on a projection miss,
folded/zero-area quad, invalid pole, non-manifold edge, hidden/interior/branched
anchor chain, shape keys, or multi-object Edit Mode.

![Five welded lanes continuing into a real five-to-three transition](docs/images/06-connected-multi-strip.png)

## Surface conformity and bake preview

**Set Up Live Shrinkwrap** creates or updates one named modifier against the
same target. Nearest Surface, Target Normal Project, and bidirectional Local Z
Project presets are exposed with offset and projection-limit controls.

For selected-to-active baking:

- **Toggle Bake Cage** shows an orange exact-topology inflated cage and detects
  when later edits make it stale.
- **Toggle Ray Preview** draws sampled high-source hits in green and misses in
  red, then reports coverage plus median and 95th-percentile hit distance.
- **Inspect Readiness** checks source selection, UVs, active bake images,
  transforms, winding, non-manifold geometry, and cage topology parity.
- **Configure Bake** prepares Cycles tangent-normal or displacement settings.
  It never starts a bake or overwrites an image.

![Orange exact-topology cage with green source hits and red misses](docs/images/07-bake-ray-preview.png)

The full workflow and implementation boundaries are documented in
[Connected Multi-Strip, Surface Conformity, and Baking](docs/CONNECTED_MULTI_STRIP_AND_BAKING.md).

## Apply a transition

In Edit Mode, select either:

- a connected four-sided face patch; its interior may contain quads, triangles,
  or n-gons; or
- the closed outside edge loop of that patch.

Choose the transition and run **Validate**, then **Apply Transition**. The
outside boundary is pinned while the interior is replaced with the selected
all-quad template. A 5 -> 3 transition needs five incoming face columns.

![Selected five-column patch](docs/images/01-select-patch.png)

![Completed five-to-three transition](docs/images/02-five-to-three-result.png)

The selected region still needs one disk-like boundary with four detectable
corners and compatible side parity. A loose, open edge chain is not enough to
define a replacement area.

## Solve selected tris and n-gons

The **Topology Repair** box keeps the two workflows separate:

- **Solve Selected Tris** first merges a compatible neighboring triangle. Only
  one triangle needs to be selected; its partner may be unselected.
- **Solve Selected N-gons** first uses a clean quad fan for even n-gons.
- Compatible triangle + odd-n-gon pairs are repaired as one even boundary.
- A completely isolated boundary face uses a center grid.
- For an embedded odd face, the solver carries edge splits through complete
  opposite-edge paths in the surrounding quad mesh, then rebuilds every touched
  face as quads.

That final method is intentionally topology-changing beyond the selected face:
an embedded odd-sided disk cannot become all-quad while every neighboring edge
remains unchanged. Propagating the cuts prevents the usual fake fix where the
triangle disappears but a pentagon appears one face away. The operation is
transactional, rejects hidden/non-manifold paths, and stops before touching more
than 10,000 faces.

Meshes with shape keys are rejected. New loops use default custom-data values,
so repaired regions with UVs, color attributes, custom normals, or creases may
need those data rebuilt.

## True density example atlas

In Object Mode, click **Add All Transition Examples**. Every labeled tile has:

1. three regular incoming rows with the stated input column count;
2. the actual pole-based transition; and
3. three regular outgoing rows with the different output column count.

The atlas is a joined, 186-face all-quad mesh with all eight transition
directions. It is meant to make density reduction/expansion visually obvious,
not to place a transition inside a same-count rectangular grid.

## Quad Flow Regions

The default **Quad Flow Regions** mode reads the retopology as broad patches:

1. interior extraordinary vertices are detected as poles;
2. separatrix edge paths continue topologically through regular valence-four
   vertices;
3. those paths, mesh boundaries, and non-quad boundaries become barriers; and
4. all visible quads are flood-filled into non-overlapping flow regions.

![Whole quad-flow region map around a five-to-three transition](docs/images/04-flow-termination.png)

The overlay colors the complete region map, brightens and outlines the active
region, and shows its pole-separated boundaries. Mouse wheel/arrow keys move
from one complete region to the next and automatically center the camera on it.
**Enter** selects the region and exits; **S** selects and stays; **F** toggles
focus; **N** toggles the full map; **Esc/right-click** restores the original
selection.

For granular inspection, switch to **Individual Face Bands**. That legacy mode
shows one-quad-wide loops/strips continued through opposite quad edges. It is
useful, but it is no longer presented as the whole retopology flow map.

![Individual one-quad-wide face band mode](docs/images/03-edge-flow-scroll.png)

## Mesh Integrity

In Edit Mode, **Check & Select Manifold Issues** identifies and selects the exact
elements responsible for:

- open boundary edges (one linked face);
- over-connected non-manifold edges (more than two linked faces);
- wire edges (no linked faces); and
- isolated vertices.

Issue edges are grouped into connected areas. **Previous Issue** and **Next
Issue** select one component at a time and frame it in the 3D View. A closed
manifold mesh reports zero issues. An intentional open retopology sheet is still
reported accurately as an open boundary; the tool diagnoses rather than assumes
that every boundary is a mistake.

## Main controls

| Control | Effect |
| --- | --- |
| Ribbon Layout | Draws a uniform connected sheet or a real loop-count transition. |
| Surface Target | Explicit high-poly mesh used for stroke hits and vertex projection. |
| Length Segments / Width Scale | Controls shared cross-rows and boundary-relative width. |
| Flip Width | Reverses which anchor endpoint is treated as the left side. |
| Transition | Incoming and outgoing loop counts. |
| Patch Axis | Uses the active boundary edge or the alternate patch axis. |
| Reverse Flow | Swaps incoming and outgoing patch sides. |
| Pole Side / Mirror | Places or mirrors the transition poles. |
| Pole Spacing | Adjusts initial extraordinary-vertex spacing. |
| Relax Strength / Iterations | Relaxes new interior vertices with a pinned boundary. |
| Conform to Surface | Projects new vertices to the original or explicit target mesh. |
| Flow View | Chooses whole regions or individual one-quad-wide bands. |
| Scope | Uses all visible faces or only selected faces. |
| Show Full Map | Colors all broad regions while one remains active. |
| Focus View | Frames the active region/band after each step. |
| Shrinkwrap Method / Offset | Maintains non-destructive source conformity while editing. |
| Bake Cage / Ray Preview | Visualizes the envelope and sampled source reach before baking. |

## Safety and limitations

- Apply Transition requires a compatible four-sided region. It does not decide
  artistic pole placement for an entire character.
- Every accepted transition validates an all-quad connected disk, manifold edge
  counts, expected pole valence, nonzero face area, preserved boundary
  coordinates, and preserved outside connectivity.
- Repair propagation can add full edge paths across a large quad mesh. The
  operator reports how many splits and surrounding quads it changed.
- Surface projection can choose the wrong sheet on tightly overlapping meshes;
  use an explicit projection target or disable conformity.
- The bake-ray overlay is a sampled diagnostic, not a replacement for the final
  Cycles bake. Green coverage does not prove UV or material correctness.
- New ribbon loops currently receive default UV/color/custom-data values and
  should be unwrapped before baking.
- Compound reductions such as 9 -> 5 are not automatically chained.
- Multi-object Edit Mode is rejected; edit one mesh data-block at a time.

## Development and verification

```powershell
python -m unittest discover -s tests -v
ruff check topology_transitions tests scripts
python -m compileall topology_transitions tests scripts
blender.exe --background --factory-startup --python tests\blender_smoke.py
python scripts\build_release.py
```

The Blender smoke is only accepted when it prints
`QT_BLENDER_SMOKE_PASS`. `tests/installed_smoke.py` verifies the installed ZIP
copy separately.

Key modules:

- `core.py`: pure transition templates and graph validation;
- `mesh_ops.py` / `operators.py`: selection analysis and transactional apply;
- `quad_repair.py` / `repair_ops.py`: local and propagated all-quad repair;
- `quad_flows.py` / `flow_ops.py`: pole-bounded regions, face bands, and overlay;
- `manifold.py` / `manifold_ops.py`: pure diagnostics and Blender selection;
- `ribbon.py` / `ribbon_ops.py`: connected multi-lane plans, surface fitting,
  modal preview, and transactional BMesh growth;
- `surface_ops.py`: live Shrinkwrap, exact-topology cages, and bake readiness;
- `bake_preview_ops.py`: sampled green-hit/red-miss bake-ray visualization;
- `examples.py`: true unequal-density atlas generation.

The topology derivation is in [docs/TOPOLOGY.md](docs/TOPOLOGY.md), and detailed
flow controls are in [docs/EDGE_FLOW_SCROLL.md](docs/EDGE_FLOW_SCROLL.md).

## License

MIT. See [LICENSE](LICENSE).
