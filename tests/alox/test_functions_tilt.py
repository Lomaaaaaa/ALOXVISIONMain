"""HL Tilt measurement (``alox.functions.tilt``): simulated XY stages on a fake clock, a simulated camera view of a
wafer (a straight edge at a known angle, or two marks at known stage positions), fake focus functions registered on a
real :class:`FunctionRunner`, the NumPy matcher."""

from __future__ import annotations

import math
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from alox.core.core import MeasureRequest
from alox.core.functions import (
    FunctionContext,
    FunctionName,
    FunctionRunner,
    JobInfo,
    Stopped,
)
from alox.core.status import TiltStatus
from alox.devices.xy import OUTSIDE_WAFER, WaferSettings, XYSettings, XYStage
from alox.events import Event
from alox.functions.focus import CameraType, FocusFailed, FocusResult
from alox.functions.services import Line, Rect, Services, Text, camera_device_name
from alox.functions.tilt import (
    TILT_NOT_FOUND,
    TILT_OUT_OF_RANGE,
    TILT_XY_TIMEOUT,
    DualObjectsDetection,
    StraightEdgeDetection,
    TiltError,
    TiltFailed,
    TiltMeasurement,
    TiltMethod,
    TiltOptions,
    TiltParams,
    TiltReport,
)
from alox.vision.matching import NumpyMatcher
from alox.vision.tilt import EdgeMethod, RakeDirection
from aloxcam.imaging.png import write_png
from aloxcam.settings import SimulationOptions
from aloxcam.zstage.sim import SimStage

SIM = SimulationOptions(stage_min_mm=0.0, stage_max_mm=200.0, stage_reference_s=0.01)
W, H = 320, 240
PX_MM = 0.002                                 # 2 µm per pixel
ORIGIN = (50.0, 50.0)                         # wafer origin in stage coordinates (system #1: X right, Y up)
START = (1.0, 2.0)                            # Start Search Pos (wafer mm) → stage (51, 52)
NOW = datetime(2026, 11, 26, 14, 5, 9, tzinfo=UTC)


def make_template(size: int = 41) -> np.ndarray:
    t = np.full((size, size), 30, np.uint8)
    t[4:10, 4:36] = 220
    t[14:34, 6:12] = 170
    t[20:30, 22:38] = 240
    t[33:38, 16:22] = 120
    return t


def edge_scene(angle_deg: float, *, only_at: Callable[[float, float], bool] | None = None):
    """A smooth straight edge through the image centre at ``angle_deg`` counter-clockwise as displayed."""
    ys, xs = np.mgrid[0:H, 0:W].astype(np.float64)
    a = math.radians(angle_deg)
    d = (xs - (W - 1) / 2.0) * math.sin(a) + (ys - (H - 1) / 2.0) * math.cos(a)
    edge = 60.0 + 120.0 / (1.0 + np.exp(-d / 0.8))
    flat = np.full((H, W), 90.0)

    def render(x: float, y: float, rng: np.random.Generator) -> np.ndarray:
        base = edge if only_at is None or only_at(x, y) else flat
        return base + rng.normal(0.0, 2.0, (H, W))
    return render


def marks_scene(marks: list[tuple[np.ndarray, tuple[float, float]]]):
    """Templates drawn at stage positions (X right, Y up; image Y down)."""
    def render(x: float, y: float, rng: np.random.Generator) -> np.ndarray:
        img = np.full((H, W), 90.0) + rng.normal(0.0, 4.0, (H, W))
        for template, (mx, my) in marks:
            cx = W / 2 + (mx - x) / PX_MM
            cy = H / 2 - (my - y) / PX_MM
            h, w = template.shape
            x0, y0 = round(cx - (w - 1) / 2), round(cy - (h - 1) / 2)
            ys, xs = slice(max(0, y0), min(H, y0 + h)), slice(max(0, x0), min(W, x0 + w))
            if ys.start < ys.stop and xs.start < xs.stop:
                img[ys, xs] = template[ys.start - y0:ys.stop - y0, xs.start - x0:xs.stop - x0]
        return img
    return render


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


class StatusRecorder:
    def __init__(self) -> None:
        self.codes: list[int] = []

    def set(self, code: int) -> bool:
        self.codes.append(int(code))
        return True


