"""``GcsChain``: several PI GCS 2.0 controllers (C-663) on ONE RS-232 port in a daisy chain, one link view each.

The XY stages are two C-663 controllers on one port: X = device 1 (master), Y = device 2 (map Part 2 §2.8; LabVIEW
opens them with the PI driver option "RS232 DaisyChain"). :class:`GcsChain` owns the single
:class:`aloxcam.zstage.gcs_serial.GcsSerialLink` to that port and hands out :class:`ChainLink` views — one per
controller, with the interface ``PiSerialStage`` uses from ``GcsSerialLink`` — so the tested
``PiSerialStage`` + ``ZController`` pair drives each controller unchanged::

    chain = GcsChain("COM4", 115200, 5.0)
    x = ZController(PiSerialStage(link_factory=chain.link_factory(1)), options)
    y = ZController(PiSerialStage(link_factory=chain.link_factory(2)), options)

Protocol research (2026-11). The PI manuals could only be read through search-engine excerpts (their hosts are not
reachable from the development environment); the rest comes from source code that was read in full.

- [M] PI C-863.12 user manual and C-663.11 user manual MS208E (§9.1.3 "Target and Sender Address", p.75):
  https://www.physikinstrumente.com/en/?type=5600&downloadUid=9093&downloadFileUid=3762 ,
  https://www.le.infn.it/~chiodini/allow_listing/pi/Manuals/C-663.11_User_MS208E_100.pdf
- [D] PI GCS 2.0 DLL manual SM151E (``PI_OpenRS232DaisyChain``, ``PI_ConnectDaisyChainDevice``):
  https://www.le.infn.it/~chiodini/allow_listing/pi/Manuals/PIGCS_2_0_DLL_SM151E210.pdf
- [O] delmic/odemis ``src/odemis/driver/pigcs.py`` (raw-serial GCS driver with daisy chains, and its simulator):
  https://github.com/delmic/odemis/blob/master/src/odemis/driver/pigcs.py
- [P] pipython 2.14.0.0 (PyPI sdist: ``pidevice/interfaces/gcsdll.py``, ``samples/connect_daisychain.py``, readme).
- [S] third-party C-863 status-register notes: http://crasy.org/WP/archives/854-11-2015-xk3
- [E] EPICS tech-talk 2025 (a site-local motorPIGCS2 daisy-chain class "prefixes the controller address onto each
  command sent, and strips it off for the replies"): https://epics.anl.gov/tech-talk/2025/msg00838.php

Confirmed by documentation ([M], [D]):

1. Controller addresses are 1–16 (DIP switches), the PC is address 0, 255 is the broadcast address (all controllers,
   no answers); a chain needs a controller with address 1 ([M], [P] readme, [O]).
2. A command is ``[<target> [<sender>]] <command>``. The target may be omitted for the controller with address 1,
   even in a chain, and its answer then carries no addresses; with a target, the answer starts with the addresses
   (``0 <target>``). Only the first line of a multi-line answer carries them ([M]).
3. ``PI_OpenRS232DaisyChain(port, baud, &n, idns, size)`` returns the ``*IDN?`` strings of the controllers found
   (−1 when none answers), then ``PI_ConnectDaisyChainDevice(chain, device)`` attaches one device ([D], [P]): the DLL
   queries the chain at open; the exact frames are not documented.

Confirmed in practice by an open-source driver, not by PI documentation ([O], [E]):

4. ``"<target> <command>"`` (no sender) is accepted, and the answer starts with ``"0 <target> "`` ([O] sends
   ``"%d %s" % (addr, com)`` and strips ``b"0 %d " % addr``; its simulator also accepts ``"<target> 0 "``).
5. The single-character commands are addressed the same way, without LF: ``"2 \\x05"``, ``"2 \\x07"``,
   ``"2 \\x18"``; ``#5`` and ``#7`` answer a prefixed line (``"0 2 1\\n"``, ``"0 2 \\xb1\\n"``) ([O]).

Assumed (configurable here, to be checked on the real chain by the check tool):

- ``address_mode``: ``"target_only"`` (default, [O]) sends ``"2 POS? 1"``; ``"target_sender"`` sends
  ``"2 0 POS? 1"`` ([M] syntax, sender optional). Answers must start with ``"0 <device> "`` in both modes; any other
  address pair is an error (−3), never silently accepted.
- ``char_mode``: ``"addressed"`` (default, [O]) sends ``"<device> "`` + the byte. ``"text_equivalents"`` replaces
  them for controllers that do not accept addressed single bytes: ``#5`` → ``SRG? 1 1`` bit 13 "in motion"
  (answered as the ``#5`` mask ``"1"``/``"0"``), ``#7`` → ``SRG? 1 1`` bit 14 "is referencing" (answered as 0xB0
  busy / 0xB1 ready), ``#24`` → ``STP`` (both "stop all axes", both latch error 10). The flags exist on the C-863.12
  ([M]); bits 12 (servo), 13 (in motion) and 15 (on target) are from [S]; bit 14 is the remaining flag (assumed). The
  masks are constructor arguments.
- Whether the addressed ``#7`` answer ends with LF: both are read (prefix + one byte; an LF that follows is consumed,
  or dropped silently before the next exchange).
- An empty answer (``RMC?`` with no macro): ``"0 2 \\n"`` and ``"0 2\\n"`` are both read as ``""`` — the address
  prefix is removed before the multi-line test, otherwise ``"0 2 \\n"`` would look like a continuation line.
- Nothing chain-specific is sent at open: the shared port is opened (input buffer cleared) and each
  ``PiSerialStage`` runs its own purge (``XXX``, ``*idn?``, ``err?``) addressed to its device. ``*IDN?``, ``ERR?``
  and the purge therefore go to one device each; the PI DLL's own open-time scan is not reproduced.

Thread contract: :attr:`GcsChain.lock` (an RLock) serialises every exchange on the port — each view call takes it
for a whole request/answer, so frames of two controllers never interleave on the wire, whichever threads drive them.
A background task (the wafer-limit supervision) that must group several exchanges takes ``chain.lock`` around them.
Lock order: a ``ZController`` lock first, then ``chain.lock``; never call a ``ZController`` while holding
``chain.lock`` (the other controller's thread may hold its ``ZController`` lock and wait for the chain). Separate
``send()`` / ``receive()`` calls are not atomic together: use ``query()``, or hold ``chain.lock``.

Port life cycle: the first view opens the port, the last one to close closes it. A view never holds the port object:
after a broken port (−2 send error or −3 read error, e.g. the Moxa connection dropped), the next :meth:`GcsChain.view`
— the next ``Z Setup`` of either controller — re-opens the port, and the other controller's open view uses the new
port without reconnecting. Timeouts (−7) do not mark the port broken.

Implementation note: the views write and read through ``GcsSerialLink``'s internal helpers (``_write``,
``_require``, ``_record``), so the frame logging, the stale-input drop and the error texts stay those of the single
controller link; the shared link keeps the raw log of both devices (:attr:`GcsChain.frames`), each view its own.
"""

