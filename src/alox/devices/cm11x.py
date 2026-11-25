"""The Spectral Products CM110/CM112 ("CM11x") monochromator: binary RS-232 protocol and serial transport.

Spec: ``docs/vision-core/monochromator.md`` §2 (link), §3 (bytes), §5 (units, gratings, speed table).

The protocol is binary. A command is one command byte followed by its data bytes (16-bit values high byte first); the
instrument answers with a **status byte** and the end byte **24** once the command is complete (a QUERY returns two
data bytes before them). :class:`Cm11x` sends one command at a time and always:

1. discards whatever is waiting in the input buffer (recorded as ``drop``), as every LabVIEW handler does;
2. writes the request;
3. reads the exact number of answer bytes with a deadline (no termination character: a 0x0A byte is data);
4. checks the end byte (24) and then bit 7 of the **status byte** only (data bytes are never tested).

ECHO (27) answers with the byte 27 alone; RESET (255, 255, 255) answers nothing usable: the instrument is polled with
ECHO afterwards. :class:`SerialLink` is the pyserial transport (``serial.serial_for_url``, so ``COM1``,
``/dev/ttyUSB0`` and ``socket://host:port`` all work), 8 data bits, no parity, 1 stop bit, no flow control.

Error codes carried by :class:`Cm11xError` (``code``):

- ``TIMEOUT`` (9403, LabVIEW's "wavelength module command timeout"): no complete answer within the timeout;
- ``OPEN_FAILED`` (−6), ``SEND_FAILED`` (−2), ``RECEIVE_FAILED`` (−3), ``NOT_OPEN`` (−4): transport problems, same
  values as the PI interface codes of the stages;
- ``BAD_ANSWER`` (−8): an answer whose end byte is not 24 or an ECHO that does not return 27 (the link is out of step);
- 128..255: the status byte itself when its bit 7 is set ("command not accepted", as LabVIEW reported it);
- ``INVALID_INPUT`` (1, LabVIEW's "an input parameter is invalid"): an argument this module refuses before sending.

``TIMEOUT``, the transport codes and ``BAD_ANSWER`` mean "the link is unusable" (:data:`LINK_FAILURE_CODES`).
"""

from __future__ import annotations

import collections
import logging
import threading
import time
from collections.abc import Callable, Sequence
from enum import IntEnum
from typing import Any, Protocol

__all__ = [
    "BAD_ANSWER",
    "CMD_CALIBRATE",
    "CMD_ECHO",
    "CMD_GOTO",
    "CMD_QUERY",
    "CMD_RESET",
    "CMD_SCAN",
    "CMD_SELECT",
    "CMD_SPEED",
    "CMD_UNITS",
    "END_BYTE",
    "GROOVES_BY_CODE",
    "INVALID_INPUT",
    "LINK_FAILURE_CODES",
    "MAX_SPEED_AT_1200",
    "NOT_OPEN",
    "OPEN_FAILED",
    "RECEIVE_FAILED",
    "SEND_FAILED",
    "STATUS_NOT_ACCEPTED",
    "TIMEOUT",
    "Cm11x",
    "Cm11xError",
    "Link",
    "LinkFactory",
    "Query",
    "SerialLink",
    "Units",
    "command_text",
    "decode_grooves",
    "describe_status",
    "frames_text",
    "normal_port",
    "pyserial_version",
    "speed_table",
    "split16",
]

log = logging.getLogger(__name__)
frame_log = logging.getLogger("alox.devices.cm11x.frames")       # every frame at DEBUG

# ---------------------------------------------------------------------------------------------- bytes
CMD_SCAN = 12
CMD_SPEED = 13
CMD_GOTO = 16
CMD_CALIBRATE = 18
CMD_SELECT = 26
CMD_ECHO = 27
CMD_UNITS = 50
CMD_QUERY = 56
CMD_RESET = 255
RESET_BYTES = bytes((CMD_RESET, CMD_RESET, CMD_RESET))
END_BYTE = 24                            # "cancel" byte that ends every answer
STATUS_NOT_ACCEPTED = 0x80               # status bit 7: the command was not accepted

