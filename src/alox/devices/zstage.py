"""The Z stage as a device of the integrated application.

Hardware: a PI C-663 controller over RS-232 (GCS), specified in ``docs/08-z-stage.md``. The GCS work is done by the
camera server's tested code, reused as it is: :class:`aloxcam.zstage.controller.ZController` (one locked link, set-up,
reference check, waits) on a :class:`aloxcam.zstage.backend.StageBackend` (``PiSerialStage`` for the real controller,
``SimStage`` for simulation, ``PiSerialStage`` + ``FakeC663Server`` in tests).

What changes compared with the camera server (``docs/vision-core/phase3-skeleton.md`` §4):

- no TCP and no LabVIEW reply clusters: methods return values or raise :class:`alox.devices.base.DeviceError`;
- a move checks ``ERR?`` right after ``MOV``/``MVR`` and after the wait, so a refused move is reported at once instead
  of surfacing at a later ``ERR?`` (plan §6: hardware errors reported immediately);
- an implicit re-initialisation (axis found unreferenced before a move: close, set-up, reference move, as LabVIEW does)
  is kept and published as the event ``device.<name>.rehomed`` (owner's decision, map §3.1);
- ``stop()`` bypasses the queue so it is not stuck behind a running wait.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from alox.devices.base import Device, DeviceError
from alox.events import Publish
from aloxcam.errors import Code, StageError
from aloxcam.settings import SimulationOptions, StageOptions, ZStageSettings
from aloxcam.zstage.backend import StageBackend
from aloxcam.zstage.controller import ZController

__all__ = ["ZStage", "ZStatus", "build_stage_backend"]

log = logging.getLogger(__name__)

#: GCS / driver codes that mean the serial link is gone (``docs/08`` §3.5): the device enters ``fault``.
LINK_FAILURE_CODES = frozenset({int(Code.PI_NOT_CONNECTED), int(Code.PI_OPEN_PORT), int(Code.PI_TIMEOUT)})
#: Extra time given to a call that waits for the hardware, on top of the hardware timeout itself.
CALL_MARGIN_S = 30.0


@dataclass(frozen=True)
class ZStatus:
    """A snapshot for the UI and the API."""

    state: str
    position: float | None
    referenced: bool | None
    moving: bool | None
    identity: str
    serial_number: str


def build_stage_backend(*, simulate: bool, simulation: SimulationOptions | None = None) -> StageBackend:
    """The backend for the real controller (``PiSerialStage``, needs pyserial) or the simulator (``SimStage``)."""
    if simulate:
        from aloxcam.zstage.sim import SimStage
        return SimStage(simulation or SimulationOptions())
    from aloxcam.zstage.pi_serial import PiSerialStage
    return PiSerialStage()


class ZStage(Device):
    """The Z stage. ``settings`` come from ``alox.ini`` (``docs/09`` §5.2), ``options`` from the ``[stage]`` options of
    the camera server (``docs/phase2-design.md``). ``sleep``/``monotonic`` let tests run the waits on a fake clock."""

    def __init__(self, backend: StageBackend, settings: ZStageSettings, options: StageOptions | None = None, *,
                 name: str = "z", publish: Publish | None = None, implicit_reinit: bool = True,
                 sleep: Callable[[float], None] | None = None, monotonic: Callable[[], float] | None = None,
                 **device_options: Any) -> None:
        self.options = options or StageOptions()
        kwargs: dict[str, Any] = {}
        if sleep is not None:
            kwargs["sleep"] = sleep
        if monotonic is not None:
            kwargs["monotonic"] = monotonic
        self.controller = ZController(backend, self.options, **kwargs)
        self._settings = settings
        self.implicit_reinit = implicit_reinit
        device_options.setdefault("default_timeout_s", CALL_MARGIN_S)
        if sleep is not None:
            device_options.setdefault("sleep", sleep)
        super().__init__(name, publish=publish, **device_options)

    # ============================================================================================== settings
    @property
    def settings(self) -> ZStageSettings:
        return self._settings

    @property
    def limits(self) -> tuple[float, float]:
        """The software range ``(low, high)`` of the moves (inclusive; ``docs/08`` §7)."""
        lo, hi = sorted((self._settings.min_pos, self._settings.max_pos))
        return lo, hi

    def set_limits(self, *, min_mm: float | None = None, max_mm: float | None = None) -> None:
        """Change the software range in memory (as ``Z Set Min/Max Limit``; the INI is not written)."""
        changes: dict[str, float] = {}
        for field_name, value in (("min_pos", min_mm), ("max_pos", max_mm)):
            if value is None:
                continue
            if not math.isfinite(value):
                raise DeviceError(self.name, f"set limits: {field_name} must be finite", code=int(Code.LV_INVALID_INPUT))
            changes[field_name] = float(value)
        self._settings = replace(self._settings, **changes)

    # ============================================================================================== device hooks
    def _open(self) -> None:
        self.controller.setup(self._settings)
        self.controller.startup_macro_check()
        session = self.controller.session
        self.emit("opened", identity=session.identity, serial_number=session.serial_number, axes=list(session.axes))

    def _close(self) -> None:
        self.controller.close()
        self.controller.reset_session()

    def _is_link_failure(self, exc: BaseException) -> bool:
        return isinstance(exc, StageError) and exc.code in LINK_FAILURE_CODES

    def _translate(self, exc: BaseException, what: str) -> DeviceError:
        if isinstance(exc, DeviceError):
            return exc
        if isinstance(exc, StageError):
            text = exc.source
            if exc.code > 0 and "GCS Error" not in text:
                description = self.controller.gcs_error_text(exc.code)       # table lookup, no I/O
                if description:
                    text = f"{text}: {description}"
            return DeviceError(self.name, f"{what}: {text}", code=exc.code)
        return super()._translate(exc, what)

    # ============================================================================================== queries
    @property
    def identity(self) -> str:
        """``*IDN?`` answer read at set-up (no I/O)."""
        return self.controller.session.identity

    @property
    def serial_number(self) -> str:
        """Serial number from ``*IDN?`` (no I/O)."""
        return self.controller.session.serial_number

    def position(self, *, timeout: float | None = None) -> float:
        """``POS?`` in mm."""
        return self.call("position", self.controller.qpos, timeout=timeout)

    def is_referenced(self, *, timeout: float | None = None) -> bool:
        """``FRF?``."""
        return self.call("is referenced", self.controller.qfrf, timeout=timeout)

    def is_moving(self, *, timeout: float | None = None) -> bool:
        """``#5``: TRUE while the axis moves."""
        return self.call("is moving", self.controller.is_moving, timeout=timeout)

    def speed(self, *, timeout: float | None = None) -> float:
        """``VEL?`` in mm/s."""
        return self.call("speed", self.controller.qvel, timeout=timeout)

    def joystick_enabled(self, joystick_id: int = 1, *, timeout: float | None = None) -> bool:
        """``JON? <id>`` ≠ 0."""
        return self.call("joystick enabled", lambda: self.controller.qjon(joystick_id), timeout=timeout)

    def read_error(self, *, timeout: float | None = None) -> tuple[int, str]:
        """``ERR?`` (reads and clears the controller error) and its GCS text: ``(0, "")`` when none."""
        def read() -> tuple[int, str]:
            try:
                self.controller.check_error()
            except StageError as exc:
                return exc.code, self.controller.gcs_error_text(exc.code)
            return 0, ""
        return self.call("read error", read, timeout=timeout)

    def status(self, *, timeout: float | None = None) -> ZStatus:
        """State, position, reference and motion in one call; the values are None when the device is not ready or a
        query fails."""
        def read() -> ZStatus:
            values: list[Any] = []
            for query in (self.controller.qpos, self.controller.qfrf, self.controller.is_moving):
                try:
                    values.append(query())
                except StageError:
                    values.append(None)
            return ZStatus(str(self.state), values[0], values[1], values[2], self.identity, self.serial_number)
        try:
            return self.call("status", read, timeout=timeout)
        except DeviceError:
            return ZStatus(str(self.state), None, None, None, self.identity, self.serial_number)

    # ============================================================================================== actions
    def set_speed(self, mm_per_s: float, *, timeout: float | None = None) -> None:
        """``VEL`` + ``ERR?`` (the INI speed is not changed)."""
        self.call("set speed", lambda: self.controller.vel(mm_per_s), timeout=timeout)

    def move_absolute(self, target_mm: float, *, wait: bool = True, timeout: float | None = None) -> float:
        """Reference check, range check, ``MOV`` + ``ERR?``, then (``wait``) the end of the move. Returns the position
        reached (``POS?``) when waiting, else the target."""
        def run() -> float:
            self._reference_check("move absolute")
            self._check_range(target_mm, "move absolute")
            self.controller.mov(target_mm)
            self.controller.check_error()
            return self._finish_move(target_mm, wait)
        return self.call("move absolute", run, timeout=self._move_timeout(wait, timeout))

    def move_relative(self, delta_mm: float, *, wait: bool = True, timeout: float | None = None) -> float:
        """Reference check, ``POS?``, range check of ``POS? + delta``, ``MVR`` + ``ERR?``, then (``wait``) the end of
        the move. Returns the position reached when waiting, else the computed target."""
        def run() -> float:
            self._reference_check("move relative")
            target = self.controller.qpos() + delta_mm
            self._check_range(target, "move relative")
            self.controller.mvr(delta_mm)
            self.controller.check_error()
            return self._finish_move(target, wait)
        return self.call("move relative", run, timeout=self._move_timeout(wait, timeout))

    def wait_end_move(self, *, add_wait_ms: int = 0, polling_ms: int = 10, add_wait_only: bool = False,
                      timeout: float | None = None) -> None:
        """``Wait for axes to stop`` then the additional wait (``docs/08`` §8)."""
        def run() -> None:
            self.controller.general_wait(add_wait_ms=max(0, add_wait_ms), polling_ms=max(0, polling_ms),
                                         add_wait_only=add_wait_only, timeout_s=self.options.wait_timeout_s)
            self.controller.check_error()
        self.call("wait end move", run, timeout=self._move_timeout(True, timeout))

    def reference(self, method: str = "FNL", *, timeout: float | None = None) -> None:
        """Reference move: ``FRF`` (reference switch) or ``FNL`` (negative limit, the default), then the waits and
        ``ERR?`` as ``Z Do Reference`` (``docs/08`` §5.3, Z13)."""
        def run() -> None:
            (self.controller.frf if method.upper() == "FRF" else self.controller.fnl)()
            if self.options.do_reference_waits_ready:
                self.controller.wait_ready(self.options.ready_timeout_s)
            self.controller.general_wait(polling_ms=10, timeout_s=self.options.wait_timeout_s)
            self.controller.check_error()
            self.emit("referenced", method=method.upper())
        limit = timeout if timeout is not None else self.options.ready_timeout_s + self.options.wait_timeout_s
        self.call("reference", run, timeout=limit + CALL_MARGIN_S if timeout is None else limit)

    def set_joystick(self, enabled: bool, *, joystick_id: int = 1, joystick_axis: int = 1,
                     timeout: float | None = None) -> None:
        """``JAX <id> <axis> 1`` then ``JON <id> 1|0`` + ``ERR?`` (``docs/08`` §9)."""
        def run() -> None:
            self.controller.jax(joystick_id, joystick_axis)
            self.controller.jon(joystick_id, enabled)
        self.call("set joystick", run, timeout=timeout)

    def stop(self) -> None:
        """``STP``, sent at once from the caller's thread (not queued), so it is not stuck behind a running wait; the
        wait then sees the axis stopped and returns. The controller latches error 10 (stopped), as on LabVIEW."""
        self.run_direct("stop", self.controller.stp)
        self.emit("stopped")

    def reinitialize(self, *, timeout: float | None = None) -> None:
        """Close and set up again (reference move included when the options ask for it)."""
        def run() -> None:
            self._close()
            self._open()
        self.call("reinitialize", run, timeout=timeout or self.options.ready_timeout_s + CALL_MARGIN_S)

    # ============================================================================================== helpers
    def _reference_check(self, label: str) -> None:
        """Before a move (``docs/08`` §6). ``implicit_reinit``: an unreferenced axis is re-initialised (reference move)
        and the event ``rehomed`` is published; otherwise an unreferenced axis refuses the move (GCS code 5)."""
        if not self.implicit_reinit:
            if not self.controller.qfrf():
                raise DeviceError(self.name, f"{label}: Z axis not referenced", code=int(Code.GCS_UNREFERENCED))
            return
        inited, warning, failure = self.controller.check_axis_reference(self._settings)
        if warning:
            log.warning("%s: axis was not referenced before %s: re-initialised (reference move)", self.name, label)
            self.emit("rehomed", operation=label, success=inited)
        if failure is not None:
            raise failure

    def _check_range(self, target: float, label: str) -> None:
        lo, hi = self.limits
        if not lo <= target <= hi:                         # NaN is out of range too
            raise DeviceError(self.name, f"{label}: target {target:g} mm outside [{lo:g}, {hi:g}] mm",
                              code=int(Code.Z_OUT_OF_RANGE))

    def _finish_move(self, target: float, wait: bool) -> float:
        if not wait:
            return target
        self.controller.general_wait(polling_ms=10, timeout_s=self.options.wait_timeout_s)
        self.controller.check_error()
        position = self.controller.qpos()
        self.emit("position", position=position)
        return position

    def _move_timeout(self, wait: bool, timeout: float | None) -> float:
        if timeout is not None:
            return timeout
        hardware = self.options.ready_timeout_s if self.implicit_reinit else 0.0   # a re-initialisation may reference
        return hardware + (self.options.wait_timeout_s if wait else 0.0) + CALL_MARGIN_S
