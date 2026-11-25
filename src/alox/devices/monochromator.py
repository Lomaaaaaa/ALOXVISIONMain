"""The monochromator ("WL", wavelength) as a device of the integrated application.

Hardware: a Spectral Products CM110/CM112 on RS-232, 9600 Bd 8N1, binary protocol
(``docs/vision-core/monochromator.md``, "the spec"). The protocol is :mod:`alox.devices.cm11x`
(:class:`~alox.devices.cm11x.Cm11x`, pyserial :class:`~alox.devices.cm11x.SerialLink`); simulation runs the same
protocol code against the emulator of :mod:`alox.devices.cm11x_sim` (:func:`build_monochromator_backend`).

The device replaces LabVIEW's AloxWLServer + ``alox_manager_monochromator`` (no TCP server any more). Positions are
integer nanometres: :meth:`Monochromator.open` puts the instrument in nanometres when it is not.

Opening (LabVIEW ``Setup``, spec §4.2, plus checks): open the port (an open one is closed first), flush, ECHO (the
instrument must return 27), QUERY 19 (serial number), QUERY 14 (units; UNITS 1 when not nanometres), QUERY 2 / 3 / 4
(grooves, blaze, grating: they give the range), QUERY 0 (position). Event ``device.<name>.opened``.

The core uses :meth:`position`, :meth:`goto`, :meth:`serial`, :meth:`units`, :meth:`groove`, :meth:`blaze`,
:meth:`grating`, :meth:`speed`, :meth:`status`. :meth:`reset`, :meth:`scan`, :meth:`calibrate`,
:meth:`select_grating`, :meth:`set_speed_level` and :meth:`query` are **engineering** methods: no LabVIEW production
code sent RESET, SCAN, CALIBRATE or SELECT (spec §4.3), and the core must not use them.

**Not in the device: the wait after a wavelength change.** LabVIEW's manager waited ``[hardware.monochromator]
waitposition`` ms after each Set Position and the functions wait for the camera exposure (changelog 1.0.0.11:
``2 · max(old, new exposure)``; Get Image: ``100 ms + 0.11 · max``; spec §6.3, Q5). Both belong to the core, which
knows the exposures: :meth:`goto` returns as soon as the instrument reports the end of the move and the position is
confirmed. ``MonochromatorSettings.wait_position_ms`` carries the INI value for the core.

Defects of the LabVIEW code that are fixed here (spec §9; reproduce → decision):

- D1 the "command not accepted" test ran on the first byte of every read, data included (QUERY 19 of a serial number
  ≥ 32768 failed with code = high byte) → only the **status byte** is tested, and the end byte must be 24
  (:meth:`Cm11x.transact`).
- D2 the VISA port kept its default termination character 0x0A on a binary link (inferred from the VISA defaults: a
  status byte 10 cut the answer and left the end byte for the next command) → pyserial without terminator,
  exact-length reads with a deadline.
- D3 SELECT sent ``26, high, low`` (two data bytes; the manual has one) → ``26, g``.
- D4 Set Scan Speed: level 10 sent speed 0 (10 entries for "0..10") and 1800 gr/mm used ``/3`` → levels 0..9 only,
  ``speed = round(1000 · 1200 / grooves / 2**level)`` for every grating (:func:`alox.devices.cm11x.speed_table`).
- D5 Reset: the ECHO loop stopped when the echo did *not* come (else ran 30 s) and dropped its errors → ECHO until the
  instrument answers 27, error 9403 when it never does within 30 s.
- D6 Get Groove / Blaze / Grating overwrote the manager's last known position (then used by Get Image to choose the
  exposure) → :attr:`Monochromator.last_position` changes only with a confirmed position (:meth:`goto`,
  :meth:`position`).
- D7 Set / Get Position timeouts raised no error and Set Position cached the target anyway → every timeout raises
  error 9403 (and puts the device in ``fault``: the link is re-opened at the next call); nothing unconfirmed is cached.
- D8 Is Inited / Is Connected were TRUE after a failed Setup → ``ready`` only after the ECHO and the serial number were
  answered.
- D9 no range check, U32 → U16 truncation → integer nanometres only, inside ``[min_nm, max_nm]`` (max from the grating,
  see :attr:`Monochromator.range_nm`), refused before anything is sent (code 1).
- D10 Setup did not close an open session → :meth:`Cm11x.open` closes first.
- D11 Search Wavelength re-read the position until it equalled the target, without limit → :meth:`goto` reads the
  position once after the move and raises error 9041 when it differs by more than ``on_target_tolerance_nm``.
- D12/D13 one ``timeout`` key meant both the VISA timeout and the reply wait → ``timeout_ms`` is the answer timeout of
  a command, ``move_timeout_ms`` (new optional key ``movetimeout``) bounds a GOTO / SCAN.

Error codes (:class:`alox.devices.base.DeviceError` ``code``): 9403 no (complete) answer, −2 / −3 / −4 / −6 / −8
transport problems (all "link failures": ``fault`` + automatic re-opening), 128..255 the status byte of a refused
command, 1 an argument refused before sending, 9041 a move that did not reach its target.
"""

