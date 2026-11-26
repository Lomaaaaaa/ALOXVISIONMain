"""The pattern matchers of ``alox.vision.matching`` on synthetic images (known offset, rotation, noise)."""

from __future__ import annotations

import math
import sys

import numpy as np
import pytest
from scipy import ndimage

from alox.vision import matching
from alox.vision.matching import (
    CurveOptions,
    Matcher,
    MatcherUnavailable,
    MatchSettings,
    NiVisionMatcher,
    NumpyMatcher,
    OpenCVMatcher,
    TemplateError,
    bounding_box,
    create_matcher,
    detect_object_preprocessing,
    extract_curves,
    load_template,
)
from aloxcam.imaging.png import write_png

try:
    import cv2  # noqa: F401
    HAVE_CV2 = True
except ImportError:                     # the optional extra "vision" is not installed
    HAVE_CV2 = False

MATCHERS = [NumpyMatcher] + ([OpenCVMatcher] if HAVE_CV2 else [])


def make_template(size: int = 41) -> np.ndarray:
    """An asymmetric pattern (no rotational symmetry): bars and a block on a dark background."""
    t = np.full((size, size), 40, np.float32)
    t[4:10, 4:36] = 210                  # top bar
    t[14:34, 6:12] = 160                 # left bar
    t[20:30, 22:38] = 230                # block, right
    t[33:38, 16:22] = 120
    return t


def scene(template: np.ndarray, center: tuple[int, int], *, shape=(200, 260), noise=6.0, seed=0,
          background=60.0) -> np.ndarray:
    """``template`` pasted with its centre pixel at ``center`` (odd template size) on a noisy background."""
    rng = np.random.default_rng(seed)
    img = np.full(shape, background, np.float32)
    h, w = template.shape
    x0, y0 = center[0] - (w - 1) // 2, center[1] - (h - 1) // 2
    img[y0:y0 + h, x0:x0 + w] = template
    return img + rng.normal(0.0, noise, shape).astype(np.float32)


def rotated_scene(template: np.ndarray, center: tuple[float, float], angle_deg: float, *, shape=(200, 260),
                  noise=6.0, seed=1) -> np.ndarray:
    """The template rotated by ``angle_deg`` counter-clockwise **as displayed** (y down) about its centre, drawn at
    ``center`` (independent construction: an inverse mapping of every scene pixel)."""
    rng = np.random.default_rng(seed)
    h, w = template.shape
    a = math.radians(angle_deg)
    c, s = math.cos(a), math.sin(a)
    ys, xs = np.mgrid[0:shape[0], 0:shape[1]].astype(np.float64)
    dx, dy = xs - center[0], ys - center[1]
    # screen-CCW rotation R maps template offsets to scene offsets: (dx, dy) = (c·u + s·v, −s·u + c·v); invert
    u = c * dx - s * dy
    v = s * dx + c * dy
    tx, ty = u + (w - 1) / 2.0, v + (h - 1) / 2.0
    values = ndimage.map_coordinates(template, [ty, tx], order=1, mode="constant", cval=np.nan)
    img = np.where(np.isnan(values), 60.0, values)
    return (img + rng.normal(0.0, noise, shape)).astype(np.float32)


# ============================================================================================== settings
def test_sample_angles_and_allowed():
    none = MatchSettings()
    assert not none.rotation and none.sample_angles(5.0) == [0.0]
    full = MatchSettings(angle_ranges=((0, 360),))
    angles = full.sample_angles(10.0)
    assert len(angles) == 36 and angles[0] == 0.0 and angles[-1] == pytest.approx(350.0)
    assert full.allowed(725.0) and full.full_circle()
    wrap = MatchSettings(angle_ranges=((-10, 10),))
    assert wrap.allowed(355.0) and wrap.allowed(5.0) and not wrap.allowed(20.0)
    assert min(wrap.sample_angles(5.0)) == 0.0 and 350.0 in wrap.sample_angles(5.0)
    with pytest.raises(ValueError):
        MatchSettings(number_of_matches=-1)


def test_bounding_box_rotates_counter_clockwise_as_displayed():
    box = bounding_box((100.0, 50.0), (20, 10), 90.0)
    # the template's top-left corner (−10, −5) goes to the bottom-left on screen after a CCW quarter turn
    assert box[0] == pytest.approx((95.0, 60.0))
    assert box[1] == pytest.approx((95.0, 40.0))


