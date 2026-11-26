"""The Optris pyrometer of the thermal map: binary serial protocol and the device.

Spec: ``docs/vision-core/map.md`` Part 6 §2.1 "Probe protocols"; diagrams ``pyrometer_module d, d3–d22`` (an FGV holding
a VISA session, methods ``Optris - Method.ctl``) and ``Get Checksum d``. The protocol is the Optris CT binary one
**(inferred** from the command bytes; model to confirm with the owner).

Link (``pyrometer_module d3`` "Init"): ``COM<[hardware.pyro] comport>`` (default 1), ``[hardware.pyro] baudrate``
(default 115200), 8 data bits, no parity, 1 stop bit, no flow control, no termination character (binary), VISA timeout
10 s. Every command first clears the port (``VISA Clear``), then writes, then reads a fixed number of bytes:

=====================  ====================================  ======  ==========================================
Method                 Request                               Answer  Value
=====================  ====================================  ======  ==========================================
Get Obj Temp.          ``01`` + checksum                     2       ``(b0·256 + b1 − 1000) / 10`` °C
Get Head Temp.         ``02`` + checksum                     2       same
Get Box Temp           ``03`` + checksum                     2       same
Get Actual Temp        ``81`` + checksum                     2       same
Get Emissivity         ``04``                                2       ``(b0·256 + b1) / 1000``
Get Transmissivity     ``05``                                2       same
Set Emissitivity       ``84 hi lo`` + checksum               —       ``hi lo`` = U16(ε · 1000)
Set Transmissivity     ``85 hi lo`` + checksum               —       same
Get Spot Illumination  ``25``                                1       laser on when ≠ 0
Set Spot Illumination  ``A5 01|00`` + checksum               —
Get Serial             ``0E``                                3       ``b0·65536 + b1·256 + b2`` **(inferred)**
Get Firmware Rev       ``0F``                                2       ``FW rev %d`` of ``b0·256 + b1``
Get Sensor Infos       ``45``                                6       ``Model: %s, Low Temp: %d, High Temp: %d``
=====================  ====================================  ======  ==========================================

The checksum (``Get Checksum d``) is the XOR of the bytes before it. LabVIEW appends it to some commands only (as in
the table); this is reproduced byte for byte, since that is what the delivered machines send. A set command's answer
is not read: the clear before the next command drops it, as here (:meth:`OptrisCt.transact` flushes the input first).
A failed read made LabVIEW return NaN; here it raises :class:`~alox.thermal.errors.ProbeError` and the measurement
counts a failed sample.

Fixed here: ``Init`` opened a new VISA session at every call (the thermal tool called it every ~100 ms while
idle, ``SM d21``) without closing the previous one → the port is opened once by :meth:`OptrisPyrometer.open` and
re-opened only after a link failure; the 10 s VISA timeout froze the tool when the pyrometer was off → answer timeout
``[hardware.pyro] timeout`` (new optional key, default 1000 ms); ε and τ were sent without a range check (``To U16``
saturates) → 0.1 … 1.1 (the Optris CT range, **choice**) or error 1 before sending.

The transport is :class:`alox.devices.cm11x.SerialLink` (pyserial ``serial_for_url``: ``COM7``, ``/dev/ttyUSB0`` or
``socket://host:port``); the emulator of :mod:`alox.thermal.optris_sim` runs behind the same protocol code.
"""

from __future__ import annotations

import collections
import logging
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import reduce
from typing import Any

from alox.devices.base import Device, DeviceError
from alox.devices.cm11x import Cm11xError, Link, LinkFactory, SerialLink, normal_port
from alox.events import Publish

from .errors import INVALID_INPUT, NOT_OPEN, OPEN_FAILED, TIMEOUT, ProbeError

__all__ = [
    "CMD_ACTUAL_TEMP",
    "CMD_BOX_TEMP",
    "CMD_EMISSIVITY",
    "CMD_FIRMWARE",
    "CMD_HEAD_TEMP",
    "CMD_OBJECT_TEMP",
    "CMD_SENSOR_INFO",
    "CMD_SERIAL",
    "CMD_SET_EMISSIVITY",
    "CMD_SET_SPOT",
    "CMD_SET_TRANSMISSIVITY",
    "CMD_SPOT",
    "CMD_TRANSMISSIVITY",
    "FACTOR_RANGE",
    "OptrisCt",
    "OptrisPyrometer",
    "PyroSettings",
    "SET_SETTLE_S",
    "build_pyrometer_backend",
    "checksum",
    "decode_factor",
    "decode_temperature",
    "encode_factor",
    "load_pyro_settings",
    "with_checksum",
]

