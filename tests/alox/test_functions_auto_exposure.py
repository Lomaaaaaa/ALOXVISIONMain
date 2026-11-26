"""Auto Exposure (:mod:`alox.functions.auto_exposure`) on the simulated monochromator and a fake camera whose image
brightness is ``sensitivity(wavelength) × exposure`` (a fake clock: no real waiting)."""

from __future__ import annotations

import math
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pytest

from alox.core.exposures import ExposureTable
from alox.core.functions import FunctionContext, FunctionName, FunctionRunner, Stopped
from alox.devices.monochromator import Monochromator, build_monochromator_backend
from alox.events import Event
from alox.functions.auto_exposure import (
    AEState,
    AutoExposure,
    AutoExposureError,
    AutoExposureMode,
    AutoExposureOptions,
    AutoExposureParams,
    FunctionFailed,
    WaitRule,
    ae_wait_s,
    fresh_frame_wait_s,
    masked_mean,
    oval_mask,
    pixel_size_um,
    select_wavelengths,
    settle_s,
    simple_line_from_two_points,
)
from alox.functions.services import Services
from alox.vision.circles import Circle

NAN = math.nan


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []
        self._lock = threading.Lock()
        self.on_sleep: Callable[[], None] | None = None

    def monotonic(self) -> float:
        with self._lock:
            return self.now

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self.now += max(0.0, seconds)
        if self.on_sleep is not None:
            self.on_sleep()

    def function_sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.sleep(seconds)


class DictProfile:
    def __init__(self, values: dict[str, Any] | None = None) -> None:
        self.values = dict(values or {})

    def get(self, dotted: str, default: Any = None) -> Any:
        return self.values.get(dotted, default)

    def set(self, dotted: str, value: Any) -> None:
        self.values[dotted] = value


@dataclass
class Shot:
    pixels: np.ndarray
    number: int = 0
    time: float = 0.0
    exposure_us: float = 0.0


@dataclass
class FakeCamera:
    """Uniform image of ``sensitivity(wl) × exposure`` grey levels (uint8, saturated), or ``scene(wl, exposure)``."""

    wl: Monochromator
    sensitivity: Callable[[float], float]
    exposure_us: float = 20000.0
    shape: tuple[int, int] = (48, 64)
    scene: Callable[[float, float], np.ndarray] | None = None
    exposures: list[float] = field(default_factory=list)
    grabs: int = 0

    def exposure(self) -> float:
        return self.exposure_us

    def set_exposure(self, microseconds: float) -> float:
        self.exposure_us = min(max(float(microseconds), 35.0), 2_000_000.0)
        self.exposures.append(self.exposure_us)
        return self.exposure_us

    def grab(self, *, newest: bool = True, timeout_ms: int | None = None) -> Shot:
        self.grabs += 1
        wl = float(self.wl.last_position or 0)
        if self.scene is not None:
            return Shot(self.scene(wl, self.exposure_us), self.grabs, 0.0, self.exposure_us)
        level = min(255.0, round(self.sensitivity(wl) * self.exposure_us))
        return Shot(np.full(self.shape, level, dtype=np.uint8), self.grabs, 0.0, self.exposure_us)


@dataclass
class Rig:
    clock: FakeTime
    wl: Monochromator
    camera: FakeCamera
    profile: DictProfile
    settings: DictProfile
    table: ExposureTable
    services: Services
    runner: FunctionRunner
    events: list[Event]

    def ctx(self) -> FunctionContext:
        return FunctionContext(FunctionName.AUTO_EXPOSURE, None, self.runner)

    def function(self, **options: Any) -> AutoExposure:
        return AutoExposure(self.services, options=AutoExposureOptions(**options))

    def states(self) -> list[str]:
        """The state changes (a measurement publishes its state again with its data)."""
        out: list[str] = []
        for e in self.events:
            if e.topic == "function.auto_exposure.state" and (not out or out[-1] != e.data["state"]):
                out.append(e.data["state"])
        return out

    def gotos(self) -> list[int]:
        return [int(e.data["target_nm"]) for e in self.wl_events if e.topic == "device.wl.move_started"]

    wl_events: list[Event] = field(default_factory=list)


TABLE = [[float(wl), 20000.0, 16000.0, 24000.0, NAN, NAN, NAN] for wl in (700, 750, 800, 850)]


