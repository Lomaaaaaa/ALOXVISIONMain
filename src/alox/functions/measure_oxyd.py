"""Measure Oxyd (LabVIEW "Measure Mesa"): the mesa and oxide-aperture diameters in one image, as a measurement function.

Spec: ``docs/vision-core/map.md`` Part 3 §2.1, §3.10; Part 4 §3.4 (edges), §3.7 (circles and roundness), §3.8
(``tools_alox_bound_measured_values``, ``alox_tools_convert_mesa_measures_to_micrometers``). Diagrams:
``alox_function_ll_measure_mesa_state_machine d`` (controls ``Image``, ``Circle In``, ``Measure Mesa Profile``),
``d40`` (case ``"Circular", Default`` of ``Mesa Shape Type``), ``d41…d69`` (its sub-cases), ``d13`` (case ``"Line"``),
``d70`` (case ``"Rectangle"``), ``d145`` (frame 3: end state), ``tool_alox_measLine_ROI_builder d``,
``tool_alox_measLine_distance_computation d``, ``alox_function_ll_measure_mesa_bundle_profile_parameters d`` (profile keys and defaults),
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
   annulus, e.g. ``maxMesaDiam`` 0, the LabVIEW default; the port's default is 40 µm, owner R10): the external
   circle is ``Circle In`` (changelog 1.0.0.11) and ``Mesa Found`` is FALSE;
2. oxide: ``IMAQ Spoke`` "Get First Edge" on ``[Oxyd Min Diam/2, Oxyd Max Diam/2]`` (Oxyd Min Diam is the constant 0
   of the bundle VI) centred on the external circle, oxide direction and edge options, step = Oxyd Step;
3. ``IMAQ Fit Circle 2`` on the edge points (Fit Max Dist., Fit Min Score, Fit Max Iterations, Reject Outliers): the
   oxide circle; the points used are the inliers when Reject Outliers, else all;
4. diameters (pixels): Fit = 2·R of the fitted circle; Mean, Median, STDDEV = 2 × mean, median, sample standard
   deviation of the distances of the used points to the fitted centre (``Use Fitted Circle Center``; to the
   circumscribed centre for the measure type ``Circumscribed``); Surface, Inscribed, Circumscribed from
   :func:`alox.vision.circles.contour_diameters` (owner M9: the exact minimum circumscribed and maximum inscribed
   circles; LabVIEW's minimum-zone variant with its Elzinga–Hearn / Delaunay fallback is ``method="labview"``; 0 → NaN);
   ``Circ/Insc Ratio`` = circumscribed / inscribed; ``Accuracy`` = 100·(circumscribed − inscribed)/inscribed (as read
   in ``d40``: this is the subtraction of owner question M3, it feeds Accuracy, not the ratio); External = 2·R of the
   external circle; one chord line per used point (through the centre) and its length;
5. ``tools_alox_bound_measured_values``: External·px outside ``[Mesa Min, Mesa Max]`` µm → NaN; Fit outside
   ``[0, Oxyd Max/px]`` px → Fit, Median, Mean, Surface, Inscribed, Circumscribed NaN;
6. ``oxid result offset`` (µm) / px added to the oxide diameters;
7. frame 3: ``Finished`` when (Mesa Found or not ``measure.vcsel.MeasMesaMandatory``) and (Oxyd Found or not
   ``measure.oxyd.MeasOxydMandatory``), else ``Error``; the Fit Circle error "too few points" is not an error ("Oxyd
   missing is normal when starting process").

``Measure`` (case ``Line``, ``d13``: a stripe mesa, oxidised from both sides; owner R8: in use):

1. orientation: ``IMAQ Find Edge`` (straight edge) twice in a loop, search direction ``2·i`` (Left to Right, then
   Top to Bottom), mesa edge strength and kernel; the best score gives the rake direction and the edge angle
   (:func:`alox.vision.edges.find_straight_edge`; here in the mesa square below, not rotated);
2. mesa: ``tool_alox_measLine_ROI_builder`` = the square ``[X − R, Y − R, X + R, Y + R]`` around ``Circle In`` with
   ``R = Mesa Max Diam/2`` (µm → px), rotated by the edge tilt; ``IMAQ Rake 3`` "Get First + Last Edge" in the chosen
   direction, step = Mesa Step, mesa kernel, width and strength, polarity the constant ``All Edges``;
   ``tool_alox_measLine_distance_computation``: the first ↔ last distance on each line and their mean within
   ``mean ± 1σ``: the mesa width; no edge: ``Mesa Found`` FALSE (9022 "No Mesa found" when mandatory);
3. oxide: the same rake (oxide kernel, width, strength and step) in the square of side ``Oxyd Max Diam`` centred on the
   mesa (here: the mean of the mesa segments' midpoints; ``Circle In`` without mesa): the first and last edges are the
   two borders of the non-oxidised strip; distances and robust mean: the aperture width;
4. diameters (pixels, the bundle of ``d13``): External = the mesa width; Fit = Inscribed = the aperture width
   (``ZERO → NaN``); Depth = ``(External − Fit)/2``, the oxidised depth per side (NaN without a mesa; LabVIEW put it
   in Median, owner S3 "depth only, never median": its own field, measure type ``DEPTH``, Median NaN); Mean = the
   mean of the aperture distances; STDDEV = their sample standard deviation; Diameters Coordinates / Lengths = the
   first ↔ last segments of the oxide lines and their lengths; Points OXYD / MESA Used = their first and last edges;
   Surface, Circumscribed, Circ/Insc Ratio, Accuracy: their wires were not legible in ``d13``, NaN here; then steps
   5–7 as ``Circular`` (the bound VI, the offset, frame 3). The search direction parameters do not apply (the rake
   direction comes from step 1).

``Measure`` (case ``Rectangle``, ``d70``; owner R8: in use): the edges are found as for ``Circular`` (mesa: ``IMAQ Find
Circular Edge`` points on the mesa annulus; oxide: ``IMAQ Spoke`` first edges from the mesa centre), then LabVIEW writes
each point list (``"%f;%f"`` lines, ``data_mesa_detection_LV.mesadata`` / ``data_oxyd_detection_LV.mesadata`` in
``…/02-Vision/Temp``), runs ``py mesure_edges.py`` (``System Exec``) and reads ``resultsmesa.csv`` /
``resultsoxid.csv``, one ``key;value`` line each: label, center_x_px, center_y_px, area_px2, equivalent_diam_px,
mean_radial_diam_px, inscribed_diam_px, circumscribed_diam_px, width_px, height_px, angle_deg, circularity_px,
n_points, n_rejected. The script is not in the export: :func:`edge_shape` computes these values from the points
(polygon of the points in angle order: area, centroid, equivalent diameter ``2·√(A/π)``, mean radial diameter, the
inscribed and circumscribed diameters of :func:`alox.vision.circles.contour_diameters`, minimum-area rectangle: width
= its longer side, height = the shorter, angle in [0, 180)), :attr:`MeasureOxydReport.mesa_shape` /
:attr:`MeasureOxydReport.oxyd_shape`. Diameters (the bundle of ``d70``): External = the mesa ``equivalent_diam_px``
(``x*1``); Fit = the oxide ``equivalent_diam_px``; Median = 2 × the median distance of the oxide points to the oxide
centroid (the chord loop, inferred); Surface = the oxide ``height_px`` in LabVIEW, here (owner S2: the types used are
Fit, Surface, vertical y, horizontal x) the surface-equivalent diameter ``2·√(A/π)`` of the oxide polygon (the
same value as Fit, :func:`edge_shape`'s ``equivalent_diam_px``); Height Y / Width X (measure types ``HEIGHT_Y`` /
``WIDTH_X``, Python only) = the sides of the oxide's minimum-area rectangle, the one closest to the image Y axis and
the other one (:attr:`EdgeShape.height_y`); Mean, Inscribed, Circumscribed, Circ/Insc, STDDEV, Accuracy: the
constant 0 in LabVIEW, NaN here (not measured); then steps 5–7 as ``Circular`` (the offset is added to Fit, Median,
Surface, Height Y and Width X).

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
- ``Line``: ``Oxyd Found`` was the constant TRUE (``>> Valid Fit >>``): here at least one oxide line with two edges;
  the offset is added to Fit, Mean and Inscribed, and Median is computed with the offset Fit (LabVIEW added it to the
  whole cluster). ``Rectangle``: the external Python script and its temporary files are replaced by
  :func:`edge_shape` (no outlier rejection: ``n_rejected`` 0); the constant-0 fields are NaN.
- The LabVIEW overlays of ``Line`` and ``Rectangle`` were not read: the external and oxide outlines are the two
  borders of the stripe (``Line``) or the minimum-area rectangle (``Rectangle``) instead of circles; the target of
  ``Line`` is two lines ``Target Diam`` apart along the stripe; with the measure type ``DEPTH`` the target
  (``measure.oxyd.oxydtarget``) is an oxidised depth per side, drawn ``Target`` inside each mesa border.
- ``measure.vcsel.maxMesaDiam`` defaults to 40 µm (owner R10; LabVIEW 0 never found the mesa).
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
    polygon_area,
)
from alox.vision.edges import (
    EdgeOptions,
    EdgePolarity,
    RakeDirection,
    RotatedRect,
    SearchDirection,
)
from alox.vision.stats import OxideMeasureType, sample_std

__all__ = [
    "MEASURE_OXYD_IMAGE",
    "MEASURE_OXYD_NO_MESA",
    "MEASURE_OXYD_PROCESSING",
    "DiametersReport",
    "EdgeShape",
    "MOState",
    "MeasureOxyd",
    "MeasureOxydParams",
    "MeasureOxydReport",
    "MesaShapeType",
    "edge_shape",
    "robust_mean",
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
                   OxideMeasureType.INSCRIBED: "Inscribed", OxideMeasureType.DEPTH: "Depth",
                   OxideMeasureType.HEIGHT_Y: "Height Y", OxideMeasureType.WIDTH_X: "Width X"}
#: The overlay text of the measure type (``Oxyd Diam.`` for the diameters).
_MEASURE_TEXTS = {OxideMeasureType.DEPTH: "Oxyd Depth", OxideMeasureType.HEIGHT_Y: "Oxyd Height Y",
                  OxideMeasureType.WIDTH_X: "Oxyd Width X"}


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
    "mesa_max_diam_um": ("measure.vcsel.maxMesaDiam", 40.0),
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
    mesa_max_diam_um: float = 40.0              # owner R10 (LabVIEW 0)
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
    """``alox_typedef_measure_mesa_diameters_report`` (pixels; ratio and accuracy unitless), plus the Python-only
    ``depth`` (``Line``: the oxidised depth per side) and ``height_y`` / ``width_x`` (``Rectangle``: the aperture's
    sides along the image Y and X axes), NaN for the other shapes."""

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
    depth: float = math.nan
    height_y: float = math.nan
    width_x: float = math.nan

    def scaled(self, pixel_size_um: float) -> DiametersReport:
        """``alox_tools_convert_mesa_measures_to_micrometers``: External…Circumscribed (and the depth, height and
        width) × pixel size (µm)."""
        k = pixel_size_um
        return DiametersReport(self.external * k, self.fit * k, self.median * k, self.mean * k, self.surface * k,
                               self.inscribed * k, self.circumscribed * k, self.circ_insc_ratio, self.stddev,
                               self.accuracy, self.depth * k, self.height_y * k, self.width_x * k)

    def selected(self, measure_type: OxideMeasureType) -> float:
        """The diameter of ``Oxyd Measure Type`` (the depth, height or width for the types 6…8)."""
        return {OxideMeasureType.MEAN: self.mean, OxideMeasureType.MEDIAN: self.median, OxideMeasureType.FIT: self.fit,
                OxideMeasureType.SURFACE: self.surface, OxideMeasureType.CIRCUMSCRIBED: self.circumscribed,
                OxideMeasureType.INSCRIBED: self.inscribed, OxideMeasureType.DEPTH: self.depth,
                OxideMeasureType.HEIGHT_Y: self.height_y,
                OxideMeasureType.WIDTH_X: self.width_x}[OxideMeasureType(measure_type)]


Segment = tuple[float, float, float, float]


def robust_mean(values: Any) -> float:
    """``tool_alox_measLine_distance_computation`` ``mean``: the mean of the values inside ``mean ± 1σ`` (sample
    standard deviation, limits included); NaN for none."""
    v = np.asarray(values, dtype=np.float64)
    if v.size == 0:
        return math.nan
    m, sd = float(v.mean()), sample_std(v)
    kept = v[(v >= m - sd) & (v <= m + sd)]
    return float(kept.mean()) if kept.size else m


@dataclass(frozen=True)
class EdgeShape:
    """The values LabVIEW's ``Rectangle`` case reads back from ``mesure_edges.py`` for one edge-point list (pixels;
    ``rect`` = the minimum-area rectangle, ``width`` its longer side, ``angle`` its direction in [0, 180) degrees,
    counter-clockwise as displayed)."""

    center: tuple[float, float]
    area: float
    equivalent_diameter: float
    mean_radial_diameter: float
    inscribed_diameter: float
    circumscribed_diameter: float
    rect: RotatedRect
    n_points: int
    n_rejected: int = 0

    @property
    def width(self) -> float:
        return self.rect.width

    @property
    def height(self) -> float:
        return self.rect.height

    @property
    def angle(self) -> float:
        return self.rect.angle % 180.0

    @property
    def _long_side_vertical(self) -> bool:
        return 45.0 < self.angle < 135.0

    @property
    def height_y(self) -> float:
        """The side of the minimum-area rectangle closest to the image Y axis (vertical as displayed): ``width`` (the
        long side) when its direction is within 45° of vertical (``angle`` in (45°, 135°)), else ``height``. A
        rectangle turned by a small angle keeps its vertical side as the height whichever side is longer; at
        exactly 45° the short side is the height."""
        return self.rect.width if self._long_side_vertical else self.rect.height

    @property
    def width_x(self) -> float:
        """The other side of the minimum-area rectangle (closest to the image X axis), see :attr:`height_y`."""
        return self.rect.height if self._long_side_vertical else self.rect.width


def _min_area_rect(points: np.ndarray) -> RotatedRect:
    """Rotating calipers on the convex hull: the rectangle of least area holding the points."""
    from scipy.spatial import ConvexHull, QhullError

    try:
        hull = points[ConvexHull(points).vertices]
    except (QhullError, ValueError):
        hull = points
    best: tuple[float, ...] | None = None
    for i in range(hull.shape[0]):
        e = hull[(i + 1) % hull.shape[0]] - hull[i]
        length = math.hypot(float(e[0]), float(e[1]))
        if length == 0.0:
            continue
        u = e / length
        v = np.array([-u[1], u[0]])
        pu, pv = hull @ u, hull @ v
        area = float((pu.max() - pu.min()) * (pv.max() - pv.min()))
        if best is None or area < best[0]:
            best = (area, u[0], u[1], pu.min(), pu.max(), pv.min(), pv.max())
    if best is None:
        c = points.mean(axis=0)
        return RotatedRect(float(c[0]), float(c[1]), 0.0, 0.0, 0.0)
    _, ux, uy, a0, a1, b0, b1 = best
    u, v = np.array([ux, uy]), np.array([-uy, ux])
    c = u * (a0 + a1) / 2.0 + v * (b0 + b1) / 2.0
    w, h = float(a1 - a0), float(b1 - b0)
    angle = math.degrees(math.atan2(-uy, ux))
    if w < h:
        w, h, angle = h, w, angle + 90.0
    angle %= 180.0
    return RotatedRect(float(c[0]), float(c[1]), w, h, 0.0 if angle > 180.0 - 1e-9 else angle)


def edge_shape(points: Any) -> EdgeShape | None:
    """:class:`EdgeShape` of an edge-point list (the port of ``mesure_edges.py``, see the module docstring); None
    for fewer than 3 points or a flat polygon."""
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    if pts.shape[0] < 3:
        return None
    c0 = pts.mean(axis=0)
    poly = pts[np.argsort(np.arctan2(pts[:, 1] - c0[1], pts[:, 0] - c0[0]), kind="stable")]
    area = polygon_area(poly)
    if not area > 0.0:
        return None
    x, y = poly[:, 0] - c0[0], poly[:, 1] - c0[1]
    xn, yn = np.roll(x, -1), np.roll(y, -1)
    cross = x * yn - xn * y
    a2 = float(cross.sum())
    centre = (float(c0[0] + ((x + xn) * cross).sum() / (3.0 * a2)), float(c0[1] + ((y + yn) * cross).sum() / (3.0 * a2)))
    contour = contour_diameters(poly)
    return EdgeShape(centre, area, 2.0 * math.sqrt(area / math.pi),
                     2.0 * float(np.mean(np.hypot(pts[:, 0] - centre[0], pts[:, 1] - centre[1]))),
                     contour.inscribed, contour.circumscribed, _min_area_rect(poly), int(pts.shape[0]))


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
    shape_type: MesaShapeType = MesaShapeType.CIRCULAR
    mesa_lines: tuple[Segment, ...] = ()        # Line: the first ↔ last segment of each mesa search line
    mesa_shape: EdgeShape | None = None         # Rectangle: the mesure_edges.py values of the mesa edges
    oxyd_shape: EdgeShape | None = None         # Rectangle: idem, oxide edges

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
        p = self.p
        shape = p.mesa_shape_type
        measure = {MesaShapeType.LINE: self._line, MesaShapeType.RECTANGLE: self._rectangle}.get(shape, self._circular)
        self.report = measure()
        self._overlays(self.report)
        self.ctx.set_state(str(MOState.MEASURE), mesa_found=self.report.mesa_found, oxyd_found=self.report.oxyd_found,
                           diameters=self.report.diameters)
        # frame 3: "MESA found And no error"
        if not self.report.mesa_found and p.mesa_mandatory:
            return self.fail(FunctionError(MEASURE_OXYD_NO_MESA, "measure oxyd error (none circle found): no mesa"),
                             MOState.MEASURE)
        if not self.report.oxyd_found and p.oxyd_mandatory:
            what = "circle" if shape is MesaShapeType.CIRCULAR else f"({shape.label})"
            return self.fail(FunctionError(MEASURE_OXYD_PROCESSING, f"measure oxyd error (processing): no oxide edge "
                                                                    f"{what}"), MOState.MEASURE)
        return MOState.FINISHED

    def _options(self, mesa: bool, polarity: EdgePolarity | None = None) -> EdgeOptions:
        p = self.p
        if mesa:
            return EdgeOptions(p.mesa_polarity if polarity is None else polarity, p.mesa_kernel, p.mesa_width,
                               p.mesa_strength)
        return EdgeOptions(p.oxyd_polarity if polarity is None else polarity, p.oxyd_kernel, p.oxyd_width,
                           p.oxyd_strength)

    def _gradient(self, lines: Any) -> tuple[float, ...]:
        """Gradient Values: the edge-strength profile along the first oxide search line."""
        if not lines:
            return ()
        (a, b) = lines[0]
        assert self.image is not None
        return tuple(float(v) for v in edges.edge_strengths(edges.line_profile(self.image, a, b, self.p.oxyd_width),
                                                            self.p.oxyd_kernel))

    def _oxide_spokes(self, centre: tuple[float, float]) -> tuple[list[edges.SpokeLine], np.ndarray]:
        """Step 2: ``IMAQ Spoke`` first edges on ``[Oxyd Min Diam/2, Oxyd Max Diam/2]`` around ``centre``."""
        p, px = self.p, self.px
        assert self.image is not None
        r_in, r_out = p.oxyd_min_diam_um / 2.0 / px, p.oxyd_max_diam_um / 2.0 / px
        lines = edges.spoke_lines(centre, r_in, r_out, step=p.oxyd_step, direction=p.oxyd_search_dir) \
            if r_out > r_in else []
        found = edges.first_edges(self.image, lines, self._options(False))
        return lines, np.array([(e.x, e.y) for e in found if e is not None], dtype=np.float64).reshape(-1, 2)

    def _mesa_circular_edge(self) -> edges.CircularEdge:
        """Step 1: ``IMAQ Find Circular Edge`` on the annulus ``[Mesa Min Diam/2, Mesa Max Diam/2]``."""
        p, px = self.p, self.px
        assert self.image is not None and p.circle is not None
        return edges.find_circular_edge(
            self.image, p.circle.center, p.mesa_min_diam_um / 2.0 / px, p.mesa_max_diam_um / 2.0 / px,
            direction=p.mesa_search_dir, step=p.mesa_step, options=self._options(True))

    def _circular(self) -> MeasureOxydReport:
        p, px = self.p, self.px
        assert p.circle is not None
        mesa = self._mesa_circular_edge()
        mesa_found = mesa.found
        external = mesa.circle if mesa_found else p.circle
        lines, points = self._oxide_spokes(external.center)
        # 3. IMAQ Fit Circle 2
        fit = edges.fit_circle_robust(points, max_distance=p.fit_max_dist, min_score=p.fit_min_score,
                                      max_iterations=p.fit_max_iterations, reject_outliers=p.reject_outliers)
        oxyd_found = fit.valid
        used = points[fit.inliers] if (p.reject_outliers and oxyd_found) else points
        diameters, chords, lengths = self._diameters(external, fit.circle if oxyd_found else NAN_CIRCLE, used,
                                                     oxyd_found)
        return MeasureOxydReport(
            external_circle=external, oxyd_circle=fit.circle if oxyd_found else NAN_CIRCLE, diameters=diameters,
            diameter_lines=chords, diameter_lengths=lengths,
            oxyd_points=tuple((float(x), float(y)) for x, y in used),
            mesa_found=mesa_found, oxyd_found=oxyd_found, process_time_ms=(self.s.clock() - self.t0) * 1000.0,
            oxyd_search_lines=_segments(lines), mesa_search_lines=_segments(mesa.lines),
            mesa_points=tuple((float(x), float(y)) for x, y in mesa.points), gradient_values=self._gradient(lines),
            pixel_size_um=px, fit_score=fit.score)

    def _bounded(self, external: float, values: dict[str, float], offset_keys: tuple[str, ...]) -> tuple[
            float, dict[str, float]]:
        """Steps 5–6 (``tools_alox_bound_measured_values``, the offset) for ``Line`` and ``Rectangle``."""
        p, px = self.p, self.px
        if not (p.mesa_min_diam_um <= external * px <= p.mesa_max_diam_um):
            external = math.nan
        if not (0.0 <= values["fit"] <= p.oxyd_max_diam_um / px):
            values = dict.fromkeys(values, math.nan)
        offset = p.oxid_result_offset_um / px
        return external, {k: v + offset if k in offset_keys else v for k, v in values.items()}

    # ---------------------------------------------------------------------------------------------- Line
    def _rake(self, roi: RotatedRect, direction: RakeDirection, mesa: bool
              ) -> tuple[list[edges.SpokeLine], list[tuple[edges.Edge, edges.Edge]]]:
        """``IMAQ Rake 3`` "Get First + Last Edge", polarity ``All Edges`` (the constant of ``d13``)."""
        assert self.image is not None
        step = self.p.mesa_step if mesa else self.p.oxyd_step
        lines = edges.rake_lines(roi, step=step, direction=direction)
        pairs = edges.first_last_edges(self.image, lines, self._options(mesa, EdgePolarity.ALL))
        return lines, [pair for pair in pairs if pair is not None]

    def _line(self) -> MeasureOxydReport:
        p, px, image = self.p, self.px, self.image
        assert p.circle is not None and image is not None
        cx, cy = p.circle.center
        # 1. orientation: IMAQ Find Edge, search direction 2·i (Left to Right, Top to Bottom), the best score
        direction, tilt = RakeDirection.LEFT_TO_RIGHT, 0.0
        half = p.mesa_max_diam_um / 2.0 / px
        mesa_lines: list[edges.SpokeLine] = []
        mesa_pairs: list[tuple[edges.Edge, edges.Edge]] = []
        if half > 0.0:
            square = RotatedRect(cx, cy, 2.0 * half, 2.0 * half)
            best: edges.StraightEdge | None = None
            for candidate in (RakeDirection.LEFT_TO_RIGHT, RakeDirection.TOP_TO_BOTTOM):
                found = edges.find_straight_edge(image, square, direction=candidate,
                                                 options=self._options(True, EdgePolarity.ALL), step=p.mesa_step)
                if found.found and (best is None or found.score > best.score):
                    best, direction = found, candidate
            if best is not None:      # the tilt of the edge against the axis across the search direction
                across = 90.0 if direction is RakeDirection.LEFT_TO_RIGHT else 0.0
                tilt = (best.angle - across + 90.0) % 180.0 - 90.0
            # 2. mesa: rotated square around Circle In, first + last edges, robust mean of the distances
            mesa_lines, mesa_pairs = self._rake(RotatedRect(cx, cy, 2.0 * half, 2.0 * half, tilt), direction, True)
        mesa_d = [math.hypot(b.x - a.x, b.y - a.y) for a, b in mesa_pairs]
        mesa_found = bool(mesa_d)
        mesa_width = robust_mean(mesa_d)
        if mesa_found:
            centre = (float(np.mean([(a.x + b.x) / 2.0 for a, b in mesa_pairs])),
                      float(np.mean([(a.y + b.y) / 2.0 for a, b in mesa_pairs])))
        else:
            centre = (cx, cy)
        # 3. oxide: the same rake in the square of side Oxyd Max Diam around the mesa centre
        half_o = p.oxyd_max_diam_um / 2.0 / px
        ox_lines, ox_pairs = self._rake(RotatedRect(*centre, 2.0 * half_o, 2.0 * half_o, tilt), direction, False) \
            if half_o > 0.0 else ([], [])
        ox_d = [math.hypot(b.x - a.x, b.y - a.y) for a, b in ox_pairs]
        oxyd_found = bool(ox_d)
        # 4. diameters
        width = robust_mean(ox_d)
        fit = width if width else math.nan                                          # ZERO -> NaN
        external = mesa_width if mesa_found else 2.0 * p.circle.radius
        values = {"fit": fit, "mean": float(np.mean(ox_d)) if ox_d else math.nan, "inscribed": fit,
                  "median": math.nan, "surface": math.nan, "circumscribed": math.nan, "depth": math.nan}
        bounded_ext, values = self._bounded(external, values, ("fit", "mean", "inscribed"))
        if mesa_found and not math.isnan(values["fit"]):
            values["depth"] = (external - values["fit"]) / 2.0                       # oxidised depth per side (S3)
        diameters = DiametersReport(external=bounded_ext, stddev=sample_std(ox_d) if ox_d else math.nan, **values)
        ox_centre = (float(np.mean([(a.x + b.x) / 2.0 for a, b in ox_pairs])),
                     float(np.mean([(a.y + b.y) / 2.0 for a, b in ox_pairs]))) if oxyd_found else centre
        segments = tuple((a.x, a.y, b.x, b.y) for a, b in ox_pairs)
        return MeasureOxydReport(
            external_circle=Circle(centre[0], centre[1], mesa_width / 2.0) if mesa_found else p.circle,
            oxyd_circle=Circle(ox_centre[0], ox_centre[1], width / 2.0) if oxyd_found else NAN_CIRCLE,
            diameters=diameters, diameter_lines=segments, diameter_lengths=tuple(float(d) for d in ox_d),
            oxyd_points=tuple(pt for a, b in ox_pairs for pt in ((a.x, a.y), (b.x, b.y))),
            mesa_found=mesa_found, oxyd_found=oxyd_found, process_time_ms=(self.s.clock() - self.t0) * 1000.0,
            oxyd_search_lines=_segments(ox_lines), mesa_search_lines=_segments(mesa_lines),
            mesa_points=tuple(pt for a, b in mesa_pairs for pt in ((a.x, a.y), (b.x, b.y))),
            gradient_values=self._gradient(ox_lines), pixel_size_um=px,
            fit_score=1000.0 * len(ox_pairs) / len(ox_lines) if ox_lines else 0.0, shape_type=MesaShapeType.LINE,
            mesa_lines=tuple((a.x, a.y, b.x, b.y) for a, b in mesa_pairs))

    # ---------------------------------------------------------------------------------------------- Rectangle
    def _rectangle(self) -> MeasureOxydReport:
        p, px = self.p, self.px
        assert p.circle is not None
        mesa = self._mesa_circular_edge()
        mesa_shape = edge_shape(mesa.points)
        mesa_found = mesa_shape is not None
        centre = mesa_shape.center if mesa_shape is not None else p.circle.center
        lines, points = self._oxide_spokes(centre)
        oxyd_shape = edge_shape(points)
        oxyd_found = oxyd_shape is not None
        external = mesa_shape.equivalent_diameter if mesa_shape is not None else 2.0 * p.circle.radius
        chords: tuple[Segment, ...] = ()
        lengths: tuple[float, ...] = ()
        values = dict.fromkeys(("fit", "median", "mean", "surface", "inscribed", "circumscribed", "height_y",
                                "width_x"), math.nan)
        if oxyd_shape is not None:
            ox, oy = oxyd_shape.center
            radii = np.hypot(points[:, 0] - ox, points[:, 1] - oy)
            chords = tuple((2 * ox - x, 2 * oy - y, x, y) for x, y in points)
            lengths = tuple(float(2.0 * r) for r in radii)
            values.update(fit=oxyd_shape.equivalent_diameter, median=2.0 * float(np.median(radii)),
                          surface=2.0 * math.sqrt(oxyd_shape.area / math.pi),        # surface-equivalent (S2)
                          height_y=oxyd_shape.height_y, width_x=oxyd_shape.width_x)
        bounded_ext, values = self._bounded(external, values, ("fit", "median", "surface", "height_y", "width_x"))
        return MeasureOxydReport(
            external_circle=Circle(centre[0], centre[1], external / 2.0) if mesa_found else p.circle,
            oxyd_circle=Circle(*oxyd_shape.center, oxyd_shape.equivalent_diameter / 2.0) if oxyd_shape else NAN_CIRCLE,
            diameters=DiametersReport(external=bounded_ext, **values), diameter_lines=chords, diameter_lengths=lengths,
            oxyd_points=tuple((float(x), float(y)) for x, y in points),
            mesa_found=mesa_found, oxyd_found=oxyd_found, process_time_ms=(self.s.clock() - self.t0) * 1000.0,
            oxyd_search_lines=_segments(lines), mesa_search_lines=_segments(mesa.lines),
            mesa_points=tuple((float(x), float(y)) for x, y in mesa.points), gradient_values=self._gradient(lines),
            pixel_size_um=px, fit_score=1000.0 * points.shape[0] / len(lines) if lines else 0.0,
            shape_type=MesaShapeType.RECTANGLE, mesa_shape=mesa_shape, oxyd_shape=oxyd_shape)

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
        if p.overlay_external:
            mesa += self._outline(r, r.mesa_lines, r.mesa_shape, r.external_circle, r.mesa_found, GREEN)
        if p.overlay_mesa_search_lines:
            mesa += [LineShape(*seg, BLUE, 1) for seg in r.mesa_search_lines]
        if p.overlay_mesa_edges and r.mesa_points:
            mesa.append(Points(r.mesa_points, GREEN, 1, marker=3))
        oxyd: list[Shape] = []
        if p.overlay_oxyd:
            oxyd += self._outline(r, r.diameter_lines, r.oxyd_shape, r.oxyd_circle, r.oxyd_found, RED)
        if p.overlay_oxyd_measure:
            value = r.diameters.selected(p.oxyd_measure_type) * r.pixel_size_um
            name = _MEASURE_TEXTS.get(p.oxyd_measure_type, "Oxyd Diam.")
            text = f"{name}: -" if math.isnan(value) else f"{name}: {value:.2f} µm"
            oxyd.append(Text(10.0, 10.0, text, RED, 20))
        edges_shapes: list[Shape] = [Points(r.oxyd_points, YELLOW, 1, marker=3)] \
            if p.overlay_oxyd_edges and r.oxyd_points else []
        diameters: list[Shape] = [LineShape(*seg, CYAN, 1) for seg in r.diameter_lines] \
            if p.overlay_oxyd_diameters else []
        target: list[Shape] = []
        centre = r.oxyd_circle if not r.oxyd_circle.is_nan else r.external_circle
        half = p.target_diam_um / 2.0 / r.pixel_size_um
        if r.shape_type is MesaShapeType.LINE and p.oxyd_measure_type is OxideMeasureType.DEPTH:
            # the target is an oxidised depth per side: a line Target inside each border of the stripe
            centre = r.external_circle
            half = centre.radius - p.target_diam_um / r.pixel_size_um if r.mesa_found else math.nan
            if not half > 0.0:
                centre = NAN_CIRCLE
        if p.overlay_target and not centre.is_nan and r.shape_type is MesaShapeType.LINE and r.diameter_lines:
            x1, y1, x2, y2 = r.diameter_lines[0]                   # two lines along the stripe, Target Diam apart
            n = math.hypot(x2 - x1, y2 - y1) or 1.0
            ux, uy = (x2 - x1) / n, (y2 - y1) / n
            length = p.oxyd_max_diam_um / 2.0 / r.pixel_size_um
            for side in (-half, half):
                ox, oy = centre.x + side * ux, centre.y + side * uy
                target.append(LineShape(ox + length * uy, oy - length * ux, ox - length * uy, oy + length * ux,
                                        MAGENTA, 1))
        elif p.overlay_target and not centre.is_nan:
            target.append(CircleShape(centre.x, centre.y, half, MAGENTA, 1))
        images.set_overlay("mesa", mesa)
        images.set_overlay("oxyd", oxyd)
        images.set_overlay("edges", edges_shapes)
        images.set_overlay("diameters", diameters)
        images.set_overlay("target", target)

    @staticmethod
    def _outline(r: MeasureOxydReport, segments: tuple[Segment, ...], shape: EdgeShape | None, circle: Circle,
                 found: bool, colour: tuple[int, int, int]) -> list[Shape]:
        """The external / oxide outline: the circle (``Circular``; the external circle is ``Circle In`` without a
        mesa), the two borders of the stripe through the first and the last edges (``Line``) or the minimum-area
        rectangle (``Rectangle``)."""
        if r.shape_type is MesaShapeType.LINE:
            if not (found and segments):
                return []
            first, last = segments[0], segments[-1]
            return [LineShape(first[0], first[1], last[0], last[1], colour, 2),
                    LineShape(first[2], first[3], last[2], last[3], colour, 2)]
        if r.shape_type is MesaShapeType.RECTANGLE and shape is not None:
            corners = shape.rect.corners()
            return [LineShape(*corners[i], *corners[(i + 1) % 4], colour, 2) for i in range(4)]
        return [] if circle.is_nan else [CircleShape(circle.x, circle.y, circle.radius, colour, 2)]

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
