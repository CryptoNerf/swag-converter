# Measuring tracing quality

```bash
pip install -e '.[quality]'
python benchmarks/run.py                 # compare against the baseline
python benchmarks/run.py --record        # record a new one
python benchmarks/run.py --quality max
```

It traces six drawn images and reports two numbers each. **SSIM** is how close
the rendering is overall. **edges** is how much of the original's edge energy
landed where the original's edges are — the one that moved when body copy went
from unreadable to readable while SSIM barely twitched.

The corpus is drawn rather than photographed so the numbers are comparable
between runs, and so that measuring quality never requires sending anybody's
pictures to a build runner. Each image isolates something the tracer has got
wrong before:

| image | what it is for |
| --- | --- |
| `poster-text` | type at three sizes; 20px body copy used to come out as gibberish |
| `flat-shapes` | corners that must stay corners, a hole that must stay a hole |
| `shaded-orb` | a gradient that should be answered with a gradient, not bands |
| `hairlines` | one- and two-pixel strokes, which absorption likes to eat |
| `texture` | grain that should go, over structure that should not |
| `alpha-cutout` | a soft alpha edge that must not gain a halo |

The baseline records the platform it was measured on: the corpus sets type in
whatever font the system has, so a macOS number and a Linux number are not the
same measurement. Comparing across platforms is refused rather than reported.

`.github/workflows/quality.yml` runs this on a runner — on demand, and on a
tag — so a laptop never has to.
