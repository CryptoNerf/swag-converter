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
│      source  logo.png  512×512  84.2 KB                      │
│     content  icon  (texture 0.36, flat 77%, 449 colours)     │
│      shapes  45 regions  ·  19 gradients  ·  1,158 nodes     │
│  similarity  96.4%                                           │
│      output  logo.svg   29.6 KB  in 6.1s                     │
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
- **Alpha is respected.** The silhouette comes from the alpha channel alone, and
  edge pixels take their colour from the nearest solid neighbour, so a faint
  halo stays faint instead of turning into solid paint.

## Install

```bash
pip install swag-converter
```

From source:

```bash
git clone https://github.com/CryptoNerf/swag-converter
cd swag-converter
pip install -e '.[dev]'
```

Python 3.10+. The similarity score needs [CairoSVG](https://cairosvg.org)
(`pip install 'swag-converter[quality]'`, plus `brew install cairo` on macOS);
conversion works fine without it.

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
| `-q, --quality` | `fast`, `balanced` (default), `max` |
| `--max-edge PX` | Resize the longer edge before tracing (default 1024, `0` disables) |
| `--background` | `auto` (default), `always`, `keep` — clear a flat backdrop |
| `-j, --workers N` | Parallel workers for batches |
| `--no-measure` | Skip the similarity score |

Input can be PNG, JPEG, WebP, GIF, BMP, TIFF, TGA, ICO, HEIC/AVIF — anything
Pillow reads. EXIF rotation, grayscale, palette and CMYK all get normalised.

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

What it will **not** do is hang: a 3840×2160 photograph converts in about 25
seconds because the working size is capped and the merge stage runs on a
priority queue.

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

## Development

```bash
pip install -e '.[dev]'
pytest                      # full suite
pytest -m 'not slow'        # skip the timing guards
python examples/make_samples.py
```

## License

MIT — see [LICENSE](LICENSE).

The SVGs you produce are yours; this tool claims nothing over them. Do check the
licence of whatever you feed it — vectorising an image does not change who owns
it.
