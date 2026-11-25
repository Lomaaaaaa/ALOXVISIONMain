#!/usr/bin/env python3
r"""Check the Spectral Products CM110/CM112 monochromator over RS-232: read-only information, then optional moves.

Spec: docs/vision-core/monochromator.md (§2 link, §3 bytes, §5 gratings and range, §10 open questions Q1, Q2, Q3,
Q8). The tool uses the application's own protocol code (``alox.devices.cm11x``: pyserial, 9600 Bd 8N1, binary, input
discarded before every command, each answer checked for its status byte and its end byte 24), so it sends exactly
what the new application will send. It writes a report (``wl_report_<date>-<time>.json`` with every value and every
byte exchanged, plus a ``.txt`` summary) next to this tool, or in ``--out``. That report is what to send back.

Run it on the PC connected to the monochromator, from the repository folder, **with Alox Vision stopped**
(``AloxWLServer.exe`` keeps the COM port open)::

    py -3 tools\wl_check.py --alox-ini D:\alox\settings\alox.ini
    py -3 tools\wl_check.py --alox-ini D:\alox\settings\alox.ini --motion

``--port COM1`` (and ``--baud``) gives the port directly. ``--fake`` runs everything against an emulated CM110 inside
this program (a rehearsal: no port is opened).

Information (the default): read-only
------------------------------------
Opening the port sends nothing. Then exactly these requests, in this order (before each one the bytes waiting in the
input buffer are discarded, which sends nothing):

``ECHO`` (27), then ``QUERY`` (56, n) for n = 19, 1, 13, 4, 2, 3, 14, 0, 5, 6, 15, 16, 17, 18, 7, 8, 9, 10, 11,
12, 20: serial number, type, number of gratings, current grating, grooves/mm, blaze, units, position, scan speed,
step size, grooves and blaze of grating 1, grooves and blaze of grating 2, the four zero offsets, the two
calibrations, NOVRAM #14.

QUERY only reads: nothing moves, no setting changes. Closing the port sends nothing. A refused or unexpected
answer is recorded (raw bytes) and the next request is sent; after 3 requests in a row without an answer the
rest is not sent.

Moves (``--motion``): the grating turns
-------------------------------------
The information above is read first. The plan is printed and nothing is sent until the operator types ``yes``; each
test needs its own ``yes`` (anything else skips it, ``quit`` skips the rest):

0. Only when QUERY 14 is not 1: ``UNITS 1`` (nanometres), as LabVIEW sends at every start (after a ``yes``).
1. Small move: ``GOTO start+step`` (``--step``, default 10 nm; ``start−step`` near the top of the range), ``QUERY 0``,
   ``GOTO start``, ``QUERY 0``, each timed.
2. Working range (Q3, move duration): ``GOTO lo``, ``QUERY 0``, ``GOTO hi``, ``QUERY 0`` (``--range LO HI``,
   default 700 950), each timed.
3. Only with ``--reset`` (Q2): ``RESET`` (255, 255, 255), then ``ECHO`` every 0.5 s until the instrument answers 27
   (at most 60 s); what arrives meanwhile is kept in the report; then ``QUERY 14`` (``UNITS 1`` if needed) and
   ``QUERY 0``.

At the end: ``GOTO start`` (after a ``yes``) and ``QUERY 0``. Every target is inside the range of the current grating
(0 .. 1500 · 1200 / grooves nm) and 0 .. 3000 nm. The tool never sends CALIBRATE, SELECT, SPEED or SCAN. A GOTO cannot
be interrupted: Ctrl+C stops the tool after the current move; nothing more is sent. ``--yes`` (answers every
question) is refused unless ``--fake`` is given.

Exit code: 0 the tool ran to the end (findings are in the report), 1 it could not connect, lost the link, or a move
failed, 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import subprocess
import sys
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

from alox import __version__ as ALOX_VERSION
from alox.config import TypedIni
from alox.devices.cm11x import (
    NOT_OPEN,
    OPEN_FAILED,
    PYSERIAL_MISSING,
    RECEIVE_FAILED,
    SEND_FAILED,
    TIMEOUT,
    Cm11x,
    Cm11xError,
    LinkFactory,
    Query,
    SerialLink,
    Units,
    command_text,
    normal_port,
    pyserial_version,
    speed_table,
)
from alox.devices.cm11x_sim import Cm11xEmulatorServer, SimOptions
from alox.devices.monochromator import MAX_NM_AT_1200, load_monochromator_settings

__all__ = ["INFO_QUERIES", "INFO_REQUESTS", "Environment", "main", "plan_targets", "start_fake_server"]

TOOL_NAME = "wl_check"
TOOL_VERSION = "1.0"
TOOL_DIR = Path(__file__).resolve().parent
REPORT_PREFIX = "wl_report_"
DEFAULT_BAUD = 9600
DEFAULT_TIMEOUT_MS = 5000
DEFAULT_MOVE_TIMEOUT_S = 60.0
DEFAULT_RANGE = (700, 950)
DEFAULT_STEP = 10
ABSOLUTE_MAX_NM = 3000                   # the table editor's bound (spec §5.2)
RESET_ECHO_TIMEOUT_S = 60.0
RESET_POLL_S = 0.5
RESET_ANSWER_S = 1.0
MAX_TIMEOUTS_IN_A_ROW = 3
EXIT_OK, EXIT_PROBLEM, EXIT_USAGE = 0, 1, 2
#: Errors after which nothing more is sent. A wrong answer (no 24 at the end) or a refused query is recorded and the
#: next query is sent: the raw bytes are what is needed to correct the protocol.
TRANSPORT_FAILURES = frozenset({SEND_FAILED, RECEIVE_FAILED, NOT_OPEN})
TEST_QUESTION = ("Type yes and press Enter to run this test (anything else skips it; quit skips all the remaining "
                 "tests):")

#: The QUERY sub-codes of the information mode, in order.
INFO_QUERIES: tuple[int, ...] = (19, 1, 13, 4, 2, 3, 14, 0, 5, 6, 15, 16, 17, 18, 7, 8, 9, 10, 11, 12, 20)
#: Exactly what the information mode sends when everything is answered.
INFO_REQUESTS: tuple[bytes, ...] = (bytes((27,)), *(bytes((56, code)) for code in INFO_QUERIES))
_LABELS = {19: "serial number", 1: "type", 13: "number of gratings", 4: "current grating", 2: "grooves/mm",
           3: "blaze (nm)", 14: "units", 0: "position", 5: "scan speed (A/s)", 6: "step size",
           15: "grating 1 grooves/mm", 16: "grating 1 blaze (nm)", 17: "grating 2 grooves/mm",
           18: "grating 2 blaze (nm)", 7: "zero offset m1g1", 8: "zero offset m1g2", 9: "zero offset m2g1",
           10: "zero offset m2g2", 11: "calibration m1g1", 12: "calibration m1g2", 20: "NOVRAM #14"}


class UsageError(Exception):
    """A command-line problem: exit code 2, nothing opened."""


# ---------------------------------------------------------------------------------------------- outside world
@dataclass
class Environment:
    """What the tool needs from the outside world; the tests replace parts of it."""

    read_line: Callable[[], str] = input
    output: TextIO | None = None                        # None: sys.stdout
    sleep: Callable[[float], None] = time.sleep
    monotonic: Callable[[], float] = time.monotonic
    link_factory: LinkFactory | None = None             # None: SerialLink (pyserial)
    fake_server: Cm11xEmulatorServer | None = None      # --fake: use this one (not closed by the tool)


class FrameLog:
    """Every byte sequence exchanged, time-stamped (seconds since the tool started)."""

    def __init__(self, monotonic: Callable[[], float]) -> None:
        self._monotonic = monotonic
        self._t0 = monotonic()
        self.frames: list[tuple[float, str, bytes]] = []

    def add(self, direction: str, data: bytes) -> None:
        self.frames.append((round(self._monotonic() - self._t0, 4), direction, bytes(data)))

    def mark(self) -> int:
        return len(self.frames)

    def since(self, mark: int, direction: str | None = None) -> list[bytes]:
        return [d for _t, dr, d in self.frames[mark:] if direction is None or dr == direction]

    def as_records(self) -> list[dict[str, Any]]:
        return [{"i": i, "t_s": t, "dir": d, "hex": b.hex(" "), "cmd": command_text(b) if d == "tx" else ""}
                for i, (t, d, b) in enumerate(self.frames)]


class Console:
    """Plain console output and the operator's answers (recorded for the report)."""

    def __init__(self, output: TextIO, read_line: Callable[[], str], *, auto_yes: bool) -> None:
        self._out = output
        self._read_line = read_line
        self.auto_yes = auto_yes
        self.answers: list[dict[str, str]] = []

    def line(self, text: str = "") -> None:
        try:
            self._out.write(text + "\n")
        except UnicodeEncodeError:
            self._out.write(text.encode("ascii", "backslashreplace").decode("ascii") + "\n")
        self._out.flush()

    def section(self, title: str) -> None:
        self.line()
        self.line(f"=== {title} ===")

    def ask(self, question: str) -> str:
        """``"yes"`` only for a typed yes; ``"quit"`` for quit / q / stop or the end of input; else ``"skip"``."""
        prompt = question.rstrip() + " "
        if self.auto_yes:
            self.line(prompt + "yes   (--yes)")
            raw = "yes"
        else:
            self._out.write(prompt)
            self._out.flush()
            try:
                raw = self._read_line()
            except EOFError:
                self.line()
                raw = "<end of input>"
        text = raw.strip().lower()
        meaning = "yes" if text == "yes" else "quit" if text in ("quit", "q", "stop", "<end of input>") else "skip"
        self.answers.append({"question": question, "answer": raw, "meaning": meaning})
        return meaning


