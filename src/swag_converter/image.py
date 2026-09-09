"""Loading arbitrary raster input into the one shape the tracer accepts.

Everything downstream assumes straight-alpha RGBA at a size the segmenter can
afford.  Real files are none of those things: they arrive as JPEG without an
alpha channel, as 16-bit or CMYK or palette images, rotated by an EXIF tag, or
at 6000 px on the long edge.  This module is where all of that is normalised.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

# Pillow refuses very large files by default as a decompression-bomb guard.
# Raise it rather than remove it, and surface an error instead of crashing.
Image.MAX_IMAGE_PIXELS = 256_000_000

SUPPORTED_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff",
    ".ppm", ".pgm", ".tga", ".ico", ".jfif", ".avif", ".heic", ".heif",
}


@dataclass
class LoadedImage:
    pixels: np.ndarray          # RGBA uint8, straight alpha
    source_size: tuple[int, int]
    working_size: tuple[int, int]
    scale: float                # working / source
    had_alpha: bool
    background_removed: bool
    path: Path

    @property
    def width(self) -> int:
        return self.pixels.shape[1]

    @property
    def height(self) -> int:
        return self.pixels.shape[0]

    @property
    def downscaled(self) -> bool:
        return self.scale < 0.999


def load(
    path: Path,
    max_edge: int = 1024,
    remove_background: str = "auto",
    background_tolerance: float = 12.0,
) -> LoadedImage:
    """Read ``path`` as straight-alpha RGBA, resized to fit ``max_edge``."""
    with Image.open(path) as opened:
        opened = ImageOps.exif_transpose(opened) or opened
        had_alpha = opened.mode in ("RGBA", "LA", "PA") or "transparency" in opened.info
        image = opened.convert("RGBA")
        source_size = (image.width, image.height)

        longest = max(image.width, image.height)
        scale = 1.0
        if max_edge and longest > max_edge:
            scale = max_edge / longest
            image = image.resize(
                (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                Image.Resampling.LANCZOS,
            )
        pixels = np.asarray(image, dtype=np.uint8).copy()

    removed = False
    if remove_background == "always" or (remove_background == "auto" and not had_alpha):
        removed = _drop_flat_background(pixels, background_tolerance)

    return LoadedImage(
        pixels=pixels,
        source_size=source_size,
        working_size=(pixels.shape[1], pixels.shape[0]),
        scale=scale,
        had_alpha=had_alpha,
        background_removed=removed,
        path=path,
    )


def _drop_flat_background(pixels: np.ndarray, tolerance: float) -> bool:
    """Clear a uniform border colour to transparent, edge-connected only.

    Only pixels reachable from the border are cleared, so a white shirt in the
    middle of a logo survives while the white sheet behind it does not.  If the
    border is not uniform the image is left untouched.
    """
    from scipy import ndimage

    rgb = pixels[:, :, :3].astype(np.int16)
    border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
    reference = np.median(border, axis=0)
    if float(np.abs(border - reference).mean()) > tolerance:
        return False  # busy border: this is not a flat backdrop
    close = (np.abs(rgb - reference).max(axis=2) <= tolerance) & (pixels[:, :, 3] > 0)
    if not close.any():
        return False
    labels, count = ndimage.label(close)
    if count == 0:
        return False
    edge_labels = set(labels[0]) | set(labels[-1]) | set(labels[:, 0]) | set(labels[:, -1])
    edge_labels.discard(0)
    if not edge_labels:
        return False
    background = np.isin(labels, list(edge_labels))
    covered = float(background.mean())
    if covered < 0.02 or covered > 0.97:
        return False  # nothing to gain, or it would erase the whole picture
    pixels[background, 3] = 0
    return True
