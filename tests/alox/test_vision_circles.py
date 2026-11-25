"""Tests of alox.vision.circles: LSC, Elzinga–Hearn, Delaunay inscribed, MZC, equivalent diameters, NaN rules."""

from __future__ import annotations

import itertools
import math

import numpy as np
import pytest

from alox.vision.circles import (
    NAN_CIRCLE,
    Circle,
    circle_from_3_points,
    circularity,
    circumscribed_circle,
    contour_diameters,
    fit_circle_least_squares,
    inscribed_circle,
    minimum_zone_circles,
    perimeter_equivalent_diameter,
    polygon_area,
    polygon_perimeter,
    surface_equivalent_diameter,
)


def circle_points(cx: float, cy: float, r: float, n: int = 180, phase: float = 0.1) -> np.ndarray:
    t = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False) + phase
    return np.column_stack((cx + r * np.cos(t), cy + r * np.sin(t)))


def ellipse_points(a: float, b: float, n: int = 360) -> np.ndarray:
    t = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    return np.column_stack((a * np.cos(t), b * np.sin(t)))


def assert_circle(c: Circle, x: float, y: float, r: float, tol: float = 1e-9) -> None:
    assert (c.x, c.y, c.radius) == pytest.approx((x, y, r), abs=tol)


def brute_force_mec(pts: np.ndarray) -> float:
    """Smallest radius among the pair/triple circles enclosing every point (reference for Elzinga–Hearn)."""
    best = math.inf
    cands = [((p[0] + q[0]) / 2, (p[1] + q[1]) / 2) for p, q in itertools.combinations(pts, 2)]
    for p, q, s in itertools.combinations(pts, 3):
        c = circle_from_3_points(p, q, s)
        if not c.is_nan:
            cands.append(c.center)
    for cx, cy in cands:
        r = float(np.max(np.hypot(pts[:, 0] - cx, pts[:, 1] - cy)))
        best = min(best, r)
    return best


# ---------------------------------------------------------------------------------------------------------------------
# Polygon measures and equivalent diameters
# ---------------------------------------------------------------------------------------------------------------------


def test_square_area_perimeter_and_equivalent_diameters():
    square = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)]
    assert polygon_area(square) == pytest.approx(16.0)
    assert polygon_area(square[::-1]) == pytest.approx(16.0)  # orientation does not matter
    d, a = surface_equivalent_diameter(square)
    assert a == pytest.approx(16.0)
    assert d == pytest.approx(2.0 * math.sqrt(16.0 / math.pi))
    assert polygon_perimeter(square) == pytest.approx(16.0)  # closed: the wrap-around side is counted
    assert perimeter_equivalent_diameter(square) == pytest.approx(16.0 / math.pi)


def test_equivalent_diameters_of_a_dense_circle_converge_to_2r():
    pts = circle_points(100.0, 200.0, 25.0, n=2000)
    assert surface_equivalent_diameter(pts)[0] == pytest.approx(50.0, rel=1e-5)
    assert perimeter_equivalent_diameter(pts) == pytest.approx(50.0, rel=1e-5)


def test_polygon_measures_degenerate():
    assert polygon_area([(0, 0), (1, 1)]) == 0.0
    assert polygon_perimeter([(1, 1)]) == 0.0
    assert surface_equivalent_diameter([]) == (0.0, 0.0)


# ---------------------------------------------------------------------------------------------------------------------
# Elementary circles
# ---------------------------------------------------------------------------------------------------------------------


def test_circle_from_3_points():
    assert_circle(circle_from_3_points((0, 0), (2, 0), (0, 2)), 1.0, 1.0, math.sqrt(2.0))
    assert_circle(circle_from_3_points((1e6, 1e6), (1e6 + 2, 1e6), (1e6, 1e6 + 2)), 1e6 + 1, 1e6 + 1, math.sqrt(2))


@pytest.mark.parametrize("pts", [((0, 0), (1, 1), (2, 2)), ((0, 0), (0, 0), (1, 0))])
def test_circle_from_3_points_degenerate_is_nan(pts):
    assert circle_from_3_points(*pts).is_nan  # LabVIEW has no D = 0 guard


