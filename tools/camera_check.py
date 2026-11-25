#!/usr/bin/env python3
r"""Check the two IDS GigE Vision cameras through the application's GenICam driver: read-only information and timed
grabs, then optional guided actions (exposure, packet size, LED / bulb). Writes a report to send back.

Background: docs/05 §2, §4, §5, §9, §12; the checklist at the end of the docstring of
``src/alox/devices/camera_genicam.py`` (feature names, pixel format, packet size, I/O lines).

Run it on the PC the cameras are connected to, from the repository folder, **with the LabVIEW camera server and IDS
peak Cockpit closed** (a camera is opened exclusively)::

    py -3 tools\camera_check.py --producer <ids_gevgentlk.cti> --alox-ini D:\alox\settings\alox.ini
    py -3 tools\camera_check.py --producer <ids_gevgentlk.cti> --alox-ini D:\alox\settings\alox.ini --actions

Without ``--producer`` the producers of ``GENICAM_GENTL64_PATH`` (set by the IDS peak setup) are used. ``--fake``
runs everything on the fake Harvesters stack of ``alox.devices.fake_harvesters`` (a rehearsal: no camera, no
producer, no harvesters package). The report (``camera_report_<date>-<time>.json``, a ``.txt`` summary and one
``.png`` per camera) is written next to this tool, or in ``--out``. That report is what to send back.

Information (the default)
-------------------------
1. Every camera the producer(s) see: vendor, model, serial number, user-defined name, transport layer, GenTL id.
2. Each camera to check (``--camera``, else the ``name_STR`` of ``[hardware.camera.highmag]`` and
   ``[hardware.camera.lowmag]`` of ``--alox-ini``, else the defaults ``HighMag`` / ``LowMag``; ``--name-map
   cam2=<serial>`` for NI MAX aliases) is opened as the application opens it, **without writing anything at open**
   (no pixel format, no packet size, no trigger mode), and read:

   - identity and network: vendor, model, firmware, serial number, user ID, IP address, subnet mask, MAC;
   - every feature the application uses (:data:`FEATURES`): type, access, value, min / max / unit, enumeration
     entries with their integer values; absent features are listed;
   - the features behind each selector (:data:`SELECTORS`: ``LineSelector`` → ``LineMode`` / ``LineSource`` /
     ``LineInverter`` / ``LineStatus``, ``UserOutputSelector`` → ``UserOutputValue``, ``TriggerSelector``,
     ``GainSelector``, ``DeviceTemperatureSelector``). **Selectors are the only features written in this mode**: each
     is stepped through its entries and put back to its original entry. A selector only chooses which line / output
     the other features show; no output changes;
   - the application's attributes through the driver (``alox.ini`` ``exposure_STR`` / ``gain_STR`` /
     ``framerate_STR`` with their types, ``MaxDBL`` of the frame rate, the temperature, ``TriggerMode``): the GenICam
     feature each one resolves to and the value the application reads.

3. ``--frames`` frames (default 5) are grabbed as the application grabs them (5-buffer ring, continuous, ``Next``
   mode, ``--timeout-ms``, default 5000): time to the first frame, frame rate achieved, frames missed, timeouts,
   incomplete buffers discarded by Harvesters (the jumbo-frame symptom). The last frame is saved as PNG with its mean
   / min / max grey level. An acquisition start / stop writes nothing the camera keeps.

Guided actions (``--actions``): each is explained and needs a typed ``yes``
---------------------------------------------------------------------------
Run after the information, on each checked camera (anything but ``yes`` skips an action, ``quit`` skips the rest):

1. Exposure: read the ``alox.ini`` exposure attribute, write ``--test-exposure`` (default 10000 µs, half of it when
   the exposure already has that value) with the ``alox.ini`` type, read it back, grab 2 frames (mean grey), write
   the original value back and read it back.
2. Packet size (GigE cameras): the application's ``PacketSize = 8996`` (``GevSCPSPacketSize``), grab ``--frames``
   frames, write the original value back. Timeouts or incomplete buffers here and not in the information grab mean the
   network card carries no jumbo frames.
3. LED / bulb, on the camera of ``--lights`` (default: the lights camera of ``alox.ini`` ``setLEDcam_I32``, i.e. High
   Mag): the application's own ``Camera.set_led(True)`` (GigE: ``LineSelector`` 1, ``LineSource`` 1,
   ``UserOutputSelector`` 0, ``UserOutputValue`` read then written; USB: ``LineSelector`` / ``LineMode`` /
   ``LineSource`` / ``LineInverter`` on the LED and bulb lines, docs/05 §9.2), read back, you say what you saw; then
   ``set_led(False)``, read back, you say what you saw. The output is switched off even after an error or Ctrl+C.

Rules: ``--yes`` (answers every question) is refused unless ``--fake`` is given. An action restores what it changed
(exposure, packet size) even after an error or Ctrl+C. Nothing is saved in the camera (no UserSet is written).

Exit code: 0 the tool ran to the end (findings and problems are in the report), 1 no producer, harvesters missing, the
camera list could not be read or a camera could not be opened, 2 usage error.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import itertools
import json
import logging
import math
import platform
import re
import subprocess
import sys
import time
import traceback
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

try:                                    # a source checkout without ``pip install -e .``: use <repo>/src
    import aloxcam  # noqa: F401
except ImportError:                     # pragma: no cover - depends on how the tool is started
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np

from alox.devices.base import DeviceError
from alox.devices.camera import Camera
from alox.devices.camera_genicam import (
    GenICamBackend,
    GenICamDevice,
    find_gentl_producers,
)
from alox.devices.fake_harvesters import (
    FAKE_PRODUCER,
    FakeHarvester,
    fake_cameras,
    gradient_image,
)
from aloxcam import PHASE
from aloxcam import __version__ as ALOXCAM_VERSION
from aloxcam.camera.attributes import (
    DEVICE_TEMPERATURE,
    EXPOSURE_UPPER_LIMIT,
    GET_IMAGE_TIMEOUT_MS,
    GIGE_PACKET_SIZE,
    NUM_BUFFERS,
    TRIGGER_MODE,
    read_typed,
    write_typed,
)
from aloxcam.camera.backend import Frame, ValueType
from aloxcam.config.alox_ini import load_alox_ini
from aloxcam.errors import CameraError, Code
from aloxcam.imaging.png import write_png
from aloxcam.settings import (
    PLUG_USB,
    AloxSettings,
    CameraOptions,
    CameraSettings,
    LightSettings,
)

__all__ = ["FEATURES", "SELECTORS", "Environment", "build_parser", "main", "read_node", "report_paths"]

TOOL_NAME = "camera_check"
TOOL_VERSION = "1.0"
TOOL_DIR = Path(__file__).resolve().parent
REPORT_PREFIX = "camera_report_"

DEFAULT_FRAMES = 5
MAX_FRAMES = 1000
DEFAULT_TEST_EXPOSURE_US = 10_000.0
MAX_TIMEOUTS_IN_A_ROW = 2               # a grab stops after this many get-image timeouts in a row
EXPECTED_SIZE = (2048, 2448)            # Sony IMX264, height x width (owner-answers §4)
FAKE_FRAME_PERIOD_S = 0.05              # --fake: 20 frames/s
EXIT_OK, EXIT_PROBLEM, EXIT_USAGE = 0, 1, 2
ACTION_QUESTION = ("Type yes and press Enter to run this action (anything else skips it; quit skips all the remaining "
                   "actions):")
#: LabVIEW's integer values of the LED enumerations (docs/05 §9.1 diagram comments) and the entry they mean.
LABVIEW_IO = (("LineSelector", 1, "Line1"), ("LineSource", 1, "UserOutput0"), ("UserOutputSelector", 0, "UserOutput0"))

IDENTITY = ("DeviceVendorName", "DeviceModelName", "DeviceFirmwareVersion", "DeviceVersion", "DeviceSerialNumber",
            "DeviceUserID", "GevCurrentIPAddress", "GevCurrentSubnetMask", "GevMACAddress")
#: The features the application uses (or that decide how it behaves), legacy names included.
FEATURES = (
    "ExposureTime", "ExposureTimeAbs", "ExposureTimeRaw", "ExposureAuto", "ExposureMode",
    "GainSelector", "Gain", "GainAbs", "GainRaw", "GainAuto",
    "AcquisitionMode", "AcquisitionFrameRate", "AcquisitionFrameRateAbs", "AcquisitionFrameRateEnable",
    "AcquisitionFrameRateTarget", "AcquisitionFrameRateTargetEnable", "ResultingFrameRate",
    "DeviceTemperatureSelector", "DeviceTemperature",
    "PixelFormat", "Width", "Height", "OffsetX", "OffsetY", "BinningHorizontal", "BinningVertical", "SensorWidth",
    "SensorHeight", "PayloadSize",
    "GevSCPSPacketSize", "GevSCPD", "DeviceLinkThroughputLimit",
    "TriggerSelector", "TriggerMode", "TriggerSource", "TriggerActivation", "TriggerSoftware",
    "LineSelector", "LineMode", "LineSource", "LineInverter", "LineStatus", "UserOutputSelector", "UserOutputValue",
    "UserSetSelector", "UserSetDefault",
)
#: Selector → the features it selects (read for every entry of the selector).
SELECTORS: dict[str, tuple[str, ...]] = {
    "LineSelector": ("LineMode", "LineSource", "LineInverter", "LineStatus"),
    "UserOutputSelector": ("UserOutputValue",),
    "TriggerSelector": ("TriggerMode", "TriggerSource", "TriggerActivation"),
    "GainSelector": ("Gain", "GainAuto"),
    "DeviceTemperatureSelector": ("DeviceTemperature",),
}
IO_FEATURES = ("LineSelector", "LineMode", "LineSource", "LineInverter", "UserOutputSelector", "UserOutputValue")
_TYPES = {2: "Integer", 3: "Boolean", 4: "Command", 5: "Float", 6: "String", 7: "Register", 8: "Category",
          9: "Enumeration"}
_ACCESS = {0: "NI", 1: "NA", 2: "WO", 3: "RO", 4: "RW"}       # GenApi EAccessMode


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
    fake_harvester: Any = None                          # --fake: use this harvester-like object (default: 2 cameras)


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
        except UnicodeEncodeError:                     # a console that cannot show a character
            self._out.write(text.encode("ascii", "backslashreplace").decode("ascii") + "\n")
        self._out.flush()

    def section(self, title: str) -> None:
        self.line()
        self.line(f"=== {title} ===")

    def _read(self, prompt: str, auto: str) -> str:
        if self.auto_yes:
            self.line(f"{prompt} {auto}   (--yes)")
            return auto
        self._out.write(prompt + " ")
        self._out.flush()
        try:
            return self._read_line()
        except EOFError:                                # no operator (input closed)
            self.line()
            return "<end of input>"

    def ask(self, question: str) -> str:
        """``"yes"`` only when the operator typed yes; ``"quit"`` for quit / q / stop or no input; ``"skip"``
        otherwise (a bare Enter included)."""
        raw = self._read(question.rstrip(), "yes")
        text = raw.strip().lower()
        meaning = "yes" if text == "yes" else "quit" if text in ("quit", "q", "stop", "<end of input>") else "skip"
        self.answers.append({"question": question, "answer": raw, "meaning": meaning})
        return meaning

    def text(self, question: str) -> str:
        """A free answer (what the operator saw)."""
        raw = self._read(question.rstrip(), "(no operator: --yes)")
        self.answers.append({"question": question, "answer": raw, "meaning": "text"})
        return raw.strip()


def describe(exc: BaseException) -> str:
    if isinstance(exc, (CameraError, DeviceError)):
        code = getattr(exc, "code", None)
        text = exc.source if isinstance(exc, CameraError) else str(exc)
        return f"error {code}: {text}" if code is not None else text
    return f"{type(exc).__name__}: {exc}"


def _attempt(fn: Callable[[], Any]) -> tuple[Any, str | None]:
    try:
        return fn(), None
    except Exception as exc:                            # noqa: BLE001 - every driver failure is a finding
        return None, describe(exc)


def _plain(value: Any) -> Any:
    """A JSON-friendly value (numpy and enum types, non-finite floats)."""
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return float(value) if math.isfinite(value) else str(value)
    return value if value is None or isinstance(value, str) else str(value)


def dotted_ip(value: Any) -> str | None:
    if not isinstance(value, int) or not 0 <= value <= 0xFFFF_FFFF:
        return None
    return ".".join(str((value >> s) & 0xFF) for s in (24, 16, 8, 0))


def mac_text(value: Any) -> str | None:
    if not isinstance(value, int) or not 0 <= value < 2**48:
        return None
    return ":".join(f"{(value >> s) & 0xFF:02X}" for s in range(40, -8, -8))


# ---------------------------------------------------------------------------------------------- raw GenApi nodes
class HarvesterSpy:
    """Wraps the Harvester (real or fake) given to the driver and keeps every image acquirer it creates, so that the
    tool can read the raw GenApi nodes (types, access, enumeration entries with their integer values)."""

    def __init__(self, harvester: Any) -> None:
        self.__dict__["_harvester"] = harvester
        self.__dict__["acquirers"] = {}

    def __getattr__(self, name: str) -> Any:
        return getattr(self._harvester, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._harvester, name, value)

    def create(self, search_key: Any, *args: Any, **kwargs: Any) -> Any:
        acquirer = self._harvester.create(search_key, *args, **kwargs)
        self.acquirers[search_key] = acquirer
        return acquirer


def get_node(node_map: Any, name: str) -> Any:
    try:
        return node_map.get_node(name)
    except Exception:                                   # noqa: BLE001 - GenApi LogicalErrorException: no such node
        return None


def read_node(node_map: Any, name: str) -> dict[str, Any]:
    """Type, access, value, min / max / unit / increment and enumeration entries of one feature."""
    node = get_node(node_map, name)
    rec: dict[str, Any] = {"feature": name, "present": node is not None}
    if node is None:
        return rec
    itype, _ = _attempt(lambda: int(node.node.principal_interface_type))
    rec["type"] = _TYPES.get(itype, str(itype))
    rec["access"], _ = _attempt(lambda: _ACCESS.get(int(node.get_access_mode()), "?"))
    if rec["type"] in ("Command", "Category", "Register") or rec["access"] in ("NI", "NA", "WO"):
        return rec
    value, err = _attempt(lambda: node.value)
    if err is None:
        rec["value"] = _plain(value)
    else:
        rec["error"] = err
    if rec["type"] in ("Integer", "Float"):
        for key in ("min", "max", "inc", "unit"):
            extra, err = _attempt(lambda key=key: getattr(node, key))
            if err is None and extra not in (None, ""):
                rec[key] = _plain(extra)
    if rec["type"] == "Enumeration":
        entries, _ = _attempt(lambda: [{"name": str(e.symbolic), "value": int(e.value)} for e in node.entries])
        if entries is not None:
            rec["entries"] = entries
        current, err = _attempt(lambda: int(node.get_int_value()))
        if err is None:
            rec["int_value"] = current
    return rec


def show_node(rec: dict[str, Any]) -> str:
    """One line: ``ExposureTime  Float RW  10000.0 us  [28.0 .. 2000000.0]``."""
    if not rec.get("present"):
        return f"{rec['feature']:<28} not present"
    head = f"{rec['feature']:<28} {rec.get('type', '?'):<11} {rec.get('access') or '?':<3}"
    if "error" in rec:
        return f"{head} read FAILED: {rec['error']}"
    if "value" not in rec:
        return head
    text = f"{head} {rec['value']!r}"
    if "int_value" in rec:
        text += f" (= {rec['int_value']})"
    if rec.get("unit"):
        text += f" {rec['unit']}"
    if "min" in rec or "max" in rec:
        text += f"  [{rec.get('min')} .. {rec.get('max')}]"
    if rec.get("entries"):
        text += "  entries: " + ", ".join(f"{e['name']}={e['value']}" for e in rec["entries"])
    return text


def step_selectors(node_map: Any) -> dict[str, dict[str, Any]]:
    """For each selector of :data:`SELECTORS`: the selected features under every entry; the selector is put back."""
    out: dict[str, dict[str, Any]] = {}
    for selector, selected in SELECTORS.items():
        node = get_node(node_map, selector)
        if node is None:
            continue
        rec: dict[str, Any] = {"entries": []}
        out[selector] = rec
        original, err = _attempt(lambda node=node: node.value)
        names, err2 = _attempt(lambda node=node: [str(e.symbolic) for e in node.entries])
        if err or err2:
            rec["error"] = err or err2
            continue
        for entry in names:
            row: dict[str, Any] = {"entry": entry}
            _, err = _attempt(lambda node=node, entry=entry: setattr(node, "value", entry))
            if err is not None:
                row["error"] = err                       # an entry not available on this camera
            else:
                for feature in selected:
                    r = read_node(node_map, feature)
                    if r["present"]:
                        row[feature] = r.get("value", r.get("error", r.get("access")))
            rec["entries"].append(row)
        _, err = _attempt(lambda node=node, original=original: setattr(node, "value", original))
        rec["restored"] = original if err is None else f"FAILED: {err}"
    return out


class IncompleteCounter(logging.Handler):
    """Counts Harvesters' "incomplete or not available; discarded" warnings (a buffer with missing packets)."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.count = 0
        self._logger = logging.getLogger("harvesters.core")
        self._saved: tuple[int, bool] | None = None

    def emit(self, record: logging.LogRecord) -> None:
        if "incomplete" in record.getMessage().lower():
            self.count += 1

    def attach(self) -> None:
        """After harvesters is imported (its import resets this logger to ERROR, propagate FALSE)."""
        if self._saved is None:
            self._saved = (self._logger.level, self._logger.propagate)
            self._logger.addHandler(self)
            self._logger.setLevel(logging.WARNING)
            self._logger.propagate = False

    def detach(self) -> None:
        if self._saved is not None:
            self._logger.removeHandler(self)
            self._logger.setLevel(self._saved[0])
            self._logger.propagate = self._saved[1]
            self._saved = None


