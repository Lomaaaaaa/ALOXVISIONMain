"""Search Object (``alox.functions.search_object``): simulated XY stages, a simulated Low Mag view of a wafer that
carries the pattern at a known stage position, fake focus functions and the NumPy matcher."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from alox.core.functions import FunctionContext, FunctionName, FunctionRunner, Stopped
from alox.devices.base import DeviceError, DeviceState
from alox.devices.xy import OUTSIDE_WAFER, XYSettings, XYStage
from alox.functions.focus import (
    AutofocusParams,
    CameraType,
    FocusFailed,
    FocusResult,
    RefocusParams,
)
from alox.functions.search_object import (
    DETECT_OBJECT_HARDWARE,
    DETECT_OBJECT_NOT_FOUND,
    XY_ERROR,
    SearchObject,
    SearchObjectOptions,
    SearchObjectParams,
    default_autofocus_params,
    default_refocus_params,
    match_overlay,
    object_position,
    snail_positions,
    wafer_to_stage,
)
from alox.functions.services import Points, Services, Text
from alox.functions.tracking import FunctionFailed
from alox.vision.matching import NumpyMatcher
from aloxcam.imaging.png import write_png
from aloxcam.settings import SimulationOptions
from aloxcam.zstage.sim import SimStage

SIM = SimulationOptions(stage_min_mm=0.0, stage_max_mm=200.0, stage_reference_s=0.01)
W, H = 200, 160
PX_MM = 0.002                                 # 2 µm per pixel (units 1 = µm)
ORIGIN = (50.0, 50.0)                         # wafer origin in stage coordinates
START_WAFER = (1.0, 2.0)                      # Start Search Pos (mm, wafer) → stage (51, 52) with system #1


def make_template(size: int = 41) -> np.ndarray:
    t = np.full((size, size), 30, np.uint8)
    t[4:10, 4:36] = 220
    t[14:34, 6:12] = 170
    t[20:30, 22:38] = 240
    t[33:38, 16:22] = 120
    return t


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


class Ready:
    """A device that is up (``state`` READY) with the joystick calls."""

    def __init__(self) -> None:
        self.state = DeviceState.READY
        self.joystick: list[bool] = []

    def set_joystick(self, enabled: bool, **kwargs: Any) -> None:
        self.joystick.append(enabled)


class LowMagView:
    """Get Image for the Low Mag camera: the window of a wafer seen at the current XY position (stage system #1:
    X right, Y up; image X right, Y down), the pattern drawn at ``object_mm`` (stage coordinates)."""

    name = FunctionName.GET_IMAGE

    def __init__(self, services: Services, xy: XYStage, template: np.ndarray, object_mm: tuple[float, float] | None):
        self.services = services
        self.xy = xy
        self.template = template
        self.object_mm = object_mm
        self.calls: list[Any] = []
        self.rng = np.random.default_rng(7)

    def run(self, params: Any, ctx: FunctionContext) -> Any:
        self.calls.append(params)
        x, y = self.xy.position()
        img = np.full((H, W), 90.0) + self.rng.normal(0.0, 5.0, (H, W))
        if self.object_mm is not None:
            cx = W / 2 + (self.object_mm[0] - x) / PX_MM
            cy = H / 2 - (self.object_mm[1] - y) / PX_MM
            h, w = self.template.shape
            x0, y0 = round(cx - (w - 1) / 2), round(cy - (h - 1) / 2)
            ys, xs = slice(max(0, y0), min(H, y0 + h)), slice(max(0, x0), min(W, x0 + w))
            if ys.start < ys.stop and xs.start < xs.stop:
                img[ys, xs] = self.template[ys.start - y0:ys.stop - y0, xs.start - x0:xs.stop - x0]
        pixels = np.clip(img, 0, 255).astype(np.uint8)
        self.services.images.put("lowmag", pixels)
        return SimpleNamespace(pixels=pixels)


class FakeFocus:
    """Autofocus / Refocus: each run pops the next outcome (an exception to raise, or a best Z)."""

    def __init__(self, name: FunctionName, outcomes: list[Any]) -> None:
        self.name = name
        self.outcomes = outcomes
        self.calls: list[Any] = []

    def run(self, params: Any, ctx: FunctionContext) -> FocusResult:
        self.calls.append(params)
        outcome = self.outcomes.pop(0) if self.outcomes else 33.5
        if isinstance(outcome, BaseException):
            raise outcome
        return FocusResult(table=((33.4, 1.0), (outcome, 2.0)), best_z=outcome, best_score=2.0, best_index=1,
                           position=outcome, progress=100.0)


@pytest.fixture
def rig(tmp_path) -> Iterator[Any]:
    created: list[XYStage] = []

    def build(*, object_mm: tuple[float, float] | None = (50.75, 52.03), autofocus: list[Any] | None = None,
              refocus: list[Any] | None = None, xy_settings: XYSettings | None = None,
              devices: dict[str, Any] | None = None) -> SimpleNamespace:
        clock = FakeTime()
        sims = [SimStage(SIM, clock=clock.monotonic, dh_running=False) for _ in "xy"]
        xy = XYStage(sims[0], sims[1], xy_settings or XYSettings(), sleep=clock.sleep, monotonic=clock.monotonic,
                     supervise=False, reconnect_delay_s=0.0)
        xy.open(timeout=10)
        created.append(xy)
        settings = Settings({"hardware.camera.lowmag.pixelsize": PX_MM, "hardware.camera.lowmag.units": 2,
                             "hardware.xy.stagescoordsys": 0, "hardware.xy.waferxorg": ORIGIN[0],
                             "hardware.xy.waferyorg": ORIGIN[1], "hardware.xy.speed": 10.0})
        template = make_template()
        write_png(tmp_path / "model.png", template)
        log = Log()
        all_devices = {"xy": xy, "z": Ready(), "lowmag": Ready(), "wl": Ready()}
        all_devices.update(devices or {})
        services = Services(devices={k: v for k, v in all_devices.items() if v is not None}, settings=settings,
                            log=log, clock=clock.monotonic, sleep=clock.sleep)
        runner = FunctionRunner()
        view = LowMagView(services, xy, template, object_mm)
        af = FakeFocus(FunctionName.AUTOFOCUS_LARGE, list(autofocus or []))
        rf = FakeFocus(FunctionName.REFOCUS, list(refocus or []))
        for function in (view, af, rf):
            runner.register(function)
        search = SearchObject(services, NumpyMatcher())
        runner.register(search)
        return SimpleNamespace(xy=xy, settings=settings, services=services, runner=runner, view=view, af=af, rf=rf,
                               search=search, clock=clock, log=log, states=[], tmp=tmp_path)

    yield build
    for xy in created:
        xy.shutdown(timeout=5)


def params(rig: SimpleNamespace, **overrides: Any) -> SearchObjectParams:
    values: dict[str, Any] = {"start_pos": START_WAFER, "search_model_path": str(rig.tmp / "model.png"),
                              "autofocus": False, "refocus": False, "show_window": False, "max_moves": 15,
                              "step_mm": 0.1}
    values.update(overrides)
    return SearchObjectParams(**values)


def run(rig: SimpleNamespace, p: SearchObjectParams | None) -> Any:
    ctx = FunctionContext(FunctionName.SEARCH_OBJECT, None, rig.runner)
    original = ctx.set_state

    def record(state: str, **data: Any) -> None:
        rig.states.append((state, data))
        original(state, **data)
    ctx.set_state = record                                                # type: ignore[method-assign]
    return rig.search.run(p, ctx)


def states(rig: SimpleNamespace) -> list[str]:
    """The state sequence (events published with the same state are dropped)."""
    out: list[str] = []
    for state, data in rig.states:
        if "event" not in data and (not out or out[-1] != state):
            out.append(state)
    return out


def events(rig: SimpleNamespace) -> list[str]:
    return [data["event"] for _, data in rig.states if "event" in data]


# ============================================================================================== helpers
def test_snail_positions():
    assert snail_positions((0, 0), 1.0, 7) == [(0, 0), (-1, 0), (-1, -1), (0, -1), (1, -1), (1, 0), (1, 1)]
    assert snail_positions((0, 0), 1.0, 0) == [(0, 0), (-1, 0)]          # a While loop: one leg at least
    fifteen = snail_positions((10.0, 20.0), 0.1, 15)
    assert len(fifteen) == 17                                            # the last leg is completed
    assert np.allclose(fifteen[7:13], [(10.0, 20.1), (9.9, 20.1), (9.8, 20.1), (9.8, 20.0), (9.8, 19.9), (9.8, 19.8)])
    assert np.allclose(fifteen[13:], [(9.9, 19.8), (10.0, 19.8), (10.1, 19.8), (10.2, 19.8)])


def test_wafer_to_stage():
    assert wafer_to_stage((1, 2), (50, 60), 0) == (51.0, 62.0)
    assert wafer_to_stage((1, 2), (50, 60), 1) == (49.0, 62.0)
    assert wafer_to_stage((1, 2), (50, 60), 2) == (51.0, 58.0)
    assert wafer_to_stage((1, 2), (50, 60), 3) == (49.0, 58.0)


def test_object_position_follows_tracking_move():
    services = Services(settings=Settings({"hardware.camera.lowmag.pixelsize": 2.0, "hardware.camera.lowmag.units": 1,
                                           "hardware.xy.stagescoordsys": 0}))
    # 10 px right and 5 px below the centre, system #1 (Y up): +0.02 mm in X, −0.01 mm in Y
    assert object_position(services, (10.0, 20.0), (110.0, 85.0), (200, 160)) == pytest.approx((10.02, 19.99))
    services.settings["hardware.camera.lowmag.hflip"] = True             # horizontal flip: Y negated
    assert object_position(services, (10.0, 20.0), (110.0, 85.0), (200, 160)) == pytest.approx((10.02, 20.01))


def test_params_from_profile_and_match_settings():
    profile = Settings({"search.object.startx": "1,5", "search.object.starty": -2, "search.object.minscore": 60,
                        "search.object.maxocclusionpc": 20, "search.object.autofocus": "FALSE",
                        "search.object.offsetX": 0.25, "search.object.searchmodel": "mark.png"})
    p = SearchObjectParams.from_profile(profile, max_moves=3)
    assert p.start_pos == (1.5, -2.0) and p.min_score == 60.0 and p.autofocus is False and p.refocus is True
    assert p.offset_x == 0.25 and p.offset_y == 0.0 and p.search_model_path == "mark.png" and p.max_moves == 3
    default = SearchObjectParams.from_profile(Settings())
    assert (default.edge_threshold, default.min_length, default.min_score, default.max_occlusion,
            default.max_moves, default.step_mm) == (25, 5, 70.0, 50.0, 15, 0.1)
    m = p.match_settings()
    assert (m.number_of_matches, m.min_score, m.angle_ranges, m.occlusion_range, m.subpixel) == (
        50, 600.0, ((0.0, 360.0),), (0.0, 20.0), False)
    assert (m.curve.edge_threshold, m.curve.min_length, m.curve.row_step, m.curve.max_endpoint_gap) == (25, 5, 5, 5)
    with pytest.raises(FunctionFailed):
        SearchObjectParams(start_pos=(1.0,))


def test_default_focus_params():
    services = Services(profile=Settings({"autofocus.speed": 1, "autofocus.moveduration": 4, "refocus.nbsteps": 3,
                                          "refocus.step": 0.01}),
                        settings=Settings({"hardware.z.zminautofocus": 32.0}))
    af = default_autofocus_params(services)
    assert isinstance(af, AutofocusParams) and af.camera_type is CameraType.LOW_MAG
    assert (af.speed, af.duration_s, af.z_min, af.z_max, af.subsampling) == (1, 4, 32.0, 34.5, 0.25)
    rf = default_refocus_params(services)
    assert isinstance(rf, RefocusParams) and rf.camera_type is CameraType.LOW_MAG
    assert (rf.steps, rf.step_mm, rf.fin_roi_width, rf.wait_ms) == (3, 0.01, 128, 0)


def test_match_overlay_texts():
    from alox.vision.matching import Match
    m = Match((50.2, 60.7), 12.34, 100.0, 873.0, ((10, 20), (30, 20), (30, 40), (10, 40)))
    shapes = match_overlay([m, m])
    texts = [s.text for s in shapes if isinstance(s, Text)]
    assert texts == ["POS.X: 50, Y:61", "ANGLE: 12.3 (°)", "SCALE: 100.0 (%)", "SCORE: 87.3 (%)",
                     "OCCLUSION: 0.0 (%)"]
    assert shapes[0] == Points(((10, 20), (30, 20), (30, 40), (10, 40)), (255, 0, 0), 1, closed=True)
    assert sum(isinstance(s, Points) for s in shapes) == 4


# ============================================================================================== runs
def test_finds_the_object_on_the_spiral_and_centres_it(rig):
    r = rig()
    report = run(r, params(r, offset_x=0.01, offset_y=-0.02))
    assert report.found
    # start (51, 52); position 1 = (50.9, 52) sees the object 75 px left and 15 px above the centre
    assert np.allclose(report.search_positions[:3], [(51.0, 52.0), (50.9, 52.0), (50.9, 51.9)])
    assert report.current_index == 2 and report.current_xy == pytest.approx((50.9, 52.0))
    assert report.found_pos == pytest.approx((50.75, 52.03), abs=0.0015)
    assert report.target == pytest.approx((report.found_pos[0] + 0.01, report.found_pos[1] - 0.02))
    assert r.xy.position() == pytest.approx(report.target, abs=1e-6)
    assert report.best_match is not None and report.best_match.score > 900
    assert report.best_match.position == pytest.approx((25.0, 65.0), abs=0.6)
    assert report.all_matches[0] is report.best_match
    assert states(r) == ["Start", "Started", "Move To Pos", "Position OK?", "Detect Object", "Move To Pos",
                         "Position OK?", "Detect Object", "Move To Object", "Object Pos OK ?", "Finished"]
    assert events(r)[0] == "DO Started" and events(r)[-1] == "DO Finished" and "DO Searching" in events(r)
    assert len(r.view.calls) == 2 and r.view.calls[0] is CameraType.LOW_MAG
    assert r.af.calls == [] and r.rf.calls == []
    overlay = r.services.images.overlays["detectobject"]
    assert any(isinstance(s, Text) and s.text.startswith("SCORE:") for s in overlay)
    assert any(isinstance(s, Points) and s.color == (0, 255, 0) for s in overlay)      # contours
    assert r.search.last_report is report


def test_autofocus_and_refocus_only_at_the_first_position(rig):
    r = rig(refocus=[FocusFailed(9401, "z timeout", state="Check Position", result=None),   # type: ignore[arg-type]
                     RuntimeError("refocus not valid")])
    report = run(r, params(r, autofocus=True, refocus=True, show_window=True))
    assert report.found and report.autofocus_z == 33.5 and report.autofocus_score == 2.0
    assert report.autofocus_data == ((33.4, 1.0), (33.5, 2.0)) and report.refocus_errors == 0
    assert len(r.af.calls) == 1 and isinstance(r.af.calls[0], AutofocusParams)
    assert r.af.calls[0].camera_type is CameraType.LOW_MAG
    assert len(r.rf.calls) == 3                                           # 2 failures, then success
    seq = states(r)
    assert seq[:9] == ["Start", "Started", "Move To Pos", "Position OK?", "Autofocus Init", "Autofocus",
                       "Autofocus Finished", "Refocus Init", "Refocus"]
    assert seq.count("Autofocus") == 1
    assert "DO Autofocus" in events(r) and events(r).count("DO Refocus") == 3
    # focused images: 1 after the autofocus, 3 after each refocus, then 2 detections
    assert len(r.view.calls) == 6


def test_refocus_fails_after_five_attempts(rig):
    r = rig(refocus=[FocusFailed(9401, "z timeout", state="x", result=None)] * 6)    # type: ignore[arg-type]
    with pytest.raises(FunctionFailed) as info:
        run(r, params(r, refocus=True))
    assert info.value.code == 9401 and info.value.state == "Refocus"
    assert len(r.rf.calls) == 5 and info.value.report.refocus_errors == 4
    assert ("error", "Detect Object (Refocus): 9401: z timeout") in r.log.lines


def test_invalid_autofocus_is_an_error(rig):
    r = rig(autofocus=[float("nan")])
    with pytest.raises(FunctionFailed) as info:
        run(r, params(r, autofocus=True))
    assert info.value.state == "Autofocus"
    assert states(r)[-1] == "Error"


def test_no_object_found_after_the_last_position(rig):
    r = rig(object_mm=None)
    with pytest.raises(FunctionFailed) as info:
        run(r, params(r, max_moves=3))
    assert info.value.code == DETECT_OBJECT_NOT_FOUND and info.value.state == "Move To Pos"
    report = info.value.report
    assert not report.found and report.found_pos is None and report.current_index == 3
    assert len(report.search_positions) == 3 and len(r.view.calls) == 3
    assert any(text.startswith("Detect Object (Move XY Position)") for _, text in r.log.lines)


def test_hardware_check_is_9046(rig):
    r = rig(devices={"wl": None})
    with pytest.raises(FunctionFailed) as info:
        run(r, params(r))
    assert info.value.code == DETECT_OBJECT_HARDWARE and "wl" in info.value.source
    r = rig()
    r.services.devices["lowmag"].state = DeviceState.FAULT
    with pytest.raises(FunctionFailed) as info:
        run(r, params(r))
    assert info.value.code == DETECT_OBJECT_HARDWARE


def test_joysticks_off_and_speed(rig, monkeypatch):
    r = rig()
    speeds: list[float] = []
    monkeypatch.setattr(r.xy, "set_speed", lambda v, *a, **k: speeds.append(v))
    run(r, params(r))
    assert r.services.devices["z"].joystick == [False]
    assert speeds == [10.0]


def test_missing_model_is_an_error_before_any_move(rig):
    r = rig()
    with pytest.raises(FunctionFailed) as info:
        run(r, params(r, search_model_path=str(r.tmp / "none.png")))
    assert info.value.code == 7 and info.value.state == "Start"
    assert r.xy.position() == pytest.approx((0.0, 0.0))


def test_model_path_is_under_the_profile_folder(rig, tmp_path):
    r = rig()
    (tmp_path / "profile").mkdir()
    write_png(tmp_path / "profile" / "mark.png", make_template())
    mask = np.full((41, 41), 255, np.uint8)
    write_png(tmp_path / "profile" / "mark_mask.png", mask)
    r.services.folders = SimpleNamespace(profile=tmp_path / "profile")
    assert r.search.model_path("mark.png") == tmp_path / "profile" / "mark.png"
    report = run(r, params(r, search_model_path="mark.png", learn_mask_path="mark_mask.png"))
    assert report.found


def test_refused_moves_are_retried_then_fail_with_the_device_code(rig, monkeypatch):
    r = rig()
    attempts: list[tuple[float, float]] = []

    def refuse(x: float, y: float, **kwargs: Any) -> None:
        attempts.append((x, y))
        raise DeviceError("xy", "outside the wafer", code=OUTSIDE_WAFER)
    monkeypatch.setattr(r.xy, "move_absolute", refuse)
    with pytest.raises(FunctionFailed) as info:
        run(r, params(r))
    assert info.value.code == OUTSIDE_WAFER and info.value.state == "Move To Pos"
    assert len(attempts) == 4                                              # Error Count ≥ 3 → Error


def test_position_not_reached_is_9099(rig, monkeypatch):
    r = rig()
    monkeypatch.setattr(r.xy, "move_absolute", lambda *a, **k: None)      # accepted, never moves
    r.search.options = SearchObjectOptions(move_timeout_s=2.0, poll_s=0.1)
    with pytest.raises(FunctionFailed) as info:
        run(r, params(r))
    assert info.value.code == XY_ERROR and info.value.state == "Position OK?"
    assert ("error", "Detect Object (Wait Position): 9099: xy stages error (position not reached)") in r.log.lines


def test_stop_during_the_search(rig):
    r = rig(object_mm=None)
    ctx = FunctionContext(FunctionName.SEARCH_OBJECT, None, r.runner)
    original = ctx.set_state

    def stop_on_detect(state: str, **data: Any) -> None:
        original(state, **data)
        r.states.append((state, data))
        if state == "Detect Object":
            ctx.stop_event.set()
    ctx.set_state = stop_on_detect                                          # type: ignore[method-assign]
    with pytest.raises(Stopped):
        r.search.run(params(r), ctx)
    assert r.states[-1][0] == "Stopped"


def test_runs_from_the_runner_with_profile_params(rig):
    r = rig()
    r.services.profile = Settings({"search.object.startx": START_WAFER[0], "search.object.starty": START_WAFER[1],
                                   "search.object.searchmodel": str(r.tmp / "model.png"),
                                   "search.object.autofocus": False, "search.object.refocus": False,
                                   "search.object.showwindow": False})
    report = r.runner.run_once(FunctionName.SEARCH_OBJECT, None, timeout=60)
    assert report.found