class Ready:
    def __init__(self) -> None:
        self.joystick: list[bool] = []

    def set_joystick(self, enabled: bool, **kwargs: Any) -> None:
        self.joystick.append(enabled)


class View:
    """Get Image: renders the scene at the current XY position and stores it as the camera's last image."""

    name = FunctionName.GET_IMAGE

    def __init__(self, services: Services, xy: XYStage, scene: Callable[..., np.ndarray]) -> None:
        self.services = services
        self.xy = xy
        self.scene = scene
        self.calls: list[Any] = []
        self.rng = np.random.default_rng(11)

    def run(self, params: Any, ctx: FunctionContext) -> Any:
        self.calls.append(params)
        x, y = self.xy.position()
        pixels = np.clip(self.scene(x, y, self.rng), 0, 255).astype(np.uint8)
        self.services.images.put(camera_device_name(params), pixels)
        return SimpleNamespace(pixels=pixels)


class FakeFocus:
    """Autofocus / Refocus: each run pops the next outcome (an exception, a callable, or a best Z)."""

    def __init__(self, name: FunctionName, outcomes: list[Any]) -> None:
        self.name = name
        self.outcomes = outcomes
        self.calls: list[Any] = []

    def run(self, params: Any, ctx: FunctionContext) -> FocusResult:
        self.calls.append(params)
        outcome = self.outcomes.pop(0) if self.outcomes else 33.5
        if callable(outcome):
            outcome = outcome(ctx)
        if isinstance(outcome, BaseException):
            raise outcome
        return FocusResult(table=((33.4, 1.0), (outcome, 2.0)), best_z=outcome, best_score=2.0, best_index=1,
                           position=outcome, progress=100.0)


@pytest.fixture
def rig(tmp_path) -> Iterator[Callable[..., SimpleNamespace]]:
    created: list[XYStage] = []

    def build(scene: Callable[..., np.ndarray], *, autofocus: list[Any] | None = None,
              refocus: list[Any] | None = None, xy_settings: XYSettings | None = None,
              options: TiltOptions | None = None) -> SimpleNamespace:
        clock = FakeTime()
        sims = [SimStage(SIM, clock=clock.monotonic, dh_running=False) for _ in "xy"]   # build_xy_backends(sim)
        xy = XYStage(sims[0], sims[1], xy_settings or XYSettings(), sleep=clock.sleep, monotonic=clock.monotonic,
                     supervise=False, reconnect_delay_s=0.0)
        xy.open(timeout=10)
        created.append(xy)
        settings = Settings({"hardware.camera.lowmag.pixelsize": PX_MM, "hardware.camera.lowmag.units": 2,
                             "hardware.camera.highmag.pixelsize": PX_MM, "hardware.camera.highmag.units": 2,
                             "hardware.xy.stagescoordsys": 0, "hardware.xy.waferxorg": ORIGIN[0],
                             "hardware.xy.waferyorg": ORIGIN[1], "hardware.xy.speed": 10.0})
        (tmp_path / "profile").mkdir(exist_ok=True)
        write_png(tmp_path / "profile" / "mark.png", make_template())
        events: list[Event] = []
        status = StatusRecorder()
        folders = SimpleNamespace(data=tmp_path / "data", jobhistory=tmp_path / "jobhistory",
                                  profile=tmp_path / "profile")
        log = Log()
        services = Services(devices={"xy": xy, "z": Ready()}, settings=settings, log=log, folders=folders,
                            publish=events.append, statuses={"tilt": status}, clock=clock.monotonic,
                            sleep=clock.sleep)
        runner = FunctionRunner()
        view = View(services, xy, scene)
        af = FakeFocus(FunctionName.AUTOFOCUS_LARGE, list(autofocus or []))
        rf = FakeFocus(FunctionName.REFOCUS, list(refocus or []))
        for function in (view, af, rf):
            runner.register(function)
        tilt = TiltMeasurement(services, NumpyMatcher(), options=options, now=lambda: NOW)
        runner.register(tilt)
        return SimpleNamespace(xy=xy, settings=settings, services=services, runner=runner, view=view, af=af, rf=rf,
                               tilt=tilt, clock=clock, log=log, events=events, status=status, states=[],
                               tmp=tmp_path)

    yield build
    for xy in created:
        xy.shutdown(timeout=5)


