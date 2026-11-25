"""``FakeDaisyChain`` / ``FakeDaisyChainServer``: emulated PI C-663 controllers in an RS-232 daisy chain behind one
loopback socket (tests and rehearsal of :class:`alox.devices.gcs_chain.GcsChain`).

Each device is an :class:`aloxcam.zstage.fake_c663.FakeC663` (the GCS protocol on the ``SimStage`` model, reused as
it is); :class:`FakeDaisyChain` parses the daisy-chain addresses, hands the bare command to the addressed device and
puts the addresses on its answer. The syntax follows :mod:`alox.devices.gcs_chain` (sources there):

- ``"<target> <command>"`` or ``"<target> 0 <command>"``; the answer's first line starts with ``"0 <target> "``
  (``"<sender> <target> "`` for another sender); further lines of a multi-line answer carry no addresses. An empty
  answer is ``"0 <target> \\n"``.
- A command without address goes to device 1 and is answered without addresses (PI: "the target address can be
  omitted if the target controller has the address 1"). Address 255 reaches every device, which never answers it.
  Commands to an address that has no device get no answer (the PC times out).
- Single bytes (``#5``, ``#7``, ``#24``) take the address prefix of the bytes before them on the line
  (``"2 \\x05"``); ``#5`` answers ``"0 2 1\\n"``, ``#7`` ``"0 2 \\xb1"`` + LF (``ready_lf=False``: no LF).
- ``SRG? <axis> <register>`` (``FakeC663`` has no such command) answers ``"1 1=0x<hex>"`` from the model: bit 12
  servo on, bit 13 in motion, bit 14 referencing (controller not ready), bit 15 on target — the bits
  ``GcsChain(char_mode="text_equivalents")`` reads.

Options mirroring ``GcsChain``'s assumptions, to rehearse each variant: ``address_mode`` ``"target_only"`` (a sender
address is not understood: the device latches error 2, no answer), ``"target_sender"`` (a command without sender is
not understood: error 2, no answer) or ``None`` (both accepted, default); ``char_mode`` ``"addressed"`` (default:
addressed and plain single bytes accepted) or ``"text_equivalents"`` (an addressed single byte is ignored: only device
1 can be reached with a bare byte, the others through ``SRG?`` / ``STP``).

Fault injection: every ``FakeC663`` hook of a device (``devices[n].drop_next_answer()``, ``fail_next``,
``bad_ready_byte``, ``disconnect``), plus :meth:`FakeDaisyChain.drop_next_answer` (any answer of a device, ``SRG?``
included), :meth:`FakeDaisyChain.answer_as` (the next answer carries another sender address, or none) and
:attr:`FakeDaisyChain.answer_delay_s`. Command line, for rehearsal::

    python -m alox.devices.fake_chain --port 5556 [--devices 1 2] [--initially-referenced]
"""

from __future__ import annotations

import argparse
import logging
import re
import select
import signal
import socket
import threading
from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import replace
from types import MappingProxyType
from typing import Self

from aloxcam.settings import SimulationOptions
from aloxcam.zstage.fake_c663 import FakeC663

__all__ = [
    "DEFAULT_PORT",
    "FakeDaisyChain",
    "FakeDaisyChainServer",
    "chain_identity",
    "main",
]

log = logging.getLogger("alox.devices.fake_chain")

DEFAULT_PORT = 5556
FRAME_LOG_SIZE = 4096
BROADCAST = 255
_E_UNKNOWN_COMMAND = 2
_E_INVALID_AXIS = 15
_SINGLE_BYTE = {0x05: "#5", 0x07: "#7", 0x18: "#24"}
_LF = 0x0A
_POLL_S = 0.05
_SERVO, _MOVING, _REFERENCING, _ON_TARGET = 1 << 12, 1 << 13, 1 << 14, 1 << 15
_ADDRESSES = re.compile(r"\s*(\d+)\s+(?:(\d+)\s+)?(.*)$", re.DOTALL)
_PREFIX_ONLY = re.compile(r"\s*(\d+)\s+(?:(\d+)\s+)?$")


