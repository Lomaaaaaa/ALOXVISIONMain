"""Autofocus and refocus as measurement functions (``alox.functions.focus``, ``docs/06-autofocus-refocus.md``).

The Z stage is the real :class:`ZStage` device on ``SimStage`` with a fake clock (moves take simulated time, sleeps
advance the clock instantly). The camera is a test double whose image is a fixed random pattern blurred by a Gaussian
whose width grows with ``|Z − Z_focus|``, Z being read from the simulated stage at the exposure time; the grab then
returns ``latency_s`` later. One test drives the real :class:`Camera` device on a stage-driven ``SimCameraBackend``.
"""

from __future__ import annotations

import logging
import math
import re
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from typing import Any

import numpy as np
import pytest

from alox.core.functions import FunctionContext, FunctionName, FunctionRunner, Stopped
from alox.devices.base import DeviceError
from alox.devices.camera import Camera, CameraImage
from alox.devices.zstage import ZStage
from alox.events import Event
from alox.functions.focus import (
    Autofocus,
    AutofocusMethod,
    AutofocusParams,
    AutofocusSpeed,
    CameraType,
    FocusFailed,
    FocusOptions,
    FocusResult,
    Refocus,
    RefocusParams,
    refocus_image_name,
)
from alox.vision.focus import ni_lowpass
from aloxcam.camera.backend import Frame
from aloxcam.camera.sim import SimCameraBackend, SimCameraDevice
from aloxcam.errors import Code, FocusError
from aloxcam.imaging.png import read_png
from aloxcam.settings import (
    CameraSettings,
    SimulationOptions,
    StageOptions,
    ZStageSettings,
)
from aloxcam.zstage.sim import SimStage

Z_FOCUS = 1.234
LATENCY_S = 0.02


class FakeTime:
    """``sleep`` advances the clock instantly (as in ``test_zstage_device.py``)."""

    def __init__(self) -> None:
        self.now = 1000.0
        self._lock = threading.Lock()

    def monotonic(self) -> float:
        with self._lock:
            return self.now

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self.now += max(0.0, seconds)


class FocusScene:
    """A random pattern, Gaussian-blurred (periodic, exact in Fourier space) with σ = ``px_per_mm·|Z − Z_focus|``."""

    def __init__(self, sim: SimStage, *, z_focus: float = Z_FOCUS, shape: tuple[int, int] = (48, 64),
                 px_per_mm: float = 100.0) -> None:
        self.sim = sim
        self.z_focus = z_focus
        self.px_per_mm = px_per_mm
        self._spectrum = np.fft.fft2(np.random.default_rng(3).uniform(0, 255, shape))
        fy, fx = np.fft.fftfreq(shape[0])[:, None], np.fft.fftfreq(shape[1])[None, :]
        self._f2 = fx ** 2 + fy ** 2

    def render(self, z: float) -> np.ndarray:
        sigma = self.px_per_mm * abs(z - self.z_focus)
        img = np.fft.ifft2(self._spectrum * np.exp(-2 * np.pi ** 2 * sigma ** 2 * self._f2)).real
        return np.clip(np.rint(img), 0, 255).astype(np.uint8)


class FocusCamera:
    """Test double of :class:`alox.devices.camera.Camera` (the functions only call ``grab``): waits
    ``frame_wait_s`` for the next frame, exposes it at the stage's position, returns it ``latency_s`` later."""

    name = "hm"

    def __init__(self, scene: FocusScene, clock: FakeTime, *, frame_wait_s: float = 0.01,
                 latency_s: float = LATENCY_S) -> None:
        self.scene = scene
        self.clock = clock
        self.frame_wait_s = frame_wait_s
        self.latency_s = latency_s
        self.calls = 0
        self.fail_at: set[int] = set()
        self.on_grab: Callable[[int], None] | None = None
        self.exposures: list[tuple[float, float]] = []              # (time, true Z) of each returned image
        self.images: list[np.ndarray] = []
        self.newest: list[bool] = []

    def grab(self, *, newest: bool = True, timeout_ms: int | None = None) -> CameraImage:
        n = self.calls
        self.calls += 1
        self.newest.append(newest)
        if self.on_grab is not None:
            self.on_grab(n)
        if n in self.fail_at:
            raise DeviceError("hm", "grab: IMAQdx Get Image2: timeout (simulation)", code=int(Code.CAMERA_SDK))
        self.clock.sleep(self.frame_wait_s)
        t, z = self.clock.monotonic(), self.scene.sim.position
        pixels = self.scene.render(z)
        self.exposures.append((t, z))
        self.images.append(pixels)
        self.clock.sleep(self.latency_s)
        return CameraImage(pixels, n, t, self.name, 1000.0, 0.0, False)


