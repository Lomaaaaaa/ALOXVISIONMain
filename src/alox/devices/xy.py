"""The XY stages as one device of the integrated application.

Hardware: two PI C-663 controllers (GCS 2.0) on one RS-232 port in a daisy chain, X = device 1 (master), Y = device 2
(``docs/vision-core/xy-stage.md``, map Part 2 §2.8). Each controller is driven by the camera server's tested
:class:`aloxcam.zstage.controller.ZController` on its own backend: ``PiSerialStage`` on a view of the shared port
(:mod:`alox.devices.gcs_chain`) for the hardware, ``SimStage`` for simulation.

What the device keeps from LabVIEW's AloxXYServer: the per-controller set-up, the reference check before every move
that re-initialises (and references) both axes when either lost its reference, the check that a move's destination is
inside the wafer, and the periodic wafer-limit supervision that stops the stages and the joystick when they leave the
wafer. What changes (plan §6): the supervision goes through the device's worker like every other operation, so it can
no longer interleave frames with a command on the shared port; an automatic re-homing is published as the event
``device.xy.rehomed``; a move refused because it leaves the wafer, or refused by a controller, is reported by the move
itself.
"""

from __future__ import annotations

import logging
import math
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from alox.devices.base import Device, DeviceError, DeviceState
from alox.events import Publish
from aloxcam.errors import Code, StageError
from aloxcam.settings import SimulationOptions, StageOptions, ZStageSettings
from aloxcam.zstage.backend import StageBackend
from aloxcam.zstage.controller import ZController

__all__ = ["AXES", "WaferSettings", "XYAxisSettings", "XYSettings", "XYStage", "XYStatus", "build_xy_backends",
           "load_xy_settings"]

log = logging.getLogger(__name__)

AXES = ("x", "y")
#: LabVIEW's code for a destination outside the wafer (``alox_xy_server``: "X/Y Move outside wafer").
OUTSIDE_WAFER = 9404
#: GCS error 10 "Controller was stopped by command": also used when a waited move ends before its destination.
STOPPED = 10
LINK_FAILURE_CODES = frozenset({int(Code.PI_NOT_CONNECTED), int(Code.PI_OPEN_PORT), int(Code.PI_TIMEOUT)})
CALL_MARGIN_S = 30.0


@dataclass(frozen=True)
class XYAxisSettings:
    """One axis (``alox_typedef_ext_xystage_properties``): ``device`` is the daisy-chain device number."""

    device: int
    com: int = 2
    baudrate: int = 115200
    speed: float = 10.0
    slow_speed: float = 1.0

    def stage_settings(self) -> ZStageSettings:
        """The same values as the ``ZStageSettings`` the shared controller code expects (no software range)."""
        return ZStageSettings(com=self.com, baudrate=self.baudrate, axis=self.device, speed=self.speed,
                              slow_speed=self.slow_speed, min_pos=-math.inf, max_pos=math.inf)


@dataclass(frozen=True)
class WaferSettings:
    """The wafer used by the two checks (``docs/vision-core/xy-stage.md`` §5, §6). A diameter or an origin of 0 means
    "undefined": every position is accepted, as in LabVIEW.

    - Before a move, the destination must be within ``move_radius_mm`` of the wafer origin. LabVIEW hard-wires 80 mm
      (``alox_xy_check_wafer position d0``; the diameter only switches the check on); the radius in the deployed
      executable is an open question (xy-stage.md Q4), hence the setting.
    - The periodic supervision stops the stages beyond ``diameter_mm / 2``.
    """

    diameter_mm: float = 100.0
    origin: tuple[float, float] = (0.0, 0.0)
    chuck_origin: tuple[float, float] = (0.0, 0.0)
    limits_enabled: bool = False
    check_period_ms: int = 500
    move_radius_mm: float = 80.0

    @property
    def defined(self) -> bool:
        return self.diameter_mm != 0.0 and self.origin[0] != 0.0 and self.origin[1] != 0.0

    def _distance(self, x: float, y: float) -> float:
        ox, oy = self.origin
        return math.hypot(ox - x, oy - y)

    def inside(self, x: float, y: float) -> bool:
        """Supervision zone: distance to the origin ≤ diameter / 2 (TRUE when the wafer is undefined)."""
        return not self.defined or self._distance(x, y) <= self.diameter_mm * 0.5

    def move_allowed(self, x: float, y: float) -> bool:
        """Move check: distance to the origin ≤ ``move_radius_mm`` (TRUE when the wafer is undefined)."""
        return not self.defined or self._distance(x, y) <= self.move_radius_mm


