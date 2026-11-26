"""Straight-edge tilt and object masking (``alox.vision.tilt``) on synthetic images with a known edge angle; the
sub-pixel edge line, the mesa lattice and the long-baseline helpers of the Python-only tilt methods."""

from __future__ import annotations

import math

import numpy as np
import pytest

from alox.vision.edges import EdgeOptions, EdgePolarity
from alox.vision.tilt import (
    EdgeMethod,
    LineOptions,
    RakeDirection,
    baseline_target,
    detect_mesas,
    find_straight_edges,
    fit_baseline,
    fit_edge_line,
    fit_lattice,
    fold_angle,
    mask_matched_object,
    measure_tilt_straight_edge,
    next_baseline_step,
    noise_sigma,
    pick_row_mesa,
    rake_lines,
)

W, H = 240, 180


def edge_image(angle_deg: float, *, w: int = W, h: int = H, vertical: bool = False, noise: float = 2.0,
               seed: int = 3, columns: slice | None = None) -> np.ndarray:
    """A smooth step edge through the image centre at ``angle_deg`` counter-clockwise as displayed (dark → bright
    across it); ``vertical`` adds 90°. ``columns``: the edge exists only there (flat elsewhere)."""
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float64)
    a = math.radians(angle_deg + (90.0 if vertical else 0.0))
    # line direction (cos a, −sin a) in image coordinates; signed distance along its normal (sin a, cos a)
    d = (xs - (w - 1) / 2.0) * math.sin(a) + (ys - (h - 1) / 2.0) * math.cos(a)
    img = 60.0 + 120.0 / (1.0 + np.exp(-d / 0.8))
    if columns is not None:
        flat = np.full_like(img, 60.0)
        flat[:, columns] = img[:, columns]
        img = flat
    img += np.random.default_rng(seed).normal(0.0, noise, img.shape)
    return np.clip(img, 0, 255).astype(np.uint8)


EDGE = EdgeOptions(polarity=EdgePolarity.ALL, kernel_size=3, width=5, min_strength=20.0)


def test_rake_lines_are_centred_and_follow_the_direction():
    lines = rake_lines(100, 50, RakeDirection.TOP_TO_BOTTOM, 30)
    assert [a for a, _ in lines] == [(4.5, 0.0), (34.5, 0.0), (64.5, 0.0), (94.5, 0.0)]       # 99 px, 4 lines
    assert all(b[1] == 49.0 for _, b in lines)
    up = rake_lines(100, 50, RakeDirection.BOTTOM_TO_TOP, 30)
    assert up[0] == ((4.5, 49.0), (4.5, 0.0))
    left = rake_lines(100, 50, RakeDirection.RIGHT_TO_LEFT, 10)
    assert len(left) == 5 and left[0] == ((99.0, 4.5), (0.0, 4.5))
    assert rake_lines(100, 50, RakeDirection.LEFT_TO_RIGHT, 1000) == [((0.0, 24.5), (99.0, 24.5))]


@pytest.mark.parametrize("method", list(EdgeMethod))
@pytest.mark.parametrize("angle", [-4.0, 0.0, 2.5])
def test_horizontal_edge_angle_every_method(method, angle):
    img = edge_image(angle)
    report = measure_tilt_straight_edge(img, EDGE, RakeDirection.TOP_TO_BOTTOM,
                                        LineOptions(method=method, step_px=15, angle_range_deg=10.0))
    assert report.valid and report.tilt_found and report.edges_ok and report.tolerance_ok
    assert report.angle_deg == pytest.approx(angle, abs=0.15)
    assert report.message == f"TILT ANGLE: {report.angle_deg:.1f}°"
    assert report.found_edges_percent >= 90.0
    assert report.point1[0] < report.point2[0]             # Point1 → Point2 follows the search-line order


def test_angle_is_counter_clockwise_as_displayed():
    img = edge_image(3.0)
    rep = measure_tilt_straight_edge(img, EDGE, RakeDirection.TOP_TO_BOTTOM, LineOptions(step_px=10))
    assert rep.point2[1] < rep.point1[1]                    # the right end is higher on screen: positive angle
    assert rep.angle_deg > 0