def chain_identity(address: int) -> str:
    """The ``*IDN?`` of a default fake device: the device address is its serial number."""
    return f"(c) aloxcam FakeDaisyChain, C-663.FAKE, {int(address):010d}, 0.0.0"


class FakeDaisyChain:
    """Several :class:`FakeC663` behind one port (see the module docstring). Thread-safe.

    ``options``: one :class:`SimulationOptions` for every device, a mapping ``address → options``, or None (defaults
    with :func:`chain_identity` as identity). ``clock`` / ``dh_running`` are passed to every device.
    """

    answer_delay_s: float

    def __init__(self, addresses: Iterable[int] = (1, 2),
                 options: SimulationOptions | Mapping[int, SimulationOptions] | None = None, *,
                 clock: Callable[[], float] | None = None, dh_running: bool = True, address_mode: str | None = None,
                 char_mode: str = "addressed", ready_lf: bool = True, encoding: str = "cp1252") -> None:
        if address_mode not in (None, "target_only", "target_sender"):
            raise ValueError(f"address_mode must be None, 'target_only' or 'target_sender': {address_mode!r}")
        if char_mode not in ("addressed", "text_equivalents"):
            raise ValueError(f"char_mode must be 'addressed' or 'text_equivalents': {char_mode!r}")
        devices: dict[int, FakeC663] = {}
        for address in addresses:
            if not 1 <= int(address) <= 16:
                raise ValueError(f"device address must be in 1..16: {address!r}")
            if isinstance(options, Mapping):
                opts = options.get(int(address), SimulationOptions(stage_identity=chain_identity(address)))
            elif options is None:
                opts = SimulationOptions(stage_identity=chain_identity(address))
            else:
                opts = options
            devices[int(address)] = FakeC663(opts, clock=clock, dh_running=dh_running, encoding=encoding)
        self._devices = MappingProxyType(devices)
        self.address_mode = address_mode
        self.char_mode = char_mode
        self.ready_lf = bool(ready_lf)
        self._encoding = encoding
        self._cond = threading.Condition(threading.RLock())
        self._buffer = bytearray()
        self._frames: deque[str] = deque(maxlen=FRAME_LOG_SIZE)
        self._processed = 0
        self._drop: set[int] = set()
        self._answer_as: dict[int, int | None] = {}
        self._disconnect = threading.Event()
        self.answer_delay_s = 0.0

    # ============================================================================================== state and hooks
    @property
    def devices(self) -> Mapping[int, FakeC663]:
        """``address → FakeC663`` (test hooks: ``.model``, ``.frames``, fault injection)."""
        return self._devices

    def device(self, address: int) -> FakeC663:
        return self._devices[int(address)]

    @property
    def frames(self) -> list[str]:
        """Every frame received, oldest first, addresses included: ``"2 POS? 1"``, ``"2 #5"``, ``"#7"``."""
        with self._cond:
            return list(self._frames)

    def clear_frames(self) -> None:
        with self._cond:
            self._frames.clear()
            for device in self._devices.values():
                device.clear_frames()

    @property
    def processed_bytes(self) -> int:
        """How many bytes :meth:`feed` has processed (all connections)."""
        with self._cond:
            return self._processed

    def wait_processed(self, count: int, timeout: float) -> bool:
        """Wait until :attr:`processed_bytes` ≥ ``count``; False on timeout."""
        with self._cond:
            return self._cond.wait_for(lambda: self._processed >= count, timeout)

    def drop_next_answer(self, address: int) -> None:
        """The next answer of device ``address`` (any command, ``SRG?`` included) is not sent: the PC times out."""
        with self._cond:
            self._drop.add(int(address))

    def answer_as(self, address: int, sender: int | None) -> None:
        """The next answer of device ``address`` carries ``"0 <sender> "`` instead (``None``: no addresses)."""
        with self._cond:
            self._answer_as[int(address)] = sender

    def disconnect(self) -> None:
        """Drop the current client connection (the cable is pulled); the devices keep their state."""
        self._disconnect.set()

    def take_disconnect_request(self) -> bool:
        """For the server: whether :meth:`disconnect` (of the chain or of a device) was called; clears it."""
        requested = self._disconnect.is_set()
        self._disconnect.clear()
        for device in self._devices.values():
            requested = device.take_disconnect_request() or requested
        return requested

    def reset_input(self) -> None:
        """For the server, on a new connection: a partial line of the previous one is forgotten."""
        with self._cond:
            self._buffer.clear()
            for device in self._devices.values():
                device.reset_input()

    # ============================================================================================== protocol
    def feed(self, data: bytes) -> bytes:
        """Process received bytes (any chunking) and return the answer bytes."""
        out = bytearray()
        with self._cond:
            for byte in bytes(data):
                if byte in _SINGLE_BYTE:
                    prefix = bytes(self._buffer)
                    self._buffer.clear()
                    out += self._single(byte, prefix)
                elif byte == _LF:
                    line = bytes(self._buffer)
                    self._buffer.clear()
                    out += self._line(line)
                else:
                    self._buffer.append(byte)
            self._processed += len(data)
            self._cond.notify_all()
        return bytes(out)

    def _single(self, byte: int, raw_prefix: bytes) -> bytes:
        prefix = raw_prefix.decode(self._encoding, errors="replace")
        name = _SINGLE_BYTE[byte]
        self._frames.append(f"{prefix}{name}")
        if not prefix.strip():                                   # no address: device 1, plain answer
            return self._answer(1, None, self._feed_device(1, bytes([byte])), addressed=False)
        match = _PREFIX_ONLY.match(prefix)
        if match is None:
            log.info("fake chain: %r before %s is not an address prefix: ignored", prefix, name)
            return b""
        if self.char_mode == "text_equivalents":
            log.info("fake chain: addressed single byte %r%s ignored (char_mode text_equivalents)", prefix, name)
            return b""
        target, sender = int(match.group(1)), match.group(2)
        if not self._sender_accepted(target, sender, name):
            return b""
        targets = list(self._devices) if target == BROADCAST else [target]
        answer = b""
        for address in targets:
            if address in self._devices:
                answer = self._feed_device(address, bytes([byte]))
        if target == BROADCAST or target not in self._devices or not answer:
            return b""
        if byte == 0x07 and self.ready_lf:
            answer += b"\n"
        return self._answer(target, int(sender) if sender is not None else None, answer, addressed=True)

    def _line(self, raw: bytes) -> bytes:
        text = raw.decode(self._encoding, errors="replace").strip()
        if not text:
            return b""
        self._frames.append(text)
        match = _ADDRESSES.match(text)
        if match is None:                                        # no address: device 1, plain answer
            return self._answer(1, None, self._command(1, text), addressed=False)
        target, sender, command = int(match.group(1)), match.group(2), match.group(3).strip()
        if not command or not self._sender_accepted(target, sender, command):
            return b""
        if target == BROADCAST:
            for address in self._devices:
                self._command(address, command)
            return b""
        if target not in self._devices:
            log.debug("fake chain: no device %d for %r", target, text)
            return b""
        return self._answer(target, int(sender) if sender is not None else None, self._command(target, command),
                            addressed=True)

    def _sender_accepted(self, target: int, sender: str | None, command: str) -> bool:
        """``address_mode``: a sender address the device does not expect (or a missing one) makes the command
        unknown: error 2 on the device, no answer."""
        if self.address_mode is None or (sender is None) == (self.address_mode == "target_only"):
            return True
        log.info("fake chain: %r to device %d not understood in address_mode %s", command, target, self.address_mode)
        for address in (self._devices if target == BROADCAST else [target]):
            if address in self._devices:
                self._devices[address].model.latch_error(_E_UNKNOWN_COMMAND)
        return False

    def _feed_device(self, address: int, data: bytes) -> bytes:
        device = self._devices.get(address)
        return device.feed(data) if device is not None else b""

    def _command(self, address: int, command: str) -> bytes:
        """The device's answer to a bare text command (``SRG?`` is answered here)."""
        if address not in self._devices:
            return b""
        words = command.split()
        if words[0].upper() == "SRG?":
            return self._srg(address, words[1:])
        if words[0].upper() == "JBS?":
            return self._jbs(words[1:])
        return self._feed_device(address, (command + "\n").encode(self._encoding, errors="replace"))

    def _jbs(self, args: list[str]) -> bytes:
        """``JBS? <joystick> <axis>``: the joystick button, never pressed here."""
        pairs = list(zip(args[0::2], args[1::2], strict=False)) or [("1", "1")]
        return (" \n".join(f"{joystick} {axis}=0" for joystick, axis in pairs) + "\n").encode(self._encoding)

    def _srg(self, address: int, args: list[str]) -> bytes:
        model = self._devices[address].model
        pairs = list(zip(args[0::2], args[1::2], strict=False)) or [("1", "1")]
        if len(args) % 2 or any(axis not in model.qsai() for axis, _ in pairs):
            model.latch_error(_E_INVALID_AXIS)
            return b""
        lines = []
        for axis, register in pairs:
            moving = model.is_moving()
            servo = model.qsvo(axis)
            value = (_SERVO if servo else 0) | (_MOVING if moving else 0)
            value |= 0 if model.is_controller_ready() else _REFERENCING
            value |= _ON_TARGET if servo and not moving else 0
            lines.append(f"{axis} {register}=0x{value:X}")
        return (" \n".join(lines) + "\n").encode(self._encoding)

    def _answer(self, address: int, sender: int | None, answer: bytes, *, addressed: bool) -> bytes:
        """Apply the chain-level faults and put ``"<sender> <address> "`` before an addressed answer."""
        if not answer:
            return b""
        if address in self._drop:
            self._drop.discard(address)
            log.info("fake chain: answer %r of device %d dropped", answer, address)
            return b""
        if address in self._answer_as:
            fake_sender = self._answer_as.pop(address)
            if fake_sender is None:
                return answer
            return f"0 {fake_sender} ".encode("ascii") + answer
        if not addressed:
            return answer
        return f"{0 if sender is None else sender} {address} ".encode("ascii") + answer


