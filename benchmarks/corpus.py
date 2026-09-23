"""A fixed set of images to measure tracing quality against.

Drawn rather than photographed, for three reasons: the numbers are then
comparable between machines and between runs, nobody's private pictures have
to travel to a build runner, and each image isolates one thing the tracer has
historically got wrong.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

#: Bumping this invalidates a recorded baseline, on purpose.
CORPUS_VERSION = 1


def _font(size: int) -> ImageFont.ImageFont:
    for candidate in (
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def poster() -> Image.Image:
    """Type at three sizes.  Body copy used to come out as gibberish."""
    image = Image.new("RGB", (900, 600), (250, 249, 246))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle([60, 60, 300, 200], radius=28, fill=(32, 90, 200))
    draw.ellipse([110, 100, 190, 180], fill=(255, 214, 72))
    draw.text((60, 240), "Vector", font=_font(92), fill=(20, 22, 28))
    draw.text((60, 340), "Sharp edges matter", font=_font(40), fill=(60, 64, 72))
    draw.text((60, 400), "Small text is where tracers fall apart — 13px body copy,",
              font=_font(20), fill=(90, 95, 104))
    draw.text((60, 430), "thin strokes, tight counters in a, e, o and g.",
              font=_font(20), fill=(90, 95, 104))
    draw.text((60, 480), "MIXED Case 0123456789", font=_font(28), fill=(32, 90, 200))
    return image


def flat_shapes() -> Image.Image:
    """Corners that must stay corners, and a hole that must stay a hole."""
    image = Image.new("RGB", (640, 640), (246, 243, 238))
    draw = ImageDraw.Draw(image)
    draw.rectangle([60, 60, 300, 300], fill=(22, 28, 38))
    draw.polygon([(360, 60), (580, 60), (470, 280)], fill=(210, 64, 58))
    draw.ellipse([60, 360, 300, 600], fill=(38, 140, 96))
    draw.ellipse([130, 430, 230, 530], fill=(246, 243, 238))
    draw.rectangle([360, 360, 580, 420], fill=(246, 178, 40))
    draw.rectangle([360, 460, 580, 480], fill=(80, 88, 100))
    draw.rectangle([360, 520, 580, 528], fill=(80, 88, 100))
    return image


def shaded_orb() -> Image.Image:
    """A gradient the tracer should answer with a gradient, not with bands."""
    size = 520
    grid_y, grid_x = np.mgrid[0:size, 0:size].astype(np.float32)
    radius = np.hypot(grid_x - size * 0.42, grid_y - size * 0.38)
    shade = np.clip(1.0 - radius / (size * 0.62), 0.0, 1.0)
    array = np.zeros((size, size, 3), dtype=np.uint8)
    array[:, :, 0] = np.clip(40 + shade * 190, 0, 255)
    array[:, :, 1] = np.clip(70 + shade * 150, 0, 255)
    array[:, :, 2] = np.clip(140 + shade * 100, 0, 255)
    disc = np.hypot(grid_x - size / 2, grid_y - size / 2) < size * 0.44
    canvas = np.full((size, size, 3), 244, dtype=np.uint8)
    canvas[disc] = array[disc]
    return Image.fromarray(canvas, "RGB")


def hairlines() -> Image.Image:
    """Strokes one and two pixels wide, which absorption likes to eat."""
    image = Image.new("RGB", (560, 560), (252, 252, 250))
    draw = ImageDraw.Draw(image)
    for index in range(12):
        offset = 40 + index * 40
        draw.line([(offset, 30), (offset, 260)], fill=(30, 34, 42), width=1 + index % 3)
        draw.line([(30, offset + 270), (530, offset + 270)], fill=(180, 60, 120), width=1 + index % 2)
    draw.arc([60, 300, 500, 540], start=200, end=340, fill=(20, 110, 180), width=2)
    return image


def texture() -> Image.Image:
    """Grain that should be discarded, over structure that should not."""
    generator = np.random.default_rng(11)
    size = 480
    grid_y, grid_x = np.mgrid[0:size, 0:size].astype(np.float32)
    base = 120 + 60 * np.sin(grid_x / 48.0) + 40 * np.cos(grid_y / 31.0)
    array = np.zeros((size, size, 3), dtype=np.uint8)
    for channel in range(3):
        noisy = base + generator.normal(0, 14, (size, size)) + channel * 18
        array[:, :, channel] = np.clip(noisy, 0, 255).astype(np.uint8)
    array[180:300, 180:300] = (18, 22, 30)
    return Image.fromarray(array, "RGB")


def cutout() -> Image.Image:
    """Alpha with a soft edge: the silhouette must not gain a halo."""
    size = 480
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse([70, 70, 410, 410], fill=(236, 92, 70, 255))
    draw.ellipse([150, 150, 250, 250], fill=(255, 236, 180, 255))
    draw.polygon([(240, 120), (380, 330), (120, 330)], fill=(48, 62, 110, 255))
    return image


IMAGES = {
    "poster-text": (poster, "png"),
    "flat-shapes": (flat_shapes, "png"),
    "shaded-orb": (shaded_orb, "png"),
    "hairlines": (hairlines, "png"),
    "texture": (texture, "png"),
    "alpha-cutout": (cutout, "png"),
}


def write(directory: Path) -> list[Path]:
    """Draw the corpus into ``directory`` and return the files, in order."""
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name, (draw, suffix) in IMAGES.items():
        path = directory / f"{name}.{suffix}"
        draw().save(path)
        written.append(path)
    return written


if __name__ == "__main__":
    import sys

    target = Path(sys.argv[1] if len(sys.argv) > 1 else "corpus")
    for path in write(target):
        print(path)
