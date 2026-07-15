# Topology Transitions

Topology Transitions is a Blender add-on for rebuilding a selected rectangular quad patch with a guided edge-loop reduction or expansion, then inspecting how edge flows travel through the result. It automates deterministic connectivity while leaving the artistic decision—where the poles belong—under your control.

It supports:

- 5 ↔ 3
- 3 ↔ 1
- 4 ↔ 2
- 1 ↔ 2
- left, center, and right pole placement where the pattern permits it
- mirror and reverse-flow controls
- pinned outside boundaries
- surface conformity to the original patch or another mesh
- pinned-boundary relaxation
- non-destructive Catmull-Clark preview
- validation before mutation and rollback on apply-time failure
- a wheel-driven edge-flow inspector with side-to-side ordering, full quad-strip highlighting, automatic view focus, metrics, and endpoints
- a ready-made colored 5 → 3 example plane for learning and testing the workflow

The operator never silently inserts triangles or n-gons.

## What it does

Select a rectangular all-quad patch whose width matches the larger loop count. The panel makes the selection contract and transition settings visible before any mesh data changes.

![A five-face-wide quad patch selected beside the Topology Transitions panel](docs/images/01-select-patch.png)

Applying the 5 to 3 pattern replaces only the selected interior, keeps its outside boundary pinned, and creates two guided N-poles without triangles or n-gons.

![The completed five-to-three all-quad transition with its two N-poles selected](docs/images/02-five-to-three-result.png)

## Install

1. Download the release ZIP from the GitHub Releases page.
2. In Blender 4.2 or newer, open **Edit → Preferences → Get Extensions**.
3. Use the menu in the top-right corner and choose **Install from Disk**.
4. Select `topology-transitions-<version>.zip` and enable **Topology Transitions**.

The panel appears in **3D View → Sidebar → Quad Transition**.

## Example plane

In Object Mode, choose **Add 5 to 3 Example Plane** at the top of the panel. The add-on creates a portrait all-quad mesh at the 3D Cursor with a transition embedded between colored reference bands. Enter Edit Mode and start Edge Flow Scroll to explore it immediately.

![The generated example plane with a complete quad strip highlighted](docs/images/05-example-plane-strip.png)

## Workflow

1. Enter Edit Mode and select one connected rectangular grid of quad faces.
2. Make the strip as wide as the larger loop count. A 5 → 3 transition therefore needs a five-face-wide selection; its height determines how much room the transition has.
3. If direction matters, make an edge on the incoming boundary active. Use **Reverse Flow** to swap to the opposite boundary.
4. Choose the transition and pole controls in the **Quad Transition** panel.
5. Run **Validate**. This checks connectivity, parity, boundary shape, and the generated quad graph without changing the mesh.
6. Run **Apply Transition**. The outside boundary remains pinned and the new interior is relaxed and optionally projected.
7. Toggle the Catmull-Clark preview to inspect the subdivided flow.

Blender's **Adjust Last Operation** panel can be used immediately after applying a transition. Normal Blender Undo is also supported.

## Edge Flow Scroll

In Edit Mode, open **Edge Flow Scroll** and choose **Start Wheel Inspector**. The modal inspector discovers flows across the active mesh and lets you browse them without changing the current selection until you confirm.

![The edge-flow inspector framing and filling a complete quad strip on a torus](docs/images/03-edge-flow-scroll.png)

- **Translucent orange faces** are the complete quad strip adjoining the current flow; the stronger orange line keeps its center edge chain readable.
- **Cyan** shows only parallel flows one quad away, not perpendicular flows that happen to touch the same face.
- **Magenta** marks open endpoints, including boundaries and extraordinary vertices.
- **Mouse wheel / arrow keys** browse and automatically frame each strip; **Home / End** jump to the first or last flow.
- **Enter** selects the full quad strip and exits; **S** selects it and keeps browsing.
- **F** toggles automatic view focus; **N** toggles parallel neighbors; **Esc / right-click** restores the original selection.

**Quad Topology** follows opposite edges through regular valence-four quad vertices and stops at boundaries or poles. **Geometric** can continue through extraordinary vertices by choosing the straightest available continuation. Use **All Visible** to inspect the full mesh or **Selected Edges** to isolate a region. The default **Side to Side** order walks adjacent parallel strips across a surface before changing orientation; length, smoothness, and stable-index orders remain available.

![An open edge flow terminating at an N-pole created by a five-to-three transition](docs/images/04-flow-termination.png)

The HUD and sidebar report the flow number, edge and quad counts, world-space length, alignment, closed/open state, endpoint classifications, and parallel-neighbor count. See [docs/EDGE_FLOW_SCROLL.md](docs/EDGE_FLOW_SCROLL.md) for the discovery rules and control reference.

## Selection contract

The selected patch must be:

- one connected disk with no holes;
- made only of quads;
- a complete rectangular face grid with exactly four corners;
- as wide as `max(incoming loops, outgoing loops)`;
- free of branching and non-manifold selected edges.

