"""An emulated Spectral Products CM110/CM112 monochromator that speaks the binary CM11x protocol.

Spec: ``docs/vision-core/monochromator.md`` §3 (bytes), §5 (units, gratings, speeds). Used three ways:

- :class:`SimLink` (through :func:`sim_link_factory`) is the **simulation backend** of the device: the emulator runs
  in-process behind the same :class:`alox.devices.cm11x.Cm11x` protocol code as the real instrument;
- :class:`Cm11xEmulatorServer` serves it on a loopback TCP port, so ``Cm11x`` on a real :class:`SerialLink` with
  ``socket://127.0.0.1:<port>`` exercises pyserial and the byte stream exactly as on the COM port (tests, rehearsal of
  ``tools/wl_check.py --fake``). Command line::

      python -m alox.devices.cm11x_sim --port 5556 [--slew-nm-s 500] [--log-level DEBUG]

- the model itself (:class:`Cm11xEmulator`) for unit tests: ``feed(bytes)`` returns the answers and when they leave.

Behaviour (emulator choices where the manual was not available are marked *choice*):

- every command answers ``status, 24`` when it is complete; QUERY answers ``high, low, status, 24``; ECHO answers 27;
- GOTO takes ``|target − position| / slew_nm_per_s`` seconds (*choice*, default 500 nm/s), SELECT ``select_s``, SCAN
  the slew to the start then ``|end − start| · 10 / speed`` (speed in Å/s);
- a refused command answers a status byte with bit 7 set and does nothing: 0xC0 for a position above the range (bit 6
  "too large", *choice*), 0x80 otherwise (unknown command, invalid units, grating or speed);
- the range is 0 .. ``max_nm_at_1200 · 1200 / grooves`` nm (*choice*, 1500 nm at 1200 gr/mm);
- UNITS 0 / 1 / 2 report positions in µm / nm / Å (*choice*, spec §5.1);
- RESET makes the instrument busy for ``reset_s`` (bytes received meanwhile are ignored), moves it to 0 and sends
  ``reset_reply`` (nothing by default, *choice*);
- QUERY 15 / 17 answer the groove *code* (0 = 3600 … 7 = 75 gr/mm).

Fault injection: :attr:`Cm11xEmulator.answer_delay_s`, :meth:`drop_next_answer`, :meth:`fail_next`,
:meth:`bad_end_next`, :meth:`status_next`, :meth:`stop_short_next`, :meth:`inject`, :meth:`disconnect`,
:attr:`Cm11xEmulator.port_missing`, :attr:`Cm11xEmulator.link_broken`.
"""

from __future__ import annotations

import argparse
import logging
import select
import signal
import socket
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Self

from alox.devices.cm11x import (
    CMD_CALIBRATE,
    CMD_ECHO,
    CMD_GOTO,
    CMD_QUERY,
    CMD_RESET,
    CMD_SCAN,
    CMD_SELECT,
    CMD_SPEED,
    CMD_UNITS,
    END_BYTE,
    GROOVES_BY_CODE,
    OPEN_FAILED,
    RECEIVE_FAILED,
    SEND_FAILED,
    Cm11x,
    Cm11xError,
    Query,
    command_text,
    speed_table,
)

__all__ = [
    "DEFAULT_PORT",
    "Cm11xEmulator",
    "Cm11xEmulatorServer",
    "Grating",
    "SimLink",
    "SimOptions",
    "build_sim_cm11x",
    "main",
    "sim_link_factory",
]

log = logging.getLogger("alox.devices.cm11x_sim")

DEFAULT_PORT = 5556
STATUS_OK = 0x00
STATUS_REFUSED = 0x80
STATUS_TOO_LARGE = 0xC0                  # bit 7 + bit 6 (choice)
#: Bytes of each request, command byte included (RESET is 255, 255, 255).
REQUEST_LENGTH = {CMD_SCAN: 5, CMD_SPEED: 3, CMD_GOTO: 3, CMD_CALIBRATE: 3, CMD_SELECT: 2, CMD_ECHO: 1,
                  CMD_UNITS: 2, CMD_QUERY: 2, CMD_RESET: 3}
