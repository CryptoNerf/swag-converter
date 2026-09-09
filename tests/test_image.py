"""Input normalisation: formats, orientation, size and backgrounds."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from swag_converter.image import load


def _write(tmp_path: Path, array: np.ndarray, name: str, mode: str = "RGBA") -> Path:
    path = tmp_path / name
    Image.fromarray(array, mode).save(path)
    return path


def test_jpeg_without_alpha_loads_as_rgba(tmp_path: Path) -> None:
    rgb = np.full((40, 60, 3), 200, dtype=np.uint8)
    rgb[10:30, 20:40] = (30, 60, 200)
    path = _write(tmp_path, rgb, "flat.jpg", "RGB")
    loaded = load(path, remove_background="keep")
    assert loaded.pixels.shape == (40, 60, 4)
    assert not loaded.had_alpha
    assert (loaded.pixels[:, :, 3] == 255).all()


def test_palette_and_grayscale_are_accepted(tmp_path: Path) -> None:
    grey = np.tile(np.arange(64, dtype=np.uint8), (32, 1))
    path = _write(tmp_path, grey, "grey.png", "L")
    assert load(path, remove_background="keep").pixels.shape == (32, 64, 4)
    palette = Image.fromarray(np.zeros((16, 16, 3), dtype=np.uint8), "RGB").convert("P")
    palette_path = tmp_path / "p.png"
    palette.save(palette_path)
    assert load(palette_path, remove_background="keep").pixels.shape[2] == 4


def test_large_input_is_resized_to_the_edge_budget(tmp_path: Path) -> None:
    big = np.zeros((300, 900, 4), dtype=np.uint8)
    big[:, :, 3] = 255
    path = _write(tmp_path, big, "wide.png")
    loaded = load(path, max_edge=300)
    assert max(loaded.width, loaded.height) == 300
    assert loaded.downscaled
    assert loaded.source_size == (900, 300)


def test_max_edge_zero_keeps_the_original_size(tmp_path: Path) -> None:
    array = np.zeros((120, 200, 4), dtype=np.uint8)
    array[:, :, 3] = 255
    loaded = load(_write(tmp_path, array, "keep.png"), max_edge=0)
    assert (loaded.width, loaded.height) == (200, 120)
    assert not loaded.downscaled


def test_flat_border_is_cleared_but_interior_colour_survives(tmp_path: Path) -> None:
    """A white sheet behind a logo goes; a white shape inside it stays."""
    array = np.full((80, 80, 4), 255, dtype=np.uint8)
    array[20:60, 20:60, :3] = (200, 40, 40)   # red body
    array[35:45, 35:45, :3] = (255, 255, 255)  # white detail inside the body
    path = _write(tmp_path, array, "onwhite.png")
    loaded = load(path, remove_background="always")
    assert loaded.background_removed
    assert loaded.pixels[0, 0, 3] == 0, "border should be cleared"
    assert loaded.pixels[40, 40, 3] == 255, "enclosed white detail must survive"


def test_busy_border_is_left_alone(tmp_path: Path) -> None:
    generator = np.random.default_rng(3)
    array = np.zeros((60, 60, 4), dtype=np.uint8)
    array[:, :, :3] = generator.integers(0, 255, (60, 60, 3), dtype=np.uint8)
    array[:, :, 3] = 255
    loaded = load(_write(tmp_path, array, "busy.png"), remove_background="always")
    assert not loaded.background_removed


def test_exif_orientation_is_applied(tmp_path: Path) -> None:
    array = np.zeros((20, 40, 3), dtype=np.uint8)
    image = Image.fromarray(array, "RGB")
    path = tmp_path / "rotated.jpg"
    exif = image.getexif()
    exif[274] = 6  # rotate 90°
    image.save(path, exif=exif)
    loaded = load(path, remove_background="keep")
    # A 40x20 image tagged "rotate 90" must come back as 20x40.
    assert (loaded.width, loaded.height) == (20, 40)
