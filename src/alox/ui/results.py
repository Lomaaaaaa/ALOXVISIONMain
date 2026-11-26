"""The results of the measurement functions shown in the LOW-LEVEL / HIGH-LEVEL MEASUREMENTS tabs (Qt-free).

LabVIEW: the state machines send ``Function UI Event`` user events ("MESA Measured", "BOW Measured", "TILT
Measured", the focus points ...) to the run panels ``alox_function_<ll|hl>_<x>_ui_run.vi`` that
``alox_manager_ll_results_panels`` / ``alox_manager_hl_results_panels`` open in the two tabs (map Part 3 §2.1). Here
the functions publish the same data with ``ctx.set_state(state, **data)`` (bus events ``function.<name>.state``) and
end with ``function.<name>.finished`` / ``failed`` / ``stopped``; the runner's ``last_result(name)`` is the report.

:class:`ResultsBook` takes these bus events (UI thread, through the window's queued bridge) and keeps one model per
function with a panel: :class:`MesaResults`, :class:`BowingResults`, :class:`TiltResults` (high level),
:class:`FocusResults` (Autofocus, Refocus), :class:`WavelengthResults`, :class:`ExposureResults`,
:class:`CircleResults` (Detect Circle) and :class:`OxydResults` (Measure Oxyd) (low level). The panels
(:mod:`alox.ui.results_panels`) only draw what the models hold; the texts are formatted here.

A model is cleared when its function starts (``started``) or when a new run sends its first state after a finished
one (the low-level functions run inside a high-level one send no ``started``).
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from alox.core.functions import FunctionName
from alox.functions.bowing import POINT_FIELDS
from alox.functions.bowing import progress as bowing_progress
from alox.vision.wafer import BOWING_ROWS

__all__ = [
    "HIGH",
    "LOW",
    "CircleResults",
    "ExposureResults",
    "FocusResults",
    "MesaResults",
    "OxydResults",
    "Results",
    "ResultsBook",
    "TiltResults",
    "Validity",
    "WavelengthResults",
    "fmt",
    "fmt_duration",
    "fmt_time",
    "get",
]

HIGH, LOW = "high", "low"
DASH = "—"
#: The states that end a run (every state machine).
TERMINAL_STATES = frozenset({"finished", "error", "stopped"})


# ================================================================================================== helpers
def get(obj: Any, name: str, default: Any = None) -> Any:
    """The field ``name`` of a report: a dataclass, an object or a mapping (the events carry either)."""
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def num(value: Any) -> float:
    """``value`` as a float, NaN when it is missing or not a number."""
    if value is None or isinstance(value, bool):
        return math.nan if value is None else float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def finite(value: Any) -> bool:
    return math.isfinite(num(value))


def fmt(value: Any, decimals: int = 2, unit: str = "") -> str:
    """A number for a field: ``decimals`` digits, "—" when it is missing / NaN."""
    v = num(value)
    if not math.isfinite(v):
        return DASH
    return f"{v:.{decimals}f}" + (f" {unit}" if unit else "")


def fmt_duration(seconds: Any) -> str:
    """``HH:MM:SS`` (LabVIEW "Remain Time String"), "—" when unknown."""
    s = num(seconds)
    if not math.isfinite(s) or s < 0:
        return DASH
    s = round(s)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def fmt_time(unix_s: float) -> str:
    """A time of day (local) for the time axes."""
    return time.strftime("%H:%M:%S", time.localtime(unix_s))


def _points(rows: Any) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for row in rows or ():
        try:
            x, y = num(row[0]), num(row[1])
        except (TypeError, IndexError):
            continue
        if math.isfinite(x) and math.isfinite(y):
            out.append((x, y))
    return out


class Validity(StrEnum):
    """A validity lamp: valid (green), invalid (red), not measured yet (neutral)."""

    VALID = "VALID"
    INVALID = "INVALID"
    UNKNOWN = DASH

    @classmethod
    def of(cls, flag: Any, measured: bool = True) -> Validity:
        if not measured or flag is None:
            return cls.UNKNOWN
        return cls.VALID if bool(flag) else cls.INVALID


# ================================================================================================== base
class Results:
    """The run of one function: job, state, status code, outcome; subclasses add the measurements."""

    title = ""

    def __init__(self, function: FunctionName) -> None:
        self.function = function
        self.revision = 0
        self.reset()

    def reset(self) -> None:
        self.job: Any = None
        self.state = ""
        self.status: int | None = None
        self.outcome = ""                  # "running", "finished", "failed", "stopped"
        self.error = ""
        self.done = False
        self.clear()

    def clear(self) -> None:
        """Forget the measurements (subclasses)."""

    # ---------------------------------------------------------------------------------------------- events
    def started(self, data: Mapping[str, Any]) -> None:
        self.reset()
        self.outcome = "running"
        self._common(data)

    def on_state(self, data: Mapping[str, Any]) -> None:
        if self.done:
            self.reset()
        if not self.outcome:
            self.outcome = "running"
        self._common(data)
        state = str(data.get("state", "") or "")
        if state:
            self.state = state
        if "error" in data and data.get("error"):
            self.error = str(data["error"])
        self.take(data)
        if state.lower() in TERMINAL_STATES:
            self.done = True
            if state.lower() == "error":
                self.outcome = "failed"
            elif state.lower() == "stopped":
                self.outcome = "stopped"

    def ended(self, kind: str, data: Mapping[str, Any], result: Any = None) -> None:
        self._common(data)
        self.done = True
        self.outcome = kind
        if kind == "failed":
            self.error = str(data.get("error", "") or self.error)
        if result is not None:
            self.take_result(result)

    def _common(self, data: Mapping[str, Any]) -> None:
        self.revision += 1
        if data.get("job") is not None:
            self.job = data["job"]
        if "status" in data and data["status"] is not None:
            try:
                self.status = int(data["status"])
            except (TypeError, ValueError):
                pass

    def take(self, data: Mapping[str, Any]) -> None:
        """The data of a ``state`` event (subclasses)."""

    def take_result(self, result: Any) -> None:
        """The runner's ``last_result`` after ``finished`` (subclasses)."""

    # ---------------------------------------------------------------------------------------------- texts
    def job_fields(self) -> list[tuple[str, str]]:
        """SEMI SECS/GEM informations of the run panels."""
        return [("PROCESS JOB", str(get(self.job, "process_job_id", "") or DASH)),
                ("LOT", str(get(self.job, "lot_id", "") or DASH)),
                ("WAFER", str(get(self.job, "wafer_id", "") or DASH)),
                ("ZONE", str(get(self.job, "zone_id", "") or DASH))]

    def state_text(self) -> str:
        text = self.state or DASH
        if self.outcome == "failed" and self.error:
            text += f" ({self.error})"
        return text

    def badge(self) -> str:
        """The badge key of the outcome (:data:`alox.ui.theme.STATUS_KEYS` values)."""
        return {"running": "running", "finished": "finished", "failed": "error", "stopped": "off"}.get(
            self.outcome, "off-ok")

    def badge_text(self) -> str:
        return {"running": "RUNNING", "finished": "FINISHED", "failed": "ERROR", "stopped": "STOPPED"}.get(
            self.outcome, "IDLE")

    @property
    def empty(self) -> bool:
        return not self.outcome


