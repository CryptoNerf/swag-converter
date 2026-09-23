"""Interface text, per language.

Each language is one flat JSON file of key to sentence.  The page asks for a
whole dictionary once and renders from it, so switching language is a redraw
rather than a reload, and a translator only ever touches JSON.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent / "locales"
FALLBACK = "en"


def available() -> list[dict[str, str]]:
    """Every language that has a file, with the name it calls itself."""
    found = []
    for path in sorted(HERE.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        found.append({"code": path.stem, "name": data.get("language.name", path.stem)})
    return found


def codes() -> list[str]:
    return [entry["code"] for entry in available()]


def strings(code: str) -> dict[str, str]:
    """The dictionary for ``code``, with English filling any gap.

    A half-finished translation should show English where it is unfinished,
    never a bare key or an empty control.
    """
    base = _read(FALLBACK)
    if code != FALLBACK:
        base.update(_read(code))
    return base


def _read(code: str) -> dict[str, str]:
    path = HERE / f"{code}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {key: str(value) for key, value in data.items()} if isinstance(data, dict) else {}