_CODE_BY_GROOVES = {v: k for k, v in GROOVES_BY_CODE.items()}
_UNIT_FACTOR = {0: 0.001, 1: 1.0, 2: 10.0}          # reported value = nm · factor
_POLL_S = 0.05


@dataclass(frozen=True)
class Grating:
    grooves: int                         # gr/mm
    blaze: int                           # nm


@dataclass(frozen=True)
class SimOptions:
    """The emulated instrument. Defaults: two gratings 1200 gr/mm (blaze 750 nm) and 600 gr/mm (blaze 1250 nm), at
    0 nm, in nanometres, serial number 12345."""

    serial_number: int = 12345
    type_code: int = 1
    gratings: tuple[Grating, ...] = (Grating(1200, 750), Grating(600, 1250))
    grating: int = 1
    position_nm: float = 0.0
    units: int = 1
    speed: int = 1000                    # Å/s
    step_size: int = 1
    novram_14: int = 0
    zero_offsets: tuple[int, int, int, int] = (0, 0, 0, 0)
    calibrations: tuple[int, int] = (10000, 10000)
    slew_nm_per_s: float = 500.0
    select_s: float = 0.5
    reset_s: float = 2.0
    reset_reply: bytes = b""
    max_nm_at_1200: int = 1500


class Cm11xEmulator:
    """The CM11x byte protocol over a small model of the instrument. Thread-safe.

    :meth:`feed` takes the bytes a client sends and returns ``[(release_time, answer), ...]``: when (``clock`` time)
    each answer leaves the instrument. Answers leave in order: a command received before the previous answer left is
    executed after it, as on the real instrument."""

    def __init__(self, options: SimOptions | None = None, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.options = options or SimOptions()
        self._clock = clock
        self._lock = threading.RLock()
        o = self.options
        self.position_nm = float(o.position_nm)
        self.units = int(o.units)
        self.grating = int(o.grating)
        self.speed = int(o.speed)
        self.serial_number = int(o.serial_number)
        self.answer_delay_s = 0.0
        self.port_missing = False         # SimLink: opening fails (OPEN_FAILED)
        self.link_broken = False          # SimLink: writes and reads fail (SEND_FAILED / RECEIVE_FAILED)
        self.received: list[bytes] = []   # every request parsed, in order
        self.frames: deque[tuple[str, bytes]] = deque(maxlen=4096)
        self._buffer = bytearray()
        self._busy_until = float("-inf")
        self._last_release = float("-inf")
        self._drop_next = False
        self._fail_next: tuple[int, int] | None = None
        self._bad_end_next = False
        self._status_next: int | None = None
        self._stop_short_next: float | None = None
        self._injected: list[bytes] = []
        self._disconnect = False

    # ============================================================================================== state
    @property
    def grooves(self) -> int:
        return self.options.gratings[self.grating - 1].grooves

    @property
    def blaze(self) -> int:
        return self.options.gratings[self.grating - 1].blaze

    @property
    def max_nm(self) -> float:
        return self.options.max_nm_at_1200 * 1200 / self.grooves

    @property
    def commands(self) -> list[str]:
        """The requests received, as text (``"QUERY 19 (SERIAL)"``, ``"GOTO 750"``…)."""
        with self._lock:
            return [command_text(r) for r in self.received]

    def reported(self, nm: float) -> int:
        return round(nm * _UNIT_FACTOR[self.units])

    def to_nm(self, value: int) -> float:
        return value / _UNIT_FACTOR[self.units]

    # ============================================================================================== injection
    def drop_next_answer(self) -> None:
        """The next answer is not sent (the command is executed)."""
        with self._lock:
            self._drop_next = True

    def fail_next(self, command: int, status: int = STATUS_REFUSED) -> None:
        """The next request starting with ``command`` is refused with ``status`` (nothing executed)."""
        with self._lock:
            self._fail_next = (command, status)

    def bad_end_next(self) -> None:
        """The next answer ends with 0 instead of 24."""
        with self._lock:
            self._bad_end_next = True

    def status_next(self, status: int) -> None:
        """The next answer carries this status byte (bit 7 clear: the command is executed normally)."""
        with self._lock:
            self._status_next = status

    def stop_short_next(self, delta_nm: float) -> None:
        """The next GOTO ends ``delta_nm`` before its target (answered normally)."""
        with self._lock:
            self._stop_short_next = float(delta_nm)

    def inject(self, data: bytes) -> None:
        """Unsolicited bytes, available to the client at once (noise, a late answer)."""
        with self._lock:
            self._injected.append(bytes(data))

    def take_injected(self) -> bytes:
        with self._lock:
            data, self._injected = b"".join(self._injected), []
        return data

    def disconnect(self) -> None:
        """The server drops the connection (the instrument stays as it is)."""
        with self._lock:
            self._disconnect = True

    def take_disconnect_request(self) -> bool:
        with self._lock:
            request, self._disconnect = self._disconnect, False
        return request

    def reset_input(self) -> None:
        """Forget a partial request (new connection)."""
        with self._lock:
            self._buffer.clear()

    # ============================================================================================== protocol
    def feed(self, data: bytes) -> list[tuple[float, bytes]]:
        out: list[tuple[float, bytes]] = []
        with self._lock:
            now = self._clock()
            if now < self._busy_until:                 # resetting: the instrument does not listen
                self.frames.append(("ignored", bytes(data)))
                return out
            self.frames.append(("rx", bytes(data)))
            self._buffer += data
            while self._buffer:
                cmd = self._buffer[0]
                need = REQUEST_LENGTH.get(cmd, 1)
                if cmd == CMD_RESET and any(b != CMD_RESET for b in self._buffer[1:3]):
                    need = 1                            # a lone 255 (not 255, 255, 255): refused below
                if len(self._buffer) < need:
                    break
                request = bytes(self._buffer[:need])
                del self._buffer[:need]
                self.received.append(request)
                start = max(now, self._last_release)
                duration, answer = self._execute(request, start)
                if answer is None:
                    continue
                release = start + duration + self.answer_delay_s
                self._last_release = release
                if self._drop_next:
                    self._drop_next = False
                    self.frames.append(("dropped", answer))
                    continue
                self.frames.append(("tx", answer))
                out.append((release, answer))
        return out

    def _answer(self, status: int, data: bytes = b"") -> bytes:
        if self._status_next is not None:
            status, self._status_next = self._status_next, None
        end = END_BYTE
        if self._bad_end_next:
            self._bad_end_next, end = False, 0
        return data + bytes((status, end))

    def _execute(self, request: bytes, start: float) -> tuple[float, bytes | None]:
        """``(duration_s, answer)``; answer None = nothing sent."""
        cmd, args = request[0], request[1:]
        if self._fail_next is not None and self._fail_next[0] == cmd:
            status = self._fail_next[1]
            self._fail_next = None
            return 0.0, self._answer(status, b"\x00\x00" if cmd == CMD_QUERY else b"")
        if cmd == CMD_ECHO:
            return 0.0, bytes((CMD_ECHO,))
        if request == bytes((CMD_RESET,) * 3):
            self._busy_until = start + self.options.reset_s
            self.position_nm = 0.0
            return self.options.reset_s, (self.options.reset_reply or None)
        if cmd not in REQUEST_LENGTH or cmd == CMD_RESET:
            return 0.0, self._answer(STATUS_REFUSED)
        word = (args[0] << 8 | args[1]) if len(args) >= 2 else 0
        if cmd == CMD_QUERY:
            value = self._query(args[0])
            if value is None:
                return 0.0, self._answer(STATUS_REFUSED, b"\x00\x00")
            return 0.0, self._answer(STATUS_OK, bytes((value >> 8 & 0xFF, value & 0xFF)))
        if cmd == CMD_GOTO:
            return self._goto(self.to_nm(word))
        if cmd == CMD_UNITS:
            if args[0] not in _UNIT_FACTOR:
                return 0.0, self._answer(STATUS_REFUSED)
            self.units = args[0]
            return 0.0, self._answer(STATUS_OK)
        if cmd == CMD_SPEED:
            if word not in speed_table(self.grooves):
                return 0.0, self._answer(STATUS_REFUSED)
            self.speed = word
            return 0.0, self._answer(STATUS_OK)
        if cmd == CMD_SELECT:
            if not 1 <= args[0] <= len(self.options.gratings):
                return 0.0, self._answer(STATUS_REFUSED)
            changed = args[0] != self.grating
            self.grating = args[0]
            return (self.options.select_s if changed else 0.0), self._answer(STATUS_OK)
        if cmd == CMD_CALIBRATE:
            self.position_nm = self.to_nm(word)
            return 0.0, self._answer(STATUS_OK)
        if cmd == CMD_SCAN:
            start_nm, end_nm = self.to_nm(word), self.to_nm(args[2] << 8 | args[3])
            if max(start_nm, end_nm) > self.max_nm:
                return 0.0, self._answer(STATUS_TOO_LARGE)
            slew = abs(start_nm - self.position_nm) / self.options.slew_nm_per_s
            sweep = abs(end_nm - start_nm) * 10.0 / max(1, self.speed)
            self.position_nm = end_nm
            return slew + sweep, self._answer(STATUS_OK)
        return 0.0, self._answer(STATUS_REFUSED)                          # pragma: no cover - all handled above

    def _goto(self, target_nm: float) -> tuple[float, bytes]:
        if target_nm > self.max_nm:
            return 0.0, self._answer(STATUS_TOO_LARGE)
        final = target_nm
        if self._stop_short_next is not None:
            final, self._stop_short_next = target_nm - self._stop_short_next, None
        duration = abs(final - self.position_nm) / self.options.slew_nm_per_s
        self.position_nm = final
        return duration, self._answer(STATUS_OK)

    def _query(self, code: int) -> int | None:
        o = self.options
        g1 = o.gratings[0]
        g2 = o.gratings[1] if len(o.gratings) > 1 else o.gratings[0]
        values = {
            Query.POSITION: self.reported(self.position_nm),
            Query.TYPE: o.type_code,
            Query.GROOVES: self.grooves,
            Query.BLAZE: self.blaze,
            Query.GRATING: self.grating,
            Query.SPEED: self.speed,
            Query.STEP_SIZE: o.step_size,
            Query.ZERO_OFFSET_M1G1: o.zero_offsets[0],
            Query.ZERO_OFFSET_M1G2: o.zero_offsets[1],
            Query.ZERO_OFFSET_M2G1: o.zero_offsets[2],
            Query.ZERO_OFFSET_M2G2: o.zero_offsets[3],
            Query.CALIBRATION_M1G1: o.calibrations[0],
            Query.CALIBRATION_M1G2: o.calibrations[1],
            Query.GRATINGS: len(o.gratings),
            Query.UNITS: self.units,
            Query.GROOVES_G1: _CODE_BY_GROOVES.get(g1.grooves, 7),
            Query.BLAZE_G1: g1.blaze,
            Query.GROOVES_G2: _CODE_BY_GROOVES.get(g2.grooves, 7),
            Query.BLAZE_G2: g2.blaze,
            Query.SERIAL: self.serial_number,
            Query.NOVRAM_14: o.novram_14,
        }
        value = values.get(code)                                           # type: ignore[call-overload]
        return None if value is None else int(value) & 0xFFFF


# ---------------------------------------------------------------------------------------------- in-process link
class SimLink:
    """A :class:`alox.devices.cm11x.Link` to a :class:`Cm11xEmulator` in the same process. ``sleep``/``monotonic``
    must be the emulator's clock (a fake clock makes every move instantaneous)."""

    def __init__(self, emulator: Cm11xEmulator, *, sleep: Callable[[float], None] = time.sleep,
                 monotonic: Callable[[], float] = time.monotonic, name: str = "SIM") -> None:
        self.emulator = emulator
        self.name = name
        self._sleep = sleep
        self._monotonic = monotonic
        self._pending: list[tuple[float, bytes]] = []
        self._ready = bytearray()
        self._closed = False

    def _check(self, code: int, what: str) -> None:
        if self._closed:
            raise Cm11xError(f"{what}: link closed", code)
        if self.emulator.link_broken:
            raise Cm11xError(f"{what} on {self.name} failed: link broken (simulated)", code)

    def _collect(self) -> None:
        now = self._monotonic()
        self._ready += self.emulator.take_injected()
        keep = []
        for release, data in self._pending:
            if release <= now:
                self._ready += data
            else:
                keep.append((release, data))
        self._pending = keep

    def write(self, data: bytes) -> None:
        self._check(SEND_FAILED, "write")
        self._collect()
        self._pending.extend(self.emulator.feed(bytes(data)))
        self._pending.sort(key=lambda item: item[0])

    def read(self, count: int, timeout_s: float) -> bytes:
        self._check(RECEIVE_FAILED, "read")
        deadline = self._monotonic() + max(0.0, timeout_s)
        while True:
            self._collect()
            if len(self._ready) >= count:
                break
            now = self._monotonic()
            if now >= deadline:
                break
            nxt = self._pending[0][0] if self._pending else deadline
            self._sleep(max(0.0, min(nxt, deadline) - now))
        data = bytes(self._ready[:count])
        del self._ready[:count]
        return data

    def flush_input(self) -> bytes:
        self._check(RECEIVE_FAILED, "flush")
        self._collect()
        data = bytes(self._ready)
        self._ready.clear()
        return data

    def close(self) -> None:
        self._closed = True


def sim_link_factory(emulator: Cm11xEmulator, *, sleep: Callable[[float], None] = time.sleep,
                     monotonic: Callable[[], float] = time.monotonic) -> Callable[[str, int, float], SimLink]:
    """A :data:`alox.devices.cm11x.LinkFactory` that opens :class:`SimLink` s on ``emulator`` (``OPEN_FAILED`` when
    :attr:`Cm11xEmulator.port_missing`)."""

    def open_link(port: str, baudrate: int, timeout_s: float) -> SimLink:
        if emulator.port_missing:
            raise Cm11xError(f"cannot open {port}: no such port (simulated)", OPEN_FAILED)
        emulator.reset_input()
        return SimLink(emulator, sleep=sleep, monotonic=monotonic, name=port)

    return open_link


def build_sim_cm11x(options: SimOptions | None = None, *, sleep: Callable[[float], None] = time.sleep,
                    monotonic: Callable[[], float] = time.monotonic) -> tuple[Cm11x, Cm11xEmulator]:
    """A :class:`Cm11x` on a new emulator (simulation backend); returns both."""
    emulator = Cm11xEmulator(options, clock=monotonic)
    return Cm11x(sim_link_factory(emulator, sleep=sleep, monotonic=monotonic), sleep=sleep,
                 monotonic=monotonic), emulator


# ---------------------------------------------------------------------------------------------- TCP server
class Cm11xEmulatorServer:
    """A :class:`Cm11xEmulator` on a loopback TCP port, one connection at a time; the instrument state survives
    reconnections. ``port=0`` picks a free port; :attr:`url` is ``"socket://<host>:<port>"`` for
    :class:`alox.devices.cm11x.SerialLink` (pyserial ``serial_for_url``)."""

    def __init__(self, options: SimOptions | None = None, *, host: str = "127.0.0.1", port: int = 0) -> None:
        self.emulator = Cm11xEmulator(options)
        self._host = host
        self._requested_port = int(port)
        self._listener: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._conn: socket.socket | None = None
        self._connections = 0

    def start(self) -> Cm11xEmulatorServer:
        if self._listener is not None:
            return self
        self._listener = socket.create_server((self._host, self._requested_port))
        self._thread = threading.Thread(target=self._run, name=f"cm11x-emulator-{self.port}", daemon=True)
        self._thread.start()
        log.info("CM11x emulator listening on %s", self.url)
        return self

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            conn = self._conn
        if conn is not None:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(5.0)
            self._thread = None
        if self._listener is not None:
            self._listener.close()

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def address(self) -> tuple[str, int]:
        if self._listener is None:
            raise RuntimeError("Cm11xEmulatorServer not started")
        host, port = self._listener.getsockname()[:2]
        return str(host), int(port)

    @property
    def port(self) -> int:
        return self.address[1]

    @property
    def url(self) -> str:
        host, port = self.address
        return f"socket://{host}:{port}"

    @property
    def connections(self) -> int:
        with self._lock:
            return self._connections

    def disconnect(self) -> None:
        self.emulator.disconnect()

    def _run(self) -> None:
        listener = self._listener
        assert listener is not None
        while not self._stop.is_set():
            try:
                readable, _, _ = select.select([listener], [], [], _POLL_S)
            except (OSError, ValueError):
                break
            if not readable:
                continue
            try:
                conn, peer = listener.accept()
            except OSError:
                continue
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.emulator.reset_input()
            self.emulator.take_disconnect_request()
            with self._lock:
                self._conn = conn
                self._connections += 1
            log.info("CM11x emulator: client %s connected", peer)
            try:
                self._serve(conn)
            except Exception:                                   # never let the thread die silently
                log.exception("CM11x emulator: connection failed")
            finally:
                with self._lock:
                    self._conn = None
                conn.close()
                log.info("CM11x emulator: client %s disconnected", peer)

    def _serve(self, conn: socket.socket) -> None:
        emulator = self.emulator
        while not self._stop.is_set():
            if emulator.take_disconnect_request():
                log.info("CM11x emulator: injected disconnect")
                return
            injected = emulator.take_injected()
            if injected:
                conn.sendall(injected)
            readable, _, _ = select.select([conn], [], [], _POLL_S)
            if not readable:
                continue
            try:
                data = conn.recv(4096)
            except OSError:
                return
            if not data:
                return
            for release, answer in emulator.feed(data):
                delay = release - time.monotonic()
                if delay > 0 and self._stop.wait(delay):
                    return
                if emulator.take_disconnect_request():
                    log.info("CM11x emulator: injected disconnect")
                    return
                try:
                    conn.sendall(answer)
                except OSError:
                    return


# ---------------------------------------------------------------------------------------------- command line
def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m alox.devices.cm11x_sim",
                                description="An emulated Spectral Products CM110/CM112 monochromator on a TCP port "
                                            "(binary CM11x protocol), for rehearsals: use socket://127.0.0.1:<port> "
                                            "as the port.")
    p.add_argument("--host", default="127.0.0.1", help="listen address (default 127.0.0.1)")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"TCP port (default {DEFAULT_PORT}; 0 = any)")
    p.add_argument("--position-nm", type=float, default=0.0, help="start position (default 0 nm)")
    p.add_argument("--serial", type=int, default=12345, help="serial number (QUERY 19, default 12345)")
    p.add_argument("--slew-nm-s", type=float, default=500.0, help="GOTO speed (default 500 nm/s)")
    p.add_argument("--reset-s", type=float, default=2.0, help="duration of a RESET (default 2 s)")
    p.add_argument("--log-level", type=str.upper, default="INFO", choices=("DEBUG", "INFO", "WARNING"))
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    options = SimOptions(serial_number=args.serial, position_nm=args.position_nm,
                         slew_nm_per_s=max(1e-3, args.slew_nm_s), reset_s=max(0.0, args.reset_s))
    try:
        server = Cm11xEmulatorServer(options, host=args.host, port=args.port).start()
    except OSError as exc:
        print(f"cannot listen on {args.host}:{args.port}: {exc}", flush=True)
        return 1
    print(f"CM11x emulator listening on {server.url}", flush=True)
    print("Ctrl+C stops it.", flush=True)
    stopped = threading.Event()
    try:
        signal.signal(signal.SIGTERM, lambda _signum, _frame: stopped.set())
    except (ValueError, OSError):
        pass
    try:
        while not stopped.wait(0.5):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        server.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
