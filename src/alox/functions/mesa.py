r"""HL Mesa measurement: the oxide aperture of a VCSEL mesa followed during the lateral oxidation, the oxidation rate
and the remaining time to the Oxyd Target (or one measurement in Singleshot), as the main supervisor job.

Spec: ``docs/vision-core/map.md`` Part 3 §2.1 (common pattern), §3.11 (HL mesa); Part 1 §2.6 (start, local and remote
overrides); Part 4 §3.8 (:mod:`alox.vision.stats`: ``tools_mesa_averaged_measurement``, ``alox_tool_extract_mesa_fit_
data``, ``alox_tools_fit_diameters_measures``, ``alox_tools_get_mesa_remaining_time``), §3.11 (file names,
``tools_alox_save_img_type``: :mod:`alox.functions.jobfiles`); Part 5 Appendix B (``measure_mesa``); owner answers in
``docs/vision-core/questions.md`` (B3, B9, M1–M9, D4, I2). Diagrams cited ``sm dN`` =
``alox_function_hl_mesa_measurement_state_machine``, ``bundle d`` = ``…_bundle_profile_parameters d``, ``avg`` =
``tools_mesa_averaged_measurement``, ``cast`` = ``alox_tools_cast_measure_mesa_to_json_report``; the globals are
``alox_global_mesa_measurement_function p``.

:meth:`MesaMeasurement.run` runs the state machine in the runner's thread, one state per loop iteration (with
LabVIEW's 10 ms wait), ``ctx.check()`` between states and ``ctx.set_state`` on every change ("MESA State Changed";
"MESA Measured" / "MESA Finished" as ``event=`` data). The low-level functions are called with ``ctx.run_once``
exactly where LabVIEW does ``Run Once``. The core (:class:`alox.core.core.Core`) sets the mesa status 200, sends the
websocket ``started`` / ``failed`` events and ``finished`` after the run (``Mesa Job Finished``); the function sets
the mesa status codes (:class:`alox.core.status.MesaStatus`) through ``services.set_status("mesa", code)`` and
publishes the ``measure_mesa`` and ``error`` events (and ``failed`` for a stop and for an error of a continuous run,
which goes on) with :mod:`alox.api.reports`.

**States** (``alox_typedef_mesa_measurement_function_state``, 21) and transitions as drawn:

- ``Start`` (sm d6, d7…d15): status 401, globals ``MEAS MESA RUNNING`` = T, ``WL RETRY`` = ``AUTO EXPO RETRY`` = F,
  session reset (``trefocus`` = now), job-history folders (``path_process_lot_wafer_job``, ``files\mesa``); next
  ``Measure MESA Init`` when ``analysis``, else ``Move Start`` when ``positionsctrl``, else ``Autofocus Large``. The
  outer frame 0 (sm d2, d3) disables the Z and XY joysticks.
- ``Move Start`` (d18, d19): without ``positionsctrl`` → ``Autofocus Large``; status 402; Start Position (wafer mm)
  rotated by the core's tilt when ``Tilt Correction`` (:func:`alox.vision.wafer.tilted_coordinates`), converted to
  the stage (``hardware.xy.waferxorg/yorg``, ``stagescoordsys``), ``Move Absolute XY`` without a wait → ``Wait Move``;
  error 1501.
- ``Wait Move`` (d29): status 403; both axes within ±0.001 mm → ``Search Init``; 30 s after the move → 9560 logged
  "MESA (Wait Move)", status 1502, ``Error``.
- ``Search Init`` (d36): counters reset, ``Circle Detected`` = F → ``Search Object``.
- ``Search Object`` (d41): status 404; Search Object ``Run Once`` from the (tilt-corrected) Start Position; found →
  monochromator to ``wavelength.wlmanual`` → ``Refocus``; else status 1503, ``Error``.
- ``Autofocus Large`` (d50): with ``positionsctrl`` the monochromator goes to ``wavelength.wlmanual`` first; status
  405 + tries; LL autofocus ``Run Once`` on the **Low Mag** camera; OK → ``Refocus``; a failure retries until
  ``tries ≥ Refocus Retries − 1``, then "loop to previous state" ``Search Init`` (State Error Count + 1) until the
  State Error Count reaches 3: status 1504, ``Error``.
- ``Refocus`` and ``Measure MESA Refocus`` (d63, d151, identical; no transition of the export leads to the second
  one, ``Display Save`` goes to ``Refocus``): refocus when ``Refocus Retries ≠ 0`` and (``now −
  trefocus ≥ Refocus Time`` or a refocus or state error is pending), else → ``Detect Circle Init``. High Mag image,
  then **Refocus Oxyd** ``Run Once`` on the detected circle (status 411 + tries) when ``Circle Detected``, else the
  plain Refocus (status 408 + tries); OK (no error and Valid) → ``trefocus`` = now, counters reset, ``Detect Circle
  Init``; a failure retries ``Refocus`` until ``tries ≥ Refocus Retries − 1``, then ``Autofocus Large`` once (State
  Error Count + 1), then status 1505, ``Error``.
- ``Detect Circle Init`` (d80) → ``Detect Circle`` (d81): status 414 + tries; High Mag Get Image ``Run Once`` and
  Detect Circle ``Run Once`` (overlay best circle on, all circles / objects off); found → ``Circle Detected`` = T,
  counters reset, ``Track MESA``; retries up to tries ≥ 4, then ``Refocus`` once, then status 1506, ``Error``.
- ``Track MESA`` (d96): without ``Tracking`` → ``Auto Expo``; status 419 + tries; Tracking ``Run Once`` on the
  detected circle; OK → ``Auto Expo``; retries up to tries ≥ 4, then ``Detect Circle Init`` once, then 1507.
- ``Auto Expo`` (d109, d114…d122): ``EXPOSURE MODE`` 0 (``CALIB EXPOSURE``, "Fixed"): the exposure table must have its
  fixed exposures (``Has Fixed Expo ?``) → ``Search Wavelength``, else status 1508, ``Error``; other modes: once
  ``Auto Expo Done`` → ``Search Wavelength``; else status 422 + tries, Auto Exposure ``Run Once`` (mode All
  Wavelengths, the detected circle); OK (no error and Found Exposure) → ``Auto Expo Done``, global ``AUTO EXPO
  RETRY`` = F, ``Search Wavelength``; 3 failures → status 1508, ``Error``.
- ``Search Wavelength`` (d124, d129…d134): ``WL Mode`` MANUAL → ``Measure MESA Init`` when the global ``WL RETRY``,
  else ``Set Wavelength``; SCAN: once ``Wavelength Done`` → ``Set Wavelength``; else status 425 + tries, Search
  Wavelength ``Run Once`` (the detected circle); OK (no error and Valid) → ``Wavelength Done``, ``WL RETRY`` = F,
  ``Set Wavelength``; 3 failures → status 1509, ``Error``.
- ``Set Wavelength`` (d135): MANUAL: status 428 + tries, monochromator to ``wavelength.wlmanual``, ``WL RETRY`` = T;
  3 failures → ``Error``; → ``Measure MESA Init``.
- ``Measure MESA Init`` (d146): a monochromator position in [1, 670] nm is replaced by ``wavelength.wlmanual``;
  → ``Measure MESA``.
- ``Measure MESA`` (sm d, d4): status 431 + tries; averaged measurement (``avg``: ``# Averages`` × {High Mag Get Image
  ``Run Once``, Measure Oxyd ``Run Once`` on the detected circle, wait exposure/1000 ms}, then the NaN filter and the
  means of :func:`alox.vision.stats.average_measurements`); positions (monochromator, XY converted to wafer
  coordinates, Z); the sample ``(t, Oxyd Diam.)`` appended to ``Oxyd Measures`` (``t0`` = first sample); oxidation
  rate and remaining time (:func:`alox.vision.stats.estimate_oxidation`: window of the last ``Line Reg. Time`` with
  ≥ 10 points, RANSAC {1000, 0.1, 30 %}, outliers rejected); 9004 is cleared, 9003 only logged once ``t − t0 > Line
  Reg. Time`` ("Log Insuf. #points warning?"); the globals ``WL RETRY`` / ``AUTO EXPO RETRY`` force a new wavelength
  search / auto exposure (``Wavelength Done`` / ``Auto Expo Done`` = F); found → ``measure_mesa`` event, "MESA
  Measured", ``Display Save``; else retried until 5 tries → ``Display Save ERROR``.
- ``Display Save`` (d173, d174…d199): status 436; ``.mesa`` data file and PNG image (below); log "MESA (Finished) -
  Results: …"; Singleshot → ``Finished``; else → ``Refocus`` (the select's selector is the constant T, d197).
- ``Display Save ERROR`` (d200…d226): status 436, data file, then status 1511 and ``Error``.
- ``Finished`` (d227…d238): status 500, ``MEAS MESA RUNNING`` = F; the report is returned (the core queues ``Mesa
  Job Finished``).
- ``Error`` (d239…d258): websocket ``error`` {Source ``measure_mesa``, Code, Description}, ``failed``, log "MESA
  (Error FAIL)"; Singleshot: the run ends (:class:`MesaFailed`); **continuous: the run restarts at ``Move Start``**
  (the loop's stop terminal is F unless Singleshot, d239).
- ``Stopped`` (d259…d270): status 850, ``failed`` event, log "MESA (Stopped)", ``MEAS MESA RUNNING`` = F; a stop or an
  abort at any time (``ctx.check``, or the low-level function's) ends there (:class:`~alox.core.functions.Stopped`).

**Files** (settings ``log.*``, ``general.*``; folders of :func:`alox.functions.jobfiles.job_folders`):
data ``<root>\files\mesa\<job_data_filename>.mesa`` = ``Oxyd Measures`` (row 0 LabVIEW timestamps, row 1 diameters,
``%.4f``) when ``log.savemesadata`` (T) and (``general.coretype`` ≠ 0, Singleshot, or ``log.datafrequency`` ms since
the last data file); image ``<root>\images\<mapping|oxid>\<PJ>_<Wafer>_<Zone>_%y%m%d_%H%M%S.png`` (``%y%m%d_%H%M%S``
alone without a Process Job, ``mapping`` for a Zone ID ≥ "1") when ``log.savemesaimages`` (T) and (``general.coretype``
≠ 0, Singleshot, or more than ``log.imagesfrequency`` ms since the last image), with the overlays when
``general.saveOverlay`` (T) and a new live image first when ``general.saveOverlayRes`` (T) is F.

**measure_mesa** (``cast``): :func:`alox.api.reports.mesa_event` with ``Type_Measure_Rate`` average / median / fitted
/ surface / circumscribed / inscribed, ``Remaining_time`` = the remaining seconds as ``%H:%M:%S`` (86 400 s =
"24:00:00" when the rate cannot be estimated), positions in wafer mm, the monochromator wavelength.

Reproduced (LabVIEW): the states, transitions, retry counts and loops above; status codes; profile keys and defaults
(bundle d); the request overrides (Part 1 §2.6); the averaged measurement and the rate fit; the error/failed/stopped
events and the restart of a continuous run after an error; files, names and the log texts.

Fixed (internal defects, or owner decisions):

- Owner B3: after every wavelength change the next image is one exposed entirely after it (wait old + new exposure,
  :func:`alox.functions.auto_exposure.fresh_frame_wait_s`); LabVIEW used the next frame.
- Owner B9: the large autofocus ends at its best Z (:class:`alox.functions.focus.Autofocus`).
- Status codes stay in their step's range (e.g. ``Track MESA`` 419…421: a 4th or 5th try shows 421, LabVIEW showed
  422…423 "Auto Exposure").
- Without ``positionsctrl`` a failed large autofocus loops back to ``Autofocus Large``, not to ``Search Init``
  (which would move the stage the supervisor asked not to move).
- The ``.mesa`` data file of the process chamber is written when ``log.datafrequency`` has elapsed since the last
  one; LabVIEW tested ``round(remainder(t − t0, period)) = 0``, which a measurement loop of a few seconds rarely hits.
- A continuous run restarted after an error starts with its retry counters at 0 (LabVIEW kept them, so the restart
  could fail at once) and after ``MesaOptions.error_restart_pause_s`` (1 s; LabVIEW: none, it can flood the
  supervisor with error events when the hardware is down). ``MesaOptions.restart_after_error = False`` ends the run.
- The "loop to previous state" retries could cycle forever: the state looped back to resets the State Error Count on
  success (e.g. Detect Circle lost → Refocus OK → Detect Circle lost → …, or Track ↔ Detect Circle), so the "max
  loops" test never fired and a Singleshot never ended. At most ``MesaOptions.max_loops`` (10) loop-backs per
  measurement cycle, then the failing step's error.
- The ``Wait Move`` timeout and the XY move are measured with the monotonic clock.

Choices of this implementation:

- ``measure_mesa`` is published only for a found measurement (the "MESA found ?" status ends the error chain before
  ``Send Notification``, inferred); ``STDDEV FET/#`` = sample standard deviation of the filtered Fit series (the
  report field "STDEV FIT/#"; formula inferred from ``avg``).
- The circle of Measure Oxyd / Refocus Oxyd / Tracking / Auto Exposure / Search Wavelength is the last one Detect
  Circle found in this run, else Detect Circle's last report, else ``avg``'s constant {1224, 1024, 30}.
- The globals ``LAUNCH FROM HMI AE/WL`` skip the auto exposure / the wavelength steps (their TRUE cases are not in
  the export, inferred); :data:`GLOBALS` holds the five globals for the UI.
- The overlay switches ``Overlay Time/Oxyd Rate/Oxyd Diameter/Oxyd Status`` add texts to the ``mesa`` overlay
  (remaining time, rate, diameter, state; texts inferred). ``Show Window`` (run panel) is the ``ctx.set_state`` data.
- Search Wavelength's ``locate`` hook: :func:`locate_mesa` runs Measure Oxyd on Detect Circle's last circle (LabVIEW:
  ``alox_function_ll_measure_mesa`` Run Once inside the LL worker); the registry passes it when it builds
  :class:`alox.functions.search_wavelength.SearchWavelength`.
- The core's tilt is read through the ``tilt`` constructor argument (the registry passes ``core.tilt``).
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
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
from alox.core.status import MesaStatus
from alox.devices.base import DeviceError
from alox.functions import jobfiles
from alox.functions.auto_exposure import (
    AutoExposureMode,
    AutoExposureParams,
    LabelledEnum,
    Pauser,
    as_bool,
    error_code_source,
    fresh_frame_wait_s,
    grab_image,
    registered,
)
from alox.functions.detect_circle import INVALID_ARGUMENTS, DetectCircleParams, app_log
from alox.functions.focus import CameraType
from alox.functions.get_image import stage_to_wafer
from alox.functions.measure_oxyd import (
    MEASURE_OXYD_PROCESSING,
    MeasureOxydParams,
    MesaShapeType,
)
from alox.functions.refocus_oxyd import RefocusOxydParams
from alox.functions.search_object import (
    DETECT_OBJECT_NOT_FOUND,
    SearchObjectParams,
    default_autofocus_params,
    default_refocus_params,
    wafer_to_stage,
)
from alox.functions.search_wavelength import (
    MesaLocation,
    SearchWavelengthParams,
    WLMode,
)
from alox.functions.services import Services, Text
from alox.functions.tracking import TrackingParams, setting, stage_coordinate_system
from alox.vision.circles import Circle
from alox.vision.stats import (
    INSUFFICIENT_POINTS,
    SERIES,
    AveragedMeasurement,
    LinearFit,
    OxidationEstimate,
    OxideMeasureType,
    average_measurements,
    estimate_oxidation,
    format_hms,
    sample_std,
)
from alox.vision.wafer import tilted_coordinates
from aloxcam.errors import AloxError
from aloxcam.imaging.png import PngError, write_png

__all__ = [
    "DEFAULT_CIRCLE",
    "GLOBALS",
    "MEASURE_TYPE_NAMES",
    "REPORT_NAME",
    "MesaError",
    "MesaExposureMode",
    "MesaFailed",
    "MesaGlobals",
    "MesaMeasurement",
    "MesaOptions",
    "MesaParams",
    "MesaReport",
    "MesaState",
    "OxydDiameters",
    "image_filename",
    "locate_mesa",
    "mesa_event",
]

log = logging.getLogger(__name__)

#: ``Function`` of the websocket events and ``Source`` of the ``error`` event.
REPORT_NAME = "measure_mesa"
#: ``Best Circle`` constant of ``avg`` before any detection (centre of a 2448 × 2048 sensor, radius 30 px).
DEFAULT_CIRCLE = Circle(1224.0, 1024.0, 30.0)
#: ``Oxyd Measure Type`` → ``Type_Measure_Rate`` (``cast d1…d6``).
MEASURE_TYPE_NAMES: Mapping[OxideMeasureType, str] = {
    OxideMeasureType.MEAN: "average", OxideMeasureType.MEDIAN: "median", OxideMeasureType.FIT: "fitted",
    OxideMeasureType.SURFACE: "surface", OxideMeasureType.CIRCUMSCRIBED: "circumscribed",
    OxideMeasureType.INSCRIBED: "inscribed"}
#: LabVIEW timestamps count seconds from 1904-01-01 UTC (the ``.mesa`` file and the graph).
LABVIEW_EPOCH_OFFSET_S = 2_082_844_800.0
#: "XY Move timeout ?" (sm d29): ``alox_error`` 9560.
XY_MOVE_TIMEOUT = 9560

#: Errors of a step (device, low-level function, missing function or device); never :class:`Stopped`.
_STEP_ERRORS = (AloxError, DeviceError, LookupError, ValueError, RuntimeError, OSError)


# ================================================================================================== enums, globals
class MesaState(StrEnum):
    """``alox_typedef_mesa_measurement_function_state``."""

    START = "Start"
    MOVE_START = "Move Start"
    WAIT_MOVE = "Wait Move"
    SEARCH_INIT = "Search Init"
    SEARCH_OBJECT = "Search Object"
    AUTOFOCUS_LARGE = "Autofocus Large"
    REFOCUS = "Refocus"
    DETECT_CIRCLE_INIT = "Detect Circle Init"
    DETECT_CIRCLE = "Detect Circle"
    TRACK_MESA = "Track MESA"
    AUTO_EXPO = "Auto Expo"
    SEARCH_WAVELENGTH = "Search Wavelength"
    SET_WAVELENGTH = "Set Wavelength"
    MEASURE_MESA_INIT = "Measure MESA Init"
    MEASURE_MESA_REFOCUS = "Measure MESA Refocus"
    MEASURE_MESA = "Measure MESA"
    DISPLAY_SAVE = "Display Save"
    DISPLAY_SAVE_ERROR = "Display Save ERROR"
    FINISHED = "Finished"
    ERROR = "Error"
    STOPPED = "Stopped"


class MesaExposureMode(LabelledEnum):
    """``EXPOSURE MODE`` of the params: 0 = the calibrated exposure table ("Fixed", sm d109), 1 = run the auto
    exposure to build it (label of 1 inferred)."""

    CALIB_EXPOSURE = 0
    AUTO_EXPOSURE = 1

    @classmethod
    def _labels(cls) -> tuple[str, ...]:
        return ("CALIB EXPOSURE", "AUTO EXPOSURE")


class MesaGlobals:
    """``alox_global_mesa_measurement_function`` (``WL RETRY``, ``AUTO EXPO RETRY``, ``LAUNCH FROM HMI WL``, ``LAUNCH
    FROM HMI AE``) and the global ``MEAS MESA RUNNING``. The UI sets ``wl_retry`` / ``auto_expo_retry`` to force a new
    wavelength search / auto exposure during a running job."""

    def __init__(self) -> None:
        self.wl_retry = False
        self.auto_expo_retry = False
        self.launch_from_hmi_wl = False
        self.launch_from_hmi_ae = False
        self.meas_mesa_running = False


#: The application's globals (one mesa function per application).
GLOBALS = MesaGlobals()


# ================================================================================================== errors
class MesaError(AloxError):
    """Refused parameters (9002): nothing moved."""


class MesaFailed(AloxError):
    """A Singleshot run (or a run with ``restart_after_error`` off) reached ``Error``: ``code``/``source`` of the
    failure, ``state`` that failed, ``status`` (mesa status code) and the last ``report`` (None before a
    measurement)."""

    def __init__(self, code: int, source: str, *, state: str, status: int, report: MesaReport | None) -> None:
        super().__init__(code, source)
        self.state = state
        self.status = status
        self.report = report


def _invalid(text: str) -> MesaError:
    return MesaError(INVALID_ARGUMENTS, text)


# ================================================================================================== params
def _set(obj: object, name: str, value: Any) -> None:
    object.__setattr__(obj, name, value)


def _float(value: Any, what: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise _invalid(f"{what}: not a number: {value!r}") from None
    if not math.isfinite(number):
        raise _invalid(f"{what}: not finite: {value!r}")
    return number


def _int(value: Any, what: str) -> int:
    number = _float(value, what)
    if number != int(number) or number < 0:
        raise _invalid(f"{what}: a whole number ≥ 0 expected, got {value!r}")
    return int(number)


#: ``bundle d``: field → (profile key, default).
_PROFILE_KEYS: Mapping[str, tuple[str, Any]] = {
    "tilt_correction": ("measure.mesa.tiltcorrection", True),
    "refocus_time_s": ("measure.mesa.refocustime", 15.0),
    "refocus_retries": ("measure.mesa.refocusretries", 3),
    "line_reg_time_s": ("measure.mesa.regressiontime", 0.0),
    "singleshot": ("measure.mesa.singleshot", False),
    "averages": ("measure.mesa.averages", 1),
    "show_window": ("measure.mesa.showwindow", False),
    "overlay_time": ("measure.mesa.overlaytime", False),
    "overlay_rate": ("measure.mesa.overlayrate", False),
    "overlay_diameter": ("measure.mesa.overlaydiameter", False),
    "overlay_status": ("measure.mesa.overlaystatus", False),
    "positionsctrl": ("measure.mesa.positionsctrl", False),
    "analysis": ("measure.mesa.analysis", False),
    "tracking": ("measure.mesa.tracking", False),
    "wl_mode": ("measure.mesa.wlmodetab", 1),
    "exposure_mode": ("measure.mesa.exposuremode", 1),
}
#: Request override → params field (``alox_core_ui d451…d458`` "Use x,y,tilt,target REST API Parameters").
_REQUEST_KEYS = {"target": "oxyd_target_um", "tilt": "tilt_correction", "singleshot": "singleshot",
                 "positionsctrl": "positionsctrl", "analysis": "analysis", "indexID": "index_id"}


@dataclass(frozen=True)
class MesaParams:
    """``alox_typedef_mesa_measurement_function_parameters`` in cluster order (defaults = the typedef's;
    :meth:`from_profile` = the bundle VI's), plus the request's ``indexID`` (read, not used, map Part 5 Q6)."""

    start_position_mm: tuple[float, float] = (0.0, 0.0)     # wafer coordinates
    tilt_correction: bool = False
    refocus_time_s: float = 0.0
    refocus_retries: int = 0
    oxyd_target_um: float = 0.0
    line_reg_time_s: float = 0.0
    singleshot: bool = False
    averages: int = 0                                       # 0 measures once
    show_window: bool = False
    overlay_time: bool = False
    overlay_rate: bool = False
    overlay_diameter: bool = False
    overlay_status: bool = False
    positionsctrl: bool = False
    analysis: bool = False
    tracking: bool = False
    wl_mode: WLMode = WLMode.MANUAL
    exposure_mode: MesaExposureMode = MesaExposureMode.CALIB_EXPOSURE
    index_id: str | None = None

    def __post_init__(self) -> None:
        try:
            x, y = self.start_position_mm
        except (TypeError, ValueError):
            raise _invalid(f"Start Position (mm): two values expected, got {self.start_position_mm!r}") from None
        _set(self, "start_position_mm", (_float(x, "Start Position X"), _float(y, "Start Position Y")))
        for name in ("tilt_correction", "singleshot", "show_window", "overlay_time", "overlay_rate", "overlay_diameter",
                     "overlay_status", "positionsctrl", "analysis", "tracking"):
            _set(self, name, as_bool(getattr(self, name)))
        for name in ("refocus_time_s", "oxyd_target_um", "line_reg_time_s"):
            _set(self, name, _float(getattr(self, name), name))
        for name in ("refocus_time_s", "line_reg_time_s"):
            if getattr(self, name) < 0:
                raise _invalid(f"{name} must be ≥ 0, got {getattr(self, name)!r}")
        _set(self, "refocus_retries", _int(self.refocus_retries, "Refocus Retries"))
        _set(self, "averages", _int(self.averages, "# Averages"))
        _set(self, "wl_mode", WLMode.coerce(self.wl_mode, "WL Mode", INVALID_ARGUMENTS))
        _set(self, "exposure_mode", MesaExposureMode.coerce(self.exposure_mode, "EXPOSURE MODE", INVALID_ARGUMENTS))
        if self.index_id is not None:
            _set(self, "index_id", str(self.index_id))

    @classmethod
    def from_profile(cls, profile: Any, **overrides: Any) -> MesaParams:
        """``alox_function_hl_mesa_measurement_bundle_profile_parameters d``: ``measure.mesa.*`` (Oxyd Target from
        ``measure.oxyd.oxydtarget``, else ``measure.mesa.oxydtarget``: both constants are stacked in the diagram);
        ``overrides`` (field names) win."""
        g = profile.get
        values: dict[str, Any] = {name: g(key, default) for name, (key, default) in _PROFILE_KEYS.items()}
        values["start_position_mm"] = (g("measure.mesa.startx", 0.0), g("measure.mesa.starty", 0.0))
        values["oxyd_target_um"] = g("measure.oxyd.oxydtarget", g("measure.mesa.oxydtarget", 0.0))
        values.update(overrides)
        return cls(**values)

    @classmethod
    def from_request(cls, overrides: Mapping[str, Any], profile: Any) -> MesaParams:
        """The profile parameters with the request's ``x``, ``y`` (Start Position), ``target`` (Oxyd Target),
        ``tilt`` (Tilt Correction), ``singleshot``, ``positionsctrl``, ``analysis`` and ``indexID`` (missing ones keep
        the profile's). A local single start passes ``{"tilt": False, "singleshot": True}`` (``alox_core_ui d315``)."""
        extra: dict[str, Any] = {field_: overrides[key] for key, field_ in _REQUEST_KEYS.items()
                                 if overrides.get(key) is not None}
        if overrides.get("x") is not None or overrides.get("y") is not None:
            base = cls.from_profile(profile).start_position_mm
            extra["start_position_mm"] = (overrides["x"] if overrides.get("x") is not None else base[0],
                                          overrides["y"] if overrides.get("y") is not None else base[1])
        return cls.from_profile(profile, **extra)


@dataclass(frozen=True)
class MesaOptions:
    """Server-side settings (never client-visible). Defaults: the LabVIEW constants."""

    camera: str = "highmag"                  # "LOW ANGLE / HIGH MAG" camera of the measurements
    state_pause_s: float = 0.010             # Wait (ms) 10 of the state loop
    poll_s: float = 0.010                    # Wait Move: between two position reads
    move_timeout_s: float = 30.0             # Wait Move: "XY Move timeout ?" 30000 ms → 9560
    position_tolerance_mm: float = 0.001     # Wait Move: "Check position" ±0.001
    af_max_loops: int = 3                    # Autofocus Large: State Error Count ≥ 3 → Error
    detect_tries: int = 4                    # Detect Circle: tries ≥ 4 → loop / Error
    track_tries: int = 4                     # Track MESA: tries ≥ 4 → loop / Error
    max_retries: int = 3                     # Auto Expo, Search Wavelength, Set Wavelength: "Max Retries" 3
    measure_tries: int = 5                   # Measure MESA: "# Max tries" 5
    max_loops: int = 10                      # NEW: loop-backs per measurement cycle before Error
    min_fit_points: int = 10                 # "# Min Points" of alox_tool_extract_mesa_fit_data
    wl_protect_nm: tuple[float, float] = (1.0, 670.0)   # Measure MESA Init: In Range → Manual WL
    fresh_frame_after_wl: bool = True        # owner B3
    restart_after_error: bool = True         # continuous run: Error → Move Start (LabVIEW)
    error_restart_pause_s: float = 1.0       # NEW: pause before that restart


# ================================================================================================== report
@dataclass(frozen=True)
class OxydDiameters:
    """``Oxyd`` cluster of the report (µm; ``circularity`` = Circumscribed/Inscribed)."""

    fit: float = math.nan
    median: float = math.nan
    average: float = math.nan
    surface: float = math.nan
    inscribed: float = math.nan
    circumscribed: float = math.nan
    circularity: float = math.nan
    measure_type: OxideMeasureType = OxideMeasureType.MEAN
    stdev_fit: float = math.nan


@dataclass(frozen=True)
class MesaReport:
    """``alox_typedef_mesa_measurement_report`` (+ the session data the run panel and the files use).

    ``timestamp``: Unix seconds; ``rate_um_per_min`` / ``remaining_s`` / ``remaining_text``: ``Oxyd Rate``
    (86 400 s, "?" and NaN while the rate cannot be estimated, ``rate_error`` 9003/9004); ``linear_fit``: the line
    ``d = slope·t + intercept`` (µm/s, Unix seconds); ``position``: wafer X, Y and Z (mm); ``oxyd_diameter_um``: the
    series of ``Oxyd Measure Type``; ``samples``: ``Oxyd Measures`` ``(t, d)``; ``fitted``: the fitted points of the
    window."""

    job: JobInfo
    timestamp: float
    external_fit_um: float
    oxyd: OxydDiameters
    wavelength_nm: float
    rate_um_per_min: float
    remaining_s: float
    remaining_text: str
    linear_fit: LinearFit | None
    position: tuple[float, float, float]
    oxyd_diameter_um: float
    oxyd_target_um: float
    averages: int
    oxid_filtered: tuple[float, ...] = ()
    oxid_raw: tuple[float, ...] = ()
    found: bool = False
    rate_error: int | None = None
    samples: tuple[tuple[float, float], ...] = ()
    fitted: tuple[tuple[float, float], ...] = ()
    images: tuple[Path, ...] = ()
    data_files: tuple[Path, ...] = ()


def mesa_event(report: MesaReport) -> Any:
    """The websocket ``measure_mesa`` event of a report (``cast``)."""
    o = report.oxyd
    return reports.mesa_event(
        report.job, unix_time=report.timestamp, external_fitted=report.external_fit_um,
        oxyd={"Fitted": o.fit, "Median": o.median, "Average": o.average, "Surface": o.surface,
              "Inscribed": o.inscribed, "Circumscribed": o.circumscribed, "Circularity": o.circularity},
        measure_type=MEASURE_TYPE_NAMES[o.measure_type], stddev=o.stdev_fit, rate=report.rate_um_per_min,
        remaining=format_hms(report.remaining_s), position=report.position, wavelength=report.wavelength_nm)


def image_filename(job: JobInfo | None, when: datetime) -> str:
    """The PNG name of ``Display Save`` (sm d173): ``<PJ>_<Wafer>_<Zone>_%y%m%d_%H%M%S``, the time stamp alone when
    the Process Job ID is ``""`` or ``"Pj"`` (no extension)."""
    j = job or JobInfo()
    ts = jobfiles.format_timestamp(when)
    if j.process_job_id in jobfiles.NO_JOB_IDS:
        return ts
    return f"{j.process_job_id}_{j.wafer_id}_{j.zone_id}_{ts}"


# ================================================================================================== locate hook
def _best_circle_of(ctx: FunctionContext) -> Circle | None:
    detect = registered(ctx, FunctionName.DETECT_CIRCLE)
    last = getattr(detect, "last_report", None) if detect is not None else None
    circle = getattr(last, "best_circle", None) if last is not None and getattr(last, "found", False) else None
    return circle if isinstance(circle, Circle) else None


def locate_mesa(ctx: FunctionContext, pixels: np.ndarray, *, circle: Circle | None = None) -> MesaLocation | None:
    """Search Wavelength's ``locate`` hook: Measure Oxyd ``Run Once`` on ``pixels`` around ``circle`` (default:
    Detect Circle's last circle; none: None, the search keeps its circle). Pass it as
    ``SearchWavelength(services, locate=locate_mesa)``."""
    circle = circle or _best_circle_of(ctx)
    if circle is None:
        return None
    function = registered(ctx, FunctionName.MEASURE_OXYD)
    if function is None:
        return None
    services = getattr(function, "services", None)
    profile = services.profile if services is not None else None
    params = (MeasureOxydParams.from_profile(profile, circle=circle, image=pixels) if profile is not None
              else MeasureOxydParams(circle=circle, image=pixels))
    report = ctx.run_once(FunctionName.MEASURE_OXYD, params)
    return MesaLocation(mesa_found=bool(report.mesa_found), oxyd_found=bool(report.oxyd_found),
                        accuracy=float(report.diameters.accuracy), fit_diameter_px=float(report.diameters.fit),
                        mesa=report.external_circle, oxyd=report.oxyd_circle,
                        circular=params.mesa_shape_type is MesaShapeType.CIRCULAR)


# ================================================================================================== the function
class MesaMeasurement(MeasurementFunction):
    """HL Mesa measurement (module docstring).

    ``run(params, ctx)`` accepts the core's :class:`alox.core.core.MeasureRequest` (profile + its ``overrides``,
    :meth:`MesaParams.from_request`), a :class:`MesaParams`, a mapping of :class:`MesaParams` fields over the profile,
    or None (the profile). Returns the last :class:`MesaReport` (Singleshot: the measurement; continuous: at
    ``Finished``, never reached as drawn); raises :class:`MesaError` 9002 for invalid parameters (no motion),
    :class:`MesaFailed` after ``Error`` (Singleshot), :class:`~alox.core.functions.Stopped` after a stop or an abort.

    ``tilt``: the core's measured tilt in degrees (``core.tilt``; default 0). ``wall``: Unix time of the samples and
    the file names (default :func:`time.time`); intervals use ``services.clock``; pauses go through
    ``services.sleep`` when it is not :func:`time.sleep` (tests), else ``ctx.sleep`` (a stop wakes them at once).
    ``globals_``: :data:`GLOBALS` by default."""

    name = FunctionName.MESA_MEASURE

    def __init__(self, services: Services, *, options: MesaOptions | None = None,
                 tilt: Callable[[], float] | None = None, wall: Callable[[], float] | None = None,
                 globals_: MesaGlobals | None = None) -> None:
        self.services = services
        self.options = options or MesaOptions()
        for device in (self.options.camera, "z", "wl"):
            if device not in services.devices:
                raise LookupError(f"device {device!r} is not available")
        self._tilt = tilt or (lambda: 0.0)
        self._wall = wall or time.time
        self.globals = globals_ or GLOBALS
        self.last_report: MesaReport | None = None

    def tilt(self) -> float:
        return float(self._tilt())

    def params_for(self, request: Any) -> MesaParams:
        """:class:`MesaParams` of a request (class docstring)."""
        if isinstance(request, MesaParams):
            return request
        profile = self.services.profile
        if request is None:
            return MesaParams.from_profile(profile)
        overrides = getattr(request, "overrides", None)
        if overrides is not None:
            return MesaParams.from_request(overrides, profile)
        if isinstance(request, Mapping):
            return MesaParams.from_profile(profile, **dict(request))
        raise _invalid(f"mesa: unsupported parameters {type(request).__name__}")

    def run(self, params: Any, ctx: FunctionContext) -> MesaReport | None:
        job = ctx.job if ctx.job is not None else getattr(params, "job", None) or JobInfo()
        try:
            p = self.params_for(params)
        except (MesaError, TypeError) as exc:
            self.services.set_status("mesa", int(MesaStatus.ERROR))
            if isinstance(exc, MesaError):
                raise
            raise _invalid(f"mesa: {exc}") from None
        return _MesaJob(self, p, job, ctx).execute()


@dataclass
class _Session:
    """``MESA Session`` (sm p): counters, flags, times and the series."""

    t0: float = 0.0                      # Unix time of the first sample
    t0_save: float | None = None         # clock of the last saved image
    t_data: float | None = None          # clock of the last data file
    trefocus: float = 0.0                # clock of the last refocus ("Reset Autofocus timestamp")
    xy_move_t: float = 0.0               # clock of the XY move
    target: tuple[float, float] = (0.0, 0.0)    # stage position of Move Start
    samples: list[tuple[float, float]] = field(default_factory=list)   # Oxyd Measures (Unix t, µm)
    auto_expo_done: bool = False
    wavelength_done: bool = False
    circle_detected: bool = False
    circle: Circle | None = None
    af_err: int = 0
    rf_err: int = 0
    dc_err: int = 0
    tr_err: int = 0
    state_err: int = 0
    loops: int = 0                       # loop-backs since the last measurement (Fixed)

    def reset_counters(self) -> None:
        self.af_err = self.rf_err = self.dc_err = self.tr_err = self.state_err = self.loops = 0


class _MesaJob:
    """One run of the state machine."""

    def __init__(self, function: MesaMeasurement, p: MesaParams, job: JobInfo, ctx: FunctionContext) -> None:
        self.f = function
        self.s = function.services
        self.o = function.options
        self.g = function.globals
        self.p = p
        self.job = job
        self.ctx = ctx
        self.pause = Pauser(self.s, ctx)
        self.ss = _Session()
        self.state = MesaState.START
        self.code: int | None = None
        self.error: tuple[int, str] | None = None
        self.failed_state = ""
        self.last_ll_error: BaseException | None = None
        self.folders: jobfiles.JobFolders | None = None
        self.report: MesaReport | None = None
        self.images: list[Path] = []
        self.data_files: list[Path] = []
        self.handlers: dict[MesaState, Callable[[], MesaState | None]] = {
            MesaState.START: self.start, MesaState.MOVE_START: self.move_start, MesaState.WAIT_MOVE: self.wait_move,
            MesaState.SEARCH_INIT: self.search_init, MesaState.SEARCH_OBJECT: self.search_object,
            MesaState.AUTOFOCUS_LARGE: self.autofocus_large, MesaState.REFOCUS: self.refocus,
            MesaState.MEASURE_MESA_REFOCUS: self.refocus, MesaState.DETECT_CIRCLE_INIT: self.detect_circle_init,
            MesaState.DETECT_CIRCLE: self.detect_circle, MesaState.TRACK_MESA: self.track_mesa,
            MesaState.AUTO_EXPO: self.auto_expo, MesaState.SEARCH_WAVELENGTH: self.search_wavelength,
            MesaState.SET_WAVELENGTH: self.set_wavelength, MesaState.MEASURE_MESA_INIT: self.measure_mesa_init,
            MesaState.MEASURE_MESA: self.measure_mesa, MesaState.DISPLAY_SAVE: self.display_save,
            MesaState.DISPLAY_SAVE_ERROR: self.display_save_error, MesaState.FINISHED: self.finished,
            MesaState.ERROR: self.error_state}

    # ---------------------------------------------------------------------------------------------- loop
    def execute(self) -> MesaReport | None:
        state: MesaState | None = MesaState.START
        previous: MesaState | None = None
        self.joysticks_off()
        try:
            while state is not None:
                self.ctx.check()
                self.state = state
                if state is not previous:
                    self.ctx.set_state(state.value)                     # "MESA State Changed"
                    previous = state
                try:
                    state = self.handlers[state]()
                except MesaFailed:
                    raise
                except _STEP_ERRORS as exc:                             # an unexpected step error
                    state = self.fail(exc, self.state, MesaStatus.ERROR, f"MESA ({self.state.value})")
                if state is not None:
                    self.pause(self.o.state_pause_s)
        except Stopped:
            self.stopped()
            raise
        finally:
            self.g.meas_mesa_running = False
        self.f.last_report = self.report
        return self.report

    # ---------------------------------------------------------------------------------------------- helpers
    def set_status(self, code: int) -> None:
        if code != self.code:
            self.code = int(code)
            self.s.set_status("mesa", self.code)

    def log(self, level: str, text: str) -> None:
        app_log(self.s, level, text)

    def log_error(self, source: str, exc: BaseException) -> None:
        code, text = error_code_source(exc)
        self.log("error", f"{source}: error {code}: {text}")

    def fail(self, exc: BaseException, state: MesaState, status: int, source: str) -> MesaState:
        """Record the error of ``state`` (status, log) and go to ``Error``."""
        self.error = error_code_source(exc)
        self.failed_state = state.value
        self.set_status(int(status))
        self.log_error(source, exc)
        return MesaState.ERROR

    def get(self, key: str, default: Any) -> Any:
        """A setting (``alox.ini``, LabVIEW "PROPS")."""
        return setting(self.s, key, default)

    def device(self, name: str) -> Any:
        return self.s.device(name)

    def step_ok(self, what: str, call: Callable[[], Any], ok: Callable[[Any], bool]) -> bool:
        """Run a low-level step; an error or a not-OK result is logged ``what`` and gives False."""
        try:
            result = call()
        except _STEP_ERRORS as exc:
            self.last_ll_error = exc
            self.log_error(what, exc)
            return False
        if not ok(result):
            self.log("warning", f"{what}: not valid")
            return False
        return True

    def joysticks_off(self) -> None:
        """Outer frame 0 (sm d2, d3): Z joystick and both XY joysticks off."""
        for name in ("z", "xy"):
            device = self.s.devices.get(name)
            if device is None:
                continue
            try:
                device.set_joystick(False)
            except (DeviceError, AloxError, AttributeError, OSError) as exc:
                self.log("warning", f"MESA (Disable Joystick {name}): {exc}")

    def exposure_us(self) -> float:
        try:
            value = self.s.camera(self.o.camera).status().exposure_us
        except (DeviceError, AloxError, LookupError, AttributeError, OSError):
            return 0.0
        return float(value) if value is not None and math.isfinite(value) else 0.0

    def grab(self) -> np.ndarray:
        """High Mag Get Image ``Run Once`` (the newest camera frame without a registered Get Image)."""
        return grab_image(self.s, self.ctx, self.o.camera)

    def goto_wavelength(self, nm: float) -> None:
        """``Set Position`` of the monochromator (U32), then owner B3's wait for a fresh frame when it moved."""
        wl = self.device("wl")
        target = round(float(nm))
        try:
            old: int | None = int(wl.position())
        except (DeviceError, AloxError, AttributeError, OSError, TypeError, ValueError):
            old = None
        wl.goto(target, wait=True)
        if self.o.fresh_frame_after_wl and old != target:
            exposure = self.exposure_us()
            period = getattr(self.s.devices.get(self.o.camera), "frame_period_s", 0.0)
            self.pause(fresh_frame_wait_s(exposure, exposure, float(period or 0.0)))

    def manual_wavelength(self) -> float:
        return float(self.s.profile.get("wavelength.wlmanual", 0.0))

    def best_circle(self) -> Circle:
        """The circle of the measurement steps (module docstring)."""
        return self.ss.circle or _best_circle_of(self.ctx) or DEFAULT_CIRCLE

    def start_position(self) -> tuple[float, float]:
        """Start Position (wafer mm), rotated by the measured tilt when ``Tilt Correction``."""
        start = self.p.start_position_mm
        if self.p.tilt_correction:
            return tilted_coordinates(start, self.f.tilt())
        return start

    def origin_system(self) -> tuple[tuple[float, float], int]:
        origin = (float(self.get("hardware.xy.waferxorg", 0.0)), float(self.get("hardware.xy.waferyorg", 0.0)))
        return origin, int(stage_coordinate_system(self.s))

    @staticmethod
    def tries(base: MesaStatus, count: int, last: int) -> int:
        """``base + count``, kept in the step's range ``[base, base + last]`` (Fixed)."""
        return int(base) + max(0, min(count, last))

    # ---------------------------------------------------------------------------------------------- states
    def start(self) -> MesaState:
        """``Start`` (sm d6…d15)."""
        self.set_status(MesaStatus.INITIALIZING)                     # 401
        self.g.meas_mesa_running = True
        self.g.wl_retry = False
        self.g.auto_expo_retry = False
        self.ss = _Session(trefocus=self.s.clock())                  # "Reset Autofocus timestamp"
        if self.s.folders is not None:
            zone = self.job.zone_id
            try:
                self.folders = jobfiles.job_folders(self.s.folders, self.job,
                                                    images="mapping" if zone >= "1" else "oxid", files=("log", "mesa"))
            except OSError as exc:
                self.log_error("MESA (Job folders)", exc)
        if self.p.analysis:
            return MesaState.MEASURE_MESA_INIT
        return MesaState.MOVE_START if self.p.positionsctrl else MesaState.AUTOFOCUS_LARGE

    def move_start(self) -> MesaState:
        """``Move Start`` (sm d18, d19)."""
        if not self.p.positionsctrl:
            return MesaState.AUTOFOCUS_LARGE
        self.set_status(MesaStatus.MOVE_START)                       # 402
        try:
            origin, system = self.origin_system()
            target = wafer_to_stage(self.start_position(), origin, system)
            self.device("xy").move_absolute(target[0], target[1], wait=False)
        except _STEP_ERRORS as exc:
            return self.fail(exc, MesaState.MOVE_START, MesaStatus.ERROR_MOVE_START, "MESA (Move Start)")
        self.ss.target = (float(target[0]), float(target[1]))
        self.ss.xy_move_t = self.s.clock()
        return MesaState.WAIT_MOVE

    def wait_move(self) -> MesaState:
        """``Wait Move`` (sm d29)."""
        self.set_status(MesaStatus.WAIT_MOVE_START)                  # 403
        tol = self.o.position_tolerance_mm
        try:
            x, y = self.device("xy").position()
        except _STEP_ERRORS as exc:
            return self.fail(exc, MesaState.WAIT_MOVE, MesaStatus.ERROR_WAIT_MOVE_START, "MESA (Wait Move)")
        tx, ty = self.ss.target
        if abs(x - tx) <= tol and abs(y - ty) <= tol:
            return MesaState.SEARCH_INIT
        if self.s.clock() - self.ss.xy_move_t > self.o.move_timeout_s:
            exc = AloxError(XY_MOVE_TIMEOUT, f"XY move timeout: ({x:.4f}, {y:.4f}) mm, target ({tx:.4f}, {ty:.4f})")
            return self.fail(exc, MesaState.WAIT_MOVE, MesaStatus.ERROR_WAIT_MOVE_START, "MESA (Wait Move)")
        self.pause(self.o.poll_s)
        return MesaState.WAIT_MOVE

    def search_init(self) -> MesaState:
        """``Search Init`` (sm d36)."""
        self.ss.reset_counters()
        self.ss.circle_detected = False
        return MesaState.SEARCH_OBJECT

    def search_object(self) -> MesaState:
        """``Search Object`` (sm d41)."""
        self.set_status(MesaStatus.SEARCH_OBJECT)                    # 404
        params = SearchObjectParams.from_profile(self.s.profile, start_pos=self.start_position())
        try:
            report = self.ctx.run_once(FunctionName.SEARCH_OBJECT, params)
            if not getattr(report, "found", False):
                raise AloxError(DETECT_OBJECT_NOT_FOUND, "Search Object: no object found")
            self.goto_wavelength(self.manual_wavelength())
        except _STEP_ERRORS as exc:
            return self.fail(exc, MesaState.SEARCH_OBJECT, MesaStatus.ERROR_SEARCH_OBJECT, "MESA (Search Object)")
        self.ss.rf_err = 0
        return MesaState.REFOCUS

    def autofocus_large(self) -> MesaState:
        """``Autofocus Large`` (sm d50)."""
        ss = self.ss
        if self.p.positionsctrl:
            self.goto_wavelength(self.manual_wavelength())
        self.set_status(self.tries(MesaStatus.AUTOFOCUS_1, ss.af_err, 2))     # 405…407
        params = default_autofocus_params(self.s, CameraType.LOW_MAG)
        if self.step_ok("MESA (Autofocus)", lambda: self.ctx.run_once(FunctionName.AUTOFOCUS_LARGE, params),
                        lambda r: getattr(r, "best_index", None) is not None):
            ss.af_err = 0
            return MesaState.REFOCUS
        if ss.af_err >= self.p.refocus_retries - 1:                  # "Failed (max tries)"
            if ss.state_err >= self.o.af_max_loops:                  # "Failed (max loops)"
                return self.fail(self.last_error("autofocus failed"), MesaState.AUTOFOCUS_LARGE,
                                 MesaStatus.ERROR_AUTOFOCUS, "MESA (Autofocus)")
            ss.state_err += 1                                        # "Loop to prev. state", reset #tries
            ss.af_err = 0
            nxt = self.loop_back(MesaState.SEARCH_INIT if self.p.positionsctrl else MesaState.AUTOFOCUS_LARGE)
            if nxt is None:
                return self.fail(self.last_error("autofocus failed"), MesaState.AUTOFOCUS_LARGE,
                                 MesaStatus.ERROR_AUTOFOCUS, "MESA (Autofocus)")
            return nxt
        ss.af_err += 1
        return MesaState.AUTOFOCUS_LARGE

    def last_error(self, text: str, code: int = 9500) -> BaseException:
        """The last low-level error (the error wire of the failed step), else ``AloxError(code, text)``."""
        exc, self.last_ll_error = self.last_ll_error, None
        return exc if exc is not None else AloxError(code, text)

    def loop_back(self, state: MesaState) -> MesaState | None:
        """Count a "Loop to prev. state" of this measurement cycle; None once ``MesaOptions.max_loops`` is
        exceeded (Fixed: the state looped back to resets LabVIEW's State Error Count, so the loops never ended)."""
        self.ss.loops += 1
        return state if self.ss.loops <= self.o.max_loops else None

    def refocus(self) -> MesaState:
        """``Refocus`` / ``Measure MESA Refocus`` (sm d63, d151)."""
        ss, p = self.ss, self.p
        due = p.refocus_retries != 0 and (self.s.clock() - ss.trefocus >= p.refocus_time_s or ss.rf_err > 0
                                          or ss.state_err > 0)
        if not due:
            return MesaState.DETECT_CIRCLE_INIT
        circle = ss.circle if ss.circle_detected else None
        oxyd = circle is not None
        base = MesaStatus.REFOCUS_OXYD_1 if oxyd else MesaStatus.REFOCUS_1   # 411 / 408
        self.set_status(self.tries(base, ss.rf_err, 2))

        def run() -> Any:
            self.grab()
            if oxyd:
                params = RefocusOxydParams.from_profile(self.s.profile, circle=circle,
                                                        camera_type=CameraType.HIGH_MAG)
                return self.ctx.run_once(FunctionName.REFOCUS_OXYD, params)
            return self.ctx.run_once(FunctionName.REFOCUS, default_refocus_params(self.s, CameraType.HIGH_MAG))

        def valid(result: Any) -> bool:
            if oxyd:
                return bool(getattr(result, "valid", False))
            return getattr(result, "best_index", None) is not None

        if self.step_ok("MESA (Refocus)", run, valid):
            ss.trefocus = self.s.clock()
            ss.rf_err = 0
            ss.state_err = 0
            return MesaState.DETECT_CIRCLE_INIT
        if ss.rf_err >= p.refocus_retries - 1:                       # "Failed (max tries)"
            if ss.state_err >= 1:                                    # "Failed (max loops)"
                return self.fail(self.last_error("refocus failed"), self.state, MesaStatus.ERROR_REFOCUS,
                                 "MESA (Refocus)")
            ss.state_err += 1                                        # "Loop to prev. state"
            ss.rf_err = 0
            if self.loop_back(MesaState.AUTOFOCUS_LARGE) is None:
                return self.fail(self.last_error("refocus failed"), self.state, MesaStatus.ERROR_REFOCUS,
                                 "MESA (Refocus)")
            return MesaState.AUTOFOCUS_LARGE
        ss.rf_err += 1                                               # "Try this state again"
        return MesaState.REFOCUS

    def detect_circle_init(self) -> MesaState:
        """``Detect Circle Init`` (sm d80)."""
        return MesaState.DETECT_CIRCLE

    def detect_circle(self) -> MesaState:
        """``Detect Circle`` (sm d81)."""
        ss = self.ss
        self.set_status(self.tries(MesaStatus.DETECT_MESA_1, ss.dc_err, 4))    # 414…418
        found: list[Circle] = []

        def run() -> Any:
            pixels = self.grab()
            params = DetectCircleParams.from_profile(self.s.profile, overlay_best_circle=True,
                                                     overlay_all_circles=False, overlay_objects=False,
                                                     camera=CameraType.HIGH_MAG, image=pixels)
            report = self.ctx.run_once(FunctionName.DETECT_CIRCLE, params)
            if report.found and report.best_circle is not None:
                found.append(report.best_circle)
            return report

        if self.step_ok("MESA Measurement (Detect Circle)", run, lambda _r: bool(found)):
            ss.circle = found[0]
            ss.circle_detected = True
            ss.dc_err = 0
            ss.state_err = 0
            return MesaState.TRACK_MESA
        ss.circle_detected = False
        if ss.dc_err >= self.o.detect_tries:
            if ss.state_err >= 1:
                return self.fail(self.last_error("detect circle failed"), MesaState.DETECT_CIRCLE,
                                 MesaStatus.ERROR_DETECT_CIRCLE, "MESA Measurement (Detect Circle)")
            ss.state_err += 1
            ss.dc_err = 0
            if self.loop_back(MesaState.REFOCUS) is None:
                return self.fail(self.last_error("detect circle failed"), MesaState.DETECT_CIRCLE,
                                 MesaStatus.ERROR_DETECT_CIRCLE, "MESA Measurement (Detect Circle)")
            return MesaState.REFOCUS
        ss.dc_err += 1
        return MesaState.DETECT_CIRCLE

    def track_mesa(self) -> MesaState:
        """``Track MESA`` (sm d96, d101)."""
        ss = self.ss
        if not self.p.tracking:
            ss.tr_err = ss.state_err = 0
            return MesaState.AUTO_EXPO
        self.set_status(self.tries(MesaStatus.TRACK_MESA_1, ss.tr_err, 2))     # 419…421
        c = self.best_circle()
        params = TrackingParams.from_profile(self.s.profile, circle=(c.x, c.y, c.radius))
        if self.step_ok("MESA (Track MESA)", lambda: self.ctx.run_once(FunctionName.TRACKING, params),
                        lambda _r: True):
            ss.tr_err = ss.state_err = 0
            return MesaState.AUTO_EXPO
        if ss.tr_err >= self.o.track_tries:
            if ss.state_err >= 1:
                return self.fail(self.last_error("tracking failed"), MesaState.TRACK_MESA,
                                 MesaStatus.ERROR_TRACK_MESA, "MESA (Track MESA)")
            ss.state_err += 1
            ss.tr_err = 0
            if self.loop_back(MesaState.DETECT_CIRCLE_INIT) is None:
                return self.fail(self.last_error("tracking failed"), MesaState.TRACK_MESA,
                                 MesaStatus.ERROR_TRACK_MESA, "MESA (Track MESA)")
            return MesaState.DETECT_CIRCLE_INIT
        ss.tr_err += 1
        return MesaState.TRACK_MESA

    def auto_expo(self) -> MesaState:
        """``Auto Expo`` (sm d109, d114…d122)."""
        ss = self.ss
        if self.g.launch_from_hmi_ae:
            return MesaState.SEARCH_WAVELENGTH
        if self.p.exposure_mode is MesaExposureMode.CALIB_EXPOSURE:
            table = self.s.exposures
            if table is not None and table.has_fixed_expo():
                return MesaState.SEARCH_WAVELENGTH                   # "Wavelengths/Exposures table OK"
            exc = AloxError(int(MesaStatus.ERROR_INVALID_EXPOSURES), "invalid columns 1-2 in exposure=f(WL) table")
            return self.fail(exc, MesaState.AUTO_EXPO, MesaStatus.ERROR_AUTO_EXPOSURE, "MESA (Auto-Exposure)")
        if ss.auto_expo_done:
            return MesaState.SEARCH_WAVELENGTH
        self.set_status(self.tries(MesaStatus.AUTO_EXPOSURE_1, ss.state_err, 2))   # 422…424
        params = replace(AutoExposureParams.from_profile(self.s.profile, circle_in=self.best_circle()),
                         mode=AutoExposureMode.ALL_WAVELENGTHS)
        if self.step_ok("MESA (Auto-Exposure)", lambda: self.ctx.run_once(FunctionName.AUTO_EXPOSURE, params),
                        lambda r: bool(getattr(r, "found_exposure", False))):
            ss.auto_expo_done = True
            self.g.auto_expo_retry = False
            ss.state_err = 0
            return MesaState.SEARCH_WAVELENGTH
        if ss.state_err + 1 >= self.o.max_retries:                   # "AutoExpo failed 3 times"
            return self.fail(self.last_error("auto exposure failed 3 times"), MesaState.AUTO_EXPO,
                             MesaStatus.ERROR_AUTO_EXPOSURE, "MESA (Auto-Exposure)")
        ss.state_err += 1
        return MesaState.AUTO_EXPO

    def search_wavelength(self) -> MesaState:
        """``Search Wavelength`` (sm d124, d129…d134)."""
        ss = self.ss
        if self.p.wl_mode is WLMode.MANUAL:
            return MesaState.MEASURE_MESA_INIT if self.g.wl_retry else MesaState.SET_WAVELENGTH
        if self.g.launch_from_hmi_wl or ss.wavelength_done:
            return MesaState.SET_WAVELENGTH
        self.set_status(self.tries(MesaStatus.SEARCH_WAVELENGTH_1, ss.state_err, 2))   # 425…427
        params = SearchWavelengthParams.from_profile(self.s.profile, circle_in=self.best_circle())
        if self.step_ok("MESA Measurement (Auto Wavelength)",
                        lambda: self.ctx.run_once(FunctionName.SEARCH_WAVELENGTH, params),
                        lambda r: bool(getattr(r, "valid", False))):
            ss.wavelength_done = True
            self.g.wl_retry = False
            ss.state_err = 0
            return MesaState.SET_WAVELENGTH
        if ss.state_err + 1 >= self.o.max_retries:
            return self.fail(self.last_error("search wavelength failed"), MesaState.SEARCH_WAVELENGTH,
                             MesaStatus.ERROR_SEARCH_WAVELENGTH, "MESA Measurement (Auto Wavelength)")
        ss.state_err += 1
        return MesaState.SEARCH_WAVELENGTH

    def set_wavelength(self) -> MesaState:
        """``Set Wavelength`` (sm d135)."""
        ss = self.ss
        if self.p.wl_mode is WLMode.MANUAL and not self.g.launch_from_hmi_wl:
            self.set_status(self.tries(MesaStatus.SET_WAVELENGTH_1, ss.state_err, 2))  # 428…430
            if not self.step_ok("MESA Measurement (Set Wavelength)",
                                lambda: self.goto_wavelength(self.manual_wavelength()), lambda _r: True):
                if ss.state_err + 1 >= self.o.max_retries:
                    return self.fail(self.last_error("set wavelength failed"), MesaState.SET_WAVELENGTH,
                                     MesaStatus.ERROR_SEARCH_WAVELENGTH, "MESA Measurement (Set Wavelength)")
                ss.state_err += 1
                return MesaState.SET_WAVELENGTH
            self.g.wl_retry = True
        ss.state_err = 0
        return MesaState.MEASURE_MESA_INIT

    def measure_mesa_init(self) -> MesaState:
        """``Measure MESA Init`` (sm d146)."""
        lo, hi = self.o.wl_protect_nm
        try:
            wl = float(self.device("wl").position())
            if lo <= wl <= hi:
                self.goto_wavelength(self.manual_wavelength())
        except _STEP_ERRORS as exc:
            self.log_error("MESA (Measure MESA Init)", exc)
        self.ss.state_err = 0
        return MesaState.MEASURE_MESA

    # ---------------------------------------------------------------------------------------------- measure
    def averaged_measurement(self) -> tuple[AveragedMeasurement, MeasureOxydParams]:
        """``tools_mesa_averaged_measurement`` (``avg``): ``# Averages`` images measured by Measure Oxyd, µm."""
        n = max(1, self.p.averages)
        base = MeasureOxydParams.from_profile(self.s.profile, circle=self.best_circle())
        series: dict[str, list[float]] = {name: [] for name in SERIES}
        for _ in range(n):
            self.ctx.check()
            pixels = self.grab()
            try:
                report = self.ctx.run_once(FunctionName.MEASURE_OXYD, replace(base, image=pixels))
                d = report.diameters_um
                values = {"external": d.external, "fit": d.fit, "median": d.median, "mean": d.mean,
                          "surface": d.surface, "inscribed": d.inscribed, "circumscribed": d.circumscribed,
                          "circularity": d.circ_insc_ratio}
            except _STEP_ERRORS as exc:
                self.last_ll_error = exc
                self.log_error("MESA (Measure)", exc)
                values = dict.fromkeys(SERIES, math.nan)
            for name in SERIES:
                series[name].append(float(values[name]))
            self.pause(self.exposure_us() / 1e6)                    # Wait exposure/1000 ms
        averaged = average_measurements(series, measure_type=base.oxyd_measure_type,
                                        mesa_mandatory=base.mesa_mandatory)
        return averaged, base

    def positions(self) -> tuple[float, tuple[float, float, float]]:
        """Monochromator wavelength, XY (wafer mm, ``Convert`` XY Stages → Wafer) and Z; a missing device gives NaN."""
        devices = self.s.devices
        wl = float(devices["wl"].position()) if "wl" in devices else math.nan
        x = y = math.nan
        if "xy" in devices:
            sx, sy = devices["xy"].position()
            origin, system = self.origin_system()
            x, y = stage_to_wafer(float(sx), float(sy), origin, system)
        z = float(devices["z"].position()) if "z" in devices else math.nan
        return wl, (x, y, z)

    def measure_mesa(self) -> MesaState:
        """``Measure MESA`` (sm d, d4)."""
        ss, p = self.ss, self.p
        self.set_status(self.tries(MesaStatus.MEASURE_MESA_1, ss.state_err, 4))    # 431…435
        try:
            averaged, mo = self.averaged_measurement()
            wl, position = self.positions()
        except _STEP_ERRORS as exc:
            self.last_ll_error = exc
            self.log_error("MESA (Measure)", exc)
            return self.measure_failed()
        ti = float(self.f._wall())
        if ss.t0 == 0.0:                                             # "inited t0"
            ss.t0 = ti
        ss.samples.append((ti, averaged.oxide_diameter))
        ts = [t for t, _ in ss.samples]
        ds = [d for _, d in ss.samples]
        est = estimate_oxidation(ts, ds, target_um=p.oxyd_target_um, regression_time_s=p.line_reg_time_s,
                                 min_points=self.o.min_fit_points)
        if est.error is not None and est.error.code == INSUFFICIENT_POINTS and ti - ss.t0 > p.line_reg_time_s:
            self.log_error("MESA (Measure)", est.error)              # "Log Insuf. #points warning?"; 9004 cleared
        if self.g.wl_retry:
            ss.wavelength_done = False
        if self.g.auto_expo_retry:
            ss.auto_expo_done = False
        found = averaged.oxide_found and averaged.mesa_found
        self.report = self.make_report(averaged, mo, est, ti, wl, position, found)
        if not found:
            return self.measure_failed()
        self.display_results(self.report)
        if self.s.publish is not None:                               # "Send measurement to Supervisor"
            self.s.publish(mesa_event(self.report))
        self.ctx.set_state(self.state.value, event="MESA Measured", report=self.report)
        ss.state_err = ss.loops = 0
        return MesaState.DISPLAY_SAVE

    def measure_failed(self) -> MesaState:
        ss = self.ss
        if ss.state_err + 1 >= self.o.measure_tries:                 # "# Max tries" 5
            return MesaState.DISPLAY_SAVE_ERROR
        ss.state_err += 1
        return MesaState.MEASURE_MESA

    def make_report(self, averaged: AveragedMeasurement, mo: MeasureOxydParams, est: OxidationEstimate, ti: float,
                    wl: float, position: tuple[float, float, float], found: bool) -> MesaReport:
        a = averaged.averages
        oxyd = OxydDiameters(fit=a["fit"], median=a["median"], average=a["mean"], surface=a["surface"],
                             inscribed=a["inscribed"], circumscribed=a["circumscribed"],
                             circularity=a["circularity"], measure_type=averaged.measure_type,
                             stdev_fit=sample_std(averaged.filters["fit"].final))
        rem = est.remaining
        fitted: tuple[tuple[float, float], ...] = ()
        linear = None
        if est.fit is not None:
            linear = est.fit.fit
            fitted = tuple(zip(map(float, est.fit.timestamps), map(float, est.fit.fitted), strict=True))
        return MesaReport(
            job=self.job, timestamp=ti, external_fit_um=a["external"], oxyd=oxyd, wavelength_nm=wl,
            rate_um_per_min=rem.rate_um_per_min, remaining_s=rem.seconds, remaining_text=rem.text,
            linear_fit=linear, position=position, oxyd_diameter_um=averaged.oxide_diameter,
            oxyd_target_um=self.p.oxyd_target_um, averages=max(1, self.p.averages),
            oxid_filtered=tuple(map(float, averaged.oxide_filtered)), oxid_raw=tuple(map(float, averaged.oxide_raw)),
            found=found, rate_error=None if est.error is None else est.error.code, samples=tuple(self.ss.samples),
            fitted=fitted, images=tuple(self.images), data_files=tuple(self.data_files))

    def display_results(self, report: MesaReport) -> None:
        """"Display Image & Results": the texts of the overlay switches added to the ``mesa`` overlay."""
        p = self.p
        texts: list[str] = []
        if p.overlay_time:
            texts.append(f"Remaining time: {format_hms(report.remaining_s)}")
        if p.overlay_rate:
            texts.append(f"Oxyd rate: {report.rate_um_per_min:.3f} um/min")
        if p.overlay_diameter:
            texts.append(f"Oxyd diameter: {report.oxyd_diameter_um:.3f} um")
        if p.overlay_status:
            texts.append(f"Status: {self.state.value}")
        if not texts:
            return
        images = self.s.images
        shapes_of = getattr(images, "shapes", None)
        base = tuple(shapes_of("mesa")) if callable(shapes_of) else ()
        base = tuple(s for s in base if not (isinstance(s, Text) and s.text.startswith(_TEXT_PREFIXES)))
        images.set_overlay("mesa", base + tuple(Text(20.0, 20.0 + 24.0 * i, t, (255, 255, 0), 20)
                                                for i, t in enumerate(texts)))

    # ---------------------------------------------------------------------------------------------- display save
    def display_save(self) -> MesaState:
        """``Display Save`` (sm d173, d174…d199)."""
        self.set_status(MesaStatus.DATA_LOGGING)                     # 436
        self.save_data()
        self.save_image()
        self.log_results()
        self.ss.state_err = 0
        return MesaState.FINISHED if self.p.singleshot else MesaState.REFOCUS

    def display_save_error(self) -> MesaState:
        """``Display Save ERROR`` (sm d200…d226)."""
        self.set_status(MesaStatus.DATA_LOGGING)
        self.save_data()
        return self.fail(self.last_error("measure mesa failed", MEASURE_OXYD_PROCESSING), MesaState.MEASURE_MESA,
                         MesaStatus.ERROR_MEASURE_MESA, "MESA (Measure)")

    def chamber_due(self, last: float | None, period_ms_key: str) -> bool:
        """Measure chamber (``general.coretype`` ≠ 0) or Singleshot: always; process chamber: once the period of
        ``period_ms_key`` (ms) has elapsed since ``last`` (a clock time; None: never saved)."""
        if int(self.get("general.coretype", 0) or 0) != 0 or self.p.singleshot or last is None:
            return True
        period_s = float(self.get(period_ms_key, 0) or 0) / 1000.0
        return self.s.clock() - last > period_s

    def save_data(self) -> None:
        """Frame 1 "Save MESA data": ``Oxyd Measures`` as ``<job_data_filename>.mesa`` in ``files\\mesa``."""
        if self.folders is None or not as_bool(self.get("log.savemesadata", True)):
            return
        if not self.chamber_due(self.ss.t_data, "log.datafrequency"):
            return
        rows = [[t + LABVIEW_EPOCH_OFFSET_S for t, _ in self.ss.samples], [d for _, d in self.ss.samples]]
        when = datetime.fromtimestamp(self.f._wall()).astimezone()
        try:
            path = self.folders.file_folder("mesa") / (jobfiles.job_data_filename(self.job, when) + ".mesa")
            self.data_files.append(jobfiles.write_spreadsheet(path, rows, "%.4f"))
        except (OSError, ValueError, KeyError) as exc:
            self.log_error("MESA (Log data)", exc)
            return
        self.ss.t_data = self.s.clock()

    def save_image(self) -> None:
        """Frame 2 "Save MESA image" (``tools_alox_save_img_type`` "PNG")."""
        if self.folders is None or self.folders.images is None or not as_bool(self.get("log.savemesaimages", True)):
            return
        if not self.chamber_due(self.ss.t0_save, "log.imagesfrequency"):
            return
        images = self.s.images
        try:
            if not as_bool(self.get("general.saveOverlayRes", True)):
                self.grab()                                          # "Live Mode" image without the results
            when = datetime.fromtimestamp(self.f._wall()).astimezone()
            path = self.folders.images / f"{image_filename(self.job, when)}.png"
            merged = getattr(images, "merged", None)
            picture = merged() if as_bool(self.get("general.saveOverlay", True)) and callable(merged) else None
            if picture is not None:
                picture.save(path, format="PNG")
            else:
                pixels = images.last(self.o.camera)
                if pixels is None:
                    self.log("info", "MESA (Log Image): no image to save")
                    return
                write_png(path, np.asarray(pixels))
        except (*_STEP_ERRORS, PngError) as exc:
            self.log_error("MESA (Log Image)", exc)
            return
        self.images.append(path)
        self.ss.t0_save = self.s.clock()
        if self.report is not None:
            self.report = replace(self.report, images=tuple(self.images), data_files=tuple(self.data_files))

    def log_results(self) -> None:
        """Frame 3: "MESA (Finished) - Results: …" (Log Info, 3 decimals)."""
        r = self.report
        if r is None:
            return
        o = r.oxyd
        values = (("Ext Diam", r.external_fit_um), ("Oxyd fit diam", o.fit), ("Oxyd med diam", o.median),
                  ("Oxyd ave diam", o.average), ("Oxyd surf diam", o.surface), ("Oxyd inscr diam", o.inscribed),
                  ("Oxyd circum diam", o.circumscribed))
        text = ", ".join(f"{label}: {value:.3f} µm" for label, value in values)
        self.log("info", f"MESA (Measurement finished OK): MESA (Finished) - Results: {text}")
        if self.report is not None:
            self.report = replace(self.report, images=tuple(self.images), data_files=tuple(self.data_files))

    # ---------------------------------------------------------------------------------------------- end states
    def finished(self) -> None:
        """``Finished`` (sm d227…d238)."""
        self.set_status(MesaStatus.FINISHED)                         # 500
        self.g.meas_mesa_running = False
        self.ctx.set_state(MesaState.FINISHED.value, event="MESA Finished", report=self.report)

    def error_state(self) -> MesaState:
        """``Error`` (sm d239…d258): ``error`` and ``failed`` events; Singleshot ends, a continuous run restarts."""
        code, source = self.error or (9500, "mesa: unknown error")
        publish = self.s.publish
        if publish is not None:                                      # "send 'error' event (websocket)"
            publish(reports.error_event(REPORT_NAME, code, source))
        self.g.meas_mesa_running = False
        self.log("error", f"MESA (Error FAIL): error {code}: {source}")
        self.ctx.set_state(MesaState.ERROR.value, event="MESA Finished", report=self.report)
        if self.p.singleshot or not self.o.restart_after_error:
            self.f.last_report = self.report
            raise MesaFailed(code, source, state=self.failed_state, status=self.code or int(MesaStatus.ERROR),
                             report=self.report)
        if publish is not None:                                      # "send 'failed' event (websocket)"
            publish(reports.function_event("failed", REPORT_NAME, self.job))
        self.error = None
        self.ss.reset_counters()
        self.pause(self.o.error_restart_pause_s)
        self.g.meas_mesa_running = True
        return MesaState.MOVE_START

    def stopped(self) -> None:
        """``Stopped`` (sm d259…d270)."""
        self.state = MesaState.STOPPED
        self.set_status(MesaStatus.STOPPED)                          # 850
        self.ctx.set_state(MesaState.STOPPED.value)                  # "MESA State Changed"
        self.ctx.set_state(MesaState.STOPPED.value, event="MESA Finished", report=self.report)
        if self.s.publish is not None:
            self.s.publish(reports.function_event("failed", REPORT_NAME, self.job))
        self.log("info", "MESA (Stopped)")
        self.g.meas_mesa_running = False
        self.f.last_report = self.report


#: Prefixes of the texts :meth:`_MesaJob.display_results` adds (replaced at every measurement).
_TEXT_PREFIXES = ("Remaining time: ", "Oxyd rate: ", "Oxyd diameter: ", "Status: ")

