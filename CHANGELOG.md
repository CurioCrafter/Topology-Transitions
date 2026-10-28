# Changelog

## 0.10.2 - 2026-10-27

- Relicensed from MIT to **GPL-3.0-or-later**, the licence Blender uses and the
  one extensions.blender.org requires for add-ons. No code changes. Releases up
  to 0.10.1 stay available under MIT.

## 0.10.1 - 2026-10-27

- The Topology Lens hover card now flips to the left of the cursor before it
  would slide under the sidebar, instead of only at the window edge.
- README: animated GIFs of Surface Paint, the Topology Lens and Mirror X, plus
  scripts to regenerate them and the social preview image.

## 0.10.0 - 2026-10-27

- Added **Surface Paint Quads**: paint coverage on a high-poly target and fill
  it with a surface-following all-quad grid.
  - Brush dabs cover a disc-shaped column along the surface normal, so the
    floor of a socket or crease under the brush is painted too.
  - Each stroke grows a spine along its direction and ribs at right angles,
    walking over the surface in sub-steps so the grid bends over creases such
    as a brow ridge instead of stopping at them; outward growth fills the rest.
  - Border cleanup removes one-cell spurs and corner-only contacts, and border
    vertices are smoothed along the outline instead of staying stair-stepped.
  - **Join Patches** connects facing borders of separate strokes with quads,
    using the add-on's own n -> n-2 and 2 -> 1 transition templates where loop
    counts differ; joins that would fold are dropped.
  - **Mirror X** paints one half and reflects it across the retopo object's
    local X with a shared seam.
  - Smoothing never folds a quad, faces are wound consistently, and border
    vertices can weld onto open edges with an unwelded fallback.
- Added the **Topology Lens**: a live overlay that colours 3-, 5- and 6+ poles,
  doublets, border poles, triangles and n-gons while you keep editing, with a
  hover card that names each vertex, highlights its edges and explains it.
- Added coloured in-viewport coach cards with stat chips and key caps for the
  modal tools.
- Reorganised the sidebar into a **Start Here** card (Find Holes, Topology
  Lens, Surface Paint, and a red/green setup checklist) followed by collapsible
  colour-coded sections: Inspect, Surface Paint, Transitions, Repair &
  Optimize, Flow Browser, Merge & Cleanup, and Surface & Bake.
- Removed the experimental boundary-chain strip drawing (Connected Multi-Strip
  and Transition Ribbon). Loop-count transitions remain available through
  Apply Transition.
- **Auto Solve All Non-Manifold Areas** is much faster on dense scans with
  many holes (560 s -> 100 s on a 24,000-triangle AI scan, 47 s -> 6 s on a
  6,000-triangle statue) with identical results: open and branched areas are
  skipped from the first scan, and only the new cap updates its normals.
- Tested on Blender 4.2.19 LTS, 4.5 LTS, 5.0.1, 5.1.2 and 5.2.3 LTS; the Topology
  Lens falls back to Blender 4.2's point shader.
- Added `tests/real_mesh_stress.py`, a sweep that runs every main tool on a
  real mesh file and checks the result stays manifold.
- Added pure tests for creases, cleanup, joins, mirroring and erase dabs, plus
  Blender checks for welding, mirroring and Lens pole counts, and a
  reproducible README screenshot script.

## 0.9.3 - 2026-08-23

- Promoted **Mesh Integrity** to the first sidebar section and made the
  non-topology-changing **Find Holes / Check Manifold** diagnostic its primary
  action.
- Expanded the checker to catch inconsistent two-face edge winding and
  disconnected incident face fans, including closed shells that touch at only
  one vertex, instead of reporting those cases as clean.
- Moved automatic non-manifold repair below the diagnostic and labeled it as a
  topology-changing option so inspection remains the default first step.
- Reworked the README to lead with hole-rim, open-boundary, wire-edge,
  over-connected-edge, and isolated-vertex inspection, while documenting that
  intentional open retopology sheets are reported rather than assumed broken.

## 0.9.2 - 2026-08-19

- Fixed Automatic/Custom optimization stopping with "Candidate batch changed
  the mesh topology class" when one candidate or an interaction made an
  otherwise useful batch unsafe.
- A failed full batch is now divided into progressively smaller candidate
  groups and tested transactionally, up to 64 attempts. The largest proven-safe
  subset is committed while topology-conflicting flows are deferred for a later
  cycle instead of discarding every valid flow.
