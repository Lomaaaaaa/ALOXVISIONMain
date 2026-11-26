"""Autofocus ("large") and refocus ("fine") as measurement functions of the integrated application.

Spec: ``docs/06-autofocus-refocus.md`` (section and defect numbers below refer to it unless stated), the metrics of
``docs/07-focus-metrics.md`` (:mod:`alox.vision.focus`), the decisions of ``docs/10-migration-plan.md`` §6 and
``docs/answers/owner-answers.md`` §7, and ``docs/vision-core/migration-plan.md`` §6.

In the camera server each procedure is a LabVIEW sub-VI executing **one state per call**, re-posted through the camera
queue until it reports ``Finished`` (§1.2, §3). Here the procedures run in-process on the devices: :meth:`run`
executes the same state machine in the function runner's thread, one state per loop iteration, with
``ctx.check()`` between iterations (stop/abort) and ``ctx.set_state`` on every state change and after every
measurement. The persistent shift registers become per-run objects (no state shared between runs or cameras, D4).

Device mapping (§7.1): ``POS?`` → :meth:`ZStage.position`, ``VEL?`` → :meth:`ZStage.speed`, ``VEL`` →
:meth:`ZStage.set_speed`, ``MOV`` → ``ZStage.move_absolute(z, wait=False)`` (the device also checks the software
range and ``ERR?``: a refused move is an error at once, vision-core plan §6), ``JON? 1`` →
:meth:`ZStage.joystick_enabled`, ``IMAQdx Get Image2`` + flip + flat field → :meth:`Camera.grab`.

Reproduced (LabVIEW): states, transitions and their order; the ±1 µm windows (AF inclusive, RF upper bound excluded);
the 100 ms AF settle, ``Wait (ms)`` after each RF position; the 30 s AF start timeout and the 5 s RF per-move timeout
(9401 "z stage get position timeout"); 9031 "'refocus' warning (joystick enabled)"; the Slow positions by repeated
addition and the final over-travel ``MOV`` (Q22); the Fast Z estimate ``Z Min + dZ·((ti − T0)/Duration)`` (D16);
the progress values (§2.7, D2); score 0 rows after a failed measurement (D15); the refocus strict argmax, clamping and
zero-padded table; the method dispatch (AF: anything but FFT/Prewitt runs Sobel; RF: 7 methods, fixed scale 0.5);
the non-centred Low Mag Fin ROI (D12, Q21); the refocus PNG names (Q26).

Fixed (internal defects the client could not observe, or owner decisions):

- D1: the ``Error`` state runs (initial speed and initial Z restored); the result/exception keeps the progress and the
  data of the failed step. A stop or an abort runs the same restore (§9.5 ``abort``, §9.6).
- D6 / Q22: a timeout (``af_position_timeout_s``, 9401) also in AF Slow ``Goto Z Pos`` and AF Fast ``Wait Started``.
- D8: invalid parameters raise :class:`aloxcam.errors.FocusError` 9504 before any motion (the TCP ACK switch
  ``focus_validate_params`` no longer applies, vision-core plan §6).
- D9, D11 / Q20: only values actually read are restored (no ``VEL 0`` / ``MOV 0.0``).
- D10, D19, D20, D21, D23: no ``error.source`` overwrite, no command history, the Z device's lock, a monotonic clock,
  no settle pause after a timeout.
- D13: a failed ``POS?`` counts as "not reached" / "not left Z Min" (timeouts still apply).
- D14: works with simulated devices.
- D18 / Q27: the metric formulas as in 07, FULL computed sequentially (no race).

Choices of this implementation (documented deviations):

- **Latency compensation** (vision-core plan §6, D16): the Fast Z estimate was tuned on the camera server, whose
  post-grab timestamp included IMAQdx latencies that differ in-process. ``FocusOptions.fast_latency_s`` (default 0 =
  the LabVIEW formula) is subtracted from the post-grab timestamp ``ti``:
  ``Zest = Z Min + dZ·((ti − latency − T0)/Duration)``. It must be re-validated on the machine (≈ exposure/2 +
  transfer and processing time). The end condition ``ti − T0 ≥ Duration`` is unchanged; the progress follows the
  compensated Z, so with a compensation the last point may report slightly less than 100.
- **Frame** (D16): :meth:`Camera.grab` with ``newest=True`` (the newest frame not yet returned) instead of IMAQdx
  "Next", which can return a frame that waited in the 5-buffer ring since before the stage settled.
  ``FocusOptions.newest_frame = False`` reproduces "Next".
- The Slow AF final over-travel ``MOV`` (one step beyond ``Z Max``) is skipped, with a warning, when it is outside the
  stage's software limits: the device refuses such moves, and LabVIEW never failed a completed scan for it.
- Autofocus ``best_z`` is the strict argmax of the raw scores (Vision Core "Curve Maximum"). **Owner's decision B9**:
  at the end the stage moves to it (at the restored speed, waited for); LabVIEW left it where the scan ended (Q22).
  ``FocusOptions.af_move_to_best = False`` gives LabVIEW's behaviour back.
- Refocus PNG: pixels as written by :func:`aloxcam.imaging.png.write_png`; NI's "Vision Info" overlay block (the ROI)
  is not written (§8.3, D17).
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import IntEnum, StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

import numpy as np

from alox.core.functions import FunctionContext, FunctionName, MeasurementFunction
from alox.devices.base import DeviceError
from alox.vision.focus import NiCompatMetrics, autofocus_metric_method, fin_roi
from aloxcam.errors import TEXT, AloxError, Code, FocusError, cluster_of
from aloxcam.imaging.png import write_png

if TYPE_CHECKING:
    from alox.devices.camera import Camera
    from alox.devices.zstage import ZStage

__all__ = [
    "AFState",
    "Autofocus",
    "AutofocusMethod",
    "AutofocusParams",
    "AutofocusSpeed",
    "CameraType",
    "FocusFailed",
    "FocusOptions",
    "FocusResult",
    "RFState",
    "Refocus",
    "RefocusMethod",
    "RefocusParams",
    "refocus_image_name",
]

log = logging.getLogger(__name__)

#: LabVIEW error clusters that the state machines chain, store and turn into the ``Error`` state (§1.3, §4.8).
_LV_ERRORS = (DeviceError, AloxError)


# ================================================================================================== enums (§2.4)
class _Labelled(IntEnum):
    """An enum whose wire code is its value (owner answers §1) and whose LabVIEW item text is :attr:`label`."""

    @property
    def label(self) -> str:
        return self._labels()[self.value]

    @classmethod
    def _labels(cls) -> tuple[str, ...]:
        raise NotImplementedError

    @classmethod
    def coerce(cls, value: Any, what: str) -> Self:
        """A member, its code, its label (``"High Mag."``) or its name (``"high_mag"``); else :class:`FocusError`
        9504 (an invalid enum value is malformed input, 10 §6 carve-out (a))."""
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            text = value.strip()
            for member in cls:
                if text in (member.label, member.name) or text.upper() == member.name:     # type: ignore[attr-defined]
                    return member
        elif isinstance(value, (int, np.integer)) and not isinstance(value, bool):
            try:
                return cls(int(value))
            except ValueError:
                pass
        raise FocusError(Code.FOCUS_INVALID_PARAMS, f"{what}: invalid value {value!r}")


class CameraType(_Labelled):
    """``alox_typedef_camera_type.ctl``."""

    HIGH_MAG = 0
    LOW_MAG = 1

    @classmethod
    def _labels(cls) -> tuple[str, ...]:
        return ("High Mag.", "Low Mag.")


class AutofocusSpeed(_Labelled):
    """``alox_typedef_autofocus_speed.ctl``."""

    SLOW = 0
    FAST = 1

    @classmethod
    def _labels(cls) -> tuple[str, ...]:
        return ("Slow", "Fast")


class AutofocusMethod(_Labelled):
    """``alox_typedef_autofocus_method.ctl`` (07 §1.1 for what each computes)."""

    FFT = 0
    SOBEL = 1
    PREWITT = 2
    DIFF = 3
    GRADIENT = 4
    SIGMA = 5
    FULL = 6

    @classmethod
    def _labels(cls) -> tuple[str, ...]:
        return ("FFT", "Sobel", "Prewitt", "Diff", "Gradient", "Sigma", "FULL")


class RefocusMethod(_Labelled):
    """``alox_typedef_refocus_method.ctl``: ignored, the VI's selector is a constant ``Fin`` (D7, owner Q19)."""

    FIN = 0
    OXYD = 1

    @classmethod
    def _labels(cls) -> tuple[str, ...]:
        return ("Fin", "Oxyd")


