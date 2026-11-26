"""The thermal map tool: the logic of LabVIEW's thermal measurement window, without GUI.

Spec: ``docs/vision-core/map.md`` Part 6 §2.1. ``SM`` = ``alox_function_ll_thermal_measurement_state_machine`` (opened
by the core's "THERMAL MEASUREMENT" button, ``alox_core_ui d397``); frames cited as ``SM dN``.

What it does. A temperature probe — the Optris pyrometer (:class:`alox.thermal.optris.OptrisPyrometer`) or the kSA
BandiT reading over Modbus TCP (:class:`alox.thermal.bandit.BanditProbe`) — is moved by the XY stages to points on
concentric circles around the *thermal origin*; at each point the tool waits for the temperature to settle, takes
``NB Meas Pts`` samples, and writes the mean and deviation into a table with global and per-circle statistics, which
is exported as a tab-separated ``Thermal_measurement_%y%m%d_%H%M%S.csv`` and can be plotted in 3D.

API for the panel (all hardware work runs in the controller's threads; methods marked *blocking* are documented):

- life cycle: :meth:`ThermalController.open` (connects the default probe, starts the idle refresh),
  :meth:`~ThermalController.close` (*blocking*, short: stops everything and writes the settings back);
- state: :attr:`~ThermalController.state`, :attr:`~ThermalController.tool`, :attr:`~ThermalController.connected`,
  :attr:`~ThermalController.settings`, :attr:`~ThermalController.points`, :attr:`~ThermalController.table`,
  :attr:`~ThermalController.can_start`;
- settings (refused while measuring): :meth:`~ThermalController.set_circles`,
  :meth:`~ThermalController.set_offset_angle`, :meth:`~ThermalController.set_samples`,
  :meth:`~ThermalController.set_wait_ms`, :meth:`~ThermalController.set_script_command`;
- commands returning a :class:`concurrent.futures.Future` (run in the controller's command thread, one at a time):
  :meth:`~ThermalController.select_tool`, :meth:`~ThermalController.refresh`, :meth:`~ThermalController.move_to`,
  :meth:`~ThermalController.move_z`, :meth:`~ThermalController.set_joystick`, :meth:`~ThermalController.set_origin`,
  :meth:`~ThermalController.read_positions`, :meth:`~ThermalController.set_emissivity`,
  :meth:`~ThermalController.get_emissivity`, :meth:`~ThermalController.set_transmissivity`,
  :meth:`~ThermalController.get_transmissivity`, :meth:`~ThermalController.set_laser`;
- measurement: :meth:`~ThermalController.start` (returns at once; the measurement runs in its own thread),
  :meth:`~ThermalController.stop`, :meth:`~ThermalController.emergency_stop` (sends the stage stops from the caller's
  thread: quick), :meth:`~ThermalController.wait` (*blocking*, for tools and tests);
- results: :meth:`~ThermalController.default_csv_name`, :meth:`~ThermalController.export_csv` (*blocking*, a small
  file), :meth:`~ThermalController.launch_plot`, :meth:`~ThermalController.plot_png` (*blocking*, matplotlib).

Events (``alox.events.Event``, topic ``thermal.<kind>``, source ``thermal``): ``state`` {state},
``tool`` {tool, connected, error}, ``temperature`` {tool, value} (idle refresh and every sample), ``position``
{stage_x, stage_y, chuck_x, chuck_y, z}, ``origin`` {x, y}, ``points`` {count, circles}, ``progress`` {progress,
point, points, step, step_progress}, ``point`` {order, circle, x, y, rho, theta, temperature, std, failed},
``finished`` {completed, reason, measured, points, error, csv_name}, ``exported`` {path}, ``error`` {operation,
message, code}, ``emissivity`` / ``transmissivity`` {value}, ``laser`` {on}.

Reproduced from LabVIEW:

- the points (``get_pyrometer_measures_positions_tablesorted_v2 d``), their order (sorted on the angle), the table,
  its formats and its statistics, the CSV (:mod:`alox.thermal.table`);
- the state machine per point (``SM d148``–``d159``): START (step progress 0) → MOVING (point in chuck coordinates,
  Chuck → XY Stages with the stored coordinate system, ``Move Absolute XY``, ``SM d150``) → WAIT MOVE (``Get Position
  XY`` until ``|ΔX|`` and ``|ΔY|`` ≤ 0.01 mm, ``SM d151``) → WAIT TEMP (``hardware.bandit.wait`` ms, default 2000,
  ``SM d154``) → MEASURING (``NB Meas Pts`` samples, one every 50 ms in parallel with the read, mean and sample
  deviation, ``SM d155``–``d157``) → WRITING (table row and statistics, step progress 100, ``SM d158``) → END (0, next
  point, ``SM d159``); overall progress ``i · 100 / N`` before each point and 100 at the end (``SM d148``, ``d161``);
- STOP ends the measurement between two states and leaves a running move going (only the emergency stop stops the
  stages: ``Stop`` X, ``Stop`` Y, ``Z Stop``, ``SM d137``–``d139``); after STOP the export is offered as after a
  complete measurement (``SM d161`` runs in both cases; here the ``finished`` event carries the proposed file name);
- the tool selector: PYROMETER by default, BANDIT selectable only when ``hardware.bandit.enableBandit`` (``SM d10``);
  START is possible once the probe answered (``TOOL_CONNECTED``, ``SM d21``); SET EMISSIVITY / TRANSMIS. wait 250 ms
  and read the value back (``SM d117``); LASER sets the spot illumination (``SM d129``);
- moves of single axes from the X/Y fields, which are chuck coordinates (``SM d52``, ``d57``), the Z move and the
  positions shown in stage and chuck coordinates (``SM d27``, ``d37``);
- SET DEVICE ORIGIN: the current stage position becomes the chuck origin (thermal origin), stored in
  ``hardware.xy.chuckxorg/chuckyorg`` and sent to the XY device's chuck origin (``SM d90``, ``d91``);
- closing writes the panel values back to the settings (``SM d171``, :func:`alox.thermal.settings.write_back`).

Fixed (LabVIEW defect → here):

- the tool was a synchronous subVI of the core UI's event case (``alox_core_ui d397``): the main window froze while
  it was open → the controller runs in its own threads and never blocks the core;
- WAIT MOVE restarted its 30 s timer on every poll (``SM d152`` reads the tick count each time the state runs), so the
  move was never re-issued, and it waited forever → the timer starts when the move is issued; the move is re-issued
  after 30 s, at most ``MeasureOptions.move_attempts`` moves, then the measurement stops with an error; positions are
  polled every 50 ms instead of back to back;
- a refused move (outside the stage's allowed radius, error 9404) made LabVIEW poll forever → the measurement stops
  with the error, and :meth:`ThermalController.start` refuses a pattern whose points the XY device would refuse;
- a failed sample (NaN) entered the mean and made the point NaN (``SM d155``) → failed samples are left out and
  counted (``failed``); a point without any valid sample is NaN and is left out of the statistics (as LabVIEW's
  statistics already did);
- a point interrupted by STOP during MEASURING was written with its partial samples → it is not written;
- the probe was read by the idle refresh (``SM d21``, every ~100 ms, re-opening the port each time) concurrently with
  the measurement → no idle probe read while measuring (the samples are published instead);
- the 3D script ran after EXPORT even when the dialog was cancelled, with an unquoted path (``SM d100``,
  ``tool_plot_3D_map d``) → only after a file was written, path quoted (:mod:`alox.thermal.plot`);
- the thermal origin was not restored from the INI after a restart → read from ``hardware.xy.chuckxorg/chuckyorg``;
  START is refused while it is undefined (a 0 coordinate);
- the global ``ThermalMeasurement`` flag set while the window is open (``SM d1``, ``d176``; its readers were not
  identified, map Part 6 Q3) is :attr:`ThermalController.is_open`.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime
from enum import IntEnum, StrEnum
from pathlib import Path
from typing import Any, Self, TypeVar

from alox.devices.base import DeviceError, DeviceState
from alox.events import Event, Publish
from alox.ui.adapters import stage_to_wafer, wafer_to_stage

from . import plot as plot_module
from .settings import (
    MeasureOptions,
    ThermalConfig,
    ThermalSettings,
    write_back,
    write_origin,
)
from .table import (
    MeasurePoint,
    PointResult,
    ResultTable,
    default_csv_name,
    measurement_points,
    sample_statistics,
)

__all__ = [
    "STEP_PROGRESS",
    "ChuckFrame",
    "MeasurementSummary",
    "Positions",
    "Step",
    "ThermalBusy",
    "ThermalController",
    "ThermalError",
    "ThermalState",
    "Tool",
    "polar_to_chuck",
]

log = logging.getLogger(__name__)

T = TypeVar("T")


class Tool(IntEnum):
    """The ``Selector tool`` ring (``SM d10``)."""

    BANDIT = 0
    PYROMETER = 1


class ThermalState(StrEnum):
    CLOSED = "closed"
    IDLE = "idle"
    MEASURING = "measuring"
    STOPPING = "stopping"


class Step(StrEnum):
    """The states of ``PROC LOOP`` (``SM d149``–``d159``)."""

    START = "START"
    MOVING = "MOVING"
    WAIT_MOVE = "WAIT MOVE"
    WAIT_TEMP = "WAIT TEMP"
    MEASURING = "MEASURING"
    WRITING = "WRITING"
    END = "END"


#: ``PROGRESS Meas`` per state: 0 at START and END, 100 at WRITING (``SM d149``, ``d158``, ``d159``); in between
#: LabVIEW scaled the loop count, here the state's rank.
STEP_PROGRESS = {Step.START: 0, Step.MOVING: 17, Step.WAIT_MOVE: 33, Step.WAIT_TEMP: 50, Step.MEASURING: 67,
                 Step.WRITING: 100, Step.END: 0}


class ThermalError(Exception):
    """A refused or failed thermal operation; ``code`` is the device code when one caused it."""

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class ThermalBusy(ThermalError):
    """Refused because a measurement is running (LabVIEW disabled these controls, ``SM d97``)."""


class _Stopped(Exception):
    pass


@dataclass(frozen=True)
class ChuckFrame:
    """``alox_manager_xy_coordinate_system`` for the chuck: origin (stage mm) and stage system 0..3 (#1..#4)."""

    origin: tuple[float, float]
    system: int = 3

    def to_stage(self, x: float, y: float) -> tuple[float, float]:
        """Chuck → XY Stages (``xy-stage.md`` §9.2, :func:`alox.ui.adapters.wafer_to_stage`)."""
        return wafer_to_stage(x, y, self.origin, self.system)

    def to_chuck(self, x: float, y: float) -> tuple[float, float]:
        """XY Stages → Chuck."""
        return stage_to_wafer(x, y, self.origin, self.system)


@dataclass(frozen=True)
class Positions:
    """Stage and chuck XY (mm), Z (mm); None when not readable."""

    stage_x: float | None
    stage_y: float | None
    chuck_x: float | None
    chuck_y: float | None
    z: float | None


@dataclass(frozen=True)
class MeasurementSummary:
    completed: bool
    reason: str               # "completed", "stopped", "emergency stop", "error"
    measured: int
    points: int
    error: str | None
    csv_name: str


def polar_to_chuck(rho: float, theta_deg: float) -> tuple[float, float]:
    """RHO / THETA fields → chuck X / Y (mm)."""
    rad = math.radians(theta_deg)
    return rho * math.cos(rad), rho * math.sin(rad)


class ThermalController:
    """The thermal map tool. ``xy`` is the :class:`alox.devices.xy.XYStage`, ``z`` the
    :class:`alox.devices.zstage.ZStage` (optional), ``pyrometer`` / ``bandit`` the probe devices (either may be None);
    the controller opens and closes the probes, never the stages. ``store`` receives the settings when the tool
    closes (``set(name, value)``: the application's :class:`alox.core.properties.Properties`). ``clock``/``sleep``
    pace the measurement (inject a fake clock in tests); ``now`` gives the CSV time stamp."""

    def __init__(self, *, xy: Any, z: Any = None, pyrometer: Any = None, bandit: Any = None,
                 config: ThermalConfig | None = None, store: Any = None, publish: Publish | None = None,
                 options: MeasureOptions | None = None, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, now: Callable[[], datetime] = datetime.now,
                 device_timeout_s: float | None = None) -> None:
        self.xy = xy
        self.z = z
        self.probes: dict[Tool, Any] = {Tool.PYROMETER: pyrometer, Tool.BANDIT: bandit}
        self.store = store
        self.options = options or MeasureOptions()
        self._publish = publish
        self._clock = clock
        self._sleep = sleep
        self._now = now
        self._timeout = device_timeout_s
        self._config = config or ThermalConfig()
        self._settings: ThermalSettings = self._config.settings
        self._lock = threading.RLock()
        self._state = ThermalState.CLOSED
        self._tool = Tool.PYROMETER
        self._connected = False
        self._points = self._compute_points(self._settings)
        self._table = ResultTable(self._points)
        self._executor: ThreadPoolExecutor | None = None
        self._measure_thread: threading.Thread | None = None
        self._live_thread: threading.Thread | None = None
        self._live_stop = threading.Event()
        self._stop = threading.Event()
        self._stop_reason = "stopped"
        self._summary: MeasurementSummary | None = None
        self.last_temperature: float | None = None

    # ============================================================================================== events
    def _emit(self, kind: str, **data: Any) -> None:
        if self._publish is not None:
            try:
                self._publish(Event(f"thermal.{kind}", "thermal", dict(data)))
            except Exception:
                log.exception("thermal: publishing %s failed", kind)

    def _error(self, operation: str, exc: BaseException) -> None:
        code = getattr(exc, "code", None)
        message = getattr(exc, "message", None) or str(exc)
        log.error("thermal: %s failed: %s", operation, message)
        self._emit("error", operation=operation, message=message, code=code)

    # ============================================================================================== state
    @property
    def state(self) -> ThermalState:
        with self._lock:
            return self._state

    @property
    def is_open(self) -> bool:
        """LabVIEW's global ``ThermalMeasurement``: TRUE while the tool is open."""
        return self.state != ThermalState.CLOSED

    @property
    def measuring(self) -> bool:
        return self.state in (ThermalState.MEASURING, ThermalState.STOPPING)

    @property
    def tool(self) -> Tool:
        with self._lock:
            return self._tool

    @property
    def connected(self) -> bool:
        """``TOOL_CONNECTED``: the selected probe answered its last read."""
        with self._lock:
            return self._connected

    @property
    def settings(self) -> ThermalSettings:
        with self._lock:
            return self._settings

    @property
    def frame(self) -> ChuckFrame:
        s = self.settings
        return ChuckFrame(s.origin, s.coordinate_system)

    @property
    def points(self) -> list[MeasurePoint]:
        """The points of the current settings, in measurement order (the ``POSITIONS`` graph)."""
        with self._lock:
            return list(self._points)

    @property
    def table(self) -> ResultTable:
        """A copy of the result table."""
        with self._lock:
            return self._table.copy()

    @property
    def summary(self) -> MeasurementSummary | None:
        """The end of the last measurement (None before the first)."""
        with self._lock:
            return self._summary

    @property
    def can_start(self) -> bool:
        """What enables START: idle, probe connected, an origin and at least one point."""
        with self._lock:
            return (self._state == ThermalState.IDLE and self._connected and bool(self._points)
                    and self._settings.origin_defined)

    def _set_state(self, state: ThermalState) -> None:
        with self._lock:
            if state == self._state:
                return
            self._state = state
        self._emit("state", state=str(state))

    def _set_connected(self, connected: bool, error: str | None = None) -> None:
        with self._lock:
            changed = connected != self._connected
            self._connected = connected
            tool = self._tool
        if changed or error:
            self._emit("tool", tool=tool.name, connected=connected, error=error)

    def _probe(self, tool: Tool | None = None) -> Any:
        tool = self.tool if tool is None else tool
        probe = self.probes.get(tool)
        if probe is None:
            raise ThermalError(f"no {tool.name.lower()} configured")
        return probe

    # ============================================================================================== settings
    @staticmethod
    def _compute_points(settings: ThermalSettings) -> list[MeasurePoint]:
        return measurement_points(settings.diameters, settings.nb_points, settings.offset_angle)

    def _change_settings(self, **changes: Any) -> None:
        with self._lock:
            if self._state in (ThermalState.MEASURING, ThermalState.STOPPING):
                raise ThermalBusy("a measurement is running")
            settings = replace(self._settings, **changes)
            self._settings = settings
            self._points = self._compute_points(settings)
            self._table = ResultTable(self._points)
            count = len(self._points)
        self._emit("points", count=count, circles=sum(1 for n in settings.nb_points if n > 0))

    def set_circles(self, diameters: Sequence[float], nb_points: Sequence[int]) -> None:
        """Diameters (mm) and points per circle (at most 9 circles); recomputes the points (event [1])."""
        try:
            self._change_settings(diameters=tuple(float(d) for d in diameters),
                                  nb_points=tuple(int(n) for n in nb_points))
        except ValueError as exc:
            raise ThermalError(str(exc)) from exc

    def set_offset_angle(self, degrees: int) -> None:
        self._change_settings(offset_angle=int(degrees))

    def set_samples(self, count: int) -> None:
        """``NB Meas Pts`` (≥ 1)."""
        try:
            self._change_settings(samples=int(count))
        except ValueError as exc:
            raise ThermalError(str(exc)) from exc

    def set_wait_ms(self, milliseconds: int) -> None:
        """Settle time after each move (``hardware.bandit.wait``)."""
        try:
            self._change_settings(wait_ms=int(milliseconds))
        except ValueError as exc:
            raise ThermalError(str(exc)) from exc

    def set_script_command(self, command: str) -> None:
        """The external 3D-map command prefix (``hardware.bandit.ScriptPythonPath``)."""
        self._change_settings(script_command=str(command))

    # ============================================================================================== life cycle
    def open(self, *, live: bool = True) -> Future[bool]:
        """Open the tool: select the pyrometer (``SM d10``), connect it in the command thread, and (``live``) start
        the idle refresh of the temperature and positions every ``MeasureOptions.live_period_s``. Returns the
        connection's future (TRUE when the probe answered)."""
        with self._lock:
            if self._state != ThermalState.CLOSED:
                raise ThermalError("the thermal tool is already open")
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="thermal-command")
            self._tool = Tool.PYROMETER
            self._connected = False
        self._set_state(ThermalState.IDLE)
        self._emit("origin", x=self.settings.origin[0], y=self.settings.origin[1])
        future = self._submit("connect", self._connect)
        if live:
            self._live_stop.clear()
            self._live_thread = threading.Thread(target=self._live_loop, name="thermal-live", daemon=True)
            self._live_thread.start()
        return future

    def close(self, *, save: bool = False, timeout: float = 10.0) -> list[str]:
        """*Blocking* (≤ ``timeout``): stop a measurement (the stages are not stopped), stop the refresh and the
        command thread, close the probes, write the settings back to ``store`` (``SM d171``; ``save`` also calls
        ``store.save()``, else the application saves ``alox.ini`` at exit). Returns the names written."""
        if self.state == ThermalState.CLOSED:
            return []
        self._request_stop("stopped", replace_reason=False)
        thread = self._measure_thread
        if thread is not None:
            thread.join(timeout)
        self._live_stop.set()
        if self._live_thread is not None:
            self._live_thread.join(timeout)
            self._live_thread = None
        executor, self._executor = self._executor, None
        if executor is not None:                                 # a command still running ends on its own
            executor.shutdown(wait=False, cancel_futures=True)
        for probe in self.probes.values():
            if probe is not None:
                try:
                    probe.close(timeout=timeout)
                except DeviceError as exc:
                    log.warning("thermal: closing %s failed: %s", probe.name, exc)
        written: list[str] = []
        if self.store is not None:
            written = write_back(self.store, self.settings)
            if save and hasattr(self.store, "save"):
                try:
                    self.store.save()
                except (OSError, ValueError, TypeError) as exc:
                    self._error("save settings", exc)
        with self._lock:
            self._connected = False
        self._set_state(ThermalState.CLOSED)
        return written

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ============================================================================================== commands
    def _submit(self, operation: str, fn: Callable[[], T]) -> Future[T]:
        executor = self._executor
        if executor is None:
            raise ThermalError("the thermal tool is not open")

        def run() -> T:
            try:
                return fn()
            except Exception as exc:
                self._error(operation, exc)
                raise
        return executor.submit(run)

    def _not_measuring(self, what: str) -> None:
        if self.measuring:
            raise ThermalBusy(f"{what}: a measurement is running")

    def _connect(self) -> bool:
        """Open the selected probe if needed and read it once (``Init`` + ``Get Obj Temp.``, ``SM d21``; for the
        BandiT LabVIEW pinged the address, ``SM d130``)."""
        tool = self.tool
        probe = self._probe(tool)
        try:
            if probe.state != DeviceState.READY:
                probe.open(timeout=self._timeout)
            value = probe.temperature(timeout=self._timeout)
        except DeviceError as exc:
            self._set_connected(False, error=str(exc))
            return False
        self.last_temperature = value
        self._set_connected(True)
        self._emit("temperature", tool=tool.name, value=value)
        return True

    def select_tool(self, tool: Tool | int) -> Future[bool]:
        """``Selector tool`` (``SM d114``): switch probe and connect it. BANDIT needs
        ``hardware.bandit.enableBandit``."""
        choice = Tool(int(tool))
        self._not_measuring("select tool")
        if choice == Tool.BANDIT and not self.settings.bandit_enabled:
            raise ThermalError("the BandiT is not enabled (hardware.bandit.enableBandit)")
        self._probe(choice)
        with self._lock:
            previous, self._tool = self._tool, choice
            self._connected = False
        self._emit("tool", tool=choice.name, connected=False, error=None)
        other = self.probes.get(previous)
        if previous != choice and other is not None:
            def switch() -> bool:
                try:
                    other.close(timeout=self._timeout)
                except DeviceError as exc:
                    log.warning("thermal: closing %s failed: %s", other.name, exc)
                return self._connect()
            return self._submit("select tool", switch)
        return self._submit("select tool", self._connect)

    def refresh(self) -> Future[bool]:
        """``Refresh Button`` (``SM d130``): connect the selected probe again."""
        self._not_measuring("refresh")
        return self._submit("refresh", self._connect)

    def _read_positions(self) -> Positions:
        sx = sy = cx = cy = zz = None
        try:
            sx, sy = self.xy.position(timeout=self._timeout)
            cx, cy = self.frame.to_chuck(sx, sy)
        except DeviceError as exc:
            log.debug("thermal: XY position not read: %s", exc)
        if self.z is not None:
            try:
                zz = self.z.position(timeout=self._timeout)
            except DeviceError as exc:
                log.debug("thermal: Z position not read: %s", exc)
        positions = Positions(sx, sy, cx, cy, zz)
        self._emit("position", stage_x=sx, stage_y=sy, chuck_x=cx, chuck_y=cy, z=zz)
        return positions

    def read_positions(self) -> Future[Positions]:
        """Stage and chuck XY, Z (``SM d27``, ``d37``)."""
        return self._submit("read positions", self._read_positions)

    def move_to(self, x: float | None = None, y: float | None = None) -> Future[tuple[float, float]]:
        """X_MOVE / Y_MOVE (``SM d52``, ``d57``): move the given axes to chuck coordinates (Chuck → XY Stages, each axis
        on its own). Returns the stage position reached."""
        self._not_measuring("move")
        if x is None and y is None:
            raise ThermalError("move: no axis given")
        sx, sy = self.frame.to_stage(0.0 if x is None else float(x), 0.0 if y is None else float(y))

        def run() -> tuple[float, float]:
            reached = self.xy.move_absolute(None if x is None else sx, None if y is None else sy, wait=True,
                                            timeout=self._timeout)
            self._read_positions()
            return tuple(reached)                                          # type: ignore[return-value]
        return self._submit("move", run)

    def move_z(self, z_mm: float) -> Future[float]:
        """Z_MOVE: absolute Z move (mm)."""
        self._not_measuring("move Z")
        if self.z is None:
            raise ThermalError("no Z stage")

        def run() -> float:
            reached = float(self.z.move_absolute(float(z_mm), wait=True, timeout=self._timeout))
            self._read_positions()
            return reached
        return self._submit("move Z", run)

    def set_joystick(self, enabled: bool) -> Future[None]:
        """XYZ_JOYSTICK: the joystick on the XY stages and, when present, the Z stage."""
        self._not_measuring("joystick")

        def run() -> None:
            self.xy.set_joystick(bool(enabled), timeout=self._timeout)
            if self.z is not None:
                self.z.set_joystick(bool(enabled), timeout=self._timeout)
        return self._submit("joystick", run)

    def set_origin(self) -> Future[tuple[float, float]]:
        """SET DEVICE ORIGIN (``SM d90``, ``d91``): the current stage position becomes the thermal (chuck) origin,
        written to ``hardware.xy.chuckxorg/chuckyorg`` and to the XY device's chuck origin."""
        self._not_measuring("set origin")

        def run() -> tuple[float, float]:
            x, y = self.xy.position(timeout=self._timeout)
            origin = (float(x), float(y))
            self._change_settings(origin=origin)
            if self.store is not None:
                write_origin(self.store, origin)
            if hasattr(self.xy, "set_wafer"):
                try:
                    self.xy.set_wafer(chuck_origin=origin)
                except (DeviceError, TypeError, ValueError) as exc:
                    log.warning("thermal: chuck origin not sent to the XY device: %s", exc)
            log.info("thermal: thermal origin set to (%.6f, %.6f) mm", *origin)
            self._emit("origin", x=origin[0], y=origin[1])
            return origin
        return self._submit("set origin", run)

    # -- pyrometer settings (allowed during a measurement, as in LabVIEW)
    def _pyro_call(self, operation: str, fn: Callable[[Any], T]) -> Future[T]:
        probe = self._probe(Tool.PYROMETER)
        return self._submit(operation, lambda: fn(probe))

    def set_emissivity(self, value: float) -> Future[float]:
        """SET EMISSIVITY: set, wait 250 ms, read back (``SM d117``)."""
        def run(probe: Any) -> float:
            read = float(probe.set_emissivity(float(value), timeout=self._timeout))
            self._emit("emissivity", value=read)
            return read
        return self._pyro_call("set emissivity", run)

    def get_emissivity(self) -> Future[float]:
        def run(probe: Any) -> float:
            read = float(probe.emissivity(timeout=self._timeout))
            self._emit("emissivity", value=read)
            return read
        return self._pyro_call("get emissivity", run)

    def set_transmissivity(self, value: float) -> Future[float]:
        def run(probe: Any) -> float:
            read = float(probe.set_transmissivity(float(value), timeout=self._timeout))
            self._emit("transmissivity", value=read)
            return read
        return self._pyro_call("set transmissivity", run)

    def get_transmissivity(self) -> Future[float]:
        def run(probe: Any) -> float:
            read = float(probe.transmissivity(timeout=self._timeout))
            self._emit("transmissivity", value=read)
            return read
        return self._pyro_call("get transmissivity", run)

    def set_laser(self, on: bool) -> Future[None]:
        """LASER: the pyrometer's sighting laser (``SM d129``)."""
        def run(probe: Any) -> None:
            probe.set_laser(bool(on), timeout=self._timeout)
            self._emit("laser", on=bool(on))
        return self._pyro_call("laser", run)

    # ============================================================================================== idle refresh
    def refresh_live(self) -> None:
        """One idle refresh (what LabVIEW's event timeout did, ``SM d21``, ``d27``, ``d37``): read the selected probe
        and the positions. Skipped while measuring. Called by the refresh thread; tests call it directly."""
        if self.state != ThermalState.IDLE:
            return
        tool = self.tool
        probe = self.probes.get(tool)
        if probe is not None:
            try:
                value = probe.temperature(timeout=self._timeout)
            except DeviceError as exc:
                self._set_connected(False, error=str(exc))
            else:
                if self.tool == tool:
                    self.last_temperature = value
                    self._set_connected(True)
                    self._emit("temperature", tool=tool.name, value=value)
        self._read_positions()

    def _live_loop(self) -> None:
        while not self._live_stop.wait(self.options.live_period_s):
            try:
                self.refresh_live()
            except Exception:
                log.exception("thermal: idle refresh failed")

    # ============================================================================================== measurement
    def start(self) -> None:
        """START: check, then run the measurement in its own thread (returns at once). Raises :class:`ThermalError`
        when the tool is not idle, the probe is not connected, the thermal origin is undefined, there is no point, or
        a point is outside the XY stage's allowed radius."""
        with self._lock:
            if self._state != ThermalState.IDLE:
                raise ThermalBusy("start: the tool is not idle") if self.measuring else \
                    ThermalError("start: the thermal tool is not open")
            if not self._connected:
                raise ThermalError(f"start: the {self._tool.name.lower()} is not connected")
            settings = self._settings
            points = list(self._points)
            tool = self._tool
        if not settings.origin_defined:
            raise ThermalError("start: the thermal origin is not set (SET DEVICE ORIGIN)")
        if not points:
            raise ThermalError("start: no measurement point")
        frame = ChuckFrame(settings.origin, settings.coordinate_system)
        targets = [frame.to_stage(p.x, p.y) for p in points]
        wafer = getattr(self.xy, "wafer", None)
        if wafer is not None and hasattr(wafer, "move_allowed"):
            refused = [p.order + 1 for p, t in zip(points, targets, strict=True) if not wafer.move_allowed(*t)]
            if refused:
                raise ThermalError(f"start: {len(refused)} point(s) outside the allowed XY radius "
                                   f"(points {refused[:10]}{'…' if len(refused) > 10 else ''})", code=9404)
        with self._lock:
            self._table = ResultTable(points)
            self._summary = None
            self._stop.clear()
            self._stop_reason = "stopped"
            self._state = ThermalState.MEASURING
        self._emit("state", state=str(ThermalState.MEASURING))
        self._measure_thread = threading.Thread(target=self._run, args=(points, targets, settings, tool),
                                                name="thermal-measure", daemon=True)
        self._measure_thread.start()

    def stop(self) -> None:
        """STOP: end the measurement after the current state (a running move goes on)."""
        self._request_stop("stopped", replace_reason=False)

    def _request_stop(self, reason: str, *, replace_reason: bool) -> None:
        """Set the stop flag and STOPPING atomically with the measurement's end (which sets IDLE under the lock)."""
        with self._lock:
            if self._state not in (ThermalState.MEASURING, ThermalState.STOPPING):
                return
            if self._state == ThermalState.MEASURING or replace_reason:
                self._stop_reason = reason
            self._stop.set()
            changed = self._state != ThermalState.STOPPING
            self._state = ThermalState.STOPPING
        if changed:
            self._emit("state", state=str(ThermalState.STOPPING))

    def emergency_stop(self) -> None:
        """EMERGENCY STOP XYZ (``SM d137``–``d139``): stop the measurement and send ``STP`` to the XY and Z stages at
        once, from the caller's thread (the stage devices bypass their queues for it)."""
        self._request_stop("emergency stop", replace_reason=True)
        for name, device in (("XY", self.xy), ("Z", self.z)):
            if device is None:
                continue
            try:
                device.stop()
            except DeviceError as exc:
                self._error(f"emergency stop {name}", exc)

    def wait(self, timeout: float | None = None) -> bool:
        """*Blocking*: wait for the measurement thread; TRUE when it ended."""
        thread = self._measure_thread
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    # -- the thread
    def _check_stop(self) -> None:
        if self._stop.is_set():
            raise _Stopped

    def _pause(self, seconds: float) -> None:
        """Wait ``seconds`` on the controller's clock, stopping early on STOP."""
        deadline = self._clock() + seconds
        while True:
            self._check_stop()
            remaining = deadline - self._clock()
            if remaining <= 0:
                return
            self._sleep(min(remaining, max(self.options.poll_s, 1e-3)))

    def _step(self, step: Step, point: MeasurePoint, total: int) -> None:
        self._emit("progress", progress=point.order * 100.0 / total, point=point.order + 1, points=total,
                   step=str(step), step_progress=STEP_PROGRESS[step])

    def _run(self, points: list[MeasurePoint], targets: list[tuple[float, float]], settings: ThermalSettings,
             tool: Tool) -> None:
        reason, error, completed = "completed", None, False
        try:
            probe = self._probe(tool)
            for point, target in zip(points, targets, strict=True):
                self._check_stop()
                result = self._measure_point(point, target, settings, tool, probe, len(points))
                with self._lock:
                    self._table.set_result(result)
                self._step(Step.WRITING, point, len(points))
                self._emit("point", order=point.order, circle=point.circle, x=point.x, y=point.y, rho=point.rho,
                           theta=point.theta, temperature=result.temperature, std=result.std, failed=result.failed)
                self._step(Step.END, point, len(points))
            completed = True
        except _Stopped:
            reason = self._stop_reason
        except (DeviceError, ThermalError) as exc:
            reason, error = "error", getattr(exc, "message", str(exc))
            self._error("measurement", exc)
        except Exception as exc:
            reason, error = "error", str(exc)
            log.exception("thermal: measurement failed")
            self._emit("error", operation="measurement", message=str(exc), code=None)
        finally:
            with self._lock:
                measured = self._table.measured
                summary = MeasurementSummary(completed, reason, measured, len(points), error,
                                             default_csv_name(self._now()))
                self._summary = summary
                self._state = ThermalState.IDLE
            self._emit("progress", progress=100.0, point=measured, points=len(points), step=str(Step.END),
                       step_progress=0)
            self._emit("state", state=str(ThermalState.IDLE))
            self._emit("finished", completed=completed, reason=reason, measured=measured, points=len(points),
                       error=error, csv_name=summary.csv_name)
            log.info("thermal: measurement %s (%d/%d points)%s", reason, measured, len(points),
                     f": {error}" if error else "")

    def _measure_point(self, point: MeasurePoint, target: tuple[float, float], settings: ThermalSettings, tool: Tool,
                       probe: Any, total: int) -> PointResult:
        o = self.options
        self._step(Step.START, point, total)
        frame = ChuckFrame(settings.origin, settings.coordinate_system)
        attempts = 0
        while True:                                              # MOVING ↔ WAIT MOVE
            self._check_stop()
            self._step(Step.MOVING, point, total)
            attempts += 1
            self.xy.move_absolute(target[0], target[1], wait=False, timeout=self._timeout)
            issued = self._clock()
            self._step(Step.WAIT_MOVE, point, total)
            reached = False
            while True:
                self._check_stop()
                x, y = self.xy.position(timeout=self._timeout)
                cx, cy = frame.to_chuck(x, y)
                self._emit("position", stage_x=x, stage_y=y, chuck_x=cx, chuck_y=cy, z=None)
                reached = abs(x - target[0]) <= o.reached_tolerance_mm and abs(y - target[1]) <= o.reached_tolerance_mm
                if reached or self._clock() - issued >= o.move_reissue_s:
                    break
                self._sleep(o.poll_s)
            if reached:
                break
            if attempts >= o.move_attempts:
                raise ThermalError(f"point {point.order + 1}: stage position ({target[0]:.3f}, {target[1]:.3f}) mm "
                                   f"not reached after {attempts} moves of {o.move_reissue_s:g} s (at ({x:.3f}, "
                                   f"{y:.3f}))")
            log.warning("thermal: point %d not reached after %g s: move issued again", point.order + 1,
                        o.move_reissue_s)
        self._step(Step.WAIT_TEMP, point, total)
        self._pause(settings.wait_ms / 1000.0)
        self._step(Step.MEASURING, point, total)
        samples: list[float] = []
        for _ in range(settings.samples):
            self._check_stop()
            started = self._clock()
            try:
                value = float(probe.temperature(timeout=self._timeout))
            except DeviceError as exc:
                log.warning("thermal: point %d: sample failed: %s", point.order + 1, exc)
                value = math.nan
            samples.append(value)
            if math.isfinite(value):
                self.last_temperature = value
                self._emit("temperature", tool=tool.name, value=value)
            remaining = o.sample_period_s - (self._clock() - started)
            if remaining > 0:
                self._sleep(remaining)
        mean, std, failed = sample_statistics(samples)
        return PointResult(point, mean, std, tuple(samples), failed)

    # ============================================================================================== results
    def default_csv_name(self) -> str:
        """``Thermal_measurement_%y%m%d_%H%M%S.csv`` now (the file dialog's proposal)."""
        return default_csv_name(self._now())

    def export_csv(self, path: str | Path, *, launch_plot: bool = True) -> Path:
        """*Blocking* (a small file): write the table (tab-separated, as LabVIEW) and, when ``launch_plot`` and a
        script command is set, start the external 3D-map script on it. A script that cannot start is reported as an
        ``error`` event, the file stays written."""
        target = self.table.write_csv(path)
        self._emit("exported", path=str(target))
        if launch_plot and self.settings.script_command.strip():
            try:
                self.launch_plot(target)
            except OSError as exc:
                self._error("3D map script", exc)
        return target

    def launch_plot(self, csv_path: str | Path) -> Any:
        """Start ``ScriptPythonPath "<csv path>"`` without waiting (None when no script is set)."""
        return plot_module.launch_plot_script(self.settings.script_command, csv_path)

    def plot_png(self, path: str | Path) -> Path:
        """*Blocking*: save the 3D scatter of the measured points (needs matplotlib, :class:`ImportError`)."""
        return plot_module.plot_map(self.table.results, path)
