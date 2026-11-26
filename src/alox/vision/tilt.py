"""Straight-edge tilt (``IMAQ Find Straight Edges 3`` stand-in), the masking of a matched object, and the
algorithms of the precise (long-baseline) tilt methods.

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

Precise methods (Python only; owner's answer R22–R24 "build a better method")
-----------------------------------------------------------------------------
The algorithms of the Tilt Methods Long Baseline Edge and Lattice (:mod:`alox.functions.tilt`, which moves the
stage). The LabVIEW methods measure the angle **inside one field of view**: the straight edge against the camera's
pixel axes, the two objects against a baseline of at most the field of view when both are in one image. Their limits:
a 1 px error over a 2000 px image is 0.03°, NI's 1-D edges are interpolated on a ``Step Size`` rake (30 px by
default) with a parabola, and above all the camera's mounting rotation against the stage axes adds to the angle
unseen (the mesa job moves the **stage**, so the angle it needs is against the stage axes).

- :func:`fit_edge_line`: one edge point every ``step_px`` (default 4) pixels (:func:`edge_points_subpixel`: a profile
  averaged over ``width_px`` lines, derivative of a Gaussian, the strongest extremum of the polarity, sub-pixel by
  a Gaussian through three samples, exact for a blurred step), RANSAC + total least squares
  (:func:`alox.vision.geometry.fit_line_ransac`); the angle's 1σ from the residuals; the **point of the edge nearest
  the image centre** with its 1σ.
- :func:`detect_mesas`: Otsu threshold, particles (border, size outliers dropped), grey-weighted centroids over the
  particle grown by 2 px (no threshold bias); nothing without a pattern (contrast < 6 × :func:`noise_sigma`).
  :func:`fit_lattice` (owner S6: "can be everything", any lattice type at any angle to the flat): the two shortest
  independent vectors that occur often between neighbouring centres, integer indices, least-squares lattice
  (origin and two vectors) with re-indexing and outlier rejection, then the reduced basis (:func:`reduce_basis`: the
  two shortest independent lattice vectors), the lattice type and its rotational symmetry (:func:`lattice_kind`:
  hexagonal 60°, square 90°, rectangular / centred rectangular / oblique 180°); the **row** is a shortest lattice
  vector (the densest row of mesas; among equally short ones the nearest a hint direction), its angle folded to that
  symmetry. Nothing assumes the rows to be along the image or stage axes, or the grid to be rectangular.
- :func:`fit_baseline`: the line through the feature's points measured at stage positions far apart (wafer mm),
  ``atan2(Δy, Δx)`` and its 1σ (points' 1σ across the line — detection and stage repeatability — over their spread
  along it, or the residuals when larger); :func:`baseline_target`, :func:`next_baseline_step`,
  :func:`pick_row_mesa`: the walk along the feature (predict where it is, grow the baseline only as far as the
  current uncertainty allows, keep the mesa of the start row).

Precision on synthetic images (320 × 240 px at 2 µm, noise σ 2–4 grey levels, edge blur 0.8 px; tests
``test_vision_tilt.py`` / ``test_functions_tilt.py``; true angle 0.137°):

=================================================  ================  ===============================
method                                             angle error       with the camera turned 0.3°
=================================================  ================  ===============================
LabVIEW straight edge (rake, 20 px step), 1 image  0.001–0.004°      0.29° (the mounting rotation)
:func:`fit_edge_line`, 1 image                     0.0002–0.0003°    0.30° (the mounting rotation)
:func:`fit_lattice`, 1 image                       0.001–0.002°      0.30° (the mounting rotation)
Long Baseline Edge, L = 20 mm (3 images)           0.00002°          0.00002°
Lattice, L = 20 mm (7 images, 6 moves)             0.00015°          0.00015°
=================================================  ================  ===============================

On a machine the long-baseline angle is limited by the stage: a repeatability σ gives ``σ / sqrt(Σ(s − s̄)²)``
(0.002° for 0.5 µm over ±10 mm, three points; the reported uncertainty includes it, the tests shake the stage by
0.5 µm and stay within it), plus the straightness of the X/Y guides over L, which no measurement on one axis can
separate from the wafer. The one-image angles stay available as the consistency check (their difference to the
baseline angle is the camera's rotation, plus the one-image error).
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import IntEnum

import numpy as np
from scipy import ndimage

from .edges import Edge, EdgeOptions, EdgePolarity, detect_edges, find_edges_on_line
from .geometry import GeometryError, Line, fit_line_ransac, fit_line_tls
from .wafer import straight_edge_tilt

__all__ = [
    "LATTICE_PERIODS",
    "BaselineFit",
    "EdgeLineFit",
    "EdgeMethod",
    "LatticeFit",
    "LineOptions",
    "RakeDirection",
    "SearchLine",
    "StraightEdge",
    "StraightEdgeSearch",
    "StraightEdgeTiltReport",
    "baseline_target",
    "detect_mesas",
    "edge_points_subpixel",
    "find_straight_edges",
    "fit_baseline",
    "fit_edge_line",
    "fit_lattice",
    "fold_angle",
    "lattice_kind",
    "mask_matched_object",
    "measure_tilt_straight_edge",
    "next_baseline_step",
    "noise_sigma",
    "pick_row_mesa",
    "rake_lines",
    "reduce_basis",
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



# ================================================================================================== precise methods
def fold_angle(angle_deg: float, period: float = 180.0, centre: float = 0.0) -> float:
    """``angle_deg`` modulo ``period`` in ``(centre − period/2, centre + period/2]``; NaN stays NaN. A line is
    defined modulo 180°, a rectangular lattice modulo 90°."""
    if not math.isfinite(angle_deg):
        return math.nan
    r = math.fmod(float(angle_deg) - centre, period)
    if r <= -period / 2.0:
        r += period
    elif r > period / 2.0:
        r -= period
    return centre + r + 0.0


def _grey(image: np.ndarray) -> np.ndarray:
    arr = np.asarray(image, dtype=np.float64)
    if arr.ndim == 3:
        arr = arr[..., :3].mean(axis=2)
    if arr.ndim != 2 or arr.size == 0:
        raise ValueError(f"a non-empty image is required, got shape {np.shape(image)}")
    return arr


def _displayed_deg(dx: float, dy: float) -> float:
    """Direction of the pixel vector ``(dx, dy)`` counter-clockwise **as displayed** (image Y down)."""
    return math.degrees(math.atan2(-dy, dx))


@dataclass(frozen=True)
class EdgeLineFit:
    """A straight edge fitted on sub-pixel edge points (:func:`fit_edge_line`), pixels. ``angle_deg``: the line's
    direction as displayed, in (−90°, 90°]; ``angle_sigma_deg`` its 1σ from the residuals; ``centre_point``: the foot
    of the perpendicular from the image centre ``(W/2, H/2)`` (the centre of :func:`alox.vision.wafer.tracking_move`)
    and ``centre_sigma_px`` the 1σ of the line's position there; ``points``: the edge points used (inliers)."""

    line: Line
    angle_deg: float
    angle_sigma_deg: float
    points: np.ndarray
    candidates: int
    rms_px: float
    centre_point: Point
    centre_sigma_px: float
    horizontal: bool

    @property
    def n_points(self) -> int:
        return int(self.points.shape[0])


