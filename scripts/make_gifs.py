"""Join captured frames into the README GIFs and the listing MP4 videos.

    python scripts/make_gifs.py <frames folder>

The folder holds frames from capture_showcase.py (paint-gif_NN.png,
mirror-gif_NN.png and the 10/11 result shots) and capture_real_mesh.py
(lens_NN.png, manta-before.png, manta-after.png). Writes
docs/images/{paint,mirror,lens}.gif, cropped to the 3D viewport, and
dist/videos/*.mp4 (1280x720, full Blender window with a caption bar) using
ffmpeg from PATH. Missing inputs are skipped.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
IMAGES = ROOT / "docs" / "images"
VIDEOS = ROOT / "dist" / "videos"
GIF_CROP = (0, 80, 1050, 900)  # viewport of a 1600x1000 Blender window
GIF_WIDTH = 760
VIDEO_CROP = (0, 5, 1600, 905)  # 16:9 from the same window
INK = (17, 19, 26)
ACCENTS = {
    "paint": (46, 217, 158),
    "mirror": (46, 217, 158),
    "lens": (64, 158, 255),
    "autosolve": (255, 173, 46),
}


def font(size: int, bold: bool = False):
    for name in ("segoeuib.ttf" if bold else "segoeui.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def recipes(folder: Path) -> dict[str, list[tuple[Path, float, str]]]:
    """name: [(frame, seconds, caption)]"""

    def seq(pattern: str, step: float, hold: float, caption: str, final=None):
        paths = sorted(folder.glob(pattern))
        if final is not None and (folder / final).exists():
            paths.append(folder / final)
        return [
            (path, hold if index == len(paths) - 1 else step, caption)
            for index, path in enumerate(paths)
        ]

    return {
        "paint": seq(
            "paint-gif_*.png",
            0.22,
            0.6,
            "Surface Paint: paint where quads go, the grid follows the surface",
        )
        + [
            (
                folder / "10-surface-paint-result.png",
                2.6,
                "Enter: an all-quad retopo mesh",
            )
        ],
        "mirror": seq(
            "mirror-gif_*.png",
            0.4,
            2.4,
            "Mirror X: paint one side, get both with a shared seam",
            final="11-surface-paint-mirror.png",
        ),
        "lens": seq(
            "lens_*.png",
            1.7,
            1.9,
            "Topology Lens: hover any vertex to learn what it does to the flow",
        ),
        "autosolve": [
            (folder / "manta-before.png", 2.2, "AI scan: 2,152 open edges"),
            (
                folder / "manta-after.png",
                3.0,
                "Auto Solve, 0.3 s: seams welded, 2,152 open edges down to 16",
            ),
        ],
    }


def caption(frame: Image.Image, text: str, accent) -> Image.Image:
    frame = frame.copy()
    draw = ImageDraw.Draw(frame)
    bold = font(34, True)
    width = draw.textlength(text, font=bold)
    x, y = 112, 86
    draw.rounded_rectangle(
        (x - 22, y - 14, x + width + 22, y + 52), radius=14, fill=INK
    )
    draw.rectangle((x - 22, y - 14, x - 14, y + 52), fill=accent)
    draw.text((x, y - 4), text, font=bold, fill=(237, 240, 247))
    return frame


def write_gif(name: str, frames: list[tuple[Path, float, str]]) -> None:
    images = []
    for path, _seconds, _text in frames:
        image = Image.open(path).convert("RGB").crop(GIF_CROP)
        image = image.resize(
            (GIF_WIDTH, round(GIF_WIDTH * image.height / image.width)), Image.LANCZOS
        )
        images.append(image.quantize(colors=128, method=Image.Quantize.MEDIANCUT))
    target = IMAGES / f"{name}.gif"
    images[0].save(
        target,
        save_all=True,
        append_images=images[1:],
        duration=[round(seconds * 1000) for _p, seconds, _t in frames],
        loop=0,
        optimize=True,
    )
    print(f"{target.name}: {len(images)} frames, {target.stat().st_size // 1024} KB")


def write_mp4(name: str, frames: list[tuple[Path, float, str]], ffmpeg: str) -> None:
    VIDEOS.mkdir(parents=True, exist_ok=True)
    target = VIDEOS / f"{name}.mp4"
    with tempfile.TemporaryDirectory() as temp:
        lines = []
        for index, (path, seconds, text) in enumerate(frames):
            image = Image.open(path).convert("RGB").crop(VIDEO_CROP)
            still = Path(temp) / f"{index:03d}.png"
            caption(image, text, ACCENTS[name]).save(still)
            lines += [f"file '{still.as_posix()}'", f"duration {seconds}"]
        lines.append(f"file '{still.as_posix()}'")  # concat needs the last twice
        listing = Path(temp) / "frames.txt"
        listing.write_text("\n".join(lines), encoding="utf-8")
        subprocess.run(
            [
                ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                "-i", str(listing), "-vf", "fps=30,scale=1280:720:flags=lanczos",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20",
                "-movflags", "+faststart", str(target),
            ],
            check=True,
        )  # fmt: skip
    print(f"{target.name}: {target.stat().st_size // 1024} KB")


def main(folder: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    for name, frames in recipes(folder).items():
        frames = [frame for frame in frames if frame[0].exists()]
        if not frames:
            print(f"no frames for {name}")
            continue
        if name != "autosolve":
            write_gif(name, frames)
        if ffmpeg:
            write_mp4(name, frames, ffmpeg)
    if not ffmpeg:
        print("ffmpeg not found: skipped the MP4 videos")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
