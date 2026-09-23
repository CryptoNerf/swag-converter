# sw(a)g.converter

Locally convert raster images into **real** vector SVG — actual `<path>` data with
fitted `<linearGradient>` / `<radialGradient>` fills. No embedded bitmaps, no
cloud service, no API key. Everything runs on your machine.

```bash
swag logo.png
```

```
sw(a)g.converter   raster → vector
────────────────────────────────────────────────────
╭──────────────────────────────────────────────────────────────╮
│      source  logo.png  320×320  46.4 KB                      │
│     content  icon  (texture 0.00, flat 100%, 334 colours)    │
│      shapes  18 regions  ·  12 gradients  ·  2,076 nodes     │
│  similarity  98.2%                                           │
│      output  logo.svg   40.2 KB  in 3.2s                     │
╰──────────────────────────────────────────────────────────────╯
```

## Why another tracer

Most raster-to-vector tools flatten everything into **flat colour patches**.
Smooth shading becomes a stack of hard-edged bands, and any semi-transparent
pixel is painted as if it were opaque — which is what produces the coloured
"shadow" you often see around a traced image.

This one does two things differently:

- **Gradients are fitted, not faked.** Neighbouring regions are merged for as
  long as a single linear or radial gradient still explains them, and the
  gradient stops are sampled from the real pixels. A shaded sphere becomes one
  path with one gradient instead of twenty concentric slivers.
- **A merge has to be harmless to both sides.** The cost of joining two
  regions is how much worse the shared paint describes the worse-off one, not
  how much it moves an average over their combined area. Averaging lets a
  four-pixel eye disappear into a face for free, which is why tracers so often
  return a portrait with no features.
- **Alpha is respected.** The silhouette comes from the alpha channel alone, and
  edge pixels take their colour from the nearest solid neighbour, so a faint
  halo stays faint instead of turning into solid paint.
- **Colour is read from a region's interior.** Where two regions meet the
  source is anti-aliased, and those pixels belong to neither. Fitting across
  them reads the blend as shading, which is how a flat white counter inside a
  letter picks up a grey gradient.

## The app

If you would rather not touch a terminal, there is a window:

