"""Measure Oxyd (LabVIEW "Measure Mesa"): the mesa and oxide-aperture diameters in one image, as a measurement function.

Spec: ``docs/vision-core/map.md`` Part 3 §2.1, §3.10; Part 4 §3.4 (edges), §3.7 (circles and roundness), §3.8
(``tools_alox_bound_measured_values``, ``alox_tools_convert_mesa_measures_to_micrometers``). Diagrams:
``alox_function_ll_measure_mesa_state_machine d`` (controls ``Image``, ``Circle In``, ``Measure Mesa Profile``),
``d40`` (case ``"Circular", Default`` of ``Mesa Shape Type``), ``d41…d69`` (its sub-cases), ``d145`` (frame 3: end
state), ``alox_function_ll_measure_mesa_bundle_profile_parameters d`` (profile keys and defaults),
``tools_alox_bound_measured_values d, d1…d4``. Formulas: :mod:`alox.vision.circles`; NI tools:
:mod:`alox.vision.edges`.

Data flow (LabVIEW ``Circle In``, written by the HL mesa from Detect Circle's ``Get Results``): :attr:`MeasureOxydParams.
circle` is the circle found by Detect Circle (:attr:`alox.functions.detect_circle.DetectCircleReport.best_circle`) and
is required; :attr:`MeasureOxydParams.image` is the caller's image (None: the newest image of the camera in the image
store). The results are in **pixels**, as LabVIEW's; :attr:`MeasureOxydReport.diameters_um` applies
``alox_tools_convert_mesa_measures_to_micrometers`` (the seven diameters External…Circumscribed × pixel size).

``Measure`` (case ``Circular``):

1. mesa: ``IMAQ Find Circular Edge`` on the annulus ``[Mesa Min Diam/2, Mesa Max Diam/2]`` (µm → px) centred on
   ``Circle In``, angles 0–359, mesa search direction and edge options, step = Mesa Step; not found (or an empty
   annulus, e.g. the default ``maxMesaDiam`` 0): the external circle is ``Circle In`` (changelog 1.0.0.11) and
   ``Mesa Found`` is FALSE;
2. oxide: ``IMAQ Spoke`` "Get First Edge" on ``[Oxyd Min Diam/2, Oxyd Max Diam/2]`` (Oxyd Min Diam is the constant 0
   of the bundle VI) centred on the external circle, oxide direction and edge options, step = Oxyd Step;
3. ``IMAQ Fit Circle 2`` on the edge points (Fit Max Dist., Fit Min Score, Fit Max Iterations, Reject Outliers): the
   oxide circle; the points used are the inliers when Reject Outliers, else all;
4. diameters (pixels): Fit = 2·R of the fitted circle; Mean, Median, STDDEV = 2 × mean, median, sample standard
   deviation of the distances of the used points to the fitted centre (``Use Fitted Circle Center``; to the
   circumscribed centre for the measure type ``Circumscribed``); Surface, Inscribed, Circumscribed from
   :func:`alox.vision.circles.contour_diameters` (minimum-zone circles, Elzinga–Hearn / Delaunay fallback, 0 → NaN);
   ``Circ/Insc Ratio`` = circumscribed / inscribed; ``Accuracy`` = 100·(circumscribed − inscribed)/inscribed (as read
   in ``d40``: this is the subtraction of owner question M3, it feeds Accuracy, not the ratio); External = 2·R of the
   external circle; one chord line per used point (through the centre) and its length;
5. ``tools_alox_bound_measured_values``: External·px outside ``[Mesa Min, Mesa Max]`` µm → NaN; Fit outside
   ``[0, Oxyd Max/px]`` px → Fit, Median, Mean, Surface, Inscribed, Circumscribed NaN;
6. ``oxid result offset`` (µm) / px added to the oxide diameters;
7. frame 3: ``Finished`` when (Mesa Found or not ``measure.vcsel.MeasMesaMandatory``) and (Oxyd Found or not
   ``measure.oxyd.MeasOxydMandatory``), else ``Error``; the Fit Circle error "too few points" is not an error ("Oxyd
   missing is normal when starting process").

Reproduced (LabVIEW): states and transitions; params, profile keys and defaults; the steps above; the overlay switches
(external circle, mesa search lines and edges, oxide circle, oxide edges, diameters, target circle, measure text);
results in pixels; Gradient Values = the edge-strength profile along the first oxide search line.

Fixed:

- The offset (step 6) was added to the whole diameters cluster by LabVIEW's polymorphic ``+``, i.e. also to
  ``Circ/Insc Ratio``, ``STDDEV`` and ``Accuracy`` (External was restored after); only the six oxide diameters get it.
- The dead fallback "inscribed = NaN → 1.1 × surface" (compared with ``=`` NaN, never TRUE) is not reproduced.
- When the oxide is not found the error state carries a code: 9022 "measure oxyd error (none circle found)" for a
  required mesa, 9021 "measure oxyd error (processing)" for a required oxide (LabVIEW reached ``Error`` with an
  empty error cluster); no image or no ``Circle In``: 9023 "(image/region of interest)".
- Mesa Shape Type ``Line`` and ``Rectangle`` (radial/linear rakes, ``d13``, ``d70``) are not implemented: 9021 at
  ``Start`` (owner question; the default is ``Circular``).
- The global ``MEAS MESA RUNNING`` (which silenced error 9022 inside the HL mesa) is not used: the mandatory flags
  decide.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import numpy as np

from alox.core.functions import FunctionContext, FunctionName, MeasurementFunction
from alox.functions.detect_circle import (
    INVALID_ARGUMENTS,
    FunctionError,
    LabelledEnum,
    app_log,
    camera_name,
    coerce_enum,
    pixel_size_um,
    run_states,
)
from alox.functions.focus import CameraType
from alox.functions.services import Circle as CircleShape
from alox.functions.services import Line as LineShape
from alox.functions.services import Points, Services, Shape, Text
from alox.vision import edges
from alox.vision.circles import (
    NAN_CIRCLE,
    Circle,
    circumscribed_circle,
    contour_diameters,
)
from alox.vision.edges import EdgeOptions, EdgePolarity, SearchDirection
from alox.vision.stats import OxideMeasureType, sample_std

__all__ = [
    "MEASURE_OXYD_IMAGE",
    "MEASURE_OXYD_NO_MESA",
    "MEASURE_OXYD_PROCESSING",
    "DiametersReport",
    "MOState",
    "MeasureOxyd",
    "MeasureOxydParams",
    "MeasureOxydReport",
    "MesaShapeType",
]

log = logging.getLogger(__name__)

MEASURE_OXYD_PROCESSING = 9021            # "measure oxyd error (processing)"
MEASURE_OXYD_NO_MESA = 9022               # "measure oxyd error (none circle found)"
MEASURE_OXYD_IMAGE = 9023                 # "measure oxyd error (image/region of interest)"

GREEN, RED, BLUE, YELLOW, CYAN, MAGENTA = (0, 255, 0), (255, 0, 0), (0, 0, 255), (255, 255, 0), (0, 255, 255), \
    (255, 0, 255)


class MesaShapeType(LabelledEnum):
    """``alox_typedef_measure_oxyd_mesa_shape_type``."""

    CIRCULAR = 0
    LINE = 1
    RECTANGLE = 2

    @classmethod
    def _labels(cls) -> tuple[str, ...]:
        return ("Circular", "Line", "Rectangle")


class MOState(StrEnum):
    """``alox_typedef_measure_mesa_function_state``."""

    START = "Start"
    STARTED = "Started"
    MEASURE = "Measure"
    FINISHED = "Finished"
    ERROR = "Error"
    STOPPED = "Stopped"


_DIRECTION_LABELS = {SearchDirection.OUTSIDE_TO_INSIDE: "Outside To Inside",
                     SearchDirection.INSIDE_TO_OUTSIDE: "Inside To Outside"}
_POLARITY_LABELS = {EdgePolarity.ALL: "All", EdgePolarity.RISING: "Rising Edges", EdgePolarity.FALLING: "Falling Edges"}
_MEASURE_LABELS = {OxideMeasureType.MEAN: "Mean", OxideMeasureType.MEDIAN: "Median", OxideMeasureType.FIT: "Fit",
                   OxideMeasureType.SURFACE: "Surface", OxideMeasureType.CIRCUMSCRIBED: "Circumscribed",
                   OxideMeasureType.INSCRIBED: "Inscribed"}


def _labelled(cls: type, value: Any, labels: dict[Any, str], what: str) -> Any:
    if isinstance(value, str):
        for member, label in labels.items():
            if value.strip() == label:
                return member
    return coerce_enum(cls, value, what)


# ================================================================================================== params
#: field: (profile key, LabVIEW default of the bundle VI); "" = not read by the bundle VI (typedef default kept).
_PROFILE_KEYS: dict[str, tuple[str, Any]] = {
    "mesa_search_dir": ("measure.vcsel.direction", 0),
    "mesa_polarity": ("measure.vcsel.polarity", 0),
    "mesa_strength": ("measure.vcsel.minstrength", 10.0),
    "mesa_kernel": ("measure.vcsel.kernelsize", 3),
    "mesa_width": ("measure.vcsel.width", 3),
    "mesa_step": ("measure.vcsel.stepsize", 5.0),
    "mesa_min_diam_um": ("measure.vcsel.minMesaDiam", 5.0),
    "oxyd_search_dir": ("measure.oxyd.direction", 1),
    "oxyd_polarity": ("measure.oxyd.polarity", 0),
    "oxyd_strength": ("measure.oxyd.minstrength", 10.0),
    "oxyd_kernel": ("measure.oxyd.kernelsize", 3),
    "oxyd_width": ("measure.oxyd.width", 3),
    "oxyd_step": ("measure.oxyd.stepsize", 5.0),
    "oxyd_max_diam_um": ("measure.oxyd.oxydmax", 50.0),
    "oxyd_measure_type": ("measure.oxyd.measuretype", 0),
    "target_diam_um": ("measure.oxyd.oxydtarget", 25.0),
    "fit_max_dist": ("measure.oxyd.fitmaxdistance", 3),
    "fit_min_score": ("measure.oxyd.fitminscore", 500.0),
    "fit_max_iterations": ("measure.oxyd.fitmaxiterations", 500),
    "reject_outliers": ("measure.oxyd.rejectoutliers", True),
    "overlay_external": ("measure.oxyd.overlaymesa", True),
    "overlay_oxyd_diameters": ("measure.oxyd.overlaydiameters", True),
    "overlay_oxyd": ("measure.oxyd.overlayoxyd", True),
    "overlay_target": ("measure.oxyd.overlaytarget", True),
    "overlay_oxyd_edges": ("measure.oxyd.overlaypoints", True),
    "overlay_oxyd_measure": ("measure.oxyd.overlaymeasure", True),
    "mesa_max_diam_um": ("measure.vcsel.maxMesaDiam", 0.0),
    "overlay_mesa_search_lines": ("measure.vcsel.mesaSearchLines", True),
    "overlay_mesa_edges": ("measure.vcsel.overlaymesaedges", True),
    "mesa_shape_type": ("measure.vcsel.mesashapetype", 0),
    "oxid_result_offset_um": ("measure.oxyd.resultoffset", 0.0),
    "mesa_mandatory": ("measure.vcsel.MeasMesaMandatory", True),
    "oxyd_mandatory": ("measure.oxyd.MeasOxydMandatory", True),
}
_FLOATS = ("mesa_strength", "mesa_step", "mesa_min_diam_um", "oxyd_strength", "oxyd_step", "oxyd_max_diam_um",
           "target_diam_um", "fit_min_score", "mesa_max_diam_um", "oxyd_min_diam_um", "oxid_result_offset_um")
_INTS = ("mesa_kernel", "mesa_width", "oxyd_kernel", "oxyd_width", "fit_max_dist", "fit_max_iterations")
_BOOLS = ("reject_outliers", "overlay_external", "overlay_oxyd_diameters", "overlay_oxyd", "overlay_target",
          "overlay_oxyd_edges", "overlay_oxyd_measure", "overlay_mesa_search_lines", "overlay_mesa_edges", "meas_mesa",
          "meas_oxid", "mesa_mandatory", "oxyd_mandatory")


def _number(value: Any, what: str, *, integer: bool = False) -> Any:
    try:
        number = float(str(value).replace(",", ".")) if isinstance(value, str) else float(value)
    except (TypeError, ValueError):
        raise FunctionError(INVALID_ARGUMENTS, f"{what}: not a number: {value!r}") from None
    if isinstance(value, bool) or not math.isfinite(number):
        raise FunctionError(INVALID_ARGUMENTS, f"{what}: invalid value {value!r}")
    return round(number) if integer else number


def _flag(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().upper() in ("TRUE", "T", "1", "YES", "ON")
    return bool(value)


@dataclass(frozen=True)
class MeasureOxydParams:
    """``alox_typedef_measure_mesa_function_parameters`` (in cluster order; defaults = the bundle VI's profile
    defaults), then the worker inputs and the two "mandatory" profile flags the state machine reads itself."""

    mesa_search_dir: SearchDirection = SearchDirection.OUTSIDE_TO_INSIDE
    mesa_polarity: EdgePolarity = EdgePolarity.ALL
    mesa_strength: float = 10.0
    mesa_kernel: int = 3
    mesa_width: int = 3
    mesa_step: float = 5.0
    mesa_min_diam_um: float = 5.0
    oxyd_search_dir: SearchDirection = SearchDirection.INSIDE_TO_OUTSIDE
    oxyd_polarity: EdgePolarity = EdgePolarity.ALL
    oxyd_strength: float = 10.0
    oxyd_kernel: int = 3
    oxyd_width: int = 3
    oxyd_step: float = 5.0
    oxyd_max_diam_um: float = 50.0
    oxyd_measure_type: OxideMeasureType = OxideMeasureType.MEAN
    target_diam_um: float = 25.0
    fit_max_dist: int = 3                       # px (IMAQ Fit Circle 2 "Pixel Radius")
    fit_min_score: float = 500.0                # 0–1000
    fit_max_iterations: int = 500
    reject_outliers: bool = True
    overlay_external: bool = True
    overlay_oxyd_diameters: bool = True
    overlay_oxyd: bool = True
    overlay_target: bool = True
    overlay_oxyd_edges: bool = True
    overlay_oxyd_measure: bool = True
    mesa_max_diam_um: float = 0.0
    oxyd_min_diam_um: float = 0.0               # the bundle VI wires the constant 0
    overlay_mesa_search_lines: bool = True
    overlay_mesa_edges: bool = True
    meas_mesa: bool = True                      # not read from the profile; kept for the panel (unused)
    meas_oxid: bool = True                      # idem
    mesa_shape_type: MesaShapeType = MesaShapeType.CIRCULAR
    oxid_result_offset_um: float = 0.0
    # worker inputs
    circle: Circle | None = None                # "Circle In": Detect Circle's best circle (pixels), required
    image: np.ndarray | None = field(default=None, compare=False, repr=False)
    camera: CameraType = CameraType.HIGH_MAG
    # profile flags read by frame 3 (and by tools_mesa_averaged_measurement)
    mesa_mandatory: bool = True                 # measure.vcsel.MeasMesaMandatory
    oxyd_mandatory: bool = True                 # measure.oxyd.MeasOxydMandatory

    def __post_init__(self) -> None:
        s = object.__setattr__
        s(self, "mesa_search_dir", _labelled(SearchDirection, self.mesa_search_dir, _DIRECTION_LABELS,
                                             "Mesa Search Dir"))
        s(self, "oxyd_search_dir", _labelled(SearchDirection, self.oxyd_search_dir, _DIRECTION_LABELS,
                                             "Oxyd Search Dir"))
        s(self, "mesa_polarity", _labelled(EdgePolarity, self.mesa_polarity, _POLARITY_LABELS, "Mesa Edge Polarity"))
        s(self, "oxyd_polarity", _labelled(EdgePolarity, self.oxyd_polarity, _POLARITY_LABELS, "Oxyd Edge Polarity"))
        s(self, "oxyd_measure_type", _labelled(OxideMeasureType, self.oxyd_measure_type, _MEASURE_LABELS,
                                               "Oxyd Measure Type"))
        s(self, "mesa_shape_type", MesaShapeType.coerce(self.mesa_shape_type, "Mesa Shape Type"))
        s(self, "camera", coerce_enum(CameraType, self.camera, "Camera Type"))
        for name in _FLOATS:
            s(self, name, _number(getattr(self, name), name))
        for name in _INTS:
            s(self, name, _number(getattr(self, name), name, integer=True))
        for name in _BOOLS:
            s(self, name, _flag(getattr(self, name)))
        if self.circle is not None and not isinstance(self.circle, Circle):
            c = self.circle
            try:
                s(self, "circle", Circle(*c) if isinstance(c, (tuple, list)) else Circle(c.x, c.y, c.radius))
            except (TypeError, AttributeError, ValueError):
                raise FunctionError(INVALID_ARGUMENTS, f"Circle In: invalid circle {c!r}") from None
        if self.image is not None:
            image = np.asarray(self.image)
            if image.ndim != 2 or image.size == 0:
                raise FunctionError(INVALID_ARGUMENTS, f"image: 2-D grey image expected, got shape {image.shape}")
            s(self, "image", image)

    @classmethod
    def from_profile(cls, profile: Any, **overrides: Any) -> MeasureOxydParams:
        """``alox_function_ll_measure_mesa_bundle_profile_parameters`` (+ the two mandatory flags); ``overrides``
        (e.g. ``circle=``, ``image=``) win."""
        values = {name: profile.get(key, default) for name, (key, default) in _PROFILE_KEYS.items()}
        values.update(overrides)
        return cls(**values)


# ================================================================================================== report
@dataclass(frozen=True)
class DiametersReport:
    """``alox_typedef_measure_mesa_diameters_report`` (pixels; ratio and accuracy unitless)."""

    external: float = math.nan
    fit: float = math.nan
    median: float = math.nan
    mean: float = math.nan
    surface: float = math.nan
    inscribed: float = math.nan
    circumscribed: float = math.nan
    circ_insc_ratio: float = math.nan
    stddev: float = math.nan
    accuracy: float = math.nan

    def scaled(self, pixel_size_um: float) -> DiametersReport:
        """``alox_tools_convert_mesa_measures_to_micrometers``: External…Circumscribed × pixel size (µm)."""
        k = pixel_size_um
        return DiametersReport(self.external * k, self.fit * k, self.median * k, self.mean * k, self.surface * k,
                               self.inscribed * k, self.circumscribed * k, self.circ_insc_ratio, self.stddev,
                               self.accuracy)

    def selected(self, measure_type: OxideMeasureType) -> float:
        """The diameter of ``Oxyd Measure Type``."""
        return {OxideMeasureType.MEAN: self.mean, OxideMeasureType.MEDIAN: self.median, OxideMeasureType.FIT: self.fit,
                OxideMeasureType.SURFACE: self.surface, OxideMeasureType.CIRCUMSCRIBED: self.circumscribed,
                OxideMeasureType.INSCRIBED: self.inscribed}[OxideMeasureType(measure_type)]


Segment = tuple[float, float, float, float]


@dataclass(frozen=True)
class MeasureOxydReport:
    """``alox_typedef_measure_mesa_function_results`` (pixels), plus the calibration and the oxide fit score."""

    external_circle: Circle
    oxyd_circle: Circle
    diameters: DiametersReport
    diameter_lines: tuple[Segment, ...]
    diameter_lengths: tuple[float, ...]
    oxyd_points: tuple[tuple[float, float], ...]
    mesa_found: bool
    oxyd_found: bool
    process_time_ms: float
    oxyd_search_lines: tuple[Segment, ...]
    mesa_search_lines: tuple[Segment, ...]
    mesa_points: tuple[tuple[float, float], ...]
    gradient_values: tuple[float, ...]
    pixel_size_um: float = math.nan
    fit_score: float = 0.0

    @property
    def diameters_um(self) -> DiametersReport:
        return self.diameters.scaled(self.pixel_size_um)


def _segments(lines: Any) -> tuple[Segment, ...]:
    return tuple((float(a[0]), float(a[1]), float(b[0]), float(b[1])) for a, b in lines)


# ================================================================================================== the function
class MeasureOxyd(MeasurementFunction):
    """Measure Oxyd (``alox_function_ll_measure_mesa``): ``run`` returns a :class:`MeasureOxydReport`; raises
    :class:`FunctionError` 9021/9022/9023 (or 9002 for invalid parameters) from the ``Error`` state,
    :class:`alox.core.functions.Stopped` on stop/abort. ``params`` None: the profile, with the circle of
    :meth:`set_params` (or none: 9023)."""

    name = FunctionName.MEASURE_OXYD

    def __init__(self, services: Services) -> None:
        self.services = services
        self._last: MeasureOxydReport | None = None
        self._params: MeasureOxydParams | None = None

    @property
    def last_report(self) -> MeasureOxydReport | None:
        """LabVIEW "Get Results"."""
        return self._last

    def set_params(self, params: MeasureOxydParams) -> None:
        """LabVIEW "Set Params": the params used when :meth:`run` gets None."""
        self._params = params

    def run(self, params: MeasureOxydParams | None, ctx: FunctionContext) -> MeasureOxydReport:
        if params is None:
            params = self._params or MeasureOxydParams.from_profile(self.services.profile)
        if not isinstance(params, MeasureOxydParams):
            raise FunctionError(INVALID_ARGUMENTS, f"measure oxyd: MeasureOxydParams expected, got "
                                                   f"{type(params).__name__}")
        job = _MeasureJob(self, params, ctx)
        try:
            return job.execute()
        finally:
            if job.report is not None:
                self._last = job.report


class _MeasureJob:
    def __init__(self, function: MeasureOxyd, p: MeasureOxydParams, ctx: FunctionContext) -> None:
        self.s = function.services
        self.p = p
        self.ctx = ctx
        self.camera = camera_name(p.camera)
        self.image: np.ndarray | None = None
        self.px = math.nan
        self.t0 = 0.0
        self.report: MeasureOxydReport | None = None
        self.error: FunctionError | None = None
        self.failed_state = ""

    def execute(self) -> MeasureOxydReport:
        handlers = {MOState.START: self.start, MOState.STARTED: self.started, MOState.MEASURE: self.measure,
                    MOState.FINISHED: self.finished, MOState.ERROR: self.failed}
        run_states(self.ctx, MOState.START, handlers, stopped=MOState.STOPPED)
        assert self.report is not None
        return self.report

    def fail(self, exc: FunctionError, state: MOState) -> MOState:
        self.error, self.failed_state = exc, str(state)
        return MOState.ERROR

    def start(self) -> MOState:
        self.t0 = self.s.clock()
        p = self.p
        if p.mesa_shape_type is not MesaShapeType.CIRCULAR:
            return self.fail(FunctionError(MEASURE_OXYD_PROCESSING, f"measure oxyd: Mesa Shape Type "
                                                                    f"{p.mesa_shape_type.label} is not implemented"),
                             MOState.START)
        circle = p.circle
        if circle is None or circle.is_nan:
            return self.fail(FunctionError(MEASURE_OXYD_IMAGE, "measure oxyd error (image/region of interest): "
                                                               "no input circle"), MOState.START)
        image = p.image if p.image is not None else self.s.images.last(self.camera)
        if image is None:
            return self.fail(FunctionError(MEASURE_OXYD_IMAGE, f"measure oxyd error (image/region of interest): "
                                                               f"no image of {self.camera}"), MOState.START)
        self.image = np.asarray(image)
        return MOState.STARTED

    def started(self) -> MOState:
        try:
            self.px = pixel_size_um(self.s, self.camera)
        except FunctionError as exc:
            return self.fail(exc, MOState.STARTED)
        return MOState.MEASURE

    # ---------------------------------------------------------------------------------------------- Measure
    def measure(self) -> MOState:
        p, image, px = self.p, self.image, self.px
        assert image is not None and p.circle is not None
        circle_in = p.circle
        # 1. mesa: IMAQ Find Circular Edge on the annulus [Mesa Min Diam/2, Mesa Max Diam/2]
        mesa = edges.find_circular_edge(
            image, circle_in.center, p.mesa_min_diam_um / 2.0 / px, p.mesa_max_diam_um / 2.0 / px,
            direction=p.mesa_search_dir, step=p.mesa_step,
            options=EdgeOptions(p.mesa_polarity, p.mesa_kernel, p.mesa_width, p.mesa_strength))
        mesa_found = mesa.found
        external = mesa.circle if mesa_found else circle_in
        # 2. oxide: IMAQ Spoke, first edge on each line of [Oxyd Min Diam/2, Oxyd Max Diam/2]
        r_in, r_out = p.oxyd_min_diam_um / 2.0 / px, p.oxyd_max_diam_um / 2.0 / px
        oxyd_options = EdgeOptions(p.oxyd_polarity, p.oxyd_kernel, p.oxyd_width, p.oxyd_strength)
        lines = edges.spoke_lines(external.center, r_in, r_out, step=p.oxyd_step, direction=p.oxyd_search_dir) \
            if r_out > r_in else []
        found = edges.first_edges(image, lines, oxyd_options)
        points = np.array([(e.x, e.y) for e in found if e is not None], dtype=np.float64).reshape(-1, 2)
        gradient: tuple[float, ...] = ()
        if lines:
            (a, b) = lines[0]
            gradient = tuple(float(v) for v in edges.edge_strengths(
                edges.line_profile(image, a, b, p.oxyd_width), p.oxyd_kernel))
        # 3. IMAQ Fit Circle 2
        fit = edges.fit_circle_robust(points, max_distance=p.fit_max_dist, min_score=p.fit_min_score,
                                      max_iterations=p.fit_max_iterations, reject_outliers=p.reject_outliers)
        oxyd_found = fit.valid
        used = points[fit.inliers] if (p.reject_outliers and oxyd_found) else points
        diameters, chords, lengths = self._diameters(external, fit.circle if oxyd_found else NAN_CIRCLE, used,
                                                     oxyd_found)
        self.report = MeasureOxydReport(
            external_circle=external, oxyd_circle=fit.circle if oxyd_found else NAN_CIRCLE, diameters=diameters,
            diameter_lines=chords, diameter_lengths=lengths,
            oxyd_points=tuple((float(x), float(y)) for x, y in used),
            mesa_found=mesa_found, oxyd_found=oxyd_found, process_time_ms=(self.s.clock() - self.t0) * 1000.0,
            oxyd_search_lines=_segments(lines), mesa_search_lines=_segments(mesa.lines),
            mesa_points=tuple((float(x), float(y)) for x, y in mesa.points), gradient_values=gradient,
            pixel_size_um=px, fit_score=fit.score)
        self._overlays(self.report)
        self.ctx.set_state(str(MOState.MEASURE), mesa_found=mesa_found, oxyd_found=oxyd_found,
                           diameters=self.report.diameters)
        # frame 3: "MESA found And no error"
        if not mesa_found and p.mesa_mandatory:
            return self.fail(FunctionError(MEASURE_OXYD_NO_MESA, "measure oxyd error (none circle found): no mesa"),
                             MOState.MEASURE)
        if not oxyd_found and p.oxyd_mandatory:
            return self.fail(FunctionError(MEASURE_OXYD_PROCESSING, "measure oxyd error (processing): no oxide edge "
                                                                    "circle"), MOState.MEASURE)
        return MOState.FINISHED

    def _diameters(self, external: Circle, oxyd: Circle, used: np.ndarray, found: bool
                   ) -> tuple[DiametersReport, tuple[Segment, ...], tuple[float, ...]]:
        p, px = self.p, self.px
        ext = 2.0 * external.radius
        if not (p.mesa_min_diam_um <= ext * px <= p.mesa_max_diam_um):        # bound: External
            ext = math.nan
        if not found or used.shape[0] < 3:
            return DiametersReport(external=ext), (), ()
        centre = oxyd
        if p.oxyd_measure_type is OxideMeasureType.CIRCUMSCRIBED:            # "Use Circumscribed Circle Center"
            centre = circumscribed_circle(used)
        radii = np.hypot(used[:, 0] - centre.x, used[:, 1] - centre.y)
        chords = tuple((2 * centre.x - x, 2 * centre.y - y, x, y) for x, y in used)
        contour = contour_diameters(used)
        circ, insc = contour.circumscribed, contour.inscribed
        values = {"fit": 2.0 * oxyd.radius, "median": 2.0 * float(np.median(radii)),
                  "mean": 2.0 * float(np.mean(radii)), "surface": contour.surface, "inscribed": insc,
                  "circumscribed": circ}
        if not (0.0 <= values["fit"] <= p.oxyd_max_diam_um / px):              # bound: the oxide diameters
            values = dict.fromkeys(values, math.nan)
        offset = p.oxid_result_offset_um / px
        values = {k: v + offset for k, v in values.items()}
        ratio = circ / insc if insc else math.nan
        report = DiametersReport(external=ext, circ_insc_ratio=ratio, stddev=2.0 * sample_std(radii),
                                 accuracy=100.0 * (circ - insc) / insc if insc else math.nan, **values)
        return report, chords, tuple(float(2.0 * r) for r in radii)

    def _overlays(self, r: MeasureOxydReport) -> None:
        """The overlay groups of the result (colours chosen here; the LabVIEW colours were not read)."""
        p, images = self.p, self.s.images
        mesa: list[Shape] = []
        if p.overlay_external and not r.external_circle.is_nan:
            c = r.external_circle
            mesa.append(CircleShape(c.x, c.y, c.radius, GREEN, 2))
        if p.overlay_mesa_search_lines:
            mesa += [LineShape(*seg, BLUE, 1) for seg in r.mesa_search_lines]
        if p.overlay_mesa_edges and r.mesa_points:
            mesa.append(Points(r.mesa_points, GREEN, 1, marker=3))
        oxyd: list[Shape] = []
        if p.overlay_oxyd and not r.oxyd_circle.is_nan:
            c = r.oxyd_circle
            oxyd.append(CircleShape(c.x, c.y, c.radius, RED, 2))
        if p.overlay_oxyd_measure:
            value = r.diameters.selected(p.oxyd_measure_type) * r.pixel_size_um
            text = "Oxyd Diam.: -" if math.isnan(value) else f"Oxyd Diam.: {value:.2f} µm"
            oxyd.append(Text(10.0, 10.0, text, RED, 20))
        edges_shapes: list[Shape] = [Points(r.oxyd_points, YELLOW, 1, marker=3)] \
            if p.overlay_oxyd_edges and r.oxyd_points else []
        diameters: list[Shape] = [LineShape(*seg, CYAN, 1) for seg in r.diameter_lines] \
            if p.overlay_oxyd_diameters else []
        target: list[Shape] = []
        centre = r.oxyd_circle if not r.oxyd_circle.is_nan else r.external_circle
        if p.overlay_target and not centre.is_nan:
            target.append(CircleShape(centre.x, centre.y, p.target_diam_um / 2.0 / r.pixel_size_um, MAGENTA, 1))
        images.set_overlay("mesa", mesa)
        images.set_overlay("oxyd", oxyd)
        images.set_overlay("edges", edges_shapes)
        images.set_overlay("diameters", diameters)
        images.set_overlay("target", target)

    def finished(self) -> None:
        return None

    def failed(self) -> None:
        err = self.error or FunctionError(MEASURE_OXYD_PROCESSING, "measure oxyd error (processing)")
        if self.report is None:
            circle = self.p.circle if self.p.circle is not None else NAN_CIRCLE
            self.report = MeasureOxydReport(circle, NAN_CIRCLE, DiametersReport(), (), (), (), False, False,
                                            (self.s.clock() - self.t0) * 1000.0, (), (), (), (), self.px)
        app_log(self.s, "error", f"MEASURE OXYD (Measure): {err.code} {err.source}")
        raise FunctionError(err.code, err.source, state=self.failed_state, report=self.report)
