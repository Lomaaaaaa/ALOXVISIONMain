"""HL Mesa measurement (``alox.functions.mesa``): fake low-level functions registered on a real ``FunctionRunner``
(canned reports: an oxide diameter shrinking linearly in time, so the rate and the remaining time are known), fake
devices and a fake clock (no real waiting)."""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from alox.core.functions import (
    FunctionContext,
    FunctionName,
    FunctionRunner,
    FunctionStatus,
    JobInfo,
)
from alox.core.status import MesaStatus
from alox.functions.detect_circle import DetectCircleReport
from alox.functions.focus import FocusResult
from alox.functions.measure_oxyd import (
    DiametersReport,
    MeasureOxydParams,
    MeasureOxydReport,
)
from alox.functions.mesa import (
    DEFAULT_CIRCLE,
    LABVIEW_EPOCH_OFFSET_S,
    MesaError,
    MesaExposureMode,
    MesaFailed,
    MesaGlobals,
    MesaMeasurement,
    MesaOptions,
    MesaParams,
    MesaReport,
    MesaState,
    OxydDiameters,
    image_filename,
    locate_mesa,
    mesa_event,
)
from alox.functions.search_wavelength import WLMode
from alox.functions.services import Services
from alox.vision.circles import NAN_CIRCLE, Circle
from alox.vision.stats import OxideMeasureType

WALL0 = 1_700_000_000.0           # wall time = WALL0 + fake clock
D0, SLOPE = 30.0, -0.1            # oxide diameter (µm) = D0 + SLOPE · (fake clock − 1000): 6 µm/min
CIRCLE = Circle(100.0, 90.0, 20.0)
JOB = JobInfo("CJ", "PJ1", "LOT1", "W01", "1")


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0
        self._lock = threading.Lock()

    def clock(self) -> float:
        with self._lock:
            return self.now

    def wall(self) -> float:
        return WALL0 + self.clock()

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self.now += max(0.0, seconds)


class Store(dict):
    def get(self, dotted: str, default: Any = None) -> Any:          # type: ignore[override]
        return super().get(dotted, default)


class Statuses:
    def __init__(self) -> None:
        self.codes: list[int] = []

    def set(self, code: int) -> bool:
        self.codes.append(int(code))
        return True


class Log:
    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []

    def info(self, text: str) -> None:
        self.lines.append(("info", text))

    def warning(self, text: str) -> None:
        self.lines.append(("warning", text))

    def error(self, text: str) -> None:
        self.lines.append(("error", text))


class Camera:
    def __init__(self, t: FakeTime, exposure_us: float = 0.0) -> None:
        self.t, self.exposure_us = t, exposure_us

    def grab(self, newest: bool = True) -> Any:
        self.t.sleep(1.0)                                   # exposure + transfer of one image
        return SimpleNamespace(pixels=np.full((32, 32), 128, np.uint8))

    def status(self) -> Any:
        return SimpleNamespace(exposure_us=self.exposure_us)

    def set_joystick(self, on: bool) -> None:
        pass


class ZStage:
    def __init__(self) -> None:
        self.joystick: list[bool] = []

    def position(self) -> float:
        return 33.75

    def set_joystick(self, on: bool) -> None:
        self.joystick.append(on)


class XY:
    def __init__(self) -> None:
        self.pos = (0.0, 0.0)
        self.moves: list[tuple[float, float]] = []
        self.joystick: list[bool] = []
        self.stuck = False

    def position(self) -> tuple[float, float]:
        return self.pos

    def move_absolute(self, x: float, y: float, *, wait: bool = True) -> tuple[float, float]:
        self.moves.append((x, y))
        if not self.stuck:
            self.pos = (x, y)
        return self.pos

    def set_joystick(self, on: bool) -> None:
        self.joystick.append(on)


class Mono:
    def __init__(self, nm: int = 800) -> None:
        self.nm = nm
        self.gotos: list[int] = []

    def position(self) -> int:
        return self.nm

    def goto(self, nm: int, *, wait: bool = True) -> int:
        self.gotos.append(nm)
        self.nm = nm
        return nm


