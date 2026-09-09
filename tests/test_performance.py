"""Guards on the cost of the merge stage.

A textured image starts with tens of thousands of regions.  With a linear
scan over the candidate edges a 256x256 photograph took 22 minutes and
collapsed to two shapes; these tests pin down both halves of that fix.
"""

from __future__ import annotations

from pathlib import Path
import time

import numpy as np
import pytest
from PIL import Image

from swag_converter.convert import convert


def _noise_image(tmp_path: Path, size: int = 192, seed: int = 5) -> Path:
    generator = np.random.default_rng(seed)
    grid_y, grid_x = np.mgrid[0:size, 0:size]
    base = 128 + 55 * np.sin(grid_x / 11.0) + 45 * np.cos(grid_y / 8.0)
    array = np.zeros((size, size, 4), dtype=np.uint8)
    array[:, :, 3] = 255
    for channel in range(3):
        array[:, :, channel] = np.clip(base + generator.normal(0, 30, (size, size)), 0, 255).astype(np.uint8)
    path = tmp_path / "noise.png"
    Image.fromarray(array, "RGBA").save(path)
    return path


@pytest.mark.slow
def test_textured_input_finishes_promptly(tmp_path: Path) -> None:
    source = _noise_image(tmp_path)
    started = time.perf_counter()
    result = convert(source, tmp_path / "out.svg", measure=False)
    elapsed = time.perf_counter() - started
    # Generous: the point is to catch a return of the quadratic blow-up,
    # not to police a few seconds either way.
    assert elapsed < 90, f"textured input took {elapsed:.0f}s"
    assert result.preset == "photo"


def test_merge_never_collapses_the_image_to_nothing(tmp_path: Path) -> None:
    result = convert(_noise_image(tmp_path), tmp_path / "out.svg", measure=False)
    assert result.regions >= 24, "the region floor keeps a photograph legible"


def test_large_input_cost_is_bounded_by_downscaling(tmp_path: Path) -> None:
    """A 4K image must not cost 16x a 1K one; the working size is capped."""
    generator = np.random.default_rng(1)
    array = np.zeros((1600, 2400, 4), dtype=np.uint8)
    array[:, :, 3] = 255
    grid_y, grid_x = np.mgrid[0:1600, 0:2400]
    for channel in range(3):
        array[:, :, channel] = np.clip(120 + 80 * np.sin(grid_x / 90.0 + channel), 0, 255).astype(np.uint8)
    path = tmp_path / "big.png"
    Image.fromarray(array, "RGBA").save(path)
    result = convert(path, tmp_path / "big.svg", max_edge=512, measure=False)
    assert max(result.width, result.height) == 512
    assert result.downscaled and result.source_size == (2400, 1600)