from __future__ import annotations

import collections
import logging
import re
import threading
from collections.abc import Callable
from typing import Any, Self

from aloxcam.errors import Code, StageError
from aloxcam.zstage.gcs_serial import (
    BUSY_BYTE,
    CHAR_MOVING,
    CHAR_READY,
    CHAR_STOP_ALL,
    DEFAULT_ENCODING,
    DROPPED,
    FRAME_LOG_SIZE,
    READY_BYTE,
    RX,
    TX,
    GcsSerialLink,
)

__all__ = [
    "ADDRESS_MODES",
    "BROADCAST_ADDRESS",
    "CHAR_MODES",
    "DEFAULT_ADDRESS_MODE",
    "DEFAULT_CHAR_MODE",
    "MAX_ADDRESS",
    "MIN_ADDRESS",
    "PC_ADDRESS",
    "SRG_IN_MOTION",
    "SRG_REFERENCING",
    "ChainLink",
    "GcsChain",
    "answer_prefix",
    "command_prefix",
    "parse_register",
]

log = logging.getLogger(__name__)

ADDRESS_MODES = ("target_only", "target_sender")
CHAR_MODES = ("addressed", "text_equivalents")
DEFAULT_ADDRESS_MODE = "target_only"     # [O]: "2 POS? 1"
DEFAULT_CHAR_MODE = "addressed"          # [O]: "2 \x05"
PC_ADDRESS = 0
BROADCAST_ADDRESS = 255                  # every controller, no answer (not offered as a view)
MIN_ADDRESS, MAX_ADDRESS = 1, 16
SRG_AXIS, SRG_REGISTER = "1", "1"        # SRG? <axis> <register>: the C-663's only axis
SRG_IN_MOTION = 1 << 13                  # [S]
SRG_REFERENCING = 1 << 14                # assumed: the "is referencing" flag of [M]