@dataclass
class Rig:
    clock: FakeTime
    sim: SimStage
    z: ZStage
    scene: FocusScene
    camera: FocusCamera
    runner: FunctionRunner
    events: list[Event]

    def ctx(self, name: FunctionName = FunctionName.AUTOFOCUS_LARGE) -> FunctionContext:
        return FunctionContext(name, None, self.runner)

    def autofocus(self, cameras: Any = None, **options: Any) -> Autofocus:
        return Autofocus(self.z, self.camera if cameras is None else cameras, options=FocusOptions(**options),
                         clock=self.clock.monotonic, sleep=self.clock.sleep)

    def refocus(self, cameras: Any = None, **options: Any) -> Refocus:
        return Refocus(self.z, self.camera if cameras is None else cameras, options=FocusOptions(**options),
                       clock=self.clock.monotonic, sleep=self.clock.sleep)

    def settled_position(self) -> float:
        self.z.wait_end_move()
        return self.z.position()

    def states(self, topic: str) -> list[dict[str, Any]]:
        return [e.data for e in self.events if e.topic == f"function.{topic}.state"]

    def motion_frames(self) -> list[str]:
        return [f for f in self.sim.frames if f.startswith(("MOV", "VEL "))]


@pytest.fixture
def rig() -> Iterator[Rig]:
    clock = FakeTime()
    sim = SimStage(SimulationOptions(), clock=clock.monotonic, dh_running=False)
    z = ZStage(sim, ZStageSettings(min_pos=0.0, max_pos=20.0), StageOptions(), sleep=clock.sleep,
               monotonic=clock.monotonic, reconnect_delay_s=0.0)
    z.open(timeout=10)
    events: list[Event] = []
    scene = FocusScene(sim)
    yield Rig(clock, sim, z, scene, FocusCamera(scene, clock), FunctionRunner(publish=events.append), events)
    z.shutdown(timeout=5)


def slow_params(**changes: Any) -> AutofocusParams:
    values: dict[str, Any] = {"speed": "Slow", "method": "Sobel", "z_min": 1.0, "z_max": 1.5, "step": 0.1,
                              "subsampling": 0.5}
    values.update(changes)
    return AutofocusParams(**values)


def fast_params(**changes: Any) -> AutofocusParams:
    return slow_params(**({"speed": "Fast", "duration_s": 1, "step": 0.0} | changes))


def rf_params(**changes: Any) -> RefocusParams:
    values: dict[str, Any] = {"method": "Sobel", "steps": 3, "step_mm": 0.01, "fin_roi_width": 40,
                              "fin_roi_height": 30, "wait_ms": 20}
    values.update(changes)
    return RefocusParams(**values)


# ============================================================================================== autofocus, Slow
def test_slow_autofocus_points_progress_and_end_state(rig):
    """§9.7 "AF Slow point set": repeated addition gives 5 rows, the last MOV goes one step beyond Z Max, the last
    Measure reports 100.00000000000009, then Finished 100; the initial speed is restored."""
    rig.z.set_speed(2.0)
    result = rig.autofocus().run(slow_params(), rig.ctx())
    assert [z for z, _ in result.table] == [1.0, 1.1, 1.2000000000000002, 1.3000000000000003, 1.4000000000000004]
    assert result.best_z == 1.2000000000000002 and result.best_index == 2       # nearest to Z_FOCUS
    assert result.best_score == max(s for _, s in result.table)
    states = rig.states("autofocus_large")
    assert [s["state"] for s in states] == (["Start"] + ["Goto Z Pos", "Measure"] * 5 + ["Finished"])
    measures = [s for s in states if s["state"] == "Measure"]
    assert measures[-1]["progress"] == 100.00000000000009
    assert [m["z"] for m in measures] == [z for z, _ in result.table]
    assert all(s["progress"] == 0.0 for s in states if s["state"] == "Goto Z Pos")   # back to 0 between points
    assert states[-1]["progress"] == 100.0 and result.progress == 100.0
    assert rig.sim.velocity == 2.0                                                   # VEL Init Speed
    assert "MOV 1 1.5000" in rig.sim.frames                                          # one step beyond Z Max
    assert rig.settled_position() == pytest.approx(result.best_z)                    # owner B9: ends at the best Z
    assert result.position == pytest.approx(result.best_z)
    assert rig.camera.newest == [True] * 5                                           # Camera.grab(newest=True)


