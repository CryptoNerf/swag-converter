"""Updating the installed app from its own releases.

The app is not code-signed, so the only trust anchors available are TLS to
GitHub and the checksum published alongside the build.  Both are used: the
download is verified against the ``SHA256SUMS.txt`` of the same release
before anything is unpacked, and nothing but an application bundle is ever
written.  An update that does not verify is discarded, not installed.

Nothing here runs from a source checkout — there is no bundle to replace —
and nothing installs without the window asking first.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable
import urllib.error
import urllib.request

RELEASES = "https://api.github.com/repos/CryptoNerf/swag-converter/releases/latest"
#: Only these hosts are ever fetched from.
ALLOWED_HOSTS = ("api.github.com", "github.com", "objects.githubusercontent.com",
                 "release-assets.githubusercontent.com")
TIMEOUT = 30
USER_AGENT = "swag-converter-updater"


def version_tuple(text: str) -> tuple[int, ...]:
    """``0.2.10`` sorts after ``0.2.9``, which string comparison gets wrong."""
    parts = re.findall(r"\d+", text or "")
    return tuple(int(part) for part in parts[:4]) or (0,)


def running_bundle() -> Path | None:
    """The .app we are executing from, if we are executing from one."""
    if not getattr(sys, "frozen", False) or sys.platform != "darwin":
        return None
    for parent in Path(sys.executable).resolve().parents:
        if parent.suffix == ".app":
            return parent
    return None


@dataclass
class Available:
    version: str
    url: str
    name: str
    digest: str | None
    notes: str


class Updater:
    """Checks, downloads and stages; the window decides when to apply."""

    def __init__(self, current: str, store: Any = None) -> None:
        self.current = current
        self.store = store
        self.state: dict[str, Any] = {"phase": "idle", "version": None, "detail": ""}
        self._staged: Path | None = None
        self._scratch: Path | None = None
        self._lock = threading.Lock()

    # -- what the window shows --------------------------------------------

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                **self.state,
                "supported": running_bundle() is not None,
                "current": self.current,
                "ready": self._staged is not None,
            }

    def _set(self, phase: str, **rest: Any) -> None:
        with self._lock:
            self.state = {"phase": phase, "version": self.state.get("version"), "detail": "", **rest}

    # -- the work ----------------------------------------------------------

    def check_in_background(self, then: Callable[[], None] | None = None) -> None:
        threading.Thread(target=lambda: self.check(then), daemon=True).start()

    def check(self, then: Callable[[], None] | None = None) -> dict[str, Any]:
        if running_bundle() is None:
            self._set("unsupported")
            return self.status()
        self._set("checking")
        try:
            release = self._latest()
        except Exception as exc:
            self._set("failed", detail=_short(exc))
            return self.status()

        if release is None or version_tuple(release.version) <= version_tuple(self.current):
            self._set("current")
            return self.status()

        self._set("found", version=release.version)
        try:
            self._download(release)
        except Exception as exc:
            self._set("failed", version=release.version, detail=_short(exc))
        if then:
            then()
        return self.status()

    def _latest(self) -> Available | None:
        payload = json.loads(_fetch(RELEASES).decode("utf-8"))
        tag = str(payload.get("tag_name") or "")
        assets = payload.get("assets") or []
        image = next((a for a in assets if str(a.get("name", "")).endswith(".dmg")), None)
        if not tag or image is None:
            return None
        # The desktop build publishes its own sums file; the Python release
        # publishes another that covers only the wheel and the sdist.  Take
        # whichever actually names this disk image.
        digest = None
        for candidate in ("SHA256SUMS-macos.txt", "SHA256SUMS.txt"):
            listing = next((a for a in assets if str(a.get("name", "")) == candidate), None)
            if listing is None:
                continue
            digest = _digest_for(_fetch(listing["browser_download_url"]).decode("utf-8"), image["name"])
            if digest:
                break
        return Available(
            version=tag.lstrip("v"),
            url=image["browser_download_url"],
            name=image["name"],
            digest=digest,
            notes=str(payload.get("body") or "")[:4000],
        )

    def _download(self, release: Available) -> None:
        if release.digest is None:
            # Without a published checksum there is nothing to verify against,
            # and an unsigned bundle is not something to install on trust.
            raise RuntimeError("the release publishes no checksum to verify against")
        self._set("downloading", version=release.version)
        scratch = Path(tempfile.mkdtemp(prefix="swag-update-"))
        image = scratch / release.name
        _fetch_to(release.url, image)

        actual = _sha256(image)
        if actual != release.digest:
            shutil.rmtree(scratch, ignore_errors=True)
            raise RuntimeError("the download does not match its published checksum")

        self._set("unpacking", version=release.version)
        staged = _extract_app(image, scratch)
        image.unlink(missing_ok=True)
        if staged is None:
            shutil.rmtree(scratch, ignore_errors=True)
            raise RuntimeError("the update contains no application")

        self._discard_previous()
        with self._lock:
            self._staged, self._scratch = staged, scratch
        self._set("ready", version=release.version)

    # -- applying ----------------------------------------------------------

    def apply_and_restart(self) -> str:
        """Hand the swap to a script that outlives us, then quit.

        A bundle cannot replace itself while it is the thing running, so a
        short shell script waits for this process to go and does it after.
        """
        target = running_bundle()
        with self._lock:
            staged = self._staged
        if target is None:
            return "unsupported"
        if staged is None:
            return "nothing staged"
        if not os.access(target.parent, os.W_OK):
            return f"no permission to replace {target.parent}"

        script = staged.parent / "swap.sh"
        script.write_text(
            "#!/bin/sh\n"
            f'pid={os.getpid()}\n'
            'i=0\n'
            'while kill -0 "$pid" 2>/dev/null && [ "$i" -lt 100 ]; do sleep 0.2; i=$((i+1)); done\n'
            f'rm -rf {_quote(target)}.old\n'
            f'mv {_quote(target)} {_quote(target)}.old 2>/dev/null\n'
            f'cp -R {_quote(staged)} {_quote(target)} || mv {_quote(target)}.old {_quote(target)}\n'
            f'rm -rf {_quote(target)}.old\n'
            f'xattr -dr com.apple.quarantine {_quote(target)} 2>/dev/null\n'
            f'open {_quote(target)}\n'
            f'rm -rf {_quote(staged.parent)}\n',
            encoding="utf-8",
        )
        script.chmod(0o755)
        subprocess.Popen(["/bin/sh", str(script)], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return "restarting"

    def _discard_previous(self) -> None:
        with self._lock:
            previous = self._scratch
            self._staged = self._scratch = None
        if previous is not None:
            shutil.rmtree(previous, ignore_errors=True)

    def shutdown(self) -> None:
        self._discard_previous()


# -- plumbing --------------------------------------------------------------

def _check_host(url: str) -> None:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise RuntimeError(f"refusing to fetch from {parsed.scheme}://{parsed.hostname}")


def _request(url: str) -> urllib.request.Request:
    _check_host(url)
    return urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                "Accept": "application/vnd.github+json"})


def _fetch(url: str, limit: int = 8 * 1024 * 1024) -> bytes:
    with urllib.request.urlopen(_request(url), timeout=TIMEOUT) as response:
        return response.read(limit)


def _fetch_to(url: str, destination: Path, limit: int = 600 * 1024 * 1024) -> None:
    written = 0
    with urllib.request.urlopen(_request(url), timeout=TIMEOUT) as response:
        with destination.open("wb") as handle:
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > limit:
                    raise RuntimeError("the download is implausibly large")
                handle.write(chunk)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _digest_for(sums: str, name: str) -> str | None:
    for line in sums.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == name:
            return parts[0].lower()
    return None


def _extract_app(image: Path, into: Path) -> Path | None:
    """Mount the disk image, copy the bundle out, unmount whatever happens."""
    mount = into / "mount"
    mount.mkdir(exist_ok=True)
    attached = subprocess.run(
        ["hdiutil", "attach", str(image), "-nobrowse", "-quiet", "-readonly",
         "-mountpoint", str(mount)],
        capture_output=True, text=True,
    )
    if attached.returncode != 0:
        return None
    try:
        source = next((p for p in mount.iterdir() if p.suffix == ".app"), None)
        if source is None:
            return None
        staged = into / source.name
        shutil.copytree(source, staged, symlinks=True)
    finally:
        subprocess.run(["hdiutil", "detach", str(mount), "-force", "-quiet"],
                       capture_output=True)
    return staged if _is_application(staged) else None


def _is_application(bundle: Path) -> bool:
    """A cursory check that what we copied is the thing we expect."""
    info = bundle / "Contents" / "Info.plist"
    if not info.is_file():
        return False
    try:
        plist = plistlib.loads(info.read_bytes())
    except Exception:
        return False
    if plist.get("CFBundleIdentifier") != "com.cryptonerf.swagconverter":
        return False
    executable = bundle / "Contents" / "MacOS" / str(plist.get("CFBundleExecutable", ""))
    return executable.is_file() and os.access(executable, os.X_OK)


def _quote(path: Path) -> str:
    return "'" + str(path).replace("'", "'\\''") + "'"


def _short(exc: Exception) -> str:
    text = str(exc) or type(exc).__name__
    return text[:160]
