"""The Modbus TCP client and fake server (``alox.thermal.modbus``) and the BandiT device (``alox.thermal.bandit``)."""

from __future__ import annotations

import socket
import struct
import threading
from collections.abc import Iterator

import pytest

from alox.devices.base import DeviceError, DeviceState
from alox.events import Event
from alox.thermal.bandit import (
    REGISTER_OFFSET,
    BanditProbe,
    BanditSettings,
    load_bandit_settings,
    voltage_to_temperature,
)
from alox.thermal.errors import (
    BAD_ANSWER,
    INVALID_INPUT,
    MODBUS_EXCEPTION_BASE,
    NOT_OPEN,
    OPEN_FAILED,
    RECEIVE_FAILED,
    TIMEOUT,
    ProbeError,
)
from alox.thermal.modbus import (
    FC_READ_HOLDING_REGISTERS,
    FC_READ_INPUT_REGISTERS,
    ModbusTcpClient,
)
from alox.thermal.modbus_sim import FakeModbusServer


@pytest.mark.parametrize(("mv", "expected"), [(0, 300.0), (2500, 400.0), (5000, 500.0), (6000, 540.0)])
def test_conversion_of_the_default_scales(mv: int, expected: float) -> None:
    assert voltage_to_temperature(mv) == pytest.approx(expected)


def test_conversion_orders_the_scales_and_handles_an_offset_and_empty_ranges() -> None:
    assert voltage_to_temperature(2500, (5.0, 0.0), (500.0, 300.0)) == pytest.approx(400.0)
    assert voltage_to_temperature(3000, (1.0, 5.0), (300.0, 500.0)) == pytest.approx(400.0)   # LabVIEW: 450
    assert voltage_to_temperature(3000, (2.0, 2.0), (300.0, 500.0)) == 300.0
    assert voltage_to_temperature(3000, (0.0, 5.0), (300.0, 300.0)) == 300.0


@pytest.fixture
def server() -> Iterator[FakeModbusServer]:
    with FakeModbusServer({12298: 2500, 12299: 7}, {5: 42}) as srv:
        yield srv


@pytest.fixture
def client(server: FakeModbusServer) -> Iterator[ModbusTcpClient]:
    c = ModbusTcpClient()
    c.open(" 127.0.0.1", server.port, 0.5)
    yield c
    c.close()


def test_read_holding_and_input_registers(server: FakeModbusServer, client: ModbusTcpClient) -> None:
    assert client.host == "127.0.0.1"
    assert client.read_holding_registers(12298, 2) == [2500, 7]
    assert client.read_input_registers(5, 1, unit_id=3) == [42]
    assert server.requests == [(1, FC_READ_HOLDING_REGISTERS, 12298, 2), (3, FC_READ_INPUT_REGISTERS, 5, 1)]


def test_exceptions_invalid_arguments_and_timeouts(server: FakeModbusServer, client: ModbusTcpClient) -> None:
    with pytest.raises(ProbeError) as info:
        client.read_holding_registers(1, 1)
    assert info.value.code == MODBUS_EXCEPTION_BASE + 2 and "illegal data address" in info.value.message
    server.exception_next(4)
    with pytest.raises(ProbeError) as info:
        client.read_holding_registers(12298, 1)
    assert info.value.code == MODBUS_EXCEPTION_BASE + 4 and not info.value.link_failure
    for address, count in ((-1, 1), (0x10000, 1), (0, 0), (0, 126), (0xFFFF, 2)):
        with pytest.raises(ProbeError) as info:
            client.read_holding_registers(address, count)
        assert info.value.code == INVALID_INPUT
    server.drop_next()
    with pytest.raises(ProbeError) as info:
        client.read_holding_registers(12298, 1)
    assert info.value.code == TIMEOUT
    client.open("127.0.0.1", server.port, 0.5)                              # the late answer is gone with the link
    server.close_next()
    with pytest.raises(ProbeError) as info:
        client.read_holding_registers(12298, 1)
    assert info.value.code == RECEIVE_FAILED
    client.close()
    with pytest.raises(ProbeError) as info:
        client.read_holding_registers(12298, 1)
    assert info.value.code == NOT_OPEN


def test_connect_failure_and_an_answer_out_of_step() -> None:
    probe_sock = socket.socket()
    probe_sock.bind(("127.0.0.1", 0))
    port = probe_sock.getsockname()[1]
    probe_sock.close()
    c = ModbusTcpClient()
    with pytest.raises(ProbeError) as info:
        c.open("127.0.0.1", port, 0.5)
    assert info.value.code == OPEN_FAILED

    listener = socket.create_server(("127.0.0.1", 0))

    def serve() -> None:
        conn, _ = listener.accept()
        with conn:
            request = conn.recv(12)
            tid = struct.unpack(">H", request[:2])[0]
            conn.sendall(struct.pack(">HHHB", tid + 1, 0, 5, 1) + bytes((3, 2, 0, 1)))
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    c.open("127.0.0.1", listener.getsockname()[1], 0.5)
    with pytest.raises(ProbeError) as info:
        c.read_holding_registers(0, 1)
    assert info.value.code == BAD_ANSWER
    c.close()
    listener.close()
    thread.join(2)


def test_settings_from_the_ini() -> None:
    ini = {"hardware.bandit.ipadress": " 192.168.1.2", "hardware.bandit.startregister": 12,
           "hardware.bandit.minVoltage": 0, "hardware.bandit.maxVoltage": 10, "hardware.bandit.minTemperature": 200,
           "hardware.bandit.maxTemperature": 1200}
    s = load_bandit_settings(ini)
    assert (s.host, s.port, s.unit_id, s.register) == ("192.168.1.2", 502, 1, 12 + REGISTER_OFFSET)
    assert s.voltage_scale == (0.0, 10.0) and s.temperature_scale == (200.0, 1200.0)
    warnings: list[str] = []
    s = load_bandit_settings({"hardware.bandit.maxVoltage": "x", "hardware.bandit.timeout": -1}, warn=warnings.append)
    assert s.voltage_scale == (0.0, 5.0) and s.timeout_ms == 3000 and len(warnings) == 2


class Clock:
    def __init__(self) -> None:
        self.slept = 0.0

    def sleep(self, seconds: float) -> None:
        self.slept += seconds


def test_device_connects_once_and_converts(server: FakeModbusServer) -> None:
    clock = Clock()
    events: list[Event] = []
    probe = BanditProbe(ModbusTcpClient(), BanditSettings(host="127.0.0.1", port=server.port, timeout_ms=500),
                        publish=events.append, sleep=clock.sleep, reconnect_delay_s=0.0)
    try:
        probe.open(timeout=5)
        assert clock.slept == pytest.approx(1.0)                            # LabVIEW's 1 s after connecting, once
        opened = next(e for e in events if e.topic == "device.bandit.opened")
        assert opened.data["register"] == 12298 and opened.data["temperature"] == pytest.approx(400.0)
        server.set_register(12298, 5000)
        assert probe.read_millivolts() == 5000 and probe.temperature() == pytest.approx(500.0)
        assert server.connections == 1 and clock.slept == pytest.approx(1.0)
        server.disconnect_all()
        with pytest.raises(DeviceError) as info:
            probe.temperature()
        assert info.value.code == RECEIVE_FAILED and probe.state == DeviceState.FAULT
        assert probe.temperature() == pytest.approx(500.0) and server.connections == 2
        probe.set_settings(temperature_scale=(0.0, 100.0))
        assert probe.temperature() == pytest.approx(100.0)
    finally:
        probe.shutdown(timeout=5)