The patch may sit inside a larger manifold mesh. Outside faces are preserved, and every selected boundary edge retains its original face count after replacement.

For 1 ↔ 2, quad parity requires one compensating edge on a side boundary. **Pole Side** and **Mirror** choose which side carries that shoulder.

## Controls

| Control | Effect |
| --- | --- |
| Transition | Incoming and outgoing loop counts. |
| Patch Axis | Uses the active boundary edge automatically or the alternate valid axis on square selections. |
| Reverse Flow | Swaps the incoming and outgoing sides of the selected rectangle. |
| Pole Side | Positions the local reduction cell left, center, or right where the width allows. |
| Mirror | Mirrors the pole slot or the asymmetric 1 ↔ 2 shoulder. |
| Pole Spacing | Adjusts the initial spacing of the valence-three poles before relaxation. |
| Relax Strength / Iterations | Smooths new interior vertices while boundary vertices remain pinned. |
| Conform to Surface | Projects new vertices onto the original selected surface. |
| Projection Target | Uses another mesh instead of the original patch for nearest-surface projection. |
| Preview Levels | Sets the viewport level of the add-on's Catmull-Clark modifier. |

### Edge-flow controls

| Control | Effect |
| --- | --- |
| Flow Mode | Uses strict quad-topology continuation or geometric straightest continuation. |
| Scope | Discovers flows across all visible edges or only selected edges. |
| Order | Traverses side-to-side by default, or sorts by length, smoothness, or stable mesh index. |
| Minimum Edges | Hides short flow fragments. |
| Pair Threshold | Prevents two edges from being paired when their continuation is below the chosen straightness. |
| Focus View | Centers and frames the active strip whenever the browser advances. |
| Show Neighbors | Draws parallel flows one quad away in cyan. |
| Previous / Refresh / Next | Steps through flows without entering the modal wheel inspector. |

## Safety and guarantees

For every accepted operation, the add-on checks:

- all generated faces are quads;
- the template is one connected disk with Euler characteristic 1;
- generated edges have at most two linked faces;
- expected N-poles have valence three;
- the outside boundary coordinates and face counts are unchanged;
- no generated quad has zero area.

Meshes with shape keys are rejected rather than modified because interpolating new vertices across every key is not unambiguous.

## Current limitations

- This release rebuilds selected rectangular face patches. It does not yet bridge arbitrary open chains or choose pole locations across an entire model.
- New loops receive default per-loop custom-data values. Material index and smooth shading are preserved, but UVs, custom normals, color attributes, creases, and similar loop/edge data may need to be rebuilt on the replaced patch.
- Nearest-surface projection can choose the wrong sheet on tightly overlapping geometry. Use an explicit projection target or disable conformity in that situation.
- Compound reductions such as 9 → 5 are not yet chained automatically.
- Multi-object Edit Mode is rejected; edit one mesh data-block at a time.
- Quad Topology intentionally stops at poles; Geometric mode is a directional heuristic and can make a different choice on ambiguous, symmetric junctions.

## Development

Pure topology tests:

```powershell
python -m unittest discover -s tests -v
python -m compileall topology_transitions tests scripts
```

Headless Blender smoke:

```powershell
blender.exe --background --factory-startup --python tests\blender_smoke.py
```

Recreate the documentation screenshots in a normal Blender window:

```powershell
blender.exe --factory-startup --python scripts\capture_docs.py -- --shot flow
```

Valid shot names are `before`, `after`, `flow`, `pole`, and `example`. The originals are saved to the Windows **Pictures\Screenshots** folder before curated copies are added to `docs/images`.

Build the installable ZIP:

```powershell
python scripts\build_release.py
```

The smoke requires the explicit marker `QT_BLENDER_SMOKE_PASS`; a zero process exit by itself is not treated as proof.

## Architecture

- `topology_transitions/core.py` builds and validates Blender-independent quad graphs.
- `topology_transitions/mesh_ops.py` validates and partitions Edit Mode selections.
- `topology_transitions/operators.py` fits, projects, applies, verifies, and rolls back BMesh changes.
- `topology_transitions/edge_flows.py` discovers and measures mesh-independent flow chains.
- `topology_transitions/flow_ops.py` adapts Edit Mode BMesh data and owns the modal viewport overlay.
- `topology_transitions/ui.py` exposes the workflow in the 3D View sidebar.
- `tests/test_core.py` proves graph invariants with standard Python.
- `tests/test_edge_flows.py` proves flow discovery, filtering, poles, and neighborhood relationships.
- `tests/blender_smoke.py` proves behavior inside Blender.
- `scripts/capture_docs.py` reproducibly creates the real Blender documentation captures.

The topology and parity derivation is documented in [docs/TOPOLOGY.md](docs/TOPOLOGY.md).

## License

MIT. See [LICENSE](LICENSE).
