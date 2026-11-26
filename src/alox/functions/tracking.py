"""Tracking: re-centre the mesa under the High Mag camera by relative XY moves, as a measurement function.

Spec: ``docs/vision-core/map.md`` Part 3 §2.1 (common pattern), §3.7 (Tracking), Part 4 §3.8
(``tools_tracking_move_relative``, ported as :func:`alox.vision.wafer.tracking_move`); ``docs/vision-core/xy-stage.md``
§7 (manager methods), §9 (coordinate systems); owner question M6 (signs as read in the diagrams: confirmed).
Diagrams: ``alox_function_ll_tracking_state_machine`` (cited ``sm dN``: ``d9…d11`` Started, ``d12…d33`` Track and
its five frames), ``alox_function_ll_tracking_bundle_profile_parameters d`` (``bundle d``).

States (``alox_typedef_tracking_function_state``): ``Start`` → ``Started`` → ``Track`` → ``Finished``; ``Error``,
``Stopped``. :meth:`Tracking.run` executes one state per loop iteration, ``ctx.check()`` between states and
``ctx.set_state`` on every change (as :mod:`alox.functions.focus`).

- ``Started`` (sm d9…d11, "Valid input circle"): the circle of the caller (``Circle In``: HL mesa passes the last
  Detect Circle result with ``Set Params``) equal to ``{0, 0, 0}`` → 9028 "tracking error (image/region of
  interest)".
- ``Track`` frame 0: start time. Frame 1 (sm d12, "TRACKING ALWAYS USE LOW ANGLE/HIGH MAG CAMERA"): ``Get Position
  XY`` (errors cleared) = the original position; ``Tracking Radius (mm) = Tracking Diam. (µm) / 2 / 1000``;
  ``tools_tracking_move_relative`` with the High Mag image (size and calibration), the circle centre, the radius as
  ``Min. Distance`` and the flips ``hardware.camera.highmag.hflip/vflip``. When **either** component reaches the
  radius (``Min. Move X OR Min. Move Y`` selects both cases, sm d12), X then Y: ``Get Status`` (``ERR?``, FALSE →
  9027 "tracking error (xy stages)", sm d18, d22), ``Move Relative`` by the full component, ``Move Wait`` (0 ms, 10 ms
  polling), log ``Tracking (Move X=%f)`` / ``Tracking (Move Y=%f)``; ``Moved X/Y`` = no error; an error logs
  ``Tracking (Move X)`` / ``Tracking (Move Y)``. Frame 2 (sm d25): on error, ``Move Absolute XY`` back to the original
  position, log ``Tracking (Position Restored)``, the restore's own errors cleared (the first error is kept). Frame 3
  (sm d28): wait 500 ms; when moved, ``get_image Run Once`` (High Mag) "Refresh image of track object (centered)";
  overlay ``LL_TRACKING`` (option ``Overlay Tracking Zone``): a circle at the image centre of radius ``Tracking
  Diam. · 0.5 / pixel size (µm)``. Frame 4 (sm d33): process time; error → ``Error``, else ``Finished``.
- Parameters (``bundle d``): ``tracking.diameter`` (0.2, µm), ``tracking.overlayzone`` (F).

Reproduced: the states, the validity check of the circle and 9028, the radius formula, the "either axis" rule and the
full-component moves, X before Y, the per-axis status check (9027), the restore on error, the 500 ms pause, the
refresh image, the zone overlay, the log texts, the report fields.

Fixed:

- 9080 (``tools_tracking_move_relative`` "Undefined" calibration) is an error of the function; LabVIEW passed it on
  through frame 4 the same way (no change visible), but the calibration now comes from ``hardware.camera.highmag.
  pixelsize`` / ``units`` instead of the NI image's calibration info.
- A failed ``Get Position XY`` read (0, 0) as the original position, so a restore after an error drove the stage to
  (0, 0): the restore is skipped when the position could not be read.
- ``Move Y`` is skipped after a failed ``Move X`` (LabVIEW's chained error wire made its VIs do nothing).
- The image: the caller's (``TrackingParams.image``) or the newest High Mag image of the image store; none → 9028
  (LabVIEW ran on an empty image).
- The refresh image and the overlay errors are logged and cleared (as the "Clear Errors" of frame 3), not lost.
- The debug globals ``X/Y Stage Invert`` are :class:`TrackingOptions` fields (default OFF, production).
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import numpy as np

from alox.core.functions import (
    FunctionContext,
    FunctionName,
    MeasurementFunction,
    Stopped,
)
from alox.devices.base import DeviceError
from alox.functions.focus import CameraType
from alox.functions.services import Circle, Services, Shape
from alox.vision.wafer import (
    INVALID_CALIBRATION,
    CalibrationError,
    PixelUnit,
    StageCoordinateSystem,
    tracking_move,
)
from aloxcam.errors import AloxError

__all__ = [
    "INVALID_ARGUMENTS",
    "INVALID_CALIBRATION",
    "TRACKING_IMAGE",
    "TRACKING_PROCESSING",
    "TRACKING_TIMEOUT",
    "TRACKING_XY",
    "FunctionFailed",
    "TRState",
    "Tracking",
    "TrackingOptions",
    "TrackingParams",
    "TrackingReport",
    "app_log",
    "calibration",
    "stage_coordinate_system",
]

log = logging.getLogger(__name__)

#: ``alox_en.lang`` codes.
INVALID_ARGUMENTS = 9002                   # "invalid arguments error"
TRACKING_TIMEOUT = 9025                    # "tracking error (timeout)": not raised by the diagrams read
TRACKING_PROCESSING = 9026                 # "tracking error (processing)": not raised by the diagrams read
TRACKING_XY = 9027                         # "tracking error (xy stages)"
TRACKING_IMAGE = 9028                      # "tracking error (image/region of interest)"

OVERLAY = "tracking"
ZONE_COLOR = (0, 255, 0)


# ================================================================================================== shared helpers
class FunctionFailed(AloxError):
    """A low-level function reached its ``Error`` state: ``code``/``source`` as LabVIEW's error cluster, ``state``
    the state that failed, ``report`` the report at that moment (LabVIEW keeps the indicator)."""

    def __init__(self, code: int, source: str, *, state: str = "", report: Any = None) -> None:
        super().__init__(code, source)
        self.state = state
        self.report = report


def app_log(services: Services, level: str, text: str) -> None:
    """``alox_manager_log`` (the application log) when there is one, and Python logging."""
    getattr(log, level)(text)
    target = getattr(services.log, level, None) if services.log is not None else None
    if callable(target):
        try:
            target(text)
        except Exception:                                  # logging must never fail a function
            log.debug("application log failed", exc_info=True)


def setting(services: Services, key: str, default: Any) -> Any:
    """``alox_manager_properties`` "Get Property": the value, or ``default`` when missing or unreadable."""
    try:
        value = services.settings.get(key, default)
    except (RuntimeError, LookupError, TypeError, ValueError):
        return default
    return default if value is None else value


def flag(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().upper() in ("TRUE", "T", "1", "YES", "ON")
    return bool(value)


def number(value: Any, what: str, *, integer: bool = False) -> Any:
    """A finite number from a number or an INI string (decimal comma accepted); else :class:`FunctionFailed` 9002."""
    if isinstance(value, bool) or not isinstance(value, (int, float, np.integer, np.floating)):
        try:
            value = float(str(value).strip().replace(",", "."))
        except ValueError:
            raise FunctionFailed(INVALID_ARGUMENTS, f"{what}: not a number: {value!r}") from None
    if not math.isfinite(float(value)):
        raise FunctionFailed(INVALID_ARGUMENTS, f"{what}: not finite: {value!r}")
    return round(float(value)) if integer else float(value)


def calibration(services: Services, camera: str) -> tuple[float, PixelUnit | str]:
    """The pixel size of ``camera`` and its unit (``hardware.camera.<camera>.pixelsize`` / ``units``: 0 pixels, 1 µm,
    2 mm): what ``IMAQ Get Calibration Info`` gave LabVIEW. A unit in pixels is returned as ``"pixels"``
    (:func:`alox.vision.wafer.tracking_move` then raises 9080)."""
    size = setting(services, f"hardware.camera.{camera}.pixelsize", 1.0)
    units = str(setting(services, f"hardware.camera.{camera}.units", 1)).strip().lower()
    try:
        value = float(str(size).replace(",", "."))
    except ValueError:
        value = math.nan
    if units in ("2", "mm", "millimeter", "millimetre"):
        return value, PixelUnit.MILLIMETER
    if units in ("1", "um", "µm", "μm", "micrometer", "micrometre"):
        return value, PixelUnit.MICROMETER
    return value, "pixels"


def pixel_size_um(size: float, unit: PixelUnit | str) -> float:
    if unit is PixelUnit.MILLIMETER:
        return size * 1000.0
    if unit is PixelUnit.MICROMETER:
        return size
    raise CalibrationError(INVALID_CALIBRATION, f"invalid calibration error (unit {unit!r})")


def stage_coordinate_system(services: Services) -> StageCoordinateSystem:
    """``alox_manager_xy_coordinate_system`` "Get Wafer Coord. Sys": ``hardware.xy.stagescoordsys`` (0–3 = #1–#4;
    ``Coord. Sys. 4`` when missing, the manager's Init value)."""
    try:
        return StageCoordinateSystem(int(number(setting(services, "hardware.xy.stagescoordsys", 3), "stagescoordsys",
                                                integer=True)))
    except (FunctionFailed, ValueError):
        return StageCoordinateSystem.X_LEFT_Y_DOWN


def flips(services: Services, camera: str) -> tuple[bool, bool]:
    """``hardware.camera.<camera>.hflip`` / ``vflip`` (default F)."""
    return (flag(setting(services, f"hardware.camera.{camera}.hflip", False)),
            flag(setting(services, f"hardware.camera.{camera}.vflip", False)))


def error_code_source(exc: BaseException, device_code: int = TRACKING_XY) -> tuple[int, str]:
    """The LabVIEW error cluster of ``exc``: an :class:`AloxError`'s own; a device error's code, ``device_code`` when it
    has none; 9002 for invalid values; else 9026 "processing"."""
    if isinstance(exc, AloxError):
        return exc.code, exc.source
    if isinstance(exc, DeviceError):
        return (device_code if exc.code is None else int(exc.code)), str(exc)
    return (INVALID_ARGUMENTS if isinstance(exc, (TypeError, ValueError)) else TRACKING_PROCESSING), str(exc)


def _set(obj: object, name: str, value: Any) -> None:
    object.__setattr__(obj, name, value)


# ================================================================================================== params, report
class TRState(StrEnum):
    """``alox_typedef_tracking_function_state``."""

    START = "Start"
    STARTED = "Started"
    TRACK = "Track"
    FINISHED = "Finished"
    ERROR = "Error"
    STOPPED = "Stopped"


@dataclass(frozen=True)
class TrackingParams:
    """``Tracking Params`` (``Tracking Diam. (µm)``, ``Overlay Tracking Zone``) and the inputs the caller sets with
    ``Set Params``: ``circle`` (``Circle In``: centre x, y and radius in pixels of the High Mag image; None = the
    circle set with :meth:`Tracking.set_circle`) and ``image`` (None = the newest High Mag image)."""

    diameter_um: float = 0.2
    overlay_zone: bool = False
    circle: tuple[float, float, float] | None = None
    image: np.ndarray | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        _set(self, "diameter_um", number(self.diameter_um, "Tracking Diam. (µm)"))
        _set(self, "overlay_zone", flag(self.overlay_zone))
        if self.circle is not None:
            _set(self, "circle", _circle_tuple(self.circle))
        if self.image is not None:
            image = np.asarray(self.image)
            if image.ndim < 2 or image.size == 0:
                raise FunctionFailed(TRACKING_IMAGE, f"tracking: image of shape {image.shape}")
            _set(self, "image", image)

    @classmethod
    def from_profile(cls, profile: Any, **overrides: Any) -> TrackingParams:
        """``alox_function_ll_tracking_bundle_profile_parameters d``: ``tracking.diameter`` (0.2),
        ``tracking.overlayzone`` (F); ``overrides`` win."""
        values: dict[str, Any] = {"diameter_um": _profile_get(profile, "tracking.diameter", 0.2),
                                  "overlay_zone": _profile_get(profile, "tracking.overlayzone", False)}
        values.update(overrides)
        return cls(**values)


def _profile_get(profile: Any, key: str, default: Any) -> Any:
    try:
        value = profile.get(key, default)
    except (RuntimeError, LookupError, TypeError, ValueError):          # no profile loaded
        return default
    return default if value is None else value


def _circle_tuple(circle: Any) -> tuple[float, float, float]:
    """A circle as ``(x, y, r)``: a tuple, :class:`alox.vision.circles.Circle` (``x``, ``y``, ``radius``) or the overlay
    :class:`~alox.functions.services.Circle` (``cx``, ``cy``, ``r``)."""
    if hasattr(circle, "cx"):
        values = (circle.cx, circle.cy, circle.r)
    elif hasattr(circle, "radius"):
        values = (circle.x, circle.y, circle.radius)
    else:
        values = tuple(circle)
        if len(values) == 2:
            values = (*values, 0.0)
    if len(values) != 3:
        raise FunctionFailed(INVALID_ARGUMENTS, f"tracking: circle (x, y, r) expected, got {circle!r}")
    return tuple(number(v, "Circle In") for v in values)            # type: ignore[return-value]


@dataclass(frozen=True)
class TrackingReport:
    """``Tracking Results`` (``alox_typedef_tracking_function_results``): the move in mm (stage axes) and pixels,
    whether each axis moved, the process time; plus the position before the move (None if unreadable)."""

    move_mm: tuple[float, float] = (0.0, 0.0)
    move_px: tuple[float, float] = (0.0, 0.0)
    moved: tuple[bool, bool] = (False, False)
    process_time_ms: float = 0.0
    original_position: tuple[float, float] | None = None


@dataclass(frozen=True)
class TrackingOptions:
    """Server-side settings (defaults: the LabVIEW values)."""

    settle_s: float = 0.5                   # frame 3: Wait (ms) 500 before the refresh image
    move_wait_polling_ms: int = 10          # Move Wait {Axis, 0, 10, F}
    x_stage_invert: bool = False            # debug globals "X/Y Stage Invert" (OFF for AET)
    y_stage_invert: bool = False
    camera: str = "highmag"                 # "TRACKING ALWAYS USE LOW ANGLE/HIGH MAG CAMERA"


# ================================================================================================== the function
class Tracking(MeasurementFunction):
    """Tracking (``alox_function_ll_tracking``): ``run(params, ctx)`` returns a :class:`TrackingReport`; raises
    :class:`FunctionFailed` (9027, 9028, 9080, the XY device's code) after the ``Error`` state and
    :class:`alox.core.functions.Stopped` on stop/abort. ``params`` None: :meth:`TrackingParams.from_profile` with the
    circle of :meth:`set_circle`."""

    name = FunctionName.TRACKING

    def __init__(self, services: Services, *, options: TrackingOptions | None = None) -> None:
        services.device("xy")
        self.services = services
        self.options = options or TrackingOptions()
        self._circle: tuple[float, float, float] | None = None
        self._last: TrackingReport | None = None

    # LabVIEW "Set Params" (the circle and image of the caller) and "Get Results"
    def set_circle(self, circle: Any) -> None:
        """``Set Params``: the circle (pixels) tracked by the next runs whose params carry none."""
        self._circle = None if circle is None else _circle_tuple(circle)

    @property
    def last_report(self) -> TrackingReport | None:
        return self._last

    def run(self, params: TrackingParams | None, ctx: FunctionContext) -> TrackingReport:
        if params is None:
            params = TrackingParams.from_profile(self.services.profile)
        if not isinstance(params, TrackingParams):
            raise FunctionFailed(INVALID_ARGUMENTS, f"tracking: TrackingParams expected, got {type(params).__name__}")
        job = _TrackJob(self, params, ctx)
        try:
            return job.execute()
        finally:
            self._last = job.report


class _TrackJob:
    def __init__(self, function: Tracking, p: TrackingParams, ctx: FunctionContext) -> None:
        self.f = function
        self.s = function.services
        self.o = function.options
        self.p = p
        self.ctx = ctx
        self.circle = p.circle if p.circle is not None else function._circle
        self.report = TrackingReport()
        self.error: BaseException | None = None
        self.failed_state = ""

    # ---------------------------------------------------------------------------------------------- loop
    def execute(self) -> TrackingReport:
        handlers: Mapping[TRState, Callable[[], TRState]] = {
            TRState.START: self.start, TRState.STARTED: self.started, TRState.TRACK: self.track}
        state = TRState.START
        self.ctx.set_state(str(state))
        try:
            while state not in (TRState.FINISHED, TRState.ERROR):
                self.ctx.check()
                nxt = handlers[state]()
                if nxt is not state:
                    self._publish(nxt)
                state = nxt
        except Stopped:
            self.ctx.set_state(str(TRState.STOPPED))
            raise
        if state is TRState.ERROR:
            code, source = error_code_source(self.error) if self.error else (TRACKING_PROCESSING, "unknown")
            raise FunctionFailed(code, source, state=self.failed_state, report=self.report) from self.error
        return self.report

    def _publish(self, state: TRState) -> None:
        data: dict[str, Any] = {}
        if state is TRState.ERROR and self.error is not None:
            code, source = error_code_source(self.error)
            data = {"code": code, "error": source, "failed_state": self.failed_state}
        elif state is TRState.FINISHED:
            data = {"move_mm": self.report.move_mm, "moved": self.report.moved}
        self.ctx.set_state(str(state), **data)

    def fail(self, exc: BaseException, state: TRState) -> TRState:
        self.error, self.failed_state = exc, str(state)
        return TRState.ERROR

    def pause(self, seconds: float) -> None:
        if seconds <= 0:
            return
        if self.s.sleep is time.sleep:
            self.ctx.sleep(seconds)
        else:
            self.ctx.check()
            self.s.sleep(seconds)

    # ---------------------------------------------------------------------------------------------- states
    def start(self) -> TRState:
        return TRState.STARTED

    # "Valid input circle"
    def started(self) -> TRState:
        if self.circle is None or self.circle == (0.0, 0.0, 0.0):
            return self.fail(FunctionFailed(TRACKING_IMAGE, "tracking error (image/region of interest): no circle"),
                             TRState.STARTED)
        return TRState.TRACK

    def _image(self) -> np.ndarray | None:
        if self.p.image is not None:
            return self.p.image
        try:
            return self.s.images.last(self.o.camera)
        except Exception:                                  # noqa: BLE001 - an image store without that camera
            return None

    def track(self) -> TRState:
        s, o = self.s, self.o
        t0 = s.clock()                                                       # frame 0
        xy = s.device("xy")
        try:                                                                 # "<< Original Position <<", cleared
            original: tuple[float, float] | None = tuple(xy.position())      # type: ignore[assignment]
        except DeviceError as exc:
            log.debug("tracking: Get Position XY failed (cleared): %s", exc)
            original = None
        error: BaseException | None = None
        move_mm, move_px, moved = (0.0, 0.0), (0.0, 0.0), [False, False]
        image = self._image()
        radius_mm = self.p.diameter_um / 2.0 / 1000.0                       # "Tracking Radius (mm)"
        try:
            if image is None:
                raise FunctionFailed(TRACKING_IMAGE, "tracking error (image/region of interest): no High Mag image")
            height, width = image.shape[:2]
            size, unit = calibration(s, o.camera)
            hflip, vflip = flips(s, o.camera)
            move = tracking_move(self.circle[:2], (width, height), pixel_size=size, pixel_unit=unit,  # type: ignore[index]
                                 min_distance_mm=radius_mm, coordinate_system=stage_coordinate_system(s),
                                 flip_horizontal=hflip, flip_vertical=vflip, x_stage_invert=o.x_stage_invert,
                                 y_stage_invert=o.y_stage_invert)
            move_mm, move_px = move.move_mm, move.move_px
        except (AloxError, ValueError) as exc:
            error = exc
            move = None
        attempted = False
        if error is None and move is not None and move.needed:              # Min. Move X OR Min. Move Y
            for index, axis in enumerate(("x", "y")):
                self.ctx.check()
                attempted = True
                try:
                    self._move_axis(xy, axis, move_mm[index])
                    moved[index] = True
                except (DeviceError, AloxError) as exc:
                    app_log(s, "error", f"Tracking (Move {axis.upper()}): {exc}")
                    error = exc
                    break
        self.report = TrackingReport(move_mm, move_px, (moved[0], moved[1]), 0.0, original)
        if error is not None and original is not None and attempted:       # frame 2: restore
            try:
                xy.move_absolute(*original, wait=True)
                app_log(s, "info", "Tracking (Position Restored)")
            except DeviceError as exc:
                log.warning("tracking: restoring the position %s failed (cleared): %s", original, exc)
        self.pause(o.settle_s)                                               # frame 3
        if any(moved):
            try:                                                             # "Refresh image of track object"
                result = self.ctx.run_once(FunctionName.GET_IMAGE, CameraType.HIGH_MAG)
                image = getattr(result, "pixels", image)
            except Stopped:
                raise
            except Exception as exc:                                         # noqa: BLE001 - cleared, as frame 3
                app_log(s, "warning", f"Tracking (Refresh Image): {exc}")
        self._overlay(image)
        process_ms = (s.clock() - t0) * 1000.0                               # frame 4
        self.report = TrackingReport(move_mm, move_px, (moved[0], moved[1]), process_ms, original)
        if error is not None:
            return self.fail(error, TRState.TRACK)
        return TRState.FINISHED

    def _move_axis(self, xy: Any, axis: str, delta: float) -> None:
        """``Get Status`` (``ERR?``: FALSE → 9027), ``Move Relative``, ``Move Wait``, log ``Tracking (Move X=%f)``."""
        try:
            code, _ = xy.read_error(axis)
        except DeviceError:
            code = -1
        if code != 0:
            raise FunctionFailed(TRACKING_XY, f"tracking error (xy stages): {axis.upper()} status")
        if axis == "x":
            xy.move_relative(dx=delta, dy=0.0, wait=False)
        else:
            xy.move_relative(dx=0.0, dy=delta, wait=False)
        xy.wait_end_move(add_wait_ms=0, polling_ms=self.o.move_wait_polling_ms)
        app_log(self.s, "info", f"Tracking (Move {axis.upper()}={delta:f})")

    def _overlay(self, image: np.ndarray | None) -> None:
        """``Overlay Tracking Zone``: circle of the tracking diameter at the image centre (group ``LL_TRACKING``)."""
        shapes: list[Shape] = []
        if self.p.overlay_zone and image is not None:
            try:
                px_um = pixel_size_um(*calibration(self.s, self.o.camera))
                radius = self.p.diameter_um * 0.5 / px_um                     # "µm => pixels"
                height, width = image.shape[:2]
                shapes.append(Circle(width / 2.0, height / 2.0, radius, ZONE_COLOR))
            except (AloxError, ValueError, ZeroDivisionError) as exc:
                app_log(self.s, "warning", f"Tracking (Overlay): {exc}")
        try:
            self.s.images.set_overlay(OVERLAY, shapes)
        except Exception as exc:                                             # noqa: BLE001 - display only
            log.debug("tracking overlay not set: %s", exc)