_LF = b"\n"
_CONTINUATION = b" \n"
#: "<target> <sender>" at the start of an answer, then a space, or nothing before the LF ("0 2\n": empty answer).
_ANSWER_ADDRESSES = re.compile(rb"(\d+) (\d+)(?: |(?=\n))")

Link = Any                               # a GcsSerialLink (or a subclass, e.g. a test's synchronised link)


def command_prefix(address: int, address_mode: str = DEFAULT_ADDRESS_MODE) -> str:
    """``"2 "`` (``target_only``) or ``"2 0 "`` (``target_sender``)."""
    if address_mode == "target_sender":
        return f"{address} {PC_ADDRESS} "
    return f"{address} "


def answer_prefix(address: int) -> bytes:
    """``b"0 2 "``: the addresses an answer of device ``address`` starts with (to the PC, from the device)."""
    return f"{PC_ADDRESS} {address} ".encode("ascii")


def parse_register(answer: str) -> int:
    """The value of a ``SRG?`` answer (``"1 1=0x9004"``): the hexadecimal number after the last ``=``.
    ``StageError(100)`` when there is none."""
    _, sep, value = answer.strip().rpartition("=")
    try:
        if not sep:
            raise ValueError(answer)
        return int(value.strip(), 16)            # int() accepts the "0x" prefix with base 16
    except ValueError:
        raise StageError(Code.PI_DRIVER, f"SRG?: unexpected answer {answer!r}") from None


def _check_address(address: object) -> int:
    if isinstance(address, bool) or not isinstance(address, int) or not MIN_ADDRESS <= address <= MAX_ADDRESS:
        raise ValueError(f"daisy-chain device address must be an int in {MIN_ADDRESS}..{MAX_ADDRESS}: {address!r}")
    return address


def _same_port(a: str, b: str) -> bool:
    """``"COM4"`` and ``"com4"`` name the same port (Windows names are case-insensitive)."""
    return str(a).strip().casefold() == str(b).strip().casefold()