log = logging.getLogger(__name__)
frame_log = logging.getLogger("alox.thermal.optris.frames")

CMD_OBJECT_TEMP = 0x01
CMD_HEAD_TEMP = 0x02
CMD_BOX_TEMP = 0x03
CMD_EMISSIVITY = 0x04
CMD_TRANSMISSIVITY = 0x05
CMD_SERIAL = 0x0E
CMD_FIRMWARE = 0x0F
CMD_SPOT = 0x25
CMD_SENSOR_INFO = 0x45
CMD_ACTUAL_TEMP = 0x81
CMD_SET_EMISSIVITY = 0x84
CMD_SET_TRANSMISSIVITY = 0x85
CMD_SET_SPOT = 0xA5
#: Accepted emissivity / transmissivity (Optris CT range, **choice**).
FACTOR_RANGE = (0.1, 1.1)
CALL_MARGIN_S = 10.0
#: ``SET EMISSIVITY`` / ``SET TRANSMIS.``: wait before reading the value back (``SM d117``).
SET_SETTLE_S = 0.25


# ---------------------------------------------------------------------------------------------- helpers
def checksum(data: bytes) -> int:
    """``Get Checksum d``: XOR of every byte (initial value 0)."""
    return reduce(lambda acc, b: acc ^ b, data, 0)


def with_checksum(data: bytes) -> bytes:
    return bytes(data) + bytes((checksum(data),))


def decode_temperature(data: bytes) -> float:
    """``(b0·256 + b1 − 1000) / 10`` °C."""
    return (data[0] * 256 + data[1] - 1000) / 10.0


def decode_factor(data: bytes) -> float:
    """Emissivity / transmissivity: ``(b0·256 + b1) / 1000``."""
    return (data[0] * 256 + data[1]) / 1000.0


def encode_factor(value: float) -> bytes:
    """``U16(value · 1000)``, high byte first (LabVIEW ``To U16`` rounds half to even, as :func:`round`)."""
    raw = round(value * 1000.0)
    return bytes(((raw >> 8) & 0xFF, raw & 0xFF))


def _check_factor(value: Any, what: str) -> float:
    lo, hi = FACTOR_RANGE
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value) \
            or not lo <= value <= hi:
        raise ProbeError(f"{what}: {value!r} is not in [{lo:g}, {hi:g}]", INVALID_INPUT)
    return float(value)


