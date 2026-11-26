"""Refocus Oxyd: a short Z scan scored in a circular ROI on the mesa, as a measurement function.

Spec: ``docs/vision-core/map.md`` Part 3 §2.1, §3.3, §3.4; Part 4 §3.3 (``alox_vision_oxyd_roi``), §3.5
(``alox_vision_overlay_thick_circle``), §3.11 (``tools_save_refocus_data``). Diagrams:
``alox_function_ll_refocus_oxyd_state_machine d`` (controls ``Image``, ``Circle In``, ``Refocus Profile``), ``d1``
(frame 0: ``Set Running``), ``d3…d9`` (``Start``: results reset, status 204, 9037 on an empty ``Circle In``), ``d12…d18``
(``Started``: run event, Z and camera status → 9030, ``get_image`` Run Once "to set exposure time & LED state",
joystick → 009036, Z speed ``hardware.z.speed``), ``d21`` (``Measure``: µm ⇒ pixels or 9080, camera refocus with the
``Camera Refocus Data`` {method ``Oxyd``, Fin ROI 0 × 0, Oxyd X/Y = circle centre, Oxyd Diam = ROI diameter in
pixels}, timeout 60 s, errors → 9035, ``alox_function_II_refocus_filter`` with ``No filter`` = TRUE, "Error if focus
position is at the first/last two points", event ``Refocus Oxyd Measured``, data and images saved), ``d41``/``d65``
(``Finished``/``Error``: new image, overlay texts "Autofocus Pos. (mm): %.3f", "Autofocus Score (mm): %.1f", the ROI
as a thick circle of 5), ``d84`` (``Stopped``); ``alox_function_II_refocus_filter d, d1, d2, p``;
``alox_function_ll_refocus_oxyd_bundle_profile_parameters d`` (``refocus.oxyd.*``).

The Z scan is the camera-server refocus, now in-process: :class:`alox.functions.focus.Refocus` (positions, moves,
settle, joystick, best-Z move, speed restore, refocus PNGs) is run with the ``Camera Refocus Data`` LabVIEW builds; its
metric runs on the Oxyd ROI (:class:`_OxydRoiMetrics`). ``Circle In`` (:attr:`RefocusOxydParams.circle`) is the circle
of Detect Circle (:attr:`alox.functions.detect_circle.DetectCircleReport.best_circle`). No HL function of V1.2.6.2
calls this function (the HL mesa uses the plain Refocus); it is started from the UI or the API.

Reproduced (LabVIEW): states and transitions, params and profile keys with their defaults; 9037 for an all-zero
``Circle In``; 9030 when the Z stage or the camera is not ready; the joystick warning 009036 → ``Error``; the Z speed
``hardware.z.speed``; the ROI diameter µm → pixels (µm as is, mm × 1000, anything else 9080) rounded to an integer; the
Oxyd ROI (``alox_vision_oxyd_roi``: centred on the circle, or on the image when the centre is (0, 0)); 2·Steps + 1
positions, ``Wait (ms)``; camera-server errors → 9035 "refocus oxyd error"; the score filter with ``No filter`` TRUE
(the raw strict argmax); ``Valid ?`` FALSE and ``Error`` when the best index is one of the first or last two; the
report ``{Best Z Position, Zs, Best Score, Scores, Valid ?, Progress %}``; the overlays (texts at the top centre when
enabled, ROI circle magenta thickness 5) on a new image; ``log.saverefocusimages`` (``<data>/images/Misc/refocus``)
and ``log.saveautofocusdata`` (``.focf`` / ``.focfraw`` in ``<data>/files/refocus``).

Fixed:

- The camera server ignored ``Refocus Method`` (its selector is the constant ``Fin``, ``docs/06`` §5.9, D7) and so
  scored the Fin ROI of size 0 × 0 sent by this function (an empty rectangle); here the metric runs on the bounding
  square of the Oxyd ROI (clipped to the image), which is what the function was written for (owner question).
- A best position at the edges of the scan ended in ``Error`` with an empty error cluster; it carries 9035 here.
- The run panel events (``Refocus Oxyd Started/Measured/Finished``) are ``ctx.set_state`` publications (the
  states of the in-process camera-server refocus are published as ``Measure`` with ``refocus_state``); the system
  status 204 "LL running" is the core's.
"""

