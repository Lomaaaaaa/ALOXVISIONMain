"""The Z stage device of the integrated application (``alox.devices.zstage``).

Most tests run ``SimStage`` on a fake clock (instant moves); the stop test uses real time, and one test drives the real
``PiSerialStage`` against the ``FakeC663Server`` (GCS over a loopback socket).
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from alox.devices.base import DeviceError, DeviceNotReady, DeviceState
from alox.devices.zstage import ZStage
from alox.events import Event
from aloxcam.errors import Code, StageError
from aloxcam.settings import SimulationOptions, StageOptions, ZStageSettings
from aloxcam.zstage.fake_c663 import FakeC663Server
from aloxcam.zstage.sim import SimStage


class FakeTime:
    """``sleep`` advances the clock instantly."""

    def __init__(self) -> None:
        self.now = 1000.0
        self._lock = threading.Lock()

    def monotonic(self) -> float:
        with self._lock:
            return self.now

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self.now += max(0.0, seconds)


@pytest.fixture
def events() -> list[Event]:
    return []


@pytest.fixture
def make_z(events: list[Event]) -> Iterator[Callable[..., tuple[ZStage, SimStage]]]:
    devices: list[ZStage] = []

    def factory(*, settings: ZStageSettings | None = None, sim: dict[str, Any] | None = None, open_: bool = True,
                **kwargs: Any) -> tuple[ZStage, SimStage]:
        clock = FakeTime()
        backend = SimStage(SimulationOptions(**(sim or {})), clock=clock.monotonic, dh_running=False)
        kwargs.setdefault("options", StageOptions())
        z = ZStage(backend, settings or ZStageSettings(min_pos=0.0, max_pos=20.0), publish=events.append,
                   sleep=clock.sleep, monotonic=clock.monotonic, reconnect_delay_s=0.0, **kwargs)
        devices.append(z)
        if open_:
            z.open(timeout=10)
        return z, backend

    yield factory
    for z in devices:
        z.shutdown(timeout=5)


def topics(events: list[Event]) -> list[str]:
    return [e.topic for e in events]


# ============================================================================================== open / close
def test_open_sets_up_the_controller(make_z, events):
    z, sim = make_z()
    assert z.state == DeviceState.READY
    assert z.serial_number == "0000000001"
    assert "C-663-SIM" in z.identity
    assert sim.referenced                                   # always_reference: FRF at set-up
    assert "device.z.opened" in topics(events)
    assert [e.data["state"] for e in events if e.topic == "device.z.state"] == ["opening", "ready"]


def test_open_failure_puts_the_device_in_fault(make_z):
    z, sim = make_z(open_=False)
    sim.inject_failure("connect_rs232", StageError(int(Code.PI_OPEN_PORT), "cannot open COM2"))
    with pytest.raises(DeviceError) as info:
        z.open(timeout=10)
    assert info.value.code == int(Code.PI_OPEN_PORT)
    assert z.state == DeviceState.FAULT


def test_close_then_call_is_refused(make_z):
    z, _ = make_z()
    z.close(timeout=5)
    assert z.state == DeviceState.CLOSED
    with pytest.raises(DeviceNotReady):
        z.position(timeout=5)


# ============================================================================================== moves
def test_move_absolute_waits_and_returns_the_position(make_z, events):
    z, sim = make_z()
    assert z.move_absolute(12.5) == pytest.approx(12.5)
    assert sim.position == pytest.approx(12.5)
    assert events[-1].topic == "device.z.position"
    assert events[-1].data["position"] == pytest.approx(12.5)


def test_move_relative(make_z):
    z, _ = make_z()
    z.move_absolute(5.0)
    assert z.move_relative(2.5) == pytest.approx(7.5)


def test_move_without_wait_returns_the_target(make_z):
    z, _ = make_z()
    assert z.move_absolute(3.0, wait=False) == 3.0
    z.wait_end_move()
    assert z.position() == pytest.approx(3.0)


@pytest.mark.parametrize("target", [-0.1, 20.1, math.nan])
def test_move_outside_the_software_range_is_refused(make_z, target):
    z, sim = make_z()
    with pytest.raises(DeviceError) as info:
        z.move_absolute(target)
    assert info.value.code == int(Code.Z_OUT_OF_RANGE)
    assert sim.position == pytest.approx(0.0)


def test_relative_move_range_uses_the_current_position(make_z):
    z, _ = make_z()
    z.move_absolute(19.0)
    with pytest.raises(DeviceError) as info:
        z.move_relative(2.0)
    assert info.value.code == int(Code.Z_OUT_OF_RANGE)


def test_limits_can_be_changed_in_memory(make_z):
    z, _ = make_z()
    z.set_limits(min_mm=2.0, max_mm=4.0)
    assert z.limits == (2.0, 4.0)
    with pytest.raises(DeviceError):
        z.move_absolute(5.0)
    with pytest.raises(DeviceError) as info:
        z.set_limits(max_mm=math.inf)
    assert info.value.code == int(Code.LV_INVALID_INPUT)
    assert z.limits == (2.0, 4.0)


def test_refused_move_is_reported_at_once(make_z):
    """MOV refused by the controller (error latched): reported by the move itself, not at a later ERR? (plan §6)."""
    z, sim = make_z()
    sim.inject_failure("mov", StageError(int(Code.PI_NOT_CONNECTED), "MOV: not connected"))
    with pytest.raises(DeviceError):
        z.move_absolute(1.0)


# ============================================================================================== reference
def test_unreferenced_axis_is_rehomed_before_a_move(make_z, events):
    z, sim = make_z()
    sim.power_cycle()                                       # axis loses its reference
    assert not z.is_referenced()
    assert z.move_absolute(4.0) == pytest.approx(4.0)
    rehomed = [e for e in events if e.topic == "device.z.rehomed"]
    assert rehomed and rehomed[0].data == {"operation": "move absolute", "success": True}
    assert sim.referenced


def test_without_implicit_reinit_an_unreferenced_axis_refuses_moves(make_z, events):
    z, sim = make_z(implicit_reinit=False)
    sim.power_cycle()
    with pytest.raises(DeviceError) as info:
        z.move_absolute(4.0)
    assert info.value.code == int(Code.GCS_UNREFERENCED)
    assert "device.z.rehomed" not in topics(events)


def test_reference_method(make_z, events):
    z, sim = make_z()
    z.move_absolute(3.0)
    z.reference("FRF")
    assert sim.referenced
    assert events[-1].topic == "device.z.referenced"


def test_after_a_power_cycle_only_reinitialize_can_reference(make_z):
    """Servo off after a controller reset: FRF is refused (GCS 5) as on the real controller; the set-up switches the
    servo on and references."""
    z, sim = make_z()
    sim.power_cycle()
    with pytest.raises(DeviceError) as info:
        z.reference("FRF")
    assert info.value.code == int(Code.GCS_UNREFERENCED)
    z.reinitialize()
    assert sim.referenced and z.state == DeviceState.READY


# ============================================================================================== errors and recovery
def test_link_failure_then_automatic_reconnection(make_z, events):
    z, sim = make_z()
    sim.inject_failure("qpos", StageError(int(Code.PI_TIMEOUT), "POS?: timeout"))
    with pytest.raises(DeviceError) as info:
        z.position()
    assert info.value.code == int(Code.PI_TIMEOUT)
    assert z.state == DeviceState.FAULT
    assert z.position() == pytest.approx(0.0)              # the next call re-opens the link first
    assert z.state == DeviceState.READY
    assert "device.z.reconnecting" in topics(events)
    assert "device.z.reconnected" in topics(events)


def test_controller_error_does_not_fault_the_device(make_z):
    z, sim = make_z()
    sim.latch_error(10)
    code, text = z.read_error()
    assert code == 10 and text
    assert z.read_error() == (0, "")
    assert z.state == DeviceState.READY


def test_speed_and_joystick(make_z):
    z, _ = make_z()
    z.set_speed(2.0)
    assert z.speed() == pytest.approx(2.0)
    z.set_joystick(True)
    assert z.joystick_enabled()
    z.set_joystick(False)
    assert not z.joystick_enabled()


def test_status(make_z):
    z, _ = make_z()
    z.move_absolute(1.5)
    status = z.status()
    assert status.state == "ready" and status.position == pytest.approx(1.5)
    assert status.referenced is True and status.moving is False
    z.close(timeout=5)
    closed = z.status()
    assert closed.state == "closed" and closed.position is None


# ============================================================================================== stop (real time)
def test_stop_interrupts_a_running_move(events):
    backend = SimStage(SimulationOptions(stage_reference_s=0.01), dh_running=False)
    z = ZStage(backend, ZStageSettings(min_pos=0.0, max_pos=20.0, speed=1.0), StageOptions(), publish=events.append)
    try:
        z.open(timeout=10)
        z.move_absolute(0.0)
        result: dict[str, Any] = {}

        def move() -> None:
            try:
                result["value"] = z.move_absolute(10.0, timeout=10)  # 10 mm at 1 mm/s
            except DeviceError as exc:
                result["error"] = exc

        worker = threading.Thread(target=move)
        worker.start()
        time.sleep(0.2)
        z.stop()
        worker.join(5)
        assert not worker.is_alive()
        assert "error" in result                           # STP latches error 10: the move reports it
        assert result["error"].code == 10
        assert z.position() < 2.0
        assert "device.z.stopped" in topics(events)
    finally:
        z.shutdown(timeout=5)


# ============================================================================================== real driver
def test_real_driver_against_the_fake_controller(events):
    """``PiSerialStage`` (GCS over pyserial) to ``FakeC663Server``: the device works with the real driver code."""
    pytest.importorskip("serial")
    from aloxcam.zstage.pi_serial import PiSerialStage

    with FakeC663Server(SimulationOptions(stage_reference_s=0.01), dh_running=False) as server:
        server.start()
        z = ZStage(PiSerialStage(), ZStageSettings(com=2, speed=20.0),
                   StageOptions(port_map={"COM2": server.url}), publish=events.append)
        try:
            z.open(timeout=20)
            assert z.state == DeviceState.READY
            assert z.move_absolute(1.0, timeout=20) == pytest.approx(1.0, abs=1e-3)
            assert z.is_referenced()
        finally:
            z.shutdown(timeout=5)