class AFState(StrEnum):
    """``typedef_ext_camera_autofocus_state.ctl`` (published by ``ctx.set_state``)."""

    START = "Start"
    GOTO_Z_POS = "Goto Z Pos"
    WAIT_STARTED = "Wait Started"
    MEASURE = "Measure"
    FINISHED = "Finished"
    ERROR = "Error"


class RFState(StrEnum):
    """``typedef_ext_camera_refocus_state.ctl`` (original spelling ``Mesure Position``)."""

    START = "Start"
    GET_POSITIONS = "Get Positions"
    MOVE_POSITION = "Move Position"
    CHECK_POSITION = "Check Position"
    MESURE_POSITION = "Mesure Position"
    GET_FOCUS = "Get Focus"
    MOVE_FOCUS = "Move Focus"
    WAIT_FOCUS = "Wait Focus"
    FINISHED = "Finished"
    ERROR = "Error"


# ================================================================================================== parameters
def _set(obj: object, name: str, value: Any) -> None:
    object.__setattr__(obj, name, value)


def _profile_value(store: Any, dotted: str, default: Any) -> Any:
    """``store.get(dotted, default)`` (a recipe or the settings); ``default`` when there is no store or no value."""
    if store is None:
        return default
    value = store.get(dotted, default)
    return default if value is None else value


@dataclass(frozen=True)
class AutofocusParams:
    """``Camera Autofocus Data`` (§2.2), in cluster order; defaults = the typedef defaults (which are invalid: a start
    with them is refused, D8). Enum fields accept members, codes or labels."""

    camera_type: CameraType = CameraType.HIGH_MAG
    speed: AutofocusSpeed = AutofocusSpeed.SLOW           # Slow: move, settle, grab; Fast: continuous move
    method: AutofocusMethod = AutofocusMethod.FFT         # anything but FFT / Prewitt runs Sobel (§4.5)
    duration_s: int = 0                                   # Fast: scan duration in whole seconds
    z_min: float = 0.0                                    # mm, absolute scan start
    z_max: float = 0.0                                    # mm, absolute scan end
    step: float = 0.0                                     # mm, Slow step
    subsampling: float = 0.0                              # metric Scale Factor
    roi: tuple[int, ...] = ()                             # [Left, Top, Right, Bottom]; empty = whole image
    acquisition_timeout_ms: int = 5000                    # not used (D7)

    @classmethod
    def from_profile(cls, profile: Any, settings: Any = None, *,
                     camera_type: CameraType | int | str = CameraType.HIGH_MAG) -> AutofocusParams:
        """``alox_function_ll_autofocus_bundle_profile_parameters d``: ``autofocus.method`` (0), ``speed`` (0),
        ``moveduration`` (10 s), ``step`` (0), ``subsampling`` (0.25) of the recipe; the scan range is the
        application's ``hardware.z.zminautofocus`` / ``zmaxautofocus`` (``settings``; defaults = the shipped
        ``alox_default.ini`` 33.0 / 34.5 mm), as the LL ``Start`` reads it."""
        return cls(camera_type=camera_type, speed=int(_profile_value(profile, "autofocus.speed", 0)),
                   method=int(_profile_value(profile, "autofocus.method", 0)),
                   duration_s=int(_profile_value(profile, "autofocus.moveduration", 10)),
                   z_min=float(_profile_value(settings, "hardware.z.zminautofocus", 33.0)),
                   z_max=float(_profile_value(settings, "hardware.z.zmaxautofocus", 34.5)),
                   step=float(_profile_value(profile, "autofocus.step", 0.0)),
                   subsampling=float(_profile_value(profile, "autofocus.subsampling", 0.25)))

    def __post_init__(self) -> None:
        _set(self, "camera_type", CameraType.coerce(self.camera_type, "Camera Type"))
        _set(self, "speed", AutofocusSpeed.coerce(self.speed, "Autofocus Speed"))
        _set(self, "method", AutofocusMethod.coerce(self.method, "Autofocus Method"))
        _set(self, "roi", _roi_tuple(self.roi))


