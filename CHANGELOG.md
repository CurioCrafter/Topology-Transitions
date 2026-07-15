# Changelog

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
