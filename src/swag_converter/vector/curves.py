"""Sub-pixel contour extraction and cubic Bezier fitting.

Region masks are traced with marching squares at a super-sampled resolution,
lightly de-staircased, split at genuine corners and then approximated with
cubic Beziers using Schneider's least-squares fit.  The result is a compact
``d`` attribute: smooth where the artwork is smooth, sharp where it is sharp.
"""

from __future__ import annotations

from typing import Iterable

import numpy as np
from skimage import measure


Point = np.ndarray


# --------------------------------------------------------------------------
# contours
# --------------------------------------------------------------------------

def mask_bounds(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    """Tight row/column bounds of the set pixels, or None when empty."""
    rows = np.flatnonzero(mask.any(axis=1))
    if not len(rows):
        return None
    columns = np.flatnonzero(mask.any(axis=0))
    return int(rows[0]), int(rows[-1]) + 1, int(columns[0]), int(columns[-1]) + 1


def mask_contours(mask: np.ndarray, scale: float, min_points: int = 8) -> list[np.ndarray]:
    """Trace ``mask`` and return closed polylines in SVG user units.

    ``scale`` is the super-sampling factor used to build the mask, so a
    hi-res column ``c`` maps back to user coordinate ``(c + 0.5) / scale``.

    Only the mask's bounding box is scanned: a typical emoji has dozens of
    small regions, and tracing each one across the whole raster dominated the
    run time.  The box is padded so shapes touching its edge stay closed.
    """
    bounds = mask_bounds(mask)
    if bounds is None:
        return []
    top, bottom, left, right = bounds
    padded = np.pad(mask[top:bottom, left:right].astype(np.float32), 1)
    contours: list[np.ndarray] = []
    for raw in measure.find_contours(padded, 0.5):
        if len(raw) < min_points:
            continue
        points = np.empty((len(raw), 2), dtype=np.float64)
        points[:, 0] = (raw[:, 1] + left - 0.5) / scale
        points[:, 1] = (raw[:, 0] + top - 0.5) / scale
        if len(points) > 1 and np.allclose(points[0], points[-1]):
            points = points[:-1]
        if len(points) < 3:
            continue
        contours.append(points)
    return contours


def polygon_area(points: np.ndarray) -> float:
    x, y = points[:, 0], points[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


# --------------------------------------------------------------------------
# polyline conditioning
# --------------------------------------------------------------------------

def detect_corners(points: np.ndarray, span: int, angle_degrees: float) -> np.ndarray:
    """Flag vertices whose turn over +-``span`` samples exceeds the threshold."""
    count = len(points)
    corners = np.zeros(count, dtype=bool)
    if count < max(8, 3 * span):
        return corners
    before = points - np.roll(points, span, axis=0)
    after = np.roll(points, -span, axis=0) - points
    before_norm = np.linalg.norm(before, axis=1)
    after_norm = np.linalg.norm(after, axis=1)
    usable = (before_norm > 1e-9) & (after_norm > 1e-9)
    cosine = np.zeros(count)
    cosine[usable] = np.clip(
        (before[usable] * after[usable]).sum(axis=1) / (before_norm[usable] * after_norm[usable]), -1.0, 1.0
    )
    angles = np.degrees(np.arccos(cosine))
    angles[~usable] = 0.0
    candidates = np.flatnonzero(angles > angle_degrees)
    # Non-maximum suppression keeps one vertex per genuine corner instead of a
    # cluster of near-identical ones along the same bend.
    for index in candidates[np.argsort(-angles[candidates])]:
        window = np.arange(index - span, index + span + 1) % count
        if not corners[window].any():
            corners[index] = True
    return corners


def smooth_closed(points: np.ndarray, passes: int, frozen: np.ndarray | None = None) -> np.ndarray:
    """Remove marching-squares staircase without moving detected corners."""
    if passes <= 0 or len(points) < 5:
        return points
    protected = None
    if frozen is not None and frozen.any():
        count = len(points)
        protected = np.zeros(count, dtype=bool)
        for index in np.flatnonzero(frozen):
            protected[np.arange(index - 1, index + 2) % count] = True
    current = points
    for _ in range(passes):
        smoothed = 0.25 * np.roll(current, 1, axis=0) + 0.5 * current + 0.25 * np.roll(current, -1, axis=0)
        if protected is not None:
            smoothed[protected] = current[protected]
        current = smoothed
    return current


def _drop_duplicates(points: np.ndarray, epsilon: float = 1e-9) -> np.ndarray:
    if len(points) < 2:
        return points
    keep = np.ones(len(points), dtype=bool)
    keep[1:] = (np.abs(np.diff(points, axis=0)) > epsilon).any(axis=1)
    result = points[keep]
    if len(result) > 2 and np.allclose(result[0], result[-1], atol=epsilon):
        result = result[:-1]
    return result


# --------------------------------------------------------------------------
# Schneider cubic fitting
# --------------------------------------------------------------------------

def _unit(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 1e-12 else np.zeros(2)


def _end_tangent(points: np.ndarray, reverse: bool = False) -> np.ndarray:
    span = min(4, len(points) - 1)
    if span < 1:
        return np.zeros(2)
    if reverse:
        deltas = points[-1 - np.arange(1, span + 1)] - points[-1]
    else:
        deltas = points[np.arange(1, span + 1)] - points[0]
    weights = 1.0 / np.arange(1, span + 1)
    return _unit((deltas * weights[:, None]).sum(axis=0))


def _chord_parameters(points: np.ndarray) -> np.ndarray:
    distances = np.linalg.norm(np.diff(points, axis=0), axis=1)
    cumulative = np.concatenate([[0.0], np.cumsum(distances)])
    total = cumulative[-1]
    return cumulative / total if total > 1e-12 else np.linspace(0.0, 1.0, len(points))


def _bezier_at(control: np.ndarray, t: np.ndarray) -> np.ndarray:
    t = t[:, None]
    inverse = 1.0 - t
    return (
        control[0] * inverse ** 3
        + control[1] * 3 * t * inverse ** 2
        + control[2] * 3 * t ** 2 * inverse
        + control[3] * t ** 3
    )


def _generate_bezier(points: np.ndarray, t: np.ndarray, left: np.ndarray, right: np.ndarray) -> np.ndarray:
    first, last = points[0], points[-1]
    inverse = 1.0 - t
    b0 = inverse ** 3
    b1 = 3 * t * inverse ** 2
    b2 = 3 * t ** 2 * inverse
    b3 = t ** 3
    a0 = left[None, :] * b1[:, None]
    a1 = right[None, :] * b2[:, None]
    c00 = float((a0 * a0).sum())
    c01 = float((a0 * a1).sum())
    c11 = float((a1 * a1).sum())
    residual = points - (first[None, :] * (b0 + b1)[:, None] + last[None, :] * (b2 + b3)[:, None])
    x0 = float((a0 * residual).sum())
    x1 = float((a1 * residual).sum())
    determinant = c00 * c11 - c01 * c01
    fallback = float(np.linalg.norm(last - first)) / 3.0
    if abs(determinant) < 1e-12:
        alpha_left = alpha_right = fallback
    else:
        alpha_left = (x0 * c11 - x1 * c01) / determinant
        alpha_right = (c00 * x1 - c01 * x0) / determinant
    epsilon = 1e-6 * fallback
    if alpha_left < epsilon or alpha_right < epsilon:
        alpha_left = alpha_right = fallback
    return np.array([first, first + left * alpha_left, last + right * alpha_right, last])


def _max_error(points: np.ndarray, control: np.ndarray, t: np.ndarray) -> tuple[float, int]:
    distances = np.linalg.norm(_bezier_at(control, t) - points, axis=1)
    index = int(np.argmax(distances))
    return float(distances[index]), index


def _reparameterize(points: np.ndarray, control: np.ndarray, t: np.ndarray) -> np.ndarray:
    d1 = 3.0 * (control[1:] - control[:-1])
    d2 = 2.0 * (d1[1:] - d1[:-1])
    tt = t[:, None]
    inverse = 1.0 - tt
    q = _bezier_at(control, t) - points
    q1 = d1[0] * inverse ** 2 + d1[1] * 2 * tt * inverse + d1[2] * tt ** 2
    q2 = d2[0] * inverse + d2[1] * tt
    numerator = (q * q1).sum(axis=1)
    denominator = (q1 * q1).sum(axis=1) + (q * q2).sum(axis=1)
    safe = np.abs(denominator) > 1e-12
    step = np.zeros_like(numerator)
    np.divide(numerator, denominator, out=step, where=safe)
    step[~np.isfinite(step)] = 0.0
    return np.clip(t - step, 0.0, 1.0)


def fit_open_segment(points: np.ndarray, left: np.ndarray, right: np.ndarray, tolerance: float) -> list[np.ndarray]:
    """Approximate an open polyline with as few cubic segments as possible."""
    if len(points) < 2:
        return []
    result: list[tuple[int, np.ndarray]] = []
    stack: list[tuple[int, int, np.ndarray, np.ndarray, int]] = [(0, len(points) - 1, left, right, 0)]
    while stack:
        low, high, tangent_left, tangent_right, depth = stack.pop()
        segment = points[low : high + 1]
        if len(segment) < 3:
            distance = float(np.linalg.norm(segment[-1] - segment[0])) / 3.0
            result.append(
                (low, np.array([segment[0], segment[0] + tangent_left * distance, segment[-1] + tangent_right * distance, segment[-1]]))
            )
            continue
        t = _chord_parameters(segment)
        control = _generate_bezier(segment, t, tangent_left, tangent_right)
        error, split = _max_error(segment, control, t)
        if error < tolerance:
            result.append((low, control))
            continue
        if error < tolerance * tolerance * 4.0 or depth < 2:
            refined_t = t
            for _ in range(12):
                refined_t = _reparameterize(segment, control, refined_t)
                control = _generate_bezier(segment, refined_t, tangent_left, tangent_right)
                error, split = _max_error(segment, control, refined_t)
                if error < tolerance:
                    break
            if error < tolerance:
                result.append((low, control))
                continue
        if depth >= 24 or split <= 0 or split >= len(segment) - 1:
            result.append((low, control))
            continue
        centre = _unit(segment[split - 1] - segment[split + 1])
        stack.append((low + split, high, -centre, tangent_right, depth + 1))
        stack.append((low, low + split, tangent_left, centre, depth + 1))
    result.sort(key=lambda item: item[0])
    return [control for _, control in result]


def fit_closed_contour(
    points: np.ndarray,
    tolerance: float,
    corner_span: int = 3,
    corner_angle: float = 62.0,
    smooth_passes: int = 2,
) -> list[np.ndarray]:
    """Fit a closed contour, honouring corners and keeping the seam smooth."""
    points = _drop_duplicates(np.asarray(points, dtype=np.float64))
    if len(points) < 4:
        return []
    corners = detect_corners(points, corner_span, corner_angle)
    points = smooth_closed(points, smooth_passes, corners)
    points = _drop_duplicates(points)
    if len(points) < 4:
        return []
    corners = detect_corners(points, corner_span, corner_angle)
    indices = np.flatnonzero(corners)
    if len(indices) == 0:
        # No corners: start the seam anywhere and keep it tangent-continuous.
        loop = np.vstack([points, points[:1]])
        seam = _unit(loop[1] - loop[-2])
        return fit_open_segment(loop, seam, -seam, tolerance)
    if len(indices) == 1:
        rolled = np.roll(points, -int(indices[0]), axis=0)
        loop = np.vstack([rolled, rolled[:1]])
        return fit_open_segment(loop, _end_tangent(loop), _end_tangent(loop, reverse=True), tolerance)
    segments: list[np.ndarray] = []
    count = len(points)
    for position, start in enumerate(indices):
        stop = indices[(position + 1) % len(indices)]
        length = (stop - start) % count
        piece = points[(start + np.arange(length + 1)) % count]
        if len(piece) < 2:
            continue
        segments.extend(fit_open_segment(piece, _end_tangent(piece), _end_tangent(piece, reverse=True), tolerance))
    return segments


# --------------------------------------------------------------------------
# path serialisation
# --------------------------------------------------------------------------

def _number(value: float, precision: int) -> str:
    """Shortest SVG-legal spelling of a number, e.g. ``-0.50`` -> ``-.5``."""
    text = f"{value:.{precision}f}".rstrip("0").rstrip(".")
    if text in ("", "-", "-0", "0"):
        return "0"
    if text.startswith("0."):
        return text[1:]
    if text.startswith("-0."):
        return "-" + text[2:]
    return text


def _join(tokens: list[str]) -> str:
    """Concatenate numbers, dropping every separator SVG does not need.

    A leading ``-`` always terminates the previous number.  A leading ``.``
    only does so when the previous number already contains a dot, otherwise
    ``15`` + ``.5`` would silently read back as the single number ``15.5``.
    """
    parts = [tokens[0]]
    for index in range(1, len(tokens)):
        token, previous = tokens[index], tokens[index - 1]
        if token[0] == "-" or (token[0] == "." and "." in previous):
            parts.append(token)
        else:
            parts.append(" " + token)
    return "".join(parts)


def contour_path_data(segments: Iterable[np.ndarray], precision: int = 2) -> str:
    """Serialise fitted cubics as one closed sub-path, using relative curves.

    Deltas are taken against the *rounded* pen position, so rounding error
    cannot accumulate along a long contour.
    """
    segments = list(segments)
    if not segments:
        return ""
    quantum = 10.0 ** -precision
    start = np.round(segments[0][0] / quantum) * quantum
    parts = ["M" + _join([_number(start[0], precision), _number(start[1], precision)])]
    pen = start.copy()
    for control in segments:
        # In a relative cubic all three points are offsets from the segment's
        # start point, not from one another.
        tokens: list[str] = []
        last = None
        for point in control[1:]:
            delta = np.round((point - pen) / quantum) * quantum
            tokens.extend([_number(delta[0], precision), _number(delta[1], precision)])
            last = delta
        parts.append("c" + _join(tokens))
        pen = pen + last
    parts.append("z")
    return "".join(parts)


def trace_mask(
    mask: np.ndarray,
    scale: float,
    tolerance: float,
    min_area: float,
    precision: int = 2,
    corner_angle: float = 62.0,
    smooth_passes: int = 2,
) -> tuple[str, int]:
    """Full mask -> path pipeline; returns ``(d, node_count)``."""
    corner_span = max(2, int(round(scale)))
    pieces: list[str] = []
    nodes = 0
    for contour in mask_contours(mask, scale):
        if polygon_area(contour) < min_area:
            continue
        fitted = fit_closed_contour(contour, tolerance, corner_span, corner_angle, smooth_passes)
        if not fitted:
            continue
        data = contour_path_data(fitted, precision)
        if data:
            pieces.append(data)
            nodes += len(fitted) + 1
    return "".join(pieces), nodes
