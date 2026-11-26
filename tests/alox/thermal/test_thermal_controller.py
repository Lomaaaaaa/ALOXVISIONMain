"""The thermal map controller (``alox.thermal.controller``) on simulated stages and emulated probes."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest

from alox.core.properties import Properties
from alox.devices.xy import WaferSettings
from alox.events import Event
from alox.thermal import controller as controller_module
from alox.thermal import plot as plot_module
from alox.thermal.controller import (
    ChuckFrame,
    Step,
    ThermalBusy,
    ThermalController,
    ThermalError,
    ThermalState,
    Tool,
    polar_to_chuck,
)
from alox.thermal.optris import OptrisPyrometer, PyroSettings
from alox.thermal.optris_sim import build_sim_optris
from alox.thermal.settings import MeasureOptions, ThermalConfig, ThermalSettings

from .conftest import ORIGIN, SMALL, FakeTime, Rig, chuck_temperature


def hook(rig: Rig, action: Callable[[Event], None]) -> None:
    """Call ``action`` on every event published by the controller (in the publishing thread)."""
    def publish(event: Event) -> None:
        rig.events.append(event)
        action(event)
    rig.controller._publish = publish


def test_frame_and_polar_helpers() -> None:
    frame = ChuckFrame((100.0, 100.0), 3)
    assert frame.to_stage(10.0, -5.0) == (90.0, 105.0)
    assert frame.to_chuck(90.0, 105.0) == (10.0, -5.0)
    assert ChuckFrame((50.0, 60.0), 0).to_stage(1.0, 2.0) == (51.0, 62.0)
    assert polar_to_chuck(10.0, 90.0) == pytest.approx((0.0, 10.0))


def test_full_measurement_with_the_pyrometer(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    c = rig.controller
    assert c.is_open and c.tool == Tool.PYROMETER and c.connected and c.can_start
    assert len(c.points) == 5
    t0 = rig.clock.now
    c.start()
    assert c.wait(30)
    summary = c.summary
    assert summary is not None and summary.completed and summary.reason == "completed"
    assert (summary.measured, summary.points, summary.csv_name) == (5, 5, "Thermal_measurement_261126_140509.csv")
    assert c.state == ThermalState.IDLE
    results = c.table.results
    expected = [chuck_temperature(p.x, p.y) for p in c.points]
    assert [r.temperature for r in results] == pytest.approx(expected, abs=0.051)
    assert [round(r.temperature) for r in results] == [300, 310, 301, 290, 299]
    assert all(r.std == 0.0 and r.failed == 0 and len(r.samples) == 3 for r in results)
    assert rig.chuck_position() == pytest.approx((0.0, -10.0), abs=1e-9)      # last point: angle 270
    # the states of one point, in order, and the overall progress
    steps = [e.data["step"] for e in rig.of("thermal.progress") if e.data["point"] == 2][:7]
    assert steps == [str(s) for s in (Step.START, Step.MOVING, Step.WAIT_MOVE, Step.WAIT_TEMP, Step.MEASURING,
                                      Step.WRITING, Step.END)]
    progress = [e.data["progress"] for e in rig.of("thermal.progress")]
    assert progress[0] == 0.0 and 20.0 in progress and progress[-1] == 100.0
    points = rig.of("thermal.point")
    assert [e.data["order"] for e in points] == [0, 1, 2, 3, 4] and points[1].data["x"] == pytest.approx(10.0)
    finished = rig.of("thermal.finished")
    assert len(finished) == 1 and finished[0].data["completed"] is True
    # WAIT TEMP (100 ms) and 3 samples 50 ms apart at least, per point
    assert rig.clock.now - t0 >= 5 * (0.1 + 0.15)
    cells = c.table.cells()
    assert cells[1][:2] == ["10.00", "0.00"] and cells[1][4] == "310.00"


def test_stop_ends_after_the_current_point_without_stopping_the_stages(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    c = rig.controller
    hook(rig, lambda e: c.stop() if e.topic == "thermal.point" else None)
    c.start()
    assert c.wait(30)
    summary = c.summary
    assert summary is not None and not summary.completed and summary.reason == "stopped" and summary.measured == 1
    assert not [e for e in rig.events if e.topic == "device.xy.stopped"]
    assert [e.data["state"] for e in rig.of("thermal.state")][-3:] == ["measuring", "stopping", "idle"]
    assert rig.of("thermal.progress")[-1].data["progress"] == 100.0


def test_stop_while_measuring_does_not_write_the_partial_point(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    c = rig.controller
    hook(rig, lambda e: c.stop() if e.topic == "thermal.temperature" else None)
    c.start()
    assert c.wait(30)
    assert c.summary is not None and c.summary.measured == 0 and c.table.results == []


def test_emergency_stop_stops_the_stages(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    c = rig.controller

    def on_event(event: Event) -> None:
        if event.topic == "thermal.progress" and event.data["step"] == "WAIT MOVE" and event.data["point"] == 2:
            c.emergency_stop()
    hook(rig, on_event)
    c.start()
    assert c.wait(30)
    assert c.summary is not None and c.summary.reason == "emergency stop" and c.summary.measured == 1
    topics = {e.topic for e in rig.events}
    assert {"device.xy.stopped", "device.z.stopped"} <= topics


def test_points_outside_the_allowed_radius_are_refused_before_starting(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(wafer=WaferSettings(diameter_mm=100.0, origin=ORIGIN, move_radius_mm=5.0))
    with pytest.raises(ThermalError) as info:
        rig.controller.start()
    assert info.value.code == 9404 and "4 point(s)" in info.value.message
    assert rig.controller.state == ThermalState.IDLE


def test_start_needs_a_connected_probe_an_origin_and_points(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(open_tool=False)
    c = rig.controller
    with pytest.raises(ThermalError, match="not open"):
        c.start()
    rig.emulator.port_missing = True
    assert c.open(live=False).result(10) is False
    assert not c.connected and not c.can_start
    with pytest.raises(ThermalError, match="not connected"):
        c.start()
    assert rig.of("thermal.tool")[-1].data["connected"] is False
    rig.emulator.port_missing = False
    assert c.refresh().result(10) is True and c.connected
    c.set_circles([], [])
    with pytest.raises(ThermalError, match="no measurement point"):
        c.start()
    rig2 = make_rig(ThermalSettings(diameters=(10.0,), nb_points=(4,), origin=(0.0, 0.0)))
    with pytest.raises(ThermalError, match="origin"):
        rig2.controller.start()


def test_failed_samples_are_left_out_and_counted(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    rig.emulator.drop_next_answer()                       # the first sample of point 1 times out (pyrometer fault)
    rig.controller.start()
    assert rig.controller.wait(30)
    first = rig.controller.table.results[0]
    assert first.failed == 1 and len(first.samples) == 3 and first.temperature == pytest.approx(300.0)
    assert rig.of("thermal.point")[0].data["failed"] == 1
    assert any(e.topic == "device.pyro.reconnected" for e in rig.events)
    assert rig.controller.summary is not None and rig.controller.summary.completed


class StuckXY:
    """An XY device whose stage never reaches its destination until ``reach_after`` moves."""

    def __init__(self, clock: FakeTime, reach_after: int | None = None) -> None:
        self.clock = clock
        self.reach_after = reach_after
        self.moves: list[tuple[float, float, float]] = []
        self.target = (0.0, 0.0)
        self.stopped = 0

    def move_absolute(self, x: float | None = None, y: float | None = None, *, wait: bool = True,
                      timeout: float | None = None) -> tuple[float, float]:
        self.moves.append((self.clock.now, x or 0.0, y or 0.0))
        self.target = (x or 0.0, y or 0.0)
        return self.target

    def position(self, *, timeout: float | None = None) -> tuple[float, float]:
        if self.reach_after is not None and len(self.moves) >= self.reach_after:
            return self.target
        return self.target[0] + 0.5, self.target[1]

    def stop(self) -> None:
        self.stopped += 1


def _stuck_controller(clock: FakeTime, xy: StuckXY) -> tuple[ThermalController, OptrisPyrometer, list[Event]]:
    backend, _ = build_sim_optris(sleep=clock.sleep, monotonic=clock.monotonic)
    pyro = OptrisPyrometer(backend, PyroSettings(), sleep=clock.sleep, reconnect_delay_s=0.0)
    events: list[Event] = []
    settings = ThermalSettings(diameters=(20.0,), nb_points=(1,), samples=1, wait_ms=0, offset_angle=0,
                               origin=ORIGIN)
    c = ThermalController(xy=xy, pyrometer=pyro, config=ThermalConfig(settings=settings), publish=events.append,
                          clock=clock.monotonic, sleep=clock.sleep)
    assert c.open(live=False).result(10)
    return c, pyro, events


def test_a_move_is_issued_again_after_30_s_then_the_point_fails() -> None:
    clock = FakeTime()
    xy = StuckXY(clock)
    c, pyro, events = _stuck_controller(clock, xy)
    try:
        t0 = clock.now
        c.start()
        assert c.wait(30)
        times = [t - t0 for t, _, _ in xy.moves]
        assert times[0] == 0.0 and len(times) == 3
        assert [b - a for a, b in pairwise(times)] == pytest.approx([30.0, 30.0], abs=0.06)
        assert all((x, y) == (90.0, 100.0) for _, x, y in xy.moves)
        assert c.summary is not None and c.summary.reason == "error" and "not reached after 3 moves" in str(
            c.summary.error)
        assert any(e.topic == "thermal.error" for e in events)
    finally:
        c.close()
        pyro.shutdown()


def test_a_move_reached_after_being_issued_again() -> None:
    clock = FakeTime()
    xy = StuckXY(clock, reach_after=2)
    c, pyro, _ = _stuck_controller(clock, xy)
    try:
        c.start()
        assert c.wait(30)
        assert len(xy.moves) == 2 and c.summary is not None and c.summary.completed
    finally:
        c.close()
        pyro.shutdown()


def test_a_refused_move_ends_the_measurement_with_its_error(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    c = rig.controller
    rig.xy.set_wafer(diameter_mm=100.0, origin=ORIGIN, move_radius_mm=50.0)
    hook(rig, lambda e: rig.xy.set_wafer(move_radius_mm=1.0) if e.topic == "thermal.point" else None)
    c.start()
    assert c.wait(30)
    assert c.summary is not None and c.summary.reason == "error" and c.summary.measured == 1
    error = rig.of("thermal.error")[-1]
    assert error.data["code"] == 9404 and error.data["operation"] == "measurement"


def test_measurement_with_the_bandit(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(bandit=True)
    c = rig.controller
    assert rig.server is not None
    assert c.select_tool(Tool.BANDIT).result(10) is True
    assert c.tool == Tool.BANDIT and c.connected and rig.pyro.state == "closed"
    c.start()
    assert c.wait(30)
    assert [r.temperature for r in c.table.results] == pytest.approx([400.0] * 5)
    assert {req[2] for req in rig.server.requests} == {12298} and rig.server.connections == 1
    assert c.select_tool(Tool.PYROMETER).result(10) is True and c.connected


def test_the_bandit_needs_enable_bandit(make_rig: Callable[..., Rig]) -> None:
    settings = ThermalSettings(diameters=(10.0,), nb_points=(2,), origin=ORIGIN, bandit_enabled=False)
    rig = make_rig(settings, bandit=True)
    with pytest.raises(ThermalError, match="enableBandit"):
        rig.controller.select_tool(Tool.BANDIT)
    rig2 = make_rig()
    with pytest.raises(ThermalError, match="no bandit"):
        rig2.controller.select_tool(Tool.BANDIT)


def test_settings_recompute_the_points_and_are_refused_while_measuring(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    c = rig.controller
    c.set_circles([0.0, 20.0, 40.0], [1, 4, 8])
    assert len(c.points) == 13 and rig.of("thermal.points")[-1].data == {"count": 13, "circles": 3}
    c.set_offset_angle(5)
    assert c.points[0].theta == 5.0
    c.set_samples(2)
    c.set_wait_ms(0)
    c.set_script_command("py plot.py --path ")
    assert c.settings.samples == 2 and c.settings.script_command == "py plot.py --path "
    with pytest.raises(ThermalError):
        c.set_samples(0)
    with pytest.raises(ThermalError):
        c.set_circles([1.0] * 10, [1] * 10)
    refused: list[BaseException] = []

    def on_event(event: Event) -> None:
        if event.topic == "thermal.point" and not refused:
            for action in (lambda: c.set_circles([10.0], [1]), lambda: c.move_to(x=1.0), c.refresh,
                           c.start, lambda: c.select_tool(Tool.PYROMETER)):
                try:
                    action()
                except ThermalError as exc:
                    refused.append(exc)
    hook(rig, on_event)
    c.start()
    assert c.wait(30)
    assert len(refused) == 5 and all(isinstance(e, ThermalBusy) for e in refused)


def test_moves_origin_and_positions(make_rig: Callable[..., Rig]) -> None:
    store = Properties.parse("[hardware.xy]\nchuckxorg_DBL = 100,0\nchuckyorg_DBL = 100,0\n")
    rig = make_rig(store=store)
    c = rig.controller
    stage = c.move_to(x=5.0).result(10)
    assert stage[0] == pytest.approx(95.0)
    stage = c.move_to(y=-2.0).result(10)
    assert stage == pytest.approx((95.0, 102.0))
    positions = c.read_positions().result(10)
    assert (positions.chuck_x, positions.chuck_y) == pytest.approx((5.0, -2.0))
    assert c.move_z(3.0).result(10) == pytest.approx(3.0)
    assert c.read_positions().result(10).z == pytest.approx(3.0)
    c.set_joystick(True).result(10)
    assert rig.xy.joystick_enabled() and rig.z.joystick_enabled()
    c.set_joystick(False).result(10)
    with pytest.raises(ThermalError):
        c.move_to()
    origin = c.set_origin().result(10)
    assert origin == pytest.approx((95.0, 102.0)) and c.settings.origin == pytest.approx((95.0, 102.0))
    assert store.get("hardware.xy.chuckxorg") == pytest.approx(95.0)
    assert rig.xy.wafer.chuck_origin == pytest.approx((95.0, 102.0))
    assert rig.of("thermal.origin")[-1].data == pytest.approx({"x": 95.0, "y": 102.0})
    assert c.read_positions().result(10).chuck_x == pytest.approx(0.0)


def test_pyrometer_settings_through_the_controller(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    c = rig.controller
    assert c.set_emissivity(0.8).result(10) == pytest.approx(0.8)
    assert c.get_emissivity().result(10) == pytest.approx(0.8)
    assert c.set_transmissivity(0.9).result(10) == pytest.approx(0.9)
    assert c.get_transmissivity().result(10) == pytest.approx(0.9)
    c.set_laser(True).result(10)
    assert rig.emulator.laser is True
    assert rig.of("thermal.laser")[-1].data == {"on": True}
    with pytest.raises(Exception, match="not in"):
        c.set_emissivity(5.0).result(10)
    assert rig.of("thermal.error")[-1].data["operation"] == "set emissivity"


def test_idle_refresh(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    c = rig.controller
    rig.emulator.object_temperature = 123.4
    c.refresh_live()
    assert rig.of("thermal.temperature")[-1].data == {"tool": "PYROMETER", "value": pytest.approx(123.4)}
    assert rig.of("thermal.position")[-1].data["chuck_x"] is not None
    rig.emulator.link_broken = True
    c.refresh_live()
    assert not c.connected and rig.of("thermal.tool")[-1].data["connected"] is False


def test_the_refresh_thread_runs_on_its_own(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(open_tool=False, options=MeasureOptions(live_period_s=0.01))
    assert rig.controller.open(live=True).result(10)
    deadline = time.monotonic() + 5
    while len(rig.of("thermal.position")) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(rig.of("thermal.position")) >= 2


def test_export_csv_and_the_3d_script(make_rig: Callable[..., Rig], tmp_path: Path,
                                      monkeypatch: pytest.MonkeyPatch) -> None:
    rig = make_rig()
    c = rig.controller
    c.start()
    assert c.wait(30)
    launched: list[tuple[str, Any]] = []
    monkeypatch.setattr(plot_module, "launch_plot_script", lambda command, path: launched.append((command, path)))
    path = c.export_csv(tmp_path / c.default_csv_name())
    assert path.name == "Thermal_measurement_261126_140509.csv" and launched == []
    lines = path.read_bytes().decode("cp1252").split("\r\n")
    assert lines[1].startswith("Point 1\t0.00\t0.00\t0.00\t0.00\t300.00")
    assert rig.of("thermal.exported")[-1].data == {"path": str(path)}
    c.set_script_command('py "readdata_arg.py" --path ')
    c.export_csv(tmp_path / "b.csv")
    assert launched == [('py "readdata_arg.py" --path ', tmp_path / "b.csv")]

    def fail(command: str, path: Any) -> None:
        raise OSError("py not found")
    monkeypatch.setattr(plot_module, "launch_plot_script", fail)
    assert c.export_csv(tmp_path / "c.csv").exists()
    assert rig.of("thermal.error")[-1].data["operation"] == "3D map script"


def test_close_writes_the_settings_back_and_closes_the_probes(make_rig: Callable[..., Rig]) -> None:
    store = Properties.parse("[hardware.bandit]\ndiam1_DBL = 0,0\nnbpoints1_U32 = 1\n")
    rig = make_rig(store=store)
    c = rig.controller
    c.set_circles([0.0, 30.0], [1, 6])
    written = c.close()
    assert "hardware.bandit.diam2" in written and store.get("hardware.bandit.diam2") == 30.0
    assert store.get("hardware.bandit.nbpoints2") == 6 and store.get("hardware.bandit.nbpoints9") == 0
    assert store.suffix_of("hardware.bandit.nbpoints1") == "U32"
    assert c.state == ThermalState.CLOSED and not c.is_open and rig.pyro.state == "closed"
    assert c.close() == []
    with pytest.raises(ThermalError, match="not open"):
        c.refresh()


def test_close_during_a_measurement_stops_it(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    c = rig.controller
    started = threading.Event()
    hook(rig, lambda e: started.set() if e.topic == "thermal.point" else None)
    c.set_circles([0.0, 20.0, 40.0], [1, 8, 16])
    c.start()
    started.wait(10)
    c.close(timeout=10)
    assert c.summary is not None and c.summary.reason == "stopped" and c.state == ThermalState.CLOSED


def test_the_controller_module_exports() -> None:
    assert controller_module.STEP_PROGRESS[Step.WRITING] == 100
    assert SMALL.origin == ORIGIN


class SavingStore(dict):
    """A minimal settings store: ``set`` and ``save``."""

    saved = 0

    def set(self, name: str, value: Any) -> None:
        self[name] = value

    def save(self) -> None:
        self.saved += 1


def test_close_with_save_then_open_again(make_rig: Callable[..., Rig]) -> None:
    store = SavingStore()
    rig = make_rig(store=store)
    c = rig.controller
    c.close(save=True)
    assert store.saved == 1 and store["hardware.bandit.nbpoints2"] == 4 and store["hardware.bandit.diam9"] == 0.0
    assert c.open(live=False).result(10) is True and c.state == ThermalState.IDLE
    c.start()
    assert c.wait(30) and c.summary is not None and c.summary.completed
    with pytest.raises(ThermalError, match="already open"):
        c.open()