- Topology rejections now identify the protected signature that would have
  changed: Euler characteristic, boundary-component count, over-connected
  edges, wire edges, or isolated vertices.
- Added factory coverage proving a forced boundary-component batch conflict is
  isolated, still reduces faces, and leaves only non-degenerate quads.

## 0.9.1 - 2026-08-19

- Replaced the overly strict default quad-flow detection with **Automatic**
  sensitivity. It tries Conservative, Balanced, then Aggressive safety presets,
  allowing common straight open remesh sheets to optimize without manually
  discovering that boundary protection was blocking every flow.
- Added direct Conservative, Balanced, Aggressive, and Custom modes. Explicit
  UV seams, Sharp marks, weighted creases, and material borders remain
  independently protected while geometric angle/error, guard-ring, flow-length,
  and open-boundary thresholds respond to the chosen sensitivity.
- Replaced the generic "No eligible repetitive quad flows" result with ranked
  rejection diagnostics for boundary protection, short or pole-interrupted
  flows, selected scope, features, curvature, surface error, and unsafe
  topology.
- Added an in-panel **Explain Every Control** guide, effective-mode reporting,
  full terminology documentation, and Blender coverage proving that Automatic
  escalates from a boundary-blocked Conservative scan to a successful Balanced
  64-to-16 quad reduction.

## 0.9.0 - 2026-08-19

- Added **Quad Flow Optimization** for removing repetitive remesher density
  without producing triangles or n-gons. It discovers complete topological edge
  flows and dissolves an alternating, non-adjacent set in both quad directions,
  so a regular 2x2 block can become one larger quad.
- Added adjustable cycles, surface-angle and crease-angle thresholds, local
  surface-error percentage, branch guard rings, minimum flow length, All
  Visible/Selected Faces scope, and protection for open boundaries, UV seams,
  marked-sharp edges, crease weights, and material boundaries.
- Straight feature crossings retain seam, sharp, crease, and linear UV data
  while feature junctions, poles, corners, strongly curved flows, and failed
  error-budget trials remain dense. Every accepted pass stays all-quad,
  preserves Euler/boundary/non-manifold signatures, and passes a bounded BVH
  surface-distance check before commit.
- Added **Analyze / Select Flows** plus persistent result counters. Factory
  Blender coverage proves two-cycle 64-to-4 quad reduction, flat-versus-curved
  regional behavior, feature/UV retention, and a closed 384-to-150 quad
  reduction with branch corners and manifold topology intact. Regular
  path/cycle conflict groups use an exact alternating solver; a 4,096-quad
  grid reduces to 1,024 quads in one cycle instead of retaining an index-order
  strip.

## 0.8.0 - 2026-08-19

- Added **Auto Solve All Non-Manifold Areas** in Object and Edit Mode. One pass
  cleans over-connected exact duplicate faces, wire edges, and isolated
  vertices, then closes every simple open boundary it can solve safely.
- Even boundary loops prefer the clean minimum-face quad fan. Odd or unsuitable
  loops use a conforming center grid and carry their midpoint cuts through
  opposite edges of surrounding quad flows instead of creating T-junctions or
  moving an n-gon into a neighbor.
- Triangular holes become exactly three target quads around a new center. The
  operation is copy-staged, limited to 10,000 propagated faces per boundary,
  and leaves ambiguous branched boundaries or over-connected sheets selected
  with an unresolved count instead of deleting an arbitrary surface.
- Added pure boundary-loop planning tests plus factory and installed-copy
  Blender coverage for minimum quad closure, triangle-to-three-quad closure,
  loose cleanup, and conservative unresolved selection.

## 0.7.5 - 2026-08-05

- Added default-on **Protect UVs** to Directional Merge. It tracks UVs by
  stable face-corner data across Blender's destructive weld instead of using
  invalidated loop references or shifted loop indices.
- Fixed connected edge collapses choosing a UV corner from face winding rather
  than the requested merge anchor. Collapsing faces now keep their own anchor
  corner, while source-only and anchor-only attached faces retain their
  distinct per-face UVs and seams.
- Protection covers every UV map plus UV pins and the UV-selection state
  exposed by the running Blender version. The temporary tracking layer is
  removed before the edit mesh is updated.

## 0.7.4 - 2026-08-05

- Fixed Directional Merge appearing to ignore valid selections. It now defaults
  to **All Selected**, allows connected vertices by default, and treats exactly
  two selected vertices as an explicit pair that can ignore Merge Distance.
