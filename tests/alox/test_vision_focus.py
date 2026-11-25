"""The focus metrics (``alox.vision.focus``, ``docs/07-focus-metrics.md``).

Expected values are hand-computed from the formulas of 07 (or computed by the brute-force pixel loops below, written
straight from the tables of §6.3 and §7.2), never by the code under test. Section numbers refer to 07.
"""

from __future__ import annotations

import math
from itertools import pairwise

import numpy as np
import pytest

from alox.vision import focus as f
from aloxcam.errors import Code, FocusError

RNG = np.random.default_rng(7)
NOISE = RNG.integers(0, 256, (40, 52)).astype(np.uint8)


# ============================================================================================== reference loops
def _px(img: np.ndarray, x: int, y: int, border: str = "edge") -> int:
    """Pixel (x = column, y = row) with the one-pixel frame of §6.5."""
    h, w = img.shape
    if border == "constant" and not (0 <= x < w and 0 <= y < h):
        return 0
    if border == "reflect":
        x = -x if x < 0 else (2 * (w - 1) - x if x >= w else x)
        y = -y if y < 0 else (2 * (h - 1) - y if y >= h else y)
    return int(img[min(max(y, 0), h - 1), min(max(x, 0), w - 1)])


def brute_edge(img: np.ndarray, method: str, border: str = "edge") -> np.ndarray:
    """IMAQ EdgeDetection from the table of §6.3, one pixel at a time, saturated to U8."""
    h, w = img.shape
    out = np.zeros((h, w), dtype=np.uint8)
    for y in range(h):
        for x in range(w):
            def p(dx: int, dy: int, x: int = x, y: int = y) -> int:
                return _px(img, x + dx, y + dy, border)
            c = p(0, 0)
            if method == "Differentiation":
                v: float = max(abs(p(-1, 0) - c), abs(p(-1, -1) - c), abs(p(0, -1) - c))
            elif method == "Gradient":
                v = max(abs(p(0, -1) - c), abs(p(-1, -1) - p(-1, 0)))
            elif method == "Roberts":
                v = max(abs(p(-1, -1) - c), abs(p(0, -1) - p(-1, 0)))
            elif method in ("Prewitt", "Sobel"):
                k = 1 if method == "Prewitt" else 2
                k1 = [[-1, 0, 1], [-k, 0, k], [-1, 0, 1]]
                gx = sum(k1[j][i] * p(i - 1, j - 1) for i in range(3) for j in range(3))
                gy = sum(k1[i][j] * p(i - 1, j - 1) for i in range(3) for j in range(3))      # K2 = K1ᵀ
                v = max(abs(gx), abs(gy))
            else:                                                                            # Sigma
                block = [p(i, j) for j in (-1, 0, 1) for i in (-1, 0, 1)]
                m = sum(block) / 9.0
                sd = math.sqrt(sum((b - m) ** 2 for b in block) / 9.0)
                v = round(m) if abs(c - m) <= sd else c
            out[y, x] = min(255, v)
    return out


