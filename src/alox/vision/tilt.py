"""Straight-edge tilt (``IMAQ Find Straight Edges 3`` stand-in) and the masking of a matched object.

Spec: ``docs/vision-core/map.md`` Part 4 §3.4 (``alox_vision_measure_tilt_straight_edge``), §3.2
(``alox_vision_mask_matched_object``), Part 3 §3.13 (HL Tilt). Diagrams (cited ``<vi> dN``):
``alox_vision_measure_tilt_straight_edge d, p``, ``alox_vision_mask_matched_object d``,
``alox_function_hl_tilt_measurement_state_machine d81`` (how the HL function wires the options).

What LabVIEW does (``… measure_tilt_straight_edge d``): ROI = the whole image (``IMAQ GetImageSize`` → rectangle
``[0, 0, W − 1, H − 1]``); ``IMAQ Find Straight Edges 3`` with the caller's ``Edge Options`` (polarity, kernel size,
width, minimum edge strength, bilinear interpolation, average projection), ``Search Direction`` and ``Line Options``
whose ``Orientation``, ``Angle Range`` and ``Angle Tolerance`` are replaced by the constants 0, 360 and 1 (every
orientation is searched); the HL function passes ``Number of Lines`` 1, ``Type`` = Edge Method, ``Min. Score`` 10,
``Max. Score`` 1000, ``Step Size`` = Step (Pixels), ``Min. SNR`` 0, ``Hough Iterations`` 5 (``…state_machine d81``).
Then the checks of :func:`alox.vision.wafer.straight_edge_tilt` on the **first** straight edge: EDGE FOUND? (at
least one straight edge), MIN POINTS OK? (``used edges / search lines · 100 ≥ Min. Points (%)``), ANGLE RANGE OK?
(``|angle|`` in ``[Orientation − Angle Range, Orientation + Angle Range]`` of the caller's Line Options). Overlays in
the group ``HL_TILT``: search lines (blue), all edges (orange), found line (red), used edges (yellow), text ``TILT
ANGLE: %.1f°`` or ``TILT NOT FOUND/INVALID`` (here: shapes built by the caller from :class:`StraightEdgeSearch`).
Report ``alox_typedef_tilt_measurement_straight_edge_report``: Point1, Point2 (pixels), Tilt Angle (°), Found Edges
(%), Tilt Found?, # edges OK, Tilt Tol OK; and the process time (ms).

NI → here (``IMAQ Find Straight Edges 3`` is closed source; this is a documented stand-in, not a bit-exact copy):

- **Rake** (:func:`rake_lines`): parallel search lines ``Step Size`` pixels apart across the ROI, centred in it,
  in the NI ``RakeDirection`` (Left to Right: horizontal lines from the left edge to the right edge, ordered top →
  bottom; Top to Bottom: vertical lines ordered left → right; the reversed directions reverse each line). NI's exact
  first-line offset is not documented.
- 1-D edges on each line: :func:`alox.vision.edges.find_edges_on_line` (bilinear profile averaged over ``Width``
  lines, kernel ``Kernel Size``, ``Minimum Edge Strength``, polarity): the same model as the other NI edge tools.
- ``First Edge Rake`` / ``Best Edge Rake``: the first / the strongest edge of each search line, then a line fit by
  :func:`alox.vision.geometry.fit_line_ransac` (seeded RANSAC, inliers within ``fit_max_distance_px``, total least
  squares on the inliers); the inliers are the **used edges**. NI's own fit and outlier rule are not documented.
- ``Hough Edge Rake``: every edge of every line; the line supported by the most edges (RANSAC over all the edges:
  the maximum of a Hough accumulator, without its quantisation), at most one used edge per search line (the nearest).
- ``First Edge Projection`` / ``Best Edge Projection``: the image is projected (averaged) along candidate edge
  orientations every ``Angle Tolerance`` degrees within ±45° of the rake's line direction (then refined ten times
  finer), over the part of each projection that crosses the whole ROI; the first / the strongest edge of each
  projected profile is taken and the orientation with the strongest such edge wins; the used edges are the edges
  of the rake (one per line, the nearest) lying within ``fit_max_distance_px`` of that line, so that MIN POINTS OK?
  keeps a meaning. Large images are sub-sampled for the projection (at most ``projection_samples`` pixels).
- ``Min. Score`` / ``Max. Score`` / ``Min. SNR`` / ``Hough Iterations`` have no equivalent (accepted, not used);
  ``Number of Lines`` > 1 returns the further lines found in the edges left over.
- **Angle** (``Angle (Pixel)``): the direction of the line from Point1 to Point2, in degrees counter-clockwise **as
  displayed** (image Y down: ``atan2(−dy, dx)``), the NI convention of the matcher angles. Point1 → Point2 follows the
  order of the search lines (top → bottom for horizontal search lines, left → right for vertical ones): a horizontal
  edge found with a Top to Bottom rake is at ≈ 0°, positive when its right end is higher; a vertical edge found with a
  Left to Right rake is at ≈ −90°. NI's orientation of the found line is not documented (it might report ±180° away);
  the checks use ``|angle|`` as LabVIEW does.

``alox_vision_mask_matched_object d``: with at least one match, the polygon of the first match's bounding box (I32
corners) → ``IMAQ ROIToMask 2`` (value 255, temporary image ``TMP_MASK_OBJ``) → ``IMAQ Mask``. As the map reads it,
``IMAQ Mask`` keeps the pixels **inside** the box and sets the others to 0; the HL tilt then searches object 2 in that
image so that object 1 is not found again — which only makes sense if the matched object is **removed**. The diagram
cannot tell which (the ``Image Model`` and ``0`` inputs of ``IMAQ ROIToMask 2`` are not readable):
:func:`mask_matched_object` removes the object by default (``keep_inside=False``) and keeps only it with
``keep_inside=True`` (an open question for the owner).
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import IntEnum

import numpy as np

from .edges import Edge, EdgeOptions, detect_edges, find_edges_on_line
from .geometry import GeometryError, Line, fit_line_ransac
from .wafer import straight_edge_tilt

__all__ = [
    "EdgeMethod",
    "LineOptions",
    "RakeDirection",
    "SearchLine",
    "StraightEdge",
    "StraightEdgeSearch",
    "StraightEdgeTiltReport",
    "find_straight_edges",
    "mask_matched_object",
    "measure_tilt_straight_edge",
    "rake_lines",
]

SearchLine = tuple[tuple[float, float], tuple[float, float]]
Point = tuple[float, float]


class RakeDirection(IntEnum):
    """NI ``RakeDirection`` (``Search Direction`` of the tilt settings)."""

    LEFT_TO_RIGHT = 0
    RIGHT_TO_LEFT = 1
    TOP_TO_BOTTOM = 2
    BOTTOM_TO_TOP = 3

    @property
    def horizontal(self) -> bool:
        """The search lines are horizontal (the edge sought is vertical-ish)."""
        return self in (RakeDirection.LEFT_TO_RIGHT, RakeDirection.RIGHT_TO_LEFT)


class EdgeMethod(IntEnum):
    """``Edge Method`` (NI straight-edge ``Type``), in the order of the settings panel."""

    FIRST_EDGE_RAKE = 0
    BEST_EDGE_RAKE = 1
    HOUGH_EDGE_RAKE = 2
    FIRST_EDGE_PROJECTION = 3
    BEST_EDGE_PROJECTION = 4

    @property
    def projection(self) -> bool:
        return self in (EdgeMethod.FIRST_EDGE_PROJECTION, EdgeMethod.BEST_EDGE_PROJECTION)

    @property
    def first(self) -> bool:
        return self in (EdgeMethod.FIRST_EDGE_RAKE, EdgeMethod.FIRST_EDGE_PROJECTION)


@dataclass(frozen=True)
class LineOptions:
    """NI ``Line Options`` (``alox_vision_measure_tilt_straight_edge p``); defaults = the front panel's. The
    ``orientation_deg`` / ``angle_range_deg`` are the caller's (used by the checks only: the search covers every
    orientation, as the VI's constants 0 / 360 do)."""

    number_of_lines: int = 1
    method: EdgeMethod = EdgeMethod.BEST_EDGE_RAKE
    min_score: float = 10.0
    max_score: float = 1000.0
    orientation_deg: float = 0.0
    angle_range_deg: float = 10.0
    angle_tolerance_deg: float = 1.0
    step_px: int = 3
    min_snr: float = 0.0
    min_points_percent: float = 25.0
    hough_iterations: int = 5

    def __post_init__(self) -> None:
        object.__setattr__(self, "method", EdgeMethod(int(self.method)))
        if int(self.step_px) < 1:
            raise ValueError(f"Step Size must be >= 1 pixel, got {self.step_px!r}")
        object.__setattr__(self, "step_px", int(self.step_px))
        if not (math.isfinite(self.angle_tolerance_deg) and self.angle_tolerance_deg > 0):
            raise ValueError(f"Angle Tolerance must be > 0, got {self.angle_tolerance_deg!r}")


