"""HL Bowing measurement (``alox.functions.bowing``) on the simulated XY stage, with fake LL autofocus/refocus/get-image
functions registered on a real :class:`FunctionRunner` (the refocus returns the Z of a known surface under the XY
position, so bowing and parallelism are known)."""

from __future__ import annotations

import math
import threading
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from alox.api.backend import Measure
from alox.core.core import MeasureRequest
from alox.core.folders import Folders
from alox.core.functions import FunctionContext, FunctionName, FunctionRunner, JobInfo, MeasurementFunction, Stopped
from alox.core.status import FunctionStatusID
from alox.devices.base import DeviceError
from alox.devices.xy import WaferSettings, XYSettings, XYStage
from alox.events import Event
from alox.functions.bowing import (
    BOWING_ERROR,
    PARALLELISM_ERROR,
    BowingError,
    BowingFailed,
    BowingMeasurement,
    BowingOptions,
    BowingParams,
    BowState,
    autofocus_params_from_profile,
    bowing_positions,
    chuck_properties,
    progress,
    refocus_params_from_profile,
    wafer_to_stage,
)
from alox.functions.focus import AutofocusMethod, AutofocusParams, CameraType, FocusResult, RefocusParams
from alox.functions.get_image import stage_to_wafer
from alox.functions.registry import register_functions
from alox.functions.services import Services
from aloxcam.settings import SimulationOptions
from aloxcam.zstage.sim import SimStage

SIM = SimulationOptions(stage_min_mm=0.0, stage_max_mm=200.0, stage_reference_s=0.01)
ORIGIN = (100.0, 100.0)
WHEN = datetime(2026, 11, 26, 9, 15, 30)
JOB = JobInfo(control_job_id="CJ1", process_job_id="PJ1", lot_id="LOT1", wafer_id="W01", zone_id="0")
WAFER, OFFSET, ANGLE, DIST = 100.0, 5.0, 45.0, 40.0


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


class Store:
    def __init__(self, values: dict[str, Any] | None = None) -> None:
        self.values = dict(values or {})

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)


class Log:
    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []

    def info(self, text: str) -> None:
        self.lines.append(("info", text))

    def warning(self, text: str) -> None:
        self.lines.append(("warning", text))

    def error(self, text: str) -> None:
        self.lines.append(("error", text))


class FakeZ:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def set_joystick(self, enabled: bool) -> None:
        self.calls.append(("joystick", enabled))

    def move_absolute(self, z: float, *, wait: bool = True) -> float:
        self.calls.append(("move", z, wait))
        return z


class FakeWL:
    def __init__(self, position: int = 850, *, fail: bool = False) -> None:
        self.pos = position
        self.fail = fail
        self.gotos: list[int] = []

    def position(self) -> int:
        if self.fail:
            raise DeviceError("wl", "WL Timeout", code=9403)
        return self.pos

    def goto(self, nm: int) -> int:
        self.gotos.append(nm)
        self.pos = nm
        return nm


def wafer_z(wx: float, wy: float) -> float:
    """The surface under the camera: a paraboloid wafer (r ≤ 50 mm) on a chuck tilted along X."""
    if math.hypot(wx, wy) <= WAFER / 2:
        return 30.0 + 1e-5 * (wx * wx + wy * wy)
    return 29.0 + 1e-4 * wx


def expected() -> dict[str, float]:
    p = bowing_positions(WAFER, OFFSET, ANGLE, DIST)
    z = [wafer_z(*xy) for xy in p]                                      # ZW2 ZW3 ZC3 ZW1 ZC1 ZW4 ZC4 ZW5 ZC5
    scale = WAFER / (2 * DIST) * 1000.0
    return {"bow_a": ((z[3] + z[1]) / 2 - z[0]) * scale, "bow_b": ((z[5] + z[7]) / 2 - z[0]) * scale,
            "para_a": ((z[4] - z[3]) - (z[2] - z[1])) * 1000.0, "para_b": ((z[6] - z[5]) - (z[8] - z[7])) * 1000.0,
            "z": z}