class GcsChain:
    """One RS-232 port shared by the controllers of a daisy chain (see the module docstring).

    ``link_factory(port, baudrate, timeout_s)`` opens the shared link (:class:`GcsSerialLink`, whose ``timeout_s`` is
    the read timeout of every view). ``address_mode`` / ``char_mode`` select the assumed syntax variants
    (:data:`ADDRESS_MODES`, :data:`CHAR_MODES`); ``in_motion_mask`` / ``busy_mask`` are the ``SRG?`` bits used by
    ``char_mode="text_equivalents"``.
    """

    lock: threading.RLock

    def __init__(self, port: str, baudrate: int, timeout_s: float, *,
                 link_factory: Callable[[str, int, float], Link] = GcsSerialLink,
                 address_mode: str = DEFAULT_ADDRESS_MODE, char_mode: str = DEFAULT_CHAR_MODE,
                 in_motion_mask: int = SRG_IN_MOTION, busy_mask: int = SRG_REFERENCING) -> None:
        if address_mode not in ADDRESS_MODES:
            raise ValueError(f"address_mode must be one of {ADDRESS_MODES}: {address_mode!r}")
        if char_mode not in CHAR_MODES:
            raise ValueError(f"char_mode must be one of {CHAR_MODES}: {char_mode!r}")
        self.port = str(port)
        self.baudrate = int(baudrate)
        self.timeout_s = max(0.0, float(timeout_s))
        self.address_mode = address_mode
        self.char_mode = char_mode
        self.in_motion_mask = int(in_motion_mask)
        self.busy_mask = int(busy_mask)
        self.lock = threading.RLock()
        self._link_factory = link_factory
        self._link: Link | None = None
        self._frames: collections.deque[tuple[str, bytes]] = collections.deque(maxlen=FRAME_LOG_SIZE)
        self._views: list[ChainLink] = []
        self._opens = 0
        self._broken = False
        self._after_ready = False                # an addressed #7 answer was read: one lone LF may follow

    def __repr__(self) -> str:
        return (f"GcsChain({self.port!r}, {self.baudrate}, address_mode={self.address_mode!r}, "
                f"char_mode={self.char_mode!r})")

    # ============================================================================================== state
    @property
    def link(self) -> Link | None:
        """The open shared link, or None."""
        return self._link

    @property
    def is_open(self) -> bool:
        """TRUE while the shared port is open (at least one open view)."""
        return self._link is not None

    @property
    def open_count(self) -> int:
        """How many times the shared port was opened."""
        return self._opens

    @property
    def views(self) -> tuple[int, ...]:
        """The device addresses of the open views (one entry per view)."""
        with self.lock:
            return tuple(v.address for v in self._views)

    @property
    def frames(self) -> collections.deque[tuple[str, bytes]]:
        """The raw ``(direction, bytes)`` log of the shared link — both devices, in wire order (the open link's, or
        the last one's after closing; empty before the first open)."""
        return self._frames

    # ============================================================================================== views
    def view(self, address: int) -> ChainLink:
        """An open :class:`ChainLink` to device ``address`` (1–16). Opens the shared port if no view has it open
        (``StageError(-6)`` when it cannot be opened), or re-opens it when it is broken (see the module docstring)."""
        address = _check_address(address)
        with self.lock:
            if self._link is not None and self._broken:
                log.warning("%s: re-opening the daisy-chain port after a link failure (device %d connects)",
                            self.port, address)
                self._close_link()
            if self._link is None:
                self._open_link()
            view = ChainLink(self, address)
            self._views.append(view)
            log.debug("%s: device %d view open (%d open)", self.port, address, len(self._views))
            return view

    def link_factory(self, address: int) -> Callable[[str, int, float], ChainLink]:
        """A ``PiSerialStage`` link factory for device ``address``: ``(port, baudrate, timeout_s) -> ChainLink``.
        ``StageError(-6)`` when ``port`` / ``baudrate`` are not the chain's (the read timeout is the chain's)."""
        address = _check_address(address)

        def open_view(port: str, baudrate: int, timeout_s: float) -> ChainLink:
            if not _same_port(port, self.port) or int(baudrate) != self.baudrate:
                raise StageError(Code.PI_OPEN_PORT,
                                 f"Open RS232 daisy chain device {address}: {port} @ {int(baudrate)} asked, but the "
                                 f"chain is on {self.port} @ {self.baudrate}")
            if float(timeout_s) != self.timeout_s:
                log.debug("%s: device %d asked for a %.3f s timeout; the chain's %.3f s applies", self.port, address,
                          float(timeout_s), self.timeout_s)
            return self.view(address)

        return open_view

    def close(self) -> None:
        """Close every view and the port (idempotent; waits for a running exchange)."""
        with self.lock:
            views, self._views = self._views, []
            for view in views:
                view._open = False
            self._close_link()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ============================================================================================== internals
    def _open_link(self) -> None:
        link = self._link_factory(self.port, self.baudrate, self.timeout_s)
        self._link = link
        self._frames = link.frames
        self._opens += 1
        self._broken = False
        self._after_ready = False
        log.info("daisy chain open: %s at %d Bd (%s, %s)", self.port, self.baudrate, self.address_mode,
                 self.char_mode)

    def _close_link(self) -> None:
        link, self._link = self._link, None
        self._broken = False
        if link is not None:
            link.close()
            log.info("daisy chain closed: %s", self.port)

    def _release(self, view: ChainLink) -> None:
        with self.lock:
            self._views = [v for v in self._views if v is not view]
            if not self._views:
                self._close_link()

    def _take_after_ready(self) -> bool:
        after, self._after_ready = self._after_ready, False
        return after


