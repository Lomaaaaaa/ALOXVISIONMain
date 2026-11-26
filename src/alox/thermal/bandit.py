"""The kSA BandiT reading of the thermal map, through Modbus TCP, as a device.

Spec: ``docs/vision-core/map.md`` Part 6 §2.1; diagrams ``get_Bandit_TempValue_Modbus d, d1`` and
``ConvertionTempVoltage d``.

LabVIEW, for every sample: ``New TCP Master`` to ``[hardware.bandit] ipadress`` port 502, wait 1000 ms (in parallel
with ``address = starting address + 12288``), ``Read Holding Registers`` (function 3) of ``Number of Inputs`` (1)
registers, element 0 = ``OUTPUT mV`` (U16), ``Shutdown``. ``starting address`` is ``[hardware.bandit] startregister``
(default 10, ``SM d10``) in the measurement (``SM d156``); the idle refresh used a hard-coded 10 (``SM d22``).
``ConvertionTempVoltage``: ``V = mV / 1000``; ``xmax/xmin`` = max/min of the voltage scale ``[minVoltage,
maxVoltage]`` (0/5 V), ``ymax/ymin`` of the temperature scale ``[minTemperature, maxTemperature]`` (300/500 °C);
``slope = (ymax − ymin)/(xmax − xmin)``, 0 when either range is empty; ``T = V · slope + ymin``.

Fixed here:

- one TCP connection kept open (LabVIEW connected, waited 1 s and disconnected for each sample: > 1 s per sample);
  the 1 s wait (``connect_wait_ms``) is kept once after connecting, in case the device needs it;
- ``T = ymin + slope · (V − xmin)``: LabVIEW's ``V · slope + ymin`` is only right when ``minVoltage`` is 0 (the
  shipped value, for which both agree);
- the idle reading uses the configured register, not the constant 10 (``SM d22``);
- the probe is "connected" when the register can be read (LabVIEW: ``ping <IP>`` exit code 0, ``SM d130``, which
  says nothing about Modbus); the leading space of the shipped ``ipadress`` is stripped.

New optional keys: ``[hardware.bandit] port`` (502), ``unitid`` (Modbus unit, default 1: the NI library's default is not
visible in the export, question for the owner), ``timeout`` (ms, 3000), ``connectwait`` (ms, 1000),
``registeroffset`` (12288).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from alox.devices.base import Device, DeviceError
from alox.events import Publish

from .errors import ProbeError
from .modbus import DEFAULT_PORT, ModbusTcpClient

__all__ = ["REGISTER_OFFSET", "BanditProbe", "BanditSettings", "load_bandit_settings", "voltage_to_temperature"]

log = logging.getLogger(__name__)

#: ``get_Bandit_TempValue_Modbus d1``: added to the configured start register.
REGISTER_OFFSET = 12288
CALL_MARGIN_S = 10.0


def voltage_to_temperature(millivolts: float, voltage_scale: Sequence[float] = (0.0, 5.0),
                           temperature_scale: Sequence[float] = (300.0, 500.0)) -> float:
    """``ConvertionTempVoltage`` with the offset fixed: ``ymin + slope · (mV/1000 − xmin)``, ``slope = (ymax −
    ymin)/(xmax − xmin)`` (0 when either range is empty, as LabVIEW)."""
    volts = float(millivolts) / 1000.0
    xmax, xmin = max(voltage_scale[0], voltage_scale[1]), min(voltage_scale[0], voltage_scale[1])
    ymax, ymin = max(temperature_scale[0], temperature_scale[1]), min(temperature_scale[0], temperature_scale[1])
    slope = 0.0 if ymin == ymax or xmin == xmax else (ymax - ymin) / (xmax - xmin)
    return ymin + slope * (volts - xmin)


@dataclass(frozen=True)
class BanditSettings:
    """``[hardware.bandit]`` keys of the probe (``SM d10`` defaults)."""

    host: str = "192.168.1.2"
    port: int = DEFAULT_PORT
    unit_id: int = 1
    start_register: int = 10
    register_offset: int = REGISTER_OFFSET
    voltage_scale: tuple[float, float] = (0.0, 5.0)
    temperature_scale: tuple[float, float] = (300.0, 500.0)
    timeout_ms: int = 3000
    connect_wait_ms: int = 1000

    @property
    def register(self) -> int:
        return self.start_register + self.register_offset


def load_bandit_settings(ini: Any, *, warn: Callable[[str], None] = log.warning) -> BanditSettings:
    """Settings from ``alox.ini`` (any object with ``get(name, default)``)."""

    def get(name: str, default: Any) -> Any:
        try:
            return ini.get(name, default)
        except (TypeError, ValueError) as exc:
            warn(f"alox.ini: {exc}; default {default!r} used")
            return default

    def number(name: str, default: float) -> float:
        value = get(name, default)
        try:
            result = float(value)
        except (TypeError, ValueError):
            warn(f"alox.ini: [hardware.bandit] {name.rpartition('.')[2]} = {value!r} is not a number; {default} used")
            return default
        return result if math.isfinite(result) else default

    timeout = int(number("hardware.bandit.timeout", 3000))
    if timeout <= 0:
        warn(f"alox.ini: [hardware.bandit] timeout = {timeout} ms is not positive; 3000 used")
        timeout = 3000
    return BanditSettings(
        host=str(get("hardware.bandit.ipadress", "192.168.1.2")).strip(),
        port=int(number("hardware.bandit.port", DEFAULT_PORT)),
        unit_id=int(number("hardware.bandit.unitid", 1)),
        start_register=int(number("hardware.bandit.startregister", 10)),
        register_offset=int(number("hardware.bandit.registeroffset", REGISTER_OFFSET)),
        voltage_scale=(number("hardware.bandit.minVoltage", 0.0), number("hardware.bandit.maxVoltage", 5.0)),
        temperature_scale=(number("hardware.bandit.minTemperature", 300.0),
                           number("hardware.bandit.maxTemperature", 500.0)),
        timeout_ms=timeout,
        connect_wait_ms=max(0, int(number("hardware.bandit.connectwait", 1000))))


class BanditProbe(Device):
    """The BandiT reading as a device: :meth:`open` connects (then waits ``connect_wait_ms`` and reads the register
    once), :meth:`temperature` reads one sample. Re-connects after a link failure."""

    def __init__(self, client: ModbusTcpClient | None = None, settings: BanditSettings | None = None, *,
                 name: str = "bandit", publish: Publish | None = None, **device_options: Any) -> None:
        self.client = client or ModbusTcpClient()
        self._settings = settings or BanditSettings()
        device_options.setdefault("default_timeout_s", self._settings.timeout_ms / 1000.0 + CALL_MARGIN_S)
        super().__init__(name, publish=publish, **device_options)

    @property
    def settings(self) -> BanditSettings:
        return self._settings

    def set_settings(self, **changes: Any) -> None:
        """Change settings in memory: the scales apply at once, the address and port at the next :meth:`open`."""
        self._settings = replace(self._settings, **changes)

    # -- hooks
    def _open(self) -> None:
        s = self._settings
        self.client.open(s.host, s.port, s.timeout_ms / 1000.0)
        if s.connect_wait_ms > 0:
            self._sleep(s.connect_wait_ms / 1000.0)
        millivolts = self._read()
        self.emit("opened", host=self.client.host, port=self.client.port, register=s.register, millivolts=millivolts,
                  temperature=self._convert(millivolts))

    def _close(self) -> None:
        self.client.close()

    def _is_link_failure(self, exc: BaseException) -> bool:
        return isinstance(exc, ProbeError) and exc.link_failure

    def _translate(self, exc: BaseException, what: str) -> DeviceError:
        if isinstance(exc, DeviceError):
            return exc
        if isinstance(exc, ProbeError):
            return DeviceError(self.name, f"{what}: {exc.message}", code=exc.code)
        return super()._translate(exc, what)

    def _read(self) -> int:
        s = self._settings
        return self.client.read_holding_registers(s.register, 1, unit_id=s.unit_id)[0]

    def _convert(self, millivolts: float) -> float:
        s = self._settings
        return voltage_to_temperature(millivolts, s.voltage_scale, s.temperature_scale)

    # -- reads
    def read_millivolts(self, *, timeout: float | None = None) -> int:
        """The register (mV, unsigned)."""
        return self.call("read register", self._read, timeout=timeout)

    def temperature(self, *, timeout: float | None = None) -> float:
        """One sample in °C (register → :func:`voltage_to_temperature`)."""
        return self.call("temperature", lambda: self._convert(self._read()), timeout=timeout)
