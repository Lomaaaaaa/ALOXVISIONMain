"""A small Modbus TCP server on loopback for the tests and the field-check rehearsal (functions 3 and 4).

``FakeModbusServer(holding={12298: 2500})`` serves holding register 12298 = 2500 (2.5 V on the BandiT scale).
Missing registers answer exception 2 (illegal data address), other functions exception 1. Fault injection:
:attr:`FakeModbusServer.delay_s` (answer delay), :meth:`FakeModbusServer.drop_next` (no answer),
:meth:`FakeModbusServer.close_next` (connection closed instead of the answer), :meth:`FakeModbusServer.exception_next`.
:attr:`FakeModbusServer.requests` records ``(unit, function, address, count)``.

Command line (rehearsal of ``tools/thermal_check.py``)::

    python -m alox.thermal.modbus_sim --port 5502 --register 12298=2500
"""

from __future__ import annotations

import argparse
import logging
import socket
import struct
import threading
import time
from collections.abc import Sequence
from typing import Self

from .modbus import FC_READ_HOLDING_REGISTERS, FC_READ_INPUT_REGISTERS

__all__ = ["FakeModbusServer", "main"]

log = logging.getLogger("alox.thermal.modbus_sim")


class FakeModbusServer:
    """Serves ``holding`` (function 3) and ``inputs`` (function 4) registers; ``unit_id`` None answers any unit."""

    def __init__(self, holding: dict[int, int] | None = None, inputs: dict[int, int] | None = None, *,
                 host: str = "127.0.0.1", port: int = 0, unit_id: int | None = None) -> None:
        self.holding: dict[int, int] = dict(holding or {})
        self.inputs: dict[int, int] = dict(inputs or {})
        self.unit_id = unit_id
        self.delay_s = 0.0
        self.requests: list[tuple[int, int, int, int]] = []
        self.connections = 0
        self._host, self._port = host, port
        self._lock = threading.Lock()
        self._listener: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._drop = 0
        self._close = 0
        self._exception: int | None = None
        self._clients: list[socket.socket] = []

    # -- life cycle
    def start(self) -> FakeModbusServer:
        self._listener = socket.create_server((self._host, self._port))
        self._listener.settimeout(0.1)
        self._thread = threading.Thread(target=self._accept, name="fake-modbus", daemon=True)
        self._thread.start()
        return self

    def close(self) -> None:
        self._stop.set()
        if self._listener is not None:
            self._listener.close()
        with self._lock:
            clients, self._clients = self._clients, []
        for conn in clients:
            try:
                conn.close()
            except OSError:                                                 # pragma: no cover
                pass
        if self._thread is not None:
            self._thread.join(2.0)

    def __enter__(self) -> Self:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def port(self) -> int:
        assert self._listener is not None, "server not started"
        return int(self._listener.getsockname()[1])

    @property
    def host(self) -> str:
        return self._host

    # -- injection
    def set_register(self, address: int, value: int, *, holding: bool = True) -> None:
        with self._lock:
            (self.holding if holding else self.inputs)[address] = value & 0xFFFF

    def drop_next(self, count: int = 1) -> None:
        with self._lock:
            self._drop += count

    def close_next(self) -> None:
        with self._lock:
            self._close += 1

    def exception_next(self, code: int) -> None:
        with self._lock:
            self._exception = code

    def disconnect_all(self) -> None:
        """Close every client connection (the client sees the link drop)."""
        with self._lock:
            clients, self._clients = self._clients, []
        for conn in clients:
            try:
                conn.shutdown(socket.SHUT_RDWR)
                conn.close()
            except OSError:                                                 # pragma: no cover
                pass

    # -- serving
    def _accept(self) -> None:
        assert self._listener is not None
        while not self._stop.is_set():
            try:
                conn, _ = self._listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with self._lock:
                self.connections += 1
                self._clients.append(conn)
            threading.Thread(target=self._serve, args=(conn,), name="fake-modbus-conn", daemon=True).start()

    @staticmethod
    def _recv(conn: socket.socket, count: int) -> bytes | None:
        data = bytearray()
        while len(data) < count:
            try:
                chunk = conn.recv(count - len(data))
            except OSError:
                return None
            if not chunk:
                return None
            data += chunk
        return bytes(data)

    def _serve(self, conn: socket.socket) -> None:
        with conn:
            while not self._stop.is_set():
                header = self._recv(conn, 7)
                if header is None:
                    return
                tid, proto, length, unit = struct.unpack(">HHHB", header)
                pdu = self._recv(conn, length - 1)
                if pdu is None:
                    return
                answer = self._answer(unit, pdu)
                with self._lock:
                    if self._close:
                        self._close -= 1
                        return
                    if self._drop:
                        self._drop -= 1
                        continue
                if answer is None:
                    continue
                if self.delay_s > 0:
                    time.sleep(self.delay_s)
                try:
                    conn.sendall(struct.pack(">HHHB", tid, proto, len(answer) + 1, unit) + answer)
                except OSError:
                    return

    def _answer(self, unit: int, pdu: bytes) -> bytes | None:
        if self.unit_id is not None and unit != self.unit_id:
            return None                                                     # another unit: no answer
        function = pdu[0]
        with self._lock:
            if self._exception is not None:
                code, self._exception = self._exception, None
                return bytes((function | 0x80, code))
            if function not in (FC_READ_HOLDING_REGISTERS, FC_READ_INPUT_REGISTERS) or len(pdu) != 5:
                return bytes((function | 0x80, 1))
            address, count = struct.unpack(">HH", pdu[1:5])
            self.requests.append((unit, function, address, count))
            table = self.holding if function == FC_READ_HOLDING_REGISTERS else self.inputs
            if not 1 <= count <= 125 or any(a not in table for a in range(address, address + count)):
                return bytes((function | 0x80, 2))
            values = [table[a] & 0xFFFF for a in range(address, address + count)]
        return struct.pack(f">BB{count}H", function, 2 * count, *values)


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m alox.thermal.modbus_sim", description=__doc__.splitlines()[0])
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5502)
    p.add_argument("--register", action="append", default=[], metavar="ADDRESS=VALUE",
                   help="holding register value (repeatable), e.g. 12298=2500")
    args = p.parse_args(argv)
    holding = {}
    for item in args.register:
        address, _, value = item.partition("=")
        holding[int(address)] = int(value)
    logging.basicConfig(level=logging.INFO)
    with FakeModbusServer(holding, host=args.host, port=args.port) as server:
        log.info("Modbus TCP server on %s:%d, holding registers %s (Ctrl+C to stop)", server.host, server.port, holding)
        try:
            while True:
                time.sleep(1.0)
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":                                                  # pragma: no cover
    raise SystemExit(main())
