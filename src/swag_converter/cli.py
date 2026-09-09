"""Command line entry point."""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
import os
from pathlib import Path
import time
import sys
from typing import Any

from . import __version__
from .convert import Result, convert
from .image import SUPPORTED_SUFFIXES
from .presets import PRESETS, QUALITY


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="swag",
        description="sw(a)g.converter — turn raster images into real vector SVG, locally.",
        epilog="Examples:\n"
               "  swag logo.png\n"
               "  swag icons/*.png --out svg/\n"
               "  swag photo.jpg --preset photo --quality max\n"
               "  swag art/ --out svg/ --workers 8",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("inputs", nargs="*", type=Path, help="Image files or directories")
    parser.add_argument("-o", "--out", type=Path, help="Output directory (default: alongside each input)")
    parser.add_argument("-p", "--preset", default="auto", choices=["auto", *sorted(PRESETS)],
                        help="Tracing preset (default: auto-detect from content)")
    parser.add_argument("-q", "--quality", default="balanced", choices=list(QUALITY),
                        help="Quality tier (default: balanced)")
    parser.add_argument("--max-edge", type=int, default=1024, metavar="PX",
                        help="Resize longer edge before tracing (default: 1024, 0 disables)")
    parser.add_argument("--background", default="auto", choices=["auto", "always", "keep"],
                        help="Remove a flat backdrop (default: auto — only when there is no alpha)")
    parser.add_argument("-j", "--workers", type=int, default=0, metavar="N",
                        help="Parallel workers for batches (default: auto)")
    parser.add_argument("--no-measure", action="store_true", help="Skip the similarity score (faster)")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON only")
    parser.add_argument("--quiet", action="store_true", help="Only report errors")
    parser.add_argument("--version", action="version", version=f"sw(a)g.converter {__version__}")
    return parser


def _collect(inputs: list[Path]) -> list[Path]:
    found: list[Path] = []
    for item in inputs:
        if item.is_dir():
            found.extend(
                sorted(p for p in item.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES)
            )
        elif item.is_file():
            found.append(item)
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in found:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(path)
    return unique


def _destination(source: Path, out: Path | None) -> Path:
    return (out / f"{source.stem}.svg") if out else source.with_suffix(".svg")


def _run_one(payload: dict[str, Any]) -> dict[str, Any]:
    """Picklable worker."""
    source = Path(payload["source"])
    try:
        result = convert(
            source,
            Path(payload["destination"]),
            preset=payload["preset"],
            quality=payload["quality"],
            max_edge=payload["max_edge"],
            remove_background=payload["background"],
            measure=payload["measure"],
        )
        return {"ok": True, "result": result}
    except Exception as exc:  # one bad file must not stop a batch
        return {"ok": False, "source": str(source), "error": f"{type(exc).__name__}: {exc}"}


def _as_json(result: Result) -> dict[str, Any]:
    return {
        "source": str(result.source),
        "output": str(result.destination),
        "preset": result.preset,
        "quality": result.quality,
        "content": result.analysis.kind,
        "source_size": list(result.source_size),
        "traced_size": [result.width, result.height],
        "regions": result.regions,
        "gradients": result.gradients,
        "nodes": result.nodes,
        "svg_bytes": result.svg_bytes,
        "source_bytes": result.source_bytes,
        "similarity": result.similarity,
        "seconds": round(result.seconds, 3),
        "warnings": result.warnings,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    from . import ui

    console = ui.console(quiet=args.quiet or args.json)
    if not args.inputs:
        _parser().print_help()
        return 2
    sources = _collect(args.inputs)
    if not sources:
        ui.failure(console, "No images found.",
                   f"Supported: {', '.join(sorted(SUPPORTED_SUFFIXES))}")
        return 2
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)

    if not args.json and not args.quiet:
        ui.banner(console)

    jobs = [
        {
            "source": str(source),
            "destination": str(_destination(source, args.out)),
            "preset": args.preset,
            "quality": args.quality,
            "max_edge": args.max_edge,
            "background": args.background,
            "measure": not args.no_measure,
        }
        for source in sources
    ]

    results: list[Result] = []
    failures: list[tuple[Path, str]] = []
    started = time.perf_counter()

    if len(jobs) == 1:
        results, failures = _run_serial(jobs, console, args)
    else:
        results, failures = _run_batch(jobs, console, args)

    if args.json:
        print(json.dumps(
            {"converted": [_as_json(r) for r in results],
             "failed": [{"source": str(p), "error": e} for p, e in failures]},
            ensure_ascii=False, indent=2,
        ))
    elif not args.quiet:
        if len(results) == 1 and not failures:
            ui.single_report(console, results[0])
        else:
            ui.batch_summary(console, results, failures, args.out or Path.cwd(),
                             elapsed=time.perf_counter() - started)
        console.print()
    for path, reason in failures:
        if args.quiet:
            print(f"{path}: {reason}", file=sys.stderr)
    # Any failure is a failure: a batch that half-worked must not look like
    # success to `swag icons/ && deploy`.
    return 1 if failures else 0


def _run_serial(jobs: list[dict[str, Any]], console: Any, args: Any):
    from . import ui

    results: list[Result] = []
    failures: list[tuple[Path, str]] = []
    job = jobs[0]
    source = Path(job["source"])
    stage = {"label": "starting"}
    with ui.progress(console) as bar:
        task = bar.add_task(f"tracing {source.name}", total=1, detail="")

        def step(label: str) -> None:
            stage["label"] = label
            bar.update(task, detail=label)

        try:
            results.append(convert(
                source, Path(job["destination"]), preset=job["preset"], quality=job["quality"],
                max_edge=job["max_edge"], remove_background=job["background"],
                measure=job["measure"], progress=step,
            ))
            bar.update(task, advance=1)
        except Exception as exc:
            failures.append((source, f"{type(exc).__name__}: {exc} (during {stage['label']})"))
    if failures and not args.json and not args.quiet:
        ui.failure(console, f"{failures[0][0].name}: {failures[0][1]}")
    return results, failures


def _run_batch(jobs: list[dict[str, Any]], console: Any, args: Any):
    from . import ui

    results: list[Result] = []
    failures: list[tuple[Path, str]] = []
    workers = args.workers or min(8, max(1, (os.cpu_count() or 2) - 1))
    with ui.progress(console) as bar:
        task = bar.add_task("tracing", total=len(jobs), detail="")
        try:
            with ProcessPoolExecutor(max_workers=min(workers, len(jobs))) as pool:
                futures = {pool.submit(_run_one, job): job for job in jobs}
                for future in as_completed(futures):
                    _absorb(future.result(), results, failures)
                    bar.update(task, advance=1, detail=f"{len(results)} done")
        except OSError:
            # Some sandboxes disallow POSIX semaphores; stay usable there.
            for job in jobs:
                _absorb(_run_one(job), results, failures)
                bar.update(task, advance=1, detail=f"{len(results)} done")
    results.sort(key=lambda item: item.source.name)
    return results, failures


def _absorb(payload: dict[str, Any], results: list[Result], failures: list[tuple[Path, str]]) -> None:
    if payload["ok"]:
        results.append(payload["result"])
    else:
        failures.append((Path(payload["source"]), payload["error"]))


if __name__ == "__main__":
    raise SystemExit(main())
