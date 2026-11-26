"""Search Object (LabVIEW "Detect Object"): find a learned alignment pattern by moving XY on a spiral and centre it.

Spec: ``docs/vision-core/map.md`` Part 3 §2.1 (common pattern), §3.6 (Search Object / Detect Object), Part 4 §3.2
(pre-processing), §3.4 (pattern matching), §3.8 (``tools_tracking_move_relative``); ``docs/vision-core/xy-stage.md``
§7 (manager methods), §9 (coordinate systems). Diagrams: ``alox_function_ll_detect_object_state_machine`` (cited
``sm dN``), ``alox_function_ll_detect_object_bundle_profile_parameters d`` (``bundle d``),
``alox_function_ll_detect_object_get_position d…d12`` (``get_position dN``), ``alox_vision_overlay_geometric_pattern_
matching_results d``. The matcher is :mod:`alox.vision.matching`.

States (``alox_typedef_detect_object_function_state``), one per loop iteration, ``ctx.check()`` between states and
``ctx.set_state`` on every change (as :mod:`alox.functions.focus`):

- before the loop (sm d1…d4): ``Set Running``; Z joystick off; XY joystick off (X, then Y); errors logged and cleared.
- ``Start`` (sm d7, d8): pre-compute the **snail** positions (:func:`snail_positions`) from ``Start Search Pos (mm)``
  converted Wafer → XY Stages (``alox_manager_xy_coordinate_system`` "Convert"); report index 0 and ``Current XY Pos``
  = the first position; read the model PNG ``<settings>\\profile\\<Search Model Path>`` (``tools_windows_user_
  documents_folder`` + ``profile``; the constant ``alox\\profile`` is unwired) with its vision info.
- ``Started`` (sm d16, d19, d20): frame 0 "Query camera, xy stages, z stage and monochromator status": XY X and Y,
  Z, Low Mag camera, monochromator serial; any failure → 9046 "detect object error (hardware)". Frame 1: XY joystick
  off when on (X, Y), ``Set Speed`` X and Y = ``hardware.xy.speed``; errors logged ``Detect Object (Set Speed)``. Frame
  2 (WL reset) is disabled ("JCB 14/01/2020 NO MORE WL RESET"). Event ``DO Started``. → ``Move To Pos``.
- ``Move To Pos`` (sm d32…d40): no position left (``index > size − 1``) → 9047 "detect object error (no object
  found)", log ``Detect Object (Move XY Position)``; else ``Move Absolute XY`` (not waited), event ``DO Moving``. A
  move error is retried: ``Error Count ≥ 3`` → ``Error`` (4 attempts), else the count grows and the state repeats;
  success resets the count; the move time is kept for the timeout.
- ``Position OK?`` (sm d41…d47): ``Get Position XY`` (errors cleared) within ``±0.001 mm`` of the position (both
  axes, limits included) → wait 300 ms "Make sure the stages has stopped moving", ``Current Pos Index + 1``; at the
  first position only ("Autofocus/Refocus sur 1ere position uniquement") → ``Autofocus Init`` (``Autofocus``) or
  ``Refocus Init`` (``Refocus``), else ``Detect Object``. Not reached for more than 30 s after the move → 9099 "xy
  stages error", log ``Detect Object (Wait Position)``. ``Current XY Pos`` = the position read.
- ``Autofocus`` (sm d49…d53): the LL autofocus settings with ``Camera Type = Low Mag.``, ``Run Once``; ``Get Image``
  ``Run Once`` (Low Mag., the focused image); report ``Autofocus Z Pos/Score/Data/Graph``; an error or an invalid
  result → ``Error``, log ``Detect Object (Autofocus)``; else ``Autofocus Finished`` → ``Refocus Init`` (``Refocus``)
  or ``Detect Object``. With ``Show Window``: event ``DO Autofocus`` and a 1 s pause.
- ``Refocus`` (sm d55…d65): the LL refocus settings (``Low Mag.``), ``Run Once``, ``Get Image`` ``Run Once``; error or
  invalid: ``Refocus Err ≥ 4`` → ``Error`` (5 attempts; the comment says "max 3 tries"), else count + 1 and again;
  success → count 0, ``Refocus Finished`` → ``Detect Object``. ``Show Window``: event ``DO Refocus``, 1 s pause.
- ``Detect Object`` (sm d66): ``Get Image`` ``Run Once`` (Low Mag.); pre-processing (Morph Open, Morph Close); the
  contours overlay (``Overlay Contours``: curves with the edge threshold and min length, green); ``IMAQ Setup/Match
  Geometric Pattern 2``: 50 matches, minimum score ``Min Score (%) × 10``, rotation [0, 360]°, occlusion [0, Max
  Occlusion (%)], subpixel F (:mod:`alox.vision.matching`); ``Found Object ? = (#matches > 0) AND (score[0] ≥ Min
  Score (%))`` (the second test compares a 0–1000 score with a percentage: always true for a returned match);
  overlay ``LL_SEARCH_OBJECT`` (red, ``Overlay Match``); ``Found Pos (mm)`` by ``alox_function_ll_detect_object_get_
  position`` = ``Current XY Pos +`` :func:`alox.vision.wafer.tracking_move` of the best match (Low Mag calibration,
  ``hardware.camera.lowmag.hflip/vflip``, stage coordinate system; the same cases as ``tools_tracking_move_relative``,
  ``get_position d2…d12``); event ``DO Searching``. Found → ``Move To Object``, else ``Move To Pos``.
- ``Move To Object`` (sm d73): ``Move Absolute XY`` to ``Found Pos + (Offset X, Offset Y)``, retried as ``Move To
  Pos`` (log ``Detect Object (Move To Object)``). ``Object Pos OK ?`` (sm d76): ``±0.001 mm`` → wait 100 ms →
  ``Finished``; 30 s → 9099, log ``Detect Object (Object Pos ?)``.
- ``Finished`` / ``Error``: event ``DO Finished``.

Parameters (``bundle d``, profile ``search.object.*``): ``startx``/``starty`` (0), ``searchmodel``, ``learnmask``,
``edgethreshold`` (25), ``minlength`` (5), ``minscore`` (70), ``maxocclusionpc`` (50), ``morphopen``/``morphclose``
(0), ``maxmoves`` (15), ``step`` (0.1), ``autofocus``/``refocus``/``showwindow``/``overlaymatch``/
``overlaycontours`` (T), ``offsetX``/``offsetY`` (0).

Reproduced: the states and transitions, the snail, the retry counts (moves 4 attempts, refocus 5), the tolerances,
waits and timeouts, the first-position-only focus, the match settings, the found test, the pixel → stage conversion,
the overlays and the log texts, the error codes 9046, 9047, 9099, 9080.

Fixed / changed:

- The model's NI geometric template (vision info of the PNG) cannot be read without NI Vision: the template is
  learned again from the PNG pixels, with the learn mask when ``Learn Mask Path`` names a file (LabVIEW used the mask
  only in the settings panel, when learning). The matcher is not NI's (:mod:`alox.vision.matching`).
- ``hardware.xy.speed`` missing: LabVIEW sent ``VEL 0`` (its default); the XY device's configured speed is used.
- A failed ``Get Position XY`` (LabVIEW: (0, 0)) keeps the previous ``Current XY Pos``.
- ``Found Pos`` is None when nothing is found (LabVIEW converted the pixel (0, 0) of an empty match).
- The hardware check uses the device states (READY) instead of ``ERR?``/status commands. Owner's decision R7: the
  monochromator is not checked any more (a Low Mag function does not need it; LabVIEW refused to start without it).
- Positions are float64 (LabVIEW SGL). The system status 204/500 ("LL running"/"Idle") is the core's (as Get Image).
- The run panel (``_ui_run``) is not ported: its events are ``ctx.set_state`` data (``event``: ``DO Started``, ``DO
  Moving``, ``DO Autofocus``, ``DO Refocus``, ``DO Searching``, ``DO Finished``); the 1 s display pauses are kept
  (``SearchObjectOptions.display_pause_s``).
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import numpy as np

from alox.core.functions import (
    FunctionContext,
    FunctionName,
    MeasurementFunction,
    Stopped,
)
from alox.devices.base import DeviceError, DeviceState
from alox.functions.focus import AutofocusParams, CameraType, RefocusParams
from alox.functions.services import Points, Services, Shape, Text
from alox.functions.tracking import (
    INVALID_ARGUMENTS,
    FunctionFailed,
    app_log,
    calibration,
    error_code_source,
    flag,
    flips,
    number,
    setting,
    stage_coordinate_system,
)
from alox.vision import matching
from alox.vision.matching import (
    CurveOptions,
    Match,
    Matcher,
    MatchSettings,
    Template,
    TemplateError,
)
from alox.vision.wafer import tracking_move
from aloxcam.errors import AloxError

__all__ = [
    "DETECT_OBJECT_HARDWARE",
    "DETECT_OBJECT_NOT_FOUND",
    "DETECT_OBJECT_TIMEOUT",
    "XY_ERROR",
    "SOState",
    "SearchObject",
    "SearchObjectOptions",
    "SearchObjectParams",
    "SearchObjectReport",
    "default_autofocus_params",
    "default_refocus_params",
    "match_overlay",
    "object_position",
    "snail_positions",
    "wafer_to_stage",
]

log = logging.getLogger(__name__)

#: ``alox_en.lang`` codes.
DETECT_OBJECT_TIMEOUT = 9045               # "detect object error (timeout)": not raised by the diagrams read
DETECT_OBJECT_HARDWARE = 9046              # "detect object error (hardware)"
DETECT_OBJECT_NOT_FOUND = 9047             # "detect object error (no object found)"
XY_ERROR = 9099                            # "xy stages error"
FILE_NOT_FOUND = 7                         # LabVIEW "File not found" (IMAQ Read File)

OVERLAY = "detectobject"
MATCH_COLOR = (255, 0, 0)                  # "Overlay/Display": red
CONTOUR_COLOR = (0, 255, 0)


class SOState(StrEnum):
    """``alox_typedef_detect_object_function_state``."""

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
    DETECT_OBJECT = "Detect Object"
    MOVE_TO_OBJECT = "Move To Object"
    OBJECT_POS_OK = "Object Pos OK ?"
    FINISHED = "Finished"
    ERROR = "Error"
    STOPPED = "Stopped"


# ================================================================================================== parameters
def _set(obj: object, name: str, value: Any) -> None:
    object.__setattr__(obj, name, value)


#: field → (profile key, default) of ``alox_function_ll_detect_object_bundle_profile_parameters d``.
_PROFILE_KEYS: dict[str, tuple[str, Any]] = {
    "search_model_path": ("searchmodel", ""),
    "learn_mask_path": ("learnmask", ""),
    "edge_threshold": ("edgethreshold", 25),
    "min_length": ("minlength", 5),
    "min_score": ("minscore", 70.0),
    "max_occlusion": ("maxocclusionpc", 50.0),
    "morph_open": ("morphopen", 0),
    "morph_close": ("morphclose", 0),
    "max_moves": ("maxmoves", 15),
    "step_mm": ("step", 0.1),
    "autofocus": ("autofocus", True),
    "refocus": ("refocus", True),
    "show_window": ("showwindow", True),
    "overlay_match": ("overlaymatch", True),
    "overlay_contours": ("overlaycontours", True),
    "offset_y": ("offsetY", 0.0),
    "offset_x": ("offsetX", 0.0),
}


def _profile_get(profile: Any, key: str, default: Any) -> Any:
    try:
        value = profile.get(key, default)
    except (RuntimeError, LookupError, TypeError, ValueError):          # no profile loaded
        return default
    return default if value is None else value


@dataclass(frozen=True)
class SearchObjectParams:
    """``Detect Object Params`` (``alox_typedef_detect_object_function_parameters``) in cluster order; defaults =
    the profile defaults of the bundle VI. ``start_pos`` in wafer mm; ``min_score`` and ``max_occlusion`` in %;
    ``step_mm`` and the offsets in mm (stage axes)."""

    start_pos: tuple[float, float] = (0.0, 0.0)
    search_model_path: str = ""
    learn_mask_path: str = ""
    edge_threshold: int = 25
    min_length: int = 5
    min_score: float = 70.0
    max_occlusion: float = 50.0
    morph_open: int = 0
    morph_close: int = 0
    max_moves: int = 15
    step_mm: float = 0.1
    autofocus: bool = True
    refocus: bool = True
    show_window: bool = True
    overlay_match: bool = True
    overlay_contours: bool = True
    offset_y: float = 0.0
    offset_x: float = 0.0

    def __post_init__(self) -> None:
        start = tuple(self.start_pos)
        if len(start) != 2:
            raise FunctionFailed(INVALID_ARGUMENTS, f"Start Search Pos (mm): (x, y) expected, got {self.start_pos!r}")
        _set(self, "start_pos", (number(start[0], "Start Search Pos X"), number(start[1], "Start Search Pos Y")))
        for name in ("search_model_path", "learn_mask_path"):
            _set(self, name, "" if getattr(self, name) is None else str(getattr(self, name)))
        for name in ("edge_threshold", "min_length", "morph_open", "morph_close", "max_moves"):
            _set(self, name, number(getattr(self, name), name, integer=True))
        for name in ("min_score", "max_occlusion", "step_mm", "offset_y", "offset_x"):
            _set(self, name, number(getattr(self, name), name))
        for name in ("autofocus", "refocus", "show_window", "overlay_match", "overlay_contours"):
            _set(self, name, flag(getattr(self, name)))

    @classmethod
    def from_profile(cls, profile: Any, **overrides: Any) -> SearchObjectParams:
        """The ``search.object.*`` keys of the active profile, LabVIEW's defaults for the missing ones; ``overrides``
        win."""
        values: dict[str, Any] = {name: _profile_get(profile, f"search.object.{key}", default)
                                  for name, (key, default) in _PROFILE_KEYS.items()}
        values["start_pos"] = (_profile_get(profile, "search.object.startx", 0.0),
                               _profile_get(profile, "search.object.starty", 0.0))
        values.update(overrides)
        return cls(**values)

    def match_settings(self, number_of_matches: int = 50) -> MatchSettings:
        """``IMAQ Setup/Match Geometric Pattern 2`` as wired in sm d66."""
        curve = CurveOptions(extraction_mode="Normal", edge_threshold=self.edge_threshold, edge_filter_size="Normal",
                             min_length=self.min_length, row_step=5, column_step=5, max_endpoint_gap=5, closed=False,
                             subpixel=False)
        return MatchSettings(number_of_matches=number_of_matches, min_score=self.min_score * 10.0,
                             angle_ranges=((0.0, 360.0),), occlusion_range=(0.0, self.max_occlusion), subpixel=False,
                             curve=curve)


@dataclass(frozen=True)
class SearchObjectOptions:
    """Server-side settings (defaults: the LabVIEW values)."""

    position_tolerance_mm: float = 0.001     # Position OK? / Object Pos OK ?: In Range ±0.001
    move_timeout_s: float = 30.0             # "XY Move timeout" 30000 ms → 9099
    settle_s: float = 0.300                  # Position OK?: "Make sure the stages has stopped moving"
    object_settle_s: float = 0.100           # Object Pos OK ?
    display_pause_s: float = 1.0             # Autofocus / Refocus with Show Window: "Wait 1s"
    poll_s: float = 0.010                    # between two position reads (LabVIEW: the 5 ms loop wait)
    move_retries: int = 3                    # Error Count ≥ 3 → Error
    refocus_retries: int = 4                 # Refocus Err ≥ 4 → Error
    number_of_matches: int = 50
    required_devices: tuple[str, ...] = ("xy", "z", "lowmag")    # Started frame 0, without the monochromator (owner R7)
    x_stage_invert: bool = False             # debug globals of get_position (OFF for AET)
    y_stage_invert: bool = False
    camera: str = "lowmag"


@dataclass(frozen=True)
class SearchObjectReport:
    """``Detect Object Report`` (``alox_typedef_detect_object_function_results``): positions in XY stage mm, matches
    in pixels of the Low Mag image; ``autofocus_data`` = the autofocus table ``(Z, score)``; plus ``target`` (the
    position moved to: Found Pos + offsets) and ``refocus_errors`` (``Refocus Err``)."""

    search_positions: tuple[tuple[float, float], ...] = ()
    current_xy: tuple[float, float] = (0.0, 0.0)
    current_index: int = 0
    autofocus_z: float | None = None
    autofocus_score: float | None = None
    found_pos: tuple[float, float] | None = None
    autofocus_data: tuple[tuple[float, float], ...] = ()
    found: bool = False
    best_match: Match | None = None
    all_matches: tuple[Match, ...] = ()
    target: tuple[float, float] | None = None
    refocus_errors: int = 0


# ================================================================================================== pure helpers
def snail_positions(start: Sequence[float], step: float, max_moves: int) -> list[tuple[float, float]]:
    """"Pre-compute Snail XY positions (XY stages coordinate system)" (sm d7). Positions = ``[start]``; then legs
    ``i = 0, 1, 2, …`` (a While loop: at least one leg): leg ``i`` moves along X when ``i`` is even, else Y, by
    ``+step`` when ``(i // 2)`` is odd, else ``−step``, ``i // 2 + 1`` times, appending each new position; the loop
    stops after the first leg that leaves ``len(positions) ≥ max_moves`` (the last leg is completed, so there can be
    more than ``max_moves`` positions). From the start: −X, −Y, +X, +X, +Y, +Y, −X ×3, −Y ×3, …"""
    x, y = float(start[0]), float(start[1])
    positions = [(x, y)]
    i = 0
    while True:
        quotient = i // 2
        along_x = i % 2 == 0
        delta = step if quotient % 2 != 0 else -step
        for _ in range(quotient + 1):
            if along_x:
                x += delta
            else:
                y += delta
            positions.append((x, y))
        i += 1
        if len(positions) >= max_moves:
            return positions


def wafer_to_stage(point: Sequence[float], origin: Sequence[float], system: int) -> tuple[float, float]:
    """``alox_manager_xy_coordinate_system`` "Convert" Wafer → XY Stages (``xy-stage.md`` §9.2): per axis, direct
    ``p = w + O``, indirect ``p = O − w``; X indirect for systems 1 and 3 (#2, #4), Y indirect for 2 and 3 (#3, #4)."""
    wx, wy = float(point[0]), float(point[1])
    ox, oy = float(origin[0]), float(origin[1])
    px = ox - wx if int(system) in (1, 3) else wx + ox
    py = oy - wy if int(system) in (2, 3) else wy + oy
    return px, py


def object_position(services: Services, current: Sequence[float], match_px: Sequence[float],
                    image_size: Sequence[int], *, camera: str = "lowmag", x_stage_invert: bool = False,
                    y_stage_invert: bool = False) -> tuple[float, float]:
    """``alox_function_ll_detect_object_get_position`` (``get_position d…d12``): the stage position (mm) that brings
    the object seen at ``match_px`` to the image centre = ``current`` + the move of ``tools_tracking_move_relative``
    (same steps and cases: pixel offset to the centre, calibration µm/mm (else 9080), inverse move, flips of
    ``camera``, stage coordinate system)."""
    size, unit = calibration(services, camera)
    hflip, vflip = flips(services, camera)
    move = tracking_move(match_px, image_size, pixel_size=size, pixel_unit=unit, min_distance_mm=0.0,
                         coordinate_system=stage_coordinate_system(services), flip_horizontal=hflip,
                         flip_vertical=vflip, x_stage_invert=x_stage_invert, y_stage_invert=y_stage_invert)
    return float(current[0]) + move.move_mm[0], float(current[1]) + move.move_mm[1]


def default_autofocus_params(services: Services, camera: CameraType = CameraType.LOW_MAG) -> AutofocusParams:
    """"Get Autofocus settings" (sm d49): ``alox_function_ll_autofocus_bundle_profile_parameters d`` (``autofocus.
    method`` 0, ``speed`` 0, ``moveduration`` 10 s, ``step`` 0, ``subsampling`` 0.25) with ``Camera Type`` replaced,
    and the scan range ``hardware.z.zminautofocus`` / ``zmaxautofocus`` (33.0 / 34.5 mm) that the LL autofocus gives
    the camera-server procedure (map Part 3 §3.2)."""
    profile = services.profile
    return AutofocusParams(
        camera_type=camera,
        speed=number(_profile_get(profile, "autofocus.speed", 0), "autofocus.speed", integer=True),
        method=number(_profile_get(profile, "autofocus.method", 0), "autofocus.method", integer=True),
        duration_s=number(_profile_get(profile, "autofocus.moveduration", 10), "autofocus.moveduration", integer=True),
        z_min=number(setting(services, "hardware.z.zminautofocus", 33.0), "hardware.z.zminautofocus"),
        z_max=number(setting(services, "hardware.z.zmaxautofocus", 34.5), "hardware.z.zmaxautofocus"),
        step=number(_profile_get(profile, "autofocus.step", 0.0), "autofocus.step"),
        subsampling=number(_profile_get(profile, "autofocus.subsampling", 0.25), "autofocus.subsampling"))


def default_refocus_params(services: Services, camera: CameraType = CameraType.LOW_MAG) -> RefocusParams:
    """"Get Refocus settings" (sm d56): ``alox_function_ll_refocus_bundle_profile_parameters d`` (``refocus.method``
    0, ``nbsteps`` 4, ``step`` 0.05 mm, ``roiwidth``/``roiheight`` 128, ``waitms`` 0) with ``Camera Type`` replaced
    (``refocus.offset`` has no field in the camera-server procedure)."""
    profile = services.profile
    return RefocusParams(
        camera_type=camera,
        method=number(_profile_get(profile, "refocus.method", 0), "refocus.method", integer=True),
        steps=number(_profile_get(profile, "refocus.nbsteps", 4), "refocus.nbsteps", integer=True),
        step_mm=number(_profile_get(profile, "refocus.step", 0.05), "refocus.step"),
        fin_roi_width=number(_profile_get(profile, "refocus.roiwidth", 128), "refocus.roiwidth", integer=True),
        fin_roi_height=number(_profile_get(profile, "refocus.roiheight", 128), "refocus.roiheight", integer=True),
        wait_ms=number(_profile_get(profile, "refocus.waitms", 0), "refocus.waitms", integer=True))


def match_overlay(matches: Sequence[Match], color: tuple[int, int, int] = MATCH_COLOR) -> list[Shape]:
    """``alox_vision_overlay_geometric_pattern_matching_results d``: for each match its bounding box and a landmark
    on its centre; for the first one the text ``POS.X: %d, Y:%d`` / ``ANGLE: %.1f (°)`` / ``SCALE: %.1f (%)`` /
    ``SCORE: %.1f (%)`` (score / 10) / ``OCCLUSION: %.1f (%)`` at its first corner + (100, 100)."""
    shapes: list[Shape] = []
    for i, m in enumerate(matches):
        shapes.append(Points(tuple(m.bounding_box), color, 1, closed=True))
        shapes.append(Points((m.position,), color, 1, marker=10))
        if i == 0:
            x0, y0 = m.bounding_box[0][0] + 100.0, m.bounding_box[0][1] + 100.0
            lines = (f"POS.X: {round(m.position[0]):d}, Y:{round(m.position[1]):d}", f"ANGLE: {m.angle:.1f} (°)",
                     f"SCALE: {m.scale:.1f} (%)", f"SCORE: {m.score / 10.0:.1f} (%)",
                     f"OCCLUSION: {m.occlusion:.1f} (%)")
            shapes.extend(Text(x0, y0 + k * 16.0, line, color) for k, line in enumerate(lines))
    return shapes


# ================================================================================================== the function
class SearchObject(MeasurementFunction):
    """Search Object (``alox_function_ll_detect_object``, condition name ``Search Object``): ``run(params, ctx)``
    returns a :class:`SearchObjectReport`; raises :class:`~alox.functions.tracking.FunctionFailed` after ``Error``
    (9046, 9047, 9099, 9080, a device's or a called function's code) and :class:`alox.core.functions.Stopped` on
    stop/abort. ``params`` None: :meth:`SearchObjectParams.from_profile`.

    ``matcher``: a :class:`alox.vision.matching.Matcher` (default :func:`alox.vision.matching.create_matcher`, built
    at the first run). ``autofocus_params`` / ``refocus_params``: ``f(services)`` → the params of the ``Run Once``
    calls (defaults :func:`default_autofocus_params`, :func:`default_refocus_params`). The images come from
    ``ctx.run_once(GET_IMAGE, Low Mag.)``; the focus steps from ``ctx.run_once(AUTOFOCUS_LARGE / REFOCUS)``."""

    name = FunctionName.SEARCH_OBJECT

    def __init__(self, services: Services, matcher: Matcher | None = None, *,
                 options: SearchObjectOptions | None = None,
                 autofocus_params: Callable[[Services], Any] | None = None,
                 refocus_params: Callable[[Services], Any] | None = None) -> None:
        services.device("xy")
        self.services = services
        self._matcher = matcher
        self.options = options or SearchObjectOptions()
        self.autofocus_params = autofocus_params or default_autofocus_params
        self.refocus_params = refocus_params or default_refocus_params
        self._last: SearchObjectReport | None = None

    @property
    def matcher(self) -> Matcher:
        if self._matcher is None:
            self._matcher = matching.create_matcher()
            log.info("search object: %s matcher", self._matcher.name)
        return self._matcher

    @property
    def last_report(self) -> SearchObjectReport | None:
        """``Get Results``."""
        return self._last

    def model_path(self, name: str) -> Path:
        """``<settings>\\profile\\<name>`` (sm d7); ``name`` as is without folders (tests) or when absolute."""
        path = Path(name)
        folders = self.services.folders
        if folders is None or path.is_absolute():
            return path
        return Path(folders.profile) / path

    def run(self, params: SearchObjectParams | None, ctx: FunctionContext) -> SearchObjectReport:
        if params is None:
            params = SearchObjectParams.from_profile(self.services.profile)
        if not isinstance(params, SearchObjectParams):
            raise FunctionFailed(INVALID_ARGUMENTS, f"search object: SearchObjectParams expected, got "
                                                    f"{type(params).__name__}")
        job = _SearchJob(self, params, ctx)
        try:
            report = job.execute()
        except BaseException:
            self._last = job.report()
            raise
        self._last = report
        return report


class _SearchJob:
    """One run (the per-run "shift registers": session and report)."""

    def __init__(self, function: SearchObject, p: SearchObjectParams, ctx: FunctionContext) -> None:
        self.f = function
        self.s = function.services
        self.o = function.options
        self.p = p
        self.ctx = ctx
        self.xy = self.s.device("xy")
        self.state = SOState.START
        self.positions: list[tuple[float, float]] = []
        self.index = 0
        self.current: tuple[float, float] = (0.0, 0.0)
        self.error_count = 0
        self.refocus_errors = 0
        self.move_t = 0.0
        self.template: Template | None = None
        self.af_z: float | None = None
        self.af_score: float | None = None
        self.af_data: tuple[tuple[float, float], ...] = ()
        self.found = False
        self.found_pos: tuple[float, float] | None = None
        self.matches: tuple[Match, ...] = ()
        self.target: tuple[float, float] | None = None
        self.error: BaseException | None = None
        self.failed_state = ""

    # ---------------------------------------------------------------------------------------------- loop
    def report(self) -> SearchObjectReport:
        return SearchObjectReport(
            search_positions=tuple(self.positions), current_xy=self.current, current_index=self.index,
            autofocus_z=self.af_z, autofocus_score=self.af_score, found_pos=self.found_pos,
            autofocus_data=self.af_data, found=self.found, best_match=self.matches[0] if self.matches else None,
            all_matches=self.matches, target=self.target, refocus_errors=self.refocus_errors)

    def execute(self) -> SearchObjectReport:
        handlers: Mapping[SOState, Callable[[], SOState]] = {
            SOState.START: self.start, SOState.STARTED: self.started, SOState.MOVE_TO_POS: self.move_to_pos,
            SOState.POSITION_OK: self.position_ok, SOState.AUTOFOCUS_INIT: lambda: SOState.AUTOFOCUS,
            SOState.AUTOFOCUS: self.autofocus, SOState.AUTOFOCUS_FINISHED: self.autofocus_finished,
            SOState.REFOCUS_INIT: self.refocus_init, SOState.REFOCUS: self.refocus,
            SOState.REFOCUS_FINISHED: lambda: SOState.DETECT_OBJECT, SOState.DETECT_OBJECT: self.detect_object,
            SOState.MOVE_TO_OBJECT: self.move_to_object, SOState.OBJECT_POS_OK: self.object_pos_ok}
        self._init_hardware()
        self._publish(SOState.START)
        try:
            while self.state not in (SOState.FINISHED, SOState.ERROR):
                self.ctx.check()
                nxt = handlers[self.state]()
                if nxt is not self.state:
                    self._publish(nxt)
                self.state = nxt
        except Stopped:
            self.ctx.set_state(str(SOState.STOPPED), **self._data())
            raise
        self.event("DO Finished")
        if self.state is SOState.ERROR:
            code, source = error_code_source(self.error, XY_ERROR) if self.error else (XY_ERROR, "unknown error")
            raise FunctionFailed(code, source, state=self.failed_state, report=self.report()) from self.error
        return self.report()

    def _data(self) -> dict[str, Any]:
        return {"index": self.index, "current_xy": self.current, "found": self.found}

    def _publish(self, state: SOState) -> None:
        data = self._data()
        if state is SOState.ERROR and self.error is not None:
            code, source = error_code_source(self.error, XY_ERROR)
            data.update(code=code, error=source, failed_state=self.failed_state)
        self.ctx.set_state(str(state), **data)

    def event(self, name: str, **data: Any) -> None:
        """A run-panel event (``alox_manager_functions_ui_events``): published with the current state."""
        self.ctx.set_state(str(self.state), event=name, **self._data(), **data)

    def fail(self, exc: BaseException, state: SOState, log_source: str | None = None) -> SOState:
        self.error, self.failed_state = exc, str(state)
        if log_source:
            app_log(self.s, "error", f"{log_source}: {exc}")
        return SOState.ERROR

    def pause(self, seconds: float) -> None:
        if seconds <= 0:
            return
        if self.s.sleep is time.sleep:
            self.ctx.sleep(seconds)
        else:
            self.ctx.check()
            self.s.sleep(seconds)

    def _image(self) -> np.ndarray:
        """``get_image Run Once`` with the Low Mag settings."""
        result = self.ctx.run_once(FunctionName.GET_IMAGE, CameraType.LOW_MAG)
        pixels = getattr(result, "pixels", result)
        if pixels is None:
            raise FunctionFailed(INVALID_ARGUMENTS, "search object: Get Image returned no image")
        return np.asarray(pixels)

    def _read_position(self) -> tuple[float, float] | None:
        try:
            x, y = self.xy.position()
        except DeviceError as exc:
            log.debug("search object: Get Position XY failed (cleared): %s", exc)
            return None
        return float(x), float(y)

    def _at(self, pos: tuple[float, float] | None, target: tuple[float, float]) -> bool:
        tol = self.o.position_tolerance_mm
        return pos is not None and all(t - tol <= v <= t + tol for v, t in zip(pos, target, strict=True))

    # ---------------------------------------------------------------------------------------------- before the loop
    def _init_hardware(self) -> None:
        """sm d2…d4: Z joystick off, XY joystick off; errors logged and cleared."""
        z = self.s.devices.get("z")
        for device, label in ((z, "Z"), (self.xy, "XY")):
            if device is None:
                continue
            try:
                device.set_joystick(False)
            except (DeviceError, AloxError, AttributeError) as exc:
                app_log(self.s, "warning", f"Detect Object (Disable Joystick {label}): {exc}")

    # ---------------------------------------------------------------------------------------------- states
    # "Init State machine here": snail positions, model
    def start(self) -> SOState:
        p = self.p
        origin = (number(setting(self.s, "hardware.xy.waferxorg", 0.0), "waferxorg"),
                  number(setting(self.s, "hardware.xy.waferyorg", 0.0), "waferyorg"))
        start = wafer_to_stage(p.start_pos, origin, int(stage_coordinate_system(self.s)))
        self.positions = snail_positions(start, p.step_mm, p.max_moves)
        self.index = 0
        self.current = self.positions[0]
        try:
            self.template = self._load_template()
        except (TemplateError, OSError) as exc:
            code = FILE_NOT_FOUND if "not found" in str(exc) else INVALID_ARGUMENTS
            return self.fail(FunctionFailed(code, f"Detect Object (Init): {exc}"), SOState.START,
                             "Detect Object (Init)")
        return SOState.STARTED

    def _load_template(self) -> Template:
        p = self.p
        if not p.search_model_path:
            raise TemplateError("template file not found: no Search Model Path")
        model = self.f.model_path(p.search_model_path)
        mask = None
        if p.learn_mask_path:
            candidate = self.f.model_path(p.learn_mask_path)
            if candidate.is_file():
                mask = candidate
            else:
                app_log(self.s, "warning", f"Detect Object (Init): learn mask {candidate} not found, not used")
        return matching.load_template(self.f.matcher, model, mask, curve=p.match_settings().curve)

    # "Query camera, xy stages, z stage and monochromator status", "Disable Joystick XY, Set XY high speed"
    def started(self) -> SOState:
        bad = []
        for name in self.o.required_devices:
            device = self.s.devices.get(name)
            if device is None or str(getattr(device, "state", DeviceState.READY)) != str(DeviceState.READY):
                bad.append(name)
        if bad:
            exc = FunctionFailed(DETECT_OBJECT_HARDWARE, f"detect object error (hardware): {', '.join(bad)}")
            return self.fail(exc, SOState.STARTED, "Detect Object (Init)")
        try:
            if self.xy.joystick_enabled():
                self.xy.set_joystick(False)
            speed = setting(self.s, "hardware.xy.speed", None)
            if speed is None:                                    # LabVIEW's default 0 would be VEL 0: Fixed
                speed = getattr(getattr(getattr(self.xy, "settings", None), "x", None), "speed", None)
            if speed is not None:
                self.xy.set_speed(number(speed, "hardware.xy.speed"))
        except (DeviceError, AloxError) as exc:
            return self.fail(exc, SOState.STARTED, "Detect Object (Set Speed)")
        self.event("DO Started")
        return SOState.MOVE_TO_POS

    def _move(self, target: tuple[float, float], state: SOState, nxt: SOState, log_source: str) -> SOState:
        """``Move Absolute XY`` (not waited), with the retry of ``Error Count``."""
        try:
            self.xy.move_absolute(target[0], target[1], wait=False)
        except (DeviceError, AloxError) as exc:
            self.move_t = self.s.clock()
            if self.error_count >= self.o.move_retries:
                return self.fail(exc, state, log_source)
            self.error_count += 1
            log.warning("search object: move to %s failed (%d): %s", target, self.error_count, exc)
            return state
        self.error_count = 0
        self.move_t = self.s.clock()
        return nxt

    # "Move to next XY position"
    def move_to_pos(self) -> SOState:
        if self.index > len(self.positions) - 1:                 # ">> No More positions >>"
            exc = FunctionFailed(DETECT_OBJECT_NOT_FOUND, "detect object error (no object found)")
            return self.fail(exc, SOState.MOVE_TO_POS, "Detect Object (Move XY Position)")
        nxt = self._move(self.positions[self.index], SOState.MOVE_TO_POS, SOState.POSITION_OK,
                         "Detect Object (Move XY Position)")
        if nxt is SOState.POSITION_OK:
            self.event("DO Moving", target=self.positions[self.index])
        return nxt

    # "Check position"
    def position_ok(self) -> SOState:
        pos = self._read_position()
        if pos is not None:
            self.current = pos
        if self._at(pos, self.positions[self.index]):
            self.pause(self.o.settle_s)                          # "Make sure the stages has stopped moving"
            first = self.index == 0                              # "Autofocus/Refocus sur 1ere position uniquement"
            self.index += 1
            self.event("DO Moving", reached=True)
            if first and self.p.autofocus:
                return SOState.AUTOFOCUS_INIT
            if first and self.p.refocus:
                return SOState.REFOCUS_INIT
            return SOState.DETECT_OBJECT
        if self.s.clock() - self.move_t > self.o.move_timeout_s:
            return self.fail(FunctionFailed(XY_ERROR, "xy stages error (position not reached)"), SOState.POSITION_OK,
                             "Detect Object (Wait Position)")
        self.pause(self.o.poll_s)
        return SOState.POSITION_OK

    # "Autofocus Large (Synchronous)"
    def autofocus(self) -> SOState:
        try:
            result = self.ctx.run_once(FunctionName.AUTOFOCUS_LARGE, self.f.autofocus_params(self.s))
            self.af_z = getattr(result, "best_z", None)
            self.af_score = getattr(result, "best_score", None)
            self.af_data = tuple(tuple(row) for row in getattr(result, "table", ()))    # type: ignore[misc]
            valid = self.af_z is not None and math.isfinite(self.af_z)
            if not valid:
                raise FunctionFailed(INVALID_ARGUMENTS, "autofocus: no valid focus position")
        except Stopped:
            raise
        except Exception as exc:                                  # noqa: BLE001 - the LL function's error cluster
            return self.fail(exc, SOState.AUTOFOCUS, "Detect Object (Autofocus)")
        self._focused_image("Autofocus")
        if self.p.show_window:
            self.event("DO Autofocus", z=self.af_z, score=self.af_score)
            self.pause(self.o.display_pause_s)
        return SOState.AUTOFOCUS_FINISHED

    def _focused_image(self, what: str) -> None:
        """"Get focused image" / "Display focused image" (errors cleared)."""
        try:
            self._image()
        except Stopped:
            raise
        except Exception as exc:                                  # noqa: BLE001 - cleared, as LabVIEW
            app_log(self.s, "warning", f"Detect Object ({what} image): {exc}")

    def autofocus_finished(self) -> SOState:
        return SOState.REFOCUS_INIT if self.p.refocus else SOState.DETECT_OBJECT

    def refocus_init(self) -> SOState:
        self.refocus_errors = 0
        return SOState.REFOCUS

    # "Refocus fine"
    def refocus(self) -> SOState:
        error: BaseException | None = None
        try:
            self.ctx.run_once(FunctionName.REFOCUS, self.f.refocus_params(self.s))
        except Stopped:
            raise
        except Exception as exc:                                  # noqa: BLE001 - the LL function's error cluster
            error = exc
        self._focused_image("Refocus")
        if self.p.show_window:
            self.event("DO Refocus", attempt=self.refocus_errors + 1)
            self.pause(self.o.display_pause_s)
        if error is None:                                         # "Jump Next State", "Reset counters"
            self.refocus_errors = 0
            return SOState.REFOCUS_FINISHED
        if self.refocus_errors >= self.o.refocus_retries:         # "Failed (max tries)"
            return self.fail(error, SOState.REFOCUS, "Detect Object (Refocus)")
        self.refocus_errors += 1                                  # "Try this state again"
        app_log(self.s, "warning", f"Detect Object (Refocus): {error}")
        return SOState.REFOCUS

    # "Detect Object"
    def detect_object(self) -> SOState:
        p = self.p
        assert self.template is not None
        try:
            image = self._image()
            pre = matching.detect_object_preprocessing(image, p.morph_open, p.morph_close)
            settings = p.match_settings(self.o.number_of_matches)
            matches = tuple(self.f.matcher.match(pre, self.template, settings))
            self.matches = matches
            self.found = bool(matches) and matches[0].score >= p.min_score      # as wired (0–1000 vs %)
            shapes: list[Shape] = []
            if p.overlay_contours:
                shapes += [Points(tuple(map(tuple, c)), CONTOUR_COLOR, 1, marker=1)
                           for c in matching.extract_curves(pre, settings.curve)]
            if p.overlay_match:
                shapes += match_overlay(matches)
            self._overlay(shapes)
            if self.found:
                height, width = image.shape[:2]
                self.found_pos = object_position(self.s, self.current, matches[0].position, (width, height),
                                                 camera=self.o.camera, x_stage_invert=self.o.x_stage_invert,
                                                 y_stage_invert=self.o.y_stage_invert)
            else:
                self.found_pos = None
        except Stopped:
            raise
        except Exception as exc:                                  # noqa: BLE001 - the state's error cluster
            return self.fail(exc, SOState.DETECT_OBJECT, "Detect Object Match")
        self.event("DO Searching", matches=len(self.matches), found_pos=self.found_pos,
                   score=self.matches[0].score if self.matches else None)
        return SOState.MOVE_TO_OBJECT if self.found else SOState.MOVE_TO_POS

    def _overlay(self, shapes: list[Shape]) -> None:
        try:
            self.s.images.set_overlay(OVERLAY, shapes)
        except Exception as exc:                                  # noqa: BLE001 - display only
            log.debug("search object overlay not set: %s", exc)

    # "Move to found object position"
    def move_to_object(self) -> SOState:
        assert self.found_pos is not None
        self.target = (self.found_pos[0] + self.p.offset_x, self.found_pos[1] + self.p.offset_y)
        return self._move(self.target, SOState.MOVE_TO_OBJECT, SOState.OBJECT_POS_OK,
                          "Detect Object (Move To Object)")

    # "Check position OK"
    def object_pos_ok(self) -> SOState:
        assert self.target is not None
        pos = self._read_position()
        if self._at(pos, self.target):
            self.pause(self.o.object_settle_s)
            return SOState.FINISHED
        if self.s.clock() - self.move_t > self.o.move_timeout_s:
            return self.fail(FunctionFailed(XY_ERROR, "xy stages error (object position not reached)"),
                             SOState.OBJECT_POS_OK, "Detect Object (Object Pos ?)")
        self.pause(self.o.poll_s)
        return SOState.OBJECT_POS_OK