# ---------------------------------------------------------------------------------------------- grabbing
def image_stats(pixels: np.ndarray) -> dict[str, Any]:
    return {"shape": list(pixels.shape), "dtype": str(pixels.dtype), "mean": round(float(pixels.mean()), 2),
            "min": int(pixels.min()), "max": int(pixels.max())}


def grab(device: GenICamDevice, frames: int, timeout_ms: int, counter: IncompleteCounter,
         monotonic: Callable[[], float]) -> tuple[dict[str, Any], Frame | None]:
    """``frames`` frames in ``Next`` mode from a continuous 5-buffer acquisition, with timing."""
    rec: dict[str, Any] = {"frames_wanted": frames, "timeout_ms": timeout_ms}
    counter.count = 0
    got: list[Frame] = []
    errors: list[str] = []
    timeouts = in_a_row = 0
    t0 = monotonic()
    device.configure(True, NUM_BUFFERS)
    device.start()
    try:
        for _ in range(frames):
            try:
                frame = device.get_next(timeout_ms)
            except CameraError as exc:
                errors.append(exc.source)
                timeouts += "timeout" in exc.source
                in_a_row += 1
                if in_a_row >= MAX_TIMEOUTS_IN_A_ROW:
                    break
                continue
            in_a_row = 0
            if not got:
                rec["first_frame_s"] = round(monotonic() - t0, 3)
            got.append(frame)
    finally:
        elapsed = monotonic() - t0
        rec["delivered"] = device.frames_acquired
        rec["last_driver_error"] = device.last_grab_error
        for step in (device.stop, device.unconfigure):
            _, err = _attempt(step)
            if err is not None:
                errors.append(f"{step.__name__}: {err}")
    numbers = [f.number for f in got]
    span = got[-1].t_monotonic - got[0].t_monotonic if len(got) >= 2 else 0.0
    rec.update(frames_received=len(got), numbers=numbers, timeouts=timeouts, errors=errors,
               missed=sum(b - a - 1 for a, b in itertools.pairwise(numbers) if b > a + 1),
               rate_fps=round((len(got) - 1) / span, 2) if span > 0 else None, elapsed_s=round(elapsed, 3),
               delivered_fps=round(rec["delivered"] / elapsed, 2) if elapsed > 0 else None,
               incomplete=counter.count)
    if got:
        rec["image"] = image_stats(got[-1].pixels)
    return rec, got[-1] if got else None


