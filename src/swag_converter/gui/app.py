"""The desktop window.

pywebview draws through the platform's own browser engine — WKWebView on
macOS, WebView2 on Windows — so the interface is HTML while the binary stays
a Python program with no browser bundled inside it.
"""

from __future__ import annotations

from pathlib import Path
import sys


WEB = Path(__file__).resolve().parent / "web"


def self_check() -> int:
    """Prove a bundle is complete without needing a screen to draw on.

    What breaks in a frozen build is never the window: it is a data file that
    did not get collected or an import PyInstaller could not see. Both show
    up here, and a CI runner has no session to open a window in anyway.
    """
    missing = [name for name in ("index.html", "app.css", "app.js") if not (WEB / name).is_file()]
    if missing:
        print(f"web assets missing from the bundle: {', '.join(missing)}", file=sys.stderr)
        return 1
    try:
        import webview  # noqa: F401

        from ..convert import convert  # noqa: F401
        from .bridge import Bridge
        from .jobs import Queue  # noqa: F401

        described = Bridge().describe()
    except Exception as exc:
        print(f"the bundle cannot start: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    # The riskiest thing in a frozen build is not importing, it is spawning:
    # workers restart this same binary, and a bundle that gets that wrong
    # opens a window per conversion or hangs. Convert something real.
    converted = _convert_something()
    if converted is None:
        return 1

    from ..image import SUPPORTED_SUFFIXES

    if ".heic" not in SUPPORTED_SUFFIXES:
        print("the bundle cannot open HEIC, which is what phones produce", file=sys.stderr)
        return 1

    print(f"ok — sw(a)g.converter {described['version']}, "
          f"{len(described['presets'])} presets, scoring {'on' if described['scoring'] else 'off'}, "
          f"{len(described['suffixes'])} formats, "
          f"converted a test image to {converted} shapes")
    return 0


def _convert_something() -> int | None:
    """Run one tiny image through the real queue, workers and all."""
    import tempfile
    import time

    import numpy as np
    from PIL import Image

    from .bridge import Bridge

    bridge = Bridge()
    try:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "check.png"
            pixels = np.zeros((48, 48, 4), dtype=np.uint8)
            pixels[:, :, 3] = 255
            pixels[:, :, :3] = 210
            pixels[12:36, 12:36] = (30, 60, 190, 255)
            Image.fromarray(pixels, "RGBA").save(source)

            bridge.update_settings({"quality": "fast", "measure": False, "output_dir": directory})
            added = bridge._accept([source])
            if not added:
                print("the queue refused a plain PNG", file=sys.stderr)
                return None
            bridge.start()
            deadline = time.monotonic() + 180
            while bridge.poll()["busy"] and time.monotonic() < deadline:
                time.sleep(0.1)
            job = bridge.poll()["jobs"][0]
            if job["status"] != "done":
                print(f"a conversion did not finish: {job['status']} {job['error'] or ''}", file=sys.stderr)
                return None
            return int(job["result"]["regions"])
    finally:
        bridge.shutdown()


def main(argv: list[str] | None = None) -> int:
    import multiprocessing

    # Bundled builds re-execute the binary to spawn workers; without this the
    # app would launch a second window per conversion.
    multiprocessing.freeze_support()

    arguments = sys.argv[1:] if argv is None else argv
    if "--self-check" in arguments:
        return self_check()

    try:
        import webview
    except ImportError:
        print(
            "The desktop window needs pywebview:\n"
            "    pip install 'swag-converter[gui]'",
            file=sys.stderr,
        )
        return 2

    from .bridge import Bridge

    bridge = Bridge()
    window = webview.create_window(
        "sw(a)g.converter",
        str(WEB / "index.html"),
        js_api=bridge,
        width=1180,
        height=820,
        min_size=(880, 620),
        background_color="#17181B",
    )
    bridge.window = window

    def on_closing() -> None:
        # pywebview runs this inline on the closing path, so anything slow
        # here freezes the window as it shuts. Returning None (never False)
        # lets the close proceed.
        bridge.shutdown()

    window.events.closing += on_closing
    # Look for a newer build once the window is up, never before: a slow or
    # unreachable network must not delay anything the user can see.
    window.events.loaded += bridge.start_update_check

    webview.start()
    bridge.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