def test_autofocus_can_leave_the_stage_where_the_scan_ended_as_labview(rig):
    """``af_move_to_best=False``: LabVIEW's end position (one step beyond Z Max), Q22."""
    rig.autofocus(af_move_to_best=False).run(slow_params(), rig.ctx())
    assert rig.settled_position() == pytest.approx(1.5)


def test_slow_autofocus_settles_100_ms_at_each_point(rig):
    durations = []
    for settle in (0.1, 0.0):
        rig.z.move_absolute(1.0)
        t0 = rig.clock.monotonic()
        rig.autofocus(af_settle_s=settle).run(slow_params(), rig.ctx())
        durations.append(rig.clock.monotonic() - t0)
    assert durations[0] - durations[1] == pytest.approx(5 * 0.1)


def test_autofocus_runs_sobel_for_every_method_but_fft_and_prewitt(rig):
    """§4.5: Diff, Gradient, Sigma and FULL run the Sobel metric in the autofocus."""
    tables = {m: rig.autofocus().run(slow_params(method=m), rig.ctx()).table for m in AutofocusMethod}
    for method in ("Diff", "Gradient", "Sigma", "FULL"):
        assert tables[AutofocusMethod.coerce(method, "m")] == tables[AutofocusMethod.SOBEL]
    assert tables[AutofocusMethod.FFT] != tables[AutofocusMethod.SOBEL] != tables[AutofocusMethod.PREWITT]
    assert all(t[2][1] == max(s for _, s in t) for t in tables.values())            # all peak at 1.2


def test_final_over_travel_move_outside_the_limits_is_skipped(rig, caplog):
    rig.z.move_absolute(19.6)
    with caplog.at_level(logging.WARNING, logger="alox.functions.focus"):
        result = rig.autofocus().run(slow_params(z_min=19.6, z_max=20.0, step=0.2), rig.ctx())
    assert [z for z, _ in result.table] == [19.6, 19.8, 20.0]
    assert "outside the limits" in caplog.text
    assert rig.settled_position() == pytest.approx(result.best_z)                    # 19.6, nearest to the focus


def test_points_outside_the_limits_fail_at_once(rig):
    """The Z device refuses moves outside the software range (9405), so a scan beyond the limits stops there."""
    rig.z.move_absolute(5.0)
    with pytest.raises(FocusFailed) as info:
        rig.autofocus().run(slow_params(z_min=19.8, z_max=20.2), rig.ctx())
    assert info.value.code == Code.Z_OUT_OF_RANGE and info.value.state == "Measure"
    assert [z for z, _ in info.value.result.table] == [19.8, 19.900000000000002]
    assert rig.settled_position() == pytest.approx(5.0)                         # Error state: back to Init Z


