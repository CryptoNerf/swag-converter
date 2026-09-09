"""Paint models for traced regions: flat, linear gradient or radial gradient.

Apple Emoji are mostly smooth shading over few shapes.  Representing a shaded
region as one path plus a gradient keeps both the look and the node count
sane, where flat-colour tracing would emit dozens of banded slivers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


# Weighted RGB distance ("redmean"); a close, conversion-free stand-in for
# CIE76 dE that is cheap enough to call inside the region-merge loop.
def perceptual_distance(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    red_mean = 0.5 * (a[..., 0] + b[..., 0])
    delta = a - b
    weights = np.stack(
        [2.0 + red_mean / 256.0, np.full_like(red_mean, 4.0), 2.0 + (255.0 - red_mean) / 256.0], axis=-1
    )
    return np.sqrt((weights * delta * delta).sum(axis=-1)) / 3.0


def _num(value: float, precision: int = 2) -> str:
    rounded = round(float(value), precision)
    if rounded == 0:
        return "0"
    text = f"{rounded:.{precision}f}".rstrip("0").rstrip(".")
    return text or "0"


def to_hex(color: np.ndarray) -> str:
    values = np.clip(np.rint(color), 0, 255).astype(int)
    return "#{:02X}{:02X}{:02X}".format(*values)


@dataclass
class Paint:
    kind: str  # flat | linear | radial
    residual: float
    color: np.ndarray | None = None
    opacity: float = 1.0
    stops: list[tuple[float, np.ndarray, float]] = field(default_factory=list)
    geometry: dict[str, float] = field(default_factory=dict)

    @property
    def stop_count(self) -> int:
        return len(self.stops)


def _sample(indices_count: int, limit: int, seed: int) -> np.ndarray | None:
    if indices_count <= limit:
        return None
    generator = np.random.default_rng(seed)
    return generator.choice(indices_count, size=limit, replace=False)


def _stops_from_parameter(
    parameter: np.ndarray, colors: np.ndarray, alpha: np.ndarray, count: int, low: float, high: float
) -> list[tuple[float, np.ndarray, float]]:
    """Average colour inside ``count`` quantile bins of the ramp parameter.

    ``low``/``high`` are the values the SVG paint server maps to offset 0 and
    1.  For a linear gradient those are the two end points; for a radial one
    offset 0 is always the centre (r = 0) whatever the region's inner radius,
    so the caller must pass ``low = 0`` there or the ramp renders stretched.

    Sampling the real pixels rather than trusting the fitted line is what
    reproduces the non-linear ramps Apple actually uses.
    """
    span = high - low
    if span < 1e-9:
        return [(0.0, colors.mean(axis=0), float(alpha.mean()))]
    edges = np.quantile(parameter, np.linspace(0.0, 1.0, count + 1))
    stops: list[tuple[float, np.ndarray, float]] = []
    for index in range(count):
        start, stop = edges[index], edges[index + 1]
        if index == count - 1:
            selected = (parameter >= start) & (parameter <= stop)
        else:
            selected = (parameter >= start) & (parameter < stop)
        if selected.sum() < 2:
            continue
        offset = float(np.clip((parameter[selected].mean() - low) / span, 0.0, 1.0))
        stops.append((offset, colors[selected].mean(axis=0), float(alpha[selected].mean())))
    if len(stops) < 2:
        return [(0.0, colors.mean(axis=0), float(alpha.mean()))]
    stops.sort(key=lambda item: item[0])
    # Bin means sit at bin centroids, so the ramp would stop short of the data.
    # Extend it to the real extremes; anything beyond them is padded by the
    # renderer and is never painted anyway.
    first = float(np.clip((float(parameter.min()) - low) / span, 0.0, 1.0))
    last = float(np.clip((float(parameter.max()) - low) / span, 0.0, 1.0))
    if stops[0][0] - first > 1e-3:
        stops.insert(0, (first,) + _extrapolate(stops[0], stops[1], first))
    else:
        stops[0] = (first,) + stops[0][1:]
    if last - stops[-1][0] > 1e-3:
        stops.append((last,) + _extrapolate(stops[-1], stops[-2], last))
    else:
        stops[-1] = (last,) + stops[-1][1:]
    return stops


def _extrapolate(
    near: tuple[float, np.ndarray, float], far: tuple[float, np.ndarray, float], target: float
) -> tuple[np.ndarray, float]:
    span = near[0] - far[0]
    if abs(span) < 1e-9:
        return np.clip(near[1], 0.0, 255.0), float(np.clip(near[2], 0.0, 1.0))
    ratio = (target - far[0]) / span
    color = far[1] + (near[1] - far[1]) * ratio
    opacity = far[2] + (near[2] - far[2]) * ratio
    return np.clip(color, 0.0, 255.0), float(np.clip(opacity, 0.0, 1.0))


def _evaluate_stops(stops: list[tuple[float, np.ndarray, float]], offsets: np.ndarray) -> np.ndarray:
    positions = np.array([stop[0] for stop in stops])
    colors = np.array([stop[1] for stop in stops])
    return np.stack([np.interp(offsets, positions, colors[:, channel]) for channel in range(3)], axis=-1)


def _evaluate_stop_alpha(stops: list[tuple[float, np.ndarray, float]], offsets: np.ndarray) -> np.ndarray:
    positions = np.array([stop[0] for stop in stops])
    return np.interp(offsets, positions, np.array([stop[2] for stop in stops]))


def _alpha_error(actual: np.ndarray, predicted: np.ndarray, weight: float) -> float:
    """Opacity error, rescaled to sit on the same axis as the colour residual.

    Opaque glyphs are normalised to alpha 1 upstream, so this is exactly zero
    for them and only bites on genuinely translucent artwork - steam, glass,
    smoke - where a single averaged fill-opacity would flatten the veil.
    """
    return float(np.abs(actual - predicted).mean()) * weight


def _prune_stops(
    stops: list[tuple[float, np.ndarray, float]], tolerance: float
) -> list[tuple[float, np.ndarray, float]]:
    """Drop stops whose colour a straight interpolation already reproduces."""
    while len(stops) > 2:
        best_index, best_error = None, None
        for index in range(1, len(stops) - 1):
            reduced = stops[:index] + stops[index + 1 :]
            predicted = _evaluate_stops(reduced, np.array([stops[index][0]]))[0]
            error = float(perceptual_distance(predicted, stops[index][1]))
            if best_error is None or error < best_error:
                best_index, best_error = index, error
        if best_error is None or best_error > tolerance:
            break
        stops.pop(best_index)
    return stops


def _signal(colors: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """Colour plus, where it varies, opacity - on one comparable scale.

    The ramp direction has to come from whatever actually changes across the
    region.  A steam veil is a single colour at many opacities, so fitting the
    direction from colour alone would find nothing to fit.
    """
    if float(alpha.max() - alpha.min()) < 0.02:
        return colors
    return np.column_stack([colors, alpha * 255.0])


def _linear_parameter(coords: np.ndarray, colors: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
    centred = coords - coords.mean(axis=0)
    design = np.column_stack([np.ones(len(centred)), centred])
    try:
        solution, *_ = np.linalg.lstsq(design, colors, rcond=None)
    except np.linalg.LinAlgError:
        return None
    slopes = solution[1:].T  # (3, 2): per-channel spatial gradient
    if not np.isfinite(slopes).all() or np.abs(slopes).max() < 1e-9:
        return None
    _, _, right = np.linalg.svd(slopes, full_matrices=False)
    direction = right[0]
    norm = float(np.linalg.norm(direction))
    if norm < 1e-9:
        return None
    return direction / norm, coords.mean(axis=0)


def _radial_centre(coords: np.ndarray, colors: np.ndarray) -> np.ndarray | None:
    centred = coords - coords.mean(axis=0)
    quadratic = (centred ** 2).sum(axis=1)
    design = np.column_stack([np.ones(len(centred)), centred, quadratic])
    try:
        solution, *_ = np.linalg.lstsq(design, colors, rcond=None)
    except np.linalg.LinAlgError:
        return None
    linear = solution[1:3].T  # (channels, 2)
    curvature = solution[3]  # (channels,)
    weight = float((curvature ** 2).sum())
    if weight < 1e-12:
        return None
    offset = -(linear * curvature[:, None]).sum(axis=0) / (2.0 * weight)
    if not np.isfinite(offset).all():
        return None
    return coords.mean(axis=0) + offset


def fit_paint(
    coords: np.ndarray,
    colors: np.ndarray,
    alpha: np.ndarray,
    settings: dict[str, Any],
    seed: int = 0,
) -> Paint:
    """Pick the cheapest paint model that explains the region's pixels."""
    max_stops = int(settings.get("gradient_stops", 5))
    flat_tolerance = float(settings.get("flat_tolerance", 2.0))
    gradient_gain = float(settings.get("gradient_gain", 0.55))
    stop_prune = float(settings.get("stop_prune_tolerance", 1.4))
    limit = int(settings.get("fit_sample_limit", 4000))

    picked = _sample(len(coords), limit, seed)
    if picked is not None:
        coords, colors, alpha = coords[picked], colors[picked], alpha[picked]

    alpha_weight = float(settings.get("alpha_residual_weight", 90.0))
    mean_color = colors.mean(axis=0)
    mean_alpha = float(alpha.mean())
    flat_residual = float(perceptual_distance(colors, mean_color[None, :]).mean()) + _alpha_error(
        alpha, mean_alpha, alpha_weight
    )
    best = Paint(kind="flat", residual=flat_residual, color=mean_color, opacity=mean_alpha)
    if flat_residual <= flat_tolerance or len(coords) < 16 or max_stops < 2:
        return best


    candidates: list[Paint] = []

    signal = _signal(colors, alpha)

    linear = _linear_parameter(coords, signal)
    if linear is not None:
        direction, origin = linear
        parameter = (coords - origin) @ direction
        low, high = float(parameter.min()), float(parameter.max())
        stops = _prune_stops(_stops_from_parameter(parameter, colors, alpha, max_stops, low, high), stop_prune)
        if len(stops) >= 2:
            offsets = np.clip((parameter - low) / max(high - low, 1e-9), 0.0, 1.0)
            residual = float(
                perceptual_distance(colors, _evaluate_stops(stops, offsets)).mean()
            ) + _alpha_error(alpha, _evaluate_stop_alpha(stops, offsets), alpha_weight)
            start = origin + direction * low
            end = origin + direction * high
            candidates.append(
                Paint(
                    kind="linear",
                    residual=residual,
                    opacity=mean_alpha,
                    stops=stops,
                    geometry={"x1": start[0], "y1": start[1], "x2": end[0], "y2": end[1]},
                )
            )

    centre = _radial_centre(coords, signal)
    if centre is not None:
        span = float(np.linalg.norm(coords.max(axis=0) - coords.min(axis=0))) + 1e-9
        # Reject wildly extrapolated centres; they produce unstable ramps.
        if float(np.linalg.norm(centre - coords.mean(axis=0))) < 2.5 * span:
            radius = np.linalg.norm(coords - centre, axis=1)
            outer = float(radius.max())
            if outer > 1e-6:
                stops = _prune_stops(
                    _stops_from_parameter(radius, colors, alpha, max_stops, 0.0, outer), stop_prune
                )
                if len(stops) >= 2:
                    offsets = np.clip(radius / outer, 0.0, 1.0)
                    residual = float(
                        perceptual_distance(colors, _evaluate_stops(stops, offsets)).mean()
                    ) + _alpha_error(alpha, _evaluate_stop_alpha(stops, offsets), alpha_weight)
                    candidates.append(
                        Paint(
                            kind="radial",
                            residual=residual,
                            opacity=mean_alpha,
                            stops=stops,
                            geometry={"cx": centre[0], "cy": centre[1], "r": outer},
                        )
                    )

    if not candidates:
        return best
    # A gradient costs a <defs> entry and a paint-server reference, so it has
    # to beat the flat fill by a clear margin rather than by a hair.
    cheapest = min(candidates, key=lambda candidate: candidate.residual)
    return cheapest if cheapest.residual < flat_residual * gradient_gain else best