# ================================================================================================== high level
class MesaResults(Results):
    """MESA MEASUREMENT: the oxide diameter over time (``samples``), the fit window's line, the rate (µm/min), the
    remaining time to the target, the external (mesa) diameter, the wavelength and the position."""

    title = "MESA MEASUREMENT"

    def clear(self) -> None:
        self.samples: list[tuple[float, float]] = []
        self.fitted: list[tuple[float, float]] = []
        self.slope = math.nan                       # µm/s
        self.intercept = math.nan
        self.rate = math.nan                        # µm/min
        self.remaining_s = math.nan
        self.remaining = ""
        self.external = math.nan
        self.oxyd = math.nan
        self.target = math.nan
        self.wavelength = math.nan
        self.position: tuple[float, float, float] | None = None
        self.found: bool | None = None
        self.rate_error: int | None = None
        self.timestamp = math.nan

    def take(self, data: Mapping[str, Any]) -> None:
        report = data.get("report")
        if report is not None and data.get("event") in ("MESA Measured", "MESA Finished", None):
            self.take_result(report)

    def take_result(self, report: Any) -> None:
        if report is None or get(report, "timestamp") is None:
            return
        self.timestamp = num(get(report, "timestamp"))
        self.oxyd = num(get(report, "oxyd_diameter_um"))
        samples = _points(get(report, "samples", ()))
        if samples:
            self.samples = samples
        elif finite(self.timestamp) and finite(self.oxyd) and (self.timestamp, self.oxyd) not in self.samples:
            self.samples.append((self.timestamp, self.oxyd))
        self.fitted = _points(get(report, "fitted", ()))
        fit = get(report, "linear_fit")
        self.slope, self.intercept = num(get(fit, "slope")), num(get(fit, "intercept"))
        self.rate = num(get(report, "rate_um_per_min"))
        self.remaining_s = num(get(report, "remaining_s"))
        self.remaining = str(get(report, "remaining_text", "") or "")
        self.external = num(get(report, "external_fit_um"))
        self.target = num(get(report, "oxyd_target_um"))
        self.wavelength = num(get(report, "wavelength_nm"))
        position = get(report, "position")
        if position is not None and len(position) >= 2:
            self.position = tuple(num(v) for v in (*position, math.nan)[:3])          # type: ignore[assignment]
        found = get(report, "found")
        self.found = None if found is None else bool(found)
        error = get(report, "rate_error")
        self.rate_error = None if error is None else int(error)

    # ---------------------------------------------------------------------------------------------- chart data
    def fit_line(self) -> list[tuple[float, float]]:
        """The rate line over the fit window (its two ends), else over the samples; [] without a fit."""
        xs = [t for t, _ in self.fitted] or [t for t, _ in self.samples]
        if not xs:
            return []
        if finite(self.slope) and finite(self.intercept):
            lo, hi = min(xs), max(xs)
            return [(lo, self.slope * lo + self.intercept), (hi, self.slope * hi + self.intercept)]
        return list(self.fitted)

    def rate_text(self) -> str:
        return fmt(self.rate, 3)

    def remaining_text(self) -> str:
        if self.remaining and self.remaining != "?":
            return self.remaining
        if self.remaining == "?" or self.rate_error is not None:
            return "?"
        return fmt_duration(self.remaining_s)

    def position_text(self) -> str:
        if self.position is None:
            return DASH
        x, y, z = self.position
        text = f"X {fmt(x, 3)}  Y {fmt(y, 3)}"
        return text + (f"  Z {fmt(z, 3)}" if finite(z) else "")

    def fields(self) -> list[tuple[str, str]]:
        """The value fields of the panel (caption, text)."""
        return [("OXID DIAMETER (µm)", fmt(self.oxyd, 3)), ("MESA DIAMETER (µm)", fmt(self.external, 3)),
                ("OXID RATE (µm/min)", self.rate_text()), ("REMAINING TIME", self.remaining_text()),
                ("TARGET (µm)", fmt(self.target, 3) if num(self.target) > 0 else DASH),
                ("WAVELENGTH (nm)", fmt(self.wavelength, 0)),
                ("POSITION (mm)", self.position_text()), ("MEASURES", str(len(self.samples)))]


