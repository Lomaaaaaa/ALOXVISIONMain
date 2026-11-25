#!/usr/bin/env python3
r"""Check the two PI C-663 XY controllers on their RS-232 daisy chain: which protocol variant they speak, read-only
information, then optional guided motion tests.

Background: ``src/alox/devices/gcs_chain.py`` (the daisy-chain transport and the protocol assumptions this tool
settles), ``src/alox/devices/xy.py`` (the XY device), docs/vision-core/map.md Part 2 §2.8 (LabVIEW's AloxXYServer).
Modelled on ``tools/z_stage_check.py``.

Run it on the PC that controls the stages, from the repository folder, **with the LabVIEW XY server
(AloxXYServer.exe) stopped** (it keeps the COM port open). Information first; the motion tests only when the stage
area is safe::

    python tools\xy_stage_check.py --alox-ini D:\alox\settings\alox.ini
    python tools\xy_stage_check.py --alox-ini D:\alox\settings\alox.ini --motion

``--port COM3`` (and ``--baud``) gives the port directly. X is daisy-chain device ``xaxis_I32`` and Y device
``yaxis_I32`` of ``alox.ini [hardware.xy]`` (1 and 2 without ``--alox-ini``). ``--fake`` runs everything against two
emulated C-663 inside this program (a rehearsal: no port is opened); ``--fake-address-mode`` / ``--fake-char-mode``
make the emulation speak the other protocol variant. The report (``xy_stage_report_<date>-<time>.json`` with every
answer and every raw frame, plus a ``.txt`` summary) is written next to this tool, or in ``--out``. That report is
what to send back.

Information (the default): read-only
------------------------------------
These frames, and only these, are sent, in this order (``d`` is a device number, X first):

1. Opening the port sends nothing (bytes already waiting are discarded).
2. Protocol probes, raw frames:

   - without address: ``*IDN?``, ``ERR?``, ``#5``, ``#7`` (a command without address goes to device 1);
   - per device: ``d *IDN?`` (target address only), ``d 0 *IDN?`` (target and sender address);
   - then, in the first form both devices answered, per device: ``ERR?``, ``#5``, ``#7``, ``SRG? 1 1``, ``RMC?``,
     ``ERR?`` (``#5`` / ``#7`` are the single bytes 0x05 / 0x07 after the address; ``SRG? 1 1`` is the status
     register that can replace them);
   - a lone LF after a single-byte probe that was not answered in the addressed form (it ends a partial line the
     controllers may still hold).

3. Information per controller, through the application's daisy-chain code in the variant found: ``SAI?``,
   ``*IDN?``, ``CST? 1``, ``TMN? 1``, ``TMX? 1``, ``TRS? 1``, ``LIM? 1``, ``FRF? 1``, ``POS? 1``, ``VEL? 1``,
   ``SVO? 1``, ``RON? 1``, ``JON? 1``, ``JBS? 1 1``, ``ERR?`` (``JBS? 1 1``, the joystick button, on X only).
4. Only when a query gets no usable answer: one more ``ERR?`` right after it, to read the controller's reason.

``ERR?`` reads **and clears** the controller's pending error; every value read is in the report. An address form or
single byte a controller does not understand only latches an error, which the next ``ERR?`` reads. Nothing moves a
stage, changes a setting or stops a macro. Closing the port sends nothing.

Motion tests (``--motion``): the stages move
---------------------------------------------
The information above is read first, and the daisy-chain variant found is used. The plan is printed (start
positions, allowed windows, test speed) and nothing moves until the operator types ``yes``; each step is explained and
needs its own ``yes`` (anything else skips it, ``quit`` skips the rest). Everything goes through the application's
XY device (``XYStage`` → ``ZController`` → ``PiSerialStage`` → ``GcsChain``):

0. Set-up (needed by the tests): per controller the purge ``XXX``, ``*idn?``, ``err?``, then ``*IDN?``, ``SAI?``,
   ``CST? 1``, ``SVO 1 1`` (servo on), ``SVO? 1``, ``RON? 1``, ``FRF? 1``, ``TMN? 1``, ``TMX? 1``, ``POS? 1``,
   ``ERR?``, ``VEL 1 <test speed>``, ``ERR?``. An axis that is not referenced is referenced by the set-up (``FRF 1``
   with a reference switch, else ``FNL 1``: it drives to a switch, OUTSIDE the window): an extra warning and a second
   ``yes`` are required, otherwise the tests stop here.
1. X relative moves: ``MVR 1 +d``, then ``MVR 1 -d`` (``d`` = ``--step``, 1 mm), each with ``ERR?``, position checks.
2. Y relative moves, the same.
3. Inside-wafer refusal: with a test wafer centred on the stages whose move radius is 0.25 mm (the device's move check:
   destination within ``move_radius_mm`` of the wafer origin), a relative move of ``d`` must be refused (error 9404)
   with no ``MOV`` / ``MVR`` sent.
4. STP during a move: an X move across the window; after 40 % of it, the device's Stop (``STP 1`` and ``ERR?`` on
   both controllers); ``POS?`` / ``#5`` until standstill (``#24`` if X still moves 1 s after ``STP``).

While a stage moves, ``POS? 1`` and ``#5`` of both controllers are read every 50 ms. At the end: optionally the move
back to the start positions (``MOV``, after a ``yes``), the original speeds (``VEL 1 <original>``, ``ERR?``) and the
final positions. The joystick is never switched on or off.

Safety rules (all enforced by the code below):

- ``--yes`` (answers every question) is refused unless ``--fake`` is given.
- Allowed window per axis = intersection of the controller range ``[TMN? 1, TMX? 1]``, ``alox.ini [hardware.xy]
  xmin/xmax`` or ``ymin/ymax`` (when given) and the start position +/- ``--window`` (default 1 mm). While a stage
  moves, a position outside its window (+0.05 mm) sends ``#24`` to both controllers at once.
- Test speed ``--speed`` (default 1 mm/s; at most 5 mm/s and never above a controller's current speed, unless
  ``--fake``); the original speeds are restored at the end.
- Every move has a timeout. Ctrl+C, any error or a timeout sends ``#24`` (stop all axes) to both controllers at once
  - again on a re-opened port when the link itself failed - and the tool never moves the stages back after it.
- No reference move except in the set-up of an unreferenced axis, after the second ``yes``.

Exit code: 0 the tool ran to the end (findings are in the report), 1 it could not connect, lost the link, found no
working daisy-chain variant, or the motion tests were stopped (#24), 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import textwrap
import time
import traceback
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

try:                                    # a source checkout without ``pip install -e .``: use <repo>/src
    import alox  # noqa: F401
except ImportError:                     # pragma: no cover - depends on how the tool is started
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import z_stage_check as zc

from alox.config import TypedIni
from alox.devices.base import DeviceError
from alox.devices.fake_chain import (
    FakeDaisyChain,
    FakeDaisyChainServer,
    chain_identity,
)
from alox.devices.gcs_chain import (
    SRG_IN_MOTION,
    SRG_REFERENCING,
    ChainLink,
    GcsChain,
    command_prefix,
    parse_register,
)
from alox.devices.xy import (
    OUTSIDE_WAFER,
    WaferSettings,
    XYSettings,
    XYStage,
    load_xy_settings,
)
from aloxcam import __version__ as ALOXCAM_VERSION
from aloxcam.errors import Code, StageError
from aloxcam.settings import SimulationOptions, StageOptions
from aloxcam.zstage.backend import format_gcs_float
from aloxcam.zstage.gcs_serial import (
    PYSERIAL_MISSING,
    RX,
    TX,
    GcsSerialLink,
    pyserial_version,
)
from aloxcam.zstage.pi_serial import (
    LinkFactory,
    PiSerialStage,
    axis_value,
    parse_error_code,
)

__all__ = [
    "INFO_STEPS",
    "PROBE_COMMANDS",
    "Environment",
    "ProbeResult",
    "analyse_probes",
    "main",
    "start_fake_server",
]

TOOL_NAME = "xy_stage_check"
TOOL_VERSION = "1.0"
TOOL_DIR = Path(__file__).resolve().parent
REPORT_PREFIX = "xy_stage_report_"

AXES = ("x", "y")
AXIS = "1"                              # the C-663's only axis
DEFAULT_BAUD = 115200
DEFAULT_TIMEOUT_MS = 5000
DEFAULT_PROBE_TIMEOUT_MS = 1000         # a probe the controllers do not understand gets no answer: wait this long
QUIET_S = 0.15                          # a probe answer without final LF is complete after this long without a byte
MAX_ANSWER_BYTES = 4096
DEFAULT_WINDOW_MM = 1.0
DEFAULT_STEP_MM = 1.0
DEFAULT_SPEED = 1.0
MAX_SPEED_REAL = 5.0
MIN_SPEED = 0.05
MAX_WINDOW_REAL = 10.0
POLL_S = 0.05
AT_TARGET_MM = 0.01
WINDOW_MARGIN_MM = 0.05
ACT_FRACTION = 0.4
MIN_STOP_TRAVEL_MM = 0.2
STOP_GRACE_S = 1.0
STANDSTILL_WAIT_S = 3.0
MOVE_TIMEOUT_MIN_S = 3.0
MOVE_TIMEOUT_FACTOR = 3.0
SETUP_READY_TIMEOUT_S = 180.0
TEST_WAFER_MM = 0.5                     # the inside-wafer step: diameter (enables the check), radius = half of it
FAKE_TRAVEL_MM = 150.0
FAKE_START_MM = 75.0                    # --fake: both emulated stages referenced at mid-travel (FRF)

EXIT_OK, EXIT_PROBLEM, EXIT_USAGE = zc.EXIT_OK, zc.EXIT_PROBLEM, zc.EXIT_USAGE
LINK_ERRORS = zc.LINK_ERRORS
TEST_QUESTION = zc.TEST_QUESTION
_SINGLE = {"#5": 0x05, "#7": 0x07, "#24": 0x18}
_SINGLE_NAMES = {code: name for name, code in _SINGLE.items()}
_ANSWER_ADDRESSES = re.compile(rb"(\d+) (\d+)(?: |(?=\n)|$)")
_SENT_PREFIX = re.compile(rb"(\d+ (?:0 )?)(.*)$", re.DOTALL)

Console = zc.Console
UsageError, Refusal, SafetyStop = zc.UsageError, zc.Refusal, zc.SafetyStop
Window = zc.Window
describe, frame_text, show_err = zc.describe, zc.frame_text, zc._show_err


# ---------------------------------------------------------------------------------------------- outside world
@dataclass
class Environment:
    """What the tool needs from the outside world; the tests replace parts of it (virtual time, a prepared fake)."""

    read_line: Callable[[], str] = input
    output: TextIO | None = None
    sleep: Callable[[float], None] = time.sleep
    monotonic: Callable[[], float] = time.monotonic
    link_factory: LinkFactory | None = None             # None: GcsSerialLink
    fake_server: FakeDaisyChainServer | None = None     # --fake: use this one (not closed by the tool)


# ---------------------------------------------------------------------------------------------- frames
def command_text(data: bytes) -> str:
    """A sent frame as a command: ``"2 POS? 1"``, ``"2 #5"``, ``"#7"``, ``"(LF)"`` for a lone LF."""
    if data == b"\n":
        return "(LF)"
    match = _SENT_PREFIX.match(data)
    prefix, body = (match.group(1), match.group(2)) if match else (b"", data)
    if len(body) == 1 and body[0] in _SINGLE_NAMES:
        return prefix.decode("ascii") + _SINGLE_NAMES[body[0]]
    return frame_text(data[:-1] if data.endswith(b"\n") else data)


class FrameLog(zc.FrameLog):
    """Every frame of every link the tool opened, time-stamped; sent frames shown with their address."""

    def sent_since(self, mark: int) -> list[str]:
        return [command_text(f.data) for f in self.since(mark) if f.direction == TX]

    def as_records(self) -> list[dict[str, Any]]:
        records = super().as_records()
        for record, frame in zip(records, self.frames, strict=True):
            if frame.direction == TX:
                record["cmd"] = command_text(frame.data)
        return records


# ---------------------------------------------------------------------------------------------- guidance
def advice(exc: StageError, port: str, timeout_ms: int) -> list[str]:
    """What the operator should check for a link error (simple words)."""
    low = exc.source.lower()
    if exc.code == Code.PI_OPEN_PORT:
        if PYSERIAL_MISSING.lower() in low or "pyserial" in low:
            return ["pyserial (the serial port library) is not installed for this Python.",
                    "Install it with this exact command, then run the tool again:",
                    f'    "{sys.executable}" -m pip install pyserial']
        return [f"{port} could not be opened. Close the LabVIEW XY server (AloxXYServer.exe keeps the COM port open)",
                "and any PI MikroMove or terminal window; check the port number: alox.ini [hardware.xy] comport_I32",
                "= <n> means COM<n> (Windows Device Manager > Ports lists the Moxa ports)."]
    if exc.code == Code.PI_TIMEOUT:
        return [f"No answer within {timeout_ms} ms. Check the port number (alox.ini [hardware.xy] comport_I32), the",
                "Moxa serial server, that both C-663 controllers are switched on and chained (RS-232 cable from the",
                "PC to X, X to Y), and the baud rate (115200: baudrate_I32 = 3)."]
    if exc.code in (Code.PI_SEND_ERROR, Code.PI_RECEIVE_ERROR):
        return ["The serial link failed. Check the Moxa serial server, its network cable and the RS-232 cables."]
    return []


# ---------------------------------------------------------------------------------------------- probes
#: The probe commands (the docstring lists where each is sent).
PROBE_COMMANDS = ("*IDN?", "ERR?", "#5", "#7", "SRG? 1 1", "RMC?")


def probe_frame(form: str, device: int | None, command: str) -> bytes:
    """The raw frame of a probe: ``form`` ``"plain"`` (no address), ``"target_only"`` or ``"target_sender"``."""
    prefix = "" if form == "plain" or device is None else command_prefix(device, form)
    if command in _SINGLE:
        return prefix.encode("ascii") + bytes([_SINGLE[command]])
    return (prefix + command + "\n").encode("ascii")


def split_answer(raw: bytes) -> tuple[tuple[int, int] | None, bytes]:
    """``((to, from), rest)`` for an answer starting with two addresses (``b"0 2 1=3.0\\n"``), else ``(None, raw)``."""
    match = _ANSWER_ADDRESSES.match(raw)
    if match is None:
        return None, raw
    return (int(match.group(1)), int(match.group(2))), raw[match.end():]


@dataclass
class ProbeResult:
    """One probe: what was sent, every byte received, and what it means."""

    key: str
    form: str
    device: int | None
    command: str
    sent: bytes
    raw: bytes = b""
    timed_out: bool = False
    ms: float = 0.0
    ok: bool = False
    meaning: str = ""
    value: Any = None

    def record(self) -> dict[str, Any]:
        return {"key": self.key, "form": self.form, "device": self.device, "command": self.command,
                "sent": frame_text(self.sent), "received": frame_text(self.raw) if self.raw else "",
                "received_hex": self.raw.hex(" "), "timed_out": self.timed_out, "ms": self.ms, "ok": self.ok,
                "meaning": self.meaning, "value": zc._jsonable(self.value)}


def judge(p: ProbeResult) -> None:
    """Fill ``ok`` / ``meaning`` / ``value`` of a probe from its raw answer."""
    raw, addressed = p.raw, p.form != "plain"
    if not raw:
        p.meaning = "no answer"
        return
    addresses, rest = split_answer(raw)
    if addressed:
        if addresses is None:
            p.meaning = "answered WITHOUT the addresses"
            return
        if addresses != (0, p.device):
            p.meaning = f"answered with the addresses {addresses[0]} {addresses[1]} (expected 0 {p.device})"
            return
    elif addresses is not None and p.command not in ("#5", "#7"):
        p.meaning = f"answered WITH the addresses {addresses[0]} {addresses[1]}"
    text = rest.decode("cp1252", errors="replace")
    body = text.removesuffix("\n")
    if p.command == "*IDN?":
        p.ok, p.value = bool(body.strip()) and text.endswith("\n"), body
        p.meaning = p.meaning or ("identity " + repr(body) if p.ok else "unexpected answer")
    elif p.command == "ERR?":
        match = re.fullmatch(r"\s*(-?\d+)\s*", body)
        p.ok = match is not None and text.endswith("\n")
        p.value = int(match.group(1)) if match else None
        p.meaning = p.meaning or (show_err(p.value) if p.ok else "unexpected answer")
    elif p.command == "#5":
        p.ok = addresses is None or addressed
        p.ok = p.ok and re.fullmatch(r"[0-9A-Fa-f]+", body.strip()) is not None and text.endswith("\n")
        p.value = int(body.strip(), 16) if p.ok else None
        p.meaning = (f"motion mask {body.strip()!r}" if p.ok else "unexpected answer")
    elif p.command == "#7":
        p.ok = (addresses is None or addressed) and len(rest.rstrip(b"\n")) == 1 and rest[:1] in (b"\xb0", b"\xb1")
        p.value = {"ready": rest[:1] == b"\xb1", "lf": raw.endswith(b"\n")} if p.ok else None
        if p.ok:
            p.meaning = (f"{'ready (0xB1)' if p.value['ready'] else 'busy (0xB0)'}, "
                         f"{'followed by LF' if p.value['lf'] else 'no LF after the byte'}")
        else:
            p.meaning = "unexpected answer"
    elif p.command == "SRG? 1 1":
        try:
            p.value = parse_register(body)
            p.ok = text.endswith("\n")
        except StageError:
            p.ok = False
        p.meaning = describe_register(p.value) if p.ok else "unexpected answer"
    elif p.command == "RMC?":
        p.ok = text.endswith("\n")
        p.value = body
        if not p.ok:
            p.meaning = "unexpected answer"
        elif body.strip():
            p.meaning = f"a macro runs: {body!r} (the empty-answer form is not seen)"
        else:
            p.meaning = f"empty answer, as '{frame_text(raw)}'"


def describe_register(value: int | None) -> str:
    if value is None:
        return ""
    flags = [name for bit, name in ((12, "servo on"), (13, "in motion"), (14, "referencing"), (15, "on target"))
             if value & (1 << bit)]
    return f"0x{value:04X} ({', '.join(flags) or 'none of bits 12-15 set'})"


@dataclass
class ChainVariant:
    """What the probes found: the ``GcsChain`` settings that work (None: no variant works)."""

    address_mode: str | None
    char_mode: str | None
    notes: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return self.address_mode is not None

    def code(self, port: str, baudrate: int, timeout_s: float) -> str:
        if self.address_mode is None:
            return "UNDETERMINED: no address form was answered by both controllers"
        char = repr(self.char_mode) if self.char_mode else "UNDETERMINED"
        return (f"GcsChain({port!r}, {baudrate}, {float(timeout_s)!r}, address_mode={self.address_mode!r}, "
                f"char_mode={char})")


def analyse_probes(probes: Sequence[ProbeResult], devices: tuple[int, int]) -> ChainVariant:
    """The daisy-chain variant from the probe answers (see :class:`ChainVariant`)."""
    by_key = {p.key: p for p in probes}

    def ok(form: str, device: int, command: str) -> bool:
        p = by_key.get(f"{form}:{device}:{command}")
        return p is not None and p.ok

    notes: list[str] = []
    forms = [f for f in ("target_only", "target_sender") if all(ok(f, d, "*IDN?") for d in devices)]
    for form in ("target_only", "target_sender"):
        answered = [d for d in devices if ok(form, d, "*IDN?")]
        notes.append(f"{form}: answered by device(s) {answered or 'none'}")
    address_mode = forms[0] if forms else None
    if len(forms) == 2:
        notes.append("both address forms work; target_only (the default) is chosen")
    char_mode = None
    if address_mode is not None:
        addressed = all(ok(address_mode, d, "#5") and ok(address_mode, d, "#7") for d in devices)
        text = all(ok(address_mode, d, "SRG? 1 1") for d in devices)
        notes.append(f"addressed #5/#7: {'answered by both' if addressed else 'NOT answered by both'}; "
                     f"SRG? 1 1: {'answered by both' if text else 'NOT answered by both'}")
        char_mode = "addressed" if addressed else "text_equivalents" if text else None
    return ChainVariant(address_mode, char_mode, notes)


class Prober:
    """The protocol probes: raw frames on the port, every byte of the answer recorded. Uses the internals of
    ``GcsSerialLink`` (``_write``, ``_record``, ``_serial``) to read answers whose format is not known yet."""

    def __init__(self, link: GcsSerialLink, timeout_s: float) -> None:
        self.link = link
        self.timeout_s = timeout_s

    def exchange(self, data: bytes, first_timeout_s: float | None = None) -> tuple[bytes, bool]:
        """Send ``data``; read until a complete GCS answer (a line that does not end with ``" \\n"`` and nothing more
        waiting), :data:`QUIET_S` without a new byte, or ``timeout_s`` (``first_timeout_s``) without any byte.
        Returns ``(bytes received, timed out)``."""
        link = self.link
        link._write(data)
        ser = link._serial
        buf = bytearray()
        try:
            ser.timeout = self.timeout_s if first_timeout_s is None else first_timeout_s
            buf += ser.read(1)
            while buf and len(buf) < MAX_ANSWER_BYTES:
                if buf.endswith(b"\n") and not buf.endswith(b" \n") and not ser.in_waiting:
                    break
                ser.timeout = QUIET_S
                more = ser.read(1)
                if not more:
                    break
                buf += more
        except (OSError, ValueError) as exc:
            raise StageError(Code.PI_RECEIVE_ERROR, f"Data Receive Error ({link.port}): {exc}") from exc
        finally:
            try:
                ser.timeout = link.timeout_s
            except (OSError, ValueError):
                pass
            if buf:
                link._record(RX, bytes(buf))
        return bytes(buf), not buf


# ---------------------------------------------------------------------------------------------- information
def _value(answer: str, key: str, command: str) -> str:
    return axis_value(answer, key, command)


def _float(answer: str, command: str) -> float:
    try:
        return float(_value(answer, AXIS, command))
    except ValueError:
        raise StageError(Code.PI_DRIVER, f"{command}: not a number: {answer!r}") from None


def _flag(answer: str, command: str) -> bool:
    return _float(answer, command) != 0.0


def _err(answer: str, command: str) -> int:
    if not answer.strip() or re.match(r"\s*-?\d+", answer) is None:
        raise StageError(Code.PI_DRIVER, f"{command}: unexpected answer {answer!r}")
    return parse_error_code(answer)


def _jbs(answer: str, command: str) -> int:
    value = answer.rpartition("=")[2].strip()
    if not value.lstrip("-").isdigit():
        raise StageError(Code.PI_DRIVER, f"{command}: unexpected answer {answer!r}")
    return int(value)


def _yesno(yes: str, no: str) -> Callable[[Any], str]:
    return lambda v: f"{int(bool(v))}  {yes if v else no}"


@dataclass(frozen=True)
class InfoStep:
    frame: str
    key: str
    parse: Callable[[str, str], Any]
    show: Callable[[Any], str]
    x_only: bool = False


INFO_STEPS: tuple[InfoStep, ...] = (
    InfoStep("SAI?", "sai", lambda a, c: a.split(), lambda v: " ".join(v) or "(empty)"),
    InfoStep("*IDN?", "idn", lambda a, c: a, str),
    InfoStep("CST? 1", "cst", lambda a, c: _value(a, AXIS, c), str),
    InfoStep("TMN? 1", "tmn", _float, lambda v: f"{v:.4f} mm"),
    InfoStep("TMX? 1", "tmx", _float, lambda v: f"{v:.4f} mm"),
    InfoStep("TRS? 1", "trs", _flag, _yesno("the stage has a reference switch", "no reference switch")),
    InfoStep("LIM? 1", "lim", _flag, _yesno("the stage has limit switches", "no limit switches")),
    InfoStep("FRF? 1", "frf", _flag, _yesno("referenced", "NOT referenced")),
    InfoStep("POS? 1", "pos", _float, lambda v: f"{v:.4f} mm"),
    InfoStep("VEL? 1", "vel", _float, lambda v: f"{v:.4f} mm/s"),
    InfoStep("SVO? 1", "svo", _flag, _yesno("servo on", "servo OFF")),
    InfoStep("RON? 1", "ron", _flag, _yesno("reference mode on", "reference mode off")),
    InfoStep("JON? 1", "jon", lambda a, c: int(float(_value(a, "1", c))), lambda v: f"{v}  joystick "
             f"{'ON' if v else 'off'}"),
    InfoStep("JBS? 1 1", "jbs", _jbs, lambda v: f"{v}  joystick button {'pressed' if v else 'not pressed'}",
             x_only=True),
    InfoStep("ERR?", "err", _err, show_err),
)


# ---------------------------------------------------------------------------------------------- target
@dataclass(frozen=True)
class Target:
    """Where to connect, the daisy-chain device numbers, and the alox.ini values when ``--alox-ini`` is given."""

    port: str
    baudrate: int
    source: str
    devices: tuple[int, int] = (1, 2)
    settings: XYSettings = field(default_factory=XYSettings)
    ranges: dict[str, tuple[float, float]] = field(default_factory=dict)     # alox.ini xmin/xmax, ymin/ymax
    alox_ini: Path | None = None
    ini_warnings: tuple[str, ...] = ()


def _ini_range(ini: TypedIni, axis: str) -> tuple[float, float] | None:
    """``[hardware.xy] <axis>min_DBL .. <axis>max_DBL`` (the ``_DBL`` keys win over older ``_I32`` duplicates)."""
    try:
        lo, hi = float(ini.get(f"hardware.xy.{axis}min", math.nan)), float(ini.get(f"hardware.xy.{axis}max", math.nan))
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lo) and math.isfinite(hi)) or lo == hi:
        return None
    return min(lo, hi), max(lo, hi)


def resolve_target(args: argparse.Namespace) -> Target:
    """Port, baud rate, device numbers and alox.ini values from the command line (``--fake``'s port is set later)."""
    settings = XYSettings()
    ranges: dict[str, tuple[float, float]] = {}
    warnings: list[str] = []
    if args.alox_ini is not None:
        try:
            ini = TypedIni.load(args.alox_ini)
        except OSError as exc:
            raise UsageError(f"alox.ini cannot be read: {exc}") from exc
        settings = load_xy_settings(ini, warn=warnings.append)
        for axis in AXES:
            span = _ini_range(ini, axis)
            if span is not None:
                ranges[axis] = span
    devices = (int(settings.x.device), int(settings.y.device))
    for name, device in zip(AXES, devices, strict=True):
        if not 1 <= device <= 16:
            raise UsageError(f"alox.ini [hardware.xy] {name}axis_I32 = {device} is not a daisy-chain address 1..16")
    if devices[0] == devices[1]:
        raise UsageError(f"X and Y have the same daisy-chain address {devices[0]} (alox.ini xaxis_I32 / yaxis_I32)")
    com = settings.x.com if settings.x.com > 0 else 1
    if args.fake:
        return Target("", args.baud or DEFAULT_BAUD, "--fake (two emulated C-663)", devices, settings, ranges,
                      args.alox_ini, tuple(warnings))
    if args.port:
        baud = args.baud or (settings.x.baudrate if args.alox_ini else DEFAULT_BAUD)
        return Target(zc._normal_port(args.port), baud, "--port", devices, settings, ranges, args.alox_ini,
                      tuple(warnings))
    return Target(f"COM{com}", args.baud or settings.x.baudrate, f"alox.ini {args.alox_ini}", devices, settings,
                  ranges, args.alox_ini, tuple(warnings))


def check_args(args: argparse.Namespace) -> None:
    """Command-line rules (safety first); :class:`UsageError` when one is broken."""
    if args.yes and not args.fake:
        raise UsageError("--yes is only accepted together with --fake: on the real stages the operator must type "
                         "yes for every step")
    if args.fake and args.port:
        raise UsageError("--fake and --port cannot be used together (--fake opens no port)")
    fake_options = args.fake_address_mode != "any" or args.fake_char_mode != "addressed" or args.fake_no_ready_lf
    if fake_options and not args.fake:
        raise UsageError("--fake-address-mode, --fake-char-mode and --fake-no-ready-lf need --fake")
    if not (args.fake or args.port or args.alox_ini):
        raise UsageError("say which port to use: --alox-ini <path to alox.ini> or --port COM<n> "
                         "(or --fake for a rehearsal)")
    for name in ("speed", "window", "step"):
        value = getattr(args, name)
        if not (math.isfinite(value) and value > 0):
            raise UsageError(f"--{name} must be a positive number")
    if args.speed < MIN_SPEED:
        raise UsageError(f"--speed below {MIN_SPEED:g} mm/s is too slow for the tests")
    if not args.fake and args.speed > MAX_SPEED_REAL:
        raise UsageError(f"--speed above {MAX_SPEED_REAL:g} mm/s is refused on the real stages")
    if not args.fake and args.window > MAX_WINDOW_REAL:
        raise UsageError(f"--window above {MAX_WINDOW_REAL:g} mm is refused on the real stages")
    if args.step > args.window:
        raise UsageError("--step must not be larger than --window")
    if args.baud is not None and args.baud <= 0:
        raise UsageError("--baud must be a positive number")
    if args.timeout_ms <= 0 or args.probe_timeout_ms <= 0:
        raise UsageError("--timeout-ms and --probe-timeout-ms must be positive numbers")
    if args.alox_ini is not None and not args.alox_ini.is_file():
        raise UsageError(f"alox.ini not found: {args.alox_ini}")


def start_fake_server(*, address_mode: str | None = None, char_mode: str = "addressed", ready_lf: bool = True,
                      clock: Callable[[], float] | None = None, reference_s: float = 0.2,
                      wait: Callable[[float], None] = time.sleep) -> FakeDaisyChainServer:
    """``--fake``: two emulated C-663 (devices 1 and 2, 0-150 mm), referenced at mid-travel, no macro running."""
    options = {a: SimulationOptions(stage_min_mm=0.0, stage_max_mm=FAKE_TRAVEL_MM, stage_reference_s=reference_s,
                                    stage_initially_referenced=True, stage_identity=chain_identity(a))
               for a in (1, 2)}
    chain = FakeDaisyChain((1, 2), options, clock=clock, dh_running=False, address_mode=address_mode,
                           char_mode=char_mode, ready_lf=ready_lf)
    for device in chain.devices.values():
        device.model.frf(AXIS)                              # to mid-travel in reference_s
    wait(reference_s + 0.05)
    for device in chain.devices.values():
        device.model.qerr()
    chain.clear_frames()
    return FakeDaisyChainServer(chain).start()


# ---------------------------------------------------------------------------------------------- report
class Report(zc.Report):
    """The report sent back by the owner: probes, information, motion, every frame."""

    def __init__(self, argv: Sequence[str]) -> None:
        super().__init__(argv)
        self.data["report"] = f"{TOOL_NAME} report"
        self.data["versions"]["tool"] = TOOL_VERSION
        self.data["command_line"] = [sys.argv[0] if sys.argv else TOOL_NAME, *argv]
        self.data["devices"] = {}
        self.data["probes"] = {"exchanges": []}
        self.data["daisy_chain"] = None
        self.data["info"] = {axis: {"device": None, "queries": []} for axis in AXES}


def report_paths(out: Path | None, started: datetime) -> tuple[Path, Path]:
    stem = REPORT_PREFIX + started.strftime("%Y%m%d-%H%M%S")
    if out is None:
        base = TOOL_DIR / stem
    elif out.suffix.lower() in (".json", ".txt"):
        base = out.with_suffix("")
    else:
        base = out / stem
    return base.with_suffix(".json"), base.with_suffix(".txt")


def render_text(data: dict[str, Any], json_path: Path) -> str:
    """The readable ``.txt`` summary of the report."""
    lines: list[str] = []
    add = lines.append
    add("XY STAGE CHECK REPORT")
    add(f"Send this report back: {json_path}  (this .txt is the same report, easier to read)")
    add("")
    v = data["versions"]
    add(f"Tool        {TOOL_NAME} {v['tool']}, aloxcam {v['aloxcam']}, git {v['git_commit']}")
    add(f"            Python {v['python']}, pyserial {v['pyserial']}, {v['platform']}")
    add(f"Started     {data['started']}")
    add(f"Finished    {data.get('finished', '')}")
    add(f"Command     {' '.join(data['command_line'])}")
    result = data.get("result", {})
    add(f"Result      {result.get('summary', '')} (exit code {result.get('exit_code', '')})")
    add("")
    conn = data["connection"]
    add(f"Port        {conn.get('port', '')} at {conn.get('baudrate', '')} Bd ({conn.get('source', '')}); devices "
        f"X = {data['devices'].get('x', '')}, Y = {data['devices'].get('y', '')}")
    ini = data.get("alox_ini")
    if ini:
        add(f"alox.ini    {ini['path']}: COM{ini['comport_I32']}, {ini['baudrate']} Bd, xaxis {ini['xaxis_I32']}, "
            f"yaxis {ini['yaxis_I32']}, ranges {ini['ranges']}")
    chain = data.get("daisy_chain")
    add("")
    add("DAISY-CHAIN VARIANT (for the developers)")
    if chain:
        add(f"  {chain['code']}")
        for note in chain["notes"]:
            add(f"  - {note}")
    else:
        add("  not determined (the probes did not run)")
    add("")
    add("Protocol probes (raw)")
    for p in data["probes"]["exchanges"]:
        who = "no address" if p["device"] is None else f"device {p['device']}, {p['form']}"
        add(f"  {p['command']:<9} {who:<28} {'ok ' if p['ok'] else '-- '} {p['meaning']}")
        add(f"  {'':<9} sent '{p['sent']}'  received '{p['received']}'{' (timeout)' if p['timed_out'] else ''}")
    for axis in AXES:
        info = data["info"][axis]
        add("")
        add(f"Information {axis.upper()} (device {info['device']}, read-only)")
        for q in info["queries"]:
            if q["ok"]:
                add(f"  {q['frame']:<10} {q['shown']}")
            else:
                add(f"  {q['frame']:<10} FAILED: {q['error']}")
                if q.get("err_after"):
                    add(f"  {'':<10} ERR? afterwards: {q['err_after']}")
            add(f"  {'':<10} raw: {' | '.join(q['raw']) or '(nothing received)'}")
    add("")
    add("Findings")
    for f in data["findings"]:
        add(f"  [{f['ref']}] {f['topic']}: {f['answer']}")
    if not data["findings"]:
        add("  (none)")
    motion = data.get("motion")
    if motion:
        add("")
        add("Motion tests")
        if motion.get("refused"):
            add(f"  Not run: {motion['refused']}")
        elif motion.get("declined"):
            add("  Not run: the operator did not type yes.")
        else:
            if "start_mm" in motion:
                add(f"  Start (X, Y) = {motion['start_mm']}, windows {motion['windows_text']}, test speed "
                    f"{motion['speed_mm_s']:.4f} mm/s (original {motion['original_speed_mm_s']})")
            for t in motion["tests"]:
                add(f"  - {t['title']}: {t.get('status', '')}")
                for key in ("answer", "reason"):
                    if t.get(key):
                        add(f"      {t[key]}")
                if t.get("sent"):
                    add(f"      sent: {', '.join(t['sent'])}")
            back = motion.get("move_back")
            if back:
                add(f"  Move back: {back.get('result', '')}")
            for r in motion.get("restore", []):
                add(f"  Restore {r['what']}: {r['result']}")
            for e in motion.get("emergency_stops", []):
                add(f"  EMERGENCY STOP at {e['t_s']:.2f} s: {e['reason']}")
                for a in e["attempts"]:
                    add(f"      attempt {a['attempt']}{' (port re-opened)' if a['reopened'] else ''}: {a['result']}")
            if motion.get("aborted"):
                add(f"  Stopped: {motion['aborted']}")
            if "end_mm" in motion:
                add(f"  End position (X, Y) = {motion['end_mm']}")
    add("")
    add("Problems")
    for p in data["problems"]:
        add(f"  - {p}")
    if not data["problems"]:
        add("  (none)")
    answers = data.get("operator_answers") or []
    if answers:
        add("")
        add("Operator answers")
        for a in answers:
            add(f"  {a['question']} -> {a['answer']!r} ({a['meaning']})")
    add("")
    add("Frames (tx = sent by this PC, rx = answer, drop = input discarded before a command; t in s)")
    for f in data.get("frames", []):
        add(f"  {f['t_s']:9.3f} {f['dir']:<4} {f['text']}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------------------------- the check
class XYCheck:
    """Probes, information and the report data (the motion tests: :class:`MotionSession`)."""

    def __init__(self, args: argparse.Namespace, target: Target, env: Environment, console: Console,
                 report: Report) -> None:
        self.args = args
        self.target = target
        self.env = env
        self.console = console
        self.report = report
        self.log = FrameLog(env.monotonic)
        self.factory = zc.recording_factory(env.link_factory or GcsSerialLink, self.log)
        self.timeout_s = args.timeout_ms / 1000.0
        self.devices = target.devices
        self.probes: list[ProbeResult] = []
        self.variant: ChainVariant | None = None
        self.chain: GcsChain | None = None
        self.values: dict[str, dict[str, Any]] = {axis: {} for axis in AXES}

    def device_of(self, axis: str) -> int:
        return self.devices[AXES.index(axis)]

    # ============================================================================================== failures
    def link_failure(self, what: str, exc: StageError) -> None:
        c = self.console
        c.line()
        c.line(f"{what}: {describe(exc)}")
        hints = advice(exc, self.target.port, self.args.timeout_ms)
        for hint in hints:
            c.line(hint)
        self.report.problem(f"{what}: {describe(exc)}" + (f" -- {' '.join(h.strip() for h in hints)}" if hints else ""))

    # ============================================================================================== probes
    def run_probes(self) -> bool:
        """The raw protocol probes; False when the port could not be opened or the link was lost."""
        c, t = self.console, self.target
        conn = self.report.data["connection"]
        conn.update(port=t.port, baudrate=t.baudrate, source=t.source, timeout_ms=self.args.timeout_ms,
                    probe_timeout_ms=self.args.probe_timeout_ms, fake=bool(self.args.fake))
        c.line(f"Port {t.port} at {t.baudrate} Bd ({t.source}); X = device {self.devices[0]}, Y = device "
               f"{self.devices[1]}.")
        c.line("Protocol probes: which address form and which single-byte commands the controllers answer (read-only;")
        c.line(f"a form they do not understand only goes unanswered, {self.args.probe_timeout_ms} ms each).")
        try:
            link = self.factory(t.port, t.baudrate, self.args.probe_timeout_ms / 1000.0)
        except StageError as exc:
            self.link_failure("Opening the port failed", exc)
            return False
        prober = Prober(link, self.args.probe_timeout_ms / 1000.0)
        try:
            for command in ("*IDN?", "ERR?", "#5", "#7"):
                self._probe(prober, "plain", None, command)
            for device in self.devices:
                for form in ("target_only", "target_sender"):
                    self._probe(prober, form, device, "*IDN?")
            variant = analyse_probes(self.probes, self.devices)
            if variant.address_mode is not None:
                for device in self.devices:
                    for command in ("ERR?", "#5", "#7", "SRG? 1 1", "RMC?", "ERR?"):
                        self._probe(prober, variant.address_mode, device, command)
                variant = analyse_probes(self.probes, self.devices)
        except StageError as exc:
            self.link_failure("The link failed during the probes", exc)
            return False
        finally:
            link.close()
        self.variant = variant
        self._report_variant()
        return True

    def _probe(self, prober: Prober, form: str, device: int | None, command: str) -> ProbeResult:
        index = sum(1 for p in self.probes if (p.form, p.device, p.command) == (form, device, command))
        key = f"{form}:{device if device is not None else '-'}:{command}" + (f"#{index + 1}" if index else "")
        p = ProbeResult(key, form, device, command, probe_frame(form, device, command))
        t0 = self.env.monotonic()
        p.raw, p.timed_out = prober.exchange(p.sent)
        p.ms = round((self.env.monotonic() - t0) * 1000.0, 1)
        judge(p)
        self.probes.append(p)
        self.report.data["probes"]["exchanges"].append(p.record())
        who = "no address" if device is None else f"device {device} {form}"
        self.console.line(f"  {command_text(p.sent):<14} ({who}): {'ok' if p.ok else '--'}  {p.meaning}"
                          f"   [{frame_text(p.raw) or 'nothing'}]")
        if command in ("#5", "#7") and form != "plain" and not p.ok:
            flush = ProbeResult(f"flush after {key}", "plain", None, "(LF)", b"\n", ok=True)
            flush.raw, flush.timed_out = prober.exchange(b"\n", QUIET_S)
            received = f"; received '{frame_text(flush.raw)}'" if flush.raw else ""
            flush.meaning = f"flush: ends a partial line{received}"
            self.report.data["probes"]["exchanges"].append(flush.record())
        return p

    def _report_variant(self) -> None:
        c, r, v, t = self.console, self.report, self.variant, self.target
        assert v is not None
        code = v.code(t.port, t.baudrate, self.timeout_s)
        r.data["daisy_chain"] = {"address_mode": v.address_mode, "char_mode": v.char_mode, "notes": v.notes,
                                 "code": code}
        c.line()
        c.line(f"DAISY-CHAIN VARIANT: {code}")
        for note in v.notes:
            c.line(f"  - {note}")
        r.finding("chain", "Daisy-chain settings for the application", code)
        r.finding("chain", "Address forms", "; ".join(v.notes[:2]))
        plain = next((p for p in self.probes if p.key == "plain:-:*IDN?"), None)
        if plain is not None:
            r.finding("chain", "Command without address",
                      f"answered: {plain.value!r} ({plain.meaning})" if plain.ok else f"{plain.meaning}")
        if v.address_mode is None:
            r.problem("no address form was answered by both controllers: check the device numbers (DIP switches, "
                      "alox.ini xaxis_I32 / yaxis_I32) and the cables; the information and motion parts are not run")
            return
        sevens = [p for p in self.probes if p.command == "#7" and p.form == v.address_mode and p.ok]
        if sevens:
            r.finding("chain", "#7 answer (addressed)", "; ".join(f"device {p.device}: '{frame_text(p.raw)}' "
                                                               f"({p.meaning})" for p in sevens))
        empties = [p for p in self.probes if p.command == "RMC?" and p.ok]
        if empties:
            r.finding("chain", "Empty answer (RMC? without a running macro)",
                      "; ".join(f"device {p.device}: {p.meaning}" for p in empties))
        if v.char_mode is None:
            r.problem("neither addressed #5/#7 nor SRG? 1 1 was answered by both controllers: the motion tests "
                      "cannot wait for the end of a move and are not run")
        elif v.char_mode == "text_equivalents":
            r.finding("chain", "Single-byte commands", "addressed #5/#7 are NOT answered: the application must use "
                      "char_mode='text_equivalents' (SRG? 1 1 and STP)")

    # ============================================================================================== information
    def open_chain(self) -> GcsChain:
        v = self.variant
        assert v is not None and v.address_mode is not None
        if self.chain is None:
            self.chain = GcsChain(self.target.port, self.target.baudrate, self.timeout_s, link_factory=self.factory,
                                  address_mode=v.address_mode, char_mode=v.char_mode or "addressed")
        return self.chain

    def run_info(self) -> bool:
        c = self.console
        chain = self.open_chain()
        for axis in AXES:
            device = self.device_of(axis)
            self.report.data["info"][axis]["device"] = device
            c.section(f"Information {axis.upper()} (device {device}) - read-only")
            try:
                view = chain.view(device)
            except StageError as exc:
                self.link_failure("Opening the port failed", exc)
                return False
            try:
                for step in INFO_STEPS:
                    if step.x_only and axis != "x":
                        continue
                    entry = self._query(axis, view, step)
                    if entry.get("link_lost"):
                        self.report.problem("the link was lost during the information queries")
                        return False
            finally:
                view.close()
        failed = sum(1 for axis in AXES for q in self.report.data["info"][axis]["queries"] if not q["ok"])
        if failed:
            self.report.problem(f"{failed} information queries failed (see the report)")
        c.line()
        c.line(f"Information done: {failed} queries failed.")
        return True

    def _query(self, axis: str, view: ChainLink, step: InfoStep) -> dict[str, Any]:
        c = self.console
        mark = self.log.mark()
        entry: dict[str, Any] = {"frame": step.frame, "key": step.key}
        self.report.data["info"][axis]["queries"].append(entry)
        try:
            value = step.parse(view.query(step.frame), step.frame)
        except StageError as exc:
            entry.update(ok=False, code=exc.code, error=describe(exc),
                         raw=[frame_text(f.data) for f in self.log.since(mark) if f.direction != TX])
            c.line(f"  {step.frame:<10} FAILED: {describe(exc)}")
            if exc.code in LINK_ERRORS:
                entry["link_lost"] = True
                self.link_failure(f"{step.frame} failed", exc)
                return entry
            try:
                entry["err_after"] = show_err(_err(view.query("ERR?"), "ERR?"))
            except StageError as err:
                entry["err_after"] = f"ERR? failed too: {describe(err)}"
            c.line(f"  {'':<10} ERR? afterwards: {entry['err_after']}")
            return entry
        self.values[axis][step.key] = value
        entry.update(ok=True, value=zc._jsonable(value), shown=step.show(value),
                     raw=[frame_text(f.data) for f in self.log.since(mark) if f.direction != TX])
        c.line(f"  {step.frame:<10} {entry['shown']}")
        return entry

    def info_findings(self) -> None:
        r, v = self.report, self.values
        if all("idn" in v[a] for a in AXES):
            r.finding("owner", "Controllers (*IDN?)", "; ".join(f"{a.upper()}: {v[a]['idn']!r}" for a in AXES))
        if any("cst" in v[a] for a in AXES):
            r.finding("owner", "Stage models (CST? 1)", "; ".join(f"{a.upper()}: {v[a].get('cst', '?')!r}"
                                                                   for a in AXES))
        if all("tmn" in v[a] and "tmx" in v[a] for a in AXES):
            r.finding("XY", "Travel ranges (TMN? 1 .. TMX? 1)",
                      "; ".join(f"{a.upper()}: {v[a]['tmn']:.4f} .. {v[a]['tmx']:.4f} mm" for a in AXES))
        for a in AXES:
            if "trs" in v[a] and "lim" in v[a]:
                how = "FRF 1 (reference switch)" if v[a]["trs"] else "FNL 1 (negative limit switch)" if v[a]["lim"] \
                    else "nothing (neither switch)"
                r.finding("XY", f"{a.upper()} reference / limit switches",
                          f"TRS? 1 = {int(v[a]['trs'])}, LIM? 1 = {int(v[a]['lim'])}: the set-up references with {how}")
        for p in self.probes:                       # the SRG? bit assumptions against SVO? 1
            if p.command == "SRG? 1 1" and p.ok and p.device in self.devices:
                axis = AXES[self.devices.index(p.device)]
                if "svo" in v[axis]:
                    servo_bit = bool(p.value & (1 << 12))
                    same = servo_bit == bool(v[axis]["svo"])
                    r.finding("chain", f"SRG? 1 1 bits, device {p.device}",
                              f"{describe_register(p.value)}; bit 12 {'matches' if same else 'does NOT match'} SVO? 1 "
                              f"= {int(v[axis]['svo'])}; in motion = bit 13 (0x{SRG_IN_MOTION:04X}), referencing = "
                              f"bit 14 (0x{SRG_REFERENCING:04X}) assumed")

    # ============================================================================================== run
    def run(self) -> int:
        c = self.console
        c.section(f"XY stage check {TOOL_VERSION} (aloxcam {ALOXCAM_VERSION}) - read-only probes and information")
        if self.args.fake:
            c.line("REHEARSAL (--fake): two emulated C-663 inside this program; no serial port is opened.")
        for warning in self.target.ini_warnings:
            c.line(f"alox.ini warning: {warning}")
        if not self.run_probes():
            return EXIT_PROBLEM
        assert self.variant is not None
        if not self.variant.usable:
            c.line("No address form works for both controllers: the information and motion parts are not run.")
            return EXIT_PROBLEM
        ok = self.run_info()
        self.info_findings()
        if not ok:
            return EXIT_PROBLEM
        if self.args.motion and MotionSession(self).run():
            return EXIT_PROBLEM
        return EXIT_OK

    def close(self) -> None:
        if self.chain is not None:
            try:
                self.chain.close()
            except Exception as exc:  # noqa: BLE001 - never hide the report because of a close failure
                self.report.problem(f"closing the port failed: {describe(exc)}")


# ---------------------------------------------------------------------------------------------- motion tests
@dataclass(frozen=True)
class Step:
    key: str
    title: str
    question: str


MOTION_STEPS: tuple[Step, ...] = (
    Step("relative_x", "X relative moves", "Do MVR +d / -d on X arrive where expected (POS? 1)?"),
    Step("relative_y", "Y relative moves", "Do MVR +d / -d on Y arrive where expected (POS? 1)?"),
    Step("wafer_refusal", "Inside-wafer refusal", "Is a move to a destination outside the wafer refused (9404) "
         "without moving?"),
    Step("stp_during_move", "STP during a move", "Does the device's Stop (STP 1 on both) stop a moving stage?"),
)


@dataclass(frozen=True)
class Sample:
    t: float
    moving: bool
    x: float
    y: float


class MotionSession:
    """The guided motion tests; every rule of the module docstring's "Safety rules" is here."""

    def __init__(self, check: XYCheck) -> None:
        self.check = check
        self.console = check.console
        self.env = check.env
        self.args = check.args
        self.data: dict[str, Any] = {"requested": True, "tests": [], "emergency_stops": [], "restore": []}
        check.report.data["motion"] = self.data
        self.xy: XYStage | None = None
        self.armed = False
        self.speed_touched = False
        self.quit = False
        self.start = (0.0, 0.0)
        self.speed = float(self.args.speed)
        self.original_speed = {"x": 0.0, "y": 0.0}
        self.windows: dict[str, Window] = {}
        self.limits: dict[str, list[tuple[str, float, float]]] = {}

    # ============================================================================================== helpers
    def _now(self) -> float:
        return self.env.monotonic()

    def _timeout(self, distance: float) -> float:
        return MOVE_TIMEOUT_MIN_S + MOVE_TIMEOUT_FACTOR * abs(distance) / self.speed

    def _windows_text(self) -> str:
        return ", ".join(f"{a.upper()} {w.text()}" for a, w in self.windows.items())

    def _device(self) -> XYStage:
        assert self.xy is not None
        return self.xy

    def _room(self, axis: str, pos: float) -> tuple[int, float]:
        w = self.windows[axis]
        up, down = w.hi - pos, pos - w.lo
        return (1, up) if up >= down else (-1, down)

    # ============================================================================================== refusal and plan
    def _refusal(self) -> str | None:
        v, variant = self.check.values, self.check.variant
        assert variant is not None
        if variant.char_mode is None:
            return "no single-byte variant works (the end of a move cannot be waited for)"
        for axis in AXES:
            missing = [k for k in ("tmn", "tmx", "pos", "vel", "jon") if k not in v[axis]]
            if missing:
                return f"{axis.upper()}: these values could not be read: {', '.join(missing)}"
            if v[axis]["jon"]:
                return (f"the joystick is on ({axis.upper()} JON? 1 = 1): moves would be refused. Switch it off with "
                        "LabVIEW (Disable Joystick), then run this tool again.")
            if not (math.isfinite(v[axis]["vel"]) and v[axis]["vel"] > 0):
                return f"{axis.upper()}: the speed is not a positive number (VEL? 1 = {v[axis]['vel']!r})"
        self.original_speed = {a: float(v[a]["vel"]) for a in AXES}
        self.speed = float(self.args.speed)
        if not self.args.fake:
            self.speed = min(self.speed, *self.original_speed.values())
        if self.speed < MIN_SPEED:
            return (f"a controller's speed is very low ({self.original_speed}) and the tool never moves faster than "
                    "it: set the normal speed with LabVIEW (Setup) first")
        try:
            self._set_windows((float(v["x"]["pos"]), float(v["y"]["pos"])))
        except Refusal as exc:
            return str(exc)
        return None

    def _set_windows(self, start: tuple[float, float]) -> None:
        v, half = self.check.values, float(self.args.window)
        self.start = start
        for axis, pos in zip(AXES, start, strict=True):
            limits = [("controller travel range (TMN? 1 .. TMX? 1)", float(v[axis]["tmn"]), float(v[axis]["tmx"]))]
            if axis in self.check.target.ranges:
                lo, hi = self.check.target.ranges[axis]
                limits.append((f"alox.ini [hardware.xy] {axis}min .. {axis}max", lo, hi))
            for name, lo, hi in limits:
                if not lo <= pos <= hi:
                    raise Refusal(f"{axis.upper()} is at {pos:.4f} mm, outside the {name} {lo:.4f} .. {hi:.4f} mm. "
                                  "Move it inside with LabVIEW first.")
            limits.append((f"start position +/- {half:.4f} mm (--window)", pos - half, pos + half))
            self.limits[axis] = limits
            self.windows[axis] = Window(max(lo for _, lo, _ in limits), min(hi for _, _, hi in limits))

    def _print_plan(self) -> None:
        c = self.console
        c.line(f"Start position   X {self.start[0]:.4f} mm, Y {self.start[1]:.4f} mm (POS? 1)")
        for axis in AXES:
            c.line(f"Allowed window {axis.upper()} {self.windows[axis].text()}: the intersection of")
            for name, lo, hi in self.limits[axis]:
                c.line(f"                   {lo:.4f} .. {hi:.4f} mm  {name}")
        c.line(f"Test speed       {self.speed:.4f} mm/s (now: X {self.original_speed['x']:.4f}, Y "
               f"{self.original_speed['y']:.4f} mm/s; restored at the end)")
        c.line("Steps (each one is explained first and needs its own yes): 0. set-up, then")
        for index, step in enumerate(MOTION_STEPS, 1):
            c.line(f"  {index}. {step.title}")
        c.line()
        c.line("Before you type yes: make sure nothing on or near the stages can be hit or damaged by a move inside")
        c.line("the windows, and keep the controllers' power switch (or the machine's emergency stop) within reach.")
        c.line("Press Ctrl+C at any time to stop: the tool sends #24 (stop all axes) to both controllers.")

    # ============================================================================================== watching moves
    def _read(self) -> Sample:
        xy = self._device()
        x, y = xy.position()
        return Sample(0.0, xy.is_moving(), x, y)

    def watch(self, t_cmd: float, windows: dict[str, Window], timeout_s: float, *,
              target: tuple[float, float] | None = None, until: Callable[[Sample], bool] | None = None,
              on_timeout: str = "stop") -> tuple[list[Sample], str]:
        """``POS?`` and ``#5`` of both axes every :data:`POLL_S` until standstill (at ``target`` when given), or
        ``until(sample)``. A position outside its window sends ``#24`` and raises :class:`SafetyStop`; so does a
        timeout unless ``on_timeout == "return"``."""
        samples: list[Sample] = []
        while True:
            read = self._read()
            sample = Sample(round(self._now() - t_cmd, 4), read.moving, read.x, read.y)
            samples.append(sample)
            for axis, pos in (("x", sample.x), ("y", sample.y)):
                if not windows[axis].contains(pos, WINDOW_MARGIN_MM):
                    reason = (f"{axis.upper()} left the allowed range {windows[axis].text()}: POS? 1 = {pos:.4f} mm")
                    self.emergency_stop(reason)
                    raise SafetyStop(reason, stop_sent=True)
            if until is not None and until(sample):
                return samples, "until"
            arrived = target is None or (abs(sample.x - target[0]) <= AT_TARGET_MM
                                         and abs(sample.y - target[1]) <= AT_TARGET_MM)
            if not sample.moving and (arrived or sample.t >= 0.3):
                return samples, "stopped"
            if sample.t > timeout_s:
                if on_timeout == "return":
                    return samples, "timeout"
                reason = f"timeout: a stage still moved {timeout_s:.1f} s after the command"
                self.emergency_stop(reason)
                raise SafetyStop(reason, stop_sent=True)
            self.env.sleep(POLL_S)

    @staticmethod
    def _samples(samples: Sequence[Sample]) -> list[list[Any]]:
        return [[s.t, int(s.moving), s.x, s.y] for s in samples]

    # ============================================================================================== stopping
    def _send_stop(self) -> str | None:
        """``#24`` to both controllers through fresh views (a broken port is re-opened by the chain)."""
        chain = self.check.chain
        if chain is None:
            return "no port"
        errors = []
        for device in self.check.devices:
            try:
                view = chain.view(device)
                try:
                    view.send_char(0x18)
                finally:
                    view.close()
            except KeyboardInterrupt:
                errors.append(f"device {device}: interrupted by Ctrl+C")
            except Exception as exc:  # noqa: BLE001 - any failure: stop the stages / still write the report
                errors.append(f"device {device}: {describe(exc)}")
        return "; ".join(errors) or None

    def emergency_stop(self, reason: str, *, link_error: bool = False) -> bool:
        c = self.console
        c.line()
        c.line(f"!!! STOP: {reason}")
        c.line("!!! Sending #24 (stop all axes) to both controllers now.")
        event: dict[str, Any] = {"t_s": round(self.check.log.elapsed(), 3), "reason": reason, "attempts": []}
        self.data["emergency_stops"].append(event)
        certain = False
        for attempt in (1, 2, 3):
            reopened = attempt > 1 or link_error
            if reopened and self.check.chain is not None:
                self.check.chain._broken = True             # the next view re-opens the port
            error = self._send_stop()
            event["attempts"].append({"attempt": attempt, "reopened": reopened, "result": error or "#24 sent"})
            if error is None:
                certain = True
                break
            c.line(f"!!! #24 not confirmed ({error}): re-opening the port and sending it again.")
        event["sent"] = certain
        if not certain:
            c.line("!!! COULD NOT CONFIRM THAT #24 REACHED THE CONTROLLERS. If a stage moves, stop it by hand:")
            c.line("!!! switch off the C-663 controllers (or use the machine's emergency stop).")
            self.check.report.problem(f"emergency stop ({reason}): #24 could not be confirmed")
            return False
        c.line(f"!!! #24 sent to both controllers (attempt {len(event['attempts'])}).")
        self._wait_standstill(event)
        return True

    def _wait_standstill(self, event: dict[str, Any]) -> None:
        chain = self.check.chain
        assert chain is not None
        t0 = self._now()
        try:
            views = [chain.view(d) for d in self.check.devices]
            try:
                while True:
                    moving = [int(v.query_char(0x05).strip() or "0", 16) != 0 for v in views]
                    if not any(moving):
                        pos = [_float(v.query("POS? 1"), "POS? 1") for v in views]
                        event.update(standstill=True, position_mm=pos)
                        self.console.line(f"The stages stand still at X {pos[0]:.4f} mm, Y {pos[1]:.4f} mm.")
                        return
                    if self._now() - t0 > STANDSTILL_WAIT_S:
                        event["standstill"] = False
                        self.console.line("!!! A stage STILL MOVES after #24. Stop it by hand: switch off the "
                                          "controllers.")
                        self.check.report.problem("a stage still moved after #24")
                        return
                    self.env.sleep(POLL_S)
            finally:
                for v in views:
                    v.close()
        except KeyboardInterrupt:
            event["standstill"] = "not checked (Ctrl+C)"
        except Exception as exc:  # noqa: BLE001 - any failure: stop the stages / still write the report
            event["standstill"] = f"could not be checked: {describe(exc)}"

    def _guarded(self, step: Callable[[], None]) -> str | None:
        """Run ``step``; on Ctrl+C, any error or a :class:`SafetyStop`, send ``#24`` (unless already sent) and return
        the reason. None when ``step`` ended normally."""
        handled = False
        try:
            step()
            handled = True
            return None
        except SafetyStop as exc:
            if not exc.stop_sent:
                self.emergency_stop(exc.reason)
            handled = True
            return exc.reason
        except KeyboardInterrupt:
            self.emergency_stop("Ctrl+C pressed")
            handled = True
            return "Ctrl+C pressed by the operator"
        except Exception as exc:  # noqa: BLE001 - any failure: stop the stages / still write the report
            self.data.setdefault("errors", []).append(traceback.format_exc())
            code = getattr(exc, "code", None)
            reason = f"unexpected error: {describe(exc) if isinstance(exc, StageError) else exc}"
            self.emergency_stop(reason, link_error=code in LINK_ERRORS)
            handled = True
            return reason
        finally:
            if not handled:
                self.emergency_stop("the tool is stopping")

    # ============================================================================================== the session
    def run(self) -> str | None:
        """The whole motion phase; returns why it was stopped (#24 sent), or None."""
        c = self.console
        c.section("Motion tests - THE STAGES WILL MOVE")
        refusal = self._refusal()
        if refusal:
            return self._refuse(refusal)
        self.data.update(start_mm=list(self.start), speed_mm_s=self.speed, original_speed_mm_s=self.original_speed,
                         windows={a: {"lo": w.lo, "hi": w.hi} for a, w in self.windows.items()},
                         windows_text=self._windows_text())
        self._print_plan()
        if c.ask("Type yes and press Enter to start the motion tests (anything else stops here):") != "yes":
            c.line("Motion tests not started. Nothing was moved.")
            self.data["declined"] = True
            return None
        moved = self._moved_since_read()
        if moved:
            return self._refuse(moved)
        abort: str | None = "the tool stopped unexpectedly"
        try:
            self.armed = True
            abort = self._guarded(self._setup)
            if abort is None and self.xy is not None and self.xy.state == "ready":
                abort = self._guarded(self._run_tests)
                if abort is None:
                    abort = self._guarded(self._move_back)
        finally:
            self._finish(abort)
        return abort

    def _views(self) -> list[ChainLink]:
        chain = self.check.open_chain()
        return [chain.view(d) for d in self.check.devices]

    def _moved_since_read(self) -> str | None:
        """Why the stages are not where the information read them (``POS? 1`` and ``#5`` again), or None."""
        try:
            views = self._views()
            try:
                pos = [_float(v.query("POS? 1"), "POS? 1") for v in views]
                moving = [int(v.query_char(0x05).strip() or "0", 16) != 0 for v in views]
            finally:
                for v in views:
                    v.close()
        except StageError as exc:
            return f"the stages could not be read again ({describe(exc)}). Nothing was moved."
        if any(moving) or any(abs(p - s) > AT_TARGET_MM for p, s in zip(pos, self.start, strict=True)):
            return (f"the stages moved since they were read (now X {pos[0]:.4f}, Y {pos[1]:.4f} mm, #5 = "
                    f"{[int(m) for m in moving]}). Nothing was moved by the tool; run it again.")
        return None

    def _refuse(self, reason: str) -> None:
        self.console.line(f"Motion tests NOT run: {reason}")
        self.data["refused"] = reason
        self.check.report.problem(f"motion tests not run: {reason}")

    # ============================================================================================== step 0: set-up
    def _setup(self) -> None:
        c, check = self.console, self.check
        v = check.values
        unreferenced = [a for a in AXES if not v[a].get("frf")]
        c.section("Step 0: set-up (servo on, test speed)")
        c.line("For each controller, through the application's XY device: XXX, *idn?, err?, *IDN?, SAI?, CST? 1,")
        c.line(f"SVO 1 1 (servo ON), SVO? 1, RON? 1, FRF? 1, TMN? 1, TMX? 1, POS? 1, ERR?, VEL 1 "
               f"{format_gcs_float(self.speed)}, ERR?.")
        entry: dict[str, Any] = {"key": "setup", "title": "Set-up", "status": "not started"}
        self.data["tests"].append(entry)
        if unreferenced:
            names = " and ".join(a.upper() for a in unreferenced)
            c.line(f"{names} {'is' if len(unreferenced) == 1 else 'are'} NOT referenced (FRF? 1 = 0): the set-up "
                   "references it: FRF 1 (reference switch) or FNL 1 (negative limit switch). THE STAGE DRIVES TO THE")
            c.line("SWITCH, OUTSIDE THE WINDOW. Make sure the whole travel is free.")
        if c.ask("Type yes to run the set-up (anything else stops the motion tests here):") != "yes":
            entry["status"] = "skipped"
            c.line("Set-up skipped: the motion tests stop here. Nothing was moved.")
            return
        if unreferenced and c.ask("Type yes again to confirm the reference move:") != "yes":
            entry["status"] = "skipped (no reference move)"
            c.line("No reference move: the motion tests stop here (moves need referenced axes). Nothing was moved.")
            return
        xy = self.xy = self._build_device()
        mark = check.log.mark()
        t0 = self._now()
        self.speed_touched = True
        try:
            xy.open(timeout=2 * SETUP_READY_TIMEOUT_S + 60.0)
        finally:
            entry["sent"] = check.log.sent_since(mark)
        x, y = xy.position()
        entry.update(status="done", duration_s=round(self._now() - t0, 3), identities=list(xy.identities),
                     position_mm=[x, y], answer=f"set-up done in {self._now() - t0:.2f} s; X {x:.4f} mm, Y {y:.4f} mm")
        c.line(f"Set-up done: X {x:.4f} mm, Y {y:.4f} mm.")
        if unreferenced:                                    # a reference move: new start, new windows
            self._set_windows((x, y))
            self.data.update(start_mm=[x, y], windows_text=self._windows_text(), start_after_reference=True)
            c.line(f"New start position after the reference move; windows {self._windows_text()}.")
        elif abs(x - self.start[0]) > AT_TARGET_MM or abs(y - self.start[1]) > AT_TARGET_MM:
            raise SafetyStop(f"the stages moved during the set-up (now X {x:.4f}, Y {y:.4f} mm)")

    def _build_device(self) -> XYStage:
        check, t = self.check, self.check.target
        chain = check.open_chain()
        axes = {a: replace(getattr(t.settings, a), device=check.device_of(a), baudrate=t.baudrate, speed=self.speed,
                           slow_speed=self.speed) for a in AXES}
        settings = XYSettings(x=axes["x"], y=axes["y"], wafer=WaferSettings(diameter_mm=0.0))   # no wafer check
        port_map = {f"COM{s.com if s.com > 0 else 1}": t.port for s in axes.values()}
        options = StageOptions(always_reference=False, ready_timeout_s=SETUP_READY_TIMEOUT_S,
                               wait_timeout_s=60.0, port_map=port_map)
        backends = [PiSerialStage(link_factory=chain.link_factory(check.device_of(a)), sleep=self.env.sleep)
                    for a in AXES]
        return XYStage(backends[0], backends[1], settings, options, sleep=self.env.sleep,
                       monotonic=self.env.monotonic, supervise=False, auto_reconnect=False)

    # ============================================================================================== steps 1-4
    def _run_tests(self) -> None:
        for index, step in enumerate(MOTION_STEPS, 1):
            if self.quit:
                self.data["tests"].append({"key": step.key, "title": step.title, "status": "skipped (quit)"})
                continue
            self._run_one(index, step)

    def _run_one(self, index: int, step: Step) -> None:
        c = self.console
        c.section(f"Step {index} of {len(MOTION_STEPS)}: {step.title}")
        c.line(f"Question: {step.question}")
        entry: dict[str, Any] = {"key": step.key, "title": step.title, "question": step.question,
                                 "status": "not started"}
        self.data["tests"].append(entry)
        now = self._read()
        if now.moving:
            raise SafetyStop("a stage is moving before the step")
        for axis, pos in (("x", now.x), ("y", now.y)):
            if not self.windows[axis].contains(pos, WINDOW_MARGIN_MM):
                raise SafetyStop(f"{axis.upper()} is at {pos:.4f} mm, outside its window, before the step")
        try:
            plan: dict[str, Any] = getattr(self, f"_plan_{step.key}")(now)
        except Refusal as exc:
            entry.update(status="refused", reason=str(exc))
            c.line(f"Step refused: {exc}")
            return
        c.line("What will happen:")
        for line in plan["lines"]:
            c.line(f"  {line}")
        answer = c.ask(TEST_QUESTION)
        if answer == "quit":
            self.quit = True
        if answer != "yes":
            entry["status"] = "skipped"
            c.line("Skipped.")
            return
        mark = self.check.log.mark()
        entry["status"] = "running"
        try:
            result: dict[str, Any] = getattr(self, f"_run_{step.key}")(plan)
        finally:
            entry["sent"] = self.check.log.sent_since(mark)
        entry.update(result)
        entry["status"] = result.get("status", "done")
        c.line(f"Result: {result['answer']}")
        self.check.report.finding("motion", step.title, result["answer"])

    def _relative_plan(self, axis: str, now: Sample) -> dict[str, Any]:
        pos = now.x if axis == "x" else now.y
        sign, room = self._room(axis, pos)
        step = min(float(self.args.step), room)
        if step < MIN_STOP_TRAVEL_MM:
            raise Refusal(f"only {room:.4f} mm of free travel inside the {axis.upper()} window")
        d = float(format_gcs_float(sign * step))
        return {"axis": axis, "d": d, "start": (now.x, now.y),
                "lines": [(f"MVR 1 {format_gcs_float(d)} on {axis.upper()} (from {pos:.4f} to {pos + d:.4f} mm, "
                           f"about {abs(d) / self.speed:.1f} s), ERR?; POS? 1 / #5 of both axes until it stands "
                           "still;"),
                          (f"then MVR 1 {format_gcs_float(-d)} back to {pos:.4f} mm, the same way. Each position is "
                           f"checked (+/- {AT_TARGET_MM} mm).")]}

    def _plan_relative_x(self, now: Sample) -> dict[str, Any]:
        return self._relative_plan("x", now)

    def _plan_relative_y(self, now: Sample) -> dict[str, Any]:
        return self._relative_plan("y", now)

    def _move_and_check(self, dx: float, dy: float, expected: tuple[float, float]) -> dict[str, Any]:
        xy = self._device()
        t = self._now()
        xy.move_relative(dx, dy, wait=False)
        samples, _ = self.watch(t, self.windows, self._timeout(max(abs(dx), abs(dy))), target=expected)
        xy.wait_end_move(polling_ms=10)                     # #5 on both, then ERR? on both
        x, y = xy.position()
        off = max(abs(x - expected[0]), abs(y - expected[1]))
        return {"expected_mm": list(expected), "reached_mm": [x, y], "off_mm": round(off, 6),
                "ok": off <= AT_TARGET_MM, "samples": self._samples(samples)}

    def _run_relative(self, plan: dict[str, Any]) -> dict[str, Any]:
        axis, d, (sx, sy) = plan["axis"], plan["d"], plan["start"]
        dx, dy = (d, 0.0) if axis == "x" else (0.0, d)
        out = self._move_and_check(dx, dy, (sx + dx, sy + dy))
        back = self._move_and_check(-dx, -dy, (sx, sy))
        ok = out["ok"] and back["ok"]
        answer = (f"MVR {format_gcs_float(d)} reached {axis.upper()} = {out['reached_mm'][AXES.index(axis)]:.4f} mm "
                  f"(off {out['off_mm']:.4f}), back to {back['reached_mm'][AXES.index(axis)]:.4f} mm (off "
                  f"{back['off_mm']:.4f}): {'OK' if ok else 'POSITION MISMATCH'}")
        return {"status": "done" if ok else "mismatch", "answer": answer, "out": out, "back": back}

    def _run_relative_x(self, plan: dict[str, Any]) -> dict[str, Any]:
        return self._run_relative(plan)

    def _run_relative_y(self, plan: dict[str, Any]) -> dict[str, Any]:
        return self._run_relative(plan)

    def _plan_wafer_refusal(self, now: Sample) -> dict[str, Any]:
        sign, room = self._room("x", now.x)
        d = float(format_gcs_float(sign * min(float(self.args.step), room)))
        # the device ignores the check when an origin coordinate is exactly 0 (LabVIEW rule): nudge it off zero
        origin = (now.x if now.x != 0.0 else TEST_WAFER_MM / 4, now.y if now.y != 0.0 else TEST_WAFER_MM / 4)
        if math.hypot(now.x + d - origin[0], now.y - origin[1]) <= TEST_WAFER_MM / 2 + WINDOW_MARGIN_MM:
            raise Refusal(f"only {room:.4f} mm of free travel inside the X window: the test destination would be on "
                          "the test wafer")
        return {"d": d, "origin": origin, "start": (now.x, now.y),
                "lines": [(f"A test wafer centred at X {origin[0]:.4f}, Y {origin[1]:.4f} mm with a move radius of "
                           f"{TEST_WAFER_MM / 2} mm is set in the device (memory only)."),
                          (f"Then a relative move of {format_gcs_float(d)} mm on X: its destination is outside the "
                           "wafer, so it must be refused with error 9404 and nothing may move."),
                          "POS? 1 of both axes before and after; then the test wafer is removed."]}

    def _run_wafer_refusal(self, plan: dict[str, Any]) -> dict[str, Any]:
        xy = self._device()
        before = xy.wafer
        xy.set_wafer(diameter_mm=TEST_WAFER_MM, origin=plan["origin"], move_radius_mm=TEST_WAFER_MM / 2)
        mark = self.check.log.mark()
        code: int | None = None
        try:
            xy.move_relative(plan["d"], 0.0, wait=False)
        except DeviceError as exc:
            if exc.code != OUTSIDE_WAFER:
                raise
            code = exc.code
        finally:
            xy.set_wafer(diameter_mm=before.diameter_mm, origin=before.origin, move_radius_mm=before.move_radius_mm)
        moving_frames = [f for f in self.check.log.sent_since(mark) if re.search(r"\b(MOV|MVR)\b", f)]
        if code is None:                                    # accepted: the move must be watched and stopped safely
            self.watch(self._now(), self.windows, self._timeout(plan["d"]))
        x, y = xy.position()
        still = abs(x - plan["start"][0]) <= AT_TARGET_MM and abs(y - plan["start"][1]) <= AT_TARGET_MM
        ok = code == OUTSIDE_WAFER and not moving_frames and still
        answer = (f"move refused with {code} and nothing moved (X {x:.4f}, Y {y:.4f} mm, no MOV/MVR sent)" if ok else
                  f"NOT refused as expected: error {code}, frames {moving_frames}, X {x:.4f}, Y {y:.4f} mm")
        return {"status": "done" if ok else "failed", "answer": answer, "error_code": code,
                "moving_frames": moving_frames, "position_mm": [x, y]}

    def _plan_stp_during_move(self, now: Sample) -> dict[str, Any]:
        sign, room = self._room("x", now.x)
        if room < MIN_STOP_TRAVEL_MM:
            raise Refusal(f"only {room:.4f} mm of free travel inside the X window")
        d = float(format_gcs_float(sign * room))
        stop_at = ACT_FRACTION * abs(d) / self.speed
        return {"d": d, "start": (now.x, now.y), "stop_at_s": stop_at,
                "lines": [(f"MVR 1 {format_gcs_float(d)} on X (to {now.x + d:.4f} mm, about "
                           f"{abs(d) / self.speed:.1f} s), ERR?."),
                          (f"After about {stop_at:.2f} s: the device's Stop: STP 1 on X and Y, then ERR? on both (10 "
                           "is expected and cleared)."),
                          f"POS? 1 / #5 until it stands still; #24 if X still moves {STOP_GRACE_S:.0f} s after STP."]}

    def _run_stp_during_move(self, plan: dict[str, Any]) -> dict[str, Any]:
        xy = self._device()
        d, (sx, sy), stop_at = plan["d"], plan["start"], plan["stop_at_s"]
        t = self._now()
        xy.move_relative(d, 0.0, wait=False)
        samples, why = self.watch(t, self.windows, self._timeout(d), target=(sx + d, sy),
                                  until=lambda s: s.t >= stop_at)
        result: dict[str, Any] = {"samples": self._samples(samples)}
        if why != "until" or not samples[-1].moving:
            x, _ = xy.position()
            result.update(status="inconclusive", answer=f"X was not moving when STP was due (X {x:.4f} mm); STP was "
                                                        "not sent")
            return result
        at_stop = samples[-1].x
        t_stop = self._now()
        xy.stop()
        after, why_after = self.watch(t_stop, self.windows, STOP_GRACE_S, on_timeout="return")
        stopped = why_after == "stopped"
        if not stopped:
            self.console.line(f"STP did not stop X within {STOP_GRACE_S:.0f} s: sending #24.")
            self._send_stop()
            self.watch(self._now(), self.windows, self._timeout(d))
        x, y = xy.position()
        err_x, _ = xy.read_error("x")
        result.update(samples_after_stp=self._samples(after), position_at_stp_mm=at_stop, final_mm=[x, y],
                      err_after=err_x)
        target = sx + d
        if not stopped:
            verdict = f"STP did NOT stop X within {STOP_GRACE_S:.0f} s; #24 was sent; it stood still at {x:.4f} mm"
        elif abs(x - target) <= AT_TARGET_MM:
            verdict = f"STP did NOT stop X: it went on to its target {target:.4f} mm"
        else:
            verdict = (f"STP STOPPED X: it stood still {after[-1].t:.2f} s after STP at {x:.4f} mm (target "
                       f"{target:.4f} mm)")
        result.update(status="done", answer=f"{verdict}; ERR? afterwards = {show_err(err_x)}")
        return result

    # ============================================================================================== end
    def _move_back(self) -> None:
        c, xy = self.console, self._device()
        c.section("Move back to the start position")
        x, y = xy.position()
        sx, sy = self.start
        back: dict[str, Any] = {"from_mm": [x, y], "to_mm": [sx, sy]}
        self.data["move_back"] = back
        if abs(x - sx) <= AT_TARGET_MM and abs(y - sy) <= AT_TARGET_MM:
            back["result"] = "not needed: the stages are at their start position"
            c.line("The stages are at their start position: no move needed.")
            return
        path = {"x": Window(min(x, sx), max(x, sx)), "y": Window(min(y, sy), max(y, sy))}
        c.line(f"MOV to X {sx:.4f}, Y {sy:.4f} mm (from X {x:.4f}, Y {y:.4f} mm) at {self.speed:.4f} mm/s.")
        if c.ask("Type yes to move the stages back to their start position (anything else leaves them here):") != "yes":
            back["result"] = f"declined: the stages stay at X {x:.4f}, Y {y:.4f} mm"
            return
        t = self._now()
        xy.move_absolute(sx, sy, wait=False)
        samples, _ = self.watch(t, path, self._timeout(max(abs(sx - x), abs(sy - y))), target=(sx, sy))
        xy.wait_end_move(polling_ms=10)
        fx, fy = xy.position()
        back.update(samples=self._samples(samples), final_mm=[fx, fy],
                    result=f"moved back: X {fx:.4f}, Y {fy:.4f} mm (start X {sx:.4f}, Y {sy:.4f})")
        c.line(f"Back at X {fx:.4f}, Y {fy:.4f} mm.")

    def _finish(self, abort: str | None) -> None:
        """Always runs: #24 when an abnormal end has not sent it yet, the original speeds, the final positions, and the
        XY device closed."""
        c = self.console
        if abort is not None and self.armed and not self.data["emergency_stops"]:
            self.emergency_stop(abort)
        c.section("End of the motion tests")
        if abort is not None:
            self.data["aborted"] = abort
            self.check.report.problem(f"motion tests stopped: {abort}")
            c.line(f"The motion tests were STOPPED: {abort}")
            for entry in self.data["tests"]:
                if entry.get("status") == "running":
                    entry["status"] = "aborted"
            c.line("After a stop the tool does NOT move the stages back.")
        xy = self.xy
        try:
            if self.speed_touched:
                for axis in AXES:
                    self.data["restore"].append({"what": f"speed {axis.upper()}", "result": self._restore_speed(axis)})
            if self.armed:
                views = self._views()
                try:
                    x, y = (_float(v.query("POS? 1"), "POS? 1") for v in views)
                finally:
                    for v in views:
                        v.close()
                self.data["end_mm"] = [x, y]
                c.line(f"The stages are at X {x:.4f}, Y {y:.4f} mm (start X {self.start[0]:.4f}, Y "
                       f"{self.start[1]:.4f} mm).")
        except KeyboardInterrupt:
            self.data["restore"].append({"what": "rest", "result": "skipped (Ctrl+C)"})
            self.check.report.problem("restore skipped by Ctrl+C")
        except Exception as exc:  # noqa: BLE001 - any failure: stop the stages / still write the report
            self.data["restore"].append({"what": "rest", "result": f"FAILED: {describe(exc)}"})
            self.check.report.problem(f"restore failed: {describe(exc)}")
        finally:
            if xy is not None:
                xy.shutdown(timeout=10.0)

    def _restore_speed(self, axis: str) -> str:
        """``VEL 1 <original>``, ``ERR?``, ``VEL? 1`` on one controller (through a chain view: works whatever state
        the XY device is in)."""
        speed = self.original_speed[axis]
        try:
            view = self.check.open_chain().view(self.check.device_of(axis))
            try:
                view.send(f"VEL 1 {format_gcs_float(speed)}")
                err = _err(view.query("ERR?"), "ERR?")
                read = _float(view.query("VEL? 1"), "VEL? 1")
            finally:
                view.close()
        except StageError as exc:
            self.console.line(f"Could not restore the {axis.upper()} speed: {describe(exc)}")
            return f"FAILED: {describe(exc)}"
        note = f"ERR? = {show_err(err)}" + (" (10 is left over from the stop)" if err == 10 else "")
        self.console.line(f"{axis.upper()} speed restored: VEL? 1 = {read:.4f} mm/s ({note}).")
        return f"restored: VEL? 1 = {read:.4f} mm/s, {note}"


# ---------------------------------------------------------------------------------------------- command line
def _help_epilog() -> str:
    info = textwrap.fill(", ".join(step.frame for step in INFO_STEPS), width=96, initial_indent="  then per "
                         "controller: ", subsequent_indent="    ", break_long_words=False, break_on_hyphens=False)
    return f"""\
information mode (the default) - read-only:
  protocol probes: *IDN?, ERR?, #5, #7 without address; d *IDN? and d 0 *IDN? per device; then per
    device in the form that works: ERR?, #5, #7, SRG? 1 1, RMC?, ERR?
{info}
  (JBS? 1 1 on X only), and one ERR? after a query without usable answer. ERR? reads AND clears the
  controller's error. Nothing moves a stage, changes a setting or stops a macro.

motion tests (--motion) - the stages move:
  set-up through the application's XY device (servo on; a reference move only for an unreferenced axis,
  after a second yes), X and Y relative moves of +/- --step, the inside-wafer refusal, STP during a move.
  Every step needs a typed yes. Moves stay inside start +/- --window, [TMN? 1, TMX? 1] and alox.ini
  [hardware.xy] xmin/xmax, ymin/ymax. Ctrl+C, an error or a timeout sends #24 to both controllers.

examples (Windows, from the repository folder, LabVIEW XY server stopped):
  py -3 tools\\xy_stage_check.py --alox-ini D:\\alox\\settings\\alox.ini
  py -3 tools\\xy_stage_check.py --alox-ini D:\\alox\\settings\\alox.ini --motion
  py -3 tools\\xy_stage_check.py --fake --motion --yes            (rehearsal, no hardware)

The report (xy_stage_report_<date>-<time>.json and .txt) is written next to this tool, or in --out.
Exit code: 0 done, 1 could not connect / no working variant / link lost / motion stopped, 2 usage error.
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python tools\\xy_stage_check.py",
        description="Check the two PI C-663 XY controllers on their RS-232 daisy chain: protocol variant, read-only "
                    "information, then optional guided motion tests. Writes a report to send back.",
        epilog=_help_epilog(), formatter_class=argparse.RawDescriptionHelpFormatter)
    where = p.add_argument_group("controllers")
    where.add_argument("--alox-ini", type=Path, metavar="PATH",
                       help="read [hardware.xy] comport_I32, baudrate_I32, xaxis_I32, yaxis_I32 and the xmin/xmax, "
                            "ymin/ymax ranges of this alox.ini (only read, never written)")
    where.add_argument("--port", help="serial port: COM3 (or just 3), /dev/ttyUSB0, or socket://host:port; overrides "
                                      "alox.ini")
    where.add_argument("--baud", type=int, help=f"baud rate (default: alox.ini, else {DEFAULT_BAUD})")
    where.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS, metavar="MS",
                       help=f"answer timeout (default {DEFAULT_TIMEOUT_MS} ms)")
    where.add_argument("--probe-timeout-ms", type=int, default=DEFAULT_PROBE_TIMEOUT_MS, metavar="MS",
                       help=f"how long a protocol probe waits for an answer (default {DEFAULT_PROBE_TIMEOUT_MS} ms)")
    where.add_argument("--fake", action="store_true",
                       help="rehearsal: two emulated C-663 (devices 1 and 2) inside this program; no port is opened")
    where.add_argument("--fake-address-mode", choices=("any", "target_only", "target_sender"), default="any",
                       help="--fake: the address form the emulation understands (default: both)")
    where.add_argument("--fake-char-mode", choices=("addressed", "text_equivalents"), default="addressed",
                       help="--fake: text_equivalents = the emulation ignores addressed single bytes")
    where.add_argument("--fake-no-ready-lf", action="store_true", help="--fake: the addressed #7 answer has no LF")
    motion = p.add_argument_group("motion tests (THE STAGES MOVE)")
    motion.add_argument("--motion", action="store_true", help="after the information, run the guided motion tests")
    motion.add_argument("--window", type=float, default=DEFAULT_WINDOW_MM, metavar="MM",
                        help=f"moves stay within start position +/- MM per axis (default {DEFAULT_WINDOW_MM:g}; at "
                             f"most {MAX_WINDOW_REAL:g} unless --fake)")
    motion.add_argument("--step", type=float, default=DEFAULT_STEP_MM, metavar="MM",
                        help=f"relative move length (default {DEFAULT_STEP_MM:g}; at most --window)")
    motion.add_argument("--speed", type=float, default=DEFAULT_SPEED, metavar="MM_S",
                        help=f"test speed in mm/s (default {DEFAULT_SPEED:g}; at most {MAX_SPEED_REAL:g} and never "
                             "above a controller's speed, unless --fake)")
    motion.add_argument("--yes", action="store_true", help="answer yes to every question (only with --fake)")
    p.add_argument("--out", type=Path, metavar="PATH",
                   help="folder for the report (default: the folder of this tool), or a .json file name")
    return p


def _write_report(report: Report, out: Path | None, console: Console) -> tuple[Path, Path] | None:
    json_path, txt_path = report_paths(out, report.started)
    candidates = [(json_path, txt_path)]
    if out is None:
        fallback = report_paths(Path.cwd(), report.started)
        if fallback != candidates[0]:
            candidates.append(fallback)
    for json_path, txt_path in candidates:
        try:
            json_path.parent.mkdir(parents=True, exist_ok=True)
            report.data["send_back"] = str(json_path)
            json_path.write_text(json.dumps(report.data, indent=2, ensure_ascii=False, default=str) + "\n",
                                 encoding="utf-8")
            txt_path.write_text(render_text(report.data, json_path), encoding="utf-8")
            return json_path, txt_path
        except OSError as exc:
            console.line(f"Could not write the report to {json_path.parent}: {exc}")
    return None


def main(argv: Sequence[str] | None = None, *, env: Environment | None = None) -> int:
    env = env or Environment()
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else EXIT_USAGE
    console = Console(env.output or sys.stdout, env.read_line, auto_yes=bool(args.yes and args.fake))
    try:
        check_args(args)
        target = resolve_target(args)
    except UsageError as exc:
        console.line(f"error: {exc}")
        console.line("Run with --help to see the options.")
        return EXIT_USAGE
    report = Report(argv)
    report.data["versions"]["pyserial"] = pyserial_version()
    report.data["options"] = {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()}
    report.data["devices"] = {"x": target.devices[0], "y": target.devices[1]}
    if target.alox_ini is not None:
        s = target.settings
        report.data["alox_ini"] = {"path": str(target.alox_ini), "comport_I32": s.x.com, "baudrate": s.x.baudrate,
                                   "xaxis_I32": s.x.device, "yaxis_I32": s.y.device,
                                   "ranges": {a: list(r) for a, r in target.ranges.items()},
                                   "speed_DBL": s.x.speed, "warnings": list(target.ini_warnings)}
    server: FakeDaisyChainServer | None = None
    check: XYCheck | None = None
    code = EXIT_PROBLEM
    t0 = env.monotonic()
    try:
        if args.fake:
            server = env.fake_server or start_fake_server(
                address_mode=None if args.fake_address_mode == "any" else args.fake_address_mode,
                char_mode=args.fake_char_mode, ready_lf=not args.fake_no_ready_lf)
            target = replace(target, port=server.url)
            report.data["connection"]["fake_modes"] = {"address_mode": server.chain.address_mode or "any",
                                                       "char_mode": server.chain.char_mode,
                                                       "ready_lf": server.chain.ready_lf}
        check = XYCheck(args, target, env, console, report)
        code = check.run()
    except KeyboardInterrupt:
        console.line()
        console.line("Interrupted (Ctrl+C).")
        report.problem("interrupted by Ctrl+C")
        code = EXIT_PROBLEM
    except Exception as exc:  # noqa: BLE001 - any failure: stop the stages / still write the report
        console.line(f"Unexpected error: {describe(exc)}")
        report.problem(f"unexpected error: {describe(exc)}")
        report.data["traceback"] = traceback.format_exc()
        code = EXIT_PROBLEM
    finally:
        if check is not None:
            check.close()
            report.data["frames"] = check.log.as_records()
        if server is not None and env.fake_server is None:
            server.close()
        report.data["operator_answers"] = console.answers
        report.data["finished"] = datetime.now().astimezone().isoformat(timespec="seconds")
        report.data["duration_s"] = round(env.monotonic() - t0, 3)
        summary = {EXIT_OK: "done", EXIT_PROBLEM: "stopped early or a problem (see Problems)"}.get(code, str(code))
        report.data["result"] = {"exit_code": code, "summary": summary}
        paths = _write_report(report, args.out, console)
        zc._final_lines(console, paths, summary)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