# ---------------------------------------------------------------------------------------------- protocol
class OptrisCt:
    """The command set on one :class:`alox.devices.cm11x.Link` (pyserial by default). Thread-safe (one lock).
    Every method that talks to the pyrometer raises :class:`~alox.thermal.errors.ProbeError`. :attr:`frames` keeps the
    last frames (``tx``, ``rx``, ``drop``) with a time stamp."""

    def __init__(self, link_factory: LinkFactory | None = None, *, monotonic: Callable[[], float] = time.monotonic,
                 on_frame: Callable[[str, bytes], None] | None = None, frame_log_size: int = 512) -> None:
        self._factory: LinkFactory = link_factory or SerialLink
        self._monotonic = monotonic
        self.on_frame = on_frame
        self.frames: collections.deque[tuple[float, str, bytes]] = collections.deque(maxlen=frame_log_size)
        self.lock = threading.RLock()
        self._link: Link | None = None
        self.port = ""
        self.baudrate = 115200
        self.timeout_s = 1.0

    @property
    def is_open(self) -> bool:
        return self._link is not None

    def open(self, port: str, baudrate: int = 115200, timeout_s: float = 1.0) -> None:
        """Open the port (an open one is closed first)."""
        with self.lock:
            self.close()
            self.port, self.baudrate, self.timeout_s = normal_port(port), int(baudrate), float(timeout_s)
            try:
                self._link = self._factory(self.port, self.baudrate, self.timeout_s)
            except Cm11xError as exc:
                raise ProbeError(exc.message, exc.code) from exc
            except ProbeError:
                raise
            except Exception as exc:                                       # a factory that raises something else
                raise ProbeError(f"cannot open {self.port}: {exc}", OPEN_FAILED) from exc

    def close(self) -> None:
        with self.lock:
            link, self._link = self._link, None
            if link is not None:
                try:
                    link.close()
                except Exception as exc:                                   # noqa: BLE001 - closing never fails
                    log.debug("close of %s failed: %s", self.port, exc)

    def _record(self, direction: str, data: bytes) -> None:
        if not data:
            return
        self.frames.append((self._monotonic(), direction, bytes(data)))
        if frame_log.isEnabledFor(logging.DEBUG):
            frame_log.debug("%s %s %s", self.port, direction, data.hex(" "))
        if self.on_frame is not None:
            self.on_frame(direction, bytes(data))

    def transact(self, request: bytes, answer_len: int, *, what: str, timeout_s: float | None = None) -> bytes:
        """Discard the waiting input (LabVIEW's ``VISA Clear``), write ``request``, read exactly ``answer_len`` bytes
        (``TIMEOUT`` when fewer come in time). Returns the answer (``b""`` for ``answer_len`` 0)."""
        limit = self.timeout_s if timeout_s is None else timeout_s
        with self.lock:
            if self._link is None:
                raise ProbeError(f"{what}: port not open", NOT_OPEN)
            link = self._link
            try:
                self._record("drop", link.flush_input())
                self._record("tx", request)
                link.write(request)
                if answer_len <= 0:
                    return b""
                answer = link.read(answer_len, limit)
            except Cm11xError as exc:
                raise ProbeError(f"{what}: {exc.message}", exc.code, received=exc.received) from exc
            self._record("rx", answer)
        if len(answer) < answer_len:
            got = answer.hex(" ") if answer else "nothing"
            raise ProbeError(f"{what}: no complete answer within {limit * 1000:.0f} ms (received {got})", TIMEOUT,
                             received=answer)
        return answer

    # -- reads
    def _temperature(self, command: int, what: str) -> float:
        return decode_temperature(self.transact(with_checksum(bytes((command,))), 2, what=what))

    def object_temperature(self) -> float:
        """Get Obj. Temp. (°C)."""
        return self._temperature(CMD_OBJECT_TEMP, "Get Obj Temp.")

    def head_temperature(self) -> float:
        return self._temperature(CMD_HEAD_TEMP, "Get Head Temp.")

    def box_temperature(self) -> float:
        return self._temperature(CMD_BOX_TEMP, "Get Box Temp")

    def actual_temperature(self) -> float:
        return self._temperature(CMD_ACTUAL_TEMP, "Get Actual Temp")

    def emissivity(self) -> float:
        return decode_factor(self.transact(bytes((CMD_EMISSIVITY,)), 2, what="Get Emissivity"))

    def transmissivity(self) -> float:
        return decode_factor(self.transact(bytes((CMD_TRANSMISSIVITY,)), 2, what="Get Transmissivity"))

    def laser(self) -> bool:
        """Get Spot Illumination: TRUE when the sighting laser is on."""
        return self.transact(bytes((CMD_SPOT,)), 1, what="Get Spot Illumination")[0] != 0

    def serial(self) -> str:
        data = self.transact(bytes((CMD_SERIAL,)), 3, what="Get Serial")
        return str((data[0] << 16) | (data[1] << 8) | data[2])

    def firmware(self) -> str:
        data = self.transact(bytes((CMD_FIRMWARE,)), 2, what="Get Firmware Rev")
        return f"FW rev {data[0] * 256 + data[1]}"

    def sensor_info(self) -> str:
        data = self.transact(bytes((CMD_SENSOR_INFO,)), 6, what="Get Sensor Infos")
        model = data[0:2].decode("latin-1")
        return f"Model: {model}, Low Temp: {data[2] * 256 + data[3]}, High Temp: {data[4] * 256 + data[5]}"

    # -- writes
    def set_emissivity(self, value: float) -> None:
        """Set Emissitivity: ``84 hi lo cs`` (0.1 … 1.1)."""
        raw = encode_factor(_check_factor(value, "Set Emissivity"))
        self.transact(with_checksum(bytes((CMD_SET_EMISSIVITY,)) + raw), 0, what="Set Emissivity")

    def set_transmissivity(self, value: float) -> None:
        raw = encode_factor(_check_factor(value, "Set Transmissivity"))
        self.transact(with_checksum(bytes((CMD_SET_TRANSMISSIVITY,)) + raw), 0, what="Set Transmissivity")

    def set_laser(self, on: bool) -> None:
        """Set Spot Illumination: ``A5 01|00 cs``."""
        self.transact(with_checksum(bytes((CMD_SET_SPOT, 1 if on else 0))), 0, what="Set Spot Illumination")

    def send_raw(self, request: bytes, answer_len: int) -> bytes:
        """Engineering: any request, any answer length (nothing decoded)."""
        return self.transact(bytes(request), answer_len, what=f"raw {bytes(request).hex(' ')}")