def edge_params(**overrides: Any) -> TiltParams:
    values: dict[str, Any] = {
        "start_pos": START, "method": TiltMethod.STRAIGHT_EDGE, "max_moves": 3, "step_mm": 0.3,
        "autofocus": False, "refocus": False,
        "straight_edge": StraightEdgeDetection(search_direction=RakeDirection.TOP_TO_BOTTOM, step_px=20, width=5)}
    values.update(overrides)
    return TiltParams(**values)


def object_params(**overrides: Any) -> TiltParams:
    dual = DualObjectsDetection(obj1_model_path="mark.png", obj2_model_path="mark.png", obj1_min_score=60.0,
                                obj2_min_score=60.0)
    values: dict[str, Any] = {"start_pos": START, "method": TiltMethod.DUAL_OBJECTS, "max_moves": 2, "step_mm": 0.3,
                              "autofocus": False, "refocus": False, "dual_objects": dual,
                              "straight_edge": StraightEdgeDetection(angle_expected_deg=0.0, angle_range_deg=10.0)}
    values.update(overrides)
    return TiltParams(**values)


def run(r: SimpleNamespace, params: Any, job: JobInfo | None = None,
        on_state: Callable[[FunctionContext, str, dict[str, Any]], None] | None = None) -> TiltReport:
    ctx = FunctionContext(FunctionName.TILT_MEASURE, job, r.runner)
    original = ctx.set_state

    def record(state: str, **data: Any) -> None:
        r.states.append((state, data))
        original(state, **data)
        if on_state is not None:
            on_state(ctx, state, data)
    ctx.set_state = record                                                # type: ignore[method-assign]
    return r.tilt.run(params, ctx)


def states(r: SimpleNamespace) -> list[str]:
    out: list[str] = []
    for state, data in r.states:
        if "event" not in data and (not out or out[-1] != state):
            out.append(state)
    return out


def events(r: SimpleNamespace) -> list[str]:
    return [data["event"] for _, data in r.states if "event" in data]


def codes(r: SimpleNamespace) -> list[int]:
    """The tilt statuses set, repeats collapsed (``Position OK?`` sets 304 at every poll, as LabVIEW)."""
    out: list[int] = []
    for code in r.status.codes:
        if not out or out[-1] != code:
            out.append(code)
    return out


def ws(r: SimpleNamespace, name: str) -> list[dict[str, Any]]:
    return [e.data for e in r.events if e.topic == f"ws.{name}"]


# ============================================================================================== parameters
def test_params_from_profile_defaults_and_keys():
    p = TiltParams.from_profile(Settings())
    assert p.camera_type is CameraType.LOW_MAG and p.method is TiltMethod.STRAIGHT_EDGE and p.start_pos == (0, 0)
    assert (p.max_moves, p.step_mm, p.autofocus, p.refocus, p.overlay_tilt) == (0, 0.1, True, True, True)
    se = p.straight_edge
    assert (se.search_direction, se.min_edge_strength, se.step_px, se.width, se.kernel_size) == (
        RakeDirection.LEFT_TO_RIGHT, 20.0, 30, 10, 3)
    assert (se.min_points_percent, se.angle_expected_deg, se.angle_range_deg, se.edge_method) == (
        25.0, 0.0, 10.0, EdgeMethod.FIRST_EDGE_RAKE)
    d = p.dual_objects
    assert (d.edge_threshold, d.min_length, d.obj1_min_score, d.obj1_max_occlusion, d.obj2_max_occlusion) == (
        25, 5, 70.0, 40.0, 40.0)
    profile = Settings({"measure.tilt.camera": 0, "measure.tilt.startx": "1,5", "measure.tilt.starty": -2,
                        "measure.tilt.method": 1, "measure.tilt.maxmoves": 8, "measure.tilt.edgesearchdir": 2,
                        "measure.tilt.edgemethod": 4, "measure.tilt.anglerangedeg": 3,
                        "measure.tilt.obj1model": "a.png", "measure.tilt.obj2minscore": 55,
                        "measure.tilt.autofocus": "FALSE"})
    q = TiltParams.from_profile(profile, max_moves=4)
    assert q.camera_type is CameraType.HIGH_MAG and q.start_pos == (1.5, -2.0) and q.method is TiltMethod.DUAL_OBJECTS
    assert q.max_moves == 4 and q.autofocus is False and q.refocus is True
    assert q.straight_edge.search_direction is RakeDirection.TOP_TO_BOTTOM
    assert q.straight_edge.edge_method is EdgeMethod.BEST_EDGE_PROJECTION and q.straight_edge.angle_range_deg == 3
    assert q.dual_objects.obj1_model_path == "a.png" and q.dual_objects.obj2_min_score == 55.0
    m = q.dual_objects.match_settings(2)
    assert m.min_score == 550.0 and m.angle_ranges == ((0.0, 360.0),) and m.occlusion_range == (0.0, 40.0)
    assert m.number_of_matches == 1 and m.curve.edge_threshold == 25 and m.curve.row_step == 5
    assert q.dual_objects.match_settings(1, score_scale=1.0).min_score == 70.0          # as wired in LabVIEW
    lo = q.straight_edge.line_options()
    assert (lo.number_of_lines, lo.min_score, lo.max_score, lo.angle_tolerance_deg, lo.hough_iterations) == (
        1, 10.0, 1000.0, 1.0, 5)
    assert TiltParams(method="Dual Objects").method is TiltMethod.DUAL_OBJECTS