@pytest.mark.parametrize("method", [EdgeMethod.BEST_EDGE_RAKE, EdgeMethod.BEST_EDGE_PROJECTION])
def test_vertical_edge_with_a_left_to_right_rake(method):
    img = edge_image(2.0, vertical=True)
    rep = measure_tilt_straight_edge(img, EDGE, RakeDirection.LEFT_TO_RIGHT,
                                     LineOptions(method=method, step_px=10, orientation_deg=90.0,
                                                 angle_range_deg=10.0))
    assert rep.valid
    assert rep.angle_deg == pytest.approx(-88.0, abs=0.2)  # Point1 at the top, Point2 at the bottom
    # with the HL default (Angle Expected 0 ± 10) a vertical edge is out of range
    out = measure_tilt_straight_edge(img, EDGE, RakeDirection.LEFT_TO_RIGHT,
                                     LineOptions(method=method, step_px=10))
    assert out.tilt_found and out.edges_ok and not out.tolerance_ok and not out.valid
    assert out.message == "TILT NOT FOUND/INVALID"


def test_no_edge_is_not_found():
    flat = np.full((H, W), 100, np.uint8)
    rep = measure_tilt_straight_edge(flat, EDGE, RakeDirection.TOP_TO_BOTTOM, LineOptions(step_px=10))
    assert not rep.tilt_found and not rep.valid and math.isnan(rep.angle_deg)
    assert rep.found_edges_percent == 0.0 and rep.message == "TILT NOT FOUND/INVALID"
    assert rep.search.straight_edges == () and len(rep.search.search_lines) == 24
    proj = measure_tilt_straight_edge(flat, EDGE, RakeDirection.TOP_TO_BOTTOM,
                                      LineOptions(method=EdgeMethod.FIRST_EDGE_PROJECTION, step_px=10))
    assert not proj.tilt_found


def test_min_points_check():
    img = edge_image(1.0, columns=slice(0, 40))             # the edge crosses 1/6 of the image only
    rep = measure_tilt_straight_edge(img, EDGE, RakeDirection.TOP_TO_BOTTOM,
                                     LineOptions(step_px=10, min_points_percent=25.0))
    assert rep.tilt_found and not rep.edges_ok and not rep.valid
    assert rep.found_edges_percent == pytest.approx(100.0 * 4 / 24)
    assert measure_tilt_straight_edge(img, EDGE, RakeDirection.TOP_TO_BOTTOM,
                                      LineOptions(step_px=10, min_points_percent=10.0)).valid


def test_outliers_are_not_used_edges():
    img = edge_image(0.0)
    img[20:24, 100:140] = 250                               # a bright scratch above the edge
    rep = measure_tilt_straight_edge(img, EDGE, RakeDirection.TOP_TO_BOTTOM,
                                     LineOptions(method=EdgeMethod.FIRST_EDGE_RAKE, step_px=10))
    assert rep.valid and rep.angle_deg == pytest.approx(0.0, abs=0.15)
    used_y = [y for _, y in rep.search.straight_edges[0].used_edges]
    assert all(abs(y - (H - 1) / 2.0) < 3.0 for y in used_y)
    assert len(rep.search.edges) > len(used_y)              # the scratch edges were found but not used


def test_polarity_selects_the_edges():
    img = edge_image(0.0)                                   # dark above, bright below: rising from the top
    rising = EdgeOptions(polarity=EdgePolarity.RISING, kernel_size=3, width=5, min_strength=20.0)
    falling = EdgeOptions(polarity=EdgePolarity.FALLING, kernel_size=3, width=5, min_strength=20.0)
    assert measure_tilt_straight_edge(img, rising, RakeDirection.TOP_TO_BOTTOM, LineOptions(step_px=10)).valid
    assert not measure_tilt_straight_edge(img, falling, RakeDirection.TOP_TO_BOTTOM,
                                          LineOptions(step_px=10)).tilt_found
    assert measure_tilt_straight_edge(img, falling, RakeDirection.BOTTOM_TO_TOP, LineOptions(step_px=10)).valid


def test_two_lines_requested():
    img = edge_image(0.0).astype(np.float64)
    img[150:, :] = 250.0                                    # a second (brighter) horizontal edge lower down
    search = find_straight_edges(img.astype(np.uint8), EDGE, RakeDirection.TOP_TO_BOTTOM,
                                 LineOptions(method=EdgeMethod.HOUGH_EDGE_RAKE, step_px=10, number_of_lines=2))
    assert len(search.straight_edges) == 2
    ys = sorted(round((e.point1[1] + e.point2[1]) / 2.0) for e in search.straight_edges)
    assert ys[0] == pytest.approx(89.5, abs=1.0) and ys[1] == pytest.approx(149.5, abs=1.0)