class BowingResults(Results):
    """BOWING MEASUREMENT: the nine Z (mm) of the wafer map, Bowing A/B and Parallelism A/B (µm) with their
    validity, the point being measured and the progress."""

    title = "BOWING MEASUREMENT"
    ROWS: tuple[str, ...] = BOWING_ROWS
    FIELDS: tuple[str, ...] = POINT_FIELDS

    def clear(self) -> None:
        self.values: Any = None
        self.index: int | None = None
        self.measured = 0
        self.progress = 0.0
        self.positions: tuple[tuple[float, float], ...] = ()

    def take(self, data: Mapping[str, Any]) -> None:
        if data.get("values") is not None:
            self.values = data["values"]
        if data.get("index") is not None:
            self.index = int(data["index"])
        if data.get("measured") is not None:
            self.measured = int(data["measured"])
            self.progress = bowing_progress(self.measured)
        if data.get("progress") is not None:
            self.progress = num(data["progress"])

    def take_result(self, report: Any) -> None:
        values = get(report, "values")
        if values is not None:
            self.values = values
        positions = get(report, "positions", ())
        if positions:
            self.positions = tuple((num(p[0]), num(p[1])) for p in positions)
        self.measured = sum(1 for _row, _f, z in self.points() if finite(z))
        self.progress = bowing_progress(self.measured)
        self.index = None

    def points(self) -> list[tuple[str, str, float]]:
        """The nine points in ``Z Measures`` order: (row name ``ZW2``…, field, Z mm or NaN)."""
        return [(row, name, num(get(self.values, name))) for row, name in zip(self.ROWS, self.FIELDS, strict=True)]

    def current_row(self) -> str | None:
        if self.done or self.index is None or not 0 <= self.index < len(self.ROWS):
            return None
        return self.ROWS[self.index]

    def result(self, axis: str) -> tuple[float, float, Validity, Validity]:
        """Axis ``a`` / ``b``: (bowing µm, parallelism µm, bowing validity, parallelism validity)."""
        bowing = num(get(self.values, f"bowing_{axis}_um"))
        parallelism = num(get(self.values, f"parallelism_{axis}_um"))
        return (bowing, parallelism, Validity.of(get(self.values, f"bowing_valid_{axis}"), finite(bowing)),
                Validity.of(get(self.values, f"parallelism_valid_{axis}"), finite(parallelism)))


