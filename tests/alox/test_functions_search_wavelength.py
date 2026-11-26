"""Search Wavelength (:mod:`alox.functions.search_wavelength`) on the simulated monochromator and a fake camera whose
image is a mesa: a central spot (non-oxidised) and an annulus (oxidised) whose brightness peaks at 800 nm (fake
clock: no real waiting)."""

from __future__ import annotations

import math
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from alox.core.exposures import ExposureTable
from alox.core.functions import FunctionContext, FunctionName, FunctionRunner, Stopped
from alox.devices.monochromator import Monochromator, build_monochromator_backend
from alox.events import Event
from alox.functions.auto_exposure import FunctionFailed, WaitRule, oval_mask
from alox.functions.search_wavelength import (
    DetectCircleMethod,
    MesaLocation,
    SearchWavelength,
    SearchWavelengthError,
    SearchWavelengthOptions,
    SearchWavelengthParams,
    SpectrumPoint,
    SWState,
    WLMode,
    WLRoiType,
    less_far_and_found,
    refocus_due,
    wavelength_data_text,
    wavelength_rois,
)
from alox.functions.services import Circle as CircleShape
from alox.functions.services import Services
from alox.vision.circles import Circle

NAN = math.nan
SHAPE = (64, 64)
CENTRE = (32.0, 32.0)


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

    def function_sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.sleep(seconds)
        if self.on_sleep is not None:
            self.on_sleep()


class DictProfile:
    def __init__(self, values: dict[str, Any] | None = None) -> None:
        self.values = dict(values or {})

    def get(self, dotted: str, default: Any = None) -> Any:
        return self.values.get(dotted, default)

    def set(self, dotted: str, value: Any) -> None:
        self.values[dotted] = value


def oxide_gain(wl: float) -> float:
    """Grey levels per µs of the annulus: peak at 800 nm."""
    return 0.010 + 0.006 * math.exp(-(((wl - 800.0) / 20.0) ** 2))


@dataclass
class Shot:
    pixels: np.ndarray
    number: int = 0
    time: float = 0.0
    exposure_us: float = 0.0


@dataclass
class MesaCamera:
    wl: Monochromator
    spot_gain: float = 0.008
    exposure_us: float = 20000.0
    centre: tuple[float, float] = CENTRE
    exposures: list[float] = field(default_factory=list)
    grabs: list[float] = field(default_factory=list)

    def exposure(self) -> float:
        return self.exposure_us

    def set_exposure(self, microseconds: float) -> float:
        self.exposure_us = min(max(float(microseconds), 35.0), 2_000_000.0)
        self.exposures.append(self.exposure_us)
        return self.exposure_us

    def grab(self, *, newest: bool = True, timeout_ms: int | None = None) -> Shot:
        wl = float(self.wl.last_position or 0)
        self.grabs.append(wl)
        e = self.exposure_us
        img = np.full(SHAPE, min(255, round(0.002 * e)), dtype=np.uint8)
        cx, cy = self.centre
        img[oval_mask(SHAPE, cx, cy, 12.5, 7.5)] = min(255, round(oxide_gain(wl) * e))
        img[oval_mask(SHAPE, cx, cy, 4.5)] = min(255, round(self.spot_gain * e))
        return Shot(img, len(self.grabs), 0.0, e)


@dataclass
class Rig:
    clock: FakeTime
    wl: Monochromator
    camera: MesaCamera
    profile: DictProfile
    settings: DictProfile
    services: Services
    runner: FunctionRunner
    events: list[Event]
    wl_events: list[Event]

    def ctx(self) -> FunctionContext:
        return FunctionContext(FunctionName.SEARCH_WAVELENGTH, None, self.runner)

    def function(self, **kwargs: Any) -> SearchWavelength:
        options = SearchWavelengthOptions(**kwargs.pop("options", {}))
        return SearchWavelength(self.services, options=options, **kwargs)

    def states(self) -> list[str]:
        out: list[str] = []
        for e in self.events:
            if e.topic == "function.search_wavelength.state" and (not out or out[-1] != e.data["state"]):
                out.append(e.data["state"])
        return out

    def gotos(self) -> list[int]:
        return [int(e.data["target_nm"]) for e in self.wl_events if e.topic == "device.wl.move_started"]

    def params(self, **changes: Any) -> SearchWavelengthParams:
        values = {"wl_mode": WLMode.SCAN, "start_wl": 760.0, "end_wl": 840.0, "step_wl": 10.0, "min_contrast": 20.0,
                  "roi_type": WLRoiType.SPOT_ANNULAR, "annul_spot_size_um": 8.0, "annul_diam1_um": 16.0,
                  "annul_diam2_um": 24.0, "circle_in": Circle(*CENTRE, 12.0), "overlay_rois": True}
        values.update(changes)
        return SearchWavelengthParams(**values)