# ---------------------------------------------------------------------------------------------- guidance
def describe(exc: BaseException) -> str:
    if isinstance(exc, Cm11xError):
        return f"error {exc.code}: {exc.message}"
    return f"{type(exc).__name__}: {exc}"


def advice(exc: Cm11xError, port: str) -> list[str]:
    """What the operator should check (simple words)."""
    low = exc.message.lower()
    if exc.code == OPEN_FAILED:
        if PYSERIAL_MISSING in low:
            return ["pyserial (the serial port library) is not installed for this Python.",
                    "Install it with this exact command, then run the tool again:",
                    f'    "{sys.executable}" -m pip install pyserial']
        if any(w in low for w in ("access is denied", "permission", "busy", "being used", "errno 16", "errno 13")):
            return [f"{port} is busy: another program has it open. Stop Alox Vision (AloxWLServer.exe keeps the",
                    "COM port open) and any terminal program, then try again."]
        return [f"{port} could not be opened. Check the port number (alox.ini [hardware.monochromator]",
                "comport_I32 = <n> means COM<n>; Windows Device Manager > Ports lists them) and that no other",
                "program uses it."]
    if exc.code == TIMEOUT:
        return [f"The monochromator did not answer in time on {port}. Check:",
                "- that the monochromator is switched on and its RS-232 cable is plugged in;",
                "- the port number (alox.ini [hardware.monochromator] comport_I32);",
                "- the baud rate (9600: alox.ini baudrate_I32 = 0)."]
    return []


