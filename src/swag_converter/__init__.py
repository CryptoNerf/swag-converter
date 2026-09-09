"""sw(a)g.converter — local, high-quality raster to vector conversion."""

__version__ = "0.1.0"

from .convert import Result, convert  # noqa: E402
from .presets import PRESETS, QUALITY  # noqa: E402

__all__ = ["convert", "Result", "PRESETS", "QUALITY", "__version__"]
