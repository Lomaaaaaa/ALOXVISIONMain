"""The NI Vision equivalents of :mod:`alox.vision.edges` on synthetic images (mesa: bright disc, brighter aperture)."""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy import ndimage

from alox.vision import edges
from alox.vision.edges import EdgeOptions, EdgePolarity, SearchDirection

CX, CY = 170.3, 148.7


def mesa_image(h: int = 300, w: int = 360, *, cx: float = CX, cy: float = CY, r_mesa: float = 100.0,
               r_aperture: float | None = 60.0, noise: float = 2.0, blur: float = 1.0, seed: int = 1) -> np.ndarray:
    """Background 40, mesa 120, oxide aperture 200, Gaussian optics blur and noise (8 bits)."""
    yy, xx = np.mgrid[0:h, 0:w]
    d = np.hypot(xx - cx, yy - cy)
    img = np.full((h, w), 40.0)
    img[d <= r_mesa] = 120.0
    if r_aperture:
        img[d <= r_aperture] = 200.0
    if blur:
        img = ndimage.gaussian_filter(img, blur)
    img += np.random.default_rng(seed).normal(0.0, noise, img.shape)
    return np.clip(np.rint(img), 0, 255).astype(np.uint8)


# ================================================================================================== 1-D edges
def test_step_profile_gives_one_edge_between_the_samples_with_its_contrast():
    profile = np.array([10.0] * 10 + [60.0] * 10)
    found = edges.detect_edges(profile, EdgeOptions(min_strength=10))
    assert len(found) == 1
    position, strength = found[0]
    assert position == pytest.approx(9.5)
    assert strength == pytest.approx(50.0)


def test_polarity_and_minimum_strength_select_the_edges():
    profile = np.array([10.0] * 8 + [60.0] * 8 + [20.0] * 8)
    rising = edges.detect_edges(profile, EdgeOptions(EdgePolarity.RISING, min_strength=10))
    falling = edges.detect_edges(profile, EdgeOptions(EdgePolarity.FALLING, min_strength=10))
    assert [round(p, 1) for p, _ in rising] == [7.5]
    assert [round(p, 1) for p, _ in falling] == [15.5]
    assert edges.detect_edges(profile, EdgeOptions(min_strength=45)) == [(pytest.approx(7.5), 50.0)]


def test_kernel_size_averages_the_neighbourhood():
    profile = np.array([0.0] * 10 + [30.0] * 10)
    s3 = edges.edge_strengths(profile, 3)
    s7 = edges.edge_strengths(profile, 7)
    assert s3.max() == pytest.approx(30.0)
    assert s7.max() == pytest.approx(30.0)
    assert np.count_nonzero(s7) > np.count_nonzero(s3)                  # wider kernel, wider response


def test_line_profile_is_bilinear_and_averaged_across_the_width():
    img = np.tile(np.arange(10, dtype=float), (5, 1))                  # value = x
    prof = edges.line_profile(img, (1.5, 2.0), (6.5, 2.0), width=3)
    assert prof == pytest.approx([1.5, 2.5, 3.5, 4.5, 5.5, 6.5])


def test_first_edge_along_a_line_in_the_image():
    img = mesa_image()
    found = edges.find_edges_on_line(img, (CX, CY), (CX + 150, CY), EdgeOptions())
    assert [round(e.distance) for e in found] == [60, 100]
    assert found[0].strength < 0 and found[1].strength < 0             # brighter inside: falling outward


