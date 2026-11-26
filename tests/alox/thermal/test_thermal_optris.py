"""The Optris protocol (``alox.thermal.optris``) against its emulator, and the pyrometer device."""

from __future__ import annotations

import math
import threading
from collections.abc import Iterator

import pytest

from alox.devices.base import DeviceError, DeviceState
from alox.events import Event
from alox.thermal.errors import (
    INVALID_INPUT,
    NOT_OPEN,
    OPEN_FAILED,
    RECEIVE_FAILED,
    TIMEOUT,
    ProbeError,
)
from alox.thermal.optris import (
    OptrisCt,
    OptrisPyrometer,
    PyroSettings,
    build_pyrometer_backend,
    checksum,
    decode_factor,
    decode_temperature,
    encode_factor,
    load_pyro_settings,
)
from alox.thermal.optris_sim import (
    OptrisEmulator,
    OptrisSimOptions,
    build_sim_optris,
    encode_temperature,
)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self._lock = threading.Lock()

    def monotonic(self) -> float:
        with self._lock:
            return self.now

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self.now += max(0.0, seconds)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def ct(clock: Clock) -> Iterator[tuple[OptrisCt, OptrisEmulator]]:
    backend, emulator = build_sim_optris(sleep=clock.sleep, monotonic=clock.monotonic)
    backend.open("COM7", 115200, 0.5)
    yield backend, emulator
    backend.close()


def tx(backend: OptrisCt) -> list[bytes]:
    return [data for _, d, data in backend.frames if d == "tx"]


def test_helpers() -> None:
    assert checksum(b"") == 0 and checksum(b"\x01") == 1 and checksum(bytes((0x84, 0x03, 0xB6))) == 0x84 ^ 0x03 ^ 0xB6
    assert decode_temperature(bytes((0x0F, 0xA0))) == pytest.approx(300.0)        # 4000 → (4000 − 1000)/10
    assert decode_temperature(bytes((0x03, 0xE8))) == 0.0
    assert decode_factor(bytes((0x03, 0xB6))) == pytest.approx(0.95)
    assert encode_factor(0.95) == bytes((0x03, 0xB6)) and encode_temperature(-12.5) == bytes((0x03, 0x6B))


def test_commands_are_framed_as_labview_sends_them(ct: tuple[OptrisCt, OptrisEmulator]) -> None:
    backend, emulator = ct
    emulator.object_temperature = 412.3
    assert backend.object_temperature() == pytest.approx(412.3)
    assert backend.head_temperature() == pytest.approx(30.0)
    assert backend.box_temperature() == pytest.approx(28.0)
    assert backend.actual_temperature() == pytest.approx(412.3)
    assert backend.emissivity() == pytest.approx(0.95)
    assert backend.transmissivity() == pytest.approx(1.0)
    assert backend.serial() == "1234567"
    assert backend.firmware() == "FW rev 2001"
    assert backend.sensor_info() == "Model: LT, Low Temp: 0, High Temp: 975"
    assert backend.laser() is False
    assert tx(backend) == [b"\x01\x01", b"\x02\x02", b"\x03\x03", b"\x81\x81", b"\x04", b"\x05", b"\x0e", b"\x0f",
                           b"\x45", b"\x25"]


def test_set_commands_and_their_unread_answers(ct: tuple[OptrisCt, OptrisEmulator]) -> None:
    backend, emulator = ct
    backend.set_emissivity(0.874)
    assert emulator.emissivity == pytest.approx(0.874)
    assert tx(backend)[-1] == bytes((0x84, 0x03, 0x6A, 0x84 ^ 0x03 ^ 0x6A))
    assert backend.emissivity() == pytest.approx(0.874)                     # the echo of the set was dropped
    assert [d for _, d, _ in backend.frames][-3:] == ["drop", "tx", "rx"]
    backend.set_transmissivity(0.5)
    backend.set_laser(True)
    assert emulator.transmissivity == 0.5 and emulator.laser is True
    assert tx(backend)[-1] == bytes((0xA5, 0x01, 0xA4))
    with pytest.raises(ProbeError) as info:
        backend.set_emissivity(1.5)
    assert info.value.code == INVALID_INPUT
    with pytest.raises(ProbeError):
        backend.set_transmissivity(math.nan)


def test_timeout_garbage_and_closed_port(ct: tuple[OptrisCt, OptrisEmulator], clock: Clock) -> None:
    backend, emulator = ct
    emulator.drop_next_answer()
    t0 = clock.now
    with pytest.raises(ProbeError) as info:
        backend.object_temperature()
    assert info.value.code == TIMEOUT and info.value.link_failure and clock.now - t0 == pytest.approx(0.5)
    emulator.inject(b"\x99\x99\x99")                                         # unrequested bytes: flushed first
    emulator.object_temperature = 25.0
    assert backend.object_temperature() == 25.0
    emulator.link_broken = True
    with pytest.raises(ProbeError) as info:
        backend.object_temperature()
    assert info.value.code in (RECEIVE_FAILED, -2)
    backend.close()
    with pytest.raises(ProbeError) as info:
        backend.emissivity()
    assert info.value.code == NOT_OPEN


