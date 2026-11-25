"""Plane geometry of lines: normal form, total-least-squares fit, RANSAC fit, distances and angles.

Spec: ``docs/vision-core/map.md`` Part 4 §3.6 and "Owner's answers" §3.1. LabVIEW VIs covered:
``geometry_line_equation_from_two_points``, ``geometry_least_square_distance_points_line``,
``geometry_fit_line_ransac2`` (and its wrapper ``geometry_fit_line_ransac``).

A line is kept in **normal form** ``a·x + b·y + c = 0`` with ``a² + b² = 1``, so every orientation (horizontal,
vertical, oblique) has the same representation and ``|a·x + b·y + c|`` is the Euclidean point-to-line distance.

Old LabVIEW behaviour (documented, deliberately **not** reproduced; owner's answer 25/11/2026: "fixed")
------------------------------------------------------------------------------------------------------
``geometry_line_equation_from_two_points d, d2, d3``: ``a = y1 − y2``, ``b = x2 − x1``, ``c = x1·y2 − x2·y1``, scaled by
``1/sqrt(a² + b²)`` — the same formula as :meth:`Line.from_points` — but the VI returns error −1 "Same Points" and the
line ``[1, 0, 0]`` whenever ``x1 = x2`` **or** ``y1 = y2``, i.e. for every vertical or horizontal pair, not only for
coincident points.

``geometry_fit_line_ransac2 d`` ("Fit Options" = {# Iterations, Max Distance, Min Score (%)}, names from
``alox_tools_fit_diameters_measures p``):

1. ``# Iterations`` couples of indices ``round(random()·N)`` (no ``−1``: index ``N`` is out of range and LabVIEW's
   Index Array then yields the default point (0, 0)); couples with equal indices are dropped;
2. for each couple without error: line through the two points, ``Σ d_i`` over **all** points
   (``geometry_least_square_distance_points_line d``); keep the couple with the **smallest sum**
   ("Keep couple of points which minimizes distance");
3. ``Residual`` = that sum, ``Score`` = inlier percentage (``d_i ≤ Max Distance``), ``Valid Fit = Score ≥ Min Score AND
   #inliers ≥ 2``. The line of the best couple is returned as is (no refit).

So it was a least-absolute-deviation search over random pairs, not a consensus RANSAC; it could never return a
horizontal or vertical line, could draw a phantom (0, 0) point, and failed when no valid pair existed.

What this module does instead
-----------------------------
:func:`fit_line_ransac` is a real RANSAC: pairs of **distinct** indices drawn from a seeded
:class:`numpy.random.Generator` (reproducible, never out of range), every orientation accepted (coincident pairs
only are skipped), the candidate with the **most inliers** wins (ties: smallest sum of inlier distances), then a
total-least-squares refit on the inliers.
The LabVIEW outputs are kept: ``residual`` = Σ distances over all points, ``score`` = inlier %, ``valid`` = score ≥
``min_score`` and at least two inliers.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np

__all__ = [
    "DistanceScore",
    "GeometryError",
    "Line",
    "LineFit",
    "as_points",
    "distance",
    "distance_score",
    "fit_line_ransac",
    "fit_line_tls",
    "fold_angle_180",
    "line_angle_deg",
    "lines_angle_deg",
    "point_line_distances",
    "segment_angle_deg",
]

PointsLike = Sequence[Sequence[float]] | np.ndarray


class GeometryError(ValueError):
    """Degenerate geometric input (too few points, coincident points)."""


def as_points(points: PointsLike) -> np.ndarray:
    """``points`` as a float64 array of shape (N, 2); raises :class:`GeometryError` for any other shape."""
    arr = np.asarray(points, dtype=np.float64)
    if arr.size == 0:
        return arr.reshape(0, 2)
    if arr.ndim != 2 or arr.shape[1] != 2:
        raise GeometryError(f"expected an (N, 2) array of points, got shape {arr.shape}")
    return arr


@dataclass(frozen=True)
class Line:
    """Line ``a·x + b·y + c = 0`` in normal form (``a² + b² = 1``); ``(a, b)`` is the unit normal."""

    a: float
    b: float
    c: float

    def __post_init__(self) -> None:
        for name in ("a", "b", "c"):
            object.__setattr__(self, name, float(getattr(self, name)))

    @classmethod
    def from_points(cls, p1: Sequence[float], p2: Sequence[float]) -> Line:
        """Line through two points, any orientation.

        Same coefficients and sign as ``geometry_line_equation_from_two_points d`` (``a = y1 − y2``, ``b = x2 − x1``,
        ``c = x1·y2 − x2·y1``, normalised), without its horizontal/vertical rejection; raises :class:`GeometryError`
        only for coincident points.
        """
        x1, y1 = float(p1[0]), float(p1[1])
        x2, y2 = float(p2[0]), float(p2[1])
        a, b = y1 - y2, x2 - x1
        norm = math.hypot(a, b)
        if norm == 0.0 or not math.isfinite(norm):
            raise GeometryError("a line needs two distinct, finite points")
        return cls(a / norm, b / norm, (x1 * y2 - x2 * y1) / norm)

    @classmethod
    def from_point_direction(cls, point: Sequence[float], direction: Sequence[float]) -> Line:
        """Line through ``point`` along ``direction`` (any non-zero vector)."""
        dx, dy = float(direction[0]), float(direction[1])
        norm = math.hypot(dx, dy)
        if norm == 0.0 or not math.isfinite(norm):
            raise GeometryError("the direction vector must be non-zero and finite")
        a, b = -dy / norm, dx / norm
        return cls(a, b, -(a * float(point[0]) + b * float(point[1])))

    @property
    def normal(self) -> tuple[float, float]:
        return (self.a, self.b)

    @property
    def direction(self) -> tuple[float, float]:
        """Unit direction vector ``(b, −a)`` (for :meth:`from_points`: from ``p1`` towards ``p2``)."""
        return (self.b, -self.a)

    @property
    def is_vertical(self) -> bool:
        return self.b == 0.0

    @property
    def slope(self) -> float:
        """``dy/dx`` = ``−a/b``; ``±inf`` for a vertical line."""
        if self.b == 0.0:
            return math.copysign(math.inf, -self.a)
        return -self.a / self.b

    @property
    def intercept(self) -> float:
        """``y`` at ``x = 0`` = ``−c/b``; NaN for a vertical line."""
        return math.nan if self.b == 0.0 else -self.c / self.b

    def y_at(self, x: float | np.ndarray) -> float | np.ndarray:
        """``y = −(a·x + c)/b`` (the "Y Fitted" loop of ``alox_tools_fit_diameters_measures d``); NaN if vertical."""
        if self.b == 0.0:
            return np.full_like(np.asarray(x, dtype=np.float64), np.nan) if np.ndim(x) else math.nan
        return -(self.a * x + self.c) / self.b

    def x_at(self, y: float | np.ndarray) -> float | np.ndarray:
        """``x = −(b·y + c)/a``; NaN for a horizontal line."""
        if self.a == 0.0:
            return np.full_like(np.asarray(y, dtype=np.float64), np.nan) if np.ndim(y) else math.nan
        return -(self.b * y + self.c) / self.a

    def signed_distances(self, points: PointsLike) -> np.ndarray:
        pts = as_points(points)
        return self.a * pts[:, 0] + self.b * pts[:, 1] + self.c

    def distances(self, points: PointsLike) -> np.ndarray:
        """``|a·x_i + b·y_i + c|`` (the coefficients are already normalised)."""
        return np.abs(self.signed_distances(points))

    def project(self, points: PointsLike) -> np.ndarray:
        """Orthogonal projections of the points on the line, shape (N, 2)."""
        pts = as_points(points)
        d = self.signed_distances(pts)
        return pts - np.outer(d, (self.a, self.b))

    def canonical(self) -> Line:
        """Same line with the sign fixed: the larger of ``|a|``, ``|b|`` is made positive (``b`` on a tie), so that
        nearly horizontal lines have ``b > 0`` and nearly vertical ones ``a > 0`` whatever the rounding noise."""
        key = self.b if abs(self.b) >= abs(self.a) else self.a
        if key < 0.0:
            return Line(-self.a, -self.b, -self.c)
        return self

    def as_tuple(self) -> tuple[float, float, float]:
        return (self.a, self.b, self.c)


def distance(p1: Sequence[float], p2: Sequence[float]) -> float:
    """Euclidean distance (``IMAQ Point Distances``)."""
    return math.hypot(float(p2[0]) - float(p1[0]), float(p2[1]) - float(p1[1]))


def point_line_distances(points: PointsLike, line: Line | Sequence[float]) -> np.ndarray:
    """``d_i = |a·x_i + b·y_i + c| / sqrt(a² + b²)`` for a line given as :class:`Line` or any ``(a, b, c)``.

    ``geometry_least_square_distance_points_line d``. A zero normal ``(0, 0)`` raises :class:`GeometryError`.
    """
    if isinstance(line, Line):
        return line.distances(points)
    a, b, c = (float(v) for v in line)
    norm = math.hypot(a, b)
    if norm == 0.0:
        raise GeometryError("(a, b) = (0, 0) is not a line")
    pts = as_points(points)
    return np.abs(a * pts[:, 0] + b * pts[:, 1] + c) / norm


@dataclass(frozen=True)
class DistanceScore:
    """Outputs of ``geometry_least_square_distance_points_line``."""

    distances: np.ndarray
    sum_distance: float
    inliers: np.ndarray  # boolean mask, d_i <= max_distance
    score: float  # %, NaN for an empty point set

    @property
    def n_inliers(self) -> int:
        return int(np.count_nonzero(self.inliers))


def distance_score(points: PointsLike, line: Line | Sequence[float], max_distance: float) -> DistanceScore:
    """``Sum distance = Σ d_i`` (all points), inliers ``d_i ≤ Max Distance``, ``Score (%) = #inliers/#points·100``.

    ``geometry_least_square_distance_points_line d``.
    """
    d = point_line_distances(points, line)
    inl = d <= max_distance
    n = d.size
    score = 100.0 * np.count_nonzero(inl) / n if n else math.nan
    return DistanceScore(d, float(d.sum()), inl, float(score))


def fit_line_tls(points: PointsLike) -> Line:
    """Total-least-squares (orthogonal) line fit: minimises Σ d_i², valid for every orientation.

    The line passes through the centroid along the principal axis of the centred points (SVD). Raises
    :class:`GeometryError` for fewer than two distinct points or non-finite coordinates. The result is
    :meth:`Line.canonical`.
    """
    pts = as_points(points)
    if pts.shape[0] < 2:
        raise GeometryError("a line fit needs at least two points")
    if not np.all(np.isfinite(pts)):
        raise GeometryError("non-finite coordinates")
    centroid = pts.mean(axis=0)
    centred = pts - centroid
    scale = float(np.max(np.abs(centred)))
    if scale == 0.0:
        raise GeometryError("all points are coincident")
    _, _, vt = np.linalg.svd(centred / scale, full_matrices=False)
    direction = vt[0]
    return Line.from_point_direction(centroid, direction).canonical()


@dataclass(frozen=True)
class LineFit:
    """Result of :func:`fit_line_ransac` (the outputs of ``geometry_fit_line_ransac2``)."""

    line: Line
    residual: float  # Σ distances over all points (LabVIEW "Residual")
    score: float  # % of inliers (LabVIEW "Score")
    inliers: np.ndarray  # boolean mask
    valid: bool  # score >= min_score and at least two inliers
    iterations: int  # candidate pairs actually evaluated

    @property
    def n_inliers(self) -> int:
        return int(np.count_nonzero(self.inliers))


def _candidate_lines(pts: np.ndarray, i: np.ndarray, j: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Normalised (a, b, c) of the lines through pts[i] and pts[j], vectorised; NaN rows for coincident pairs."""
    x1, y1 = pts[i, 0], pts[i, 1]
    x2, y2 = pts[j, 0], pts[j, 1]
    a, b = y1 - y2, x2 - x1
    norm = np.hypot(a, b)
    with np.errstate(divide="ignore", invalid="ignore"):
        bad = norm == 0.0
        norm = np.where(bad, np.nan, norm)
        return a / norm, b / norm, (x1 * y2 - x2 * y1) / norm