class FakeDaisyChainServer:
    """A :class:`FakeDaisyChain` on a loopback TCP port, served by a daemon thread, one connection at a time (as
    ``FakeC663Server``). ``port=0`` picks a free port; :attr:`url` is ``"socket://<host>:<port>"``. Without
    ``chain``, one is built from ``chain_kwargs`` (:class:`FakeDaisyChain` arguments)."""

    def __init__(self, chain: FakeDaisyChain | None = None, *, host: str = "127.0.0.1", port: int = 0,
                 **chain_kwargs: object) -> None:
        self.chain = chain if chain is not None else FakeDaisyChain(**chain_kwargs)   # type: ignore[arg-type]
        self._host = host
        self._requested_port = int(port)
        self._listener: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._conn: socket.socket | None = None
        self._connections = 0

    # ============================================================================================== life cycle
    def start(self) -> FakeDaisyChainServer:
        """Bind and start serving (idempotent); returns ``self``."""
        if self._listener is not None:
            return self
        self._listener = socket.create_server((self._host, self._requested_port))
        self._thread = threading.Thread(target=self._run, name=f"fake-chain-{self.port}", daemon=True)
        self._thread.start()
        log.info("FakeDaisyChain (devices %s) listening on %s", list(self.chain.devices), self.url)
        return self

    def close(self) -> None:
        """Stop serving and close every socket (idempotent)."""
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
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ============================================================================================== state
    @property
    def address(self) -> tuple[str, int]:
        if self._listener is None:
            raise RuntimeError("FakeDaisyChainServer not started")
        host, port = self._listener.getsockname()[:2]
        return str(host), int(port)

    @property
    def port(self) -> int:
        return self.address[1]

    @property
    def url(self) -> str:
        """``"socket://<host>:<port>"``, for ``GcsChain`` or ``[stage] port_map``."""
        host, port = self.address
        return f"socket://{host}:{port}"

    @property
    def connections(self) -> int:
        with self._lock:
            return self._connections

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._conn is not None

    def disconnect(self) -> None:
        """:meth:`FakeDaisyChain.disconnect`."""
        self.chain.disconnect()

    # ============================================================================================== serving
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
            self.chain.reset_input()
            self.chain.take_disconnect_request()
            with self._lock:
                self._conn = conn
                self._connections += 1
            log.info("FakeDaisyChain: client %s connected", peer)
            try:
                self._serve(conn)
            except Exception:
                log.exception("FakeDaisyChain: connection failed")
            finally:
                with self._lock:
                    self._conn = None
                conn.close()
                log.info("FakeDaisyChain: client %s disconnected", peer)

    def _serve(self, conn: socket.socket) -> None:
        chain = self.chain
        while not self._stop.is_set():
            if chain.take_disconnect_request():
                log.info("FakeDaisyChain: injected disconnect")
                return
            readable, _, _ = select.select([conn], [], [], _POLL_S)
            if not readable:
                continue
            if chain.take_disconnect_request():
                log.info("FakeDaisyChain: injected disconnect")
                return
            try:
                data = conn.recv(4096)
            except OSError:
                return
            if not data:
                return
            answer = chain.feed(data)
            if not answer:
                continue
            delay = chain.answer_delay_s
            if delay > 0 and self._stop.wait(delay):
                return
            try:
                conn.sendall(answer)
            except OSError:
                return