@pytest.mark.parametrize("bad", [{"method": 7}, {"step_mm": "x"}, {"camera_type": 5},
                                 {"straight_edge": {"step_px": 0}}, {"straight_edge": {"edge_method": 9}}])
def test_invalid_parameters_refused_before_any_motion(rig, bad):
    r = rig(edge_scene(0.0))
    with pytest.raises(TiltError) as info:
        run(r, bad)
    assert info.value.code == 9002
    assert r.status.codes == [TiltStatus.ERROR] and r.view.calls == []


def test_measure_request_overrides_the_start_position(rig):
    r = rig(edge_scene(0.0))
    r.services.profile = Settings({"measure.tilt.startx": 9.0, "measure.tilt.starty": 8.0, "measure.tilt.step": 0.5})
    request = MeasureRequest(measure=None, job=JobInfo(), profile=None, overrides={"x": 1.25})   # type: ignore[arg-type]
    p = r.tilt.params_for(request)
    assert p.start_pos == (1.25, 8.0) and p.step_mm == 0.5
    assert r.tilt.params_for(MeasureRequest(None, JobInfo(), None, {})).start_pos == (9.0, 8.0)   # type: ignore[arg-type]


# ============================================================================================== straight edge
def test_straight_edge_found_at_the_first_position(rig):
    r = rig(edge_scene(2.0))
    job = JobInfo(control_job_id="C1", process_job_id="PJ1", lot_id="L1", wafer_id="W1")
    report = run(r, edge_params(), job=job)
    assert report.valid and report.angle_deg == pytest.approx(2.0, abs=0.15)
    assert report.straight_edge is not None and report.straight_edge.valid
    assert states(r) == ["Start", "Started", "Move To Pos", "Position OK?", "Measure Tilt (Straight Edge)",
                         "Notify Client", "Display & Save", "Finished"]
    assert codes(r) == [301, 302, 303, 304, 307, 309, 400, 500]
    assert events(r) == ["TILT Started", "TILT Moving", "TILT Moving", "TILT Measured", "TILT Measured",
                         "TILT Finished"]
    assert r.xy.position() == pytest.approx((51.0, 52.0), abs=0.001)
    assert report.current_index == 1 and report.search_positions[0] == (51.0, 52.0)
    # the supervisor's measure_tilt JSON (Part 5 Appendix B)
    [tilt] = ws(r, "measure_tilt")
    assert tilt == {"Job": {"Control_Job_ID": "C1", "Process_Job_ID": "PJ1", "Wafer_ID": "W1", "Lot_ID": "L1",
                            "Zone_ID": ""},
                    "Tilt": {"Angle": f"{report.angle_deg:.6f}", "Unit": "degrees"}}
    assert ws(r, "error") == [] and ws(r, "failed") == []
    # files: job-history folders, image and data
    vision = r.tmp / "jobhistory" / "PJ1_L1" / "PJ1_L1_W1" / "Vision"
    assert report.images == (vision / "images" / "tilt" / "PJ1_L1_W1_261126_140509_tilt_tilt.png",)
    assert report.images[0].is_file()
    assert report.data_file == vision / "files" / "tilt" / "PJ1_L1_W1_261126_140509_tilt.tilt"
    assert report.data_file.read_bytes() == f"{report.angle_deg:.3f}\r\n".encode()
    assert ("info", f"Tilt Angle found: {report.angle_deg:f}°") in r.log.lines
    # overlay: the search lines, the found line and the result box
    shapes = r.services.images.overlays["tilt"]
    assert any(isinstance(s, Line) and s.color == (255, 0, 0) for s in shapes)
    assert any(isinstance(s, Text) and s.text == f"TILT ANGLE: {report.angle_deg:.1f}°" for s in shapes)
    assert any(isinstance(s, Rect) and s.color == (0, 255, 0) for s in shapes)