# ---------------------------------------------------------------------------------------------- targets
def plan_targets(start: int, max_nm: int, step: int, lo: int, hi: int) -> tuple[int, int, int]:
    """``(small_target, lo, hi)``: the small move goes up by ``step`` unless that leaves the range, then down.
    :class:`ValueError` when a target is outside ``0 .. max_nm``."""
    if not 0 <= lo < hi <= max_nm:
        raise ValueError(f"--range {lo} {hi} is not inside 0 .. {max_nm} nm (range of the current grating)")
    small = start + step if start + step <= max_nm else start - step
    if not 0 <= small <= max_nm:
        raise ValueError(f"no small move of {step} nm fits around {start} nm in 0 .. {max_nm} nm")
    return small, lo, hi


# ---------------------------------------------------------------------------------------------- report
def _git_commit() -> str | None:
    try:
        done = subprocess.run(["git", "rev-parse", "--short=12", "HEAD"], cwd=TOOL_DIR, capture_output=True,
                              text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() or None if done.returncode == 0 else None


class Report:
    def __init__(self, argv: Sequence[str]) -> None:
        self.started = datetime.now().astimezone()
        self.data: dict[str, Any] = {
            "report": f"{TOOL_NAME} report",
            "versions": {"tool": TOOL_VERSION, "alox": ALOX_VERSION, "python": platform.python_version(),
                         "pyserial": pyserial_version(), "platform": platform.platform(),
                         "git_commit": _git_commit()},
            "started": self.started.isoformat(timespec="seconds"),
            "command_line": [sys.argv[0] if sys.argv else TOOL_NAME, *argv],
            "connection": {},
            "alox_ini": None,
            "info": {"queries": []},
            "motion": None,
            "findings": [],
            "problems": [],
        }

    def finding(self, ref: str, topic: str, answer: str) -> None:
        self.data["findings"].append({"ref": ref, "topic": topic, "answer": answer})

    def problem(self, text: str) -> None:
        self.data["problems"].append(text)


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
    lines: list[str] = []
    add = lines.append
    add("MONOCHROMATOR CHECK REPORT")
    add(f"Send this report back: {json_path}  (this .txt is the same report, easier to read)")
    add("")
    v = data["versions"]
    add(f"Tool        {TOOL_NAME} {v['tool']}, alox {v['alox']}, git {v['git_commit']}")
    add(f"            Python {v['python']}, pyserial {v['pyserial']}, {v['platform']}")
    add(f"Started     {data['started']}")
    add(f"Finished    {data.get('finished', '')}")
    add(f"Command     {' '.join(data['command_line'])}")
    result = data.get("result", {})
    add(f"Result      {result.get('summary', '')} (exit code {result.get('exit_code', '')})")
    add("")
    conn = data["connection"]
    add(f"Connection  {conn.get('port', '')} at {conn.get('baudrate', '')} Bd ({conn.get('source', '')}), answer "
        f"timeout {conn.get('timeout_ms', '')} ms, move timeout {conn.get('move_timeout_s', '')} s")
    if data.get("alox_ini"):
        ini = data["alox_ini"]
        add(f"alox.ini    {ini['path']}: COM{ini['comport']}, {ini['baudrate']} Bd, timeout {ini['timeout_ms']} ms, "
            f"waitposition {ini['waitposition_ms']} ms")
    add("")
    add("Information (read-only)")
    for q in data["info"]["queries"]:
        shown = q["shown"] if q["ok"] else f"FAILED: {q['error']}"
        add(f"  {q['request']:<24} {q['label']:<22} {shown}")
        add(f"  {'':<24} raw: {q['raw'] or '(nothing received)'}")
    add("")
    add("Findings")
    for f in data["findings"]:
        add(f"  [{f['ref']}] {f['topic']}: {f['answer']}")
    if not data["findings"]:
        add("  (none)")
    motion = data.get("motion")
    if motion:
        add("")
        add("Moves")
        if motion.get("refused"):
            add(f"  Not run: {motion['refused']}")
        for t in motion.get("tests", []):
            add(f"  - {t['title']}: {t.get('status', '')}")
            for m in t.get("moves", []):
                add(f"      GOTO {m['target_nm']} nm from {m['from_nm']} nm: {m.get('result', '')}")
            if t.get("note"):
                add(f"      {t['note']}")
        if motion.get("move_back"):
            add(f"  Move back: {motion['move_back']}")
        if motion.get("aborted"):
            add(f"  Stopped: {motion['aborted']}")
    add("")
    add("Problems")
    for p in data["problems"]:
        add(f"  - {p}")
    if not data["problems"]:
        add("  (none)")
    if data.get("operator_answers"):
        add("")
        add("Operator answers")
        for a in data["operator_answers"]:
            add(f"  {a['question']} -> {a['answer']!r} ({a['meaning']})")
    add("")
    add("Bytes (tx = sent by this PC, rx = received, drop = input discarded before a command; t in s)")
    for f in data.get("frames", []):
        add(f"  {f['t_s']:9.3f} {f['dir']:<4} {f['hex']:<20} {f['cmd']}".rstrip())
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------------------------- target
@dataclass(frozen=True)
class Target:
    port: str
    baudrate: int
    timeout_ms: int
    source: str
    alox_ini: Path | None = None
    ini_values: dict[str, Any] = field(default_factory=dict)
    ini_warnings: tuple[str, ...] = ()


def check_args(args: argparse.Namespace) -> None:
    if args.yes and not args.fake:
        raise UsageError("--yes is only accepted together with --fake: on the real monochromator the operator must "
                         "type yes for every test")
    if args.fake and args.port:
        raise UsageError("--fake and --port cannot be used together (--fake opens no port)")
    if not (args.fake or args.port or args.alox_ini):
        raise UsageError("say which port to use: --alox-ini <path to alox.ini> or --port COM<n> "
                         "(or --fake for a rehearsal)")
    if args.reset and not args.motion:
        raise UsageError("--reset is a move test: add --motion")
    if args.baud is not None and args.baud <= 0:
        raise UsageError("--baud must be a positive number")
    if args.timeout_ms is not None and args.timeout_ms <= 0:
        raise UsageError("--timeout-ms must be a positive number")
    if not (math.isfinite(args.move_timeout_s) and args.move_timeout_s > 0):
        raise UsageError("--move-timeout-s must be a positive number")
    lo, hi = args.range
    if not 0 <= lo < hi <= ABSOLUTE_MAX_NM:
        raise UsageError(f"--range LO HI must satisfy 0 <= LO < HI <= {ABSOLUTE_MAX_NM}")
    if not 1 <= args.step <= 100:
        raise UsageError("--step must be 1 .. 100 nm")
    if args.alox_ini is not None and not args.alox_ini.is_file():
        raise UsageError(f"alox.ini not found: {args.alox_ini}")


def resolve_target(args: argparse.Namespace) -> Target:
    values: dict[str, Any] = {}
    warnings: list[str] = []
    settings = None
    if args.alox_ini is not None:
        settings = load_monochromator_settings(TypedIni.load(args.alox_ini), warn=warnings.append)
        values = {"path": str(args.alox_ini), "comport": settings.com, "baudrate": settings.baudrate,
                  "timeout_ms": settings.timeout_ms, "waitposition_ms": settings.wait_position_ms}
    timeout = args.timeout_ms or (settings.timeout_ms if settings else DEFAULT_TIMEOUT_MS)
    if args.fake:
        return Target("", args.baud or DEFAULT_BAUD, timeout, "--fake (emulated CM110)", args.alox_ini, values,
                      tuple(warnings))
    if args.port:
        baud = args.baud or (settings.baudrate if settings else DEFAULT_BAUD)
        return Target(normal_port(args.port), baud, timeout, "--port", args.alox_ini, values, tuple(warnings))
    assert settings is not None
    return Target(settings.port_name, args.baud or settings.baudrate, timeout, f"alox.ini {args.alox_ini}",
                  args.alox_ini, values, tuple(warnings))


def start_fake_server(*, slew_nm_per_s: float = 500.0, reset_s: float = 1.0) -> Cm11xEmulatorServer:
    """``--fake``: an emulated CM110 at 750 nm, two gratings (1200 and 600 gr/mm)."""
    return Cm11xEmulatorServer(SimOptions(position_nm=750.0, slew_nm_per_s=slew_nm_per_s, reset_s=reset_s)).start()


# ---------------------------------------------------------------------------------------------- the check
class WlCheck:
    def __init__(self, args: argparse.Namespace, target: Target, env: Environment, console: Console,
                 report: Report) -> None:
        self.args = args
        self.target = target
        self.env = env
        self.console = console
        self.report = report
        self.log = FrameLog(env.monotonic)
        self.cm = Cm11x(env.link_factory or SerialLink, sleep=env.sleep, monotonic=env.monotonic,
                        on_frame=self.log.add, frame_log_size=8)
        self.values: dict[int, int] = {}
        self.statuses: set[int] = set()

    # ============================================================================================== connection
    def connect(self) -> bool:
        c, t = self.console, self.target
        self.report.data["connection"].update(port=t.port, baudrate=t.baudrate, source=t.source,
                                              timeout_ms=t.timeout_ms, move_timeout_s=self.args.move_timeout_s,
                                              fake=bool(self.args.fake))
        c.line(f"Port {t.port} at {t.baudrate} Bd 8N1 ({t.source}), answer timeout {t.timeout_ms} ms.")
        try:
            self.cm.open(t.port, t.baudrate, t.timeout_ms / 1000.0)
        except Cm11xError as exc:
            self._link_failure("Opening the port failed", exc)
            return False
        c.line("Port open (opening sends nothing).")
        return True

    def _link_failure(self, what: str, exc: Cm11xError) -> None:
        c = self.console
        c.line()
        c.line(f"{what}: {describe(exc)}")
        hints = advice(exc, self.target.port)
        for hint in hints:
            c.line(hint)
        self.report.problem(f"{what}: {describe(exc)}" + (f" -- {' '.join(h.strip() for h in hints)}" if hints else ""))

    def close(self) -> None:
        self.cm.close()

    # ============================================================================================== information
    def run_info(self) -> bool:
        c = self.console
        c.line("Reading the monochromator (ECHO and QUERY only: nothing moves, no setting changes):")
        steps: list[tuple[str, str, Callable[[], Any], int | None]] = [("ECHO", "echo", self.cm.echo, None)]
        steps += [(f"QUERY {code}", _LABELS[code], lambda code=code: self.cm.query(code), code)
                  for code in INFO_QUERIES]
        timeouts = 0
        failed = 0
        for request, label, read, code in steps:
            mark = self.log.mark()
            entry: dict[str, Any] = {"request": request, "label": label}
            self.report.data["info"]["queries"].append(entry)
            try:
                value = read()
            except Cm11xError as exc:
                failed += 1
                entry.update(ok=False, code=exc.code, error=describe(exc), raw=self._raw(mark))
                c.line(f"  {request:<9} {label:<22} FAILED: {describe(exc)}")
                if exc.code in TRANSPORT_FAILURES:                # the port itself failed: stop
                    self._link_failure(f"{request} failed", exc)
                    return False
                timeouts = timeouts + 1 if exc.code == TIMEOUT else 0
                if timeouts >= MAX_TIMEOUTS_IN_A_ROW:
                    self._link_failure(f"no answer to {timeouts} requests in a row; the rest is not sent", exc)
                    return False
                continue
            timeouts = 0
            if self.cm.last_status is not None and code is not None:
                self.statuses.add(self.cm.last_status)
            shown = self._show(code, value)
            entry.update(ok=True, value=value, shown=shown, raw=self._raw(mark))
            if code is not None:
                self.values[code] = value
            c.line(f"  {request:<9} {label:<22} {shown}")
        self.report.data["info"]["failed"] = failed
        c.line(f"Information done: {len(steps)} requests, {failed} failed.")
        if failed:
            self.report.problem(f"{failed} information requests failed (see the report)")
        return True

    def _raw(self, mark: int) -> str:
        return " | ".join(d.hex(" ") for d in self.log.since(mark, "rx"))

    @staticmethod
    def _show(code: int | None, value: Any) -> str:
        if code is None:
            return "27 returned: the link works"
        if code == Query.UNITS:
            names = {0: "unit 0 (LabVIEW: centimeters; manual: microns?)", 1: "nanometres", 2: "angstroms"}
            return f"{value}  {names.get(value, 'unknown')}"
        return str(value)

    def info_findings(self) -> None:
        v, r = self.values, self.report
        if 19 in v:
            r.finding("Q1", "Serial number", str(v[19]))
            r.finding("D1", "LabVIEW's false 'Command not accepted'",
                      "this serial number is >= 32768: LabVIEW's driver reported it as an error at every Setup"
                      if v[19] >= 32768 else "serial number below 32768: the LabVIEW defect does not show on this "
                      "unit")
        if 1 in v or 13 in v:
            r.finding("Q1", "Type and gratings", f"QUERY 1 (type) = {v.get(1)}, QUERY 13 (gratings) = {v.get(13)}, "
                      f"current grating {v.get(4)}")
        if 15 in v:
            r.finding("Q1", "Grating 1", f"{v.get(15)} gr/mm, blaze {v.get(16)} nm")
        if 17 in v:
            r.finding("Q1", "Grating 2", f"{v.get(17)} gr/mm, blaze {v.get(18)} nm")
        if 14 in v:
            r.finding("spec 5.1", "Units", "nanometres (as the application uses)" if v[14] == Units.NANOMETERS else
                      f"units {v[14]}: the application sends UNITS 1 (nanometres) when it opens the monochromator")
        if v.get(2):
            top = int(MAX_NM_AT_1200 * 1200 / v[2])
            table = speed_table(v[2])
            r.finding("spec 5.2", "Range used by the application", f"0 .. {top} nm for {v[2]} gr/mm (to confirm, Q1)")
            r.finding("spec 5.4", "Scan speed", f"QUERY 5 = {v.get(5)} A/s; levels 0..9 for {v[2]} gr/mm: {table}"
                      + ("" if v.get(5) in table else " (the current speed is not one of the levels)"))
        if 0 in v:
            r.finding("spec 3", "Position", f"{v[0]} (in the current units)")
        if self.statuses:
            r.finding("spec 3.3", "Status bytes seen", ", ".join(f"0x{s:02X}" for s in sorted(self.statuses)))

    # ============================================================================================== run
    def run(self) -> int:
        c = self.console
        c.section(f"Monochromator check {TOOL_VERSION} (alox {ALOX_VERSION}) - information, read-only")
        if self.args.fake:
            c.line("REHEARSAL (--fake): an emulated CM110 inside this program; no serial port is opened.")
        for warning in self.target.ini_warnings:
            c.line(f"alox.ini warning: {warning}")
        if not self.connect():
            return EXIT_PROBLEM
        ok = self.run_info()
        self.info_findings()
        if not ok:
            return EXIT_PROBLEM
        if self.args.motion:
            return MotionSession(self).run()
        return EXIT_OK


# ---------------------------------------------------------------------------------------------- moves
class MotionSession:
    def __init__(self, check: WlCheck) -> None:
        self.check = check
        self.cm = check.cm
        self.c = check.console
        self.args = check.args
        self.env = check.env
        self.data: dict[str, Any] = {"tests": []}
        check.report.data["motion"] = self.data
        self.position: int | None = check.values.get(0)
        self.start: int | None = self.position
        self.moved = False
        self.off_target = False

    def _goto(self, target: int, record: list[dict[str, Any]]) -> None:
        entry: dict[str, Any] = {"target_nm": target, "from_nm": self.position}
        record.append(entry)
        self.c.line(f"  GOTO {target} nm (from {self.position} nm) ...")
        t0 = self.env.monotonic()
        self.moved = True
        self.position = None
        try:
            self.cm.goto(target, timeout_s=self.args.move_timeout_s)
        finally:
            entry["duration_s"] = round(self.env.monotonic() - t0, 3)
            entry["status_byte"] = self.cm.last_status
        reached = self.cm.query(Query.POSITION)
        self.position = reached
        entry["reached_nm"] = reached
        distance = abs(target - (entry["from_nm"] if entry["from_nm"] is not None else target))
        rate = f", {entry['duration_s'] / distance * 1000:.1f} ms/nm" if distance else ""
        entry["result"] = f"reached {reached} nm in {entry['duration_s']:.3f} s{rate}" + \
            ("" if reached == target else f" - NOT AT THE TARGET ({target} nm)")
        self.c.line(f"    {entry['result']}")
        if reached != target:
            self.off_target = True
            self.check.report.problem(f"GOTO {target} nm ended at {reached} nm")

    def run(self) -> int:
        c, v = self.c, self.check.values
        c.section("Moves (the grating turns)")
        if self.start is None or not v.get(2):
            return self._refuse("the position or the grooves/mm could not be read")
        try:
            if v.get(14) != Units.NANOMETERS and not self._units():
                return EXIT_PROBLEM
            max_nm = min(ABSOLUTE_MAX_NM, int(MAX_NM_AT_1200 * 1200 / v[2]))
            try:
                small, lo, hi = plan_targets(self.start, max_nm, self.args.step, *self.args.range)
            except ValueError as exc:
                return self._refuse(str(exc))
            start = self.start
            self.data.update(start_nm=start, max_nm=max_nm, small_target_nm=small, range_nm=[lo, hi])
            c.line(f"Start position {start} nm; allowed 0 .. {max_nm} nm ({v[2]} gr/mm).")
            c.line(f"Tests: small move to {small} nm and back; working range {lo} nm then {hi} nm"
                   + ("; RESET" if self.args.reset else "") + ". A GOTO cannot be interrupted once sent.")

            def small_move(moves: list[dict[str, Any]]) -> None:
                self._goto(small, moves)
                self._goto(start, moves)

            def working_range(moves: list[dict[str, Any]]) -> None:
                self._goto(lo, moves)
                self._goto(hi, moves)

            tests: list[tuple[str, Callable[[list[dict[str, Any]]], None]]] = [
                ("Small move", small_move), ("Working range (Q3)", working_range)]
            if self.args.reset:
                tests.append(("RESET (Q2)", self._reset))
            for title, run in tests:
                if self._test(title, run) == "quit":
                    break
            self._move_back()
        except KeyboardInterrupt:
            self.data["aborted"] = "Ctrl+C: nothing more was sent (a GOTO already sent finishes by itself)"
            c.line()
            c.line(f"Stopped: {self.data['aborted']}.")
            self.check.report.problem("moves interrupted by Ctrl+C")
            return EXIT_PROBLEM
        except Cm11xError as exc:
            self.data["aborted"] = describe(exc)
            self.check._link_failure("A move failed", exc)
            return EXIT_PROBLEM
        return EXIT_PROBLEM if self.off_target else EXIT_OK

    def _refuse(self, reason: str) -> int:
        self.data["refused"] = reason
        self.c.line(f"Not run: {reason}.")
        return EXIT_PROBLEM

    def _units(self) -> bool:
        self.c.line("The monochromator is not in nanometres. UNITS 1 sets nanometres (LabVIEW sends it at every "
                    "start).")
        if self.c.ask("Type yes to send UNITS 1 (anything else: no moves):") != "yes":
            self.data["refused"] = "units not set to nanometres"
            return False
        self.cm.set_units(Units.NANOMETERS)
        self.position = self.start = self.cm.query(Query.POSITION)
        self.data["units_set"] = {"position_nm": self.position}
        return True

    def _test(self, title: str, run: Callable[[list[dict[str, Any]]], Any]) -> str:
        test: dict[str, Any] = {"title": title, "moves": []}
        self.data["tests"].append(test)
        self.c.line()
        self.c.line(f"--- {title} ---")
        answer = self.c.ask(TEST_QUESTION)
        if answer != "yes":
            test["status"] = "skipped" if answer == "skip" else "quit"
            return answer
        try:
            run(test["moves"])
        except Cm11xError as exc:
            test["status"] = f"failed: {describe(exc)}"
            raise
        test["status"] = "done"
        return answer

    def _reset(self, moves: list[dict[str, Any]]) -> None:
        test = self.data["tests"][-1]
        self.c.line("  RESET, then ECHO every 0.5 s until 27 comes back ...")
        mark = self.check.log.mark()
        self.position = None
        self.moved = True
        seconds = self.cm.reset(echo_timeout_s=RESET_ECHO_TIMEOUT_S, poll_s=RESET_POLL_S, answer_s=RESET_ANSWER_S)
        received = [f"{d} {b.hex(' ')}" for _t, d, b in self.check.log.frames[mark:] if d != "tx"]
        units = self.cm.query(Query.UNITS)
        if units != Units.NANOMETERS:
            self.cm.set_units(Units.NANOMETERS)
        self.position = self.cm.query(Query.POSITION)
        test["note"] = (f"echo after {seconds:.2f} s; bytes received after RESET: {received or 'none'}; units "
                        f"{units}; position {self.position} nm")
        self.c.line(f"    {test['note']}")
        self.check.report.finding("Q2", "RESET", test["note"])

    def _move_back(self) -> None:
        if not self.moved or self.start is None or self.position == self.start:
            if self.moved:
                self.data["move_back"] = f"already at the start position {self.start} nm"
            return
        self.c.line()
        if self.c.ask(f"Move back to the start position {self.start} nm? Type yes:") != "yes":
            self.data["move_back"] = f"declined; left at {self.position} nm"
            return
        moves: list[dict[str, Any]] = []
        self._goto(self.start, moves)
        self.data["move_back"] = moves[0]["result"]


# ---------------------------------------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python tools\\wl_check.py",
        description="Check the Spectral Products CM110/CM112 monochromator (RS-232): read-only information, then "
                    "optional guided moves. Writes a report to send back.",
        epilog="examples (Windows, from the repository folder, Alox Vision stopped):\n"
               "  py -3 tools\\wl_check.py --alox-ini D:\\alox\\settings\\alox.ini\n"
               "  py -3 tools\\wl_check.py --port COM1\n"
               "  py -3 tools\\wl_check.py --alox-ini D:\\alox\\settings\\alox.ini --motion\n"
               "  py -3 tools\\wl_check.py --fake --motion --reset --yes      (rehearsal, no hardware)\n\n"
               "information mode sends only ECHO and QUERY " + ", ".join(map(str, INFO_QUERIES)) + ".\n"
               "The report (wl_report_<date>-<time>.json and .txt) is written next to this tool, or in --out.\n"
               "Exit code: 0 done, 1 could not connect / link lost / a move failed, 2 usage error.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    where = p.add_argument_group("monochromator")
    where.add_argument("--alox-ini", type=Path, metavar="PATH",
                       help="read comport_I32, baudrate_I32 and timeout_I32 from [hardware.monochromator] of this "
                            "alox.ini (only read, never written)")
    where.add_argument("--port", help="serial port: COM1 (or just 1), /dev/ttyUSB0, or socket://host:port")
    where.add_argument("--baud", type=int, help=f"baud rate (default: alox.ini, else {DEFAULT_BAUD})")
    where.add_argument("--timeout-ms", type=int, metavar="MS",
                       help=f"answer timeout (default: alox.ini timeout_I32, else {DEFAULT_TIMEOUT_MS})")
    where.add_argument("--fake", action="store_true", help="rehearsal: an emulated CM110 inside this program")
    motion = p.add_argument_group("moves (THE GRATING TURNS)")
    motion.add_argument("--motion", action="store_true", help="after the information, run the guided moves")
    motion.add_argument("--range", type=int, nargs=2, default=list(DEFAULT_RANGE), metavar=("LO", "HI"),
                        help=f"working-range test: GOTO LO then HI nm (default {DEFAULT_RANGE[0]} {DEFAULT_RANGE[1]})")
    motion.add_argument("--step", type=int, default=DEFAULT_STEP, help=f"small move in nm (default {DEFAULT_STEP})")
    motion.add_argument("--move-timeout-s", type=float, default=DEFAULT_MOVE_TIMEOUT_S, metavar="S",
                        help=f"longest wait for the end of a GOTO (default {DEFAULT_MOVE_TIMEOUT_S:g} s)")
    motion.add_argument("--reset", action="store_true", help="also RESET the monochromator (it re-homes the grating)")
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
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else EXIT_USAGE
    console = Console(env.output or sys.stdout, env.read_line, auto_yes=bool(args.yes and args.fake))
    try:
        check_args(args)
        target = resolve_target(args)
    except (UsageError, ValueError, OSError) as exc:
        console.line(f"error: {exc}")
        console.line("Run with --help to see the options.")
        return EXIT_USAGE
    report = Report(argv)
    report.data["options"] = {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()}
    if target.ini_values:
        report.data["alox_ini"] = {**target.ini_values, "warnings": list(target.ini_warnings)}
    server: Cm11xEmulatorServer | None = None
    check: WlCheck | None = None
    code = EXIT_PROBLEM
    t0 = env.monotonic()
    try:
        if args.fake:
            server = env.fake_server or start_fake_server()
            target = replace(target, port=server.url)
        check = WlCheck(args, target, env, console, report)
        code = check.run()
    except KeyboardInterrupt:
        console.line()
        console.line("Interrupted (Ctrl+C).")
        report.problem("interrupted by Ctrl+C")
        code = EXIT_PROBLEM
    except Exception as exc:                       # noqa: BLE001 - reported, the port is still closed below
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
        console.line()
        console.line("=" * 78)
        console.line(f"Result: {summary}")
        if paths is None:
            console.line("The report could NOT be written (see the error above).")
        else:
            console.line("Report written:")
            console.line(f"  {paths[0]}")
            console.line(f"  {paths[1]}  (the same report, easier to read)")
            console.line(f"PLEASE SEND THIS FILE BACK: {paths[0]}")
        console.line("=" * 78)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
