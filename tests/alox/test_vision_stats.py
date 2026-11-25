"""Tests of alox.vision.stats: NaN/outlier filter, averaging, fit window, diameter fit, remaining time."""

from __future__ import annotations

import math

import numpy as np
import pytest

from alox.vision.stats import (
    FIT_LINE_ERROR,
    INSUFFICIENT_POINTS,
    MAX_REMAINING_TIME_S,
    LinearFit,
    OxideMeasureType,
    RateEstimationError,
    average_measurements,
    estimate_oxidation,
    extract_fit_window,
    filter_invalid_measures,
    fit_diameters,
    format_hms,
    nan_filter,
    remaining_time,
    sample_std,
)

T0 = 3_850_000_000.0  # a LabVIEW timestamp (s since 1904)


# ---------------------------------------------------------------------------------------------------------------------
# NaN filter (tools_alox_NaN_filter)
# ---------------------------------------------------------------------------------------------------------------------


def test_sample_std_weighting():
    assert sample_std([1.0, 2.0, 3.0, 4.0]) == pytest.approx(np.std([1, 2, 3, 4], ddof=1))
    assert sample_std([5.0]) == 0.0
    assert math.isnan(sample_std([]))


def test_nan_values_are_removed_first():
    r = nan_filter([10.0, math.nan, 10.1, math.nan])
    assert r.nan_filtered.tolist() == [10.0, 10.1]
    assert r.nan_filtered_mean == pytest.approx(10.05)
    assert math.isnan(r.raw_mean)  # mean of the raw input ("backupVal" node)


def test_empty_and_all_nan_inputs():
    for values in ([], [math.nan, math.nan]):
        r = nan_filter(values)
        assert r.n_samples == 0
        assert math.isnan(r.final_mean) and math.isnan(r.fix_filtered_mean) and math.isnan(r.nan_filtered_mean)
        assert math.isnan(r.kept_percent)


def test_single_sample_is_kept():
    r = nan_filter([9.5])
    assert r.final.tolist() == [9.5]
    assert r.final_mean == 9.5


def test_up_to_five_samples_asymmetric_mean_band():
    # <= 5 samples: keep mean - 1 sigma <= v <= mean + 2 sigma (as wired in d3)
    low = nan_filter([10.0, 10.0, 10.0, 10.0, 8.0])  # mean 9.6, s = 0.894: 8.0 < 8.706 -> dropped
    assert low.final.tolist() == [10.0] * 4
    high = nan_filter([10.0, 10.0, 10.0, 10.0, 12.0])  # 12.0 <= 10.4 + 1.789 -> kept
    assert high.final.tolist() == [10.0] * 4 + [12.0]
    assert high.fix_filtered.tolist() == high.final.tolist()


def test_symmetric_band_can_be_selected():
    r = nan_filter([10.0, 10.0, 10.0, 10.0, 8.0], lower_sigmas=2.0)
    assert r.final.size == 5


def test_sample_count_threshold_is_more_than_five():
    five = [10.0, 10.0, 10.0, 10.0, 8.0]
    six = [*five, 10.0]
    assert nan_filter(five).final.size == 4  # mean band
    r6 = nan_filter(six)  # median 10, s = 0.816: band 2 = [9.18, 11.63]; 8.0 dropped as well
    assert r6.fix_filtered.tolist() == [10.0] * 5


def test_more_than_five_samples_median_bands():
    # median 10.0: the 0.25 band keeps 10.0 and 10.2; 13.0 > median + 2 s -> dropped; sigma2 rounds to 0: no pass 3
    r = nan_filter([10.0] * 6 + [10.2, 13.0])
    assert r.fix_filtered.tolist() == [10.0] * 6 + [10.2]
    assert r.final.tolist() == r.fix_filtered.tolist()
    assert r.final_mean == pytest.approx((60.0 + 10.2) / 7)
    assert r.kept_percent == pytest.approx(100.0 * 7 / 8)