class FakeFocus(MeasurementFunction):
    """LL autofocus / refocus: the Z of the surface under the current XY position (or a scripted outcome)."""

    def __init__(self, name: FunctionName, xy: XYStage, outcomes: list[Any] | None = None,
                 on_call: Callable[[int], None] | None = None) -> None:
        self.name = name
        self.xy = xy
        self.outcomes = outcomes or []
        self.calls: list[Any] = []
        self.on_call = on_call

    def run(self, params: Any, ctx: FunctionContext) -> FocusResult:
        self.calls.append(params)
        if self.on_call is not None:
            self.on_call(len(self.calls))
        outcome = self.outcomes.pop(0) if self.outcomes else "surface"
        if isinstance(outcome, BaseException):
            raise outcome
        if outcome == "surface":
            sx, sy = self.xy.position()
            z: float | None = wafer_z(*stage_to_wafer(sx, sy, ORIGIN, 3))
        else:
            z = outcome
        return FocusResult(table=((0.0, 1.0),), best_z=z, best_score=1.0, best_index=None if z is None else 0,
                           position=z, progress=100.0)


class FakeGetImage(MeasurementFunction):
    name = FunctionName.GET_IMAGE

    def __init__(self, services: Services) -> None:
        self.services = services
        self.calls: list[Any] = []

    def run(self, params: Any, ctx: FunctionContext) -> Any:
        self.calls.append(params)
        camera = "highmag" if params == 0 else "lowmag"
        self.services.images.put(camera, np.full((8, 8), len(self.calls), dtype=np.uint8))
        return None


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Callable[..., SimpleNamespace]]:
    created: list[XYStage] = []

    def build(*, profile: dict[str, Any] | None = None, settings: dict[str, Any] | None = None,
              autofocus: list[Any] | None = None, refocus: list[Any] | None = None, wl: FakeWL | None = None,
              xy_cls: type[XYStage] = XYStage, options: BowingOptions | None = None,
              on_autofocus: Callable[[int], None] | None = None, get_image: bool = True) -> SimpleNamespace:
        clock = FakeTime()
        sims = [SimStage(SIM, clock=clock.monotonic, dh_running=False) for _ in "xy"]
        wafer = WaferSettings(diameter_mm=WAFER, origin=ORIGIN, limits_enabled=True)
        xy = xy_cls(sims[0], sims[1], XYSettings(wafer=wafer), sleep=clock.sleep, monotonic=clock.monotonic,
                    supervise=False, reconnect_delay_s=0.0)
        xy.open(timeout=10)
        created.append(xy)
        events: list[Event] = []
        status_events: list[Event] = []
        folders = Folders(exe=tmp_path / "exe", settings=tmp_path / "settings", vision=tmp_path / "vision",
                          data=tmp_path / "data", profiles=tmp_path / "profiles", exposures=tmp_path / "exposures",
                          jobhistory=tmp_path / "jobhistory")
        values = {"general.wafersize": WAFER, "general.bowingOffset": OFFSET, "general.bowingAngle": ANGLE,
                  "hardware.xy.waferxorg": ORIGIN[0], "hardware.xy.waferyorg": ORIGIN[1],
                  "hardware.xy.stagescoordsys": 3, "hardware.xy.speed": 50.0, "hardware.chuck.maxparallelism": 10.0}
        values.update(settings or {})
        prof = {"measure.bowing.distance": DIST, "measure.bowing.maxbowing": 50.0, "wavelength.wlmanual": 700}
        prof.update(profile or {})
        z, fake_wl, log = FakeZ(), wl or FakeWL(), Log()
        services = Services(devices={"xy": xy, "z": z, "wl": fake_wl}, profile=Store(prof), settings=Store(values),
                            folders=folders, publish=events.append, log=log, clock=clock.monotonic,
                            statuses={"bowing": FunctionStatusID("bowing", publish=status_events.append)})
        runner = FunctionRunner()
        af = FakeFocus(FunctionName.AUTOFOCUS_LARGE, xy, autofocus, on_autofocus)
        rf = FakeFocus(FunctionName.REFOCUS, xy, refocus)
        gi = FakeGetImage(services)
        for function in (af, rf) + ((gi,) if get_image else ()):
            runner.register(function)
        bowing = BowingMeasurement(services, options=options, sleep=clock.sleep, now=lambda: WHEN)
        runner.register(bowing)
        ctx = FunctionContext(FunctionName.BOWING_MEASURE, JOB, runner)
        states: list[tuple[str, dict[str, Any]]] = []
        original = ctx.set_state

        def record(state: str, **data: Any) -> None:
            states.append((state, data))
            original(state, **data)
        ctx.set_state = record                                          # type: ignore[method-assign]
        return SimpleNamespace(xy=xy, sims=sims, clock=clock, events=events, status_events=status_events,
                               services=services, runner=runner, af=af, rf=rf, gi=gi, bowing=bowing, ctx=ctx, z=z,
                               wl=fake_wl, log=log, folders=folders, states=states)

    yield build
    for xy in created:
        xy.shutdown(timeout=5)


