"""Content classification."""

from __future__ import annotations

import numpy as np
import pytest

from swag_converter.analyze import analyze


def _flat_art(size: int = 128) -> np.ndarray:
    """Crisp shapes on a flat ground: what a logo looks like to the analyser."""
    image = np.zeros((size, size, 4), dtype=np.uint8)
    image[:, :, 3] = 255
    image[:, :, :3] = (240, 240, 245)
    image[20:70, 20:70, :3] = (220, 40, 40)
    image[60:110, 55:110, :3] = (40, 90, 220)
    return image


def _gradient_art(size: int = 128) -> np.ndarray:
    grid_y, grid_x = np.mgrid[0:size, 0:size]
    image = np.zeros((size, size, 4), dtype=np.uint8)
    image[:, :, 3] = 255
    image[:, :, 0] = (grid_x / size * 255).astype(np.uint8)
    image[:, :, 1] = (grid_y / size * 255).astype(np.uint8)
    image[:, :, 2] = 180
    return image


def _noisy_photo(size: int = 128, seed: int = 0) -> np.ndarray:
    """Smooth structure plus fine grain — the thing a median cannot remove."""
    generator = np.random.default_rng(seed)
    grid_y, grid_x = np.mgrid[0:size, 0:size]
    base = 128 + 60 * np.sin(grid_x / 9.0) + 40 * np.cos(grid_y / 7.0)
    image = np.zeros((size, size, 4), dtype=np.uint8)
    image[:, :, 3] = 255
    for channel in range(3):
        noisy = base + generator.normal(0, 26, (size, size))
        image[:, :, channel] = np.clip(noisy, 0, 255).astype(np.uint8)
    return image


def test_flat_artwork_is_not_called_photographic() -> None:
    result = analyze(_flat_art())
    assert result.kind in ("icon", "illustration")
    assert not result.is_photographic


def test_smooth_gradient_art_is_not_called_photographic() -> None:
    """Abstract gradient wallpapers were the classifier's main false positive."""
    result = analyze(_gradient_art())
    assert not result.is_photographic


def test_grainy_content_is_detected_as_photographic() -> None:
    result = analyze(_noisy_photo())
    assert result.is_photographic
    assert result.texture > 1.6


def test_texture_separates_grain_from_clean_edges() -> None:
    """A median filter removes grain but keeps an edge; that is the signal."""
    assert analyze(_noisy_photo()).texture > analyze(_flat_art()).texture * 3


def test_fully_transparent_input_is_handled() -> None:
    result = analyze(np.zeros((32, 32, 4), dtype=np.uint8))
    assert result.kind == "illustration"
    assert result.confidence == 0.0
