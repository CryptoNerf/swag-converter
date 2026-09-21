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
    print(f"ok — sw(a)g.converter {described['version']}, "
          f"{len(described['presets'])} presets, scoring {'on' if described['scoring'] else 'off'}")
    return 0


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
    window.events.closing += lambda: bridge.shutdown()

    webview.start()
    bridge.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
