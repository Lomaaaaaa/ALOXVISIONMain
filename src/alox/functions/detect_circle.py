"""Detect Circle: find the circular mesa (VCSEL) in an image, as a measurement function of the integrated application.

Spec: ``docs/vision-core/map.md`` Part 3 §2.1 (common pattern), §3.5 (Detect Circle), Part 4 §3.2 (pre-processing),
§3.5 (overlays). Diagrams: ``alox_function_ll_detect_circle_state_machine d10`` (pre-processing, Obj learning, best
circle "Score"), ``d39`` (Shape), ``d44`` (Obj learning), ``d22…d30`` (best circle methods),
``d68`` (DST image, overlays), ``d88`` (results, 9015); ``alox_function_ll_detect_circle_bundle_profile_parameters
d`` (profile keys ``detect.vcsel.*`` and their defaults). The NI Vision tools are the documented equivalents of
:mod:`alox.vision.edges`.

The worker does not acquire: the image is the caller's (``Image SRC/DST``), here :attr:`DetectCircleParams.image`, or
the newest image of the camera in the image store when it is None. :meth:`DetectCircle.run` runs the state machine
``Start → Started → Detect Circle → Finished`` (``Error``, ``Stopped``) one state per loop iteration, with
``ctx.check()`` between states and ``ctx.set_state`` on every change. The last report is kept (LabVIEW ``Get
Results``) and can be replaced without running (``Force Results``, :meth:`DetectCircle.force_results`): the HL mesa
passes :attr:`DetectCircleReport.best_circle` to Measure Oxyd (:attr:`alox.functions.measure_oxyd.MeasureOxydParams.
circle`) and Refocus Oxyd (:attr:`alox.functions.refocus_oxyd.RefocusOxydParams.circle`).

This module also holds what the three functions of this family share: the labelled enums, the error class
:class:`FunctionError`, the camera calibration (:func:`pixel_size_um`) and the state loop (:func:`run_states`).

Reproduced (LabVIEW): the params cluster and its profile keys and defaults; the radius range µm → pixels by the High
Mag calibration (``× 1000`` when the unit is mm, the value as is otherwise) and the downsampling factor (``None`` 1,
``25%`` 0.75, ``50%`` 0.5, ``75%`` 0.25, ``90%`` 0.1, zero-order resample); grey morphology open, close, erode, dilate
(k rounded up to odd, skipped below 3); the methods in use (owner R8: ``Shape``: circles then ellipses, ellipse
radius ``(minor + major)/2``, minimum score ``× 10``; ``Obj learning`` ("Image"): the model PNG matched with a rotation
of 0–360°, score ``× 10``);
centres and radii scaled back to the full image; best circle ``Centered`` (smallest distance to the image centre,
first wins) and ``Largest`` (largest radius, first wins); ``Found`` = a best index ≥ 0 and at least one circle, else
error 9015 "detect circle error (no circular vcsel found)"; the overlay group (best circle red, thickness 10; the
others blue, thickness 1; drawn only when enabled); the ``Output Image Type`` (source, pre-processed, binarized,
filtered, rescaled to the image size); the process time.

Fixed (internal defects, or behaviour that cannot be kept):

- Best circle ``Score`` kept the circle with the **lowest** score (``<`` against a running minimum, copied from the
  ``Centered`` case, ``d10``); it keeps the highest score here (owner question).
- ``Blobs`` (``d53``: local threshold, particle filters, ``IMAQ Find Circles``) is not used anymore (owner R8) and is
  not ported: a recipe that still selects it (``detect.vcsel.method`` 2) runs ``Shape``, with a warning in the
  application log; its settings (``binarymethod``, ``objecttype``, ``detectkernel``, ``removesmalls``,
  ``removesmallscount``, ``binaryinvert``, ``removeborder``, ``fillhole``, ``mincircfactor``, ``maxcircfactor``) are
  not read. The ``Binarized`` and ``Filtered`` output images were Blobs' intermediate images: the pre-processed image
  is shown instead.
- ``Obj learning`` flipped the match positions with ``hardware.camera.highmag.hflip/vflip``: the image is already
  flipped by the camera device, so the positions are used as found. The match radius, absent from NI's result, is
  half the smaller side of the model.
- The model path: ``detect.vcsel.modelpath`` when set (a relative path, as real recipes write it: ``"mesa_model.png"``,
  is in the recipe folder ``<settings>/profile``, as for Search Object and Tilt), else
  ``<settings>/profile/mesa_model.png`` (LabVIEW built a path in the Windows documents folder). The geometric matcher is the :class:`alox.vision.matching.Matcher` of Search
  Object (NI when available, else normalised cross-correlation with a rotation search over 0–360°, up to 50 matches;
  see :mod:`alox.vision.matching` for how the scores differ); the occlusion range is passed but the correlation
  matchers ignore it. The template is learned once per model file (and downsampling).
- The unused parameters (``Rectangle``, ``Occlusion``, ``Extend research``, the Hough ``MRQT`` values, ``Learn Mask
  Path``, ``OBJ LEARNING OCCLUSION``) are kept in the params for the settings panel and ignored, as in V1.2.6.2 or for
  lack of an equivalent (:mod:`alox.vision.edges`).
- The image comes from the caller or the image store; a missing image is error 9015 at ``Start`` (LabVIEW ran on an
  empty image).
- ``Shape`` (:func:`alox.vision.edges.detect_circles`) reports one circle per centre (the radius with the most edge
  pixels): of two concentric circles in the radius range (mesa and oxide aperture) only the stronger is listed.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from pathlib import Path
from typing import Any, Self, TypeVar

import numpy as np

from alox.core.functions import (
    FunctionContext,
    FunctionName,
    MeasurementFunction,
    Stopped,
)
from alox.functions.focus import CameraType
from alox.functions.services import Circle as CircleShape
from alox.functions.services import Services, Shape, camera_device_name
from alox.vision import edges, matching
from alox.vision.circles import Circle
from alox.vision.focus import lv_round, resample_nearest
from aloxcam.errors import AloxError
from aloxcam.imaging.png import PngError, read_png

__all__ = [
    "DETECT_CIRCLE_ERROR",
    "INVALID_ARGUMENTS",
    "INVALID_CALIBRATION",
    "AlgoMethod",
    "BestCircleMethod",
    "DCState",
    "DetectCircle",
    "DetectCircleParams",
    "DetectCircleReport",
    "Downsampling",
    "FunctionError",
    "LabelledEnum",
    "OutputImageType",
    "camera_name",
    "coerce_enum",
    "pixel_size_um",
    "run_states",
]

log = logging.getLogger(__name__)
_E = TypeVar("_E", bound=IntEnum)

#: ``alox_en.lang`` codes.
INVALID_ARGUMENTS = 9002                  # "invalid arguments error"
DETECT_CIRCLE_ERROR = 9015                # "detect circle' error (no circular vcsel found)"
INVALID_CALIBRATION = 9080                # "invalid calibration error"

OVERLAY = "detectcircle"
MAX_MATCHES = 50                          # matches requested from the matcher (Obj learning)
RED, BLUE = (255, 0, 0), (0, 0, 255)


# ================================================================================================== shared helpers
class FunctionError(AloxError):
    """A low-level function ended in its ``Error`` state: ``code``/``source`` as LabVIEW's error cluster, ``state``
    the state that failed, ``report`` the report at that moment (LabVIEW keeps the indicator)."""

    def __init__(self, code: int, source: str, *, state: str = "", report: Any = None) -> None:
        super().__init__(code, source)
        self.state = state
        self.report = report


class LabelledEnum(IntEnum):
    """An enum whose wire code is its value and whose LabVIEW item text is :attr:`label`."""

    @property
    def label(self) -> str:
        return self._labels()[self.value]

    @classmethod
    def _labels(cls) -> tuple[str, ...]:
        raise NotImplementedError

    @classmethod
    def coerce(cls, value: Any, what: str) -> Self:
        return coerce_enum(cls, value, what)


def coerce_enum(cls: type[_E], value: Any, what: str) -> _E:
    """A member of ``cls`` from a member, its code, its label (``"Obj learning"``) or its name (``"OBJ_LEARNING"``,
    any case); else :class:`FunctionError` 9002."""
    if isinstance(value, cls):
        return value
    if isinstance(value, str):
        text = value.strip()
        for member in cls:
            label = getattr(member, "label", member.name)
            if text in (label, member.name) or text.upper().replace(" ", "_") == member.name:
                return member
    elif isinstance(value, (int, np.integer)) and not isinstance(value, bool):
        try:
            return cls(int(value))
        except ValueError:
            pass
    raise FunctionError(INVALID_ARGUMENTS, f"{what}: invalid value {value!r}")


def _set(obj: object, name: str, value: Any) -> None:
    object.__setattr__(obj, name, value)


def _number(value: Any, what: str, *, integer: bool = False) -> Any:
    if isinstance(value, bool) or not isinstance(value, (int, float, np.integer, np.floating)):
        try:
            value = float(str(value).replace(",", "."))
        except ValueError:
            raise FunctionError(INVALID_ARGUMENTS, f"{what}: not a number: {value!r}") from None
    if not math.isfinite(float(value)):
        raise FunctionError(INVALID_ARGUMENTS, f"{what}: not finite: {value!r}")
    return round(float(value)) if integer else float(value)


def _flag(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().upper() in ("TRUE", "T", "1", "YES", "ON")
    return bool(value)


def camera_name(camera: Any) -> str:
    """``CameraType.HIGH_MAG`` / ``0`` / ``"High Mag."`` → ``"highmag"`` (the device and image-store name)."""
    if isinstance(camera, CameraType):
        camera = int(camera)
    return camera_device_name(camera)


def pixel_size_um(services: Services, camera: str, *, pixels_ok: bool = True) -> float:
    """The calibration of ``camera`` in µm per pixel: ``hardware.camera.<camera>.pixelsize`` in the unit
    ``hardware.camera.<camera>.units`` (0 pixels, 1 µm, 2 mm; ``core.PIXEL_UNITS``): mm ×1000, µm as is. Pixels: the
    value as is when ``pixels_ok`` (Detect Circle, Measure Oxyd), else :class:`FunctionError` 9080 (Refocus Oxyd's
    "µm ⇒ pixels" case structure); a value that is not a positive number is 9080 too."""
    raw = services.settings.get(f"hardware.camera.{camera}.pixelsize", 1.0)
    units = services.settings.get(f"hardware.camera.{camera}.units", 1)
    try:
        size = float(str(raw).replace(",", "."))
    except ValueError:
        size = math.nan
    key = str(units).strip().lower()
    if key in ("2", "mm", "millimeter", "millimetre"):
        size *= 1000.0
    elif key not in ("1", "um", "µm", "μm", "micrometer", "micrometre") and not pixels_ok:
        raise FunctionError(INVALID_CALIBRATION, f"invalid calibration error ({camera}: unit {units!r})")
    if not (math.isfinite(size) and size > 0):
        raise FunctionError(INVALID_CALIBRATION, f"invalid calibration error ({camera}: pixel size {raw!r})")
    return size


def app_log(services: Services, level: str, text: str) -> None:
    """The application log (``alox_manager_log``) when there is one, and Python logging."""
    getattr(log, level)(text)
    target = getattr(services.log, level, None) if services.log is not None else None
    if callable(target):
        try:
            target(text)
        except Exception:                                   # logging must never fail a function
            log.debug("application log failed", exc_info=True)


def run_states(ctx: FunctionContext, start: StrEnum, handlers: Mapping[StrEnum, Callable[[], StrEnum | None]], *,
               stopped: StrEnum, data: Callable[[StrEnum], dict[str, Any]] | None = None) -> None:
    """The worker loop of the LL state machines: one state per iteration, ``ctx.check()`` before each (a stop or an
    abort publishes ``stopped`` and re-raises :class:`Stopped`), ``ctx.set_state`` on every change. A handler
    returns the next state, or None to end (the ``Finished`` handler; the ``Error`` handler raises)."""
    state = start
    ctx.set_state(str(state), **(data(state) if data else {}))
    try:
        while True:
            ctx.check()
            nxt = handlers[state]()
            if nxt is None:
                return
            if nxt is not state:
                ctx.set_state(str(nxt), **(data(nxt) if data else {}))
            state = nxt
    except Stopped:
        ctx.set_state(str(stopped))
        raise


# ================================================================================================== enums
class AlgoMethod(LabelledEnum):
    """``alox_typedef_detect_circle_algo_method``. ``BLOBS`` is kept to read old recipes: it runs ``SHAPE`` (owner
    R8, module docstring)."""

    SHAPE = 0
    OBJ_LEARNING = 1
    BLOBS = 2

    @classmethod
    def _labels(cls) -> tuple[str, ...]:
        return ("Shape", "Obj learning", "Blobs")


class BestCircleMethod(LabelledEnum):
    """``alox_typedef_detect_circle_function_best_method``."""

    CENTERED = 0
    SCORE = 1
    LARGEST = 2

    @classmethod
    def _labels(cls) -> tuple[str, ...]:
        return ("Centered", "Score", "Largest")


class Downsampling(LabelledEnum):
    """``Pre Processings.Downsampling``: the size reduction (``75%`` keeps a quarter of the width, ``d10``)."""

    NONE = 0
    P25 = 1
    P50 = 2
    P75 = 3
    P90 = 4

    @classmethod
    def _labels(cls) -> tuple[str, ...]:
        return ("None", "25%", "50%", "75%", "90%")

    @property
    def factor(self) -> float:
        return (1.0, 0.75, 0.5, 0.25, 0.1)[self.value]


class OutputImageType(LabelledEnum):
    """``alox_typedef_detect_circle_function_output_image``."""

    SOURCE = 0
    PRE_PROCESSED = 1
    BINARIZED = 2
    FILTERED = 3

    @classmethod
    def _labels(cls) -> tuple[str, ...]:
        return ("Source", "Pre-Processed", "Binarized", "Filtered")


class DCState(StrEnum):
    """``alox_typedef_detect_circle_function_state``."""

    START = "Start"
    STARTED = "Started"
    DETECT_CIRCLE = "Detect Circle"
    FINISHED = "Finished"
    ERROR = "Error"
    STOPPED = "Stopped"


# ================================================================================================== params, report
_PROFILE_KEYS: dict[str, tuple[str, Any]] = {
    # field: (profile key under detect.vcsel., LabVIEW default of the bundle VI)
    "algo_method": ("method", 0),
    "best_circle_method": ("bestcirclemethod", 0),
    "radius_min_um": ("minradius", 20.0),
    "radius_max_um": ("maxradius", 50.0),
    "downsampling": ("downsamplemethod", 0),
    "morph_open": ("morphopen", 0),
    "morph_close": ("morphclose", 0),
    "morph_dilate": ("dilate", 0),
    "morph_erode": ("erode", 0),
    "edge_threshold": ("edgethreshold", 15),
    "min_length": ("edgeminlength", 15),
    "occlusion": ("edgeocclusion", True),
    "min_score": ("edgeminscore", 70.0),
    "extend_research": ("extendshape", True),
    "rectangles": ("rectangles", True),
    "ellipses": ("ellipses", True),
    "circles": ("circles", True),
    "overlay_best_circle": ("overlaybestcircle", False),
    "overlay_all_circles": ("overlayallcircles", False),
    "overlay_objects": ("overlayobjects", False),
    "hough_dp": ("dp", 1.25),
    "hough_min_dist": ("mindist", 1.25),
    "hough_param1": ("param1", 1.25),
    "hough_param2": ("param2", 1.25),
    "obj_score": ("scoreresult", 75.0),
    "obj_occlusion": ("occlusionresult", 30.0),
    "model_path": ("modelpath", ""),
    "mask_path": ("maskpath", ""),
}
_INTS = ("morph_open", "morph_close", "morph_dilate", "morph_erode", "edge_threshold", "min_length")
_FLOATS = ("radius_min_um", "radius_max_um", "min_score", "hough_dp", "hough_min_dist", "hough_param1",
           "hough_param2", "obj_score", "obj_occlusion")
_BOOLS = ("occlusion", "extend_research", "rectangles", "ellipses", "circles", "overlay_best_circle",
          "overlay_all_circles", "overlay_objects")


@dataclass(frozen=True)
class DetectCircleParams:
    """``alox_typedef_detect_circle_function_parameters`` (flattened, in cluster order; defaults = the bundle VI's
    profile defaults), then the worker inputs: ``camera`` (LabVIEW: the constant "High Mag."), ``image`` (``Image
    SRC/DST``; None = the newest image of ``camera`` in the image store) and ``output_image_type``."""

    algo_method: AlgoMethod = AlgoMethod.SHAPE
    # Circle Settings
    best_circle_method: BestCircleMethod = BestCircleMethod.CENTERED
    radius_min_um: float = 20.0
    radius_max_um: float = 50.0
    # Pre Processings
    downsampling: Downsampling = Downsampling.NONE
    morph_open: int = 0
    morph_close: int = 0
    morph_dilate: int = 0
    morph_erode: int = 0
    # Shape Settings
    edge_threshold: int = 15
    min_length: int = 15
    occlusion: bool = True                      # no equivalent (ignored)
    min_score: float = 70.0                     # % (× 10 → NI score 0–1000)
    extend_research: bool = True                # no equivalent (ignored)
    rectangles: bool = True                     # unused in V1.2.6.2
    ellipses: bool = True
    circles: bool = True
    # (Blobs Settings: not used anymore, owner R8)
    # Overlays
    overlay_best_circle: bool = False
    overlay_all_circles: bool = False
    overlay_objects: bool = False
    # MRQT (OpenCV HoughCircles values, unused in V1.2.6.2)
    hough_dp: float = 1.25
    hough_param2: float = 1.25
    hough_min_dist: float = 1.25
    hough_param1: float = 1.25
    # OBJ LEARNING
    obj_score: float = 75.0                     # % (× 10)
    obj_occlusion: float = 30.0                 # % (occlusion range of the matcher; ignored by correlation)
    model_path: str | Path = ""
    mask_path: str | Path = ""                  # unused (ignored)
    # worker inputs
    camera: CameraType = CameraType.HIGH_MAG
    image: np.ndarray | None = field(default=None, compare=False, repr=False)
    output_image_type: OutputImageType = OutputImageType.SOURCE

    def __post_init__(self) -> None:
        _set(self, "algo_method", AlgoMethod.coerce(self.algo_method, "Algo Method"))
        _set(self, "best_circle_method", BestCircleMethod.coerce(self.best_circle_method, "Best Circle Method"))
        _set(self, "downsampling", Downsampling.coerce(self.downsampling, "Downsampling"))
        _set(self, "output_image_type", OutputImageType.coerce(self.output_image_type, "Output Image Type"))
        _set(self, "camera", coerce_enum(CameraType, self.camera, "Camera Type"))
        for name in _INTS:
            _set(self, name, _number(getattr(self, name), name, integer=True))
        for name in _FLOATS:
            _set(self, name, _number(getattr(self, name), name))
        for name in _BOOLS:
            _set(self, name, _flag(getattr(self, name)))
        if self.radius_min_um < 0 or self.radius_max_um <= 0:
            raise FunctionError(INVALID_ARGUMENTS, f"Radius Min/Max (µm): invalid range "
                                                   f"[{self.radius_min_um}, {self.radius_max_um}]")
        if self.image is not None:
            image = np.asarray(self.image)
            if image.ndim != 2 or image.size == 0:
                raise FunctionError(INVALID_ARGUMENTS, f"image: 2-D grey image expected, got shape {image.shape}")
            _set(self, "image", image)

    @classmethod
    def from_profile(cls, profile: Any, **overrides: Any) -> DetectCircleParams:
        """``alox_function_ll_detect_circle_bundle_profile_parameters``: the ``detect.vcsel.*`` keys of the active
        profile (``profile.get(key, default)``), LabVIEW's defaults for the missing ones; ``overrides`` win."""
        values = {name: profile.get(f"detect.vcsel.{key}", default) for name, (key, default) in _PROFILE_KEYS.items()}
        values.update(overrides)
        return cls(**values)