# ============================================================================================== autofocus, Fast
def test_fast_autofocus_scans_continuously(rig):
    """§9.7 "AF Fast": VEL = |dZ|/Duration, increasing interpolated Z, ends after ≥ Duration at Z Max, speed
    restored."""
    rig.z.set_speed(2.0)
    result = rig.autofocus().run(fast_params(), rig.ctx())
    assert "VEL 1 0.5000" in rig.sim.frames and "MOV 1 1.5000" in rig.sim.frames
    zs = [z for z, _ in result.table]
    assert len(zs) >= 25 and all(a < b for a, b in pairwise(zs))
    assert zs[-1] >= 1.5                                                          # last image: elapsed ≥ Duration
    assert abs(result.best_z - Z_FOCUS) <= 0.015
    assert rig.sim.velocity == 2.0
    assert rig.settled_position() == pytest.approx(result.best_z)
    states = rig.states("autofocus_large")
    assert [s["state"] for s in states][:4] == ["Start", "Goto Z Pos", "Wait Started", "Measure"]
    measures = [s for s in states if s["state"] == "Measure"]
    assert measures[-1]["progress"] >= 100.0                                      # §2.7: the last point reports ≥ 100
    assert states[-1] == {"state": "Finished", "progress": 100.0}


@pytest.mark.parametrize(("compensation", "low", "high"), [(0.0, 0.007, 0.0105), (LATENCY_S, -0.002, 0.002)])
def test_fast_latency_compensation(rig, compensation, low, high):
    """D16 / vision-core plan §6: the Z of a Fast image is estimated from the post-grab time ``ti``. Without
    compensation (LabVIEW formula) it is biased by ≈ speed × latency (0.5 mm/s × 20 ms = 10 µm, minus the ≤ 3 ms by
    which T0 trails the real start); ``fast_latency_s`` = the real latency removes the bias."""
    result = rig.autofocus(fast_latency_s=compensation).run(fast_params(), rig.ctx())
    true_z = np.array([z for _, z in rig.camera.exposures])
    errors = np.array([z for z, _ in result.table]) - true_z
    moving = (true_z > 1.0 + 1e-4) & (true_z < 1.5 - 1e-4)                    # exclude the end of travel
    assert low <= errors[moving].min() and errors[moving].max() <= high


def test_fast_latency_compensation_shifts_every_estimate_by_speed_times_latency(rig):
    rows: list[np.ndarray] = []
    for compensation in (0.0, 0.03):
        rig.z.move_absolute(1.0)
        rows.append(np.array([z for z, _ in rig.autofocus(fast_latency_s=compensation).run(
            fast_params(), rig.ctx()).table]))
    n = min(len(r) for r in rows)
    assert np.allclose(rows[0][:n] - rows[1][:n], 0.5 * 0.03, atol=1e-9)


def test_fast_autofocus_times_out_reaching_z_min(rig):
    """§4.5 Goto Z Pos (Fast): 30 s from Start → 9401 "z stage get position timeout", then the Error state."""
    t0 = rig.clock.monotonic()
    with pytest.raises(FocusFailed) as info:
        rig.autofocus(normal_speed=0.01, poll_interval_s=0.5).run(fast_params(), rig.ctx())
    assert (info.value.code, info.value.source) == (9401, "z stage get position timeout")
    assert info.value.state == "Goto Z Pos" and info.value.result.table == ()
    assert info.value.result.progress == 0.0
    assert 30.0 < rig.clock.monotonic() - t0 < 32.0
    assert rig.states("autofocus_large")[-1] == {"state": "Error", "progress": 0.0, "code": 9401,
                                                 "error": "z stage get position timeout",
                                                 "failed_state": "Goto Z Pos"}


def test_slow_autofocus_has_a_timeout_too(rig):
    """D6 / owner Q22: "add a timeout" where LabVIEW waited forever."""
    with pytest.raises(FocusFailed) as info:
        rig.autofocus(normal_speed=0.01, poll_interval_s=0.5, af_position_timeout_s=5.0).run(slow_params(), rig.ctx())
    assert info.value.code == Code.Z_POSITION_TIMEOUT and info.value.state == "Goto Z Pos"