def test_a_pyrometer_without_checksum_mode_still_answers_correctly(clock: Clock) -> None:
    backend, emulator = build_sim_optris(OptrisSimOptions(expects_checksum=False), sleep=clock.sleep,
                                         monotonic=clock.monotonic)
    backend.open("COM1", 115200, 0.5)
    emulator.object_temperature = 50.0
    assert backend.object_temperature() == 50.0                             # the checksum 0x01 = a second read
    emulator.object_temperature = 60.0
    assert backend.object_temperature() == 60.0                             # its answer was flushed


def test_open_failure_and_settings(clock: Clock) -> None:
    backend, emulator = build_sim_optris(sleep=clock.sleep, monotonic=clock.monotonic)
    emulator.port_missing = True
    with pytest.raises(ProbeError) as info:
        backend.open("7")
    assert info.value.code == OPEN_FAILED
    assert PyroSettings(com=7).port_name == "COM7" and PyroSettings(port="socket://x:1").port_name == "socket://x:1"
    ini = {"hardware.pyro.comport": 7, "hardware.pyro.baudrate": 115200}
    settings = load_pyro_settings(ini)
    assert (settings.com, settings.baudrate, settings.timeout_ms) == (7, 115200, 1000)
    warnings: list[str] = []
    assert load_pyro_settings({"hardware.pyro.timeout": 0}, warn=warnings.append).timeout_ms == 1000 and warnings
    sim = build_pyrometer_backend(simulate=True, sleep=clock.sleep, monotonic=clock.monotonic)
    assert isinstance(sim.emulator, OptrisEmulator)                          # type: ignore[attr-defined]
    assert isinstance(build_pyrometer_backend(simulate=False), OptrisCt)


@pytest.fixture
def device(clock: Clock) -> Iterator[tuple[OptrisPyrometer, OptrisEmulator, list[Event]]]:
    backend, emulator = build_sim_optris(sleep=clock.sleep, monotonic=clock.monotonic)
    events: list[Event] = []
    pyro = OptrisPyrometer(backend, PyroSettings(com=7, timeout_ms=200), publish=events.append, sleep=clock.sleep,
                           reconnect_delay_s=0.0)
    yield pyro, emulator, events
    pyro.shutdown(timeout=5)


def test_device_open_reads_the_temperature_once(device: tuple[OptrisPyrometer, OptrisEmulator, list[Event]]) -> None:
    pyro, emulator, events = device
    emulator.object_temperature = 450.0
    pyro.open(timeout=5)
    assert pyro.state == DeviceState.READY
    opened = [e for e in events if e.topic == "device.pyro.opened"]
    assert opened and opened[0].data["temperature"] == 450.0 and opened[0].data["port"] == "COM7"
    assert pyro.temperature() == 450.0
    assert pyro.serial() == "1234567" and pyro.firmware() == "FW rev 2001"
    assert pyro.head_temperature() == 30.0 and pyro.box_temperature() == 28.0 and pyro.actual_temperature() == 450.0
    assert pyro.sensor_info().startswith("Model: LT") and pyro.laser() is False
    assert pyro.send_raw(b"\x04", 2) == bytes((0x03, 0xB6))


def test_device_set_emissivity_waits_and_reads_back(device: tuple[OptrisPyrometer, OptrisEmulator, list[Event]],
                                                    clock: Clock) -> None:
    pyro, emulator, events = device
    pyro.open(timeout=5)
    t0 = clock.now
    assert pyro.set_emissivity(0.8) == pytest.approx(0.8)
    assert clock.now - t0 == pytest.approx(0.25)
    assert pyro.set_transmissivity(0.9) == pytest.approx(0.9) and pyro.transmissivity() == pytest.approx(0.9)
    pyro.set_laser(True)
    assert emulator.laser is True and pyro.emissivity() == pytest.approx(0.8)
    assert {"device.pyro.emissivity", "device.pyro.transmissivity", "device.pyro.laser"} <= {e.topic for e in events}
    with pytest.raises(ProbeError):
        pyro.set_emissivity(0.0)


def test_device_link_failure_faults_and_reconnects(device: tuple[OptrisPyrometer, OptrisEmulator, list[Event]]
                                                   ) -> None:
    pyro, emulator, events = device
    pyro.open(timeout=5)
    emulator.drop_next_answer()
    with pytest.raises(DeviceError) as info:
        pyro.temperature()
    assert info.value.code == TIMEOUT and pyro.state == DeviceState.FAULT
    emulator.object_temperature = 99.0
    assert pyro.temperature() == 99.0 and pyro.state == DeviceState.READY
    assert any(e.topic == "device.pyro.reconnected" for e in events)
    emulator.port_missing = True
    pyro.close()
    with pytest.raises(DeviceError) as info:
        pyro.open(timeout=5)
    assert info.value.code == OPEN_FAILED and pyro.state == DeviceState.FAULT