@pytest.fixture
def make_rig() -> Iterator[Callable[..., Rig]]:
    devices: list[Monochromator] = []

    def factory(*, settings: dict[str, Any] | None = None, **camera: Any) -> Rig:
        clock = FakeTime()
        wl_events: list[Event] = []
        backend = build_monochromator_backend(simulate=True, sleep=clock.sleep, monotonic=clock.monotonic)
        wl = Monochromator(backend, publish=wl_events.append, reconnect_delay_s=0.0, sleep=clock.sleep)
        devices.append(wl)
        wl.open(timeout=10)
        wl.goto(600)
        wl_events.clear()
        cam = MesaCamera(wl, **camera)
        prof = DictProfile({"general.exposures": [[700.0, 10000.0, 8000.0, 12000.0, NAN, NAN, NAN],
                                                  [950.0, 10000.0, 8000.0, 12000.0, NAN, NAN, NAN]]})
        sets = DictProfile({"hardware.camera.highmag.pixelsize": 1.0, "hardware.camera.highmag.units": 1,
                            **(settings or {})})
        events: list[Event] = []
        services = Services(devices={"highmag": cam, "wl": wl}, profile=prof, settings=sets,
                            exposures=ExposureTable(prof), clock=clock.monotonic, sleep=clock.function_sleep)
        return Rig(clock, wl, cam, prof, sets, services, FunctionRunner(publish=events.append), events, wl_events)

    yield factory
    for wl in devices:
        wl.shutdown()


# ============================================================================================== pure parts
def test_rois_of_both_types() -> None:
    p = SearchWavelengthParams(roi_type="Dual Spots", dual_spots_size_um=4.0, dual_spots_distance_um=10.0,
                               dual_spots_angle_deg=90.0, wl_mode="SCAN")
    rois = wavelength_rois(p, Circle(30, 20, 9), pixel_um=2.0)
    assert (rois.non_oxyd.x, rois.non_oxyd.y, rois.non_oxyd.radius) == (30, 20, 1.0)
    assert rois.oxyd.x == pytest.approx(30.0) and rois.oxyd.y == pytest.approx(15.0)      # y = cy − d·sin(a)
    p = SearchWavelengthParams(roi_type=WLRoiType.SPOT_ANNULAR, annul_spot_size_um=8, annul_diam1_um=30,
                               annul_diam2_um=20)
    rois = wavelength_rois(p, Circle(30, 20, 9), pixel_um=1.0)
    assert (rois.oxyd.radius, rois.oxyd.inner_radius) == (15.0, 10.0)                  # [min, max] of the radii
    with pytest.raises(SearchWavelengthError) as info:
        wavelength_rois(replace_mode(p, WLMode.SCAN), Circle(0, 0, 0), 1.0)
    assert info.value.code == 9042


def replace_mode(p: SearchWavelengthParams, mode: WLMode) -> SearchWavelengthParams:
    from dataclasses import replace
    return replace(p, wl_mode=mode)


def test_refocus_rule_and_less_far() -> None:
    assert [w for w in range(760, 850, 10) if refocus_due(w, 760, 20)] == [780, 800, 820, 840]
    assert not refocus_due(760, 760, 20) and not refocus_due(780, 760, 0)
    assert less_far_and_found(True, Circle(32, 32, 5), Circle(30, 30, 9), 10)
    assert not less_far_and_found(True, Circle(60, 60, 5), Circle(30, 30, 9), 10)
    assert not less_far_and_found(False, Circle(30, 30, 5), Circle(30, 30, 9), 10)


def test_params_from_profile_defaults_and_data_text() -> None:
    p = SearchWavelengthParams.from_profile(DictProfile())
    assert (p.wl_mode, p.start_wl, p.end_wl, p.step_wl, p.min_contrast, p.roi_type) == (
        WLMode.MANUAL, 700.0, 950.0, 2.0, 20.0, WLRoiType.DUAL_SPOTS)                # start 700: owner D4
    assert (p.dual_spots_size_um, p.dual_spots_distance_um, p.dual_spots_angle_deg) == (2.0, 5.0, 45.0)
    assert (p.annul_spot_size_um, p.annul_diam1_um, p.annul_diam2_um) == (2.0, 20.0, 30.0)
    assert p.detect_circle_method is DetectCircleMethod.SINGLE and (p.step_refocus, p.wl_shift_nm) == (0, 0)
    text = wavelength_data_text([SpectrumPoint(760, 80, 101, 21, 1e4)])
    assert text == "WL\tNOXYD\tOXYD\tCONTRAST\r\n760.000\t80.000\t101.000\t21.000\r\n"


