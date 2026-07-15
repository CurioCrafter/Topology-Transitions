"""Persistent scene settings for the Topology Transitions panel."""

from __future__ import annotations

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import Object, PropertyGroup

from .core import transition_items


def _mesh_object_poll(_self, obj: Object) -> bool:
    return obj is not None and obj.type == "MESH"


class QT_PG_settings(PropertyGroup):
    transition: EnumProperty(
        name="Transition",
        description="Incoming and outgoing edge-loop counts",
        items=transition_items(),
        default="FIVE_TO_THREE",
    )
    axis_mode: EnumProperty(
        name="Patch Axis",
        description="Choose which pair of opposite patch sides carries the loop count",
        items=(
            (
                "AUTO",
                "Auto / Active Edge",
                "Use the active boundary edge when possible",
            ),
            (
                "ALTERNATE",
                "Alternate Axis",
                "Use the other valid axis on a square patch",
            ),
        ),
        default="AUTO",
    )
    flip_flow: BoolProperty(
        name="Reverse Flow",
        description="Use the opposite side of the selected strip as the incoming side",
        default=False,
    )
    pole_side: EnumProperty(
        name="Pole Side",
        description="Place the local reduction cell toward the left, center, or right",
        items=(
            ("LEFT", "Left", "Move the pole pattern toward the left"),
            ("CENTER", "Center", "Center the pole pattern where the count permits"),
            ("RIGHT", "Right", "Move the pole pattern toward the right"),
        ),
        default="CENTER",
    )
    mirror: BoolProperty(
        name="Mirror",
        description="Mirror the pole arrangement or asymmetric parity shoulder",
        default=False,
    )
    pole_spacing: FloatProperty(
        name="Pole Spacing",
        description="Scale the initial spacing between extraordinary vertices",
        min=0.5,
        max=2.0,
        default=1.0,
    )
    relax_strength: FloatProperty(
        name="Relax Strength",
        description=(
            "Laplacian relaxation applied while the patch boundary remains pinned"
        ),
        min=0.0,
        max=1.0,
        default=0.55,
        subtype="FACTOR",
    )
    relax_iterations: IntProperty(
        name="Relax Iterations",
        description="Number of pinned-boundary relaxation passes",
        min=0,
        max=100,
        default=24,
    )
    conform_surface: BoolProperty(
        name="Conform to Surface",
        description=(
            "Project new interior vertices back to the original selected surface"
        ),
        default=True,
    )
    projection_target: PointerProperty(
        name="Projection Target",
        description=(
            "Optional mesh used instead of the original patch for surface projection"
        ),
        type=Object,
        poll=_mesh_object_poll,
    )
    subdivision_levels: IntProperty(
        name="Preview Levels",
        description=(
            "Viewport subdivision level for the non-destructive preview modifier"
        ),
        min=1,
        max=4,
        default=2,
    )
    flow_mode: EnumProperty(
        name="Flow Mode",
        description="How an edge continues through a vertex",
        items=(
            (
                "TOPOLOGY",
                "Quad Topology",
                "Follow opposite edges at regular quad vertices and stop at poles",
            ),
            (
                "GEOMETRIC",
                "Geometric Continuity",
                "Pair the straightest edges, including through extraordinary vertices",
            ),
        ),
        default="TOPOLOGY",
    )
    flow_scope: EnumProperty(
        name="Scope",
        description="Edges considered by the flow browser",
        items=(
            ("ALL", "All Visible", "Inspect all visible edges on the active mesh"),
            ("SELECTED", "Selected", "Inspect only currently selected edges"),
        ),
        default="ALL",
    )
    flow_sort: EnumProperty(
        name="Order",
        description="Order used when scrolling through flows",
        items=(
            (
                "SIDE_TO_SIDE",
                "Side to Side",
                "Keep parallel flows together and traverse adjacent quad strips",
            ),
            ("LONGEST", "Longest First", "Show flows with the most edges first"),
            (
                "SMOOTHEST",
                "Smoothest First",
                "Show the straightest or smoothest flows first",
            ),
            ("INDEX", "Mesh Order", "Use deterministic mesh edge order"),
        ),
        default="SIDE_TO_SIDE",
    )
    flow_min_edges: IntProperty(
        name="Minimum Edges",
        description="Hide shorter flow fragments; use one to include every edge",
        min=1,
        max=1000,
        default=1,
    )
    flow_min_alignment: FloatProperty(
        name="Pair Threshold",
        description="Minimum straightness required to pair two edges into one flow",
        min=0.0,
        max=1.0,
        default=0.15,
        subtype="FACTOR",
    )
    flow_show_neighbors: BoolProperty(
        name="Show Neighbors",
        description="Draw parallel flows one quad away in cyan",
        default=True,
    )
    flow_focus_view: BoolProperty(
        name="Focus View",
        description="Center and frame the active quad strip while browsing",
        default=True,
    )
    flow_index: IntProperty(name="Current Flow", min=0, default=0)
    flow_object_name: StringProperty(name="Inspected Object", default="")
    flow_count: IntProperty(name="Flow Count", min=0, default=0)
    flow_edge_count: IntProperty(name="Edge Count", min=0, default=0)
    flow_quad_count: IntProperty(name="Quad Count", min=0, default=0)
    flow_neighbor_count: IntProperty(name="Neighbor Count", min=0, default=0)
    flow_length: FloatProperty(name="Flow Length", min=0.0, default=0.0)
    flow_alignment: FloatProperty(name="Flow Alignment", min=0.0, max=1.0, default=0.0)
    flow_closed: BoolProperty(name="Closed Flow", default=False)
    flow_start_label: StringProperty(name="Start", default="Not inspected")
    flow_end_label: StringProperty(name="End", default="Not inspected")


CLASSES = (QT_PG_settings,)


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.topology_transitions = PointerProperty(type=QT_PG_settings)


def unregister() -> None:
    if hasattr(bpy.types.Scene, "topology_transitions"):
        del bpy.types.Scene.topology_transitions
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