def test_least_squares_circle_exact_and_noisy():
    assert_circle(fit_circle_least_squares(circle_points(5.0, -2.0, 3.0)), 5.0, -2.0, 3.0)
    rng = np.random.default_rng(11)
    pts = circle_points(1224.0, 1024.0, 40.0, n=300) + rng.normal(0.0, 0.2, (300, 2))
    assert_circle(fit_circle_least_squares(pts), 1224.0, 1024.0, 40.0, tol=0.05)
    arc = circle_points(0.0, 0.0, 10.0, n=50)[:12]  # short arc
    assert_circle(fit_circle_least_squares(arc), 0.0, 0.0, 10.0, tol=1e-6)


@pytest.mark.parametrize("pts", [[], [(0, 0), (1, 1)], [(0, 0), (1, 1), (2, 2), (3, 3)], [(1, 1)] * 5])
def test_least_squares_circle_degenerate_is_nan(pts):
    assert fit_circle_least_squares(pts).is_nan


# ---------------------------------------------------------------------------------------------------------------------
# Circumscribed circle (Elzinga–Hearn)
# ---------------------------------------------------------------------------------------------------------------------


def test_circumscribed_exact_circle():
    assert_circle(circumscribed_circle(circle_points(5.0, -2.0, 3.0)), 5.0, -2.0, 3.0)


def test_circumscribed_ellipse_is_the_major_axis_circle():
    assert_circle(circumscribed_circle(ellipse_points(4.0, 2.0)), 0.0, 0.0, 4.0)


@pytest.mark.parametrize("seed", range(6))
def test_circumscribed_matches_brute_force(seed):
    rng = np.random.default_rng(seed)
    pts = rng.normal(0.0, 10.0, (14, 2))
    c = circumscribed_circle(pts)
    assert c.radius == pytest.approx(brute_force_mec(pts), rel=1e-9)
    assert np.all(c.radial_distances(pts) <= c.radius + 1e-9)


def test_circumscribed_degenerate_inputs():
    assert circumscribed_circle([]).is_nan
    assert_circle(circumscribed_circle([(3.0, 4.0)]), 3.0, 4.0, 0.0)
    assert_circle(circumscribed_circle([(0.0, 0.0), (4.0, 0.0)]), 2.0, 0.0, 2.0)
    assert_circle(circumscribed_circle([(0.0, 0.0), (1.0, 0.0), (3.0, 0.0), (2.0, 0.0)]), 1.5, 0.0, 1.5)
    assert_circle(circumscribed_circle([(1.0, 1.0)] * 3 + [(3.0, 1.0)]), 2.0, 1.0, 1.0)


def test_circumscribed_nan_after_max_iterations():
    pts = np.random.default_rng(0).normal(size=(50, 2))
    assert circumscribed_circle(pts, max_iterations=1).is_nan
    assert not circumscribed_circle(pts).is_nan


# ---------------------------------------------------------------------------------------------------------------------
# Inscribed circle (Delaunay)
# ---------------------------------------------------------------------------------------------------------------------


def test_inscribed_exact_circle():
    assert_circle(inscribed_circle(circle_points(5.0, -2.0, 3.0)), 5.0, -2.0, 3.0, tol=1e-6)


def test_inscribed_ellipse_close_to_minor_axis():
    c = inscribed_circle(ellipse_points(4.0, 2.0, n=720))
    assert c.radius == pytest.approx(2.0, abs=0.01)
    assert abs(c.y) < 1e-6 and abs(c.x) < 0.1


def test_inscribed_is_the_largest_delaunay_circle_not_the_distance_maximum():
    # square corners: the only Delaunay circumcircle is the square's circumcircle (LabVIEW definition)
    c = inscribed_circle([(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)])
    assert_circle(c, 1.0, 1.0, math.sqrt(2.0))


def test_inscribed_center_outside_hull_rule():
    obtuse = [(0.0, 0.0), (10.0, 0.0), (5.0, 1.0)]  # circumcentre below the triangle
    assert inscribed_circle(obtuse).is_nan
    c = inscribed_circle(obtuse, allow_center_outside_hull=True)
    assert c.y < 0.0
    assert c.radius == pytest.approx(circle_from_3_points(*obtuse).radius)


@pytest.mark.parametrize("pts", [[], [(0, 0), (1, 0)], [(0, 0), (1, 1), (2, 2), (3, 3)]])
def test_inscribed_degenerate_is_nan(pts):
    assert inscribed_circle(pts).is_nan


