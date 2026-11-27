r"""HL Bowing measurement: wafer bowing and wafer/chuck parallelism from the focus Z of nine XY positions.

Spec: ``docs/vision-core/map.md`` Part 3 §2.1 (common pattern), §3.12 (bowing), Part 4 §3.8
(``tools_alox_bowing_computation``: :func:`alox.vision.wafer.compute_bowing`), §3.10/§3.11 (folders and file names:
:mod:`alox.functions.jobfiles`); ``docs/vision-core/xy-stage.md`` §9 (coordinate systems); owner answers B7, M7, B9 in
``docs/vision-core/questions.md``. Diagrams: ``alox_function_hl_bowing_measurement_state_machine`` (cited ``sm dN``),
``…_bundle_profile_parameters d``, ``…_bundle_chuck_properties d``, ``…_ui_run d11`` (progress), the LL bundle VIs
``alox_function_ll_autofocus_bundle_profile_parameters d`` and ``alox_function_ll_refocus_bundle_profile_parameters d``,
``alox_api_v1_put_functions_measure_bowing d`` (the ``x`` parameter).

:meth:`BowingMeasurement.run` runs the state machine in the runner's thread, one state per loop iteration, with
``ctx.check()`` between states and ``ctx.set_state`` on every change (and for LabVIEW's run-panel events ``BOW
Started``, ``BOW Autofocus``, ``BOW Refocus``, ``BOW Measured``, ``BOW Finished``, as ``event=`` data). The core
(:class:`alox.core.core.Core`) sets the bowing status 200, sends the websocket ``started``/``failed`` events and
``finished`` after the run; the function sets the bowing status codes 201…1033 through
``services.set_status("bowing", code)`` and publishes the ``measure_bowing`` and ``error`` websocket events itself.

**Positions** (sm d24…d84; wafer coordinates in mm, X right, Y up; ``θ`` = ``general.bowingAngle`` in degrees, ``D``
= Measure Distance, ``R`` = ``general.wafersize``/2 + ``general.bowingOffset``), in the order of ``Z Measures``
(:data:`alox.vision.wafer.BOWING_ROWS`):

==== ====== ====================== ======= ====== ======================
row  point  position               status  error  move without a wait
==== ====== ====================== ======= ====== ======================
0    ZW2    (0, 0) [#]_            203     1002
1    ZW3    (D cosθ, D sinθ) NE    204     1003
2    ZC3    (R cosθ, R sinθ) NE    205     1004   (Fast method: skipped)
3    ZW1    (−D cosθ, −D sinθ) SW  206     1005
4    ZC1    (−R cosθ, −R sinθ) SW  207     1006   yes (Fast: skipped)
5    ZW4    (D cosθ, −D sinθ) SE   208     1007   (Fast: skipped)
6    ZC4    (R cosθ, −R sinθ) SE   209     1008   yes (Fast: skipped)
7    ZW5    (−D cosθ, D sinθ) NW   210     1009   (Fast: skipped)
8    ZC5    (−R cosθ, R sinθ) NW   211     1010   yes (Fast: skipped)
==== ====== ====================== ======= ====== ======================

.. [#] (2, 2) mm while the global ``Chuck Calibration`` is set (:attr:`BowingParams.chuck_calibration`).

The wafer point is converted to stage coordinates as ``alox_manager_xy_coordinate_system`` "Convert" Wafer → Stages
(:func:`wafer_to_stage`: ``[hardware.xy] waferxorg``/``waferyorg``, ``stagescoordsys``).

**States** (``alox_typedef_bowing_measurement_function_state``):

- before the loop (sm d1, d2, d3): Z joystick off, XY joystick off (errors logged), the XY wafer-limit supervision
  switched off (the chuck points lie beyond the wafer edge) and switched back to its previous value after the run
  (sm d263);
- ``Start`` (sm d6…d23, 8 frames): status 201; report reset to NaN; ``BOW Started``; "Reset Wavelength": the
  monochromator position is read (the initial wavelength), then set to 0 nm for the High Mag. camera, to the same
  position for the Low Mag. camera (sm d12, d15); a failure is status 1001 and ``Error``; XY speed
  ``hardware.xy.speed`` (10) on both axes (errors logged, "Bowing (Set Speed)"); the job-history folders (errors
  cleared). Next ``Started`` (or ``Error``);
- ``Started`` (sm d23): status 202 → ``Move to Pos Zi``;
- ``Move to Pos Zi`` (sm d24…d84): row ``i`` = the number of rows stored; status 203 + i; ``Move Absolute XY`` (not
  waited); on an error the move is sent again, up to 3 attempts ("Retry 3 times if error"), then status 1002 + i and
  ``Error``; on success the row ``[x, y, NaN]`` (wafer mm) is stored and the next state is ``Wait Pos Zi``. The chuck
  points take at least 250 ms (a parallel wait). With the Fast method, ZC3, ZC1, ZW4, ZC4, ZW5 and ZC5 are skipped
  (row stored with NaN, no move). ``i`` ≥ 9 is the ``Default`` case: ``Error`` (status unchanged). Owner's decision
  R1: the Fast method was never finished in LabVIEW (it always reached that ``Default`` case); here it ends after
  ZW1 (the third measured point): Bowing A from ZW1, ZW2, ZW3, then ``Finished`` (or ``Error`` when out of range);
  Bowing B and the parallelism are not measured (NaN, the parallelism counts as valid);
- ``Wait Pos Zi`` (sm d85): status 212 + i; ``Get Position XY`` until both axes are within ±0.01 mm of the target
  (read errors: keep waiting), then 100 ms "make sure the stages has stopped moving" and ``Autofocus Large``; 30 s
  after the move: status 1002 + i and ``Error``;
- ``Autofocus Large`` (sm d89): status 221 + i; LL autofocus Run Once (the profile's ``autofocus.*`` parameters with
  the bowing camera); Get Image Run Once (display); ``BOW Autofocus``; an error or an invalid result retries, at the
  3rd consecutive failure status 1011 + i and ``Error``; else ``Autofocus Fine``;
- ``Autofocus Fine`` (sm d92): status 230 + i; Get Image Run Once; LL refocus Run Once (``refocus.*`` with the bowing
  camera and the method ``Sobel``); Get Image Run Once; ``BOW Refocus``; Z of row i = the refocus Best Z Position;
  retries up to 5 failures, then status 1020 + i and ``Error``; else ``Measure Bowing``;
- ``Measure Bowing`` (sm d93…d190): 1 s "Let some time to the UI to refresh refocus data"; status 239 + i; the
  report gets the new Z (after ZC1: Bowing A, Parallelism A and their validity, dZchuck1; after ZC5: everything
  through ``tools_alox_bowing_computation``, the "Bowing Measured" flag, the result text overlay on a new image,
  ``Finished`` when bowing and parallelism are valid (always while ``Chuck Calibration``), else ``Error``);
  ``BOW Measured``; the image of the point when ``log.savebowingimages`` (default T):
  ``<job data file name>.png`` in the job's ``images\bowing`` folder, name from
  :func:`~alox.functions.jobfiles.job_data_filename` (kind ``bowing``) with the format
  ``%y%m%d_%H%M%S_bowing_<zwafer2|zchuck3|…>``; next ``Move to Pos Zi``;
- ``Finished`` (sm d191…d215, 12 frames): status 300; ``BOW Finished``; XY back to the wafer centre (not waited); Z to
  Zwafer2 (not waited); status 248; websocket ``measure_bowing`` (:func:`alox.api.reports.bowing_event`); the data
  file ``<job data file name>.bow`` in ``files\bowing`` (``%.4f``, tab-delimited: the nine rows ``x y z 0`` then
  ``BowingA BowingB ParallelismA ParallelismB``; while ``Chuck Calibration``: ``ChuckCalibration<name>.chuck`` in
  ``<data>\ChuckCalibration``); status 500; 2 s; the monochromator to the profile's ``wavelength.wlmanual`` (0);
- ``Error`` (sm d216…d253, 13 frames): status 300 when the nine points were measured; websocket ``error``
  (``measure_bowing``, code, text); ``BOW Finished``; XY to the centre, Z to Zwafer2; status 248 when measured; the
  data file ``<yymmdd_HHMMSS>.bow`` in ``files\bowing`` when ``log.savebowingdata`` (T); when measured, the
  detailed status: 1029 bowing out of range (error 9500 "Bowing error"), 1031 both (9502), else for the
  parallelism 1033 axis B (A valid), 1032 axis A (B valid), 1030 both axes (9501 "Parallelism error"); 5 s; the
  monochromator to ``wavelength.wlmanual``. Then :class:`BowingFailed` (the core sends ``failed``);
- ``Stopped`` (sm d254…d262, a stop or an abort): the monochromator to ``wavelength.wlmanual``, status 850, ``BOW
  Finished``, websocket ``failed`` (the core does not send it for a stop); :class:`~alox.core.functions.Stopped` is
  re-raised.

Reproduced: the positions and their order, the statuses and error statuses, the attempts (3 moves, 3 autofocus, 5
refocus; the changelog 1.0.0.7 "loop on error" is already bounded in V1.2.6.2: the bounds are options), the ±0.01 mm
window and the 30 s move timeout, the 100 ms settle, the 250 ms of the chuck points, the UI pauses (1 s per point, 2 s
at the end, 5 s after an error; options), the Fast method's skipped points, the formulas (:mod:`alox.vision.wafer`),
the report, the overlay text, the image and data files and their names, the ``measure_bowing`` JSON (with the owner's
key fix of :func:`alox.api.reports.bowing_event`), the wavelength at the end.

Fixed (internal defects, or behaviour that cannot be kept):

- ZC1, ZC4 and ZC5 went from ``Move to Pos Zi`` straight to ``Autofocus Large`` without ``Wait Pos Zi``: the
  autofocus could start while the XY stage was still moving. Every point waits for its position here
  (``BowingOptions.wait_every_position``).
- The run panel's progress (``…ui_run d11``) counted the leading measured values in the report order (ZW2, ZW3, ZW1,
  …), so it stalled while the chuck points were measured and never reached 100 %; the ``BOW Measured`` progress is
  ``measured points × 100 / 9`` here.
- The websocket ``error`` event was sent before the validity error was set (an event with code 0 after a bowing out
  of range, sm d220 vs d239): it carries the code of the failure here (9500/9501/9502, the device's code, 9402 for
  the XY timeout, 9008/9030 for an invalid autofocus/refocus).
- The ``dZ`` column of ``Z Measures`` (sm d176) is replaced in a 3-column array: LabVIEW's ``Replace Array Subset``
  ignores it; not reproduced (no effect).
- Z is not moved to Zwafer2 when it was not measured (LabVIEW sent a move to NaN after an early error).
- Invalid parameters or settings (Measure Distance not > 0, non-finite values, a coordinate system not 0…3) are
  refused before any motion: status 1000 and
  :class:`BowingError` 9002 (LabVIEW measured the centre five times and ended out of range).
- The joystick re-check of ``Start`` (sm d17: ``Joystick Enabled?`` then ``Disable``) is replaced by the disable
  before the loop (the same effect).
- Pauses are cut short by a stop (``ctx.sleep``); the end pauses do not turn a finished run into a stopped one.

Not ported: the run panel (``Launch Run UI``, ``Close Panel``), the system status 201/500 (set by the core), the
``Bowing Job Finished`` command (the core's runner callback), the simulation variant (§3.14: the devices are
simulated instead).
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import numpy as np

from alox.api import reports
from alox.core.functions import (
    FunctionContext,
    FunctionName,
    JobInfo,
    MeasurementFunction,
    Stopped,
)
from alox.core.status import BowingStatus
from alox.devices.base import DeviceError
from alox.functions import jobfiles
from alox.functions.focus import (
    AutofocusMethod,
    AutofocusParams,
    CameraType,
    RefocusParams,
)
from alox.functions.services import Services, Text, camera_device_name
from alox.vision.wafer import BOWING_ROWS, ChuckProperties, compute_bowing
from aloxcam.errors import AloxError, FocusError, cluster_of
from aloxcam.imaging.png import PngError, write_png

__all__ = [
    "BOWING_ERROR",
    "BOWING_PARALLELISM_ERROR",
    "PARALLELISM_ERROR",
    "POINT_FIELDS",
    "BowState",
    "BowingError",
    "BowingFailed",
    "BowingMeasurement",
    "BowingOptions",
    "BowingParams",
    "BowingReport",
    "BowingValues",
    "autofocus_params_from_profile",
    "bowing_positions",
    "chuck_properties",
    "progress",
    "refocus_params_from_profile",
    "wafer_to_stage",
]

log = logging.getLogger(__name__)

INVALID_ARGUMENTS = 9002            # "invalid arguments error"
UNKNOWN_ERROR = 9001                # "unknown error"
AUTOFOCUS_ERROR = 9008              # "autofocus error" (an invalid LL autofocus result)
REFOCUS_ERROR = 9030                # "refocus error" (an invalid LL refocus result)
XY_TIMEOUT = 9402                   # "XY Timeout" (Wait Pos Zi)
BOWING_ERROR = 9500                 # "Bowing error" (sm d239, ALOX error constant)
PARALLELISM_ERROR = 9501            # "Parallelism error"
BOWING_PARALLELISM_ERROR = 9502     # "Bowing + Parallelism error"

#: The report field (and image name) of each row of ``Z Measures``.
POINT_FIELDS = ("zwafer2", "zwafer3", "zchuck3", "zwafer1", "zchuck1", "zwafer4", "zchuck4", "zwafer5", "zchuck5")
CHUCK_ROWS = frozenset({2, 4, 6, 8})
FAST_SKIPPED_ROWS = frozenset({2, 4, 5, 6, 7, 8})
NO_WAIT_ROWS = frozenset({4, 6, 8})            # LabVIEW: Move to Pos Zi → Autofocus Large (sm d49, d63, d77)
AXIS_A_ROW = 4                                  # ZC1 stored: Bowing A, Parallelism A (sm d94)
FAST_LAST_ROW = 3                               # Fast method (owner R1): ZW2, ZW3, ZW1 measured, then Bowing A
LAST_ROW = len(BOWING_ROWS) - 1
#: ``Chuck Calibration``: the wafer centre point (sm d25 case True).
CALIBRATION_CENTRE = (2.0, 2.0)
REPORT_NAME = "measure_bowing"
OVERLAY = "bowing"
GREEN, RED = (0, 255, 0), (255, 0, 0)

#: The states that work on one point (their ``ctx.set_state`` data names it).
_POINT_STATES = frozenset({"Move to Pos Zi", "Wait Pos Zi", "Autofocus Large", "Autofocus Fine", "Measure Bowing"})
#: LabVIEW error clusters of the devices and the formulas.
_LV_ERRORS = (DeviceError, AloxError, LookupError, OSError, TypeError, ValueError)


class BowState(StrEnum):
    """``alox_typedef_bowing_measurement_function_state``."""

    START = "Start"
    STARTED = "Started"
    MOVE_TO_POS = "Move to Pos Zi"
    WAIT_POS = "Wait Pos Zi"
    AUTOFOCUS_LARGE = "Autofocus Large"
    AUTOFOCUS_FINE = "Autofocus Fine"
    MEASURE_BOWING = "Measure Bowing"
    FINISHED = "Finished"
    ERROR = "Error"
    STOPPED = "Stopped"


class BowingError(AloxError):
    """A bowing error (9002 invalid parameters; 9500…9502 out of range; the code of a device or LL function)."""


class BowingFailed(BowingError):
    """The run reached ``Error``: ``state`` failed, ``status`` is the bowing status at the end, ``report`` the data."""

    def __init__(self, code: int, source: str, *, state: str, status: int, report: BowingReport) -> None:
        super().__init__(code, source)
        self.state = state
        self.status = status
        self.report = report


# ================================================================================================== geometry
def bowing_positions(wafer_size_mm: float, offset_mm: float, angle_deg: float, distance_mm: float, *,
                     centre: tuple[float, float] = (0.0, 0.0)) -> tuple[tuple[float, float], ...]:
    """The nine wafer points (mm) in ``Z Measures`` order (module docstring, sm d24)."""
    radius = wafer_size_mm / 2.0 + offset_mm
    theta = angle_deg / 180.0 * math.pi                     # "degrees to radians"
    c, s = math.cos(theta), math.sin(theta)
    xw, yw = distance_mm * c, distance_mm * s
    xc, yc = radius * c, radius * s
    return ((float(centre[0]), float(centre[1])), (xw, yw), (xc, yc), (-xw, -yw), (-xc, -yc), (xw, -yw), (xc, -yc),
            (-xw, yw), (-xc, yc))


def wafer_to_stage(point: Sequence[float], origin: Sequence[float], system: int) -> tuple[float, float]:
    """``alox_manager_xy_coordinate_system`` "Convert" Wafer → Stages (``xy-stage.md`` §9.2): systems 0…3 = #1…#4; X
    indirect for #2 and #4, Y indirect for #3 and #4 (``p = O − w``), else ``p = w + O``."""
    wx, wy = float(point[0]), float(point[1])
    ox, oy = float(origin[0]), float(origin[1])
    sx = ox - wx if system in (1, 3) else wx + ox
    sy = oy - wy if system in (2, 3) else wy + oy
    return sx, sy