@dataclass(frozen=True)
class RefocusParams:
    """``Camera Refocus Data`` (§2.3), in cluster order; defaults = the typedef defaults."""

    camera_type: CameraType = CameraType.HIGH_MAG          # camera, and the Fin ROI formula (§5.6)
    method: AutofocusMethod = AutofocusMethod.FFT          # metric (§5.7)
    refocus_method: RefocusMethod = RefocusMethod.FIN      # ignored (constant "Fin", D7)
    steps: int = 0                                         # N = 2·Steps + 1 positions
    step_mm: float = 0.0
    fin_roi_width: int = 0                                 # High Mag: ROI width; Low Mag: ROI left
    fin_roi_height: int = 0                                # High Mag: ROI height; Low Mag: ROI top
    oxyd_x: int = 0                                        # dead "Oxyd" branch (§5.9): ignored
    oxyd_y: int = 0
    oxyd_diam: int = 0
    acquisition_timeout_ms: int = 5000                     # not used (D7)
    wait_ms: int = 0                                       # settle pause once a position is reached
    save_images: bool = False                              # one PNG per position (§8)
    folder: str | Path = ""                                # existing folder of the PNG files (not created)

    @classmethod
    def from_profile(cls, profile: Any, settings: Any = None, *,
                     camera_type: CameraType | int | str = CameraType.HIGH_MAG,
                     method: AutofocusMethod | int | str | None = None) -> RefocusParams:
        """``alox_function_ll_refocus_bundle_profile_parameters d``: ``refocus.method`` (0), ``nbsteps`` (4),
        ``step`` (0.05 mm), ``roiwidth`` / ``roiheight`` (128), ``waitms`` (0) of the recipe (``refocus.offset`` has
        no field here). ``method`` replaces the recipe's (callers that force Sobel: bowing, the Low Mag refocus)."""
        return cls(camera_type=camera_type,
                   method=int(_profile_value(profile, "refocus.method", 0)) if method is None else method,
                   steps=int(_profile_value(profile, "refocus.nbsteps", 4)),
                   step_mm=float(_profile_value(profile, "refocus.step", 0.05)),
                   fin_roi_width=int(_profile_value(profile, "refocus.roiwidth", 128)),
                   fin_roi_height=int(_profile_value(profile, "refocus.roiheight", 128)),
                   wait_ms=int(_profile_value(profile, "refocus.waitms", 0)))

    def __post_init__(self) -> None:
        _set(self, "camera_type", CameraType.coerce(self.camera_type, "Camera Type"))
        _set(self, "method", AutofocusMethod.coerce(self.method, "Autofocus Method"))
        _set(self, "refocus_method", RefocusMethod.coerce(self.refocus_method, "Refocus Method"))


def _roi_tuple(roi: Any) -> tuple[int, ...]:
    if roi is None:
        return ()
    try:
        values = [float(v) for v in roi]
    except (TypeError, ValueError):
        raise FocusError(Code.FOCUS_INVALID_PARAMS, f"ROI: not an array of integers: {roi!r}") from None
    if any(not math.isfinite(v) or v != int(v) for v in values):
        raise FocusError(Code.FOCUS_INVALID_PARAMS, f"ROI: not an array of integers: {roi!r}")
    return tuple(int(v) for v in values)


@dataclass(frozen=True)
class FocusOptions:
    """Server-side settings of both procedures (never client-visible). Defaults: the LabVIEW values (§7.2)."""

    position_tolerance_mm: float = 0.001     # ±1 µm "reached" window (AF inclusive, RF [t − tol, t + tol))
    af_settle_s: float = 0.100               # AF Slow: pause once a position is reached
    af_position_timeout_s: float = 30.0      # AF: reach Z Min (Fast, from Start: LabVIEW); NEW: each Slow position
    #                                          (from its MOV) and Fast "Wait Started" (from MOV Z Max) (D6, Q22)
    rf_move_timeout_s: float = 5.0           # RF: per move, from the MOV
    rf_scale: float = 0.5                    # RF: fixed metric Scale Factor
    poll_interval_s: float = 0.001           # pause between position polls (LabVIEW: Wait 1 ms per step)
    fast_latency_s: float = 0.0              # Fast AF: time from exposure to the post-grab timestamp (D16; re-validate)
    newest_frame: bool = True                # Camera.grab(newest=…); False = IMAQdx "Next" as LabVIEW
    normal_speed: float | None = None        # mm/s set at the start; None = ZStage.settings.speed (alox.ini)
    check_z_limits: bool = False             # af_check_z_limits (D5): refuse [Z Min, Z Max] outside the limits (9405)
    af_move_to_best: bool = True             # owner B9: autofocus ends at its best Z (LabVIEW: where the scan ended)
    metrics: NiCompatMetrics = field(default_factory=NiCompatMetrics)   # NI behaviour parameters (07 §9.1)

    def __post_init__(self) -> None:
        for name in ("position_tolerance_mm", "af_settle_s", "af_position_timeout_s", "rf_move_timeout_s",
                     "poll_interval_s", "fast_latency_s"):
            value = getattr(self, name)
            if not (math.isfinite(value) and value >= 0):
                raise ValueError(f"FocusOptions.{name} must be finite and >= 0, got {value!r}")
        if not (math.isfinite(self.rf_scale) and self.rf_scale > 0):
            raise ValueError(f"FocusOptions.rf_scale must be finite and > 0, got {self.rf_scale!r}")
        if self.normal_speed is not None and not (math.isfinite(self.normal_speed) and self.normal_speed > 0):
            raise ValueError(f"FocusOptions.normal_speed must be finite and > 0, got {self.normal_speed!r}")