TILT_METHODS = {0: "Straight Edge", 1: "Dual Objects", 2: "Long Baseline Edge", 3: "Lattice"}


class TiltResults(Results):
    """TILT MEASUREMENT: the angle (°), the method, the uncertainty (1σ, Long Baseline / Lattice), the validity, the
    position and the objects found (Dual Objects)."""

    title = "TILT MEASUREMENT"

    def clear(self) -> None:
        self.angle = math.nan
        self.valid: bool | None = None
        self.uncertainty = math.nan
        self.method: str = ""
        self.baseline_mm = math.nan
        self.image_angle = math.nan
        self.current_xy: tuple[float, float] | None = None
        self.index: int | None = None
        self.positions = 0
        self.objects: tuple[bool | None, bool | None] = (None, None)
        self.lost = ""

    def take(self, data: Mapping[str, Any]) -> None:
        if data.get("current_xy") is not None:
            xy = data["current_xy"]
            self.current_xy = (num(xy[0]), num(xy[1]))
        if data.get("index") is not None:
            self.index = int(data["index"])
        if data.get("positions") is not None:
            self.positions = int(data["positions"])
        if data.get("event") in ("TILT Measured", "TILT Finished"):
            self.angle = num(data.get("angle"))
            self.valid = bool(data.get("valid")) if data.get("valid") is not None else self.valid
            if "uncertainty" in data:
                self.uncertainty = num(data["uncertainty"])
                self.method = self.method or "Long baseline"
            if "baseline_mm" in data:
                self.baseline_mm = num(data["baseline_mm"])
            if "image_angle" in data:
                self.image_angle = num(data["image_angle"])
            if "object1_found" in data:
                self.objects = (bool(data["object1_found"]), bool(data.get("object2_found")))
                self.method = self.method or TILT_METHODS[1]
            if data.get("lost"):
                self.lost = str(data["lost"])

    def take_result(self, report: Any) -> None:
        method = get(report, "method")
        if method is not None:
            try:
                self.method = TILT_METHODS.get(int(method), str(method))
            except (TypeError, ValueError):
                self.method = str(method)
        self.angle = num(get(report, "angle_deg", self.angle))
        self.valid = bool(get(report, "valid", self.valid))
        self.uncertainty = num(get(report, "uncertainty_deg", self.uncertainty))
        baseline = get(report, "baseline")
        fit = get(baseline, "fit")
        if fit is not None:
            self.baseline_mm = num(get(fit, "length", self.baseline_mm))
        if baseline is not None:
            self.image_angle = num(get(baseline, "image_angle_deg", self.image_angle))
        xy = get(report, "current_xy")
        if xy is not None:
            self.current_xy = (num(xy[0]), num(xy[1]))
        if get(report, "method") == 1:
            self.objects = (bool(get(report, "object1_found")), bool(get(report, "object2_found")))

    def validity(self) -> Validity:
        return Validity.of(self.valid, self.valid is not None or finite(self.angle))

    def fields(self) -> list[tuple[str, str]]:
        xy = self.current_xy
        out = [("TILT ANGLE (°)", fmt(self.angle, 4)), ("METHOD", self.method or DASH),
               ("UNCERTAINTY 1σ (°)", fmt(self.uncertainty, 4)),
               ("BASELINE (mm)", fmt(self.baseline_mm, 3)),
               ("IMAGE ANGLE (°)", fmt(self.image_angle, 4)),
               ("POSITION (mm)", DASH if xy is None else f"X {fmt(xy[0], 3)}  Y {fmt(xy[1], 3)}")]
        if self.objects != (None, None):
            out.append(("OBJECTS FOUND", " / ".join("yes" if f else "no" for f in self.objects)))
        if self.lost:
            out.append(("LOST", self.lost))
        return out