def progress(measured: int) -> float:
    """``BOW Measured`` progress (%): measured points × 100 / 9."""
    return min(max(measured, 0), len(BOWING_ROWS)) * 100.0 / len(BOWING_ROWS)


# ================================================================================================== parameters
def _getter(reader: Any) -> Callable[[str, Any], Any]:
    def get(key: str, default: Any) -> Any:
        try:
            value = reader.get(key, default)
        except (RuntimeError, LookupError, TypeError, ValueError):      # no profile loaded
            return default
        return default if value is None else value
    return get


def _set(obj: object, name: str, value: Any) -> None:
    object.__setattr__(obj, name, value)


def _invalid(text: str) -> BowingError:
    return BowingError(INVALID_ARGUMENTS, text)


def _bool(value: Any, what: str) -> bool:
    if isinstance(value, bool | np.bool_):
        return bool(value)
    if isinstance(value, int | np.integer) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in ("true", "false", "1", "0"):
        return value.strip().lower() in ("true", "1")
    raise _invalid(f"{what}: not a boolean: {value!r}")


def _float(value: Any, what: str) -> float:
    if isinstance(value, bool):
        raise _invalid(f"{what}: not a number: {value!r}")
    try:
        return float(value)
    except (TypeError, ValueError):
        raise _invalid(f"{what}: not a number: {value!r}") from None