class ChainLink:
    """One controller of a :class:`GcsChain`: the ``GcsSerialLink`` interface (``send``, ``send_char``, ``receive``,
    ``query``, ``query_char``, ``discard_input``, ``close``, ``is_open``, ``frames``, ``port``, ``baudrate``,
    ``timeout_s``, ``encoding``) with the device address added to every command and checked on every answer.
    Built by :meth:`GcsChain.view`, open from the start; closing it releases the shared port.

    :attr:`frames` keeps this device's ``(direction, bytes)`` frames as they were on the wire (addresses included).
    Errors name the device: ``"Data Receive Timeout (RS-232, COM4, baud 115200, device 2)"``.
    """

    frames: collections.deque[tuple[str, bytes]]

    def __init__(self, chain: GcsChain, address: int) -> None:
        self.chain = chain
        self.address = _check_address(address)
        self.port = chain.port
        self.baudrate = chain.baudrate
        self.timeout_s = chain.timeout_s
        link = chain.link
        self.encoding: str = getattr(link, "encoding", DEFAULT_ENCODING)
        self.frames = collections.deque(maxlen=FRAME_LOG_SIZE)
        self._prefix = command_prefix(self.address, chain.address_mode)
        self._answer_prefix = answer_prefix(self.address)
        self._open = True

    def __repr__(self) -> str:
        return f"ChainLink({self.port!r}, device {self.address}{'' if self._open else ', closed'})"

    # ============================================================================================== state
    @property
    def is_open(self) -> bool:
        """TRUE until :meth:`close` (or :meth:`GcsChain.close`)."""
        return self._open

    def close(self) -> None:
        """Release the view; the last open view closes the shared port. Idempotent, never raises."""
        if not self._open:
            return
        self._open = False
        try:
            self.chain._release(self)
        except (OSError, StageError) as exc:         # a port that disappeared: nothing left to release
            log.debug("%s device %d: close failed: %s", self.port, self.address, exc)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ============================================================================================== frames
    def send(self, cmd: str) -> None:
        """``"<address> [0 ]<cmd>"`` + LF."""
        with self.chain.lock:
            link = self._link("send")
            self._write(link, (self._prefix + cmd + "\n").encode(self.encoding, errors="replace"))

    def send_char(self, code: int) -> None:
        """A single-byte command: ``"<address> "`` + the byte (``char_mode="addressed"``), or its text equivalent
        (``#5``/``#7`` → ``SRG? 1 1``, ``#24`` → ``STP``); an answer is left unread, as with ``GcsSerialLink``."""
        code = self._check_byte(code)
        with self.chain.lock:
            if self.chain.char_mode == "text_equivalents":
                self.send(self._text_equivalent(code))
                return
            link = self._link("send")
            self._write(link, self._prefix.encode("ascii") + bytes([code]))

    def receive(self) -> str:
        """One answer of this device: the address prefix of the first line is checked and removed, then lines are
        read until one that does not end with ``" \\n"``; only the final LF is removed. −7 on timeout; −3 when the
        answer is from another device, or has no addresses."""
        with self.chain.lock:
            return self._read_answer(self._link("receive"))

    def query(self, cmd: str) -> str:
        """:meth:`send` then :meth:`receive`, atomically on the shared port."""
        with self.chain.lock:
            self.send(cmd)
            return self.receive()

    def query_char(self, code: int) -> str:
        """A single-byte query, answered like ``GcsSerialLink.query_char``: ``#5`` → the hexadecimal mask
        (``"1"`` / ``"0"``), ``#7`` → one character (0xB1 ready, 0xB0 busy). In ``text_equivalents`` mode the
        ``SRG?`` register is translated into those answers."""
        code = self._check_byte(code)
        with self.chain.lock:
            if self.chain.char_mode == "text_equivalents":
                return self._query_text_equivalent(code)
            link = self._link("send")
            self._write(link, self._prefix.encode("ascii") + bytes([code]))
            if code == CHAR_READY:
                return self._read_ready(link)
            return self._read_answer(link)

    def discard_input(self) -> bytes:
        """Read and drop every byte waiting on the shared port (whichever device sent it)."""
        with self.chain.lock:
            link = self._link("discard_input")
            try:
                data = link.discard_input()
            except StageError as exc:
                raise self._tagged(exc) from exc
            if data:
                self.frames.append((DROPPED, bytes(data)))
            return bytes(data or b"")

    # ============================================================================================== char mapping
    @staticmethod
    def _check_byte(code: int) -> int:
        if not 0 <= int(code) <= 0xFF:
            raise ValueError(f"not a byte: {code!r}")
        return int(code)

    def _text_equivalent(self, code: int) -> str:
        if code in (CHAR_MOVING, CHAR_READY):
            return f"SRG? {SRG_AXIS} {SRG_REGISTER}"
        if code == CHAR_STOP_ALL:
            return "STP"
        raise StageError(Code.PI_DRIVER, f"#{code}: no text equivalent in daisy-chain char_mode 'text_equivalents'")

    def _query_text_equivalent(self, code: int) -> str:
        if code == CHAR_STOP_ALL:
            raise StageError(Code.PI_DRIVER, "#24 has no answer")
        register = parse_register(self.query(self._text_equivalent(code)))
        if code == CHAR_MOVING:
            return "1" if register & self.chain.in_motion_mask else "0"
        return chr(BUSY_BYTE) if register & self.chain.busy_mask else chr(READY_BYTE)

    # ============================================================================================== internals
    def _link(self, what: str) -> Link:
        link = self.chain.link if self._open else None
        if link is None:
            raise StageError(Code.PI_NOT_CONNECTED, f"{what}: not connected")
        return link

    def _where(self) -> str:
        return f"RS-232, {self.port}, baud {self.baudrate}, device {self.address}"

    def _tagged(self, exc: StageError) -> StageError:
        """A shared-link error with this device named (``"… [daisy chain device 2]"``)."""
        if exc.code in (Code.PI_SEND_ERROR, Code.PI_RECEIVE_ERROR):
            self.chain._broken = True
        return StageError(exc.code, f"{exc.source} [daisy chain device {self.address}]")

    def _timeout(self) -> StageError:
        return StageError(Code.PI_TIMEOUT, f"Data Receive Timeout ({self._where()})")

    def _receive_error(self, exc: BaseException) -> StageError:
        log.debug("%s device %d: read failed: %s", self.port, self.address, exc)
        self.chain._broken = True
        return StageError(Code.PI_RECEIVE_ERROR, f"Data Receive Error ({self._where()}): {exc}")

    def _wrong_answer(self, data: bytes, what: str) -> StageError:
        log.warning("%s device %d: %s: %r", self.port, self.address, what, data[:80])
        return StageError(Code.PI_RECEIVE_ERROR, f"Data Receive Error ({self._where()}): {what}: {data[:80]!r}")

    def _write(self, link: Link, data: bytes) -> None:
        try:
            link._write(data)                        # GcsSerialLink: stale-input drop, frame log, −2 / −4 texts
        except StageError as exc:
            raise self._tagged(exc) from exc
        self.frames.append((TX, data))

    def _record_rx(self, link: Link, data: bytes | bytearray) -> None:
        if data:
            link._record(RX, bytes(data))
            self.frames.append((RX, bytes(data)))

    def _check_addresses(self, data: bytes) -> int:
        """Validate the answer's ``"0 <device>"`` prefix; returns its length."""
        match = _ANSWER_ADDRESSES.match(data)
        if match is None:
            raise self._wrong_answer(data, f"answer without the address prefix {self._answer_prefix!r}")
        target, sender = int(match.group(1)), int(match.group(2))
        if sender != self.address:
            raise self._wrong_answer(data, f"answer from device {sender}, not from device {self.address}")
        if target != PC_ADDRESS:
            raise self._wrong_answer(data, f"answer addressed to {target}, not to the PC ({PC_ADDRESS})")
        return match.end()

    def _read_answer(self, link: Link) -> str:
        ser = link._require("receive")
        skip_lone_lf = self.chain._take_after_ready()
        data = bytearray()
        first = True
        while True:
            try:
                line = ser.read_until(_LF)
            except (OSError, ValueError) as exc:
                self._record_rx(link, data)
                raise self._receive_error(exc) from exc
            if first and skip_lone_lf and line == _LF:   # the late LF of an addressed #7 answer
                link._record(DROPPED, line)
                skip_lone_lf = False
                continue
            data += line
            if not line.endswith(_LF):
                self._record_rx(link, data)
                raise self._timeout()
            body = line
            if first:
                first = False
                match = _ANSWER_ADDRESSES.match(line)
                if match is not None:
                    body = line[match.end():]        # "0 2 \n" is an empty answer, not a continuation line
            if not body.endswith(_CONTINUATION):
                break
        raw = bytes(data)
        self._record_rx(link, raw)
        start = self._check_addresses(raw)
        return raw[start:-1].decode(self.encoding, errors="replace")

    def _read_ready(self, link: Link) -> str:
        """The addressed ``#7`` answer: ``"0 <device> "`` + one byte; an LF right after it is consumed when already
        waiting, or dropped silently before the next exchange."""
        ser = link._require("receive")
        skip_lone_lf = self.chain._take_after_ready()
        size = len(self._answer_prefix) + 1
        data = bytearray()
        while len(data) < size:
            try:
                chunk = ser.read(size - len(data))
            except (OSError, ValueError) as exc:
                self._record_rx(link, data)
                raise self._receive_error(exc) from exc
            if not chunk:
                self._record_rx(link, data)
                raise self._timeout()
            data += chunk
            if skip_lone_lf and data[:1] == _LF:
                link._record(DROPPED, _LF)
                del data[:1]
            skip_lone_lf = False
        extra = b""
        try:
            if ser.in_waiting:
                extra = bytes(ser.read(1))
        except (OSError, ValueError) as exc:          # the next exchange reports a broken port
            log.debug("%s device %d: check for the LF after #7 failed: %s", self.port, self.address, exc)
        if extra == _LF:
            data += _LF
        else:
            if extra:
                link._record(DROPPED, extra)
            self.chain._after_ready = True
            link._after_ready_byte = True            # GcsSerialLink: a lone LF before the next command is expected
        raw = bytes(data)
        self._record_rx(link, raw)
        if raw[:len(self._answer_prefix)] != self._answer_prefix:
            self._check_addresses(raw)               # names the wrong sender
            raise self._wrong_answer(raw, f"#7 answer without the address prefix {self._answer_prefix!r}")
        return raw[size - 1:size].decode(self.encoding, errors="replace")
