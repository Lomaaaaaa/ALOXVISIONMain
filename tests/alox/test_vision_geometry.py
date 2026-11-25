"""Tests of alox.vision.geometry: normal-form lines, TLS fit, the fixed RANSAC, distances and angles."""

from __future__ import annotations

import math

import numpy as np
import pytest

from alox.vision.geometry import (
    GeometryError,
    Line,
    distance,
    distance_score,
    fit_line_ransac,
    fit_line_tls,
    fold_angle_180,
    line_angle_deg,
    lines_angle_deg,
    point_line_distances,
    segment_angle_deg,
)


def same_line(l1: Line, l2: Line, tol: float = 1e-9) -> bool:
    a, b = np.array(l1.canonical().as_tuple()), np.array(l2.canonical().as_tuple())
    return bool(np.allclose(a, b, atol=tol))


# ---------------------------------------------------------------------------------------------------------------------
# Line from two points (fixed: every orientation)
# ---------------------------------------------------------------------------------------------------------------------


def test_line_from_points_uses_labview_coefficients_normalised():
    line = Line.from_points((1.0, 2.0), (4.0, 6.0))
    # a = y1 - y2 = -4, b = x2 - x1 = 3, c = x1*y2 - x2*y1 = 6 - 8 = -2, norm 5
    assert line.as_tuple() == pytest.approx((-0.8, 0.6, -0.4))
    assert math.hypot(line.a, line.b) == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("p1", "p2", "expected"),
    [
        ((3.0, 0.0), (3.0, 5.0), Line(1.0, 0.0, -3.0)),  # vertical: rejected by LabVIEW ("Same Points")
        ((0.0, 2.0), (7.0, 2.0), Line(0.0, 1.0, -2.0)),  # horizontal: rejected by LabVIEW
        ((0.0, 0.0), (1.0, 1.0), Line(-math.sqrt(0.5), math.sqrt(0.5), 0.0)),
    ],
)
def test_line_from_points_any_orientation(p1, p2, expected):
    line = Line.from_points(p1, p2)
    assert same_line(line, expected)
    assert line.distances([p1, p2]) == pytest.approx([0.0, 0.0], abs=1e-12)


def test_line_from_points_direction_goes_from_p1_to_p2():
    line = Line.from_points((0.0, 0.0), (0.0, -2.0))
    assert line.direction == pytest.approx((0.0, -1.0))


def test_line_from_coincident_points_raises():
    with pytest.raises(GeometryError):
        Line.from_points((1.0, 1.0), (1.0, 1.0))


def test_line_slope_intercept_and_vertical():
    line = Line.from_points((0.0, 1.0), (2.0, 5.0))
    assert line.slope == pytest.approx(2.0)
    assert line.intercept == pytest.approx(1.0)
    assert line.y_at(3.0) == pytest.approx(7.0)
    assert line.x_at(7.0) == pytest.approx(3.0)
    vertical = Line.from_points((3.0, 0.0), (3.0, 1.0))
    assert vertical.is_vertical
    assert math.isinf(vertical.slope)
    assert math.isnan(vertical.intercept)
    assert math.isnan(vertical.y_at(1.0))


def test_project_points_on_line():
    line = Line.from_points((0.0, 0.0), (1.0, 0.0))
    assert line.project([(2.0, 3.0)]) == pytest.approx(np.array([[2.0, 0.0]]))


# ---------------------------------------------------------------------------------------------------------------------
# Distances
# ---------------------------------------------------------------------------------------------------------------------


def test_point_line_distances_accepts_unnormalised_tuple():
    # 2x + 0y - 6 = 0 is x = 3
    assert point_line_distances([(0.0, 0.0), (5.0, 9.0)], (2.0, 0.0, -6.0)) == pytest.approx([3.0, 2.0])
    with pytest.raises(GeometryError):
        point_line_distances([(0.0, 0.0)], (0.0, 0.0, 1.0))


def test_distance_score_inclusive_threshold():
    line = Line.from_points((0.0, 0.0), (1.0, 0.0))
    res = distance_score([(0.0, 0.0), (1.0, 0.5), (2.0, 1.0), (3.0, -2.0)], line, max_distance=0.5)
    assert res.sum_distance == pytest.approx(3.5)
    assert res.inliers.tolist() == [True, True, False, False]
    assert res.score == pytest.approx(50.0)
    assert math.isnan(distance_score(np.empty((0, 2)), line, 1.0).score)


def test_distance():
    assert distance((0, 0), (3, 4)) == pytest.approx(5.0)