def statuses(r: SimpleNamespace) -> list[int]:
    return [int(e.data["code"]) for e in r.status_events]


def ws(r: SimpleNamespace, name: str) -> list[Event]:
    return [e for e in r.events if e.topic == f"ws.{name}"]


def state_names(r: SimpleNamespace) -> list[str]:
    names: list[str] = []
    for state, data in r.states:
        if "event" not in data and (not names or names[-1] != state):
            names.append(state)
    return names


# ================================================================================================== pure parts
def test_positions_follow_the_diagram_order_and_signs() -> None:
    p = bowing_positions(100.0, 5.0, 45.0, 40.0)
    d, r = 40.0 / math.sqrt(2), 55.0 / math.sqrt(2)
    want = [(0, 0), (d, d), (r, r), (-d, -d), (-r, -r), (d, -d), (r, -r), (-d, d), (-r, r)]
    for got, exp in zip(p, want, strict=True):
        assert got == pytest.approx(exp)
    flat = bowing_positions(100.0, 0.0, 0.0, 30.0, centre=(2.0, 2.0))
    assert flat[0] == (2.0, 2.0) and flat[1] == pytest.approx((30.0, 0.0)) and flat[8] == pytest.approx((-50.0, 0.0))


@pytest.mark.parametrize("system", [0, 1, 2, 3])
def test_wafer_to_stage_inverts_the_get_image_conversion(system: int) -> None:
    stage = wafer_to_stage((12.5, -7.25), (77.375, 77.223), system)
    assert stage_to_wafer(*stage, (77.375, 77.223), system) == pytest.approx((12.5, -7.25))
    assert wafer_to_stage((10.0, 5.0), (100.0, 100.0), 3) == (90.0, 95.0)          # #4: both axes indirect


def test_params_from_profile_request_and_checks() -> None:
    p = BowingParams.from_profile(Store())
    assert p == BowingParams() and p.camera_type is CameraType.LOW_MAG and p.measure_distance_mm == 0.0
    prof = Store({"measure.bowing.camera": "High Mag.", "measure.bowing.distance": 35, "measure.bowing.maxbowing": 20,
                  "measure.bowing.methodFast": True, "measure.bowing.overlaybowing": 1})
    p = BowingParams.from_profile(prof)
    assert (p.camera_type, p.measure_distance_mm, p.max_bowing_um, p.fast_method, p.overlay_bowing) == \
        (CameraType.HIGH_MAG, 35.0, 20.0, True, True)
    with pytest.raises(BowingError) as err:
        BowingParams().check()
    assert err.value.code == 9002
    with pytest.raises(BowingError):
        BowingParams(camera_type="Medium Mag.")
    with pytest.raises(BowingError):
        BowingParams(measure_distance_mm=math.inf, max_bowing_um=1).check()


def test_chuck_properties_and_ll_parameters() -> None:
    settings = Store({"hardware.chuck.dz1": 0.01, "hardware.chuck.dz3": 0.03, "hardware.chuck.dz4": 0.04,
                      "hardware.chuck.dz5": 0.05, "hardware.chuck.maxparallelism": 15, "general.wafersize": 150,
                      "hardware.z.zminautofocus": 33.2, "hardware.z.zmaxautofocus": 34.0})
    chuck = chuck_properties(settings)
    assert (chuck.wafer_diameter_mm, chuck.dz1, chuck.dz3, chuck.dz4, chuck.dz5, chuck.max_parallelism_um) == \
        (150.0, 0.01, 0.03, 0.04, 0.05, 15.0)
    assert chuck_properties(Store()).wafer_diameter_mm == 0.0
    af = autofocus_params_from_profile(Store({"autofocus.step": 0.01}), settings, CameraType.LOW_MAG)
    assert isinstance(af, AutofocusParams)
    assert (af.camera_type, af.z_min, af.z_max, af.step, af.subsampling, af.duration_s) == \
        (CameraType.LOW_MAG, 33.2, 34.0, 0.01, 0.25, 10)
    rf = refocus_params_from_profile(Store({"refocus.method": 0}), CameraType.HIGH_MAG)
    assert isinstance(rf, RefocusParams)
    assert (rf.method, rf.steps, rf.step_mm, rf.fin_roi_width, rf.camera_type) == \
        (AutofocusMethod.SOBEL, 4, 0.05, 128, CameraType.HIGH_MAG)
    assert progress(0) == 0.0 and progress(9) == 100.0 and progress(3) == pytest.approx(100 / 3)