# ============================================================================================== autofocus, errors
def test_failure_mid_scan_restores_and_keeps_the_data(rig):
    """§9.7 "AF failure mid-scan" / D1, D15: a row with score 0 is kept, the Error state restores the initial speed
    and moves back to Init Z; progress and data stay those of the failed step (never 100)."""
    rig.z.move_absolute(3.0)
    rig.z.set_speed(2.0)
    rig.camera.fail_at = {2}
    with pytest.raises(FocusFailed) as info:
        rig.autofocus().run(slow_params(), rig.ctx())
    failure = info.value
    assert failure.code == Code.CAMERA_SDK and failure.state == "Measure"
    assert isinstance(failure.__cause__, DeviceError)
    assert [z for z, _ in failure.result.table] == [1.0, 1.1, 1.2000000000000002]
    assert failure.result.table[2][1] == 0.0
    assert failure.result.progress == pytest.approx(60.0)
    assert rig.sim.velocity == 2.0
    assert not any(f.startswith("MOV 1 1.3") for f in rig.sim.frames)                # no move after the failure
    assert rig.settled_position() == pytest.approx(3.0)
    assert rig.states("autofocus_large")[-1]["state"] == "Error"


@pytest.mark.parametrize("abort", [False, True])
def test_stop_during_a_scan(rig, abort):
    """ctx.check() between steps: a stop (or abort) after the 3rd image ends the run with Stopped, after the
    restore of the Error state (§9.5 abort): initial speed and initial position."""
    rig.z.move_absolute(3.0)
    rig.z.set_speed(2.0)
    ctx = rig.ctx()
    rig.camera.on_grab = lambda n: (ctx.abort_event if abort else ctx.stop_event).set() if n == 2 else None
    with pytest.raises(Stopped) as info:
        rig.autofocus().run(slow_params(), ctx)
    assert info.value.aborted is abort
    assert rig.camera.calls == 3
    assert rig.sim.velocity == 2.0
    assert rig.settled_position() == pytest.approx(3.0)
    assert "Finished" not in [s["state"] for s in rig.states("autofocus_large")]


def test_stop_interrupts_the_wait_for_the_stage(rig):
    ctx = rig.ctx()
    calls = {"n": 0}
    real_sleep = rig.clock.sleep

    def sleep(seconds: float) -> None:
        calls["n"] += 1
        if calls["n"] == 10:
            ctx.stop_event.set()
        real_sleep(seconds)

    autofocus = Autofocus(rig.z, rig.camera, options=FocusOptions(normal_speed=0.01), clock=rig.clock.monotonic,
                          sleep=sleep)
    with pytest.raises(Stopped):
        autofocus.run(slow_params(), ctx)
    assert rig.camera.calls == 0 and calls["n"] == 10


# ============================================================================================== parameters
@pytest.mark.parametrize("params", [
    AutofocusParams(),                                          # typedef defaults: Subsampling 0, Step 0 (D8, F13)
    slow_params(step=0.0), slow_params(step=-0.1), slow_params(step=math.nan),
    slow_params(subsampling=0.0), slow_params(subsampling=-0.5), slow_params(subsampling=math.nan),
    slow_params(z_min=math.nan), slow_params(z_max=math.inf), slow_params(roi=(1, 2, 3)),
    fast_params(duration_s=0), fast_params(duration_s=-1), fast_params(duration_s=1.5), fast_params(z_max=1.0),
])
def test_invalid_autofocus_parameters_are_refused_before_any_motion(rig, params):
    rig.sim.clear_frames()
    with pytest.raises(FocusError) as info:
        rig.autofocus().run(params, rig.ctx())
    assert info.value.code == Code.FOCUS_INVALID_PARAMS
    assert rig.motion_frames() == [] and rig.camera.calls == 0


@pytest.mark.parametrize("params", [
    rf_params(steps=-1), rf_params(steps=1.5), rf_params(step_mm=math.nan), rf_params(wait_ms=-5),
    rf_params(fin_roi_width=10.5)])
def test_invalid_refocus_parameters_are_refused_before_any_motion(rig, params):
    rig.sim.clear_frames()
    with pytest.raises(FocusError) as info:
        rig.refocus().run(params, rig.ctx(FunctionName.REFOCUS))
    assert info.value.code == Code.FOCUS_INVALID_PARAMS and rig.motion_frames() == []