@dataclass(frozen=True)
class StraightEdge:
    """One straight edge: Point1 → Point2 (pixels, clipped to the ROI), ``angle_deg`` (module docstring), the used
    edge points, and ``score`` = used edges per candidate edge point × 1000 (not NI's score)."""

    point1: Point
    point2: Point
    angle_deg: float
    used_edges: tuple[Point, ...]
    score: float


@dataclass(frozen=True)
class StraightEdgeSearch:
    """Everything ``IMAQ Find Straight Edges 3`` returns that the caller draws: the search lines, every edge found on
    them, the straight edges (best first)."""

    search_lines: tuple[SearchLine, ...]
    edges: tuple[Point, ...]
    straight_edges: tuple[StraightEdge, ...]


@dataclass(frozen=True)
class StraightEdgeTiltReport:
    """``alox_typedef_tilt_measurement_straight_edge_report`` + process time; ``search`` for the overlays."""

    point1: Point
    point2: Point
    angle_deg: float
    found_edges_percent: float
    tilt_found: bool
    edges_ok: bool
    tolerance_ok: bool
    message: str
    process_time_ms: float
    search: StraightEdgeSearch

    @property
    def valid(self) -> bool:
        """``Tilt OK`` = Tilt Found? AND # edges OK AND Tilt Tol OK (``…state_machine d81``)."""
        return self.tilt_found and self.edges_ok and self.tolerance_ok


