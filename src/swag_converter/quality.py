"""How close the SVG is to the raster it came from.

The SVG is rendered back to pixels and compared, so the score reflects what a
viewer will actually see rather than anything internal to the tracer.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np


#: Where a system cairo usually lives, plus the bundle when frozen.
_LIBRARY_DIRECTORIES = ("/opt/homebrew/lib", "/usr/local/lib", "/usr/lib")
_cairo_search_installed = False


def _make_cairo_findable() -> None:
    """Point cairocffi at a cairo the dynamic loader will not find on its own.

    macOS reads ``DYLD_FALLBACK_LIBRARY_PATH`` when a process starts, so
    setting it from inside one is theatre.  cairocffi asks
    ``ctypes.util.find_library`` first and opens whatever absolute path comes
    back, which is something we *can* answer at runtime — including from
    inside an app bundle, where the loader has never heard of Homebrew.
    """
    global _cairo_search_installed
    if _cairo_search_installed or os.name == "nt":
        return
    _cairo_search_installed = True

    import ctypes.util
    import glob
    import re
    import sys

    directories = [getattr(sys, "_MEIPASS", ""), *_LIBRARY_DIRECTORIES]
    directories = [path for path in directories if path and Path(path).is_dir()]
    if not directories:
        return
    original = ctypes.util.find_library

    def find_library(name: str) -> str | None:
        found = original(name)
        if found:
            return found
        # cairocffi asks for "cairo-2"; the file is libcairo.2.dylib.
        stem = re.sub(r"[-.]?\d+$", "", name)
        stem = stem[3:] if stem.startswith("lib") else stem
        for directory in directories:
            for pattern in (f"lib{stem}.*.dylib", f"lib{stem}.dylib", f"lib{stem}.so.*"):
                matches = sorted(glob.glob(str(Path(directory) / pattern)))
                if matches:
                    return matches[0]
        return None

    ctypes.util.find_library = find_library


def _render(svg: Path, width: int, height: int) -> np.ndarray | None:
    try:
        _make_cairo_findable()
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