@dataclass(frozen=True)
class XYSettings:
    x: XYAxisSettings = field(default_factory=lambda: XYAxisSettings(device=1))
    y: XYAxisSettings = field(default_factory=lambda: XYAxisSettings(device=2))
    wafer: WaferSettings = field(default_factory=WaferSettings)


def load_xy_settings(ini: Any, *, warn: Callable[[str], None] = log.warning) -> XYSettings:
    """``XYSettings`` from a :class:`alox.config.TypedIni` of ``alox.ini`` (map Part 2 §2.8): ``[hardware.xy]``
    ``comport``, ``baudrate`` (index 0-3), ``xaxis``/``yaxis`` (device numbers), ``speed``, ``slowspeed``,
    ``waferxorg``/``waferyorg``, ``chuckxorg``/``chuckyorg``, ``waferlimit``, ``wafercheckfreq``; ``[general]
    wafersize``. Defaults as in LabVIEW."""
    from aloxcam.config.alox_ini import BAUD_BY_INDEX

    def get(name: str, default: Any) -> Any:
        try:
            return ini.get(name, default)
        except ValueError as exc:
            warn(f"alox.ini: {exc}; default {default!r} used")
            return default

    index = get("hardware.xy.baudrate", 3)
    baudrate = BAUD_BY_INDEX.get(index)
    if baudrate is None:
        warn(f"alox.ini: [hardware.xy] baudrate = {index} is not an index 0-3; 9600 used")
        baudrate = 9600
    common = {"com": get("hardware.xy.comport", 2), "baudrate": baudrate, "speed": get("hardware.xy.speed", 10.0),
              "slow_speed": get("hardware.xy.slowspeed", 1.0)}
    wafer = WaferSettings(
        diameter_mm=get("general.wafersize", 100.0),
        origin=(get("hardware.xy.waferxorg", 0.0), get("hardware.xy.waferyorg", 0.0)),
        chuck_origin=(get("hardware.xy.chuckxorg", 0.0), get("hardware.xy.chuckyorg", 0.0)),
        limits_enabled=get("hardware.xy.waferlimit", False),
        check_period_ms=max(1, get("hardware.xy.wafercheckfreq", 500)))
    return XYSettings(x=XYAxisSettings(device=get("hardware.xy.xaxis", 1), **common),
                      y=XYAxisSettings(device=get("hardware.xy.yaxis", 2), **common), wafer=wafer)


@dataclass(frozen=True)
class XYStatus:
    state: str
    x: float | None
    y: float | None
    referenced: bool | None
    moving: bool | None
    inside_wafer: bool | None
    identities: tuple[str, str]


def build_xy_backends(*, simulate: bool, chain: Any = None,
                      simulation: SimulationOptions | None = None) -> tuple[StageBackend, StageBackend]:
    """``(x, y)`` backends: two ``SimStage`` in simulation; otherwise two ``PiSerialStage`` on views of ``chain``
    (an :class:`alox.devices.gcs_chain.GcsChain`; device numbers 1 and 2)."""
    if simulate:
        from aloxcam.zstage.sim import SimStage
        options = simulation or SimulationOptions(stage_min_mm=0.0, stage_max_mm=200.0)
        return SimStage(options, dh_running=False), SimStage(options, dh_running=False)
    if chain is None:
        raise ValueError("a GcsChain is needed for the real XY stages")
    from aloxcam.zstage.pi_serial import PiSerialStage
    return PiSerialStage(link_factory=chain.link_factory(1)), PiSerialStage(link_factory=chain.link_factory(2))