from __future__ import annotations

import logging
import math
import struct
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import numpy as np

from alox.core.functions import FunctionContext, FunctionName, MeasurementFunction
from alox.devices.base import DeviceError, DeviceState
from alox.functions.detect_circle import (
    INVALID_ARGUMENTS,
    FunctionError,
    app_log,
    camera_name,
    coerce_enum,
    pixel_size_um,
    run_states,
)
from alox.functions.focus import (
    AutofocusMethod,
    CameraType,
    FocusOptions,
    FocusResult,
    Refocus,
    RefocusMethod,
    RefocusParams,
)
from alox.functions.services import Circle as CircleShape
from alox.functions.services import Services, Shape, Text
from alox.vision.circles import Circle
from alox.vision.focus import lv_round
from aloxcam.errors import AloxError

__all__ = [
    "REFOCUS_ERROR",
    "REFOCUS_OXYD_ERROR",
    "REFOCUS_OXYD_JOYSTICK",
    "REFOCUS_OXYD_ROI",
    "RFOState",
    "RefocusOxyd",
    "RefocusOxydParams",
    "RefocusOxydReport",
    "oxyd_roi",
    "refocus_filter",
]

log = logging.getLogger(__name__)

REFOCUS_ERROR = 9030                      # "refocus error" (Started: Z / camera status)
REFOCUS_OXYD_ERROR = 9035                 # "refocus oxyd error"
REFOCUS_OXYD_JOYSTICK = 9036              # "refocus oxyd warning (joystick enabled)"
REFOCUS_OXYD_ROI = 9037                   # "refocus oxyd error (image/region of interest)"

OVERLAY = "refocusoxyd"
MAGENTA = (255, 0, 255)
TEXT_COLOR = (255, 255, 0)
TEXT_SIZE = 24


class RFOState(StrEnum):
    """``alox_typedef_refocus_oxyd_function_state``."""

    START = "Start"
    STARTED = "Started"
    MEASURE = "Measure"
    FINISHED = "Finished"
    ERROR = "Error"
    STOPPED = "Stopped"


# ================================================================================================== params, report
_PROFILE_KEYS: dict[str, tuple[str, Any]] = {
    "camera_type": ("refocus.oxyd.camera", 0),
    "method": ("refocus.oxyd.method", 0),
    "steps": ("refocus.oxyd.nbsteps", 4),
    "step_mm": ("refocus.oxyd.step", 0.05),
    "roi_diameter_um": ("refocus.oxyd.roidiameter", 40),
    "wait_ms": ("refocus.oxyd.waitms", 0),
    "show_ui": ("refocus.oxyd.showwindow", False),
    "overlay_score": ("refocus.oxyd.overlayscore", False),
    "overlay_position": ("refocus.oxyd.overlayposition", False),
    "overlay_roi": ("refocus.oxyd.overlayroi", False),
}