# ---------------------------------------------------------------------------------------------------------------------
# Minimum-zone circles (RoundnessCalculations-NI)
# ---------------------------------------------------------------------------------------------------------------------


def test_mzc_exact_circle_collapses_to_the_circle():
    r = minimum_zone_circles(circle_points(10.0, 20.0, 5.0))
    assert r.success
    assert_circle(r.outer, 10.0, 20.0, 5.0, tol=1e-6)
    assert_circle(r.inner, 10.0, 20.0, 5.0, tol=1e-6)
    assert r.zone_width == pytest.approx(0.0, abs=1e-6)
    assert_circle(r.lsc, 10.0, 20.0, 5.0)


def test_mzc_ellipse_outer_and_inner_radii():
    r = minimum_zone_circles(ellipse_points(4.0, 2.0))
    assert r.success
    assert r.outer.radius == pytest.approx(4.0, abs=1e-4)
    assert r.inner.radius == pytest.approx(2.0, abs=1e-4)
    assert r.outer.center == r.inner.center
    assert r.mzc_minimum > 0.0


def test_mzc_constraints_hold_on_noisy_contour():
    rng = np.random.default_rng(5)
    pts = circle_points(0.0, 0.0, 30.0, n=200) + rng.normal(0.0, 0.3, (200, 2))
    r = minimum_zone_circles(pts)
    assert r.success
    d = r.outer.radial_distances(pts)
    assert np.all(d <= r.outer.radius + 1e-6)
    assert np.all(d >= r.inner.radius - 1e-6)


def test_mzc_degenerate_input():
    r = minimum_zone_circles([(0, 0), (1, 1)])
    assert not r.success
    assert r.outer.is_nan and r.inner.is_nan and r.lsc.is_nan


# ---------------------------------------------------------------------------------------------------------------------
# Circularity and the measure-mesa composition
# ---------------------------------------------------------------------------------------------------------------------


def test_circularity_matches_recorded_labview_event():
    # vc_text/measure_mesa.txt: circumscribed 10.069082, incribed 8.019226, circularity 1.255618
    assert circularity(10.069082, 8.019226) == pytest.approx(1.255618, abs=5e-7)


@pytest.mark.parametrize(("c", "i"), [(math.nan, 1.0), (1.0, math.nan), (1.0, 0.0)])
def test_circularity_nan_rules(c, i):
    assert math.isnan(circularity(c, i))


def test_contour_diameters_circle():
    res = contour_diameters(circle_points(1224.0, 1024.0, 30.0, n=720))
    assert not res.used_fallback
    assert res.fit == pytest.approx(60.0)
    assert res.inscribed == pytest.approx(60.0, abs=1e-6)
    assert res.circumscribed == pytest.approx(60.0, abs=1e-6)
    assert res.surface == pytest.approx(60.0, rel=1e-4)
    assert res.circularity == pytest.approx(1.0, abs=1e-6)


def test_contour_diameters_ellipse_with_and_without_mzc():
    pts = ellipse_points(4.0, 2.0, n=720)
    mzc = contour_diameters(pts)
    geo = contour_diameters(pts, use_mzc=False)
    assert geo.used_fallback
    assert mzc.circumscribed == pytest.approx(8.0, abs=1e-3)
    assert geo.circumscribed == pytest.approx(8.0, abs=1e-9)
    assert mzc.inscribed == pytest.approx(4.0, abs=1e-3)
    assert geo.inscribed == pytest.approx(4.0, abs=0.02)
    assert mzc.circularity == pytest.approx(2.0, abs=1e-3)
    assert mzc.surface == pytest.approx(2.0 * math.sqrt(8.0), rel=1e-4)  # area π·a·b


def test_contour_diameters_degenerate_contour_gives_nan():
    res = contour_diameters([(0.0, 0.0), (1.0, 0.0), (2.0, 0.0)])
    assert res.used_fallback
    assert math.isnan(res.surface)  # zero area -> 0 -> NaN
    assert math.isnan(res.inscribed)
    assert math.isnan(res.circularity)
    assert res.circumscribed == pytest.approx(2.0)


def test_nan_circle_constant():
    assert NAN_CIRCLE.is_nan
    assert math.isnan(NAN_CIRCLE.diameter)