# ---------------------------------------------------------------------------------------------- codes
TIMEOUT = 9403
OPEN_FAILED = -6
SEND_FAILED = -2
RECEIVE_FAILED = -3
NOT_OPEN = -4
BAD_ANSWER = -8
INVALID_INPUT = 1
LINK_FAILURE_CODES = frozenset({TIMEOUT, OPEN_FAILED, SEND_FAILED, RECEIVE_FAILED, NOT_OPEN, BAD_ANSWER})

PYSERIAL_MISSING = "pyserial is not installed"

#: QUERY 15 / 17 (grooves of grating 1 / 2) answer a code, not a density (``spectral_products_query d5-d14``).
GROOVES_BY_CODE: dict[int, int] = {0: 3600, 1: 2400, 2: 1800, 3: 1200, 4: 600, 5: 300, 6: 150, 7: 75}
#: Fastest scan speed (Å/s) of a 1200 gr/mm grating; the others scale with 1200 / grooves (manual, spec §5.4).
MAX_SPEED_AT_1200 = 1000
SPEED_LEVELS = 10                        # levels 0 (fastest) .. 9: maximum / 2**level


class Query(IntEnum):
    """QUERY (56) sub-codes = ``typedef_spectral_products_query`` (spec §2.1)."""

    POSITION = 0
    TYPE = 1
    GROOVES = 2                          # of the current grating, in gr/mm
    BLAZE = 3                            # of the current grating, in nm
    GRATING = 4                          # current grating number (1 or 2)
    SPEED = 5                            # scan speed, Å/s
    STEP_SIZE = 6
    ZERO_OFFSET_M1G1 = 7
    ZERO_OFFSET_M1G2 = 8
    ZERO_OFFSET_M2G1 = 9
    ZERO_OFFSET_M2G2 = 10
    CALIBRATION_M1G1 = 11
    CALIBRATION_M1G2 = 12
    GRATINGS = 13                        # number of gratings
    UNITS = 14
    GROOVES_G1 = 15                      # code 0..7, see GROOVES_BY_CODE
    BLAZE_G1 = 16
    GROOVES_G2 = 17                      # code 0..7
    BLAZE_G2 = 18
    SERIAL = 19
    NOVRAM_14 = 20


class Units(IntEnum):
    """UNITS (50) values. LabVIEW's ``typedef_ext_wl_units`` calls 0 "Centimeters"; the CM-series manual is believed to
    say microns (spec §5.1, Q2). The application only uses nanometres."""

    UNIT_0 = 0
    NANOMETERS = 1
    ANGSTROMS = 2


class Cm11xError(Exception):
    """A monochromator operation failed. ``code``: see the module docstring; ``status``: the status byte when the
    instrument refused the command."""

    def __init__(self, message: str, code: int, *, status: int | None = None, received: bytes = b"") -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status
        self.received = received

    @property
    def link_failure(self) -> bool:
        return self.code in LINK_FAILURE_CODES


# ---------------------------------------------------------------------------------------------- helpers
def split16(value: int) -> tuple[int, int]:
    """``(high, low)`` bytes of a 16-bit value; :class:`Cm11xError` (``INVALID_INPUT``) outside 0..65535 (LabVIEW
    dropped the high bits silently, spec D9)."""
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFF:
        raise Cm11xError(f"{value!r} is not a 16-bit value (0..65535)", INVALID_INPUT)
    return value >> 8, value & 0xFF


def decode_grooves(code: int) -> int:
    """gr/mm of a QUERY 15 / 17 code (7 and above: 75)."""
    return GROOVES_BY_CODE.get(code, 75)


