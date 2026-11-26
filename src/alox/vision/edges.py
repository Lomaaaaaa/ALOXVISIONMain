"""Edge and shape tools: the NumPy/SciPy equivalents of the NI Vision functions used by Detect Circle and Measure Oxyd.

Spec: ``docs/vision-core/map.md`` Part 3 §3.5 (Detect Circle), §3.10 (Measure Oxyd), Part 4 §3.2 (pre-processing),
§3.4 (measurements on images). NI Vision is closed source: every function here is a **documented equivalent** with the
NI parameters, not a bit-exact copy. Coordinates are image pixels, x right, y down; angles in degrees counter-clockwise
with y up (NI convention: a point at angle θ on a circle is ``(cx + r·cos θ, cy − r·sin θ)``).

NI → here:

- ``IMAQ Edge Tool 3`` / the 1-D edge detection inside ``IMAQ Spoke 4``, ``IMAQ Find Circular Edge 3``,
  ``IMAQ Rake 3`` (Edge Options: Polarity, Kernel Size, Width, Minimum Edge Strength, Interpolation "Bilinear Fixed",
  Data Processing "Average") → :func:`line_profile` (bilinear samples every pixel along the line, averaged over
  ``width`` parallel lines) + :func:`edge_strengths` (kernel ``[−1…−1, 0, +1…+1]`` of ``kernel_size`` taps,
  normalised: a step of contrast C gives strength C) + :func:`detect_edges` (local extrema of |strength| ≥ minimum,
  polarity, parabolic sub-pixel position). NI's exact kernel normalisation and sub-pixel model are not documented.
- ``IMAQ Spoke 4`` (radial search lines, "Get First Edge") → :func:`spoke_lines` + :func:`first_edges`. NI's
  ``Step Size`` / gap is read as the spacing in pixels between lines on the outer circle (assumption).
- ``IMAQ Find Circular Edge 3`` → :func:`find_circular_edge`: first edge on each spoke of the annulus, then a
  least-squares circle with one outlier-rejection pass (|residual| > max(2 px, 3·MAD)). NI's own fit is unknown.
- ``IMAQ Fit Circle 2`` (Pixel Radius = Fit Max Dist., Min Score 0–1000, Max Iterations, Reject Outliers) →
  :func:`fit_circle_robust`: with Reject Outliers a seeded RANSAC on point triples, then a least-squares refit on the
  inliers; the score is ``1000 × inliers / points`` (NI's score definition is not documented).
- ``IMAQ Detect Circles`` / ``IMAQ Detect Ellipses`` (curve extraction + shape matching) → :func:`detect_circles`:
  gradient-direction Hough transform (centre accumulator, then radius histogram, then least-squares refinement); the
  score is ``1000 × fraction of the circle covered by edge pixels``; ``Min Length`` is the minimum covered arc length
  in pixels; ellipses take the radius ``(minor + major)/2`` of an algebraic ellipse fit, as the LabVIEW caller does.
  NI ``Occlusion`` and ``Extend research`` have no equivalent (partial circles are always accepted down to the score).
- ``IMAQ Local Threshold`` / ``RemoveParticle`` / ``RejectBorder`` / ``FillHole`` / ``Particle Filter`` (Heywood
  circularity) / ``IMAQ Find Circles`` → :func:`local_threshold`, :func:`remove_small_particles`,
  :func:`reject_border`, :func:`fill_holes`, :func:`particles`, :func:`find_blob_circles` (one circle per particle:
  centroid and area-equivalent radius; NI separates touching circles with a distance map, not reproduced).
- ``IMAQ Match Geometric Pattern 2`` → :func:`match_template`: normalised cross-correlation (no rotation, no scale,
  no occlusion: geometric matching has no SciPy equivalent).
- ``IMAQ GrayMorphology`` → :func:`grey_morphology` (square k×k structuring element, k rounded up to odd, skipped
  below 3, as ``alox_vision_detectcircle_preprocessing_conf d``; NI "proper" opening/closing are plain ones here).
- ``IMAQ Resample`` (Zero Order) → :func:`alox.vision.focus.resample_nearest`.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import IntEnum

import numpy as np
from scipy import ndimage, signal

from .circles import NAN_CIRCLE, Circle, circle_from_3_points, fit_circle_least_squares
from .geometry import PointsLike, as_points

__all__ = [
    "CircularEdge",
    "DetectedCircle",
    "Edge",
    "EdgeOptions",
    "EdgePolarity",
    "FitCircleResult",
    "Particle",
    "SearchDirection",
    "SpokeLine",
    "TemplateMatch",
    "detect_circles",
    "detect_edges",
    "edge_strengths",
    "fill_holes",
    "find_blob_circles",
    "find_circular_edge",
    "first_edges",
    "fit_circle_robust",
    "fit_ellipse_radius",
    "grey_morphology",
    "line_profile",
    "local_threshold",
    "match_template",
    "odd_up",
    "particles",
    "reject_border",
    "remove_small_particles",
    "spoke_lines",
]


class EdgePolarity(IntEnum):
    """``alox_typedef_measure_oxyd_edge_polarity``: Rising = dark → bright along the search line."""

    ALL = 0
    RISING = 1
    FALLING = 2


class SearchDirection(IntEnum):
    """``alox_typedef_measure_oxyd_search_direction``."""

    OUTSIDE_TO_INSIDE = 0
    INSIDE_TO_OUTSIDE = 1


@dataclass(frozen=True)
class EdgeOptions:
    """NI ``Edge Options`` (the interpolation is always bilinear, the projection an average)."""

    polarity: EdgePolarity = EdgePolarity.ALL
    kernel_size: int = 3
    width: int = 3
    min_strength: float = 10.0


@dataclass(frozen=True)
class Edge:
    """One edge on a search line: position, distance from the line start (px) and signed strength (grey levels)."""

    x: float
    y: float
    distance: float
    strength: float


SpokeLine = tuple[tuple[float, float], tuple[float, float]]


def odd_up(n: int) -> int:
    """``tools_round_u32_to_upper_odd_value``: an even ``n`` becomes ``n + 1``."""
    n = int(n)
    return n + 1 if n % 2 == 0 else n


# ================================================================================================== 1-D edges
def line_profile(image: np.ndarray, start: Sequence[float], end: Sequence[float], width: int = 1) -> np.ndarray:
    """Grey levels every pixel from ``start`` to ``end`` (``floor(length) + 1`` samples), bilinear, averaged over
    ``width`` parallel lines one pixel apart (NI "Average" projection). Outside the image: the nearest pixel."""
    x0, y0, x1, y1 = float(start[0]), float(start[1]), float(end[0]), float(end[1])
    length = math.hypot(x1 - x0, y1 - y0)
    n = math.floor(length) + 1
    if length == 0.0:
        ux, uy = 1.0, 0.0
    else:
        ux, uy = (x1 - x0) / length, (y1 - y0) / length
    t = np.arange(n, dtype=np.float64)
    w = max(1, int(width))
    offsets = np.arange(w, dtype=np.float64) - (w - 1) / 2.0
    xs = x0 + t[None, :] * ux - offsets[:, None] * uy
    ys = y0 + t[None, :] * uy + offsets[:, None] * ux
    img = np.asarray(image, dtype=np.float64)
    samples = ndimage.map_coordinates(img, [ys.ravel(), xs.ravel()], order=1, mode="nearest")
    return samples.reshape(w, n).mean(axis=0)


def edge_strengths(profile: np.ndarray, kernel_size: int = 3) -> np.ndarray:
    """Signed contrast at every sample: ``mean(p[i+1 … i+h]) − mean(p[i−h … i−1])`` with ``h = max(1, k // 2)``
    (0 where the kernel does not fit). A step of contrast C between two samples gives C on both of them."""
    p = np.asarray(profile, dtype=np.float64)
    h = max(1, int(kernel_size) // 2)
    n = p.size
    s = np.zeros(n)
    if n < 2 * h + 1:
        return s
    c = np.concatenate(([0.0], np.cumsum(p)))
    i = np.arange(h, n - h)
    after = (c[i + 1 + h] - c[i + 1]) / h
    before = (c[i] - c[i - h]) / h
    s[h:n - h] = after - before
    return s


def detect_edges(profile: np.ndarray, options: EdgeOptions) -> list[tuple[float, float]]:
    """``(position, strength)`` of the edges of a profile, in order: local maxima of ``|strength|`` at least
    ``min_strength`` with the requested polarity; a single-sample maximum is refined by a parabola through its two
    neighbours, a plateau (a ramp or a step between samples) gives its centre."""
    s = edge_strengths(profile, options.kernel_size)
    a = np.abs(s)
    edges: list[tuple[float, float]] = []
    n = s.size
    i = 1
    while i < n - 1:
        if a[i] <= a[i - 1] or a[i] < options.min_strength:
            i += 1
            continue
        j = i
        while j + 1 < n and a[j + 1] == a[i]:
            j += 1                                            # plateau i..j
        if j + 1 < n and a[j + 1] < a[i]:
            wanted = (options.polarity is EdgePolarity.ALL or (options.polarity is EdgePolarity.RISING) == (s[i] > 0))
            if wanted:
                if j > i:
                    position = (i + j) / 2.0
                else:
                    lo, mid, hi = a[i - 1], a[i], a[i + 1]
                    den = lo - 2.0 * mid + hi
                    position = i + (float(np.clip(0.5 * (lo - hi) / den, -0.5, 0.5)) if den != 0.0 else 0.0)
                edges.append((position, float(s[i])))
        i = j + 1
    return edges


def find_edges_on_line(image: np.ndarray, start: Sequence[float], end: Sequence[float],
                       options: EdgeOptions) -> list[Edge]:
    """Every edge along the segment ``start → end`` (``IMAQ Edge Tool 3``)."""
    profile = line_profile(image, start, end, options.width)
    x0, y0, x1, y1 = float(start[0]), float(start[1]), float(end[0]), float(end[1])
    length = math.hypot(x1 - x0, y1 - y0)
    ux, uy = ((x1 - x0) / length, (y1 - y0) / length) if length else (1.0, 0.0)
    return [Edge(x0 + d * ux, y0 + d * uy, d, strength) for d, strength in detect_edges(profile, options)]


# ================================================================================================== spokes, circles
def spoke_lines(center: Sequence[float], r_inner: float, r_outer: float, *, step: float,
                direction: SearchDirection, start_deg: float = 0.0, end_deg: float = 360.0) -> list[SpokeLine]:
    """Radial search lines of an annulus (``IMAQ Spoke 4``), in increasing angle; ``step`` = pixels between lines on
    the outer circle (at least 3 lines on a full circle). ``OUTSIDE_TO_INSIDE`` lines start on the outer circle."""
    cx, cy = float(center[0]), float(center[1])
    span = float(end_deg) - float(start_deg)
    full = abs(span) >= 360.0
    arc = math.radians(abs(span)) * max(float(r_outer), 1.0)
    count = max(3 if full else 2, int(arc // max(float(step), 1e-9)))
    angles = (float(start_deg) + span * np.arange(count) / count if full
              else np.linspace(float(start_deg), float(end_deg), count))
    lines: list[SpokeLine] = []
    for angle in np.radians(angles):
        c, s = math.cos(angle), -math.sin(angle)
        inner = (cx + r_inner * c, cy + r_inner * s)
        outer = (cx + r_outer * c, cy + r_outer * s)
        lines.append((outer, inner) if direction is SearchDirection.OUTSIDE_TO_INSIDE else (inner, outer))
    return lines


def first_edges(image: np.ndarray, lines: Sequence[SpokeLine], options: EdgeOptions) -> list[Edge | None]:
    """The first edge on each search line ("Get First Edge"), None where there is none."""
    out: list[Edge | None] = []
    for start, end in lines:
        found = find_edges_on_line(image, start, end, options)
        out.append(found[0] if found else None)
    return out


@dataclass(frozen=True)
class FitCircleResult:
    """``IMAQ Fit Circle 2`` outputs: circle, inlier mask (all True without outlier rejection), score 0–1000, valid."""

    circle: Circle
    inliers: np.ndarray
    score: float
    valid: bool


def _residuals(points: np.ndarray, circle: Circle) -> np.ndarray:
    return np.abs(np.hypot(points[:, 0] - circle.x, points[:, 1] - circle.y) - circle.radius)


def fit_circle_robust(points: PointsLike, *, max_distance: float = 3.0, min_score: float = 500.0,
                      max_iterations: int = 500, reject_outliers: bool = True, seed: int = 0) -> FitCircleResult:
    """``IMAQ Fit Circle 2`` equivalent. Without outlier rejection: least squares on every point. With it: up to
    ``max_iterations`` random triples (seeded, reproducible), the triple whose circle has the most points within
    ``max_distance`` wins (ties: smaller residual sum), least-squares refit on its inliers, inliers recomputed.
    ``score = 1000 × inliers / points``; ``valid`` = at least 3 points, a finite circle and ``score ≥ min_score``."""
    pts = as_points(points)
    n = pts.shape[0]
    none = np.zeros(n, dtype=bool)
    if n < 3 or not np.all(np.isfinite(pts)):
        return FitCircleResult(NAN_CIRCLE, none, 0.0, False)
    if not reject_outliers:
        circle = fit_circle_least_squares(pts)
        if circle.is_nan:
            return FitCircleResult(NAN_CIRCLE, none, 0.0, False)
        inside = _residuals(pts, circle) <= max_distance
        score = 1000.0 * float(inside.mean())
        return FitCircleResult(circle, np.ones(n, dtype=bool), score, score >= min_score)
    rng = np.random.default_rng(seed)
    best_mask: np.ndarray | None = None
    best_key = (-1, math.inf)
    for _ in range(max(1, int(max_iterations))):
        i, j, k = rng.choice(n, size=3, replace=False)
        c = circle_from_3_points(pts[i], pts[j], pts[k])
        if c.is_nan:
            continue
        res = _residuals(pts, c)
        mask = res <= max_distance
        key = (int(mask.sum()), float(res[mask].sum()))
        if key[0] > best_key[0] or (key[0] == best_key[0] and key[1] < best_key[1]):
            best_key, best_mask = key, mask
        if key[0] == n:
            break
    if best_mask is None or best_mask.sum() < 3:
        return FitCircleResult(NAN_CIRCLE, none, 0.0, False)
    circle = fit_circle_least_squares(pts[best_mask])
    if circle.is_nan:
        return FitCircleResult(NAN_CIRCLE, none, 0.0, False)
    mask = _residuals(pts, circle) <= max_distance
    if mask.sum() >= 3:
        refit = fit_circle_least_squares(pts[mask])
        if not refit.is_nan:
            circle = refit
            mask = _residuals(pts, circle) <= max_distance
    score = 1000.0 * float(mask.mean())
    return FitCircleResult(circle, mask, score, bool(mask.sum() >= 3 and score >= min_score))


@dataclass(frozen=True)
class CircularEdge:
    """``IMAQ Find Circular Edge 3`` outputs: found, circle, the edge points (one per spoke with an edge) and lines."""

    found: bool
    circle: Circle
    points: np.ndarray
    lines: tuple[SpokeLine, ...]


def find_circular_edge(image: np.ndarray, center: Sequence[float], r_inner: float, r_outer: float, *,
                       direction: SearchDirection, options: EdgeOptions, step: float,
                       start_deg: float = 0.0, end_deg: float = 360.0) -> CircularEdge:
    """First edge on each spoke of the annulus, then a circle: least squares, one pass removing the points farther
    than ``max(2 px, 3·MAD)`` from it, least squares again. Not found: fewer than 3 edges, an empty annulus
    (``r_outer ≤ r_inner``) or a degenerate fit."""
    if not (math.isfinite(r_inner) and math.isfinite(r_outer)) or r_outer <= max(r_inner, 0.0):
        return CircularEdge(False, NAN_CIRCLE, np.empty((0, 2)), ())
    lines = tuple(spoke_lines(center, max(r_inner, 0.0), r_outer, step=step, direction=direction,
                              start_deg=start_deg, end_deg=end_deg))
    edges = [e for e in first_edges(image, lines, options) if e is not None]
    pts = np.array([(e.x, e.y) for e in edges], dtype=np.float64).reshape(-1, 2)
    if pts.shape[0] < 3:
        return CircularEdge(False, NAN_CIRCLE, pts, lines)
    circle = fit_circle_least_squares(pts)
    if not circle.is_nan:
        res = _residuals(pts, circle)
        limit = max(2.0, 3.0 * float(np.median(np.abs(res - np.median(res)))) / 0.6745)
        keep = res <= limit
        if 3 <= keep.sum() < pts.shape[0]:
            refit = fit_circle_least_squares(pts[keep])
            if not refit.is_nan:
                circle = refit
    return CircularEdge(not circle.is_nan, circle, pts, lines)


# ================================================================================================== shape detection
@dataclass(frozen=True)
class DetectedCircle:
    """A detected circle (pixels) and its score 0–1000."""

    x: float
    y: float
    radius: float
    score: float


def _gradients(image: np.ndarray, smoothing: float = 1.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    img = np.asarray(image, dtype=np.float32)
    if smoothing > 0:
        img = ndimage.gaussian_filter(img, smoothing)      # orientation of pixelated edges: a few degrees at most
    gx = ndimage.sobel(img, axis=1) / 4.0                 # a step of contrast C gives C
    gy = ndimage.sobel(img, axis=0) / 4.0
    return gx, gy, np.hypot(gx, gy)


def _thin_edges(gx: np.ndarray, gy: np.ndarray, mag: np.ndarray, threshold: float) -> np.ndarray:
    """Pixels at least ``threshold`` that are maxima of the magnitude along their gradient (4 direction bins)."""
    angle = np.mod(np.degrees(np.arctan2(gy, gx)), 180.0)
    padded = np.pad(mag, 1, mode="constant")
    h, w = mag.shape

    def shifted(dy: int, dx: int) -> np.ndarray:
        return padded[1 + dy:1 + dy + h, 1 + dx:1 + dx + w]

    keep = np.zeros_like(mag, dtype=bool)
    for lo, hi, (dy, dx) in ((-1, 22.5, (0, 1)), (157.5, 181, (0, 1)), (22.5, 67.5, (1, 1)),
                             (67.5, 112.5, (1, 0)), (112.5, 157.5, (1, -1))):
        sel = (angle >= lo) & (angle < hi)
        keep |= sel & (mag >= shifted(dy, dx)) & (mag >= shifted(-dy, -dx))
    return keep & (mag >= threshold)


def fit_ellipse_radius(points: PointsLike) -> float:
    """``(minor + major)/2`` of the algebraic ellipse through the points (``a x² + b xy + c y² + d x + e y = 1`` in
    centred coordinates); NaN when the conic is not an ellipse or there are fewer than 5 points."""
    pts = as_points(points)
    if pts.shape[0] < 5:
        return math.nan
    p = pts - pts.mean(axis=0)
    scale = float(np.max(np.abs(p))) or 1.0
    x, y = p[:, 0] / scale, p[:, 1] / scale
    design = np.column_stack((x * x, x * y, y * y, x, y))
    coef, *_ = np.linalg.lstsq(design, np.ones_like(x), rcond=None)
    a, b, c, d, e = coef
    m = np.array([[a, b / 2], [b / 2, c]])
    if np.linalg.det(m) <= 0:
        return math.nan
    center = np.linalg.solve(2 * m, -np.array([d, e]))
    k = 1.0 + center @ m @ center
    eig = np.linalg.eigvalsh(m)
    if k <= 0 or np.any(eig <= 0):
        return math.nan
    axes = np.sqrt(k / eig) * scale
    return float(axes.mean())


def detect_circles(image: np.ndarray, r_min: float, r_max: float, *, edge_threshold: float = 15.0,
                   min_length: float = 15.0, min_score: float = 700.0, ellipses: bool = False,
                   max_candidates: int = 30) -> list[DetectedCircle]:
    """Circles of radius ``[r_min, r_max]`` px (``IMAQ Detect Circles``, ``IMAQ Detect Ellipses`` with ``ellipses``),
    best score first. Steps: Sobel gradient (contrast units), edge pixels = thinned magnitude ≥ ``edge_threshold``;
    each edge pixel votes for the centres at every radius on both sides along its gradient; the strongest centre peaks
    get the radius of their edge-distance histogram, refined by least squares on the edge pixels within 2 px; kept
    when ``score ≥ min_score`` and the covered arc ``≥ min_length`` px; duplicates (centres closer than half a radius,
    radii within max(3 px, 10 %)) keep the best one. The gradient is taken on the image smoothed by a Gaussian of
    σ = 1 px (orientation of pixelated edges), the centre accumulator is smoothed by σ = 2 px."""
    r_lo, r_hi = max(1.0, float(min(r_min, r_max))), float(max(r_min, r_max))
    if not (math.isfinite(r_lo) and math.isfinite(r_hi)) or r_hi < 1.0:
        return []
    gx, gy, mag = _gradients(image)
    edges = _thin_edges(gx, gy, mag, float(edge_threshold))
    ys, xs = np.nonzero(edges)
    if xs.size == 0:
        return []
    h, w = mag.shape
    ux, uy = gx[ys, xs] / mag[ys, xs], gy[ys, xs] / mag[ys, xs]
    radii = np.arange(math.floor(r_lo), math.ceil(r_hi) + 1, max(1.0, (r_hi - r_lo) / 120.0))
    votes: list[np.ndarray] = []
    for sign in (1.0, -1.0):
        for chunk in np.array_split(radii, max(1, radii.size * xs.size // 2_000_000 + 1)):
            cx = np.rint(xs[None, :] + sign * chunk[:, None] * ux[None, :]).astype(np.int64).ravel()
            cy = np.rint(ys[None, :] + sign * chunk[:, None] * uy[None, :]).astype(np.int64).ravel()
            ok = (cx >= 0) & (cx < w) & (cy >= 0) & (cy < h)
            votes.append(cy[ok] * w + cx[ok])
    acc = np.bincount(np.concatenate(votes), minlength=h * w).astype(np.float64)
    acc2 = ndimage.gaussian_filter(acc.reshape(h, w), 2.0)
    size = max(3, int(r_lo))
    peaks = (acc2 == ndimage.maximum_filter(acc2, size=size)) & (acc2 > 0)
    py, px = np.nonzero(peaks)
    order = np.argsort(acc2[py, px])[::-1][:max_candidates]
    support_mask = ndimage.binary_dilation(edges, iterations=1)
    candidates: list[DetectedCircle] = []
    for idx in order:
        circle = Circle(float(px[idx]), float(py[idx]), math.nan)
        near = np.zeros(xs.size, dtype=bool)
        for _ in range(3):                                   # radius histogram, then least squares, re-centred
            d = np.hypot(xs - circle.x, ys - circle.y)
            sel = (d >= r_lo - 1) & (d <= r_hi + 1)
            if sel.sum() < 3:
                break
            hist, bin_edges = np.histogram(d[sel], bins=np.arange(r_lo - 1, r_hi + 2.0, 1.0))
            hist = np.convolve(hist, np.ones(3), mode="same")
            r0 = float(bin_edges[int(np.argmax(hist))] + 0.5)
            near = np.abs(d - r0) <= 2.0
            circle = Circle(circle.x, circle.y, r0)
            if near.sum() < 5:
                break
            refit = fit_circle_least_squares(np.column_stack((xs[near], ys[near])))
            if refit.is_nan or abs(refit.radius - r0) > 3.0:
                break
            moved = math.hypot(refit.x - circle.x, refit.y - circle.y)
            circle = refit
            near = np.abs(np.hypot(xs - circle.x, ys - circle.y) - circle.radius) <= 2.0
            if moved < 0.1:
                break
        if math.isnan(circle.radius):
            continue
        radius = circle.radius
        if ellipses:
            radius = fit_ellipse_radius(np.column_stack((xs[near], ys[near])))
            if not math.isfinite(radius):
                continue
        if not r_lo - 0.5 <= radius <= r_hi + 0.5:
            continue
        n_samples = max(16, int(2 * math.pi * circle.radius))
        t = 2 * math.pi * np.arange(n_samples) / n_samples
        sx = np.rint(circle.x + circle.radius * np.cos(t)).astype(np.int64)
        sy = np.rint(circle.y + circle.radius * np.sin(t)).astype(np.int64)
        inside = (sx >= 0) & (sx < w) & (sy >= 0) & (sy < h)
        covered = np.zeros(n_samples, dtype=bool)
        covered[inside] = support_mask[sy[inside], sx[inside]]
        coverage = float(covered.mean())
        score = 1000.0 * coverage
        if score < min_score or coverage * 2 * math.pi * circle.radius < min_length:
            continue
        candidates.append(DetectedCircle(circle.x, circle.y, radius, score))
    candidates.sort(key=lambda c: -c.score)
    kept: list[DetectedCircle] = []
    for c in candidates:
        if any(math.hypot(c.x - k.x, c.y - k.y) < 0.5 * k.radius
               and abs(c.radius - k.radius) < max(3.0, 0.1 * k.radius) for k in kept):
            continue
        kept.append(c)
    return kept


# ================================================================================================== blobs
def grey_morphology(image: np.ndarray, operation: str, size: int) -> np.ndarray:
    """``IMAQ GrayMorphology`` with a k×k square (k rounded up to odd; k < 3: the image unchanged). ``operation``:
    ``open``, ``close``, ``erode``, ``dilate``."""
    k = odd_up(size) if int(size) > 0 else 0
    if k < 3:
        return image
    ops = {"open": ndimage.grey_opening, "close": ndimage.grey_closing, "erode": ndimage.grey_erosion,
           "dilate": ndimage.grey_dilation}
    return ops[operation](image, size=(k, k), mode="nearest")


def local_threshold(image: np.ndarray, window: int, *, method: int = 0, dark_objects: bool = False,
                    deviation_factor: float = 0.2) -> np.ndarray:
    """``IMAQ Local Threshold`` over a ``window`` × ``window`` neighbourhood (odd, at least 3). ``method`` 0 Niblack:
    object pixels beyond ``local mean ± deviation_factor · local std``; 1 background correction: beyond the local
    mean. Bright objects by default, dark ones with ``dark_objects``."""
    k = max(3, odd_up(max(int(window), 1)))
    img = np.asarray(image, dtype=np.float64)
    mean = ndimage.uniform_filter(img, size=k, mode="reflect")
    offset = 0.0
    if int(method) == 0:
        var = ndimage.uniform_filter(img * img, size=k, mode="reflect") - mean * mean
        offset = deviation_factor * np.sqrt(np.maximum(var, 0.0))
    return img < mean - offset if dark_objects else img > mean + offset


def remove_small_particles(mask: np.ndarray, iterations: int) -> np.ndarray:
    """``IMAQ RemoveParticle`` (small particles): keeps the particles that survive ``iterations`` 3×3 erosions."""
    if int(iterations) <= 0:
        return mask.copy()
    labels, _ = ndimage.label(mask, structure=np.ones((3, 3)))
    eroded = ndimage.binary_erosion(mask, structure=np.ones((3, 3)), iterations=int(iterations))
    survivors = np.unique(labels[eroded])
    return np.isin(labels, survivors[survivors > 0])


def reject_border(mask: np.ndarray) -> np.ndarray:
    """``IMAQ RejectBorder``: removes the particles touching the image border."""
    labels, _ = ndimage.label(mask, structure=np.ones((3, 3)))
    border = np.unique(np.concatenate((labels[0], labels[-1], labels[:, 0], labels[:, -1])))
    return mask & ~np.isin(labels, border[border > 0])


def fill_holes(mask: np.ndarray) -> np.ndarray:
    """``IMAQ FillHole``."""
    return ndimage.binary_fill_holes(mask)


@dataclass(frozen=True)
class Particle:
    """A particle: area (px), centroid, perimeter (px) and Heywood circularity factor (perimeter / circumference of
    the disc of the same area)."""

    label: int
    area: float
    x: float
    y: float
    perimeter: float
    heywood: float


def _perimeter(boundary_x: np.ndarray, boundary_y: np.ndarray, cx: float, cy: float) -> float:
    """The outline as a radial profile around the centroid: the boundary pixels are grouped in angle sectors (about
    two pixels each), each sector gives its mean angle and its mean distance + 0.5 px (pixel centre → pixel edge);
    the perimeter is the length of that closed polygon."""
    n = boundary_x.size
    if n < 3:
        return math.pi
    angle = np.arctan2(boundary_y - cy, boundary_x - cx)
    radius = np.hypot(boundary_x - cx, boundary_y - cy) + 0.5
    bins = max(8, n // 2)
    sector = np.minimum(((angle + math.pi) / (2 * math.pi) * bins).astype(np.int64), bins - 1)
    count = np.bincount(sector, minlength=bins)
    used = count > 0
    sx = np.bincount(sector, weights=np.cos(angle), minlength=bins)[used]
    sy = np.bincount(sector, weights=np.sin(angle), minlength=bins)[used]
    r = (np.bincount(sector, weights=radius, minlength=bins) / np.maximum(count, 1))[used]
    a = np.arctan2(sy, sx)
    x, y = r * np.cos(a), r * np.sin(a)
    return float(np.hypot(np.diff(x, append=x[0]), np.diff(y, append=y[0])).sum())


def particles(mask: np.ndarray) -> list[Particle]:
    """The 8-connected particles of a binary image. The perimeter is that of the outline seen as a radial profile
    around the centroid (:func:`_perimeter`): close to the true one for star-shaped particles (discs, rectangles),
    shorter for the others (NI traces the contour; not reproduced)."""
    labels, count = ndimage.label(mask, structure=np.ones((3, 3)))
    if count == 0:
        return []
    boundary = labels * (mask & ~ndimage.binary_erosion(mask, structure=np.ones((3, 3)), border_value=0))
    index = np.arange(1, count + 1)
    areas = ndimage.sum_labels(np.ones_like(labels), labels, index)
    centres = ndimage.center_of_mass(np.ones_like(labels), labels, index)
    by, bx = np.nonzero(boundary)
    bl = boundary[by, bx]
    out = []
    for k, (area, (cy, cx)) in enumerate(zip(areas, centres, strict=True), start=1):
        sel = bl == k
        perimeter = _perimeter(bx[sel].astype(float), by[sel].astype(float), float(cx), float(cy))
        heywood = perimeter / (2.0 * math.sqrt(math.pi * area)) if area > 0 else math.inf
        out.append(Particle(k, float(area), float(cx), float(cy), float(perimeter), float(heywood)))
    return out


def find_blob_circles(mask: np.ndarray, r_min: float, r_max: float, *, circularity_min: float = 0.75,
                      circularity_max: float = 1.25) -> list[DetectedCircle]:
    """Particle filter on the Heywood factor ``[circularity_min, circularity_max]`` then ``IMAQ Find Circles``: one
    circle per particle (centroid, ``sqrt(area/π)``) with a radius in ``[r_min, r_max]``; score
    ``1000 / max(H, 1/H)`` (1000 for a perfect disc)."""
    lo, hi = min(r_min, r_max), max(r_min, r_max)
    out = []
    for p in particles(mask):
        if not circularity_min <= p.heywood <= circularity_max:
            continue
        radius = math.sqrt(p.area / math.pi)
        if lo <= radius <= hi:
            out.append(DetectedCircle(p.x, p.y, radius, 1000.0 / max(p.heywood, 1.0 / p.heywood)))
    return out


# ================================================================================================== template matching
@dataclass(frozen=True)
class TemplateMatch:
    """A match: centre of the model in the image (px) and score 0–1000."""

    x: float
    y: float
    score: float


def match_template(image: np.ndarray, model: np.ndarray, *, min_score: float = 750.0,
                   max_matches: int = 50) -> list[TemplateMatch]:
    """Normalised cross-correlation of ``model`` over ``image`` (valid positions only), local maxima at least
    ``min_score`` (score = 1000 × correlation), best first."""
    img = np.asarray(image, dtype=np.float64)
    tpl = np.asarray(model, dtype=np.float64)
    th, tw = tpl.shape[:2]
    if th > img.shape[0] or tw > img.shape[1] or th < 2 or tw < 2:
        return []
    t = tpl - tpl.mean()
    t_norm = math.sqrt(float((t * t).sum()))
    if t_norm == 0.0:
        return []
    num = signal.fftconvolve(img, t[::-1, ::-1], mode="valid")
    ones = np.ones_like(tpl)
    s1 = signal.fftconvolve(img, ones, mode="valid")
    s2 = signal.fftconvolve(img * img, ones, mode="valid")
    var = np.maximum(s2 - s1 * s1 / tpl.size, 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        ncc = np.where(var > 1e-9, num / (np.sqrt(var) * t_norm), 0.0)
    peaks = (ncc == ndimage.maximum_filter(ncc, size=max(3, min(th, tw) // 2))) & (ncc * 1000.0 >= min_score)
    py, px = np.nonzero(peaks)
    order = np.argsort(ncc[py, px])[::-1][:max_matches]
    return [TemplateMatch(float(px[i]) + (tw - 1) / 2.0, float(py[i]) + (th - 1) / 2.0, float(ncc[py[i], px[i]]) * 1000)
            for i in order]