# ============================================================================================== runs
def test_scan_finds_the_wavelength_of_maximal_contrast(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    report = rig.function().run(rig.params(), rig.ctx())
    assert report.valid and report.optimal_wl_nm == 800.0
    assert report.optimal_contrast == pytest.approx(160 - 80)
    assert report.optimal_expo_us == 10000.0
    assert [p.wl_nm for p in report.spectrum] == [760.0 + 10 * k for k in range(9)]
    assert report.progress == pytest.approx((840 - 760 + 1) / 80 * 100)          # as drawn: > 100 at the end
    assert rig.gotos() == [760, *range(760, 850, 10), 800]                         # Init WL, Move WL ×9, Move Opt WL
    assert rig.wl.last_position == 800 and rig.camera.exposure_us == 10000.0
    assert rig.states()[:8] == ["Start", "Started", "Init WL", "Move WL", "WL Pos OK ?", "Refocus", "Detect circle",
                                "Measure"]
    assert rig.states()[-3:] == ["Move Opt WL", "Check Opt WL", "Finished"]
    shapes = rig.services.images.overlays["wavelength"]                            # type: ignore[attr-defined]
    assert {(s.cx, s.cy, s.r) for s in shapes if isinstance(s, CircleShape)} == {(32, 32, 4), (32, 32, 12),
                                                                                  (32, 32, 8)}
    assert 1.0 in rig.clock.slept                                                  # Init WL: 1 s after Get Image


def test_wl_shift_and_negative_min_contrast(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    assert rig.function().run(rig.params(wl_shift_nm=5), rig.ctx()).optimal_wl_nm == 805.0
    assert rig.wl.last_position == 805
    rig = make_rig(spot_gain=0.02)                                                 # contrast < 0 everywhere
    report = rig.function().run(rig.params(min_contrast=-5.0), rig.ctx())
    assert report.valid and report.optimal_wl_nm == 760.0                          # first minimum
    assert report.optimal_contrast < -5


def test_manual_mode_moves_only(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    report = rig.function().run(SearchWavelengthParams(wl_mode="MANUAL", manual_wl_nm=820), rig.ctx())
    assert (report.valid, report.optimal_wl_nm, report.current_wl_nm, report.progress) == (True, 820.0, 820.0, 100.0)
    assert report.optimal_expo_us == 10000.0 and report.spectrum == ()
    assert rig.gotos() == [820, 820, 820]
    assert "Measure" not in rig.states() and rig.camera.exposure_us == 10000.0


def test_contrast_below_minimum_fails_and_restores(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    with pytest.raises(FunctionFailed) as info:
        rig.function().run(rig.params(min_contrast=200.0), rig.ctx())
    assert info.value.code == 9042 and info.value.state == "Check Opt WL"
    assert not info.value.report.valid and info.value.report.optimal_wl_nm == 800.0
    assert rig.wl.last_position == 600                                             # initial wavelength restored


def test_empty_circle_in_scan_mode_is_9042(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    with pytest.raises(FunctionFailed) as info:
        rig.function().run(rig.params(circle_in=None), rig.ctx())
    assert info.value.code == 9042 and info.value.state == "Detect circle"
    assert rig.wl.last_position == 600


def test_refocus_every_step_and_its_retries(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    seen: list[int] = []

    def refocus(ctx: FunctionContext) -> bool:
        seen.append(int(rig.wl.last_position or 0))
        return True

    rig.function(refocus=refocus).run(rig.params(step_refocus=20), rig.ctx())
    assert seen == [780, 800, 820, 840]
    rig = make_rig()
    calls: list[int] = []
    with pytest.raises(FunctionFailed) as info:
        rig.function(refocus=lambda ctx: calls.append(1) or False).run(rig.params(step_refocus=20), rig.ctx())
    assert len(calls) == 6 and info.value.state == "Refocus" and info.value.report.error_ref_count == 5
    assert rig.wl.last_position == 600


def test_refocus_without_a_refocus_function_fails(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    with pytest.raises(FunctionFailed) as info:
        rig.function().run(rig.params(step_refocus=20), rig.ctx())
    assert info.value.code == 9043


def test_detected_circle_recentres_the_rois(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    found: list[float] = []

    def locate(ctx: FunctionContext, pixels: np.ndarray) -> MesaLocation:
        found.append(float(rig.wl.last_position or 0))
        return MesaLocation(True, True, 100.0, 24.0, Circle(32, 32, 12), Circle(32, 32, 6))

    report = rig.function(locate=locate).run(rig.params(circle_in=Circle(30, 30, 12)), rig.ctx())
    assert found == [760.0]                                                         # SINGLE: at the start WL
    shapes = rig.services.images.overlays["wavelength"]                            # type: ignore[attr-defined]
    assert {(s.cx, s.cy) for s in shapes} == {(32.0, 32.0)}
    assert report.optimal_wl_nm == 800.0
    rig = make_rig()
    found.clear()
    params = rig.params(circle_in=Circle(30, 30, 12), detect_circle_method=DetectCircleMethod.FULL)
    rig.function(locate=lambda ctx, px: (found.append(1), MesaLocation(
        True, True, 100.0, 24.0, Circle(60, 60, 12), Circle(60, 60, 6)))[1]).run(params, rig.ctx())
    assert len(found) == 9                                                          # FULL: at every WL
    shapes = rig.services.images.overlays["wavelength"]                            # type: ignore[attr-defined]
    assert {(s.cx, s.cy) for s in shapes} == {(30.0, 30.0)}                         # too far: "no move"


def test_files_are_written_when_enabled(make_rig: Callable[..., Rig], tmp_path: Path) -> None:
    rig = make_rig(settings={"log.savewavelengthdata": True, "log.savewavelengthimage": True})
    options = {"image_folder": tmp_path / "img", "data_folder": tmp_path / "data"}
    rig.function(options=options).run(rig.params(), rig.ctx())
    images = sorted(p.name for p in (tmp_path / "img").iterdir())
    assert len(images) == 9 and "800nm_10000us.png" in images
    (wal,) = (tmp_path / "data").iterdir()
    assert wal.suffix == ".wal" and wal.read_text().splitlines()[0] == "WL\tNOXYD\tOXYD\tCONTRAST"
    assert len(wal.read_text().splitlines()) == 10


def test_stop_restores_the_wavelength(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    ctx = rig.ctx()
    rig.clock.on_sleep = lambda: ctx.stop_event.set() if len(rig.camera.grabs) >= 3 else None
    with pytest.raises(Stopped):
        rig.function().run(rig.params(), ctx)
    assert rig.wl.last_position == 600 and rig.states()[-1] == "Stopped"


def test_invalid_scan_parameters_are_refused(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    with pytest.raises(SearchWavelengthError) as info:
        rig.function().run(rig.params(step_wl=0.0), rig.ctx())
    assert info.value.code == 9041 and rig.gotos() == []
    with pytest.raises(SearchWavelengthError) as info:
        rig.function().run(rig.params(annul_spot_size_um=0.0), rig.ctx())
    assert info.value.code == 9042


def test_wait_rules(make_rig: Callable[..., Rig]) -> None:
    def waits(rule: WaitRule) -> list[float]:
        rig = make_rig()
        rig.function(options={"wait_rule": rule}).run(rig.params(), rig.ctx())
        return [s for s in rig.clock.slept if s < 0.5]                              # not the 1 s of Init WL

    # owner B3: old + new exposure after the first change (20 → 10 ms), then 10 + 10 ms after each new wavelength
    assert waits(WaitRule.FRESH_FRAME) == pytest.approx([0.030] + [0.020] * 8)
    assert waits(WaitRule.CHANGELOG) == pytest.approx([0.040] + [0.020] * 8)         # 2 × max(old, new)
    assert waits(WaitRule.AS_DRAWN) == pytest.approx([0.100 + 0.11 * 0.020])         # exposure changes only


def test_images_come_from_the_get_image_function_when_registered(make_rig: Callable[..., Rig]) -> None:
    from alox.functions.get_image import GetImage

    rig = make_rig()
    rig.runner.register(GetImage(rig.services, sleep=rig.clock.function_sleep))
    report = rig.function().run(rig.params(), rig.ctx())
    assert report.valid and report.optimal_wl_nm == 800.0
    gi = [e for e in rig.events if e.topic == "function.get_image.state" and e.data["state"] == "Finished"]
    assert len(gi) == len(rig.camera.grabs) == 10                                   # Init WL + 9 Detect circle
    assert rig.camera.exposures[0] == 10000.0                                       # Get Image "Fixed": table


def test_needs_the_high_mag_camera_and_the_monochromator() -> None:
    with pytest.raises(LookupError, match="'wl'"):
        SearchWavelength(Services(devices={"highmag": object()}))
    with pytest.raises(LookupError, match="'highmag'"):
        SearchWavelength(Services(devices={"wl": object(), "lowmag": object()}))


def test_states_are_the_labview_names() -> None:
    assert [s.value for s in SWState] == [
        "Start", "Started", "Init WL", "Move WL", "WL Pos OK ?", "Refocus", "Detect circle", "Measure",
        "Move Opt WL", "Check Opt WL", "Finished", "Error", "Stopped"]