def test_process_time_uses_the_clock():
    ticks = iter([1.0, 1.25])
    rep = measure_tilt_straight_edge(edge_image(0.0), EDGE, RakeDirection.TOP_TO_BOTTOM, LineOptions(step_px=20),
                                     clock=lambda: next(ticks))
    assert rep.process_time_ms == pytest.approx(250.0)


def test_line_options_are_checked():
    with pytest.raises(ValueError):
        LineOptions(step_px=0)
    with pytest.raises(ValueError):
        LineOptions(angle_tolerance_deg=0.0)
    assert LineOptions(method=3).method is EdgeMethod.FIRST_EDGE_PROJECTION


def test_mask_matched_object():
    img = np.full((20, 30), 200, np.uint8)
    box = ((5.2, 4.0), (14.0, 4.0), (14.0, 9.8), (5.0, 10.0))
    removed = mask_matched_object(img, [box])
    assert removed[4:11, 5:15].max() == 0 and removed[0:4].min() == 200 and removed[:, 16:].min() == 200
    assert removed[3, 5] == 200 and removed[11, 5] == 200
    kept = mask_matched_object(img, [box], keep_inside=True)
    assert kept[4:11, 5:15].min() == 200 and kept[0:4].max() == 0 and kept[:, 16:].max() == 0
    assert np.array_equal(mask_matched_object(img, []), img)
    assert mask_matched_object(img, []) is not img
    rotated = ((10.0, 2.0), (18.0, 10.0), (10.0, 18.0), (2.0, 10.0))     # a diamond, the other winding too
    for poly in (rotated, rotated[::-1]):
        out = mask_matched_object(img, [poly])
        assert out[10, 10] == 0 and out[2, 2] == 200 and out[10, 2] == 0 and out[10, 19] == 200


# ============================================================================================== precise methods
def grid_image(angle_deg: float, *, pitch: tuple[float, float] = (25.0, 25.0), radius: float = 8.0, w: int = 320,
               h: int = 240, noise: float = 4.0, seed: int = 1, offset: tuple[float, float] = (3.3, 7.1),
               dark: bool = False) -> np.ndarray:
    """Discs of ``radius`` px on a grid whose rows are at ``angle_deg`` as displayed, ``pitch`` (along, across) px,
    a node at the image centre + ``offset``; anti-aliased, with noise."""
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float64)
    t = math.radians(angle_deg)
    px, py = xs - w / 2.0 - offset[0], ys - h / 2.0 - offset[1]
    u = (px * math.cos(t) - py * math.sin(t)) / pitch[0]
    v = (px * math.sin(t) + py * math.cos(t)) / pitch[1]
    d = np.hypot((u - np.rint(u)) * pitch[0], (v - np.rint(v)) * pitch[1])
    img = 50.0 + 150.0 / (1.0 + np.exp((d - radius) / 0.7))
    if dark:
        img = 250.0 - img
    img += np.random.default_rng(seed).normal(0.0, noise, img.shape)
    return np.clip(img, 0, 255).astype(np.uint8)


def test_fold_angle():
    assert fold_angle(91.0) == 91.0 - 180.0 and fold_angle(90.0) == 90.0 and fold_angle(-90.0) == 90.0
    assert fold_angle(181.5) == pytest.approx(1.5) and fold_angle(46.0, 90.0) == -44.0 and fold_angle(45.0, 90.0) == 45.0
    assert fold_angle(179.0, 180.0, 90.0) == 179.0 and fold_angle(-1.0, 180.0, 90.0) == 179.0
    assert math.isnan(fold_angle(math.nan))


@pytest.mark.parametrize(("angle", "vertical"), [(0.137, False), (-3.0, False), (12.0, False), (-30.0, False),
                                                 (2.0, True), (-0.5, True)])
