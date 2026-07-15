"""Persistent scene settings for the Topology Transitions panel."""

from __future__ import annotations

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
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
