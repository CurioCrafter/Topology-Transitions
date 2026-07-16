# Changelog

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
