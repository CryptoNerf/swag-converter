# Running and building the desktop app

## From source

This is the fast loop: it picks up edits to `src/swag_converter/gui/` on the
next launch, with no build step.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[gui]'
swag-gui
```

`python -m swag_converter.gui.app` does the same thing if the console script
is not on your PATH. To check the whole stack without opening a window —
useful over SSH or in CI — `swag-gui --self-check` imports everything, then
converts a small image through the real queue and reports what it found.

Editing `web/index.html`, `web/app.css` or `web/app.js` needs no reinstall;
close the window and open it again.

### The similarity score

The score renders the SVG back with CairoSVG, which loads a system library.
macOS reads `DYLD_FALLBACK_LIBRARY_PATH` when a process starts, so exporting
it inside the app is too late — it has to be set before launch:

```bash
brew install cairo
export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib
swag-gui
```

Without it the *Score the result* checkbox is disabled and says why.
Conversion is unaffected.

## Building the app

```bash
pip install -e '.[gui]' pyinstaller
pyinstaller packaging/swag-gui.spec --distpath dist-app --noconfirm
open dist-app
```

That writes `dist-app/sw(a)g.converter.app`.

## If `lipo` refuses

```
You have not agreed to the Xcode license agreements.
```

PyInstaller ships a universal bootloader and has to thin it to arm64, which
needs the Xcode command line tools. Agree once:

```bash
sudo xcodebuild -license accept
```

Nothing else in the project needs Xcode; the release builds run on GitHub's
macOS runners, where the licence is already accepted.

## Why arm64 only

Every native wheel the tracer depends on — NumPy, SciPy, scikit-image,
Pillow — installs as arm64 on Apple Silicon, so a universal binary is not
available to build from. An Intel build would need an Intel Python and an
Intel wheel set.

## The bundle is unsigned

macOS will refuse it on first open. Right-click the app and choose **Open**,
then **Open** again in the dialog; that is the standard escape hatch for
unsigned software and only has to be done once. Signing and notarising it
instead needs an Apple Developer account.
