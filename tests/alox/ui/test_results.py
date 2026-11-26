"""The results models of the measurement tabs (``alox.ui.results``, Qt-free) fed with synthetic bus events."""

from __future__ import annotations

import math
from dataclasses import asdict
from types import SimpleNamespace
from typing import Any

import pytest

from alox.core.functions import FunctionName, JobInfo
from alox.events import Event
from alox.functions.auto_exposure import AutoExposureReport
from alox.functions.bowing import BowingReport, BowingValues
from alox.functions.detect_circle import DetectCircleReport
from alox.functions.focus import FocusResult
from alox.functions.measure_oxyd import DiametersReport
from alox.functions.mesa import MesaReport, OxydDiameters
from alox.functions.search_wavelength import SearchWavelengthReport, SpectrumPoint
from alox.functions.tilt import TiltMethod, TiltReport
from alox.ui.chart import nice_ticks, time_ticks
from alox.ui.results import (
    DASH,
    HIGH,
    LOW,
    BowingResults,
    MesaResults,
    ResultsBook,
    TiltResults,
    Validity,
    fmt,
    fmt_duration,
)
from alox.vision.circles import Circle
from alox.vision.edges import DetectedCircle
from alox.vision.stats import LinearFit

JOB = JobInfo("CJ", "PJ-7", "LOT-1", "W-2", "Z-3")
T0 = 1_764_190_000.0


def event(name: FunctionName, kind: str, **data: Any) -> Event:
    return Event(f"function.{name.topic}.{kind}", "core", {"job": JOB, **data})


def state(name: FunctionName, state_name: str, **data: Any) -> Event:
    return event(name, "state", state=state_name, **data)


def mesa_report(n: int, *, fit: bool = True) -> MesaReport:
    """The report after ``n`` measures of a diameter shrinking by 0.1 µm/min (one measure every 30 s)."""
    samples = tuple((T0 + 30.0 * i, 24.0 - 0.05 * i) for i in range(n))
    slope = -0.1 / 60.0
    line = LinearFit(slope, 24.0 - slope * T0, 0.0, 99.0) if fit else None
    fitted = tuple((t, 24.0 + slope * (t - T0)) for t, _ in samples[-5:]) if fit else ()
    remaining = (samples[-1][1] - 22.5) / 0.1 * 60.0
    return MesaReport(job=JOB, timestamp=samples[-1][0], external_fit_um=31.5, oxyd=OxydDiameters(fit=samples[-1][1]),
                      wavelength_nm=850.0, rate_um_per_min=-0.1 if fit else math.nan,
                      remaining_s=remaining if fit else 86400.0,
                      remaining_text=fmt_duration(remaining) if fit else "?", linear_fit=line,
                      position=(1.25, -3.5, 12.6), oxyd_diameter_um=samples[-1][1], oxyd_target_um=22.5, averages=1,
                      found=True, rate_error=None if fit else 9003, samples=samples, fitted=fitted)


# ================================================================================================== helpers
def test_formatting_helpers():
    assert fmt(1.23456, 3) == "1.235" and fmt(math.nan) == DASH and fmt(None) == DASH and fmt("x") == DASH
    assert fmt(850, 0, "nm") == "850 nm"
    assert fmt_duration(3725) == "01:02:05" and fmt_duration(math.nan) == DASH and fmt_duration(-1) == DASH


def test_chart_ticks_are_round():
    assert nice_ticks(660, 750, 5) == [660, 680, 700, 720, 740]
    assert nice_ticks(0.0, 1.0, 4) == [0.0, 0.25, 0.5, 0.75, 1.0]
    assert nice_ticks(5, 5) == [5] and nice_ticks(math.nan, 1) == []
    ticks = time_ticks(T0 + 7, T0 + 700, 5)
    assert len(ticks) >= 2 and all(t % 300 == 0 for t in ticks) and ticks[0] >= T0 + 7 and ticks[-1] <= T0 + 700


