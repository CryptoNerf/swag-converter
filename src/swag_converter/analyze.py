"""Tell flat artwork from photographs, and pick a preset accordingly.

The tracer's cost is driven by how many colour regions an image starts with,
and that is a property of content rather than size: a logo has a handful of
flat areas, a photograph has texture in every pixel.

The signal that actually separates them is *fine detail that survives a
median filter*.  A clean edge does not - the median keeps it - so drawings
and gradient art score low no matter how colourful they are.  Photographic
grain does survive, because there is no single "correct" value to snap to.
Measured over a mixed set of app icons, abstract wallpapers and photographs,
the textured photograph scored 2.7 while the busiest piece of abstract art
reached 1.4.

The bias is deliberately towards ``illustration``: with the merge stage on a
priority queue a misread photograph costs seconds, whereas treating artwork
as a photograph visibly softens edges that should stay crisp.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage


#: Fine-detail level above which content reads as photographic.
PHOTO_TEXTURE = 1.6
#: ...provided the detail is spread out rather than concentrated on edges.
PHOTO_SPREAD = 0.35


@dataclass
class Analysis:
    kind: str                 # icon | illustration | photo
    texture: float            # fine detail surviving a median filter
    spread: float             # share of pixels carrying any fine detail
    flatness: float           # share of pixels in near-uniform areas
    unique_colors: int
    #: How few colours carry almost all of the picture.  Distinct from
    #: ``unique_colors``, which counts every shade including the blends along
    #: an edge: those are numerous and cover almost nothing.
    carrying_colors: int
    transparency: float
    confidence: float

    @property
    def is_photographic(self) -> bool:
        return self.kind == "photo"

    @property
    def summary(self) -> str:
        return (
            f"texture {self.texture:.2f}, flat {self.flatness:.0%}, "
            f"{self.unique_colors} colours"
        )


def _carrying_colors(
    rgb: np.ndarray, opaque: np.ndarray, radius: float = 38.0, share: float = 0.97
) -> int:
    """How few colours put almost every pixel within ``radius`` of one of them.

    Counting distinct values does not answer this.  A JPEG spreads one flat
    fill across dozens of them — the lettering that prompted this measure
    reported two hundred and sixty-five colours and is drawn in three — while
    a gradient genuinely needs many.  Covering balls tell the two apart:
    compression noise falls inside a ball, a ramp needs a new one every
    ``radius`` along its length.

    The count bounds how many clusters are worth spending.  Every cluster
    beyond what the picture holds lands on the blend between two colours,
    where it becomes a sliver along an edge.
    """
    if not opaque.any():
        return 0

    # Coarse bins first: greedy covering over a few hundred populated cells
    # rather than over every pixel.
    step = 8
    cells = (rgb[opaque] / step).astype(np.int32)
    levels = 256 // step
    packed = (cells[:, 0] * levels + cells[:, 1]) * levels + cells[:, 2]
    unique, counts = np.unique(packed, return_counts=True)
    blue = unique % levels
    green = (unique // levels) % levels
    red = unique // (levels * levels)
    centres = np.stack([red, green, blue], axis=1).astype(np.float32) * step + step / 2.0

    total = counts.sum()
    claimed = np.zeros(len(unique), dtype=bool)
    covered = 0.0
    taken = 0
    while covered < share * total and not claimed.all():
        candidate = int(np.argmax(np.where(claimed, -1, counts)))
        reach = np.linalg.norm(centres - centres[candidate], axis=1) <= radius
        newly = reach & ~claimed
        claimed |= newly
        covered += counts[newly].sum()
        taken += 1
    return taken


def analyze(pixels: np.ndarray, sample_edge: int = 256) -> Analysis:
    height, width = pixels.shape[:2]
    step = max(1, int(max(height, width) / sample_edge))
    sample = pixels[::step, ::step]
    rgb = sample[:, :, :3].astype(np.float32)
    alpha = sample[:, :, 3].astype(np.float32) / 255.0

    opaque = alpha > 0.5
    transparency = float((alpha < 0.99).mean())
    if opaque.sum() < 32:
        return Analysis("illustration", 0.0, 0.0, 1.0, 0, 0, transparency, 0.0)

    luma = rgb @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    contrast = ndimage.maximum_filter(luma, size=3) - ndimage.minimum_filter(luma, size=3)
    texture = float(np.abs(luma - ndimage.median_filter(luma, size=3))[opaque].mean())
    spread = float((contrast[opaque] > 6.0).mean())
    flatness = float((contrast[opaque] < 6.0).mean())

    quantised = rgb.astype(np.int32) >> 3
    packed = (quantised[:, :, 0] << 10) | (quantised[:, :, 1] << 5) | quantised[:, :, 2]
    unique_colors = int(np.unique(packed[opaque]).size)
    carrying_colors = _carrying_colors(rgb, opaque)

    if texture >= PHOTO_TEXTURE and spread >= PHOTO_SPREAD:
        kind = "photo"
        confidence = min(1.0, texture / (PHOTO_TEXTURE * 2))
    elif flatness > 0.6 and unique_colors < 900:
        kind = "icon"
        confidence = min(1.0, flatness)
    else:
        kind = "illustration"
        confidence = 0.5 + min(0.45, abs(texture - PHOTO_TEXTURE) / (PHOTO_TEXTURE * 2))
    return Analysis(kind, round(texture, 3), round(spread, 3), round(flatness, 3),
                    unique_colors, carrying_colors,
                    round(transparency, 3), round(confidence, 2))
