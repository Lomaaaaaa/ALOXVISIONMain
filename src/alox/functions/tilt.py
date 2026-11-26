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
replace them, ``alox_api_v1_put_functions_measure_tilt``), ``method`` (0 Straight Edge, 1 Dual Objects),
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
    EdgeMethod,
    LineOptions,
    RakeDirection,
    StraightEdgeTiltReport,
    mask_matched_object,
    measure_tilt_straight_edge,
)
from alox.vision.wafer import tilt_from_objects
from aloxcam.errors import AloxError, FocusError
from aloxcam.imaging.png import PngError, write_png

__all__ = [
    "TILT_ANGLE_NOT_FOUND",
    "TILT_NOT_FOUND",
    "TILT_OUT_OF_RANGE",
    "TILT_XY_TIMEOUT",
    "DualObjectsDetection",
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
    NOTIFY_CLIENT = "Notify Client"
    DISPLAY_SAVE = "Display & Save"
    FINISHED = "Finished"
    ERROR = "Error"
    STOPPED = "Stopped"


class TiltMethod(IntEnum):
    """``Tilt Method``."""

    STRAIGHT_EDGE = 0
    DUAL_OBJECTS = 1

    @classmethod
    def coerce(cls, value: Any) -> TiltMethod:
        if isinstance(value, str) and not value.strip().lstrip("-").isdigit():
            key = value.strip().lower().replace(" ", "").replace("_", "")
            for member, label in ((cls.STRAIGHT_EDGE, "straightedge"), (cls.DUAL_OBJECTS, "dualobjects")):
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

    @classmethod
    def from_profile(cls, profile: Any, **overrides: Any) -> TiltParams:
        """The ``measure.tilt.*`` keys of the active profile with the bundle VI's defaults; ``overrides`` (field
        names; ``straight_edge`` / ``dual_objects`` may be mappings of their fields, merged) win."""
        def read(keys: Mapping[str, tuple[str, Any]]) -> dict[str, Any]:
            return {name: _profile_get(profile, f"measure.tilt.{key}", default) for name, (key, default) in keys.items()}
        values = read(_TOP_KEYS)
        values["start_pos"] = (_profile_get(profile, "measure.tilt.startx", 0.0),
                               _profile_get(profile, "measure.tilt.starty", 0.0))
        edge, objects = read(_EDGE_KEYS), read(_OBJECT_KEYS)
        extra = dict(overrides)
        for name, base in (("straight_edge", edge), ("dual_objects", objects)):
            given = extra.pop(name, None)
            if isinstance(given, (StraightEdgeDetection, DualObjectsDetection)):
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


# ================================================================================================== results
Point = tuple[float, float]


@dataclass(frozen=True)
class TiltReport:
    """``alox_typedef_tilt_measurement_report``: the straight-edge report, the best matches (pixels and wafer mm) and
    the index of the position where each object was found (``Current Pos Index``: 1 = the start position), ``Tilt
    Angle (°)`` (NaN until measured) and its validity, the process times (ms), the SEMI job; plus the search positions
    (XY stage mm), the files written and the tilt status at the end."""

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
            process_time_ms=self.process_ms, images=tuple(self.images), data_file=self.data_file, status=self.code)

    def execute(self) -> TiltReport:
        handlers: dict[TiltState, Callable[[], TiltState]] = {
            TiltState.START: self.start, TiltState.STARTED: self.started, TiltState.MOVE_TO_POS: self.move_to_pos,
            TiltState.POSITION_OK: self.position_ok, TiltState.AUTOFOCUS_INIT: lambda: TiltState.AUTOFOCUS,
            TiltState.AUTOFOCUS: self.autofocus, TiltState.AUTOFOCUS_FINISHED: self.autofocus_finished,
            TiltState.REFOCUS_INIT: lambda: TiltState.REFOCUS, TiltState.REFOCUS: self.refocus,
            TiltState.REFOCUS_FINISHED: self.measure_state, TiltState.MEASURE_STRAIGHT_EDGE: self.measure_straight_edge,
            TiltState.MEASURE_OBJECTS: self.measure_objects, TiltState.NOTIFY_CLIENT: lambda: TiltState.DISPLAY_SAVE,
            TiltState.DISPLAY_SAVE: self.display_save}
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
        return (TiltState.MEASURE_STRAIGHT_EDGE if self.p.method is TiltMethod.STRAIGHT_EDGE
                else TiltState.MEASURE_OBJECTS)

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
        if self.found[1] and self.found[2]:                        # both objects, angle out of range
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
        both = self.p.method is TiltMethod.STRAIGHT_EDGE or (self.found[1] and self.found[2])
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