- Replaced the generic no-eligible-vertices warning with specific guidance for
  boundary-scope filtering, edge protection, or the measured nearest-pair
  distance that the threshold needs to exceed.

## 0.7.3 - 2026-08-05

- Added **View: Keep Toward You** and **View: Keep Away From You** Directional
  Merge strategies. They choose the surviving vertex using depth in the active
  3D viewport, including perspective, orthographic, and camera-aligned views.
- Added quick **Toward View** and **Away From View** sidebar buttons and matching
  entries in Blender's Edit Mode Merge menu.

## 0.7.2 - 2026-08-05

- Added **Directional Merge** for selected close vertices with an explicit
  survivor direction instead of an arbitrary merge destination: radial outer,
  radial inner, face-normal outside, or face-normal inside.
- Added world-space merge distance, boundary/loose-only and all-selected scopes,
  plus default-on protection against collapsing vertices that already share an
  edge.
- Added Directional Merge to the add-on sidebar and Blender's Edit Mode Merge
  menu, with quick **Keep Outer** and **Keep Inner** buttons.

## 0.7.1 - 2026-08-05

- Added **Detached Face X-Ray** for finding face islands inside one mesh without
  separating the object. Faces are considered properly connected only when they
  share full mesh edges with the largest connected region.
- Added an adjustable translucent red overlay drawn in front of the mesh, with
  Previous/Next controls that select and frame one suspect region at a time.
- Refreshing the inspection recalculates the remaining islands after repairs;
  the overlay and counters clear automatically when none remain.

## 0.7.0 - 2026-08-05

- Added **Target Mesh Connector** for two selected mesh objects. Selected faces
  on the active contact mesh define one boundary loop; the nearby-delete slider
  chooses faces on the other target mesh and defines its matching loop.
- Added a translucent red deletion preview with an opacity slider, plus an
  explicit clear button, so target face removal is visible before building.
- Added non-destructive connected-result generation: contact faces are omitted
  from a new combined mesh, unequal closed loops are paired by proximity, and a
  fully triangulated bridge joins them. The two originals are preserved hidden.

## 0.6.3 - 2026-08-04

- Added **Directional Face Delete** Object Mode buttons for +X, -X, +Y, -Y,
  +Z, and -Z. Each button removes faces on selected mesh objects whose
  world-space normals point mostly toward that direction.
- Preserved linked mesh safety by making selected linked mesh data single-user
  before deleting faces, so an unselected duplicate is not changed by surprise.

## 0.6.2 - 2026-07-17

- Changed one-quad Apply Transition to place only the true minimum transition
  core inside the selected quad instead of generating the surrounding local
  adapter frame.
- Moved connected drawing out of the top-level sidebar flow and behind a
  default-off **Show Connected Drawing** experimental section.
- Removed named third-party comparison/callout documentation from the current
  docs; repository search found no copied third-party source or asset files.
- Marked generated transition faces with a durable `topology_transition_role`
  integer face layer: `2` is the actual transition core.
- Added source and packaged installed-copy smoke checks that prove the local
  five-to-three single-quad placement is the 11-face transition pattern.

## 0.6.1 - 2026-07-17

- Made **Apply Transition** accept one selected quad by inserting a connected
  all-quad local adapter around the requested real transition pattern.
- Added support for the same one-quad workflow when the quad's closed edge loop
  is selected instead of the face.
- Added fitted-face fold/intersection validation before BMesh mutation, catching
  invalid pole-spacing or selection-shape combinations before the mesh changes.
- Added source and installed-copy smoke coverage for all eight single-quad
  transition insertions plus a packaged five-to-three single-quad proof.
- Documented the single-quad adapter tradeoff: neighboring edges stay fixed, but
  extra local adapter poles are added around the real transition.

## 0.6.0 - 2026-07-16

- Added **Draw Connected Multi-Strip**: one surface stroke grows every selected
  open-boundary edge as a lane in one welded quad sheet, reusing the exact
  bottom vertices and selecting the far boundary for immediate continuation.
- Added a **Transition Ribbon** layout that draws all eight existing pole-based
  density patterns over an explicit target, with cyan filled quad preview,
  magenta transition cells, width/length controls, width flipping, and rollback
  on invalid surface or topology.
- Added idempotent live Shrinkwrap setup with runtime-compatible Nearest Surface,
  Target Normal Project, and directional Project presets.