**[Download for macOS](https://github.com/CryptoNerf/swag-converter/releases/latest)**
— Apple Silicon, no Python needed.

Drop images in, pick how much detail you want, press Convert. Results open in
a comparison view: drag the handle across the image to wipe between the
original and the vector, scroll to zoom, arrow keys to walk the batch.

It keeps itself up to date — it looks for a newer release on launch, downloads
it in the background and offers to restart when it is ready. Nothing installs
that does not match the checksum published with it. Turn it off in Settings if
you would rather not.

The interface is in English and Russian; switch in Settings.

The build is not signed, so macOS refuses it the first time. Right-click the
app, choose **Open**, then **Open** again — once, and never after. (Signing it
properly needs an Apple Developer account; if that matters to you, say so on
the issue tracker.)

Already have Python? `pip install 'swag-converter[gui]'` then `swag-gui`.
Running it from a clone, or building the app yourself, is
[packaging/README.md](packaging/README.md).

## Install

```bash
pip install swag-converter
```

Python 3.10 or newer is the only prerequisite; that command pulls in everything
else and puts a `swag` command on your PATH. Check it landed:

```bash
swag --version
```

<details>
<summary>Other ways to install</summary>

Isolated from your other packages, via [pipx](https://pipx.pypa.io):

```bash
pipx install swag-converter
```

Straight from a release, no PyPI involved:

```bash
pip install https://github.com/CryptoNerf/swag-converter/releases/download/v0.1.0/swag_converter-0.1.0-py3-none-any.whl
```

From a clone, for hacking on it:

```bash
git clone https://github.com/CryptoNerf/swag-converter
cd swag-converter
pip install -e '.[dev]'
```

</details>

The similarity score is the one optional extra: it needs
[CairoSVG](https://cairosvg.org), so `pip install 'swag-converter[quality]'`
(plus `brew install cairo` on macOS). Without it conversion works exactly the
same and the score reads `not measured`.

## Your first conversion

Point it at any image. There is nothing to configure:

```bash
swag logo.png
```

That writes `logo.svg` next to the original and prints the report above. Reading
it top to bottom:

| line | what it tells you |
| --- | --- |
| `source` | the file it read, its pixel size and weight on disk |
| `content` | which preset `auto` picked, and the measurements behind the choice |
| `shapes` | how many paths, gradients and Bézier nodes the SVG contains |
| `similarity` | how closely the SVG re-renders to the original, 100% being pixel-identical — reads `not measured` until you install the optional CairoSVG above |
| `output` | where the SVG went, how big it is, how long it took |

A `similarity` in the nineties means the vector is a faithful stand-in for the
original. If it comes out low, the image is probably photographic — see
[Photographs, honestly](#photographs-honestly).

Nothing is ever overwritten silently except a `.svg` of the same name, and no
file leaves your machine.

## Usage

```bash
swag logo.png                         # writes logo.svg next to it
swag icons/ --out svg/                # whole folder, in parallel
swag photo.jpg --preset photo         # stylised vector from a photograph
swag art.png --quality max            # slower, closer to the source
swag *.png --json                     # machine-readable output
```

| option | what it does |
| --- | --- |
| `-o, --out DIR` | Write SVGs into `DIR` instead of beside each input |
| `-p, --preset` | `auto` (default), `icon`, `illustration`, `photo`, `poster` |
| `-q, --quality` | `fast`, `balanced` (default), `max` — how much detail to keep |
| `--max-edge PX` | Resize the longer edge before tracing (default 1024, `0` disables) |
| `--background` | `auto` (default), `always`, `keep` — clear a flat backdrop |
| `-j, --workers N` | Parallel workers for batches |
| `--no-measure` | Skip the similarity score |

Input can be PNG, JPEG, WebP, GIF, BMP, TIFF, TGA, ICO, HEIC/AVIF — anything
Pillow reads. EXIF rotation, grayscale, palette and CMYK all get normalised.

## Quality

`--quality` sets the region budget: how many regions the image may keep before
the cheapest ones are absorbed. That budget is what decides whether texture
reads as detail or as smear, and it is also what most of the run time buys.

| tier | budget | on a 450×450 photograph |
| --- | --- | --- |
| `fast` | 0.35x | 384 regions, 323 KB, 14s |
| `balanced` | 1x | 1,345 regions, 935 KB, 41s |
| `max` | 2x | 2,923 regions, 1.7 MB, 71s |

Flat artwork saturates well before `max` — there is no more detail to find, so
the extra budget goes unused and costs nothing.

## Presets

`auto` picks one by measuring how much fine detail survives a median filter —
grain does, clean edges do not — so drawings are never mistaken for photographs
however colourful they are.

| preset | for | result |
| --- | --- | --- |
| `icon` | logos, UI icons, flat art | crisp edges, few shapes |
| `illustration` | shaded artwork, stickers, game assets | gradients preserved |
| `photo` | photographs | deliberate stylisation, bounded cost |
| `poster` | any image | a few big flat shapes, screen-print look |

## Photographs, honestly

A photograph is not really vector material: it has texture in every pixel and no
region structure to find. `--preset photo` gives you a clean, deliberate
stylisation in seconds rather than a faithful reproduction — expect something
closer to a screen print than to the original. If you want fidelity from a
photo, keep the raster.

What it will **not** do is hang: a 3840×2160 photograph converts in under two
minutes, or half a minute at `--quality fast`, because the working size is
capped, the region budget is fixed before tracing starts and the merge stage
runs on a priority queue.

## Using it as a library

```python
from swag_converter import convert

result = convert("logo.png", "logo.svg", preset="auto", quality="max")
print(result.regions, result.nodes, result.similarity)
```

## How it works

```text
image
  └─ normalise: EXIF, colour mode, size cap, optional backdrop removal
       └─ classify content → preset
            └─ repair edge colour, derive the silhouette from alpha
                 └─ cluster colour in CIELAB, split into connected regions
                      └─ merge neighbours while one gradient still fits them
                           └─ marching squares → cubic Bézier fitting
                                └─ flat / linear / radial paint per region
                                     └─ SVG, then render back to score it
```

Curve fitting is Schneider's least-squares cubic algorithm, so a square traces
to exactly four segments with sharp corners and a circle to about ten smooth
ones. Adjacent paths overlap by a fraction of a pixel, which is what stops
anti-aliasing leaving hairline seams between them.

## Limitations

- Photographs are stylised, not reproduced — see above.
- Very large images are downscaled to `--max-edge` before tracing; raise it for
  more detail, at a real cost in time and file size.
- Soft transitions become a boundary between two regions. Adjacent gradients are
  matched so the colour is continuous across it, but a truly soft edge (a blur,
  a glow) cannot be represented.
- Output is larger than the source PNG for detailed images. That is inherent:
  vector data at this fidelity costs more than a compressed bitmap.
- Texture made of isolated single pixels — a star field, heavy film grain — is
  not recoverable. The scene around it traces fine; the specks themselves
  cannot become paths without one path each, so they go.

## Development

```bash
pip install -e '.[dev,gui]'
pytest                      # full suite
pytest -m 'not slow'        # skip the timing guards
python examples/make_samples.py
swag-gui                    # the desktop window
```

Building the app itself is [packaging/README.md](packaging/README.md).

Releases are cut by pushing a tag; see [RELEASING.md](RELEASING.md).

## License

MIT — see [LICENSE](LICENSE).

The SVGs you produce are yours; this tool claims nothing over them. Do check the
licence of whatever you feed it — vectorising an image does not change who owns
it.