def speed_table(grooves: int) -> list[int]:
    """Allowed scan speeds (Å/s) for levels 0..9: ``round(1000 · 1200 / grooves / 2**level)`` (half to even, as
    LabVIEW's Round To Nearest). One rule for every grating (LabVIEW divided by 3 for 1800 gr/mm, spec D4)."""
    if grooves <= 0:
        raise Cm11xError(f"invalid groove density {grooves}", INVALID_INPUT)
    top = MAX_SPEED_AT_1200 * 1200 / grooves
    return [round(top / 2**level) for level in range(SPEED_LEVELS)]


def normal_port(text: str) -> str:
    """``"4"`` → ``"COM4"``, ``"com4"`` → ``"COM4"``; anything else (``/dev/ttyUSB0``, ``socket://…``) unchanged."""
    text = text.strip()
    if text.isdigit():
        return f"COM{int(text)}"
    if text[:3].lower() == "com" and text[3:].isdigit():
        return text.upper()
    return text


def command_text(data: bytes) -> str:
    """A request as text: ``"QUERY 19"``, ``"GOTO 750"``, ``"SELECT 2"``, ``"RESET"``, ``"ECHO"``…"""
    if not data:
        return ""
    cmd, args = data[0], data[1:]

    def word(i: int) -> int:
        return (args[i] << 8) | args[i + 1]

    try:
        if cmd == CMD_QUERY and len(args) == 1:
            name = Query(args[0]).name if args[0] in Query._value2member_map_ else "?"
            return f"QUERY {args[0]} ({name})"
        if cmd == CMD_GOTO and len(args) == 2:
            return f"GOTO {word(0)}"
        if cmd == CMD_SPEED and len(args) == 2:
            return f"SPEED {word(0)}"
        if cmd == CMD_CALIBRATE and len(args) == 2:
            return f"CALIBRATE {word(0)}"
        if cmd == CMD_SCAN and len(args) == 4:
            return f"SCAN {word(0)} {word(2)}"
        if cmd == CMD_UNITS and len(args) == 1:
            return f"UNITS {args[0]}"
        if cmd == CMD_SELECT and len(args) == 1:
            return f"SELECT {args[0]}"
        if data == bytes((CMD_ECHO,)):
            return "ECHO"
        if data == RESET_BYTES:
            return "RESET"
    except IndexError:                                       # pragma: no cover - lengths checked above
        pass
    return "bytes " + data.hex(" ")


def pyserial_version() -> str | None:
    try:
        import serial
    except ImportError:
        return None
    return str(getattr(serial, "__version__", "?"))


# ---------------------------------------------------------------------------------------------- transport
class Link(Protocol):
    """What :class:`Cm11x` needs from a transport."""

    def write(self, data: bytes) -> None: ...

    def read(self, count: int, timeout_s: float) -> bytes:
        """Up to ``count`` bytes, waiting at most ``timeout_s``; fewer only when the time is up."""
        ...

    def flush_input(self) -> bytes:
        """Discard the bytes waiting in the input buffer and return them."""
        ...

    def close(self) -> None: ...


#: ``factory(port, baudrate, timeout_s)`` → an open :class:`Link`.
LinkFactory = Callable[[str, int, float], Link]