from __future__ import annotations

import logging
import math
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from alox.devices.base import Device, DeviceError
from alox.devices.cm11x import (
    INVALID_INPUT,
    LINK_FAILURE_CODES,
    Cm11x,
    Cm11xError,
    Query,
    Units,
    speed_table,
)
from alox.events import Publish

__all__ = [
    "MAX_NM_AT_1200",
    "NOT_REACHED",
    "Monochromator",
    "MonochromatorSettings",
    "MonochromatorStatus",
    "build_monochromator_backend",
    "load_monochromator_settings",
]

log = logging.getLogger(__name__)

#: LabVIEW's code when the monochromator check of Search Wavelength fails ("Monochro. Error"); used for a move that
#: ended away from its target.
NOT_REACHED = 9041
#: Upper end of the range at 1200 gr/mm; other gratings scale with 1200 / grooves. Inferred from LabVIEW's disabled
#: check "position > 1500 is invalid" (spec §5.2): override with ``MonochromatorSettings.max_nm`` (open question Q1).
MAX_NM_AT_1200 = 1500
U16_MAX = 0xFFFF
CALL_MARGIN_S = 30.0
BAUD_BY_INDEX = {0: 9600, 1: 19200, 2: 38400, 3: 115200}


@dataclass(frozen=True)
class MonochromatorSettings:
    """``[hardware.monochromator]`` of ``alox.ini`` (spec §8) plus the values LabVIEW had as constants.

    ``port`` overrides ``COM<com>`` (``/dev/ttyUSB0``, ``socket://127.0.0.1:5556``). ``max_nm`` None: from the
    grating (:data:`MAX_NM_AT_1200` · 1200 / grooves). ``wait_position_ms`` is **not** applied by the device (see the
    module docstring)."""

    com: int = 2
    baudrate: int = 9600
    timeout_ms: int = 5000
    move_timeout_ms: int = 30000
    wait_position_ms: int = 0
    port: str | None = None
    min_nm: int = 0
    max_nm: int | None = None
    on_target_tolerance_nm: int = 0
    reset_timeout_ms: int = 30000

    @property
    def port_name(self) -> str:
        return self.port or f"COM{self.com if self.com > 0 else 1}"


def load_monochromator_settings(ini: Any, *, warn: Callable[[str], None] = log.warning,
                                port: str | None = None) -> MonochromatorSettings:
    """Settings from a :class:`alox.config.TypedIni` of ``alox.ini``: ``[hardware.monochromator]`` ``comport``
    (default 2), ``baudrate`` (index 0-3, default 0 = 9600), ``timeout`` (ms, default 5000 as the manager and the
    shipped INI), ``waitposition`` (ms, default 0, for the core) and the new optional ``movetimeout`` (ms, default
    30000). The TCP ``port`` of the LabVIEW server is not used."""

    def get(name: str, default: Any) -> Any:
        try:
            return ini.get(name, default)
        except ValueError as exc:
            warn(f"alox.ini: {exc}; default {default!r} used")
            return default

    index = get("hardware.monochromator.baudrate", 0)
    baudrate = BAUD_BY_INDEX.get(index)
    if baudrate is None:
        warn(f"alox.ini: [hardware.monochromator] baudrate = {index} is not an index 0-3; 9600 used")
        baudrate = 9600
    timeout_ms = get("hardware.monochromator.timeout", 5000)
    if timeout_ms <= 0:
        warn(f"alox.ini: [hardware.monochromator] timeout = {timeout_ms} ms is not positive; 5000 used")
        timeout_ms = 5000
    move_ms = get("hardware.monochromator.movetimeout", 30000)
    if move_ms <= 0:
        warn(f"alox.ini: [hardware.monochromator] movetimeout = {move_ms} ms is not positive; 30000 used")
        move_ms = 30000
    return MonochromatorSettings(com=get("hardware.monochromator.comport", 2), baudrate=baudrate,
                                 timeout_ms=timeout_ms, move_timeout_ms=move_ms,
                                 wait_position_ms=max(0, get("hardware.monochromator.waitposition", 0)), port=port)