# ================================================================================================== full runs
def test_a_full_run_measures_the_nine_points(rig: Callable[..., SimpleNamespace]) -> None:
    r = rig()
    report = r.bowing.run(MeasureRequest(Measure.BOWING, JOB, "DEFAULT", {"x": DIST}), r.ctx)
    exp = expected()
    v = report.values
    assert [row[2] for row in report.z_measures] == pytest.approx(exp["z"], abs=1e-6)
    assert v.bowing_a_um == pytest.approx(exp["bow_a"], abs=1e-3) and v.bowing_a_um == pytest.approx(20.0, abs=1e-3)
    assert v.bowing_b_um == pytest.approx(exp["bow_b"], abs=1e-3)
    assert v.parallelism_a_um == pytest.approx(exp["para_a"], abs=1e-3)
    assert v.parallelism_b_um == pytest.approx(exp["para_b"], abs=1e-3)
    assert v.bowing_valid and v.parallelism_valid and report.valid and report.status == 500
    assert v.zwafer2 == pytest.approx(30.0) and v.zchuck5 == pytest.approx(exp["z"][8], abs=1e-6)
    # statuses: per point move, wait, autofocus, refocus, store; then the end
    codes = statuses(r)
    assert codes[:7] == [201, 202, 203, 212, 221, 230, 239]
    assert codes[7:12] == [204, 213, 222, 231, 240]
    assert codes[-4:] == [247, 300, 248, 500]
    assert 207 in codes and 216 in codes and 211 in codes and 220 in codes   # chuck points wait too (fixed)
    # LL calls: 9 autofocus, 9 refocus with Sobel and the bowing camera; Get Image before/after
    assert len(r.af.calls) == 9 and len(r.rf.calls) == 9
    assert all(p.method is AutofocusMethod.SOBEL and p.camera_type is CameraType.LOW_MAG for p in r.rf.calls)
    assert len(r.gi.calls) == 9 * 3 + 1 and set(r.gi.calls) == {1}
    # websocket measure_bowing, no error event
    [bow] = ws(r, "measure_bowing")
    assert bow.data["Job"]["Process_Job_ID"] == "PJ1"
    assert float(bow.data["Bowing"]["bowinga"]) == pytest.approx(v.bowing_a_um, abs=1e-5)
    assert float(bow.data["Parallelism"]["zchuck3"]) == pytest.approx(v.zchuck3, abs=1e-5)
    assert not ws(r, "error") and not ws(r, "failed")
    # devices: WL re-set (Low Mag.) then wlmanual; Z to Zwafer2; XY home; limits restored; joysticks off
    assert r.wl.gotos == [850, 700] and report.initial_wl == 850
    assert ("move", v.zwafer2, False) in r.z.calls and ("joystick", False) in r.z.calls
    assert r.xy.wafer.limits_enabled is True
    # files: data file and nine images in the job tree
    root = r.folders.jobhistory / "PJ1_LOT1" / "PJ1_LOT1_W01" / "Vision"
    assert report.data_file == root / "files" / "bowing" / "PJ1_LOT1_W01_261126_091530.bow"
    lines = report.data_file.read_bytes().decode("ascii").split("\r\n")
    assert len(lines) == 11 and lines[-1] == ""
    assert lines[0].split("\t") == ["0.0000", "0.0000", "30.0000", "0.0000"]
    assert lines[9].split("\t")[0] == f"{v.bowing_a_um:.4f}"
    names = sorted(p.name for p in report.images)
    assert len(names) == 9 and "PJ1_LOT1_W01_261126_091530_bowing_zchuck5.png" in names
    assert all(p.parent == root / "images" / "bowing" and p.is_file() for p in report.images)
    # run-panel events: BOW Measured progress 1/9 … 9/9
    measured = [d["progress"] for _, d in r.states if d.get("event") == "BOW Measured"]
    assert measured == pytest.approx([progress(k) for k in range(1, 10)])
    assert state_names(r)[:6] == ["Start", "Started", "Move to Pos Zi", "Wait Pos Zi", "Autofocus Large",
                                  "Autofocus Fine"]
    assert r.services.images.overlays["bowing"][0].color == (0, 255, 0)
    assert "Bowing A: 20 µm" in r.services.images.overlays["bowing"][0].text


