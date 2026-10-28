"""Join capture_showcase.py GIF frames into the README GIFs.

    python scripts/make_gifs.py <frames folder>

Writes docs/images/paint.gif, mirror.gif and lens.gif, cropped to the 3D
viewport (without the sidebar) and scaled to README width.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

IMAGES = Path(__file__).resolve().parents[1] / "docs" / "images"
CROP = (0, 80, 1050, 900)  # viewport of a 1600x1000 Blender window
WIDTH = 760
# name: (frame milliseconds, final-frame hold milliseconds)
TIMING = {"paint": (260, 2200), "mirror": (420, 2400), "lens": (1600, 1600)}


def main(folder: Path) -> None:
    for name, (step, hold) in TIMING.items():
        paths = sorted(folder.glob(f"{name}-gif_*.png"))
        if not paths:
            print(f"no frames for {name}")
            continue
        frames = []
        for path in paths:
            frame = Image.open(path).convert("RGB").crop(CROP)
            frame = frame.resize(
                (WIDTH, round(WIDTH * frame.height / frame.width)), Image.LANCZOS
            )
            frames.append(frame.quantize(colors=128, method=Image.Quantize.MEDIANCUT))
        durations = [step] * (len(frames) - 1) + [hold]
        target = IMAGES / f"{name}.gif"
        frames[0].save(
            target,
            save_all=True,
            append_images=frames[1:],
            duration=durations,
            loop=0,
            optimize=True,
        )
        print(
            f"{target.name}: {len(frames)} frames, {target.stat().st_size // 1024} KB"
        )


if __name__ == "__main__":
    main(Path(sys.argv[1]))