@dataclass(frozen=True)
class MonochromatorStatus:
    """A snapshot for the UI and the API (None: unknown / not readable now)."""

    state: str
    position_nm: int | None
    units: int | None
    grating: int | None
    grooves: int | None
    blaze_nm: int | None
    speed: int | None
    serial_number: str
    range_nm: tuple[int, int] | None


def build_monochromator_backend(*, simulate: bool, sim_options: Any = None,
                                sleep: Callable[[float], None] | None = None,
                                monotonic: Callable[[], float] | None = None) -> Cm11x:
    """The protocol object: on pyserial (real instrument) or on the emulator of :mod:`alox.devices.cm11x_sim`
    (``simulate``; the emulator is ``backend.emulator``)."""
    kwargs: dict[str, Any] = {}
    if sleep is not None:
        kwargs["sleep"] = sleep
    if monotonic is not None:
        kwargs["monotonic"] = monotonic
    if simulate:
        from alox.devices.cm11x_sim import build_sim_cm11x
        backend, emulator = build_sim_cm11x(sim_options, **kwargs)
        backend.emulator = emulator                                    # type: ignore[attr-defined]
        return backend
    return Cm11x(**kwargs)


class Monochromator(Device):
    """The CM11x monochromator. Positions in integer nanometres. Thread-safe (every operation runs in the device's
    worker)."""

    def __init__(self, backend: Cm11x, settings: MonochromatorSettings | None = None, *, name: str = "wl",
                 publish: Publish | None = None, **device_options: Any) -> None:
        self.cm = backend
        self._settings = settings or MonochromatorSettings()
        self._serial_number = ""
        self._grooves: int | None = None
        self._blaze: int | None = None
        self._grating: int | None = None
        self._last_position: int | None = None
        self._failure_lock = threading.Lock()
        self._async_failure: DeviceError | None = None
        device_options.setdefault("default_timeout_s", self._settings.timeout_ms / 1000.0 + CALL_MARGIN_S)
        super().__init__(name, publish=publish, **device_options)

    # ============================================================================================== settings
    @property
    def settings(self) -> MonochromatorSettings:
        return self._settings

    def set_settings(self, **changes: Any) -> None:
        """Change settings in memory (``min_nm``, ``max_nm``, ``on_target_tolerance_nm``, ``timeout_ms``,
        ``move_timeout_ms``…); the INI is not written. Port changes apply at the next :meth:`open`."""
        self._settings = replace(self._settings, **changes)

    @property
    def _timeout_s(self) -> float:
        return self._settings.timeout_ms / 1000.0

    @property
    def _move_timeout_s(self) -> float:
        return self._settings.move_timeout_ms / 1000.0

    # ============================================================================================== cached values
    @property
    def serial_number(self) -> str:
        """QUERY 19 read at opening (decimal text; no I/O)."""
        return self._serial_number

    @property
    def last_position(self) -> int | None:
        """The last **confirmed** position in nm (opening, :meth:`goto`, :meth:`position`); None after a failure."""
        return self._last_position

    @property
    def range_nm(self) -> tuple[int, int] | None:
        """``(min, max)`` nm accepted by :meth:`goto`; None before the grating is known."""
        if self._settings.max_nm is not None:
            high = self._settings.max_nm
        elif self._grooves:
            high = int(MAX_NM_AT_1200 * 1200 / self._grooves)
        else:
            return None
        return max(0, self._settings.min_nm), min(U16_MAX, high)

    # ============================================================================================== device hooks
    def _open(self) -> None:
        cm = self.cm
        self._last_position = None
        cm.open(self._settings.port_name, self._settings.baudrate, self._timeout_s)
        cm.echo()
        self._serial_number = str(cm.query(Query.SERIAL))
        units = cm.query(Query.UNITS)
        if units != Units.NANOMETERS:
            log.info("%s: the instrument was in units %d: UNITS 1 (nanometres) sent", self.name, units)
            cm.set_units(Units.NANOMETERS)
            self.emit("units_set", previous=units, units=int(Units.NANOMETERS))
        self._read_grating()
        self._last_position = cm.query(Query.POSITION)
        self.emit("opened", serial_number=self._serial_number, grating=self._grating, grooves=self._grooves,
                  blaze_nm=self._blaze, position_nm=self._last_position, port=cm.port)

    def _close(self) -> None:
        self.cm.close()

    def _is_link_failure(self, exc: BaseException) -> bool:
        return isinstance(exc, Cm11xError) and exc.code in LINK_FAILURE_CODES

    def _translate(self, exc: BaseException, what: str) -> DeviceError:
        if isinstance(exc, DeviceError):
            return exc
        if isinstance(exc, Cm11xError):
            return DeviceError(self.name, f"{what}: {exc.message}", code=exc.code)
        return super()._translate(exc, what)

    def _handle_failure(self, exc: BaseException, what: str) -> DeviceError:
        if self._is_link_failure(exc):
            self._last_position = None                  # nothing is known about the instrument any more
        return super()._handle_failure(exc, what)

    def _read_grating(self) -> None:
        self._grooves = self.cm.query(Query.GROOVES)
        self._blaze = self.cm.query(Query.BLAZE)
        self._grating = self.cm.query(Query.GRATING)

    # ============================================================================================== queries
    def position(self, *, timeout: float | None = None) -> int:
        """QUERY 0, in nm; updates :attr:`last_position`."""
        def run() -> int:
            self._last_position = self.cm.query(Query.POSITION)
            return self._last_position
        return self.call("position", run, timeout=timeout)

    def serial(self, *, timeout: float | None = None) -> str:
        """QUERY 19 again (LabVIEW ``Get Serial``, also its heartbeat check)."""
        def run() -> str:
            self._serial_number = str(self.cm.query(Query.SERIAL))
            return self._serial_number
        return self.call("serial", run, timeout=timeout)

    def units(self, *, timeout: float | None = None) -> Units:
        """QUERY 14 (always nanometres once open, unless an engineering command changed it)."""
        return self.call("units", lambda: Units(self.cm.query(Query.UNITS)), timeout=timeout)

    def groove(self, *, timeout: float | None = None) -> int:
        """QUERY 2: gr/mm of the current grating (does not touch :attr:`last_position`)."""
        def run() -> int:
            self._grooves = self.cm.query(Query.GROOVES)
            return self._grooves
        return self.call("groove", run, timeout=timeout)

    def blaze(self, *, timeout: float | None = None) -> int:
        """QUERY 3: blaze wavelength (nm) of the current grating."""
        def run() -> int:
            self._blaze = self.cm.query(Query.BLAZE)
            return self._blaze
        return self.call("blaze", run, timeout=timeout)

    def grating(self, *, timeout: float | None = None) -> int:
        """QUERY 4: current grating number (1 or 2)."""
        def run() -> int:
            self._grating = self.cm.query(Query.GRATING)
            return self._grating
        return self.call("grating", run, timeout=timeout)

    def speed(self, *, timeout: float | None = None) -> int:
        """QUERY 5: scan speed (Å/s)."""
        return self.call("speed", lambda: self.cm.query(Query.SPEED), timeout=timeout)

    def echo(self, *, timeout: float | None = None) -> None:
        """ECHO: a link check that changes nothing (the core's heartbeat can use it, or :meth:`serial`)."""
        self.call("echo", self.cm.echo, timeout=timeout)

    def status(self, *, timeout: float | None = None) -> MonochromatorStatus:
        """State, position, units, grating, grooves, blaze and speed in one call; None for what cannot be read."""
        def read() -> MonochromatorStatus:
            values: list[int | None] = []
            for code in (Query.POSITION, Query.UNITS, Query.GRATING, Query.GROOVES, Query.BLAZE, Query.SPEED):
                try:
                    values.append(self.cm.query(code))
                except Cm11xError as exc:
                    if exc.link_failure:
                        raise
                    values.append(None)
            position, units, grating, grooves, blaze, speed = values
            self._last_position = position
            self._grating, self._grooves, self._blaze = grating, grooves, blaze
            return MonochromatorStatus(str(self.state), position, units, grating, grooves, blaze, speed,
                                       self._serial_number, self.range_nm)
        try:
            return self.call("status", read, timeout=timeout)
        except DeviceError:
            return MonochromatorStatus(str(self.state), None, None, None, None, None, None, self._serial_number,
                                       self.range_nm)

    # ============================================================================================== moves
    def check_target(self, nm: Any) -> int:
        """The target as an int nm inside :attr:`range_nm`, else :class:`DeviceError` code 1 (nothing is sent)."""
        if isinstance(nm, bool) or not isinstance(nm, int | float) or not math.isfinite(nm) or nm != int(nm):
            raise DeviceError(self.name, f"goto: {nm!r} is not a whole number of nm", code=INVALID_INPUT)
        target = int(nm)
        limits = self.range_nm
        if limits is None:
            raise DeviceError(self.name, "goto: the range is unknown (device not open)", code=INVALID_INPUT)
        lo, hi = limits
        if not lo <= target <= hi:
            raise DeviceError(self.name, f"goto: {target} nm outside [{lo}, {hi}] nm", code=INVALID_INPUT)
        return target

    def goto(self, nm: int, *, wait: bool = True, timeout: float | None = None) -> int:
        """GOTO ``nm`` (integer nanometres inside :attr:`range_nm`), wait for the instrument's end of move, read the
        position back (QUERY 0) and check it (error 9041 if it differs by more than ``on_target_tolerance_nm``).
        Publishes ``move_started`` then ``position``. Returns the position reached.

        ``wait=False``: the move is queued and the target returned at once; :meth:`wait_end_move` waits for it and
        raises its error; a failure is also published as ``move_failed``. The wait after a wavelength change (light
        and exposure settling) is the core's job, not this method's."""
        target = self.check_target(nm)
        if not wait:
            future = self.submit("goto", lambda: self._goto(target))
            future.add_done_callback(lambda f: self._async_done(f, target))
            return target
        limit = timeout if timeout is not None else self._move_timeout_s + self._timeout_s + CALL_MARGIN_S
        return self.call("goto", lambda: self._goto(target), timeout=limit)

    def _goto(self, target: int) -> int:
        self.check_target(target)                       # the range may have changed since the call was queued
        self.emit("move_started", target_nm=target, from_nm=self._last_position)
        self._last_position = None
        self.cm.goto(target, timeout_s=self._move_timeout_s)
        reached = self.cm.query(Query.POSITION)
        self._last_position = reached
        self.emit("position", position_nm=reached, target_nm=target)
        if abs(reached - target) > self._settings.on_target_tolerance_nm:
            raise DeviceError(self.name, f"goto: the monochromator is at {reached} nm, not at {target} nm",
                              code=NOT_REACHED)
        return reached

    def _async_done(self, future: Any, target: int) -> None:
        exc = future.exception()
        if exc is None:
            return
        error = exc if isinstance(exc, DeviceError) else self._translate(exc, "goto")
        with self._failure_lock:
            self._async_failure = error
        log.error("%s: queued goto %d nm failed: %s", self.name, target, error)
        self.emit("move_failed", target_nm=target, error=str(error), code=error.code)

    def wait_end_move(self, *, timeout: float | None = None) -> None:
        """Wait until the queued moves (``goto(wait=False)``) are done; raises the error of a failed one (once)."""
        limit = timeout if timeout is not None else self._move_timeout_s + self._timeout_s + CALL_MARGIN_S
        self.call("wait end move", lambda: None, timeout=limit)
        with self._failure_lock:
            error, self._async_failure = self._async_failure, None
        if error is not None:
            raise error

    # ============================================================================================== engineering
    def set_speed_level(self, level: int, *, timeout: float | None = None) -> int:
        """Engineering. SPEED from a level 0 (fastest) .. 9 of :func:`alox.devices.cm11x.speed_table` for the
        current grating (QUERY 2 first, as LabVIEW). Returns the speed sent (Å/s)."""
        if isinstance(level, bool) or not isinstance(level, int) or not 0 <= level <= 9:
            raise DeviceError(self.name, f"set speed: level {level!r} is not 0..9", code=INVALID_INPUT)

        def run() -> int:
            self._grooves = self.cm.query(Query.GROOVES)
            value = speed_table(self._grooves)[level]
            self.cm.set_speed(value)
            self.emit("speed", level=level, speed=value)
            return value
        return self.call("set speed", run, timeout=timeout)

    def select_grating(self, grating: int, *, timeout: float | None = None) -> None:
        """Engineering. SELECT ``grating`` (1 or 2), then re-read grooves / blaze / grating (new range) and the
        position."""
        if isinstance(grating, bool) or grating not in (1, 2):
            raise DeviceError(self.name, f"select grating: {grating!r} is not 1 or 2", code=INVALID_INPUT)

        def run() -> None:
            self._last_position = None
            self.cm.select_grating(grating, timeout_s=self._move_timeout_s)
            self._read_grating()
            self._last_position = self.cm.query(Query.POSITION)
            self.emit("grating", grating=self._grating, grooves=self._grooves, blaze_nm=self._blaze)
        self.call("select grating", run, timeout=timeout or self._move_timeout_s + CALL_MARGIN_S)

    def reset(self, *, timeout: float | None = None) -> float:
        """Engineering. RESET then ECHO until the instrument answers (at most ``reset_timeout_ms``); then the units,
        grating and position are read again (nanometres restored when needed). Returns the seconds until the echo."""
        def run() -> float:
            self._last_position = None
            elapsed = self.cm.reset(echo_timeout_s=self._settings.reset_timeout_ms / 1000.0)
            if self.cm.query(Query.UNITS) != Units.NANOMETERS:
                self.cm.set_units(Units.NANOMETERS)
            self._read_grating()
            self._last_position = self.cm.query(Query.POSITION)
            self.emit("reset", seconds=round(elapsed, 3), position_nm=self._last_position)
            return elapsed
        limit = timeout or self._settings.reset_timeout_ms / 1000.0 + CALL_MARGIN_S
        return self.call("reset", run, timeout=limit)

    def scan(self, start_nm: int, end_nm: int, *, timeout: float | None = None) -> int:
        """Engineering. SCAN from ``start_nm`` to ``end_nm`` at the current speed; returns the position read
        afterwards. ``timeout`` should cover the slew plus ``|end − start| · 10 / speed`` seconds."""
        start, end = self.check_target(start_nm), self.check_target(end_nm)

        def run() -> int:
            self._last_position = None
            self.cm.scan(start, end, timeout_s=timeout or self._move_timeout_s)
            self._last_position = self.cm.query(Query.POSITION)
            self.emit("position", position_nm=self._last_position, target_nm=end)
            return self._last_position
        return self.call("scan", run, timeout=(timeout or self._move_timeout_s) + CALL_MARGIN_S)

    def calibrate(self, nm: int, *, timeout: float | None = None) -> None:
        """Engineering, destructive. CALIBRATE: declares the current position to be ``nm``; the manual says it
        erases the factory calibration and must follow a ZERO."""
        value = self.check_target(nm)

        def run() -> None:
            self.cm.calibrate(value)
            self._last_position = self.cm.query(Query.POSITION)
            self.emit("calibrated", position_nm=self._last_position)
        self.call("calibrate", run, timeout=timeout)

    def query(self, code: int, *, timeout: float | None = None) -> int:
        """Engineering. Any QUERY sub-code (0..20, :class:`alox.devices.cm11x.Query`), decoded as LabVIEW did."""
        return self.call(f"query {code}", lambda: self.cm.query(code), timeout=timeout)

