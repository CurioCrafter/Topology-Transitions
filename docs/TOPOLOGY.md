# Topology model

## Why the operator is template-driven

Loop reductions are deterministic once four things are known:

1. the incoming loop count;
2. the outgoing loop count;
3. the patch boundary segmentation;
4. the requested pole side.

The difficult artistic choice is where the extraordinary vertices should sit on the model. Topology Transitions therefore does not attempt whole-object automatic retopology. It validates a user-selected patch, builds a known quad graph, then lets the user control its orientation and fit.

## Quad-disk parity

For a disk-like patch of quads:

```text
V - E + F = 1
4F = 2E_interior + E_boundary
V_boundary = E_boundary
```

Eliminating the edge terms gives:

```text
F = V_interior + E_boundary / 2 - 1
```

Therefore the boundary edge count must be even. A direct 1 → 2 band with one edge on each side would have `1 + 2 + 1 + 1 = 5` boundary edges and cannot be filled by quads. The classic asymmetric pattern adds one compensating side edge, producing six boundary edges.

The add-on assigns shoulder edges from the wide side of the selected rectangle to the side boundaries. This preserves every existing outside edge while giving the transition template the boundary parity it needs.

The same parity rule governs the repair buttons:

- two adjacent triangles have a four-edge union boundary and can become one quad;
- one triangle plus one odd n-gon has an even union boundary and can be repartitioned into quads;
- an even n-gon can be split into `n / 2 - 1` boundary-preserving quads;
- an isolated triangle or odd n-gon cannot be filled only with quads unless its boundary gains vertices. The boundary-only center-grid fallback safely splits those edges because no unselected neighboring faces use them.

The solver does not split a manifold boundary edge beside an unselected face, because doing so would turn that neighbor into an n-gon or expand the requested edit without consent.

## Two-loop cell

The 5 → 3, 3 → 1, and 4 → 2 families all use the same local operation: a three-segment span on the wide row becomes one segment on the narrow row. Quads on either side pass through one-for-one.

The cell contains:

- two valence-three interior vertices (N-poles);
- one closing quad on the narrow side;
- two narrow-row vertices that become valence-five E-poles when regular topology continues beyond the selected boundary.

Moving the three-segment span across the wide row implements the left, center, and right controls. Reversing the same graph produces an expansion.

## One-loop cell

The 1 ↔ 2 cell is asymmetric because of the parity result above. It has:

- one valence-three interior N-pole;
- one extra boundary edge on the chosen shoulder side;
- three quads in the minimum one-row patch.

Mirroring changes which side receives the compensating edge.

## Geometric fitting

Topology construction and geometric fitting are separate:

1. Existing boundary vertices are bound directly to template boundary keys and never moved.
2. New vertices receive normalized `(u, v)` coordinates and a bilinear initial position over the selected rectangle.
3. Pinned-boundary Laplacian relaxation distributes the new interior.
4. Pole anchors retain some of the requested pole spacing during relaxation.
5. New vertices are projected onto the original selected surface or an explicit target mesh.
6. The resulting BMesh is checked for boundary preservation, manifold affected edges, quad area, and pole valence.

The original BMesh is copied before mutation. If an apply-time check or Blender API operation fails, the backup is restored before the operator returns an error.

## Patch input topology

The transition template depends on the outside boundary, not on the face types being replaced. Apply Transition can therefore consume either a selected mixed-topology face disk or a closed selected edge loop around one. The boundary must still form four sides with compatible opposite segment counts. For a mixed interior, the four strongest geometric turns identify the physical corners; a regular all-quad grid continues to use its exact combinatorial corners.

## Single-quad insertion

A selected quad has only four outside edges, while a real 5 -> 3, 3 -> 1, 4 -> 2, or 1 -> 2 transition needs a longer boundary loop to express its incoming and outgoing counts. Directly binding those templates to a single quad would either split neighboring faces or create invalid non-quad topology.

For that case, Apply Transition wraps the real preset in a connected all-quad local frame:

1. the selected quad's four outside vertices and edges stay fixed;
2. a guard loop and a smaller transition loop are added inside the quad;
3. the actual preset topology is placed in the center; and
4. the guard and preset boundary are locked during relaxation so the adapter does not collapse.

This makes "turn any quad into this transition" practical and transactional. The tradeoff is visible density: the local frame adds adapter extraordinary vertices in addition to the transition's own N-poles. Larger four-sided patch selections remain the cleaner choice when the surrounding edge flow can participate in the transition boundary.

Before the BMesh is changed, the operator now checks that every shared internal edge is used in opposite loop directions and that the fitted projected edges do not cross. If the chosen pole controls or selection shape would fold the template over itself, the operation cancels before mutating the mesh.
