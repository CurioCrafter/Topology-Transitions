# Quad Flow Scroll

Quad Flow Scroll is an Edit Mode inspector for the one-quad-wide face loops and open face strips that pass through a retopologized mesh. Faces are the primary flow elements. Edges only define how the band enters and leaves each quad.

![A filled quad face band and its parallel neighbors on a torus](images/03-edge-flow-scroll.png)

## What counts as a quad flow

Every quad has two perpendicular flow directions. For either direction, the browser enters through one edge, crosses the face, leaves through the opposite edge, and repeats in the neighboring quad. The maximal connected sequence is one flow.

On a 4 × 3 quad grid this produces seven flows: three rows of four faces and four columns of three faces. Each face therefore belongs to exactly two perpendicular flows. The previous edge-chain implementation produced five chains and filled faces on both sides of each chain; that is no longer the model used by the add-on.

A flow stops at:

- a mesh boundary;
- the edge of the selected-face scope;
- a triangle or n-gon;
- a non-manifold edge.

Pole valence does not choose or terminate a face flow. Continuation is determined by the opposite edge of each quad, which keeps the result topological and pose-independent.

## Reading the overlay

| Color | Meaning |
| --- | --- |
| Translucent orange faces | The active quad flow itself. |
| Thin orange grid | Every edge belonging to the active face band. |
| Strong orange outline | The outside boundary of the active face band. |
| Translucent cyan faces | Directly adjacent parallel face bands. |
| Magenta edge | An open terminal edge. |

The HUD and sidebar show the current/total flow number, quad count, world-space centerline length, centerline smoothness, open or closed state, terminal classifications, and parallel-band count.

With **Side to Side** ordering, side-adjacent bands form a parallel-family graph. The browser finishes one family from one side to the other before moving to a perpendicular direction or disconnected component.

![A one-quad-wide face flow crossing a five-to-three transition](images/04-flow-termination.png)

## Scope and filtering

- **All Visible** considers every non-hidden quad face in the active mesh.
- **Selected Faces** restricts discovery to the current selected-face region.
- **Minimum Quads** removes short bands from the browser.
- **Order** traverses side-to-side by default, or sorts by quad count, centerline smoothness, or stable face index.

## Controls

| Input | Action |
| --- | --- |
| Wheel / Left / Right / Up / Down | Browse and frame the next/previous quad face band. |
| Home / End | Jump to first / last flow. |
| Enter | Select the full current face band and finish. |
| S | Select the full face band and continue browsing. |
| F | Toggle automatic viewport centering and framing. |
| N | Toggle adjacent parallel-band overlay. |
| Middle mouse | Pass viewport navigation through to Blender. |
| Esc / right-click | Cancel and restore the pre-inspector selection. |

The non-modal **Previous**, **Refresh**, and **Next** buttons use the same face-flow engine.

## Example atlas

Use **Add All Transition Examples** in Object Mode to create one joined atlas mesh at the 3D Cursor. Its eight labeled tiles cover every supported direction: 5 → 3, 3 → 5, 3 → 1, 1 → 3, 4 → 2, 2 → 4, 1 → 2, and 2 → 1. The atlas contains 256 quads and is ready for the flow inspector after entering Edit Mode.

![The generated example atlas](images/05-example-plane-strip.png)

## Topology changes while browsing

The inspector leaves mesh data and selection untouched until a face band is confirmed; view framing is the only default interaction. If vertex, edge, or face counts change while it is active, the session cancels instead of drawing stale indices against a changed BMesh.
