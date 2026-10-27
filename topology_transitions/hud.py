"""Coloured in-viewport coach cards shared by the modal tools.

Cards are drawn in POST_PIXEL space: a dark rounded panel with an accent bar,
a title, rows of coloured text runs, coloured stat chips, and key-cap hints.
"""

from __future__ import annotations

from collections.abc import Sequence
from math import cos, pi, sin

import blf
import gpu
from gpu_extras.batch import batch_for_shader

Color = tuple[float, float, float, float]

INK = (0.93, 0.94, 0.97, 1.0)
MUTED = (0.62, 0.66, 0.74, 1.0)
PANEL = (0.07, 0.08, 0.11, 0.9)
KEYCAP = (0.82, 0.85, 0.92, 1.0)
KEY_INK = (0.07, 0.08, 0.11, 1.0)

TEAL = (0.18, 0.85, 0.62, 1.0)
SKY = (0.25, 0.62, 1.0, 1.0)
AMBER = (1.0, 0.68, 0.18, 1.0)
MAGENTA = (0.98, 0.32, 0.78, 1.0)
CORAL = (1.0, 0.35, 0.33, 1.0)
VIOLET = (0.64, 0.5, 1.0, 1.0)

FONT = 0
TITLE_SIZE = 17.0
TEXT_SIZE = 13.5
PAD = 12.0
LINE = 21.0

# A row is a sequence of runs. A run is ("text", color), ("chip", color, text)
# or ("key", text); keys are drawn as light key caps.
Run = tuple
Row = Sequence[Run]


def left_margin(area) -> float:
    """Clear the toolbar when it overlaps the viewport (Region Overlap)."""

    tools = next((r for r in area.regions if r.type == "TOOLS"), None)
    return (tools.width if tools is not None else 0.0) + 16.0


def _shader():
    return gpu.shader.from_builtin("UNIFORM_COLOR")


def rounded_rect(x: float, y: float, w: float, h: float, r: float, color: Color):
    r = max(0.0, min(r, w / 2.0, h / 2.0))
    points = []
    for cx, cy, start in (
        (x + w - r, y + h - r, 0.0),
        (x + r, y + h - r, pi / 2.0),
        (x + r, y + r, pi),
        (x + w - r, y + r, 1.5 * pi),
    ):
        for step in range(7):
            angle = start + step * (pi / 2.0) / 6
            points.append((cx + cos(angle) * r, cy + sin(angle) * r))
    center = (x + w / 2.0, y + h / 2.0)
    tris = []
    for first, second in zip(points, points[1:] + points[:1], strict=True):
        tris.extend((center, first, second))
    shader = _shader()
    shader.bind()
    shader.uniform_float("color", color)
    batch_for_shader(shader, "TRIS", {"pos": tris}).draw(shader)


def _text(x: float, y: float, text: str, color: Color, size: float) -> float:
    blf.size(FONT, size)
    blf.color(FONT, *color)
    blf.position(FONT, x, y, 0.0)
    blf.draw(FONT, text)
    return blf.dimensions(FONT, text)[0]


def _run_width(run: Run, size: float) -> float:
    blf.size(FONT, size)
    kind = run[0] if run[0] in {"chip", "key"} else "text"
    if kind == "text":
        return blf.dimensions(FONT, run[0])[0]
    label = run[2] if kind == "chip" else run[1]
    return blf.dimensions(FONT, label)[0] + 14.0


def row_width(row: Row, size: float = TEXT_SIZE) -> float:
    return sum(_run_width(run, size) + 6.0 for run in row)


def draw_row(x: float, y: float, row: Row, size: float = TEXT_SIZE) -> None:
    for run in row:
        if run[0] == "chip":
            _kind, color, label = run
            width = _run_width(run, size)
            rounded_rect(x, y - 5.0, width, 19.0, 8.0, color)
            _text(x + 7.0, y, label, KEY_INK, size)
        elif run[0] == "key":
            width = _run_width(run, size)
            rounded_rect(x, y - 5.0, width, 19.0, 4.0, KEYCAP)
            _text(x + 7.0, y, run[1], KEY_INK, size)
        else:
            width = _text(x, y, run[0], run[1], size)
        x += width + 6.0


def measure_card(title: str, rows: Sequence[Row]) -> tuple[float, float]:
    blf.size(FONT, TITLE_SIZE)
    width = max([blf.dimensions(FONT, title)[0]] + [row_width(row) for row in rows])
    return width + 2.0 * PAD + 6.0, PAD * 2.0 + 26.0 + LINE * len(rows)


def draw_card(
    x: float,
    y: float,
    title: str,
    accent: Color,
    rows: Sequence[Row],
    *,
    min_width: float = 0.0,
) -> tuple[float, float]:
    """Draw a card with its bottom-left corner at (x, y); returns (w, h)."""

    blf.size(FONT, TITLE_SIZE)
    width = (
        max(
            [min_width, blf.dimensions(FONT, title)[0]]
            + [row_width(row) for row in rows]
        )
        + 2.0 * PAD
        + 6.0
    )
    height = PAD * 2.0 + 26.0 + LINE * len(rows)
    gpu.state.blend_set("ALPHA")
    try:
        rounded_rect(x, y, width, height, 10.0, PANEL)
        rounded_rect(x, y, 6.0, height, 3.0, accent)
        top = y + height - PAD - 15.0
        _text(x + PAD + 6.0, top, title, accent, TITLE_SIZE)
        for index, row in enumerate(rows):
            draw_row(x + PAD + 6.0, top - 28.0 - index * LINE, row)
    finally:
        gpu.state.blend_set("NONE")
    return width, height