def test_parameters_accept_labels_codes_and_members():
    p = AutofocusParams(camera_type="Low Mag.", speed="fast", method=2, roi=[1, 2, 30, 40])
    assert (p.camera_type, p.speed, p.method, p.roi) == (CameraType.LOW_MAG, AutofocusSpeed.FAST,
                                                         AutofocusMethod.PREWITT, (1, 2, 30, 40))
    assert RefocusParams(method="FULL", refocus_method="Oxyd").method is AutofocusMethod.FULL
    for bad in ({"method": "Laplace"}, {"speed": 7}, {"camera_type": True}, {"roi": [1.5, 2, 3, 4]}):
        with pytest.raises(FocusError) as info:
            AutofocusParams(**bad)
        assert info.value.code == Code.FOCUS_INVALID_PARAMS


def test_wrong_parameter_type_is_refused(rig):
    with pytest.raises(FocusError):
        rig.autofocus().run(rf_params(), rig.ctx())
    with pytest.raises(FocusError):
        rig.refocus().run(None, rig.ctx(FunctionName.REFOCUS))


def test_check_z_limits_refuses_the_start(rig):
    """D5 switch ``af_check_z_limits``: [Z Min, Z Max] outside the stage limits → 9405 "Z move out of range"."""
    rig.sim.clear_frames()
    with pytest.raises(FocusError) as info:
        rig.autofocus(check_z_limits=True).run(slow_params(z_min=19.0, z_max=21.0), rig.ctx())
    assert (info.value.code, info.value.source) == (9405, "Z move out of range") and rig.motion_frames() == []
    rig.autofocus(check_z_limits=True).run(slow_params(), rig.ctx())                    # inside: accepted


def test_options_are_validated():
    for bad in ({"poll_interval_s": -1.0}, {"fast_latency_s": math.nan}, {"rf_scale": 0.0}, {"normal_speed": 0.0}):
        with pytest.raises(ValueError):
            FocusOptions(**bad)


def test_cameras_are_selected_by_camera_type(rig):
    autofocus = rig.autofocus(cameras={"Low Mag.": rig.camera})
    with pytest.raises(FocusError) as info:
        autofocus.run(slow_params(), rig.ctx())
    assert info.value.code == Code.FOCUS_INVALID_PARAMS and "High Mag." in info.value.source
    assert len(autofocus.run(slow_params(camera_type="Low Mag."), rig.ctx()).table) == 5


# ============================================================================================== refocus
@pytest.mark.parametrize("method", ["FFT", "Sobel", "Prewitt", "Diff", "Gradient", "FULL"])
def test_refocus_moves_to_the_best_position(rig, method):
    """§9.7 "RF nominal": 7 positions 1.22 … 1.28 around 1.25, progress 100 from the 7th measurement on, best =
    grid point nearest to Z_focus, stage left there, speed restored."""
    rig.z.move_absolute(1.25)
    rig.z.set_speed(2.0)
    t0 = rig.clock.monotonic()
    result = rig.refocus().run(rf_params(method=method), rig.ctx(FunctionName.REFOCUS))
    positions = [1.25 - 3.0 * 0.01 + float(i) * 0.01 for i in range(7)]
    assert [z for z, _ in result.table] == positions
    assert result.best_index == 1 and result.best_z == positions[1]                # 1.23: |dZ| = 4 µm
    assert result.best_score == max(s for _, s in result.table)
    assert result.position == pytest.approx(1.23) and rig.z.position() == pytest.approx(1.23)
    assert rig.sim.velocity == 2.0
    assert rig.clock.monotonic() - t0 >= 8 * 0.020                                 # Wait (ms) at 7 points + best
    states = rig.states("refocus")
    measures = [s for s in states if s["state"] == "Mesure Position"]
    assert [round(m["progress"], 6) for m in measures] == [round((i + 1) / 7 * 100, 6) for i in range(7)]
    assert [s["state"] for s in states][-4:] == ["Get Focus", "Move Focus", "Wait Focus", "Finished"]
    assert all(s["progress"] == 100.0 for s in states[-4:])


def test_refocus_sigma_saves_the_filtered_images(rig, tmp_path):
    """§5.7 / D18: Sigma low-pass-filters the whole image first; the saved PNG is the filtered one (§8)."""
    rig.z.move_absolute(1.25)
    result = rig.refocus().run(rf_params(method="Sigma", save_images=True, folder=tmp_path),
                               rig.ctx(FunctionName.REFOCUS))
    assert len(result.images) == 7 == len(list(tmp_path.iterdir()))
    for i, path in enumerate(result.images):
        assert re.fullmatch(rf"\d{{6}}_\d{{6}}_refocus__mesa {i}\.png", path.name)
        assert np.array_equal(read_png(path), ni_lowpass(rig.camera.images[i], 20, 20, 40))