@pytest.fixture
def make_rig() -> Iterator[Callable[..., Rig]]:
    devices: list[Monochromator] = []

    def factory(*, sensitivity: Callable[[float], float] = lambda wl: 0.004, start_nm: int = 750,
                profile: dict[str, Any] | None = None, scene: Any = None) -> Rig:
        clock = FakeTime()
        wl_events: list[Event] = []
        backend = build_monochromator_backend(simulate=True, sleep=clock.sleep, monotonic=clock.monotonic)
        wl = Monochromator(backend, publish=wl_events.append, reconnect_delay_s=0.0, sleep=clock.sleep)
        devices.append(wl)
        wl.open(timeout=10)
        wl.goto(start_nm)
        wl_events.clear()
        camera = FakeCamera(wl, sensitivity, scene=scene)
        values = {"autoexpo.exposurelow": 5000.0, "autoexpo.exposurehigh": 15000.0, "autoexpo.roidiameter": 20.0,
                  "general.exposures": [list(row) for row in TABLE]}
        values.update(profile or {})
        prof = DictProfile(values)
        settings = DictProfile({"hardware.camera.highmag.pixelsize": 1.0, "hardware.camera.highmag.units": 1})
        table = ExposureTable(prof)
        events: list[Event] = []
        services = Services(devices={"highmag": camera, "wl": wl}, profile=prof, settings=settings, exposures=table,
                            clock=clock.monotonic, sleep=clock.function_sleep)
        rig = Rig(clock, wl, camera, prof, settings, table, services, FunctionRunner(publish=events.append), events)
        rig.wl_events = wl_events
        return rig

    yield factory
    for wl in devices:
        wl.shutdown()


# ============================================================================================== helpers
def test_wait_rules_and_line() -> None:
    assert ae_wait_s(10000, 1) == pytest.approx(0.010 * 10 + 0.2)          # E ≤ 20 ms: ×10, +200 ms
    assert ae_wait_s(30000, 2) == pytest.approx(0.030 * 2 + 0.5)           # E > 20 ms: ×2, +500 ms
    assert settle_s(WaitRule.CHANGELOG, 10000, 40000) == pytest.approx(0.080)
    assert settle_s(WaitRule.FRESH_FRAME, 10000, 40000) == fresh_frame_wait_s(10000, 40000) == pytest.approx(0.050)
    assert fresh_frame_wait_s(NAN, 40000) == pytest.approx(0.080)               # unknown old exposure: the new one
    assert settle_s(WaitRule.GET_IMAGE, 10000, 40000) == pytest.approx(0.100 + 0.11 * 0.040)
    assert simple_line_from_two_points((0, 0), (1000, 50)) == (0.05, 0.0)
    assert simple_line_from_two_points((0, 0), (1000, -50))[0] == 0.05     # abs(): slope always ≥ 0
    a, b = simple_line_from_two_points((0, 0), (0, 50))
    assert math.isinf(a) and math.isnan(b)


def test_masks_pixel_size_and_selection() -> None:
    disc = oval_mask((21, 21), 10, 10, 3)
    assert disc[10, 10] and not disc[0, 0] and disc.sum() == 32
    ring = oval_mask((41, 41), 20, 20, 10, 6)
    assert not ring[20, 20] and ring[20, 28]
    assert math.isnan(masked_mean(np.ones((4, 4)), np.zeros((4, 4), dtype=bool)))
    settings = DictProfile({"hardware.camera.highmag.pixelsize": 0.002, "hardware.camera.highmag.units": 2})
    assert pixel_size_um(settings) == pytest.approx(2.0)
    settings.values["hardware.camera.highmag.units"] = 0
    assert pixel_size_um(settings) == 0.002 and pixel_size_um(settings, pixels_as_one=True) == 1.0
    assert select_wavelengths([0, 700, 750, 800, 950], 740, 810) == [750, 800]
    assert select_wavelengths([0, 700, 950], 760, 800) == [700]                 # nearest table wavelength


def test_params_from_profile_defaults() -> None:
    p = AutoExposureParams.from_profile(DictProfile())
    assert (p.mode, p.exposure_min_us, p.exposure_max_us, p.target_brightness, p.roi_diameter_um) == (
        AutoExposureMode.CURRENT_WAVELENGTH, 5000.0, 15000.0, 128, 100.0)
    assert not (p.show_window or p.overlay_roi or p.overlay_result or p.bypass_circle)
    p = AutoExposureParams.from_profile(DictProfile({"autoexpo.mode": 1, "autoexpo.bypassCircleIn": "TRUE"}))
    assert p.mode is AutoExposureMode.ALL_WAVELENGTHS and p.bypass_circle
    assert AutoExposureParams(mode="All Wavelengths").mode is AutoExposureMode.ALL_WAVELENGTHS


