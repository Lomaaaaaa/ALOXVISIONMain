"""An emulated Optris pyrometer speaking the binary protocol of :mod:`alox.thermal.optris`.

It plugs into the in-process link of the monochromator emulator (:func:`alox.devices.cm11x_sim.sim_link_factory`,
which only needs ``feed``, ``take_injected``, ``reset_input``, ``link_broken`` and ``port_missing``), so the protocol
code runs unchanged against it, on a fake clock in tests.

Behaviour (*choice* where the manual was not available):

- requests are framed by their command byte, with the lengths LabVIEW sends (``01``/``02``/``03``/``81`` + checksum,
  ``84``/``85`` + 2 bytes + checksum, ``A5`` + 1 byte + checksum, single-byte reads); with
  ``expects_checksum=False`` the checksum byte is parsed as the next command, as a pyrometer without checksum mode
  would (*choice*: shows why every command clears the input first);
- a wrong checksum or an unknown command byte gets no answer (*choice*);
- set commands answer with their data bytes (*choice*; the driver does not read them);
- temperatures are encoded ``round(T · 10 + 1000)`` on 16 bits; :attr:`OptrisEmulator.object_temperature` may be a
  callable (the tests tie it to the simulated XY position to get a thermal map).

Fault injection: :attr:`OptrisEmulator.answer_delay_s`, :meth:`OptrisEmulator.drop_next_answer`,
:meth:`OptrisEmulator.inject`, :attr:`OptrisEmulator.link_broken`, :attr:`OptrisEmulator.port_missing`.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from alox.devices.cm11x_sim import sim_link_factory

from .optris import (
    CMD_ACTUAL_TEMP,
    CMD_BOX_TEMP,
    CMD_EMISSIVITY,
    CMD_FIRMWARE,
    CMD_HEAD_TEMP,
    CMD_OBJECT_TEMP,
    CMD_SENSOR_INFO,
    CMD_SERIAL,
    CMD_SET_EMISSIVITY,
    CMD_SET_SPOT,
    CMD_SET_TRANSMISSIVITY,
    CMD_SPOT,
    CMD_TRANSMISSIVITY,
    OptrisCt,
    checksum,
    decode_factor,
)

__all__ = ["OptrisEmulator", "OptrisSimOptions", "build_sim_optris", "encode_temperature"]

#: Data bytes after the command byte, checksum excluded, and whether LabVIEW appends a checksum.
_FRAMES: dict[int, tuple[int, bool]] = {
    CMD_OBJECT_TEMP: (0, True), CMD_HEAD_TEMP: (0, True), CMD_BOX_TEMP: (0, True), CMD_ACTUAL_TEMP: (0, True),
    CMD_EMISSIVITY: (0, False), CMD_TRANSMISSIVITY: (0, False), CMD_SERIAL: (0, False), CMD_FIRMWARE: (0, False),
    CMD_SPOT: (0, False), CMD_SENSOR_INFO: (0, False),
    CMD_SET_EMISSIVITY: (2, True), CMD_SET_TRANSMISSIVITY: (2, True), CMD_SET_SPOT: (1, True),
}


def encode_temperature(celsius: float) -> bytes:
    raw = max(0, min(0xFFFF, round(celsius * 10.0 + 1000.0)))
    return bytes((raw >> 8, raw & 0xFF))


@dataclass(frozen=True)
class OptrisSimOptions:
    object_temp_c: float = 25.0
    head_temp_c: float = 30.0
    box_temp_c: float = 28.0
    emissivity: float = 0.95
    transmissivity: float = 1.0
    serial: int = 1234567
    firmware: int = 2001
    model: str = "LT"
    low_temp_c: int = 0
    high_temp_c: int = 975
    expects_checksum: bool = True


class OptrisEmulator:
    """The protocol over a small model of the pyrometer. Thread-safe."""

    def __init__(self, options: OptrisSimOptions | None = None, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.options = options or OptrisSimOptions()
        o = self.options
        self._clock = clock
        self._lock = threading.RLock()
        self.object_temperature: float | Callable[[], float] = o.object_temp_c
        self.emissivity = o.emissivity
        self.transmissivity = o.transmissivity
        self.laser = False
        self.answer_delay_s = 0.0
        self.port_missing = False
        self.link_broken = False
        self.received: list[bytes] = []
        self._buffer = bytearray()
        self._drop_next = False
        self._injected = bytearray()

    # -- injection
    def drop_next_answer(self) -> None:
        with self._lock:
            self._drop_next = True

    def inject(self, data: bytes) -> None:
        """Bytes that arrive unrequested (the next command's flush must drop them)."""
        with self._lock:
            self._injected += data

    def take_injected(self) -> bytes:
        with self._lock:
            data, self._injected = bytes(self._injected), bytearray()
            return data

    def reset_input(self) -> None:
        with self._lock:
            self._buffer.clear()

    # -- protocol
    def _object(self) -> float:
        value = self.object_temperature
        return float(value()) if callable(value) else float(value)

    def feed(self, data: bytes) -> list[tuple[float, bytes]]:
        """Bytes from the client → ``[(release time, answer), …]``."""
        out: list[tuple[float, bytes]] = []
        with self._lock:
            self._buffer += data
            while self._buffer:
                command = self._buffer[0]
                frame = _FRAMES.get(command)
                if frame is None:
                    del self._buffer[0]                                    # unknown byte: ignored
                    continue
                data_len, has_cs = frame
                length = 1 + data_len + (1 if has_cs and self.options.expects_checksum else 0)
                if len(self._buffer) < length:
                    break
                request = bytes(self._buffer[:length])
                del self._buffer[:length]
                self.received.append(request)
                if has_cs and self.options.expects_checksum and checksum(request[:-1]) != request[-1]:
                    continue
                answer = self._execute(command, request[1:1 + data_len])
                if self._drop_next:
                    self._drop_next = False
                    continue
                if answer:
                    out.append((self._clock() + self.answer_delay_s, answer))
        return out

    def _execute(self, command: int, data: bytes) -> bytes:
        o = self.options
        if command == CMD_OBJECT_TEMP:
            return encode_temperature(self._object())
        if command == CMD_HEAD_TEMP:
            return encode_temperature(o.head_temp_c)
        if command == CMD_BOX_TEMP:
            return encode_temperature(o.box_temp_c)
        if command == CMD_ACTUAL_TEMP:
            return encode_temperature(self._object())
        if command == CMD_EMISSIVITY:
            raw = round(self.emissivity * 1000)
            return bytes((raw >> 8, raw & 0xFF))
        if command == CMD_TRANSMISSIVITY:
            raw = round(self.transmissivity * 1000)
            return bytes((raw >> 8, raw & 0xFF))
        if command == CMD_SERIAL:
            return bytes(((o.serial >> 16) & 0xFF, (o.serial >> 8) & 0xFF, o.serial & 0xFF))
        if command == CMD_FIRMWARE:
            return bytes((o.firmware >> 8, o.firmware & 0xFF))
        if command == CMD_SPOT:
            return bytes((1 if self.laser else 0,))
        if command == CMD_SENSOR_INFO:
            model = o.model.encode("latin-1")[:2].ljust(2, b" ")
            return model + bytes((o.low_temp_c >> 8, o.low_temp_c & 0xFF, o.high_temp_c >> 8, o.high_temp_c & 0xFF))
        if command == CMD_SET_EMISSIVITY:
            self.emissivity = decode_factor(data)
            return bytes(data)
        if command == CMD_SET_TRANSMISSIVITY:
            self.transmissivity = decode_factor(data)
            return bytes(data)
        if command == CMD_SET_SPOT:
            self.laser = data[0] != 0
            return bytes(data)
        return b""                                                          # pragma: no cover - framed above


def build_sim_optris(options: OptrisSimOptions | None = None, *, sleep: Callable[[float], None] = time.sleep,
                     monotonic: Callable[[], float] = time.monotonic) -> tuple[OptrisCt, OptrisEmulator]:
    """An :class:`~alox.thermal.optris.OptrisCt` on a new emulator; returns both."""
    emulator = OptrisEmulator(options, clock=monotonic)
    factory = sim_link_factory(emulator, sleep=sleep, monotonic=monotonic)          # type: ignore[arg-type]
    return OptrisCt(factory, monotonic=monotonic), emulator