def test_bowing_out_of_range_ends_in_error(rig: Callable[..., SimpleNamespace]) -> None:
    r = rig(profile={"measure.bowing.maxbowing": 5.0})
    with pytest.raises(BowingFailed) as err:
        r.bowing.run(None, r.ctx)
    assert err.value.code == BOWING_ERROR and err.value.status == 1029 and err.value.state == "Measure Bowing"
    assert statuses(r)[-4:] == [247, 300, 248, 1029]
    assert not ws(r, "measure_bowing")
    [error] = ws(r, "error")
    assert error.data == {"Source": "measure_bowing", "Code": BOWING_ERROR, "Description": err.value.source}
    data = err.value.report.data_file
    assert data is not None and data.name == "261126_091530.bow" and data.is_file()
    assert r.wl.gotos[-1] == 700 and r.xy.wafer.limits_enabled is True
    assert r.services.images.overlays["bowing"][0].color == (255, 0, 0)


def test_parallelism_axis_b_out_of_range(rig: Callable[..., SimpleNamespace]) -> None:
    r = rig(settings={"hardware.chuck.dz4": 0.02})                    # ParaB +20 µm, ParaA stays −7.8 µm
    with pytest.raises(BowingFailed) as err:
        r.bowing.run(None, r.ctx)
    assert err.value.code == PARALLELISM_ERROR and err.value.status == 1033
    v = err.value.report.values
    assert v.parallelism_valid_a and not v.parallelism_valid_b and v.bowing_valid


def test_chuck_calibration_finishes_and_writes_a_chuck_file(rig: Callable[..., SimpleNamespace]) -> None:
    r = rig(profile={"measure.bowing.maxbowing": 1.0})
    report = r.bowing.run({"chuck_calibration": True}, r.ctx)
    assert report.positions[0] == (2.0, 2.0) and not report.valid and report.status == 500
    assert report.data_file == r.folders.chuck_calibration / "ChuckCalibrationPJ1_LOT1_W01_261126_091530.chuck"
    assert "Chuck calibrated" in r.services.images.overlays["bowing"][0].text


def test_high_mag_sets_the_zero_order(rig: Callable[..., SimpleNamespace]) -> None:
    r = rig(profile={"measure.bowing.camera": 0})
    r.bowing.run(None, r.ctx)
    assert r.wl.gotos == [0, 700]
    assert set(r.gi.calls) == {0}


# ================================================================================================== failures
class FlakyXY(XYStage):
    failures = 0

    def move_absolute(self, x: float | None = None, y: float | None = None, **kwargs: Any) -> tuple[float, float]:
        if type(self).failures > 0:
            type(self).failures -= 1
            raise DeviceError("xy", "move absolute: XY Error", code=9099)
        return super().move_absolute(x, y, **kwargs)


class StuckXY(XYStage):
    def move_absolute(self, x: float | None = None, y: float | None = None, **kwargs: Any) -> tuple[float, float]:
        return (x or 0.0, y or 0.0)                                   # accepted, never moves


def test_moves_are_retried_three_times(rig: Callable[..., SimpleNamespace]) -> None:
    FlakyXY.failures = 2
    r = rig(xy_cls=FlakyXY)
    assert r.bowing.run(None, r.ctx).status == 500
    FlakyXY.failures = 3
    r = rig(xy_cls=FlakyXY)
    with pytest.raises(BowingFailed) as err:
        r.bowing.run(None, r.ctx)
    assert err.value.code == 9099 and err.value.status == 1002 and statuses(r)[-1] == 1002
    assert statuses(r).count(203) == 1 and err.value.state == "Move to Pos Zi"
    [error] = ws(r, "error")
    assert error.data["Code"] == 9099
    assert any("Bowing (Move to Zwafer2)" in text for _, text in r.log.lines)


def test_wait_position_times_out_after_30_s(rig: Callable[..., SimpleNamespace]) -> None:
    r = rig(xy_cls=StuckXY)
    start = r.clock.now
    with pytest.raises(BowingFailed) as err:
        r.bowing.run(None, r.ctx)
    assert err.value.code == 9402 and err.value.status == 1002 and err.value.state == "Wait Pos Zi"
    assert 30.0 < r.clock.now - start < 40.0
    assert not r.af.calls


