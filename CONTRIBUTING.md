# Contributing

Thanks for taking a look.

## Getting set up

```bash
pip install -e '.[dev]'
pytest
```

## Before opening a pull request

- `pytest` passes. `pytest -m 'not slow'` skips the timing guards if you are
  iterating quickly, but run the full suite before you push.
- If you change tracing behaviour, say what you measured. Quality here is
  judged by looking at the output next to the source, not by a metric alone —
  several real bugs in this code moved SSIM by less than a percent while being
  obvious on screen.
- New behaviour comes with a test. The tests in `tests/test_performance.py`
  exist because a photograph once took 22 minutes and collapsed to two shapes;
  that class of regression is easy to reintroduce.

## Where things live

| file | responsibility |
| --- | --- |
| `image.py` | turning any input file into straight-alpha RGBA at a sane size |
| `analyze.py` | telling artwork from photographs |
| `presets.py` | the tuned numbers, and what each preset trades away |
| `vector/segment.py` | clustering and gradient-aware region merging |
| `vector/paint.py` | flat / linear / radial paint models |
| `vector/curves.py` | contour extraction and Bézier fitting |
| `vector/render.py` | SVG assembly |
| `ui.py` / `cli.py` | terminal presentation and argument handling |