def app_row_text(row: dict[str, Any]) -> str:
    """``CameraAttributes::Exposure::Value (DBL) -> ExposureTime = 10000.0`` (or the error)."""
    shown = row["error"] if "error" in row else f"{row.get('feature')} = {row.get('value')!r}"
    return f"{row['attribute']} ({row['type']}) -> {shown}"


def frames_lost(rec: dict[str, Any]) -> bool:
    return bool(rec["timeouts"] or rec["incomplete"] or rec["frames_received"] < rec["frames_wanted"])


def grab_line(rec: dict[str, Any]) -> str:
    rate = f"{rec['rate_fps']} frames/s" if rec.get("rate_fps") else "rate n/a"
    first = f", first after {rec['first_frame_s']} s" if "first_frame_s" in rec else ""
    return (f"{rec['frames_received']}/{rec['frames_wanted']} frames, {rate}{first}, timeouts {rec['timeouts']}, "
            f"missed {rec['missed']}, incomplete buffers {rec['incomplete']}")


class LightsCamera(Camera):
    """The application's :class:`~alox.devices.camera.Camera` whose open only opens the driver session (no packet size,
    acquisition or trigger writes): ``set_led`` / ``led`` run the application's code unchanged."""

    def _open(self) -> None:
        self._device = self.backend.open_camera(self.open_name)