# ---------------------------------------------------------------------------------------------- settings
@dataclass(frozen=True)
class PyroSettings:
    """``[hardware.pyro]``: ``comport`` (default 1 as ``pyrometer_module d3``), ``baudrate`` (115200), ``timeout``
    (new optional key, ms). ``port`` overrides ``COM<com>`` (``/dev/ttyUSB0``, ``socket://…``)."""

    com: int = 1
    baudrate: int = 115200
    timeout_ms: int = 1000
    port: str | None = None

    @property
    def port_name(self) -> str:
        return self.port or f"COM{self.com if self.com > 0 else 1}"


def load_pyro_settings(ini: Any, *, warn: Callable[[str], None] = log.warning, port: str | None = None
                       ) -> PyroSettings:
    """Settings from ``alox.ini`` (any object with ``get(name, default)``: ``TypedIni``, ``Properties``)."""

    def get(name: str, default: Any) -> Any:
        try:
            return ini.get(name, default)
        except (TypeError, ValueError) as exc:
            warn(f"alox.ini: {exc}; default {default!r} used")
            return default

    timeout = int(get("hardware.pyro.timeout", 1000))
    if timeout <= 0:
        warn(f"alox.ini: [hardware.pyro] timeout = {timeout} ms is not positive; 1000 used")
        timeout = 1000
    return PyroSettings(com=int(get("hardware.pyro.comport", 1)), baudrate=int(get("hardware.pyro.baudrate", 115200)),
                        timeout_ms=timeout, port=port)


def build_pyrometer_backend(*, simulate: bool, sim_options: Any = None,
                            sleep: Callable[[float], None] | None = None,
                            monotonic: Callable[[], float] | None = None) -> OptrisCt:
    """The protocol object on pyserial, or on the emulator of :mod:`alox.thermal.optris_sim` (``simulate``; the
    emulator is ``backend.emulator``)."""
    kwargs: dict[str, Any] = {}
    if monotonic is not None:
        kwargs["monotonic"] = monotonic
    if simulate:
        from .optris_sim import build_sim_optris
        backend, emulator = build_sim_optris(sim_options, sleep=sleep or time.sleep,
                                             monotonic=monotonic or time.monotonic)
        backend.emulator = emulator                                         # type: ignore[attr-defined]
        return backend
    return OptrisCt(**kwargs)