def test_refocus_image_names():
    """§8.3: double underscore, index of width 2 padded with a space (inferred)."""
    when = datetime(2023, 10, 8, 14, 32, 10, tzinfo=UTC)
    assert refocus_image_name(3, when) == "231008_143210_refocus__mesa 3.png"
    assert refocus_image_name(12, when) == "231008_143210_refocus__mesa12.png"
    assert refocus_image_name(123, when) == "231008_143210_refocus__mesa123.png"


def test_refocus_image_folder_must_exist(rig, tmp_path):
    rig.z.move_absolute(1.25)
    with pytest.raises(FocusFailed) as info:
        rig.refocus().run(rf_params(save_images=True, folder=tmp_path / "missing"), rig.ctx(FunctionName.REFOCUS))
    failure = info.value
    assert failure.code == Code.IMAGE_FILE and failure.state == "Mesure Position"
    assert len(failure.result.table) == 7                                           # zero-padded (§2.6)
    assert failure.result.table[0][1] > 0 and all(s == 0.0 for _, s in failure.result.table[1:])
    assert failure.result.best_z is None
    assert rig.settled_position() == pytest.approx(1.25)                             # back to Init Z


def test_refocus_refuses_to_run_with_the_joystick_enabled(rig):
    """§9.7 "RF joystick": 9031; nothing was read, so the restore sends no VEL / MOV (D11, Q20)."""
    rig.z.set_joystick(True)
    rig.sim.clear_frames()
    with pytest.raises(FocusFailed) as info:
        rig.refocus().run(rf_params(), rig.ctx(FunctionName.REFOCUS))
    assert (info.value.code, info.value.source) == (9031, "'refocus' warning (joystick enabled)")
    assert info.value.state == "Start" and info.value.result.table == () and info.value.result.progress == 0.0
    assert rig.motion_frames() == []


def test_refocus_positions_are_clamped_to_the_limits(rig):
    """§5.5 Get Positions: near a limit, several positions are clamped to it and measured again."""
    rig.z.move_absolute(19.995)
    rig.scene.z_focus = 20.0
    result = rig.refocus().run(rf_params(), rig.ctx(FunctionName.REFOCUS))
    assert [z for z, _ in result.table] == [19.965, 19.975, 19.985, 19.995, 20.0, 20.0, 20.0]
    assert result.best_index == 4                                                     # first maximum wins
    assert rig.z.position() == pytest.approx(20.0)


def test_refocus_per_move_timeout(rig):
    """§9.7 "RF per-move timeout": 5 s after the MOV → 9401, then the Error state."""
    rig.z.move_absolute(1.25)
    rig.z.set_speed(2.0)
    t0 = rig.clock.monotonic()
    with pytest.raises(FocusFailed) as info:
        rig.refocus(normal_speed=0.001, poll_interval_s=0.25).run(rf_params(), rig.ctx(FunctionName.REFOCUS))
    assert info.value.code == Code.Z_POSITION_TIMEOUT and info.value.state == "Check Position"
    assert 5.0 < rig.clock.monotonic() - t0 < 6.0
    assert [s for _, s in info.value.result.table] == [0.0] * 7
    assert rig.sim.velocity == 2.0


def test_refocus_roi_outside_the_image_fails_with_the_metric_error(rig):
    """07 F12: the Fin ROI is not clipped; a rectangle outside the image is a metric error (9503) at the first
    measurement, which still appends a score 0 (D15)."""
    rig.z.move_absolute(1.25)
    with pytest.raises(FocusFailed) as info:
        rig.refocus().run(rf_params(fin_roi_width=300), rig.ctx(FunctionName.REFOCUS))
    assert info.value.code == Code.FOCUS_METRIC and info.value.state == "Mesure Position"
    assert info.value.result.table[0] == (1.22, 0.0)
    assert info.value.result.progress == pytest.approx(100 / 7)


