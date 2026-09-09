"""Terminal presentation.

Kept apart from the pipeline so conversion can run head-less (library use,
``--json``, tests) without importing any of this.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from rich.console import Console, Group
from rich.markup import escape
from rich.panel import Panel
from rich.progress import (
    BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn,
)
from rich.table import Table
from rich.text import Text

from .convert import Result


BRAND = "sw(a)g.converter"
ACCENT = "bright_magenta"
MUTED = "grey54"

KIND_STYLE = {"icon": "cyan", "illustration": "green", "photo": "yellow", "poster": "magenta"}


def console(quiet: bool = False) -> Console:
    return Console(quiet=quiet, highlight=False, soft_wrap=False)


def banner(target: Console) -> None:
    line = Text()
    line.append(BRAND, style=f"bold {ACCENT}")
    line.append("   raster → vector", style=MUTED)
    target.print()
    target.print(line)
    target.print(Text("─" * 52, style=MUTED))


def human_bytes(value: float) -> str:
    if value < 1024:
        return f"{value:.0f} B"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KB"
    return f"{value / 1048576:.1f} MB"


def progress(target: Console) -> Progress:
    return Progress(
        SpinnerColumn(style=ACCENT),
        TextColumn("[bold]{task.description}"),
        BarColumn(complete_style=ACCENT, finished_style="green"),
        MofNCompleteColumn(),
        TextColumn("{task.fields[detail]}", style=MUTED),
        TimeElapsedColumn(),
        console=target,
        transient=True,
    )


def describe_source(result: Result) -> Text:
    line = Text()
    line.append(escape(result.source.name), style="bold")
    line.append(f"  {result.source_size[0]}×{result.source_size[1]}", style=MUTED)
    if result.downscaled:
        line.append(f" → {result.width}×{result.height}", style="yellow")
    line.append(f"  {human_bytes(result.source_bytes)}", style=MUTED)
    return line


def _short_path(path: Path) -> str:
    """Relative to the working directory when that is shorter to read."""
    try:
        relative = path.resolve().relative_to(Path.cwd().resolve())
        return str(relative)
    except ValueError:
        return str(path)


def single_report(target: Console, result: Result) -> None:
    """The detailed view shown when converting exactly one file."""
    kind = KIND_STYLE.get(result.preset, "white")
    facts = Table.grid(padding=(0, 2))
    facts.add_column(style=MUTED, justify="right")
    facts.add_column()
    facts.add_row("source", describe_source(result))
    facts.add_row("content", Text(f"{result.preset}", style=kind) + Text(f"  ({result.analysis.summary})", style=MUTED))
    if result.background_removed:
        facts.add_row("background", Text("flat backdrop removed", style="yellow"))
    facts.add_row(
        "shapes",
        Text(f"{result.regions} regions", style="white")
        + Text(f"  ·  {result.gradients} gradients  ·  {result.nodes:,} nodes", style=MUTED),
    )
    quality = (
        Text(f"{result.similarity:.1%}", style="green" if result.similarity and result.similarity > 0.9 else "yellow")
        if result.similarity is not None
        else Text("not measured", style=MUTED)
    )
    facts.add_row("similarity", quality)
    facts.add_row(
        "output",
        Text(escape(_short_path(result.destination)), style="bold green")
        + Text(f"   {human_bytes(result.svg_bytes)}  in {result.seconds:.1f}s", style=MUTED),
    )
    target.print(Panel(facts, border_style=MUTED, padding=(1, 2)))
    for warning in result.warnings:
        target.print(Text(f"  ! {warning}", style="yellow"))


def results_table(target: Console, results: Iterable[Result]) -> None:
    table = Table(box=None, pad_edge=False, padding=(0, 2))
    table.add_column("file", overflow="ellipsis", max_width=34)
    table.add_column("content", justify="left")
    table.add_column("regions", justify="right")
    table.add_column("nodes", justify="right")
    table.add_column("size", justify="right")
    table.add_column("match", justify="right")
    table.add_column("time", justify="right")
    for result in results:
        similarity = f"{result.similarity:.1%}" if result.similarity is not None else "–"
        style = "green" if (result.similarity or 0) > 0.9 else "yellow"
        table.add_row(
            escape(result.source.name),
            Text(result.preset, style=KIND_STYLE.get(result.preset, "white")),
            f"{result.regions}",
            f"{result.nodes:,}",
            human_bytes(result.svg_bytes),
            Text(similarity, style=style),
            f"{result.seconds:.1f}s",
        )
    target.print(table)


def batch_summary(
    target: Console,
    results: list[Result],
    failures: list[tuple[Path, str]],
    out_dir: Path,
    elapsed: float | None = None,
) -> None:
    target.print()
    if results:
        results_table(target, results)
        target.print()
        scored = [r.similarity for r in results if r.similarity is not None]
        total_svg = sum(r.svg_bytes for r in results)
        total_src = sum(r.source_bytes for r in results)
        summary = Table.grid(padding=(0, 2))
        summary.add_column(style=MUTED, justify="right")
        summary.add_column()
        summary.add_row("converted", f"{len(results)} file{'s' if len(results) != 1 else ''}")
        if scored:
            summary.add_row("average match", f"{sum(scored) / len(scored):.1%}")
        summary.add_row("total size", f"{human_bytes(total_svg)}  (raster {human_bytes(total_src)})")
        # Wall clock, not the sum of per-file times: with parallel workers the
        # two differ by roughly the worker count and the sum reads as a stall.
        cpu_time = sum(r.seconds for r in results)
        if elapsed is not None:
            row = f"{elapsed:.1f}s"
            if cpu_time > elapsed * 1.3:
                row += f"   ({cpu_time:.0f}s of CPU across workers)"
            summary.add_row("time", row)
        else:
            summary.add_row("time", f"{cpu_time:.1f}s")
        summary.add_row("output", Text(escape(_short_path(out_dir)), style="green"))
        target.print(summary)
    for path, reason in failures:
        target.print(Text(f"  ✗ {escape(path.name)}: {reason}", style="red"))
    warned = [(r.source.name, w) for r in results for w in r.warnings]
    if warned:
        target.print()
        for name, warning in warned[:6]:
            target.print(Text(f"  ! {escape(name)}: {warning}", style="yellow"))
        if len(warned) > 6:
            target.print(Text(f"  ! …and {len(warned) - 6} more", style=MUTED))


def failure(target: Console, message: str, hint: str | None = None) -> None:
    target.print()
    body = Text(message, style="red")
    if hint:
        body.append(f"\n{hint}", style=MUTED)
    target.print(Panel(body, border_style="red", padding=(0, 2)))