# ================================================================================================== rake
def _roi(width: int, height: int, roi: Sequence[float] | None) -> tuple[float, float, float, float]:
    if roi is None:
        return 0.0, 0.0, float(width - 1), float(height - 1)
    left, top, right, bottom = (float(v) for v in roi)
    if right <= left or bottom <= top:
        raise ValueError(f"invalid ROI {tuple(roi)!r}")
    return left, top, right, bottom


def rake_lines(width: int, height: int, direction: RakeDirection | int, step: float,
               roi: Sequence[float] | None = None) -> list[SearchLine]:
    """The search lines of a rake over ``roi`` (``(left, top, right, bottom)``, default the whole image), ``step``
    pixels apart, centred in the ROI, in the order top → bottom (horizontal lines) or left → right (vertical)."""
    direction = RakeDirection(int(direction))
    left, top, right, bottom = _roi(width, height, roi)
    step = max(1.0, float(step))
    lo, hi = (top, bottom) if direction.horizontal else (left, right)
    count = math.floor((hi - lo) / step) + 1
    offset = (hi - lo - (count - 1) * step) / 2.0
    lines: list[SearchLine] = []
    for k in range(count):
        v = lo + offset + k * step
        if direction is RakeDirection.LEFT_TO_RIGHT:
            lines.append(((left, v), (right, v)))
        elif direction is RakeDirection.RIGHT_TO_LEFT:
            lines.append(((right, v), (left, v)))
        elif direction is RakeDirection.TOP_TO_BOTTOM:
            lines.append(((v, top), (v, bottom)))
        else:
            lines.append(((v, bottom), (v, top)))
    return lines


def _pick(edges: Sequence[Edge], first: bool) -> Edge | None:
    if not edges:
        return None
    return edges[0] if first else max(edges, key=lambda e: abs(e.strength))