@dataclass(frozen=True)
class DetectCircleReport:
    """``alox_typedef_detect_circle_function_result``: ``best_circle`` (None when nothing was found),
    ``all_circles`` (with their score 0–1000), ``best_index`` (−1: none), ``found``, ``process_time_ms``; plus the
    calibration used and the image size ``(width, height)``."""

    best_circle: Circle | None
    all_circles: tuple[edges.DetectedCircle, ...]
    best_index: int
    found: bool
    process_time_ms: float
    pixel_size_um: float = math.nan
    image_size: tuple[int, int] = (0, 0)


# ================================================================================================== the function
class DetectCircle(MeasurementFunction):
    """Detect Circle (``alox_function_ll_detect_circle``): ``run`` returns a :class:`DetectCircleReport`; raises
    :class:`FunctionError` 9015 when no circle is found (or no image), 9002 for invalid parameters,
    :class:`alox.core.functions.Stopped` on stop/abort. ``params`` None: :meth:`DetectCircleParams.from_profile`."""

    name = FunctionName.DETECT_CIRCLE

    def __init__(self, services: Services, matcher: matching.Matcher | None = None) -> None:
        self.services = services
        self._matcher = matcher
        self._templates: dict[tuple[str, float, float], tuple[matching.Template, float]] = {}
        self._last: DetectCircleReport | None = None
        self._params: DetectCircleParams | None = None

    @property
    def matcher(self) -> matching.Matcher:
        """The ``Obj learning`` matcher (default :func:`alox.vision.matching.create_matcher`, built at first use)."""
        if self._matcher is None:
            self._matcher = matching.create_matcher()
            log.info("detect circle: %s matcher", self._matcher.name)
        return self._matcher

    def template(self, path: Path, factor: float) -> tuple[matching.Template, float]:
        """The learned model of ``path`` at the downsampling ``factor`` and its radius (half the smaller side),
        cached while the file is unchanged; :class:`FunctionError` 9015 when it cannot be read or learned."""
        try:
            key = (str(path), path.stat().st_mtime, factor)
            if key not in self._templates:
                model = read_png(path)
                if model.ndim == 3:
                    model = model.mean(axis=2)
                if factor != 1.0:
                    mh, mw = model.shape
                    model = resample_nearest(model, max(2, lv_round(mw * factor)), max(2, lv_round(mh * factor)))
                self._templates = {key: (self.matcher.learn(np.asarray(model)), min(model.shape) / 2.0)}
        except (OSError, PngError, ValueError, matching.MatcherUnavailable) as exc:
            raise FunctionError(DETECT_CIRCLE_ERROR, f"detect circle: model image {path}: {exc}") from exc
        return self._templates[key]

    # LabVIEW "Get Results", "Force Results", "Set Params"
    @property
    def last_report(self) -> DetectCircleReport | None:
        return self._last

    def force_results(self, report: DetectCircleReport | Circle) -> DetectCircleReport:
        """Replace the last report without running (a :class:`Circle` becomes a found report of that circle)."""
        if isinstance(report, Circle):
            report = DetectCircleReport(report, (edges.DetectedCircle(report.x, report.y, report.radius, 1000.0),),
                                        0, True, 0.0)
        self._last = report
        return report

    def set_params(self, params: DetectCircleParams) -> None:
        """The params used by :meth:`run` when it is called with None."""
        self._params = params

    def run(self, params: DetectCircleParams | None, ctx: FunctionContext) -> DetectCircleReport:
        if params is None:
            params = self._params or DetectCircleParams.from_profile(self.services.profile)
        if not isinstance(params, DetectCircleParams):
            raise FunctionError(INVALID_ARGUMENTS, f"detect circle: DetectCircleParams expected, got "
                                                   f"{type(params).__name__}")
        job = _DetectJob(self, params, ctx)
        try:
            return job.execute()
        finally:
            if job.report is not None:
                self._last = job.report