def test_low_mag_refocus_uses_the_margin_roi(rig):
    rig.z.move_absolute(1.25)
    params = rf_params(camera_type="Low Mag.", fin_roi_width=300)                 # FinW is the left margin:
    with pytest.raises(FocusFailed):                                               # [300, 30, 64, 48] is empty
        rig.refocus(cameras={CameraType.LOW_MAG: rig.camera}).run(params, rig.ctx(FunctionName.REFOCUS))
    result = rig.refocus(cameras={CameraType.LOW_MAG: rig.camera}).run(
        rf_params(camera_type="Low Mag.", fin_roi_width=10, fin_roi_height=8), rig.ctx(FunctionName.REFOCUS))
    assert result.best_z == pytest.approx(1.23)


def test_steps_zero_measures_the_current_position_only(rig):
    rig.z.move_absolute(1.25)
    result = rig.refocus().run(rf_params(steps=0), rig.ctx(FunctionName.REFOCUS))
    assert len(result.table) == 1 and result.best_z == 1.25


# ============================================================================================== integration
def test_functions_run_through_the_function_runner(rig):
    rig.runner.register(rig.autofocus())
    rig.runner.register(rig.refocus())
    result = rig.runner.run_once(FunctionName.AUTOFOCUS_LARGE, slow_params(), timeout=10)
    assert isinstance(result, FocusResult) and result.array.shape == (5, 2)
    assert rig.runner.last_result(FunctionName.AUTOFOCUS_LARGE) is result
    topics = [e.topic for e in rig.events]
    assert topics[0] == "function.autofocus_large.started" and topics[-1] == "function.autofocus_large.finished"
    rig.z.set_joystick(True)
    with pytest.raises(RuntimeError, match="9031"):
        rig.runner.run_once(FunctionName.REFOCUS, rf_params(), timeout=10)
    assert "joystick" in (rig.runner.last_error(FunctionName.REFOCUS) or "")


class _StageDrivenDevice(SimCameraDevice):
    """A simulated IMAQdx session whose frames are rendered from the Z stage at the moment of the grab."""

    def __init__(self, name: str, options: SimulationOptions, scene: FocusScene) -> None:
        super().__init__(name, options, manual=True)
        self.scene = scene
        self.modes: list[str] = []

    def _frame(self, mode: str) -> Frame:
        if not self.acquisition_in_progress():
            raise AssertionError("acquisition not started")
        self.modes.append(mode)
        return Frame(len(self.modes) - 1, self.scene.render(self.scene.sim.position), 0.0)

    def get_last_new(self, timeout_ms: int) -> Frame:
        return self._frame("last new")

    def get_next(self, timeout_ms: int) -> Frame:
        return self._frame("next")


class _StageDrivenBackend(SimCameraBackend):
    def __init__(self, options: SimulationOptions, scene: FocusScene) -> None:
        super().__init__(options, manual=True)
        self.scene = scene
        self.device: _StageDrivenDevice | None = None

    def open_camera(self, name: str) -> _StageDrivenDevice:
        self.device = _StageDrivenDevice(name, self.options, self.scene)
        return self.device


@pytest.mark.parametrize("newest", [True, False])
def test_refocus_with_the_camera_device(rig, newest):
    """The real :class:`Camera` device (worker thread, flip, attributes) on a stage-driven simulated driver;
    ``newest_frame`` selects IMAQdx "Last New" or LabVIEW's "Next"."""
    backend = _StageDrivenBackend(SimulationOptions(image_width=64, image_height=48), rig.scene)
    camera = Camera(backend, CameraSettings.default_high_mag(), name="hm", sleep=lambda _s: None)
    camera.open(timeout=10)
    try:
        rig.z.move_absolute(1.25)
        result = rig.refocus(cameras=camera, newest_frame=newest).run(rf_params(), rig.ctx(FunctionName.REFOCUS))
    finally:
        camera.shutdown(timeout=5)
    assert result.best_z == pytest.approx(1.23)
    assert backend.device is not None and backend.device.modes == ["last new" if newest else "next"] * 7
