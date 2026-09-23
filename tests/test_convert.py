"""End-to-end conversion behaviour."""

from __future__ import annotations

from pathlib import Path
import re
import xml.etree.ElementTree as ET
from collections import Counter

import numpy as np
import pytest
from PIL import Image, ImageDraw

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


def test_photo_preset_budgets_for_texture() -> None:
    """A photograph needs the room a flat icon does not.

    The median filter this preset used to rely on is gone: absorbing the
    least valuable regions discards grain, because grain is small *and*
    low-contrast, where a median pass took the eyelashes with it.  What keeps
    a textured image tractable now is the budget, so that is what is pinned.
    """
    photo = build("photo")
    assert photo["max_initial_regions"] > build("icon")["max_initial_regions"]
    assert photo["max_initial_regions"] <= 4000


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


def _mark_on_a_field(size: int = 160) -> np.ndarray:
    """A large pale field carrying one small, very dark mark."""
    image = np.zeros((size, size, 4), dtype=np.uint8)
    image[:, :, 3] = 255
    image[:, :, :3] = 235
    image[size // 2 : size // 2 + 8, size // 4 : size // 4 + 30] = (20, 20, 25, 255)
    return image


def test_a_small_dark_mark_survives_the_merge(tmp_path: Path) -> None:
    """Area-weighted merge costs used to dissolve thin dark detail for free.

    A thirty-by-eight mark against twenty-five thousand pale pixels barely
    moves a mean residual, so legs, eyes and lettering merged away; the cost
    is now what the join does to the worse-off side.
    """
    result = convert(_write(tmp_path, _mark_on_a_field()), tmp_path / "out.svg", measure=False)
    text = result.destination.read_text(encoding="utf-8")
    fills = re.findall(r'fill="#([0-9A-F]{6})"', text)
    darkest = min(int(value[0:2], 16) + int(value[2:4], 16) + int(value[4:6], 16) for value in fills)
    assert darkest < 200, f"the dark mark was merged away; darkest fill was #{min(fills)}"


def test_a_flat_region_is_painted_flat(tmp_path: Path) -> None:
    """Paint is fitted on a region's interior, not on its anti-aliased rim.

    Fitting across the blend with a neighbour reads the edge as shading and
    answers a uniform white counter inside a letter with a grey gradient.
    """
    size = 160
    image = np.zeros((size, size, 4), dtype=np.uint8)
    image[:, :, 3] = 255
    image[:, :, :3] = 20
    image[40:120, 40:120, :3] = 250
    # Soften the border so the square carries a genuine anti-aliased edge.
    source = Image.fromarray(image, "RGBA").resize((size * 2, size * 2), Image.BILINEAR)
    source = source.resize((size, size), Image.BILINEAR)
    path = tmp_path / "square.png"
    source.save(path)

    result = convert(path, tmp_path / "out.svg", measure=False)
    rendered = np.asarray(Image.open(path).convert("RGB"), dtype=np.int16)
    middle = rendered[70:90, 70:90]
    assert middle.min() > 200  # the fixture really is a pale flat square
    text = result.destination.read_text(encoding="utf-8")
    pale = re.findall(r'fill="#(F[0-9A-F]{5})"', text)
    assert pale, "the pale square lost its flat fill"


def test_a_two_colour_mark_traces_to_two_shapes(tmp_path: Path) -> None:
    """A black mark on white must not come back ringed with grey.

    Anti-aliasing along the silhouette used to cluster into slivers of its
    own — a plain mark traced to two hundred regions, ninety of them
    intermediate greys, and looked smudged at any magnification. The fade
    there belongs to the alpha channel, not to a shape.
    """
    drawn = Image.new("L", (1200, 1200), 255)
    pen = ImageDraw.Draw(drawn)
    pen.arc([150, 150, 1050, 1050], start=200, end=70, fill=0, width=120)
    pen.ellipse([520, 140, 740, 360], fill=0)
    source = tmp_path / "mark.png"
    drawn.resize((600, 600), Image.LANCZOS).convert("RGB").save(source)

    result = convert(source, tmp_path / "mark.svg", measure=False)
    assert result.regions <= 6, f"a two-colour mark became {result.regions} regions"

    text = result.destination.read_text(encoding="utf-8")
    fills = re.findall(r'fill="#([0-9A-F]{6})"', text)
    def tone(value: str) -> float:
        red, green, blue = (int(value[i:i + 2], 16) for i in (0, 2, 4))
        return red * 0.299 + green * 0.587 + blue * 0.114
    muddy = [value for value in fills if 40 <= tone(value) <= 215]
    assert not muddy, f"the mark is ringed with intermediate greys: {muddy}"
