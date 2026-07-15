# Edge Flow Scroll

Edge Flow Scroll is an Edit Mode inspector for understanding how complete quad strips cross an all-quad mesh, how parallel strips line up from one side to the other, and where their center edge chains terminate.

![A filled quad strip and its parallel neighbors on a torus](images/03-edge-flow-scroll.png)

## Reading the overlay

| Color | Meaning |
| --- | --- |
| Translucent orange faces | Full quad strip adjoining the current flow. |
| Strong orange line | Center edge chain retained for precise topology reading. |
| Cyan | Parallel flows one quad away across opposite edges of a quad. |
| Magenta | Endpoint at a boundary, pole, or other non-continuing vertex. |

The HUD and sidebar show the current/total flow number, edge and strip-face counts, world-space length, average directional alignment, open or closed state, endpoint labels, and parallel-neighbor count.

With **Side to Side** ordering, opposite edges across quads form a parallel-family graph. The browser completes one adjacent family before moving to a different orientation or disconnected part of the mesh, eliminating the old length/index jumps between unrelated flows.

![A three-edge flow terminating at an N-pole](images/04-flow-termination.png)

## Discovery modes

### Quad Topology

At a regular valence-four vertex surrounded by quads, the incoming edge continues through the topologically opposite edge. This makes the result independent of the mesh's pose. A flow stops at a boundary, an N-pole (valence three), an E-pole (valence five), or another extraordinary junction. Aligned valence-two wire/boundary segments can continue as a practical special case.

### Geometric

At each vertex, candidate edges are paired by straightest continuation. This can trace a visually continuous path through an extraordinary junction, but it is intentionally a heuristic: equally plausible directions can exist on symmetric or highly distorted topology.

## Scope and filtering

- **All Visible** considers every non-hidden edge in the active mesh.
- **Selected Edges** restricts discovery to the current selected-edge subgraph.
- **Minimum Edges** removes short fragments from the browser.
- **Pair Threshold** prevents continuation between edge pairs below the chosen straightness.
- **Order** traverses side-to-side by default, or sorts by longest, smoothest, or stable mesh index.

Closed flows are canonicalized to a stable starting edge and direction. Open flows retain endpoint classifications such as `Boundary (v3)` or `N-pole (v3)`.

## Controls

| Input | Action |
| --- | --- |
| Wheel / Left / Right / Up / Down | Browse and frame the next/previous quad strip. |
| Home / End | Jump to first / last flow. |
| Enter | Select the full current quad strip and finish. |
| S | Select the full strip and continue browsing. |
| F | Toggle automatic viewport centering and framing. |
| N | Toggle parallel-neighbor overlay. |
| Middle mouse | Pass viewport navigation through to Blender. |
| Esc / right-click | Cancel and restore the selection from before the inspector started. |

The non-modal **Previous**, **Refresh**, and **Next** buttons use the same discovery engine when stepping without a wheel session is preferable.

## Example plane

Use **Add 5 to 3 Example Plane** in Object Mode to create a portrait reference mesh at the 3D Cursor. It contains 56 quads, colored topology bands, four N-poles, and two E-poles, and is ready for the flow inspector after entering Edit Mode.

![The generated example plane with its active quad strip](images/05-example-plane-strip.png)

## Topology changes while browsing

The inspector leaves mesh data and selection untouched until a strip is confirmed; view framing is the only default interaction. If vertex or edge counts change while it is active, the session cancels and asks for a refresh instead of drawing stale indices against a changed BMesh.
