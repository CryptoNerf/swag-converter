# Building the desktop app

```bash
pip install -e '.[gui]' pyinstaller
pyinstaller packaging/swag-gui.spec --distpath dist-app --noconfirm
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