# ---------------------------------------------------------------------------------------------- report
def _git_commit() -> str | None:
    try:
        done = subprocess.run(["git", "rev-parse", "--short=12", "HEAD"], cwd=TOOL_DIR, capture_output=True,
                              text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() or None if done.returncode == 0 else None


def _version(package: str) -> str | None:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None


class Report:
    def __init__(self, argv: Sequence[str]) -> None:
        self.started = datetime.now().astimezone()
        self.data: dict[str, Any] = {
            "report": f"{TOOL_NAME} report",
            "versions": {"tool": TOOL_VERSION, "aloxcam": ALOXCAM_VERSION, "phase": PHASE,
                         "python": platform.python_version(), "harvesters": _version("harvesters"),
                         "genicam": _version("genicam"), "numpy": np.__version__, "platform": platform.platform(),
                         "git_commit": _git_commit()},
            "started": self.started.isoformat(timespec="seconds"),
            "command_line": [sys.argv[0] if sys.argv else TOOL_NAME, *argv],
            "producer": {},
            "alox_ini": None,
            "cameras_seen": [],
            "checks": [],
            "actions": None,
            "findings": [],
            "problems": [],
        }

    def finding(self, camera: str, topic: str, answer: str) -> None:
        self.data["findings"].append({"camera": camera, "topic": topic, "answer": answer})

    def problem(self, text: str) -> None:
        self.data["problems"].append(text)


def report_paths(out: Path | None, started: datetime) -> tuple[Path, Path]:
    """``<folder>/camera_report_<yyyymmdd-hhmmss>.json`` and ``.txt``; ``out`` is a folder or a ``.json`` name."""
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
    add("CAMERA CHECK REPORT")
    add(f"Send this report back: {json_path}  (with the .png files next to it; this .txt is the same report)")
    add("")
    v = data["versions"]
    add(f"Tool        {TOOL_NAME} {v['tool']}, aloxcam {v['aloxcam']} (phase {v['phase']}), git {v['git_commit']}")
    add(f"            Python {v['python']}, harvesters {v['harvesters']}, genicam {v['genicam']}, numpy {v['numpy']}")
    add(f"            {v['platform']}")
    add(f"Started     {data['started']}   Finished {data.get('finished', '')}")
    add(f"Command     {' '.join(data['command_line'])}")
    result = data.get("result", {})
    add(f"Result      {result.get('summary', '')} (exit code {result.get('exit_code', '')})")
    producer = data["producer"]
    add(f"Producer    {', '.join(producer.get('files', [])) or '(none)'} ({producer.get('source', '')})")
    if data.get("alox_ini"):
        ini = data["alox_ini"]
        add(f"alox.ini    {ini['path']}: High Mag {ini['high_mag']['name']!r}, Low Mag {ini['low_mag']['name']!r}, "
            f"lights camera {ini['lights_camera']!r}")
    add("")
    add("Cameras seen by the producer")
    for c in data["cameras_seen"]:
        add(f"  #{c['index']} {c['vendor']} {c['model']} serial {c['serial_number']} user name "
            f"{c['user_defined_name']!r} ({c['tl_type']}, {c['id']})")
    if not data["cameras_seen"]:
        add("  (none)")
    for check in data["checks"]:
        add("")
        add(f"Camera {check['name']!r} ({check['label']})")
        if "open_error" in check:
            add(f"  OPEN FAILED: {check['open_error']}")
            continue
        add(f"  Matched     {check['matched']}")
        net = check.get("network", {})
        add(f"  Network     IP {net.get('ip')}, mask {net.get('mask')}, MAC {net.get('mac')}")
        for row in check.get("application", []):
            add(f"  App         {row['what']}: {app_row_text(row)}")
        add("  Features")
        for rec in check.get("features", []):
            if rec.get("present"):
                add(f"    {show_node(rec)}")
        missing = [r["feature"] for r in check.get("features", []) if not r.get("present")]
        add(f"    not present: {', '.join(missing) or '-'}")
        for selector, rec in check.get("selectors", {}).items():
            add(f"  {selector} (restored to {rec.get('restored')!r})")
            for row in rec.get("entries", []):
                rest = {k: v for k, v in row.items() if k != "entry"}
                add(f"    {row['entry']:<14} {', '.join(f'{k}={v!r}' for k, v in rest.items())}")
        g = check.get("grab")
        if g:
            add(f"  Grab        {grab_line(g)}")
            if g.get("image"):
                add(f"              last frame {g['image']['shape']} {g['image']['dtype']}: mean {g['image']['mean']}, "
                    f"min {g['image']['min']}, max {g['image']['max']} -> {check.get('png')}")
            for e in g.get("errors", []):
                add(f"              {e}")
    actions = data.get("actions")
    if actions is not None:
        add("")
        add("Guided actions")
        for a in actions:
            add(f"  - {a['action']} on {a.get('camera', '')!r}: {a.get('status', '')}")
            for key in ("result", "error", "restore_error", "seen_on", "seen_off"):
                if a.get(key):
                    add(f"      {key}: {a[key]}")
    add("")
    add("Findings")
    for f in data["findings"]:
        add(f"  [{f['camera']}] {f['topic']}: {f['answer']}")
    if not data["findings"]:
        add("  (none)")
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
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------------------------- targets
@dataclass(frozen=True)
class Target:
    name: str
    label: str
    settings: CameraSettings


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name) or "camera"


def resolve_targets(args: argparse.Namespace, alox: AloxSettings | None) -> tuple[list[Target], Target, LightSettings]:
    """The cameras to check, the lights camera and its wiring."""
    hm, lm = (alox.high_mag, alox.low_mag) if alox else (CameraSettings.default_high_mag(),
                                                          CameraSettings.default_low_mag())
    known = {hm.camera_name: ("High Mag", hm)}
    known.setdefault(lm.camera_name, ("Low Mag", lm))
    names = list(dict.fromkeys(args.camera or [hm.camera_name, lm.camera_name]))
    targets = [Target(n, *known.get(n, ("--camera", CameraSettings.default_high_mag()))) for n in names]
    light = alox.light if alox else LightSettings()
    lights_name = args.lights or (hm.camera_name if light.set_led_cam == 0 else lm.camera_name)
    label, settings = known.get(lights_name, ("--lights", CameraSettings.default_high_mag()))
    light = replace(light, camera_sort_led=settings.plug_type)     # the lights camera's plug type (docs/05 §9.1)
    return targets, Target(lights_name, label, settings), light


def check_args(args: argparse.Namespace) -> None:
    if args.yes and not args.fake:
        raise UsageError("--yes is only accepted together with --fake: on the real cameras the operator must type "
                         "yes for every action")
    if args.fake and args.producer:
        raise UsageError("--fake and --producer cannot be used together (--fake loads no producer)")
    if not 1 <= args.frames <= MAX_FRAMES:
        raise UsageError(f"--frames must be between 1 and {MAX_FRAMES}")
    if args.timeout_ms <= 0:
        raise UsageError("--timeout-ms must be a positive number")
    if not (math.isfinite(args.test_exposure) and 0 < args.test_exposure <= EXPOSURE_UPPER_LIMIT):
        raise UsageError(f"--test-exposure must be in ]0, {EXPOSURE_UPPER_LIMIT:g}] us")
    if args.alox_ini is not None and not args.alox_ini.is_file():
        raise UsageError(f"alox.ini not found: {args.alox_ini}")
    for path in args.producer or []:
        if not path.is_file():
            raise UsageError(f"GenTL producer not found: {path}")
    for item in args.name_map or []:
        if "=" not in item or not item.split("=", 1)[0].strip() or not item.split("=", 1)[1].strip():
            raise UsageError(f"--name-map {item!r}: write NAME=SERIAL (e.g. cam2=4103123456)")


