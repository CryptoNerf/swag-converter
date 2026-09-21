"""Everything the page is allowed to ask Python to do.

Each method here is reachable from the page as ``pywebview.api.<name>``, so
this file is the whole attack surface of the window: it takes paths and
option names, never code, and it never reaches outside the files the user
chose or the directory they picked.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any

from ..image import SUPPORTED_SUFFIXES
from ..presets import PRESETS, QUALITY
from .jobs import Queue

#: Kept small deliberately: a thumbnail is a hint, not a preview.
THUMBNAIL = 320
#: A dropped file is read through the page, so it has to be bounded.
MAX_DROP_BYTES = 64 * 1024 * 1024

DEFAULTS: dict[str, Any] = {
    "preset": "auto",
    "quality": "balanced",
    "max_edge": 1024,
    "background": "auto",
    "measure": True,
    "output_dir": "",
}


class Bridge:
    def __init__(self) -> None:
        self.queue = Queue()
        self.settings = dict(DEFAULTS)
        self.window: Any = None
        self._scratch = Path(tempfile.mkdtemp(prefix="swag-gui-"))

    # -- what the page needs to draw itself --------------------------------

    def describe(self) -> dict[str, Any]:
        from .. import __version__

        return {
            "version": __version__,
            "presets": ["auto", *sorted(PRESETS)],
            "qualities": list(QUALITY),
            "suffixes": sorted(SUPPORTED_SUFFIXES),
            "settings": self.settings,
            "scoring": _scoring_available(),
        }

    def update_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        allowed = set(DEFAULTS)
        for key, value in patch.items():
            if key not in allowed:
                continue
            if key == "preset" and value not in ("auto", *PRESETS):
                continue
            if key == "quality" and value not in QUALITY:
                continue
            if key == "background" and value not in ("auto", "always", "keep"):
                continue
            if key == "max_edge":
                try:
                    value = max(0, min(8192, int(value)))
                except (TypeError, ValueError):
                    continue
            if key == "measure":
                value = bool(value)
            if key == "output_dir":
                value = str(value or "")
            self.settings[key] = value
        return self.settings

    # -- getting images in -------------------------------------------------

    def choose_images(self) -> list[dict[str, Any]]:
        import webview

        patterns = " ".join(f"*{suffix}" for suffix in sorted(SUPPORTED_SUFFIXES))
        picked = self.window.create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=True, file_types=(f"Images ({patterns})",)
        )
        return self._accept([Path(item) for item in picked or []])

    def choose_output_dir(self) -> str:
        import webview

        picked = self.window.create_file_dialog(webview.FOLDER_DIALOG)
        if picked:
            self.settings["output_dir"] = str(Path(picked[0]))
        return self.settings["output_dir"]

    def use_source_folder(self) -> str:
        self.settings["output_dir"] = ""
        return ""

    def accept_drop(self, name: str, payload: str) -> list[dict[str, Any]]:
        """Take a file the page read for us.

        The system webview will not tell a page where a dropped file lives, so
        the page reads the bytes and hands them over; we write them somewhere
        we control and treat it like any other input.
        """
        suffix = Path(name).suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            return []
        try:
            raw = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError):
            return []
        if not raw or len(raw) > MAX_DROP_BYTES:
            return []
        target = self._scratch / f"{os.urandom(6).hex()}{suffix}"
        target.write_bytes(raw)
        return self._accept([target], temporary=True)

    def _accept(self, paths: list[Path], temporary: bool = False) -> list[dict[str, Any]]:
        added = []
        for path in paths:
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
                continue
            job = self.queue.add(path, self._destination_for_path(path), temporary=temporary)
            added.append({**job.state(), "thumbnail": _thumbnail(path)})
        return added

    # -- running -----------------------------------------------------------

    def start(self) -> dict[str, Any]:
        options = {
            "preset": self.settings["preset"],
            "quality": self.settings["quality"],
            "max_edge": int(self.settings["max_edge"]),
            "remove_background": self.settings["background"],
            "measure": bool(self.settings["measure"]) and _scoring_available(),
        }
        self.queue.start(options, lambda job: self._destination_for_path(job.source))
        return self.poll()

    def cancel(self) -> dict[str, Any]:
        self.queue.cancel()
        return self.poll()

    def poll(self) -> dict[str, Any]:
        return {"busy": self.queue.busy, "jobs": [job.state() for job in self.queue.jobs()]}

    def remove(self, identifier: str) -> dict[str, Any]:
        self.queue.remove(identifier)
        return self.poll()

    def clear_finished(self) -> dict[str, Any]:
        self.queue.clear(finished_only=True)
        return self.poll()

    def clear_all(self) -> dict[str, Any]:
        self.queue.clear()
        return self.poll()

    # -- looking at the result ---------------------------------------------

    def preview(self, identifier: str) -> dict[str, Any]:
        job = self.queue.job(identifier)
        if job is None or job.status != "done":
            return {}
        destination = Path(job.destination)
        if not destination.is_file():
            return {}
        return {
            "id": identifier,
            "name": job.name,
            "svg": destination.read_text(encoding="utf-8"),
            "source": _data_url(job.source, limit=1400),
            "result": job.result,
        }

    def reveal(self, path: str) -> bool:
        target = Path(path)
        if not target.exists():
            return False
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", "-R", str(target)], check=False)
            elif os.name == "nt":
                subprocess.run(["explorer", "/select,", str(target)], check=False)
            else:
                subprocess.run(["xdg-open", str(target.parent)], check=False)
        except OSError:
            return False
        return True

    def open_path(self, path: str) -> bool:
        target = Path(path)
        if not target.exists():
            return False
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", str(target)], check=False)
            elif os.name == "nt":
                os.startfile(str(target))  # type: ignore[attr-defined]
            else:
                subprocess.run(["xdg-open", str(target)], check=False)
        except OSError:
            return False
        return True

    def save_copy(self, identifier: str) -> str:
        import webview

        job = self.queue.job(identifier)
        if job is None or job.status != "done":
            return ""
        picked = self.window.create_file_dialog(
            webview.SAVE_DIALOG, save_filename=Path(job.destination).name, file_types=("SVG (*.svg)",)
        )
        if not picked:
            return ""
        target = Path(picked if isinstance(picked, str) else picked[0])
        target.write_bytes(Path(job.destination).read_bytes())
        return str(target)

    # -- plumbing ----------------------------------------------------------

    def _destination_for_path(self, source: Path) -> Path:
        directory = self.settings.get("output_dir") or ""
        if directory:
            return Path(directory) / f"{source.stem}.svg"
        return source.with_suffix(".svg")

    def shutdown(self) -> None:
        self.queue.shutdown()
        for leftover in self._scratch.glob("*"):
            try:
                leftover.unlink()
            except OSError:
                pass
        try:
            self._scratch.rmdir()
        except OSError:
            pass


def _scoring_available() -> bool:
    """Whether the similarity score can be computed here."""
    try:
        from ..quality import _render  # noqa: F401
        import cairosvg  # noqa: F401
    except Exception:
        return False
    return True


def _thumbnail(path: Path, size: int = THUMBNAIL) -> str:
    return _data_url(path, limit=size)


def _data_url(path: Path, limit: int) -> str:
    """A downscaled PNG of ``path``, inline, so the page needs no file access."""
    from io import BytesIO

    from PIL import Image, ImageOps

    try:
        with Image.open(path) as opened:
            opened = ImageOps.exif_transpose(opened) or opened
            image = opened.convert("RGBA")
            image.thumbnail((limit, limit), Image.Resampling.LANCZOS)
            buffer = BytesIO()
            image.save(buffer, format="PNG", optimize=True)
    except Exception:
        return ""
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