# ---------------------------------------------------------------------------------------------- device
class OptrisPyrometer(Device):
    """The pyrometer as a device (worker thread, timeouts, automatic re-opening after a link failure).

    :meth:`open` opens the port and reads the object temperature once (LabVIEW's ``Init`` + ``Get Obj Temp.``, which
    set ``TOOL_CONNECTED``, ``SM d21``). Methods return values or raise :class:`alox.devices.base.DeviceError`."""

    def __init__(self, backend: OptrisCt | None = None, settings: PyroSettings | None = None, *, name: str = "pyro",
                 publish: Publish | None = None, **device_options: Any) -> None:
        self.ct = backend or OptrisCt()
        self._settings = settings or PyroSettings()
        device_options.setdefault("default_timeout_s", self._settings.timeout_ms / 1000.0 + CALL_MARGIN_S)
        super().__init__(name, publish=publish, **device_options)

    @property
    def settings(self) -> PyroSettings:
        return self._settings

    def set_settings(self, **changes: Any) -> None:
        """Change settings in memory (applied at the next :meth:`open`)."""
        self._settings = replace(self._settings, **changes)

    # -- hooks
    def _open(self) -> None:
        s = self._settings
        self.ct.open(s.port_name, s.baudrate, s.timeout_ms / 1000.0)
        temperature = self.ct.object_temperature()
        self.emit("opened", port=self.ct.port, baudrate=s.baudrate, temperature=temperature)

    def _close(self) -> None:
        self.ct.close()

    def _is_link_failure(self, exc: BaseException) -> bool:
        return isinstance(exc, ProbeError) and exc.link_failure

    def _translate(self, exc: BaseException, what: str) -> DeviceError:
        if isinstance(exc, DeviceError):
            return exc
        if isinstance(exc, ProbeError):
            return DeviceError(self.name, f"{what}: {exc.message}", code=exc.code)
        return super()._translate(exc, what)

    # -- reads
    def temperature(self, *, timeout: float | None = None) -> float:
        """Get Obj. Temp. (°C): the measured temperature."""
        return self.call("object temperature", self.ct.object_temperature, timeout=timeout)

    def head_temperature(self, *, timeout: float | None = None) -> float:
        return self.call("head temperature", self.ct.head_temperature, timeout=timeout)

    def box_temperature(self, *, timeout: float | None = None) -> float:
        return self.call("box temperature", self.ct.box_temperature, timeout=timeout)

    def actual_temperature(self, *, timeout: float | None = None) -> float:
        return self.call("actual temperature", self.ct.actual_temperature, timeout=timeout)

    def emissivity(self, *, timeout: float | None = None) -> float:
        return self.call("get emissivity", self.ct.emissivity, timeout=timeout)

    def transmissivity(self, *, timeout: float | None = None) -> float:
        return self.call("get transmissivity", self.ct.transmissivity, timeout=timeout)

    def laser(self, *, timeout: float | None = None) -> bool:
        return self.call("get laser", self.ct.laser, timeout=timeout)

    def serial(self, *, timeout: float | None = None) -> str:
        return self.call("get serial", self.ct.serial, timeout=timeout)

    def firmware(self, *, timeout: float | None = None) -> str:
        return self.call("get firmware", self.ct.firmware, timeout=timeout)

    def sensor_info(self, *, timeout: float | None = None) -> str:
        return self.call("get sensor info", self.ct.sensor_info, timeout=timeout)

    # -- writes
    def set_emissivity(self, value: float, *, timeout: float | None = None) -> float:
        """Set Emissitivity, wait 250 ms, Get Emissivity (``SM d117``); returns the value read back."""
        _check_factor(value, "set emissivity")

        def run() -> float:
            self.ct.set_emissivity(value)
            self._sleep(SET_SETTLE_S)
            read = self.ct.emissivity()
            self.emit("emissivity", value=read)
            return read
        return self.call("set emissivity", run, timeout=timeout)

    def set_transmissivity(self, value: float, *, timeout: float | None = None) -> float:
        """Set Transmissivity, wait 250 ms, Get Transmissivity; returns the value read back."""
        _check_factor(value, "set transmissivity")

        def run() -> float:
            self.ct.set_transmissivity(value)
            self._sleep(SET_SETTLE_S)
            read = self.ct.transmissivity()
            self.emit("transmissivity", value=read)
            return read
        return self.call("set transmissivity", run, timeout=timeout)

    def set_laser(self, on: bool, *, timeout: float | None = None) -> None:
        """Set Spot Illumination (the sighting laser, ``SM d129``)."""
        def run() -> None:
            self.ct.set_laser(bool(on))
            self.emit("laser", on=bool(on))
        self.call("set laser", run, timeout=timeout)

    def send_raw(self, request: bytes, answer_len: int, *, timeout: float | None = None) -> bytes:
        """Engineering: see :meth:`OptrisCt.send_raw`."""
        return self.call("raw", lambda: self.ct.send_raw(request, answer_len), timeout=timeout)