class Table:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok

    def has_fixed_expo(self) -> bool:
        return self.ok


class Fake:
    """A low-level function returning ``behaviour(params, ctx, call_index)``; exceptions are raised as is."""

    def __init__(self, name: FunctionName, behaviour: Any, t: FakeTime) -> None:
        self.name, self.behaviour, self.t = name, behaviour, t
        self.calls: list[tuple[float, Any]] = []
        self.last_report: Any = None

    def run(self, params: Any, ctx: FunctionContext) -> Any:
        self.calls.append((self.t.clock(), params))
        result = self.behaviour(params, ctx, len(self.calls) - 1)
        if isinstance(result, BaseException):
            raise result
        self.last_report = result
        return result


def focus_result(index: int | None = 3) -> FocusResult:
    return FocusResult(table=(), best_z=33.75, best_score=1.0, best_index=index, position=33.75, progress=100.0)


def detect_report(found: bool = True) -> DetectCircleReport:
    return DetectCircleReport(CIRCLE if found else None, (), 0 if found else -1, found, 1.0, 1.0, (32, 32))


def oxyd_report(diameter: float, external: float = 40.0, found: bool = True) -> MeasureOxydReport:
    d = DiametersReport(external=external, fit=diameter, median=diameter + 0.1, mean=diameter, surface=diameter - 0.1,
                        inscribed=diameter - 0.5, circumscribed=diameter + 0.5,
                        circ_insc_ratio=(diameter + 0.5) / (diameter - 0.5), stddev=0.1, accuracy=100.0)
    return MeasureOxydReport(Circle(100, 90, external / 2), Circle(100, 90, diameter / 2), d, (), (), (), found, found,
                             1.0, (), (), (), (), pixel_size_um=1.0)


def linear_oxyd(t: FakeTime) -> Any:
    def measure(_p: Any, _ctx: Any, _i: int) -> MeasureOxydReport:
        return oxyd_report(D0 + SLOPE * (t.clock() - 1000.0))
    return measure