- Added an orange exact-topology bake cage with topology, geometry, and transform
  staleness detection, plus sampled green-hit/red-miss bake-ray visualization
  with coverage and distance statistics.
- Added selected-to-active bake readiness checks and safe Cycles configuration
  for tangent normals or displacement; preparation never starts a bake.
- Added pure topology tests, Blender welded-ribbon tests, Shrinkwrap/cage/readiness
  tests, bake-ray hit/miss tests, and research-backed workflow documentation.

## 0.5.0 - 2026-07-16

- Rebuilt **Quad Flow Regions** around interior extraordinary vertices: pole
  separatrices divide the complete visible quad mesh into broad, non-overlapping
  topology zones, with a colored full-map overlay, region selection, scrolling,
  and automatic camera focus. The former one-quad-wide traversal remains as the
  explicitly named **Individual Face Bands** mode.
- Made the tri/n-gon solvers handle ordinary isolated manifold defects. When a
  local pair is unavailable, they propagate opposite-edge splits through entire
  surrounding quad paths and rebuild every touched face as quads instead of
  moving the non-quad problem into a neighbor.
- Made a single selected triangle automatically pair with a compatible adjacent
  unselected triangle, and retained separate buttons for triangles and n-gons.
- Replaced the misleading uniform-grid atlas with eight true density examples:
  three regular input rows, the pole transition, then three regular output rows
  with a visibly different column count.
- Added **Mesh Integrity** controls that find open boundaries, over-connected
  edges, wire geometry, and isolated vertices; select the exact problem
  elements; and step/focus connected issue areas.
- Added pure and Blender tests for region coverage, pole separation, embedded
  triangle/pentagon propagation, exact open-boundary selection, non-manifold
  shared edges, and closed-mesh checks.

## 0.4.0 - 2026-07-15

- Replaced edge-chain browsing with true quad face-flow discovery: each flow is a maximal one-quad-wide band crossing opposite edges of successive quads.
- Changed the viewport overlay, selection, focus, metrics, selected scope, neighbor display, and side-to-side ordering to operate on face bands directly.
- Added separate **Solve Selected Tris** and **Solve Selected N-gons** operators with adjacent-triangle merging, even n-gon fans, triangle-plus-odd-n-gon parity repair, boundary center grids, rollback, and unsupported-face selection.
- Expanded Apply Transition to replace rectangular mixed quad/triangle/n-gon regions and regions enclosed by a closed selected edge loop.
- Replaced the single 5 → 3 example with one labeled 256-quad atlas covering all eight supported transition directions.
- Added Blender-independent quad-flow and repair-planning tests plus headless Blender coverage for five repair cases and both new transition input modes.

## 0.3.0 - 2026-07-15

- Changed flow browsing to group true parallel families and traverse each side-to-side before moving to another orientation or disconnected family.
- Added translucent full-quad-strip highlighting around the active edge flow and limited cyan neighbors to parallel flows one quad away.
- Added automatic viewport centering and framing on every scroll step, with an `F` shortcut and **Focus View** toggle.
- Changed flow confirmation to select the full adjoining quad strip instead of only the center edge chain.
- Added an **Add 5 to 3 Example Plane** action that creates a portrait all-quad reference mesh with colored topology bands.
- Added pure and Blender integration coverage for spatial ordering, strip membership, strip selection, and example-plane topology.

## 0.2.0 - 2026-07-15

- Added a modal Edge Flow Scroll inspector with wheel/arrow navigation, selection controls, and cancellation that restores the original selection.
- Added strict quad-topology and geometric continuation modes, selected/all-visible scopes, minimum-size and alignment filters, and length/smoothness/index ordering.
- Added viewport overlays for the active flow, neighboring flows, and open endpoints plus live HUD/sidebar metrics and pole classifications.
- Added Blender-independent edge-flow tests and headless Blender coverage for browsing and N-pole termination.
- Added a reproducible Blender screenshot workflow and visual documentation for transition setup, output, flow browsing, and pole termination.

## 0.1.0 - 2026-07-15

- Initial selection-driven rectangular patch workflow.
- Added 5 ↔ 3, 3 ↔ 1, 4 ↔ 2, and 1 ↔ 2 all-quad templates.
- Added active-edge direction, reverse flow, pole side, mirror, and pole spacing controls.
- Added pinned-boundary relaxation and original/external surface projection.
- Added validation-only and subdivision-preview operators.
- Added graph invariants, Blender headless integration tests, rollback safety, and packaging.