def _order_axis(direction: RakeDirection) -> tuple[float, float]:
    """Unit vector of the search-line order: down for horizontal search lines, right for vertical ones."""
    return (0.0, 1.0) if direction.horizontal else (1.0, 0.0)


def _clip_line(line: Line, roi: tuple[float, float, float, float]) -> tuple[Point, Point] | None:
    """The two ends of ``line`` inside the rectangle ``roi`` (Liang–Barsky on a long segment), None if outside."""
    left, top, right, bottom = roi
    cx, cy = (left + right) / 2.0, (top + bottom) / 2.0
    # foot of the perpendicular from the ROI centre
    d = line.a * cx + line.b * cy + line.c
    px, py = cx - line.a * d, cy - line.b * d
    dx, dy = line.direction
    big = 4.0 * (abs(right - left) + abs(bottom - top) + 1.0)
    t0, t1 = -big, big
    for p, q in ((-dx, px - left), (dx, right - px), (-dy, py - top), (dy, bottom - py)):
        if p == 0.0:
            if q < 0.0:
                return None
            continue
        r = q / p
        if p < 0.0:
            t0 = max(t0, r)
        else:
            t1 = min(t1, r)
    if t0 > t1:
        return None
    return (px + t0 * dx, py + t0 * dy), (px + t1 * dx, py + t1 * dy)


def _straight_edge(line: Line, used: np.ndarray, candidates: int, direction: RakeDirection,
                   roi: tuple[float, float, float, float]) -> StraightEdge | None:
    """Point1/Point2 (the ends of the line in the ROI, ordered along the search-line order) and the angle."""
    ends = _clip_line(line, roi)
    if ends is None:
        return None
    ox, oy = _order_axis(direction)
    (x1, y1), (x2, y2) = ends
    if (x2 - x1) * ox + (y2 - y1) * oy < 0:
        (x1, y1), (x2, y2) = (x2, y2), (x1, y1)
    angle = math.degrees(math.atan2(-(y2 - y1), x2 - x1)) + 0.0
    points = tuple((float(x), float(y)) for x, y in used)
    score = 1000.0 * len(points) / candidates if candidates else 0.0
    return StraightEdge((x1, y1), (x2, y2), angle, points, score)


def _fit(points: np.ndarray, max_distance: float, seed: int) -> tuple[Line, np.ndarray] | None:
    if points.shape[0] < 2:
        return None
    try:
        fit = fit_line_ransac(points, max_distance=max_distance, iterations=500, seed=seed)
    except GeometryError:
        return None
    if fit.n_inliers < 2:
        return None
    return fit.line, fit.inliers


def _nearest_per_line(per_line: Sequence[Sequence[Edge]], line: Line, max_distance: float) -> np.ndarray:
    """On each search line, the edge nearest to ``line`` when within ``max_distance``."""
    out: list[tuple[float, float]] = []
    for edges in per_line:
        best: tuple[float, Edge] | None = None
        for e in edges:
            d = abs(line.a * e.x + line.b * e.y + line.c)
            if d <= max_distance and (best is None or d < best[0]):
                best = (d, e)
        if best is not None:
            out.append((best[1].x, best[1].y))
    return np.asarray(out, dtype=np.float64).reshape(-1, 2)