def _edge_is_horizontal(grey: np.ndarray, smoothing: float) -> bool:
    """The dominant gradient is vertical (the edge runs left → right)."""
    gy = ndimage.gaussian_filter(grey, max(smoothing, 0.5), order=(1, 0))
    gx = ndimage.gaussian_filter(grey, max(smoothing, 0.5), order=(0, 1))
    return bool(np.sum(gy * gy) >= np.sum(gx * gx))


def edge_points_subpixel(image: np.ndarray, *, horizontal: bool, step_px: int = 4, width_px: int = 5,
                         smoothing_px: float = 1.5, min_strength: float = 10.0,
                         polarity: EdgePolarity | int = EdgePolarity.ALL) -> np.ndarray:
    """One edge point per profile (N×2, pixels): profiles every ``step_px`` pixels across the edge (top → bottom for a
    ``horizontal`` edge, left → right otherwise), each averaged over ``width_px`` parallel lines; the derivative of a
    Gaussian (σ = ``smoothing_px``) gives the strength (scaled so that a sharp step of contrast C gives C); the
    strongest extremum of the ``polarity`` (rising = dark → bright along the profile) of at least ``min_strength``;
    its sub-pixel position by a Gaussian (log-parabola) through the three samples around the peak (exact for a
    Gaussian-blurred step; a parabola when a sample is 0)."""
    grey = _grey(image)
    img = grey if horizontal else grey.T                      # the edge runs along the rows of ``img``
    h, w = img.shape
    step, width, sigma = max(1, int(step_px)), max(1, int(width_px)), max(0.5, float(smoothing_px))
    if width > 1:
        img = ndimage.uniform_filter1d(img, size=width, axis=1, mode="nearest")
    cols = np.arange(((w - 1) % step) // 2, w, step)
    g = ndimage.gaussian_filter1d(img[:, cols], sigma, axis=0, order=1, mode="nearest") * math.sqrt(2 * math.pi) * sigma
    pol = EdgePolarity(int(polarity))
    s = g if pol is EdgePolarity.RISING else (-g if pol is EdgePolarity.FALLING else np.abs(g))
    margin = math.ceil(3.0 * sigma) + 1
    if h <= 2 * margin + 2:
        return np.empty((0, 2))
    s = s.copy()
    s[:margin] = -np.inf
    s[h - margin:] = -np.inf
    k = np.argmax(s, axis=0)
    idx = np.arange(cols.size)
    ok = s[k, idx] >= float(min_strength)
    a = np.abs(g)
    lo, mid, hi = a[k - 1, idx], a[k, idx], a[k + 1, idx]
    with np.errstate(divide="ignore", invalid="ignore"):
        llo, lmid, lhi = np.log(lo), np.log(mid), np.log(hi)
        den = llo - 2.0 * lmid + lhi
        delta = np.where((lo > 0) & (hi > 0) & (den < 0), 0.5 * (llo - lhi) / den, np.nan)
        pden = lo - 2.0 * mid + hi
        parabola = np.where(pden != 0, 0.5 * (lo - hi) / pden, 0.0)
    delta = np.clip(np.where(np.isfinite(delta), delta, parabola), -0.5, 0.5)
    along = cols[ok].astype(np.float64)
    across = k[ok] + delta[ok]
    pts = np.column_stack((along, across)) if horizontal else np.column_stack((across, along))
    return pts.reshape(-1, 2)


def fit_edge_line(image: np.ndarray, *, horizontal: bool | None = None, step_px: int = 4, width_px: int = 5,
                  smoothing_px: float = 1.5, min_strength: float = 10.0,
                  polarity: EdgePolarity | int = EdgePolarity.ALL, max_distance_px: float = 1.5,
                  min_points: int = 5, seed: int = 0) -> EdgeLineFit | None:
    """The straight edge of ``image`` (:func:`edge_points_subpixel`, then :func:`alox.vision.geometry.
    fit_line_ransac`: inliers within ``max_distance_px``, total least squares on them). ``horizontal`` None: from the
    dominant gradient direction. None when fewer than ``min_points`` points support the line."""
    grey = _grey(image)
    if horizontal is None:
        horizontal = _edge_is_horizontal(grey, smoothing_px)
    pts = edge_points_subpixel(grey, horizontal=horizontal, step_px=step_px, width_px=width_px,
                               smoothing_px=smoothing_px, min_strength=min_strength, polarity=polarity)
    if pts.shape[0] < max(2, int(min_points)):
        return None
    fitted = _fit(pts, max_distance_px, seed)
    if fitted is None:
        return None
    line, inliers = fitted
    used = pts[inliers]
    n = used.shape[0]
    if n < max(3, int(min_points)):
        return None
    d = line.signed_distances(used)
    rms = math.sqrt(float(np.sum(d * d)) / max(n - 2, 1))
    dx, dy = line.direction
    s = used @ np.array([dx, dy])
    s_mean = float(s.mean())
    spread = float(np.sum((s - s_mean) ** 2))
    if spread <= 0.0:
        return None
    h, w = grey.shape
    cx, cy = w / 2.0, h / 2.0
    dist = line.a * cx + line.b * cy + line.c
    foot = (cx - line.a * dist, cy - line.b * dist)
    s_foot = foot[0] * dx + foot[1] * dy
    centre_sigma = rms * math.sqrt(1.0 / n + (s_foot - s_mean) ** 2 / spread)
    return EdgeLineFit(line=line, angle_deg=fold_angle(_displayed_deg(dx, dy)),
                       angle_sigma_deg=math.degrees(rms / math.sqrt(spread)), points=used,
                       candidates=int(pts.shape[0]), rms_px=rms, centre_point=foot, centre_sigma_px=centre_sigma,
                       horizontal=bool(horizontal))


# -------------------------------------------------------------------------------------------------- lattice
def _otsu(values: np.ndarray) -> float:
    v = np.asarray(values, dtype=np.float64).ravel()
    lo, hi = float(v.min()), float(v.max())
    if hi <= lo:
        return lo
    hist, edges = np.histogram(v, bins=256, range=(lo, hi))
    centres = (edges[:-1] + edges[1:]) / 2.0
    w0 = np.cumsum(hist).astype(np.float64)
    w1 = w0[-1] - w0
    m = np.cumsum(hist * centres)
    with np.errstate(divide="ignore", invalid="ignore"):
        between = w0 * w1 * (m / w0 - (m[-1] - m) / w1) ** 2
    between[~np.isfinite(between)] = -1.0
    return float(edges[int(np.argmax(between)) + 1])


def noise_sigma(image: np.ndarray) -> float:
    """The pixel noise (1σ) of an image: the median absolute difference of horizontal neighbours / (0.6745·√2),
    insensitive to the edges (a minority of the differences)."""
    grey = _grey(image)
    if grey.shape[1] < 2:
        return 0.0
    return float(np.median(np.abs(np.diff(grey, axis=1)))) / (0.6745 * math.sqrt(2.0))


def detect_mesas(image: np.ndarray, *, bright: bool | None = None, min_area_px: float = 6.0,
                 smoothing_px: float = 1.0, min_snr: float = 6.0) -> np.ndarray:
    """Centres (N×2, pixels) of the mesas (discs or any compact pads) of a grid of any type: Otsu threshold of the
    lightly smoothed image, the bright or the dark phase as objects (``bright`` None: the phase that gives the more
    mesas, so that a dense grid — a hexagonal one covers up to 91 % of the surface — is found whichever phase is the
    larger; the minority phase on a tie); nothing when the two phases differ by less than ``min_snr`` ×
    :func:`noise_sigma` (no pattern); 8-connected particles, those touching the border and those whose area is outside
    [½, 2] × the median (merged, cut) dropped; each centre is the grey-weighted centroid (weights ``(I − background) /
    (object − background)`` clipped to [0, 1]) over the particle grown by 2 pixels, unbiased by the threshold for a
    symmetric object."""
    grey = _grey(image)
    smooth = ndimage.gaussian_filter(grey, smoothing_px) if smoothing_px > 0 else grey
    high = smooth > _otsu(smooth)
    if bright is not None:
        return _phase_centres(grey, high if bright else ~high, min_area_px, min_snr)
    minority = bool(high.mean() <= 0.5)
    first = _phase_centres(grey, high if minority else ~high, min_area_px, min_snr)
    second = _phase_centres(grey, ~high if minority else high, min_area_px, min_snr)
    return second if len(second) > len(first) else first


def _phase_centres(grey: np.ndarray, mask: np.ndarray, min_area_px: float, min_snr: float) -> np.ndarray:
    """The particle centres of one phase of :func:`detect_mesas`."""
    if not mask.any() or mask.all():
        return np.empty((0, 2))
    labels, count = ndimage.label(mask, structure=np.ones((3, 3)))
    index = np.arange(1, count + 1)
    areas = np.asarray(ndimage.sum_labels(np.ones_like(grey), labels, index))
    grown = np.where(labels > 0, labels, ndimage.grey_dilation(labels, size=(5, 5)))
    border = np.unique(np.concatenate((grown[0], grown[-1], grown[:, 0], grown[:, -1])))
    fg, bg = float(np.median(grey[mask])), float(np.median(grey[~mask]))
    if fg == bg or abs(fg - bg) < float(min_snr) * noise_sigma(grey):
        return np.empty((0, 2))
    weights = np.clip((grey - bg) / (fg - bg), 0.0, 1.0)
    total = np.asarray(ndimage.sum_labels(weights, grown, index))
    keep = (areas >= float(min_area_px)) & ~np.isin(index, border) & (total > 0)
    if not keep.any():
        return np.empty((0, 2))
    median = float(np.median(areas[keep]))
    keep &= (areas >= 0.5 * median) & (areas <= 2.0 * median)
    centres = np.asarray(ndimage.center_of_mass(weights, grown, index[keep]), dtype=np.float64).reshape(-1, 2)
    return centres[:, ::-1].copy()                                # (row, col) → (x, y)


#: Tolerances of the lattice classification (:func:`fit_lattice`): relative length difference and angle (°).
LATTICE_LENGTH_TOLERANCE = 0.03
LATTICE_ANGLE_TOLERANCE_DEG = 2.0
#: Rotational symmetry (°) of each lattice kind: a row direction is defined modulo this period.
LATTICE_PERIODS = {"hexagonal": 60.0, "square": 90.0, "rectangular": 180.0, "centred rectangular": 180.0,
                   "oblique": 180.0}


@dataclass(frozen=True)
class LatticeFit:
    """A 2-D lattice of any type and orientation fitted on mesa centres (:func:`fit_lattice`), pixels:
    ``node(i, j) = origin + i·row + j·column``. ``row`` is a shortest lattice vector (the densest row of mesas; among
    the equally short ones, the nearest the hint direction), ``column`` the second vector of the reduced basis;
    ``kind`` the lattice type (``hexagonal``, ``square``, ``rectangular``, ``centred rectangular`` or ``oblique``) and
    ``period_deg`` its rotational symmetry (60°, 90°, else 180°: the row direction is defined modulo it);
    ``angle_deg`` the row direction as displayed, folded to ``hint ± period/2``; ``angle_sigma_deg`` its 1σ from the
    least-squares covariance; ``rms_px`` the 1σ of a centre per axis (residuals); ``centres`` / ``indices`` the
    centres used and their ``(i, j)``."""

    origin: Point
    row: Point
    column: Point
    angle_deg: float
    angle_sigma_deg: float
    rms_px: float
    centres: np.ndarray
    indices: np.ndarray
    kind: str = "oblique"
    period_deg: float = 180.0

    @property
    def pitch_row_px(self) -> float:
        return math.hypot(*self.row)

    @property
    def pitch_across_px(self) -> float:
        """The distance between two rows (the column vector's component across the rows)."""
        return abs(self.row[0] * self.column[1] - self.row[1] * self.column[0]) / self.pitch_row_px

    def node(self, i: float, j: float) -> Point:
        return (self.origin[0] + i * self.row[0] + j * self.column[0],
                self.origin[1] + i * self.row[1] + j * self.column[1])


def reduce_basis(a: Sequence[float], b: Sequence[float]) -> tuple[np.ndarray, np.ndarray]:
    """Lagrange–Gauss reduction of the lattice basis ``(a, b)``: the two shortest independent lattice vectors
    ``(r1, r2)``, ``|r1| ≤ |r2|`` and ``|r1·r2| ≤ |r1|²/2`` (their angle in [60°, 120°]), as the columns of the first
    array, and the unimodular integer matrix ``M`` with ``[r1 r2] = [a b] @ M``."""
    basis = np.column_stack((np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)))
    m = np.eye(2)
    if abs(float(np.linalg.det(basis))) < 1e-12:
        raise ValueError("the lattice vectors are collinear")
    for _ in range(200):
        if basis[:, 1] @ basis[:, 1] < basis[:, 0] @ basis[:, 0]:
            basis, m = basis[:, ::-1].copy(), m[:, ::-1].copy()
        k = round(float(basis[:, 0] @ basis[:, 1]) / float(basis[:, 0] @ basis[:, 0]))
        if k == 0:
            break
        basis[:, 1] -= k * basis[:, 0]
        m[:, 1] -= k * m[:, 0]
    return basis, m


