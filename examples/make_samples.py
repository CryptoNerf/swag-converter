#!/usr/bin/env python3
"""Generate the sample images used by the README.

Everything here is drawn from scratch so the repository ships nothing whose
licence is not ours.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent


def _rgba(size: int) -> np.ndarray:
    return np.zeros((size, size, 4), dtype=np.uint8)


def badge(size: int = 320) -> np.ndarray:
    """A rounded badge with a radial sheen and a cut-out mark."""
    image = _rgba(size)
    grid_y, grid_x = np.mgrid[0:size, 0:size].astype(np.float32)
    centre = size / 2
    radius = np.hypot(grid_x - centre, grid_y - centre * 0.85)
    inside = np.maximum(np.abs(grid_x - centre), np.abs(grid_y - centre)) < size * 0.40
    corner = np.hypot(
        np.clip(np.abs(grid_x - centre) - size * 0.26, 0, None),
        np.clip(np.abs(grid_y - centre) - size * 0.26, 0, None),
    )
    inside &= corner < size * 0.14
    sheen = np.clip(1.0 - radius / (size * 0.75), 0, 1)
    image[:, :, 0] = np.clip(40 + sheen * 90, 0, 255).astype(np.uint8)
    image[:, :, 1] = np.clip(110 + sheen * 120, 0, 255).astype(np.uint8)
    image[:, :, 2] = np.clip(210 + sheen * 45, 0, 255).astype(np.uint8)
    image[inside, 3] = 255
    bar = (np.abs(grid_y - centre) < size * 0.045) & (np.abs(grid_x - centre) < size * 0.22)
    stem = (np.abs(grid_x - centre) < size * 0.045) & (grid_y > centre) & (grid_y < centre + size * 0.24)
    mark = (bar | stem) & inside
    for channel, value in enumerate((252, 253, 255)):
        image[:, :, channel][mark] = value
    return _antialias(image, inside)


def orb(size: int = 320) -> np.ndarray:
    """A shaded sphere: the case gradients were built for."""
    image = _rgba(size)
    grid_y, grid_x = np.mgrid[0:size, 0:size].astype(np.float32)
    centre = size / 2
    radius = np.hypot(grid_x - centre, grid_y - centre)
    inside = radius < size * 0.42
    highlight = np.hypot(grid_x - size * 0.38, grid_y - size * 0.34)
    light = np.clip(1.0 - highlight / (size * 0.62), 0, 1) ** 1.6
    depth = np.clip(1.0 - radius / (size * 0.42), 0, 1)
    image[:, :, 0] = np.clip(210 * light + 150 * depth + 20, 0, 255).astype(np.uint8)
    image[:, :, 1] = np.clip(180 * light + 40 * depth + 15, 0, 255).astype(np.uint8)
    image[:, :, 2] = np.clip(150 * light + 120 * depth + 60, 0, 255).astype(np.uint8)
    image[inside, 3] = 255
    return _antialias(image, inside)


def _antialias(image: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Soften the silhouette so the sample behaves like a real asset."""
    from scipy import ndimage

    alpha = ndimage.gaussian_filter(mask.astype(np.float32), 0.8)
    image[:, :, 3] = np.clip(alpha * 255, 0, 255).astype(np.uint8)
    return image


def main() -> None:
    for name, array in (("badge.png", badge()), ("orb.png", orb())):
        Image.fromarray(array, "RGBA").save(HERE / name)
        print(f"wrote {HERE / name}")


if __name__ == "__main__":
    main()