# ================================================================================================== projection
def _projection_line(image: np.ndarray, direction: RakeDirection, options: EdgeOptions, method: EdgeMethod,
                     tolerance_deg: float, roi: tuple[float, float, float, float], max_samples: int) -> Line | None:
    """The edge line of the projection methods (module docstring), or None."""
    img = np.asarray(image, dtype=np.float64)
    if img.ndim == 3:
        img = img[..., :3].mean(axis=2)
    left, top, right, bottom = roi
    sub = img[math.ceil(top):math.floor(bottom) + 1, math.ceil(left):math.floor(right) + 1]
    h, w = sub.shape
    if h < 2 or w < 2:
        return None
    stride = max(1, math.ceil(math.sqrt(h * w / max(1, max_samples))))
    ys, xs = np.mgrid[0:h:stride, 0:w:stride]
    vals = sub[ys, xs].ravel()
    xs = xs.ravel().astype(np.float64) + math.ceil(left)
    ys = ys.ravel().astype(np.float64) + math.ceil(top)
    cx, cy = (left + right) / 2.0, (top + bottom) / 2.0
    base = {RakeDirection.LEFT_TO_RIGHT: 0.0, RakeDirection.RIGHT_TO_LEFT: 180.0,
            RakeDirection.TOP_TO_BOTTOM: -90.0, RakeDirection.BOTTOM_TO_TOP: 90.0}[direction]
    opts = EdgeOptions(options.polarity, options.kernel_size, 1, options.min_strength)

    def evaluate(rotation: float) -> tuple[float, float] | None:
        """(|strength|, position along the search direction) of the edge of the projection at ``rotation``."""
        a = math.radians(base + rotation)
        dx, dy = math.cos(a), -math.sin(a)                 # counter-clockwise as displayed
        s = (xs - cx) * dx + (ys - cy) * dy
        s0 = float(s.min())
        idx = np.floor((s - s0) / stride).astype(np.int64)
        counts = np.bincount(idx)
        sums = np.bincount(idx, weights=vals)
        keep = np.flatnonzero(counts >= max(2, 0.5 * counts.max()))      # where the projection crosses the ROI
        if keep.size < 3:
            return None
        lo, hi = int(keep[0]), int(keep[-1])
        profile = sums[lo:hi + 1] / np.maximum(counts[lo:hi + 1], 1)
        edge = None
        found = detect_edges(profile, opts)
        if found:
            edge = found[0] if method.first else max(found, key=lambda e: abs(e[1]))
        if edge is None:
            return None
        return abs(edge[1]), s0 + (lo + edge[0] + 0.5) * stride

    def search(rotations: np.ndarray) -> list[tuple[float, float, float]]:
        out: list[tuple[float, float, float]] = []
        for r in rotations:
            got = evaluate(float(r))
            if got is not None:
                out.append((got[0], got[1], float(r)))
        return out

    coarse = search(np.arange(-45.0, 45.0 + tolerance_deg / 2.0, tolerance_deg))   # the edge crosses the rake
    if not coarse:
        return None
    best = max(coarse)
    fine = search(best[2] + np.linspace(-tolerance_deg, tolerance_deg, 21)) or [best]
    top = max(f[0] for f in fine)
    # the strength is flat near the true orientation: the centre of the angles within 1 % of the maximum
    rotation = float(np.mean([f[2] for f in fine if f[0] >= 0.99 * top]))
    got = evaluate(rotation)
    position = got[1] if got is not None else max(fine)[1]
    a = math.radians(base + rotation)
    dx, dy = math.cos(a), -math.sin(a)
    px, py = cx + position * dx, cy + position * dy
    return Line.from_point_direction((px, py), (-dy, dx))


# ================================================================================================== search
def find_straight_edges(image: np.ndarray, edge_options: EdgeOptions, direction: RakeDirection | int,
                        line_options: LineOptions, *, roi: Sequence[float] | None = None,
                        fit_max_distance_px: float = 2.0, projection_samples: int = 120_000,
                        seed: int = 0) -> StraightEdgeSearch:
    """``IMAQ Find Straight Edges 3`` stand-in (module docstring)."""
    arr = np.asarray(image)
    if arr.ndim not in (2, 3) or arr.size == 0:
        raise ValueError(f"a non-empty image is required, got shape {arr.shape}")
    direction = RakeDirection(int(direction))
    height, width = arr.shape[:2]
    box = _roi(width, height, roi)
    lines = rake_lines(width, height, direction, line_options.step_px, box)
    grey = arr if arr.ndim == 2 else arr[..., :3].mean(axis=2)
    per_line = [find_edges_on_line(grey, start, end, edge_options) for start, end in lines]
    all_edges = tuple((e.x, e.y) for edges in per_line for e in edges)
    method = line_options.method
    found: list[StraightEdge] = []
    remaining = [list(edges) for edges in per_line]
    for k in range(max(1, int(line_options.number_of_lines))):
        if method.projection:
            if k > 0:
                break                                     # one projection line (no edges are "left over")
            line = _projection_line(grey, direction, edge_options, method, line_options.angle_tolerance_deg, box,
                                    projection_samples)
            if line is None:
                break
            used = _nearest_per_line(per_line, line, fit_max_distance_px)
            candidates = len(lines)
        else:
            if method is EdgeMethod.HOUGH_EDGE_RAKE:
                pts = [(e.x, e.y) for edges in remaining for e in edges]
            else:
                pts = [(e.x, e.y) for e in (_pick(edges, method.first) for edges in remaining) if e is not None]
            points = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
            fitted = _fit(points, fit_max_distance_px, seed)
            if fitted is None:
                break
            line, inliers = fitted
            used = (_nearest_per_line(remaining, line, fit_max_distance_px) if method is EdgeMethod.HOUGH_EDGE_RAKE
                    else points[inliers])
            candidates = points.shape[0]
        if used.shape[0] < 2:
            break
        edge = _straight_edge(line, used, candidates, direction, box)
        if edge is None:
            break
        found.append(edge)
        remaining = [[e for e in edges if abs(line.a * e.x + line.b * e.y + line.c) > fit_max_distance_px]
                     for edges in remaining]
    return StraightEdgeSearch(tuple(lines), all_edges, tuple(found))


