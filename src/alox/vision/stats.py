"""Post-processing of the mesa / oxide measurements: NaN and outlier filter, averaging, oxidation rate, remaining time.

Spec: ``docs/vision-core/map.md`` Part 4 §3.8 and Part 3 §3.11 (HL mesa ``Measure MESA``). LabVIEW VIs covered:
``tools_alox_NaN_filter``, ``tools_mesa_averaged_measurement`` (its averaging part),
``tools_filter_invalid_chart_measures`` / ``alox_tools_filter_invalid_measures``, ``alox_tool_extract_mesa_fit_data``,
``alox_tools_fit_diameters_measures``, ``alox_tools_get_mesa_remaining_time``.

Units: diameters in µm (``alox_tools_convert_mesa_measures_to_micrometers`` runs before the filter, so the 0.25 band
is in µm), timestamps in seconds (LabVIEW timestamps in the original; any epoch works, only differences matter),
oxidation rate in µm/min, remaining time in s.

Errors: ``009003`` "insufficient number of points for estimating remaining time" and ``009004`` "fit line error, can
not estimate remaining time" (``vc_text/translations/alox_en.lang``) are :class:`RateEstimationError`. The HL mesa
caller tolerates both (as read in ``alox_function_hl_mesa_measurement_state_machine d``: 9004 is cleared, and an error
is only logged once more than ``Line Reg. Time`` has elapsed since the start, "Log Insuf. #points warning?"), so
:func:`estimate_oxidation` returns them in its result instead of raising.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import IntEnum

import numpy as np
from scipy import optimize

from aloxcam.errors import AloxError

from .geometry import GeometryError, fit_line_ransac

__all__ = [
    "FIT_LINE_ERROR",
    "INSUFFICIENT_POINTS",
    "MAX_REMAINING_TIME_S",
    "SERIES",
    "AveragedMeasurement",
    "DiameterFit",
    "FitWindow",
    "LinearFit",
    "NanFilterResult",
    "OxidationEstimate",
    "OxideMeasureType",
    "RateEstimationError",
    "RemainingTime",
    "average_measurements",
    "estimate_oxidation",
    "extract_fit_window",
    "filter_invalid_measures",
    "fit_diameters",
    "format_hms",
    "nan_filter",
    "remaining_time",
    "sample_std",
]

INSUFFICIENT_POINTS = 9003
FIT_LINE_ERROR = 9004
#: "max 24 HOURS" of ``alox_tools_get_mesa_remaining_time d``.
MAX_REMAINING_TIME_S = 86_400


class RateEstimationError(AloxError):
    """``009003`` (not enough points) or ``009004`` (line fit failed)."""


# --------------------------------------------------------------------------------------------------------------------
# NaN / outlier filter and averaging
# --------------------------------------------------------------------------------------------------------------------


def sample_std(values: Sequence[float] | np.ndarray) -> float:
    """Sample standard deviation (N − 1), NI ``Std Deviation and Variance`` default weighting (inferred, NI doc).

    0 for one sample (assumption: keeps a single measurement through the filter, see :func:`nan_filter`), NaN for
    none.
    """
    x = np.asarray(values, dtype=np.float64)
    if x.size == 0:
        return math.nan
    if x.size == 1:
        return 0.0
    return float(np.std(x, ddof=1))


def _mean(x: np.ndarray) -> float:
    return float(np.mean(x)) if x.size else math.nan


def _in_range(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """``In Range and Coerce`` with both limits included; NaN limits or values → FALSE."""
    with np.errstate(invalid="ignore"):
        return (x >= lo) & (x <= hi)


@dataclass(frozen=True)
class NanFilterResult:
    """Outputs of ``tools_alox_NaN_filter`` (+ their means)."""

    nan_filtered: np.ndarray  # "NaN filter": input without NaN
    nan_filtered_mean: float  # "NAN filter mean"
    fix_filtered: np.ndarray  # "Fix Filter": second pass
    fix_filtered_mean: float  # "Fix filter mean"
    final: np.ndarray  # "Filter Final": third pass
    final_mean: float  # "Filter final mean"; NaN when empty
    raw_mean: float  # mean of the raw input (NaN as soon as one value is NaN), the "backupVal" mean node

    @property
    def n_samples(self) -> int:
        return int(self.final.size)

    @property
    def kept_percent(self) -> float:
        """``Measure keeped: %d %%`` of the oxide overlay (final / non-NaN count); NaN when nothing was measured."""
        return 100.0 * self.final.size / self.nan_filtered.size if self.nan_filtered.size else math.nan


def nan_filter(
    values: Sequence[float] | np.ndarray,
    *,
    median_band: float = 0.25,
    lower_sigmas: float = 1.0,
    upper_sigmas: float = 2.0,
    final_sigmas: float = 2.0,
    min_samples: int = 5,
    min_final_samples: int = 4,
) -> NanFilterResult:
    """Three-stage NaN / outlier filter — ``tools_alox_NaN_filter d, d3, d6, d9, d10, d11``.

    ::

        x1 = values without NaN                                              # pass 1 ("NaN filter")
        if len(x1) > 5:                                                      # d / d6
            med, s = median(x1), std(x1)
            x2 = [v in x1 if med−0.25 ≤ v ≤ med+0.25  OR  med − 1·s ≤ v ≤ med + 2·s]     # pass 2 ("Fix Filter")
            if len(x2) > 4 and round(std(x2)) != 0:                          # d10/d11 (case selector = σ₂ as I32)
                m2, s2 = mean(x2), std(x2)
                x3 = [v in x2 if isnan(s2) OR m2 − 2·s2 ≤ v ≤ m2 + 2·s2]                # pass 3 ("Filter Final")
            else:
                x3 = x2                                                      # d9 (≤ 4), d10 case "0"
        else:                                                                # d3
            m, s = mean(x1), std(x1)
            x2 = x3 = [v in x1 if m − 1·s ≤ v ≤ m + 2·s]

    As read in the diagrams (the map's summary "mean ± 2σ" is not what is wired): the second band of pass 2 is
    centred on the **median**, and both the pass-2 band and the ≤ 5-sample band are **asymmetric** (``−1σ``/``+2σ``,
    the lower limit uses σ where the upper one uses 2σ). These are keyword parameters (``lower_sigmas``,
    ``upper_sigmas``) so the owner can switch to a symmetric ±2σ band. Limits are inclusive (``In Range and Coerce``
    defaults). ``median_band`` is absolute, in the unit of the input (µm). Standard deviations: :func:`sample_std`.
    In the ≤ 5-sample case LabVIEW does not write "Fix Filter" (indicators inside the other case); here it is the same
    as ``final``.
    """
    x = np.asarray(values, dtype=np.float64).ravel()
    raw_mean = float(np.mean(x)) if x.size else math.nan
    x1 = x[~np.isnan(x)]
    if x1.size > min_samples:
        med, s = float(np.median(x1)), sample_std(x1)
        keep = _in_range(x1, med - median_band, med + median_band) | _in_range(
            x1, med - lower_sigmas * s, med + upper_sigmas * s
        )
        x2 = x1[keep]
        s2 = sample_std(x2)
        if x2.size > min_final_samples and not (math.isfinite(s2) and round(s2) == 0):
            m2 = _mean(x2)
            x3 = x2 if math.isnan(s2) else x2[_in_range(x2, m2 - final_sigmas * s2, m2 + final_sigmas * s2)]
        else:
            x3 = x2
    else:
        m, s = _mean(x1), sample_std(x1)
        x2 = x3 = x1[_in_range(x1, m - lower_sigmas * s, m + upper_sigmas * s)]
    return NanFilterResult(
        nan_filtered=x1,
        nan_filtered_mean=_mean(x1),
        fix_filtered=x2,
        fix_filtered_mean=_mean(x2),
        final=x3,
        final_mean=_mean(x3),
        raw_mean=raw_mean,
    )


class OxideMeasureType(IntEnum):
    """``Oxyd Measure Type`` selector of ``tools_mesa_averaged_measurement d``."""

    MEAN = 0
    MEDIAN = 1
    FIT = 2
    SURFACE = 3
    CIRCUMSCRIBED = 4
    INSCRIBED = 5


#: Diameter series averaged by ``tools_mesa_averaged_measurement d`` (in that order).
SERIES = ("external", "fit", "median", "mean", "surface", "inscribed", "circumscribed", "circularity")
_SELECTED = {
    OxideMeasureType.MEAN: "mean",
    OxideMeasureType.MEDIAN: "median",
    OxideMeasureType.FIT: "fit",
    OxideMeasureType.SURFACE: "surface",
    OxideMeasureType.CIRCUMSCRIBED: "circumscribed",
    OxideMeasureType.INSCRIBED: "inscribed",
}


@dataclass(frozen=True)
class AveragedMeasurement:
    """``alox_typedef_oxyd_averaged_measurement_report`` and the found flags."""

    averages: dict[str, float]  # mean of each filtered series ("Filter Final"), NaN when empty
    filters: dict[str, NanFilterResult]
    measure_type: OxideMeasureType
    oxide_diameter: float  # the series selected by measure_type ("Oxyd Diam.")
    n_samples: int  # final sample count of the selected series ("# Samples Oxyd", series inferred)
    oxide_found: bool  # n_samples > 0
    mesa_found: bool  # external average != 0 (NaN counts as found, LabVIEW "≠")

    @property
    def oxide_filtered(self) -> np.ndarray:
        """``Oxid Filtered`` of the overlay: the final values of the selected series."""
        return self.filters[_SELECTED[self.measure_type]].final

    @property
    def oxide_raw(self) -> np.ndarray:
        """``Oxid Row`` of the overlay: the selected series without NaN."""
        return self.filters[_SELECTED[self.measure_type]].nan_filtered


def average_measurements(
    series: Mapping[str, Sequence[float]],
    *,
    measure_type: OxideMeasureType | int = OxideMeasureType.MEAN,
    mesa_mandatory: bool = True,
    **filter_options: float,
) -> AveragedMeasurement:
    """Averaging part of ``tools_mesa_averaged_measurement d``: each series → :func:`nan_filter` → mean of "Filter
    Final" (NaN when empty).

    ``series`` maps the names of :data:`SERIES` to the per-image values in µm (one value per averaged image; a missing
    series counts as empty). The ``external`` series is only collected when ``measure.vcsel.MeasMesaMandatory`` is
    TRUE (``mesa_mandatory``); otherwise its average is NaN and the mesa counts as found. ``filter_options`` are passed
    to :func:`nan_filter`.
    """
    mtype = OxideMeasureType(measure_type)
    unknown = set(series) - set(SERIES)
    if unknown:
        raise ValueError(f"unknown series: {sorted(unknown)}")
    filters: dict[str, NanFilterResult] = {}
    for name in SERIES:
        values = series.get(name, ()) if (name != "external" or mesa_mandatory) else ()
        filters[name] = nan_filter(values, **filter_options)
    averages = {name: f.final_mean for name, f in filters.items()}
    selected = filters[_SELECTED[mtype]]
    return AveragedMeasurement(
        averages=averages,
        filters=filters,
        measure_type=mtype,
        oxide_diameter=averages[_SELECTED[mtype]],
        n_samples=selected.n_samples,
        oxide_found=selected.n_samples > 0,
        mesa_found=bool(averages["external"] != 0.0),
    )


def filter_invalid_measures(
    timestamps: Sequence[float] | np.ndarray,
    data: Sequence[float] | np.ndarray,
    *,
    filter_nan: bool = True,
    filter_null: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    """Drop the points whose **data** is NaN (``filter_nan``) or 0 (``filter_null``), timestamps kept aligned.

    ``tools_filter_invalid_chart_measures d`` = ``alox_tools_filter_invalid_measures d``; the loop runs over the
    shorter of the two arrays (auto-indexing), so extra elements are dropped.
    """
    t = np.asarray(timestamps, dtype=np.float64).ravel()
    d = np.asarray(data, dtype=np.float64).ravel()
    n = min(t.size, d.size)
    t, d = t[:n], d[:n]
    bad = np.zeros(n, dtype=bool)
    if filter_nan:
        bad |= np.isnan(d)
    if filter_null:
        bad |= d == 0.0
    return t[~bad], d[~bad]


# --------------------------------------------------------------------------------------------------------------------
# Oxidation rate and remaining time
# --------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FitWindow:
    """Outputs of ``alox_tool_extract_mesa_fit_data``."""

    timestamps: np.ndarray
    diameters: np.ndarray
    enough_points: bool


def extract_fit_window(
    timestamps: Sequence[float] | np.ndarray,
    diameters: Sequence[float] | np.ndarray,
    *,
    regression_time_s: float,
    min_points: int = 10,
) -> FitWindow:
    """Samples of the last ``regression_time_s`` seconds — ``alox_tool_extract_mesa_fit_data d, d7``.

    NaN and 0 diameters are dropped first; the window starts at the first sample with ``I64(t_i) ≥ I64(t_last) −
    Regression time``; ``enough_points = len(window) ≥ min_points`` (HL mesa passes ``# Min Points`` = 10 and
    ``Line Reg. Time (s)``). An empty input gives an empty window (the "not enough" error of the VI sits in a
    Disabled frame).
    """
    t, d = filter_invalid_measures(timestamps, diameters)
    if t.size == 0:
        return FitWindow(t, d, False)
    start_limit = float(np.rint(t[-1])) - float(regression_time_s)
    idx = np.flatnonzero(np.rint(t) >= start_limit)
    if idx.size == 0:
        return FitWindow(t[:0], d[:0], False)
    t, d = t[idx[0]:], d[idx[0]:]
    return FitWindow(t, d, bool(t.size >= min_points))


@dataclass(frozen=True)
class LinearFit:
    """LabVIEW ``Linear Fit`` cluster {Slope, Intercept, Residual, Fit Score(%)}; slope in µm/s."""

    slope: float
    intercept: float
    residual: float
    score: float

    def at(self, t: float | np.ndarray) -> float | np.ndarray:
        return self.slope * t + self.intercept


@dataclass(frozen=True)
class DiameterFit:
    """Outputs of ``alox_tools_fit_diameters_measures``."""

    fit: LinearFit
    line: tuple[float, float, float]  # "Line Equation" (a, b, c), a·t + b·d + c = 0 (normalised)
    timestamps: np.ndarray  # "Fitted X"
    fitted: np.ndarray  # "Fitted Y"


def _lar_fit(x: np.ndarray, y: np.ndarray) -> tuple[float, float, float]:
    """Least-absolute-residual line ``y = s·x + i`` (NI ``Linear Fit``, method LAR) as a linear programme.

    Returns ``(slope, intercept, mean |residual|)``; the NI "residue" of LAR is taken as the mean absolute residual
    (inferred).
    """
    n = x.size
    x0, xs = float(x.mean()), float(np.ptp(x)) or 1.0
    y0, ys = float(y.mean()), float(np.ptp(y)) or 1.0
    u, v = (x - x0) / xs, (y - y0) / ys
    c = np.concatenate(([0.0, 0.0], np.ones(n)))
    eye = np.eye(n)
    a_ub = np.block([[-u[:, None], -np.ones((n, 1)), -eye], [u[:, None], np.ones((n, 1)), -eye]])
    b_ub = np.concatenate((-v, v))
    bounds = [(None, None), (None, None)] + [(0, None)] * n
    res = optimize.linprog(c, A_ub=a_ub, b_ub=b_ub, bounds=bounds, method="highs")
    if not res.success:
        raise RateEstimationError(FIT_LINE_ERROR, f"line regression: {res.message}")
    su, iu = res.x[0], res.x[1]
    slope = su * ys / xs
    intercept = y0 + ys * iu - slope * x0
    return float(slope), float(intercept), float(np.mean(np.abs(y - (slope * x + intercept))))


def fit_diameters(
    timestamps: Sequence[float] | np.ndarray,
    diameters: Sequence[float] | np.ndarray,
    *,
    reject_outliers: bool = True,
    iterations: int = 1000,
    max_distance: float = 0.1,
    min_score: float = 30.0,
    seed: int | np.random.Generator | None = 0,
) -> DiameterFit:
    """Diameter-versus-time line — ``alox_tools_fit_diameters_measures d … d11``.

    NaN and 0 diameters are dropped (``tools_filter_invalid_chart_measures`` with both filters ON). With
    ``reject_outliers`` (HL mesa: TRUE, Fit Options ``{1000, 0.1, 30}``): :func:`~alox.vision.geometry.fit_line_ransac`
    (the fixed RANSAC; LabVIEW used ``geometry_fit_line_ransac2``), ``slope = −a/b``, ``intercept = −c/b``, residual
    and score from the fit; an invalid fit (score < ``min_score`` or < 2 inliers) is error 9004 "line fit". Without
    it: least-absolute-residual regression (NI ``Linear Fit``, "Least Absolute Residual"), score 0; a failure is
    error 9004 "line regression". Raises :class:`RateEstimationError` (9004); fewer than two points also give 9004.
    """
    t, d = filter_invalid_measures(timestamps, diameters)
    if t.size < 2 or np.ptp(t) == 0.0:
        raise RateEstimationError(FIT_LINE_ERROR, "line fit: fewer than two distinct timestamps")
    if reject_outliers:
        try:
            res = fit_line_ransac(
                np.column_stack((t, d)),
                max_distance=max_distance,
                iterations=iterations,
                min_score=min_score,
                seed=seed,
            )
        except GeometryError as exc:
            raise RateEstimationError(FIT_LINE_ERROR, f"line fit: {exc}") from exc
        if not res.valid:
            raise RateEstimationError(FIT_LINE_ERROR, f"line fit: score {res.score:.1f} % < {min_score} %")
        line = res.line
        if line.b == 0.0:
            raise RateEstimationError(FIT_LINE_ERROR, "line fit: vertical line (constant timestamp)")
        fit = LinearFit(line.slope, line.intercept, res.residual, res.score)
        return DiameterFit(fit, line.as_tuple(), t, np.asarray(line.y_at(t), dtype=np.float64))
    slope, intercept, residual = _lar_fit(t, d)
    norm = math.hypot(slope, 1.0)
    fit = LinearFit(slope, intercept, residual, 0.0)
    return DiameterFit(fit, (slope / norm, -1.0 / norm, intercept / norm), t, slope * t + intercept)


def format_hms(seconds: float) -> str:
    """``%02d:%02d:%02d`` of ``h = floor(t/3600)``, ``m = floor(t/60) % 60``, ``s = t % 60`` (U32 formula node of
    ``alox_tools_get_mesa_remaining_time d``)."""
    t = int(seconds)
    return f"{t // 3600:02d}:{(t // 60) % 60:02d}:{t % 60:02d}"


@dataclass(frozen=True)
class RemainingTime:
    """Outputs of ``alox_tools_get_mesa_remaining_time``."""

    seconds: float  # "Remain Time (s)", whole seconds in [0, 86400]
    text: str  # "Remain Time String" HH:MM:SS ("?" in the error case)
    rate_um_per_min: float  # "Oxyd Rate (µm/min)" = |slope·60|
    residual: float  # the fit residual, passed through
    score: float  # % of window points within the score tolerance of the line (vertical distance)


#: Error case of ``alox_tools_get_mesa_remaining_time d6``.
REMAINING_TIME_ERROR = RemainingTime(float(MAX_REMAINING_TIME_S), "?", math.nan, math.nan, math.nan)


def _to_i64_seconds(value: float) -> int:
    """LabVIEW ``To I64`` (round half to even), +inf → max, −inf → min; NaN mapped to +max (not established)."""
    if math.isnan(value) or value == math.inf:
        return 2**63 - 1
    if value == -math.inf:
        return -(2**63)
    return int(np.rint(value))


def remaining_time(
    timestamps: Sequence[float] | np.ndarray,
    diameters: Sequence[float] | np.ndarray,
    fit: LinearFit,
    *,
    target_um: float,
    score_tolerance_um: float = 1.0,
) -> RemainingTime:
    """Remaining time to reach ``target_um`` and oxidation rate — ``alox_tools_get_mesa_remaining_time d … d5``.

    ``Mesures`` = the fit window (row 0 timestamps, row 1 diameters). Slope > 0 ("Positive slope, should not
    happen") → 86 400 s. Otherwise ``target time = (target − intercept)/slope``, ``remaining = I64(target time −
    last timestamp)``, 0 if negative, at most 86 400 ("max 24 HOURS"). ``rate = |slope·60|`` µm/min. ``score`` = % of
    the points whose vertical distance ``|d_i − (slope·t_i + intercept)|`` is ≤ ``score_tolerance_um`` (HL mesa: 1;
    0 for no point). The ``residual`` is the fit's, passed through.
    """
    t = np.asarray(timestamps, dtype=np.float64).ravel()
    d = np.asarray(diameters, dtype=np.float64).ravel()
    n = min(t.size, d.size)
    t, d = t[:n], d[:n]
    if fit.slope > 0.0:
        seconds = MAX_REMAINING_TIME_S
    else:
        last = float(t[-1]) if n else 0.0
        with np.errstate(divide="ignore", invalid="ignore"):
            target_time = np.float64(target_um - fit.intercept) / np.float64(fit.slope)
        remaining = _to_i64_seconds(float(target_time) - last)
        seconds = min(max(remaining, 0), MAX_REMAINING_TIME_S)
    with np.errstate(invalid="ignore"):
        dist = np.abs(d - (fit.slope * t + fit.intercept))
    score = 100.0 * np.count_nonzero(~(dist > score_tolerance_um)) / n if n else 0.0
    return RemainingTime(
        seconds=float(seconds),
        text=format_hms(seconds),
        rate_um_per_min=abs(fit.slope * 60.0),
        residual=fit.residual,
        score=float(score),
    )


@dataclass(frozen=True)
class OxidationEstimate:
    """Result of :func:`estimate_oxidation`; on error ``remaining`` is the LabVIEW error case (86 400 s, "?")."""

    window: FitWindow
    remaining: RemainingTime
    fit: DiameterFit | None = None
    error: RateEstimationError | None = field(default=None)

    @property
    def ok(self) -> bool:
        return self.error is None


def estimate_oxidation(
    timestamps: Sequence[float] | np.ndarray,
    diameters: Sequence[float] | np.ndarray,
    *,
    target_um: float,
    regression_time_s: float,
    min_points: int = 10,
    reject_outliers: bool = True,
    iterations: int = 1000,
    max_distance: float = 0.1,
    min_score: float = 30.0,
    score_tolerance_um: float = 1.0,
    seed: int | np.random.Generator | None = 0,
) -> OxidationEstimate:
    """"Compute Oxydation Rate & remaining time" of the HL mesa ``Measure MESA`` state
    (``alox_function_hl_mesa_measurement_state_machine d``): window → fit → remaining time.

    Defaults are the constants wired there (``# Min Points`` 10, Fit Options {1000 iterations, max distance 0.1,
    min score 30 %}, Reject Outliers TRUE, score tolerance 1 µm). Fewer than ``min_points`` samples in the window →
    error 9003 (no fit attempted; LabVIEW computes the "Enough Points?" flag but its own error is disabled); a failed
    fit → 9004. Both are returned in ``error`` with the LabVIEW error-case outputs, never raised.
    """
    window = extract_fit_window(timestamps, diameters, regression_time_s=regression_time_s, min_points=min_points)
    if not window.enough_points:
        err = RateEstimationError(
            INSUFFICIENT_POINTS,
            f"insufficient number of points for estimating remaining time ({window.timestamps.size} < {min_points})",
        )
        return OxidationEstimate(window, REMAINING_TIME_ERROR, None, err)
    try:
        dfit = fit_diameters(
            window.timestamps,
            window.diameters,
            reject_outliers=reject_outliers,
            iterations=iterations,
            max_distance=max_distance,
            min_score=min_score,
            seed=seed,
        )
    except RateEstimationError as err:
        return OxidationEstimate(window, REMAINING_TIME_ERROR, None, err)
    rem = remaining_time(
        window.timestamps, window.diameters, dfit.fit, target_um=target_um, score_tolerance_um=score_tolerance_um
    )
    return OxidationEstimate(window, rem, dfit, None)