# ================================================================================================== high level
def test_mesa_points_give_the_rate_line_and_the_remaining_time():
    book = ResultsBook()
    assert book.handle(event(FunctionName.MESA_MEASURE, "started")) == HIGH
    for n in range(1, 13):
        book.handle(state(FunctionName.MESA_MEASURE, "Measure MESA", event="MESA Measured", report=mesa_report(n)))
    model = book.model(HIGH)
    assert isinstance(model, MesaResults) and model.outcome == "running"
    assert len(model.samples) == 12 and model.samples[0] == (T0, 24.0)
    (t1, d1), (t2, d2) = model.fit_line()
    assert (t1, t2) == (T0 + 30 * 7, T0 + 30 * 11)                         # the fit window
    assert d2 == pytest.approx(24.0 - 0.1 * 30 * 11 / 60) and d1 > d2
    fields = dict(model.fields())
    assert fields["OXID RATE (µm/min)"] == "-0.100"
    assert fields["REMAINING TIME"] == "00:09:30"                         # (23.45 - 22.5) / 0.1 µm/min
    assert fields["MESA DIAMETER (µm)"] == "31.500" and fields["WAVELENGTH (nm)"] == "850"
    assert fields["TARGET (µm)"] == "22.500" and fields["MEASURES"] == "12"
    assert fields["POSITION (mm)"] == "X 1.250  Y -3.500  Z 12.600"
    assert model.job_fields() == [("PROCESS JOB", "PJ-7"), ("LOT", "LOT-1"), ("WAFER", "W-2"), ("ZONE", "Z-3")]
    assert model.badge() == "running"
    book.handle(state(FunctionName.MESA_MEASURE, "Finished", event="MESA Finished", report=mesa_report(12)))
    book.handle(event(FunctionName.MESA_MEASURE, "finished"))
    assert model.outcome == "finished" and model.badge_text() == "FINISHED"
    book.handle(event(FunctionName.MESA_MEASURE, "started"))                # a new run starts empty
    assert model.samples == [] and model.fields()[0][1] == DASH


def test_mesa_without_rate_shows_unknown_and_points_without_samples_accumulate():
    model = MesaResults(FunctionName.MESA_MEASURE)
    model.on_state({"state": "Measure MESA", "event": "MESA Measured", "report": mesa_report(3, fit=False)})
    assert model.rate_text() == DASH and model.remaining_text() == "?" and model.fit_line() == []
    model.reset()
    for i in range(3):                                                    # a report without the session samples
        model.take_result({"timestamp": T0 + i, "oxyd_diameter_um": 20.0 - i, "samples": ()})
    assert model.samples == [(T0, 20.0), (T0 + 1, 19.0), (T0 + 2, 18.0)]


def test_bowing_values_and_validity():
    book = ResultsBook()
    book.handle(event(FunctionName.BOWING_MEASURE, "started"))
    values = BowingValues(zwafer2=12.6, zwafer3=12.61)
    book.handle(state(FunctionName.BOWING_MEASURE, "Measure Bowing", event="BOW Measured", values=values,
                      measured=2, progress=22.2, index=1, status=201))
    book.handle(state(FunctionName.BOWING_MEASURE, "Move to Pos Zi", index=2, measured=2, status=201))
    model = book.model(HIGH)
    assert isinstance(model, BowingResults)
    assert model.current_row() == "ZC3" and model.measured == 2 and model.status == 201
    assert model.progress == pytest.approx(200 / 9)
    points = {row: z for row, _field, z in model.points()}
    assert points["ZW2"] == 12.6 and points["ZW3"] == 12.61 and math.isnan(points["ZC5"])
    assert model.result("a")[2:] == (Validity.UNKNOWN, Validity.UNKNOWN)
    final = BowingValues(**{**asdict(BowingValues()), **dict.fromkeys(BowingResults.FIELDS, 12.6),
                            "bowing_a_um": 4.3, "parallelism_a_um": 1.2, "bowing_b_um": -2.0,
                            "parallelism_b_um": 9.0, "bowing_valid_a": True, "parallelism_valid_a": True,
                            "bowing_valid_b": True})
    book.last_result = lambda _name: BowingReport(job=JOB, values=final, z_measures=(), positions=((0.0, 0.0),) * 9)
    book.handle(event(FunctionName.BOWING_MEASURE, "finished"))
    assert model.measured == 9 and model.progress == 100.0 and model.current_row() is None
    assert model.result("a") == (4.3, 1.2, Validity.VALID, Validity.VALID)
    assert model.result("b") == (-2.0, 9.0, Validity.VALID, Validity.INVALID)