# ================================================================================================== results, errors
@dataclass(frozen=True)
class FocusResult:
    """The outcome of a run. ``table``: rows ``(Z mm, score)`` (``Last Focus Data``, §2.6): autofocus one row per image
    in acquisition order; refocus one row per planned position, 0 for unmeasured ones. ``best_*``: refocus = the
    position the stage was moved to (strict argmax, first maximum wins, NaN never wins); autofocus = the same argmax,
    where the stage ends (owner B9; ``FocusOptions.af_move_to_best``); None without measurements. ``position``: ``POS?`` at the end (None
    if unreadable). ``progress``: ``Focus Progress (%)`` of the last step (§2.7). ``images``: refocus PNG files."""

    table: tuple[tuple[float, float], ...]
    best_z: float | None
    best_score: float | None
    best_index: int | None
    position: float | None
    progress: float
    images: tuple[Path, ...] = ()

    @property
    def array(self) -> np.ndarray:
        """``table`` as an N × 2 float array (``0 × 2`` when empty)."""
        return np.asarray(self.table, dtype=float).reshape(-1, 2)


class FocusFailed(FocusError):
    """The run reached the ``Error`` state. ``code``/``source`` are those of the failure (9401, 9031, the device's or
    the metric's code; :func:`aloxcam.errors.cluster_of` for anything else); ``state`` is the state that failed and
    ``result`` the data and progress at that moment (the restore does not change them, D1)."""

    def __init__(self, code: int, source: str, *, state: str, result: FocusResult) -> None:
        super().__init__(code, source)
        self.state = state
        self.result = result


def _error_code_source(exc: BaseException) -> tuple[int, str]:
    if isinstance(exc, DeviceError) and exc.code is not None:
        return int(exc.code), str(exc)
    if isinstance(exc, DeviceError):
        return int(Code.INTERNAL), str(exc)
    cluster = cluster_of(exc)
    return cluster.code, cluster.source


def _whole(value: Any, minimum: int) -> bool:
    """An integer (or a float with an integral value) ``>= minimum``; booleans are refused."""
    if isinstance(value, bool) or not isinstance(value, (int, float, np.integer, np.floating)):
        return False
    return math.isfinite(value) and value == int(value) and value >= minimum


def _timeout_error() -> FocusError:
    return FocusError(Code.Z_POSITION_TIMEOUT, TEXT[Code.Z_POSITION_TIMEOUT])


def _invalid(text: str) -> FocusError:
    return FocusError(Code.FOCUS_INVALID_PARAMS, text)


def _lv_div(a: float, b: float) -> float:
    """LabVIEW floating-point division: never raises (x/0 → ±Inf, 0/0 → NaN)."""
    try:
        return a / b
    except ZeroDivisionError:
        return math.nan if (a == 0 or math.isnan(a)) else math.copysign(math.inf, a)


def _strict_argmax(scores: Sequence[float]) -> int | None:
    """Index of the first strict maximum; NaN never wins; all NaN → 0 (§5.5 ``Get Focus``); empty → None."""
    if not scores:
        return None
    best, best_score = 0, -math.inf
    for i, s in enumerate(scores):
        if s > best_score:
            best, best_score = i, s
    return best


def refocus_image_name(index: int, when: datetime) -> str:
    """``<yymmdd>_<HHMMSS>_refocus__mesa<index>.png`` (§8.1–§8.3): the time format ends with ``_`` and the job-file
    helper appends ``_mesa`` (double underscore); the index is ``Number To Decimal String`` of width 2, padded with
    spaces (inferred, to be confirmed). ``refocus_image_name(3, datetime(2023, 10, 8, 14, 32, 10))`` →
    ``"231008_143210_refocus__mesa 3.png"``."""
    ts = when.strftime("%y%m%d_%H%M%S_refocus_").replace(",", "", 1).replace(".", "", 1)
    return f"{ts}_mesa{index:>2d}.png"


# ================================================================================================== common machinery
class _FocusFunction(MeasurementFunction):
    """A Z-scan focus procedure on a :class:`ZStage` and one or two cameras (constructor injection).

    ``cameras`` is one :class:`Camera` (used whatever ``Camera Type`` says) or a mapping ``{CameraType: Camera}``
    (keys may also be labels or codes). ``clock`` (seconds, monotonic) and ``sleep`` let tests run on a fake clock;
    with the default ``sleep`` the pauses are ``ctx.sleep`` (woken at once by a stop)."""

    def __init__(self, z: ZStage, cameras: Camera | Mapping[Any, Camera], *, options: FocusOptions | None = None,
                 clock: Callable[[], float] | None = None, sleep: Callable[[float], None] | None = None) -> None:
        self.z = z
        if isinstance(cameras, Mapping):
            self._cameras: Camera | dict[CameraType, Camera] = {
                CameraType.coerce(key, "camera key"): camera for key, camera in cameras.items()}
        else:
            self._cameras = cameras
        self.options = options or FocusOptions()
        self._clock = clock or time.monotonic
        self._sleep = sleep

    def camera_for(self, camera_type: CameraType) -> Camera:
        """The camera of ``camera_type``; :class:`FocusError` 9504 when none is configured for it."""
        if not isinstance(self._cameras, dict):
            return self._cameras
        camera = self._cameras.get(camera_type)
        if camera is None:
            raise _invalid(f"Camera Type: no {camera_type.label} camera configured")
        return camera

    def _normal_speed(self) -> float:
        speed = self.options.normal_speed
        return float(self.z.settings.speed) if speed is None else speed


