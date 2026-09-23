"""Measure tracing quality over the drawn corpus.

Two numbers per image.  SSIM says how close the rendering is overall;
``edges`` says how much of the original's edge energy landed where the
original's edges are, which is what actually moved when body copy went from
unreadable to readable while SSIM barely twitched.

Serial by design: this is meant to run on a build runner, and a pool of
workers all doing SciPy at once is how it stopped being usable on a laptop.
"""

from __future__ import annotations

import argparse
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import time

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from swag_converter.convert import convert  # noqa: E402

import corpus  # noqa: E402


def score(source: Path, traced: Path, width: int, height: int) -> tuple[float, float]:
    from swag_converter.quality import _make_cairo_findable

    # Before importing cairosvg: it opens its library as it imports, so a
    # resolver installed afterwards is too late.
    _make_cairo_findable()

    import cairosvg
    from PIL import Image, ImageOps
    from scipy import ndimage
    from skimage.metrics import structural_similarity

    raw = cairosvg.svg2png(url=str(traced), output_width=width, output_height=height)
    rendered = np.asarray(Image.open(BytesIO(raw)).convert("RGBA"), dtype=np.float32)

    opened = Image.open(source)
    opened = ImageOps.exif_transpose(opened) or opened
    reference = np.asarray(
        opened.convert("RGBA").resize((width, height), Image.LANCZOS), dtype=np.float32
    )

    def flatten(image: np.ndarray) -> np.ndarray:
        alpha = image[:, :, 3:4] / 255.0
        return image[:, :, :3] * alpha + 160.0 * (1.0 - alpha)

    left, right = flatten(reference), flatten(rendered)
    ssim = float(structural_similarity(left, right, channel_axis=2, data_range=255.0))

    def edges(image: np.ndarray) -> np.ndarray:
        grey = image.mean(axis=2)
        return np.hypot(ndimage.sobel(grey, 0), ndimage.sobel(grey, 1))

    a, b = edges(left), edges(right)
    correlation = float(np.corrcoef(a.ravel(), b.ravel())[0, 1])
    return round(ssim, 4), round(correlation, 4)


def measure(quality: str) -> list[dict]:
    rows = []
    with tempfile.TemporaryDirectory() as directory:
        workspace = Path(directory)
        for source in corpus.write(workspace / "in"):
            traced = workspace / "out" / f"{source.stem}.svg"
            started = time.perf_counter()
            result = convert(source, traced, quality=quality, measure=False)
            seconds = time.perf_counter() - started
            ssim, edges = score(source, traced, result.width, result.height)
            rows.append({
                "name": source.stem,
                "preset": result.preset,
                "ssim": ssim,
                "edges": edges,
                "regions": result.regions,
                "nodes": result.nodes,
                "kb": round(result.svg_bytes / 1024, 1),
                "seconds": round(seconds, 1),
            })
    return rows


def render(rows: list[dict]) -> str:
    lines = [f"{'image':14} {'preset':12} {'SSIM':>7} {'edges':>7} {'regions':>8} {'nodes':>7} {'KB':>7} {'s':>6}"]
    for row in rows:
        lines.append(
            f"{row['name']:14} {row['preset']:12} {row['ssim']:>7.4f} {row['edges']:>7.4f} "
            f"{row['regions']:>8} {row['nodes']:>7} {row['kb']:>7.1f} {row['seconds']:>6.1f}"
        )
    lines.append(
        f"{'mean':14} {'':12} {np.mean([r['ssim'] for r in rows]):>7.4f} "
        f"{np.mean([r['edges'] for r in rows]):>7.4f}"
    )
    return "\n".join(lines)


def compare(rows: list[dict], baseline: list[dict], slack: float) -> list[str]:
    """Anything materially worse than the recorded run, named."""
    known = {row["name"]: row for row in baseline}
    complaints = []
    for row in rows:
        was = known.get(row["name"])
        if was is None:
            complaints.append(f"{row['name']}: not in the baseline")
            continue
        for key in ("ssim", "edges"):
            if row[key] < was[key] - slack:
                complaints.append(
                    f"{row['name']}: {key} {was[key]:.4f} -> {row[key]:.4f}"
                )
    return complaints


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quality", default="balanced")
    parser.add_argument("--baseline", type=Path, default=HERE / "baseline.json")
    parser.add_argument("--record", action="store_true", help="overwrite the baseline")
    parser.add_argument("--slack", type=float, default=0.01,
                        help="how far a score may drop before it is a regression")
    arguments = parser.parse_args()

    rows = measure(arguments.quality)
    print(render(rows))

    # Keyed by platform: the corpus draws type with whatever font the system
    # has, so a macOS number and a Linux number are not the same measurement.
    payload = {
        "corpus": corpus.CORPUS_VERSION,
        "quality": arguments.quality,
        "platform": sys.platform,
        "rows": rows,
    }
    if arguments.record:
        arguments.baseline.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"\nrecorded {arguments.baseline}")
        return 0

    if not arguments.baseline.exists():
        print("\nno baseline recorded; run with --record")
        return 0

    stored = json.loads(arguments.baseline.read_text(encoding="utf-8"))
    if stored.get("corpus") != corpus.CORPUS_VERSION:
        print("\nthe corpus has changed since the baseline; re-record it")
        return 0
    if stored.get("platform") != sys.platform:
        print(f"\nthe baseline was recorded on {stored.get('platform')}, this is "
              f"{sys.platform}; the type in the corpus is set in whatever font "
              f"the system has, so the numbers are not comparable")
        return 0
    if stored.get("quality") != arguments.quality:
        print(f"\nthe baseline is for --quality {stored.get('quality')}")
        return 0

    complaints = compare(rows, stored["rows"], arguments.slack)
    if complaints:
        print("\nworse than the baseline:")
        for line in complaints:
            print(f"  {line}")
        return 1
    print("\nno regression against the baseline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