def test_straight_edge_loops_over_the_positions_until_valid(rig):
    second = (50.7, 52.0)                                   # the second snail position (−X by the 0.3 mm step)
    r = rig(edge_scene(-1.5, only_at=lambda x, y: abs(x - second[0]) < 0.01))
    report = run(r, edge_params(autofocus=True, refocus=True))
    assert report.valid and report.angle_deg == pytest.approx(-1.5, abs=0.15)
    assert report.current_index == 2
    seq = states(r)
    assert seq.count("Move To Pos") == 2 and seq.count("Measure Tilt (Straight Edge)") == 2
    assert seq[:10] == ["Start", "Started", "Move To Pos", "Position OK?", "Autofocus Init", "Autofocus",
                        "Autofocus Finished", "Refocus Init", "Refocus", "Refocus Finished"]
    assert len(r.af.calls) == 1 and len(r.rf.calls) == 1   # first position only
    assert r.af.calls[0].camera_type is CameraType.LOW_MAG and r.rf.calls[0].camera_type is CameraType.LOW_MAG
    assert len(ws(r, "measure_tilt")) == 1                  # only the valid tilt is sent
    assert TiltStatus.AUTOFOCUS in r.status.codes and TiltStatus.REFOCUS in r.status.codes


def test_tilt_not_found_after_the_last_position(rig):
    r = rig(edge_scene(25.0))                               # found, but outside Angle Expected ± Angle Range
    with pytest.raises(TiltFailed) as info:
        run(r, edge_params(max_moves=0))                    # Max Moves 0: two positions (one snail leg)
    exc = info.value
    assert exc.code == TILT_NOT_FOUND == 9086 and exc.state == "Move To Pos" and exc.status == 1499
    assert r.status.codes[-1] == TiltStatus.ERROR_TILT_NOT_FOUND == 1499
    assert states(r)[-3:] == ["Display & Save", "Move To Pos", "Error"]
    assert states(r).count("Measure Tilt (Straight Edge)") == 2
    assert ws(r, "error") == [{"Source": "measure_tilt", "Code": 9086,
                               "Description": "tilt not found error (no more positions)"}]
    assert ws(r, "measure_tilt") == []
    assert ("error", "Tilt (Move XY): 9086: tilt not found error (no more positions)") in r.log.lines
    assert events(r)[-1] == "TILT Finished"
    report = exc.report
    assert not report.valid and report.angle_deg == pytest.approx(25.0, abs=0.3)
    assert report.straight_edge is not None and not report.straight_edge.tolerance_ok
    assert report.data_file is None and len(report.images) == 2          # images of both positions, no data


def test_tilt_not_found_without_edge(rig):
    r = rig(edge_scene(0.0, only_at=lambda x, y: False))
    with pytest.raises(TiltFailed) as info:
        run(r, edge_params(max_moves=0))
    assert info.value.code == 9086 and r.status.codes[-1] == 1499
    assert math.isnan(info.value.report.angle_deg)


