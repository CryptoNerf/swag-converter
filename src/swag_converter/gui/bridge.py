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
from . import locales
from .jobs import Queue
from .store import Store
from .updater import Updater

#: Kept small deliberately: a thumbnail is a hint, not a preview.
THUMBNAIL = 320
#: A dropped file is read through the page, so it has to be bounded.
MAX_DROP_BYTES = 64 * 1024 * 1024

def image_filter() -> str:
    """The open dialog's filter, in the shape pywebview insists on.

    ``Description (*.a;*.b)`` — semicolons, no spaces.  Anything else is
    rejected before the dialog opens, which is how "Add images" came to do
    nothing at all rather than fail visibly.
    """
    patterns = ";".join(f"*{suffix}" for suffix in sorted(SUPPORTED_SUFFIXES))
    return f"Images ({patterns})"


DEFAULTS: dict[str, Any] = {
    "preset": "auto",
    "quality": "balanced",
    "max_edge": 1024,
    "background": "auto",
    "measure": True,
    "output_dir": "",
    "language": "en",
    "auto_update": True,
}

#: Settings worth carrying between launches.  The output folder is not one of
#: them by accident: it is where someone's last batch went, and they usually
#: want the next one in the same place.
REMEMBERED = ("preset", "quality", "max_edge", "background", "measure",
              "output_dir", "language", "auto_update")


