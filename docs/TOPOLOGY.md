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
