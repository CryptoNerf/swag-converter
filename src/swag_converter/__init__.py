"""sw(a)g.converter — local, high-quality raster to vector conversion."""

from importlib.metadata import PackageNotFoundError, version as _distribution_version

try:
    # Read it from the installed distribution rather than repeating it here:
    # 0.2.0 shipped announcing itself as 0.1.0 because pyproject moved and
    # this line did not.
    __version__ = _distribution_version("swag-converter")
except PackageNotFoundError:  # running straight from a source tree
    __version__ = "0+unknown"

from .convert import Result, convert  # noqa: E402
from .presets import PRESETS, QUALITY  # noqa: E402

__all__ = ["convert", "Result", "PRESETS", "QUALITY", "__version__"]
