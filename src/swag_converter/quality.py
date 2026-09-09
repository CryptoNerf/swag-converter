"""How close the SVG is to the raster it came from.

The SVG is rendered back to pixels and compared, so the score reflects what a
viewer will actually see rather than anything internal to the tracer.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np


def _render(svg: Path, width: int, height: int) -> np.ndarray | None:
    try:
        if os.name == "posix":
            candidates = [path for path in ("/opt/homebrew/lib", "/usr/local/lib") if Path(path).exists()]
            if candidates and not os.environ.get("DYLD_FALLBACK_LIBRARY_PATH"):
                os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = ":".join(candidates)
        import cairosvg
        from io import BytesIO
        from PIL import Image

        raw = cairosvg.svg2png(url=str(svg), output_width=width, output_height=height)
        with Image.open(BytesIO(raw)) as opened:
            return np.asarray(opened.convert("RGBA"), dtype=np.float32)
    except Exception:
        return None  # scoring is optional; never fail a conversion over it


def similarity_score(pixels: np.ndarray, svg: Path) -> float | None:
    """SSIM over pre-multiplied colour, or None when no renderer is available."""
    height, width = pixels.shape[:2]
    rendered = _render(svg, width, height)
    if rendered is None:
        return None
    try:
        from skimage.metrics import structural_similarity
    except ImportError:
        return None
    original = pixels.astype(np.float32)
    source_alpha = original[:, :, 3:4] / 255.0
    result_alpha = rendered[:, :, 3:4] / 255.0
    try:
        return float(
            structural_similarity(
                original[:, :, :3] * source_alpha,
                rendered[:, :, :3] * result_alpha,
                channel_axis=2,
                data_range=255.0,
            )
        )
    except ValueError:
        return None