def fit_line_ransac(
    points: PointsLike,
    *,
    max_distance: float,
    iterations: int = 1000,
    min_score: float = 0.0,
    seed: int | np.random.Generator | None = 0,
    refine: Literal["tls", "none"] = "tls",
    chunk: int = 256,
) -> LineFit:
    """RANSAC line fit with an inlier threshold, any orientation (replaces ``geometry_fit_line_ransac2``).

    ``iterations`` random pairs of distinct indices are drawn from ``numpy.random.default_rng(seed)`` (pass a
    :class:`~numpy.random.Generator` to share one); a pair of coincident points is skipped. The candidate line with the
    most inliers (``d_i ≤ max_distance``) wins, ties broken by the smaller sum of inlier distances. With
    ``refine="tls"`` the line is then refitted by :func:`fit_line_tls` on the inliers; the refit is kept unless it
    loses inliers. Exactly two points give the line through them. The coordinates are centred internally, so large
    offsets (LabVIEW timestamps ≈ 3.6e9 s) do not cost precision.

    Raises :class:`GeometryError` for fewer than two distinct finite points; ``max_distance < 0`` or
    ``iterations < 1`` raise :class:`ValueError`.
    """
    pts = as_points(points)
    if max_distance < 0 or not math.isfinite(max_distance):
        raise ValueError("max_distance must be a finite value >= 0")
    if iterations < 1:
        raise ValueError("iterations must be >= 1")
    if pts.shape[0] < 2:
        raise GeometryError("a line fit needs at least two points")
    if not np.all(np.isfinite(pts)):
        raise GeometryError("non-finite coordinates")
    n = pts.shape[0]
    origin = pts.mean(axis=0)
    local = pts - origin
    rng = seed if isinstance(seed, np.random.Generator) else np.random.default_rng(seed)

    best: tuple[int, float] | None = None  # (n_inliers, -sum_inlier_distance) to maximise
    best_line: tuple[float, float, float] | None = None
    evaluated = 0
    if n == 2:
        pairs = [(np.array([0]), np.array([1]))]
    else:
        pairs = []
        remaining = iterations
        while remaining > 0:
            m = min(chunk, remaining)
            i = rng.integers(0, n, size=m)
            j = (i + rng.integers(1, n, size=m)) % n  # distinct from i, uniform over the other n-1 indices
            pairs.append((i, j))
            remaining -= m
    for i, j in pairs:
        a, b, c = _candidate_lines(local, i, j)
        ok = np.isfinite(a)
        if not np.any(ok):
            continue
        a, b, c = a[ok], b[ok], c[ok]
        evaluated += int(a.size)
        d = np.abs(np.outer(a, local[:, 0]) + np.outer(b, local[:, 1]) + c[:, None])
        inl = d <= max_distance
        counts = inl.sum(axis=1)
        sums = np.where(inl, d, 0.0).sum(axis=1)
        order = np.lexsort((sums, -counts))  # most inliers first, then smallest inlier sum
        k = int(order[0])
        key = (int(counts[k]), -float(sums[k]))
        if best is None or key > best:
            best, best_line = key, (float(a[k]), float(b[k]), float(c[k]))
    if best_line is None:
        raise GeometryError("all points are coincident")

    line = Line(*best_line)
    inliers = line.distances(local) <= max_distance
    if refine == "tls" and np.count_nonzero(inliers) >= 2:
        try:
            refit = fit_line_tls(local[inliers])
        except GeometryError:
            refit = None
        if refit is not None:
            refit_inliers = refit.distances(local) <= max_distance
            if np.count_nonzero(refit_inliers) >= np.count_nonzero(inliers):
                line, inliers = refit, refit_inliers
    elif refine not in ("tls", "none"):
        raise ValueError(f"unknown refine mode {refine!r}")

    distances = line.distances(local)
    # back to the caller's coordinates: a(x - ox) + b(y - oy) + c = 0
    line = Line(line.a, line.b, line.c - line.a * origin[0] - line.b * origin[1]).canonical()
    n_in = int(np.count_nonzero(inliers))
    score = 100.0 * n_in / n
    return LineFit(
        line=line,
        residual=float(distances.sum()),
        score=float(score),
        inliers=inliers,
        valid=bool(score >= min_score and n_in >= 2),
        iterations=evaluated,
    )