class _Job:
    """One run of a state machine: the per-run "shift registers", the loop, the restore and the publishing."""

    START: StrEnum
    FINISHED: StrEnum
    ERROR: StrEnum
    MEASURING: StrEnum

    def __init__(self, function: _FocusFunction, camera: Camera, ctx: FunctionContext) -> None:
        self.f = function
        self.z = function.z
        self.camera = camera
        self.ctx = ctx
        self.options = function.options
        self.clock = function._clock
        self.tol = self.options.position_tolerance_mm
        self.normal_speed = function._normal_speed()
        self.init_z: float | None = None           # None = never read (LabVIEW: 0.0), not restored (D11, Q20)
        self.init_speed: float | None = None
        self.move_t = 0.0
        self.progress = 0.0
        self.error: BaseException | None = None
        self.failed_state = ""
        self.images: list[Path] = []

    # ---------------------------------------------------------------------------------------------- loop
    def handlers(self) -> dict[StrEnum, Callable[[], StrEnum]]:
        raise NotImplementedError

    def result(self, position: float | None) -> FocusResult:
        raise NotImplementedError

    def entry_progress(self, state: StrEnum) -> float | None:
        """The ``Progress (%)`` that ``state`` writes (§2.7), set when the state is entered; None keeps the current
        value (measurement states compute theirs; ``Error`` keeps the failed step's, D1)."""
        raise NotImplementedError

    def execute(self) -> FocusResult:
        handlers = self.handlers()
        state = self.START
        self._publish(state)
        try:
            while state not in (self.FINISHED, self.ERROR):
                self.ctx.check()                                     # stop / abort between steps
                nxt = handlers[state]()
                if nxt is not state and nxt is not self.MEASURING:
                    self._publish(nxt)
                state = nxt
        except BaseException:                                        # Stopped, or a bug: restore, then re-raise
            self._restore(position=True)
            raise
        if state is self.ERROR:
            self._restore(position=True)                             # "Restore initial speed, initial position"
            code, source = _error_code_source(self.error) if self.error else (int(Code.INTERNAL), "unknown error")
            raise FocusFailed(code, source, state=self.failed_state,
                              result=self.result(self._position())) from self.error
        self._restore(position=False)                                # "Finished. Restore initial speed"
        self.finish()
        return self.result(self._position())

    def finish(self) -> None:
        """After a successful run and the speed restore (autofocus: move to the best Z, owner B9)."""

    def fail(self, exc: BaseException, state: StrEnum) -> StrEnum:
        """Record the error of ``state`` and go to ``Error``."""
        self.error = exc
        self.failed_state = str(state)
        log.warning("%s: %s failed: %s", self.ctx.name.name, state, exc)
        return self.ERROR

    def _publish(self, state: StrEnum, **data: Any) -> None:
        progress = self.entry_progress(state)
        if progress is not None:
            self.progress = progress
        extra: dict[str, Any] = {}
        if state is self.ERROR and self.error is not None:
            code, source = _error_code_source(self.error)
            extra = {"code": code, "error": source, "failed_state": self.failed_state}
        self.ctx.set_state(str(state), progress=self.progress, **extra, **data)

    # ---------------------------------------------------------------------------------------------- devices
    def query_position(self) -> float | None:
        """``POS?`` with the error cleared; a failure is None, i.e. "not reached" (D13 fix; LabVIEW reads 0.0)."""
        try:
            return self.z.position()
        except _LV_ERRORS as exc:
            log.debug("POS? failed (cleared): %s", exc)
            return None

    def move(self, target: float) -> None:
        """``MOV`` (not waited for); refused moves raise at once (device ``ERR?`` check, range check)."""
        self.z.move_absolute(target, wait=False)

    def grab(self) -> np.ndarray:
        return self.camera.grab(newest=self.options.newest_frame).pixels

    def pause(self, seconds: float) -> None:
        if seconds <= 0:
            return
        if self.f._sleep is None:
            self.ctx.sleep(seconds)
        else:
            self.ctx.check()
            self.f._sleep(seconds)

    def poll(self) -> None:
        self.pause(self.options.poll_interval_s)

    def _position(self) -> float | None:
        return self.query_position()

    def _restore(self, *, position: bool) -> None:
        """``VEL Init Speed`` (and ``MOV Init Z``, not waited for), errors cleared; only values actually read."""
        if self.init_speed is not None:
            try:
                self.z.set_speed(self.init_speed)
            except _LV_ERRORS as exc:
                log.warning("%s: restoring the speed %g mm/s failed: %s", self.ctx.name.name, self.init_speed, exc)
        if position and self.init_z is not None:
            try:
                self.move(self.init_z)
            except _LV_ERRORS as exc:
                log.warning("%s: moving back to %g mm failed: %s", self.ctx.name.name, self.init_z, exc)


