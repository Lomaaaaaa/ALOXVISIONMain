"""Straight-edge tilt and object masking (``alox.vision.tilt``) on synthetic images with a known edge angle."""

from __future__ import annotations

import math

import numpy as np
import pytest

from alox.vision.edges import EdgeOptions, EdgePolarity
from alox.vision.tilt import (
    EdgeMethod,
    LineOptions,
    RakeDirection,
    find_straight_edges,
    mask_matched_object,
    measure_tilt_straight_edge,
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