def _flag(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().upper() in ("TRUE", "T", "1", "YES", "ON")
    return bool(value)


def _number(value: Any, what: str) -> float:
    try:
        number = float(str(value).replace(",", ".")) if isinstance(value, str) else float(value)
    except (TypeError, ValueError):
        raise FunctionError(INVALID_ARGUMENTS, f"{what}: not a number: {value!r}") from None
    if isinstance(value, bool) or not math.isfinite(number):
        raise FunctionError(INVALID_ARGUMENTS, f"{what}: invalid value {value!r}")
    return number


@dataclass(frozen=True)
class RefocusOxydParams:
    """``alox_typedef_refocus_oxyd_function_parameters`` (defaults = the bundle VI's), then ``circle`` (``Circle In``,
    pixels) and ``filter_scores`` (NOT ``No filter`` of ``alox_function_II_refocus_filter``; LabVIEW: no filter)."""

    camera_type: CameraType = CameraType.HIGH_MAG
    method: AutofocusMethod = AutofocusMethod.FFT
    steps: int = 4                              # 2·Steps + 1 positions
    step_mm: float = 0.05
    roi_diameter_um: int = 40
    wait_ms: int = 0
    show_ui: bool = False
    overlay_score: bool = False
    overlay_position: bool = False
    overlay_roi: bool = False
    circle: Circle | None = None
    filter_scores: bool = False

    def __post_init__(self) -> None:
        s = object.__setattr__
        s(self, "camera_type", coerce_enum(CameraType, self.camera_type, "Camera Type"))
        s(self, "method", coerce_enum(AutofocusMethod, self.method, "Autofocus Method"))
        for name in ("steps", "roi_diameter_um", "wait_ms"):
            number = _number(getattr(self, name), name)
            if number != round(number) or number < 0:
                raise FunctionError(INVALID_ARGUMENTS, f"{name}: a whole number >= 0 expected, got "
                                                       f"{getattr(self, name)!r}")
            s(self, name, round(number))
        s(self, "step_mm", _number(self.step_mm, "Step (mm)"))
        for name in ("show_ui", "overlay_score", "overlay_position", "overlay_roi", "filter_scores"):
            s(self, name, _flag(getattr(self, name)))
        if self.circle is not None and not isinstance(self.circle, Circle):
            c = self.circle
            try:
                s(self, "circle", Circle(*c) if isinstance(c, (tuple, list)) else Circle(c.x, c.y, c.radius))
            except (TypeError, AttributeError, ValueError):
                raise FunctionError(INVALID_ARGUMENTS, f"Circle In: invalid circle {c!r}") from None

    @classmethod
    def from_profile(cls, profile: Any, **overrides: Any) -> RefocusOxydParams:
        """``alox_function_ll_refocus_oxyd_bundle_profile_parameters``; ``overrides`` (e.g. ``circle=``) win."""
        values = {name: profile.get(key, default) for name, (key, default) in _PROFILE_KEYS.items()}
        values.update(overrides)
        return cls(**values)


@dataclass(frozen=True)
class RefocusOxydReport:
    """``alox_typedef_refocus_function_results`` (``Refocus Report``) plus the best index, the final stage position,
    the Oxyd ROI ``[L, T, R, B]`` and its diameter in pixels, and the refocus PNG files."""

    best_z: float = 0.0
    zs: tuple[float, ...] = ()
    best_score: float = 0.0
    scores: tuple[float, ...] = ()
    valid: bool = False
    progress: float = 0.0
    best_index: int = -1
    position: float | None = None
    roi: tuple[int, ...] = ()
    roi_diameter_px: int = 0
    images: tuple[Path, ...] = ()


# ================================================================================================== helpers
def oxyd_roi(width: int, height: int, circle: Circle, diameter_px: float) -> tuple[int, int, int, int]:
    """``alox_vision_oxyd_roi``: the bounding box ``[L, T, R, B]`` of the oval ROI of diameter ``diameter_px``
    centred on ``circle`` or, when its centre is (0, 0) ("no circle found"), on the image centre ``(W/2, H/2)``;
    rounded to integers (half to even)."""
    r = diameter_px * 0.5
    if circle.x == 0 and circle.y == 0:
        cx, cy = width * 0.5, height * 0.5
    else:
        cx, cy = circle.x, circle.y
    return lv_round(cx - r), lv_round(cy - r), lv_round(cx + r), lv_round(cy + r)


def refocus_filter(zs: Sequence[float], scores: Sequence[float], *, no_filter: bool = True
                   ) -> tuple[int, float, float]:
    """``alox_function_II_refocus_filter``: ``(Index Pos, Focus Pos, Max Score)``.

    The strict argmax of the scores (first maximum wins, start −∞; none: index 0, Z 0, −∞). It is the result when
    ``no_filter`` or when it is one of the first or last two points. Otherwise the two best indices ``p0, p1`` (scores
    sorted descending): when they are adjacent or ``p1`` is at an end, the scores are smoothed by
    ``f[i] = mean(s[i−1], s[i], s[i])`` (``s[−1] = 0``; the third term was meant to be ``s[i+1]`` but is wired
    ``(i − 1) + 1``, as the values on the VI front panel confirm) and the result is the argmax of ``f`` (``Max Score``
    = ``f[index]``); otherwise ``index = round(mean(p0, p1))`` (half to even) with the raw score."""
    s = [float(v) for v in scores]
    z = [float(v) for v in zs]
    n = len(s)
    index, best = 0, -math.inf
    for i, v in enumerate(s):
        if v > best:
            index, best = i, v
    focus = z[index] if index < len(z) else 0.0
    if no_filter or n == 0 or index in (0, 1, n - 1, n - 2):
        return index, focus, best
    order = sorted(range(n), key=lambda i: -s[i] if not math.isnan(s[i]) else math.inf)   # stable, descending
    p0, p1 = order[0], order[1]
    if abs(p0 - p1) == 1 or p0 in (0, n - 1) or p1 in (0, n - 1):
        f = [(s[i - 1] if i >= 1 else 0.0) / 3.0 + 2.0 * s[i] / 3.0 for i in range(n)]
        index, best = 0, -math.inf
        for i, v in enumerate(f):
            if v > best:
                index, best = i, v
        return index, (z[index] if index < len(z) else 0.0), best
    index = lv_round((p0 + p1) / 2.0)
    return index, (z[index] if index < len(z) else 0.0), s[index]


class _OxydRoiMetrics:
    """The ``Metrics`` object of :class:`FocusOptions` for this run: the focus metric on the Oxyd ROI (its bounding
    square clipped to the image) instead of the Fin ROI that the camera-server refocus computes."""

    def __init__(self, base: Any, circle: Circle, diameter_px: float) -> None:
        self.base = base
        self.circle = circle
        self.diameter_px = diameter_px
        self.roi: tuple[int, ...] = ()

    def measure(self, img: np.ndarray, method: str, roi: Sequence[int] | None,
                scale: float) -> tuple[np.ndarray, float]:
        h, w = img.shape[:2]
        left, top, right, bottom = oxyd_roi(w, h, self.circle, self.diameter_px)
        self.roi = (max(left, 0), max(top, 0), min(right, w), min(bottom, h))
        return self.base.measure(img, method, list(self.roi), scale)


class _MeasureContext:
    """The run context seen by the camera-server refocus: same stop/abort flags and runner, but its states are
    published as ``Measure`` with ``refocus_state`` (LabVIEW: the camera server's states are not the function's)."""

    def __init__(self, ctx: FunctionContext) -> None:
        self._ctx = ctx
        self.name, self.job, self.runner = ctx.name, ctx.job, ctx.runner

    @property
    def stop_requested(self) -> bool:
        return self._ctx.stop_requested

    def check(self) -> None:
        self._ctx.check()

    def sleep(self, seconds: float) -> None:
        self._ctx.sleep(seconds)

    def set_state(self, state: str, **data: Any) -> None:
        self._ctx.set_state(str(RFOState.MEASURE), refocus_state=state, **data)


def _write_refocus_data(folder: Path, rows: Sequence[tuple[float, float]], when: datetime) -> list[Path]:
    """``tools_save_refocus_data``: ``<ts>.focf`` (LabVIEW binary: big-endian I32 count, then the (Z, score) DBL
    pairs) and ``<ts>.focfraw`` (tab-delimited, ``%.3f``); ``ts = %y%m%d_%H%M%S``."""
    folder.mkdir(parents=True, exist_ok=True)
    stem = folder / when.strftime("%y%m%d_%H%M%S")
    binary = stem.with_suffix(".focf")
    binary.write_bytes(struct.pack(f">i{2 * len(rows)}d", len(rows), *[v for row in rows for v in row]))
    text = stem.with_suffix(".focfraw")
    text.write_text("".join(f"{z:.3f}\t{s:.3f}\n" for z, s in rows), encoding="ascii")
    return [binary, text]


# ================================================================================================== the function
class RefocusOxyd(MeasurementFunction):
    """Refocus Oxyd (``alox_function_ll_refocus_oxyd``): ``run`` returns a :class:`RefocusOxydReport` (the stage is
    left at the best Z); raises :class:`FunctionError` 9030/9035/9036/9037/9080 (9002 for invalid parameters) from
    the ``Error`` state, :class:`alox.core.functions.Stopped` on stop/abort (after the speed/position restore of the
    refocus). ``options`` are the camera-server refocus options (timeouts, tolerances, metric NI parameters);
    ``get_image_params(camera_type)`` gives the params of the Get Image ``Run Once`` (None: its own defaults)."""

    name = FunctionName.REFOCUS_OXYD

    def __init__(self, services: Services, *, options: FocusOptions | None = None,
                 get_image_params: Callable[[CameraType], Any] | None = None) -> None:
        self.services = services
        self.options = options or FocusOptions()
        self.get_image_params = get_image_params
        self._last: RefocusOxydReport | None = None

    @property
    def last_report(self) -> RefocusOxydReport | None:
        """LabVIEW "Get Last Measure"."""
        return self._last

    def run(self, params: RefocusOxydParams | None, ctx: FunctionContext) -> RefocusOxydReport:
        if params is None:
            params = RefocusOxydParams.from_profile(self.services.profile)
        if not isinstance(params, RefocusOxydParams):
            raise FunctionError(INVALID_ARGUMENTS, f"refocus oxyd: RefocusOxydParams expected, got "
                                                   f"{type(params).__name__}")
        job = _RefocusOxydJob(self, params, ctx)
        try:
            return job.execute()
        finally:
            self._last = job.report


class _RefocusOxydJob:
    def __init__(self, function: RefocusOxyd, p: RefocusOxydParams, ctx: FunctionContext) -> None:
        self.f = function
        self.s = function.services
        self.p = p
        self.ctx = ctx
        self.cam_name = camera_name(p.camera_type)
        self.camera: Any = None
        self.z: Any = None
        self.report = RefocusOxydReport()                    # "Refocus Results" reset at Start
        self.error: AloxError | None = None
        self.failed_state = ""
        self.diameter_px = 0

    def execute(self) -> RefocusOxydReport:
        handlers = {RFOState.START: self.start, RFOState.STARTED: self.started, RFOState.MEASURE: self.measure,
                    RFOState.FINISHED: self.finished, RFOState.ERROR: self.failed}
        run_states(self.ctx, RFOState.START, handlers, stopped=RFOState.STOPPED,
                   data=lambda state: {"progress": self.report.progress})
        return self.report

    def fail(self, exc: AloxError, state: RFOState) -> RFOState:
        self.error, self.failed_state = exc, str(state)
        return RFOState.ERROR

    # "Init State machine here"
    def start(self) -> RFOState:
        c = self.p.circle
        if c is None or c.is_nan or (c.x == 0 and c.y == 0 and c.radius == 0):
            return self.fail(FunctionError(REFOCUS_OXYD_ROI, "refocus oxyd error (image/region of interest): "
                                                             "invalid input circle"), RFOState.START)
        return RFOState.STARTED

    # "Post start initialization ..."
    def started(self) -> RFOState:
        try:
            self.camera = self.s.camera(self.p.camera_type)
            self.z = self.s.device("z")
        except (LookupError, ValueError) as exc:
            return self.fail(FunctionError(REFOCUS_ERROR, f"refocus error: {exc}"), RFOState.STARTED)
        for device in (self.z, self.camera):                # "Get Z Status", "Get Status"
            state = getattr(device, "state", DeviceState.READY)
            if state != DeviceState.READY:
                return self.fail(FunctionError(REFOCUS_ERROR, f"refocus error: {getattr(device, 'name', '?')} is "
                                                              f"{state}"), RFOState.STARTED)
        try:
            self._acquire()                                  # "to set predefined exposure time & LED state"
        except (AloxError, DeviceError) as exc:
            return self.fail(_as_error(exc, REFOCUS_ERROR), RFOState.STARTED)
        try:
            joystick = self.z.joystick_enabled(1)            # "Z Joystick Enabled?"
        except DeviceError as exc:
            return self.fail(_as_error(exc, REFOCUS_ERROR), RFOState.STARTED)
        if joystick:
            app_log(self.s, "warning", "009036 refocus oxyd warning (joystick enabled)")
            return self.fail(FunctionError(REFOCUS_OXYD_JOYSTICK, "refocus oxyd warning (joystick enabled)"),
                             RFOState.STARTED)
        return RFOState.MEASURE

    # "Refocus (Autofocus oxyd)"
    def measure(self) -> RFOState:
        p, s = self.p, self.s
        assert p.circle is not None
        try:
            px = pixel_size_um(s, self.cam_name, pixels_ok=False)          # "µm ⇒ pixels"
        except FunctionError as exc:
            return self.fail(exc, RFOState.MEASURE)
        self.diameter_px = lv_round(p.roi_diameter_um / px)
        metrics = _OxydRoiMetrics(self.f.options.metrics, p.circle, self.diameter_px)
        options = replace(self.f.options, metrics=metrics)                  # type: ignore[arg-type]
        speed = s.settings.get("hardware.z.speed", None)
        if speed is not None and self.f.options.normal_speed is None:
            try:
                options = replace(options, normal_speed=float(speed))
            except (TypeError, ValueError):
                log.warning("refocus oxyd: hardware.z.speed %r ignored", speed)
        save_images, folder = self._image_folder()
        data = RefocusParams(
            camera_type=p.camera_type, method=p.method, refocus_method=RefocusMethod.OXYD, steps=p.steps,
            step_mm=p.step_mm, fin_roi_width=0, fin_roi_height=0, oxyd_x=lv_round(p.circle.x),
            oxyd_y=lv_round(p.circle.y), oxyd_diam=self.diameter_px, wait_ms=p.wait_ms, save_images=save_images,
            folder=folder)
        sleep = None if s.sleep is time.sleep else s.sleep
        refocus = Refocus(self.z, self.camera, options=options, clock=s.clock, sleep=sleep)
        try:
            result: FocusResult = refocus.run(data, _MeasureContext(self.ctx))    # type: ignore[arg-type]
        except (AloxError, DeviceError) as exc:
            partial = getattr(exc, "result", None)
            if isinstance(partial, FocusResult):
                self.report = self._report(partial, metrics, valid=False)
            return self.fail(FunctionError(REFOCUS_OXYD_ERROR, f"Refocus oxyd error: {_source(exc)}"),
                             RFOState.MEASURE)
        self.report = self._report(result, metrics)
        self.ctx.set_state(str(RFOState.MEASURE), event="Refocus Oxyd Measured", best_z=self.report.best_z,
                           best_score=self.report.best_score, valid=self.report.valid, progress=self.report.progress)
        self._save_data(result)
        if not self.report.valid:                          # "Error if focus position is at the first/last two points"
            return self.fail(FunctionError(REFOCUS_OXYD_ERROR, "refocus oxyd error: focus position at the first/last "
                                                               "two points"), RFOState.MEASURE)
        return RFOState.FINISHED

    def _report(self, result: FocusResult, metrics: _OxydRoiMetrics, *, valid: bool = True) -> RefocusOxydReport:
        zs = tuple(z for z, _ in result.table)
        scores = tuple(sc for _, sc in result.table)
        index, best_z, best_score = refocus_filter(zs, scores, no_filter=not self.p.filter_scores)
        n = len(scores)
        ok = valid and n > 0 and not (index <= 1 or index >= n - 2)
        return RefocusOxydReport(best_z=best_z, zs=zs, best_score=best_score, scores=scores, valid=ok,
                                 progress=result.progress, best_index=index, position=result.position,
                                 roi=metrics.roi, roi_diameter_px=self.diameter_px, images=result.images)

    def _image_folder(self) -> tuple[bool, str | Path]:
        """``log.saverefocusimages`` → ``<data>/images/Misc/refocus`` (created)."""
        if not _flag(self.s.settings.get("log.saverefocusimages", False)) or self.s.folders is None:
            return False, ""
        folder = Path(self.s.folders.image_folder("Misc")) / "refocus"
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            log.warning("refocus oxyd: cannot create %s: %s", folder, exc)
            return False, ""
        return True, folder

    def _save_data(self, result: FocusResult) -> None:
        """``log.saveautofocusdata`` → ``tools_save_refocus_data`` in ``<data>/files/refocus`` (no job: the data
        root of ``tools_alox_shared_jobhistory_folder_FOCUS_path``)."""
        if not _flag(self.s.settings.get("log.saveautofocusdata", False)) or self.s.folders is None:
            return
        try:
            _write_refocus_data(Path(self.s.folders.file_folder("refocus")), result.table, datetime.now(UTC).astimezone())
        except OSError as exc:
            app_log(self.s, "warning", f"Refocus Oxyd (Save Data): {exc}")

    # "Overlay Results" of Finished and Error
    def _acquire(self) -> np.ndarray | None:
        """``get_image`` Run Once (when the function is registered), else a grab stored in the image store."""
        try:
            self.ctx.runner.function(FunctionName.GET_IMAGE)
        except KeyError:
            image = self.camera.grab().pixels
            self.s.images.put(self.cam_name, image, metadata={"function": "refocus_oxyd"})
            return image
        params = self.f.get_image_params(self.p.camera_type) if self.f.get_image_params else None
        self.ctx.run_once(FunctionName.GET_IMAGE, params)
        return self.s.images.last(self.cam_name)

    def _show(self) -> None:
        r, p = self.report, self.p
        try:
            image = self._acquire() if self.camera is not None else self.s.images.last(self.cam_name)
        except (AloxError, DeviceError) as exc:
            app_log(self.s, "warning", f"Refocus Oxyd (Overlay): {_source(exc)}")
            image = self.s.images.last(self.cam_name)
        shapes: list[Shape] = []
        if image is not None:
            h, w = np.asarray(image).shape[:2]
            if p.overlay_position:
                shapes.append(Text(w / 2.0, TEXT_SIZE, f"Autofocus Pos. (mm): {r.best_z:.3f}", TEXT_COLOR, TEXT_SIZE,
                                   "mt"))
            if p.overlay_score:
                shapes.append(Text(w / 2.0, 2 * TEXT_SIZE + 4, f"Autofocus Score (mm): {r.best_score:.1f}",
                                   TEXT_COLOR, TEXT_SIZE, "mt"))
            if p.overlay_roi and p.circle is not None and self.diameter_px > 0:
                left, top, right, bottom = oxyd_roi(w, h, p.circle, self.diameter_px)
                shapes.append(CircleShape((left + right) / 2.0, (top + bottom) / 2.0, (right - left) / 2.0, MAGENTA,
                                          5))
        self.s.images.set_overlay(OVERLAY, shapes)

    def finished(self) -> None:
        self._show()
        app_log(self.s, "info", f"Refocus Oxyd (Finished): Z = {self.report.best_z:.3f} mm, score = "
                                f"{self.report.best_score:.1f}")

    def failed(self) -> None:
        err = self.error or FunctionError(REFOCUS_OXYD_ERROR, "refocus oxyd error")
        if self.failed_state == str(RFOState.MEASURE):
            self._show()
        app_log(self.s, "error", f"Refocus Oxyd (Error): {err.code} {_source(err)}")
        raise FunctionError(err.code, _source(err), state=self.failed_state, report=self.report)


def _source(exc: BaseException) -> str:
    return getattr(exc, "source", None) or str(exc)


def _as_error(exc: BaseException, code: int) -> AloxError:
    if isinstance(exc, AloxError):
        return exc
    device_code = getattr(exc, "code", None)
    return FunctionError(int(device_code) if device_code is not None else code, str(exc))
