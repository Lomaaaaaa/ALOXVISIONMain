"""Circles and roundness of a contour: the mesa / oxide-aperture diameters of ALOX.

Spec: ``docs/vision-core/map.md`` Part 4 §3.7 and Part 3 §3.10 (measure mesa). LabVIEW VIs covered:
``geometry_circle_center_from_3_points``, ``geometry_check_points_in_circle``, ``geometry_check_triangle_obtuse``,
``geometry_circumscribed_circle`` (Elzinga–Hearn), ``geometry_inscribed_circle`` (Delaunay),
``geometry_circle_diameter_equivalent_surface``, ``geometry_circle_diameter_equivalent_perimeter``,
``RoundnessCalculations-NI`` + ``MZC cost cno`` (least-squares and minimum-zone circles).

Units: whatever the points are in (pixels in LabVIEW; ``alox_tools_convert_mesa_measures_to_micrometers d`` then
multiplies the seven diameters External…Circumscribed by the pixel size, ×1000 when the calibration unit is mm).

NaN rules kept from LabVIEW: a failed construction returns :data:`NAN_CIRCLE` ``{NaN, NaN, NaN}`` (Error case of every
VI, Elzinga–Hearn after 20 000 iterations, no Delaunay circle); :func:`contour_diameters` turns a 0 diameter into NaN
(``ZERO->NAN`` nodes of ``alox_function_ll_measure_mesa_state_machine d40``).

Deliberate numeric deviations: everything is float64 (LabVIEW returns the circumcircle radius and the polygon area as
SGL); NI's ``Constrained Nonlinear Optimization`` and ``Fitting on a Sphere`` are replaced by SciPy (SLSQP, geometric
least squares), whose stopping rules differ (the NI front-panel settings ``stopping criteria``/``cno settings``/``bounds
MZC`` are not visible in the export).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy import optimize, spatial

from .geometry import PointsLike, as_points

__all__ = [
    "NAN_CIRCLE",
    "Circle",
    "ContourDiameters",
    "Roundness",
    "circle_from_3_points",
    "circularity",
    "circumscribed_circle",
    "contour_diameters",
    "fit_circle_least_squares",
    "inscribed_circle",
    "minimum_zone_circles",
    "perimeter_equivalent_diameter",
    "polygon_area",
    "polygon_perimeter",
    "surface_equivalent_diameter",
]

#: Squared-distance tolerance of ``geometry_check_points_in_circle d`` ("Epsilon" 1E-8).
IN_CIRCLE_EPS = 1e-8
#: Point-in-polygon tolerance of ``geometry_inscribed_circle d`` ("1E-8 Epsilon").
IN_HULL_EPS = 1e-8
#: "Max Iterations" of ``geometry_circumscribed_circle d``.
ELZINGA_HEARN_MAX_ITERATIONS = 20_000


@dataclass(frozen=True)
class Circle:
    """LabVIEW ``Circle`` / ``Result`` cluster {X, Y, Radius}."""

    x: float
    y: float
    radius: float

    def __post_init__(self) -> None:
        for name in ("x", "y", "radius"):
            object.__setattr__(self, name, float(getattr(self, name)))

    @property
    def center(self) -> tuple[float, float]:
        return (self.x, self.y)

    @property
    def diameter(self) -> float:
        return 2.0 * self.radius

    @property
    def is_nan(self) -> bool:
        return math.isnan(self.x) or math.isnan(self.y) or math.isnan(self.radius)

    def radial_distances(self, points: PointsLike) -> np.ndarray:
        pts = as_points(points)
        return np.hypot(pts[:, 0] - self.x, pts[:, 1] - self.y)


NAN_CIRCLE = Circle(math.nan, math.nan, math.nan)


# --------------------------------------------------------------------------------------------------------------------
# Polygon measures
# --------------------------------------------------------------------------------------------------------------------


def polygon_area(points: PointsLike) -> float:
    """Area of the closed polygon (shoelace formula, absolute value) — ``IMAQ Polygon Area``; 0 for < 3 points."""
    pts = as_points(points)
    if pts.shape[0] < 3:
        return 0.0
    x, y = pts[:, 0], pts[:, 1]
    x0, y0 = x - x.mean(), y - y.mean()  # centred: no cancellation for large offsets
    return 0.5 * abs(float(np.dot(x0, np.roll(y0, -1)) - np.dot(y0, np.roll(x0, -1))))


def surface_equivalent_diameter(points: PointsLike) -> tuple[float, float]:
    """``(d, A)`` with ``d = 2·sqrt(A/π)`` — ``geometry_circle_diameter_equivalent_surface d``."""
    area = polygon_area(points)
    return 2.0 * math.sqrt(area / math.pi), area


def polygon_perimeter(points: PointsLike) -> float:
    """Closed-polygon perimeter ``P = Σ |p[(i+1) mod N] − p[i]|`` — what ``geometry_circle_diameter_equivalent_perimeter
    d`` actually returns (no ``/π`` is visible inside the VI)."""
    pts = as_points(points)
    if pts.shape[0] < 2:
        return 0.0
    return float(np.hypot(*(np.roll(pts, -1, axis=0) - pts).T).sum())


def perimeter_equivalent_diameter(points: PointsLike) -> float:
    """``P/π``, the diameter of the circle with the polygon's perimeter.

    Open question (map Part 4 §9 Q1): the LabVIEW VI of this name returns ``P`` itself and the ``/π`` was not found in
    the caller; the measure-mesa diameter report has no perimeter field. Use :func:`polygon_perimeter` for the raw
    LabVIEW value.
    """
    return polygon_perimeter(points) / math.pi


# --------------------------------------------------------------------------------------------------------------------
# Elementary circles
# --------------------------------------------------------------------------------------------------------------------


def circle_from_3_points(p1: Sequence[float], p2: Sequence[float], p3: Sequence[float]) -> Circle:
    """Circumcircle of three points — formula node of ``geometry_circle_center_from_3_points d``.

    ``D = 2(x1(y2−y3) + x2(y3−y1) + x3(y1−y2))``, ``xc = ((x1²+y1²)(y2−y3) + (x2²+y2²)(y3−y1) + (x3²+y3²)(y1−y2))/D``,
    ``yc = ((x1²+y1²)(x3−x2) + (x2²+y2²)(x1−x3) + (x3²+y3²)(x2−x1))/D``, radius = distance centre–point 1. LabVIEW has
    no ``D = 0`` guard (it returns ±inf/NaN); here collinear or coincident points give :data:`NAN_CIRCLE`. The formula
    is evaluated relative to ``p1`` for precision.
    """
    x1, y1 = float(p1[0]), float(p1[1])
    bx, by = float(p2[0]) - x1, float(p2[1]) - y1
    cx, cy = float(p3[0]) - x1, float(p3[1]) - y1
    d = 2.0 * (bx * cy - by * cx)
    if d == 0.0 or not math.isfinite(d):
        return NAN_CIRCLE
    b2, c2 = bx * bx + by * by, cx * cx + cy * cy
    ux = (cy * b2 - by * c2) / d
    uy = (bx * c2 - cx * b2) / d
    return Circle(x1 + ux, y1 + uy, math.hypot(ux, uy))


def fit_circle_least_squares(points: PointsLike) -> Circle:
    """Least-squares circle (LSC): minimises ``Σ (|p_i − c| − R)²``.

    ``RoundnessCalculations-NI d`` uses NI ``Fitting on a Sphere.vi`` with ``Z = 0·X`` (a 2-D circle fit). Here: the
    algebraic (Kåsa) fit as the start, refined by :func:`scipy.optimize.least_squares` on the geometric residuals.
    Fewer than 3 points or collinear points give :data:`NAN_CIRCLE`.
    """
    pts = as_points(points)
    if pts.shape[0] < 3 or not np.all(np.isfinite(pts)):
        return NAN_CIRCLE
    origin = pts.mean(axis=0)
    local = pts - origin
    scale = float(np.max(np.abs(local)))
    if scale == 0.0:
        return NAN_CIRCLE
    u = local / scale
    a = np.column_stack((u[:, 0], u[:, 1], np.ones(len(u))))
    rhs = (u**2).sum(axis=1)
    sol, *_ = np.linalg.lstsq(a, rhs, rcond=None)
    cx, cy = sol[0] / 2.0, sol[1] / 2.0
    r2 = sol[2] + cx * cx + cy * cy
    if not np.isfinite(r2) or r2 <= 0.0 or np.linalg.matrix_rank(a) < 3:
        return NAN_CIRCLE

    def residuals(p: np.ndarray) -> np.ndarray:
        return np.hypot(u[:, 0] - p[0], u[:, 1] - p[1]) - p[2]

    res = optimize.least_squares(residuals, np.array([cx, cy, math.sqrt(r2)]), method="lm", xtol=1e-15, ftol=1e-15)
    px, py, pr = res.x
    if not res.success or not np.all(np.isfinite(res.x)):
        px, py, pr = cx, cy, math.sqrt(r2)
    return Circle(float(origin[0] + px * scale), float(origin[1] + py * scale), float(abs(pr) * scale))


# --------------------------------------------------------------------------------------------------------------------
# Circumscribed circle: Elzinga–Hearn
# --------------------------------------------------------------------------------------------------------------------


def _farthest_outside(pts: np.ndarray, cx: float, cy: float, r2: float) -> int | None:
    """``geometry_check_points_in_circle d``: index of the farthest point, outside if ``d²max − r² > 0`` and
    ``|d²max − r²| ≥ 1e-8``; ``None`` when every point is inside."""
    d2 = (pts[:, 0] - cx) ** 2 + (pts[:, 1] - cy) ** 2
    k = int(np.argmax(d2))
    diff = float(d2[k]) - r2
    return k if diff > 0.0 and abs(diff) >= IN_CIRCLE_EPS else None


def _diameter_circle(p: np.ndarray, q: np.ndarray) -> tuple[float, float, float]:
    cx, cy = (p[0] + q[0]) / 2.0, (p[1] + q[1]) / 2.0
    return cx, cy, (p[0] - cx) ** 2 + (p[1] - cy) ** 2


def _obtuse_pair(pts: np.ndarray, tri: tuple[int, int, int]) -> tuple[int, int] | None:
    """``geometry_check_triangle_obtuse d``: the two vertices opposite an obtuse angle (> 90°), else ``None``.

    Degenerate triangles (collinear or repeated points) count as obtuse at the middle vertex, so the longest side is
    kept; a right angle is not obtuse (its circumcircle is the diameter circle anyway).
    """
    i, j, k = tri
    for apex, (u, v) in ((i, (j, k)), (j, (i, k)), (k, (i, j))):
        e1, e2 = pts[u] - pts[apex], pts[v] - pts[apex]
        if float(np.dot(e1, e2)) < 0.0:
            return (u, v)
    sides = {(j, k): np.sum((pts[j] - pts[k]) ** 2), (i, k): np.sum((pts[i] - pts[k]) ** 2),
             (i, j): np.sum((pts[i] - pts[j]) ** 2)}
    (u, v), longest = max(sides.items(), key=lambda kv: kv[1])
    others = sum(sides.values()) - longest
    if longest > 0.0 and longest >= others and min(sides.values()) == 0.0:
        return (u, v)  # two coincident vertices
    e1, e2 = pts[j] - pts[i], pts[k] - pts[i]
    if longest > 0.0 and abs(float(e1[0] * e2[1] - e1[1] * e2[0])) <= 1e-15 * longest:
        return (u, v)  # collinear
    return None


def _elzinga_hearn_swap(
    pts: np.ndarray, tri: tuple[int, int, int], new: int, cx: float, cy: float
) -> tuple[int, int, int]:
    """Step 2 of Elzinga–Hearn (``geometry_circumscribed_circle d6``): keep ``Q``, the vertex farthest from the new
    point ``l``, and the vertex on the other side of the diameter through ``Q``; replace the third by ``l``."""
    pl = pts[new]
    q = max(tri, key=lambda t: float(np.sum((pts[t] - pl) ** 2)))
    rest = [t for t in tri if t != q]
    ax, ay = pts[q][0] - cx, pts[q][1] - cy

    def side(p: np.ndarray) -> float:
        return ax * (p[1] - cy) - ay * (p[0] - cx)

    s_l = side(pl)
    same = [t for t in rest if side(pts[t]) * s_l > 0.0]
    replaced = same[0] if same else min(rest, key=lambda t: float(np.sum((pts[t] - pl) ** 2)))
    keep = next(t for t in rest if t != replaced)
    return q, keep, new


def circumscribed_circle(points: PointsLike, *, max_iterations: int = ELZINGA_HEARN_MAX_ITERATIONS) -> Circle:
    """Minimum enclosing circle by the Elzinga–Hearn algorithm — ``geometry_circumscribed_circle d … d7``.

    Steps as wired in LabVIEW (start ``i = 0``, ``j = 1``):

    0. circle on the diameter ``[i, j]``; if a point is outside (the farthest one, :func:`_farthest_outside`) → ``k``,
       step 1, else done;
    1. if the triangle ``i, j, k`` is obtuse, keep the two vertices opposite the obtuse angle as ``i, j`` → step 0;
       otherwise circumcircle of ``i, j, k``; if a point is outside → ``l``, step 2, else done;
    2. ``Q`` = vertex of ``i, j, k`` farthest from ``l``; the vertex on the same side as ``l`` of the line through
       ``Q`` and the centre is replaced by ``l`` → step 1.

    Each step is one iteration; after ``max_iterations`` (20 000) the result is :data:`NAN_CIRCLE` as in LabVIEW.
    The result is the unique smallest enclosing circle. No point → NaN; one point → radius 0.
    """
    pts = as_points(points)
    n = pts.shape[0]
    if n == 0 or not np.all(np.isfinite(pts)):
        return NAN_CIRCLE
    if n == 1:
        return Circle(float(pts[0, 0]), float(pts[0, 1]), 0.0)
    i, j, k, new = 0, 1, -1, -1
    step = 0
    cx = cy = r2 = math.nan
    for _ in range(max_iterations):
        if step == 0:
            cx, cy, r2 = _diameter_circle(pts[i], pts[j])
            out = _farthest_outside(pts, cx, cy, r2)
            if out is None:
                return Circle(cx, cy, math.sqrt(r2))
            k, step = out, 1
        elif step == 1:
            pair = _obtuse_pair(pts, (i, j, k))
            if pair is not None:
                i, j = pair
                step = 0
                continue
            c = circle_from_3_points(pts[i], pts[j], pts[k])
            cx, cy, r2 = c.x, c.y, c.radius**2
            out = _farthest_outside(pts, cx, cy, r2)
            if out is None:
                return Circle(cx, cy, math.sqrt(r2))
            new, step = out, 2
        else:
            i, j, k = _elzinga_hearn_swap(pts, (i, j, k), new, cx, cy)
            step = 1
    return NAN_CIRCLE


# --------------------------------------------------------------------------------------------------------------------
# Inscribed circle: Delaunay
# --------------------------------------------------------------------------------------------------------------------


def inscribed_circle(points: PointsLike, *, allow_center_outside_hull: bool = False) -> Circle:
    """Largest empty circle among the Delaunay circumcircles — ``geometry_inscribed_circle d``.

    For each Delaunay triangle of the points, its circumcircle; keep the largest one (strictly greater radius) whose
    centre is inside the convex hull of the points (boundary included, ε = 1e-8). If none is found and
    ``allow_center_outside_hull``, the largest circle with its centre outside the hull; otherwise
    :data:`NAN_CIRCLE` (the initial value). Fewer than 3 points or collinear points (no triangulation) → NaN.
    This is the LabVIEW definition, not the distance-transform maximum.
    """
    pts = as_points(points)
    if pts.shape[0] < 3 or not np.all(np.isfinite(pts)):
        return NAN_CIRCLE
    origin = pts.mean(axis=0)
    local = pts - origin
    try:
        tri = spatial.Delaunay(local)
        hull = spatial.ConvexHull(local)
    except (spatial.QhullError, ValueError):
        return NAN_CIRCLE
    scale = max(1.0, float(np.max(np.abs(local))))
    best_in: Circle | None = None
    best_out: Circle | None = None
    max_in = 0.0
    max_out = 0.0
    for simplex in tri.simplices:
        c = circle_from_3_points(local[simplex[0]], local[simplex[1]], local[simplex[2]])
        if c.is_nan or not (c.radius > max_in):
            continue
        inside = bool(np.all(hull.equations[:, :2] @ (c.x, c.y) + hull.equations[:, 2] <= IN_HULL_EPS * scale))
        if inside:
            max_in, best_in = c.radius, c
        elif c.radius > max_out:
            max_out, best_out = c.radius, c
    chosen = best_in if best_in is not None else (best_out if allow_center_outside_hull else None)
    if chosen is None:
        return NAN_CIRCLE
    return Circle(chosen.x + float(origin[0]), chosen.y + float(origin[1]), chosen.radius)


# --------------------------------------------------------------------------------------------------------------------
# Roundness: least-squares and minimum-zone circles
# --------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Roundness:
    """Outputs of ``RoundnessCalculations-NI`` (MIC/MCC are in a Disabled frame and not produced)."""

    lsc: Circle  # least-squares circle
    lsc_minimum: float  # Σ (d_i − R)² of the LSC (NI "residue" definition not visible; inferred)
    outer: Circle  # "Cir": outer MZC circle
    inner: Circle  # "Ins": inner MZC circle
    mzc_minimum: float  # objective value at the optimum
    success: bool  # False = the optimiser failed (LabVIEW: error out set)

    @property
    def zone_width(self) -> float:
        return self.outer.radius - self.inner.radius


def minimum_zone_circles(points: PointsLike, *, max_iterations: int = 500, tolerance: float = 1e-10) -> Roundness:
    """Concentric outer/inner circles as ``RoundnessCalculations-NI d`` + ``MZC cost cno d`` compute them.

    Variables ``X = [xc, yc, R_out, R_in]``, started at the LSC centre with the "feasible radii"
    ``R_out = 1.01·max r_i`` and ``R_in = 0.99·min r_i`` (``r_i`` = distances to the LSC centre). Constraints
    (``MZC cost cno d``, "All datapoints inside outer circle / outside inner circle"): ``d_i ≤ R_out`` and
    ``d_i ≥ R_in``. Objective exactly as wired: ``Σ (d_i − R_out)² + Σ (d_i − R_in)²`` ("sum of each squared distance
    between inner and outer MZC circles to datapoints should be minimum") — note it is not the textbook minimum zone
    ``min(R_out − R_in)``.
    NI ``Constrained Nonlinear Optimization`` → :func:`scipy.optimize.minimize` (SLSQP, analytic gradients).
    On failure: ``success = False`` and NaN outer/inner circles (LabVIEW: the error case outputs zeros and an error,
    which the measure-mesa caller uses to fall back on Elzinga–Hearn/Delaunay, see :func:`contour_diameters`).
    """
    pts = as_points(points)
    lsc = fit_circle_least_squares(pts)
    if lsc.is_nan:
        return Roundness(NAN_CIRCLE, math.nan, NAN_CIRCLE, NAN_CIRCLE, math.nan, False)
    origin = np.array([lsc.x, lsc.y])
    local = pts - origin
    scale = max(lsc.radius, 1e-300)
    u = local / scale
    r = np.hypot(u[:, 0], u[:, 1])
    lsc_minimum = float(np.sum((r - 1.0) ** 2)) * scale**2
    x0 = np.array([0.0, 0.0, 1.01 * float(r.max()), 0.99 * float(r.min())])

    def dist(x: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        dx, dy = u[:, 0] - x[0], u[:, 1] - x[1]
        d = np.hypot(dx, dy)
        safe = np.where(d == 0.0, 1.0, d)
        return d, -dx / safe, -dy / safe  # d_i and its gradient w.r.t. xc, yc

    n_pts = float(len(u))

    def objective(x: np.ndarray) -> float:  # divided by N: O(1) values for the absolute ftol of SLSQP
        d, _, _ = dist(x)
        return float(np.sum((d - x[2]) ** 2) + np.sum((d - x[3]) ** 2)) / n_pts

    def objective_grad(x: np.ndarray) -> np.ndarray:
        d, gx, gy = dist(x)
        e = (d - x[2]) + (d - x[3])
        grad = [2 * np.sum(e * gx), 2 * np.sum(e * gy), -2 * np.sum(d - x[2]), -2 * np.sum(d - x[3])]
        return np.array(grad) / n_pts

    def constraints(x: np.ndarray) -> np.ndarray:
        d, _, _ = dist(x)
        return np.concatenate((x[2] - d, d - x[3]))  # >= 0

    def constraints_jac(x: np.ndarray) -> np.ndarray:
        d, gx, gy = dist(x)
        n = d.size
        jac = np.zeros((2 * n, 4))
        jac[:n, 0], jac[:n, 1], jac[:n, 2] = -gx, -gy, 1.0
        jac[n:, 0], jac[n:, 1], jac[n:, 3] = gx, gy, -1.0
        return jac

    res = optimize.minimize(
        objective,
        x0,
        jac=objective_grad,
        method="SLSQP",
        constraints=[{"type": "ineq", "fun": constraints, "jac": constraints_jac}],
        options={"maxiter": max_iterations, "ftol": tolerance},
    )
    x = res.x
    feasible = bool(np.all(constraints(x) >= -1e-9)) if np.all(np.isfinite(x)) else False
    # status 8 ("positive directional derivative for linesearch") = stalled at the optimum within precision
    if not ((res.success or res.status == 8) and feasible):
        return Roundness(lsc, lsc_minimum, NAN_CIRCLE, NAN_CIRCLE, math.nan, False)
    cx, cy = float(origin[0] + x[0] * scale), float(origin[1] + x[1] * scale)
    return Roundness(
        lsc=lsc,
        lsc_minimum=lsc_minimum,
        outer=Circle(cx, cy, float(x[2] * scale)),
        inner=Circle(cx, cy, float(x[3] * scale)),
        mzc_minimum=float(res.fun) * n_pts * scale**2,
        success=True,
    )


def circularity(circumscribed_diameter: float, inscribed_diameter: float) -> float:
    """``Circ/Insc Ratio`` = circumscribed / inscribed diameter (≥ 1; 1 for a perfect circle); NaN if either is NaN
    or the inscribed diameter is 0.

    Established from the recorded event ``vc_text/measure_mesa.txt`` (circumscribed 10.069082, inscribed 8.019226,
    circularity 1.255618 = their ratio). The current ``…measure_mesa_state_machine d40`` also shows a subtraction next
    to the division (possibly ``(Dc − Di)/Di``): to confirm with the owner.
    """
    if not (math.isfinite(circumscribed_diameter) and math.isfinite(inscribed_diameter)) or inscribed_diameter == 0:
        return math.nan
    return circumscribed_diameter / inscribed_diameter


def _zero_to_nan(value: float) -> float:
    return math.nan if value == 0.0 else value


@dataclass(frozen=True)
class ContourDiameters:
    """Contour part of ``alox_typedef_measure_mesa_diameters_report`` (same units as the points)."""

    fit: float  # 2·R of the least-squares circle
    surface: float  # surface-equivalent diameter
    area: float
    inscribed: float
    circumscribed: float
    circularity: float  # circumscribed / inscribed
    roundness: Roundness
    used_fallback: bool  # True when the MZC failed and Delaunay / Elzinga–Hearn were used


def contour_diameters(points: PointsLike, *, use_mzc: bool = True) -> ContourDiameters:
    """Diameters of one contour as the measure-mesa state machine assembles them.

    As read in ``alox_function_ll_measure_mesa_state_machine d40``: AREA → surface diameter; circumscribed and inscribed
    radii are taken from the MZC outer/inner circles (``RoundnessCalculations-NI``) and replaced by Elzinga–Hearn
    (:func:`circumscribed_circle`) / Delaunay (:func:`inscribed_circle`) when the MZC returns an error; each radius is
    doubled, then ``0 → NaN``. ``use_mzc=False`` always uses Elzinga–Hearn/Delaunay (the geometric definitions).
    LabVIEW also compares the inscribed diameter with NaN (``=``, always FALSE) to substitute ``1.1 ×`` another
    diameter: that dead fallback is not reproduced. The "fit" diameter here is the LSC; in LabVIEW the Fit field comes
    from ``IMAQ Fit Circle 2`` on the edge points (part 3, not in this module).
    """
    pts = as_points(points)
    surface, area = surface_equivalent_diameter(pts)
    roundness = minimum_zone_circles(pts) if use_mzc else Roundness(
        fit_circle_least_squares(pts), math.nan, NAN_CIRCLE, NAN_CIRCLE, math.nan, False)
    fallback = not roundness.success
    if fallback:
        circ = circumscribed_circle(pts).diameter
        insc = inscribed_circle(pts).diameter
    else:
        circ, insc = roundness.outer.diameter, roundness.inner.diameter
    circ, insc, surface = _zero_to_nan(circ), _zero_to_nan(insc), _zero_to_nan(surface)
    return ContourDiameters(
        fit=_zero_to_nan(roundness.lsc.diameter),
        surface=surface,
        area=area,
        inscribed=insc,
        circumscribed=circ,
        circularity=circularity(circ, insc),
        roundness=roundness,
        used_fallback=fallback,
    )

