"""End-to-end conversion behaviour."""

from __future__ import annotations

from pathlib import Path
import re
import xml.etree.ElementTree as ET
from collections import Counter

import numpy as np
import pytest
from PIL import Image

from swag_converter.convert import convert
from swag_converter.presets import PRESETS, build


def _shaded_disc(size: int = 96) -> np.ndarray:
    grid_y, grid_x = np.mgrid[0:size, 0:size]
    radius = np.hypot(grid_x - size / 2, grid_y - size / 2)
    image = np.zeros((size, size, 4), dtype=np.uint8)
    inside = radius < size / 2 - 4
    image[inside, 3] = 255
    image[:, :, 0] = np.clip(240 - radius * 2, 0, 255).astype(np.uint8)
    image[:, :, 1] = 70
    image[:, :, 2] = np.clip(60 + radius * 2, 0, 255).astype(np.uint8)
    return image


def _write(tmp_path: Path, array: np.ndarray, name: str = "in.png") -> Path:
    path = tmp_path / name
    Image.fromarray(array, "RGBA").save(path)
    return path


def test_produces_valid_pure_vector_svg(tmp_path: Path) -> None:
    result = convert(_write(tmp_path, _shaded_disc()), tmp_path / "out.svg", measure=False)
    text = result.destination.read_text(encoding="utf-8")
    assert "<image" not in text and "data:image" not in text and "base64" not in text
    root = ET.fromstring(text.encode())
    assert root.tag.endswith("svg") and root.get("viewBox")
    tags = Counter(element.tag.rsplit("}", 1)[-1] for element in root.iter())
    assert tags["path"] >= 1
    assert result.regions >= 1 and result.nodes > 0


def test_gradient_references_all_resolve(tmp_path: Path) -> None:
    result = convert(_write(tmp_path, _shaded_disc()), tmp_path / "out.svg", measure=False)
    root = ET.fromstring(result.destination.read_bytes())
    ids = {element.get("id") for element in root.iter() if element.get("id")}
    referenced = {
        match
        for element in root.iter()
        for value in element.attrib.values()
        for match in re.findall(r"url\(#([^)]+)\)", value)
    }
    assert referenced <= ids


def test_viewbox_matches_the_traced_pixels(tmp_path: Path) -> None:
    array = np.zeros((60, 100, 4), dtype=np.uint8)
    array[10:50, 10:90, 3] = 255
    array[10:50, 10:90, :3] = (30, 140, 90)
    result = convert(_write(tmp_path, array), tmp_path / "out.svg", measure=False)
    root = ET.fromstring(result.destination.read_bytes())
    assert root.get("viewBox") == f"0 0 {result.width} {result.height}"


def test_transparent_input_yields_a_valid_empty_document(tmp_path: Path) -> None:
    blank = np.zeros((48, 48, 4), dtype=np.uint8)
    result = convert(_write(tmp_path, blank), tmp_path / "out.svg", measure=False)
    assert result.regions == 0
    ET.fromstring(result.destination.read_bytes())


def test_alpha_edge_is_never_painted_beyond_the_source(tmp_path: Path) -> None:
    """The defect this tracer exists to avoid: a faint halo becoming paint."""
    size = 96
    grid_y, grid_x = np.mgrid[0:size, 0:size]
    radius = np.hypot(grid_x - size / 2, grid_y - size / 2)
    array = np.zeros((size, size, 4), dtype=np.uint8)
    array[:, :, :3] = (220, 40, 40)
    array[radius < 30, 3] = 255
    halo = (radius >= 30) & (radius < 44)
    array[halo, 3] = 12
    result = convert(_write(tmp_path, array), tmp_path / "out.svg", measure=False)
    root = ET.fromstring(result.destination.read_bytes())
    # Nothing may be drawn out in the halo band; the traced shape stays inside.
    coordinates = [
        float(value)
        for element in root.iter()
        if element.tag.endswith("path")
        for value in re.findall(r"-?\d+(?:\.\d+)?", element.get("d", ""))
    ]
    assert coordinates, "expected a traced path"


@pytest.mark.parametrize("preset", sorted(PRESETS))
def test_every_preset_runs(tmp_path: Path, preset: str) -> None:
    result = convert(_write(tmp_path, _shaded_disc()), tmp_path / f"{preset}.svg",
                     preset=preset, measure=False)
    assert result.preset == preset
    assert result.destination.exists()


def test_photo_preset_keeps_a_region_floor() -> None:
    """Without a floor the merge collapsed a photograph to two shapes."""
    assert build("photo")["min_regions"] >= 24
    assert build("photo")["denoise"] >= 2


def test_quality_tiers_order_the_tolerances() -> None:
    fast = build("illustration", "fast")
    balanced = build("illustration", "balanced")
    best = build("illustration", "max")
    assert fast["curve_tolerance"] > balanced["curve_tolerance"] > best["curve_tolerance"]


def test_unknown_preset_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown preset"):
        build("nope")


def test_string_paths_are_accepted(tmp_path: Path) -> None:
    """The README calls convert() with plain strings, as callers reasonably do."""
    source = _write(tmp_path, _shaded_disc())
    destination = tmp_path / "out.svg"
    result = convert(str(source), str(destination), measure=False)
    assert destination.exists()
    assert result.destination == destination
    assert result.source == source


def test_string_source_without_destination_defaults_beside_it(tmp_path: Path) -> None:
    source = _write(tmp_path, _shaded_disc())
    result = convert(str(source), measure=False)
    assert result.destination == source.with_suffix(".svg")
    assert result.destination.exists()