# ---------------------------------------------------------------------------------------------------------------------
# Total least squares
# ---------------------------------------------------------------------------------------------------------------------


def test_tls_vertical_and_horizontal_exact():
    ys = np.linspace(-5.0, 5.0, 11)
    assert same_line(fit_line_tls(np.column_stack((np.full(11, 3.0), ys))), Line(1.0, 0.0, -3.0))
    assert same_line(fit_line_tls(np.column_stack((ys, np.full(11, -2.0)))), Line(0.0, 1.0, 2.0))


def test_tls_noisy_oblique_line():
    rng = np.random.default_rng(42)
    x = np.linspace(0.0, 10.0, 200)
    y = 2.0 * x + 1.0 + rng.normal(0.0, 0.05, x.size)
    line = fit_line_tls(np.column_stack((x, y)))
    assert line.slope == pytest.approx(2.0, abs=0.01)
    assert line.intercept == pytest.approx(1.0, abs=0.05)


def test_tls_is_orthogonal_not_vertical_regression():
    # symmetric cross: TLS picks the principal axis; for points on y = -x it must be exact
    pts = np.array([(-2.0, 2.0), (-1.0, 1.0), (1.0, -1.0), (2.0, -2.0)])
    assert line_angle_deg(fit_line_tls(pts)) == pytest.approx(-45.0)


@pytest.mark.parametrize("pts", [[(1.0, 1.0)], [(1.0, 1.0), (1.0, 1.0)], [(0.0, 0.0), (math.nan, 1.0)]])
def test_tls_degenerate_inputs(pts):
    with pytest.raises(GeometryError):
        fit_line_tls(pts)


def test_as_points_rejects_bad_shape():
    with pytest.raises(GeometryError):
        fit_line_tls([1.0, 2.0, 3.0])


# ---------------------------------------------------------------------------------------------------------------------
# RANSAC (fixed)
# ---------------------------------------------------------------------------------------------------------------------


def outliers_on(pts: np.ndarray, every: int, offset: tuple[float, float]) -> np.ndarray:
    out = pts.copy()
    out[::every] += offset
    return out


def test_ransac_vertical_line_with_outliers():
    ys = np.arange(0.0, 50.0)  # integer pixel coordinates: the LabVIEW fit could never return x = 3
    pts = outliers_on(np.column_stack((np.full(ys.size, 3.0), ys)), 7, (5.0, 0.0))
    fit = fit_line_ransac(pts, max_distance=0.1, iterations=200, seed=1)
    assert same_line(fit.line, Line(1.0, 0.0, -3.0))
    expected_inliers = np.ones(ys.size, dtype=bool)
    expected_inliers[::7] = False
    assert fit.inliers.tolist() == expected_inliers.tolist()
    assert fit.score == pytest.approx(100.0 * expected_inliers.sum() / ys.size)
    assert fit.valid
    assert fit.residual == pytest.approx(5.0 * (~expected_inliers).sum())


def test_ransac_horizontal_line_with_outliers():
    xs = np.arange(0.0, 40.0)
    pts = outliers_on(np.column_stack((xs, np.full(xs.size, 2.0))), 5, (0.0, -3.0))
    fit = fit_line_ransac(pts, max_distance=0.5, iterations=100, seed=2)
    assert same_line(fit.line, Line(0.0, 1.0, -2.0))
    assert fit.n_inliers == 32


def test_ransac_noisy_oblique_line_reproducible_with_seed():
    rng = np.random.default_rng(7)
    x = rng.uniform(0.0, 100.0, 300)
    y = -0.5 * x + 20.0 + rng.normal(0.0, 0.1, x.size)
    y[:90] = rng.uniform(-60.0, 40.0, 90)  # 30 % gross outliers
    pts = np.column_stack((x, y))
    a = fit_line_ransac(pts, max_distance=0.3, iterations=500, seed=123)
    b = fit_line_ransac(pts, max_distance=0.3, iterations=500, seed=123)
    assert a.line == b.line
    assert a.inliers.tolist() == b.inliers.tolist()
    assert a.line.slope == pytest.approx(-0.5, abs=0.005)
    assert a.line.intercept == pytest.approx(20.0, abs=0.1)
    assert a.score >= 69.0
    gen = np.random.default_rng(123)
    assert fit_line_ransac(pts, max_distance=0.3, iterations=500, seed=gen).line == a.line