# ================================================================================================== low level
class FocusResults(Results):
    """AUTOFOCUS / REFOCUS: the Z (mm) / score points, the best Z and score, the progress."""

    def __init__(self, function: FunctionName) -> None:
        self.title = "REFOCUS" if function is FunctionName.REFOCUS else "AUTOFOCUS"
        super().__init__(function)

    def clear(self) -> None:
        self.rows: dict[int, tuple[float, float]] = {}
        self.progress = 0.0
        self.best_z = math.nan
        self.best_score = math.nan
        self.position = math.nan

    def take(self, data: Mapping[str, Any]) -> None:
        if data.get("progress") is not None:
            self.progress = num(data["progress"])
        if data.get("z") is not None and data.get("score") is not None:
            index = data.get("index")
            index = len(self.rows) if index is None else int(index)
            self.rows[index] = (num(data["z"]), num(data["score"]))

    def take_result(self, result: Any) -> None:
        table = _points(get(result, "table", ()))
        if table:
            self.rows = dict(enumerate(table))
        self.best_z = num(get(result, "best_z"))
        self.best_score = num(get(result, "best_score"))
        self.position = num(get(result, "position"))
        self.progress = num(get(result, "progress", self.progress))

    def curve(self) -> list[tuple[float, float]]:
        """The measured points by Z."""
        return sorted(p for p in self.rows.values() if finite(p[0]) and finite(p[1]))

    def best(self) -> tuple[float, float] | None:
        """The best (Z, score): the result's, else the first maximum of the points."""
        if finite(self.best_z):
            return self.best_z, self.best_score
        best: tuple[float, float] | None = None
        for _i, (z, score) in sorted(self.rows.items()):
            if finite(score) and (best is None or score > best[1]):
                best = (z, score)
        return best

    def found(self) -> Validity:
        if self.outcome == "finished":
            return Validity.of(self.best() is not None)
        return Validity.INVALID if self.outcome == "failed" else Validity.UNKNOWN

    def fields(self) -> list[tuple[str, str]]:
        best = self.best()
        return [("FOCUS POS. (mm)", fmt(None if best is None else best[0], 4)),
                ("FOCUS SCORE", DASH if best is None else f"{best[1]:.4g}"),
                ("POINTS", str(len(self.curve())))]