def test_runs_from_the_runner_and_the_core_reads_angle_deg(rig):
    r = rig(edge_scene(1.0))
    r.services.profile = Settings({"measure.tilt.startx": START[0], "measure.tilt.starty": START[1],
                                   "measure.tilt.autofocus": False, "measure.tilt.refocus": False,
                                   "measure.tilt.edgesearchdir": 2, "measure.tilt.edgestep": 20,
                                   "measure.tilt.edgewidth": 5})
    finished: list[Any] = []
    r.runner.on_finished(lambda name, job, status, result: finished.append(result))
    report = r.runner.run_once(FunctionName.TILT_MEASURE, MeasureRequest(None, JobInfo(), None, {}),  # type: ignore[arg-type]
                               timeout=60)
    assert report.valid and finished[-1].angle_deg == pytest.approx(1.0, abs=0.15)
    assert r.tilt.last_report is report


# ============================================================================================== dual objects
def test_dual_objects_in_one_image(rig):
    t = make_template()
    p1, p2 = (51.05, 52.01), (50.90, 52.00)                 # object 1 right, object 2 left: the same model
    r = rig(marks_scene([(t, p1), (t, p2)]))
    report = run(r, object_params())
    assert report.object1_found and report.object2_found and report.valid
    w1 = (p1[0] - ORIGIN[0], p1[1] - ORIGIN[1])
    w2 = (p2[0] - ORIGIN[0], p2[1] - ORIGIN[1])
    found = {report.best_match1_mm, report.best_match2_mm}
    assert len(found) == 2                                  # the masked object 1 is not found again
    for mm in found:
        assert min(math.dist(mm, w1), math.dist(mm, w2)) < 0.002
    expected = math.degrees(math.atan2(report.best_match2_mm[1] - report.best_match1_mm[1],
                                       report.best_match2_mm[0] - report.best_match1_mm[0]))
    expected = expected - 180.0 if expected > 90 else expected + 180.0 if expected <= -90 else expected
    assert report.angle_deg == pytest.approx(expected, abs=1e-9)
    assert abs(report.angle_deg - math.degrees(math.atan2(0.01, 0.15))) < 0.6
    assert report.object1_index == report.object2_index == 1
    assert states(r) == ["Start", "Started", "Move To Pos", "Position OK?", "Measure Tilt (Objects)",
                         "Notify Client", "Display & Save", "Finished"]
    assert codes(r) == [301, 302, 303, 304, 308, 309, 400, 500]
    [tilt] = ws(r, "measure_tilt")
    assert tilt["Tilt"] == {"Angle": f"{report.angle_deg:.6f}", "Unit": "degrees"}
    assert report.data_file == r.tmp / "data" / "files" / "tilt" / "261126_140509_tilt.tilt"


def test_dual_objects_at_different_positions(rig):
    t = make_template()
    p1 = (51.05, 52.01)                                     # seen from the start position only
    p2 = (50.55, 52.00)                                     # seen from the second position (50.7, 52.0) only
    r = rig(marks_scene([(t, p1), (t, p2)]))
    report = run(r, object_params())
    assert report.valid and report.object1_index == 1 and report.object2_index == 2
    assert report.best_match1_mm == pytest.approx((1.05, 2.01), abs=0.002)
    assert report.best_match2_mm == pytest.approx((0.55, 2.00), abs=0.002)
    # Match1 → Match2 points left: 180° + 1.15°, folded into (−90, 90]
    assert report.angle_deg == pytest.approx(math.degrees(math.atan2(0.01, 0.5)), abs=0.3)
    assert states(r).count("Measure Tilt (Objects)") == 2


def test_dual_objects_out_of_range_is_an_error(rig):
    t = make_template()
    r = rig(marks_scene([(t, (51.05, 52.06)), (t, (50.90, 52.00))]))     # about 21.8°
    with pytest.raises(TiltFailed) as info:
        run(r, object_params())
    assert info.value.code == TILT_OUT_OF_RANGE == 9535 and info.value.state == "Display & Save"
    assert r.status.codes[-1] == 1499
    assert ws(r, "measure_tilt") == [] and ws(r, "error")[0]["Code"] == 9535
    assert info.value.report.angle_deg == pytest.approx(21.8, abs=1.0)