class SerialLink:
    """pyserial transport: 8N1, no flow control, binary (no termination character, exact-length reads)."""

    def __init__(self, port: str, baudrate: int = 9600, timeout_s: float = 5.0) -> None:
        try:
            import serial
        except ImportError as exc:
            raise Cm11xError(f"{PYSERIAL_MISSING} (pip install pyserial)", OPEN_FAILED) from exc
        self._serial_module = serial
        self.port = normal_port(port)
        try:
            self._ser = serial.serial_for_url(self.port, baudrate=baudrate, bytesize=serial.EIGHTBITS,
                                              parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE,
                                              xonxoff=False, rtscts=False, dsrdtr=False, timeout=timeout_s,
                                              write_timeout=timeout_s)
        except (serial.SerialException, OSError, ValueError) as exc:
            raise Cm11xError(f"cannot open {self.port}: {exc}", OPEN_FAILED) from exc

    def write(self, data: bytes) -> None:
        try:
            self._ser.write(data)
            self._ser.flush()
        except (self._serial_module.SerialException, OSError) as exc:
            raise Cm11xError(f"write to {self.port} failed: {exc}", SEND_FAILED) from exc

    def read(self, count: int, timeout_s: float) -> bytes:
        deadline = time.monotonic() + max(0.0, timeout_s)
        data = bytearray()
        try:
            while len(data) < count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._ser.timeout = remaining
                chunk = self._ser.read(count - len(data))
                if chunk:
                    data += chunk
        except (self._serial_module.SerialException, OSError) as exc:
            raise Cm11xError(f"read from {self.port} failed: {exc}", RECEIVE_FAILED, received=bytes(data)) from exc
        return bytes(data)

    def flush_input(self) -> bytes:
        try:
            self._ser.timeout = 0
            waiting = self._ser.in_waiting
            data = self._ser.read(waiting) if waiting else b""
            self._ser.reset_input_buffer()
        except (self._serial_module.SerialException, OSError) as exc:
            raise Cm11xError(f"flush of {self.port} failed: {exc}", RECEIVE_FAILED) from exc
        return bytes(data)

    def close(self) -> None:
        try:
            self._ser.close()
        except (self._serial_module.SerialException, OSError) as exc:      # pragma: no cover - best effort
            log.debug("close of %s failed: %s", self.port, exc)