def brute_lowpass(img: np.ndarray, kx: int, ky: int, tol: float, include_center: bool = True) -> np.ndarray:
    """The LowPass hypothesis of §7.2: window offsets −k//2 … k−1−k//2, replicated border."""
    h, w = img.shape
    out = np.zeros_like(img)
    for y in range(h):
        for x in range(w):
            vals = [_px(img, x + i, y + j) for j in range(-(ky // 2), ky - ky // 2) for i in range(-(kx // 2),
                                                                                                 kx - kx // 2)]
            c = int(img[y, x])
            m = sum(vals) / len(vals) if include_center else (sum(vals) - c) / (len(vals) - 1)
            out[y, x] = round(m) if abs(c - m) > tol / 100.0 * m else c
    return out


def box_blur(img: np.ndarray, k: int) -> np.ndarray:
    """Synthetic defocus: k × k mean, replicated border (§4.4: the curves must decrease along such a series)."""
    if k == 1:
        return img.copy()
    p = np.pad(img.astype(np.float64), k // 2, mode="edge")
    out = np.zeros(img.shape)
    for dy in range(k):
        for dx in range(k):
            out += p[dy:dy + img.shape[0], dx:dx + img.shape[1]]
    return np.rint(out / (k * k)).astype(np.uint8)


# ============================================================================================== LabVIEW numerics
@pytest.mark.parametrize(("width", "scale", "expected"), [
    (2448, 0.25, 612), (1002, 0.25, 250), (1006, 0.25, 252), (201, 0.5, 100), (203, 0.5, 102)])
def test_target_size_rounds_half_to_even(width, scale, expected):
    """§4.2 table: ``rint`` with ties to even, not truncation."""
    assert f.lv_to_i32(width * scale) == expected


def test_lv_to_i32_saturates_and_refuses_non_finite():
    assert f.lv_to_i32(1e12) == 2 ** 31 - 1 and f.lv_to_i32(-1e12) == -(2 ** 31)
    with pytest.raises(f.FocusMetricError):
        f.lv_to_i32(math.nan)


# ============================================================================================== ROI (§4.1)
def test_roi_right_and_bottom_edges_are_excluded():
    img = np.arange(300 * 400, dtype=np.int64).reshape(300, 400)
    roi = f.extract_roi(img, [10, 20, 110, 220])
    assert roi.shape == (200, 100)                                # width R − L, height B − T (test A2)
    assert roi[0, 0] == img[20, 10] and roi[-1, -1] == img[219, 109]


@pytest.mark.parametrize("rect", [None, (), [], np.array([], dtype=np.int32)])
def test_empty_roi_is_the_whole_image(rect):
    assert f.extract_roi(NOISE, rect) is NOISE


@pytest.mark.parametrize("rect", [[0, 0, 0, 0], [-5, 0, 10, 10], [40, 0, 60, 10], [5, 5, 3, 10], [1, 2, 3],
                                  [1, 2, 3, 4, 5]])
def test_degenerate_roi_is_an_error(rect):
    """[0,0,0,0] is not empty, so it goes to Extract (F12); until A2 settles NI's behaviour it is an error (9503)."""
    with pytest.raises(f.FocusMetricError) as info:
        f.extract_roi(NOISE, rect)
    assert info.value.code == Code.FOCUS_METRIC and isinstance(info.value, FocusError)


def test_clip_rect_clips_to_the_image():
    roi = f.extract_roi(NOISE, [-5, -3, 60, 10], clip_rect=True)
    assert roi.shape == (10, 52)
    with pytest.raises(f.FocusMetricError):
        f.extract_roi(NOISE, [60, 0, 70, 10], clip_rect=True)          # nothing left after clipping


# ============================================================================================== resampling (§4.3)
@pytest.mark.parametrize(("phase", "expected"), [
    ("corner", [0, 4, 496, 501, 505, 997]),
    ("corner_round", [0, 4, 497, 501, 505, 998]),
    ("center", [2, 6, 498, 503, 507, 999]),
])
def test_resample_phases(phase, expected):
    """§4.3 table: W = 1002 → X = 250, destination columns 0, 1, 124, 125, 126, 249."""
    index_image = np.tile(np.arange(1002, dtype=np.int64), (3, 1))
    out = f.resample_nearest(index_image, 250, 3, phase)
    assert out[0, [0, 1, 124, 125, 126, 249]].tolist() == expected


def test_resample_quarter_takes_one_pixel_in_four_and_keeps_the_dtype():
    out = f.resample_nearest(NOISE, 13, 10)
    assert out.dtype == np.uint8 and out.shape == (10, 13)
    assert np.array_equal(out, NOISE[::4, ::4])
    assert np.array_equal(f.resample_nearest(NOISE, 13, 10, "center"), NOISE[2::4, 2::4])


def test_upsampling_replicates_pixels():
    out = f.resample_nearest(np.array([[1, 2], [3, 4]], np.uint8), 4, 4)
    assert out.tolist() == [[1, 1, 2, 2], [1, 1, 2, 2], [3, 3, 4, 4], [3, 3, 4, 4]]


def test_subsample_sizes_and_errors():
    img = np.zeros((750, 1002), np.uint8)
    assert f.subsample(img, (), 0.25).shape == (188, 250)          # 187.5 → 188, 250.5 → 250 (ties to even)
    assert f.subsample(img, [0, 0, 203, 201], 0.5).shape == (100, 102)
    for scale in (0.0, -0.5, math.nan):                            # F13: Subsampling 0 → 0 × 0 → error, score 0
        with pytest.raises(f.FocusMetricError):
            f.subsample(img, (), scale)
    with pytest.raises(f.FocusMetricError):
        f.subsample(np.zeros((2, 2), np.uint8), (), 0.25)          # 0.5 → 0 (§4.2)
    with pytest.raises(f.FocusMetricError):
        f.subsample(np.zeros((4, 4, 3), np.uint8), (), 0.5)        # grayscale only


# ============================================================================================== FFT metric (§5)
def test_fft_score_of_a_hand_computed_2x2():
    """F = [[10, −2], [−4, 0]] for S = [[1, 2], [3, 4]]: Σ|F| = 16 (DC included)."""
    assert f.focus_fft(np.array([[1, 2], [3, 4]], np.uint8), (), 1.0) == 16.0


def test_fft_of_a_constant_image_is_its_dc_term():
    """§5.2: a constant image c gives c·X·Y (640×480 at 0.25 → 160×120)."""
    assert f.focus_fft(np.full((480, 640), 100, np.uint8), (), 0.25) == pytest.approx(100 * 160 * 120, rel=1e-6)
    assert f.focus_fft(np.full((480, 640), 100, np.uint8)) == f.focus_fft(np.full((480, 640), 100, np.uint8), (),
                                                                          0.25)  # VI default scale 0.25


def test_fft_score_equals_the_brute_force_dft_and_is_float32():
    img = NOISE[:6, :5]
    y, x = np.mgrid[0:6, 0:5]
    total = 0.0
    for v in range(6):
        for u in range(5):
            total += abs(np.sum(img * np.exp(-2j * np.pi * (u * x / 5 + v * y / 6))))
    score = f.focus_fft(img, (), 1.0)
    assert score == pytest.approx(total, rel=1e-6)
    assert score == float(np.float32(score))                       # SGL value carried as DBL (§8)
    assert f.focus_fft(img, (), 1.0, sequential_f32=True) == pytest.approx(total, rel=1e-5)


def test_fft_uses_the_roi_and_the_scale():
    img = NOISE.copy()
    expected = f.focus_fft(img[5:25, 4:44], (), 0.5)
    assert f.focus_fft(img, [4, 5, 44, 25], 0.5) == expected
    assert np.array_equal(img, NOISE)                               # the input is never modified


# ============================================================================================== edge filters (§6.3)
@pytest.mark.parametrize("method", f.EDGE_METHODS)
@pytest.mark.parametrize("border", f.BORDERS)
def test_edge_filters_match_the_pixel_formulas(method, border):
    img = NOISE[:9, :11]
    assert np.array_equal(f.ni_edge(img, method, border), brute_edge(img, method, border))


def test_horizontal_edge_by_hand():
    """Rows 10, 10, 30 (edge border). Sobel: |Gy| = (30+60+30) − (10+20+10) = 80 on rows 1–2 → mean 480/9;
    Prewitt 60 → 40; the 2×2 filters see the 20 step on row 2 only → 60/9."""
    img = np.array([[10, 10, 10], [10, 10, 10], [30, 30, 30]], np.uint8)
    assert f.ni_edge(img, "Sobel").tolist() == [[0, 0, 0], [80, 80, 80], [80, 80, 80]]
    assert f.focus_edge(img, (), 1.0, "Sobel") == float(np.float32(480 / 9))
    assert f.focus_edge(img, (), 1.0, "Prewitt") == 40.0
    for method in ("Gradient", "Differentiation", "Roberts"):
        assert f.ni_edge(img, method).tolist() == [[0, 0, 0], [0, 0, 0], [20, 20, 20]]
        assert f.focus_edge(img, (), 1.0, method) == float(np.float32(60 / 9))


def test_gradient_sees_horizontal_edges_only():
    """§6.3: Gradient measures vertical variations only (orientation, test A3)."""
    vertical_edge = np.array([[10, 10, 30]] * 3, np.uint8)
    assert not f.ni_edge(vertical_edge, "Gradient").any()
    assert f.ni_edge(vertical_edge, "Differentiation")[:, 2].tolist() == [20, 20, 20]
    assert f.ni_edge(vertical_edge, "Roberts")[:, 2].tolist() == [20, 20, 20]


def test_prewitt_and_sobel_take_the_maximum_not_the_magnitude():
    diagonal = np.array([[0, 0, 0], [0, 0, 9], [0, 9, 9]], np.uint8)
    # centre: Sobel Gx = (0 + 2·9 + 9) − 0 = 27 = Gy → max 27 (the magnitude would be 38); Prewitt 18
    assert f.ni_edge(diagonal, "Sobel")[1, 1] == 27
    assert f.ni_edge(diagonal, "Prewitt")[1, 1] == 18


@pytest.mark.parametrize(("method", "step", "expected"), [
    ("Sobel", 63, 252), ("Sobel", 64, 255), ("Sobel", 255, 255), ("Prewitt", 85, 255), ("Prewitt", 86, 255),
    ("Prewitt", 84, 252)])
def test_edge_values_saturate_to_u8(method, step, expected):
    """§6.4: Sobel reaches 4·step, Prewitt 3·step, clipped to 255."""
    img = np.array([[0, 0, step, step]] * 4, np.uint8)
    assert f.ni_edge(img, method).max() == expected


def test_u16_edges_saturate_to_65535():
    img = np.array([[0, 0, 60000, 60000]] * 4, np.uint16)
    out = f.ni_edge(img, "Sobel")
    assert out.dtype == np.uint16 and out.max() == 65535


@pytest.mark.parametrize(("border", "column0"), [("edge", 4), ("reflect", 0), ("constant", 255)])
def test_border_signatures(border, column0):
    """§6.5 signature test A4: ramp 64 + x → Sobel column 0 = 4 (replicate), 0 (mirror), 255 (zeros)."""
    ramp = (64 + np.tile(np.arange(10), (6, 1))).astype(np.uint8)
    out = f.ni_edge(ramp, "Sobel", border)
    assert set(out[1:-1, 0].tolist()) == {column0}
    assert set(out[1:-1, 1:-1].ravel().tolist()) == {8}


def test_constant_image_scores():
    """§10.4 B4 property: a constant image c gives edge score 0 for every method except Sigma (score c) and except
    with border='constant'; FFT c·X·Y."""
    img = np.full((20, 24), 77, np.uint8)
    for method in f.EDGE_METHODS:
        expected = 77.0 if method == "Sigma" else 0.0
        assert f.focus_edge(img, (), 0.5, method) == expected
    assert f.focus_edge(img, (), 0.5, "Sobel", border="constant") > 0
    assert f.focus_fft(img, (), 0.5) == pytest.approx(77 * 12 * 10)


def test_sigma_keeps_outliers_and_smooths_the_rest():
    img = np.full((3, 3), 10, np.uint8)
    img[1, 1] = 100
    out = f.ni_edge(img, "Sigma")
    assert out[1, 1] == 100                       # |100 − 20| = 80 > σ = √800 ≈ 28.3: kept
    assert out[0, 0] == 20                        # corner block (replicated) holds the 100 once: M = 20 replaces 10


def test_histogram_mean_is_float32():
    img = np.array([[1, 2], [2, 2]], np.uint8)
    mean = f.histogram_mean(img)
    assert isinstance(mean, np.float32) and mean == np.float32(1.75)


def test_full_is_the_float32_product_of_the_six_means_in_wiring_order():
    s = f.subsample(NOISE, (), 0.5)
    prod = np.float32(1.0)
    for method in ("Differentiation", "Gradient", "Prewitt", "Roberts", "Sigma", "Sobel"):
        prod = np.float32(prod * np.float32(brute_edge(s, method).sum() / s.size))
    assert f.focus_edge(NOISE, (), 0.5, "Roberts", full=True) == float(prod)      # Method ignored with FULL


# ============================================================================================== LowPass (§7.2)
def test_lowpass_replaces_outliers_by_the_local_mean():
    img = np.full((5, 5), 100, np.uint8)
    img[2, 2] = 200
    out = f.ni_lowpass(img, 3, 3, 40)
    expected = np.full((5, 5), 100, np.uint8)
    expected[2, 2] = 111                          # M = 1000/9: |200 − 111.1| > 0.4·111.1 → rint(111.1)
    assert np.array_equal(out, expected)
    assert img[2, 2] == 200                       # not in place


@pytest.mark.parametrize(("kx", "ky", "tol", "center"), [(3, 3, 40, True), (4, 4, 10, True), (5, 3, 0, False),
                                                           (2, 2, 25, False)])
def test_lowpass_matches_the_hypothesis(kx, ky, tol, center):
    img = NOISE[:8, :9]
    assert np.array_equal(f.ni_lowpass(img, kx, ky, tol, include_center=center),
                          brute_lowpass(img, kx, ky, tol, center))


def test_lowpass_rejects_empty_kernels():
    with pytest.raises(f.FocusMetricError):
        f.ni_lowpass(NOISE, 0, 3)
    with pytest.raises(f.FocusMetricError):
        f.ni_lowpass(NOISE, 1, 1, include_center=False)


# ============================================================================================== callers (06)
def test_autofocus_method_mapping():
    """06 §4.5: FFT and Prewitt keep their metric; Sobel, Diff, Gradient, Sigma and FULL all run Sobel."""
    assert [f.autofocus_metric_method(m) for m in f.METHODS] == ["FFT", "Sobel", "Prewitt", "Sobel", "Sobel",
                                                                 "Sobel", "Sobel"]


def test_fin_roi():
    """06 §5.6 / §9.7: High Mag centred with half-to-even halves; Low Mag [FinW, FinH, W, H] (not centred)."""
    assert f.fin_roi(2448, 2048, 101, 103) == [1174, 972, 1274, 1076]
    assert f.fin_roi(2448, 2048, 101, 103, low_mag=True) == [101, 103, 2448, 2048]
    assert f.fin_roi(64, 48, 300, 10) == [-118, 19, 182, 29]          # not clipped (F12)


def test_measure_dispatch():
    roi = [4, 2, 44, 36]
    for method, edge in f.REFOCUS_EDGE.items():
        image, score = f.measure(NOISE, method, roi, 0.5)
        assert image is NOISE and score == f.focus_edge(NOISE, roi, 0.5, edge)
    assert f.measure(NOISE, "FFT", roi, 0.5) == (NOISE, f.focus_fft(NOISE, roi, 0.5))
    _, full = f.measure(NOISE, "FULL", roi, 0.5)                      # edge FULL on the ROI × FFT of the whole image
    assert full == f.focus_edge(NOISE, roi, 0.5, full=True) * f.focus_fft(NOISE, (), 0.5)
    with pytest.raises(ValueError):
        f.measure(NOISE, "Roberts", roi, 0.5)                          # not an Autofocus Method


def test_sigma_returns_the_filtered_copy():
    """§7.1: LowPass {20, 20, 40 %} on the whole image, then the Sigma edge score; the input stays untouched."""
    img = NOISE.copy()
    image, score = f.measure(img, "Sigma", [4, 2, 44, 36], 0.5)
    filtered = f.ni_lowpass(NOISE, 20, 20, 40)
    assert image is not img and np.array_equal(image, filtered) and np.array_equal(img, NOISE)
    assert score == f.focus_edge(filtered, [4, 2, 44, 36], 0.5, "Sigma")
    unfiltered, _ = f.measure(img, "Sigma", (), 0.5, sigma_lowpass=False)   # NI rejecting the 20×20 kernel (F9)
    assert unfiltered is img


def test_measure_lv_style_returns_score_zero_and_the_error():
    image, score, error = f.measure_lv_style(NOISE, "Sobel", [0, 0, 0, 0], 0.5)
    assert image is NOISE and score == 0.0 and error is not None and error.code == Code.FOCUS_METRIC
    image, score, error = f.measure_lv_style(NOISE, "Sigma", [0, 0, 0, 0], 0.5)
    assert score == 0.0 and error is not None and np.array_equal(image, f.ni_lowpass(NOISE, 20, 20, 40))
    assert f.measure_lv_style(NOISE, "FFT", (), 0.5)[1:] == (f.focus_fft(NOISE, (), 0.5), None)


def test_ni_compat_metrics_freezes_the_parameters():
    index_image = np.tile((np.arange(64) * 3 % 256).astype(np.uint8), (32, 1))
    corner = f.NiCompatMetrics().measure(index_image, "FFT", (), 0.25)[1]
    center = f.NiCompatMetrics(phase="center").measure(index_image, "FFT", (), 0.25)[1]
    assert center == f.focus_fft(index_image, (), 0.25, phase="center") != corner
    with pytest.raises(ValueError):
        f.NiCompatMetrics(border="wrap")
    with pytest.raises(ValueError):
        f.NiCompatMetrics(phase="bilinear")


# ============================================================================================== focus behaviour
@pytest.mark.parametrize("method", ["FFT", "Sobel", "Prewitt", "Diff", "Gradient", "FULL"])
@pytest.mark.parametrize("scale", [0.25, 0.5])
def test_scores_decrease_along_a_blur_series(method, scale):
    """§4.4 / B4: decimation aliases, but every metric still decreases with defocus."""
    sharp = np.random.default_rng(11).integers(0, 256, (96, 128)).astype(np.uint8)
    scores = [f.measure(box_blur(sharp, k), method, (), scale)[1] for k in (1, 3, 5, 7, 9)]
    assert all(a > b for a, b in pairwise(scores)), scores


def test_sigma_measures_brightness_not_focus():
    """§7.3 / F10: with the documented formula the Sigma score stays near the mean grey level."""
    sharp = np.random.default_rng(11).integers(0, 256, (96, 128)).astype(np.uint8)
    scores = [f.measure(box_blur(sharp, k), "Sigma", (), 0.5)[1] for k in (1, 5, 9)]
    assert all(abs(s - sharp.mean()) < 3 for s in scores)
