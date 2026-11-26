"""Fixtures of the thermal map tests: a fake clock, simulated XY/Z stages, the emulated pyrometer and a fake BandiT."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from alox.devices.xy import WaferSettings, XYSettings, XYStage
from alox.devices.zstage import ZStage
from alox.events import Event
from alox.thermal.bandit import BanditProbe, BanditSettings
from alox.thermal.controller import ThermalController
from alox.thermal.modbus import ModbusTcpClient
from alox.thermal.modbus_sim import FakeModbusServer
from alox.thermal.optris import OptrisPyrometer, PyroSettings
from alox.thermal.optris_sim import OptrisEmulator, build_sim_optris
from alox.thermal.settings import MeasureOptions, ThermalConfig, ThermalSettings
from aloxcam.settings import SimulationOptions, ZStageSettings
from aloxcam.zstage.sim import SimStage

SIM = SimulationOptions(stage_min_mm=0.0, stage_max_mm=200.0, stage_reference_s=0.01)
ORIGIN = (100.0, 100.0)
BANDIT_REGISTER = 10 + 12288


class FakeTime:
    """A monotonic clock that only moves when someone sleeps (thread-safe)."""

    def __init__(self) -> None:
        self.now = 1000.0
        self._lock = threading.Lock()

    def monotonic(self) -> float:
        with self._lock:
            return self.now

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self.now += max(0.0, seconds)


def chuck_temperature(x: float, y: float) -> float:
    """The simulated susceptor: 300 °C at the origin, +1 °C/mm along chuck X, +0.1 °C/mm along chuck Y."""
    return 300.0 + x + 0.1 * y


@dataclass
class Rig:
    clock: FakeTime
    xy: XYStage
    xsim: SimStage
    ysim: SimStage
    z: ZStage
    pyro: OptrisPyrometer
    emulator: OptrisEmulator
    events: list[Event]
    controller: ThermalController
    store: Any = None
    bandit: BanditProbe | None = None
    server: FakeModbusServer | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def topics(self, prefix: str = "thermal.") -> list[str]:
        return [e.topic for e in list(self.events) if e.topic.startswith(prefix)]

    def of(self, topic: str) -> list[Event]:
        return [e for e in list(self.events) if e.topic == topic]

    def chuck_position(self) -> tuple[float, float]:
        return ORIGIN[0] - self.xsim.position, ORIGIN[1] - self.ysim.position


SMALL = ThermalSettings(diameters=(0.0, 20.0), nb_points=(1, 4), samples=3, offset_angle=0, wait_ms=100,
                        origin=ORIGIN, coordinate_system=3)


@pytest.fixture
def make_rig() -> Iterator[Callable[..., Rig]]:
    rigs: list[Rig] = []

    def factory(settings: ThermalSettings = SMALL, *, wafer: WaferSettings | None = None, store: Any = None,
                bandit: bool = False, options: MeasureOptions | None = None, open_tool: bool = True,
                registers: dict[int, int] | None = None) -> Rig:
        clock = FakeTime()
        events: list[Event] = []
        xsim, ysim, zsim = (SimStage(SIM, clock=clock.monotonic, dh_running=False) for _ in range(3))
        xy = XYStage(xsim, ysim, XYSettings(wafer=wafer or WaferSettings()), publish=events.append,
                     sleep=clock.sleep, monotonic=clock.monotonic, supervise=False, reconnect_delay_s=0.0)
        xy.open(timeout=10)
        z = ZStage(zsim, ZStageSettings(min_pos=0.0, max_pos=20.0), publish=events.append, sleep=clock.sleep,
                   monotonic=clock.monotonic, reconnect_delay_s=0.0)
        z.open(timeout=10)
        backend, emulator = build_sim_optris(sleep=clock.sleep, monotonic=clock.monotonic)
        rig_ref: list[Rig] = []
        emulator.object_temperature = lambda: chuck_temperature(*rig_ref[0].chuck_position())
        pyro = OptrisPyrometer(backend, PyroSettings(com=7), publish=events.append, sleep=clock.sleep,
                               reconnect_delay_s=0.0)
        server = probe = None
        if bandit:
            server = FakeModbusServer({BANDIT_REGISTER: 2500} if registers is None else registers).start()
            probe = BanditProbe(ModbusTcpClient(), BanditSettings(host=" 127.0.0.1", port=server.port,
                                                                  timeout_ms=500, connect_wait_ms=1000),
                                publish=events.append, sleep=clock.sleep, reconnect_delay_s=0.0)
        controller = ThermalController(xy=xy, z=z, pyrometer=pyro, bandit=probe,
                                       config=ThermalConfig(settings=settings), store=store, publish=events.append,
                                       options=options or MeasureOptions(), clock=clock.monotonic, sleep=clock.sleep,
                                       now=lambda: datetime(2026, 11, 26, 14, 5, 9, tzinfo=UTC), device_timeout_s=10.0)
        rig = Rig(clock, xy, xsim, ysim, z, pyro, emulator, events, controller, store, probe, server)
        rig_ref.append(rig)
        rigs.append(rig)
        if open_tool:
            assert controller.open(live=False).result(10) is True
        return rig

    yield factory
    for rig in rigs:
        rig.controller.close(timeout=5)
        for device in (rig.pyro, rig.bandit, rig.xy, rig.z):
            if device is not None:
                device.shutdown(timeout=5)
        if rig.server is not None:
            rig.server.close()