@dataclass(frozen=True)
class BowingParams:
    """``alox_typedef_bowing_measurement_parameters`` (cluster order); defaults = the bundle VI's (``…bundle_profile
    _parameters d``). ``chuck_calibration`` is LabVIEW's global ``Chuck Calibration`` (set by the properties panel's
    chuck calibration, never by the supervisor)."""

    camera_type: CameraType = CameraType.LOW_MAG
    measure_distance_mm: float = 0.0
    max_bowing_um: float = 0.0
    show_window: bool = False
    overlay_bowing: bool = False
    overlay_parallelism: bool = False
    fast_method: bool = False
    chuck_calibration: bool = False

    def __post_init__(self) -> None:
        try:
            _set(self, "camera_type", CameraType.coerce(self.camera_type, "Camera Type"))
        except FocusError as exc:
            raise _invalid(exc.source) from None
        for name in ("measure_distance_mm", "max_bowing_um"):
            _set(self, name, _float(getattr(self, name), name))
        for name in ("show_window", "overlay_bowing", "overlay_parallelism", "fast_method", "chuck_calibration"):
            _set(self, name, _bool(getattr(self, name), name))

    @classmethod
    def from_profile(cls, profile: Any, **overrides: Any) -> BowingParams:
        """The profile keys ``measure.bowing.*`` with the bundle VI's defaults, then ``overrides`` (field names)."""
        g = _getter(profile)
        values: dict[str, Any] = {
            "camera_type": g("measure.bowing.camera", 1), "measure_distance_mm": g("measure.bowing.distance", 0.0),
            "max_bowing_um": g("measure.bowing.maxbowing", 0.0), "show_window": g("measure.bowing.showwindow", False),
            "overlay_bowing": g("measure.bowing.overlaybowing", False),
            "overlay_parallelism": g("measure.bowing.overlayparallelism", False),
            "fast_method": g("measure.bowing.methodFast", False)}
        values.update(overrides)
        return cls(**values)

    def check(self) -> BowingParams:
        """Refuse what cannot measure anything (:class:`BowingError` 9002): Measure Distance must be > 0 and finite,
        Max Bowing finite."""
        if not (math.isfinite(self.measure_distance_mm) and self.measure_distance_mm > 0):
            raise _invalid(f"bowing: Measure Distance (mm) must be > 0, got {self.measure_distance_mm!r}")
        if not math.isfinite(self.max_bowing_um):
            raise _invalid(f"bowing: Max Bowing (µm) must be finite, got {self.max_bowing_um!r}")
        return self


def chuck_properties(settings: Any) -> ChuckProperties:
    """``…_bundle_chuck_properties d``: ``[hardware.chuck]`` dz1…dz5, maxparallelism, bowchuckA/B and
    ``general.wafersize`` (code defaults 0)."""
    g = _getter(settings)

    def number(key: str) -> float:
        return _float(g(key, 0.0), key)
    return ChuckProperties(
        wafer_diameter_mm=number("general.wafersize"), dz1=number("hardware.chuck.dz1"),
        dz2=number("hardware.chuck.dz2"), dz3=number("hardware.chuck.dz3"), dz4=number("hardware.chuck.dz4"),
        dz5=number("hardware.chuck.dz5"), max_parallelism_um=number("hardware.chuck.maxparallelism"),
        bow_chuck_a=number("hardware.chuck.bowchuckA"), bow_chuck_b=number("hardware.chuck.bowchuckB"))


