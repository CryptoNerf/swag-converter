# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe for the desktop build.

The window is the platform's own web view, so nothing browser-shaped is
bundled here; the weight is numpy, scipy and scikit-image, which the tracer
genuinely uses.  Everything listed under ``excludes`` is something those
three drag along and we never touch.
"""

from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
    copy_metadata,
)

HERE = Path(SPECPATH).resolve()
ROOT = HERE.parent

def cairo_binaries() -> list[tuple[str, str]]:
    """The cairo the similarity score renders through.

    It is a system library, not a wheel, so nothing in the dependency graph
    points PyInstaller at it.  Naming the file is enough — macholib follows
    what it needs from there — and ``quality._make_cairo_findable`` looks in
    the bundle directory at runtime, so no loader path has to be arranged.
    """
    for directory in ("/opt/homebrew/lib", "/usr/local/lib"):
        candidate = Path(directory) / "libcairo.2.dylib"
        if candidate.exists():
            return [(str(candidate.resolve()), ".")]
    return []


hidden = [
    *collect_submodules("scipy.ndimage"),
    *collect_submodules("skimage.measure"),
    *collect_submodules("skimage.color"),
    "skimage.metrics",
    "PIL.Image",
    "PIL.ImageOps",
    # Registered at import time so phone photographs open at all.
    "pillow_heif",
    # The similarity score; cairocffi reaches its library through ctypes.
    "cairosvg",
    "cairocffi",
]

datas = [
    (str(ROOT / "src/swag_converter/gui/web"), "swag_converter/gui/web"),
    # Without these the bundle has no interface text at all and every label
    # renders as its own key.
    (str(ROOT / "src/swag_converter/gui/locales"), "swag_converter/gui/locales"),
    *collect_data_files("cairosvg"),
    *collect_data_files("cairocffi"),
    # The package reads its own version from the installed distribution, so
    # the bundle has to carry that metadata or it reports 0+unknown.
    *copy_metadata("swag-converter"),
    *collect_data_files("skimage", includes=["**/*.pyi"]),
]

excludes = [
    "matplotlib", "tkinter", "pytest", "IPython", "notebook", "sphinx",
    "pandas", "sympy", "PyQt5", "PyQt6", "PySide2", "PySide6",
    "scipy.io.matlab", "scipy.optimize", "scipy.sparse.csgraph",
    "skimage.data", "skimage.io", "skimage.viewer",
    "networkx.drawing", "networkx.generators",
]

analysis = Analysis(
    [str(HERE / "entry.py")],
    pathex=[str(ROOT / "src")],
    # pillow-heif carries libheif and its codecs; without them HEIC files
    # are offered by the dialog and then fail to open.
    binaries=[*collect_dynamic_libs("pillow_heif"), *cairo_binaries()],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="swag-converter",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    # Every native wheel in the dependency set is arm64-only, so a universal
    # build is not on offer; saying so explicitly beats letting PyInstaller
    # infer it.  Thinning the universal bootloader needs `lipo`, which is
    # gated behind the Xcode licence — see packaging/README.md.
    target_arch="arm64",
)

collection = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="swag-converter",
)

app = BUNDLE(
    collection,
    name="sw(a)g.converter.app",
    icon=str(HERE / "icon.icns") if (HERE / "icon.icns").exists() else None,
    bundle_identifier="com.cryptonerf.swagconverter",
    info_plist={
        "CFBundleName": "sw(a)g.converter",
        "CFBundleDisplayName": "sw(a)g.converter",
        "CFBundleShortVersionString": "0.2.1",
        "NSHighResolutionCapable": True,
        # The tracer never reaches the network; say so rather than leaving
        # the system to guess when it sandboxes the web view.
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
        "LSMinimumSystemVersion": "11.0",
    },
)
