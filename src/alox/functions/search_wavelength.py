"""Search Wavelength (``alox_function_ll_search_wavelength*``) as a measurement function of the integrated application.

Spec: ``docs/vision-core/map.md`` Part 3 §2.1, §3.9, Part 4 §3.3 (``tool_alox_wavelength_ROI_selection``), §3.4
(``tool_alox_detectCircle_lessFar_and_found``), §3.9 (exposure table); ``docs/vision-core/monochromator.md`` §6.3, Q5.
Citations ``<vi> dN``: ``sm`` = ``alox_function_ll_search_wavelength_state_machine``, ``bundle`` =
``alox_function_ll_search_wavelength_bundle_profile_parameters``, ``roi`` = ``tool_alox_wavelength_ROI_selection``.

The worker scans the monochromator from ``Start WL`` to ``End WL`` by ``Step WL`` and keeps the wavelength of maximal
contrast ``mean(OXYD ROI) − mean(NON OXYD ROI)`` (minimal when ``Min. Contrast`` < 0) on the High Mag camera
("WAVELENGTH ALWAYS USE LOW ANGLE/HIGH MAG CAMERA", sm d0); in ``MANUAL`` mode it only moves to ``Manual WL``.

States as drawn: ``Start`` (sm d4) → ``Started`` (d14: monochromator serial number, else 9041; initial position;
camera status, else 9043) → ``Init WL`` (d19..d23: MANUAL: move to Manual WL; SCAN: move to Start WL, Get Image,
1 s) → ``Move WL`` (d24..d28: ``Set Position`` and the table exposure of the wavelength, "Fixed" =
``Get Exposure(WL)``, "Auto" = ``Get Auto Expo``, from the Get Image profile's Exposure Mode) → ``WL Pos OK ?``
(d29: serial number, position = target, else stay; MANUAL → ``Move Opt WL``) → ``Refocus`` (d34..d47: when
``Step Refocus`` ≠ 0, ``WL ≠ Start WL`` and ``(WL − Start WL) mod Step Refocus = 0``: Get Image + LL Refocus Run
Once; a failed or invalid refocus is retried, after 5 retries ``Error``; 1 s pause) → ``Detect circle`` (d0, d48..d58:
Get Image, Measure Mesa; at the start wavelength ("SINGLE") or at every wavelength ("FULL") the measured circle
replaces the current one when found and closer than its radius; ROIs of ``roi``) → ``Measure`` (d59: contrast,
optimum, progress ``(WL − Start + 1)/(End − Start)·100`` as drawn, next ``WL + Step``, ``Move Opt WL`` once beyond
``End WL``) → ``Move Opt WL`` (d68..d74: optimal exposure from the table; MANUAL: the report is filled with the manual
wavelength, Valid, 100 %; ``Set Position(Optimal WL)``) → ``Check Opt WL`` (d75: position = optimum, then
``Set Camera Exposure(Get Exposure(Optimal WL))``; ``Finished`` when Valid, else ``Error``) → ``Finished`` (d76..d92:
``.wal`` data when ``log.savewavelengthdata`` in SCAN mode). ``Error`` (d93..d111) and ``Stopped`` (d112..d127):
initial wavelength restored, data saved.

Reproduced (LabVIEW): states and transitions; the profile keys and defaults (bundle d); the ROIs (Spot-Annular:
NON OXYD = disc ``Annul. Spot Size/2``, OXYD = annulus between ``Annul. Diam 1/2`` and ``Annul. Diam 2/2``; Dual
Spots: two discs ``Size/2``, the OXYD one at ``Distance`` and ``Angle`` from the centre, ``y = cy − d·sin a``;
pixel size 1 for a calibration in pixels, ×1000 in mm; 9042 for an empty circle in SCAN mode); the contrast, the
optimum (first maximum, or first minimum when ``Min. Contrast`` < 0) and ``Valid ?`` (``opt > Min`` or, for a
negative Min, ``opt ≤ Min``); ``Optimal WL = WL(opt) + WL shift``, ``Optimal Expo`` 0 until ``Move Opt WL``; the
refocus rule (in nm, ``R = x − y·floor(x/y)``) and its 5 retries; the progress formula (> 100 % at the last
wavelength); the next wavelength by repeated addition; the image name ``<wl>nm_<expo>us.png``
(``log.savewavelengthimage``, SCAN mode) and the ``.wal`` columns ``WL NOXYD OXYD CONTRAST``.

Fixed (internal defects):

- ``WL Pos OK ?`` / ``Check Opt WL`` re-read the position without limit (monochromator D11): bounded by
  ``SearchWavelengthOptions.position_timeout_s`` (9040 "timeout").
- ``Check Opt WL`` with an invalid result reached ``Error`` without an error code: 9042 (questions below).
- ``Step WL`` ≤ 0 looped forever, non-finite limits or ROI sizes ≤ 0 measured nothing: refused before any motion
  (9041 / 9042).
- Measure Mesa ran at every wavelength although its result is only used at the wavelengths that detect (SINGLE: the
  start one): it runs only there.

Choices of this implementation:

- **Images**: as LabVIEW, Get Image Run Once (``ctx.run_once(GET_IMAGE)`` with the profile's High Mag parameters)
  when the Get Image function is registered, else the newest camera frame; ``acquire=`` overrides both.
- **Wait after a wavelength change** (owner decision B3, monochromator Q5): :class:`WaitRule` ``FRESH_FRAME``
  (default) = before each image this function applies the table exposure ("Fixed" Get Image mode) and, when the
  wavelength or the exposure changed since the previous image, waits ``old + new exposure``
  (:func:`alox.functions.auto_exposure.fresh_frame_wait_s`) and takes the newest frame (Get Image "Current");
  ``AS_DRAWN`` = no wait of its own: Get Image ("Fixed") sets the exposure and applies its own wait;
  ``GET_IMAGE`` = ``100 ms + 0.11·max(old, new)`` after an exposure change; ``CHANGELOG`` = ``2·max(old, new)``
  after every change. The 1 s pauses of ``Init WL`` and after a refocus are kept as drawn.
- Default ``Start WL`` 700 nm (owner decision D4; LabVIEW 750). Only ``goto`` / ``position`` / ``serial`` of the
  monochromator are used (owner decision B4).
- **Mesa detection**: LabVIEW runs ``alox_function_ll_measure_mesa`` Run Once. Here a ``locate(ctx, pixels)`` hook
  returns a :class:`MesaLocation` (found flags, accuracy, fit diameter, mesa and oxide circles in pixels); without
  it the input circle is kept ("Recenter skip"), as LabVIEW does at the wavelengths that do not detect.
- **Refocus**: LabVIEW runs ``alox_function_ll_refocus`` Run Once (High Mag, profile ``refocus.*``). The default
  runs the registered :data:`FunctionName.REFOCUS` with :class:`alox.functions.focus.RefocusParams` built from those
  keys; "valid" = it returned without error. ``refocus=`` overrides it.
- ``Show UI`` (run panel) and the "WL Started/Measured/Finished" UI events become ``ctx.set_state`` data
  (``event``, ``report``); the overlay "wavelength" holds the ROIs (NON OXYD blue, OXYD red) and the result text.
- The spectrum post-processing VIs (``_concatening``, ``_search_gradient_evalutation``) and the Measure Mesa
  "Gradient Values" are not ported (they feed the run panel only).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
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
from alox.functions.auto_exposure import (
    DEFAULT_START_WL,
    LV_ERRORS,
    FunctionFailed,
    LabelledEnum,
    Pauser,
    WaitRule,
    as_bool,
    error_code_source,
    grab_image,
    is_empty_circle,
    local_now,
    masked_mean,
    oval_mask,
    pixel_size_um,
    registered,
    require_devices,
    settle_s,
    timestamp_name,
)
from alox.functions.services import Circle as CircleShape
from alox.functions.services import Services, Text
from alox.vision.circles import Circle
from aloxcam.errors import AloxError
from aloxcam.imaging.png import write_png

__all__ = [
    "DetectCircleMethod",
    "MesaLocation",
    "SWState",
    "SearchWavelength",
    "SearchWavelengthError",
    "SearchWavelengthOptions",
    "SearchWavelengthParams",
    "SearchWavelengthReport",
    "SpectrumPoint",
    "SpotRoi",
    "WLMode",
    "WLRoiType",
    "WavelengthRois",
    "less_far_and_found",
    "refocus_due",
    "wavelength_data_text",
    "wavelength_rois",
]

log = logging.getLogger(__name__)

SW_TIMEOUT = 9040             # search wavelength error (timeout)
SW_MONOCHROMATOR = 9041       # search wavelength error (monochromator)
SW_ROI = 9042                 # search wavelength error (image/region of interest)
SW_CAMERA = 9043              # search wavelength error (camera)

TEXTS = {
    SW_TIMEOUT: "search wavelength error (timeout)",
    SW_MONOCHROMATOR: "search wavelength error (monochromator)",
    SW_ROI: "search wavelength error (image/region of interest)",
    SW_CAMERA: "search wavelength error (camera)",
}


class SearchWavelengthError(AloxError):
    """A search-wavelength error 9040..9043 (also refused parameters)."""

    def __init__(self, code: int, detail: str = "") -> None:
        super().__init__(code, TEXTS.get(code, "search wavelength error") + (f": {detail}" if detail else ""))


def _wrap(exc: BaseException, code: int, detail: str = "") -> BaseException:
    if isinstance(exc, AloxError):
        return exc
    error = SearchWavelengthError(code, f"{detail}: {exc}" if detail else str(exc))
    error.__cause__ = exc
    return error


# ================================================================================================== enums
class WLMode(LabelledEnum):
    """``alox_typedef_search_wavelength_function_mode``."""

    MANUAL = 0
    SCAN = 1

    @classmethod
    def _labels(cls) -> tuple[str, ...]:
        return ("MANUAL", "SCAN")


class WLRoiType(LabelledEnum):
    """``alox_typedef_search_wavelength_function_roi_type``."""

    DUAL_SPOTS = 0
    SPOT_ANNULAR = 1

    @classmethod
    def _labels(cls) -> tuple[str, ...]:
        return ("Dual Spots", "Spot-Annular")


class DetectCircleMethod(LabelledEnum):
    """``alox_typedef_search_wavelength_detect_circle_method``."""

    SINGLE = 0
    FULL = 1

    @classmethod
    def _labels(cls) -> tuple[str, ...]:
        return ("SINGLE (AT START WL)", "FULL (AT EVERY WL)")


class SWState(StrEnum):
    """``alox_typedef_search_wavelength_function_state`` (original spellings)."""

    START = "Start"
    STARTED = "Started"
    INIT_WL = "Init WL"
    MOVE_WL = "Move WL"
    WL_POS_OK = "WL Pos OK ?"
    REFOCUS = "Refocus"
    DETECT_CIRCLE = "Detect circle"
    MEASURE = "Measure"
    MOVE_OPT_WL = "Move Opt WL"
    CHECK_OPT_WL = "Check Opt WL"
    FINISHED = "Finished"
    ERROR = "Error"
    STOPPED = "Stopped"


# ================================================================================================== ROIs
@dataclass(frozen=True)
class SpotRoi:
    """A disc (``inner_radius`` < 0) or an annulus, pixels."""

    x: float
    y: float
    radius: float
    inner_radius: float = -1.0

    def mask(self, shape: tuple[int, ...]) -> np.ndarray:
        return oval_mask(shape, self.x, self.y, self.radius, self.inner_radius)


@dataclass(frozen=True)
class WavelengthRois:
    """The two ROIs of ``tool_alox_wavelength_ROI_selection``: NON OXYD (central spot) and OXYD."""

    non_oxyd: SpotRoi
    oxyd: SpotRoi


def wavelength_rois(p: SearchWavelengthParams, circle: Circle | None, pixel_um: float) -> WavelengthRois:
    """``tool_alox_wavelength_ROI_selection`` (roi d, d1..d11) for the automatic ROIs; 9042 for an empty circle in
    SCAN mode. ``pixel_um`` = µm per pixel (1 for a calibration in pixels)."""
    if is_empty_circle(circle) and p.wl_mode is WLMode.SCAN:
        raise SearchWavelengthError(SW_ROI, "empty circle")
    c = circle if circle is not None else Circle(0.0, 0.0, 0.0)
    if p.roi_type is WLRoiType.SPOT_ANNULAR:
        r = p.annul_spot_size_um / 2.0 / pixel_um
        r1, r2 = p.annul_diam1_um / 2.0 / pixel_um, p.annul_diam2_um / 2.0 / pixel_um
        return WavelengthRois(SpotRoi(c.x, c.y, r), SpotRoi(c.x, c.y, max(r1, r2), min(r1, r2)))
    r = p.dual_spots_size_um / 2.0 / pixel_um
    d = p.dual_spots_distance_um / pixel_um
    a = math.radians(p.dual_spots_angle_deg)
    return WavelengthRois(SpotRoi(c.x, c.y, r), SpotRoi(c.x + d * math.cos(a), c.y - d * math.sin(a), r))


def less_far_and_found(found: bool, best: Circle, old: Circle, max_radius: float) -> bool:
    """``tool_alox_detectCircle_lessFar_and_found``: ``found AND distance(best, old) < max radius``."""
    return bool(found) and math.hypot(best.x - old.x, best.y - old.y) < max_radius


def refocus_due(wl: float, start: float, step_refocus: float) -> bool:
    """``Refocus`` selector (sm d34): ``R(WL − Start, Step Refocus) = 0 AND WL ≠ Start AND Step Refocus ≠ 0``
    (``Quotient & Remainder``: ``R = x − y·floor(x/y)``)."""
    if step_refocus == 0 or wl == start:
        return False
    x, y = wl - start, float(step_refocus)
    return x - y * math.floor(x / y) == 0


@dataclass(frozen=True)
class MesaLocation:
    """What the search uses from Measure Mesa (``alox_function_ll_measure_mesa`` report, pixels)."""

    mesa_found: bool
    oxyd_found: bool
    accuracy: float                      # "Diameters Measurements.Accuracy" (%): 100 required
    fit_diameter_px: float               # "Diameters Measurements.Fit": max distance of a recentring = Fit/2
    mesa: Circle
    oxyd: Circle
    circular: bool = True                # "Mesa Shape Type" = Circular

    @property
    def found(self) -> bool:
        return self.mesa_found and self.oxyd_found and self.accuracy == 100


# ================================================================================================== params, report
def _set(obj: object, name: str, value: Any) -> None:
    object.__setattr__(obj, name, value)


@dataclass(frozen=True)
class SearchWavelengthParams:
    """``alox_typedef_search_wavelength_function_parameters`` in cluster order, plus the worker's ``Circle In``
    (pixels) and ``Manual`` / ``Manual ROIs`` controls. Defaults = typedef defaults; :meth:`from_profile` = bundle
    defaults."""

    wl_mode: WLMode = WLMode.MANUAL
    manual_wl_nm: float = 0.0
    start_wl: float = 0.0
    end_wl: float = 0.0
    step_wl: float = 0.0
    min_contrast: float = 0.0
    roi_type: WLRoiType = WLRoiType.DUAL_SPOTS
    dual_spots_size_um: float = 0.0
    dual_spots_distance_um: float = 0.0
    dual_spots_angle_deg: float = 0.0
    annul_spot_size_um: float = 0.0
    annul_diam1_um: float = 0.0
    annul_diam2_um: float = 0.0
    show_ui: bool = False
    overlay_rois: bool = False
    overlay_results: bool = False
    overlay_oxid_measurement: bool = False
    detect_circle_method: DetectCircleMethod = DetectCircleMethod.SINGLE
    step_refocus: int = 0
    wl_shift_nm: int = 0
    circle_in: Circle | None = None
    manual_rois: WavelengthRois | None = None    # used when given ("Manual" TRUE): no automatic ROI

    def __post_init__(self) -> None:
        _set(self, "wl_mode", WLMode.coerce(self.wl_mode, "WL Mode", SW_MONOCHROMATOR))
        _set(self, "roi_type", WLRoiType.coerce(self.roi_type, "WL ROI Type", SW_ROI))
        _set(self, "detect_circle_method",
             DetectCircleMethod.coerce(self.detect_circle_method, "DETECT CIRCLE METHOD", SW_ROI))
        for name in ("manual_wl_nm", "start_wl", "end_wl", "step_wl", "min_contrast", "dual_spots_size_um",
                     "dual_spots_distance_um", "dual_spots_angle_deg", "annul_spot_size_um", "annul_diam1_um",
                     "annul_diam2_um"):
            _set(self, name, float(getattr(self, name)))
        for name in ("show_ui", "overlay_rois", "overlay_results", "overlay_oxid_measurement"):
            _set(self, name, as_bool(getattr(self, name)))
        _set(self, "step_refocus", int(self.step_refocus))
        _set(self, "wl_shift_nm", int(self.wl_shift_nm))
        if self.circle_in is not None and not isinstance(self.circle_in, Circle):
            _set(self, "circle_in", Circle(*self.circle_in))

    @classmethod
    def from_profile(cls, profile: Any, *, circle_in: Circle | None = None) -> SearchWavelengthParams:
        """The keys of ``alox_function_ll_search_wavelength_bundle_profile_parameters d`` with its defaults."""
        g = profile.get
        return cls(
            wl_mode=g("wavelength.wlmode", 0), manual_wl_nm=g("wavelength.wlmanual", 0.0),
            start_wl=g("wavelength.start", DEFAULT_START_WL), end_wl=g("wavelength.end", 950.0), step_wl=g("wavelength.step", 2.0),
            min_contrast=g("wavelength.contrast", 20.0), roi_type=g("wavelength.spotautoroitype", 0),
            dual_spots_size_um=g("wavelength.spotsize", 2.0), dual_spots_distance_um=g("wavelength.distance", 5.0),
            dual_spots_angle_deg=g("wavelength.angle", 45.0), annul_spot_size_um=g("wavelength.annulspotsize", 2.0),
            annul_diam1_um=g("wavelength.annuldiameter1", 20.0), annul_diam2_um=g("wavelength.annuldiameter2", 30.0),
            show_ui=g("wavelength.showwindow", False), overlay_rois=g("wavelength.overlayzones", False),
            overlay_results=g("wavelength.overlayresults", False),
            overlay_oxid_measurement=g("wavelength.overlayoxidmeas", False),
            detect_circle_method=g("wavelength.detectcirclemethod", 0), step_refocus=g("wavelength.steprefocus", 0),
            wl_shift_nm=g("wavelength.wlshift", 0), circle_in=circle_in)


@dataclass(frozen=True)
class SpectrumPoint:
    """One measured wavelength (the NOX, OX and CONTRAST plots of the spectrum graph, and the exposure)."""

    wl_nm: float
    nox: float
    ox: float
    contrast: float
    exposure_us: float


@dataclass(frozen=True)
class SearchWavelengthReport:
    """``alox_typedef_search_wavelength_function_report`` (cluster order; the graphs as data)."""

    current_wl_nm: float = 0.0
    current_expo_us: float = 0.0
    current_contrast: float = 0.0
    optimal_wl_nm: float = 0.0
    optimal_expo_us: float = 0.0
    optimal_contrast: float = 0.0
    min_contrast: float = 0.0
    progress: float = 0.0
    valid: bool = False
    spectrum: tuple[SpectrumPoint, ...] = ()
    histogram_nox: tuple[int, ...] = ()
    histogram_ox: tuple[int, ...] = ()
    error_ref_count: int = 0


@dataclass(frozen=True)
class SearchWavelengthOptions:
    """Server-side settings. Defaults = the LabVIEW constants."""

    camera: str = "highmag"
    wait_rule: WaitRule = WaitRule.FRESH_FRAME     # owner B3; AS_DRAWN = Get Image's own wait
    init_wl_pause_s: float = 1.0                 # Init WL (SCAN): after the first Get Image
    refocus_pause_s: float = 1.0                 # "Wait 1s" after a refocus
    max_refocus_errors: int = 5
    poll_s: float = 0.010                        # loop wait of the worker (5-10 ms) while a position is checked
    position_timeout_s: float = 30.0             # NEW: WL Pos OK ? / Check Opt WL (LabVIEW: no limit)
    newest_frame: bool = True
    image_folder: str | Path | None = None       # "images wavelength"; None = <data>/images/Misc
    data_folder: str | Path | None = None        # "files wavelength"; None = <data>/files/wavelength


Acquire = Callable[[FunctionContext, str], np.ndarray]
RefocusHook = Callable[[FunctionContext], bool]
Locate = Callable[[FunctionContext, np.ndarray], "MesaLocation | None"]


def wavelength_data_text(spectrum: tuple[SpectrumPoint, ...] | list[SpectrumPoint]) -> str:
    """``tools_save_wavelength_data`` (``.wal``): header ``WL NOXYD OXYD CONTRAST``, one tab-delimited row per
    wavelength (``%.3f``, CRLF)."""
    lines = ["WL\tNOXYD\tOXYD\tCONTRAST"]
    lines += [f"{p.wl_nm:.3f}\t{p.nox:.3f}\t{p.ox:.3f}\t{p.contrast:.3f}" for p in spectrum]
    return "\r\n".join(lines) + "\r\n"


# ================================================================================================== the function
class SearchWavelength(MeasurementFunction):
    """Search Wavelength (map Part 3 §3.9). ``run(params, ctx)`` returns the :class:`SearchWavelengthReport`;
    raises :class:`FunctionFailed` after ``Error`` (initial wavelength restored), :class:`SearchWavelengthError` for
    refused parameters (no motion), :class:`alox.core.functions.Stopped` after ``Stopped``. ``params`` None =
    :meth:`SearchWavelengthParams.from_profile`."""

    name = FunctionName.SEARCH_WAVELENGTH

    def __init__(self, services: Services, *, options: SearchWavelengthOptions | None = None,
                 acquire: Acquire | None = None, refocus: RefocusHook | None = None,
                 locate: Locate | None = None) -> None:
        self.services = services
        self.options = options or SearchWavelengthOptions()
        self._acquire = acquire
        self._refocus = refocus
        self._locate = locate
        require_devices(services, self.options.camera, "wl")

    def check_params(self, params: SearchWavelengthParams | None) -> SearchWavelengthParams:
        p = SearchWavelengthParams.from_profile(self.services.profile) if params is None else params
        if not isinstance(p, SearchWavelengthParams):
            raise SearchWavelengthError(SW_MONOCHROMATOR, f"SearchWavelengthParams expected, got {type(p).__name__}")
        if p.wl_mode is WLMode.MANUAL:
            if not (math.isfinite(p.manual_wl_nm) and p.manual_wl_nm >= 0):
                raise SearchWavelengthError(SW_MONOCHROMATOR, f"Manual WL (nm): {p.manual_wl_nm!r}")
            return p
        for name in ("start_wl", "end_wl", "min_contrast"):
            if not math.isfinite(getattr(p, name)):
                raise SearchWavelengthError(SW_MONOCHROMATOR, f"{name} must be finite")
        if not (math.isfinite(p.step_wl) and p.step_wl > 0):
            raise SearchWavelengthError(SW_MONOCHROMATOR, f"Step WL must be > 0, got {p.step_wl!r}")
        if p.manual_rois is None:
            sizes = (("annul_spot_size_um", "annul_diam1_um", "annul_diam2_um") if p.roi_type is
                     WLRoiType.SPOT_ANNULAR else ("dual_spots_size_um",))
            for name in sizes:
                if not (math.isfinite(getattr(p, name)) and getattr(p, name) > 0):
                    raise SearchWavelengthError(SW_ROI, f"{name} must be > 0, got {getattr(p, name)!r}")
        return p

    def run(self, params: SearchWavelengthParams | None, ctx: FunctionContext) -> SearchWavelengthReport:
        return _SWJob(self, self.check_params(params), ctx).execute()

    # ---------------------------------------------------------------------------------------------- hooks
    def refocus(self, ctx: FunctionContext) -> bool:
        """LL Refocus Run Once (High Mag, profile ``refocus.*``); True when valid."""
        if self._refocus is not None:
            return bool(self._refocus(ctx))
        if registered(ctx, FunctionName.REFOCUS) is None:
            raise SearchWavelengthError(SW_CAMERA, "refocus function not available")
        from alox.functions.focus import CameraType, RefocusParams

        g = self.services.profile.get
        params = RefocusParams(camera_type=CameraType.HIGH_MAG, method=int(g("refocus.method", 0)),
                               steps=int(g("refocus.nbsteps", 4)), step_mm=float(g("refocus.step", 0.05)),
                               fin_roi_width=int(g("refocus.roiwidth", 128)),
                               fin_roi_height=int(g("refocus.roiheight", 128)), wait_ms=int(g("refocus.waitms", 0)))
        result = ctx.run_once(FunctionName.REFOCUS, params)
        return getattr(result, "best_index", 0) is not None

    def locate(self, ctx: FunctionContext, pixels: np.ndarray) -> MesaLocation | None:
        return None if self._locate is None else self._locate(ctx, pixels)


@dataclass
class _Session:
    init_wl: int = 0
    init_wl_ok: bool = False
    wl: float = 0.0                       # Current Wavelength
    exposure: float = 0.0                 # Current Exposure (table)
    circle: Circle | None = None          # Circle In (shift register)
    rois: WavelengthRois | None = None
    pixels: np.ndarray | None = None
    spectrum: list[SpectrumPoint] = field(default_factory=list)
    distances: list[float] = field(default_factory=list)
    error_ref_count: int = 0
    move_t0: float = 0.0
    last_grab_wl: int | None = None


class _SWJob:
    def __init__(self, function: SearchWavelength, p: SearchWavelengthParams, ctx: FunctionContext) -> None:
        self.f = function
        self.s = function.services
        self.o = function.options
        self.p = p
        self.ctx = ctx
        self.pause = Pauser(self.s, ctx)
        self.ss = _Session(circle=p.circle_in)
        self.report = SearchWavelengthReport(min_contrast=p.min_contrast)
        self.error: BaseException | None = None
        self.failed_state = ""

    # ---------------------------------------------------------------------------------------------- loop
    def execute(self) -> SearchWavelengthReport:
        handlers: dict[SWState, Callable[[], SWState]] = {
            SWState.START: self.start, SWState.STARTED: self.started, SWState.INIT_WL: self.init_wl,
            SWState.MOVE_WL: self.move_wl, SWState.WL_POS_OK: self.wl_pos_ok, SWState.REFOCUS: self.refocus,
            SWState.DETECT_CIRCLE: self.detect_circle, SWState.MEASURE: self.measure,
            SWState.MOVE_OPT_WL: self.move_opt_wl, SWState.CHECK_OPT_WL: self.check_opt_wl}
        state = SWState.START
        self.publish(state)
        try:
            while state not in (SWState.FINISHED, SWState.ERROR):
                self.ctx.check()
                nxt = handlers[state]()
                if nxt is not state:
                    self.publish(nxt)
                state = nxt
        except Stopped:
            self.end(SWState.STOPPED)
            raise
        if state is SWState.ERROR:
            self.end(SWState.ERROR)
            code, source = error_code_source(self.error) if self.error else (SW_ROI, TEXTS[SW_ROI])
            raise FunctionFailed(code, source, state=self.failed_state, report=self.report) from self.error
        self.end(SWState.FINISHED)
        return self.report

    def publish(self, state: SWState, **data: Any) -> None:
        extra: dict[str, Any] = {}
        if state is SWState.ERROR and self.error is not None:
            code, source = error_code_source(self.error)
            extra = {"code": code, "error": source, "failed_state": self.failed_state}
        self.ctx.set_state(str(state), progress=self.report.progress, wl_nm=self.ss.wl, **extra, **data)

    def fail(self, exc: BaseException, state: SWState) -> SWState:
        self.error = exc
        self.failed_state = str(state)
        log.warning("search wavelength: %s failed: %s", state, exc)
        if self.s.log is not None:
            self.s.log.error(f"Search Wavelength ({state}): {exc}")
        return SWState.ERROR

    # ---------------------------------------------------------------------------------------------- devices
    @property
    def wl(self) -> Any:
        return self.s.device("wl")

    @property
    def camera(self) -> Any:
        return self.s.camera(self.o.camera)

    def exposure_mode(self) -> int:
        """The Get Image profile's Exposure Mode (0 Fixed, 1 Auto, 2 Current)."""
        try:
            return int(self.s.profile.get(f"get.image.{self.o.camera}.exposuremode", 0))
        except (TypeError, ValueError):
            return 0

    def table_exposure(self, wl: float) -> float:
        """Exposure(WL) of ``Move WL`` / ``Move Opt WL``: "Auto" → ``Get Auto Expo``, else ``Get Exposure(WL)``."""
        if self.exposure_mode() == 1:
            th, data = self.s.exposures.auto_expo(round(wl))
            return data.auto_expo if math.isfinite(data.auto_expo) else th
        return float(self.s.exposures.exposure_for(round(wl)))

    def acquire(self) -> np.ndarray:
        """Get Image Run Once (profile parameters of the High Mag camera); see the module docstring for the wait."""
        if self.f._acquire is not None:
            return self.f._acquire(self.ctx, self.o.camera)
        rule = self.o.wait_rule
        if rule is WaitRule.AS_DRAWN and registered(self.ctx, FunctionName.GET_IMAGE) is not None:
            return grab_image(self.s, self.ctx, self.o.camera, None, newest=self.o.newest_frame)
        wl = getattr(self.wl, "last_position", None)
        camera = self.camera
        old = new = float(camera.exposure())
        changed = False
        if self.exposure_mode() == 0 and wl is not None:              # Get Image "Fixed": the table exposure
            new = float(self.s.exposures.exposure_for(int(wl)))
            tolerance = float(self.s.settings.get(f"hardware.camera.{self.o.camera}.exposuretolerance", 0.0))
            changed = not (new - tolerance <= old <= new + tolerance)
            if changed:
                camera.set_exposure(new)
        if rule in (WaitRule.AS_DRAWN, WaitRule.GET_IMAGE):          # Get Image: only after an exposure change
            if changed:
                self.pause(settle_s(WaitRule.GET_IMAGE, old, new))
        elif changed or wl != self.ss.last_grab_wl:                   # B3 / changelog: also after a WL change
            self.pause(settle_s(rule, old, new, float(getattr(camera, "frame_period_s", 0.0) or 0.0)))
        self.ss.last_grab_wl = wl
        return grab_image(self.s, self.ctx, self.o.camera, "Current", newest=self.o.newest_frame)

    def check_serial(self) -> None:
        """monochromator ``Get Serial``: empty or failing → 9041."""
        serial = self.wl.serial()
        if not serial:
            raise SearchWavelengthError(SW_MONOCHROMATOR, "no serial number")

    # ---------------------------------------------------------------------------------------------- states
    # "Init State machine here ..." (sm d4)
    def start(self) -> SWState:
        ss, p = self.ss, self.p
        ss.wl = p.manual_wl_nm if p.wl_mode is WLMode.MANUAL else p.start_wl
        if self.s.log is not None and p.wl_mode is WLMode.SCAN:
            self.s.log.info(f"Wavelength (Start: {p.start_wl:.0f} nm. End: {p.end_wl:.0f} nm)")
        return SWState.STARTED

    # "Post start initialization ... Check hardware (monochromator, camera) status" (sm d14)
    def started(self) -> SWState:
        try:
            self.check_serial()
            self.ss.init_wl, self.ss.init_wl_ok = int(self.wl.position()), True
        except LV_ERRORS as exc:
            return self.fail(_wrap(exc, SW_MONOCHROMATOR, "Init"), SWState.STARTED)
        try:
            self.camera.exposure()
        except LV_ERRORS as exc:
            return self.fail(_wrap(exc, SW_CAMERA, "Init"), SWState.STARTED)
        return SWState.INIT_WL

    def _goto(self, wl: float) -> None:
        self.wl.goto(round(wl))
        self.ss.move_t0 = self.s.clock()

    # "Move to next WL Position" (sm d19..d23)
    def init_wl(self) -> SWState:
        ss = self.ss
        try:
            self._goto(ss.wl)
            ss.exposure = self.table_exposure(ss.wl)
            if self.p.wl_mode is WLMode.SCAN:
                self.acquire()                                       # "to set exposure time"
                self.pause(self.o.init_wl_pause_s)
        except LV_ERRORS as exc:
            return self.fail(_wrap(exc, SW_MONOCHROMATOR, "Move WL"), SWState.INIT_WL)
        return SWState.MOVE_WL

    # "Move to next WL Position" (sm d24..d28)
    def move_wl(self) -> SWState:
        ss = self.ss
        try:
            self._goto(ss.wl)
            ss.exposure = self.table_exposure(ss.wl)
        except LV_ERRORS as exc:
            return self.fail(_wrap(exc, SW_MONOCHROMATOR, f"Move WL {ss.wl:g}"), SWState.MOVE_WL)
        return SWState.WL_POS_OK

    def _position_is(self, target: float, state: SWState) -> bool | SWState:
        """``Get Position == U32(target)``; a failure → 9041; too long → 9040 (fix)."""
        try:
            reached = int(self.wl.position()) == round(target)
        except LV_ERRORS as exc:
            return self.fail(_wrap(exc, SW_MONOCHROMATOR, "WL Pos Check"), state)
        if reached:
            return True
        if self.s.clock() - self.ss.move_t0 > self.o.position_timeout_s:
            return self.fail(SearchWavelengthError(SW_TIMEOUT, f"{target:g} nm not reached"), state)
        self.pause(self.o.poll_s)
        return False

    # "Check WL Position" (sm d29)
    def wl_pos_ok(self) -> SWState:
        try:
            self.check_serial()
        except LV_ERRORS as exc:
            return self.fail(_wrap(exc, SW_MONOCHROMATOR, "WL Pos Check"), SWState.WL_POS_OK)
        reached = self._position_is(self.ss.wl, SWState.WL_POS_OK)
        if isinstance(reached, SWState):
            return reached
        if not reached:
            return SWState.WL_POS_OK
        return SWState.MOVE_OPT_WL if self.p.wl_mode is WLMode.MANUAL else SWState.REFOCUS

    # "Refocus / Refocus OXYD" (sm d34..d47)
    def refocus(self) -> SWState:
        ss, p = self.ss, self.p
        if not refocus_due(ss.wl, p.start_wl, p.step_refocus):
            return SWState.DETECT_CIRCLE                             # "Refocus later"
        ok, error = False, None
        try:
            self.acquire()
            ok = self.f.refocus(self.ctx)
        except LV_ERRORS as exc:
            error = exc
            log.warning("search wavelength: refocus at %g nm failed: %s", ss.wl, exc)
        if ok:
            ss.error_ref_count, nxt = 0, SWState.DETECT_CIRCLE       # "Jump Next State", "Reset counters"
        elif ss.error_ref_count >= self.o.max_refocus_errors:
            nxt = self.fail(error or SearchWavelengthError(SW_CAMERA, "refocus not valid"), SWState.REFOCUS)
        else:
            ss.error_ref_count += 1                                  # "Loop to prev. state"
            nxt = SWState.REFOCUS
        self.report = replace(self.report, error_ref_count=ss.error_ref_count)
        self.pause(self.o.refocus_pause_s)                           # "Wait 1s"
        return nxt

    # "Detect circle" (sm d0, d48..d58)
    def detect_circle(self) -> SWState:
        ss, p = self.ss, self.p
        try:
            ss.pixels = self.acquire()
        except LV_ERRORS as exc:
            return self.fail(_wrap(exc, SW_CAMERA, "Get Image"), SWState.DETECT_CIRCLE)
        detect_now = p.detect_circle_method is DetectCircleMethod.FULL or ss.wl == p.start_wl
        if detect_now:                                               # SINGLE: start WL only; FULL: every WL
            try:
                location = self.f.locate(self.ctx, ss.pixels)
            except LV_ERRORS as exc:
                log.warning("search wavelength: measure mesa failed: %s", exc)
                location = None
            if location is not None and location.circular:
                self._recenter(location)
        try:
            if p.manual_rois is not None:
                ss.rois = p.manual_rois
            else:
                px = pixel_size_um(self.s.settings, self.o.camera, pixels_as_one=True)
                ss.rois = wavelength_rois(p, ss.circle, px)
        except LV_ERRORS as exc:
            return self.fail(_wrap(exc, SW_ROI), SWState.DETECT_CIRCLE)
        return SWState.MEASURE

    def _recenter(self, loc: MesaLocation) -> None:
        ss = self.ss
        distance = math.hypot(abs(loc.mesa.x - loc.oxyd.x), abs(loc.mesa.y - loc.oxyd.y))
        ss.distances.append(distance)
        best = loc.oxyd if (distance < 1 and loc.found) else loc.mesa
        old = ss.circle if ss.circle is not None else Circle(0.0, 0.0, 0.0)
        if less_far_and_found(loc.found, best, old, loc.fit_diameter_px / 2.0):
            ss.circle = best                                         # "Detect circles recentered"
            log.debug("search wavelength: circle recentred at (%.1f, %.1f)", best.x, best.y)

    # "Measure contrast" (sm d59)
    def measure(self) -> SWState:
        ss, p, rois = self.ss, self.p, self.ss.rois
        assert ss.pixels is not None and rois is not None
        pixels = ss.pixels
        nox_mask, ox_mask = rois.non_oxyd.mask(pixels.shape), rois.oxyd.mask(pixels.shape)
        nox, ox = masked_mean(pixels, nox_mask), masked_mean(pixels, ox_mask)
        contrast = ox - nox                                          # "CONTRAST = OX - NOX"
        ss.spectrum.append(SpectrumPoint(ss.wl, nox, ox, contrast, ss.exposure))
        contrasts = [pt.contrast for pt in ss.spectrum]
        negative = p.min_contrast < 0                                # "Min Contrast sign ?"
        opt = int(np.nanargmin(contrasts) if negative else np.nanargmax(contrasts)) \
            if not all(math.isnan(c) for c in contrasts) else 0
        opt_contrast = contrasts[opt]
        valid = opt_contrast <= p.min_contrast if negative else opt_contrast > p.min_contrast
        span = p.end_wl - p.start_wl
        progress = (ss.wl - p.start_wl + 1.0) / span * 100.0 if span else math.inf
        self.report = replace(
            self.report, current_wl_nm=ss.wl, current_expo_us=ss.exposure, current_contrast=contrast,
            optimal_wl_nm=ss.spectrum[opt].wl_nm + p.wl_shift_nm, optimal_expo_us=0.0, optimal_contrast=opt_contrast,
            min_contrast=p.min_contrast, progress=progress, valid=bool(valid), spectrum=tuple(ss.spectrum),
            histogram_nox=_histogram(pixels, nox_mask), histogram_ox=_histogram(pixels, ox_mask),
            error_ref_count=ss.error_ref_count)
        self._overlay()
        self._save_image(pixels)
        self.publish(SWState.MEASURE, event="measured", report=_light(self.report))
        nxt = ss.wl + p.step_wl                                      # ">> Next position >>"
        if nxt > p.end_wl:                                           # "Finished ?"
            return SWState.MOVE_OPT_WL
        ss.wl = nxt
        return SWState.MOVE_WL

    # "Update some function results", "Move to optimal wl position" (sm d68..d74)
    def move_opt_wl(self) -> SWState:
        ss, p = self.ss, self.p
        try:
            if p.wl_mode is WLMode.MANUAL:                          # "did not enter 'Measure' state"
                self.report = replace(self.report, current_wl_nm=ss.wl, current_expo_us=ss.exposure,
                                      optimal_wl_nm=ss.wl, optimal_expo_us=ss.exposure, valid=True, progress=100.0)
            else:
                self.report = replace(self.report, optimal_expo_us=self.table_exposure(self.report.optimal_wl_nm))
            self.check_serial()
            self._goto(self.report.optimal_wl_nm)
        except LV_ERRORS as exc:
            return self.fail(_wrap(exc, SW_MONOCHROMATOR, f"Set WL {self.report.optimal_wl_nm:g}"),
                             SWState.MOVE_OPT_WL)
        return SWState.CHECK_OPT_WL

    # "Check Optimal wavelength position" (sm d75)
    def check_opt_wl(self) -> SWState:
        target = self.report.optimal_wl_nm
        reached = self._position_is(target, SWState.CHECK_OPT_WL)
        if isinstance(reached, SWState):
            return reached
        if not reached:
            return SWState.CHECK_OPT_WL
        try:
            self.camera.set_exposure(float(self.s.exposures.exposure_for(round(target))))
        except LV_ERRORS as exc:
            return self.fail(_wrap(exc, SW_CAMERA, "Check Opt.WL"), SWState.CHECK_OPT_WL)
        if not self.report.valid:
            return self.fail(SearchWavelengthError(
                SW_ROI, f"contrast {self.report.optimal_contrast:.2f} not beyond Min. Contrast "
                f"{self.p.min_contrast:g}"), SWState.CHECK_OPT_WL)
        return SWState.FINISHED

    # ---------------------------------------------------------------------------------------------- end states
    def end(self, state: SWState) -> None:
        """``Finished`` (data), ``Error`` / ``Stopped`` (initial wavelength restored, data)."""
        ss = self.ss
        if state is not SWState.FINISHED and ss.init_wl_ok:
            try:
                self.wl.goto(ss.init_wl)                             # "Wavelength (Restore WL)"
            except LV_ERRORS as exc:
                log.warning("search wavelength: restoring %d nm failed: %s", ss.init_wl, exc)
        self._save_data()
        if state is SWState.FINISHED:
            self.ctx.set_state(str(state), event="finished", progress=self.report.progress,
                               report=_light(self.report))
        elif state is SWState.STOPPED:
            self.ctx.set_state(str(state))

    def _overlay(self) -> None:
        rois, r = self.ss.rois, self.report
        shapes: list[Any] = []
        if self.p.overlay_rois and rois is not None:
            for roi, color in ((rois.non_oxyd, (0, 0, 255)), (rois.oxyd, (255, 0, 0))):
                shapes.append(CircleShape(roi.x, roi.y, roi.radius, color=color, width=2))
                if roi.inner_radius >= 0:
                    shapes.append(CircleShape(roi.x, roi.y, roi.inner_radius, color=color, width=2))
        if self.p.overlay_results:
            color = (0, 255, 0) if r.valid else (255, 0, 0)
            text = (f"WL: {r.current_wl_nm:.0f} nm  CONTRAST: {r.current_contrast:.1f}\n"
                    f"OPT. WL: {r.optimal_wl_nm:.0f} nm  CONTRAST: {r.optimal_contrast:.1f}")
            shapes.append(Text(50, 50, text, color=color, size=24))
        self.s.images.set_overlay("wavelength", shapes)

    def _save_image(self, pixels: np.ndarray) -> None:
        """``log.savewavelengthimage`` in SCAN mode: ``<images wavelength>/<wl>nm_<expo>us.png``."""
        if self.p.wl_mode is not WLMode.SCAN or not as_bool(self.s.settings.get("log.savewavelengthimage", False)):
            return
        folder = self.o.image_folder
        if folder is None:
            if self.s.folders is None:
                return
            folder = self.s.folders.image_folder("Misc")
        path = Path(folder) / f"{round(self.ss.wl)}nm_{round(self.ss.exposure)}us.png"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            write_png(path, np.asarray(pixels))
        except (OSError, ValueError) as exc:
            log.warning("Search Wavelength (image) %s: %s", path, exc)

    def _save_data(self) -> None:
        """``log.savewavelengthdata`` in SCAN mode (Finished, Error): ``tools_save_wavelength_data`` ``.wal``
        (``<files wavelength>/<yymmdd_HHMMSS>_wavelength.wal``, name inferred)."""
        if self.p.wl_mode is not WLMode.SCAN or not as_bool(self.s.settings.get("log.savewavelengthdata", False)):
            return
        if not self.ss.spectrum:
            return
        folder = self.o.data_folder
        if folder is None:
            if self.s.folders is None:
                return
            folder = self.s.folders.file_folder("wavelength")
        path = Path(folder) / (timestamp_name(local_now(), "wavelength") + ".wal")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(wavelength_data_text(self.ss.spectrum), encoding="cp1252", newline="")
        except OSError as exc:
            log.warning("Search Wavelength (Log data) %s: %s", path, exc)


def _histogram(pixels: np.ndarray, mask: np.ndarray) -> tuple[int, ...]:
    values = np.asarray(pixels)[mask]
    if not values.size:
        return ()
    return tuple(int(v) for v in np.bincount(values.astype(np.int64).ravel(), minlength=256))


def _light(report: SearchWavelengthReport) -> dict[str, Any]:
    """The report for an event: without the histograms."""
    data = asdict(report)
    data.pop("histogram_nox", None)
    data.pop("histogram_ox", None)
    return data
