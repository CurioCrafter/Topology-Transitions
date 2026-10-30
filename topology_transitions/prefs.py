"""Add-on preferences: where the sidebar lives, which sections show, how the
viewport coach cards and the Topology Lens look.

Every draw function reads these through ``get()``, which falls back to the
defaults when the add-on was registered without its preferences (tests,
scripts that import the package directly).
"""


from types import SimpleNamespace

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, StringProperty
from bpy.types import AddonPreferences

ADDON = __package__
DEFAULT_TAB = "Quad Transition"
SECTIONS = (
    ("inspect", "Inspect"),
    ("paint", "Surface Paint"),
    ("transitions", "Transitions"),
    ("repair", "Repair & Optimize"),
    ("flow", "Flow Browser"),
    ("cleanup", "Merge & Cleanup"),
    ("bake", "Surface & Bake"),
)
CORNERS = (
    ("BOTTOM_LEFT", "Bottom Left", ""),
    ("TOP_LEFT", "Top Left", ""),
    ("BOTTOM_RIGHT", "Bottom Right", ""),
    ("TOP_RIGHT", "Top Right", ""),
)


def _retab(self, _context) -> None:
    from . import ui

    ui.set_tab(self.sidebar_tab.strip() or DEFAULT_TAB)


class QT_AddonPreferences(AddonPreferences):
    bl_idname = ADDON

    sidebar_tab: StringProperty(
        name="Sidebar Tab",
        description="Name of the 3D View sidebar tab; use an existing tab name "
        "(for example Tool or Edit) to merge the panels into it",
        default=DEFAULT_TAB,
        update=_retab,
    )
    show_start_here: BoolProperty(
        name="Start Here Card",
        description="Show the Start Here card with the three main tools and the "
        "setup checklist",
        default=True,
    )
    show_hints: BoolProperty(
        name="Beginner Hints",
        description="Show short explanations under the buttons; turn off for a "
        "compact sidebar",
        default=True,
    )
    show_inspect: BoolProperty(name="Inspect", default=True)
    show_paint: BoolProperty(name="Surface Paint", default=True)
    show_transitions: BoolProperty(name="Transitions", default=True)
    show_repair: BoolProperty(name="Repair & Optimize", default=True)
    show_flow: BoolProperty(name="Flow Browser", default=True)
    show_cleanup: BoolProperty(name="Merge & Cleanup", default=True)
    show_bake: BoolProperty(name="Surface & Bake", default=True)

    show_cards: BoolProperty(
        name="Coach Cards",
        description="Show the coloured cards with live counts and key hints in "
        "the viewport while a tool runs",
        default=True,
    )
    card_scale: FloatProperty(
        name="Card Size",
        description="Size of the viewport cards, on top of Blender's interface "
        "resolution scale",
        default=1.0,
        min=0.6,
        max=2.0,
        step=10,
    )
    card_opacity: FloatProperty(
        name="Card Opacity",
        description="Opacity of the card backgrounds",
        default=0.9,
        min=0.3,
        max=1.0,
        subtype="FACTOR",
    )
    card_corner: EnumProperty(
        name="Card Corner",
        description="Viewport corner for the coach cards",
        items=CORNERS,
        default="BOTTOM_LEFT",
    )
    card_key_hints: BoolProperty(
        name="Key Hints",
        description="Show the key-cap rows on the cards",
        default=True,
    )

    lens_dot_size: FloatProperty(
        name="Lens Dot Size",
        description="Size of the Topology Lens pole dots, in pixels",
        default=11.0,
        min=4.0,
        max=30.0,
    )
    lens_hover_card: BoolProperty(
        name="Lens Hover Card",
        description="Explain the vertex under the mouse with a card next to it",
        default=True,
    )
    lens_tint_faces: BoolProperty(
        name="Tint Triangles and N-gons",
        description="Tint triangles amber and n-gons red while the Lens is on",
        default=True,
    )

    def draw(self, _context):
        layout = self.layout
        sidebar = layout.box()
        sidebar.label(text="Sidebar", icon="MENU_PANEL")
        sidebar.prop(self, "sidebar_tab")
        row = sidebar.row()
        row.prop(self, "show_start_here")
        row.prop(self, "show_hints")
        sidebar.label(text="Sections to show:")
        grid = sidebar.grid_flow(row_major=True, columns=3, align=True)
        for key, _label in SECTIONS:
            grid.prop(self, f"show_{key}", toggle=True)

        cards = layout.box()
        cards.label(text="Viewport Coach Cards", icon="WINDOW")
        cards.prop(self, "show_cards")
        column = cards.column()
        column.active = self.show_cards
        column.prop(self, "card_corner")
        column.prop(self, "card_scale", slider=True)
        column.prop(self, "card_opacity", slider=True)
        column.prop(self, "card_key_hints")

        lens = layout.box()
        lens.label(text="Topology Lens", icon="VIEWZOOM")
        lens.prop(self, "lens_dot_size", slider=True)
        row = lens.row()
        row.prop(self, "lens_hover_card")
        row.prop(self, "lens_tint_faces")


_DEFAULTS = SimpleNamespace(
    **{
        name: prop.keywords["default"]
        for name, prop in QT_AddonPreferences.__annotations__.items()
    }
)


def get():
    addon = bpy.context.preferences.addons.get(ADDON)
    return addon.preferences if addon is not None else _DEFAULTS


def register() -> None:
    bpy.utils.register_class(QT_AddonPreferences)


def unregister() -> None:
    bpy.utils.unregister_class(QT_AddonPreferences)