class _DetectJob:
    def __init__(self, function: DetectCircle, p: DetectCircleParams, ctx: FunctionContext) -> None:
        self.f = function
        self.s = function.services
        self.p = p
        self.ctx = ctx
        self.camera = camera_name(p.camera)
        self.image: np.ndarray | None = None
        self.pixel_size = math.nan
        self.t0 = 0.0
        self.report: DetectCircleReport | None = None
        self.error: FunctionError | None = None
        self.failed_state = ""

    def execute(self) -> DetectCircleReport:
        handlers = {DCState.START: self.start, DCState.STARTED: self.started, DCState.DETECT_CIRCLE: self.detect,
                    DCState.FINISHED: self.finished, DCState.ERROR: self.failed}
        run_states(self.ctx, DCState.START, handlers, stopped=DCState.STOPPED)
        assert self.report is not None
        return self.report

    def fail(self, exc: FunctionError, state: DCState) -> DCState:
        self.error, self.failed_state = exc, str(state)
        return DCState.ERROR

    # "Init State machine here"
    def start(self) -> DCState:
        self.t0 = self.s.clock()
        image = self.p.image if self.p.image is not None else self.s.images.last(self.camera)
        if image is None:
            return self.fail(FunctionError(DETECT_CIRCLE_ERROR, f"detect circle: no image of {self.camera}"),
                             DCState.START)
        self.image = np.asarray(image)
        return DCState.STARTED

    # "Post start initialization": the calibration (µm per pixel)
    def started(self) -> DCState:
        try:
            self.pixel_size = pixel_size_um(self.s, self.camera)
        except FunctionError as exc:
            return self.fail(exc, DCState.STARTED)
        return DCState.DETECT_CIRCLE

    # "Processing", "Update DST image", results
    def detect(self) -> DCState:
        p, image = self.p, self.image
        assert image is not None
        h, w = image.shape
        factor = p.downsampling.factor
        work = image
        if factor != 1.0:
            work = resample_nearest(image, max(1, lv_round(w * factor)), max(1, lv_round(h * factor)))
        for operation, size in (("open", p.morph_open), ("close", p.morph_close), ("erode", p.morph_erode),
                                ("dilate", p.morph_dilate)):
            work = edges.grey_morphology(work, operation, size)
        r_min = p.radius_min_um / self.pixel_size * factor          # "µm ⇒ Pixels" (downsampled image)
        r_max = p.radius_max_um / self.pixel_size * factor
        method = p.algo_method
        if method is AlgoMethod.BLOBS:
            app_log(self.s, "warning", "Detect Circle: the method Blobs is not used anymore, Shape is used "
                                       "(detect.vcsel.method)")
            method = AlgoMethod.SHAPE
        try:
            found = self._shape(work, r_min, r_max) if method is AlgoMethod.SHAPE else self._obj_learning(work)
        except FunctionError as exc:
            return self.fail(exc, DCState.DETECT_CIRCLE)
        circles = tuple(edges.DetectedCircle(c.x / factor, c.y / factor, c.radius / factor, c.score) for c in found)
        best = self._best(circles, w, h)
        ok = best >= 0 and len(circles) > 0
        self.report = DetectCircleReport(
            best_circle=Circle(circles[best].x, circles[best].y, circles[best].radius) if ok else None,
            all_circles=circles, best_index=best, found=ok, process_time_ms=(self.s.clock() - self.t0) * 1000.0,
            pixel_size_um=self.pixel_size, image_size=(w, h))
        self._output_image(work, w, h)
        self._overlay(circles, best)
        self.ctx.set_state(str(DCState.DETECT_CIRCLE), found=ok, best_circle=self.report.best_circle,
                           count=len(circles))
        if not ok:
            return self.fail(FunctionError(DETECT_CIRCLE_ERROR, "detect circle' error (no circular vcsel found)"),
                             DCState.DETECT_CIRCLE)
        return DCState.FINISHED

    def _shape(self, work: np.ndarray, r_min: float, r_max: float) -> list[edges.DetectedCircle]:
        p = self.p
        kwargs = {"edge_threshold": p.edge_threshold, "min_length": p.min_length, "min_score": p.min_score * 10.0}
        found: list[edges.DetectedCircle] = []
        if p.circles:                                               # "IMAQ Detect Circles"
            found += edges.detect_circles(work, r_min, r_max, **kwargs)
        if p.ellipses:                                              # "IMAQ Detect Ellipses", radius (minor + major)/2
            found += edges.detect_circles(work, r_min, r_max, ellipses=True, **kwargs)
        return found

    def _obj_learning(self, work: np.ndarray) -> list[edges.DetectedCircle]:
        """``IMAQ Setup/Match Geometric Pattern 2`` (rotation 0–360°, score × 10) with the model PNG."""
        p = self.p
        path = Path(str(p.model_path)) if str(p.model_path) else None
        folders = self.s.folders
        if path is None and folders is not None:
            path = Path(folders.profile) / "mesa_model.png"
        elif path is not None and not path.is_absolute() and folders is not None:
            path = Path(folders.profile) / path         # a bare name (real recipes: "mesa_model.png"): the recipe's file
        if path is None:
            raise FunctionError(DETECT_CIRCLE_ERROR, "detect circle: no model image (Search Model Path)")
        factor = p.downsampling.factor
        template, radius = self.f.template(path, factor)
        settings = matching.MatchSettings(number_of_matches=MAX_MATCHES, min_score=p.obj_score * 10.0,
                                          angle_ranges=((0.0, 360.0),), occlusion_range=(0.0, p.obj_occlusion))
        try:
            matches = self.f.matcher.match(work, template, settings)
        except (matching.MatcherUnavailable, matching.TemplateError, ValueError) as exc:
            raise FunctionError(DETECT_CIRCLE_ERROR, f"detect circle: object learning: {exc}") from exc
        return [edges.DetectedCircle(m.position[0], m.position[1], radius, m.score) for m in matches]

    def _best(self, circles: tuple[edges.DetectedCircle, ...], w: int, h: int) -> int:
        """``Best Circle Method`` (``d22…d30``): first strict best wins; −1 when there is no circle."""
        best, best_key = -1, math.inf
        for i, c in enumerate(circles):
            if self.p.best_circle_method is BestCircleMethod.CENTERED:
                key = math.hypot(c.x - w / 2.0, c.y - h / 2.0)
            elif self.p.best_circle_method is BestCircleMethod.LARGEST:
                key = -c.radius
            else:
                key = -c.score                                     # Fixed: LabVIEW kept the lowest score
            if key < best_key:
                best, best_key = i, key
        return best

    def _output_image(self, work: np.ndarray, w: int, h: int) -> None:
        """"Update DST image": the selected intermediate image, rescaled to the image size ("Rescale TMP image");
        ``Binarized`` / ``Filtered`` (Blobs' images) show the pre-processed image."""
        if self.p.output_image_type is OutputImageType.SOURCE:
            return
        out = work
        if out.shape != (h, w):
            out = resample_nearest(out, w, h)
        self.s.images.put(self.camera, np.ascontiguousarray(out), metadata={"function": "detect_circle"})

    def _overlay(self, circles: tuple[edges.DetectedCircle, ...], best: int) -> None:
        """Group ``LL_DETECT_CIRCLE``, cleared first: the best circle (red, 10) and the others (blue, 1)."""
        shapes: list[Shape] = []
        if self.p.overlay_all_circles:
            shapes += [CircleShape(c.x, c.y, c.radius, BLUE, 1) for i, c in enumerate(circles) if i != best]
        if self.p.overlay_best_circle and 0 <= best < len(circles):
            c = circles[best]
            shapes.append(CircleShape(c.x, c.y, c.radius, RED, 10))
        self.s.images.set_overlay(OVERLAY, shapes)

    def finished(self) -> None:
        return None

    def failed(self) -> None:
        err = self.error or FunctionError(DETECT_CIRCLE_ERROR, "detect circle: unknown error")
        if self.report is None:
            w, h = (self.image.shape[1], self.image.shape[0]) if self.image is not None else (0, 0)
            self.report = DetectCircleReport(None, (), -1, False, (self.s.clock() - self.t0) * 1000.0,
                                             self.pixel_size, (w, h))
        app_log(self.s, "error", f"Detect Circle (Detect): {err.code} {err.source}")
        raise FunctionError(err.code, err.source, state=self.failed_state, report=self.report)
