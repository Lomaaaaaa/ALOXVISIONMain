r"""HL Tilt measurement: the wafer rotation angle, on a straight edge or between two learned objects.

Spec: ``docs/vision-core/map.md`` Part 3 §2.1 (common pattern), §3.13 (HL Tilt), Part 4 §3.4 (straight edge,
pattern matching: :mod:`alox.vision.tilt`, :mod:`alox.vision.matching`), §3.6 (lines), §3.8 (tilt post-processing:
:func:`alox.vision.wafer.tilt_from_objects`, ``tools_tracking_move_relative``), §3.10/§3.11 (folders and file names:
:mod:`alox.functions.jobfiles`), Part 5 Appendix B (``measure_tilt``: :func:`alox.api.reports.tilt_event`);
``docs/vision-core/xy-stage.md`` §9 (coordinate systems); owner answers B9, M6 in ``docs/vision-core/questions.md``.
Diagrams: ``alox_function_hl_tilt_measurement_state_machine`` (cited ``sm dN``),
``…_bundle_profile_parameters d, p`` (``bundle d``), ``alox_vision_measure_tilt_straight_edge d, p``,
``alox_vision_mask_matched_object d``, ``alox_vision_bundle_setup_geometric_matching_options d``,
``tools_tilt_function_object_detection_status d``; texts of ``tilt_en.lang`` / ``alox_en.lang``.

:meth:`TiltMeasurement.run` runs the state machine in the runner's thread, one state per loop iteration, with
``ctx.check()`` between states and ``ctx.set_state`` on every change (LabVIEW's ``TILT State Changed``) and for the
run-panel events ``TILT Started``, ``TILT Moving``, ``TILT Autofocus``, ``TILT Refocus``, ``TILT Measured``,
``TILT Finished`` (as ``event=`` data). The core (:class:`alox.core.core.Core`) sets the tilt status 200, sends the
websocket ``started``/``failed`` events and ``finished`` after the run, and stores the returned
:attr:`TiltReport.angle_deg` (``alox_manager_tilt`` "Set Tilt", used by HL mesa); the function sets the tilt status
codes 301…1499 through ``services.set_status("tilt", code)`` and publishes ``measure_tilt`` and ``error`` itself.

**Parameters** (``alox_typedef_tilt_measurement_parameters``, :class:`TiltParams`; profile ``measure.tilt.*``,
defaults of ``bundle d``): ``camera`` (1 = Low Mag.), ``startx``/``starty`` (0, wafer mm; the request's ``x``/``y``
replace them, ``alox_api_v1_put_functions_measure_tilt``), ``method`` (0 Straight Edge, 1 Dual Objects; 2 Long
Baseline Edge, 3 Lattice: Python only, below),
``maxmoves`` (0), ``step`` (0.1 mm); Straight Edge Detection: ``edgesearchdir`` (0 Left to Right), ``edgepolarity``
(0 all), ``edgestrength`` (20), ``edgestep`` (30 px), ``edgewidth`` (10), ``edgekernel`` (3), ``minpointspc`` (25 %),
``expectedangledeg`` (0), ``anglerangedeg`` (10), ``edgemethod`` (0 First Edge Rake); Dual Objects Detection:
``obj1model``, ``obj1mask``, ``obj2model``, ``obj2mask`` (files of ``<settings>\profile``), ``edgethreshold`` (25),
``minlength`` (5), ``morphopen``/``morphclose`` (0), ``obj1minscore``/``obj2minscore`` (70 %),
``obj1maxocclusionpc``/``obj2maxocclusionpc`` (40 %); ``autofocus``, ``refocus``, ``showwindow``,
``overlaymatches``, ``overlaystraightedge``, ``overlayedges``, ``overlaytilt`` (T).

**States** (``alox_typedef_tilt_measurement_function_state``):

- before the loop (sm d1…d3): ``Set Running``; Z joystick off; XY joystick off (errors logged);
- ``Start`` (sm d7…d36, 7 frames): status 301; Dual Objects: the four model/mask files read from
  ``<settings>\profile\<name>`` when the name is not empty (a read error only leaves the object without model: it is
  then never found); XY joystick off when on, ``Set Speed`` ``hardware.xy.speed`` (log ``Measure Tilt (Set XY
  Speed)``, not fatal); "Reset Wavelength" is an empty diagram-disable structure (no wavelength change); the
  job-history folders ``images\tilt`` and ``files\tilt`` (errors cleared) → ``Started``;
- ``Started`` (sm d41): status 302; the **snail** of ``Max Moves`` positions of ``Step`` mm around the start position
  converted Wafer → XY Stages (:func:`alox.functions.search_object.snail_positions`, the same diagram as Search
  Object); ``TILT Started`` → ``Move To Pos``;
- ``Move To Pos`` (sm d44…d55): no position left → status 1499 and error **9086** "tilt not found error" (log
  ``Tilt (Move XY)``) → ``Error``; else status 303, ``Move Absolute XY`` (not waited), ``TILT Moving``; a move error
  is retried ("XY Move Error: Retry 3x": ``Error Count ≥ 3`` → status 1301, the device's error, ``Error``; 4 attempts);
  success → count 0, the move time, ``Position OK?``;
- ``Position OK?`` (sm d56…d66): status 304; ``Get Position XY`` (read errors: keep waiting) within ±0.001 mm of the
  position (both axes) → 100 ms "Make sure the stages has stopped moving", ``Current Pos Index + 1``, ``Current XY
  Pos`` = the position read, Get Image Run Once ("Refresh image display", errors cleared), ``TILT Moving``; at the
  first position (index 1) → ``Autofocus Init`` (Autofocus), ``Refocus Init`` (Refocus), else the measure state of
  the Tilt Method; at the next positions → the measure state. 30 s after the move: status 1302, error **9530**
  "tilt error (xy stages timeout)", log ``Tilt (Position OK?)`` → ``Error``;
- ``Autofocus Init`` ("Select high angle camera", sm d67: nothing) → ``Autofocus`` (sm d68): status 305; LL
  autofocus Run Once (the profile's ``autofocus.*`` with the tilt camera; B9: it ends at its best Z); Get Image Run
  Once; ``TILT Autofocus``; an error → status 1303, log ``Tilt (Autofocus)``, ``Error`` (no retry) → ``Autofocus
  Finished`` (sm d73) → ``Refocus Init`` or the measure state;
- ``Refocus Init`` (sm d74) → ``Refocus`` (sm d75): status 306; Get Image, LL refocus Run Once (``refocus.*``, the
  tilt camera), Get Image; ``TILT Refocus``; an error → status 1304, log ``Tilt (Refocus)``, ``Error`` → ``Refocus
  Finished`` (sm d80) → the measure state;
- ``Measure Tilt (Straight Edge)`` (sm d81): status 307; Get Image Run Once; :func:`alox.vision.tilt.
  measure_tilt_straight_edge` with Edge Options {polarity, kernel, width, minimum strength, bilinear, average} and
  Line Options {1 line, Edge Method, min score 10, max score 1000, Orientation = Angle Expected, Angle Range,
  tolerance 1°, Step (Pixels), min SNR 0, Min. Points, 5 Hough iterations}; overlays (``Overlay Straight Edge`` →
  found line, ``Overlay Edges`` → search lines and edges, ``Overlay Tilt`` → the result box); ``Tilt Angle`` /
  ``Tilt Valid`` = Tilt Found AND # edges OK AND Tilt Tol OK; a processing error is logged (``Tilt (Measure Straight
  Edge)``), not fatal; ``TILT Measured`` → ``Notify Client``;
- ``Measure Tilt (Objects)`` (sm d84…d102, 4 frames): status 308; Get Image; ``alox_vision_detect_object_
  preprocessing`` (Morph Open/Close); the contours overlay (``Overlay Edges``); while object 1 is not found:
  geometric match of model 1 (1 match, rotation [0, 360]°, occlusion [0, Obj1 Max Occl], curve options as Search
  Object), ``Object 1 found ? = #matches > 0``, its pixel position converted to wafer mm (``Get Position XY`` +
  ``tools_tracking_move_relative``, then "Convert" XY Stages → Wafer) and the position index kept; while object 2 is
  not found: the image with the object-1 match masked (``alox_vision_mask_matched_object``), match of model 2, the
  same conversion — the two objects may be found at different XY positions (changelog 1.0.0.7); when both are found
  (frame 2): ``tools_tilt_function_object_detection_status`` (angle of Match1 → Match2 in wafer mm folded into
  (−90°, 90°], valid within Angle Expected ± Angle Range of the Straight Edge Detection settings) and the box ``TILT
  ANGLE: %.1f°`` / ``TILT NOT FOUND/INVALID``; else ``OBJECT FOUND`` / ``OBJECT NOT FOUND``; errors logged ``Tilt
  (Match Objects)``; ``TILT Measured`` → ``Notify Client``;
- ``Notify Client`` (sm d103: empty) → ``Display & Save`` (sm d104…d121, 5 frames): status 309; a valid tilt: "Set
  Tilt" and the websocket ``measure_tilt`` (log ``TILT (Websocket)``); ``TILT Measured``; ``log.savetiltimages`` (T):
  the camera's last image as ``<job data file name, format %y%m%d_%H%M%S_tilt>.png`` in ``images\tilt`` (log ``TILT
  (Log Image)``); ``log.savetiltdata`` (T) and a valid tilt: ``<job data file name>.tilt`` in ``files\tilt``, the
  angle (``Write To Spreadsheet File``, ``%.3f``; log ``TILT (Log Data)``). Next: valid → ``Finished``; both objects
  found (angle out of range) → ``Error``; else ``Move To Pos`` (the next snail position);
- ``Finished`` (sm d124…d151, 7 frames): status 400; ``TILT Finished``; log info ``Tilt Angle found: %f°``; status
  500; returns the :class:`TiltReport`;
- ``Error`` (sm d152…d161): ``TILT Finished``, log ``Tilt (Error)``, websocket ``error`` (``measure_tilt``, code,
  text); then :class:`TiltFailed` (the core sends ``failed``);
- ``Stopped`` (sm d162…d171, a stop or an abort): status 850, ``TILT Finished``, websocket ``failed`` (the core does not
  send it for a stop); :class:`~alox.core.functions.Stopped` is re-raised.

Reproduced: the states and transitions, the statuses 301…309, 400, 500, 850, 1301…1304, 1499, the error codes 9086 and
9530, the snail and its retry/timeout rules (4 move attempts, ±0.001 mm, 100 ms settle, 30 s), focus at the first
position only, the measurement options as wired, the "loop over the positions until a valid tilt" of both methods,
the objects kept across positions, the pixel → wafer conversion, the overlay texts, the image and data files and
their names, the ``measure_tilt`` JSON (sent for a valid tilt only), the log sources.

Fixed (internal defects, or behaviour that cannot be kept):

- Both objects found with an angle out of Angle Expected ± Angle Range went to ``Error`` with no error set: a
  websocket ``error`` with code 0 and the tilt status left at 309 "data logging". Here: status 1499 and error
  **9535** "tilt error (tilt angle out of range)".
- ``Obj1/Obj2 Min Score (%)`` was wired unscaled into ``IMAQ Match Geometric Pattern 2`` whose score is 0–1000 (70 %
  meant 7 %); Search Object multiplies by 10. Here ``× 10`` (:attr:`TiltOptions.score_scale`; 1.0 = as wired).
- ``alox_vision_mask_matched_object`` read literally keeps only the object-1 box for the object-2 search; it removes
  object 1 here so that object 2 is not found on it again (:attr:`TiltOptions.mask_keep_inside`; see
  :mod:`alox.vision.tilt`).
- The ``Finished`` branch "tilt not found" (status 1499, error 9536) cannot be reached (``Finished`` follows a valid
  tilt only); if it were, :class:`TiltFailed` 9536 is raised instead of ending as finished.
- ``hardware.xy.speed`` missing: LabVIEW sent ``VEL 0``; the XY device's configured speed is kept.
- A failed ``Get Position XY`` during the object conversion (LabVIEW: (0, 0)) uses the last ``Current XY Pos``.
- An invalid parameter (not a number, an unknown enum value, Step (Pixels) < 1) is refused before any motion:
  status 1300 and :class:`TiltError` 9002.
- Without an edge LabVIEW reported Angle 0 (the default of an empty array); the angle is NaN here (the JSON is only
  sent for a valid tilt, so the supervisor sees no difference).

Not ported: the run panel (``Launch Run UI``, ``Close Panel``), the system status 202/500 (the core's), the ``Tilt
Job Finished`` command (the core's runner callback), the simulation variant (§3.14: the devices are simulated
instead). ``IMAQ Find Straight Edges 3`` and the geometric matching are the stand-ins of :mod:`alox.vision.tilt` and
:mod:`alox.vision.matching`: the angles agree on clean edges and marks (tests), but NI's scores, sub-pixel models
and outlier rules are not reproduced, so ``Min. Points`` and ``Min Score`` tuned on NI may need adjusting.

**Long-baseline methods** (Python only, owner's answer R22–R24 "build a better method"; a recipe selects them with
``method`` 2 or 3, the default stays Straight Edge). The LabVIEW methods measure inside one field of view, against the
camera's pixel axes; these measure a feature at stage positions up to L apart, against the **stage** axes that the
mesa job's Tilt Correction moves (precision table and algorithms: :mod:`alox.vision.tilt`, "Precise methods"):

- **Long Baseline Edge** (2): at the search position (the snail, autofocus and refocus as above) ``Measure Tilt
  (Long Baseline Edge)`` (status 310): the wafer flat in the image, sub-pixel edge points and a robust line fit
  (:func:`alox.vision.tilt.fit_edge_line`, with the Straight Edge Detection's Minimum Edge Strength and Polarity);
  its point nearest the image centre and its direction, converted to wafer mm as the objects are (``Get Position
  XY`` + ``tools_tracking_move_relative``: calibration, flips, stage coordinate system; Stages → Wafer). Then the
  baseline positions −s, +s with s = L/2 (or ``First Step``, then growing): ``Move To Baseline Pos`` (312; the stage
  to the edge point predicted on the line through the points measured so far, so that the edge is at the image
  centre; moves retried as ``Move To Pos``: 1301), ``Baseline Position OK?`` (313; ±0.001 mm, settling, 30 s →
  9530 / 1302), ``Measure Baseline Point`` (310; optional LL refocus, ``Long Baseline Refocus``: 1304; the edge
  point nearest the centre; an edge whose angle differs from the start image's by more than the consistency
  tolerance is another edge: lost). Angle = the line through the points (three with ``First Step`` 0;
  ``atan2(Δy, Δx)`` in wafer mm, folded into ``Angle Expected ± 90°``).
- **Lattice** (3): no model: ``Measure Tilt (Lattice)`` (311): the mesas of the Low Mag image
  (:func:`alox.vision.tilt.detect_mesas`), the grid (:func:`alox.vision.tilt.fit_lattice`; owner S6 "can be
  everything": any lattice type — square, rectangular, hexagonal, centred rectangular, oblique — at any angle to the
  flat): its reduced basis, its type and rotational symmetry (hexagonal 60°, square 90°, otherwise 180°) and the
  **row** = a shortest lattice vector (the densest row), the one nearest the expected direction ``Angle Expected +
  Grid Row Angle`` (``latticeangledeg``: the angle of that row against the wafer X axis by design, 0 = parallel to
  it), the mesa nearest the image centre as the start. The walk follows that lattice vector: ±s₁, ±s₂, … (s₁ =
  ``First Step`` or :attr:`TiltOptions.lattice_first_pitches` pitches; each offset a whole multiple of the vector, so
  that a mesa is at the centre); at each position the mesa of the start row nearest the prediction
  (:func:`alox.vision.tilt.pick_row_mesa`; farther than ¼ of the row spacing off the predicted row: lost, the row
  cannot be told apart); the next half-baseline is at most ``Growth`` × the previous and only as long as the current
  uncertainty keeps the predicted row within that ¼ spacing at 4σ (:func:`alox.vision.tilt.next_baseline_step`),
  until L/2. The row direction must be known to better than (¼ spacing)/s₁ (10 pitches: ±1.4°) from the one image.
  The tilt is the row's angle (wafer frame) **less the Grid Row Angle**, folded to the lattice symmetry around
  ``Angle Expected`` (``Angle Expected ± 30°`` hexagonal, ± 45° square, ± 90° otherwise): the rotation of the wafer
  that the mesa job's Tilt Correction applies, whatever the grid's orientation against the flat.
- Both: the angle's uncertainty (1σ: the detection errors and :attr:`TiltOptions.stage_repeatability_um` over the
  baseline, or the residuals when larger) in :attr:`TiltReport.uncertainty_deg`, the points in
  :attr:`TiltReport.baseline`; **valid** when the one-image angle (edge: the mean of the images; lattice: the start
  image) agrees with the baseline angle within ``Consistency`` (it differs by the camera's rotation) and the angle
  lies within ``Angle Expected ± Angle Range``; ``measure_tilt``, images, data file and ``Finished`` as the other
  methods; the overlay ``TILT ANGLE: %.3f° ± %.3f°``. A whole walk out of range → status 1499, error 9535 (as both
  objects); inconsistent (``TILT INCONSISTENT``), or the feature lost → the next search position (snail), then 9086.
  After the walk (complete or lost) the stage goes back to the walk's start position (owner S8 "Start is better"):
  ``Return To Start`` (312) and ``Start Position OK?`` (313), the rules of ``Move To Pos`` / ``Position OK?``; an
  error there is logged as a warning and the run goes on (the measurement is done).
- Recipe keys (:class:`LongBaselineDetection`): ``lblength`` (L, 20 mm), ``lbfirststep`` (0 mm = automatic),
  ``lbgrowth`` (8), ``lbtolerancedeg`` (consistency, 0.5°), ``lbrefocus`` (F), ``latticepitchum`` (the length of the
  shortest lattice vector, 0 µm = measured), ``latticeangledeg`` (Grid Row Angle, 0°);
  shared with Straight Edge Detection: ``expectedangledeg``, ``anglerangedeg``, ``edgestrength``, ``edgepolarity``
  (and ``edgesearchdir`` for the polarity's sense). Server settings in :class:`TiltOptions` (``lb_*``,
  ``stage_repeatability_um``, ``lattice_*``). Statuses 310…313 are not in ``tilt_en.lang``.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, fields, replace
from datetime import UTC, datetime
from enum import IntEnum, StrEnum
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
from alox.core.status import TiltStatus
from alox.devices.base import DeviceError
from alox.functions import jobfiles
from alox.functions.focus import CameraType
from alox.functions.get_image import stage_to_wafer
from alox.functions.search_object import (
    default_autofocus_params,
    default_refocus_params,
    match_overlay,
    object_position,
    snail_positions,
    wafer_to_stage,
)
from alox.functions.services import (
    Line,
    Points,
    Rect,
    Services,
    Shape,
    Text,
    camera_device_name,
)
from alox.functions.tracking import (
    INVALID_ARGUMENTS,
    FunctionFailed,
    app_log,
    error_code_source,
    flag,
    number,
    setting,
    stage_coordinate_system,
)
from alox.vision import matching
from alox.vision.edges import EdgeOptions, EdgePolarity
from alox.vision.matching import (
    CurveOptions,
    Match,
    Matcher,
    MatchSettings,
    Template,
    TemplateError,
)
from alox.vision.tilt import (
    BaselineFit,
    EdgeLineFit,
    EdgeMethod,
    LatticeFit,
    LineOptions,
    RakeDirection,
    StraightEdgeTiltReport,
    baseline_target,
    detect_mesas,
    fit_baseline,
    fit_edge_line,
    fit_lattice,
    fold_angle,
    mask_matched_object,
    measure_tilt_straight_edge,
    next_baseline_step,
    pick_row_mesa,
)
from alox.vision.wafer import tilt_from_objects
from aloxcam.errors import AloxError, FocusError
from aloxcam.imaging.png import PngError, write_png

__all__ = [
    "TILT_ANGLE_NOT_FOUND",
    "TILT_NOT_FOUND",
    "TILT_OUT_OF_RANGE",
    "TILT_XY_TIMEOUT",
    "BaselineReport",
    "DualObjectsDetection",
    "LongBaselineDetection",
    "StraightEdgeDetection",
    "TiltError",
    "TiltFailed",
    "TiltMeasurement",
    "TiltMethod",
    "TiltOptions",
    "TiltParams",
    "TiltReport",
    "TiltState",
]

log = logging.getLogger(__name__)

#: ``alox_en.lang`` codes.
TILT_NOT_FOUND = 9086              # "tilt not found error" (Move To Pos: no position left)
TILT_XY_TIMEOUT = 9530             # "tilt error (xy stages timeout)"
TILT_OUT_OF_RANGE = 9535           # "tilt error (tilt angle out of range)" (Fixed: both objects, invalid angle)
TILT_ANGLE_NOT_FOUND = 9536        # "tilt error (tilt angle not found)" (Finished, unreachable)
UNKNOWN_ERROR = 9001

REPORT_NAME = "measure_tilt"
OVERLAY = "tilt"                   # LabVIEW group "HL_TILT"
BLUE, ORANGE, RED, YELLOW, GREEN = (0, 0, 255), (255, 128, 0), (255, 0, 0), (255, 255, 0), (0, 255, 0)


class TiltState(StrEnum):
    """``alox_typedef_tilt_measurement_function_state``."""

    START = "Start"
    STARTED = "Started"
    MOVE_TO_POS = "Move To Pos"
    POSITION_OK = "Position OK?"
    AUTOFOCUS_INIT = "Autofocus Init"
    AUTOFOCUS = "Autofocus"
    AUTOFOCUS_FINISHED = "Autofocus Finished"
    REFOCUS_INIT = "Refocus Init"
    REFOCUS = "Refocus"
    REFOCUS_FINISHED = "Refocus Finished"
    MEASURE_STRAIGHT_EDGE = "Measure Tilt (Straight Edge)"
    MEASURE_OBJECTS = "Measure Tilt (Objects)"
    MEASURE_LONG_BASELINE = "Measure Tilt (Long Baseline Edge)"        # Python only (not in the typedef)
    MEASURE_LATTICE = "Measure Tilt (Lattice)"                         # Python only
    BASELINE_MOVE = "Move To Baseline Pos"                             # Python only
    BASELINE_POSITION_OK = "Baseline Position OK?"                     # Python only
    BASELINE_MEASURE = "Measure Baseline Point"                        # Python only
    BASELINE_RETURN = "Return To Start"                                # Python only (owner S8)
    BASELINE_RETURN_OK = "Start Position OK?"                          # Python only
    NOTIFY_CLIENT = "Notify Client"
    DISPLAY_SAVE = "Display & Save"
    FINISHED = "Finished"
    ERROR = "Error"
    STOPPED = "Stopped"


class TiltMethod(IntEnum):
    """``Tilt Method``: 0 and 1 as LabVIEW; 2 and 3 Python only (module docstring, "Long-baseline methods")."""

    STRAIGHT_EDGE = 0
    DUAL_OBJECTS = 1
    LONG_BASELINE_EDGE = 2
    LATTICE = 3

    @classmethod
    def coerce(cls, value: Any) -> TiltMethod:
        if isinstance(value, str) and not value.strip().lstrip("-").isdigit():
            key = value.strip().lower().replace(" ", "").replace("_", "")
            for member, label in ((cls.STRAIGHT_EDGE, "straightedge"), (cls.DUAL_OBJECTS, "dualobjects"),
                                  (cls.LONG_BASELINE_EDGE, "longbaselineedge"), (cls.LATTICE, "lattice")):
                if key == label:
                    return member
            raise TiltError(INVALID_ARGUMENTS, f"Tilt Method: unknown value {value!r}")
        return cls(_enum_int(value, "Tilt Method", cls))


class TiltError(FunctionFailed):
    """A tilt error: 9002 invalid parameters; else the code of the failure."""


class TiltFailed(TiltError):
    """The run reached ``Error``: ``state`` failed, ``status`` is the tilt status at the end, ``report`` the data."""

    def __init__(self, code: int, source: str, *, state: str, status: int, report: TiltReport) -> None:
        super().__init__(code, source, state=state, report=report)
        self.status = status


# ================================================================================================== parameters
def _set(obj: object, name: str, value: Any) -> None:
    object.__setattr__(obj, name, value)


def _enum_int(value: Any, what: str, enum: type[IntEnum]) -> int:
    try:
        n = int(number(value, what, integer=True))
        enum(n)
    except (FunctionFailed, ValueError):
        raise TiltError(INVALID_ARGUMENTS, f"{what}: unknown value {value!r}") from None
    return n


def _profile_get(profile: Any, key: str, default: Any) -> Any:
    try:
        value = profile.get(key, default)
    except (RuntimeError, LookupError, TypeError, ValueError):          # no profile loaded
        return default
    return default if value is None else value


@dataclass(frozen=True)
class StraightEdgeDetection:
    """``Straight Edge Detection`` (cluster order; profile defaults of ``bundle d``)."""

    search_direction: RakeDirection = RakeDirection.LEFT_TO_RIGHT
    edge_polarity: EdgePolarity = EdgePolarity.ALL
    min_edge_strength: float = 20.0
    step_px: int = 30
    width: int = 10
    kernel_size: int = 3
    min_points_percent: float = 25.0
    angle_expected_deg: float = 0.0
    angle_range_deg: float = 10.0
    edge_method: EdgeMethod = EdgeMethod.FIRST_EDGE_RAKE

    def __post_init__(self) -> None:
        for name, enum in (("search_direction", RakeDirection), ("edge_polarity", EdgePolarity),
                           ("edge_method", EdgeMethod)):
            _set(self, name, enum(_enum_int(getattr(self, name), name, enum)))
        for name in ("step_px", "width", "kernel_size"):
            _set(self, name, number(getattr(self, name), name, integer=True))
        for name in ("min_edge_strength", "min_points_percent", "angle_expected_deg", "angle_range_deg"):
            _set(self, name, number(getattr(self, name), name))
        if self.step_px < 1 or self.width < 1 or self.kernel_size < 1:
            raise TiltError(INVALID_ARGUMENTS, "Straight Edge Detection: Step (Pixels), Width and Kernel Size must be "
                                               f">= 1, got {self.step_px}, {self.width}, {self.kernel_size}")

    def edge_options(self) -> EdgeOptions:
        """``Edge Options`` (sm d81; interpolation bilinear, projection average)."""
        return EdgeOptions(polarity=self.edge_polarity, kernel_size=self.kernel_size, width=self.width,
                           min_strength=self.min_edge_strength)

    def line_options(self) -> LineOptions:
        """``Line Options`` as sm d81 bundles them."""
        return LineOptions(number_of_lines=1, method=self.edge_method, min_score=10.0, max_score=1000.0,
                           orientation_deg=self.angle_expected_deg, angle_range_deg=self.angle_range_deg,
                           angle_tolerance_deg=1.0, step_px=self.step_px, min_snr=0.0,
                           min_points_percent=self.min_points_percent, hough_iterations=5)


@dataclass(frozen=True)
class DualObjectsDetection:
    """``Dual Objects Detection`` (cluster order; profile defaults of ``bundle d``); scores and occlusions in %."""

    obj1_model_path: str = ""
    obj1_mask_path: str = ""
    obj2_model_path: str = ""
    obj2_mask_path: str = ""
    edge_threshold: int = 25
    min_length: int = 5
    morph_open: int = 0
    morph_close: int = 0
    obj1_min_score: float = 70.0
    obj1_max_occlusion: float = 40.0
    obj2_min_score: float = 70.0
    obj2_max_occlusion: float = 40.0

    def __post_init__(self) -> None:
        for name in ("obj1_model_path", "obj1_mask_path", "obj2_model_path", "obj2_mask_path"):
            _set(self, name, "" if getattr(self, name) is None else str(getattr(self, name)))
        for name in ("edge_threshold", "min_length", "morph_open", "morph_close"):
            _set(self, name, number(getattr(self, name), name, integer=True))
        for name in ("obj1_min_score", "obj1_max_occlusion", "obj2_min_score", "obj2_max_occlusion"):
            _set(self, name, number(getattr(self, name), name))

    def curve(self) -> CurveOptions:
        """``Curve Options`` (sm d84: Normal, Edge Threshold, Normal, Min Length, steps 5, gap 5, F, F)."""
        return CurveOptions(extraction_mode="Normal", edge_threshold=self.edge_threshold, edge_filter_size="Normal",
                            min_length=self.min_length, row_step=5, column_step=5, max_endpoint_gap=5, closed=False,
                            subpixel=False)

    def match_settings(self, obj: int, *, score_scale: float = 10.0, number_of_matches: int = 1) -> MatchSettings:
        """``alox_vision_bundle_setup_geometric_matching_options`` (rotation [0, 360]°, occlusion [0, Max Occl]) and
        ``IMAQ Match Geometric Pattern 2`` (1 match, minimum score ``Min Score × score_scale``)."""
        min_score, occlusion = ((self.obj1_min_score, self.obj1_max_occlusion) if obj == 1
                                else (self.obj2_min_score, self.obj2_max_occlusion))
        return MatchSettings(number_of_matches=number_of_matches, min_score=min_score * score_scale,
                             angle_ranges=((0.0, 360.0),), occlusion_range=(0.0, occlusion), subpixel=False,
                             curve=self.curve())


@dataclass(frozen=True)
class LongBaselineDetection:
    """``Long Baseline`` settings of the Python-only Tilt Methods 2 and 3 (profile ``measure.tilt.lb*`` and
    ``latticepitchum``; module docstring): the total baseline L (mm), the first half-baseline (mm; 0: L/2 for the
    edge, :attr:`TiltOptions.lattice_first_pitches` pitches for the lattice), the largest ratio between two
    successive half-baselines, the largest difference (°) between the angle seen in the images and the baseline
    angle, a refocus at every baseline position, the mesa pitch along a row (µm, the shortest lattice vector; 0:
    measured in the image), the angle of that row against the wafer X axis by design (°, wafer frame,
    counter-clockwise: 0 when the rows are parallel to the flat's axis; subtracted from the measured row angle)."""

    length_mm: float = 20.0
    first_step_mm: float = 0.0
    growth: float = 8.0
    consistency_deg: float = 0.5
    refocus: bool = False
    lattice_pitch_um: float = 0.0
    lattice_angle_deg: float = 0.0

    def __post_init__(self) -> None:
        for name in ("length_mm", "first_step_mm", "growth", "consistency_deg", "lattice_pitch_um",
                     "lattice_angle_deg"):
            _set(self, name, number(getattr(self, name), name))
        _set(self, "refocus", flag(self.refocus))
        if (self.length_mm <= 0 or self.first_step_mm < 0 or self.growth <= 1 or self.consistency_deg <= 0
                or self.lattice_pitch_um < 0):
            raise TiltError(INVALID_ARGUMENTS, "Long Baseline: Length > 0, First Step >= 0, Growth > 1, Consistency "
                                               f"> 0 and Pitch >= 0 expected, got {self!r}")


#: field → (profile key, default) of ``bundle d``.
_TOP_KEYS: dict[str, tuple[str, Any]] = {
    "camera_type": ("camera", 1), "method": ("method", 0), "max_moves": ("maxmoves", 0), "step_mm": ("step", 0.1),
    "autofocus": ("autofocus", True), "refocus": ("refocus", True), "show_window": ("showwindow", True),
    "overlay_match": ("overlaymatches", True), "overlay_straight_edge": ("overlaystraightedge", True),
    "overlay_edges": ("overlayedges", True), "overlay_tilt": ("overlaytilt", True)}
_EDGE_KEYS: dict[str, tuple[str, Any]] = {
    "search_direction": ("edgesearchdir", 0), "edge_polarity": ("edgepolarity", 0),
    "min_edge_strength": ("edgestrength", 20), "step_px": ("edgestep", 30), "width": ("edgewidth", 10),
    "kernel_size": ("edgekernel", 3), "min_points_percent": ("minpointspc", 25),
    "angle_expected_deg": ("expectedangledeg", 0.0), "angle_range_deg": ("anglerangedeg", 10.0),
    "edge_method": ("edgemethod", 0)}
_OBJECT_KEYS: dict[str, tuple[str, Any]] = {
    "obj1_model_path": ("obj1model", ""), "obj1_mask_path": ("obj1mask", ""), "obj2_model_path": ("obj2model", ""),
    "obj2_mask_path": ("obj2mask", ""), "edge_threshold": ("edgethreshold", 25), "min_length": ("minlength", 5),
    "morph_open": ("morphopen", 0), "morph_close": ("morphclose", 0), "obj1_min_score": ("obj1minscore", 70.0),
    "obj1_max_occlusion": ("obj1maxocclusionpc", 40.0), "obj2_min_score": ("obj2minscore", 70.0),
    "obj2_max_occlusion": ("obj2maxocclusionpc", 40.0)}
_BASELINE_KEYS: dict[str, tuple[str, Any]] = {
    "length_mm": ("lblength", 20.0), "first_step_mm": ("lbfirststep", 0.0), "growth": ("lbgrowth", 8.0),
    "consistency_deg": ("lbtolerancedeg", 0.5), "refocus": ("lbrefocus", False),
    "lattice_pitch_um": ("latticepitchum", 0.0), "lattice_angle_deg": ("latticeangledeg", 0.0)}


@dataclass(frozen=True)
class TiltParams:
    """``alox_typedef_tilt_measurement_parameters``; ``start_pos`` in wafer mm, ``step_mm`` in mm."""

    camera_type: CameraType = CameraType.LOW_MAG
    start_pos: tuple[float, float] = (0.0, 0.0)
    method: TiltMethod = TiltMethod.STRAIGHT_EDGE
    max_moves: int = 0
    step_mm: float = 0.1
    straight_edge: StraightEdgeDetection = field(default_factory=StraightEdgeDetection)
    dual_objects: DualObjectsDetection = field(default_factory=DualObjectsDetection)
    long_baseline: LongBaselineDetection = field(default_factory=LongBaselineDetection)
    autofocus: bool = True
    refocus: bool = True
    show_window: bool = True
    overlay_match: bool = True
    overlay_straight_edge: bool = True
    overlay_edges: bool = True
    overlay_tilt: bool = True

    def __post_init__(self) -> None:
        try:
            _set(self, "camera_type", CameraType.coerce(self.camera_type, "Camera Type"))
        except FocusError as exc:
            raise TiltError(INVALID_ARGUMENTS, exc.source) from None
        start = tuple(self.start_pos)
        if len(start) != 2:
            raise TiltError(INVALID_ARGUMENTS, f"Start Search Pos (mm): (x, y) expected, got {self.start_pos!r}")
        _set(self, "start_pos", (number(start[0], "Start Search Pos X"), number(start[1], "Start Search Pos Y")))
        _set(self, "method", TiltMethod.coerce(self.method))
        _set(self, "max_moves", number(self.max_moves, "Max Moves", integer=True))
        _set(self, "step_mm", number(self.step_mm, "Step (mm)"))
        for name in ("autofocus", "refocus", "show_window", "overlay_match", "overlay_straight_edge", "overlay_edges",
                     "overlay_tilt"):
            _set(self, name, flag(getattr(self, name)))
        if not isinstance(self.straight_edge, StraightEdgeDetection):
            _set(self, "straight_edge", StraightEdgeDetection(**dict(self.straight_edge)))
        if not isinstance(self.dual_objects, DualObjectsDetection):
            _set(self, "dual_objects", DualObjectsDetection(**dict(self.dual_objects)))
        if not isinstance(self.long_baseline, LongBaselineDetection):
            _set(self, "long_baseline", LongBaselineDetection(**dict(self.long_baseline)))

    @classmethod
    def from_profile(cls, profile: Any, **overrides: Any) -> TiltParams:
        """The ``measure.tilt.*`` keys of the active profile with the bundle VI's defaults; ``overrides`` (field
        names; ``straight_edge`` / ``dual_objects`` / ``long_baseline`` may be mappings of their fields, merged)
        win."""
        def read(keys: Mapping[str, tuple[str, Any]]) -> dict[str, Any]:
            return {name: _profile_get(profile, f"measure.tilt.{key}", default) for name, (key, default) in keys.items()}
        values = read(_TOP_KEYS)
        values["start_pos"] = (_profile_get(profile, "measure.tilt.startx", 0.0),
                               _profile_get(profile, "measure.tilt.starty", 0.0))
        edge, objects, baseline = read(_EDGE_KEYS), read(_OBJECT_KEYS), read(_BASELINE_KEYS)
        extra = dict(overrides)
        for name, base in (("straight_edge", edge), ("dual_objects", objects), ("long_baseline", baseline)):
            given = extra.pop(name, None)
            if isinstance(given, (StraightEdgeDetection, DualObjectsDetection, LongBaselineDetection)):
                values[name] = given
            else:
                base.update(dict(given or {}))
                values[name] = base
        values.update(extra)
        return cls(**values)


@dataclass(frozen=True)
class TiltOptions:
    """Server-side settings (defaults: the LabVIEW values, except the fixes of the module docstring)."""

    position_tolerance_mm: float = 0.001     # Position OK?: In Range ±0.001 (sm d56)
    move_timeout_s: float = 30.0             # "XY stages timeout" 30000 ms → 9530
    settle_s: float = 0.100                  # "Make sure the stages has stopped moving"
    poll_s: float = 0.010                    # between two position reads (LabVIEW: the loop wait)
    move_retries: int = 3                    # Error Count ≥ 3 → Error (4 attempts)
    fit_max_distance_px: float = 2.0         # straight-edge fit: inlier distance (stand-in of NI's fit)
    score_scale: float = 10.0                # Min Score (%) → 0–1000 (LabVIEW as wired: 1.0)
    number_of_matches: int = 1               # IMAQ Match Geometric Pattern 2 (sm d84)
    mask_keep_inside: bool = False           # alox_vision_mask_matched_object read literally: True
    x_stage_invert: bool = False             # debug globals of tools_tracking_move_relative (OFF for AET)
    y_stage_invert: bool = False
    data_format: str = "%.3f"                # Write To Spreadsheet File default format
    # Python-only Tilt Methods 2 and 3 (module docstring, "Long-baseline methods")
    lb_profile_step_px: int = 4              # Long Baseline Edge: one edge point every n pixels along the edge
    lb_profile_width_px: int = 5             # … each profile averaged over n parallel lines
    lb_smoothing_px: float = 1.5             # σ of the derivative-of-Gaussian across the edge
    lb_fit_max_distance_px: float = 1.5      # line fit: inlier distance
    stage_repeatability_um: float = 0.5      # 1σ of a stage position: part of the uncertainty and of the walk steps
    lattice_first_pitches: int = 10          # Lattice: first half-baseline (pitches) when lbfirststep is 0
    lattice_row_tolerance: float = 0.25      # a mesa farther than this × the row spacing off the predicted row: lost
    lattice_min_mesas: int = 6               # mesas needed in the start image


# ================================================================================================== results
Point = tuple[float, float]


@dataclass(frozen=True)
class BaselineReport:
    """A long-baseline measurement (Tilt Methods 2 and 3): the feature's points (wafer mm: the edge point nearest the
    image centre, or the mesa of the start row) in the order measured, their offsets along the line (mm) and the stage
    positions (mm) of their images; the line fit (``fit``: angle, uncertainty, length); the angle of the feature seen
    in the images (wafer frame: the mean of the edge fits, or the lattice of the start image, less the Grid Row
    Angle) and the checks; ``start_fit`` the fit of the start image (pixels). The lattice's ``fit.angle_deg`` is the
    row's direction; the tilt is that less the Grid Row Angle."""

    method: TiltMethod
    points_mm: tuple[Point, ...]
    offsets_mm: tuple[float, ...]
    stage_mm: tuple[Point, ...]
    fit: BaselineFit | None
    image_angle_deg: float
    image_angles_deg: tuple[float, ...]
    complete: bool
    consistent: bool
    in_range: bool
    message: str
    start_fit: EdgeLineFit | LatticeFit | None = None


@dataclass(frozen=True)
class TiltReport:
    """``alox_typedef_tilt_measurement_report``: the straight-edge report, the best matches (pixels and wafer mm) and
    the index of the position where each object was found (``Current Pos Index``: 1 = the start position), ``Tilt
    Angle (°)`` (NaN until measured) and its validity, the process times (ms), the SEMI job; plus the search positions
    (XY stage mm), the files written and the tilt status at the end; for the long-baseline methods the uncertainty
    (1σ, °) and the :class:`BaselineReport`."""

    job: JobInfo
    method: TiltMethod
    angle_deg: float = math.nan
    valid: bool = False
    straight_edge: StraightEdgeTiltReport | None = None
    object1_found: bool = False
    object2_found: bool = False
    best_match1_px: Point | None = None
    best_match1_mm: Point | None = None
    object1_index: int = 0
    best_match2_px: Point | None = None
    best_match2_mm: Point | None = None
    object2_index: int = 0
    match1: Match | None = None
    match2: Match | None = None
    search_positions: tuple[Point, ...] = ()
    current_index: int = 0
    current_xy: Point = (0.0, 0.0)
    edges_process_time_ms: float = 0.0
    match_process_time_ms: float = 0.0
    process_time_ms: float = 0.0
    images: tuple[Path, ...] = ()
    data_file: Path | None = None
    status: int = 0
    uncertainty_deg: float = math.nan            # 1σ (Tilt Methods 2 and 3)
    baseline: BaselineReport | None = None       # Tilt Methods 2 and 3

    def as_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


# ================================================================================================== the function
class TiltMeasurement(MeasurementFunction):
    """HL Tilt measurement (module docstring).

    ``run(params, ctx)`` accepts the core's :class:`alox.core.core.MeasureRequest` (the active profile's
    ``measure.tilt.*``; its ``overrides["x"]`` / ``["y"]`` replace the start position), a :class:`TiltParams`, a
    mapping of :class:`TiltParams` fields over the profile, or None. Returns a :class:`TiltReport`; raises
    :class:`TiltError` 9002 for invalid parameters (no motion), :class:`TiltFailed` after ``Error`` and
    :class:`~alox.core.functions.Stopped` on stop/abort.

    The LL functions are called with ``ctx.run_once``: :attr:`FunctionName.GET_IMAGE` (the measurement images),
    :attr:`FunctionName.AUTOFOCUS_LARGE`, :attr:`FunctionName.REFOCUS`; ``autofocus_params`` / ``refocus_params``
    (``f(services, camera_type)``) replace :func:`~alox.functions.search_object.default_autofocus_params` /
    ``default_refocus_params``. ``matcher``: a :class:`alox.vision.matching.Matcher` (default
    :func:`~alox.vision.matching.create_matcher`, at the first Dual Objects run). ``now`` gives the local time of the
    file names."""

    name = FunctionName.TILT_MEASURE

    def __init__(self, services: Services, matcher: Matcher | None = None, *, options: TiltOptions | None = None,
                 autofocus_params: Callable[[Services, CameraType], Any] | None = None,
                 refocus_params: Callable[[Services, CameraType], Any] | None = None,
                 now: Callable[[], datetime] | None = None) -> None:
        services.device("xy")
        self.services = services
        self._matcher = matcher
        self.options = options or TiltOptions()
        self.autofocus_params = autofocus_params or default_autofocus_params
        self.refocus_params = refocus_params or default_refocus_params
        self._now = now or (lambda: datetime.now(UTC).astimezone())
        self.last_report: TiltReport | None = None

    @property
    def matcher(self) -> Matcher:
        if self._matcher is None:
            self._matcher = matching.create_matcher()
            log.info("tilt: %s matcher", self._matcher.name)
        return self._matcher

    def model_path(self, name: str) -> Path:
        """``<settings>\\profile\\<name>`` (sm d8, d11…d25); ``name`` as is without folders or when absolute."""
        path = Path(name)
        folders = self.services.folders
        if folders is None or path.is_absolute():
            return path
        return Path(folders.profile) / path

    def params_for(self, request: Any) -> TiltParams:
        """:class:`TiltParams` of a request (class docstring)."""
        if isinstance(request, TiltParams):
            return request
        profile = self.services.profile
        if request is None:
            return TiltParams.from_profile(profile)
        overrides = getattr(request, "overrides", None)
        if overrides is not None:                                   # MeasureRequest
            base = TiltParams.from_profile(profile)
            x = overrides.get("x")
            y = overrides.get("y")
            if x is None and y is None:
                return base
            start = (base.start_pos[0] if x is None else x, base.start_pos[1] if y is None else y)
            return replace(base, start_pos=start)
        if isinstance(request, Mapping):
            return TiltParams.from_profile(profile, **dict(request))
        raise TiltError(INVALID_ARGUMENTS, f"tilt: unsupported parameters {type(request).__name__}")

    def run(self, params: Any, ctx: FunctionContext) -> TiltReport:
        job = ctx.job if ctx.job is not None else getattr(params, "job", None) or JobInfo()
        try:
            p = self.params_for(params)
        except AloxError as exc:                                    # invalid parameters: no motion
            self.services.set_status("tilt", int(TiltStatus.ERROR))
            if isinstance(exc, TiltError):
                raise
            raise TiltError(exc.code, exc.source) from exc
        run = _TiltJob(self, p, job, ctx)
        try:
            report = run.execute()
        except TiltFailed as exc:
            self.last_report = exc.report
            raise
        except Stopped:
            self.last_report = run.report()
            raise
        self.last_report = report
        return report


@dataclass
class _Walk:
    """The state of a long-baseline walk (Tilt Methods 2 and 3), wafer mm: the feature at the start position and the
    direction along it (unit) from the start image; the half-baselines ±s₁, ±s₂, … still to visit; the points."""

    lattice: bool
    start: Point
    direction: Point
    half: float
    drift_limit: float
    step: float
    image_angle: float
    start_fit: EdgeLineFit | LatticeFit
    sigma_point: float = 0.0
    pitch_along: float = math.nan
    pitch_across: float = math.nan
    horizontal: bool | None = None
    period: float = 180.0                    # the symmetry of the feature: a line 180°, a lattice its own
    angle_offset: float = 0.0                # lattice: the Grid Row Angle (the row's angle when the tilt is 0)
    home: Point = (math.nan, math.nan)       # stage position of the start image (the walk returns there)
    moved: bool = False                      # a baseline move was commanded
    pending: list[float] = field(default_factory=list)
    target: Point = (math.nan, math.nan)
    target_stage: Point = (math.nan, math.nan)
    offset: float = 0.0
    points: list[Point] = field(default_factory=list)
    sigmas: list[float] = field(default_factory=list)
    offsets: list[float] = field(default_factory=list)
    stage: list[Point] = field(default_factory=list)
    image_angles: list[float] = field(default_factory=list)

    def add(self, point: Point, sigma: float, offset: float, stage: Point, image_angle: float | None = None) -> None:
        self.points.append(point)
        self.sigmas.append(sigma)
        self.offsets.append(offset)
        self.stage.append(stage)
        if image_angle is not None:
            self.image_angles.append(image_angle)


class _TiltJob:
    """One run: the session (``Tilt Session``) and the states."""

    def __init__(self, function: TiltMeasurement, p: TiltParams, job: JobInfo, ctx: FunctionContext) -> None:
        self.f = function
        self.s = function.services
        self.o = function.options
        self.p = p
        self.job = job
        self.ctx = ctx
        self.xy = self.s.device("xy")
        self.camera = camera_device_name(int(p.camera_type))
        self.state = TiltState.START
        self.code = 0
        self.error: BaseException | None = None
        self.failed_state = ""
        # session / report
        self.positions: list[Point] = []
        self.index = 0
        self.current: Point = (0.0, 0.0)
        self.error_count = 0
        self.move_t = 0.0
        self.templates: dict[int, Template | None] = {1: None, 2: None}
        self.angle = math.nan
        self.valid = False
        self.se_report: StraightEdgeTiltReport | None = None
        self.found = {1: False, 2: False}
        self.match: dict[int, Match | None] = {1: None, 2: None}
        self.match_mm: dict[int, Point | None] = {1: None, 2: None}
        self.found_index = {1: 0, 2: 0}
        self.edges_ms = 0.0
        self.match_ms = 0.0
        self.process_ms = 0.0
        self.folders: jobfiles.JobFolders | None = None
        self.images: list[Path] = []
        self.data_file: Path | None = None
        self.walk: _Walk | None = None                              # Tilt Methods 2 and 3
        self.walk_shapes: list[Shape] = []                          # the overlay of the last baseline image
        self.baseline: BaselineReport | None = None
        self.uncertainty = math.nan

    # ---------------------------------------------------------------------------------------------- loop
    def report(self) -> TiltReport:
        return TiltReport(
            job=self.job, method=self.p.method, angle_deg=self.angle, valid=self.valid, straight_edge=self.se_report,
            object1_found=self.found[1], object2_found=self.found[2],
            best_match1_px=self.match[1].position if self.match[1] else None, best_match1_mm=self.match_mm[1],
            object1_index=self.found_index[1],
            best_match2_px=self.match[2].position if self.match[2] else None, best_match2_mm=self.match_mm[2],
            object2_index=self.found_index[2], match1=self.match[1], match2=self.match[2],
            search_positions=tuple(self.positions), current_index=self.index, current_xy=self.current,
            edges_process_time_ms=self.edges_ms, match_process_time_ms=self.match_ms,
            process_time_ms=self.process_ms, images=tuple(self.images), data_file=self.data_file, status=self.code,
            uncertainty_deg=self.uncertainty, baseline=self.baseline)

    def execute(self) -> TiltReport:
        handlers: dict[TiltState, Callable[[], TiltState]] = {
            TiltState.START: self.start, TiltState.STARTED: self.started, TiltState.MOVE_TO_POS: self.move_to_pos,
            TiltState.POSITION_OK: self.position_ok, TiltState.AUTOFOCUS_INIT: lambda: TiltState.AUTOFOCUS,
            TiltState.AUTOFOCUS: self.autofocus, TiltState.AUTOFOCUS_FINISHED: self.autofocus_finished,
            TiltState.REFOCUS_INIT: lambda: TiltState.REFOCUS, TiltState.REFOCUS: self.refocus,
            TiltState.REFOCUS_FINISHED: self.measure_state, TiltState.MEASURE_STRAIGHT_EDGE: self.measure_straight_edge,
            TiltState.MEASURE_OBJECTS: self.measure_objects, TiltState.NOTIFY_CLIENT: lambda: TiltState.DISPLAY_SAVE,
            TiltState.DISPLAY_SAVE: self.display_save, TiltState.MEASURE_LONG_BASELINE: self.measure_long_baseline,
            TiltState.MEASURE_LATTICE: self.measure_lattice, TiltState.BASELINE_MOVE: self.baseline_move,
            TiltState.BASELINE_POSITION_OK: self.baseline_position_ok,
            TiltState.BASELINE_MEASURE: self.baseline_measure, TiltState.BASELINE_RETURN: self.baseline_return,
            TiltState.BASELINE_RETURN_OK: self.baseline_return_ok}
        self.prepare()
        self.publish(TiltState.START)
        try:
            while self.state not in (TiltState.FINISHED, TiltState.ERROR):
                self.ctx.check()                                    # stop / abort between states
                nxt = handlers[self.state]()
                if nxt is not self.state:
                    self.publish(nxt)
                self.state = nxt
            if self.state is TiltState.FINISHED:
                return self.finished()
            raise self.error_state()
        except Stopped:
            self.stopped()
            raise

    def publish(self, state: TiltState) -> None:
        self.state = state
        data: dict[str, Any] = {"status": self.code, "index": self.index, "current_xy": self.current}
        if state is TiltState.ERROR and self.error is not None:
            code, source = error_code_source(self.error, UNKNOWN_ERROR)
            data.update(code=code, error=source, failed_state=self.failed_state)
        self.ctx.set_state(str(state), **data)

    def event(self, name: str, **data: Any) -> None:
        """A run-panel event (``alox_manager_functions_ui_events`` ``TILT …``) as state data."""
        self.ctx.set_state(str(self.state), event=name, status=self.code, **data)

    def fail(self, exc: BaseException, state: TiltState, log_source: str | None = None,
             status: int | None = None) -> TiltState:
        if status is not None:
            self.set_status(status)
        self.error, self.failed_state = exc, str(state)
        if log_source:
            app_log(self.s, "error", f"{log_source}: {exc}")
        return TiltState.ERROR

    def set_status(self, code: int) -> None:
        self.code = int(code)
        self.s.set_status("tilt", self.code)

    def pause(self, seconds: float) -> None:
        """A pause that a stop interrupts (raises :class:`Stopped`)."""
        if seconds <= 0:
            return
        if self.s.sleep is time.sleep:
            self.ctx.sleep(seconds)
        else:
            self.ctx.check()
            self.s.sleep(seconds)

    def setting(self, key: str, default: Any) -> Any:
        return setting(self.s, key, default)

    def overlay(self, shapes: Sequence[Shape]) -> None:
        try:
            self.s.images.set_overlay(OVERLAY, list(shapes))
        except Exception as exc:                                  # noqa: BLE001 - display only
            log.debug("tilt overlay not set: %s", exc)

    def image(self) -> np.ndarray:
        """Get Image Run Once with the tilt camera."""
        result = self.ctx.run_once(FunctionName.GET_IMAGE, self.p.camera_type)
        pixels = getattr(result, "pixels", result)
        if pixels is None:
            raise TiltError(INVALID_ARGUMENTS, "tilt: Get Image returned no image")
        return np.asarray(pixels)

    def display_image(self, what: str) -> None:
        """"Refresh image display" / "Get focused image" (errors cleared)."""
        try:
            self.image()
        except Stopped:
            raise
        except Exception as exc:                                  # noqa: BLE001 - cleared, as LabVIEW
            app_log(self.s, "warning", f"Tilt ({what} image): {exc}")

    def read_position(self) -> Point | None:
        try:
            x, y = self.xy.position()
        except (DeviceError, AloxError) as exc:
            log.debug("tilt: Get Position XY failed: %s", exc)
            return None
        return float(x), float(y)

    def origin(self) -> Point:
        return (number(self.setting("hardware.xy.waferxorg", 0.0), "hardware.xy.waferxorg"),
                number(self.setting("hardware.xy.waferyorg", 0.0), "hardware.xy.waferyorg"))

    # ---------------------------------------------------------------------------------------------- before the loop
    def prepare(self) -> None:
        """sm d1…d3: Z joystick off, XY joystick off; errors logged."""
        for name, label in (("z", "Z"), ("xy", "XY")):
            device = self.s.devices.get(name)
            if device is None:
                continue
            try:
                device.set_joystick(False)
            except (DeviceError, AloxError, AttributeError) as exc:
                app_log(self.s, "warning", f"Tilt ({label} Disable Joystick): {exc}")

    # ---------------------------------------------------------------------------------------------- Start, Started
    def start(self) -> TiltState:
        """"Init state machine here" (sm d7…d36)."""
        self.set_status(TiltStatus.INITIALIZING)                   # 301 "Tilt initializing"
        if self.p.method is TiltMethod.DUAL_OBJECTS:               # "Init Session Images"
            d = self.p.dual_objects
            for obj, model, mask in ((1, d.obj1_model_path, d.obj1_mask_path), (2, d.obj2_model_path,
                                                                                   d.obj2_mask_path)):
                self.templates[obj] = self.load_template(obj, model, mask)
        try:                                                       # "Set XY stages speed"
            if self.xy.joystick_enabled():
                self.xy.set_joystick(False)
            speed = self.setting("hardware.xy.speed", None)
            if speed is not None:                                  # LabVIEW's default 0 would be VEL 0: Fixed
                self.xy.set_speed(number(speed, "hardware.xy.speed"))
        except (DeviceError, AloxError, AttributeError) as exc:
            app_log(self.s, "error", f"Measure Tilt (Set XY Speed): {exc}")
        if self.s.folders is not None:                             # job-history folders, errors cleared
            try:
                self.folders = jobfiles.job_folders(self.s.folders, self.job, images="tilt", files=("tilt",))
            except OSError as exc:
                log.warning("tilt: job folders: %s", exc)
        return TiltState.STARTED

    def load_template(self, obj: int, model: str, mask: str) -> Template | None:
        """The model (and its learn mask) of an object; a missing or unreadable file leaves no model (logged)."""
        if not model:
            app_log(self.s, "warning", f"Tilt (Init): no model for object {obj}")
            return None
        mask_path = self.f.model_path(mask) if mask else None
        if mask_path is not None and not mask_path.is_file():
            app_log(self.s, "warning", f"Tilt (Init): learn mask {mask_path} not found, not used")
            mask_path = None
        try:
            return matching.load_template(self.f.matcher, self.f.model_path(model), mask_path,
                                          curve=self.p.dual_objects.curve())
        except (TemplateError, OSError) as exc:
            app_log(self.s, "warning", f"Tilt (Init): object {obj}: {exc}")
            return None

    def started(self) -> TiltState:
        """"Post-start initialization": the snail positions (sm d41)."""
        self.set_status(TiltStatus.INITIALIZED)                    # 302 "Tilt inited"
        start = wafer_to_stage(self.p.start_pos, self.origin(), int(stage_coordinate_system(self.s)))
        self.positions = snail_positions(start, self.p.step_mm, self.p.max_moves)
        self.index = 0
        self.current = self.positions[0]
        self.event("TILT Started", positions=len(self.positions))
        return TiltState.MOVE_TO_POS

    # ---------------------------------------------------------------------------------------------- moves
    def move_to_pos(self) -> TiltState:
        """"Move to X/Y tilt measurement pos" (sm d44…d55)."""
        if self.index > len(self.positions) - 1:                   # ">> No more positions ? >>"
            exc = TiltError(TILT_NOT_FOUND, "tilt not found error (no more positions)")
            return self.fail(exc, TiltState.MOVE_TO_POS, "Tilt (Move XY)", TiltStatus.ERROR_TILT_NOT_FOUND)
        self.set_status(TiltStatus.MOVE)                           # 303 "Move to XY position"
        target = self.positions[self.index]
        try:
            self.xy.move_absolute(target[0], target[1], wait=False)
        except (DeviceError, AloxError) as exc:
            self.move_t = self.s.clock()
            if self.error_count >= self.o.move_retries:            # "XY Move Error: Retry 3x"
                return self.fail(exc, TiltState.MOVE_TO_POS, "Tilt (Move XY)", TiltStatus.ERROR_MOVE)
            self.error_count += 1
            log.warning("tilt: move to %s failed (%d): %s", target, self.error_count, exc)
            return TiltState.MOVE_TO_POS
        self.error_count = 0
        self.move_t = self.s.clock()
        self.event("TILT Moving", target=target, index=self.index)
        return TiltState.POSITION_OK

    def position_ok(self) -> TiltState:
        """"Wait for X/Y stages to be at tilt measurement position" (sm d56…d66)."""
        self.set_status(TiltStatus.WAIT_MOVE)                      # 304
        target = self.positions[self.index]
        pos = self.read_position()
        tol = self.o.position_tolerance_mm
        if pos is not None and all(t - tol <= v <= t + tol for v, t in zip(pos, target, strict=True)):
            self.pause(self.o.settle_s)                            # "Make sure the stages has stopped moving"
            self.index += 1
            self.current = pos
            self.display_image("Position OK?")                     # "Refresh image display"
            self.event("TILT Moving", reached=True, index=self.index)
            if self.index <= 1 and self.p.autofocus:               # ">> First Move ? >>"
                return TiltState.AUTOFOCUS_INIT
            if self.index <= 1 and self.p.refocus:
                return TiltState.REFOCUS_INIT
            return self.measure_state()
        if self.s.clock() - self.move_t > self.o.move_timeout_s:  # "XY stages timeout"
            exc = TiltError(TILT_XY_TIMEOUT, "tilt error (xy stages timeout)")
            return self.fail(exc, TiltState.POSITION_OK, "Tilt (Position OK?)", TiltStatus.ERROR_WAIT_MOVE)
        self.pause(self.o.poll_s)
        return TiltState.POSITION_OK

    def measure_state(self) -> TiltState:
        return {TiltMethod.STRAIGHT_EDGE: TiltState.MEASURE_STRAIGHT_EDGE,
                TiltMethod.DUAL_OBJECTS: TiltState.MEASURE_OBJECTS,
                TiltMethod.LONG_BASELINE_EDGE: TiltState.MEASURE_LONG_BASELINE,
                TiltMethod.LATTICE: TiltState.MEASURE_LATTICE}[self.p.method]

    # ---------------------------------------------------------------------------------------------- focus
    def autofocus(self) -> TiltState:
        """"Autofocus Large (synchronous)" (sm d68)."""
        self.set_status(TiltStatus.AUTOFOCUS)                      # 305
        result: Any = None
        try:
            result = self.ctx.run_once(FunctionName.AUTOFOCUS_LARGE, self.f.autofocus_params(self.s,
                                                                                            self.p.camera_type))
            self.image()                                           # "Get focused image"
        except Stopped:
            raise
        except Exception as exc:                                  # noqa: BLE001 - the LL function's error cluster
            self.event("TILT Autofocus", result=result)
            return self.fail(exc, TiltState.AUTOFOCUS, "Tilt (Autofocus)", TiltStatus.ERROR_AUTOFOCUS)
        self.event("TILT Autofocus", result=result)
        return TiltState.AUTOFOCUS_FINISHED

    def autofocus_finished(self) -> TiltState:
        return TiltState.REFOCUS_INIT if self.p.refocus else self.measure_state()

    def refocus(self) -> TiltState:
        """"Refocus" (sm d75)."""
        self.set_status(TiltStatus.REFOCUS)                        # 306
        result: Any = None
        try:
            self.image()                                           # "Get Image"
            result = self.ctx.run_once(FunctionName.REFOCUS, self.f.refocus_params(self.s, self.p.camera_type))
            self.image()                                           # "Get refocused image"
        except Stopped:
            raise
        except Exception as exc:                                  # noqa: BLE001 - the LL function's error cluster
            self.event("TILT Refocus", result=result)
            return self.fail(exc, TiltState.REFOCUS, "Tilt (Refocus)", TiltStatus.ERROR_REFOCUS)
        self.event("TILT Refocus", result=result)
        return TiltState.REFOCUS_FINISHED

    # ---------------------------------------------------------------------------------------------- measures
    def alarm(self, text: str, ok: bool) -> list[Shape]:
        """``alox_vision_overlay_alarm``: a box (``[overlays] alarmleft/top/width/height``, 1600/50/700/300) green when
        ``ok``, else red, with the text."""
        left = number(self.setting("overlays.alarmleft", 1600), "overlays.alarmleft")
        top = number(self.setting("overlays.alarmtop", 50), "overlays.alarmtop")
        width = number(self.setting("overlays.alarmwidth", 700), "overlays.alarmwidth")
        height = number(self.setting("overlays.alarmheight", 300), "overlays.alarmheight")
        color = GREEN if ok else RED
        return [Rect(left, top, left + width, top + height, color, 3),
                Text(left + width / 2.0, top + height / 2.0, text, color, 40, anchor="mm")]

    def measure_straight_edge(self) -> TiltState:
        """"Measure TILT using Straight Edge method" (sm d81)."""
        self.set_status(TiltStatus.MEASURE_STRAIGHT_EDGE)          # 307
        t0 = self.s.clock()
        se = self.p.straight_edge
        try:
            image = self.image()
            rep = measure_tilt_straight_edge(image, se.edge_options(), se.search_direction, se.line_options(),
                                             fit_max_distance_px=self.o.fit_max_distance_px, clock=self.s.clock)
            self.se_report = rep
            self.angle, self.valid = rep.angle_deg, rep.valid
            self.edges_ms = rep.process_time_ms
            self.overlay(self.straight_edge_shapes(rep))
        except Stopped:
            raise
        except Exception as exc:                                  # noqa: BLE001 - logged, the tilt stays invalid
            self.angle, self.valid = math.nan, False
            app_log(self.s, "error", f"Tilt (Measure Straight Edge): {exc}")
        self.process_ms = (self.s.clock() - t0) * 1000.0
        self.event("TILT Measured", angle=self.angle, valid=self.valid)
        return TiltState.NOTIFY_CLIENT

    def straight_edge_shapes(self, rep: StraightEdgeTiltReport) -> list[Shape]:
        """``HL_TILT``: search lines (blue) and edges (orange) with ``Overlay Edges``, the found line (red) and the
        used edges (yellow) with ``Overlay Straight Edge``, the result box with ``Overlay Tilt``."""
        shapes: list[Shape] = []
        search = rep.search
        if self.p.overlay_edges:
            shapes += [Line(a[0], a[1], b[0], b[1], BLUE) for a, b in search.search_lines]
            if search.edges:
                shapes.append(Points(tuple(search.edges), ORANGE, 1, marker=3))
        if self.p.overlay_straight_edge and search.straight_edges:
            edge = search.straight_edges[0]
            shapes.append(Line(edge.point1[0], edge.point1[1], edge.point2[0], edge.point2[1], RED, 2))
            shapes.append(Points(edge.used_edges, YELLOW, 1, marker=5))
        if self.p.overlay_tilt:
            shapes += self.alarm(rep.message, rep.valid)
        return shapes

    def object_mm(self, match: Match, image: np.ndarray) -> Point:
        """"Pixels => mm Wafer": ``Get Position XY`` + ``tools_tracking_move_relative`` (the tilt camera's
        calibration and flips), then "Convert" XY Stages → Wafer."""
        current = self.read_position() or self.current
        height, width = image.shape[:2]
        stage = object_position(self.s, current, match.position, (width, height), camera=self.camera,
                                x_stage_invert=self.o.x_stage_invert, y_stage_invert=self.o.y_stage_invert)
        return stage_to_wafer(stage[0], stage[1], self.origin(), int(stage_coordinate_system(self.s)))

    def detect(self, obj: int, image: np.ndarray) -> tuple[Match, ...]:
        """Match one object (sm d84 "Detect Object#n"); stores it when found."""
        template = self.templates[obj]
        if template is None:
            return ()
        settings = self.p.dual_objects.match_settings(obj, score_scale=self.o.score_scale,
                                                      number_of_matches=self.o.number_of_matches)
        matches = tuple(self.f.matcher.match(image, template, settings))
        if matches:                                                # "Object n found ?" = #matches > 0
            self.found[obj], self.match[obj] = True, matches[0]
            self.match_mm[obj] = self.object_mm(matches[0], image)
            self.found_index[obj] = self.index
        return matches

    def measure_objects(self) -> TiltState:
        """"Measure Tilt (Objects)" (sm d84…d102)."""
        self.set_status(TiltStatus.MEASURE_DUAL_OBJECTS)           # 308
        t0 = self.s.clock()
        d = self.p.dual_objects
        try:
            image = self.image()
            pre = matching.detect_object_preprocessing(image, d.morph_open, d.morph_close)
            shapes: list[Shape] = []
            if self.p.overlay_edges:                               # "Overlay Contours"
                shapes += [Points(tuple(map(tuple, c)), GREEN, 1, marker=1)
                           for c in matching.extract_curves(pre, d.curve())]
            m1: tuple[Match, ...] = ()
            m2: tuple[Match, ...] = ()
            if not self.found[1]:                                  # else "Object #1 already found"
                m1 = self.detect(1, pre)
            if not self.found[2]:                                  # else "Object #2 already found"
                masked = mask_matched_object(pre, [m1[0].bounding_box] if m1 else [],
                                             keep_inside=self.o.mask_keep_inside)
                m2 = self.detect(2, masked)
            if self.p.overlay_match:
                shapes += match_overlay(m1, RED) + match_overlay(m2, RED)
            if self.found[1] and self.found[2]:                    # "Check angle tolerance valid"
                se = self.p.straight_edge
                assert self.match_mm[1] is not None and self.match_mm[2] is not None
                tilt = tilt_from_objects(self.match_mm[1], self.match_mm[2], expected_deg=se.angle_expected_deg,
                                         tolerance_deg=se.angle_range_deg)
                self.angle, self.valid = tilt.angle_deg, tilt.valid
                if self.p.overlay_tilt:
                    text = f"TILT ANGLE: {tilt.angle_deg:.1f}°" if tilt.valid else "TILT NOT FOUND/INVALID"
                    shapes += self.alarm(text, tilt.valid)
            elif self.p.overlay_match:
                seen = bool(m1) or bool(m2)
                shapes += self.alarm("OBJECT FOUND" if seen else "OBJECT NOT FOUND", seen)
            self.overlay(shapes)
        except Stopped:
            raise
        except Exception as exc:                                  # noqa: BLE001 - logged, the tilt stays invalid
            app_log(self.s, "error", f"Tilt (Match Objects): {exc}")
        self.match_ms = self.process_ms = (self.s.clock() - t0) * 1000.0
        self.event("TILT Measured", angle=self.angle, valid=self.valid, object1_found=self.found[1],
                   object2_found=self.found[2])
        return TiltState.NOTIFY_CLIENT

    # ---------------------------------------------------------------------------------------------- long baseline
    def pixel_map(self, image: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """The affine map pixel → wafer mm of an image taken at :attr:`current` (the calibration, flips and stage
        coordinate system of :meth:`object_mm`): ``wafer = A @ (x, y) + b``, returned as ``(A, b)``."""
        height, width = image.shape[:2]
        system = int(stage_coordinate_system(self.s))
        origin = self.origin()

        def wafer(px: Point) -> np.ndarray:
            stage = object_position(self.s, self.current, px, (width, height), camera=self.camera,
                                    x_stage_invert=self.o.x_stage_invert, y_stage_invert=self.o.y_stage_invert)
            return np.asarray(stage_to_wafer(stage[0], stage[1], origin, system), dtype=np.float64)
        b = wafer((0.0, 0.0))
        a = np.column_stack(((wafer((100.0, 0.0)) - b) / 100.0, (wafer((0.0, 100.0)) - b) / 100.0))
        return a, b

    @staticmethod
    def wafer_point(m: tuple[np.ndarray, np.ndarray], px: Sequence[float]) -> Point:
        w = m[0] @ np.asarray(px, dtype=np.float64) + m[1]
        return float(w[0]), float(w[1])

    @staticmethod
    def wafer_direction(m: tuple[np.ndarray, np.ndarray], d: Sequence[float]) -> Point:
        w = m[0] @ np.asarray(d, dtype=np.float64)
        n = math.hypot(float(w[0]), float(w[1]))
        return float(w[0]) / n, float(w[1]) / n

    @staticmethod
    def mm_per_px(m: tuple[np.ndarray, np.ndarray]) -> float:
        return math.sqrt(abs(float(np.linalg.det(m[0]))))

    def reset_baseline(self) -> None:
        self.walk, self.baseline, self.walk_shapes = None, None, []
        self.angle, self.valid, self.uncertainty, self.process_ms = math.nan, False, math.nan, 0.0

    def edge_fit(self, image: np.ndarray, horizontal: bool | None = None) -> EdgeLineFit | None:
        """:func:`alox.vision.tilt.fit_edge_line` with the Straight Edge Detection's strength and polarity (the
        profiles run top → bottom or left → right: the polarity is swapped for the Right to Left and Bottom to Top
        search directions) and the server's profile settings."""
        se = self.p.straight_edge
        polarity = se.edge_polarity
        if se.search_direction in (RakeDirection.RIGHT_TO_LEFT, RakeDirection.BOTTOM_TO_TOP):
            polarity = {EdgePolarity.RISING: EdgePolarity.FALLING,
                        EdgePolarity.FALLING: EdgePolarity.RISING}.get(polarity, polarity)
        return fit_edge_line(image, horizontal=horizontal, step_px=self.o.lb_profile_step_px,
                             width_px=self.o.lb_profile_width_px, smoothing_px=self.o.lb_smoothing_px,
                             min_strength=se.min_edge_strength, polarity=polarity,
                             max_distance_px=self.o.lb_fit_max_distance_px)

    def edge_shapes(self, image: np.ndarray, fit: EdgeLineFit) -> list[Shape]:
        """The fitted edge (red) and its points (yellow) with ``Overlay Straight Edge``; the centre point (red)."""
        if not self.p.overlay_straight_edge:
            return []
        height, width = image.shape[:2]
        line = fit.line
        if fit.horizontal:
            ends = ((0.0, float(line.y_at(0.0))), (width - 1.0, float(line.y_at(width - 1.0))))
        else:
            ends = ((float(line.x_at(0.0)), 0.0), (float(line.x_at(height - 1.0)), height - 1.0))
        return [Line(ends[0][0], ends[0][1], ends[1][0], ends[1][1], RED, 2),
                Points(tuple(map(tuple, fit.points)), YELLOW, 1, marker=5), Points((fit.centre_point,), RED, 2, marker=3)]

    def lattice_shapes(self, centres: np.ndarray, chosen: Sequence[float] | None) -> list[Shape]:
        """The mesa centres (orange) with ``Overlay Edges``, the mesa of the row (red) with ``Overlay Line``."""
        shapes: list[Shape] = []
        if self.p.overlay_edges and len(centres):
            shapes.append(Points(tuple(map(tuple, centres)), ORANGE, 1, marker=3))
        if self.p.overlay_straight_edge and chosen is not None:
            shapes.append(Points(((float(chosen[0]), float(chosen[1])),), RED, 2, marker=5))
        return shapes

    def start_walk(self, walk: _Walk, sigma: float, shapes: list[Shape]) -> TiltState:
        walk.add(walk.start, sigma, 0.0, self.current, walk.image_angle if not walk.lattice else None)
        walk.home = self.current
        walk.pending = [-walk.step, walk.step]
        self.walk, self.walk_shapes = walk, shapes
        self.overlay(shapes)
        return TiltState.BASELINE_MOVE

    def measure_long_baseline(self) -> TiltState:
        """"Measure Tilt (Long Baseline Edge)" at the search position: the edge of the start image, the point nearest
        the image centre and the direction of the walk (module docstring)."""
        self.set_status(TiltStatus.MEASURE_LONG_BASELINE_EDGE)     # 310
        self.reset_baseline()
        t0 = self.s.clock()
        lb, se = self.p.long_baseline, self.p.straight_edge
        try:
            image = self.image()
            fit = self.edge_fit(image)
            if fit is None:
                return self.walk_lost("no straight edge in the start image", t0)
            m = self.pixel_map(image)
            direction = self.wafer_direction(m, fit.line.direction)
            px_mm = self.mm_per_px(m)
            height, width = image.shape[:2]
            half = lb.length_mm / 2.0
            walk = _Walk(lattice=False, start=self.wafer_point(m, fit.centre_point), direction=direction, half=half,
                         drift_limit=0.25 * (height if fit.horizontal else width) * px_mm,
                         step=min(half, lb.first_step_mm or half), start_fit=fit, horizontal=fit.horizontal,
                         image_angle=fold_angle(math.degrees(math.atan2(direction[1], direction[0])), 180.0,
                                                se.angle_expected_deg))
        except Stopped:
            raise
        except Exception as exc:                                  # noqa: BLE001 - logged, the tilt stays invalid
            app_log(self.s, "error", f"Tilt (Measure Long Baseline Edge): {exc}")
            return self.walk_lost(f"processing error: {exc}", t0)
        self.process_ms = self.edges_ms = (self.s.clock() - t0) * 1000.0
        return self.start_walk(walk, fit.centre_sigma_px * px_mm, self.edge_shapes(image, fit))

    def measure_lattice(self) -> TiltState:
        """"Measure Tilt (Lattice)" at the search position: the mesa grid of the start image, the mesa nearest the
        image centre and the row direction of the walk (module docstring)."""
        self.set_status(TiltStatus.MEASURE_LATTICE)                # 311
        self.reset_baseline()
        t0 = self.s.clock()
        lb, se = self.p.long_baseline, self.p.straight_edge
        try:
            image = self.image()
            height, width = image.shape[:2]
            centres = detect_mesas(image)
            m = self.pixel_map(image)
            # the expected row direction (wafer frame) as displayed in the image: the hint among the equivalent rows
            row_deg = se.angle_expected_deg + lb.lattice_angle_deg
            hint = np.linalg.solve(m[0], [math.cos(math.radians(row_deg)), math.sin(math.radians(row_deg))])
            fit = fit_lattice(centres, reference=(width / 2.0, height / 2.0), min_nodes=self.o.lattice_min_mesas,
                              angle_hint_deg=math.degrees(math.atan2(-float(hint[1]), float(hint[0]))))
            if fit is None:
                self.walk_shapes = self.lattice_shapes(centres, None)
                return self.walk_lost(f"no mesa grid in the start image ({len(centres)} mesas)", t0)
            ref = fit.centres[int(np.argmin(np.hypot(fit.centres[:, 0] - width / 2.0,
                                                     fit.centres[:, 1] - height / 2.0)))]
            direction = self.wafer_direction(m, fit.row)
            px_mm = self.mm_per_px(m)
            pitch = lb.lattice_pitch_um / 1000.0 if lb.lattice_pitch_um > 0 else fit.pitch_row_px * px_mm
            across = fit.pitch_across_px * px_mm
            half = lb.length_mm / 2.0
            first = lb.first_step_mm or self.o.lattice_first_pitches * pitch
            walk = _Walk(lattice=True, start=self.wafer_point(m, ref), direction=direction, half=half,
                         drift_limit=self.o.lattice_row_tolerance * across, step=min(half, first), start_fit=fit,
                         sigma_point=fit.rms_px * px_mm, pitch_along=pitch, pitch_across=across,
                         period=fit.period_deg, angle_offset=lb.lattice_angle_deg,
                         image_angle=fold_angle(math.degrees(math.atan2(direction[1], direction[0]))
                                                - lb.lattice_angle_deg, fit.period_deg, se.angle_expected_deg))
        except Stopped:
            raise
        except Exception as exc:                                  # noqa: BLE001 - logged, the tilt stays invalid
            app_log(self.s, "error", f"Tilt (Measure Lattice): {exc}")
            return self.walk_lost(f"processing error: {exc}", t0)
        self.process_ms = self.edges_ms = (self.s.clock() - t0) * 1000.0
        return self.start_walk(walk, walk.sigma_point, self.lattice_shapes(centres, ref))

    def walk_line(self) -> tuple[Point, Point]:
        """A point of the line predicted through the points measured so far and its unit direction."""
        w = self.walk
        assert w is not None
        p = baseline_target(w.points, w.start, w.direction, 0.0)
        q = baseline_target(w.points, w.start, w.direction, 1.0)
        return p, (q[0] - p[0], q[1] - p[1])

    def baseline_move(self) -> TiltState:
        """"Move To Baseline Pos": the stage to the feature predicted at the next offset (the lattice: a whole number
        of pitches, so that a mesa is at the image centre); moves retried as ``Move To Pos``."""
        self.set_status(TiltStatus.MOVE_BASELINE)                  # 312
        w = self.walk
        assert w is not None
        offset = w.pending[0]
        if w.lattice:
            offset = math.copysign(max(1, round(abs(offset) / w.pitch_along)) * w.pitch_along, offset)
        w.offset = offset
        w.target = baseline_target(w.points, w.start, w.direction, offset)
        w.target_stage = wafer_to_stage(w.target, self.origin(), int(stage_coordinate_system(self.s)))
        w.moved = True
        try:
            self.xy.move_absolute(w.target_stage[0], w.target_stage[1], wait=False)
        except (DeviceError, AloxError) as exc:
            self.move_t = self.s.clock()
            if self.error_count >= self.o.move_retries:
                return self.fail(exc, TiltState.BASELINE_MOVE, "Tilt (Move XY)", TiltStatus.ERROR_MOVE)
            self.error_count += 1
            log.warning("tilt: move to %s failed (%d): %s", w.target_stage, self.error_count, exc)
            return TiltState.BASELINE_MOVE
        self.error_count = 0
        self.move_t = self.s.clock()
        self.event("TILT Moving", target=w.target_stage, baseline_offset=offset)
        return TiltState.BASELINE_POSITION_OK

    def baseline_position_ok(self) -> TiltState:
        """"Baseline Position OK?": the rules of ``Position OK?`` (tolerance, settling, timeout 9530)."""
        self.set_status(TiltStatus.WAIT_MOVE_BASELINE)             # 313
        w = self.walk
        assert w is not None
        pos = self.read_position()
        tol = self.o.position_tolerance_mm
        if pos is not None and all(t - tol <= v <= t + tol for v, t in zip(pos, w.target_stage, strict=True)):
            self.pause(self.o.settle_s)
            self.current = pos
            self.event("TILT Moving", reached=True, baseline_offset=w.offset)
            return TiltState.BASELINE_MEASURE
        if self.s.clock() - self.move_t > self.o.move_timeout_s:
            exc = TiltError(TILT_XY_TIMEOUT, "tilt error (xy stages timeout)")
            return self.fail(exc, TiltState.BASELINE_POSITION_OK, "Tilt (Baseline Position OK?)",
                             TiltStatus.ERROR_WAIT_MOVE)
        self.pause(self.o.poll_s)
        return TiltState.BASELINE_POSITION_OK

    def baseline_measure(self) -> TiltState:
        """"Measure Baseline Point": optional refocus, then the feature at this baseline position; next the following
        offset, a longer half-baseline (:func:`alox.vision.tilt.next_baseline_step`), or the angle."""
        w = self.walk
        assert w is not None
        self.set_status(TiltStatus.MEASURE_LATTICE if w.lattice else TiltStatus.MEASURE_LONG_BASELINE_EDGE)
        if self.p.long_baseline.refocus:
            try:
                self.image()
                self.ctx.run_once(FunctionName.REFOCUS, self.f.refocus_params(self.s, self.p.camera_type))
            except Stopped:
                raise
            except Exception as exc:                              # noqa: BLE001 - the LL function's error cluster
                return self.fail(exc, TiltState.BASELINE_MEASURE, "Tilt (Refocus)", TiltStatus.ERROR_REFOCUS)
        t0 = self.s.clock()
        try:
            image = self.image()
            lost = self.lattice_point(image) if w.lattice else self.edge_point(image)
        except Stopped:
            raise
        except Exception as exc:                                  # noqa: BLE001 - logged, the tilt stays invalid
            app_log(self.s, "error", f"Tilt (Measure Baseline Point): {exc}")
            lost = f"processing error: {exc}"
        if lost:
            return self.walk_lost(f"{lost} at {w.offset:+.3f} mm", t0)
        self.process_ms += (self.s.clock() - t0) * 1000.0
        w.pending.pop(0)
        if w.pending:
            return TiltState.BASELINE_MOVE
        if w.step >= w.half:
            return self.walk_finished()
        fit = fit_baseline(w.points, w.sigmas, extra_sigma=self.o.stage_repeatability_um / 1000.0)
        w.step = next_baseline_step(w.step, w.half, sigma_deg=fit.sigma_deg, drift_limit=w.drift_limit,
                                    growth=self.p.long_baseline.growth)
        w.pending = [-w.step, w.step]
        return TiltState.BASELINE_MOVE

    def edge_point(self, image: np.ndarray) -> str:
        """The edge at a baseline position: its point nearest the image centre; "" or why it is lost (no edge, or an
        edge whose angle differs from the start image's by more than the consistency tolerance: another edge)."""
        w = self.walk
        assert w is not None
        fit = self.edge_fit(image, w.horizontal)
        if fit is None:
            return "edge lost"
        m = self.pixel_map(image)
        d = self.wafer_direction(m, fit.line.direction)
        angle = fold_angle(math.degrees(math.atan2(d[1], d[0])), 180.0, w.image_angle)
        self.walk_shapes = self.edge_shapes(image, fit)
        self.overlay(self.walk_shapes)
        if abs(angle - w.image_angle) > self.p.long_baseline.consistency_deg:
            return f"another edge ({angle:.3f}° against {w.image_angle:.3f}°)"
        w.add(self.wafer_point(m, fit.centre_point), fit.centre_sigma_px * self.mm_per_px(m), w.offset, self.current,
              angle)
        return ""

    def lattice_point(self, image: np.ndarray) -> str:
        """The mesa of the start row at a baseline position (:func:`alox.vision.tilt.pick_row_mesa`); "" or why it is
        lost (no mesa, or none on the predicted row within the row tolerance)."""
        w = self.walk
        assert w is not None
        centres = detect_mesas(image)
        self.walk_shapes = []
        if not len(centres):
            return "no mesa"
        m = self.pixel_map(image)
        wafer = centres @ m[0].T + m[1]
        point, direction = self.walk_line()
        got = pick_row_mesa(wafer, w.target, direction, pitch_along=w.pitch_along, pitch_across=w.pitch_across,
                            line_point=point, tolerance=self.o.lattice_row_tolerance)
        self.walk_shapes = self.lattice_shapes(centres, centres[got[0]] if got else None)
        self.overlay(self.walk_shapes)
        if got is None:
            return "no mesa on the predicted row"
        w.add((float(wafer[got[0], 0]), float(wafer[got[0], 1])), w.sigma_point, w.offset, self.current)
        return ""

    def walk_finished(self) -> TiltState:
        """The angle of the line through the points, its uncertainty (stage repeatability included), the
        consistency with the images' angle and the range check (module docstring)."""
        w = self.walk
        assert w is not None
        lb, se = self.p.long_baseline, self.p.straight_edge
        period = w.period
        fit = fit_baseline(w.points, w.sigmas, extra_sigma=self.o.stage_repeatability_um / 1000.0, period=period,
                           centre=se.angle_expected_deg + w.angle_offset)
        angle = fit.angle_deg - w.angle_offset                      # lattice: the row's angle less the Grid Row Angle
        if w.lattice:
            image_angle = fold_angle(w.image_angle, period, angle)
        else:                                                      # the mean direction modulo 180°
            a = np.radians(np.asarray(w.image_angles)) * 2.0
            image_angle = fold_angle(math.degrees(math.atan2(np.sin(a).sum(), np.cos(a).sum()) / 2.0), period,
                                     angle)
        consistent = abs(image_angle - angle) <= lb.consistency_deg
        in_range = abs(angle - se.angle_expected_deg) <= se.angle_range_deg
        self.angle, self.uncertainty, self.valid = angle, fit.sigma_deg, consistent and in_range
        if self.valid:
            message = f"TILT ANGLE: {angle:.3f}° ± {fit.sigma_deg:.3f}°"
        else:
            message = "TILT INCONSISTENT" if not consistent else "TILT NOT FOUND/INVALID"
            app_log(self.s, "warning", f"Tilt (Baseline): {angle:.4f}° over {fit.length:.3f} mm, images "
                                       f"{image_angle:.4f}°: {'out of range' if consistent else 'inconsistent'}")
        self.baseline = self.baseline_report(fit, image_angle, True, consistent, in_range, message)
        self.overlay(self.walk_shapes + (self.alarm(message, self.valid) if self.p.overlay_tilt else []))
        self.event("TILT Measured", angle=self.angle, valid=self.valid, uncertainty=self.uncertainty,
                   baseline_mm=fit.length, image_angle=image_angle)
        return self.walk_end()

    def walk_lost(self, reason: str, t0: float) -> TiltState:
        """The feature was not found (start image) or lost (baseline position): no tilt at this search position."""
        self.process_ms += (self.s.clock() - t0) * 1000.0
        w = self.walk
        fit = None
        if w is not None and len(w.points) >= 2:
            fit = fit_baseline(w.points, w.sigmas, period=w.period,
                               centre=self.p.straight_edge.angle_expected_deg + w.angle_offset)
        self.angle, self.valid, self.uncertainty = math.nan, False, math.nan
        self.baseline = self.baseline_report(fit, math.nan, False, False, False, "TILT NOT FOUND/INVALID")
        app_log(self.s, "warning", f"Tilt (Baseline): {reason}")
        self.overlay(self.walk_shapes + (self.alarm("TILT NOT FOUND/INVALID", False) if self.p.overlay_tilt else []))
        self.event("TILT Measured", angle=self.angle, valid=False, lost=reason)
        return self.walk_end()

    def walk_end(self) -> TiltState:
        """The end of a walk: back to its start position when the stage left it (owner S8), else Notify Client."""
        w = self.walk
        if w is None or not w.moved:
            return TiltState.NOTIFY_CLIENT
        self.error_count = 0
        return TiltState.BASELINE_RETURN

    def baseline_return(self) -> TiltState:
        """"Return To Start" (Python only, owner S8 "Start is better"): the stage back to the position of the walk's
        start image; refused moves retried as ``Move To Pos``, then logged (the run goes on: the tilt is measured)."""
        self.set_status(TiltStatus.MOVE_BASELINE)                  # 312
        w = self.walk
        assert w is not None
        try:
            self.xy.move_absolute(w.home[0], w.home[1], wait=False)
        except (DeviceError, AloxError) as exc:
            self.move_t = self.s.clock()
            if self.error_count >= self.o.move_retries:
                app_log(self.s, "warning", f"Tilt (Return To Start): {exc}")
                return TiltState.NOTIFY_CLIENT
            self.error_count += 1
            log.warning("tilt: move back to %s failed (%d): %s", w.home, self.error_count, exc)
            return TiltState.BASELINE_RETURN
        self.error_count = 0
        self.move_t = self.s.clock()
        self.event("TILT Moving", target=w.home, returning=True)
        return TiltState.BASELINE_RETURN_OK

    def baseline_return_ok(self) -> TiltState:
        """"Start Position OK?": the tolerance of ``Position OK?``; after the move timeout a warning, and the run goes
        on."""
        self.set_status(TiltStatus.WAIT_MOVE_BASELINE)             # 313
        w = self.walk
        assert w is not None
        pos = self.read_position()
        tol = self.o.position_tolerance_mm
        if pos is not None and all(t - tol <= v <= t + tol for v, t in zip(pos, w.home, strict=True)):
            self.current = pos
            self.event("TILT Moving", reached=True, returned=True)
            return TiltState.NOTIFY_CLIENT
        if self.s.clock() - self.move_t > self.o.move_timeout_s:
            app_log(self.s, "warning", f"Tilt (Return To Start): start position {w.home} not reached, at {pos}")
            return TiltState.NOTIFY_CLIENT
        self.pause(self.o.poll_s)
        return TiltState.BASELINE_RETURN_OK

    def baseline_report(self, fit: BaselineFit | None, image_angle: float, complete: bool, consistent: bool,
                        in_range: bool, message: str) -> BaselineReport:
        w = self.walk
        return BaselineReport(
            method=self.p.method, points_mm=tuple(w.points) if w else (), offsets_mm=tuple(w.offsets) if w else (),
            stage_mm=tuple(w.stage) if w else (), fit=fit, image_angle_deg=image_angle,
            image_angles_deg=tuple(w.image_angles) if w else (), complete=complete, consistent=consistent,
            in_range=in_range, message=message, start_fit=w.start_fit if w else None)

    # ---------------------------------------------------------------------------------------------- Display & Save
    def display_save(self) -> TiltState:
        """"Tilt Measured, display and save results" (sm d104…d121)."""
        self.set_status(TiltStatus.DATA_LOGGING)                   # 309 "Measure Tilt data logging"
        if self.valid and self.s.publish is not None:              # "Valid Tilt Angle found: store it, send it"
            try:
                self.s.publish(reports.tilt_event(self.job, self.angle))
            except Exception as exc:                              # noqa: BLE001 - logged as LabVIEW
                app_log(self.s, "error", f"TILT (Websocket): {exc}")
        self.event("TILT Measured", angle=self.angle, valid=self.valid)
        if flag(self.setting("log.savetiltimages", True)):
            self.save_image()
        if flag(self.setting("log.savetiltdata", True)) and self.valid:
            self.save_data()
        if self.valid:
            return TiltState.FINISHED
        b = self.baseline
        out_of_range = b is not None and b.complete and b.consistent and not b.in_range
        if (self.found[1] and self.found[2]) or out_of_range:     # both objects / a whole baseline, out of range
            exc = TiltError(TILT_OUT_OF_RANGE, f"tilt error (tilt angle out of range): {self.angle:.3f}°")
            return self.fail(exc, TiltState.DISPLAY_SAVE, None, TiltStatus.ERROR_TILT_NOT_FOUND)
        return TiltState.MOVE_TO_POS

    def save_image(self) -> None:
        """``<job data file name (%y%m%d_%H%M%S_tilt)>.png`` in ``images\\tilt`` (sm d112)."""
        if self.folders is None or self.folders.images is None:
            return
        pixels = self.s.images.last(self.camera)
        if pixels is None:
            return
        name = jobfiles.job_data_filename(self.job, self.f._now(), f"{jobfiles.TIME_FORMAT}_tilt", kind="tilt")
        path = self.folders.images / f"{name}.png"
        try:
            write_png(path, np.asarray(pixels))
        except (OSError, ValueError, PngError) as exc:
            app_log(self.s, "error", f"TILT (Log Image): {exc}")
            return
        self.images.append(path)

    def save_data(self) -> None:
        """``<job data file name>.tilt`` in ``files\\tilt``: the angle (sm d119)."""
        folder = self.folders.file_folder("tilt") if self.folders is not None else None
        if folder is None:
            return
        name = jobfiles.job_data_filename(self.job, self.f._now(), kind="tilt") + ".tilt"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            self.data_file = jobfiles.write_spreadsheet(folder / name, [[self.angle]], self.o.data_format)
        except (OSError, ValueError) as exc:
            app_log(self.s, "error", f"TILT (Log Data): {exc}")

    # ---------------------------------------------------------------------------------------------- end states
    def finished(self) -> TiltReport:
        """"Function finished sucessfully" (sm d124…d151)."""
        self.set_status(TiltStatus.EXITING)                        # 400 "Exit Tilt"
        self.event("TILT Finished", angle=self.angle, valid=self.valid)
        both = self.p.method is not TiltMethod.DUAL_OBJECTS or (self.found[1] and self.found[2])
        if not (self.valid and both):                              # unreachable as wired: Fixed (raise)
            self.set_status(TiltStatus.ERROR_TILT_NOT_FOUND)
            self.error = TiltError(TILT_ANGLE_NOT_FOUND, "tilt error (tilt angle not found)")
            self.failed_state = str(TiltState.FINISHED)
            raise self.error_state()
        app_log(self.s, "info", f"Tilt Angle found: {self.angle:f}°")
        self.set_status(TiltStatus.FINISHED)                       # 500 "Function finished"
        return self.report()

    def error_state(self) -> TiltFailed:
        """"Function failed" (sm d152…d161); returns the exception to raise."""
        exc = self.error or TiltError(UNKNOWN_ERROR, "tilt: unknown error")
        code, source = error_code_source(exc, UNKNOWN_ERROR)
        self.event("TILT Finished", angle=self.angle, valid=self.valid)
        app_log(self.s, "error", f"Tilt (Error): {source}")
        if self.s.publish is not None:                             # "send 'error' event (websocket)"
            self.s.publish(reports.error_event(REPORT_NAME, code, source))
        return TiltFailed(code, source, state=self.failed_state, status=self.code, report=self.report())

    def stopped(self) -> None:
        """"Stopped" (sm d162…d171)."""
        self.set_status(TiltStatus.STOPPED)                        # 850
        self.publish(TiltState.STOPPED)
        self.event("TILT Finished", angle=self.angle, valid=self.valid)
        if self.s.publish is not None:
            self.s.publish(reports.function_event("failed", REPORT_NAME, self.job))
