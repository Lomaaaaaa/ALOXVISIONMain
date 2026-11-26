"""The operator window's logic, without Qt: what each button does, what each indicator shows, who may click what.

LabVIEW: ``alox_core_ui.vi`` producer loop (73 event cases, map Part 1 §2.4), the function status manager
(``alox_manager_functions_status`` "Update": start buttons "Enabled" / "Disabled and Grayed Out" from functions
condition "Get Condition OK", status indicators from the wrapper "Get Status", d0, d25..d32, d105) and the rights
(``alox_manager_users`` "User Rights" checked by every button case). The window (:mod:`alox.ui.window`) only shows
what the :class:`Controller` computes and calls its methods; the controller talks to the application through the
:class:`alox.core.core.Core` (its runner, command bus, profiles, users, settings, log, devices) and the image manager.

Starts (observed: ``alox_core_ui d166, d171, d186, d203, d216, d285, d315, d320, d342, d357, d372``):

- **Low-level**: right of the function → "Set default camera" when the row is tied to a camera → the joysticks that
  the case switches off → parameters bundled from the active profile (:func:`alox.ui.adapters.build_params`: the
  params class ``from_profile``, else its defaults) with the case's overrides (camera type, refocus method Sobel,
  auto-exposure / wavelength mode) → the wrapper "Start" (:meth:`FunctionRunner.start`) → Tab Control =
  LOW-LEVEL MEASUREMENTS → log ``"<Function> (start clicked)"``. The pre-runs of the auto-exposure and
  wavelength cases (a Detect Circle Run Once to fill ``Circle In``) are not reproduced: the functions take their
  circle themselves.
- **Get Image**: GET IMAGE = "Run Once" with the selected camera (here the ``Get Image`` command of the core, as the
  supervisor's grab, so the system status shows 204); ▷ = "Start" = the continuous acquisition of the display
  (:class:`alox.functions.get_image.LiveView`), ■ stops both.
- **High-level**: right → system/measure status 200 → profile parameters with the local overrides → "Start" → Tab
  Control = HIGH-LEVEL MEASUREMENTS. Here the measurement goes through the same path as the supervisor's request:
  ``Measure <X>`` on the command bus with a :class:`alox.core.core.MeasureRequest` (the core sets the statuses and
  starts the function; the high-level function bundles the profile parameters itself). Overrides (keys of the REST
  API): Mesa single ``tilt=F, singleshot=T, positionsctrl=F, analysis=F``; Mesa continuous the same with
  ``singleshot=F``; Tilt ``x``, ``y`` = the current XY position in wafer coordinates ("Start Search Pos"); Bowing
  none. The SEMI job of a local start is empty (``alox_manager_semi`` is not ported).

Status indicators (``alox_manager_functions_status d25..d32``): text ``alox_<lang>.lang`` 000039..000043 (OFF, OFF
OK, RUNNING, FINISHED, ERROR), black text on: Off ``#FF9900``, Off Cond OK transparent (LabVIEW colour "T": the
panel shows through), Running ``#FFFF00``, Finished ``#66FF00``, Failed ``#FF0000``.

Threads: every method may block on a device (joystick, moves, monochromator) and is meant to be called from a
worker thread; :meth:`look`, :meth:`start_enabled` and the other queries only read in-memory state. Events of the
bus are given to :meth:`handle` in the UI thread (:mod:`alox.ui.bridge`).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any

from alox.api.backend import Measure
from alox.core.commands import Method
from alox.core.core import Core, MeasureRequest
from alox.core.functions import FunctionBusy, FunctionName, FunctionStatus, JobInfo
from alox.core.users import UserAction, UserType
from alox.devices.base import DeviceError, DeviceState
from alox.events import Event
from alox.ui.adapters import ImageProvider, build_params, stage_to_wafer, wafer_to_stage

__all__ = ["ROWS", "TOPICS", "ActionResult", "Controller", "FunctionRow", "Snapshot", "StartButton", "StatusLook",
           "Tab", "look_of"]

log = logging.getLogger(__name__)

#: Event topics the window listens to (``status.*``, ``function.*``, ``device.*``, ``core.*``, ``command.*``).
TOPICS = ("status", "function", "device", "core", "command")


class Tab(IntEnum):
    """The Tab Control of the right panel."""

    SETTINGS = 0
    LOW_LEVEL = 1
    HIGH_LEVEL = 2

    @property
    def label(self) -> str:
        return ("SETTINGS", "LOW-LEVEL MEASUREMENTS", "HIGH-LEVEL MEASUREMENTS")[self.value]


# ================================================================================================== status look
#: ``alox_<lang>.lang`` text id and English default of each function status.
STATUS_TEXTS: dict[FunctionStatus, tuple[int, str]] = {
    FunctionStatus.OFF: (39, "OFF"), FunctionStatus.OFF_COND_OK: (40, "OFF OK"),
    FunctionStatus.RUNNING: (41, "RUNNING"), FunctionStatus.FINISHED: (42, "FINISHED"),
    FunctionStatus.FAILED: (43, "ERROR")}
#: Text background of each status (None = transparent); the text is black for all.
STATUS_COLOURS: dict[FunctionStatus, str | None] = {
    FunctionStatus.OFF: "#ff9900", FunctionStatus.OFF_COND_OK: None, FunctionStatus.RUNNING: "#ffff00",
    FunctionStatus.FINISHED: "#66ff00", FunctionStatus.FAILED: "#ff0000"}
TEXT_COLOUR = "#000000"


@dataclass(frozen=True)
class StatusLook:
    """What a status indicator shows."""

    status: FunctionStatus
    text: str
    background: str | None
    foreground: str = TEXT_COLOUR


def look_of(status: FunctionStatus, text: Callable[[int], str | None] | None = None) -> StatusLook:
    """The look of ``status``; ``text(id)`` translates (None or a missing id: the English default)."""
    ident, default = STATUS_TEXTS[status]
    found = text(ident) if text is not None else None
    return StatusLook(status, (found or default).strip(), STATUS_COLOURS[status])


# ================================================================================================== buttons
@dataclass(frozen=True)
class StartButton:
    """One start button (LabVIEW control name ``key``). ``label``: its caption ("" = the ▷ icon). ``overrides``:
    parameter fields set over the profile's; ``camera``: "Set default camera" (0 High Mag., 1 Low Mag.) before the
    start; ``joysticks_off``: devices whose joystick the case switches off; ``measure``: a high-level start (command
    bus); ``live``: the continuous Get Image; ``tab``: the tab shown after the start."""

    key: str
    label: str
    log: str
    overrides: Mapping[str, Any] = field(default_factory=dict)
    camera: int | None = None
    joysticks_off: tuple[str, ...] = ()
    measure: Measure | None = None
    live: bool = False
    run_once: bool = False
    tab: Tab | None = Tab.LOW_LEVEL


@dataclass(frozen=True)
class FunctionRow:
    """One function row of the panel: caption, group, function, the rights of its start/stop and options buttons,
    its start buttons, the names of its stop and options buttons, and whether it has a status indicator."""

    key: str
    label: str
    group: str
    function: FunctionName
    action: UserAction
    setup: UserAction
    starts: tuple[StartButton, ...]
    stop_key: str
    options_key: str | None
    status: bool = True
    panel: str = ""

    @property
    def high_level(self) -> bool:
        return self.function.high_level


ACQUISITION = "IMAGE ACQUISITION"
LOW_MAG = "LOW-LEVEL FUNCTIONS: Low Mag"
HIGH_MAG = "LOW-LEVEL FUNCTIONS: High Mag"
BOWING_TILT = "HIGH-LEVEL FUNCTIONS: BOWING AND TILT"
MESA = "HIGH-LEVEL FUNCTIONS: MESA MEASUREMENT"
_HM, _LM = 0, 1
_MESA_LOCAL = {"tilt": False, "positionsctrl": False, "analysis": False}

#: The rows of the front panel, in its order (``alox_core_ui.vip``).
ROWS: tuple[FunctionRow, ...] = (
    FunctionRow("GET_IMAGE", "GET IMAGE", ACQUISITION, FunctionName.GET_IMAGE, UserAction.GET_IMAGE,
                UserAction.GET_IMAGE_SETUP,
                (StartButton("GET_IMAGE_RUN_BTN", "GET IMAGE", "Get Image", run_once=True, tab=None),
                 StartButton("GET_IMAGE_START_BTN", "", "Get Image (Start)", live=True, tab=None)),
                "GET_IMAGE_STOP_BTN", "GET_IMAGE_OPT_BTN", panel="Get Image"),
    FunctionRow("SEARCH_OBJ", "SEARCH OBJECT", LOW_MAG, FunctionName.SEARCH_OBJECT, UserAction.SEARCH_OBJECT,
                UserAction.SEARCH_OBJECT_SETUP,
                (StartButton("SEARCH_OBJ_RUN_BTN", "", "Search Object (start clicked)", camera=_LM),),
                "SEARCH_OBJ_STOP_BTN", "SEARCH_OBJ_OPT_BTN", panel="Search Object"),
    FunctionRow("AF", "AUTOFOCUS", LOW_MAG, FunctionName.AUTOFOCUS_LARGE, UserAction.AUTOFOCUS,
                UserAction.AUTOFOCUS_SETUP,
                (StartButton("AF_RUN_BTN", "", "Autofocus (start clicked)", {"camera_type": _LM}, camera=_LM,
                             joysticks_off=("z",)),),
                "AF_STOP_BTN", "AF_OPT_BTN", panel="Autofocus"),
    FunctionRow("AF_OXYD", "REFOCUS: LowMag", LOW_MAG, FunctionName.REFOCUS, UserAction.REFOCUS_OXYD,
                UserAction.REFOCUS_OXYD_SETUP,
                (StartButton("AF_OXYD_RUN_BTN", "", "Refocus (start clicked)", {"camera_type": _LM, "method": 1},
                             camera=_LM, joysticks_off=("z",)),),
                "AF_OXYD_STOP_BTN", None, status=False, panel="Refocus Oxyd"),
    FunctionRow("AF_FINE", "REFOCUS: HighMag", HIGH_MAG, FunctionName.REFOCUS, UserAction.REFOCUS,
                UserAction.REFOCUS_SETUP,
                (StartButton("AF_FINE_RUN_BTN", "", "Refocus (start clicked)", {"camera_type": _HM}, camera=_HM,
                             joysticks_off=("z",)),),
                "AF_FINE_STOP_BTN", "AF_FINE_OPT_BTN", panel="Refocus"),
    FunctionRow("DET_CIRCLE", "DETECT CIRCLE", HIGH_MAG, FunctionName.DETECT_CIRCLE, UserAction.DETECT_CIRCLE,
                UserAction.DETECT_CIRCLE_SETUP,
                (StartButton("DET_CIRCLE_RUN_BTN", "", "Detect Circle (start clicked)", camera=_HM),),
                "DET_CIRCLE_STOP_BTN", "DET_CIRCLE_OPT_BTN", panel="Detect Circle"),
    FunctionRow("TRACK", "TRACKING", HIGH_MAG, FunctionName.TRACKING, UserAction.TRACKING, UserAction.TRACKING_SETUP,
                (StartButton("TRACK_RUN_BTN", "", "Tracking (start clicked)", camera=_HM),),
                "TRACK_STOP_BTN", "TRACK_OPT_BTN", panel="Tracking"),
    FunctionRow("AUTO_EXPO", "AUTO EXPOSURE", HIGH_MAG, FunctionName.AUTO_EXPOSURE, UserAction.AUTO_EXPOSURE,
                UserAction.AUTO_EXPOSURE_SETUP,
                (StartButton("AUTO_EXPO_RUN_BTN", "Curr.", "Auto Exposure (Start clicked)", {"mode": 0}),
                 StartButton("AUTO_EXPO_RUN_AUTO_BTN", "Full", "Auto Exposure (Start auto clicked)", {"mode": 1})),
                "AUTO_EXPO_STOP_BTN", "AUTO_EXPO_OPT_BTN", panel="Auto Exposure"),
    FunctionRow("WL", "WAVELENGTH", HIGH_MAG, FunctionName.SEARCH_WAVELENGTH, UserAction.SEARCH_WAVELENGTH,
                UserAction.SEARCH_WAVELENGTH_SETUP,
                (StartButton("WL_RUN_BTN", "Manu", "Search Wavelength (start clicked)", {"wl_mode": 0}),
                 StartButton("WL_RUN_AUTO_BTN", "Search", "Search Wavelength (start auto clicked)",
                             {"wl_mode": 1, "overlay_rois": True})),
                "WL_STOP_BTN", "WL_OPT_BTN", panel="Search Wavelength"),
    FunctionRow("MES_VCSEL", "DETECT OXID MESA", HIGH_MAG, FunctionName.MEASURE_OXYD, UserAction.MEASURE_OXYD,
                UserAction.MEASURE_OXYD_SETUP,
                (StartButton("MES_VCSEL_RUN_BTN", "", "Measure Oxyd (start clicked)", camera=_HM),),
                "MES_VCSEL_STOP_BTN", "MES_VCSEL_OPT_BTN", panel="Measure Oxyd"),
    FunctionRow("MES_BOW", "BOW MEASUREMENT", BOWING_TILT, FunctionName.BOWING_MEASURE, UserAction.BOWING,
                UserAction.BOWING_SETUP,
                (StartButton("MES_BOW_RUN_BTN", "", "Bowing Measurement (start clicked)", camera=_LM,
                             joysticks_off=("xy", "z"), measure=Measure.BOWING, tab=Tab.HIGH_LEVEL),),
                "MES_BOW_STOP_BTN", "MES_BOW_OPT_BTN", panel="Bowing Measurement"),
    FunctionRow("MES_TILT", "TILT MEASUREMENT", BOWING_TILT, FunctionName.TILT_MEASURE, UserAction.TILT,
                UserAction.TILT_SETUP,
                (StartButton("MES_TILT_RUN_BTN", "", "Tilt Measurement (start clicked)", camera=_LM,
                             joysticks_off=("xy", "z"), measure=Measure.TILT, tab=Tab.HIGH_LEVEL),),
                "MES_TILT_STOP_BTN", "MES_TILT_OPT_BTN", panel="Tilt Measurement"),
    FunctionRow("MES_MESA", "MESA MEASUREMENT", MESA, FunctionName.MESA_MEASURE, UserAction.MESA,
                UserAction.MESA_SETUP,
                (StartButton("MES_MESA_RUN_SINGLE_BTN", "SINGLESHOT MEASURE", "MESA Measurement (start single clicked)",
                             {**_MESA_LOCAL, "singleshot": True}, joysticks_off=("xy", "z"), measure=Measure.MESA,
                             tab=Tab.HIGH_LEVEL),
                 StartButton("MES_MESA_RUN_CONT_BTN", "CONTINUOUS MEASURE",
                             "MESA Measurement (start continuous clicked)", {**_MESA_LOCAL, "singleshot": False},
                             camera=_HM, measure=Measure.MESA, tab=Tab.HIGH_LEVEL)),
                "MES_MESA_STOP_BTN", "MES_MESA_OPT_BTN", panel="Mesa Measurement"),
)
_ROWS = {row.key: row for row in ROWS}
_BUTTONS = {button.key: (row, button) for row in ROWS for button in row.starts}


@dataclass(frozen=True)
class ActionResult:
    """The outcome of a click: ``ok``, a message for the operator (shown in the alarm/status field) and the tab to
    show (None: unchanged)."""

    ok: bool
    message: str = ""
    tab: Tab | None = None
    data: Any = None


@dataclass(frozen=True)
class Snapshot:
    """What the periodic refresh read (the producer's ``Timeout`` case): joystick states (None: unknown), the
    monochromator position, whether the joysticks may be used, and ``F (ms)``, the period of the refresh."""

    xy_joystick: bool | None
    z_joystick: bool | None
    wavelength_nm: int | None
    joystick_ok: bool
    period_ms: float


# ================================================================================================== controller
class Controller:
    """The window's logic on top of ``core`` (module docstring). ``live``: the application's continuous Get Image
    (``start``/``stop``/``running``), None without it. ``images``: the display's :class:`ImageProvider` (default: the
    core's image manager)."""

    DEVICE_TIMEOUT_S = 5.0

    def __init__(self, core: Core, *, live: Any = None, images: ImageProvider | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.core = core
        self.live = live
        self._images = images
        self._clock = clock
        self._lock = threading.Lock()
        self._last_poll: float | None = None
        self.requested_tab: Tab | None = None
        self.visible_request: bool | None = None

    # ============================================================================================== reading
    @property
    def images(self) -> ImageProvider | None:
        return self._images if self._images is not None else self.core.images     # type: ignore[return-value]

    @property
    def rows(self) -> tuple[FunctionRow, ...]:
        return ROWS

    @staticmethod
    def row(key: str) -> FunctionRow:
        return _ROWS[key]

    @staticmethod
    def button(key: str) -> tuple[FunctionRow, StartButton]:
        return _BUTTONS[key]

    @property
    def user(self) -> UserType:
        return self.core.users.current

    def can(self, action: UserAction) -> bool:
        """"User Rights" of the current user (FALSE when the property is missing)."""
        try:
            return self.core.users.has_right(action)
        except ValueError:
            return False

    def available(self, name: FunctionName) -> bool:
        """The function is registered on the runner (a missing one is LabVIEW's 9002 "Invalid function")."""
        try:
            self.core.functions.function(name)
        except KeyError:
            return False
        return True

    def live_running(self) -> bool:
        return bool(self.live is not None and getattr(self.live, "running", False))

    def status(self, row: FunctionRow) -> FunctionStatus:
        """The status shown for ``row``: the runner's; for Get Image, RUNNING while the continuous acquisition runs
        and no function does (LabVIEW's "Get Image Run Once" special case)."""
        runner = self.core.functions
        status = runner.status(row.function)
        if row.function is FunctionName.GET_IMAGE and self.live_running() and runner.running is None:
            return FunctionStatus.RUNNING
        return status

    def look(self, row: FunctionRow) -> StatusLook:
        return look_of(self.status(row), self._text)

    def _text(self, ident: int) -> str | None:
        return self.core.translations.lookup(ident)

    def start_enabled(self, key: str) -> bool:
        """"Update Function Start Buttons states": enabled when no function runs (Get Condition OK), the function
        exists and the user has the right; the ▷ of Get Image also needs the continuous acquisition stopped."""
        row, button = _BUTTONS[key]
        if not self.can(row.action):
            return False
        if button.live:
            return self.live is not None and not self.live_running() and self.core.functions.condition_ok()
        return self.core.functions.condition_ok() and self.available(row.function)

    def stop_enabled(self, row: FunctionRow) -> bool:
        return self.can(row.action)

    def options_enabled(self, row: FunctionRow) -> bool:
        return row.options_key is not None and self.can(row.setup)

    def joystick_ok(self) -> bool:
        """"Get Joystick OK": no high-level function runs."""
        return self.core.functions.joystick_ok()

    def system_text(self) -> str:
        code, text = self.core.system_status()
        return f"{code} : {text}"

    def alarm_text(self) -> str:
        alarm = self.core.log.alarm
        return "" if alarm is None else alarm.line

    def log_text(self) -> str:
        return self.core.log.window_text()

    def core_infos(self) -> tuple[str, str]:
        """``("CORE #n", "LOCAL" | "REMOTE")`` (boot frame 5: ``general.core``, ``general.mode`` 0 = LOCAL)."""
        props = self.core.properties
        number = props.get("general.core", 1)
        mode = props.get("general.mode", 0)
        return f"CORE #{number}", "LOCAL" if not mode else "REMOTE"

    def title(self) -> str:
        """The window title (``alox_manager_titlebar`` "Update": profile and user)."""
        profile = self.core.profile_manager.current or "-"
        version = self.core.version()
        return (f"ALOX VISION CORE {version.major}.{version.minor}.{version.build} - PROFILE {profile} - "
                f"USER {self.user.label}")

    def profiles(self) -> list[str]:
        return self.core.profile_manager.list()

    def current_profile(self) -> str | None:
        return self.core.profile_manager.current

    def selected_camera(self) -> int:
        """``general.camera``: 0 High Mag., 1 Low Mag. (default Low Mag.)."""
        try:
            value = int(self.core.properties.get("general.camera", _LM))
        except (TypeError, ValueError):
            return _LM
        return value if value in (_HM, _LM) else _LM

    def initial_wavelength(self) -> int:
        """The SET WAVELENGTH control at start-up: profile ``wavelength.wlmanual`` (init cycle frame 27)."""
        try:
            return round(float(self.core.profile_manager.get("wavelength.wlmanual", 0) or 0))
        except (RuntimeError, TypeError, ValueError):
            return 0

    # ============================================================================================== events
    def handle(self, event: Event) -> set[str]:
        """Take one bus event (UI thread) and return the parts of the window to refresh: ``functions``,
        ``system``, ``measures``, ``profile``, ``profiles``, ``infos``, ``devices``, ``alarm``, ``visible``, ``tab``.
        A high-level function started by the supervisor shows the HIGH-LEVEL tab (consumer frame 1)."""
        topic = event.topic
        parts: set[str] = set()
        if topic == "status.system":
            parts |= {"system", "functions"}
        elif topic.startswith("status."):
            parts.add("measures")
        elif topic.startswith("function."):
            parts.add("functions")
            _, name, kind = (topic.split(".", 2) + ["", ""])[:3]
            if kind == "started":
                try:
                    function = FunctionName[name.upper()]
                except KeyError:
                    function = None
                if function is not None and function.high_level:
                    self.requested_tab = Tab.HIGH_LEVEL
                    parts.add("tab")
            if kind in ("failed", "stopped"):
                parts.add("alarm")
        elif topic == "core.profile":
            parts |= {"profile", "title"}
        elif topic == "core.profiles":
            parts.add("profiles")
        elif topic == "core.infos":
            parts.add("infos")
        elif topic == "core.show_ui":
            self.visible_request = bool(event.data.get("visible", True))
            parts.add("visible")
        elif topic.startswith("device."):
            parts |= {"devices", "functions"}
        elif topic.startswith("command."):
            parts.add("alarm")
        return parts

    def take_tab(self) -> Tab | None:
        with self._lock:
            tab, self.requested_tab = self.requested_tab, None
        return tab

    # ============================================================================================== refresh
    def poll(self) -> Snapshot:
        """The producer's ``Timeout`` case (d20..d61): XY / Z "Joystick Enabled?", the monochromator position, the
        joystick condition and ``F (ms)``. Blocks on the devices (worker thread)."""
        now = self._clock()
        with self._lock:
            period = 0.0 if self._last_poll is None else (now - self._last_poll) * 1000.0
            self._last_poll = now
        xy, z, wl = self._ready("xy"), self._ready("z"), self._ready("wl")
        xy_joy = self._read(lambda: xy.joystick_enabled(timeout=self.DEVICE_TIMEOUT_S)) if xy is not None else None
        z_joy = self._read(lambda: z.joystick_enabled(timeout=self.DEVICE_TIMEOUT_S)) if z is not None else None
        nm: int | None = None
        if wl is not None:
            last = getattr(wl, "last_position", None)
            nm = last if isinstance(last, int) else self._read(lambda: wl.position(timeout=self.DEVICE_TIMEOUT_S))
        return Snapshot(xy_joy, z_joy, nm, self.joystick_ok(), period)

    def _ready(self, name: str) -> Any:
        device = self.core.devices.get(name)
        return device if device is not None and device.state == DeviceState.READY else None

    @staticmethod
    def _read(read: Callable[[], Any]) -> Any:
        try:
            return read()
        except (DeviceError, ValueError) as exc:
            log.debug("refresh: %s", exc)
            return None

    # ============================================================================================== function buttons
    def press(self, key: str) -> ActionResult:
        """A start button (module docstring)."""
        row, button = _BUTTONS[key]
        if not self.can(row.action):
            return ActionResult(False, f"{row.label}: not allowed for {self.user.label}")
        if button.live:
            return self._start_live(row, button)
        if not self.core.functions.condition_ok():
            running = self.core.functions.running
            return ActionResult(False, f"{row.label}: {running.name if running else 'a function'} is running")
        if not self.available(row.function):
            return ActionResult(False, f"{row.label}: function not available")
        if button.camera is not None:
            self.select_camera(button.camera, quiet=True)
        self._joysticks_off(button.joysticks_off)
        if button.measure is not None:
            return self._start_measure(row, button)
        if button.run_once:
            return self._get_image(row, button)
        try:
            function = self.core.functions.function(row.function)
            profile = self.core.profile_manager if self.core.profile_manager.current is not None else None
            params = build_params(function, profile, button.overrides, warn=self.core.log.warning)
            self.core.functions.start(row.function, params)
        except (FunctionBusy, KeyError) as exc:
            return ActionResult(False, f"{row.label}: {exc}")
        except Exception as exc:                              # noqa: BLE001 - invalid parameters: refused, reported
            self.core.log.error(f"{button.log}: {exc}")
            return ActionResult(False, f"{row.label}: {exc}")
        self.core.log.info(button.log)
        return ActionResult(True, button.log, button.tab)

    def _get_image(self, row: FunctionRow, button: StartButton) -> ActionResult:
        self.core.commands.post(Method.GET_IMAGE, self.selected_camera(), source="ui")
        self.core.log.info(button.log)
        return ActionResult(True, button.log, button.tab)

    def _start_live(self, row: FunctionRow, button: StartButton) -> ActionResult:
        if self.live is None:
            return ActionResult(False, f"{row.label}: no continuous acquisition in this application")
        self.live.start()
        self.core.log.info(button.log)
        return ActionResult(True, button.log, button.tab)

    def _start_measure(self, row: FunctionRow, button: StartButton) -> ActionResult:
        assert button.measure is not None
        overrides = dict(button.overrides)
        if button.measure is Measure.TILT:
            position = self.wafer_position()
            if position is not None:
                overrides["x"], overrides["y"] = position
        request = MeasureRequest(button.measure, JobInfo(), self.core.profile_manager.current, overrides)
        self.core.commands.post(Method(int(button.measure)), request, source="ui")
        self.core.log.info(button.log)
        return ActionResult(True, button.log, button.tab, request)

    def stop(self, row_key: str) -> ActionResult:
        """A stop button: the wrapper "Stop" of the row's function when it is the one running (waits for it: worker
        thread); the ■ of Get Image also stops the continuous acquisition."""
        row = _ROWS[row_key]
        if not self.can(row.action):
            return ActionResult(False, f"{row.label}: not allowed for {self.user.label}")
        message = f"{row.label.title()} (Stop clicked)"
        if row.function is FunctionName.GET_IMAGE and self.live is not None:
            self.live.stop()
        stopped = True
        if self.core.functions.running == row.function:
            stopped = self.core.functions.stop()
        self.core.log.info(message)
        return ActionResult(stopped, message if stopped else f"{row.label}: did not stop in time")

    def open_options(self, row_key: str) -> ActionResult:
        """A gear button: the function's settings panel in the SETTINGS tab (``alox_manager_panels`` "Open Panel";
        the panels are not ported yet: the window shows a placeholder)."""
        row = _ROWS[row_key]
        if not self.options_enabled(row):
            return ActionResult(False, f"{row.label}: not allowed for {self.user.label}")
        self.core.log.info(f"{row.panel} settings (clicked)")
        return ActionResult(True, row.panel, Tab.SETTINGS, row.panel)

    def open_settings(self) -> ActionResult:
        """SETTINGS: right "Edit Properties", the properties UI ("Vision Core") in the SETTINGS tab."""
        if not self.can(UserAction.EDIT_PROPERTIES):
            return ActionResult(False, f"settings: not allowed for {self.user.label}")
        self.core.log.info("General properties (clicked)")
        return ActionResult(True, "Vision Core", Tab.SETTINGS, "Vision Core")

    # ============================================================================================== acquisition
    def select_camera(self, camera: int, *, quiet: bool = False) -> ActionResult:
        """Camera type: "Set default camera" (``general.camera``: 0 High Mag., 1 Low Mag.)."""
        if camera not in (_HM, _LM):
            return ActionResult(False, f"camera type {camera!r}")
        self.core.properties.set("general.camera", int(camera))
        if not quiet:
            self.core.log.info("Change camera clicked ok")
        return ActionResult(True)

    def save_image(self, path: str | Path) -> ActionResult:
        """SAVE IMAGE: the displayed image with its overlays ("Merge Overlays") written to ``path`` (PNG unless the
        suffix says otherwise). LabVIEW first runs one Get Image; the continuous acquisition keeps it fresh here."""
        provider = self.images
        image = provider.merged() if provider is not None else None
        if image is None:
            return ActionResult(False, "no image to save")
        target = Path(path)
        try:
            image.save(target)
        except (OSError, ValueError) as exc:
            return ActionResult(False, f"image not saved: {exc}")
        self.core.log.info(f"Save image (clicked): {target}")
        return ActionResult(True, str(target))

    # ============================================================================================== profiles
    def load_profile(self, name: str) -> ActionResult:
        """VISION PROFILE: right "Change Profile", then the ``Load Profile`` command (event ``core.profile``)."""
        if not self.can(UserAction.CHANGE_PROFILE):
            return ActionResult(False, f"profile: not allowed for {self.user.label}")
        if name == self.core.profile_manager.current:
            return ActionResult(True)
        self.core.commands.post(Method.LOAD_PROFILE, name, source="ui")
        return ActionResult(True, f"profile {name}")

    def refresh_profiles(self) -> ActionResult:
        """PROFILES_REFRESH: the ``Refresh Profiles`` command (event ``core.profiles``)."""
        self.core.commands.post(Method.REFRESH_PROFILES, source="ui")
        return ActionResult(True)

    def save_profile(self) -> ActionResult:
        """SAVE PROFILE: right "Edit Profile", profile "Save" (the working copy zipped to ``<profiles>``)."""
        if not self.can(UserAction.EDIT_PROFILE):
            return ActionResult(False, f"profile: not allowed for {self.user.label}")
        try:
            path = self.core.profile_manager.save()
        except (RuntimeError, OSError, ValueError) as exc:
            self.core.log.error(f"Save profile: {exc}")
            return ActionResult(False, f"profile not saved: {exc}")
        self.core.log.info(f"Profile saved: {path.name}")
        return ActionResult(True, str(path))

    def edit_profiles(self) -> ActionResult:
        """EDIT PROFILES: right "Edit Profile", then the profiles editor (``alox_profiles_ui``, not ported yet)."""
        if not self.can(UserAction.EDIT_PROFILE):
            return ActionResult(False, f"profiles: not allowed for {self.user.label}")
        return ActionResult(True, "Profiles", None, "Profiles")

    # ============================================================================================== users
    def login(self, user: UserType | int | str, password: str) -> ActionResult:
        """USER: ``alox_login_ui`` → users "Login" (the current user changes only on success), log "Login
        (clicked)"."""
        try:
            ok = self.core.users.login(user, password)
        except ValueError as exc:
            return ActionResult(False, str(exc))
        self.core.log.info("Login (clicked)")
        if not ok:
            return ActionResult(False, "wrong password")
        return ActionResult(True, f"user {self.user.label}")

    def logout(self) -> ActionResult:
        self.core.users.logout()
        return ActionResult(True, f"user {self.user.label}")

    # ====================================================================================== stages, monochromator
    def _wafer_frame(self) -> tuple[tuple[float, float], int]:
        props = self.core.properties
        origin = (float(props.get("hardware.xy.waferxorg", 0.0) or 0.0),
                  float(props.get("hardware.xy.waferyorg", 0.0) or 0.0))
        return origin, int(props.get("hardware.xy.stagescoordsys", 3) or 0)

    def wafer_position(self) -> tuple[float, float] | None:
        """XY "Get Position XY" converted to wafer coordinates (None when the stages are not ready)."""
        xy = self._ready("xy")
        if xy is None:
            return None
        position = self._read(lambda: xy.position(timeout=self.DEVICE_TIMEOUT_S))
        if position is None:
            return None
        origin, system = self._wafer_frame()
        return stage_to_wafer(position[0], position[1], origin, system)

    def move(self, axis: str, x: float, y: float) -> ActionResult:
        """MOVE X / MOVE Y (``alox_core_ui d414``): (X_CTRL, Y_CTRL) converted Wafer → Stages, then "Move Absolute"
        of the one axis; log "Move clicked: travel X (mm): %f". The move is not waited for."""
        if axis not in ("x", "y"):
            raise ValueError(f"axis {axis!r}")
        xy = self._ready("xy")
        if xy is None:
            return ActionResult(False, "XY stages not ready")
        origin, system = self._wafer_frame()
        sx, sy = wafer_to_stage(float(x), float(y), origin, system)
        target = sx if axis == "x" else sy
        try:
            if axis == "x":
                xy.move_absolute(x=sx, wait=False, timeout=self.DEVICE_TIMEOUT_S)
            else:
                xy.move_absolute(y=sy, wait=False, timeout=self.DEVICE_TIMEOUT_S)
        except DeviceError as exc:
            self.core.log.error(f"Move {axis.upper()}: {exc}")
            return ActionResult(False, str(exc))
        self.core.log.info(f"Move clicked: travel {axis.upper()} (mm): {target:f}")
        return ActionResult(True)

    def set_wavelength(self, nm: float) -> ActionResult:
        """SET WAVELENGTH (``MONO_MOVE``): monochromator "Set Position"."""
        wl = self._ready("wl")
        if wl is None:
            return ActionResult(False, "monochromator not ready")
        try:
            reached = wl.goto(round(nm), timeout=60.0)
        except (DeviceError, ValueError) as exc:
            self.core.log.error(f"Set wavelength: {exc}")
            return ActionResult(False, str(exc))
        self.core.log.info(f"Set wavelength (clicked): {round(nm)} nm")
        return ActionResult(True, data=reached)

    def set_joystick(self, name: str, on: bool) -> ActionResult:
        """XY JOYSTICK / Z JOYSTICK: switch the device's joystick; refused while a high-level function runs ("Get
        Joystick OK")."""
        if on and not self.joystick_ok():
            return ActionResult(False, "joystick: a high-level function is running")
        device = self._ready(name)
        if device is None:
            return ActionResult(False, f"{name.upper()} not ready")
        try:
            device.set_joystick(bool(on), timeout=self.DEVICE_TIMEOUT_S)
        except DeviceError as exc:
            self.core.log.error(f"{name.upper()} joystick: {exc}")
            return ActionResult(False, str(exc))
        self.core.log.info(f"{name.upper()} joystick {'on' if on else 'off'} (clicked)")
        return ActionResult(True)

    def _joysticks_off(self, names: tuple[str, ...]) -> None:
        for name in names:
            device = self._ready(name)
            if device is None:
                continue
            try:
                device.set_joystick(False, timeout=self.DEVICE_TIMEOUT_S)
            except DeviceError as exc:
                self.core.log.warning(f"{name.upper()} joystick off: {exc}")

    # ============================================================================================== window
    def clear_alarm(self) -> ActionResult:
        self.core.log.clear_alarm()
        return ActionResult(True)

    def request_exit(self) -> ActionResult:
        """EXIT (after the "CONFIRM EXIT ?" dialog): right "Exit App", then the ``Exit`` command (the application
        stops itself through ``Core.on_shutdown``)."""
        if not self.can(UserAction.EXIT_APP):
            return ActionResult(False, f"exit: not allowed for {self.user.label}")
        self.core.log.info("Exit (clicked)")
        self.core.commands.post(Method.EXIT, source="ui")
        return ActionResult(True)
