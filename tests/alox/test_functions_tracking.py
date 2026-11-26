"""Tracking (``alox.functions.tracking``) on simulated XY stages and a fake Get Image."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from alox.core.functions import FunctionContext, FunctionName, FunctionRunner, Stopped
from alox.devices.xy import XYSettings, XYStage
from alox.functions.services import Circle, Services
from alox.functions.tracking import (
    INVALID_CALIBRATION,
    TRACKING_IMAGE,
    TRACKING_XY,
    FunctionFailed,
    Tracking,
    TrackingOptions,
    TrackingParams,
    calibration,
    stage_coordinate_system,
)
from alox.vision.wafer import PixelUnit, StageCoordinateSystem
from aloxcam.settings import SimulationOptions
from aloxcam.zstage.sim import SimStage

SIM = SimulationOptions(stage_min_mm=0.0, stage_max_mm=200.0, stage_reference_s=0.01)
W, H = 400, 300


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0
        self._lock = threading.Lock()

    def monotonic(self) -> float:
        with self._lock:
            return self.now

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self.now += max(0.0, seconds)


class Settings(dict):
    def get(self, dotted: str, default: Any = None) -> Any:          # type: ignore[override]
        return super().get(dotted, default)


class Log:
    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []

    def info(self, text: str) -> None:
        self.lines.append(("info", text))

    def warning(self, text: str) -> None:
        self.lines.append(("warning", text))

    def error(self, text: str) -> None:
        self.lines.append(("error", text))


class FakeGetImage:
    name = FunctionName.GET_IMAGE

    def __init__(self, services: Services) -> None:
        self.services = services
        self.calls: list[Any] = []

    def run(self, params: Any, ctx: FunctionContext) -> Any:
        self.calls.append(params)
        pixels = np.zeros((H, W), np.uint8)
        self.services.images.put("highmag", pixels)
        return SimpleNamespace(pixels=pixels)


@pytest.fixture
def rig() -> Iterator[SimpleNamespace]:
    clock = FakeTime()
    sims = [SimStage(SIM, clock=clock.monotonic, dh_running=False) for _ in "xy"]
    xy = XYStage(sims[0], sims[1], XYSettings(), sleep=clock.sleep, monotonic=clock.monotonic, supervise=False,
                 reconnect_delay_s=0.0)
    xy.open(timeout=10)
    xy.move_absolute(50.0, 60.0)
    settings = Settings({"hardware.camera.highmag.pixelsize": 1.0, "hardware.camera.highmag.units": 1,
                         "hardware.xy.stagescoordsys": 2})
    log = Log()
    services = Services(devices={"xy": xy}, settings=settings, log=log, clock=clock.monotonic, sleep=clock.sleep)
    services.images.put("highmag", np.zeros((H, W), np.uint8))
    runner = FunctionRunner()
    get_image = FakeGetImage(services)
    runner.register(get_image)
    tracking = Tracking(services)
    runner.register(tracking)
    states: list[tuple[str, dict]] = []
    yield SimpleNamespace(xy=xy, settings=settings, services=services, runner=runner, tracking=tracking,
                          get_image=get_image, clock=clock, log=log, states=states)
    xy.shutdown(timeout=5)


def run(rig: SimpleNamespace, params: TrackingParams | None) -> Any:
    ctx = FunctionContext(FunctionName.TRACKING, None, rig.runner)
    original = ctx.set_state

    def record(state: str, **data: Any) -> None:
        rig.states.append((state, data))
        original(state, **data)
    ctx.set_state = record                                                # type: ignore[method-assign]
    return rig.tracking.run(params, ctx)


# ============================================================================================== moves
def test_moves_the_circle_to_the_image_centre(rig):
    # object 50 px right and 20 px above the centre; 1 µm/px; system #3 (index 2): stage axes = image axes
    report = run(rig, TrackingParams(diameter_um=4.0, circle=(W / 2 + 50, H / 2 - 20, 30.0)))
    assert report.move_px == pytest.approx((-50.0, 20.0))
    assert report.move_mm == pytest.approx((0.05, -0.02))
    assert report.moved == (True, True)
    assert report.original_position == pytest.approx((50.0, 60.0))
    assert rig.xy.position() == pytest.approx((50.05, 59.98), abs=1e-6)
    assert ("info", "Tracking (Move X=0.050000)") in rig.log.lines
    assert ("info", "Tracking (Move Y=-0.020000)") in rig.log.lines
    assert len(rig.get_image.calls) == 1                                   # refresh image of the tracked object
    assert [s for s, _ in rig.states] == ["Start", "Started", "Track", "Finished"]
    assert report.process_time_ms == pytest.approx(500.0, abs=50.0)        # the 500 ms pause, fake clock
    assert rig.tracking.last_report is report


def test_inside_the_tracking_radius_nothing_moves(rig):
    report = run(rig, TrackingParams(diameter_um=4.0, circle=(W / 2 + 1.5, H / 2 - 1.0, 30.0)))
    assert report.moved == (False, False)
    assert report.move_mm == pytest.approx((0.0015, -0.001))
    assert rig.xy.position() == pytest.approx((50.0, 60.0))
    assert rig.get_image.calls == []


def test_either_axis_moves_both(rig):
    """``Min. Move X OR Min. Move Y`` selects both moves: the small Y component is moved too."""
    report = run(rig, TrackingParams(diameter_um=4.0, circle=(W / 2 - 10, H / 2 + 1, 30.0)))
    assert report.moved == (True, True)
    assert rig.xy.position() == pytest.approx((49.99, 60.001), abs=1e-6)


def test_coordinate_system_and_flips(rig):
    rig.settings["hardware.xy.stagescoordsys"] = 3                        # #4: X inverted
    rig.settings["hardware.camera.highmag.vflip"] = True                  # vertical flip: X negated again
    report = run(rig, TrackingParams(diameter_um=4.0, circle=(W / 2 + 50, H / 2, 30.0)))
    assert report.move_mm == pytest.approx((0.05, 0.0))
    assert report.move_px == pytest.approx((50.0, 0.0))


def test_mm_calibration(rig):
    rig.settings.update({"hardware.camera.highmag.pixelsize": 0.002, "hardware.camera.highmag.units": 2})
    report = run(rig, TrackingParams(diameter_um=4.0, circle=(W / 2 + 10, H / 2, 30.0)))
    assert report.move_mm == pytest.approx((0.02, 0.0))


# ============================================================================================== errors
def test_empty_circle_is_9028(rig):
    with pytest.raises(FunctionFailed) as info:
        run(rig, TrackingParams(circle=(0, 0, 0)))
    assert info.value.code == TRACKING_IMAGE and info.value.state == "Started"
    with pytest.raises(FunctionFailed) as info:
        run(rig, TrackingParams())                                        # no circle at all
    assert info.value.code == TRACKING_IMAGE
    assert rig.states[-1][0] == "Error" and rig.states[-1][1]["code"] == TRACKING_IMAGE


def test_no_image_is_9028(rig):
    rig.services.images.images.clear()
    with pytest.raises(FunctionFailed) as info:
        run(rig, TrackingParams(circle=(10, 10, 5)))
    assert info.value.code == TRACKING_IMAGE and info.value.state == "Track"


def test_pixel_calibration_is_9080(rig):
    rig.settings["hardware.camera.highmag.units"] = 0
    with pytest.raises(FunctionFailed) as info:
        run(rig, TrackingParams(circle=(10, 10, 5)))
    assert info.value.code == INVALID_CALIBRATION
    assert rig.xy.position() == pytest.approx((50.0, 60.0))


def test_y_status_error_restores_the_original_position(rig, monkeypatch):
    real = rig.xy.read_error
    monkeypatch.setattr(rig.xy, "read_error", lambda axis="x", **kw: (5, "bad") if axis == "y" else real(axis))
    with pytest.raises(FunctionFailed) as info:
        run(rig, TrackingParams(diameter_um=4.0, circle=(W / 2 + 50, H / 2 - 20, 30.0)))
    assert info.value.code == TRACKING_XY
    assert info.value.report.moved == (True, False)
    assert rig.xy.position() == pytest.approx((50.0, 60.0), abs=1e-6)     # "Restore original position"
    assert ("info", "Tracking (Position Restored)") in rig.log.lines
    assert any(level == "error" and text.startswith("Tracking (Move Y)") for level, text in rig.log.lines)


def test_refused_move_keeps_the_device_code(rig, monkeypatch):
    from alox.devices.base import DeviceError

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise DeviceError("xy", "move relative: outside the wafer", code=9404)
    monkeypatch.setattr(rig.xy, "move_relative", refuse)
    with pytest.raises(FunctionFailed) as info:
        run(rig, TrackingParams(diameter_um=4.0, circle=(W / 2 + 50, H / 2, 30.0)))
    assert info.value.code == 9404 and info.value.report.moved == (False, False)


def test_stop_before_the_run(rig):
    ctx = FunctionContext(FunctionName.TRACKING, None, rig.runner)
    ctx.stop_event.set()
    with pytest.raises(Stopped):
        rig.tracking.run(TrackingParams(circle=(10, 10, 5)), ctx)


# ============================================================================================== params, helpers
def test_params_from_profile_and_set_circle(rig):
    p = TrackingParams.from_profile(Settings({"tracking.diameter": "1,5", "tracking.overlayzone": "TRUE"}))
    assert p.diameter_um == 1.5 and p.overlay_zone is True and p.circle is None
    assert TrackingParams.from_profile(Settings()) == TrackingParams(0.2, False)
    assert TrackingParams(circle=Circle(3, 4, 5)).circle == (3.0, 4.0, 5.0)
    with pytest.raises(FunctionFailed):
        TrackingParams(diameter_um="abc")
    rig.tracking.set_circle((W / 2 + 20, H / 2, 10.0))                   # LabVIEW "Set Params"
    report = run(rig, TrackingParams(diameter_um=4.0, overlay_zone=True))
    assert report.moved == (True, True)
    zone = rig.services.images.overlays["tracking"]
    assert zone == (Circle(W / 2, H / 2, 2.0, (0, 255, 0)),)              # 4 µm · 0.5 / 1 µm


def test_registered_on_the_runner(rig):
    rig.tracking.set_circle((W / 2 + 20, H / 2, 10.0))
    rig.services.profile = Settings({"tracking.diameter": 4.0})
    report = rig.runner.run_once(FunctionName.TRACKING, None, timeout=10)
    assert report.moved == (True, True)


def test_calibration_and_coordinate_system_helpers():
    services = Services(settings=Settings({"hardware.camera.lowmag.pixelsize": "2,5",
                                           "hardware.camera.lowmag.units": 1, "hardware.xy.stagescoordsys": 9}))
    assert calibration(services, "lowmag") == (2.5, PixelUnit.MICROMETER)
    assert calibration(services, "highmag") == (1.0, PixelUnit.MICROMETER)
    assert stage_coordinate_system(services) is StageCoordinateSystem.X_LEFT_Y_DOWN    # invalid → #4
    assert stage_coordinate_system(Services()) is StageCoordinateSystem.X_LEFT_Y_DOWN


def test_options_settle_time(rig):
    rig.tracking.options = TrackingOptions(settle_s=0.0)
    report = run(rig, TrackingParams(diameter_um=4.0, circle=(W / 2 + 50, H / 2, 30.0)))
    assert report.process_time_ms < 500.0