def autofocus_params_from_profile(profile: Any, settings: Any, camera_type: CameraType) -> AutofocusParams:
    """The LL autofocus parameters (:meth:`AutofocusParams.from_profile`) with the bowing camera."""
    return AutofocusParams.from_profile(profile, settings, camera_type=camera_type)


def refocus_params_from_profile(profile: Any, camera_type: CameraType,
                                method: AutofocusMethod = AutofocusMethod.SOBEL) -> RefocusParams:
    """The LL refocus parameters (:meth:`RefocusParams.from_profile`) with the bowing camera and ``Autofocus Method``
    = Sobel (sm d92)."""
    return RefocusParams.from_profile(profile, camera_type=camera_type, method=method)


@dataclass(frozen=True)
class BowingOptions:
    """Server-side settings; defaults = the LabVIEW values."""

    move_attempts: int = 3                 # "Retry 3 times if error" (sm d24)
    autofocus_attempts: int = 3            # Autofocus Large: Error at the 3rd consecutive failure (sm d89)
    refocus_attempts: int = 5              # Autofocus Fine: Error at the 5th consecutive failure (sm d92)
    position_tolerance_mm: float = 0.01    # Wait Pos Zi window (sm d85)
    move_timeout_s: float = 30.0           # Wait Pos Zi timeout from the move (sm d85)
    settle_s: float = 0.1                  # "Make sure the stages has stopped moving" (sm d85)
    chuck_pause_s: float = 0.25            # parallel 250 ms wait of the chuck points (sm d37, d49, d63, d77)
    poll_interval_s: float = 0.01          # LabVIEW loop period (10 ms)
    ui_pause_s: float = 1.0                # Measure Bowing: "Let some time to the UI to refresh refocus data"
    finished_pause_s: float = 2.0          # Finished: "Let some time to show results in ui panel"
    error_pause_s: float = 5.0             # Error: wait before the wavelength reset (sm d251)
    wait_every_position: bool = True       # False: ZC1/ZC4/ZC5 skip Wait Pos Zi as LabVIEW

    def __post_init__(self) -> None:
        for name in ("move_attempts", "autofocus_attempts", "refocus_attempts"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"BowingOptions.{name} must be an int >= 1, got {value!r}")
        for name in ("position_tolerance_mm", "move_timeout_s", "settle_s", "chuck_pause_s", "poll_interval_s",
                     "ui_pause_s", "finished_pause_s", "error_pause_s"):
            value = getattr(self, name)
            if not (math.isfinite(value) and value >= 0):
                raise ValueError(f"BowingOptions.{name} must be finite and >= 0, got {value!r}")


# ================================================================================================== results
@dataclass(frozen=True)
class BowingValues:
    """``alox_typedef_bowing_measurement`` (``Bowing Measurement Report``), in cluster order: Z in mm, results in µm;
    the reset values of ``Start`` (NaN, F, 0)."""

    zwafer2: float = math.nan
    zwafer1: float = math.nan
    zwafer3: float = math.nan
    zwafer4: float = math.nan
    zwafer5: float = math.nan
    zchuck1: float = math.nan
    zchuck3: float = math.nan
    zchuck4: float = math.nan
    zchuck5: float = math.nan
    parallelism_a_um: float = math.nan
    bowing_a_um: float = math.nan
    parallelism_b_um: float = math.nan
    bowing_b_um: float = math.nan
    bowing_valid: bool = False
    parallelism_valid: bool = False
    parallelism_valid_a: bool = False
    parallelism_valid_b: bool = False
    bowing_valid_a: bool = False
    bowing_valid_b: bool = False
    dzchuck1: float = 0.0
    dzchuck3: float = 0.0
    dzchuck4: float = 0.0
    dzchuck5: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


@dataclass(frozen=True)
class BowingReport:
    """The outcome of a run (``Get Last Measure``): the SEMI job, the values, ``Z Measures`` (rows ``(x, y, z)``:
    wafer mm, Z mm, NaN when not measured), the wafer points, the initial wavelength (nm, None if unread), the data
    file and the images written, the bowing status at the end."""

    job: JobInfo
    values: BowingValues
    z_measures: tuple[tuple[float, float, float], ...]
    positions: tuple[tuple[float, float], ...]
    initial_wl: int | None = None
    data_file: Path | None = None
    images: tuple[Path, ...] = ()
    status: int = 0
    fast_method: bool = False

    @property
    def valid(self) -> bool:
        return self.values.bowing_valid and self.values.parallelism_valid


# ================================================================================================== the function
class BowingMeasurement(MeasurementFunction):
    """HL Bowing measurement (module docstring).

    ``run(params, ctx)`` accepts the core's :class:`alox.core.core.MeasureRequest` (the active profile's
    ``measure.bowing.*``, its ``overrides["x"]`` replacing the Measure Distance as ``alox_api_v1_put_functions_
    measure_bowing`` does), a :class:`BowingParams`, a mapping of :class:`BowingParams` fields over the profile, or
    None. Returns a :class:`BowingReport`; raises :class:`BowingError` 9002 for invalid parameters (no motion),
    :class:`BowingFailed` after ``Error`` and :class:`~alox.core.functions.Stopped` on stop/abort.

    The LL functions are called with ``ctx.run_once``: :attr:`FunctionName.AUTOFOCUS_LARGE`,
    :attr:`FunctionName.REFOCUS` and, when registered, :attr:`FunctionName.GET_IMAGE`; ``autofocus_params`` /
    ``refocus_params`` replace the builders of their parameters (``f(camera_type)``). ``sleep`` (tests) replaces
    ``ctx.sleep``; ``now`` gives the local time of the file names."""

    name = FunctionName.BOWING_MEASURE

    def __init__(self, services: Services, *, options: BowingOptions | None = None,
                 sleep: Callable[[float], None] | None = None,
                 autofocus_params: Callable[[CameraType], Any] | None = None,
                 refocus_params: Callable[[CameraType], Any] | None = None,
                 now: Callable[[], datetime] | None = None) -> None:
        if "xy" not in services.devices:
            raise LookupError("no XY stage")
        self.services = services
        self.options = options or BowingOptions()
        self._sleep = sleep
        self._autofocus_params = autofocus_params
        self._refocus_params = refocus_params
        self._now = now or (lambda: datetime.now(UTC).astimezone())
        self.last_report: BowingReport | None = None

    # ---------------------------------------------------------------------------------------------- parameters
    def params_for(self, request: Any) -> BowingParams:
        """:class:`BowingParams` of a request (class docstring)."""
        if isinstance(request, BowingParams):
            return request
        profile = self.services.profile
        if request is None:
            return BowingParams.from_profile(profile)
        overrides = getattr(request, "overrides", None)
        if overrides is not None:                                   # MeasureRequest
            extra = {}
            if overrides.get("x") is not None:
                extra["measure_distance_mm"] = _float(overrides["x"], "x")
            if overrides.get("chuck_calibration"):                 # the properties panel's Chuck Calibration
                extra["chuck_calibration"] = True
            return BowingParams.from_profile(profile, **extra)
        if isinstance(request, Mapping):
            return BowingParams.from_profile(profile, **dict(request))
        raise _invalid(f"bowing: unsupported parameters {type(request).__name__}")

    def autofocus_params(self, camera_type: CameraType) -> Any:
        if self._autofocus_params is not None:
            return self._autofocus_params(camera_type)
        return autofocus_params_from_profile(self.services.profile, self.services.settings, camera_type)

    def refocus_params(self, camera_type: CameraType) -> Any:
        if self._refocus_params is not None:
            return self._refocus_params(camera_type)
        return refocus_params_from_profile(self.services.profile, camera_type)

    # ---------------------------------------------------------------------------------------------- run
    def run(self, params: Any, ctx: FunctionContext) -> BowingReport:
        job = ctx.job if ctx.job is not None else getattr(params, "job", None) or JobInfo()
        try:
            bow = _BowingJob(self, self.params_for(params).check(), job, ctx)
        except BowingError:                                         # invalid parameters or settings: no motion
            self.services.set_status("bowing", int(BowingStatus.ERROR))
            raise
        try:
            report = bow.execute()
        except BowingFailed as exc:
            self.last_report = exc.report
            raise
        self.last_report = report
        return report