def test_tilt_angle_method_uncertainty_and_validity():
    book = ResultsBook()
    book.handle(event(FunctionName.TILT_MEASURE, "started"))
    book.handle(state(FunctionName.TILT_MEASURE, "Move", status=202, index=1, current_xy=(10.5, -2.25)))
    model = book.model(HIGH)
    assert isinstance(model, TiltResults) and model.validity() is Validity.UNKNOWN
    book.handle(state(FunctionName.TILT_MEASURE, "Measure Baseline", event="TILT Measured", angle=0.0123,
                      valid=False, uncertainty=0.0008, baseline_mm=18.2, image_angle=0.011))
    fields = dict(model.fields())
    assert fields["METHOD"] == "Long baseline" and fields["UNCERTAINTY 1σ (°)"] == "0.0008"
    assert fields["BASELINE (mm)"] == "18.200" and fields["POSITION (mm)"] == "X 10.500  Y -2.250"
    assert model.validity() is Validity.INVALID
    report = TiltReport(job=JOB, method=TiltMethod.LATTICE, angle_deg=0.0125, valid=True, uncertainty_deg=0.0007)
    book.last_result = lambda _name: report
    book.handle(event(FunctionName.TILT_MEASURE, "finished"))
    assert model.method == "Lattice" and model.angle == 0.0125 and model.validity() is Validity.VALID
    dual = TiltResults(FunctionName.TILT_MEASURE)
    dual.on_state({"state": "Match Objects", "event": "TILT Measured", "angle": 0.5, "valid": True,
                   "object1_found": True, "object2_found": False})
    assert dict(dual.fields())["OBJECTS FOUND"] == "yes / no" and dual.method == "Dual Objects"
    edge = TiltResults(FunctionName.TILT_MEASURE)
    edge.ended("finished", {}, TiltReport(job=JOB, method=TiltMethod.STRAIGHT_EDGE, angle_deg=0.3, valid=True,
                                          straight_edge=SimpleNamespace(found_edges_percent=87.5, message="TILT OK")))
    fields = dict(edge.fields())
    assert fields["METHOD"] == "Straight Edge" and fields["FOUND EDGES (%)"] == "87.5" and fields["MESSAGE"] == "TILT OK"
    assert fields["UNCERTAINTY 1σ (°)"] == DASH and edge.validity() is Validity.VALID


def test_the_book_follows_the_running_or_last_function_of_each_level():
    book = ResultsBook(last_result=lambda _name: (_ for _ in ()).throw(RuntimeError("gone")))
    assert book.model(HIGH) is None and book.model(LOW) is None
    assert book.handle(Event("status.mesa", "core", {})) is None
    assert book.handle(event(FunctionName.GET_IMAGE, "state", state="Grab")) is None   # no results panel
    assert book.handle(Event("function.unknown.state", "core", {})) is None
    assert book.handle(state(FunctionName.DETECT_CIRCLE, "Detect Circle", found=True, count=1)) == LOW
    assert book.handle(event(FunctionName.TILT_MEASURE, "started")) == HIGH
    assert book.handle(event(FunctionName.TILT_MEASURE, "failed", error="XY timeout")) == HIGH
    tilt = book.model(HIGH)
    assert tilt is not None and tilt.outcome == "failed" and tilt.badge() == "error"
    assert "XY timeout" in tilt.state_text()
    assert book.handle(event(FunctionName.DETECT_CIRCLE, "finished")) == LOW              # result unreadable
    assert book.model(LOW) is book.models[FunctionName.DETECT_CIRCLE]
    assert book.handle(state(FunctionName.AUTOFOCUS_LARGE, "Start")) == LOW
    assert book.current[LOW] is FunctionName.AUTOFOCUS_LARGE


# ================================================================================================== low level
def test_focus_curve_best_z_and_a_new_run_after_a_finished_one():
    book = ResultsBook()
    for i, (z, score) in enumerate(((12.1, 5.0), (12.0, 2.0), (12.2, 9.0), (12.3, 9.0))):
        book.handle(state(FunctionName.AUTOFOCUS_LARGE, "Measure", index=i, z=z, score=score, progress=20 * i))
    model = book.model(LOW)
    assert model is not None and model.title == "AUTOFOCUS"
    assert model.curve() == [(12.0, 2.0), (12.1, 5.0), (12.2, 9.0), (12.3, 9.0)]
    assert model.best() == (12.2, 9.0) and model.progress == 60                   # first maximum wins
    assert model.found() is Validity.UNKNOWN
    result = FocusResult(table=((12.0, 1.0), (12.25, 10.0)), best_z=12.25, best_score=10.0, best_index=1,
                         position=12.25, progress=100.0)
    book.last_result = lambda _name: result
    book.handle(state(FunctionName.AUTOFOCUS_LARGE, "Finished", progress=100))
    book.handle(event(FunctionName.AUTOFOCUS_LARGE, "finished"))
    assert model.best() == (12.25, 10.0) and model.found() is Validity.VALID
    assert dict(model.fields())["FOCUS POS. (mm)"] == "12.2500"
    book.handle(state(FunctionName.AUTOFOCUS_LARGE, "Start", progress=0))     # run once by a high-level function
    assert model.curve() == [] and model.outcome == "running" and model.best() is None
    refocus = book.models[FunctionName.REFOCUS]
    assert refocus.title == "REFOCUS"