def test_ransac_refine_none_returns_a_candidate_through_two_points():
    pts = np.array([(0.0, 0.0), (1.0, 1.1), (2.0, 1.9), (3.0, 3.0)])
    fit = fit_line_ransac(pts, max_distance=0.2, iterations=50, seed=0, refine="none")
    assert np.count_nonzero(fit.line.distances(pts) < 1e-9) >= 2
    refined = fit_line_ransac(pts, max_distance=0.2, iterations=50, seed=0)
    assert np.sum(refined.line.distances(pts) ** 2) <= np.sum(fit.line.distances(pts) ** 2)


def test_ransac_two_points_and_large_offsets():
    fit = fit_line_ransac([(5.0, 5.0), (5.0, 9.0)], max_distance=0.0)
    assert same_line(fit.line, Line(1.0, 0.0, -5.0))
    assert fit.valid and fit.score == 100.0
    t0 = 3.6e9  # LabVIEW timestamps
    t = t0 + np.arange(0.0, 600.0, 10.0)
    d = 12.0 - 0.002 * (t - t0)
    fit = fit_line_ransac(np.column_stack((t, d)), max_distance=0.01, iterations=100)
    assert fit.line.slope == pytest.approx(-0.002, rel=1e-6)
    assert fit.line.y_at(t0 + 300.0) == pytest.approx(11.4, abs=1e-6)


def test_ransac_valid_flag_uses_min_score_and_two_inliers():
    pts = np.array([(0.0, 0.0), (1.0, 0.0), (0.0, 5.0), (3.0, 9.0), (7.0, -4.0)])
    fit = fit_line_ransac(pts, max_distance=0.01, iterations=200, min_score=50.0, seed=0)
    assert fit.n_inliers == 2
    assert fit.score == pytest.approx(40.0)
    assert not fit.valid
    assert fit_line_ransac(pts, max_distance=0.01, iterations=200, min_score=40.0, seed=0).valid


def test_ransac_skips_coincident_pairs_and_never_indexes_out_of_range():
    pts = np.array([(1.0, 1.0)] * 5 + [(2.0, 2.0)])
    fit = fit_line_ransac(pts, max_distance=1e-9, iterations=1000, seed=0)
    assert fit.n_inliers == 6
    assert 0 < fit.iterations <= 1000


@pytest.mark.parametrize(
    ("pts", "kwargs", "exc"),
    [
        ([(0.0, 0.0)], {"max_distance": 1.0}, GeometryError),
        ([(1.0, 1.0), (1.0, 1.0), (1.0, 1.0)], {"max_distance": 1.0}, GeometryError),
        ([(0.0, 0.0), (1.0, 1.0)], {"max_distance": -1.0}, ValueError),
        ([(0.0, 0.0), (1.0, 1.0)], {"max_distance": 1.0, "iterations": 0}, ValueError),
        ([(0.0, 0.0), (math.inf, 1.0), (2.0, 2.0)], {"max_distance": 1.0}, GeometryError),
    ],
)
def test_ransac_degenerate_inputs(pts, kwargs, exc):
    with pytest.raises(exc):
        fit_line_ransac(pts, **kwargs)


# ---------------------------------------------------------------------------------------------------------------------
# Angles
# ---------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("angle", "folded"),
    [(0.0, 0.0), (45.0, 45.0), (90.0, 90.0), (135.0, -45.0), (180.0, 0.0), (270.0, 90.0), (-45.0, -45.0),
     (-90.0, 90.0), (359.0, -1.0), (-181.0, -1.0)],
)
def test_fold_angle_180(angle, folded):
    assert fold_angle_180(angle) == pytest.approx(folded)


def test_fold_angle_nan():
    assert math.isnan(fold_angle_180(math.nan))


def test_segment_and_line_angles():
    assert segment_angle_deg((0, 0), (1, 1)) == pytest.approx(45.0)
    assert segment_angle_deg((0, 0), (-1, 0)) == pytest.approx(180.0)
    assert line_angle_deg(Line.from_points((0, 0), (0, 1))) == pytest.approx(90.0)
    assert line_angle_deg(Line.from_points((0, 0), (0, -1))) == pytest.approx(90.0)
    assert line_angle_deg(Line.from_points((1, 1), (0, 0))) == pytest.approx(45.0)
    h = Line.from_points((0, 0), (1, 0))
    v = Line.from_points((0, 0), (0, 1))
    assert lines_angle_deg(h, v) == pytest.approx(90.0)
    assert lines_angle_deg(h, Line.from_points((0, 0), (1, -1))) == pytest.approx(45.0)
