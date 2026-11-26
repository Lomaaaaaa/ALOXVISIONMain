"""A minimal Modbus TCP master: read holding / input registers (no dependency besides :mod:`socket`).

LabVIEW used the NI Modbus Library: ``New TCP Master`` (port 502), ``Read Holding Registers`` (function code 3,
``Modbus Master.lvclass:Read Holding Registers.vi`` in ``get_Bandit_TempValue_Modbus``), ``Shutdown``. The addresses
are the 0-based PDU addresses.

Frame (Modbus Application Protocol over TCP): MBAP header ``transaction id (2) · protocol 0 (2) · length (2) · unit
id (1)`` then the PDU ``function (1) · start address (2) · quantity (2)``; the answer PDU is ``function · byte count ·
registers`` (big-endian U16), or ``function | 0x80 · exception code``.

Errors (:class:`~alox.thermal.errors.ProbeError`): ``OPEN_FAILED`` (connect), ``SEND_FAILED``, ``RECEIVE_FAILED``
(socket error or connection closed), ``TIMEOUT`` (no complete answer in time), ``BAD_ANSWER`` (wrong transaction id,
protocol, unit, function or byte count), ``100 + n`` Modbus exception ``n``, ``INVALID_INPUT`` (address or quantity
out of range, refused before sending).
"""

from __future__ import annotations

import logging
import socket
import struct
import threading
from collections.abc import Callable
from typing import Any

from .errors import (
    BAD_ANSWER,
    INVALID_INPUT,
    MODBUS_EXCEPTION_BASE,
    NOT_OPEN,
    OPEN_FAILED,
    RECEIVE_FAILED,
    SEND_FAILED,
    TIMEOUT,
    ProbeError,
)

__all__ = ["FC_READ_HOLDING_REGISTERS", "FC_READ_INPUT_REGISTERS", "MAX_REGISTERS", "ModbusTcpClient",
           "exception_text"]

log = logging.getLogger(__name__)

FC_READ_HOLDING_REGISTERS = 3
FC_READ_INPUT_REGISTERS = 4
MAX_REGISTERS = 125
DEFAULT_PORT = 502

_EXCEPTIONS = {1: "illegal function", 2: "illegal data address", 3: "illegal data value", 4: "server device failure",
               5: "acknowledge", 6: "server device busy", 8: "memory parity error",
               10: "gateway path unavailable", 11: "gateway target device failed to respond"}


def exception_text(code: int) -> str:
    return _EXCEPTIONS.get(code, f"exception {code}")


#: ``connect((host, port), timeout_s)`` → a connected socket (``socket.create_connection`` by default).
Connector = Callable[[tuple[str, int], float], Any]


def _default_connect(address: tuple[str, int], timeout_s: float) -> socket.socket:
    return socket.create_connection(address, timeout=timeout_s)