class Bridge:
    def __init__(self, store: Store | None = None) -> None:
        self.queue = Queue()
        self.store = store if store is not None else Store()
        self.settings = self.store.merged(DEFAULTS)
        if self.settings["language"] not in locales.codes():
            self.settings["language"] = locales.FALLBACK
        self.window: Any = None
        self._scratch = Path(tempfile.mkdtemp(prefix="swag-gui-"))

        from .. import __version__

        self.updater = Updater(__version__, self.store)

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
            "languages": locales.available(),
            "strings": locales.strings(self.settings["language"]),
            # So the page can refuse an oversized drop before reading it into
            # memory and base64-ing it across the bridge.
            "max_drop_bytes": MAX_DROP_BYTES,
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
            if key == "language" and value not in locales.codes():
                continue
            if key == "auto_update":
                value = bool(value)
            self.settings[key] = value
        self.store.update({key: self.settings[key] for key in REMEMBERED})
        return self.settings

    # -- updates -----------------------------------------------------------

    def update_status(self) -> dict[str, Any]:
        return self.updater.status()

    def check_for_update(self) -> dict[str, Any]:
        """Ask now, from the window's button."""
        return self.updater.check()

    def install_update(self) -> str:
        return self.updater.apply_and_restart()

    def start_update_check(self) -> None:
        """The quiet one at launch, if the setting allows it."""
        if self.settings.get("auto_update"):
            self.updater.check_in_background()

    def strings(self, code: str | None = None) -> dict[str, str]:
        """The interface text for a language, without reopening the window."""
        return locales.strings(code or self.settings["language"])

    # -- getting images in -------------------------------------------------

    def choose_images(self) -> dict[str, Any]:
        import webview

        picked = self.window.create_file_dialog(
            webview.FileDialog.OPEN, allow_multiple=True, file_types=(image_filter(),)
        )
        return self._accept([Path(item) for item in picked or []])

    def choose_output_dir(self) -> str:
        import webview

        picked = self.window.create_file_dialog(webview.FileDialog.FOLDER)
        if picked:
            self.settings["output_dir"] = str(Path(picked[0]))
        return self.settings["output_dir"]

    def use_source_folder(self) -> str:
        self.settings["output_dir"] = ""
        return ""

    def accept_drop(self, name: str, payload: str) -> dict[str, Any]:
        """Take a file the page read for us.

        The system webview will not tell a page where a dropped file lives, so
        the page reads the bytes and hands them over; we write them somewhere
        we control and treat it like any other input.
        """
        suffix = Path(name).suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            return _rejected(name, "not a format this can read")
        try:
            raw = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError):
            return _rejected(name, "could not be read")
        if not raw:
            return _rejected(name, "is empty")
        if len(raw) > MAX_DROP_BYTES:
            return _rejected(name, f"is larger than {MAX_DROP_BYTES // (1024 * 1024)} MB")
        target = self._scratch / f"{os.urandom(6).hex()}{suffix}"
        target.write_bytes(raw)
        outcome = self._accept([target], temporary=True, display_name=name)
        if not outcome["added"]:
            target.unlink(missing_ok=True)
        return outcome

    def _accept(
        self, paths: list[Path], temporary: bool = False, display_name: str | None = None
    ) -> dict[str, Any]:
        """Queue what we can actually convert, and say why about the rest."""
        added: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        queued = {str(Path(job.source).resolve()) for job in self.queue.jobs()}
        for path in paths:
            shown = display_name or path.name
            if not path.is_file():
                skipped.append({"name": shown, "reason": "is no longer there"})
                continue
            if path.suffix.lower() not in SUPPORTED_SUFFIXES:
                skipped.append({"name": shown, "reason": "not a format this can read"})
                continue
            resolved = str(path.resolve())
            if resolved in queued:
                # Two jobs for one file would race for the same output path.
                skipped.append({"name": shown, "reason": "is already in the list"})
                continue
            thumbnail = _thumbnail(path)
            if not thumbnail:
                # Whatever stopped the thumbnail will stop the conversion, and
                # failing here says so while the name is still on screen.
                skipped.append({"name": shown, "reason": "is not an image we can open"})
                continue
            queued.add(resolved)
            job = self.queue.add(path, self._destination_for_path(path), temporary=temporary)
            added.append({**job.state(), "thumbnail": thumbnail})
        return {"added": added, "skipped": skipped}

    # -- running -----------------------------------------------------------

    def _tracing_options(self) -> dict[str, Any]:
        """Exactly what a conversion depends on, for comparing runs.

        The output folder is in here because moving it means the result
        belongs somewhere else, which is also a reason to convert again.
        """
        return {
            "preset": self.settings["preset"],
            "quality": self.settings["quality"],
            "max_edge": int(self.settings["max_edge"]),
            "remove_background": self.settings["background"],
            "measure": bool(self.settings["measure"]) and _scoring_available(),
            "output_dir": self.settings.get("output_dir", ""),
        }
        # Language and update preferences change nothing about a conversion,
        # so they must not make finished results look out of date.

    def start(self) -> dict[str, Any]:
        options = self._tracing_options()
        self.queue.start(
            {key: value for key, value in options.items() if key != "output_dir"},
            lambda job: self._destination_for_path(job.source),
            signature=options,
        )
        return self.poll()

    def convert_again(self) -> dict[str, Any]:
        """Run everything once more, even what the settings say is current."""
        self.queue.rerun_all()
        return self.start()

    def cancel(self) -> dict[str, Any]:
        self.queue.cancel()
        return self.poll()

    def poll(self) -> dict[str, Any]:
        options = self._tracing_options()
        jobs = []
        for job in self.queue.jobs():
            state = job.state()
            state["outdated"] = job.outdated(options)
            jobs.append(state)
        return {
            "busy": self.queue.busy,
            "jobs": jobs,
            # How many the Convert button would actually run, so it can say so.
            "outdated": sum(1 for state in jobs if state["outdated"]),
        }

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
            webview.FileDialog.SAVE,
            save_filename=Path(job.destination).name,
            file_types=("SVG (*.svg)",),
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
        self.updater.shutdown()
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


def _rejected(name: str, reason: str) -> dict[str, Any]:
    return {"added": [], "skipped": [{"name": name, "reason": reason}]}


_scoring: bool | None = None


def _scoring_available() -> bool:
    """Whether the similarity score can actually be computed here.

    Asking whether cairosvg imports is not the same question: it imports and
    then fails to find a system cairo.  Render something instead, once, and
    remember the answer.
    """
    global _scoring
    if _scoring is None:
        try:
            from ..quality import _make_cairo_findable

            _make_cairo_findable()
            import cairosvg

            cairosvg.svg2png(
                bytestring=b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>',
                output_width=1,
                output_height=1,
            )
            _scoring = True
        except Exception:
            _scoring = False
    return _scoring


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
