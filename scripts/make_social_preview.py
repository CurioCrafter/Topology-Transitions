"""Build docs/images/social-preview.png (1280x640) from the README screenshots.

Run with a Python that has Pillow, then upload the PNG under the repository's
Settings -> General -> Social preview.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

IMAGES = Path(__file__).resolve().parents[1] / "docs" / "images"
WIDTH, HEIGHT = 1280, 640
INK = (17, 19, 26)
CHIPS = (
    ("Surface Paint", (46, 217, 158)),
    ("Topology Lens", (64, 158, 255)),
    ("5 -> 3 transitions", (250, 82, 199)),
    ("Find holes", (255, 173, 46)),
)


def font(size: int, bold: bool = False):
    for name in ("segoeuib.ttf" if bold else "segoeui.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def main() -> None:
    image = Image.new("RGB", (WIDTH, HEIGHT), INK)
    draw = ImageDraw.Draw(image)
    lens = Image.open(IMAGES / "13-topology-lens.png").crop((300, 250, 1020, 760))
    paint = Image.open(IMAGES / "09-surface-paint-live.png").crop((380, 320, 900, 620))
    lens = lens.resize((620, round(620 * lens.height / lens.width)))
    paint = paint.resize((420, round(420 * paint.height / paint.width)))
    image.paste(lens, (WIDTH - lens.width - 30, 40))
    image.paste(paint, (WIDTH - paint.width - 60, HEIGHT - paint.height - 30))
    draw.rectangle((0, 0, 10, HEIGHT), fill=CHIPS[0][1])
    draw.text((50, 70), "Topology", font=font(76, True), fill=(237, 240, 247))
    draw.text((50, 150), "Transitions", font=font(76, True), fill=CHIPS[1][1])
    for row, text in enumerate(
        ("Free retopology & quad topology", "tools for Blender 4.2 - 5.2")
    ):
        draw.text((52, 250 + row * 40), text, font=font(30), fill=(200, 205, 215))
    top = 370
    for text, color in CHIPS:
        bold = font(26, True)
        width = draw.textlength(text, font=bold) + 30
        draw.rounded_rectangle((52, top, 52 + width, top + 44), radius=20, fill=color)
        draw.text((67, top + 6), text, font=bold, fill=INK)
        top += 58
    image.save(IMAGES / "social-preview.png", optimize=True)


if __name__ == "__main__":
    main()