def measure_tilt_straight_edge(image: np.ndarray, edge_options: EdgeOptions, direction: RakeDirection | int,
                               line_options: LineOptions, *, fit_max_distance_px: float = 2.0,
                               clock: Callable[[], float] = time.perf_counter) -> StraightEdgeTiltReport:
    """``alox_vision_measure_tilt_straight_edge``: the straight edges of the whole image and the checks of the first
    one (module docstring)."""
    t0 = clock()
    search = find_straight_edges(image, edge_options, direction, line_options,
                                 fit_max_distance_px=fit_max_distance_px)
    first = search.straight_edges[0] if search.straight_edges else None
    check = straight_edge_tilt(first.angle_deg if first is not None else None,
                               used_edges=len(first.used_edges) if first is not None else 0,
                               search_lines=len(search.search_lines),
                               min_points_percent=line_options.min_points_percent,
                               orientation_deg=line_options.orientation_deg,
                               angle_range_deg=line_options.angle_range_deg)
    p1, p2 = (first.point1, first.point2) if first is not None else ((0.0, 0.0), (0.0, 0.0))
    pct = check.points_percent if math.isfinite(check.points_percent) else 0.0
    return StraightEdgeTiltReport(point1=p1, point2=p2, angle_deg=check.angle_deg, found_edges_percent=pct,
                                  tilt_found=check.edge_found, edges_ok=check.min_points_ok,
                                  tolerance_ok=check.angle_range_ok, message=check.message,
                                  process_time_ms=(clock() - t0) * 1000.0, search=search)


# ================================================================================================== masking
def mask_matched_object(image: np.ndarray, bounding_boxes: Sequence[Sequence[Sequence[float]]], *,
                        keep_inside: bool = False) -> np.ndarray:
    """``alox_vision_mask_matched_object``: the first bounding box (4 corners, pixels; the polygon's corners rounded
    to integers as LabVIEW's I32) set to 0 (``keep_inside=False``, default) or everything outside it set to 0
    (``keep_inside=True``, the map's reading). No box: a copy of the image."""
    out = np.array(image, copy=True)
    if not bounding_boxes:
        return out
    poly = np.rint(np.asarray(bounding_boxes[0], dtype=np.float64)).reshape(-1, 2)
    if poly.shape[0] < 3:
        return out
    h, w = out.shape[:2]
    ys, xs = np.mgrid[0:h, 0:w]
    inside = _inside_convex(poly, xs.astype(np.float64), ys.astype(np.float64))
    if keep_inside:
        out[~inside] = 0
    else:
        out[inside] = 0
    return out


def _inside_convex(poly: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    """Pixels inside or on the convex polygon ``poly`` (either winding)."""
    n = poly.shape[0]
    pos = np.ones(xs.shape, dtype=bool)
    neg = np.ones(xs.shape, dtype=bool)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        cross = (x2 - x1) * (ys - y1) - (y2 - y1) * (xs - x1)
        pos &= cross >= 0
        neg &= cross <= 0
    return pos | neg