def segment_angle_deg(p1: Sequence[float], p2: Sequence[float]) -> float:
    """Direction of the vector ``p1 → p2`` in degrees, ``atan2(dy, dx)`` in (−180, 180].

    Python form of ``IMAQ Get Angles`` used by ``tools_tilt_function_object_detection_status d`` (map §3.4). NI's
    orientation convention (counter-clockwise on screen, i.e. with the image Y axis pointing down) is not confirmed
    by the diagrams: for points in a Y-down frame, negate ``dy`` before calling.
    """
    return math.degrees(math.atan2(float(p2[1]) - float(p1[1]), float(p2[0]) - float(p1[0])))


def fold_angle_180(angle_deg: float) -> float:
    """Fold an undirected-line angle into (−90°, 90°].

    ``tools_tilt_function_object_detection_status d``: remainder of the division by 180 (``Quotient & Remainder``,
    result in [0, 180)), then ``r − 180`` when ``r > 90``. NaN stays NaN.
    """
    if not math.isfinite(angle_deg):
        return math.nan
    r = math.fmod(angle_deg, 180.0)
    if r < 0.0:
        r += 180.0
    if r >= 180.0:  # fmod rounding guard
        r -= 180.0
    return r - 180.0 if r > 90.0 else r


def line_angle_deg(line: Line) -> float:
    """Angle of the line with the X axis, folded into (−90°, 90°] (a line has no orientation)."""
    dx, dy = line.direction
    return fold_angle_180(math.degrees(math.atan2(dy, dx)))


def lines_angle_deg(l1: Line, l2: Line) -> float:
    """Acute angle between two lines, in [0°, 90°]."""
    cos = abs(l1.a * l2.a + l1.b * l2.b)
    return math.degrees(math.acos(min(1.0, cos)))