def test_fit_edge_line_subpixel(angle, vertical):
    img = edge_image(angle, vertical=vertical)
    fit = fit_edge_line(img)
    assert fit is not None and fit.horizontal is not vertical
    true = angle + (90.0 if vertical else 0.0)
    error = fold_angle(fit.angle_deg - true)
    assert abs(error) < 0.01 and abs(error) < 5 * fit.angle_sigma_deg + 0.002
    assert fit.rms_px < 0.1 and fit.n_points >= 0.9 * fit.candidates
    a = math.radians(true)
    x, y = fit.centre_point                                        # on the true edge, the nearest to (W/2, H/2)
    assert abs((x - (W - 1) / 2.0) * math.sin(a) + (y - (H - 1) / 2.0) * math.cos(a)) < 0.05
    assert math.hypot(x - W / 2.0, y - H / 2.0) < 1.0 and fit.centre_sigma_px < 0.05


def test_fit_edge_line_beats_the_rake_on_the_same_images():
    """Sub-pixel points every 4 px against the NI-style rake (edges every 15 px, parabolic sub-pixel): over ten
    noisy images of the same edge, the mean angle error is several times smaller."""
    true, new, old = 0.137, [], []
    for seed in range(10):
        img = edge_image(true, seed=seed, noise=4.0)
        new.append(abs(fit_edge_line(img).angle_deg - true))
        rep = measure_tilt_straight_edge(img, EDGE, RakeDirection.TOP_TO_BOTTOM, LineOptions(step_px=15))
        old.append(abs(rep.angle_deg - true))
    assert np.mean(new) < 0.01 and np.mean(new) * 3 < np.mean(old)


def test_fit_edge_line_polarity_and_no_edge():
    img = np.full((120, 200), 60.0)
    img[40:80] = 180.0                                             # rising at y 40, falling at y 80 (top → bottom)
    img = ndimage_blur(img)
    rising = fit_edge_line(img, horizontal=True, polarity=EdgePolarity.RISING)
    falling = fit_edge_line(img, horizontal=True, polarity=EdgePolarity.FALLING)
    assert rising is not None and falling is not None
    assert rising.centre_point[1] == pytest.approx(39.5, abs=0.05) and falling.centre_point[1] == pytest.approx(
        79.5, abs=0.05)
    flat = np.random.default_rng(1).normal(100.0, 2.0, (120, 200))
    assert fit_edge_line(flat, min_strength=20.0) is None


def ndimage_blur(img: np.ndarray) -> np.ndarray:
    from scipy import ndimage
    return ndimage.gaussian_filter(img, 0.8)


@pytest.mark.parametrize(("angle", "pitch", "dark"), [(0.137, (25.0, 25.0), False), (-7.3, (25.0, 25.0), True),
                                                      (30.0, (25.0, 25.0), False), (44.0, (25.0, 25.0), False),
                                                      (3.0, (30.0, 22.0), False), (-40.0, (30.0, 22.0), True)])
def test_lattice_angle_and_pitch_in_one_image(angle, pitch, dark):
    img = grid_image(angle, pitch=pitch, dark=dark)
    centres = detect_mesas(img)
    assert len(centres) > 80
    fit = fit_lattice(centres, reference=(160.0, 120.0))
    assert fit is not None and len(fit.centres) == len(centres)
    assert abs(fold_angle(fit.angle_deg - angle, 90.0)) < 0.005 and -45.0 < fit.angle_deg <= 45.0
    assert fit.angle_sigma_deg < 0.003 and fit.rms_px < 0.03
    # the row is the lattice vector within ±45° of X: its pitch is the one along the rows when |angle| < 45
    assert fit.pitch_row_px == pytest.approx(pitch[0], abs=0.01) and fit.pitch_across_px == pytest.approx(
        pitch[1], abs=0.01)
    node = fit.node(0, 0)                                          # the node nearest the reference: the true one
    assert math.hypot(node[0] - 163.3, node[1] - 127.1) < 0.05


def test_lattice_elongated_grid():
    fit = fit_lattice(detect_mesas(grid_image(2.5, pitch=(40.0, 14.0), radius=4.5)), reference=(160.0, 120.0))
    assert fit is not None and abs(fit.angle_deg - 2.5) < 0.01
    assert (fit.pitch_row_px, fit.pitch_across_px) == (pytest.approx(40.0, abs=0.01), pytest.approx(14.0, abs=0.01))


