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
    draw_layout: EnumProperty(
        name="Ribbon Layout",
        description="Grow a uniform connected sheet or draw a density transition",
        items=(
            (
                "UNIFORM",
                "Connected Multi-Strip",
                "Every selected boundary edge becomes one welded quad lane",
            ),
            (
                "TRANSITION",
                "Transition Ribbon",
                "Draw the chosen loop-count transition along the stroke",
            ),
        ),
        default="UNIFORM",
    )
    draw_segments: IntProperty(
        name="Length Segments",
        description="Number of shared cross-rows along the drawn surface path",
        min=1,
        max=64,
        default=6,
    )
    draw_width_scale: FloatProperty(
        name="Width Scale",
        description="Scale the ribbon from the width of the selected bottom boundary",
        min=0.25,
        max=4.0,
        default=1.0,
    )
    draw_flip_width: BoolProperty(
        name="Flip Width",
        description="Reverse which end of the selected chain is treated as left",
        default=False,
    )
    surface_target: PointerProperty(
        name="Surface Target",
        description="Separate high-poly mesh used for drawing and surface conformity",
        type=Object,
        poll=_mesh_object_poll,
    )
    draw_project_limit: FloatProperty(
        name="Project Limit",
        description=(
            "Maximum nearest-surface distance; zero allows any reachable distance"
        ),
        min=0.0,
        default=0.0,
        subtype="DISTANCE",
    )
    shrinkwrap_method: EnumProperty(
        name="Shrinkwrap Method",
        description="How the editable retopology cage follows the surface target",
        items=(
            (
                "NEAREST_SURFACEPOINT",
                "Nearest Surface",
                "Fast general-purpose snapping for vertices already near the target",
            ),
            (
                "TARGET_PROJECT",
                "Target Normal Project",
                "Smoother target-normal projection for organic surfaces",
            ),
            (
                "PROJECT",
                "Local Z Project",
                "Project both ways along the retopology object's local Z axis",
            ),
        ),
        default="NEAREST_SURFACEPOINT",
    )
    shrinkwrap_offset: FloatProperty(
        name="Surface Offset",
        description="Keep the editable retopology cage above the target surface",
        default=0.002,
        precision=4,
        subtype="DISTANCE",
    )
    bake_type: EnumProperty(
        name="Bake Type",
        items=(
            ("NORMAL", "Tangent Normal", "Prepare a tangent-space normal bake"),
            ("DISPLACEMENT", "Displacement", "Prepare a displacement bake"),
        ),
        default="NORMAL",
    )
    bake_use_cage: BoolProperty(
        name="Use Custom Cage",
        description="Use the exact-topology wire cage instead of ray distance",
        default=False,
    )
    bake_cage_distance: FloatProperty(
        name="Cage Distance",
        description="Local normal offset used to visualize the bake envelope",
        min=0.0,
        default=0.03,
        precision=4,
        subtype="DISTANCE",
    )
    bake_max_ray_distance: FloatProperty(
        name="Max Ray Distance",
        description="Selected-to-active ray reach when custom cage mode is disabled",
        min=0.0,
        default=0.02,
        subtype="DISTANCE",
    )
    bake_margin: IntProperty(
        name="Bake Margin",
        description="Pixel padding around UV islands",
        min=0,
        max=32767,
        default=16,
    )
    bake_ray_samples: IntProperty(
        name="Ray Samples",
        description="Maximum low-poly faces sampled by the bake-ray preview",
        min=1,
        max=5000,
        default=500,
    )
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
        name="Flow View",
        description="Choose broad retopology regions or individual face bands",
        items=(
            (
                "REGIONS",
                "Quad Flow Regions",
                "Map whole quad patches separated by poles and separatrix flows",
            ),
            (
                "FACE_STRIPS",
                "Individual Face Bands",
                "Inspect granular one-quad-wide loops and strips",
            ),
        ),
        default="REGIONS",
    )
    flow_scope: EnumProperty(
        name="Scope",
        description="Faces considered by the quad-flow browser",
        items=(
            ("ALL", "All Visible", "Inspect all visible faces on the active mesh"),
            ("SELECTED", "Selected Faces", "Inspect only selected faces"),
        ),
        default="ALL",
    )
    flow_sort: EnumProperty(
        name="Order",
        description="Order used when scrolling through flows",
        items=(
            (
                "LARGEST",
                "Largest First",
                "Show the broadest quad regions or longest face bands first",
            ),
            (
                "SIDE_TO_SIDE",
                "Side to Side",
                "Finish adjacent parallel face bands before changing direction",
            ),
            ("LONGEST", "Longest First", "Show flows with the most quads first"),
            (
                "SMOOTHEST",
                "Smoothest First",
                "Show the straightest or smoothest flows first",
            ),
            ("INDEX", "Mesh Order", "Use deterministic mesh face order"),
        ),
        default="LARGEST",
    )
    flow_min_edges: IntProperty(
        name="Minimum Quads",
        description="Hide shorter face bands; use one to include every quad",
        min=1,
        max=1000,
        default=1,
    )
    flow_show_neighbors: BoolProperty(
        name="Show Neighbors",
        description="Fill directly adjacent parallel quad bands in cyan",
        default=True,
    )
    flow_show_full_map: BoolProperty(
        name="Show Full Map",
        description="Colour every discovered quad-flow region while browsing",
        default=True,
    )
    flow_focus_view: BoolProperty(
        name="Focus View",
        description="Center and frame the active quad face band while browsing",
        default=True,
    )
    flow_index: IntProperty(name="Current Flow", min=0, default=0)
    flow_object_name: StringProperty(name="Inspected Object", default="")
    flow_count: IntProperty(name="Flow Count", min=0, default=0)
    flow_edge_count: IntProperty(name="Band Edge Count", min=0, default=0)
    flow_quad_count: IntProperty(name="Flow Quad Count", min=0, default=0)
    flow_neighbor_count: IntProperty(name="Neighbor Count", min=0, default=0)
    flow_length: FloatProperty(name="Flow Length", min=0.0, default=0.0)
    flow_alignment: FloatProperty(name="Flow Alignment", min=0.0, max=1.0, default=0.0)
    flow_closed: BoolProperty(name="Closed Flow", default=False)
    flow_start_label: StringProperty(name="Start", default="Not inspected")
    flow_end_label: StringProperty(name="End", default="Not inspected")
    manifold_object_name: StringProperty(name="Checked Object", default="")
    manifold_issue_count: IntProperty(name="Issue Elements", min=0, default=0)
    manifold_component_count: IntProperty(name="Issue Areas", min=0, default=0)
    manifold_component_index: IntProperty(name="Current Issue", min=0, default=0)
    manifold_open_edge_count: IntProperty(name="Open Edges", min=0, default=0)
    manifold_nonmanifold_edge_count: IntProperty(
        name="Non-Manifold Edges", min=0, default=0
    )
    manifold_wire_edge_count: IntProperty(name="Wire Edges", min=0, default=0)
    manifold_isolated_vertex_count: IntProperty(
        name="Isolated Vertices", min=0, default=0
    )
    manifold_current_kind: StringProperty(
        name="Current Issue Type", default="Not checked"
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