def test_search_wavelength_spectrum_and_optimum():
    book = ResultsBook()
    book.handle(state(FunctionName.SEARCH_WAVELENGTH, "Init WL", progress=0, wl_nm=830))
    spectrum = tuple(SpectrumPoint(wl, 100.0 - wl / 10, 50.0, (wl - 800) / 2, 8000.0) for wl in (850, 830, 840))
    report = SearchWavelengthReport(current_wl_nm=850, current_expo_us=8000, current_contrast=25.0,
                                    optimal_wl_nm=850, optimal_contrast=25.0, min_contrast=10.0, progress=100,
                                    valid=True, spectrum=spectrum)
    light = {k: v for k, v in asdict(report).items() if not k.startswith("histogram")}
    book.handle(state(FunctionName.SEARCH_WAVELENGTH, "Measure", event="measured", progress=50, report=light))
    model = book.model(LOW)
    series = model.series()                                                   # type: ignore[union-attr]
    assert [p[0] for p in series["CONTRAST"]] == [830, 840, 850] and series["CONTRAST"][-1] == (850, 25.0)
    assert model.optimum() == (850, 25.0) and model.validity() is Validity.VALID    # type: ignore[union-attr]
    fields = dict(model.fields())                                             # type: ignore[union-attr]
    assert fields["OPTIMAL WL (nm)"] == "850" and fields["MIN. CONTRAST"] == "10.00"
    book.last_result = lambda _name: report
    book.handle(event(FunctionName.SEARCH_WAVELENGTH, "finished"))
    assert len(model.spectrum) == 3                                           # type: ignore[union-attr]


def test_auto_exposure_measures_and_result():
    book = ResultsBook()
    book.handle(state(FunctionName.AUTO_EXPOSURE, "Measure Expo", measure=1, exposure=5000.0, brightness=40.0,
                      wl_nm=850))
    book.handle(state(FunctionName.AUTO_EXPOSURE, "Measure Expo", measure=2, exposure=15000.0, brightness=95.0,
                      wl_nm=850))
    model = book.model(LOW)
    assert model.points() == [(5000.0, 40.0), (15000.0, 95.0)]               # type: ignore[union-attr]
    assert model.result_point() is None and model.validity() is Validity.UNKNOWN   # type: ignore[union-attr]
    report = AutoExposureReport(5000.0, 40.0, 15000.0, 95.0, 12273.0, 80.0, True, 12.0, 850, 850)
    book.handle(state(FunctionName.AUTO_EXPOSURE, "Finished", event="finished", report=asdict(report)))
    assert model.result_point() == (12273.0, 80.0) and model.validity() is Validity.VALID   # type: ignore[union-attr]
    assert model.line() == [(5000.0, 40.0), (15000.0, 95.0)]                  # type: ignore[union-attr]
    fields = dict(model.fields())                                             # type: ignore[union-attr]
    assert fields["EXPOSURE (µs)"] == "12273" and fields["MEASURE 2 BRIGHTNESS"] == "95.0"
    assert model.outcome == "finished"


def test_detect_circle_and_measure_oxyd_tables():
    book = ResultsBook()
    book.handle(state(FunctionName.DETECT_CIRCLE, "Detect Circle", found=True, best_circle=Circle(320, 240, 42),
                      count=2))
    circles = book.model(LOW)
    assert [r.cells for r in circles.rows()] == [("320.0", "240.0", "84.00", DASH, DASH)]  # type: ignore[union-attr]
    report = DetectCircleReport(best_circle=Circle(320, 240, 42), best_index=1, found=True, process_time_ms=5.0,
                                all_circles=(DetectedCircle(10, 20, 40, 600), DetectedCircle(320, 240, 42, 900)),
                                pixel_size_um=0.5)
    book.last_result = lambda _name: report
    book.handle(event(FunctionName.DETECT_CIRCLE, "finished"))
    rows = circles.rows()                                                     # type: ignore[union-attr]
    assert [r.highlight for r in rows] == [False, True] and rows[1].cells == ("320.0", "240.0", "84.00", "42.000",
                                                                              "900")
    diameters = DiametersReport(external=80.0, fit=60.0, median=61.0, mean=60.5, surface=59.0, inscribed=58.0,
                                circumscribed=62.0, circ_insc_ratio=1.069, stddev=0.4, accuracy=0.98)
    book.handle(state(FunctionName.MEASURE_OXYD, "Measure", mesa_found=True, oxyd_found=False, diameters=diameters))
    oxyd = book.model(LOW)
    assert oxyd.found() == (Validity.VALID, Validity.INVALID)                 # type: ignore[union-attr]
    assert oxyd.rows()[1].cells == ("60.00", DASH) and oxyd.rows()[1].highlight  # type: ignore[union-attr]
    book.last_result = lambda _name: SimpleNamespace(diameters=diameters, mesa_found=True, oxyd_found=True,
                                                     pixel_size_um=0.5, fit_score=93.0, process_time_ms=3.0)
    book.handle(event(FunctionName.MEASURE_OXYD, "finished"))
    assert oxyd.rows()[0].cells == ("80.00", "40.000")                        # type: ignore[union-attr]
    assert dict(oxyd.fields())["CIRC. / INSC. RATIO"] == "1.069"              # type: ignore[union-attr]