class _BowingJob:
    """One run: the per-run session (``alox_typedef_bowing_measurement_session``) and the states."""

    def __init__(self, function: BowingMeasurement, p: BowingParams, job: JobInfo, ctx: FunctionContext) -> None:
        self.f = function
        self.s = function.services
        self.o = function.options
        self.p = p
        self.job = job
        self.ctx = ctx
        self.get = _getter(self.s.settings)
        self.chuck = chuck_properties(self.s.settings)
        centre = CALIBRATION_CENTRE if p.chuck_calibration else (0.0, 0.0)
        self.positions = bowing_positions(_float(self.get("general.wafersize", 0.0), "general.wafersize"),
                                          _float(self.get("general.bowingOffset", 0.0), "general.bowingOffset"),
                                          _float(self.get("general.bowingAngle", 0.0), "general.bowingAngle"),
                                          p.measure_distance_mm, centre=centre)
        self.origin = (_float(self.get("hardware.xy.waferxorg", 0.0), "hardware.xy.waferxorg"),
                       _float(self.get("hardware.xy.waferyorg", 0.0), "hardware.xy.waferyorg"))
        self.system = int(_float(self.get("hardware.xy.stagescoordsys", 3), "hardware.xy.stagescoordsys"))
        if self.system not in (0, 1, 2, 3):
            raise _invalid(f"bowing: hardware.xy.stagescoordsys must be 0…3, got {self.system}")
        self.xy = self.s.devices["xy"]
        self.state = BowState.START
        self.rows: list[list[float]] = []               # Z Measures: [x, y, z] (wafer mm, Z mm)
        self.values = BowingValues()
        self.error_count = 0
        self.move_t = 0.0
        self.target: tuple[float, float] = (math.nan, math.nan)
        self.initial_wl: int | None = None
        self.measured = False                           # "Bowing Measured"
        self.folders: jobfiles.JobFolders | None = None
        self.images: list[Path] = []
        self.data_file: Path | None = None
        self.code = 0                                   # the last bowing status set
        self.error: BaseException | None = None
        self.failed_state = ""
        self.limits_before: bool | None = None

    # ---------------------------------------------------------------------------------------------- loop
    def execute(self) -> BowingReport:
        self.prepare()
        try:
            handlers: dict[BowState, Callable[[], BowState]] = {
                BowState.START: self.start, BowState.STARTED: self.started, BowState.MOVE_TO_POS: self.move_to_pos,
                BowState.WAIT_POS: self.wait_pos, BowState.AUTOFOCUS_LARGE: self.autofocus_large,
                BowState.AUTOFOCUS_FINE: self.autofocus_fine, BowState.MEASURE_BOWING: self.measure_bowing}
            state = BowState.START
            self.publish(state)
            while state not in (BowState.FINISHED, BowState.ERROR):
                self.ctx.check()                                    # stop / abort between states
                nxt = handlers[state]()
                if nxt is not state:
                    self.publish(nxt)
                state = nxt
            if state is BowState.FINISHED:
                return self.finished()
            raise self.error_state()
        except Stopped:
            self.stopped()
            raise
        finally:
            self.restore()

    def publish(self, state: BowState, **data: Any) -> None:
        self.state = state
        extra: dict[str, Any] = {"status": self.code, "measured": self.measured_points()}
        index = {BowState.MOVE_TO_POS: len(self.rows)}.get(state, len(self.rows) - 1)
        if state in _POINT_STATES and 0 <= index < len(BOWING_ROWS):
            extra.update(index=index, point=BOWING_ROWS[index])
        if state is BowState.ERROR and self.error is not None:
            code, source = _code_source(self.error)
            extra.update(code=code, error=source, failed_state=self.failed_state)
        self.ctx.set_state(str(state), **extra, **data)

    def event(self, name: str, **data: Any) -> None:
        """A run-panel event (``alox_manager_functions_ui_events``) as state data."""
        self.ctx.set_state(str(self.state), event=name, status=self.code, **data)

    def fail(self, exc: BaseException, state: BowState) -> BowState:
        self.error = exc
        self.failed_state = str(state)
        log.warning("bowing: %s failed: %s", state, exc)
        return BowState.ERROR

    def set_status(self, code: int) -> None:
        self.code = int(code)
        self.s.set_status("bowing", self.code)

    def app_log(self, level: str, text: str) -> None:
        getattr(log, level)(text)
        target = getattr(self.s.log, level, None) if self.s.log is not None else None
        if callable(target):
            try:
                target(text)
            except Exception:
                log.debug("application log failed", exc_info=True)

    def log_error(self, source: str, exc: BaseException) -> None:
        """``alox_manager_log`` "Log Error In" with its source text."""
        self.app_log("error", f"{source}: {exc}")

    def pause(self, seconds: float) -> None:
        """A pause that a stop interrupts (raises :class:`Stopped`)."""
        if seconds <= 0:
            return
        if self.f._sleep is None:
            self.ctx.sleep(seconds)
        else:
            self.ctx.check()
            self.f._sleep(seconds)

    def quiet_pause(self, seconds: float) -> None:
        """A pause of the end states: cut short by a stop, without raising."""
        if seconds <= 0:
            return
        if self.f._sleep is not None:
            self.f._sleep(seconds)
            return
        end = time.monotonic() + seconds
        while not self.ctx.stop_requested:
            left = end - time.monotonic()
            if left <= 0:
                return
            self.ctx.stop_event.wait(min(left, 0.05))

    @property
    def camera(self) -> str:
        return camera_device_name(int(self.p.camera_type))

    def index(self) -> int:
        return len(self.rows) - 1

    def measured_points(self) -> int:
        """Points whose Z is stored."""
        return sum(1 for row in self.rows if math.isfinite(row[2]))

    # ---------------------------------------------------------------------------------------------- around the loop
    def prepare(self) -> None:
        """sm d1, d2, d3: Z and XY joysticks off, XY wafer-limit supervision off (its value is restored after)."""
        z = self.s.devices.get("z")
        if z is not None:
            try:
                z.set_joystick(False)
            except _LV_ERRORS as exc:
                self.log_error("Bowing (Z Disable Joystick)", exc)
        try:
            self.xy.set_joystick(False)
        except _LV_ERRORS as exc:
            self.log_error("Bowing (XY Disable Joystick)", exc)
        wafer = getattr(self.xy, "wafer", None)
        if wafer is not None and hasattr(self.xy, "set_wafer"):
            self.limits_before = bool(wafer.limits_enabled)
            try:
                self.xy.set_wafer(limits_enabled=False)
            except _LV_ERRORS as exc:
                self.log_error("Bowing (Set Wafer Limit)", exc)
                self.limits_before = None

    def restore(self) -> None:
        """sm d263: ``Set Wafer Limit`` back to the value it had."""
        if self.limits_before is None:
            return
        try:
            self.xy.set_wafer(limits_enabled=self.limits_before)
        except _LV_ERRORS as exc:
            self.log_error("Bowing (Set Wafer Limit)", exc)

    # ---------------------------------------------------------------------------------------------- Start, Started
    def start(self) -> BowState:
        """"Init state machine here" (sm d6…d22)."""
        self.set_status(BowingStatus.INITIALIZING)                  # 201 "Init bowing function"
        self.values = BowingValues()                                # "Reset Report"
        self.event("BOW Started", values=self.values)
        nxt = BowState.STARTED
        try:                                                        # "Reset Wavelength"
            wl = self.s.device("wl")
            self.initial_wl = int(wl.position())
            wl.goto(0 if self.p.camera_type is CameraType.HIGH_MAG else self.initial_wl)
        except (*_LV_ERRORS, LookupError) as exc:
            if self.p.camera_type is CameraType.LOW_MAG:            # owner R7: Low Mag needs no monochromator
                log.warning("bowing (Low Mag): monochromator not available, wavelength left as is: %s", exc)
            else:
                self.set_status(BowingStatus.ERROR_RESET_WAVELENGTH)    # 1001
                self.log_error("Bowing Measurement (Reset Wavelength)", exc)
                nxt = self.fail(exc, BowState.START)
        try:                                                        # "Set XY high speed"
            self.xy.set_speed(_float(self.get("hardware.xy.speed", 10.0), "hardware.xy.speed"))
        except _LV_ERRORS as exc:
            self.log_error("Bowing (Set Speed)", exc)
        if self.s.folders is not None:                              # job-history folders, errors cleared
            try:
                self.folders = jobfiles.job_folders(self.s.folders, self.job, images="bowing", files=("bowing",))
            except OSError as exc:
                log.warning("bowing: job folders: %s", exc)
        return nxt

    def started(self) -> BowState:
        self.set_status(BowingStatus.INITIALIZED)                   # 202 "bowing inited"
        return BowState.MOVE_TO_POS

    # ---------------------------------------------------------------------------------------------- moves
    def move_to_pos(self) -> BowState:
        """"Move to Z2, Z3, Z1, Z4, Z5 positions" (sm d24…d84)."""
        i = len(self.rows)
        if i >= len(BOWING_ROWS):                                   # case "9, Default"
            return self.fail(BowingError(UNKNOWN_ERROR, "bowing: no position left to measure (the Fast method "
                                                        "skips the points measured last)"), BowState.MOVE_TO_POS)
        self.set_status(BowingStatus.MOVE_ZW2 + i)
        point = self.positions[i]
        chuck = i in CHUCK_ROWS
        if self.p.fast_method and i in FAST_SKIPPED_ROWS:           # case True: no move, NaN stored
            self.rows.append([point[0], point[1], math.nan])
            self.error_count = 0
            self.move_t = self.s.clock()
            if chuck:
                self.pause(self.o.chuck_pause_s)
            return BowState.MOVE_TO_POS
        error: BaseException | None = None
        try:
            self.target = wafer_to_stage(point, self.origin, self.system)
            self.xy.move_absolute(*self.target, wait=False)        # "Move Absolute XY" (not waited)
        except _LV_ERRORS as exc:
            error = exc
        self.move_t = self.s.clock()                                # "Last Move Timestamp"
        if chuck:
            self.pause(self.o.chuck_pause_s)
        if error is not None:
            self.error_count += 1
            if self.error_count < self.o.move_attempts:            # "Retry 3 times if error"
                log.info("bowing: move to %s failed (%s), attempt %d", BOWING_ROWS[i], error, self.error_count)
                return BowState.MOVE_TO_POS
            self.log_error(f"Bowing (Move to {POINT_FIELDS[i].capitalize()})", error)
            self.set_status(BowingStatus.ERROR_MOVE_ZW2 + i)       # 1002 + i
            return self.fail(error, BowState.MOVE_TO_POS)
        self.rows.append([point[0], point[1], math.nan])           # "Store Zi position"
        self.error_count = 0
        if i in NO_WAIT_ROWS and not self.o.wait_every_position:
            return BowState.AUTOFOCUS_LARGE
        return BowState.WAIT_POS

    def wait_pos(self) -> BowState:
        """"Wait end of movement for last stored position (until position reached or timeout)" (sm d85)."""
        i = self.index()
        self.error_count = 0
        if self.s.clock() - self.move_t > self.o.move_timeout_s:  # "XY stages timeout ?"
            self.set_status(BowingStatus.ERROR_MOVE_ZW2 + i)
            return self.fail(BowingError(XY_TIMEOUT, f"bowing: XY Timeout, {BOWING_ROWS[i]} not reached within "
                                                     f"{self.o.move_timeout_s:g} s"), BowState.WAIT_POS)
        self.set_status(BowingStatus.WAIT_MOVE_ZW2 + i)
        try:
            x, y = self.xy.position()
        except _LV_ERRORS as exc:                                   # "status" TRUE: keep waiting
            log.debug("bowing: Get Position XY failed: %s", exc)
            self.pause(self.o.poll_interval_s)
            return BowState.WAIT_POS
        tol = self.o.position_tolerance_mm
        tx, ty = self.target
        if tx - tol <= x <= tx + tol and ty - tol <= y <= ty + tol:
            self.pause(self.o.settle_s)                             # "Make sure the stages has stopped moving"
            return BowState.AUTOFOCUS_LARGE
        self.pause(self.o.poll_interval_s)
        return BowState.WAIT_POS

    # ---------------------------------------------------------------------------------------------- focus
    def run_once(self, name: FunctionName, params: Any) -> Any:
        return self.ctx.run_once(name, params)

    def get_image(self) -> None:
        """Get Image Run Once (``Update Display``), when Get Image is registered."""
        try:
            self.ctx.runner.function(FunctionName.GET_IMAGE)
        except KeyError:
            return
        self.run_once(FunctionName.GET_IMAGE, int(self.p.camera_type))

    def autofocus_large(self) -> BowState:
        """"'Autofocus large' low level function" (sm d89)."""
        i = self.index()
        self.set_status(BowingStatus.AUTOFOCUS_ZW2 + i)
        error: BaseException | None = None
        result: Any = None
        try:
            result = self.run_once(FunctionName.AUTOFOCUS_LARGE, self.f.autofocus_params(self.p.camera_type))
            self.get_image()
        except Stopped:
            raise
        except Exception as exc:                    # noqa: BLE001 - an LL failure is its error cluster: retried
            error = exc
        self.event("BOW Autofocus", result=result)
        if error is None and not _valid(result):
            error = BowingError(AUTOFOCUS_ERROR, "autofocus error (no valid focus position)")
        if error is None:
            self.error_count = 0
            return BowState.AUTOFOCUS_FINE
        self.log_error("Bowing Measurement (Autofocus)", error)
        self.error_count += 1
        if self.error_count < self.o.autofocus_attempts:
            return BowState.AUTOFOCUS_LARGE
        self.set_status(BowingStatus.ERROR_AUTOFOCUS_ZW2 + i)      # 1011 + i
        return self.fail(error, BowState.AUTOFOCUS_LARGE)

    def autofocus_fine(self) -> BowState:
        """"'Autofocus fine' low level function" (sm d92): the refocus, Z stored."""
        i = self.index()
        self.set_status(BowingStatus.REFOCUS_ZW2 + i)
        error: BaseException | None = None
        result: Any = None
        try:
            self.get_image()                                        # "Get Image" before the refocus
            result = self.run_once(FunctionName.REFOCUS, self.f.refocus_params(self.p.camera_type))
            self.get_image()                                        # "Get refocused image"
        except Stopped:
            raise
        except Exception as exc:                    # noqa: BLE001 - an LL failure is its error cluster: retried
            error = exc
        self.event("BOW Refocus", result=result)
        best = getattr(result, "best_z", None)
        if best is not None:
            self.rows[i][2] = float(best)                           # "Store Zi focus position"
        if error is None and not _valid(result):
            error = BowingError(REFOCUS_ERROR, "refocus error (no valid focus position)")
        if error is None:
            self.error_count = 0
            return BowState.MEASURE_BOWING
        self.log_error("Bowing Measurement (Refocus)", error)
        self.error_count += 1
        if self.error_count < self.o.refocus_attempts:
            return BowState.AUTOFOCUS_FINE
        self.set_status(BowingStatus.ERROR_REFOCUS_ZW2 + i)        # 1020 + i
        return self.fail(error, BowState.AUTOFOCUS_FINE)

    # ---------------------------------------------------------------------------------------------- Measure Bowing
    def z_values(self) -> list[float]:
        return [row[2] for row in self.rows] + [math.nan] * (len(BOWING_ROWS) - len(self.rows))

    def measure_bowing(self) -> BowState:
        """"Measure Bowing & Parallelism" (sm d93…d190)."""
        i = self.index()
        self.pause(self.o.ui_pause_s)                               # "Let some time to the UI to refresh …"
        self.set_status(BowingStatus.STORE_ZW2 + i)                 # 239 + i
        z = self.z_values()
        changes: dict[str, Any] = {POINT_FIELDS[k]: z[k] for k in range(i + 1)}
        nxt = BowState.MOVE_TO_POS
        if self.p.fast_method and i == FAST_LAST_ROW:               # owner R1: the Fast method ends here
            r = self.compute(z)
            changes.update(bowing_a_um=r.bow_a_um, bowing_valid_a=r.bow_valid_a, bowing_valid=r.bow_valid_a,
                           parallelism_valid=True)                  # parallelism not measured: NaN, not an error
            self.measured = True
            self.values = replace(self.values, **changes)
            self.display()
            nxt = BowState.FINISHED if self.values.bowing_valid or self.p.chuck_calibration else BowState.ERROR
            if nxt is BowState.ERROR:
                self.error = self.validity_error()
                self.failed_state = str(BowState.MEASURE_BOWING)
            self.event("BOW Measured", values=self.values, measured=i + 1, progress=100.0)
            self.save_image(i)
            return nxt
        if i == AXIS_A_ROW:                                         # case 5 "Zchuck1": axis A
            r = self.compute(z)
            changes.update(bowing_a_um=r.bow_a_um, parallelism_a_um=r.para_a_um, bowing_valid_a=r.bow_valid_a,
                           parallelism_valid_a=r.para_valid_a, dzchuck1=self.chuck.dz1)
        elif i == LAST_ROW:                                         # case "9..": everything
            r = self.compute(z)
            changes.update(bowing_a_um=r.bow_a_um, bowing_b_um=r.bow_b_um, bowing_valid=r.bow_valid,
                           bowing_valid_a=r.bow_valid_a, bowing_valid_b=r.bow_valid_b, parallelism_a_um=r.para_a_um,
                           parallelism_b_um=r.para_b_um, parallelism_valid=r.para_valid,
                           parallelism_valid_a=r.para_valid_a, parallelism_valid_b=r.para_valid_b,
                           dzchuck1=self.chuck.dz1, dzchuck3=self.chuck.dz3, dzchuck4=self.chuck.dz4,
                           dzchuck5=self.chuck.dz5)
            self.measured = True
        self.values = replace(self.values, **changes)
        if i == LAST_ROW:
            self.display()
            ok = self.values.bowing_valid and self.values.parallelism_valid
            nxt = BowState.FINISHED if ok or self.p.chuck_calibration else BowState.ERROR
            if nxt is BowState.ERROR:
                self.error = self.validity_error()
                self.failed_state = str(BowState.MEASURE_BOWING)
        self.event("BOW Measured", values=self.values, measured=i + 1, progress=progress(i + 1))
        self.save_image(i)
        return nxt

    def compute(self, z: Sequence[float]) -> Any:
        return compute_bowing(z, measure_distance_mm=self.p.measure_distance_mm, max_bowing_um=self.p.max_bowing_um,
                              chuck=self.chuck)

    def overlay_text(self) -> str:
        v = self.values
        if self.p.chuck_calibration:
            deltas = (v.zwafer3 - v.zchuck3, v.zwafer1 - v.zchuck1, v.zwafer4 - v.zchuck4, v.zwafer5 - v.zchuck5)
            return ("\nChuck calibrated\nDelta Chuck 3: {} µm\nDelta Chuck 1: {} µm\nDelta Chuck 4: {} µm"
                    "\nDelta Chuck 5: {} µm").format(*(jobfiles.format_lv_number(d, "%f") for d in deltas))
        return "\nBowing A: {} µm\nBowing B: {} µm\nParallelism A: {} µm\nParallelism B: {} µm".format(
            *(_lv_int(x) for x in (v.bowing_a_um, v.bowing_b_um, v.parallelism_a_um, v.parallelism_b_um)))

    def display(self) -> None:
        """"Refresh Display" (sm d177): a new image with the results as text (green when valid, else red)."""
        ok = self.p.chuck_calibration or (self.values.bowing_valid and self.values.parallelism_valid)
        size = int(self.get("overlays.fontsize", 50))
        self.s.images.set_overlay(OVERLAY, [Text(50.0, 50.0, self.overlay_text(), GREEN if ok else RED, size)])
        try:
            self.get_image()
        except Stopped:
            raise
        except Exception as exc:                    # noqa: BLE001 - the display image is not part of the result
            self.log_error("Bowing (Display)", exc)

    def validity_error(self) -> BowingError:
        """The detailed error of an out-of-range result (sm d239…d250)."""
        v = self.values
        if not v.bowing_valid:
            if v.parallelism_valid:
                return BowingError(BOWING_ERROR, "Bowing error (bowing out of range)")
            return BowingError(BOWING_PARALLELISM_ERROR, "Bowing + Parallelism error (out of range)")
        return BowingError(PARALLELISM_ERROR, "Parallelism error (out of range)")

    def validity_status(self) -> int | None:
        v = self.values
        if not v.bowing_valid:
            return BowingStatus.ERROR_BOWING_RANGE if v.parallelism_valid else \
                BowingStatus.ERROR_BOWING_AND_PARALLELISM_RANGE
        if v.parallelism_valid:
            return None
        if v.parallelism_valid_a:
            return BowingStatus.ERROR_PARALLELISM_B_RANGE
        if v.parallelism_valid_b:
            return BowingStatus.ERROR_PARALLELISM_A_RANGE
        return BowingStatus.ERROR_PARALLELISM_RANGE

    def save_image(self, i: int) -> None:
        """``log.savebowingimages`` (T): the last image of the camera as ``<name>.png`` in ``images\\bowing``."""
        if not _truthy(self.get("log.savebowingimages", True)) or self.folders is None or self.folders.images is None:
            return
        pixels = self.s.images.last(self.camera)
        if pixels is None:
            log.info("bowing: no %s image to save for %s", self.camera, BOWING_ROWS[i])
            return
        name = jobfiles.job_data_filename(self.job, self.f._now(), f"{jobfiles.TIME_FORMAT}_bowing_{POINT_FIELDS[i]}",
                                          kind="bowing")
        path = self.folders.images / f"{name}.png"
        try:
            write_png(path, np.asarray(pixels))
        except (OSError, ValueError, PngError) as exc:
            self.log_error("Bowing (Log Image)", exc)
            return
        self.images.append(path)

    # ---------------------------------------------------------------------------------------------- end states
    def data_rows(self) -> list[list[float]]:
        """``Z Measures`` and the row ``[Bowing A, Bowing B, Parallelism A, Parallelism B]`` (sm d198)."""
        v = self.values
        return [list(row) for row in self.rows] + [[v.bowing_a_um, v.bowing_b_um, v.parallelism_a_um,
                                                    v.parallelism_b_um]]

    def go_home(self) -> None:
        """XY back to the wafer centre and Z to Zwafer2, neither waited (sm d194, d195; d222, d225)."""
        try:
            self.xy.move_absolute(*wafer_to_stage((0.0, 0.0), self.origin, self.system), wait=False)
        except _LV_ERRORS as exc:
            self.log_error("Bowing (Move to Wafer center)", exc)
        z = self.s.devices.get("z")
        if z is None or not math.isfinite(self.values.zwafer2):
            return
        try:
            z.move_absolute(self.values.zwafer2, wait=False)
        except _LV_ERRORS as exc:
            self.log_error("Bowing (Move to Z2)", exc)

    def wavelength_back(self) -> None:
        """The monochromator to the profile's ``wavelength.wlmanual`` (0)."""
        wl = self.s.devices.get("wl")
        if wl is None:
            return
        try:
            wl.goto(int(_getter(self.s.profile)("wavelength.wlmanual", 0)))
        except _LV_ERRORS as exc:
            self.log_error("Bowing (Set Wavelength)", exc)

    def finished(self) -> BowingReport:
        """"Bowing measure finished" (sm d191…d215)."""
        self.set_status(BowingStatus.EXITING)                       # 300
        self.event("BOW Finished", values=self.values)
        self.go_home()
        self.set_status(BowingStatus.DATA_LOGGING)                  # 248
        v = self.values
        if self.s.publish is not None:                              # "Send 'bowing report' to supervisor"
            self.s.publish(reports.bowing_event(
                self.job, zwafer=[v.zwafer1, v.zwafer2, v.zwafer3, v.zwafer4, v.zwafer5], bowing_a=v.bowing_a_um,
                bowing_b=v.bowing_b_um, zchuck=[v.zchuck1, v.zchuck3, v.zchuck4, v.zchuck5],
                parallelism_a=v.parallelism_a_um, parallelism_b=v.parallelism_b_um))
        self.save_data(error=False)
        self.set_status(BowingStatus.FINISHED)                      # 500
        self.quiet_pause(self.o.finished_pause_s)
        self.wavelength_back()
        return self.report()

    def save_data(self, *, error: bool) -> None:
        """The ``.bow`` file (sm d198; Error: sm d227) or, while ``Chuck Calibration``, the ``.chuck`` file."""
        when = self.f._now()
        try:
            if self.p.chuck_calibration and not error:
                folder = Path(self.s.folders.chuck_calibration) if self.s.folders is not None else None
                name = "ChuckCalibration" + jobfiles.job_data_filename(self.job, when, kind="bowing") + ".chuck"
            else:
                folder = self.folders.file_folder("bowing") if self.folders is not None else None
                name = (jobfiles.format_timestamp(when) if error else
                        jobfiles.job_data_filename(self.job, when, kind="bowing")) + ".bow"
            if folder is None:
                return
            folder.mkdir(parents=True, exist_ok=True)
            self.data_file = jobfiles.write_spreadsheet(folder / name, self.data_rows(), "%.4f")
        except (OSError, ValueError) as exc:
            self.log_error("BOWING (Log data)", exc)

    def error_state(self) -> BowingFailed:
        """"Function Error" (sm d216…d253); returns the exception to raise."""
        if self.measured:
            self.set_status(BowingStatus.EXITING)                   # 300
        exc = self.error or BowingError(UNKNOWN_ERROR, "bowing: unknown error")
        code, source = _code_source(exc)
        if self.s.publish is not None:                              # "send 'error' event (websocket)"
            self.s.publish(reports.error_event(REPORT_NAME, code, source))
        self.event("BOW Finished", values=self.values)
        self.go_home()
        if self.measured:
            self.set_status(BowingStatus.DATA_LOGGING)              # 248
        if _truthy(self.get("log.savebowingdata", True)):
            self.save_data(error=True)
        if self.measured:
            status = self.validity_status()
            if status is not None:
                self.set_status(status)
        self.log_error("Bowing (Error)", exc)
        self.quiet_pause(self.o.error_pause_s)
        self.wavelength_back()
        return BowingFailed(code, source, state=self.failed_state, status=self.code, report=self.report())

    def stopped(self) -> None:
        """"Function Stopped/Aborted" (sm d254…d262)."""
        self.state = BowState.STOPPED
        self.wavelength_back()
        self.set_status(BowingStatus.STOPPED)                       # 850
        self.publish(BowState.STOPPED)
        self.event("BOW Finished", values=self.values)
        if self.s.publish is not None:
            self.s.publish(reports.function_event("failed", REPORT_NAME, self.job))

    def report(self) -> BowingReport:
        return BowingReport(job=self.job, values=self.values,
                            z_measures=tuple((r[0], r[1], r[2]) for r in self.rows), positions=self.positions,
                            initial_wl=self.initial_wl, data_file=self.data_file, images=tuple(self.images),
                            status=self.code, fast_method=self.p.fast_method)


# ================================================================================================== helpers
def _valid(result: Any) -> bool:
    """The LL report's ``Valid ?``: its ``valid`` attribute when it has one, else a finite ``best_z``."""
    if result is None:
        return False
    valid = getattr(result, "valid", None)
    if valid is not None:
        return bool(valid)
    best = getattr(result, "best_z", None)
    return best is not None and math.isfinite(float(best))


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return bool(value)


def _lv_int(value: float) -> str:
    """``%d`` of a DBL: rounded to the nearest (ties to even)."""
    return jobfiles.format_lv_number(value, "%.0f") if not math.isfinite(value) else str(round(value))


def _code_source(exc: BaseException) -> tuple[int, str]:
    if isinstance(exc, DeviceError):
        return (int(exc.code) if exc.code is not None else UNKNOWN_ERROR), str(exc)
    if isinstance(exc, AloxError):
        return exc.code, exc.source
    cluster = cluster_of(exc)
    return cluster.code, cluster.source