def lattice_kind(r1: Sequence[float], r2: Sequence[float], *, length_tol: float = LATTICE_LENGTH_TOLERANCE,
                 angle_tol_deg: float = LATTICE_ANGLE_TOLERANCE_DEG) -> str:
    """The type of the lattice of the reduced basis ``(r1, r2)`` (:func:`reduce_basis`): ``hexagonal`` (equal
    lengths, 60°/120°), ``square``, ``rectangular`` (90°), ``centred rectangular`` (equal lengths, or ``r2``
    projecting on half of ``r1``: a staggered grid) or ``oblique``, within ``length_tol`` (relative) and
    ``angle_tol_deg``."""
    u, v = np.asarray(r1, dtype=np.float64), np.asarray(r2, dtype=np.float64)
    lu, lv = math.hypot(*u), math.hypot(*v)
    angle = math.degrees(math.acos(max(-1.0, min(1.0, float(u @ v) / (lu * lv)))))
    equal = abs(lv - lu) <= length_tol * lu
    if equal and min(abs(angle - 60.0), abs(angle - 120.0)) <= angle_tol_deg:
        return "hexagonal"
    if abs(angle - 90.0) <= angle_tol_deg:
        return "square" if equal else "rectangular"
    half = abs(float(u @ v)) / lu                                  # r2's projection on r1
    if equal or abs(half - lu / 2.0) <= length_tol * lu:
        return "centred rectangular"
    return "oblique"


