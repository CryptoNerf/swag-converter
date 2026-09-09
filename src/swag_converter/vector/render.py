"""Assembly of traced regions into one SVG document.

Regions are painted largest first, each bleeding a fraction of a pixel under
the next so neighbouring paths cannot leave an anti-aliasing hairline between
them, and every gradient stays inside its own translated group because
``userSpaceOnUse`` resolves in the coordinate system of the referencing path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from scipy import ndimage

from .curves import mask_bounds, trace_mask
from .paint import Paint, paint_attributes, paint_definition, to_hex
from .segment import Region, Segmentation, build_segmentation, merge_regions


SVG_OPEN = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:g} {h:g}" width="{w:g}" height="{h:g}">'
)


def _region_mask(segmentation: Segmentation, pixels: np.ndarray, overlap: int) -> np.ndarray:
    hi_height = segmentation.height * segmentation.scale
    hi_width = segmentation.width * segmentation.scale
    mask = np.zeros(hi_height * hi_width, dtype=bool)
    mask[segmentation.inside_indices[pixels]] = True
    mask = mask.reshape(hi_height, hi_width)
    if overlap > 0:
        # Neighbouring paths that meet exactly leave an anti-aliasing hairline.
        # A sub-pixel bleed under the next path removes it; the silhouette is
        # never crossed, so the outer edge stays exact.
        mask = ndimage.binary_dilation(mask, iterations=overlap) & segmentation.silhouette
    return mask


def render_svg_document(
    segmentation: Segmentation, regions: list[Region], settings: dict[str, Any]
) -> tuple[str, int]:
    scale = segmentation.scale
    tolerance = float(settings.get("curve_tolerance", 0.4))
    corner_angle = float(settings.get("corner_angle", 62.0))
    smooth_passes = int(settings.get("smooth_passes", 3))
    precision = int(settings.get("precision", 2))
    min_path_area = float(settings.get("min_path_area", 0.6))
    overlap = int(settings.get("seam_overlap", 1))
    base_layer = bool(settings.get("base_layer", True)) and not segmentation.translucent

    definitions: list[str] = []
    body: list[tuple[str, str, tuple[float, float, float, float]]] = []
    gradient_index = 0
    total_nodes = 0

    if base_layer and regions:
        data, nodes = trace_mask(
            segmentation.silhouette, scale, tolerance, min_path_area, precision, corner_angle, smooth_passes
        )
        if data:
            # Painted under everything purely so an anti-aliasing hairline shows
            # the dominant colour rather than the page behind it.
            largest = max(regions, key=lambda region: region.area)
            color = segmentation.colors[largest.pixels].mean(axis=0)
            body.append((f' fill="{to_hex(color)}"', data, _bounds(segmentation.silhouette, scale)))
            total_nodes += nodes

    for region in sorted(regions, key=lambda item: -item.area):
        mask = _region_mask(segmentation, region.pixels, overlap)
        data, nodes = trace_mask(mask, scale, tolerance, min_path_area, precision, corner_angle, smooth_passes)
        if not data:
            continue
        paint: Paint = region.paint
        identifier = None
        if paint.kind != "flat":
            identifier = f"g{gradient_index}"
            gradient_index += 1
            definition = paint_definition(paint, identifier)
            if definition:
                definitions.append(definition)
        body.append((paint_attributes(paint, identifier), data, _bounds(mask, scale)))
        total_nodes += nodes

    parts = [SVG_OPEN.format(w=segmentation.width, h=segmentation.height)]
    if definitions:
        parts.append("<defs>" + "".join(definitions) + "</defs>")
    for attributes, data in _coalesce(body):
        parts.append(f'<path{attributes} fill-rule="evenodd" d="{data}"/>')
    parts.append("</svg>")
    return "".join(parts), total_nodes


def _bounds(mask: np.ndarray, scale: float) -> tuple[float, float, float, float]:
    box = mask_bounds(mask)
    if box is None:
        return (0.0, 0.0, 0.0, 0.0)
    top, bottom, left, right = box
    return (left / scale, top / scale, right / scale, bottom / scale)


def _overlaps(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    # A half-pixel margin covers the seam bleed applied to every region mask.
    return not (
        a[2] + 0.5 <= b[0] or b[2] + 0.5 <= a[0] or a[3] + 0.5 <= b[1] or b[3] + 0.5 <= a[1]
    )


def _coalesce(
    body: list[tuple[str, str, tuple[float, float, float, float]]]
) -> list[tuple[str, str]]:
    """Fold identical flat fills into one element where paint order allows.

    Two sub-paths in a single evenodd element cancel where they overlap, and
    hoisting a later path earlier can bury it under something drawn between
    the two.  So a merge needs both shapes disjoint *and* nothing overlapping
    painted in between.
    """
    merged: list[list] = []
    for attributes, data, box in body:
        target = None
        if "url(#" not in attributes:
            for index in range(len(merged) - 1, -1, -1):
                candidate = merged[index]
                if candidate[0] == attributes and not any(_overlaps(box, other) for other in candidate[2]):
                    target = candidate
                    break
                if _overlaps(box, candidate[3]):
                    break  # something opaque sits in between; stop looking back
        if target is None:
            merged.append([attributes, data, [box], box])
        else:
            target[1] += data
            target[2].append(box)
            target[3] = (
                min(target[3][0], box[0]), min(target[3][1], box[1]),
                max(target[3][2], box[2]), max(target[3][3], box[3]),
            )
    return [(entry[0], entry[1]) for entry in merged]


def _thresholds(presets: list[tuple[str, dict[str, Any]]]) -> list[float]:
    return [float(settings.get("merge_tolerance", 0.8)) for _, settings in presets]


def vectorize_variants(
    source: Path, presets: list[tuple[str, dict[str, Any]]], destination_dir: Path
) -> list[tuple[str, Path, dict[str, Any]]]:
    """Produce one SVG per preset from a single shared segmentation."""
    with Image.open(source) as opened:
        rgba = np.asarray(opened.convert("RGBA"))
    # Segmentation options are shared by every preset (only the merge and
    # curve tolerances vary), so one pass serves them all.
    base_settings = presets[0][1] if presets else {}
    segmentation = build_segmentation(rgba, base_settings)
    thresholds = _thresholds(presets)
    merge_settings = dict(base_settings)
    merge_settings.pop("merge_tolerance", None)
    snapshots = {value: regions for value, regions in merge_regions(segmentation, merge_settings, thresholds)}

    destination_dir.mkdir(parents=True, exist_ok=True)
    results: list[tuple[str, Path, dict[str, Any]]] = []
    for (name, settings), threshold in zip(presets, thresholds):
        regions = snapshots.get(threshold, segmentation.regions)
        document, nodes = render_svg_document(segmentation, regions, settings)
        path = destination_dir / f"{name}.svg"
        path.write_text(document, encoding="utf-8")
        results.append(
            (
                name,
                path,
                {
                    "merge_tolerance": threshold,
                    "regions": len(regions),
                    "gradients": sum(1 for region in regions if region.paint and region.paint.kind != "flat"),
                    "curve_tolerance": float(settings.get("curve_tolerance", 0.4)),
                    "supersample": segmentation.scale,
                    "translucent": segmentation.translucent,
                    "estimated_nodes": nodes,
                },
            )
        )
    return results