class WavelengthResults(Results):
    """SEARCH WAVELENGTH: the spectrum (NOX, OX intensities and the contrast per wavelength), the optimum, the
    current wavelength and exposure, the minimum contrast."""

    title = "SEARCH WAVELENGTH"

    def clear(self) -> None:
        self.spectrum: list[tuple[float, float, float, float, float]] = []
        self.current_wl = math.nan
        self.current_expo = math.nan
        self.current_contrast = math.nan
        self.optimal_wl = math.nan
        self.optimal_contrast = math.nan
        self.optimal_expo = math.nan
        self.min_contrast = math.nan
        self.valid: bool | None = None
        self.progress = 0.0

    def take(self, data: Mapping[str, Any]) -> None:
        if data.get("progress") is not None:
            self.progress = num(data["progress"])
        if data.get("wl_nm") is not None:
            self.current_wl = num(data["wl_nm"])
        if data.get("report") is not None:
            self.take_result(data["report"])

    def take_result(self, report: Any) -> None:
        spectrum = []
        for p in get(report, "spectrum", ()) or ():
            row = tuple(num(get(p, k)) for k in ("wl_nm", "nox", "ox", "contrast", "exposure_us"))
            if finite(row[0]):
                spectrum.append(row)
        if spectrum:
            self.spectrum = sorted(spectrum)                              # type: ignore[arg-type]
        for attr, key in (("current_wl", "current_wl_nm"), ("current_expo", "current_expo_us"),
                          ("current_contrast", "current_contrast"), ("optimal_wl", "optimal_wl_nm"),
                          ("optimal_contrast", "optimal_contrast"), ("optimal_expo", "optimal_expo_us"),
                          ("min_contrast", "min_contrast"), ("progress", "progress")):
            value = get(report, key)
            if value is not None:
                setattr(self, attr, num(value))
        if get(report, "valid") is not None:
            self.valid = bool(get(report, "valid"))

    def series(self) -> dict[str, list[tuple[float, float]]]:
        """NON OXID, OXID, CONTRAST against the wavelength."""
        return {name: [(p[0], p[i]) for p in self.spectrum if finite(p[i])]
                for name, i in (("NON OXID", 1), ("OXID", 2), ("CONTRAST", 3))}

    def optimum(self) -> tuple[float, float] | None:
        return (self.optimal_wl, self.optimal_contrast) if num(self.optimal_wl) > 0 else None

    def validity(self) -> Validity:
        return Validity.of(self.valid, self.valid is not None)

    def fields(self) -> list[tuple[str, str]]:
        return [("CURRENT WL (nm)", fmt(self.current_wl, 0)), ("CURRENT EXPOSURE (µs)", fmt(self.current_expo, 0)),
                ("CONTRAST", fmt(self.current_contrast, 2)), ("MIN. CONTRAST", fmt(self.min_contrast, 2)),
                ("OPTIMAL WL (nm)", fmt(self.optimal_wl, 0) if num(self.optimal_wl) > 0 else DASH),
                ("OPTIMAL CONTRAST", fmt(self.optimal_contrast, 2) if self.optimum() else DASH)]