class XYStage(Device):
    """Both XY axes. Positions are stage coordinates in mm (conversions to wafer or chuck coordinates are done by the
    caller; ``docs/vision-core/xy-stage.md`` §7)."""

    def __init__(self, backend_x: StageBackend, backend_y: StageBackend, settings: XYSettings | None = None,
                 options: StageOptions | None = None, *, name: str = "xy", publish: Publish | None = None,
                 sleep: Callable[[float], None] | None = None, monotonic: Callable[[], float] | None = None,
                 supervise: bool = True, on_target_tolerance_mm: float = 0.01,
                 disable_joystick_on_limit: bool = False, **device_options: Any) -> None:
        self.options = options or StageOptions(always_reference=False)
        kwargs: dict[str, Any] = {}
        if sleep is not None:
            kwargs["sleep"] = sleep
        if monotonic is not None:
            kwargs["monotonic"] = monotonic
        self.controllers = {"x": ZController(backend_x, self.options, **kwargs),
                            "y": ZController(backend_y, self.options, **kwargs)}
        self._settings = settings or XYSettings()
        self.supervise = supervise
        self.on_target_tolerance_mm = on_target_tolerance_mm
        self.disable_joystick_on_limit = disable_joystick_on_limit
        self._commanded_move = False
        self._outside_reported = False
        self._supervisor: threading.Thread | None = None
        self._supervisor_stop = threading.Event()
        self._check_pending = threading.Event()
        device_options.setdefault("default_timeout_s", CALL_MARGIN_S)
        if sleep is not None:
            device_options.setdefault("sleep", sleep)
        super().__init__(name, publish=publish, **device_options)

    # ============================================================================================== settings
    @property
    def settings(self) -> XYSettings:
        return self._settings

    @property
    def wafer(self) -> WaferSettings:
        return self._settings.wafer

    def set_wafer(self, **changes: Any) -> None:
        """Change wafer values in memory (``diameter_mm``, ``origin``, ``chuck_origin``, ``limits_enabled``,
        ``check_period_ms``, ``move_radius_mm``), as the Set Wafer … commands; the INI is not written."""
        for key in ("diameter_mm", "move_radius_mm"):
            if key in changes and not math.isfinite(changes[key]):
                raise DeviceError(self.name, f"set wafer: {key} must be finite", code=int(Code.LV_INVALID_INPUT))
        if "check_period_ms" in changes and int(changes["check_period_ms"]) <= 0:
            raise DeviceError(self.name, "set wafer: check_period_ms must be > 0", code=int(Code.LV_INVALID_INPUT))
        self._settings = replace(self._settings, wafer=replace(self._settings.wafer, **changes))

    def inside_wafer(self, x: float, y: float) -> bool:
        return self._settings.wafer.inside(x, y)

    def _axis_settings(self, axis: str) -> XYAxisSettings:
        return self._settings.x if axis == "x" else self._settings.y

    # ============================================================================================== device hooks
    def _open(self) -> None:
        for axis in AXES:
            self.controllers[axis].setup(self._axis_settings(axis).stage_settings())
        self.emit("opened", identities=list(self.identities))
        self._start_supervisor()

    def _close(self) -> None:
        self._stop_supervisor()
        for axis in AXES:
            controller = self.controllers[axis]
            try:
                controller.close()
            finally:
                controller.reset_session()

    def _is_link_failure(self, exc: BaseException) -> bool:
        return isinstance(exc, StageError) and exc.code in LINK_FAILURE_CODES

    def _translate(self, exc: BaseException, what: str) -> DeviceError:
        if isinstance(exc, DeviceError):
            return exc
        if isinstance(exc, StageError):
            text = exc.source
            if exc.code > 0 and "GCS Error" not in text:
                description = self.controllers["x"].gcs_error_text(exc.code)
                if description:
                    text = f"{text}: {description}"
            return DeviceError(self.name, f"{what}: {text}", code=exc.code)
        return super()._translate(exc, what)

    # ============================================================================================== queries
    @property
    def identities(self) -> tuple[str, str]:
        return self.controllers["x"].session.identity, self.controllers["y"].session.identity

    def position(self, *, timeout: float | None = None) -> tuple[float, float]:
        """``POS?`` on X and Y (mm)."""
        return self.call("position", self._read_position, timeout=timeout)

    def is_referenced(self, *, timeout: float | None = None) -> bool:
        """``FRF?`` on both axes: TRUE when both are referenced."""
        return self.call("is referenced", self._both_referenced, timeout=timeout)

    def is_moving(self, *, timeout: float | None = None) -> bool:
        return self.call("is moving", lambda: any(c.is_moving() for c in self.controllers.values()),
                         timeout=timeout)

    def speed(self, axis: str = "x", *, timeout: float | None = None) -> float:
        return self.call("speed", self._controller(axis).qvel, timeout=timeout)

    def joystick_enabled(self, joystick_id: int = 1, *, timeout: float | None = None) -> bool:
        """``JON?`` on X (the joystick drives both axes)."""
        return self.call("joystick enabled", lambda: self.controllers["x"].qjon(joystick_id), timeout=timeout)

    def joystick_button(self, *, timeout: float | None = None) -> bool:
        """``JBS? 1 1`` on X: the joystick button. FALSE on a backend without raw queries (simulation)."""
        def read() -> bool:
            backend = self.controllers["x"]._backend
            query = getattr(backend, "raw_query", None)
            if query is None:
                return False
            with self.controllers["x"].lock:
                answer = query("JBS? 1 1")
            value = answer.rpartition("=")[2].strip()
            return value not in ("", "0")
        return self.call("joystick button", read, timeout=timeout)

    def read_error(self, axis: str = "x", *, timeout: float | None = None) -> tuple[int, str]:
        """``ERR?`` on one axis (reads and clears) and its GCS text."""
        controller = self._controller(axis)

        def read() -> tuple[int, str]:
            try:
                controller.check_error()
            except StageError as exc:
                return exc.code, controller.gcs_error_text(exc.code)
            return 0, ""
        return self.call("read error", read, timeout=timeout)

    def status(self, *, timeout: float | None = None) -> XYStatus:
        def read() -> XYStatus:
            try:
                x, y = self._read_position()
            except StageError:
                x = y = None
            try:
                referenced: bool | None = self._both_referenced()
            except StageError:
                referenced = None
            try:
                moving: bool | None = any(c.is_moving() for c in self.controllers.values())
            except StageError:
                moving = None
            inside = None if x is None or y is None else self.inside_wafer(x, y)
            return XYStatus(str(self.state), x, y, referenced, moving, inside, self.identities)
        try:
            return self.call("status", read, timeout=timeout)
        except DeviceError:
            return XYStatus(str(self.state), None, None, None, None, None, self.identities)

    # ============================================================================================== actions
    def set_speed(self, mm_per_s: float, axis: str | None = None, *, timeout: float | None = None) -> None:
        """``VEL`` + ``ERR?`` on one axis or both (``axis`` None)."""
        def run() -> None:
            for name in self._axes(axis):
                self.controllers[name].vel(mm_per_s)
        self.call("set speed", run, timeout=timeout)

    def move_absolute(self, x: float | None = None, y: float | None = None, *, wait: bool = True,
                      timeout: float | None = None) -> tuple[float, float]:
        """Move X and/or Y to absolute stage positions. Reference check (both axes), inside-wafer check of the
        destination (9404), ``MOV`` + ``ERR?`` per axis (X first), then (``wait``) the end of the moves. Returns the
        position reached when waiting, else the destination."""
        def run() -> tuple[float, float]:
            self._reference_check("move absolute")
            current = self._read_position()
            dest = (current[0] if x is None else x, current[1] if y is None else y)
            self._check_destination(dest, "move absolute")
            self._commanded_move = True
            for name, value in (("x", x), ("y", y)):
                if value is not None:
                    self.controllers[name].mov(value)
                    self.controllers[name].check_error()
            return self._finish(dest, wait)
        return self.call("move absolute", run, timeout=self._move_timeout(wait, timeout))

    def move_relative(self, dx: float = 0.0, dy: float = 0.0, *, wait: bool = True,
                      timeout: float | None = None) -> tuple[float, float]:
        """Move by ``(dx, dy)`` mm: reference check, inside-wafer check of the destination, ``MVR`` + ``ERR?`` on each
        axis with a non-zero move, then (``wait``) the end of the moves."""
        def run() -> tuple[float, float]:
            self._reference_check("move relative")
            current = self._read_position()
            dest = (current[0] + dx, current[1] + dy)
            self._check_destination(dest, "move relative")
            self._commanded_move = True
            for name, delta in (("x", dx), ("y", dy)):
                if delta:
                    self.controllers[name].mvr(delta)
                    self.controllers[name].check_error()
            return self._finish(dest, wait)
        return self.call("move relative", run, timeout=self._move_timeout(wait, timeout))

    def wait_end_move(self, *, add_wait_ms: int = 0, polling_ms: int = 10, timeout: float | None = None) -> None:
        """Wait until both axes stop, then ``add_wait_ms``."""
        def run() -> None:
            for name in AXES:
                self.controllers[name].general_wait(polling_ms=max(0, polling_ms),
                                                    timeout_s=self.options.wait_timeout_s)
            if add_wait_ms > 0:
                self._sleep(add_wait_ms / 1000.0)
            for name in AXES:
                self.controllers[name].check_error()
        self.call("wait end move", run, timeout=self._move_timeout(True, timeout))

    def reference(self, *, timeout: float | None = None) -> None:
        """``FRF`` X, wait, ``ERR?``, then the same on Y (``Do Reference``)."""
        def run() -> None:
            for name in AXES:
                self._reference_axis(name)
            self.emit("referenced")
        limit = timeout if timeout is not None else 2 * (self.options.ready_timeout_s + self.options.wait_timeout_s)
        self.call("reference", run, timeout=limit)

    def set_joystick(self, enabled: bool, *, joystick_id: int = 1, joystick_axis: int = 1,
                     timeout: float | None = None) -> None:
        """``JAX`` + ``JON 1|0`` + ``ERR?`` on both controllers."""
        def run() -> None:
            for name in AXES:
                self.controllers[name].jax(joystick_id, joystick_axis)
                self.controllers[name].jon(joystick_id, enabled)
        self.call("set joystick", run, timeout=timeout)

    def stop(self) -> None:
        """``STP`` on both axes, sent at once from the caller's thread (not queued); the stop error each controller
        latches (10) is read and cleared, as LabVIEW's Stop does."""
        def run() -> None:
            failures: list[StageError] = []
            for name in AXES:
                try:
                    self.controllers[name].stp()
                except StageError as exc:
                    failures.append(exc)
            for name in AXES:
                try:
                    self.controllers[name].check_error()
                except StageError:
                    pass                                    # 10 "stopped" is expected
            if failures:
                raise failures[0]
        self.run_direct("stop", run)
        self.emit("stopped")

    # ============================================================================================== helpers
    def _controller(self, axis: str) -> ZController:
        if axis not in self.controllers:
            raise DeviceError(self.name, f"unknown axis {axis!r} (x or y)")
        return self.controllers[axis]

    def _axes(self, axis: str | None) -> tuple[str, ...]:
        if axis is None:
            return AXES
        self._controller(axis)
        return (axis,)

    def _read_position(self) -> tuple[float, float]:
        return self.controllers["x"].qpos(), self.controllers["y"].qpos()

    def _both_referenced(self) -> bool:
        return all(self.controllers[name].qfrf() for name in AXES)

    def _reference_axis(self, name: str) -> None:
        controller = self.controllers[name]
        controller.frf()
        if self.options.do_reference_waits_ready:
            controller.wait_ready(self.options.ready_timeout_s)
        controller.general_wait(polling_ms=10, timeout_s=self.options.wait_timeout_s)
        controller.check_error()

    def _reference_check(self, label: str) -> None:
        """Before a move: when either axis is not referenced, close both, set both up again and reference them
        (``alox_xy_check_axis_references``), and publish ``rehomed``."""
        try:
            referenced = self._both_referenced()
        except StageError as exc:
            log.info("%s: FRF? failed before %s (%s): re-initialising both axes", self.name, label, exc)
            referenced = False
        if referenced:
            return
        log.warning("%s: an axis lost its reference before %s: re-initialising and referencing both axes",
                    self.name, label)
        success = False
        try:
            for name in AXES:
                controller = self.controllers[name]
                try:
                    controller.close()
                except StageError as exc:
                    log.debug("%s: close before re-initialisation failed (cleared): %s", self.name, exc)
            for name in AXES:
                self.controllers[name].setup(self._axis_settings(name).stage_settings())
            for name in AXES:
                if not self.controllers[name].qfrf():
                    self._reference_axis(name)
            success = True
        finally:
            self.emit("rehomed", operation=label, success=success)

    def _check_destination(self, dest: tuple[float, float], label: str) -> None:
        if not all(math.isfinite(v) for v in dest):
            raise DeviceError(self.name, f"{label}: destination must be finite", code=int(Code.LV_INVALID_INPUT))
        if not self.wafer.move_allowed(*dest):
            raise DeviceError(self.name, f"{label}: destination ({dest[0]:g}, {dest[1]:g}) mm outside the wafer "
                              f"(more than {self.wafer.move_radius_mm:g} mm from its origin)", code=OUTSIDE_WAFER)

    def _finish(self, dest: tuple[float, float], wait: bool) -> tuple[float, float]:
        if not wait:
            return dest
        for name in AXES:
            self.controllers[name].general_wait(polling_ms=10, timeout_s=self.options.wait_timeout_s)
        self._commanded_move = False
        for name in AXES:
            self.controllers[name].check_error()
        position = self._read_position()
        self.emit("position", x=position[0], y=position[1])
        missed = [name for name, reached, wanted in zip(AXES, position, dest, strict=True)
                  if abs(reached - wanted) > self.on_target_tolerance_mm]
        if missed:                                          # stopped (Stop, wafer limit) before the destination
            raise DeviceError(self.name, f"move ended at ({position[0]:g}, {position[1]:g}) mm, not at the destination "
                              f"({dest[0]:g}, {dest[1]:g}) mm: interrupted", code=STOPPED)
        return position

    def _move_timeout(self, wait: bool, timeout: float | None) -> float:
        if timeout is not None:
            return timeout
        rehome = 2 * (self.options.ready_timeout_s + self.options.wait_timeout_s)
        return rehome + (2 * self.options.wait_timeout_s if wait else 0.0) + CALL_MARGIN_S

    # ============================================================================================== wafer limits
    def _start_supervisor(self) -> None:
        if not self.supervise or self._supervisor is not None:
            return
        self._supervisor_stop.clear()
        self._supervisor = threading.Thread(target=self._supervise, name=f"{self.name}-wafer-limits", daemon=True)
        self._supervisor.start()

    def _stop_supervisor(self) -> None:
        thread, self._supervisor = self._supervisor, None
        if thread is None:
            return
        self._supervisor_stop.set()
        if thread is not threading.current_thread():
            thread.join(timeout=2.0)

    def _supervise(self) -> None:
        """Every ``check_period_ms``: when the limits are enabled, the wafer defined and the device idle, queue one
        check. Running it through the worker serialises it with the commands (LabVIEW's loop used the port
        concurrently, without a lock)."""
        while not self._supervisor_stop.wait(max(1, self.wafer.check_period_ms) / 1000.0):
            wafer = self.wafer
            if not (wafer.limits_enabled and wafer.defined) or self.state != DeviceState.READY:
                continue
            if self._check_pending.is_set() or self.busy_with is not None:
                continue
            self._check_pending.set()
            try:
                self.submit("wafer limit check", self.check_wafer_limits).add_done_callback(
                    lambda _f: self._check_pending.clear())
            except DeviceError:
                self._check_pending.clear()

    def check_wafer_limits(self) -> bool:
        """One supervision step (worker thread); returns whether the position is inside the supervision zone.

        Outside the zone, the stages are stopped (``STP`` and ``ERR?`` on both axes) when the joystick is on, or when
        they move without a commanded move in progress. A commanded move is left alone, so the stage can be brought
        back onto the wafer (LabVIEW stopped it too and the stage stayed stuck outside: xy-stage.md X3). The joystick
        stays on unless ``disable_joystick_on_limit`` (LabVIEW's ``JON 0`` is disabled code, xy-stage.md §6). The
        event ``wafer_limit`` is published once per excursion."""
        x, y = self._read_position()
        if self.inside_wafer(x, y):
            self._outside_reported = False
            return True
        moving = any(self.controllers[name].is_moving() for name in AXES)
        if not moving:
            self._commanded_move = False
        joystick = self.controllers["x"].qjon(1)
        if not (joystick or (moving and not self._commanded_move)):
            return False
        log.warning("%s: position (%g, %g) mm outside the wafer: stopping the stages", self.name, x, y)
        for name in AXES:
            controller = self.controllers[name]
            steps: list[Callable[[], object]] = [controller.stp, controller.check_error]
            if self.disable_joystick_on_limit:
                steps.append(lambda c=controller: c.jon(1, False))
            for step in steps:
                try:
                    step()
                except StageError as exc:
                    log.debug("%s: wafer-limit stop step failed on %s (cleared): %s", self.name, name, exc)
        if not self._outside_reported:
            self._outside_reported = True
            self.emit("wafer_limit", x=x, y=y, joystick=joystick)
        return False