# ================================================================================================== autofocus (§4)
class _AutofocusJob(_Job):
    START, FINISHED, ERROR, MEASURING = AFState.START, AFState.FINISHED, AFState.ERROR, AFState.MEASURE

    def __init__(self, function: Autofocus, p: AutofocusParams, camera: Camera, ctx: FunctionContext) -> None:
        super().__init__(function, camera, ctx)
        self.p = p
        self.fast = p.speed is AutofocusSpeed.FAST
        self.metric = autofocus_metric_method(p.method.label)            # §4.5: FFT | Prewitt | Sobel
        self.current_z = p.z_min                                         # "Current Z"
        self.t0 = 0.0                                                    # Fast: stage seen leaving Z Min
        self.duration = float(p.duration_s)
        self.rows: list[tuple[float, float]] = []                       # "AF Data"

    def handlers(self) -> dict[StrEnum, Callable[[], StrEnum]]:
        return {AFState.START: self.start, AFState.GOTO_Z_POS: self.goto_z_pos,
                AFState.WAIT_STARTED: self.wait_started, AFState.MEASURE: self.measure}

    def entry_progress(self, state: StrEnum) -> float | None:
        if state in (AFState.START, AFState.GOTO_Z_POS, AFState.WAIT_STARTED):
            return 0.0                                            # Slow: back to 0 between points (§2.7, D2)
        return 100.0 if state is AFState.FINISHED else None

    def at(self, pos: float | None, target: float) -> bool:
        return pos is not None and target - self.tol <= pos <= target + self.tol     # both bounds inclusive

    # "Check we are in start position (Zmin)"
    def start(self) -> StrEnum:
        p = self.p
        try:
            self.init_speed = self.z.speed()                      # "Get initial speed (will be restored at the end)"
        except _LV_ERRORS as exc:
            log.debug("VEL? failed (cleared): %s", exc)
        try:
            self.z.set_speed(self.normal_speed)                   # "Set Normal Speed"; errors cleared
        except _LV_ERRORS as exc:
            log.debug("VEL failed (cleared): %s", exc)
        try:
            pos = self.z.position()                               # "check Zmin pos with µm accuracy"
            self.init_z = pos                                     # before any move
            if not self.at(pos, p.z_min):
                self.move(p.z_min)                                # "Move to start position"
        except _LV_ERRORS as exc:
            return self.fail(exc, AFState.START)
        self.current_z = p.z_min
        self.move_t = self.clock()                                # "Move Timestamp"
        return AFState.GOTO_Z_POS

    # "Check Current Z (slow) or Start Z (fast) position reached"
    def goto_z_pos(self) -> StrEnum:
        p = self.p
        reached = self.at(self.query_position(), self.current_z)
        if self.fast:
            if reached:                                           # "Start Position reached => move to Zmax"
                try:
                    self.z.set_speed(abs(p.z_max - p.z_min) / self.duration)     # "Set Fast Speed"
                    self.move(p.z_max)                            # "Move to Zmax"
                except _LV_ERRORS as exc:
                    return self.fail(exc, AFState.GOTO_Z_POS)
                self.move_t = self.clock()                        # NEW: start of the "Wait Started" timeout
                return AFState.WAIT_STARTED
        elif reached:
            self.pause(self.options.af_settle_s)                  # "Make sure the stages has stopped moving"
            return AFState.MEASURE
        # not reached: Fast 30 s from Start (LabVIEW); Slow from the last MOV (NEW, D6)
        if self.clock() - self.move_t > self.options.af_position_timeout_s:
            return self.fail(_timeout_error(), AFState.GOTO_Z_POS)
        self.poll()
        return AFState.GOTO_Z_POS

    # "Wait until the stages have started to move from Zmin to ZMax"
    def wait_started(self) -> StrEnum:
        if not self.fast:
            return AFState.MEASURE                                # unreachable in a Slow run
        pos = self.query_position()
        if pos is None or self.at(pos, self.p.z_min):             # "Waiting until z stage starts moving..."
            if self.clock() - self.move_t > self.options.af_position_timeout_s:      # NEW (D6)
                return self.fail(_timeout_error(), AFState.WAIT_STARTED)
            self.poll()
            return AFState.WAIT_STARTED
        self.t0 = self.clock()                                    # "store t0 timestamp and measure"
        return AFState.MEASURE

    # "Measure Focus for current Z position, store each measure in data array"
    def measure(self) -> StrEnum:
        p = self.p
        err: BaseException | None = None
        score = 0.0
        pixels: np.ndarray | None = None
        try:
            pixels = self.grab()
        except _LV_ERRORS as exc:
            err = exc
        ti = self.clock()                                         # after the grab returns (also on error)
        if err is None:
            try:
                _, score = self.options.metrics.measure(pixels, self.metric, p.roi, p.subsampling)
            except _LV_ERRORS as exc:
                err, score = exc, 0.0                             # the metric VIs return 0 on error
        dz = p.z_max - p.z_min
        if self.fast:                                             # "Measure focus asynchronously while moving"
            elapsed = ti - self.t0
            z = p.z_min + dz * _lv_div(elapsed - self.options.fast_latency_s, self.duration)
            progress = _lv_div(z - p.z_min, dz) * 100.0
            nxt = AFState.FINISHED if elapsed >= self.duration else AFState.MEASURE
        else:                                                     # "... then move to next position, until Zmax"
            z = self.current_z                                    # commanded value, not read back
            z_next = z + p.step                                   # repeated addition (decides the last point)
            self.current_z = z_next
            if err is None:
                try:
                    self._move_next(z_next)                       # "Move to next Z position"
                except _LV_ERRORS as exc:
                    err = exc
                self.move_t = self.clock()
            progress = _lv_div(z_next - p.z_min, dz) * 100.0
            nxt = AFState.GOTO_Z_POS if z_next <= p.z_max else AFState.FINISHED
        self.rows.append((z, score))                              # appended even after a failure (score 0, D15)
        self.progress = progress
        self._publish(AFState.MEASURE, index=len(self.rows) - 1, z=z, score=score)
        if err is not None:
            return self.fail(err, AFState.MEASURE)
        return nxt

    def finish(self) -> None:
        """Owner's decision B9: end at the best Z (waited for; LabVIEW left the stage where the scan ended)."""
        if not self.options.af_move_to_best:
            return
        best = _strict_argmax([s for _, s in self.rows])
        if best is None:
            return
        target = self.rows[best][0]
        lo, hi = self.z.limits
        if not lo <= target <= hi:
            log.warning("autofocus: best Z %g mm outside the limits [%g, %g] mm: not moved", target, lo, hi)
            return
        self.ctx.check()
        self.z.move_absolute(target, wait=True)

    def _move_next(self, z_next: float) -> None:
        if z_next > self.p.z_max:                                 # the final over-travel MOV (Q22: reproduced)
            lo, hi = self.z.limits
            if not lo <= z_next <= hi:
                log.warning("autofocus: final move to %g mm (one step beyond Z Max) skipped: outside the limits "
                            "[%g, %g] mm", z_next, lo, hi)
                return
        self.move(z_next)

    def result(self, position: float | None) -> FocusResult:
        best = _strict_argmax([s for _, s in self.rows])
        return FocusResult(
            table=tuple(self.rows), best_z=None if best is None else self.rows[best][0],
            best_score=None if best is None else self.rows[best][1], best_index=best, position=position,
            progress=self.progress)


