# Edge Flow Scroll

Edge Flow Scroll is an Edit Mode inspector for understanding how edge chains cross an all-quad mesh, where they form closed loops, and where they terminate at boundaries or extraordinary vertices.

![A closed loop and its neighboring flows on a torus](images/03-edge-flow-scroll.png)

## Reading the overlay

| Color | Meaning |
| --- | --- |
| Orange | Current flow. |
| Cyan | Neighboring flows that share one or more faces with the current flow. |
| Magenta | Endpoint at a boundary, pole, or other non-continuing vertex. |

The HUD and sidebar show the current/total flow number, edge count, world-space length, average directional alignment, open or closed state, endpoint labels, and neighboring-flow count.

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
- **Order** sorts by longest, smoothest, or stable mesh index.

Closed flows are canonicalized to a stable starting edge and direction. Open flows retain endpoint classifications such as `Boundary (v3)` or `N-pole (v3)`.

## Controls

| Input | Action |
| --- | --- |
| Wheel / Left / Right / Up / Down | Browse flows. |
| Home / End | Jump to first / last flow. |
| Enter | Select the current flow and finish. |
| S | Select the current flow and continue browsing. |
| N | Toggle neighboring-flow overlay. |
| Middle mouse | Pass viewport navigation through to Blender. |
| Esc / right-click | Cancel and restore the selection from before the inspector started. |

The non-modal **Previous**, **Refresh**, and **Next** buttons use the same discovery engine when stepping without a wheel session is preferable.

## Topology changes while browsing

The inspector is intentionally read-only until selection is confirmed. If vertex or edge counts change while it is active, the session cancels and asks for a refresh instead of drawing stale indices against a changed BMesh.