def test_dual_objects_not_found(rig):
    r = rig(marks_scene([]))
    with pytest.raises(TiltFailed) as info:
        run(r, object_params())
    assert info.value.code == 9086 and r.status.codes[-1] == 1499
    assert not info.value.report.object1_found
    texts = [s.text for s in r.services.images.overlays["tilt"] if isinstance(s, Text)]
    assert "OBJECT NOT FOUND" in texts


def test_missing_model_is_logged_and_never_found(rig):
    t = make_template()
    r = rig(marks_scene([(t, (51.0, 52.0))]))
    params = object_params(dual_objects=DualObjectsDetection(obj1_model_path="mark.png", obj2_model_path="none.png",
                                                             obj1_min_score=60.0))
    with pytest.raises(TiltFailed) as info:
        run(r, params)
    assert info.value.code == 9086
    assert info.value.report.object1_found and not info.value.report.object2_found
    assert any(level == "warning" and "object 2" in text for level, text in r.log.lines)


# ============================================================================================== errors, stop
def test_autofocus_error_is_1303(rig):
    r = rig(edge_scene(0.0), autofocus=[FocusFailed(9008, "autofocus error", state="Measure", result=None)])
    with pytest.raises(TiltFailed) as info:
        run(r, edge_params(autofocus=True))
    assert info.value.code == 9008 and info.value.state == "Autofocus" and r.status.codes[-1] == 1303
    assert ("error", "Tilt (Autofocus): 9008: autofocus error") in r.log.lines
    assert ws(r, "error")[0] == {"Source": "measure_tilt", "Code": 9008, "Description": "autofocus error"}


def test_refocus_error_is_1304(rig):
    r = rig(edge_scene(0.0), refocus=[FocusFailed(9030, "refocus error", state="Measure", result=None)])
    with pytest.raises(TiltFailed) as info:
        run(r, edge_params(refocus=True))
    assert info.value.state == "Refocus" and r.status.codes[-1] == 1304


def test_refused_moves_are_retried_then_1301(rig):
    wafer = WaferSettings(diameter_mm=10.0, origin=ORIGIN, move_radius_mm=1.0)
    r = rig(edge_scene(0.0), xy_settings=XYSettings(wafer=wafer))
    with pytest.raises(TiltFailed) as info:
        run(r, edge_params(start_pos=(5.0, 5.0)))
    assert info.value.code == OUTSIDE_WAFER == 9404 and info.value.state == "Move To Pos"
    assert r.status.codes.count(TiltStatus.MOVE) == 4 and r.status.codes[-1] == 1301


def test_position_not_reached_is_9530(rig, monkeypatch):
    r = rig(edge_scene(0.0), options=TiltOptions(move_timeout_s=2.0, poll_s=0.1))
    monkeypatch.setattr(r.xy, "move_absolute", lambda *a, **k: None)      # accepted, never moves
    with pytest.raises(TiltFailed) as info:
        run(r, edge_params(start_pos=(3.0, 3.0)))
    assert info.value.code == TILT_XY_TIMEOUT == 9530 and info.value.state == "Position OK?"
    assert r.status.codes[-1] == 1302


def test_stop_during_the_autofocus(rig):
    def stop(ctx: FunctionContext) -> float:
        ctx.stop_event.set()
        return 33.5
    r = rig(edge_scene(0.0), autofocus=[stop])
    with pytest.raises(Stopped):
        run(r, edge_params(autofocus=True))
    assert r.states[-2][0] == "Stopped" and events(r)[-1] == "TILT Finished"
    assert r.status.codes[-1] == TiltStatus.STOPPED == 850
    assert ws(r, "failed") == [{"Function": "measure_tilt", "Job": {"Control Job ID": "", "Process Job ID": "",
                                                                     "Lot ID": "", "Wafer ID": "", "Zone ID": ""}}]
    assert r.tilt.last_report is not None and not r.tilt.last_report.valid


def test_joysticks_off_and_speed(rig, monkeypatch):
    r = rig(edge_scene(0.0))
    speeds: list[float] = []
    monkeypatch.setattr(r.xy, "set_speed", lambda v, *a, **k: speeds.append(v))
    run(r, edge_params())
    assert r.services.devices["z"].joystick == [False] and speeds == [10.0]