def test_median_band_is_asymmetric():
    # median 5.5, s = 3.03: [median - s, median + 2 s] = [2.47, 11.56] drops 1 and 2 but keeps 10
    r = nan_filter([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])
    assert r.fix_filtered.tolist() == [3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    assert r.final.tolist() == r.fix_filtered.tolist()  # pass 3 (mean +- 2 s2) keeps everything


def test_third_pass_mean_two_sigma():
    values = [7.7, 8.0, 8.3, 9.0, 9.1, 9.4, 11.7, 12.9]
    r = nan_filter(values)
    assert sorted(r.fix_filtered.tolist()) == [7.7, 8.0, 8.3, 9.0, 9.1, 9.4, 11.7]  # 12.9 > median + 2 s
    assert sorted(r.final.tolist()) == [7.7, 8.0, 8.3, 9.0, 9.1, 9.4]  # 11.7 > m2 + 2 s2
    assert r.final_mean == pytest.approx(np.mean([7.7, 8.0, 8.3, 9.0, 9.1, 9.4]))


def test_band_is_absolute_in_input_units():
    tight = [10.0, 10.0, 10.0, 10.0, 10.0, 10.3, 10.0]
    assert 10.3 in nan_filter(tight, median_band=0.5).final.tolist()


# ---------------------------------------------------------------------------------------------------------------------
# Averaging (tools_mesa_averaged_measurement)
# ---------------------------------------------------------------------------------------------------------------------


def test_average_measurements_selects_the_measure_type():
    series = {
        "external": [30.0, 30.1, 29.9],
        "fit": [9.0, 9.0, 9.1],
        "median": [9.2, 9.2, 9.2],
        "mean": [8.9, 9.0, math.nan],
        "surface": [9.0, 9.0, 9.0],
        "inscribed": [8.0, 8.0, 8.0],
        "circumscribed": [10.0, 10.0, 10.0],
        "circularity": [1.25, 1.25, 1.25],
    }
    avg = average_measurements(series, measure_type=OxideMeasureType.CIRCUMSCRIBED)
    assert avg.oxide_diameter == pytest.approx(10.0)
    assert avg.averages["mean"] == pytest.approx(8.95)
    assert avg.averages["external"] == pytest.approx(30.0)
    assert avg.n_samples == 3
    assert avg.oxide_found and avg.mesa_found
    assert avg.oxide_filtered.tolist() == [10.0, 10.0, 10.0]
    mean = average_measurements(series, measure_type=0)
    assert mean.oxide_diameter == pytest.approx(8.95)
    assert mean.oxide_raw.tolist() == [8.9, 9.0]


def test_average_measurements_flags():
    avg = average_measurements({"mean": [math.nan, math.nan], "external": [0.0]})
    assert not avg.oxide_found
    assert math.isnan(avg.oxide_diameter)
    assert not avg.mesa_found  # external average 0
    not_mandatory = average_measurements({"mean": [9.0], "external": [0.0]}, mesa_mandatory=False)
    assert math.isnan(not_mandatory.averages["external"])
    assert not_mandatory.mesa_found  # NaN != 0, as LabVIEW's "≠"


def test_average_measurements_rejects_unknown_series():
    with pytest.raises(ValueError):
        average_measurements({"diameter": [1.0]})


# ---------------------------------------------------------------------------------------------------------------------
# Chart filter and fit window
# ---------------------------------------------------------------------------------------------------------------------


def test_filter_invalid_measures_keeps_timestamps_aligned():
    t, d = filter_invalid_measures([1, 2, 3, 4, 5], [9.0, math.nan, 0.0, 8.5, 8.4])
    assert t.tolist() == [1, 4, 5]
    assert d.tolist() == [9.0, 8.5, 8.4]
    t, d = filter_invalid_measures([1, 2, 3], [9.0, math.nan, 0.0], filter_nan=False)
    assert t.tolist() == [1, 2]
    t, d = filter_invalid_measures([1, 2, 3], [9.0, math.nan, 0.0], filter_null=False)
    assert d.tolist() == [9.0, 0.0]
    t, d = filter_invalid_measures([1, 2, 3, 4], [9.0, 8.0])  # shorter array wins (auto-indexing)
    assert t.tolist() == [1, 2]


def test_extract_fit_window():
    t = T0 + np.arange(0.0, 101.0, 10.0)  # 0, 10, ..., 100 s
    d = np.linspace(10.0, 9.0, t.size)
    w = extract_fit_window(t, d, regression_time_s=30.0, min_points=4)
    assert (w.timestamps - T0).tolist() == [70.0, 80.0, 90.0, 100.0]
    assert w.enough_points
    assert not extract_fit_window(t, d, regression_time_s=30.0, min_points=5).enough_points
    d_nan = d.copy()
    d_nan[-2] = math.nan
    w = extract_fit_window(t, d_nan, regression_time_s=30.0, min_points=4)
    assert (w.timestamps - T0).tolist() == [70.0, 80.0, 100.0]
    assert not w.enough_points
    empty = extract_fit_window([], [], regression_time_s=30.0)
    assert empty.timestamps.size == 0 and not empty.enough_points


# ---------------------------------------------------------------------------------------------------------------------
# Diameter fit (alox_tools_fit_diameters_measures)
# ---------------------------------------------------------------------------------------------------------------------


def oxidation_series(n: int = 60, slope: float = -0.001, noise: float = 0.01, seed: int = 4):
    rng = np.random.default_rng(seed)
    t = T0 + np.arange(n) * 10.0
    d = 12.0 + slope * (t - T0) + rng.normal(0.0, noise, n)
    return t, d


def test_fit_diameters_ransac_recovers_rate_despite_outliers():
    t, d = oxidation_series()
    d[[5, 17, 33, 48]] += np.array([1.5, -2.0, 0.8, -1.1])
    res = fit_diameters(t, d, seed=0)
    assert res.fit.slope == pytest.approx(-0.001, abs=2e-5)
    assert res.fit.at(T0) == pytest.approx(12.0, abs=0.01)
    assert res.fit.score >= 90.0
    assert res.fitted == pytest.approx(res.fit.at(res.timestamps))
    a, b, _ = res.line
    assert math.hypot(a, b) == pytest.approx(1.0)
    assert fit_diameters(t, d, seed=0).fit == res.fit  # reproducible


def test_fit_diameters_least_absolute_residual_without_outlier_rejection():
    t = T0 + np.arange(10) * 10.0
    d = 12.0 - 0.002 * (t - T0)
    d[3] += 5.0  # LAR ignores a single gross outlier
    res = fit_diameters(t, d, reject_outliers=False)
    assert res.fit.slope == pytest.approx(-0.002, abs=1e-9)
    assert res.fit.at(T0) == pytest.approx(12.0, abs=1e-6)
    assert res.fit.score == 0.0
    assert res.fit.residual == pytest.approx(0.5, abs=1e-6)  # mean |residual| = 5/10


def test_fit_diameters_errors_are_9004():
    with pytest.raises(RateEstimationError) as exc:
        fit_diameters([T0], [9.0])
    assert exc.value.code == FIT_LINE_ERROR
    with pytest.raises(RateEstimationError):
        fit_diameters([T0, T0, T0], [9.0, 9.1, 9.2])  # vertical in (t, d)
    t, d = oxidation_series(noise=1.0)
    with pytest.raises(RateEstimationError) as exc:
        fit_diameters(t, d, max_distance=0.001, min_score=90.0)
    assert exc.value.code == FIT_LINE_ERROR


# ---------------------------------------------------------------------------------------------------------------------
# Remaining time (alox_tools_get_mesa_remaining_time)
# ---------------------------------------------------------------------------------------------------------------------


def test_format_hms():
    assert format_hms(0) == "00:00:00"
    assert format_hms(3725) == "01:02:05"
    assert format_hms(86400) == "24:00:00"


def test_remaining_time_exact_line():
    t = T0 + np.array([0.0, 25.0, 50.0])
    fit = LinearFit(slope=-0.01, intercept=10.0 + 0.01 * T0, residual=0.0, score=100.0)  # d = 10 - 0.01 (t - T0)
    d = fit.at(t)
    res = remaining_time(t, d, fit, target_um=9.0)  # reached at T0 + 100, last sample T0 + 50
    assert res.seconds == 50.0
    assert res.text == "00:00:50"
    assert res.rate_um_per_min == pytest.approx(0.6)
    assert res.score == 100.0


def test_remaining_time_rules():
    t = T0 + np.array([0.0, 10.0])
    falling = LinearFit(-0.001, 10.0 + 0.001 * T0, 0.1, 80.0)
    assert remaining_time(t, falling.at(t), falling, target_um=11.0).seconds == 0.0  # target already passed
    slow = LinearFit(-1e-6, 10.0 + 1e-6 * T0, 0.0, 100.0)
    assert remaining_time(t, slow.at(t), slow, target_um=5.0).seconds == MAX_REMAINING_TIME_S  # max 24 hours
    rising = LinearFit(0.001, 10.0 - 0.001 * T0, 0.0, 100.0)
    res = remaining_time(t, rising.at(t), rising, target_um=9.0)
    assert (res.seconds, res.text) == (86400.0, "24:00:00")  # "positive slope (should not happen)"
    assert res.rate_um_per_min == pytest.approx(0.06)
    flat = LinearFit(0.0, 10.0, 0.0, 100.0)  # (target - b)/0 = -inf -> 0, +inf -> 86400
    assert remaining_time(t, [10.0, 10.0], flat, target_um=9.0).seconds == 0.0
    assert remaining_time(t, [10.0, 10.0], flat, target_um=11.0).seconds == MAX_REMAINING_TIME_S


def test_remaining_time_rounds_to_whole_seconds_half_even():
    t = T0 + np.array([0.0])
    fit = LinearFit(-0.1, 10.0 + 0.1 * T0, 0.0, 100.0)
    assert remaining_time(t, [10.0], fit, target_um=9.75).seconds == 2.0  # 2.5 s -> 2 (round half to even)
    assert remaining_time(t, [10.0], fit, target_um=9.65).seconds == 4.0  # 3.5 s -> 4


def test_remaining_time_score_uses_vertical_tolerance():
    t = T0 + np.arange(4) * 10.0
    fit = LinearFit(0.0 - 0.001, 10.0 + 0.001 * T0, 0.0, 0.0)
    d = fit.at(t) + np.array([0.0, 1.0, -1.5, 0.2])  # 1.0 is inside (<=), 1.5 is outside
    assert remaining_time(t, d, fit, target_um=9.0, score_tolerance_um=1.0).score == pytest.approx(75.0)
    assert remaining_time([], [], fit, target_um=9.0).score == 0.0


# ---------------------------------------------------------------------------------------------------------------------
# Whole chain
# ---------------------------------------------------------------------------------------------------------------------


def test_estimate_oxidation_nominal():
    t, d = oxidation_series(n=100, slope=-0.0005, noise=0.005)
    est = estimate_oxidation(t, d, target_um=11.5, regression_time_s=300.0)
    assert est.ok
    assert est.window.timestamps.size == 31
    assert est.remaining.rate_um_per_min == pytest.approx(0.03, abs=0.003)
    # d(t) = 12 - 0.0005 (t - T0) reaches 11.5 at T0 + 1000; last sample at T0 + 990
    assert est.remaining.seconds == pytest.approx(10.0, abs=15.0)


def test_estimate_oxidation_insufficient_points_is_9003():
    t, d = oxidation_series(n=5)
    est = estimate_oxidation(t, d, target_um=11.0, regression_time_s=600.0)
    assert not est.ok
    assert est.error.code == INSUFFICIENT_POINTS
    assert (est.remaining.seconds, est.remaining.text) == (86400.0, "?")
    assert math.isnan(est.remaining.rate_um_per_min) and math.isnan(est.remaining.score)
    assert est.fit is None


def test_estimate_oxidation_fit_error_is_9004():
    t, d = oxidation_series(n=40, noise=2.0)
    est = estimate_oxidation(t, d, target_um=11.0, regression_time_s=1000.0, max_distance=0.001, min_score=90.0)
    assert est.error is not None and est.error.code == FIT_LINE_ERROR
    assert est.remaining.text == "?"
