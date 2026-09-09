"""The conversion pipeline: raster in, SVG plus a quality report out."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import time
from typing import Any

import numpy as np

from .analyze import Analysis, analyze
from .image import LoadedImage, load
from .presets import adapt_to_size, build
from .vector.render import render_svg_document
from .vector.segment import build_segmentation, merge_regions


@dataclass
class Result:
    source: Path
    destination: Path
    svg: str
    analysis: Analysis
    preset: str
    quality: str
    regions: int
    gradients: int
    nodes: int
    svg_bytes: int
    source_bytes: int
    width: int
    height: int
    seconds: float
    downscaled: bool
    background_removed: bool
    source_size: tuple[int, int]
    similarity: float | None = None
    warnings: list[str] = field(default_factory=list)


def _denoise(pixels: np.ndarray, size: int) -> np.ndarray:
    """Edge-preserving median pass; texture is what explodes the region count."""
    if size < 2:
        return pixels
    from scipy import ndimage

    smoothed = pixels.copy()
    for channel in range(3):
        smoothed[:, :, channel] = ndimage.median_filter(pixels[:, :, channel], size=size)
    return smoothed


def convert(
    source: Path,
    destination: Path | None = None,
    preset: str = "auto",
    quality: str = "balanced",
    max_edge: int = 1024,
    remove_background: str = "auto",
    measure: bool = True,
    overrides: dict[str, Any] | None = None,
    progress: Any = None,
) -> Result:
    """Trace one raster file into an SVG document."""
    started = time.perf_counter()
    step = progress or (lambda _label: None)

    step("reading")
    image: LoadedImage = load(source, max_edge=max_edge, remove_background=remove_background)

    step("analysing")
    analysis = analyze(image.pixels)
    chosen = analysis.kind if preset == "auto" else preset

    settings = build(chosen, quality, overrides)
    settings = adapt_to_size(settings, image.width, image.height)

    pixels = image.pixels
    if int(settings.get("denoise", 0)) > 1:
        step("denoising")
        pixels = _denoise(pixels, int(settings["denoise"]))

    step("segmenting")
    segmentation = build_segmentation(pixels, settings)

    step("merging regions")
    threshold = float(settings["merge_tolerance"])
    snapshots = dict(merge_regions(segmentation, settings, [threshold]))
    regions = snapshots.get(threshold, segmentation.regions)

    step("fitting curves")
    document, _ = render_svg_document(segmentation, regions, settings)

    warnings: list[str] = []
    if analysis.is_photographic and chosen not in ("photo", "poster"):
        warnings.append(
            f"content looks photographic but preset '{chosen}' is in use; "
            "expect a large file and a long run"
        )
    if image.downscaled:
        warnings.append(
            f"resized {image.source_size[0]}x{image.source_size[1]} to "
            f"{image.width}x{image.height} (--max-edge to change)"
        )

    destination = destination or source.with_suffix(".svg")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(document, encoding="utf-8")

    similarity = None
    if measure:
        step("scoring")
        from .quality import similarity_score

        similarity = similarity_score(pixels, destination)

    return Result(
        source=source,
        destination=destination,
        svg=document,
        analysis=analysis,
        preset=chosen,
        quality=quality,
        regions=len(regions),
        gradients=sum(1 for region in regions if region.paint and region.paint.kind != "flat"),
        nodes=_count_nodes(document),
        svg_bytes=destination.stat().st_size,
        source_bytes=source.stat().st_size,
        width=image.width,
        height=image.height,
        seconds=time.perf_counter() - started,
        downscaled=image.downscaled,
        background_removed=image.background_removed,
        source_size=image.source_size,
        similarity=similarity,
        warnings=warnings,
    )


def _count_nodes(document: str) -> int:
    import re

    return sum(len(re.findall(r"[MmZzLlHhVvCcSsQqTtAa]", match)) for match in re.findall(r'\sd="([^"]*)"', document))