class Rig:
    """Services, devices, runner and the fake low-level functions."""

    def __init__(self, tmp: Path | None = None, *, settings: dict[str, Any] | None = None,
                 profile: dict[str, Any] | None = None, exposure_us: float = 0.0, tilt: float = 0.0,
                 options: MesaOptions | None = None) -> None:
        self.t = FakeTime()
        self.events: list[Any] = []
        self.status = Statuses()
        self.log = Log()
        self.xy, self.z, self.wl = XY(), ZStage(), Mono()
        self.camera = Camera(self.t, exposure_us)
        base_settings = {"hardware.xy.waferxorg": 50.0, "hardware.xy.waferyorg": 60.0,
                         "hardware.xy.stagescoordsys": 0}
        base_settings.update(settings or {})
        base_profile = {"wavelength.wlmanual": 850, "measure.mesa.refocustime": 5.0,
                        "measure.mesa.regressiontime": 60.0, "measure.oxyd.oxydtarget": 10.0}
        base_profile.update(profile or {})
        folders = SimpleNamespace(data=tmp / "data", jobhistory=tmp / "jobhistory") if tmp is not None else None
        self.services = Services(devices={"highmag": self.camera, "z": self.z, "xy": self.xy, "wl": self.wl},
                                 profile=Store(base_profile), settings=Store(base_settings), exposures=Table(),
                                 folders=folders, publish=self.events.append, log=self.log,
                                 statuses={"mesa": self.status}, clock=self.t.clock, sleep=self.t.sleep)
        self.runner = FunctionRunner(publish=self.events.append)
        self.globals = MesaGlobals()
        self.mesa = MesaMeasurement(self.services, options=options, tilt=lambda: tilt, wall=self.t.wall,
                                    globals_=self.globals)
        self.runner.register(self.mesa)
        t = self.t
        self.fakes: dict[FunctionName, Fake] = {}
        self.fake(FunctionName.AUTOFOCUS_LARGE, lambda p, c, i: focus_result())
        self.fake(FunctionName.REFOCUS, lambda p, c, i: focus_result())
        self.fake(FunctionName.REFOCUS_OXYD, lambda p, c, i: SimpleNamespace(valid=True))
        self.fake(FunctionName.DETECT_CIRCLE, lambda p, c, i: detect_report())
        self.fake(FunctionName.TRACKING, lambda p, c, i: SimpleNamespace(moved=(False, False)))
        self.fake(FunctionName.AUTO_EXPOSURE, lambda p, c, i: SimpleNamespace(found_exposure=True))
        self.fake(FunctionName.SEARCH_WAVELENGTH, lambda p, c, i: SimpleNamespace(valid=True))
        self.fake(FunctionName.SEARCH_OBJECT, lambda p, c, i: SimpleNamespace(found=True))
        self.fake(FunctionName.MEASURE_OXYD, linear_oxyd(t))

    def fake(self, name: FunctionName, behaviour: Any) -> Fake:
        f = Fake(name, behaviour, self.t)
        self.fakes[name] = f
        self.runner.register(f)                               # type: ignore[arg-type]
        return f

    def calls(self, name: FunctionName) -> int:
        return len(self.fakes[name].calls)

    def run(self, params: Any = None, *, job: JobInfo | None = JOB) -> Any:
        return self.runner.run_once(FunctionName.MESA_MEASURE, params, job=job, timeout=30.0)

    def stop_after_measures(self, n: int) -> None:
        """MEASURE_OXYD requests a stop after its ``n``-th call (the next ``ctx.check`` stops the run)."""
        measure = linear_oxyd(self.t)

        def behaviour(p: Any, ctx: FunctionContext, i: int) -> Any:
            if i + 1 >= n:
                ctx.stop_event.set()
            return measure(p, ctx, i)
        self.fakes[FunctionName.MEASURE_OXYD].behaviour = behaviour

    def ws(self, name: str) -> list[Any]:
        return [e for e in self.events if e.topic == f"ws.{name}"]

    def states(self) -> list[str]:
        """The state changes ("MESA State Changed"; the events "MESA Measured"/"MESA Finished" carry ``event``)."""
        return [e.data["state"] for e in self.events
                if e.topic == "function.mesa_measure.state" and "event" not in e.data]


def single(**changes: Any) -> MesaParams:
    values: dict[str, Any] = {"singleshot": True, "tilt_correction": False, "refocus_time_s": 5.0,
                              "refocus_retries": 3, "oxyd_target_um": 10.0, "line_reg_time_s": 60.0, "averages": 1}
    values.update(changes)
    return MesaParams(**values)


# ================================================================================================== params
def test_params_from_profile_defaults_and_request_overrides() -> None:
    p = MesaParams.from_profile(Store())
    assert p.start_position_mm == (0.0, 0.0) and p.tilt_correction and p.refocus_time_s == 15.0
    assert p.refocus_retries == 3 and p.averages == 1 and p.wl_mode is WLMode.SCAN
    assert p.exposure_mode is MesaExposureMode.AUTO_EXPOSURE and not p.singleshot and not p.positionsctrl
    profile = Store({"measure.mesa.startx": 1.5, "measure.mesa.starty": -2.0, "measure.oxyd.oxydtarget": 12.0})
    r = MesaParams.from_request({"y": 3.0, "target": 9.5, "tilt": False, "singleshot": True, "positionsctrl": True,
                                 "analysis": False, "indexID": "7"}, profile)
    assert r.start_position_mm == (1.5, 3.0) and r.oxyd_target_um == 9.5 and not r.tilt_correction
    assert r.singleshot and r.positionsctrl and r.index_id == "7"
    assert MesaParams.from_request({}, profile).oxyd_target_um == 12.0
    with pytest.raises(MesaError) as err:
        MesaParams(averages=-1)
    assert err.value.code == 9002


def test_invalid_request_sets_the_error_status_and_moves_nothing() -> None:
    rig = Rig()
    with pytest.raises(RuntimeError, match="9002"):
        rig.run({"refocus_time_s": float("nan")})
    assert rig.status.codes == [int(MesaStatus.ERROR)] and rig.xy.moves == [] and rig.wl.gotos == []