# ---------------------------------------------------------------------------------------------- the check
class CameraCheck:
    def __init__(self, args: argparse.Namespace, env: Environment, console: Console, report: Report,
                 alox: AloxSettings | None) -> None:
        self.args = args
        self.env = env
        self.console = console
        self.report = report
        self.targets, self.lights_target, self.light = resolve_targets(args, alox)
        self.json_path, _ = report_paths(args.out, report.started)
        self.counter = IncompleteCounter()
        self.spy: HarvesterSpy | None = None
        self.backend: GenICamBackend | None = None
        self.opened_all = True
        self.grab_means: dict[str, float] = {}

    # ============================================================================================== set-up
    def _harvester(self) -> HarvesterSpy:
        if self.args.fake:
            harvester = self.env.fake_harvester or FakeHarvester(
                fake_cameras(free_run_period=FAKE_FRAME_PERIOD_S, image=gradient_image))
        else:
            try:
                from harvesters.core import Harvester
            except ImportError as exc:
                raise CameraError(Code.CAMERA_SDK, f"the 'harvesters' package is not installed: run "
                                                   f"'py -3 -m pip install harvesters' ({exc})") from exc
            harvester = Harvester()
        self.spy = HarvesterSpy(harvester)
        return self.spy

    def _producers(self) -> list[Path]:
        producer = self.report.data["producer"]
        if self.args.fake:
            files, source = [FAKE_PRODUCER], "--fake (fake Harvesters stack, no producer loaded)"
        elif self.args.producer:
            files, source = list(self.args.producer), "--producer"
        else:
            files, source = find_gentl_producers(), "GENICAM_GENTL64_PATH"
        producer.update(files=[str(p) for p in files], source=source)
        return files

    def _node_map(self, device: GenICamDevice) -> Any:
        assert self.spy is not None
        return self.spy.acquirers[device.info.index].remote_device.node_map

    def _open(self, name: str) -> GenICamDevice:
        assert self.backend is not None
        return self.backend.open_camera(name)

    # ============================================================================================== run
    def run(self) -> int:
        c, r = self.console, self.report
        c.section(f"Camera check {TOOL_VERSION} (aloxcam {ALOXCAM_VERSION}) - information")
        if self.args.fake:
            c.line("REHEARSAL (--fake): a fake Harvesters stack inside this program; no camera, no producer.")
        files = self._producers()
        c.line(f"GenTL producer(s): {', '.join(map(str, files)) or '(none)'}  ({r.data['producer']['source']})")
        if not files:
            c.line("No GenTL producer: give --producer <path of the IDS peak .cti> (see --help), or install IDS peak")
            c.line("with the GigE Vision transport layer (its setup sets GENICAM_GENTL64_PATH).")
            r.problem("no GenTL producer (.cti): give --producer or set GENICAM_GENTL64_PATH")
            return EXIT_PROBLEM
        name_map = dict(item.split("=", 1) for item in self.args.name_map or [])
        self.backend = GenICamBackend(files, name_map={k.strip(): v.strip() for k, v in name_map.items()},
                                      pixel_format=None, harvester_factory=self._harvester, poll_s=0.02)
        try:
            if not self.list_cameras():
                return EXIT_PROBLEM
            if self.args.list:
                return EXIT_OK
            for target in self.targets:
                self.check_camera(target)
            if self.args.actions:
                self.run_actions()
        finally:
            self.counter.detach()
            _, err = _attempt(self.backend.close)
            if err is not None:
                r.problem(f"closing the driver failed: {err}")
        return EXIT_OK if self.opened_all else EXIT_PROBLEM

    def list_cameras(self) -> bool:
        c, r = self.console, self.report
        assert self.backend is not None
        try:
            infos = self.backend.device_infos()
        except CameraError as exc:
            c.line(f"The camera list could not be read: {exc.source}")
            r.problem(f"the camera list could not be read: {exc.source}")
            return False
        self.counter.attach()                           # harvesters is imported by now
        r.data["cameras_seen"] = [asdict(i) for i in infos]
        c.line(f"{len(infos)} camera(s) seen:")
        for i in infos:
            c.line(f"  #{i.index} {i.vendor} {i.model} serial {i.serial_number} user name {i.user_defined_name!r} "
                   f"({i.tl_type}, {i.id})")
        if not infos:
            r.problem("the producer sees no camera: check the cables, the power, the network card settings (IP "
                      "addresses in the cameras' subnet) and that IDS peak Cockpit sees them")
        return True

    # ============================================================================================== information
    def check_camera(self, target: Target) -> None:
        c, r = self.console, self.report
        c.section(f"Camera {target.name!r} ({target.label})")
        rec: dict[str, Any] = {"name": target.name, "label": target.label}
        r.data["checks"].append(rec)
        try:
            device = self._open(target.name)
        except CameraError as exc:
            self._open_failed(target, rec, exc)
            return
        try:
            self._information(target, device, rec)
        finally:
            _, err = _attempt(device.close)
            if err is not None:
                r.problem(f"{target.name}: closing failed: {err}")

    def _open_failed(self, target: Target, rec: dict[str, Any], exc: CameraError) -> None:
        self.opened_all = False
        rec["open_error"] = exc.source
        self.console.line(f"Could not open it: {exc.source}")
        hint = ""
        if "not found" in exc.source:
            hint = (f" -- give its serial number: --name-map {target.name}=<serial> (the list above), or set its "
                    f"user-defined name (DeviceUserID) to {target.name!r} in IDS peak Cockpit")
        elif "AccessDenied" in exc.source or "in use" in exc.source.lower():
            hint = " -- close the LabVIEW camera server and IDS peak Cockpit (a camera is opened exclusively)"
        if hint:
            self.console.line(hint.strip(" -"))
        self.report.problem(f"{target.name}: could not be opened: {exc.source}{hint}")

    def _information(self, target: Target, device: GenICamDevice, rec: dict[str, Any]) -> None:
        c = self.console
        node_map = self._node_map(device)
        info = device.info
        rec["matched"] = f"#{info.index} {info.describe()}"
        identity = {name: read_node(node_map, name) for name in IDENTITY}
        rec["identity"] = list(identity.values())
        rec["network"] = {"ip": dotted_ip(identity["GevCurrentIPAddress"].get("value")),
                          "mask": dotted_ip(identity["GevCurrentSubnetMask"].get("value")),
                          "mac": mac_text(identity["GevMACAddress"].get("value"))}
        c.line(f"Matched {rec['matched']}; IP {rec['network']['ip']}, MAC {rec['network']['mac']}")
        for node in identity.values():
            if node.get("present"):
                c.line(f"  {show_node(node)}")
        c.line("Features the application uses:")
        rec["features"] = [read_node(node_map, name) for name in FEATURES]
        for node in rec["features"]:
            if node.get("present"):
                c.line(f"  {show_node(node)}")
        missing = [n["feature"] for n in rec["features"] if not n.get("present")]
        c.line(f"  not present: {', '.join(missing) or '-'}")
        c.line("Selectors (each stepped through its entries, then put back):")
        rec["selectors"] = step_selectors(node_map)
        for selector, sel in rec["selectors"].items():
            c.line(f"  {selector}: restored to {sel.get('restored')!r}")
            for row in sel.get("entries", []):
                rest = ", ".join(f"{k}={v!r}" for k, v in row.items() if k != "entry")
                c.line(f"    {row['entry']:<14} {rest}")
        rec["application"] = self._application_reads(device, target.settings)
        c.line("The application's attributes (through the driver):")
        for row in rec["application"]:
            c.line(f"  {row['what']:<26} {app_row_text(row)}")
        c.line(f"Grabbing {self.args.frames} frame(s) (timeout {self.args.timeout_ms} ms each)...")
        try:
            grabbed, last = grab(device, self.args.frames, self.args.timeout_ms, self.counter, self.env.monotonic)
        except CameraError as exc:
            grabbed, last = {"frames_wanted": self.args.frames, "start_error": exc.source}, None
            c.line(f"  the acquisition did not start: {exc.source}")
        rec["grab"] = grabbed
        if "frames_received" in grabbed:
            c.line(f"  {grab_line(grabbed)}")
        if last is not None:
            rec["png"] = self._save_png(target, last.pixels)
            image = grabbed["image"]
            self.grab_means[target.name] = image["mean"]
            c.line(f"  last frame {image['shape']} {image['dtype']}: mean {image['mean']}, min {image['min']}, "
                   f"max {image['max']} -> {rec['png']}")
        self._findings(target, rec)

    def _application_reads(self, device: GenICamDevice, s: CameraSettings) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for what, attr, typ in (("exposure (exposure_STR)", s.exposure_attribute, s.exposure_type),
                                ("gain (gain_STR)", s.gain_attribute, s.gain_type),
                                ("frame rate (framerate_STR)", s.framerate_attribute, s.framerate_type),
                                ("temperature", DEVICE_TEMPERATURE, "DBL")):
            row: dict[str, Any] = {"what": what, "attribute": attr, "type": typ}
            try:
                row["feature"] = device.feature_name(attr)
                row["value"] = _plain(read_typed(device, attr, typ))
            except CameraError as exc:
                row["error"] = exc.source
            rows.append(row)
        for what, attr, read in (("frame rate max (MaxDBL)", s.framerate_attribute,
                                  lambda: device.read_attribute_max(s.framerate_attribute)),
                                 ("trigger mode", TRIGGER_MODE,
                                  lambda: device.read_attribute(TRIGGER_MODE, ValueType.STRING))):
            row = {"what": what, "attribute": attr, "type": "MaxDBL" if "max" in what else "STRING"}
            try:
                row["feature"] = device.feature_name(attr)
                row["value"] = _plain(read())
            except CameraError as exc:
                row["error"] = exc.source
            rows.append(row)
        return rows

    def _save_png(self, target: Target, pixels: np.ndarray) -> str | None:
        path = self.json_path.with_name(f"{self.json_path.stem}_{_safe_name(target.name)}.png")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            write_png(path, pixels)
        except (OSError, ValueError) as exc:
            self.report.problem(f"{target.name}: the frame could not be saved as PNG: {describe(exc)}")
            return None
        return str(path)

    # ============================================================================================== findings
    def _findings(self, target: Target, rec: dict[str, Any]) -> None:
        r, name = self.report, target.name
        f = {n["feature"]: n for n in rec["features"]}
        ident = {n["feature"]: n for n in rec["identity"]}

        def val(feature: str) -> Any:
            return f.get(feature, ident.get(feature, {})).get("value")
        r.finding(name, "Identity", f"{val('DeviceModelName')} firmware {val('DeviceFirmwareVersion')}, serial "
                                    f"{val('DeviceSerialNumber')}, user ID {val('DeviceUserID')!r}, IP "
                                    f"{rec['network']['ip']}")
        for row in rec["application"]:
            if "error" in row:
                r.problem(f"{name}: {row['what']} {row['attribute']} fails: {row['error']} -- map it with the "
                          f"backend's aliases or change the alox.ini attribute")
            else:
                r.finding(name, f"Application {row['what']}",
                          f"{row['attribute']} ({row['type']}) -> {row['feature']} = {row['value']!r}")
        gain = f.get("Gain", {})
        if gain.get("type") == "Float" and target.settings.gain_type != "DBL":
            r.finding(name, "Gain type", f"Gain is a float [{gain.get('min')} .. {gain.get('max')}]; the alox.ini "
                                         f"gain type {target.settings.gain_type} rounds it: use DBL")
        rate = f.get("AcquisitionFrameRate", {})
        enables = [f"{n}={f[n].get('value')!r}" for n in ("AcquisitionFrameRateEnable",
                                                         "AcquisitionFrameRateTargetEnable") if f[n].get("present")]
        r.finding(name, "Frame rate", f"AcquisitionFrameRate {rate.get('value')!r} (access {rate.get('access')}, "
                                      f"max {rate.get('max')}); enable features: {', '.join(enables) or 'none'}")
        fmt = f.get("PixelFormat", {})
        entries = [e["name"] for e in fmt.get("entries", [])]
        r.finding(name, "Pixel format", f"{fmt.get('value')!r} (entries {', '.join(entries) or '?'}); the "
                                        f"application writes Mono8 at open; Width x Height {val('Width')} x "
                                        f"{val('Height')} (IMX264: {EXPECTED_SIZE[1]} x {EXPECTED_SIZE[0]})")
        if entries and "Mono8" not in entries:
            r.problem(f"{name}: PixelFormat has no Mono8 entry")
        if (val("Height"), val("Width")) != EXPECTED_SIZE:
            r.problem(f"{name}: image size {val('Width')} x {val('Height')} is not the full IMX264 sensor "
                      f"(ROI or binning left in the camera?)")
        packet = f.get("GevSCPSPacketSize", {})
        if packet.get("present"):
            fits = isinstance(packet.get("max"), int) and packet["max"] >= GIGE_PACKET_SIZE
            r.finding(name, "Packet size", f"GevSCPSPacketSize {packet.get('value')!r} [{packet.get('min')} .. "
                                           f"{packet.get('max')}], GevSCPD {val('GevSCPD')!r}; the application writes "
                                           f"{GIGE_PACKET_SIZE}: {'allowed' if fits else 'NOT allowed'}")
        trigger = f.get("TriggerMode", {})
        r.finding(name, "Trigger", f"TriggerMode {trigger.get('value')!r}, TriggerSource "
                                   f"{f.get('TriggerSource', {}).get('value')!r} (the application writes Off at "
                                   f"start on GigE)")
        self._io_findings(name, f, rec.get("selectors", {}))
        r.finding(name, "UserSetDefault", repr(val("UserSetDefault")))
        g = rec["grab"]
        if "start_error" in g:
            r.problem(f"{name}: the acquisition did not start: {g['start_error']}")
            return
        r.finding(name, "Grab", f"{grab_line(g)}; camera AcquisitionFrameRate {rate.get('value')!r}")
        if frames_lost(g):
            r.problem(f"{name}: frames lost ({grab_line(g)}) -- incomplete buffers or timeouts usually mean a packet "
                      f"size larger than the network card carries: enable jumbo frames (9014 bytes) on the card, or "
                      f"use a smaller packet size; check TriggerMode = Off")

    def _io_findings(self, name: str, f: dict[str, dict[str, Any]], selectors: dict[str, Any]) -> None:
        r = self.report
        for feature, number, labview in LABVIEW_IO:
            entries = f.get(feature, {}).get("entries")
            if not entries:
                r.problem(f"{name}: {feature} not readable: the LED path cannot work as in LabVIEW")
                continue
            by_value = {e["value"]: e["name"] for e in entries}
            actual = by_value.get(number)
            same = actual == labview
            r.finding(name, f"{feature} {number}", f"integer {number} is {actual!r} on this camera (LabVIEW: "
                                                   f"{labview}){'' if same else ' -- DIFFERENT'}; entries "
                                                   + ", ".join(f"{e['name']}={e['value']}" for e in entries))
            if not same:
                r.problem(f"{name}: {feature} {number} is {actual!r}, not {labview}: map it with enum_values "
                          f"{{{feature!r}: {{{number}: {labview!r}}}}} if {labview} is the right entry")
        lines = selectors.get("LineSelector", {}).get("entries", [])
        outputs = [row["entry"] for row in lines if row.get("LineMode") == "Output"]
        r.finding(name, "Output lines", f"LineMode = Output on: {', '.join(outputs) or 'none'}; per line: "
                  + "; ".join(f"{row['entry']} {row.get('LineMode')}/{row.get('LineSource')}" for row in lines))

    # ============================================================================================== actions
    def run_actions(self) -> None:
        c = self.console
        actions: list[dict[str, Any]] = []
        self.report.data["actions"] = actions
        c.section("Guided actions (each needs yes)")
        plan: list[tuple[str, Target, Callable[[Target, dict[str, Any]], None]]] = []
        for target in self.targets:
            plan.append(("exposure", target, self._exposure_action))
            if target.settings.plug_type != PLUG_USB:
                plan.append(("packet size", target, self._packet_action))
        plan.append(("LED / bulb", self.lights_target, self._lights_action))
        stop = False
        for action, target, run in plan:
            rec: dict[str, Any] = {"action": action, "camera": target.name}
            actions.append(rec)
            if stop:
                rec["status"] = "skipped (quit)"
                continue
            c.line()
            c.line(f"--- {action} on {target.name!r} ({target.label}) ---")
            for line in self._explain(action, target):
                c.line(line)
            answer = c.ask(ACTION_QUESTION)
            if answer != "yes":
                rec["status"] = "skipped" if answer == "skip" else "skipped (quit)"
                stop = answer == "quit"
                continue
            try:
                run(target, rec)
                rec.setdefault("status", "done")
            except (CameraError, DeviceError) as exc:
                rec["status"] = "FAILED"
                rec["error"] = describe(exc)
                c.line(f"  FAILED: {describe(exc)}")
                self.report.problem(f"{target.name}: {action} failed: {describe(exc)}")
            c.line(f"  -> {rec['status']}{': ' + rec['result'] if rec.get('result') else ''}")

    def _explain(self, action: str, target: Target) -> list[str]:
        s = target.settings
        if action == "exposure":
            return [(f"Writes {s.exposure_attribute} ({s.exposure_type}) = {self.args.test_exposure:g} us, reads "
                     "it back, grabs 2 frames,"), "then writes the original value back and reads it back."]
        if action == "packet size":
            return [(f"Writes PacketSize (GevSCPSPacketSize) = {GIGE_PACKET_SIZE} as the application does at open, "
                     f"grabs {self.args.frames} frame(s),"),
                    "then writes the original value back. Timeouts here mean the network card has no jumbo frames."]
        path = ("USB: LineSelector / LineMode / LineSource / LineInverter on the LED line "
                f"{self.light.hm_led_line} and the bulb line {self.light.hm_bulb_line}"
                if self.light.camera_sort_led == PLUG_USB else
                "GigE: LineSelector = 1, LineSource = 1, UserOutputSelector = 0, UserOutputValue read then written")
        return ["Switches the LED / bulb output ON with the application's own code (Cam Set Led), then OFF.",
                f"Path {path}.", "Look at the LED and the bulb: you will be asked what you saw after each step."]

    def _exposure_action(self, target: Target, rec: dict[str, Any]) -> None:
        s, c = target.settings, self.console
        device = self._open(target.name)
        try:
            attr, typ = s.exposure_attribute, s.exposure_type
            rec["feature"] = device.feature_name(attr)
            original = read_typed(device, attr, typ)
            test = self.args.test_exposure
            if math.isclose(original, test, rel_tol=1e-6):          # a change is needed to see one
                test /= 2.0
                c.line(f"  the exposure is already {original:g} us: {test:g} us is written instead")
            rec.update(original=original, written=test)
            try:
                write_typed(device, attr, typ, test)
                rec["read_back"] = read_typed(device, attr, typ)
                grabbed, _ = grab(device, 2, self.args.timeout_ms, self.counter, self.env.monotonic)
                rec["grab"] = grabbed
                mean = grabbed.get("image", {}).get("mean")
                rec["result"] = (f"{original:g} -> wrote {test:g}, read back {rec['read_back']:g}; mean grey {mean} "
                                 f"(at {original:g}: {self.grab_means.get(target.name)})")
            finally:
                try:
                    write_typed(device, attr, typ, original)
                    rec["restored"] = read_typed(device, attr, typ)
                    rec["result"] = f"{rec.get('result', 'not completed')}; restored {rec['restored']:g}"
                except CameraError as exc:
                    rec["restore_error"] = exc.source
                    self.report.problem(f"{target.name}: the original exposure {original:g} could not be written "
                                        f"back: {exc.source}")
        finally:
            device.close()

    def _packet_action(self, target: Target, rec: dict[str, Any]) -> None:
        device = self._open(target.name)
        try:
            original = int(device.read_attribute("PacketSize", ValueType.I64))
            rec["original"] = original
            try:
                device.set_packet_size(GIGE_PACKET_SIZE)
                rec["written"] = int(device.read_attribute("PacketSize", ValueType.I64))
                grabbed, _ = grab(device, self.args.frames, self.args.timeout_ms, self.counter, self.env.monotonic)
                rec["grab"] = grabbed
                rec["result"] = f"packet size {rec['written']}: {grab_line(grabbed)}"
                if frames_lost(grabbed):
                    self.report.problem(f"{target.name}: frames lost at packet size {GIGE_PACKET_SIZE} "
                                        f"({grab_line(grabbed)}): enable jumbo frames (9014 bytes) on the network card")
            finally:
                try:
                    device.write_attribute("PacketSize", ValueType.I64, original)
                    rec["restored"] = int(device.read_attribute("PacketSize", ValueType.I64))
                except CameraError as exc:
                    rec["restore_error"] = exc.source
                    self.report.problem(f"{target.name}: the packet size {original} could not be written back: "
                                        f"{exc.source}")
        finally:
            device.close()

    def _io_snapshot(self) -> dict[str, Any]:
        assert self.backend is not None
        device = self.backend.devices.get(self.lights_target.name)
        if device is None:
            return {}
        node_map = self._node_map(device)
        return {n: read_node(node_map, n).get("value") for n in IO_FEATURES}

    def _lights_action(self, target: Target, rec: dict[str, Any]) -> None:
        c = self.console
        assert self.backend is not None
        camera = LightsCamera(self.backend, target.settings, name="lights", open_name=target.name, lights=self.light,
                              sleep=self.env.sleep)
        try:
            camera.open(timeout=30)
            switched_on = False
            try:
                camera.set_led(True)
                switched_on = True
                rec["read_on"] = camera.led()
                rec["io_on"] = self._io_snapshot()
                c.line(f"  ON written; the application reads the LED as {rec['read_on']}; I/O {rec['io_on']}")
                rec["seen_on"] = c.text("What did you see now (LED on? bulb on? nothing?), then Enter:")
            finally:
                camera.set_led(False)
                rec["read_off"] = camera.led()
                rec["io_off"] = self._io_snapshot()
                c.line(f"  OFF written; the application reads the LED as {rec['read_off']}; I/O {rec['io_off']}")
                if switched_on:
                    rec["seen_off"] = c.text("And now (all off?), then Enter:")
            rec["result"] = f"read back ON {rec['read_on']}, OFF {rec['read_off']}"
        finally:
            camera.shutdown(timeout=10)


