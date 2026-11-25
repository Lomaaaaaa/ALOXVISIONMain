"""The XY stages device (``alox.devices.xy``) on two simulated C-663 controllers."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from alox.config import TypedIni
from alox.devices.base import DeviceError, DeviceState
from alox.devices.fake_chain import FakeDaisyChainServer
from alox.devices.gcs_chain import GcsChain
from alox.devices.xy import (
    OUTSIDE_WAFER,
    STOPPED,
    WaferSettings,
    XYAxisSettings,
    XYSettings,
    XYStage,
    build_xy_backends,
    load_xy_settings,
)
from alox.events import Event
from aloxcam.errors import Code, StageError
from aloxcam.settings import SimulationOptions, StageOptions
from aloxcam.zstage.sim import SimStage

SIM = SimulationOptions(stage_min_mm=0.0, stage_max_mm=200.0, stage_reference_s=0.01)


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


@pytest.fixture
def events() -> list[Event]:
    return []


@pytest.fixture
def make_xy(events: list[Event]) -> Iterator[Callable[..., tuple[XYStage, SimStage, SimStage]]]:
    devices: list[XYStage] = []

    def factory(*, wafer: WaferSettings | None = None, fake_time: bool = True, **kwargs: Any
                ) -> tuple[XYStage, SimStage, SimStage]:
        clock = FakeTime() if fake_time else None
        sims = [SimStage(SIM, clock=clock.monotonic if clock else time.monotonic, dh_running=False) for _ in "xy"]
        if clock is not None:
            kwargs.update(sleep=clock.sleep, monotonic=clock.monotonic)
        kwargs.setdefault("supervise", False)
        xy = XYStage(sims[0], sims[1], XYSettings(wafer=wafer or WaferSettings()), publish=events.append,
                     reconnect_delay_s=0.0, **kwargs)
        devices.append(xy)
        xy.open(timeout=10)
        return xy, sims[0], sims[1]

    yield factory
    for xy in devices:
        xy.shutdown(timeout=5)


def topics(events: list[Event]) -> list[str]:
    return [e.topic for e in events]


WAFER = WaferSettings(diameter_mm=100.0, origin=(100.0, 100.0))


# ============================================================================================== open and moves
def test_open_sets_up_and_references_both_controllers(make_xy, events):
    xy, sx, sy = make_xy()
    assert xy.state == DeviceState.READY
    assert sx.referenced and sy.referenced
    assert all("C-663-SIM" in identity for identity in xy.identities)
    assert "device.xy.opened" in topics(events)


def test_move_absolute_both_and_one_axis(make_xy):
    xy, _, _ = make_xy()
    assert xy.move_absolute(50.0, 60.0) == pytest.approx((50.0, 60.0))
    assert xy.move_absolute(y=70.0) == pytest.approx((50.0, 70.0))
    assert xy.position() == pytest.approx((50.0, 70.0))


def test_move_relative(make_xy):
    xy, _, _ = make_xy()
    xy.move_absolute(10.0, 10.0)
    assert xy.move_relative(dx=5.0, dy=-2.5) == pytest.approx((15.0, 7.5))


def test_move_without_wait(make_xy):
    xy, _, _ = make_xy()
    assert xy.move_absolute(20.0, 30.0, wait=False) == (20.0, 30.0)
    xy.wait_end_move()
    assert xy.position() == pytest.approx((20.0, 30.0))


# ============================================================================================== wafer checks
def test_destination_beyond_the_move_radius_is_refused(make_xy):
    """LabVIEW refuses a destination more than 80 mm from the wafer origin; the diameter only enables the check."""
    xy, sx, sy = make_xy(wafer=WAFER)
    xy.move_absolute(100.0, 100.0)
    assert xy.move_absolute(160.0, 100.0) == pytest.approx((160.0, 100.0))      # 60 mm: beyond the radius, accepted
    with pytest.raises(DeviceError) as info:
        xy.move_absolute(190.0, 100.0)                                          # 90 mm > 80 mm
    assert info.value.code == OUTSIDE_WAFER
    assert (sx.position, sy.position) == pytest.approx((160.0, 100.0))
    with pytest.raises(DeviceError) as info:
        xy.move_relative(dx=25.0)                                               # 185, 100
    assert info.value.code == OUTSIDE_WAFER
    xy.set_wafer(move_radius_mm=95.0)
    assert xy.move_relative(dx=25.0) == pytest.approx((185.0, 100.0))


def test_undefined_wafer_accepts_every_destination(make_xy):
    xy, _, _ = make_xy(wafer=WaferSettings(diameter_mm=100.0, origin=(0.0, 0.0)))
    assert xy.move_absolute(190.0, 5.0) == pytest.approx((190.0, 5.0))


def test_non_finite_destination_is_refused(make_xy):
    xy, _, _ = make_xy()
    with pytest.raises(DeviceError) as info:
        xy.move_absolute(float("nan"), 1.0)
    assert info.value.code == int(Code.LV_INVALID_INPUT)


def test_wafer_settings_change_in_memory(make_xy):
    xy, _, _ = make_xy(wafer=WAFER)
    xy.set_wafer(diameter_mm=300.0)
    assert xy.inside_wafer(200.0, 100.0)
    with pytest.raises(DeviceError):
        xy.set_wafer(check_period_ms=0)


def test_wafer_limit_check_stops_joystick_motion_outside_the_wafer(make_xy, events):
    xy, sx, _ = make_xy(wafer=WAFER)
    xy.move_absolute(100.0, 100.0)
    assert xy.call("check", xy.check_wafer_limits) is True
    sx.mov("1", 180.0)                                    # X driven off the wafer behind the device's back
    xy.wait_end_move()
    assert xy.call("check", xy.check_wafer_limits) is False                     # outside, joystick off, still
    assert "device.xy.wafer_limit" not in topics(events)
    xy.set_joystick(True)
    assert xy.call("check", xy.check_wafer_limits) is False                     # joystick on: stop
    assert xy.call("check", xy.check_wafer_limits) is False
    limit = [e for e in events if e.topic == "device.xy.wafer_limit"]
    assert len(limit) == 1 and limit[0].data["x"] > 150.0 and limit[0].data["joystick"] is True
    assert xy.joystick_enabled()                          # LabVIEW leaves the joystick on


def test_wafer_limit_can_also_switch_the_joystick_off(make_xy):
    xy, sx, _ = make_xy(wafer=WAFER, disable_joystick_on_limit=True)
    xy.move_absolute(100.0, 100.0)
    sx.mov("1", 180.0)
    xy.wait_end_move()
    xy.set_joystick(True)
    assert xy.call("check", xy.check_wafer_limits) is False
    assert not xy.joystick_enabled()


def test_a_commanded_move_back_onto_the_wafer_is_not_stopped(make_xy):
    """LabVIEW stopped it too, so the stage stayed stuck outside (xy-stage.md X3)."""
    xy, _, _ = make_xy(wafer=WAFER)
    xy.move_absolute(160.0, 100.0)                         # accepted (≤ 80 mm) but outside the 50 mm zone
    xy.move_absolute(110.0, 100.0, wait=False)
    assert xy.call("check", xy.check_wafer_limits) is False                     # still outside, moving: left alone
    xy.wait_end_move()
    assert xy.position() == pytest.approx((110.0, 100.0))


def test_wafer_limit_supervision_runs_in_the_background(make_xy, events):
    wafer = WaferSettings(diameter_mm=100.0, origin=(100.0, 100.0), limits_enabled=True, check_period_ms=20)
    xy, sx, _ = make_xy(wafer=wafer, fake_time=False, supervise=True)
    xy.set_speed(200.0)
    xy.move_absolute(100.0, 100.0)
    sx.mov("1", 190.0)                                    # leaves the wafer after about 0.25 s
    deadline = time.monotonic() + 3.0
    while "device.xy.wafer_limit" not in topics(events) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert "device.xy.wafer_limit" in topics(events)


# ============================================================================================== reference
def test_lost_reference_rehomes_both_axes_before_a_move(make_xy, events):
    xy, sx, sy = make_xy()
    xy.move_absolute(30.0, 30.0)
    sy.power_cycle()                                      # Y loses its reference (servo off)
    assert not xy.is_referenced()
    assert xy.move_absolute(40.0, 40.0) == pytest.approx((40.0, 40.0))
    rehomed = [e for e in events if e.topic == "device.xy.rehomed"]
    assert rehomed and rehomed[0].data == {"operation": "move absolute", "success": True}
    assert sx.referenced and sy.referenced


def test_do_reference(make_xy, events):
    xy, sx, sy = make_xy()
    xy.reference()
    assert sx.referenced and sy.referenced
    assert events[-1].topic == "device.xy.referenced"


# ============================================================================================== errors, stop
def test_link_failure_puts_the_device_in_fault_then_reconnects(make_xy, events):
    xy, sx, _ = make_xy()
    sx.inject_failure("qpos", StageError(int(Code.PI_TIMEOUT), "POS?: timeout"))
    with pytest.raises(DeviceError):
        xy.position()
    assert xy.state == DeviceState.FAULT
    assert xy.position() == pytest.approx((0.0, 0.0))
    assert "device.xy.reconnected" in topics(events)


def test_stop_interrupts_a_running_move(make_xy, events):
    xy, _, _ = make_xy(fake_time=False)
    xy.set_speed(5.0)
    result: dict[str, Any] = {}

    def move() -> None:
        try:
            result["value"] = xy.move_absolute(150.0, 150.0, timeout=20)
        except DeviceError as exc:
            result["error"] = exc

    worker = threading.Thread(target=move)
    worker.start()
    time.sleep(0.2)
    xy.stop()
    worker.join(10)
    assert not worker.is_alive()
    assert result["error"].code == STOPPED
    x, y = xy.position()
    assert x < 100.0 and y < 100.0
    assert "device.xy.stopped" in topics(events)
    assert xy.read_error("x") == (0, "") and xy.read_error("y") == (0, "")   # stop cleared error 10


def test_speed_joystick_status(make_xy):
    xy, _, _ = make_xy(wafer=WAFER)
    xy.set_speed(2.0, axis="y")
    assert xy.speed("y") == pytest.approx(2.0)
    with pytest.raises(DeviceError):
        xy.speed("z")
    xy.set_joystick(True)
    assert xy.joystick_enabled()
    assert xy.joystick_button() is False                  # simulation: no raw query
    with pytest.raises(DeviceError) as info:              # the controller refuses MOV while the joystick drives it
        xy.move_absolute(100.0, 100.0)
    assert info.value.code == 51
    xy.set_joystick(False)
    xy.move_absolute(100.0, 100.0)
    status = xy.status()
    assert status.state == "ready" and (status.x, status.y) == pytest.approx((100.0, 100.0))
    assert status.referenced is True and status.inside_wafer is True


# ============================================================================================== settings
def test_load_xy_settings_from_alox_ini():
    ini = TypedIni.parse(
        "[general]\nwafersize_DBL = 150,000000\n"
        "[hardware.xy]\ncomport_I32 = 5\nbaudrate_I32 = 2\nxaxis_I32 = 1\nyaxis_I32 = 2\nspeed_DBL = 12,500000\n"
        "waferxorg_DBL = 101,000000\nwaferyorg_DBL = 99,000000\nwaferlimit_BOOL = TRUE\nwafercheckfreq_I32 = 250\n")
    s = load_xy_settings(ini)
    assert (s.x.com, s.x.baudrate, s.x.device, s.y.device) == (5, 38400, 1, 2)
    assert s.x.speed == pytest.approx(12.5) and s.y.speed == pytest.approx(12.5)
    assert s.wafer.diameter_mm == pytest.approx(150.0) and s.wafer.origin == pytest.approx((101.0, 99.0))
    assert s.wafer.limits_enabled and s.wafer.check_period_ms == 250


def test_load_xy_settings_defaults_and_bad_baud_index():
    warnings: list[str] = []
    s = load_xy_settings(TypedIni.parse("[hardware.xy]\nbaudrate_I32 = 9\n"), warn=warnings.append)
    assert s.x.baudrate == 9600 and warnings
    assert (s.x.device, s.y.device, s.wafer.diameter_mm, s.wafer.limits_enabled) == (1, 2, 100.0, False)


# ============================================================================================== real driver
def test_xy_on_the_real_driver_through_the_daisy_chain(events):
    """Two ``PiSerialStage`` sharing one emulated RS-232 daisy chain (``GcsChain`` + ``FakeDaisyChainServer``)."""
    pytest.importorskip("serial")
    options = SimulationOptions(stage_min_mm=0.0, stage_max_mm=200.0, stage_reference_s=0.01)
    server = FakeDaisyChainServer(options=options, dh_running=False).start()
    settings = XYSettings(x=XYAxisSettings(device=1, speed=100.0), y=XYAxisSettings(device=2, speed=100.0),
                          wafer=WaferSettings(diameter_mm=100.0, origin=(100.0, 100.0)))
    xy = XYStage(*build_xy_backends(simulate=False, chain=GcsChain(server.url, 115200, 5.0)), settings,
                 StageOptions(always_reference=False, port_map={"COM2": server.url}), publish=events.append,
                 supervise=False)
    try:
        xy.open(timeout=20)
        assert xy.is_referenced()
        assert xy.move_absolute(100.0, 110.0, timeout=20) == pytest.approx((100.0, 110.0), abs=1e-3)
        with pytest.raises(DeviceError) as info:
            xy.move_absolute(190.0, 100.0, timeout=20)                         # 90 mm > 80 mm
        assert info.value.code == OUTSIDE_WAFER
    finally:
        xy.shutdown(timeout=5)
        server.close()