def test_lattice_needs_a_pattern():
    noise = np.random.default_rng(2).normal(100.0, 4.0, (240, 320))
    assert noise_sigma(noise) == pytest.approx(4.0, rel=0.05)
    assert len(detect_mesas(noise)) == 0
    few = grid_image(0.0, pitch=(130.0, 130.0))
    assert fit_lattice(detect_mesas(few)) is None                 # fewer than six mesas
    scattered = np.random.default_rng(3).uniform(0, 300, (40, 2))
    assert fit_lattice(scattered) is None                          # most points off any lattice


def test_fit_baseline_angle_and_uncertainty():
    two = fit_baseline([(-10.0, -0.02), (10.0, 0.02)], [0.0001, 0.0001], extra_sigma=0.0005)
    assert two.angle_deg == pytest.approx(math.degrees(math.atan2(0.04, 20.0)))
    assert two.sigma_deg == pytest.approx(math.degrees(math.sqrt(2.0 * (0.0001 ** 2 + 0.0005 ** 2)) / 20.0), rel=1e-5)
    assert two.length == pytest.approx(math.hypot(20.0, 0.04)) and two.rms == 0.0 and two.n_points == 2
    bent = fit_baseline([(-10.0, 0.0), (0.0, 0.01), (10.0, 0.0)])   # residuals dominate the zero detection error
    assert bent.angle_deg == pytest.approx(0.0, abs=1e-9) and bent.rms > 0.004 and bent.sigma_deg > 0.02
    lattice = fit_baseline([(0.0, 0.0), (0.0, 5.0)], period=90.0)  # a column: 90° ≡ 0° for a lattice
    assert lattice.angle_deg == pytest.approx(0.0, abs=1e-9)
    near_expected = fit_baseline([(0.0, 0.0), (-0.01, 5.0)], centre=90.0)
    assert near_expected.angle_deg == pytest.approx(90.0 + math.degrees(math.atan2(0.01, 5.0)))


def test_baseline_target_follows_the_measured_line():
    assert baseline_target([(1.0, 2.0)], (1.0, 2.0), (0.0, 1.0), -3.0) == pytest.approx((1.0, -1.0))
    # two points on y = 0.01·x: the target is on that line, the offset measured from the start's projection
    pts = [(0.0, 0.0), (-5.0, -0.05)]
    x, y = baseline_target(pts, (0.0, 0.0), (1.0, 0.0), 10.0)
    assert y == pytest.approx(0.01 * x, abs=1e-12) and math.hypot(x, y) == pytest.approx(10.0)
    x, y = baseline_target(pts, (0.0, 0.0), (-1.0, 0.0), 10.0)    # oriented as the given direction
    assert x == pytest.approx(-10.0, abs=0.01)


def test_next_baseline_step():
    assert next_baseline_step(0.5, 10.0, sigma_deg=1e-6, drift_limit=0.0125, growth=8.0) == 4.0     # growth cap
    assert next_baseline_step(4.0, 10.0, sigma_deg=1e-6, drift_limit=0.0125) == 10.0               # L / 2
    safe = 0.0125 / math.tan(math.radians(4 * 0.02))                                              # 4σ within ¼ pitch
    assert next_baseline_step(0.5, 10.0, sigma_deg=0.02, drift_limit=0.0125) == pytest.approx(min(4.0, safe))
    assert next_baseline_step(1.0, 10.0, sigma_deg=5.0, drift_limit=0.0125) == 1.5                 # at least × 1.5


def test_pick_row_mesa():
    rows = [(x * 0.05, y * 0.04) for x in range(-3, 4) for y in range(-2, 3)]
    got = pick_row_mesa(rows, (0.051, 0.003), (1.0, 0.0), pitch_along=0.05, pitch_across=0.04, line_point=(0.0, 0.0))
    assert got is not None and rows[got[0]] == (0.05, 0.0) and got[1] == 0.0
    off = pick_row_mesa(rows, (0.05, 0.015), (1.0, 0.0), pitch_along=0.05, pitch_across=0.04,
                        line_point=(0.0, 0.015))                    # the row predicted 15 µm off: ambiguous
    assert off is None
    assert pick_row_mesa([], (0.0, 0.0), (1.0, 0.0), pitch_along=0.05, pitch_across=0.04, line_point=(0, 0)) is None