def paint_definition(paint: Paint, identifier: str) -> str | None:
    """Serialise a gradient paint server; flat paints need no definition."""
    if paint.kind == "flat":
        return None
    stops = []
    for offset, color, alpha in paint.stops:
        attributes = f'offset="{offset:.3f}".rstrip' if False else f'offset="{round(offset, 3):g}"'
        stop = f'<stop {attributes} stop-color="{to_hex(color)}"'
        if alpha < 0.995:
            stop += f' stop-opacity="{round(float(alpha), 3):g}"'
        stops.append(stop + "/>")
    body = "".join(stops)
    geometry = paint.geometry
    if paint.kind == "linear":
        return (
            f'<linearGradient id="{identifier}" gradientUnits="userSpaceOnUse" '
            f'x1="{_num(geometry["x1"])}" y1="{_num(geometry["y1"])}" '
            f'x2="{_num(geometry["x2"])}" y2="{_num(geometry["y2"])}">{body}</linearGradient>'
        )
    return (
        f'<radialGradient id="{identifier}" gradientUnits="userSpaceOnUse" '
        f'cx="{_num(geometry["cx"])}" cy="{_num(geometry["cy"])}" '
        f'r="{_num(geometry["r"])}">{body}</radialGradient>'
    )


def paint_attributes(paint: Paint, identifier: str | None) -> str:
    if paint.kind == "flat":
        fill = to_hex(paint.color if paint.color is not None else np.zeros(3))
    else:
        fill = f"url(#{identifier})"
    attributes = f' fill="{fill}"'
    if paint.kind == "flat" and paint.opacity < 0.995:
        attributes += f' fill-opacity="{_num(paint.opacity, 3)}"'
    return attributes