class Autofocus(_FocusFunction):
    """Autofocus "large" (``alox_camera_server_autofocus.vi``, §4): scans ``[Z Min, Z Max]`` step by step (Slow: move,
    100 ms settle, grab, measure) or in one continuous move (Fast: Z interpolated from time), returns the ``[Z, score]``
    table and, at its initial speed, moves the stage to the best Z (owner B9; LabVIEW left it where the scan ended:
    Slow one step beyond the last point, Fast ``Z Max``, which ``FocusOptions(af_move_to_best=False)`` reproduces). ``run`` raises :class:`aloxcam.errors.FocusError` 9504 / 9405 for refused parameters (no motion),
    :class:`FocusFailed` after the ``Error`` state, :class:`alox.core.functions.Stopped` on stop/abort (after the
    restore)."""

    name = FunctionName.AUTOFOCUS_LARGE

    def check_params(self, params: AutofocusParams) -> AutofocusParams:
        """The start-command checks (D8, D5): :class:`FocusError` 9504 for parameters that would hang, fail at every
        point or send the stage astray; 9405 "Z move out of range" when ``options.check_z_limits`` and ``[Z Min,
        Z Max]`` leaves the stage limits."""
        if not isinstance(params, AutofocusParams):
            raise _invalid(f"autofocus: AutofocusParams expected, got {type(params).__name__}")
        p = params
        for name in ("z_min", "z_max", "subsampling"):
            if not math.isfinite(getattr(p, name)):
                raise _invalid(f"autofocus: {name} must be finite")
        if not p.subsampling > 0:
            raise _invalid(f"autofocus: Subsampling must be > 0, got {p.subsampling!r} (07 F13)")
        if p.roi and len(p.roi) != 4:
            raise _invalid(f"autofocus: ROI must be empty or [Left, Top, Right, Bottom], got {list(p.roi)}")
        if p.speed is AutofocusSpeed.FAST:
            if not _whole(p.duration_s, 1):
                raise _invalid(f"autofocus: Fast Duration (s) must be a whole number > 0, got {p.duration_s!r}")
            if p.z_max == p.z_min:
                raise _invalid("autofocus: Fast scan needs Z Max != Z Min (speed 0, §7.3)")
        elif not (math.isfinite(p.step) and p.step > 0):
            raise _invalid(f"autofocus: Slow Step must be > 0, got {p.step!r}")
        if self.options.check_z_limits:
            lo, hi = self.z.limits
            if not (lo <= min(p.z_min, p.z_max) and max(p.z_min, p.z_max) <= hi):
                raise FocusError(Code.Z_OUT_OF_RANGE, TEXT[Code.Z_OUT_OF_RANGE])
        return p

    def run(self, params: AutofocusParams, ctx: FunctionContext) -> FocusResult:
        p = self.check_params(params)
        return _AutofocusJob(self, p, self.camera_for(p.camera_type), ctx).execute()


