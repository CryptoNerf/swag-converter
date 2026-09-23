"""Settings that survive quitting.

Everything the window remembers lives in one small JSON file in the usual
per-user place.  It is written atomically and read defensively: a file that
has been hand-edited into nonsense must leave the window working, not stop
it from opening.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any


#: Overrides where settings live.  A test run or a portable install must be
#: able to keep its preferences somewhere other than the real user's.
HOME_VARIABLE = "SWAG_CONVERTER_HOME"


def config_directory() -> Path:
    """Where this platform expects an application to keep its settings."""
    override = os.environ.get(HOME_VARIABLE)
    if override:
        return Path(override)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "swag-converter"
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "swag-converter"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "swag-converter"


class Store:
    """A JSON file of preferences, keyed by name."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (config_directory() / "settings.json")
        self._values: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return  # absent, unreadable or corrupt: defaults it is
        if isinstance(raw, dict):
            self._values = raw

    def get(self, key: str, fallback: Any = None) -> Any:
        return self._values.get(key, fallback)

    def merged(self, defaults: dict[str, Any]) -> dict[str, Any]:
        """Defaults overlaid with whatever was remembered, keys of both kinds.

        Only keys the caller knows about survive, so a stale file cannot
        smuggle a setting the code no longer understands.
        """
        merged = dict(defaults)
        for key, value in self._values.items():
            if key in defaults and isinstance(value, type(defaults[key])):
                merged[key] = value
        return merged

    def update(self, values: dict[str, Any]) -> None:
        self._values.update(values)
        self.save()

    def save(self) -> None:
        """Write via a neighbouring temporary file, so a crash cannot truncate."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(self._values, stream, indent=2, ensure_ascii=False)
            os.replace(temporary, self.path)
        except OSError:
            pass  # a read-only home must not stop the window working
