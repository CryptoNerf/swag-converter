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


def test_compression_noise_does_not_inflate_the_colour_count() -> None:
    """A JPEG spreads one flat fill across dozens of distinct values.

    Counting those told the tracer a lettering poster in three colours held
    two hundred and sixty-five, so it spent twenty-two clusters and put the
    spare ones on the ramp along every edge. Covering balls answer the
    question that was meant: how few colours is the picture drawn in.
    """
    import io

    from PIL import Image

    from swag_converter.analyze import analyze

    flat = np.zeros((240, 240, 4), dtype=np.uint8)
    flat[:, :, 3] = 255
    flat[:, :, :3] = (244, 206, 74)
    flat[60:180, 40:200, :3] = (38, 42, 58)

    buffer = io.BytesIO()
    Image.fromarray(flat, "RGBA").convert("RGB").save(buffer, format="JPEG", quality=72)
    buffer.seek(0)
    noisy = np.asarray(Image.open(buffer).convert("RGBA"), dtype=np.uint8)

    clean = analyze(flat)
    compressed = analyze(noisy)

    assert clean.carrying_colors <= 4
    assert compressed.carrying_colors <= 6, (
        f"compression inflated the count to {compressed.carrying_colors}"
    )
    assert compressed.unique_colors > 50, "the fixture is not actually noisy"


def test_a_gradient_still_needs_many_colours() -> None:
    """The ceiling must not starve a ramp, which genuinely holds many."""
    from swag_converter.analyze import analyze

    size = 256
    ramp = np.zeros((size, size, 4), dtype=np.uint8)
    ramp[:, :, 3] = 255
    across = np.linspace(0, 255, size, dtype=np.uint8)
    ramp[:, :, 0] = across[None, :]
    ramp[:, :, 1] = across[:, None]
    ramp[:, :, 2] = 128

    assert analyze(ramp).carrying_colors >= 8