def _coarse_basis(pts: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
    """The two shortest independent vectors that occur often between neighbouring centres (each within 15 % of the
    nearest-neighbour distance of at least 30 % as many neighbour pairs as the most frequent one), averaged over
    their occurrences; None without two independent ones."""
    from scipy.spatial import cKDTree

    n = pts.shape[0]
    dist, nn = cKDTree(pts).query(pts, k=min(25, n))
    vec = (pts[nn[:, 1:]] - pts[:, None, :]).reshape(-1, 2)
    dist = dist[:, 1:].ravel()
    dmin = float(np.median(dist.reshape(n, -1)[:, 0]))
    if not dmin > 0:
        return None
    cand = vec[np.isfinite(dist) & (dist >= 0.5 * dmin) & (dist < 4.5 * dmin)]
    if cand.shape[0] < 2:
        return None
    tol = 0.15 * dmin
    both = np.vstack((cand, -cand))
    support = np.asarray(cKDTree(both).query_ball_point(cand, tol, return_length=True))
    frequent = cand[support >= 0.3 * support.max()]
    lengths = np.hypot(*frequent.T)

    def mean_of(c: np.ndarray) -> np.ndarray:
        near = both[np.hypot(*(both - c).T) < tol]
        return near.mean(axis=0)
    first = frequent[int(np.argmin(lengths))]
    b1 = mean_of(first)
    if not math.hypot(*b1) > 0.0:
        return None
    cross = np.abs(frequent[:, 0] * b1[1] - frequent[:, 1] * b1[0]) / (lengths * math.hypot(*b1))
    other = cross > math.sin(math.radians(10.0))
    if not other.any():
        return None
    b2 = mean_of(frequent[other][int(np.argmin(lengths[other]))])
    return b1, b2


def _angle_gap(a_deg: float, b_deg: float) -> float:
    return abs(fold_angle(a_deg - b_deg, 360.0))


def fit_lattice(centres: np.ndarray, *, reference: Sequence[float] | None = None, min_nodes: int = 6,
                min_fraction: float = 0.6, angle_hint_deg: float | None = None) -> LatticeFit | None:
    """A lattice of any type (square, rectangular, hexagonal, centred rectangular, oblique) and any orientation
    through the ``centres``:

    1. the coarse basis: the two shortest independent vectors that occur often between neighbouring centres
       (:func:`_coarse_basis`), no assumption on their directions;
    2. integer indices from the centre nearest ``reference`` (default the centroid), then a least-squares fit of
       ``origin`` and the two lattice vectors with re-indexing and rejection of centres farther than ¼ of the
       shorter vector from their node (3 passes);
    3. the reduced basis of the fitted vectors (:func:`reduce_basis`: the two shortest independent lattice vectors),
       the lattice type and its rotational symmetry (:func:`lattice_kind`, :data:`LATTICE_PERIODS`);
    4. the row: among the shortest lattice vectors (equal within :data:`LATTICE_LENGTH_TOLERANCE`: 6 for a hexagonal
       lattice, 4 for a square one, ± the shortest otherwise), the one whose direction as displayed is nearest
       ``angle_hint_deg`` (default 0°, the image X axis); ``angle_deg`` its direction folded to ``hint ± period/2``;
       the indices are re-expressed on ``(row, column)`` and the vectors refitted (the 1σ of the angle).

    None with fewer than ``min_nodes`` centres on the lattice, fewer than ``min_fraction`` of the centres on it, or
    fewer than two distinct indices along each vector."""
    pts = np.asarray(centres, dtype=np.float64).reshape(-1, 2)
    n = pts.shape[0]
    if n < max(4, int(min_nodes)):
        return None
    coarse = _coarse_basis(pts)
    if coarse is None:
        return None
    basis0 = np.column_stack(coarse)
    ref = np.asarray(reference if reference is not None else pts.mean(axis=0), dtype=np.float64)
    base = pts[int(np.argmin(np.hypot(*(pts - ref).T)))]
    ij = np.rint(np.linalg.solve(basis0, (pts - base).T).T)
    keep = np.ones(n, dtype=bool)
    coef = np.zeros((3, 2))
    for _ in range(3):
        if np.unique(ij[keep, 0]).size < 2 or np.unique(ij[keep, 1]).size < 2 or keep.sum() < 4:
            return None
        x = np.column_stack((np.ones(n), ij))
        coef, *_ = np.linalg.lstsq(x[keep], pts[keep], rcond=None)
        basis = coef[1:].T                                         # columns: a, b
        if abs(np.linalg.det(basis)) < 1e-9:
            return None
        ij = np.rint(np.linalg.solve(basis, (pts - coef[0]).T).T)
        res = np.hypot(*(pts - np.column_stack((np.ones(n), ij)) @ coef).T)
        keep = res <= 0.25 * min(math.hypot(*coef[1]), math.hypot(*coef[2]))
        # one centre per node: the nearest
        order = np.lexsort((res, ij[:, 1], ij[:, 0]))
        dup = np.zeros(n, dtype=bool)
        same = np.all(ij[order][1:] == ij[order][:-1], axis=1)
        dup[order[1:][same]] = True
        keep &= ~dup
    if (keep.sum() < max(4, int(min_nodes), float(min_fraction) * n) or np.unique(ij[keep, 0]).size < 2
            or np.unique(ij[keep, 1]).size < 2):
        return None
    # the reduced basis, the type, the row nearest the hint
    reduced, m = reduce_basis(coef[1], coef[2])
    r1, r2 = reduced[:, 0], reduced[:, 1]
    kind = lattice_kind(r1, r2)
    period = LATTICE_PERIODS[kind]
    hint = 0.0 if angle_hint_deg is None else float(angle_hint_deg)
    shortest = math.hypot(*r1)
    choices = []                                                   # (vector, its integer coordinates on (r1, r2))
    for c in ((1, 0), (0, 1), (1, 1), (1, -1)):
        for sign in (1, -1):
            k = np.array(c, dtype=np.float64) * sign
            vec = reduced @ k
            if math.hypot(*vec) <= (1.0 + LATTICE_LENGTH_TOLERANCE) * shortest:
                choices.append((vec, k))
    _, k_row = min(choices, key=lambda ch: _angle_gap(_displayed_deg(*ch[0]), hint))
    # the column: the other reduced vector (r2, or r1 when the row is r2), so that (row, column) is a basis
    k_col = np.array([0.0, 1.0]) if k_row[1] == 0 else np.array([1.0, 0.0])
    t = m @ np.column_stack((k_row, k_col))                        # (row, column) on the fitted (a, b)
    ij = np.rint(np.linalg.solve(t, ij.T).T)
    x = np.column_stack((np.ones(n), ij))[keep]
    coef, *_ = np.linalg.lstsq(x, pts[keep], rcond=None)
    resid = pts[keep] - x @ coef
    count = int(keep.sum())
    rms = math.sqrt(float(np.sum(resid * resid)) / (2.0 * max(count - 3, 1)))
    cov = np.linalg.inv(x.T @ x) * rms * rms
    row, column = coef[1], coef[2]
    sigma = math.degrees(math.sqrt(cov[1, 1]) / math.hypot(*row))
    return LatticeFit(origin=(float(coef[0, 0]), float(coef[0, 1])), row=(float(row[0]), float(row[1])),
                      column=(float(column[0]), float(column[1])),
                      angle_deg=fold_angle(_displayed_deg(*row), period, hint), angle_sigma_deg=sigma, rms_px=rms,
                      centres=pts[keep], indices=ij[keep], kind=kind, period_deg=period)


# -------------------------------------------------------------------------------------------------- long baseline
@dataclass(frozen=True)
class BaselineFit:
    """The line through points measured at stage positions far apart (:func:`fit_baseline`), in the points' frame
    (wafer mm, Y up): ``angle_deg`` = ``atan2(Δy, Δx)`` of the line folded modulo ``period`` around ``centre``;
    ``sigma_deg`` its 1σ; ``length`` the extent of the points along the line; ``rms`` the rms perpendicular residual
    (0 for two points); ``point`` / ``direction`` the line (centroid, unit vector)."""

    angle_deg: float
    sigma_deg: float
    length: float
    rms: float
    point: Point
    direction: Point
    n_points: int


def fit_baseline(points: Sequence[Sequence[float]], sigmas: Sequence[float] | None = None, *,
                 extra_sigma: float = 0.0, period: float = 180.0, centre: float = 0.0) -> BaselineFit:
    """Total-least-squares line through ``points`` (at least two distinct). The uncertainty: each point's 1σ across
    the line ``sqrt(sigma_i² + extra_sigma²)`` (detection and stage repeatability) over the spread of the points
    along it, ``σθ = σ / sqrt(Σ(s − s̄)²)`` (two points at L apart: ``√2·σ / L``); with three points or more, the
    residuals' estimate ``sqrt(Σd² / (n − 2)) / sqrt(Σ(s − s̄)²)`` when larger."""
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    n = pts.shape[0]
    line = fit_line_tls(pts)
    dx, dy = line.direction
    s = pts @ np.array([dx, dy])
    spread = float(np.sum((s - s.mean()) ** 2))
    sig = np.zeros(n) if sigmas is None else np.asarray(sigmas, dtype=np.float64).reshape(n)
    sigma_pt = math.sqrt(float(np.mean(sig * sig)) + float(extra_sigma) ** 2)
    d = line.signed_distances(pts)
    rms = math.sqrt(float(np.mean(d * d))) if n > 2 else 0.0
    sigma = sigma_pt / math.sqrt(spread)
    if n > 2:
        sigma = max(sigma, math.sqrt(float(np.sum(d * d)) / (n - 2)) / math.sqrt(spread))
    centroid = pts.mean(axis=0)
    return BaselineFit(angle_deg=fold_angle(math.degrees(math.atan2(dy, dx)), period, centre),
                       sigma_deg=math.degrees(sigma), length=float(s.max() - s.min()), rms=rms,
                       point=(float(centroid[0]), float(centroid[1])), direction=(dx, dy), n_points=n)


def baseline_target(points: Sequence[Sequence[float]], start: Sequence[float], direction: Sequence[float],
                    offset: float) -> Point:
    """Where the feature is expected at ``offset`` along the line from the ``start`` point: on the line through the
    ``points`` measured so far (one point: the line through ``start`` along ``direction``, a unit vector), ``offset``
    from the projection of ``start``; the line's direction is oriented as ``direction``."""
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    p0 = np.asarray(start, dtype=np.float64)
    u = np.asarray(direction, dtype=np.float64)
    if pts.shape[0] >= 2:
        fit = fit_baseline(pts)
        d = np.asarray(fit.direction)
        if d @ u < 0:
            d = -d
        c = np.asarray(fit.point)
        foot = c + ((p0 - c) @ d) * d
        u = d
    else:
        foot = p0
    target = foot + float(offset) * u
    return float(target[0]), float(target[1])


def next_baseline_step(previous: float, half_length: float, *, sigma_deg: float, drift_limit: float,
                       growth: float = 8.0, k_sigma: float = 4.0) -> float:
    """The next half-baseline of a walk ±s₁, ±s₂, … along the feature: at most ``growth`` × the previous one and,
    unless that is less than 1.5 × the previous, the longest for which the feature predicted with the angle's
    uncertainty stays within ``drift_limit`` across the line at ``k_sigma`` σ; never beyond ``half_length``."""
    tan = math.tan(math.radians(k_sigma * max(float(sigma_deg), 0.0)))
    safe = float(drift_limit) / tan if tan > 0 else math.inf
    return min(float(half_length), min(growth * previous, max(safe, 1.5 * previous)))


def pick_row_mesa(candidates: Sequence[Sequence[float]], target: Sequence[float], direction: Sequence[float], *,
                  pitch_along: float, pitch_across: float, line_point: Sequence[float],
                  tolerance: float = 0.25) -> tuple[int, float] | None:
    """The candidate (wafer mm) nearest ``target`` in lattice units, and its signed distance across the predicted row
    (the line through ``line_point`` along the unit ``direction``); None when there is none or when it lies more than
    ``tolerance`` × ``pitch_across`` off the row (the row cannot be told apart from its neighbours)."""
    pts = np.asarray(candidates, dtype=np.float64).reshape(-1, 2)
    if pts.shape[0] == 0:
        return None
    u = np.asarray(direction, dtype=np.float64)
    nrm = np.array([-u[1], u[0]])
    rel = pts - np.asarray(target, dtype=np.float64)
    score = np.hypot((rel @ u) / pitch_along, (rel @ nrm) / pitch_across)
    best = int(np.argmin(score))
    across = float((pts[best] - np.asarray(line_point, dtype=np.float64)) @ nrm)
    if abs(across) > tolerance * pitch_across:
        return None
    return best, across