# ============================================================================================== matching
@pytest.mark.parametrize("cls", MATCHERS)
def test_finds_the_template_at_a_known_offset(cls):
    matcher: Matcher = cls()
    t = make_template()
    template = matcher.learn(t)
    img = scene(t, (170, 120))
    matches = matcher.match(img, template, MatchSettings(number_of_matches=5, min_score=600))
    assert len(matches) == 1
    m = matches[0]
    assert m.position == pytest.approx((170.0, 120.0), abs=0.01)
    assert m.angle == pytest.approx(0.0) and m.scale == 100.0
    assert 950 < m.score <= 1000 and m.correlation_score == m.score and not m.contrast_reversed
    assert m.bounding_box[0] == pytest.approx((149.5, 99.5))


@pytest.mark.parametrize("cls", MATCHERS)
@pytest.mark.parametrize("angle", [30.0, 90.0, 217.0])
def test_rotation_search(cls, angle):
    matcher = cls()
    t = make_template()
    template = matcher.learn(t)
    img = rotated_scene(t, (130.0, 100.0), angle)
    settings = MatchSettings(number_of_matches=3, min_score=700, angle_ranges=((0, 360),), subpixel=True)
    matches = matcher.match(img, template, settings)
    assert matches, "not found"
    best = matches[0]
    assert best.position == pytest.approx((130.0, 100.0), abs=0.6)
    diff = (best.angle - angle + 180.0) % 360.0 - 180.0
    assert abs(diff) < 1.0
    assert best.score > 900


def test_quarter_turn_with_rot90_is_90_degrees():
    """``np.rot90`` turns an image counter-clockwise as displayed: NI's angle convention gives 90°."""
    matcher = NumpyMatcher()
    t = make_template()
    img = scene(np.rot90(t).copy(), (120, 90))
    m = matcher.match(img, matcher.learn(t), MatchSettings(min_score=800, angle_ranges=((0, 360),)))[0]
    assert m.angle == pytest.approx(90.0, abs=1.0)


def test_no_rotation_range_misses_a_rotated_pattern():
    matcher = NumpyMatcher()
    t = make_template()
    img = rotated_scene(t, (130.0, 100.0), 60.0)
    assert matcher.match(img, matcher.learn(t), MatchSettings(min_score=700)) == []


def test_number_of_matches_min_score_and_order():
    matcher = NumpyMatcher()
    t = make_template()
    img = scene(t, (60, 60), noise=4.0)
    other = scene(t, (190, 130), noise=25.0, seed=3)                  # a noisier copy: lower score
    img[100:170, 150:240] = other[100:170, 150:240]
    template = matcher.learn(t)
    both = matcher.match(img, template, MatchSettings(number_of_matches=5, min_score=500))
    assert [m.position for m in both] == [pytest.approx((60, 60), abs=0.01), pytest.approx((190, 130), abs=0.01)]
    assert both[0].score > both[1].score
    one = matcher.match(img, template, MatchSettings(number_of_matches=1, min_score=500))
    assert len(one) == 1 and one[0].position == pytest.approx((60, 60), abs=0.01)
    high = matcher.match(img, template, MatchSettings(number_of_matches=5, min_score=both[1].score + 1))
    assert len(high) == 1
    assert matcher.match(img, template, MatchSettings(number_of_matches=0)) == []


def test_mask_ignores_the_masked_pixels():
    matcher = NumpyMatcher()
    t = make_template()
    img = scene(t, (100, 100), noise=3.0)
    img[80:95, 80:121] = 255.0                                          # the top of the object changed in the scene
    mask = np.ones(t.shape, bool)
    mask[:15, :] = False
    plain = matcher.match(img, matcher.learn(t), MatchSettings(min_score=0))[0]
    masked = matcher.match(img, matcher.learn(t, mask=mask), MatchSettings(min_score=0))[0]
    assert masked.position == pytest.approx((100, 100), abs=0.01)
    assert masked.score > 950 > plain.score


def test_contrast_reversal():
    matcher = NumpyMatcher()
    t = make_template()
    img = scene(255.0 - t, (100, 90))
    template = matcher.learn(t)
    assert matcher.match(img, template, MatchSettings(min_score=800)) == []
    m = matcher.match(img, template, MatchSettings(min_score=800, contrast_reversal=True))[0]
    assert m.contrast_reversed and m.position == pytest.approx((100, 90), abs=0.01)


def test_template_larger_than_the_image():
    matcher = NumpyMatcher()
    t = make_template()
    assert matcher.match(np.zeros((20, 20), np.float32), matcher.learn(t), MatchSettings()) == []


def test_learn_refuses_unusable_templates():
    matcher = NumpyMatcher()
    with pytest.raises(TemplateError):
        matcher.learn(np.full((20, 20), 7, np.uint8))
    with pytest.raises(TemplateError):
        matcher.learn(np.zeros((3, 30), np.uint8))
    with pytest.raises(TemplateError):
        matcher.learn(make_template(), mask=np.ones((5, 5), bool))
    with pytest.raises(TemplateError):
        matcher.learn(np.zeros((2, 2, 2)))