# ============================================================================================== runs
def test_current_wavelength_finds_and_stores_the_exposure(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()                                                  # 0.004 grey/µs: target 128 → 32 000 µs
    report = rig.function().run(None, rig.ctx())
    assert report.found_exposure
    assert report.exposure == pytest.approx(32000.0)
    assert (report.exposure1, report.exposure2) == (pytest.approx(12500.0), pytest.approx(25000.0))
    assert (report.brightness1, report.brightness2, report.brightness) == (50.0, 100.0, 128.0)
    assert report.wl_nm == 750
    assert rig.camera.exposures[:2] == [5000.0, 15000.0]             # Exposure Min, Exposure Max of the profile
    assert rig.camera.exposure_us == pytest.approx(32000.0)
    row = next(r for r in rig.table.rows() if r.wl == 750)
    assert (row.expo_auto, row.expo_min, row.expo_max, row.b_min, row.b_max) == (
        pytest.approx(32000.0), pytest.approx(12500.0), pytest.approx(25000.0), 50.0, 100.0)
    assert row.expo_th == pytest.approx(32000.0)                      # "Save calib values"
    assert next(r for r in rig.table.rows() if r.wl == 700).expo_th == 20000.0   # NaN ExpoAuto not copied (fix)
    states = rig.states()
    assert states[:6] == ["Start", "Started", "Set Exposure", "Wait", "Measure Expo", "Set Exposure"]
    assert states[-3:] == ["Measure Finished", "Set Auto Expo", "Finished"]
    assert rig.wl.last_position == 750
    # owner B3 (default): Set Exposure 100 ms, then old + new exposure: 20 + 5 ms; 100 ms; 5 + 15 ms; 15 + 32 ms
    assert rig.clock.slept == pytest.approx([0.1, 0.025, 0.1, 0.020, 0.047])


def test_as_drawn_waits(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    rig.function(wait_rule=WaitRule.AS_DRAWN).run(None, rig.ctx())
    # Set Exposure 100 ms; Wait 5 ms × 10 + 200 ms; 100 ms; 15 ms × 10 + 500 ms; Set Auto Expo 32 ms × 2 + 100 ms
    assert rig.clock.slept == pytest.approx([0.1, 0.25, 0.1, 0.65, 0.164])


def test_bright_scene_is_retried_with_its_own_exposures(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(sensitivity=lambda wl: 0.03)          # 5000 µs → 150 (retry), 15000 µs → 255 (saturated)
    report = rig.function().run(None, rig.ctx())
    # measure 1: 5000 → 2500 µs; measure 2 starts at Exposure Max (fix), saturated → 5000 µs, then 2500 µs
    assert rig.camera.exposures[:4] == [5000.0, 2500.0, 15000.0, 5000.0]
    assert rig.camera.exposures[4] == 2500.0
    assert report.found_exposure and report.exposure == pytest.approx(128 / 0.03, rel=1e-3)


def test_saturation_after_five_retries_fails_and_restores(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(sensitivity=lambda wl: 10.0)
    with pytest.raises(FunctionFailed) as info:
        rig.function().run(None, rig.ctx())
    assert info.value.code == 9091 and info.value.state == "Measure Expo"
    assert rig.camera.exposure_us == 20000.0                          # initial exposure restored (fix)
    assert rig.wl.last_position == 750
    assert rig.states()[-1] == "Error"


def test_all_wavelengths_inside_the_search_range(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(sensitivity=lambda wl: 0.004 * wl / 750.0, start_nm=600,
                   profile={"autoexpo.mode": 1, "wavelength.start": 740.0, "wavelength.end": 810.0})
    report = rig.function().run(None, rig.ctx())
    assert rig.gotos() == [750, 800, 600]                             # each wavelength, then the initial one
    rows = {r.wl: r for r in rig.table.rows()}
    assert rows[750.0].expo_auto == pytest.approx(32000.0, rel=1e-3)
    assert rows[800.0].expo_auto == pytest.approx(128 / (0.004 * 800 / 750), rel=1e-2)
    assert math.isnan(rows[700.0].expo_auto) and rows[700.0].expo_th == 20000.0
    assert (report.wl_nm, report.working_wl_nm) == (800, 0)
    assert rig.camera.exposures[0] == 16000.0                         # table ExpoMin of 750 nm
    assert rig.states().count("Set Wavelength") == 2 and rig.states().count("Check Wavelength") == 2


def test_bypass_circle_measures_in_the_input_circle(make_rig: Callable[..., Rig]) -> None:
    def scene(wl: float, exposure: float) -> np.ndarray:             # dark image, a disc 4× brighter at (16, 12)
        img = np.full((48, 64), min(255.0, round(0.001 * exposure)), dtype=np.uint8)
        img[oval_mask(img.shape, 16, 12, 6)] = min(255, round(0.004 * exposure))
        return img

    rig = make_rig(scene=scene, profile={"autoexpo.roidiameter": 8.0})          # ROI radius 4 px
    params = AutoExposureParams.from_profile(rig.profile, circle_in=Circle(16, 12, 5))
    centre = rig.function().run(params, rig.ctx())
    assert centre.exposure == pytest.approx(128000.0, rel=1e-2)       # image centre: 0.001 grey/µs
    bypass = AutoExposureParams.from_profile(
        DictProfile({**rig.profile.values, "autoexpo.bypassCircleIn": True}), circle_in=Circle(16, 12, 5))
    assert rig.function().run(bypass, rig.ctx()).exposure == pytest.approx(32000.0, rel=1e-2)


def test_bypass_without_circle_is_9089_before_any_move(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(profile={"autoexpo.bypassCircleIn": True})
    with pytest.raises(FunctionFailed) as info:
        rig.function().run(None, rig.ctx())
    assert info.value.code == 9089 and info.value.state == "Start"
    assert rig.gotos() == [] and rig.camera.exposures == []


def test_slope_not_finite_is_not_found(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(profile={"autoexpo.bypassCircleIn": True})
    params = AutoExposureParams.from_profile(rig.profile, circle_in=Circle(-500, -500, 3))   # ROI off the image
    with pytest.raises(FunctionFailed) as info:
        rig.function().run(params, rig.ctx())
    assert info.value.code == 9090 and not info.value.report.found_exposure
    assert rig.camera.exposure_us == 20000.0


def test_invalid_parameters_are_refused(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(profile={"autoexpo.roidiameter": 0.0})
    with pytest.raises(AutoExposureError) as info:
        rig.function().run(None, rig.ctx())
    assert info.value.code == 9089 and rig.camera.exposures == []


def test_stop_restores_the_wavelength(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(start_nm=600, profile={"autoexpo.mode": 1, "wavelength.start": 740.0, "wavelength.end": 810.0})
    ctx = rig.ctx()
    calls = []

    def stop_late() -> None:
        calls.append(1)
        if len(calls) == 3:
            ctx.stop_event.set()

    rig.clock.on_sleep = stop_late
    with pytest.raises(Stopped):
        rig.function().run(None, ctx)
    rig.clock.on_sleep = None
    assert rig.wl.last_position == 600 and rig.states()[-1] == "Stopped"
    assert rig.camera.exposure_us == 20000.0


def test_changelog_wait_rule(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    rig.function(wait_rule=WaitRule.CHANGELOG).run(None, rig.ctx())
    assert pytest.approx(2 * 20000e-6) in rig.clock.slept             # 2 × max(old 20 ms, new 5 ms)


def test_images_come_from_the_get_image_function_when_registered(make_rig: Callable[..., Rig]) -> None:
    from alox.functions.get_image import GetImage

    rig = make_rig()
    rig.runner.register(GetImage(rig.services, sleep=rig.clock.function_sleep))
    report = rig.function().run(None, rig.ctx())
    assert report.found_exposure and report.exposure == pytest.approx(32000.0)
    gi = [e for e in rig.events if e.topic == "function.get_image.state" and e.data["state"] == "Finished"]
    assert len(gi) == rig.camera.grabs == 4          # 2 measures, the Set Auto Expo image, the Finished refresh
    assert rig.services.images.last("highmag") is not None


def test_needs_the_high_mag_camera_and_the_monochromator() -> None:
    with pytest.raises(LookupError, match="'wl'"):
        AutoExposure(Services(devices={"highmag": object()}))
    with pytest.raises(LookupError, match="'highmag'"):
        AutoExposure(Services(devices={"wl": object(), "lowmag": object()}))


def test_states_are_the_labview_names() -> None:
    assert [s.value for s in AEState] == [
        "Start", "Started", "Set Wavelength", "Check Wavelength", "Set Exposure", "Wait", "Measure Expo",
        "Measure Finished", "Set Auto Expo", "Finished", "Error", "Stopped"]