# ================================================================================================== refocus (§5)
class _RefocusJob(_Job):
    START, FINISHED, ERROR, MEASURING = RFState.START, RFState.FINISHED, RFState.ERROR, RFState.MESURE_POSITION

    def __init__(self, function: Refocus, p: RefocusParams, camera: Camera, ctx: FunctionContext) -> None:
        super().__init__(function, camera, ctx)
        self.p = p
        self.positions: list[float] = []
        self.scores: list[float] = []
        self.index = 0                                            # "Index Pos"
        self.best: int | None = None

    def handlers(self) -> dict[StrEnum, Callable[[], StrEnum]]:
        return {RFState.START: self.start, RFState.GET_POSITIONS: self.get_positions,
                RFState.MOVE_POSITION: self.move_position, RFState.CHECK_POSITION: self.check_position,
                RFState.MESURE_POSITION: self.mesure_position, RFState.GET_FOCUS: self.get_focus,
                RFState.MOVE_FOCUS: self.move_focus, RFState.WAIT_FOCUS: self.wait_focus}

    def target(self) -> float:
        return self.positions[self.index]

    def entry_progress(self, state: StrEnum) -> float | None:
        if state in (RFState.START, RFState.GET_POSITIONS):
            return 0.0
        if state in (RFState.MOVE_POSITION, RFState.CHECK_POSITION):
            return self.index_progress(self.index)
        if state in (RFState.GET_FOCUS, RFState.MOVE_FOCUS, RFState.WAIT_FOCUS, RFState.FINISHED):
            return 100.0                                          # already before the move to the best position
        return None

    def index_progress(self, index: int) -> float:
        n = len(self.positions)
        return 0.0 if n == 0 else min(max(index / n * 100.0, 0.0), 100.0)

    # "Init Refocus, store initial position and speed, set normal speed"
    def start(self) -> StrEnum:
        try:
            joystick_on = self.z.joystick_enabled(1)
        except _LV_ERRORS as exc:                                 # JON? error not wired: "disabled"
            log.debug("JON? failed (ignored): %s", exc)
            joystick_on = False
        if joystick_on:                                           # nothing read: nothing restored (D11)
            return self.fail(FocusError(Code.REFOCUS_JOYSTICK, TEXT[Code.REFOCUS_JOYSTICK]), RFState.START)
        try:
            self.init_z = self.z.position()                       # "Get Initial Pos."
            self.init_speed = self.z.speed()                      # "Get initial speed"
            self.z.set_speed(self.normal_speed)                   # "Set Normal Speed"
        except _LV_ERRORS as exc:
            return self.fail(exc, RFState.START)
        return RFState.GET_POSITIONS

    # "Pre-compute positions"
    def get_positions(self) -> StrEnum:
        p = self.p
        assert self.init_z is not None
        n = int(p.steps) * 2 + 1                                  # "Iterations"
        z_start = self.init_z - float(p.steps) * p.step_mm        # multiplication, not repeated addition
        lo, hi = self.z.limits                                    # read now: limits changed earlier apply (§7.4)
        self.positions = [min(max(z_start + float(i) * p.step_mm, lo), hi) for i in range(n)]
        self.scores, self.index = [], 0
        return RFState.MOVE_POSITION

    # "Move to next Z position"
    def move_position(self) -> StrEnum:
        try:
            self.move(self.target())
        except _LV_ERRORS as exc:
            return self.fail(exc, RFState.MOVE_POSITION)
        self.move_t = self.clock()
        return RFState.CHECK_POSITION

    def _reached(self, state: RFState, nxt: RFState) -> StrEnum:
        """``Check Position`` / ``Wait Focus``: 5 s from the MOV → 9401; ``[t − 1 µm, t + 1 µm)``; ``Wait (ms)``."""
        timed_out = self.clock() - self.move_t > self.options.rf_move_timeout_s
        t = self.target()
        pos = self.query_position()
        reached = pos is not None and t - self.tol <= pos < t + self.tol      # upper bound excluded
        if timed_out:                                             # no settle pause before Error (D23)
            return self.fail(_timeout_error(), state)
        if reached:
            self.pause(self.p.wait_ms / 1000.0)                   # "Make sure the Z stage has stopped moving"
            return nxt
        self.poll()
        return state

    def check_position(self) -> StrEnum:
        return self._reached(RFState.CHECK_POSITION, RFState.MESURE_POSITION)

    # "Measure Focus for current Z position, store each measure in data array"
    def mesure_position(self) -> StrEnum:
        p = self.p
        idx = self.index
        err: BaseException | None = None
        score = 0.0
        try:
            pixels = self.grab()
            h, w = pixels.shape[:2]
            roi = fin_roi(w, h, p.fin_roi_width, p.fin_roi_height, low_mag=p.camera_type is CameraType.LOW_MAG)
            image, score = self.options.metrics.measure(pixels, p.method.label, roi, self.options.rf_scale)
            if p.save_images:
                self.images.append(self._save(image, idx))        # the filtered image for "Sigma" (D18)
        except _LV_ERRORS as exc:
            err = exc
        self.scores.append(score)                                 # score 0 after a failed measurement (D15)
        self.index = idx + 1
        self.progress = self.index_progress(self.index)
        z = self.positions[idx]
        self._publish(RFState.MESURE_POSITION, index=idx, z=z, score=score)
        if err is not None:
            return self.fail(err, RFState.MESURE_POSITION)
        return RFState.GET_FOCUS if self.index >= len(self.positions) else RFState.MOVE_POSITION

    def _save(self, image: np.ndarray, index: int) -> Path:
        """``tools_log_refocus_image.vi`` (§8): the folder must exist (it is not created); 9501 on failure."""
        folder = str(self.p.folder)
        path = Path(folder) / refocus_image_name(index, datetime.now(UTC).astimezone())
        if not folder or not path.parent.is_dir():
            raise FocusError(Code.IMAGE_FILE, f"refocus image: invalid folder {folder!r}")
        try:
            write_png(path, image)
        except (OSError, ValueError) as exc:
            raise FocusError(Code.IMAGE_FILE, f"refocus image {path}: {exc}") from exc
        return path

    # "Get best focus position/score/index"
    def get_focus(self) -> StrEnum:
        n = min(len(self.positions), len(self.scores))
        self.best = _strict_argmax(self.scores[:n]) or 0
        self.index = self.best
        return RFState.MOVE_FOCUS

    # "Move to found focus position"
    def move_focus(self) -> StrEnum:
        try:
            self.move(self.target())
        except _LV_ERRORS as exc:
            return self.fail(exc, RFState.MOVE_FOCUS)
        self.move_t = self.clock()
        return RFState.WAIT_FOCUS

    # "Wait for the Z stage to be in best focus position"
    def wait_focus(self) -> StrEnum:
        return self._reached(RFState.WAIT_FOCUS, RFState.FINISHED)

    def result(self, position: float | None) -> FocusResult:
        n = max(len(self.positions), len(self.scores))           # Transpose(Build Array): zero padding
        rows = tuple((self.positions[i] if i < len(self.positions) else 0.0,
                      self.scores[i] if i < len(self.scores) else 0.0) for i in range(n))
        best = self.best
        return FocusResult(
            table=rows, best_z=None if best is None else self.positions[best],
            best_score=None if best is None else self.scores[best], best_index=best, position=position,
            progress=self.progress, images=tuple(self.images))


class Refocus(_FocusFunction):
    """Refocus "fine" (``alox_camera_server_refocus.vi``, §5): measures ``2·Steps + 1`` positions centred on the current
    Z (clamped to the stage limits), each after ``Wait (ms)``, on the Fin ROI with scale 0.5, moves to the strict
    argmax, restores the initial speed and returns the table with the best position. Optional PNG per position.
    Errors: 9031 joystick enabled, 9401 position timeout (5 s per move), device / metric / image-file codes
    (:class:`FocusFailed`, after the restore), 9504 refused parameters (no motion), :class:`Stopped` on stop/abort."""

    name = FunctionName.REFOCUS

    def check_params(self, params: RefocusParams) -> RefocusParams:
        """The start-command checks (D8): ``Steps ≥ 0``, finite step, ``Wait (ms) ≥ 0``; :class:`FocusError` 9504."""
        if not isinstance(params, RefocusParams):
            raise _invalid(f"refocus: RefocusParams expected, got {type(params).__name__}")
        p = params
        if not _whole(p.steps, 0):
            raise _invalid(f"refocus: Steps must be a whole number >= 0, got {p.steps!r}")
        if not math.isfinite(p.step_mm):
            raise _invalid(f"refocus: Step (mm) must be finite, got {p.step_mm!r}")
        if not _whole(p.wait_ms, 0):
            raise _invalid(f"refocus: Wait (ms) must be a whole number >= 0, got {p.wait_ms!r}")
        for name in ("fin_roi_width", "fin_roi_height"):
            if not _whole(getattr(p, name), -(2 ** 31)):
                raise _invalid(f"refocus: {name} must be a whole number, got {getattr(p, name)!r}")
        return p

    def run(self, params: RefocusParams, ctx: FunctionContext) -> FocusResult:
        p = self.check_params(params)
        return _RefocusJob(self, p, self.camera_for(p.camera_type), ctx).execute()
