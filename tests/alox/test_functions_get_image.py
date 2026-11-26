"""Get Image (``alox.functions.get_image``): the state machine, the exposure/LED setup, the processing, the overlays and
the live display, on fake devices and on the simulated camera."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Iterator
from typing import Any

import numpy as np
import pytest

from alox.core.exposures import ExposureTable
from alox.core.functions import FunctionContext, FunctionName, FunctionRunner, Stopped
from alox.core.images import ImageStore
from alox.devices.base import DeviceError
from alox.devices.camera import Camera, CameraImage
from alox.events import Event
from alox.functions.focus import CameraType
from alox.functions.get_image import (
    ExposureMode,
    GetImage,
    GetImageError,
    GetImageFailed,
    GetImageOptions,
    GetImageParams,
    GIState,
    LiveView,
    best_scale,
    stage_to_wafer,
    zoom_crop,
)
from alox.functions.registry import register_functions
from alox.functions.services import Circle, Line, Services, Text
from aloxcam.camera.sim import SimCameraBackend
from aloxcam.settings import CameraSettings, SimulationOptions

TABLE = [[700.0, 20000.0, 0, 0, 0, 0, 0], [800.0, 40000.0, 0, 0, 0, 0, 0]]
COLOR = (0x33, 0x99, 0xFF)


class Store:
    """A profile / settings store (``get``/``set``)."""

    def __init__(self, values: dict[str, Any] | None = None) -> None:
        self.values = dict(values or {})

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.values[key] = value


class FakeCamera:
    def __init__(self, name: str, *, exposure: float = 10000.0, lights: bool = False, led: bool = False) -> None:
        self.name = name
        self.settings = CameraSettings.default_high_mag() if name == "highmag" else CameraSettings.default_low_mag()
        self.lights = object() if lights else None
        self.exposure_us = exposure
        self.led_on = led
        self.calls: list[tuple[str, Any]] = []
        self.grab_error: Exception | None = None
        self.number = 0

    def exposure(self) -> float:
        return self.exposure_us

    def set_exposure(self, us: float) -> float:
        self.calls.append(("set_exposure", us))
        self.exposure_us = us
        return us

    def led(self) -> bool:
        self.calls.append(("led", None))
        return self.led_on

    def set_led(self, on: bool) -> None:
        self.calls.append(("set_led", on))
        self.led_on = on

    def grab(self, *, newest: bool = True) -> CameraImage:
        self.calls.append(("grab", newest))
        if self.grab_error is not None:
            raise self.grab_error
        self.number += 1
        pixels = np.full((60, 80), 100, np.uint8)
        pixels[20:40, 30:50] = 200
        return CameraImage(pixels=pixels, number=self.number, time=1_700_000_000.25, camera=self.name,
                           exposure_us=self.exposure_us, gain=0.0, flat_field=False)


class FakeZ:
    def position(self) -> float:
        return 1.23456


class FakeXY:
    def position(self) -> tuple[float, float]:
        return 80.0, 70.0


class FakeMono:
    last_position = 750


class Rig:
    def __init__(self, *, settings: dict[str, Any] | None = None, profile: dict[str, Any] | None = None,
                 table: list[list[float]] | None = None, devices: dict[str, Any] | None = None) -> None:
        self.hm = FakeCamera("highmag")
        self.lm = FakeCamera("lowmag", lights=True)
        self.images = ImageStore()
        self.events: list[Event] = []
        self.sleeps: list[float] = []
        self.settings = Store({"hardware.camera.highmag.exposuretolerance": 10.0,
                               "hardware.camera.lowmag.exposuretolerance": 10.0,
                               "hardware.camera.highmag.pixelsize": 0.170602, "hardware.camera.highmag.units": 1,
                               "hardware.xy.waferxorg": 77.375, "hardware.xy.waferyorg": 77.223,
                               "hardware.xy.stagescoordsys": 3, **(settings or {})})
        self.profile = Store({"general.exposures": TABLE if table is None else table, **(profile or {})})
        all_devices = {"highmag": self.hm, "lowmag": self.lm, "z": FakeZ(), "xy": FakeXY(), "wl": FakeMono()}
        all_devices.update(devices or {})
        self.services = Services(devices={k: v for k, v in all_devices.items() if v is not None},
                                 profile=self.profile, settings=self.settings,
                                 exposures=ExposureTable(self.profile), images=self.images)
        self.function = GetImage(self.services, sleep=self.sleeps.append)
        self.runner = FunctionRunner(publish=self.events.append)
        self.runner.register(self.function)

    def ctx(self) -> FunctionContext:
        return FunctionContext(FunctionName.GET_IMAGE, None, self.runner)

    def run(self, params: Any = None) -> Any:
        return self.function.run(params, self.ctx())

    def states(self) -> list[str]:
        return [e.data["state"] for e in self.events if e.topic == "function.get_image.state"]


@pytest.fixture
def rig() -> Rig:
    return Rig()


def texts(shapes) -> list[tuple[float, float, str]]:
    return [(s.x, s.y, s.text) for s in shapes if isinstance(s, Text)]


# ================================================================================================== helpers
def test_pure_helpers():
    assert best_scale(0.170602) == (50.0, pytest.approx(50 * 250 / (0.170602 * 250)))
    assert stage_to_wafer(80.0, 70.0, (77.375, 77.223), 3) == pytest.approx((-2.625, 7.223))
    assert stage_to_wafer(80.0, 70.0, (77.375, 77.223), 0) == pytest.approx((2.625, -7.223))
    assert stage_to_wafer(80.0, 70.0, (77.375, 77.223), 1) == pytest.approx((-2.625, -7.223))
    image = np.arange(100 * 60).reshape(60, 100)
    assert zoom_crop(image, 1.0) is image and zoom_crop(image, 1.5) is image
    cropped = zoom_crop(image, 0.5)                       # margins w − w/1.5 = 33, h − h/1.5 = 20
    assert cropped.shape == (20, 34) and cropped[0, 0] == image[20, 33]
    for bad in (0.0, -1.0, math.nan):
        with pytest.raises(ValueError):
            zoom_crop(image, bad)


def test_params_coercion():
    p = GetImageParams(camera_type="Low Mag.", exposure_mode="current", led_camera=0, zoom="0.5")
    assert p.camera_type is CameraType.LOW_MAG and p.exposure_mode is ExposureMode.CURRENT
    assert p.led_camera is CameraType.HIGH_MAG and p.zoom == 0.5 and p.camera == "lowmag"
    for kwargs in ({"camera_type": "x"}, {"exposure_mode": 7}, {"gamma": "abc"}):
        with pytest.raises(GetImageError) as info:
            GetImageParams(**kwargs)
        assert info.value.code == 9002


def test_params_from_the_profile(rig: Rig):
    rig.profile.values.update({"get.image.lowmag.led": True, "get.image.lowmag.exposure": 5000.0,
                               "get.image.lowmag.overlayscale": True, "get.image.lowmag.subsampling": 0.5,
                               "get.image.lowmag.roiwidth": 10})
    rig.settings.values["general.setLEDcam"] = 0
    p = rig.function.params_for("lowmag")
    assert (p.camera_type, p.led, p.low_mag_exposure, p.overlay_scale, p.zoom) == (CameraType.LOW_MAG, True, 5000.0,
                                                                                    True, 0.5)
    assert p.optional_rectangle == (0, 0, 10, 0) and p.led_camera is CameraType.HIGH_MAG
    default = rig.function.params_for(0)
    assert default == GetImageParams(camera_type=CameraType.HIGH_MAG, led_camera=CameraType.HIGH_MAG)
    assert rig.function.coerce_params(None).camera_type is CameraType.LOW_MAG          # general.camera default 1
    rig.settings.values["general.camera"] = 0
    assert rig.function.coerce_params(None).camera_type is CameraType.HIGH_MAG


# ================================================================================================== setup hardware
def test_high_mag_fixed_exposure_from_the_wavelength(rig: Rig):
    result = rig.run("High Mag.")
    assert rig.states() == ["Start", "Started", "Setup Hardware", "Acquire", "Finished"]
    assert ("set_exposure", 30000.0) in rig.hm.calls                 # table interpolated at the last WL (750 nm)
    assert rig.lm.calls == [("set_led", False)]                       # "Switch Off Low Mag LED", no read
    assert rig.sleeps == [pytest.approx(0.030 + 0.100)]               # max(new, old) + 100 ms
    assert result.camera == "highmag" and result.exposure_us == 30000.0 and result.number == 1
    assert rig.images.last("highmag") is not None and rig.images.image_info("highmag")[0] == 1


def test_exposure_within_tolerance_is_not_written(rig: Rig):
    rig.hm.exposure_us = 30005.0
    rig.run(GetImageParams(camera_type=CameraType.HIGH_MAG))
    assert not [c for c in rig.hm.calls if c[0] == "set_exposure"] and rig.sleeps == []


def test_low_mag_led_and_exposure(rig: Rig):
    rig.run(GetImageParams(camera_type=CameraType.LOW_MAG, led=True, low_mag_exposure=4000.0))
    assert rig.lm.calls[:2] == [("led", None), ("set_led", True)] and ("set_exposure", 4000.0) in rig.lm.calls
    assert rig.sleeps == [0.1, pytest.approx(0.010 + 0.100)]          # LED settle, then max(4000, 10000) µs + 100 ms
    rig.sleeps.clear()
    rig.lm.exposure_us = 4000.0
    rig.run(GetImageParams(camera_type=CameraType.LOW_MAG, led=True, low_mag_exposure=4000.0))
    assert rig.sleeps == [0.1]                                        # LED unchanged, exposure in range: no wait


def test_led_camera_fallback_and_absence():
    rig = Rig()
    rig.lm.lights, rig.hm.lights = None, object()                    # only the high-mag camera drives the LED
    rig.run(GetImageParams(camera_type=CameraType.LOW_MAG, led=True))
    assert ("set_led", True) in rig.hm.calls
    rig.hm.lights = None
    rig.run(GetImageParams(camera_type=CameraType.LOW_MAG, led=True))   # no LED wiring: step skipped
    assert not [c for c in rig.lm.calls if c[0] in ("led", "set_led")]


@pytest.mark.parametrize("mode", [ExposureMode.AUTO, ExposureMode.CURRENT])
def test_auto_and_current_change_nothing(rig: Rig, mode: ExposureMode):
    rig.run(GetImageParams(camera_type=CameraType.HIGH_MAG, exposure_mode=mode))
    assert rig.hm.calls == [("grab", True)] and rig.lm.calls == [] and rig.sleeps == []


def test_invalid_exposure_is_9014():
    rig = Rig(table=[])
    with pytest.raises(GetImageFailed) as info:
        rig.run(GetImageParams(camera_type=CameraType.HIGH_MAG))
    assert info.value.code == 9014 and info.value.state == "Setup Hardware"
    assert rig.states()[-1] == "Error" and rig.images.current() is None
    with pytest.raises(GetImageFailed) as info:
        rig.run(GetImageParams(camera_type=CameraType.LOW_MAG, low_mag_exposure=math.nan))
    assert info.value.code == 9014


# ================================================================================================== acquire
def test_grab_failures_keep_the_device_code(rig: Rig):
    rig.hm.grab_error = DeviceError("highmag", "grab: timeout", code=-1074360311)
    with pytest.raises(GetImageFailed) as info:
        rig.run(GetImageParams(camera_type=0, exposure_mode=ExposureMode.CURRENT))
    assert info.value.code == -1074360311 and info.value.state == "Acquire"
    rig.hm.grab_error = DeviceError("highmag", "camera not open")
    with pytest.raises(GetImageFailed) as info:
        rig.run(GetImageParams(camera_type=0, exposure_mode=ExposureMode.CURRENT))
    assert info.value.code == 9012


def test_processing_chain(rig: Rig):
    current = ExposureMode.CURRENT
    plain = rig.run(GetImageParams(camera_type=0, exposure_mode=current)).pixels
    assert plain.shape == (60, 80) and plain[0, 0] == 100
    zoomed = rig.run(GetImageParams(camera_type=0, exposure_mode=current, zoom=0.5)).pixels
    assert zoomed.shape == (20, 26)                                   # margins 20 (Y) and 27 (X)
    bright = rig.run(GetImageParams(camera_type=0, exposure_mode=current, brightness=228)).pixels
    assert bright[0, 0] == 200
    gained = rig.run(GetImageParams(camera_type=0, exposure_mode=current, gain=2.0)).pixels
    assert gained[0, 0] == 200 and gained[30, 40] == 255              # saturated
    with pytest.raises(GetImageFailed) as info:
        rig.run(GetImageParams(camera_type=0, exposure_mode=current, zoom=-1.0))
    assert info.value.code == 9010


def test_metadata_and_calibration(rig: Rig):
    result = rig.run(GetImageParams(camera_type=0, exposure_mode=ExposureMode.CURRENT))
    meta = result.metadata
    assert meta["buffer"] == "1" and meta["type"] == "High-Mag" and meta["name"] == rig.hm.settings.camera_name
    assert meta["pixel_size"] == pytest.approx(0.170602) and meta["units"] == "um"
    assert rig.images.current()[2]["timestamp"] == meta["timestamp"]


def test_overlays(rig: Rig):
    rig.settings.values.update({"overlays.fontlinespace": 40, "overlays.fontsize": 30})
    p = GetImageParams(camera_type=0, exposure_mode=ExposureMode.CURRENT, overlay_name=True, overlay_type=True,
                       overlay_timestamp=True, overlay_index=True, overlay_hw_infos=True, overlay_scale=True)
    result = rig.run(p)
    meta = result.metadata
    camera = result.overlays["highmagcamera"]
    assert texts(camera) == [(50, 40, meta["name"]), (50, 80, "High-Mag"), (50, 120, meta["timestamp"]),
                             (50, 160, "1")]
    assert Circle(40, 30, 3, COLOR, 1, fill=True) in camera
    assert all(s.color == COLOR and s.size == 30 for s in camera if isinstance(s, Text))
    infos = texts(result.overlays["highmaginfos"])
    assert infos == [(50, 60 - 240 + 40 * k, text) for k, text in enumerate(
        ["X (mm): -2.625", "Y (mm): 7.223", "Z (mm): 1.235", "Expo (µs): 10000.0", "WL (nm): 750"], 1)]
    scale = result.overlays["highmagscale"]
    bar = 50 * 250 / (0.170602 * 250)
    main = scale[0]
    assert isinstance(main, Line) and (main.x1, main.x2) == (pytest.approx(80 - bar - 100), 80 - 100)
    assert main.y1 == main.y2 == pytest.approx(0.98 * 60) and len(scale) == 6     # line, 4 arrow strokes, text
    assert texts(scale) == [(pytest.approx(80 - bar / 2 - 100), 60 - 80, "50 µm")]
    stored = rig.images.shapes("highmagcamera")
    assert stored == camera and rig.images.shapes("highmagscale") == scale


def test_overlays_without_devices_and_off():
    rig = Rig(devices={"xy": None, "z": None, "wl": None}, settings={"hardware.camera.highmag.units": 0})
    p = GetImageParams(camera_type=0, exposure_mode=ExposureMode.CURRENT, overlay_hw_infos=True, overlay_scale=True)
    result = rig.run(p)
    assert [t for *_, t in texts(result.overlays["highmaginfos"])] == [
        "X (mm): -", "Y (mm): -", "Z (mm): -", "Expo (µs): 10000.0", "WL (nm): -"]
    assert texts(result.overlays["highmagscale"])[0][2] == "50 pixels"
    off = rig.run(GetImageParams(camera_type=0, exposure_mode=ExposureMode.CURRENT))
    assert off.overlays == {"highmagcamera": (), "highmaginfos": (), "highmagscale": ()}


# ================================================================================================== runner, stop
def test_run_through_the_runner_with_the_api_payload(rig: Rig):
    result = rig.runner.run_once(FunctionName.GET_IMAGE, "Low Mag.", timeout=5)
    assert result.camera == "lowmag"
    topics = [e.topic for e in rig.events]
    assert topics[0] == "function.get_image.started" and topics[-1] == "function.get_image.finished"


def test_stop_between_states(rig: Rig):
    ctx = rig.ctx()
    ctx.stop_event.set()
    with pytest.raises(Stopped):
        rig.function.run(GetImageParams(), ctx)
    assert rig.hm.calls == []


def test_registry_builds_get_image(rig: Rig):
    runner = FunctionRunner()
    assert register_functions(runner, rig.services)[FunctionName.GET_IMAGE] == "registered"
    assert register_functions(FunctionRunner(), Services())[FunctionName.GET_IMAGE] == "skipped: no camera"


# ================================================================================================== live display
def test_live_view_runs_only_when_idle(rig: Rig):
    live = LiveView(rig.function, rig.runner, camera=lambda: CameraType.HIGH_MAG, interval_s=0.0)
    result = live.step()
    assert result is not None and result.camera == "highmag" and rig.states() == []        # no function events
    gate = threading.Event()

    class Blocking(GetImage):
        name = FunctionName.AUTOFOCUS_LARGE

        def run(self, params, ctx):
            gate.wait(5)

    rig.runner.register(Blocking(rig.services))
    rig.runner.start(FunctionName.AUTOFOCUS_LARGE)
    try:
        assert live.step() is None
    finally:
        gate.set()
        rig.runner.stop(timeout=5)


def test_live_view_thread_and_errors(rig: Rig):
    live = LiveView(rig.function, rig.runner, camera=lambda: 0, interval_s=0.001, error_interval_s=0.001)
    live.start()
    try:
        end = time.monotonic() + 5
        while rig.images.image_info("highmag")[0] < 3 and time.monotonic() < end:
            time.sleep(0.005)
        assert rig.images.image_info("highmag")[0] >= 3 and live.running
        rig.hm.grab_error = DeviceError("highmag", "lost")
        time.sleep(0.02)
        assert live.running                                           # an error does not stop the display
    finally:
        live.stop()
    assert not live.running
    with pytest.raises(ValueError):
        LiveView(rig.function, rig.runner, interval_s=-1)


# ================================================================================================== simulated camera
@pytest.fixture
def sim_camera() -> Iterator[Camera]:
    backend = SimCameraBackend(SimulationOptions(image_width=64, image_height=48, frame_rate=50.0))
    camera = Camera(backend, CameraSettings.default_high_mag(), name="highmag", sleep=lambda _s: None)
    camera.open(timeout=10)
    yield camera
    camera.shutdown(timeout=5)


def test_on_the_simulated_camera(sim_camera: Camera):
    profile = Store({"general.exposures": TABLE})
    images = ImageStore()
    services = Services(devices={"highmag": sim_camera}, profile=profile, settings=Store(),
                        exposures=ExposureTable(profile), images=images)
    function = GetImage(services, options=GetImageOptions(frame_margin_s=0.0), sleep=lambda _s: None)
    runner = FunctionRunner()
    runner.register(function)
    result = runner.run_once(FunctionName.GET_IMAGE, GetImageParams(camera_type=0, overlay_name=True), timeout=10)
    assert result.pixels.shape == (48, 64) and result.exposure_us == pytest.approx(20000.0, rel=0.01)
    assert sim_camera.exposure() == pytest.approx(20000.0, rel=0.01)        # no monochromator: WL 0 → first row
    images.enable_overlay("highmagcamera", True)
    assert images.jpeg(750, 32, 24)[:2] == b"\xff\xd8"
    assert GIState.FINISHED.value == "Finished"
