"""The operator main window (``alox.ui.window``) offscreen, on a booted core with fake functions."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from alox.core.core import Core
from alox.core.functions import FunctionName
from alox.events import Event
from alox.ui.controller import ROWS, Controller, Tab
from alox.ui.window import MainWindow


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])          # type: ignore[return-value]


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


def probe(core: Core, fakes: SimpleNamespace, name: FunctionName) -> Any:
    found = fakes.Probe(name)
    core.functions.register(found)
    return found


def test_window_builds_like_the_front_panel(window: MainWindow):
    assert set(window.start_buttons) == {b.key for row in ROWS for b in row.starts}
    assert set(window.stop_buttons) == {row.key for row in ROWS}
    assert set(window.indicators) == {row.key for row in ROWS if row.status}
    assert [window.tabs.tabText(i) for i in range(3)] == ["SETTINGS", "LOW-LEVEL MEASUREMENTS",
                                                         "HIGH-LEVEL MEASUREMENTS"]
    assert "PROFILE DEFAULT" in window.windowTitle()
    assert window.status_field.text() == "100 : System initialized"
    assert window.profile.currentText() == "DEFAULT" and window.camera.currentText() == "Low"
    assert window.function_status_text(FunctionName.AUTOFOCUS_LARGE) == "OFF"          # not registered
    assert not window.start_buttons["AF_RUN_BTN"].isEnabled() and not window.thermal.isEnabled()


def test_a_status_event_updates_the_indicator(window: MainWindow, core: Core, fakes: SimpleNamespace):
    af = probe(core, fakes, FunctionName.AUTOFOCUS_LARGE)
    window.refresh_functions()
    indicator = window.indicators["AF"]
    assert indicator.text() == "OFF OK" and "transparent" in indicator.styleSheet()
    worker = threading.Thread(target=core.functions.start, args=(FunctionName.AUTOFOCUS_LARGE,))
    worker.start()                                                   # the event comes from another thread
    worker.join()
    pump(window, lambda: indicator.text() == "RUNNING")
    assert "#ffff00" in indicator.styleSheet() and not window.start_buttons["AF_RUN_BTN"].isEnabled()
    af.release.set()
    pump(window, lambda: indicator.text() == "FINISHED")
    assert "#66ff00" in indicator.styleSheet() and window.start_buttons["AF_RUN_BTN"].isEnabled()


def test_a_start_button_starts_the_function_and_stop_stops_it(window: MainWindow, core: Core,
                                                              fakes: SimpleNamespace):
    af = probe(core, fakes, FunctionName.AUTOFOCUS_LARGE)
    window.refresh_functions()
    window.start_buttons["AF_RUN_BTN"].click()
    assert af.started.wait(5)
    pump(window, lambda: window.tabs.currentIndex() == int(Tab.LOW_LEVEL))
    assert af.seen[0].camera_type == 1 and core.functions.running is FunctionName.AUTOFOCUS_LARGE
    window.stop_buttons["AF"].click()
    pump(window, lambda: core.functions.running is None and window.indicators["AF"].text() == "OFF OK")
    window.option_buttons["GET_IMAGE"].click()
    pump(window, lambda: window.tabs.tabText(0) == "SETTINGS (Get Image)")


def test_a_supervisor_measure_shows_the_high_level_tab(window: MainWindow, core: Core, fakes: SimpleNamespace):
    mesa = probe(core, fakes, FunctionName.MESA_MEASURE)
    window.refresh_functions()
    window.start_buttons["MES_MESA_RUN_CONT_BTN"].click()
    assert mesa.started.wait(5)
    pump(window, lambda: window.tabs.currentIndex() == int(Tab.HIGH_LEVEL)
         and window.indicators["MES_MESA"].text() == "RUNNING")
    pump(window, lambda: "200" in window.measure_labels[next(iter(window.measure_labels))].text())
    mesa.release.set()
    pump(window, lambda: core.functions.running is None)


def test_the_display_shows_the_current_image(window: MainWindow, core: Core):
    pytest.importorskip("PIL")
    from alox.core.images import ImageStore

    store = ImageStore()
    core.images = store
    try:
        window.refresh_image()
        assert not window.view.has_image
        store.put("lowmag", np.tile(np.arange(64, dtype=np.uint8), (48, 1)))
        pump(window, lambda: window.view.has_image)
        assert window.view._image.width() == 64 and window.view._image.height() == 48
    finally:
        store.close()


def test_exit_and_show_ui(window: MainWindow, core: Core):
    window.show()
    asked = threading.Event()
    core.on_shutdown = asked.set
    window.exit_action.trigger()
    pump(window, asked.is_set)
    core.events.publish(Event("core.show_ui", "core", {"visible": False}))
    pump(window, lambda: not window.isVisible())