# ================================================================================================== singleshot
def test_singleshot_full_sequence_files_and_measure_mesa_json(tmp_path: Path) -> None:
    rig = Rig(tmp_path, exposure_us=2000.0, tilt=90.0)
    params = single(positionsctrl=True, tilt_correction=True, start_position_mm=(1.0, 2.0), tracking=True,
                    wl_mode=WLMode.SCAN, exposure_mode=MesaExposureMode.AUTO_EXPOSURE, averages=3)
    report = rig.run(params)
    assert isinstance(report, MesaReport) and report.found
    # Start position (1, 2) rotated by 90° → (−2, 1), wafer → stage with system #1: (50 − 2, 60 + 1)
    assert rig.xy.moves == [(48.0, 61.0)]
    assert rig.z.joystick == [False] and rig.xy.joystick == [False]
    states = rig.states()
    assert states[:6] == ["Start", "Move Start", "Wait Move", "Search Init", "Search Object", "Refocus"]
    assert states[-4:] == ["Measure MESA Init", "Measure MESA", "Display Save", "Finished"]
    for code in (401, 402, 403, 404, 414, 419, 422, 425, 431, 436, 500):
        assert code in rig.status.codes
    assert rig.wl.gotos == [850]                                # Search Object → wavelength.wlmanual
    so_params = rig.fakes[FunctionName.SEARCH_OBJECT].calls[0][1]
    assert so_params.start_pos == pytest.approx((-2.0, 1.0))
    assert rig.calls(FunctionName.MEASURE_OXYD) == 3           # # Averages
    mo_params = rig.fakes[FunctionName.MEASURE_OXYD].calls[0][1]
    assert isinstance(mo_params, MeasureOxydParams) and mo_params.circle == CIRCLE and mo_params.image is not None
    assert rig.fakes[FunctionName.TRACKING].calls[0][1].circle == (CIRCLE.x, CIRCLE.y, CIRCLE.radius)
    ae = rig.fakes[FunctionName.AUTO_EXPOSURE].calls[0][1]
    assert ae.circle_in == CIRCLE and ae.mode.name == "ALL_WAVELENGTHS"
    assert rig.fakes[FunctionName.SEARCH_WAVELENGTH].calls[0][1].circle_in == CIRCLE
    # the measure_mesa event
    (event,) = rig.ws("measure_mesa")
    data = event.data
    assert data["Job"] == {"Control_Job_ID": "CJ", "Process_Job_ID": "PJ1", "Wafer_ID": "W01", "Lot_ID": "LOT1",
                           "Zone_ID": "1"}
    assert data["Timestamp"]["Unix"] == str(int(report.timestamp))
    assert data["External_Diameter"] == {"Fitted": "40.000000", "Unit": "um"}
    oxyd = data["Oxyd_Diameter"]
    assert oxyd["Type_Measure_Rate"] == "average" and oxyd["Unit"] == "um"
    assert float(oxyd["Average"]) == pytest.approx(report.oxyd_diameter_um)
    assert float(oxyd["Median"]) == pytest.approx(report.oxyd_diameter_um + 0.1)
    assert set(oxyd) == {"Fitted", "Median", "Average", "Surface", "Inscribed", "Circumscribed", "Circularity",
                         "Type_Measure_Rate", "STDDEV FET/#", "Unit"}
    assert data["Oxyd_Rate"]["Remaining_time"] == "24:00:00"   # one point: 9003, the error case (86 400 s)
    assert data["Oxyd_Rate"]["Unit"] == "um/min"
    assert data["Mesa_Positions"] == {"x": "-2.000000", "y": "1.000000", "z": "33.750000", "unit": "mm"}
    assert data["Wavelength"] == {"wl": "850.000000", "unit": "nm"}
    assert report.rate_error == 9003 and report.remaining_s == 86400
    # files: the .mesa data (2 rows, LabVIEW timestamps) and the PNG
    root = tmp_path / "jobhistory" / "PJ1_LOT1" / "PJ1_LOT1_W01" / "Vision"
    (data_file,) = report.data_files
    assert data_file.parent == root / "files" / "mesa" and data_file.name.endswith("_Zone1_mapping.mesa")
    rows = [line.split("\t") for line in data_file.read_text().splitlines()]
    assert float(rows[0][0]) == pytest.approx(report.timestamp + LABVIEW_EPOCH_OFFSET_S, abs=1e-3)
    assert float(rows[1][0]) == pytest.approx(report.oxyd_diameter_um, abs=1e-4)
    (image,) = report.images
    assert image.parent == root / "images" / "mapping" and image.name.startswith("PJ1_W01_1_")
    assert image.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert any("MESA (Finished) - Results: Ext Diam: 40.000 µm" in text for _, text in rig.log.lines)
    assert rig.runner.status(FunctionName.MESA_MEASURE) == FunctionStatus.FINISHED
    assert not rig.globals.meas_mesa_running