# ---------------------------------------------------------------------------------------------- command line
def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m alox.devices.fake_chain",
                                description="Emulated PI C-663 controllers in an RS-232 daisy chain on a TCP port.")
    p.add_argument("--host", default="127.0.0.1", help="listen address (default 127.0.0.1)")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"TCP port (default {DEFAULT_PORT}; 0 = any)")
    p.add_argument("--devices", type=int, nargs="+", default=[1, 2], help="device addresses (default 1 2)")
    p.add_argument("--initially-referenced", action="store_true", help="the axes start referenced, servo on")
    p.add_argument("--max-mm", type=float, default=200.0, help="travel range maximum (default 200)")
    p.add_argument("--reference-s", type=float, default=2.0, help="duration of a reference move (default 2 s)")
    p.add_argument("--address-mode", choices=("any", "target_only", "target_sender"), default="any")
    p.add_argument("--char-mode", choices=("addressed", "text_equivalents"), default="addressed")
    p.add_argument("--no-ready-lf", action="store_true", help="the addressed #7 answer has no LF")
    p.add_argument("--log-level", type=str.upper, default="INFO", choices=("DEBUG", "INFO", "WARNING"))
    return p


def main(argv: Sequence[str] | None = None) -> int:
    """Run a :class:`FakeDaisyChainServer` until Ctrl+C (or SIGTERM)."""
    args = _parser().parse_args(argv)
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    base = SimulationOptions(stage_max_mm=args.max_mm, stage_initially_referenced=args.initially_referenced,
                             stage_reference_s=max(0.0, args.reference_s))
    options = {a: replace(base, stage_identity=chain_identity(a)) for a in args.devices}
    chain = FakeDaisyChain(args.devices, options, dh_running=False,
                           address_mode=None if args.address_mode == "any" else args.address_mode,
                           char_mode=args.char_mode, ready_lf=not args.no_ready_lf)
    try:
        server = FakeDaisyChainServer(chain, host=args.host, port=args.port).start()
    except OSError as exc:
        print(f"cannot listen on {args.host}:{args.port}: {exc}", flush=True)
        return 1
    print(f"FakeDaisyChain (devices {args.devices}) listening on {server.url}; Ctrl+C stops it.", flush=True)
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
