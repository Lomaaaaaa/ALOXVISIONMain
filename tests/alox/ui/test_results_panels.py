"""The results panels (``alox.ui.results_panels``, ``alox.ui.chart``) offscreen, and the window's measurement tabs
fed by bus events."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from alox.core.core import Core
from alox.core.functions import FunctionName, JobInfo
from alox.events import Event
from alox.ui import theme
from alox.ui.chart import Chart, Marker, Series
from alox.ui.controller import Controller, Tab
from alox.ui.results import HIGH, LOW, ResultsBook
from alox.ui.results_panels import (
    BowingPanel,
    FocusPanel,
    MesaPanel,
    ResultsPage,
    TiltPanel,
)
from alox.ui.window import MainWindow

JOB = JobInfo("CJ", "PJ-7", "LOT-1", "W-2", "Z-3")
T0 = 1_764_190_000.0


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    if not theme.applied():
        theme.apply(app, theme.LIGHT)                                    # type: ignore[arg-type]
    return app                                                           # type: ignore[return-value]


def state(name: FunctionName, state_name: str, **data: Any) -> Event:
    return Event(f"function.{name.topic}.state", "core", {"job": JOB, "state": state_name, **data})


def mesa_events(n: int) -> list[Event]:
    events = [Event("function.mesa_measure.started", "core", {"job": JOB})]
    for k in range(1, n + 1):
        samples = tuple((T0 + 30.0 * i, 24.0 - 0.05 * i) for i in range(k))
        report = {"timestamp": samples[-1][0], "oxyd_diameter_um": samples[-1][1], "samples": samples,
                  "fitted": samples[-4:], "linear_fit": {"slope": -0.05 / 30, "intercept": 24.0 + 0.05 / 30 * T0},
                  "rate_um_per_min": -0.1, "remaining_s": 570.0, "remaining_text": "00:09:30",
                  "external_fit_um": 31.5, "oxyd_target_um": 22.5, "wavelength_nm": 850.0,
                  "position": (1.25, -3.5, 12.6)}
        events.append(state(FunctionName.MESA_MEASURE, "Measure MESA", event="MESA Measured", report=report))
    return events


def page_with(level: str, events: list[Event]) -> tuple[ResultsPage, ResultsBook]:
    book = ResultsBook()
    page = ResultsPage(level)
    page.resize(888, 330)
    for e in events:
        if book.handle(e) == level:
            page.show_model(book.model(level))
    return page, book


def test_the_chart_draws_series_markers_and_an_empty_text(qapp: QApplication):
    chart = Chart("X", "Y")
    chart.resize(400, 240)
    assert chart.ranges() is None
    chart.grab()                                                          # "No data"
    chart.set_data([Series("A", [(0, 0), (1, 2), (2, 1)], "both", 0), Series("B", [(0, 1), (2, 2)], "line", 1)],
                   [Marker(x=1.0, label="BEST"), Marker(y=3.0)])
    (x0, x1), (y0, y1) = chart.ranges()                                   # type: ignore[misc]
    assert x0 < 0 and x1 > 2 and y0 < 0 and y1 > 3                         # the markers are in range
    image = chart.grab().toImage()
    blue = theme.current().name == "light" and "#257de0" or "#338ced"
    colours = {image.pixelColor(x, y).name() for x in range(0, 400, 2) for y in range(0, 240, 2)}
    assert blue in colours                                                # series 1 drawn in its slot colour


def test_the_mesa_panel_shows_points_rate_and_remaining_time(qapp: QApplication):
    page, _book = page_with(HIGH, mesa_events(24))
    panel = page.panel
    assert isinstance(panel, MesaPanel) and page.function is FunctionName.MESA_MEASURE
    assert panel.title.text() == "MESA MEASUREMENT" and panel.badge.key == "running"
    assert panel.job_labels["PROCESS JOB"].text() == "PJ-7" and panel.job_labels["ZONE"].text() == "Z-3"
    assert panel.fields.text("OXID RATE (µm/min)") == "-0.100"
    assert panel.fields.text("REMAINING TIME") == "00:09:30"
    assert panel.fields.text("MESA DIAMETER (µm)") == "31.500"
    assert panel.fields.text("WAVELENGTH (nm)") == "850"
    series = {s.name: s for s in panel.chart.series}
    assert len(series["OXIDE"].points) == 24 and len(series["FIT"].points) == 2
    assert [m.y for m in panel.chart.markers] == [22.5]                   # the target is close: drawn
    assert panel.state.text() == "Measure MESA"
    panel.grab()


def test_the_bowing_panel_colours_the_validity(qapp: QApplication):
    values = {"zwafer2": 12.6, "zwafer3": 12.61, "bowing_a_um": 4.3, "parallelism_a_um": 9.1,
              "bowing_valid_a": True, "parallelism_valid_a": False}
    page, _book = page_with(HIGH, [state(FunctionName.BOWING_MEASURE, "Measure Bowing", event="BOW Measured",
                                         values=values, measured=5, index=5)])
    panel = page.panel
    assert isinstance(panel, BowingPanel)
    assert panel.values["bowing_a"].text() == "4.300" and panel.values["parallelism_b"].text() == "—"
    assert (panel.badges["bowing_a"].key, panel.badges["bowing_a"].text()) == ("finished", "VALID")
    assert (panel.badges["parallelism_a"].key, panel.badges["parallelism_a"].text()) == ("error", "INVALID")
    assert panel.badges["bowing_b"].key == "off-ok"
    assert panel.map.current == "ZW4" and {r: z for r, _f, z in panel.map.points}["ZW3"] == 12.61
    assert panel.progress is not None and panel.progress.value == pytest.approx(500 / 9)
    image = panel.badges["parallelism_a"].grab().toImage()
    assert image.pixelColor(image.width() // 2, 3).name() == theme.current().error.background
    panel.grab()


def test_the_tilt_panel_and_a_failure(qapp: QApplication):
    page, book = page_with(HIGH, [state(FunctionName.TILT_MEASURE, "Measure", event="TILT Measured", angle=-0.25,
                                        valid=True, uncertainty=0.001, baseline_mm=20.0)])
    panel = page.panel
    assert isinstance(panel, TiltPanel)
    assert panel.angle.text() == "-0.2500°" and panel.valid.text() == "VALID"
    assert panel.fields.text("UNCERTAINTY 1σ (°)") == "0.0010" and panel.fields.text("METHOD") == "Long baseline"
    book.handle(Event("function.tilt_measure.failed", "core", {"job": JOB, "error": "9530 XY stages timeout"}))
    page.show_model(book.model(HIGH))
    assert panel.badge.key == "error" and "XY stages timeout" in panel.state.text()


def test_the_low_level_page_switches_to_the_last_function(qapp: QApplication):
    events = [state(FunctionName.AUTOFOCUS_LARGE, "Measure", index=i, z=12.0 + 0.1 * i,
                    score=10 - (i - 2) ** 2, progress=20.0 * i) for i in range(5)]
    page, book = page_with(LOW, events)
    panel = page.panel
    assert isinstance(panel, FocusPanel) and panel.title.text() == "AUTOFOCUS"
    assert panel.fields.text("FOCUS POS. (mm)") == "12.2000"
    assert [m.x for m in panel.chart.markers] == [pytest.approx(12.2)]
    book.handle(state(FunctionName.SEARCH_WAVELENGTH, "Init WL", wl_nm=830, progress=5))
    page.show_model(book.model(LOW))
    assert page.function is FunctionName.SEARCH_WAVELENGTH and page.panel is not panel
    assert page.panel.title.text() == "SEARCH WAVELENGTH"                 # type: ignore[union-attr]
    page.show_model(None)
    assert page.panel is None and page.currentWidget() is page.placeholder


# ================================================================================================== the window
@pytest.fixture
def window(qapp: QApplication, core: Core) -> Iterator[MainWindow]:
    w = MainWindow(Controller(core), confirm=lambda _question: True, poll_interval_ms=100)
    w.resize(1400, 820)
    yield w
    w.allow_close()
    w.close()


def pump(window: MainWindow, predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    end = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > end:
            raise AssertionError("condition not reached")
        window.flush(0.5)
        time.sleep(0.01)


def test_the_window_tabs_show_the_results_of_the_bus_events(window: MainWindow, core: Core):
    assert window.high_results.panel is None and window.low_results.panel is None
    for e in mesa_events(6):
        core.events.publish(e)
    core.events.publish(state(FunctionName.DETECT_CIRCLE, "Detect Circle", found=True, count=1))
    pump(window, lambda: window.high_results.function is FunctionName.MESA_MEASURE
         and window.low_results.function is FunctionName.DETECT_CIRCLE)
    panel = window.high_results.panel
    assert isinstance(panel, MesaPanel)
    pump(window, lambda: panel.fields.text("MEASURES") == "6")
    assert panel.fields.text("REMAINING TIME") == "00:09:30"
    window.tabs.setCurrentIndex(int(Tab.HIGH_LEVEL))
    window.set_theme(theme.DARK)
    assert not window.high_results.grab().isNull()
    window.set_theme(theme.LIGHT)
    assert not math.isnan(panel.chart.ranges()[0][0])                     # type: ignore[index]