# ================================================================================================== spokes, circles
def test_spoke_lines_cover_the_circle_in_angle_order_and_direction():
    lines = edges.spoke_lines((0.0, 0.0), 10.0, 50.0, step=5.0, direction=SearchDirection.INSIDE_TO_OUTSIDE)
    assert len(lines) == int(2 * math.pi * 50 // 5)
    (x0, y0), (x1, y1) = lines[0]
    assert (x0, y0, x1, y1) == pytest.approx((10.0, 0.0, 50.0, 0.0))
    quarter = lines[len(lines) // 4]
    assert quarter[1][1] < 0                                            # 90° is up (y decreases)
    out_in = edges.spoke_lines((0.0, 0.0), 10.0, 50.0, step=5.0, direction=SearchDirection.OUTSIDE_TO_INSIDE)
    assert out_in[0][0] == pytest.approx((50.0, 0.0))


def test_find_circular_edge_finds_the_mesa():
    img = mesa_image()
    found = edges.find_circular_edge(img, (CX + 3, CY - 2), 80, 130, direction=SearchDirection.OUTSIDE_TO_INSIDE,
                                     options=EdgeOptions(), step=5)
    assert found.found
    assert (found.circle.x, found.circle.y, found.circle.radius) == pytest.approx((CX, CY, 100.0), abs=0.3)
    empty = edges.find_circular_edge(img, (CX, CY), 80, 0, direction=SearchDirection.OUTSIDE_TO_INSIDE,
                                     options=EdgeOptions(), step=5)
    assert not empty.found and empty.circle.is_nan


def test_fit_circle_robust_rejects_outliers_and_scores_the_inliers():
    t = np.linspace(0, 2 * np.pi, 60, endpoint=False)
    pts = np.column_stack((50 + 20 * np.cos(t), 40 + 20 * np.sin(t)))
    pts[::10] += 15.0                                                   # 6 outliers
    robust = edges.fit_circle_robust(pts, max_distance=1.0, min_score=500)
    assert robust.valid and robust.inliers.sum() == 54
    assert robust.score == pytest.approx(900.0)
    assert (robust.circle.x, robust.circle.y, robust.circle.radius) == pytest.approx((50, 40, 20), abs=1e-6)
    plain = edges.fit_circle_robust(pts, max_distance=1.0, reject_outliers=False)
    assert plain.inliers.all() and abs(plain.circle.radius - 20) > 0.1
    assert not edges.fit_circle_robust(pts[:2]).valid
    assert not edges.fit_circle_robust(pts, max_distance=1.0, min_score=950).valid


# ================================================================================================== shapes
def test_detect_circles_finds_the_mesa_with_a_full_score():
    found = edges.detect_circles(mesa_image(), 80, 120)
    assert len(found) == 1
    c = found[0]
    assert (c.x, c.y, c.radius) == pytest.approx((CX, CY, 100.0), abs=0.5)
    assert c.score > 950
    assert edges.detect_circles(mesa_image(), 20, 40) == []             # no circle in that range


def test_detect_circles_ellipse_radius_is_the_mean_of_the_axes():
    h, w = 260, 300
    yy, xx = np.mgrid[0:h, 0:w]
    img = np.where(((xx - 150) / 90.0) ** 2 + ((yy - 130) / 70.0) ** 2 <= 1, 150.0, 30.0)
    img = ndimage.gaussian_filter(img, 1.0)
    found = edges.detect_circles(img, 60, 100, ellipses=True, min_score=300)
    assert found and found[0].radius == pytest.approx(80.0, abs=1.5)


def test_minimum_score_and_length_reject_partial_arcs():
    img = mesa_image(r_aperture=None)
    img[:, :170] = 40                                                   # half a disc
    assert edges.detect_circles(img, 80, 120, min_score=700) == []
    half = edges.detect_circles(img, 80, 120, min_score=300)
    assert half and 400 < half[0].score < 600


# ================================================================================================== blobs, morphology
def test_blob_pipeline_finds_the_disc_and_filters_non_circular_particles():
    img = np.full((200, 260), 30.0)
    yy, xx = np.mgrid[0:200, 0:260]
    img[np.hypot(xx - 80, yy - 100) <= 30] = 200.0                      # disc r = 30
    img[20:40, 150:250] = 200.0                                         # elongated bar
    img[150:153, 200:203] = 200.0                                       # speck
    mask = edges.local_threshold(img, 101, method=1)
    mask = edges.remove_small_particles(mask, 2)
    mask = edges.fill_holes(edges.reject_border(mask))
    parts = edges.particles(mask)
    assert len(parts) == 2                                              # the speck is gone
    found = edges.find_blob_circles(mask, 10, 50)
    assert len(found) == 1
    assert (found[0].x, found[0].y) == pytest.approx((80, 100), abs=0.5)
    assert found[0].radius == pytest.approx(30, abs=1.0)
    assert found[0].score > 900


def test_reject_border_and_heywood_factor():
    mask = np.zeros((50, 50), dtype=bool)
    mask[0:10, 0:10] = True
    mask[20:30, 20:30] = True
    kept = edges.reject_border(mask)
    assert kept[25, 25] and not kept[5, 5]
    (square,) = edges.particles(kept)
    assert square.area == 100
    assert 1.0 < square.heywood < 40 / (2 * math.sqrt(math.pi * 100)) + 0.01     # a square: 1.128 (corners cut)
    yy, xx = np.mgrid[0:80, 0:80]
    (disc,) = edges.particles(np.hypot(xx - 40, yy - 40) <= 25)
    assert disc.heywood == pytest.approx(1.0, abs=0.05)
    assert disc.perimeter == pytest.approx(2 * math.pi * 25, rel=0.05)


def test_grey_morphology_sizes():
    img = np.zeros((9, 9), dtype=np.uint8)
    img[4, 4] = 255
    assert edges.grey_morphology(img, "open", 2).max() == 0              # 2 → 3×3: the dot is removed
    assert edges.grey_morphology(img, "dilate", 1) is img                # below 3: unchanged
    assert edges.grey_morphology(img, "dilate", 3).sum() == 9 * 255


def test_match_template_finds_the_model():
    img = mesa_image(noise=1.0)
    model = img[CY.__round__() - 30:CY.__round__() + 31, CX.__round__() - 30:CX.__round__() + 31]
    found = edges.match_template(img, model, min_score=900)
    assert found and (found[0].x, found[0].y) == pytest.approx((round(CX), round(CY)), abs=0.5)
    assert found[0].score == pytest.approx(1000, abs=1)