def test_singleshot_without_positions_control_uses_low_mag_autofocus_and_manual_wavelength() -> None:
    rig = Rig()
    rig.wl.nm = 650                                            # in [1, 670]: Measure MESA Init sets Manual WL again
    report = rig.run(single(exposure_mode=MesaExposureMode.CALIB_EXPOSURE))
    assert report.found and rig.xy.moves == []
    states = rig.states()
    assert states[:2] == ["Start", "Autofocus Large"] and "Search Object" not in states
    af = rig.fakes[FunctionName.AUTOFOCUS_LARGE].calls[0][1]
    assert af.camera_type.name == "LOW_MAG"
    assert rig.calls(FunctionName.AUTO_EXPOSURE) == 0 and rig.calls(FunctionName.SEARCH_WAVELENGTH) == 0
    assert 428 in rig.status.codes and rig.wl.gotos == [850]
    assert rig.globals.wl_retry                                # Set Wavelength (MANUAL) sets WL RETRY


def test_analysis_measures_at_once_on_the_default_circle() -> None:
    rig = Rig()
    report = rig.run(single(analysis=True, positionsctrl=True))
    assert report.found and rig.states()[:3] == ["Start", "Measure MESA Init", "Measure MESA"]
    assert rig.fakes[FunctionName.MEASURE_OXYD].calls[0][1].circle == DEFAULT_CIRCLE
    assert rig.calls(FunctionName.AUTOFOCUS_LARGE) == 0 and rig.xy.moves == []


def test_wait_move_timeout_is_an_error_1502() -> None:
    rig = Rig(options=MesaOptions(poll_s=1.0))
    rig.xy.stuck = True
    with pytest.raises(RuntimeError, match="9560"):
        rig.run(single(positionsctrl=True))
    assert rig.status.codes[-1] == int(MesaStatus.ERROR_WAIT_MOVE_START)
    (error,) = rig.ws("error")
    assert error.data["Source"] == "measure_mesa" and error.data["Code"] == 9560
    assert rig.ws("failed") == []                              # the core sends it (runner "failed")
    assert rig.runner.status(FunctionName.MESA_MEASURE) == FunctionStatus.FAILED


# ================================================================================================== retries
def test_detect_circle_retries_then_succeeds() -> None:
    rig = Rig()
    rig.fakes[FunctionName.DETECT_CIRCLE].behaviour = lambda p, c, i: detect_report(found=i >= 2)
    assert rig.run(single()).found
    assert rig.calls(FunctionName.DETECT_CIRCLE) == 3
    codes = rig.status.codes
    assert codes.index(414) < codes.index(415) < codes.index(416) < codes.index(431)


def test_autofocus_failures_loop_then_error_1504() -> None:
    rig = Rig()
    rig.fakes[FunctionName.AUTOFOCUS_LARGE].behaviour = lambda p, c, i: focus_result(None)
    with pytest.raises(RuntimeError, match="9500"):
        rig.run(single(refocus_retries=3))
    assert rig.calls(FunctionName.AUTOFOCUS_LARGE) == 12        # 3 tries × (1 + 3 loops)
    assert {405, 406, 407} <= set(rig.status.codes) and 408 not in rig.status.codes
    assert rig.status.codes[-1] == int(MesaStatus.ERROR_AUTOFOCUS)
    with pytest.raises(MesaFailed) as err:
        rig.mesa.run(single(refocus_retries=3), FunctionContext(FunctionName.MESA_MEASURE, JOB, rig.runner))
    assert err.value.status == int(MesaStatus.ERROR_AUTOFOCUS) and err.value.state == "Autofocus Large"