class ExposureResults(Results):
    """AUTO EXPOSURE: the two measures (exposure µs, brightness), the resulting exposure and brightness, the
    wavelengths."""

    title = "AUTO EXPOSURE"

    def clear(self) -> None:
        self.measures: dict[int, tuple[float, float]] = {}
        self.exposure = math.nan
        self.brightness = math.nan
        self.found: bool | None = None
        self.wl = math.nan
        self.working_wl = math.nan
        self.process_ms = math.nan

    def take(self, data: Mapping[str, Any]) -> None:
        if data.get("wl_nm") is not None:
            self.wl = num(data["wl_nm"])
        if data.get("measure") is not None and data.get("exposure") is not None:
            self.measures[int(data["measure"])] = (num(data["exposure"]), num(data.get("brightness")))
        if data.get("report") is not None:
            self.take_result(data["report"])

    def take_result(self, report: Any) -> None:
        for i in (1, 2):
            e, b = num(get(report, f"exposure{i}")), num(get(report, f"brightness{i}"))
            if e > 0 and finite(b):
                self.measures[i] = (e, b)
        found = get(report, "found_exposure")
        self.found = None if found is None else bool(found)
        if self.found or num(get(report, "exposure")) > 0:
            self.exposure = num(get(report, "exposure"))
            self.brightness = num(get(report, "brightness"))
        for attr, key in (("wl", "wl_nm"), ("working_wl", "working_wl_nm"), ("process_ms", "process_time_ms")):
            if num(get(report, key)) > 0:
                setattr(self, attr, num(get(report, key)))

    def points(self) -> list[tuple[float, float]]:
        return [self.measures[i] for i in sorted(self.measures) if finite(self.measures[i][0])]

    def result_point(self) -> tuple[float, float] | None:
        return (self.exposure, self.brightness) if finite(self.exposure) and finite(self.brightness) else None

    def line(self) -> list[tuple[float, float]]:
        """The line through the two measures, extended to the resulting exposure."""
        pts = self.points()
        if len(pts) < 2 or pts[0][0] == pts[1][0]:
            return []
        (x1, y1), (x2, y2) = pts[0], pts[1]
        slope = (y2 - y1) / (x2 - x1)
        xs = [x1, x2] + ([self.exposure] if finite(self.exposure) else [])
        lo, hi = min(xs), max(xs)
        return [(lo, y1 + slope * (lo - x1)), (hi, y1 + slope * (hi - x1))]

    def validity(self) -> Validity:
        return Validity.of(self.found, self.found is not None)

    def fields(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for i in (1, 2):
            e, b = self.measures.get(i, (math.nan, math.nan))
            out += [(f"MEASURE {i} EXPOSURE (µs)", fmt(e, 0)), (f"MEASURE {i} BRIGHTNESS", fmt(b, 1))]
        out += [("EXPOSURE (µs)", fmt(self.exposure, 0)), ("BRIGHTNESS", fmt(self.brightness, 1)),
                ("WAVELENGTH (nm)", fmt(self.wl, 0)), ("WORKING WL (nm)", fmt(self.working_wl, 0))]
        return out


@dataclass(frozen=True)
class TableRow:
    """One row of a diameters table: the label and its cells."""

    label: str
    cells: tuple[str, ...]
    highlight: bool = False


class CircleResults(Results):
    """DETECT CIRCLE: the circles found (centre, diameter in pixels and µm, score), the best one highlighted."""

    title = "DETECT CIRCLE"
    COLUMNS = ("X (px)", "Y (px)", "DIAMETER (px)", "DIAMETER (µm)", "SCORE")

    def clear(self) -> None:
        self.found: bool | None = None
        self.best: tuple[float, float, float] | None = None
        self.circles: list[tuple[float, float, float, float]] = []
        self.best_index = -1
        self.count = 0
        self.pixel_size = math.nan
        self.process_ms = math.nan

    def take(self, data: Mapping[str, Any]) -> None:
        if "found" in data:
            self.found = bool(data["found"])
        circle = data.get("best_circle")
        if circle is not None:
            self.best = (num(get(circle, "x")), num(get(circle, "y")), num(get(circle, "radius")))
        if data.get("count") is not None:
            self.count = int(data["count"])

    def take_result(self, report: Any) -> None:
        self.found = bool(get(report, "found", self.found))
        circle = get(report, "best_circle")
        if circle is not None:
            self.best = (num(get(circle, "x")), num(get(circle, "y")), num(get(circle, "radius")))
        self.circles = [(num(get(c, "x")), num(get(c, "y")), num(get(c, "radius")), num(get(c, "score")))
                        for c in get(report, "all_circles", ()) or ()]
        self.count = len(self.circles) or self.count
        self.best_index = int(get(report, "best_index", -1))
        self.pixel_size = num(get(report, "pixel_size_um"))
        self.process_ms = num(get(report, "process_time_ms"))

    def rows(self) -> list[TableRow]:
        k = self.pixel_size
        circles = self.circles or ([(*self.best, math.nan)] if self.best is not None else [])
        out = []
        for i, (x, y, r, score) in enumerate(circles):
            best = i == self.best_index or (not self.circles and self.best is not None)
            out.append(TableRow(f"#{i + 1}", (fmt(x, 1), fmt(y, 1), fmt(2 * r, 2),
                                              fmt(2 * r * k, 3) if finite(k) else DASH, fmt(score, 0)), best))
        return out

    def validity(self) -> Validity:
        return Validity.of(self.found, self.found is not None)


class OxydResults(Results):
    """MEASURE OXYD (Detect Oxid Mesa): the diameters (pixels and µm), the ratio circumscribed / inscribed, the
    standard deviation and accuracy, mesa / oxide found."""

    title = "MEASURE OXYD"
    COLUMNS = ("PIXELS", "µm")
    DIAMETERS = (("EXTERNAL", "external"), ("FIT", "fit"), ("MEDIAN", "median"), ("MEAN", "mean"),
                 ("SURFACE", "surface"), ("INSCRIBED", "inscribed"), ("CIRCUMSCRIBED", "circumscribed"))

    def clear(self) -> None:
        self.diameters: Any = None
        self.mesa_found: bool | None = None
        self.oxyd_found: bool | None = None
        self.pixel_size = math.nan
        self.fit_score = math.nan
        self.process_ms = math.nan

    def take(self, data: Mapping[str, Any]) -> None:
        if data.get("diameters") is not None:
            self.diameters = data["diameters"]
        if "mesa_found" in data:
            self.mesa_found = bool(data["mesa_found"])
        if "oxyd_found" in data:
            self.oxyd_found = bool(data["oxyd_found"])

    def take_result(self, report: Any) -> None:
        if get(report, "diameters") is not None:
            self.diameters = get(report, "diameters")
        self.mesa_found = bool(get(report, "mesa_found", self.mesa_found))
        self.oxyd_found = bool(get(report, "oxyd_found", self.oxyd_found))
        self.pixel_size = num(get(report, "pixel_size_um"))
        self.fit_score = num(get(report, "fit_score"))
        self.process_ms = num(get(report, "process_time_ms"))

    def rows(self) -> list[TableRow]:
        k = self.pixel_size
        out = []
        for label, key in self.DIAMETERS:
            px = num(get(self.diameters, key))
            out.append(TableRow(label, (fmt(px, 2), fmt(px * k, 3) if finite(k) else DASH), key == "fit"))
        return out

    def fields(self) -> list[tuple[str, str]]:
        return [("CIRC. / INSC. RATIO", fmt(get(self.diameters, "circ_insc_ratio"), 3)),
                ("STD. DEV.", fmt(get(self.diameters, "stddev"), 3)),
                ("ACCURACY", fmt(get(self.diameters, "accuracy"), 3)),
                ("PIXEL SIZE (µm)", fmt(self.pixel_size, 4)), ("FIT SCORE", fmt(self.fit_score, 1))]

    def found(self) -> tuple[Validity, Validity]:
        return (Validity.of(self.mesa_found, self.mesa_found is not None),
                Validity.of(self.oxyd_found, self.oxyd_found is not None))


# ================================================================================================== the book
MODELS: dict[FunctionName, Callable[[FunctionName], Results]] = {
    FunctionName.MESA_MEASURE: MesaResults,
    FunctionName.BOWING_MEASURE: BowingResults,
    FunctionName.TILT_MEASURE: TiltResults,
    FunctionName.AUTOFOCUS_LARGE: FocusResults,
    FunctionName.REFOCUS: FocusResults,
    FunctionName.SEARCH_WAVELENGTH: WavelengthResults,
    FunctionName.AUTO_EXPOSURE: ExposureResults,
    FunctionName.DETECT_CIRCLE: CircleResults,
    FunctionName.MEASURE_OXYD: OxydResults,
}


class ResultsBook:
    """The models of the functions with a results panel, fed by the bus events (``alox_manager_ll_results_panels``
    / ``alox_manager_hl_results_panels``): ``current[HIGH]`` is the running or last high-level function,
    ``current[LOW]`` the last low-level one. ``last_result(name)`` (the runner's) gives the report at the end."""

    def __init__(self, last_result: Callable[[FunctionName], Any] | None = None,
                 functions: Sequence[FunctionName] | None = None) -> None:
        self.last_result = last_result
        self.models: dict[FunctionName, Results] = {
            name: MODELS[name](name) for name in (functions or MODELS) if name in MODELS}
        self.current: dict[str, FunctionName | None] = {HIGH: None, LOW: None}

    def model(self, level: str) -> Results | None:
        name = self.current.get(level)
        return None if name is None else self.models[name]

    def handle(self, event: Any) -> str | None:
        """Take a bus event; returns the level (:data:`HIGH` / :data:`LOW`) whose panel changed, else None."""
        topic = str(getattr(event, "topic", ""))
        parts = topic.split(".")
        if len(parts) != 3 or parts[0] != "function":
            return None
        try:
            name = FunctionName[parts[1].upper()]
        except KeyError:
            return None
        model = self.models.get(name)
        if model is None:
            return None
        data: Mapping[str, Any] = getattr(event, "data", None) or {}
        kind = parts[2]
        if kind == "started":
            model.started(data)
        elif kind == "state":
            model.on_state(data)
        elif kind in ("finished", "failed", "stopped"):
            result = None
            if kind == "finished" and self.last_result is not None:
                try:
                    result = self.last_result(name)
                except Exception:                                 # noqa: BLE001 - the events are enough
                    result = None
            model.ended(kind, data, result)
        else:
            return None
        level = HIGH if name.high_level else LOW
        self.current[level] = name
        return level
