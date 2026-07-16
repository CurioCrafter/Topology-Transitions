# Quad Flow Scroll

Quad Flow Scroll has two deliberately different topology views. **Quad Flow
Regions** is the default whole-mesh reading tool. **Individual Face Bands** is a
granular loop/strip browser.

## Quad Flow Regions (default)

A retopology flow region is a connected patch of quads bounded by the topology
lines that emerge from poles. Discovery is entirely combinatorial:

1. open mesh boundaries and non-quad/non-manifold boundaries become barriers;
2. interior vertices whose valence is not four are extraordinary vertices;
3. from each extraordinary vertex, separatrices continue through the opposite
   edge at regular valence-four vertices; and
4. all eligible quads are flood-filled across non-barrier edges.

Every visible eligible quad belongs to exactly one region. A completely regular
grid is one region. A five-to-three template with its two N-poles is divided
into several pole-bounded regions.

![Whole colored region map around a transition](images/04-flow-termination.png)

The complete map uses distinct translucent colors. The current region is
orange/red with a strong boundary; separatrix edges are magenta. The HUD reports
region size, boundary length, boundary-edge count, and adjacent-region count.

Mouse wheel and arrow keys browse from one complete region to the next. With
**Focus View** enabled, every step moves the 3D View to the active region instead
of jumping between unrelated edge snippets.

## Individual Face Bands

Each quad also has two perpendicular one-face-wide directions. A band enters a
quad through one edge, leaves through the opposite edge, and repeats through the
next quad. Each quad therefore belongs to two perpendicular bands.

![A granular one-quad-wide band on a torus](images/03-edge-flow-scroll.png)

This mode is useful for checking literal loop continuation and side-to-side band
families. It is not described as the complete flow-zone map. It stops at mesh,
scope, non-quad, or non-manifold boundaries.

## Scope, ordering, and filtering

- **All Visible** considers every non-hidden quad face.
- **Selected Faces** clips discovery to the selected face set.
- **Minimum Quads** removes smaller regions or bands.
- **Largest First** is the default region order.
- **Side to Side** completes adjacent parallel families in face-band mode.
- Smoothness and stable mesh-index ordering remain available.

## Controls

| Input | Quad Flow Regions | Individual Face Bands |
| --- | --- | --- |
| Wheel / arrows | Browse and focus regions | Browse and focus bands |
| Home / End | First / last region | First / last band |
| Enter | Select region and finish | Select band and finish |
| S | Select region and continue | Select band and continue |
| F | Toggle automatic focus | Toggle automatic focus |
| N | Toggle the full colored map | Toggle parallel neighbors |
| Middle mouse | Pass navigation to Blender | Pass navigation to Blender |
| Esc / right-click | Restore original selection | Restore original selection |

The non-modal **Previous**, **Refresh**, and **Next** buttons use the same active
mode and also focus/select the complete region or band.

## True density atlas

**Add All Transition Examples** makes one joined, 186-quad atlas. Each of its
eight tiles has three regular input rows, a pole-based transition, and three
regular output rows with a different column count.

![True unequal-density example atlas](images/05-example-plane-strip.png)

## Topology changes while browsing

The inspector does not alter mesh topology. Selection changes only when the user
confirms/selects a region or band; cancel restores the starting selection. If
vertex, edge, or face counts change during a modal session, it closes instead of
drawing stale BMesh indices.