# ---------------------------------------------------------------------------------------------- command line
def _help_epilog() -> str:
    return """\
information (the default): lists the cameras the producer sees, then opens each camera to check (--camera,
  else alox.ini name_STR of High Mag and Low Mag) without writing anything at open, reads the features the
  application uses (type, access, value, range, enumeration entries with their integer values), steps the
  selectors (LineSelector, UserOutputSelector, TriggerSelector, GainSelector, DeviceTemperatureSelector: the only
  features written, each put back), reads the application's attributes through the driver, grabs --frames frames
  with timing and saves the last one as PNG.

guided actions (--actions), each after a typed yes: test exposure written / read back / restored; packet size 8996
  (what the application writes) with a timed grab, restored; LED / bulb on then off on the lights camera with the
  application's Cam Set Led code, asking what you saw.

examples (Windows, from the repository folder, LabVIEW camera server and IDS peak Cockpit closed):
  py -3 tools\\camera_check.py --list
  py -3 tools\\camera_check.py --alox-ini D:\\alox\\settings\\alox.ini --name-map cam2=4103123456
  py -3 tools\\camera_check.py --producer "C:\\...\\ids_gevgentlk.cti" --alox-ini D:\\alox\\settings\\alox.ini --actions
  py -3 tools\\camera_check.py --fake --actions --yes                 (rehearsal, no hardware)

The report (camera_report_<date>-<time>.json, .txt and one .png per camera) is written next to this tool, or in
--out. Exit code: 0 done, 1 no producer / harvesters missing / camera list unreadable / a camera not opened, 2 usage.
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python tools\\camera_check.py",
        description="Check the IDS GigE Vision cameras through the application's GenICam driver: read-only "
                    "information and timed grabs, then optional guided actions. Writes a report to send back.",
        epilog=_help_epilog(), formatter_class=argparse.RawDescriptionHelpFormatter)
    where = p.add_argument_group("cameras")
    where.add_argument("--producer", type=Path, action="append", metavar="CTI",
                       help="GenTL producer file (IDS peak ids_gevgentlk.cti); repeatable (default: the .cti files "
                            "of GENICAM_GENTL64_PATH)")
    where.add_argument("--alox-ini", type=Path, metavar="PATH",
                       help="read the camera names (name_STR), attributes and the LED wiring from this alox.ini "
                            "(only read, never written)")
    where.add_argument("--camera", action="append", metavar="NAME",
                       help="camera to check, as the application names it; repeatable (default: alox.ini name_STR "
                            "of High Mag and Low Mag)")
    where.add_argument("--name-map", action="append", metavar="NAME=SERIAL",
                       help="open camera NAME (e.g. the NI MAX alias cam2) by its serial number; repeatable")
    where.add_argument("--list", action="store_true", help="only list the cameras the producer sees")
    where.add_argument("--frames", type=int, default=DEFAULT_FRAMES, metavar="N",
                       help=f"frames to grab per camera (default {DEFAULT_FRAMES})")
    where.add_argument("--timeout-ms", type=int, default=GET_IMAGE_TIMEOUT_MS, metavar="MS",
                       help=f"get-image timeout (default {GET_IMAGE_TIMEOUT_MS} ms, as the application)")
    where.add_argument("--fake", action="store_true",
                       help="rehearsal on a fake Harvesters stack (two cameras HighMag / LowMag); no hardware")
    act = p.add_argument_group("guided actions (THEY WRITE TO THE CAMERAS, each after a typed yes)")
    act.add_argument("--actions", action="store_true", help="after the information, offer the guided actions")
    act.add_argument("--test-exposure", type=float, default=DEFAULT_TEST_EXPOSURE_US, metavar="US",
                     help=f"exposure written by the exposure action (default {DEFAULT_TEST_EXPOSURE_US:g} us)")
    act.add_argument("--lights", metavar="NAME",
                     help="camera whose outputs drive the LED / bulb (default: alox.ini setLEDcam_I32, High Mag)")
    act.add_argument("--yes", action="store_true", help="answer yes to every question (only with --fake)")
    p.add_argument("--out", type=Path, metavar="PATH",
                   help="folder for the report (default: the folder of this tool), or a .json file name")
    return p


def _write_report(report: Report, out: Path | None, console: Console) -> tuple[Path, Path] | None:
    candidates = [report_paths(out, report.started)]
    if out is None:                                     # tools folder not writable: the current folder
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


def _final_lines(console: Console, paths: tuple[Path, Path] | None, summary: str, problems: int) -> None:
    console.line()
    console.line("=" * 78)
    console.line(f"Result: {summary}; {problems} problem(s) listed in the report")
    if paths is None:
        console.line("The report could NOT be written (see the error above).")
    else:
        console.line("Report written:")
        console.line(f"  {paths[0]}")
        console.line(f"  {paths[1]}  (the same report, easier to read)")
        console.line(f"PLEASE SEND BACK: {paths[0]} and the .png files next to it")
    console.line("=" * 78)


def main(argv: Sequence[str] | None = None, *, env: Environment | None = None) -> int:
    env = env or Environment()
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:                           # --help, or an argparse usage error (already printed)
        return int(exc.code) if isinstance(exc.code, int) else EXIT_USAGE
    console = Console(env.output or sys.stdout, env.read_line, auto_yes=bool(args.yes and args.fake))
    try:
        check_args(args)
    except UsageError as exc:
        console.line(f"error: {exc}")
        console.line("Run with --help to see the options.")
        return EXIT_USAGE
    report = Report(argv)
    report.data["options"] = {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()}
    alox: AloxSettings | None = None
    if args.alox_ini is not None:
        warnings: list[str] = []
        alox = load_alox_ini(args.alox_ini, CameraOptions(), warn=warnings.append)
        for warning in warnings:
            console.line(f"alox.ini warning: {warning}")
    code = EXIT_PROBLEM
    t0 = env.monotonic()
    try:
        check = CameraCheck(args, env, console, report, alox)
        if alox is not None:
            report.data["alox_ini"] = {
                "path": str(args.alox_ini),
                "high_mag": {"name": alox.high_mag.camera_name, "plug_type": alox.high_mag.plug_type,
                             "exposure": [alox.high_mag.exposure_attribute, alox.high_mag.exposure_type],
                             "gain": [alox.high_mag.gain_attribute, alox.high_mag.gain_type],
                             "framerate": alox.high_mag.framerate_attribute},
                "low_mag": {"name": alox.low_mag.camera_name, "plug_type": alox.low_mag.plug_type,
                            "exposure": [alox.low_mag.exposure_attribute, alox.low_mag.exposure_type],
                            "gain": [alox.low_mag.gain_attribute, alox.low_mag.gain_type],
                            "framerate": alox.low_mag.framerate_attribute},
                "lights_camera": check.lights_target.name, "light": asdict(check.light)}
        code = check.run()
    except KeyboardInterrupt:
        console.line()
        console.line("Interrupted (Ctrl+C).")
        report.problem("interrupted by Ctrl+C")
        code = EXIT_PROBLEM
    except Exception as exc:                            # noqa: BLE001 - the report must still be written
        console.line(f"Unexpected error: {describe(exc)}")
        report.problem(f"unexpected error: {describe(exc)}")
        report.data["traceback"] = traceback.format_exc()
        code = EXIT_PROBLEM
    finally:
        report.data["operator_answers"] = console.answers
        report.data["finished"] = datetime.now().astimezone().isoformat(timespec="seconds")
        report.data["duration_s"] = round(env.monotonic() - t0, 3)
        summary = {EXIT_OK: "done", EXIT_PROBLEM: "stopped early or a camera not reached"}.get(code, str(code))
        report.data["result"] = {"exit_code": code, "summary": summary}
        paths = _write_report(report, args.out, console)
        _final_lines(console, paths, summary, len(report.data["problems"]))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