def test_autofocus_and_refocus_attempts_are_bounded(rig: Callable[..., SimpleNamespace]) -> None:
    boom = DeviceError("z", "Z Timeout", code=9401)
    r = rig(autofocus=[boom, boom, "surface"])
    assert r.bowing.run(None, r.ctx).status == 500 and len(r.af.calls) == 11
    r = rig(autofocus=[boom] * 3)
    with pytest.raises(BowingFailed) as err:
        r.bowing.run(None, r.ctx)
    assert err.value.code == 9401 and err.value.status == 1011 and len(r.af.calls) == 3
    r = rig(refocus=["surface", None, None, None, None, None])        # ZW3 refocus invalid 5 times
    with pytest.raises(BowingFailed) as err:
        r.bowing.run(None, r.ctx)
    assert err.value.code == 9030 and err.value.status == 1021 and len(r.rf.calls) == 6
    r = rig(options=BowingOptions(refocus_attempts=1), refocus=[None])
    with pytest.raises(BowingFailed) as err:
        r.bowing.run(None, r.ctx)
    assert err.value.status == 1020 and len(r.rf.calls) == 1


def test_reset_wavelength_error(rig: Callable[..., SimpleNamespace]) -> None:
    r = rig(wl=FakeWL(fail=True))
    with pytest.raises(BowingFailed) as err:
        r.bowing.run(None, r.ctx)
    assert err.value.code == 9403 and err.value.status == 1001 and err.value.state == "Start"
    assert statuses(r) == [201, 1001] and not r.af.calls
    assert ("move", math.nan, False) not in r.z.calls and not [c for c in r.z.calls if c[0] == "move"]


def test_fast_method_ends_in_the_default_case(rig: Callable[..., SimpleNamespace]) -> None:
    r = rig(profile={"measure.bowing.methodFast": True})
    with pytest.raises(BowingFailed) as err:
        r.bowing.run(None, r.ctx)
    assert len(r.af.calls) == 3 and err.value.status == 211 and err.value.code == 9001
    z = [row[2] for row in err.value.report.z_measures]
    assert [math.isfinite(v) for v in z] == [True, True, False, True, False, False, False, False, False]
    assert not ws(r, "measure_bowing")


def test_invalid_parameters_touch_nothing(rig: Callable[..., SimpleNamespace]) -> None:
    r = rig(profile={"measure.bowing.distance": 0.0})
    with pytest.raises(BowingError) as err:
        r.bowing.run(None, r.ctx)
    assert err.value.code == 9002 and statuses(r) == [1000] and not r.z.calls and not r.wl.gotos


def test_a_stop_runs_the_stopped_state(rig: Callable[..., SimpleNamespace]) -> None:
    holder: dict[str, Any] = {}

    def stop_at(n: int) -> None:
        if n == 3:
            holder["ctx"].stop_event.set()
    r = rig(on_autofocus=stop_at)
    holder["ctx"] = r.ctx
    with pytest.raises(Stopped):
        r.bowing.run(None, r.ctx)
    assert statuses(r)[-1] == 850 and len(r.af.calls) == 3
    [failed] = ws(r, "failed")
    assert failed.data["Function"] == "measure_bowing"
    assert r.wl.gotos[-1] == 700 and r.xy.wafer.limits_enabled is True
    assert any(s == "Stopped" for s, _ in r.states)


def test_without_get_image_and_waiting_as_labview(rig: Callable[..., SimpleNamespace]) -> None:
    r = rig(get_image=False, options=BowingOptions(wait_every_position=False))
    report = r.bowing.run(None, r.ctx)
    assert report.status == 500 and not report.images                  # nothing in the image store
    codes = statuses(r)
    assert 216 not in codes and 218 not in codes and 220 not in codes     # ZC1/ZC4/ZC5: no Wait Pos Zi


def test_registry_builds_it(rig: Callable[..., SimpleNamespace]) -> None:
    r = rig()
    runner = FunctionRunner()
    outcome = register_functions(runner, r.services, {FunctionName.BOWING_MEASURE: __import__(
        "alox.functions.registry", fromlist=["FACTORIES"]).FACTORIES[FunctionName.BOWING_MEASURE]})
    assert outcome == {FunctionName.BOWING_MEASURE: "registered"}
    with pytest.raises(LookupError):
        BowingMeasurement(Services())
    assert BowState.MOVE_TO_POS == "Move to Pos Zi"