def test_refocus_failures_restart_the_autofocus_once_then_error_1505() -> None:
    rig = Rig()
    rig.fakes[FunctionName.REFOCUS].behaviour = lambda p, c, i: RuntimeError("refocus 9035")
    with pytest.raises(RuntimeError, match="9035"):
        rig.run(single(refocus_time_s=0.0, refocus_retries=3))
    assert rig.calls(FunctionName.REFOCUS) == 6 and rig.calls(FunctionName.AUTOFOCUS_LARGE) == 2
    assert {408, 409, 410} <= set(rig.status.codes)
    assert rig.status.codes[-1] == int(MesaStatus.ERROR_REFOCUS)
    assert any(level == "error" and text.startswith("MESA (Refocus)") for level, text in rig.log.lines)


def test_measure_mesa_five_tries_then_display_save_error(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.fakes[FunctionName.MEASURE_OXYD].behaviour = lambda p, c, i: oxyd_report(float("nan"), found=False)
    with pytest.raises(RuntimeError, match="9021"):
        rig.run(single())
    assert rig.calls(FunctionName.MEASURE_OXYD) == 5
    assert [c for c in rig.status.codes if 431 <= c <= 435] == [431, 432, 433, 434, 435]
    assert rig.states()[-2:] == ["Display Save ERROR", "Error"]
    assert rig.status.codes[-1] == int(MesaStatus.ERROR_MEASURE_MESA)
    assert rig.ws("measure_mesa") == [] and len(rig.ws("error")) == 1
    assert list((tmp_path / "jobhistory").rglob("*.mesa"))       # the data file of Display Save ERROR


def test_invalid_exposure_table_in_calib_mode_is_error_1508() -> None:
    rig = Rig()
    rig.services.exposures = Table(ok=False)
    with pytest.raises(RuntimeError):
        rig.run(single(exposure_mode=MesaExposureMode.CALIB_EXPOSURE))
    assert rig.status.codes[-1] == int(MesaStatus.ERROR_AUTO_EXPOSURE)


def test_endless_loop_backs_are_bounded() -> None:
    """Detect Circle lost for ever: LabVIEW looped Detect Circle → Refocus → Detect Circle without end."""
    rig = Rig(options=MesaOptions(max_loops=2))
    rig.fakes[FunctionName.DETECT_CIRCLE].behaviour = lambda p, c, i: detect_report(found=False)
    with pytest.raises(RuntimeError):
        rig.run(single(refocus_time_s=1000.0))
    assert rig.status.codes[-1] == int(MesaStatus.ERROR_DETECT_CIRCLE)
    assert max(c for c in rig.status.codes if c < 1000) <= 418


# ================================================================================================== continuous loop
def test_continuous_rate_fit_remaining_time_and_refocus_every_refocus_time(tmp_path: Path) -> None:
    rig = Rig(tmp_path, settings={"log.imagesfrequency": 10_000})
    rig.stop_after_measures(30)
    params = single(singleshot=False, refocus_time_s=5.0, line_reg_time_s=60.0, oxyd_target_um=10.0,
                    exposure_mode=MesaExposureMode.CALIB_EXPOSURE)
    assert rig.run(params) is None                              # stopped: no result
    assert rig.runner.status(FunctionName.MESA_MEASURE) != FunctionStatus.FAILED
    events = rig.ws("measure_mesa")
    assert len(events) == 30
    last = rig.mesa.last_report
    assert isinstance(last, MesaReport) and len(last.samples) == 30
    # diameter = 30 − 0.1·t: 6 µm/min; it reaches 10 µm at t = 200 s after the clock origin
    assert last.rate_um_per_min == pytest.approx(6.0, rel=1e-6)
    assert last.linear_fit is not None and last.linear_fit.slope == pytest.approx(SLOPE, rel=1e-6)
    expected = (WALL0 + 1000.0 + 200.0) - last.timestamp
    assert last.remaining_s == pytest.approx(expected, abs=1.0) and last.rate_error is None
    assert events[-1].data["Oxyd_Rate"]["Rate"] == "6.000000"
    assert events[-1].data["Oxyd_Rate"]["Remaining_time"] == last.remaining_text
    # the first measurements have fewer than 10 points (9003) → 24:00:00
    assert events[0].data["Oxyd_Rate"]["Remaining_time"] == "24:00:00"
    # refocus only once Refocus Time has elapsed since the last one
    refocus_times = [t for t, _ in rig.fakes[FunctionName.REFOCUS].calls]
    oxyd_times = [t for t, _ in rig.fakes[FunctionName.REFOCUS_OXYD].calls]
    assert rig.calls(FunctionName.REFOCUS) == 0                  # a circle is detected: Refocus Oxyd
    assert 5 <= len(oxyd_times) < 30 and not refocus_times
    gaps = np.diff(oxyd_times)
    assert np.all(gaps >= 5.0)
    assert rig.fakes[FunctionName.REFOCUS_OXYD].calls[0][1].circle == CIRCLE
    # Display Save → Refocus: every cycle detects the circle again
    assert rig.calls(FunctionName.DETECT_CIRCLE) == 30
    # stop: status 850, the function's own "failed", the runner's "stopped"
    assert rig.status.codes[-1] == int(MesaStatus.STOPPED)
    assert len(rig.ws("failed")) == 1 and rig.states()[-1] == "Stopped"
    assert any(e.topic == "function.mesa_measure.stopped" for e in rig.events)
    # process chamber: an image every 10 s at most, a data file at every cycle (data frequency 0)
    root = tmp_path / "jobhistory" / "PJ1_LOT1" / "PJ1_LOT1_W01" / "Vision"
    images = sorted((root / "images" / "mapping").glob("*.png"))
    assert 1 <= len(images) < 30 and len(last.images) == len(images)
    assert len(list((root / "files" / "mesa").glob("*.mesa"))) >= 1
    assert rig.status.codes.count(436) >= 1


def test_continuous_error_sends_error_and_failed_then_restarts() -> None:
    rig = Rig(options=MesaOptions(error_restart_pause_s=2.0))
    calls = {"n": 0}

    def measure(p: Any, ctx: FunctionContext, i: int) -> Any:
        calls["n"] += 1
        if calls["n"] >= 10:                                    # two failed cycles (5 tries each), then stop
            ctx.stop_event.set()
        return oxyd_report(float("nan"), found=False)
    rig.fakes[FunctionName.MEASURE_OXYD].behaviour = measure
    rig.run(single(singleshot=False, exposure_mode=MesaExposureMode.CALIB_EXPOSURE))
    assert len(rig.ws("error")) == 1                              # the 2nd failure is interrupted by the stop
    assert [e.data["Function"] for e in rig.ws("failed")] == ["measure_mesa", "measure_mesa"]   # error, stop
    assert rig.states().count("Autofocus Large") == 2            # Error → Move Start → Autofocus Large
    assert int(MesaStatus.ERROR_MEASURE_MESA) in rig.status.codes
    assert rig.status.codes[-1] == int(MesaStatus.STOPPED)


def test_restart_after_error_can_be_switched_off() -> None:
    rig = Rig(options=MesaOptions(restart_after_error=False))
    rig.fakes[FunctionName.MEASURE_OXYD].behaviour = lambda p, c, i: oxyd_report(float("nan"), found=False)
    with pytest.raises(RuntimeError):
        rig.run(single(singleshot=False, exposure_mode=MesaExposureMode.CALIB_EXPOSURE))
    assert rig.states().count("Autofocus Large") == 1


def test_wl_retry_global_forces_a_new_wavelength_search() -> None:
    rig = Rig()

    def measure(p: Any, ctx: FunctionContext, i: int) -> Any:
        if i == 0:
            rig.globals.wl_retry = True                           # the UI asks for a new search
        if i == 2:
            ctx.stop_event.set()
        return oxyd_report(20.0)
    rig.fakes[FunctionName.MEASURE_OXYD].behaviour = measure
    rig.run(single(singleshot=False, wl_mode=WLMode.SCAN, exposure_mode=MesaExposureMode.AUTO_EXPOSURE))
    assert rig.calls(FunctionName.SEARCH_WAVELENGTH) == 2         # first cycle, then the retry
    assert rig.calls(FunctionName.AUTO_EXPOSURE) == 1             # Auto Expo Done
    assert not rig.globals.wl_retry


def test_stop_is_responsive_with_real_sleeps() -> None:
    """With ``services.sleep = time.sleep`` the pauses are ``ctx.sleep``: a stop ends a long pause at once."""
    import time

    rig = Rig(options=MesaOptions(state_pause_s=30.0))
    rig.services.sleep = time.sleep
    rig.runner.start(FunctionName.MESA_MEASURE, single(singleshot=False), job=JOB)
    t0 = time.monotonic()
    while "Start" not in rig.states() and time.monotonic() - t0 < 5:
        time.sleep(0.01)
    assert rig.runner.stop(timeout=5.0)
    assert time.monotonic() - t0 < 5.0
    assert rig.status.codes[-1] == int(MesaStatus.STOPPED)


# ================================================================================================== helpers
def test_mesa_event_measure_type_names_and_remaining_format() -> None:
    report = MesaReport(job=JobInfo(), timestamp=1564593543.7, external_fit_um=31.241318,
                        oxyd=OxydDiameters(9.004834, 9.132412, 8.9807, 9.000087, 8.019226, 10.069082, 1.255618,
                                           OxideMeasureType.FIT, 0.1),
                        wavelength_nm=850.0, rate_um_per_min=0.058213, remaining_s=4181.0, remaining_text="01:09:41",
                        linear_fit=None, position=(1.0, 2.0, 33.5), oxyd_diameter_um=9.004834, oxyd_target_um=5.0,
                        averages=1)
    data = mesa_event(report).data
    assert data["Timestamp"] == {"Unix": "1564593543"}
    assert data["Oxyd_Diameter"]["Type_Measure_Rate"] == "fitted"
    assert data["Oxyd_Diameter"]["Fitted"] == "9.004834" and data["Oxyd_Diameter"]["Circularity"] == "1.255618"
    assert data["Oxyd_Rate"] == {"Rate": "0.058213", "Unit": "um/min", "Remaining_time": "01:09:41"}


def test_image_filename() -> None:
    from datetime import UTC, datetime

    when = datetime(2026, 11, 26, 14, 5, 9, tzinfo=UTC)
    assert image_filename(JobInfo(), when) == "261126_140509"
    assert image_filename(JobInfo(process_job_id="Pj", wafer_id="W"), when) == "261126_140509"
    assert image_filename(JOB, when) == "PJ1_W01_1_261126_140509"


def test_locate_mesa_runs_measure_oxyd_on_the_last_detected_circle() -> None:
    rig = Rig()
    ctx = FunctionContext(FunctionName.SEARCH_WAVELENGTH, None, rig.runner)
    pixels = np.zeros((8, 8), np.uint8)
    assert locate_mesa(ctx, pixels) is None                      # no circle detected yet
    rig.fakes[FunctionName.DETECT_CIRCLE].last_report = detect_report()
    rig.fakes[FunctionName.MEASURE_OXYD].behaviour = lambda p, c, i: oxyd_report(12.0)
    loc = locate_mesa(ctx, pixels)
    assert loc is not None and loc.mesa_found and loc.fit_diameter_px == 12.0 and loc.circular
    params = rig.fakes[FunctionName.MEASURE_OXYD].calls[-1][1]
    assert params.circle == CIRCLE and params.image is pixels
    assert NAN_CIRCLE is not None and MesaState.MEASURE_MESA_REFOCUS.value == "Measure MESA Refocus"


def test_needs_the_camera_z_and_monochromator() -> None:
    with pytest.raises(LookupError):
        MesaMeasurement(Services(devices={"highmag": object(), "z": object()}))