@pytest.mark.skipif(not HAVE_CV2, reason="OpenCV not installed")
def test_opencv_and_numpy_agree():
    t = make_template()
    img = rotated_scene(t, (140.3, 95.6), 47.0, seed=5)
    settings = MatchSettings(number_of_matches=2, min_score=600, angle_ranges=((0, 360),), subpixel=True)
    a = NumpyMatcher().match(img, NumpyMatcher().learn(t), settings)[0]
    b = OpenCVMatcher().match(img, OpenCVMatcher().learn(t), settings)[0]
    assert a.position == pytest.approx(b.position, abs=0.1)
    assert a.angle == pytest.approx(b.angle, abs=0.2)
    assert a.score == pytest.approx(b.score, abs=2.0)


# ============================================================================================== files, factory
def test_load_template_with_mask(tmp_path):
    t = make_template().astype(np.uint8)
    write_png(tmp_path / "model.png", t)
    mask = np.zeros(t.shape, np.uint8)
    mask[10:, :] = 255
    write_png(tmp_path / "mask.png", mask)
    template = load_template(NumpyMatcher(), tmp_path / "model.png", tmp_path / "mask.png")
    assert template.size == (41, 41) and template.path == tmp_path / "model.png"
    assert template.mask is not None and not template.mask[:10].any() and template.mask[10:].all()
    with pytest.raises(TemplateError, match="not found"):
        load_template(NumpyMatcher(), tmp_path / "missing.png")
    (tmp_path / "bad.png").write_bytes(b"not a png")
    with pytest.raises(TemplateError):
        load_template(NumpyMatcher(), tmp_path / "bad.png")
    write_png(tmp_path / "small_mask.png", np.ones((5, 5), np.uint8))
    with pytest.raises(TemplateError, match="size"):
        load_template(NumpyMatcher(), tmp_path / "model.png", tmp_path / "small_mask.png")


def test_create_matcher():
    assert isinstance(create_matcher("numpy"), NumpyMatcher)
    auto = create_matcher()
    assert isinstance(auto, OpenCVMatcher if HAVE_CV2 else NumpyMatcher)
    assert isinstance(auto, Matcher)
    with pytest.raises(ValueError):
        create_matcher("halcon")
    if sys.platform != "win32":
        assert not NiVisionMatcher.available()
        with pytest.raises(MatcherUnavailable):
            create_matcher("ni")


def test_auto_falls_back_to_numpy_without_opencv(monkeypatch):
    def missing(name, *args, **kwargs):
        raise ImportError(f"No module named {name!r}")
    monkeypatch.setattr(matching.importlib, "import_module", missing)
    assert isinstance(create_matcher("auto"), NumpyMatcher)
    with pytest.raises(MatcherUnavailable):
        create_matcher("opencv")


def test_ni_skeleton_is_not_selected_while_not_implemented(monkeypatch):
    monkeypatch.setattr(NiVisionMatcher, "available", classmethod(lambda cls: True))
    assert NiVisionMatcher.IMPLEMENTED is False
    assert not isinstance(create_matcher("auto"), NiVisionMatcher)
    ni = object.__new__(NiVisionMatcher)
    with pytest.raises(NotImplementedError):
        ni.learn(make_template())
    with pytest.raises(NotImplementedError):
        ni.match(np.zeros((10, 10)), None, MatchSettings())        # type: ignore[arg-type]


# ============================================================================================== pre-processing
def test_detect_object_preprocessing():
    img = np.full((30, 30), 50, np.uint8)
    img[10, 10] = 250                                                   # a one-pixel bright spot
    img[20:23, 5:8] = 0                                                 # a small dark hole
    assert detect_object_preprocessing(img, 0, 1) is img                # k < 3 after rounding: nothing
    opened = detect_object_preprocessing(img, 2, 0)                     # 2 → 3: opening removes the spot
    assert opened[10, 10] == 50 and opened.dtype == np.uint8
    closed = detect_object_preprocessing(img, 0, 4)                     # 4 → 5: closing fills the hole
    assert closed[21, 6] == 50


def test_extract_curves_outlines_a_square():
    img = np.zeros((60, 60), np.uint8)
    img[20:40, 20:40] = 200
    curves = extract_curves(img, CurveOptions(edge_threshold=30, min_length=10))
    assert len(curves) == 1
    xs, ys = curves[0][:, 0], curves[0][:, 1]
    assert xs.min() >= 18 and xs.max() <= 41 and ys.min() >= 18 and ys.max() <= 41
    assert extract_curves(img, CurveOptions(edge_threshold=500, min_length=10)) == []