class ModbusTcpClient:
    """One TCP connection to a Modbus server. Thread-safe (one lock); one request at a time."""

    def __init__(self, *, connect: Connector | None = None) -> None:
        self._connect = connect or _default_connect
        self._sock: Any = None
        self._lock = threading.RLock()
        self._tid = 0
        self.host = ""
        self.port = DEFAULT_PORT
        self.timeout_s = 3.0

    @property
    def is_open(self) -> bool:
        return self._sock is not None

    def open(self, host: str, port: int = DEFAULT_PORT, timeout_s: float = 3.0) -> None:
        """Connect (an open connection is closed first). ``host`` is stripped (the shipped INI has a leading
        space)."""
        with self._lock:
            self.close()
            self.host, self.port, self.timeout_s = host.strip(), int(port), float(timeout_s)
            try:
                sock = self._connect((self.host, self.port), self.timeout_s)
                sock.settimeout(self.timeout_s)
                try:
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except (OSError, AttributeError):                          # pragma: no cover - not a TCP socket
                    pass
            except OSError as exc:
                raise ProbeError(f"cannot connect to {self.host}:{self.port}: {exc}", OPEN_FAILED) from exc
            self._sock = sock

    def close(self) -> None:
        with self._lock:
            sock, self._sock = self._sock, None
            if sock is not None:
                try:
                    sock.close()
                except OSError as exc:                                     # pragma: no cover - best effort
                    log.debug("close of %s:%s failed: %s", self.host, self.port, exc)

    def _recv_exact(self, count: int, what: str) -> bytes:
        data = bytearray()
        while len(data) < count:
            try:
                chunk = self._sock.recv(count - len(data))
            except TimeoutError as exc:
                raise ProbeError(f"{what}: no complete answer within {self.timeout_s * 1000:.0f} ms", TIMEOUT,
                                 received=bytes(data)) from exc
            except OSError as exc:
                raise ProbeError(f"{what}: receive failed: {exc}", RECEIVE_FAILED, received=bytes(data)) from exc
            if not chunk:
                raise ProbeError(f"{what}: connection closed by {self.host}:{self.port}", RECEIVE_FAILED,
                                 received=bytes(data))
            data += chunk
        return bytes(data)

    def request(self, unit_id: int, pdu: bytes, *, what: str) -> bytes:
        """Send one PDU, return the answer PDU (exceptions raised as ``100 + code``)."""
        with self._lock:
            if self._sock is None:
                raise ProbeError(f"{what}: not connected", NOT_OPEN)
            self._tid = (self._tid + 1) & 0xFFFF
            tid = self._tid
            frame = struct.pack(">HHHB", tid, 0, len(pdu) + 1, unit_id & 0xFF) + pdu
            try:
                self._sock.sendall(frame)
            except TimeoutError as exc:
                raise ProbeError(f"{what}: send timed out", SEND_FAILED) from exc
            except OSError as exc:
                raise ProbeError(f"{what}: send failed: {exc}", SEND_FAILED) from exc
            header = self._recv_exact(7, what)
            r_tid, r_proto, length, r_unit = struct.unpack(">HHHB", header)
            if length < 2 or length > 254:
                raise ProbeError(f"{what}: invalid MBAP length {length}", BAD_ANSWER, received=header)
            body = self._recv_exact(length - 1, what)
        if r_tid != tid or r_proto != 0 or r_unit != (unit_id & 0xFF):
            raise ProbeError(f"{what}: answer out of step (transaction {r_tid}/{tid}, protocol {r_proto}, unit "
                             f"{r_unit}/{unit_id})", BAD_ANSWER, received=header + body)
        function = pdu[0]
        if body[0] == function | 0x80:
            code = body[1] if len(body) > 1 else 0
            raise ProbeError(f"{what}: Modbus exception {code} ({exception_text(code)})", MODBUS_EXCEPTION_BASE + code,
                             received=header + body)
        if body[0] != function:
            raise ProbeError(f"{what}: answer to function {body[0]}, not {function}", BAD_ANSWER,
                             received=header + body)
        return body

    def _read_registers(self, function: int, address: int, count: int, unit_id: int, what: str) -> list[int]:
        if isinstance(address, bool) or not isinstance(address, int) or not 0 <= address <= 0xFFFF:
            raise ProbeError(f"{what}: address {address!r} is not 0..65535", INVALID_INPUT)
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_REGISTERS \
                or address + count > 0x10000:
            raise ProbeError(f"{what}: quantity {count!r} is not 1..{MAX_REGISTERS} from {address}", INVALID_INPUT)
        body = self.request(unit_id, struct.pack(">BHH", function, address, count), what=what)
        if len(body) < 2 or body[1] != 2 * count or len(body) != 2 + 2 * count:
            raise ProbeError(f"{what}: {len(body) - 2} data bytes for {count} registers", BAD_ANSWER, received=body)
        return list(struct.unpack(f">{count}H", body[2:]))

    def read_holding_registers(self, address: int, count: int = 1, *, unit_id: int = 1) -> list[int]:
        """Function 3."""
        return self._read_registers(FC_READ_HOLDING_REGISTERS, address, count, unit_id,
                                    f"read holding registers {address}+{count}")

    def read_input_registers(self, address: int, count: int = 1, *, unit_id: int = 1) -> list[int]:
        """Function 4."""
        return self._read_registers(FC_READ_INPUT_REGISTERS, address, count, unit_id,
                                    f"read input registers {address}+{count}")