# ---------------------------------------------------------------------------------------------- protocol
class Cm11x:
    """The CM11x command set on one :class:`Link`. Thread-safe (one lock per instance); every method that talks to
    the instrument raises :class:`Cm11xError`.

    ``on_frame(direction, data)`` sees every frame (``"tx"`` sent, ``"rx"`` received, ``"drop"`` input discarded
    before a command); :attr:`frames` keeps the last ``frame_log_size`` of them with a time stamp."""

    def __init__(self, link_factory: LinkFactory | None = None, *, sleep: Callable[[float], None] = time.sleep,
                 monotonic: Callable[[], float] = time.monotonic,
                 on_frame: Callable[[str, bytes], None] | None = None, frame_log_size: int = 1024) -> None:
        self._factory: LinkFactory = link_factory or SerialLink
        self._sleep = sleep
        self._monotonic = monotonic
        self.on_frame = on_frame
        self.frames: collections.deque[tuple[float, str, bytes]] = collections.deque(maxlen=frame_log_size)
        self.lock = threading.RLock()
        self._link: Link | None = None
        self.port = ""
        self.baudrate = 9600
        self.timeout_s = 5.0
        self.last_status: int | None = None

    # ============================================================================================== link
    @property
    def is_open(self) -> bool:
        return self._link is not None

    def open(self, port: str, baudrate: int = 9600, timeout_s: float = 5.0) -> None:
        """Open the port (an open one is closed first, spec D10)."""
        with self.lock:
            self.close()
            self.port, self.baudrate, self.timeout_s = normal_port(port), int(baudrate), float(timeout_s)
            try:
                self._link = self._factory(self.port, self.baudrate, self.timeout_s)
            except Cm11xError:
                raise
            except Exception as exc:                                       # a factory that raises something else
                raise Cm11xError(f"cannot open {self.port}: {exc}", OPEN_FAILED) from exc

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
            text = command_text(data) if direction == "tx" else ""
            frame_log.debug("%s %s %s %s", self.port, direction, data.hex(" "), text)
        if self.on_frame is not None:
            self.on_frame(direction, bytes(data))

    def _need_link(self, what: str) -> Link:
        if self._link is None:
            raise Cm11xError(f"{what}: port not open", NOT_OPEN)
        return self._link

    def _flush(self, link: Link) -> None:
        self._record("drop", link.flush_input())

    def _read(self, link: Link, count: int, timeout_s: float, what: str) -> bytes:
        data = link.read(count, timeout_s)
        self._record("rx", data)
        if len(data) < count:
            got = f"{len(data)} of {count} bytes ({data.hex(' ')})" if data else "nothing"
            raise Cm11xError(f"{what}: no complete answer within {timeout_s * 1000:.0f} ms (received {got})",
                             TIMEOUT, received=data)
        return data

    def transact(self, request: bytes, data_len: int = 0, *, timeout_s: float | None = None,
                 what: str | None = None) -> bytes:
        """Flush, write ``request``, read ``data_len`` data bytes + status + end byte, check them; returns the data
        bytes. :class:`Cm11xError`: ``TIMEOUT``, ``BAD_ANSWER`` (end byte ≠ 24), or the status byte as code when its
        bit 7 is set."""
        what = what or command_text(request)
        limit = self.timeout_s if timeout_s is None else timeout_s
        with self.lock:
            link = self._need_link(what)
            self._flush(link)
            self._record("tx", request)
            link.write(request)
            answer = self._read(link, data_len + 2, limit, what)
        data, status, end = answer[:data_len], answer[data_len], answer[data_len + 1]
        self.last_status = status
        if end != END_BYTE:
            raise Cm11xError(f"{what}: answer {answer.hex(' ')} does not end with 24", BAD_ANSWER, received=answer)
        if status & STATUS_NOT_ACCEPTED:
            raise Cm11xError(f"{what}: command not accepted (status byte 0x{status:02X})", status, status=status,
                             received=answer)
        return data

    # ============================================================================================== commands
    def echo(self, *, timeout_s: float | None = None) -> None:
        """ECHO (27): the instrument returns 27. ``BAD_ANSWER`` for any other byte, ``TIMEOUT`` for none."""
        limit = self.timeout_s if timeout_s is None else timeout_s
        with self.lock:
            link = self._need_link("ECHO")
            self._flush(link)
            self._record("tx", bytes((CMD_ECHO,)))
            link.write(bytes((CMD_ECHO,)))
            answer = self._read(link, 1, limit, "ECHO")
        if answer[0] != CMD_ECHO:
            raise Cm11xError(f"ECHO: answered 0x{answer[0]:02X} instead of 0x1B", BAD_ANSWER, received=answer)

    def query_raw(self, code: int, *, timeout_s: float | None = None) -> int:
        """QUERY (56, code): the 16-bit value as sent (high byte first), not decoded."""
        if isinstance(code, bool) or not isinstance(code, int) or not 0 <= code <= 255:
            raise Cm11xError(f"QUERY: invalid sub-code {code!r}", INVALID_INPUT)
        data = self.transact(bytes((CMD_QUERY, code)), 2, timeout_s=timeout_s)
        return (data[0] << 8) | data[1]

    def query(self, code: int, *, timeout_s: float | None = None) -> int:
        """QUERY with LabVIEW's decoding: the groove codes of QUERY 15 / 17 become gr/mm."""
        value = self.query_raw(code, timeout_s=timeout_s)
        if code in (Query.GROOVES_G1, Query.GROOVES_G2):
            return decode_grooves(value)
        return value

    def goto(self, position: int, *, timeout_s: float | None = None) -> None:
        """GOTO (16, high, low): returns when the instrument reports the end of the move (status, 24)."""
        hi, lo = split16(position)
        self.transact(bytes((CMD_GOTO, hi, lo)), timeout_s=timeout_s)

    def set_speed(self, value: int, *, timeout_s: float | None = None) -> None:
        """SPEED (13, high, low), Å/s; the value must be one of :func:`speed_table` for the current grating."""
        hi, lo = split16(value)
        self.transact(bytes((CMD_SPEED, hi, lo)), timeout_s=timeout_s)

    def set_units(self, units: int, *, timeout_s: float | None = None) -> None:
        """UNITS (50, u)."""
        if int(units) not in Units._value2member_map_:
            raise Cm11xError(f"UNITS: invalid value {units!r} (0, 1, 2)", INVALID_INPUT)
        self.transact(bytes((CMD_UNITS, int(units))), timeout_s=timeout_s)

    def select_grating(self, grating: int, *, timeout_s: float | None = None) -> None:
        """SELECT (26, g): **one** data byte (LabVIEW sent two, spec D3)."""
        if isinstance(grating, bool) or not isinstance(grating, int) or not 1 <= grating <= 255:
            raise Cm11xError(f"SELECT: invalid grating {grating!r}", INVALID_INPUT)
        self.transact(bytes((CMD_SELECT, grating)), timeout_s=timeout_s)

    def calibrate(self, position: int, *, timeout_s: float | None = None) -> None:
        """CALIBRATE (18, high, low): declares the current position to be ``position`` (erases the factory
        calibration; manual: after a ZERO)."""
        hi, lo = split16(position)
        self.transact(bytes((CMD_CALIBRATE, hi, lo)), timeout_s=timeout_s)

    def scan(self, start: int, end: int, *, timeout_s: float | None = None) -> None:
        """SCAN (12, start high, start low, end high, end low) at the current speed; returns at the end of the scan."""
        sh, sl = split16(start)
        eh, el = split16(end)
        self.transact(bytes((CMD_SCAN, sh, sl, eh, el)), timeout_s=timeout_s)

    def reset(self, *, echo_timeout_s: float = 30.0, poll_s: float = 0.5, answer_s: float = 1.0) -> float:
        """RESET (255, 255, 255), then ECHO every ``poll_s`` until the instrument answers 27 (each ECHO waits
        ``answer_s``), at most ``echo_timeout_s``. Returns the seconds until the first echo. LabVIEW stopped this loop
        when the echo did *not* come and dropped its errors (spec D5); here the loop ends on the echo and
        ``TIMEOUT`` is raised when none came. Bytes the instrument sends after RESET are discarded by the flushes."""
        with self.lock:
            link = self._need_link("RESET")
            self._flush(link)
            self._record("tx", RESET_BYTES)
            link.write(RESET_BYTES)
            t0 = self._monotonic()
            last: Cm11xError | None = None
            while True:
                self._sleep(poll_s)
                try:
                    self.echo(timeout_s=answer_s)
                    return self._monotonic() - t0
                except Cm11xError as exc:
                    if exc.code not in (TIMEOUT, BAD_ANSWER):
                        raise
                    last = exc
                if self._monotonic() - t0 >= echo_timeout_s:
                    raise Cm11xError(f"RESET: no echo within {echo_timeout_s:g} s ({last.message if last else ''})",
                                     TIMEOUT)

    def send_raw(self, data: bytes, answer_len: int, *, timeout_s: float | None = None) -> bytes:
        """Engineering: flush, write ``data``, read ``answer_len`` bytes as they come (no check)."""
        limit = self.timeout_s if timeout_s is None else timeout_s
        with self.lock:
            link = self._need_link("raw")
            self._flush(link)
            self._record("tx", data)
            link.write(data)
            answer = link.read(answer_len, limit)
            self._record("rx", answer)
        return answer

    def frames_as_records(self) -> list[dict[str, Any]]:
        return [{"t": round(t, 4), "dir": d, "hex": b.hex(" "), "cmd": command_text(b) if d == "tx" else ""}
                for t, d, b in self.frames]


def describe_status(status: int) -> str:
    """Readable status byte: only bit 7 is documented here (spec §3.3); the others are shown as bits."""
    bits = [f"bit{n}" for n in range(8) if status >> n & 1]
    return f"0x{status:02X}" + (f" ({', '.join(bits)})" if bits else "") + (
        " not accepted" if status & STATUS_NOT_ACCEPTED else "")


def frames_text(frames: Sequence[tuple[float, str, bytes]]) -> list[str]:
    """One line per frame: ``t dir hex  command``."""
    return [f"{t:9.3f} {d:<4} {b.hex(' ')}  {command_text(b) if d == 'tx' else ''}".rstrip() for t, d, b in frames]
