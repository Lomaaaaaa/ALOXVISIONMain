"""Auto Exposure (``alox_function_ll_auto_exposure*``) as a measurement function of the integrated application.

Spec: ``docs/vision-core/map.md`` Part 3 §2.1 (common pattern), §3.8 (this function), Part 4 §3.3 (circle → ROI), §3.6
(``geometry_simple_line_equation_from_two_points``), §3.9 (exposure table, ``tools_alox_autoexpo_WL_selection``);
``docs/vision-core/monochromator.md`` §6.3 and Q5 (wait after a change). Citations ``<vi> dN`` are the block-diagram
images of the HTML export: ``sm`` = ``alox_function_ll_auto_exposure_state_machine``, ``bundle`` =
``alox_function_ll_auto_exposure_bundle_profile_parameters``, ``wlsel`` = ``tools_alox_autoexpo_WL_selection``.

The worker finds, for the current wavelength or for every wavelength of the exposure table, the camera exposure that
gives ``Target Brightness`` (mean grey level) in a circular ROI, and stores it in the exposure table. It always uses
the High Mag camera (red label "LOW ANGLE / HIGH MAG", sm d0). One state per loop iteration, ``ctx.check()`` between
states, ``ctx.set_state`` on every change (LabVIEW "Auto Expo State Changed" event).

Algorithm per wavelength, as drawn:

1. ``Set Wavelength`` (sm d0): at the first wavelength of "All Wavelengths" the list is the table wavelengths inside
   the Search Wavelength range ``[Start WL, End WL]`` (wlsel d); monochromator ``Set Position``; error → 9094.
2. ``Check Wavelength`` (sm d41): position read back every 250 ms until it equals the target.
3. ``Set Exposure`` (sm d47, d48): measure 1 at ``Exposure Min``, measure 2 at ``Exposure Max`` (from the profile in
   "Current Wavelength" mode, from the table's ExpoMin / ExpoMax in "All Wavelengths" mode), then 100 ms.
4. ``Wait`` (sm d63..d71): ``E/1000 · (2 if E > 20 000 µs else 10) + 200 ms`` after measure 1, ``+ 500 ms`` after
   measure 2 (E in µs); a NaN exposure → 9093.
5. ``Measure Expo`` (sm d74..d79): mean grey level in the ROI circle (diameter ``ROI Diameter (µm)`` / pixel size,
   centred on the image, or on ``Circle In`` when ``BypassCircle``). Brightness > 120 → retry with a rescaled exposure
   (measure 1: ×0.5, or ×0.01 when > 200, coerced to [50, 1 999 800] µs; measure 2: ×0.5 coerced to
   [177, 1 998 800] µs, or 5000 µs when > 200), at most 5 retries in all.
6. ``Measure Finished`` (sm d82): line through **(0, 0)** and (Exposure2, Brightness2)
   (:func:`simple_line_from_two_points`; measure 1 is not used, "Measure brightness 1: not used", sm d76);
   ``Exposure = coerce((Target − b)/a, 35, 1 999 800)``; the report's Exposure1/2 become the exposures for brightness
   50 and 100 (10 000 / 40 000 when both clamp to the same value), Brightness1/2 = 50 / 100 (ExpoMin, ExpoMax, BMin,
   BMax of the table); ``Found Exposure`` = the slope is finite; not found → Exposure = initial exposure, log 9091.
7. ``Set Auto Expo`` (sm d91..d127): apply the exposure (wait ``E/1000·2 + 100 ms``), table "Set Auto Expo" at the
   wavelength, table "Save calib values", grab and display the result with the "autoexposure" overlay, optional PNG
   ``<data>/images/autoexpo/<yymmdd_HHMMSS>_autoexpo.png`` (``log.saveautoexpoimages``) and ``.ae`` data
   (``log.saveautoexpodata``), apply the exposure again ("Restore framerate"); next wavelength or ``Finished``.
8. ``Finished`` (sm d128..d143): restore the initial wavelength, grab and display. ``Error`` / ``Stopped`` (sm d144,
   d159): restore the initial wavelength, grab and display.

Reproduced (LabVIEW): states, order and transitions; the profile keys and defaults (bundle d); the measurement
constants above (120/200 grey levels, factors, coercion limits, 5 retries, the 250 ms / 100 ms / Wait-state pauses);
the slope through the origin and the absolute-value slope of ``geometry_simple_line_equation_from_two_points``;
``BypassCircle`` TRUE = measure in ``Circle In`` (sm d74, d79: ``Select(NOT BypassCircle, image centre, Circle In)``;
the map's wording "unless BypassCircle" is reversed) and 9089 when it is TRUE with an empty circle (sm d12);
"Save calib values" after every wavelength; the restore of the initial wavelength in ``Finished``, ``Error`` and
``Stopped``; the report fields (``workin wl(nm)`` = the next wavelength of the list, 0 after the last, sm d0).

Fixed (internal defects, or no observable effect):

- ``Check Wavelength`` looped forever when the position never matched (monochromator D11): bounded by
  ``AutoExposureOptions.wl_check_timeout_s`` (error 9094).
- Set Exposure 2 took the report's Exposure2 (0, or the previous wavelength's) whenever a retry had happened on
  measure 1 (shared ``Measures Retry > 0`` test, sm d47): each measure uses its own rescaled exposure only after its
  own retry.
- Measure 1 used a circle constant with only the radius set (centre from the constant, sm d76): it uses the same
  circle as measure 2.
- After 5 failed retries on measure 2, LabVIEW went to ``Finished`` through ``Set Exposure`` ("2.." case) without
  updating anything and reported success: now error 9091 "brightness saturation" (initial wavelength and exposure
  restored).
- "Auto exposure not found" reached ``Error`` with no error code (sm d99, d103): error 9090 "not found"; an empty
  wavelength list (empty table) set the monochromator to 0 nm: error 9092 before any move.
- ``Error`` / ``Stopped`` restored only the wavelength (sm d148 "should also restore exposure time"): the initial
  exposure is restored too (``AutoExposureOptions.restore_exposure``).
- "Save calib values" copied a NaN ExpoAuto into ExpoTh for wavelengths never auto-exposed (Get Image then failed
  with 9014): only finite ExpoAuto values are copied (``AutoExposureOptions.calib_finite_only``).
- In "Current Wavelength" mode ``wl(nm)`` stayed 0 (only ``Set Wavelength`` writes it): it is the current
  wavelength.
- Invalid parameters (ROI diameter ≤ 0, Exposure Min/Max ≤ 0 in "Current Wavelength" mode, non-finite values) are
  refused before any motion with 9089 / 9093 (LabVIEW measured an empty ROI).

Choices of this implementation:

- **Images**: as LabVIEW, ``alox_function_ll_get_image`` Run Once (``ctx.run_once(GET_IMAGE)``, the profile's High
  Mag parameters with Exposure Mode "Current" during the measures, the profile's mode for the final display) when
  the Get Image function is registered; else the newest frame of the camera device, stored with
  :meth:`ImageSink.put`. ``acquire=`` injects another grabber.
- **Waits** (owner decision B3, monochromator Q5): :class:`WaitRule` ``FRESH_FRAME`` (default) = after each
  exposure change (the ``Wait`` state, which follows every wavelength change, and "Wait Next Frame") wait
  ``old + new exposure`` (:func:`fresh_frame_wait_s`) so that the newest frame was exposed entirely after the
  change; ``AS_DRAWN`` = the Wait-state formula above and ``2·E + 100 ms``; ``CHANGELOG`` = ``2·max(old, new)``;
  ``GET_IMAGE`` = ``100 ms + 0.11·max(old, new)``. The 250 ms of ``Check Wavelength`` and the 100 ms of
  ``Set Exposure`` are kept as drawn.
- Default range start 700 nm (owner decision D4; LabVIEW 750). Only ``goto`` / ``position`` / ``serial`` of the
  monochromator are used (owner decision B4: Reset, Scan, Calibrate, Select Grating stay maintenance tools).
- Pixel size: ``hardware.camera.highmag.pixelsize`` / ``units`` (0 pixels, 1 µm, 2 mm: ×1000), as
  ``IMAQ Get Calibration Info`` of the grabbed image (Get Image calibrates it from the same settings).
- The "Show Window" run panel and the LabVIEW UI events are replaced by ``ctx.set_state`` data
  (``event`` = ``started``, ``measure_finished``, ``finished``) for the websocket clients.
- The "All Wavelengths" range comes from the Search Wavelength profile (``wavelength.start`` / ``wavelength.end``);
  when no table wavelength lies inside it, the table wavelengths between the ones nearest to Start and End are used
  (wlsel d: nearest-table handling, simplified).
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from enum import IntEnum, StrEnum
from pathlib import Path
from typing import Any, Self

import numpy as np

from alox.core.exposures import AutoExposureData, ExposureError
from alox.core.functions import (
    FunctionContext,
    FunctionName,
    MeasurementFunction,
    Stopped,
)
from alox.devices.base import DeviceError
from alox.functions.services import Circle as CircleShape
from alox.functions.services import Services, Text
from alox.vision.circles import Circle
from aloxcam.errors import AloxError, cluster_of
from aloxcam.imaging.png import write_png

__all__ = [
    "AEState",
    "AutoExposure",
    "AutoExposureError",
    "AutoExposureMode",
    "AutoExposureOptions",
    "AutoExposureParams",
    "AutoExposureReport",
    "FunctionFailed",
    "WaitRule",
    "ae_wait_s",
    "fresh_frame_wait_s",
    "grab_image",
    "is_empty_circle",
    "masked_mean",
    "oval_mask",
    "pixel_size_um",
    "select_wavelengths",
    "settle_s",
    "simple_line_from_two_points",
]

log = logging.getLogger(__name__)

# error codes (vc_text/translations/alox_en.lang)
AE_ROI = 9089                 # auto-exposure error (image/region of interest)
AE_NOT_FOUND = 9090           # auto-exposure error (not found)
AE_SATURATION = 9091          # auto-exposure error (brightness saturation)
AE_DATA = 9092                # auto-exposure error (data not available)
AE_WAVELENGTH = 9093          # auto-exposure error (invalid wavelength)
AE_MONOCHROMATOR = 9094       # auto-exposure error (monochromator)
CAMERA_MODULE = 9401          # "Camera/Z module error" (sm d24)

TEXTS = {
    AE_ROI: "auto-exposure error (image/region of interest)",
    AE_NOT_FOUND: "auto-exposure error (not found)",
    AE_SATURATION: "auto-exposure error (brightness saturation)",
    AE_DATA: "auto-exposure error (data not available)",
    AE_WAVELENGTH: "auto-exposure error (invalid wavelength)",
    AE_MONOCHROMATOR: "auto-exposure error (monochromator)",
    CAMERA_MODULE: "camera/z stage module command timeout",
}

#: Lowest default wavelength (owner decision D4: the default tables start at 700 nm; LabVIEW's default was 750).
DEFAULT_START_WL = 700.0

#: Errors the state machines turn into their ``Error`` state.
LV_ERRORS = (DeviceError, AloxError, ExposureError, LookupError)


# ================================================================================================== shared helpers
class WaitRule(StrEnum):
    """Wait before using a frame after an exposure or wavelength change (monochromator.md Q5, questions B3)."""

    FRESH_FRAME = "fresh_frame"  # owner decision B3 (default): :func:`fresh_frame_wait_s`, then the newest frame
    AS_DRAWN = "as_drawn"        # what the function's own diagrams do
    CHANGELOG = "changelog"      # 2 × max(old, new exposure) (changelog 1.0.0.11)
    GET_IMAGE = "get_image"      # 100 ms + 0.11 × max(old, new exposure) (monochromator.md §6.3, Get Image)


def fresh_frame_wait_s(old_us: float, new_us: float) -> float:
    """Owner decision B3 ("wait to get a new image according to the exposure"): after a wavelength or exposure change
    the image used must be exposed entirely after the change. The frame being exposed at the change ends at most
    ``old`` µs later and the next one takes ``new`` µs, so after ``old + new`` the newest frame
    (``Camera.grab(newest=True)``) started after the change. An unknown old exposure counts as the new one. Seconds.
    Shared helper (the HL mesa function can reuse it)."""
    new = _finite(new_us)
    old = float(old_us) if math.isfinite(old_us) else new
    return (max(old, 0.0) + max(new, 0.0)) / 1e6


def settle_s(rule: WaitRule, old_us: float, new_us: float) -> float:
    """The wait of ``rule`` in seconds for exposures in µs (``AS_DRAWN`` is resolved by the caller; here it gives the
    Get Image formula of monochromator.md §6.3)."""
    if rule is WaitRule.FRESH_FRAME:
        return fresh_frame_wait_s(old_us, new_us)
    worst = max(_finite(old_us), _finite(new_us))
    if rule is WaitRule.CHANGELOG:
        return 2.0 * worst / 1e6
    return 0.100 + 0.11 * worst / 1e6


def ae_wait_s(exposure_us: float, measure: int) -> float:
    """The ``Wait`` state as drawn (sm d63..d71): ``E/1000 · (2 if E > 20 000 µs else 10) + 200 ms`` (measure 1) or
    ``+ 500 ms`` (measure 2)."""
    factor = 2.0 if exposure_us > 20000.0 else 10.0
    return (exposure_us / 1000.0 * factor + (200.0 if measure == 1 else 500.0)) / 1000.0


def _finite(value: float) -> float:
    return float(value) if math.isfinite(value) else 0.0


def pixel_size_um(settings: Any, camera: str = "highmag", *, pixels_as_one: bool = False) -> float:
    """µm per pixel from ``hardware.camera.<camera>.pixelsize`` / ``units`` (0 pixels, 1 µm, 2 mm → ×1000).
    ``pixels_as_one``: a calibration in pixels gives 1 (``tool_alox_wavelength_ROI_selection`` "pixels" case);
    otherwise the pixel size is used as is (auto exposure: ``Select(unit = mm, px·1000, px)``)."""
    size = float(settings.get(f"hardware.camera.{camera}.pixelsize", 1.0))
    units = settings.get(f"hardware.camera.{camera}.units", 0)
    try:
        unit = int(units)
    except (TypeError, ValueError):
        unit = {"pixels": 0, "um": 1, "µm": 1, "mm": 2}.get(str(units).strip().lower(), 0)
    if unit == 2:
        return size * 1000.0
    if unit == 0 and pixels_as_one:
        return 1.0
    return size


def oval_mask(shape: tuple[int, ...], cx: float, cy: float, r: float, r_in: float = -1.0) -> np.ndarray:
    """``alox_vision_circle_to_roi`` + ``IMAQ ROIToMask``: the oval inscribed in the I32 bounding box
    ``[cx − r, cy − r, cx + r, cy + r]`` (NI rectangles exclude right/bottom), minus the disc of radius ``r_in``
    (annulus, ``IMAQ Convert Annulus to ROI``) when ``r_in`` ≥ 0."""
    h, w = int(shape[0]), int(shape[1])
    if not (math.isfinite(cx) and math.isfinite(cy) and math.isfinite(r)) or r <= 0:
        return np.zeros((h, w), dtype=bool)
    left, top, right, bottom = (round(v) for v in (cx - r, cy - r, cx + r, cy + r))
    if right <= left or bottom <= top:
        return np.zeros((h, w), dtype=bool)
    ecx, ecy = (left + right - 1) / 2.0, (top + bottom - 1) / 2.0
    ax, ay = (right - left) / 2.0, (bottom - top) / 2.0
    yy, xx = np.mgrid[0:h, 0:w]
    inside = ((xx - ecx) / ax) ** 2 + ((yy - ecy) / ay) ** 2 <= 1.0
    if r_in >= 0:
        inside &= (xx - cx) ** 2 + (yy - cy) ** 2 >= r_in * r_in
    return inside


def masked_mean(pixels: np.ndarray, mask: np.ndarray) -> float:
    """``IMAQ Histogram`` "Mean Value" inside ``mask`` (NaN for an empty mask)."""
    values = np.asarray(pixels)[mask]
    return float(values.mean()) if values.size else math.nan


def simple_line_from_two_points(p1: Sequence[float], p2: Sequence[float]) -> tuple[float, float]:
    """``geometry_simple_line_equation_from_two_points`` (map Part 4 §3.6): ``a = |y2 − y1| / |x2 − x1|`` (always
    ≥ 0, ±Inf / NaN when ``x1 = x2``), ``b = y1 − a·x1``."""
    (x1, y1), (x2, y2) = p1, p2
    dx, dy = abs(float(x2) - float(x1)), abs(float(y2) - float(y1))
    with np.errstate(divide="ignore", invalid="ignore"):
        a = float(np.float64(dy) / np.float64(dx))
    b = float(y1) - a * float(x1) if math.isfinite(a) else math.nan
    return a, b


def is_empty_circle(circle: Circle | None) -> bool:
    """The LabVIEW ``Circle In == {0, 0, 0}`` test (None counts as empty)."""
    return circle is None or (circle.x == 0 and circle.y == 0 and circle.radius == 0)


def select_wavelengths(table: Sequence[float], start: float, end: float) -> list[int]:
    """``tools_alox_autoexpo_WL_selection`` "All Wavelengths" (wlsel d): the table wavelengths in ``[start, end]``;
    when none, the ones between the table wavelengths nearest to ``start`` and ``end`` (simplified)."""
    wls = [round(w) for w in table]
    lo, hi = min(start, end), max(start, end)
    inside = [w for w in wls if lo <= w <= hi]
    if inside or not wls:
        return inside
    i = min(range(len(wls)), key=lambda k: abs(wls[k] - lo))
    j = min(range(len(wls)), key=lambda k: abs(wls[k] - hi))
    return wls[min(i, j):max(i, j) + 1]


def require_devices(services: Services, *names: str) -> None:
    """:class:`LookupError` when a device is missing (the registry then leaves the function unregistered)."""
    for name in names:
        if name not in services.devices:
            raise LookupError(f"device {name!r} is not available")


def registered(ctx: FunctionContext, name: FunctionName) -> Any:
    """The function ``name`` of the runner, None when it is not registered."""
    try:
        return ctx.runner.function(name)
    except KeyError:
        return None


def grab_image(services: Services, ctx: FunctionContext, camera: str, exposure_mode: Any = None, *,
               newest: bool = True) -> np.ndarray:
    """LabVIEW's "Get Image Run Once": the Get Image function with the profile's parameters of ``camera``
    (``params_for``) and ``exposure_mode`` when given; without a registered Get Image function, the newest frame of
    the camera device, stored with :meth:`ImageSink.put`."""
    function = registered(ctx, FunctionName.GET_IMAGE)
    if function is not None and hasattr(function, "params_for"):
        params = function.params_for(camera)
        if exposure_mode is not None:
            from alox.functions.get_image import ExposureMode

            params = replace(params, exposure_mode=ExposureMode.coerce(exposure_mode))
        return np.asarray(ctx.run_once(FunctionName.GET_IMAGE, params).pixels)
    image = services.camera(camera).grab(newest=newest)
    services.images.put(camera, image.pixels, metadata={"function": ctx.name.topic})
    return np.asarray(image.pixels)


class FunctionFailed(AloxError):
    """The run reached ``Error``: ``code``/``source`` of the failure, ``state`` that failed, ``report`` at that
    moment (after the restore)."""

    def __init__(self, code: int, source: str, *, state: str, report: Any) -> None:
        super().__init__(code, source)
        self.state = state
        self.report = report


class AutoExposureError(AloxError):
    """An auto-exposure error 9089..9094 / 9401 raised inside a state (and refused parameters)."""

    def __init__(self, code: int, detail: str = "") -> None:
        super().__init__(code, TEXTS.get(code, "auto-exposure error") + (f": {detail}" if detail else ""))


def error_code_source(exc: BaseException) -> tuple[int, str]:
    if isinstance(exc, DeviceError):
        return (int(exc.code) if exc.code is not None else 9500), str(exc)
    if isinstance(exc, ExposureError):
        return (int(exc.code) if exc.code is not None else 9500), str(exc)
    cluster = cluster_of(exc)
    return cluster.code, cluster.source


class LabelledEnum(IntEnum):
    """An enum whose wire code is its value and whose LabVIEW item text is :attr:`label`."""

    @property
    def label(self) -> str:
        return self._labels()[self.value]

    @classmethod
    def _labels(cls) -> tuple[str, ...]:
        raise NotImplementedError

    @classmethod
    def coerce(cls, value: Any, what: str, code: int) -> Self:
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            text = value.strip()
            for member in cls:
                if text.upper() in (member.label.upper(), member.name):
                    return member
        elif isinstance(value, (int, np.integer, float)) and not isinstance(value, bool):
            try:
                return cls(int(value))
            except ValueError:
                pass
        raise AloxError(code, f"{what}: invalid value {value!r}")


def as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().upper() in ("TRUE", "1", "YES", "T")
    return bool(value)


class Pauser:
    """Pauses through ``services.sleep`` (tests) or ``ctx.sleep`` (woken at once by a stop)."""

    def __init__(self, services: Services, ctx: FunctionContext) -> None:
        self.services, self.ctx = services, ctx

    def __call__(self, seconds: float) -> None:
        if seconds <= 0:
            return
        if self.services.sleep is time.sleep:
            self.ctx.sleep(seconds)
        else:
            self.ctx.check()
            self.services.sleep(seconds)


def local_now() -> datetime:
    return datetime.now(UTC).astimezone()


def timestamp_name(when: datetime, suffix: str) -> str:
    """``%y%m%d_%H%M%S_<suffix>`` (sm d121)."""
    return when.strftime("%y%m%d_%H%M%S_") + suffix


# ================================================================================================== enums, params
class AutoExposureMode(LabelledEnum):
    """``alox_typedef_auto_exposure_mode``."""

    CURRENT_WAVELENGTH = 0
    ALL_WAVELENGTHS = 1

    @classmethod
    def _labels(cls) -> tuple[str, ...]:
        return ("Current Wavelength", "All Wavelengths")


class AEState(StrEnum):
    """``alox_typedef_auto_exposure_function_state``."""

    START = "Start"
    STARTED = "Started"
    SET_WAVELENGTH = "Set Wavelength"
    CHECK_WAVELENGTH = "Check Wavelength"
    SET_EXPOSURE = "Set Exposure"
    WAIT = "Wait"
    MEASURE_EXPO = "Measure Expo"
    MEASURE_FINISHED = "Measure Finished"
    SET_AUTO_EXPO = "Set Auto Expo"
    FINISHED = "Finished"
    ERROR = "Error"
    STOPPED = "Stopped"


def _set(obj: object, name: str, value: Any) -> None:
    object.__setattr__(obj, name, value)


@dataclass(frozen=True)
class AutoExposureParams:
    """``alox_typedef_auto_exposure_function_parameters`` in cluster order, plus ``circle_in`` (the worker's
    ``Circle In`` control, pixels; used when ``bypass_circle``). Defaults = the typedef defaults;
    :meth:`from_profile` = the bundle VI defaults."""

    mode: AutoExposureMode = AutoExposureMode.CURRENT_WAVELENGTH
    exposure_min_us: float = 0.0
    exposure_max_us: float = 0.0
    target_brightness: int = 0
    roi_diameter_um: float = 0.0
    show_window: bool = False
    overlay_roi: bool = False
    overlay_result: bool = False
    bypass_circle: bool = False
    circle_in: Circle | None = None

    def __post_init__(self) -> None:
        _set(self, "mode", AutoExposureMode.coerce(self.mode, "Mode", AE_DATA))
        for name in ("exposure_min_us", "exposure_max_us", "roi_diameter_um"):
            _set(self, name, float(getattr(self, name)))
        _set(self, "target_brightness", int(self.target_brightness))
        for name in ("show_window", "overlay_roi", "overlay_result", "bypass_circle"):
            _set(self, name, as_bool(getattr(self, name)))
        c = self.circle_in
        if c is not None and not isinstance(c, Circle):
            _set(self, "circle_in", Circle(*c))

    @classmethod
    def from_profile(cls, profile: Any, *, circle_in: Circle | None = None) -> AutoExposureParams:
        """The keys of ``alox_function_ll_auto_exposure_bundle_profile_parameters d`` with its defaults."""
        g = profile.get
        return cls(mode=g("autoexpo.mode", 0), exposure_min_us=g("autoexpo.exposurelow", 5000.0),
                   exposure_max_us=g("autoexpo.exposurehigh", 15000.0),
                   target_brightness=g("autoexpo.targetbrightness", 128),
                   roi_diameter_um=g("autoexpo.roidiameter", 100.0), show_window=g("autoexpo.showwindow", False),
                   overlay_roi=g("autoexpo.overlayroi", False), overlay_result=g("autoexpo.overlayresults", False),
                   bypass_circle=g("autoexpo.bypassCircleIn", False), circle_in=circle_in)


@dataclass(frozen=True)
class AutoExposureReport:
    """``alox_typedef_auto_exposure_report`` (cluster order)."""

    exposure1: float = 0.0
    brightness1: float = 0.0
    exposure2: float = 0.0
    brightness2: float = 0.0
    exposure: float = 0.0
    brightness: float = 0.0
    found_exposure: bool = False
    process_time_ms: float = 0.0
    wl_nm: int = 0
    working_wl_nm: int = 0


@dataclass(frozen=True)
class AutoExposureOptions:
    """Server-side settings (never client-visible). Defaults = the LabVIEW constants."""

    camera: str = "highmag"                       # "AUTO EXPOSURE ALWAYS USE LOW ANGLE/HIGH MAG CAMERA"
    retry_brightness: float = 120.0               # brightness above which a measure is retried
    saturated_brightness: float = 200.0
    max_retries: int = 5
    retry_factor: float = 0.5
    saturated_factor1: float = 0.01               # measure 1, brightness > 200
    saturated_exposure2_us: float = 5000.0        # measure 2, brightness > 200
    retry_limits1_us: tuple[float, float] = (50.0, 1999800.0)
    retry_limits2_us: tuple[float, float] = (177.0, 1998800.0)
    exposure_limits_us: tuple[float, float] = (35.0, 1999800.0)   # the solution (Measure Finished)
    table_brightness: tuple[float, float] = (50.0, 100.0)         # BMin, BMax written to the table
    default_table_exposures_us: tuple[float, float] = (10000.0, 40000.0)
    wl_check_period_s: float = 0.250
    wl_check_timeout_s: float = 30.0              # NEW (LabVIEW: no limit)
    set_exposure_pause_s: float = 0.100
    wait_rule: WaitRule = WaitRule.FRESH_FRAME     # owner B3; AS_DRAWN = the Wait-state formula of the diagram
    restore_exposure: bool = True                 # fix: Error / Stopped also restore the initial exposure
    calib_finite_only: bool = True                # fix: "Save calib values" skips NaN ExpoAuto
    newest_frame: bool = True


# ================================================================================================== the function
Acquire = Callable[[FunctionContext, str], np.ndarray]


class AutoExposure(MeasurementFunction):
    """Auto Exposure (map Part 3 §3.8). ``run(params, ctx)`` returns the last :class:`AutoExposureReport`; raises
    :class:`FunctionFailed` after ``Error`` (initial wavelength and exposure restored), :class:`AutoExposureError`
    for refused parameters (no motion), :class:`alox.core.functions.Stopped` after ``Stopped``. ``params`` None =
    :meth:`AutoExposureParams.from_profile` of the active profile."""

    name = FunctionName.AUTO_EXPOSURE

    def __init__(self, services: Services, *, options: AutoExposureOptions | None = None,
                 acquire: Acquire | None = None) -> None:
        self.services = services
        self.options = options or AutoExposureOptions()
        self._acquire = acquire
        require_devices(services, self.options.camera, "wl")

    def check_params(self, params: AutoExposureParams | None) -> AutoExposureParams:
        p = AutoExposureParams.from_profile(self.services.profile) if params is None else params
        if not isinstance(p, AutoExposureParams):
            raise AutoExposureError(AE_DATA, f"AutoExposureParams expected, got {type(p).__name__}")
        if not (math.isfinite(p.roi_diameter_um) and p.roi_diameter_um > 0):
            raise AutoExposureError(AE_ROI, f"ROI Diameter (µm) must be > 0, got {p.roi_diameter_um!r}")
        if p.mode is AutoExposureMode.CURRENT_WAVELENGTH:
            for name in ("exposure_min_us", "exposure_max_us"):
                value = getattr(p, name)
                if not (math.isfinite(value) and value > 0):
                    raise AutoExposureError(AE_WAVELENGTH, f"{name} must be > 0, got {value!r}")
        return p

    def run(self, params: AutoExposureParams | None, ctx: FunctionContext) -> AutoExposureReport:
        return _AEJob(self, self.check_params(params), ctx).execute()

    def acquire(self, ctx: FunctionContext, exposure_mode: Any = "Current") -> np.ndarray:
        """One image of the High Mag camera: ``alox_function_ll_get_image`` Run Once with the profile's Get Image
        parameters and ``exposure_mode`` ("Current" during the measures, None = the profile's mode), or the
        ``acquire`` hook."""
        if self._acquire is not None:
            return self._acquire(ctx, self.options.camera)
        return grab_image(self.services, ctx, self.options.camera, exposure_mode, newest=self.options.newest_frame)


@dataclass
class _Session:
    """``alox_typedef_auto_exposure_function_session`` (the per-run shift registers)."""

    wavelengths: list[int] = field(default_factory=list)
    index: int = 0                   # Current WL Index
    count: int = 0                   # Measures Count
    retry: int = 0                   # Measures Retry
    retried: set[int] = field(default_factory=set)   # measures retried (fix of the shared test)
    t0: float = 0.0
    init_exposure: float = 0.0
    init_wl: int = 0
    init_expo_ok: bool = False
    init_wl_ok: bool = False
    check_t0: float | None = None


class _AEJob:
    def __init__(self, function: AutoExposure, p: AutoExposureParams, ctx: FunctionContext) -> None:
        self.f = function
        self.s = function.services
        self.o = function.options
        self.p = p
        self.ctx = ctx
        self.pause = Pauser(self.s, ctx)
        self.session = _Session()
        self.report = AutoExposureReport()
        self.error: BaseException | None = None
        self.failed_state = ""
        self.circle: Circle | None = None
        self.previous_exposure = math.nan       # camera exposure before the last Set Exposure (WaitRule)

    # ---------------------------------------------------------------------------------------------- loop
    def execute(self) -> AutoExposureReport:
        handlers: dict[AEState, Callable[[], AEState]] = {
            AEState.START: self.start, AEState.STARTED: self.started, AEState.SET_WAVELENGTH: self.set_wavelength,
            AEState.CHECK_WAVELENGTH: self.check_wavelength, AEState.SET_EXPOSURE: self.set_exposure,
            AEState.WAIT: self.wait, AEState.MEASURE_EXPO: self.measure_expo,
            AEState.MEASURE_FINISHED: self.measure_finished, AEState.SET_AUTO_EXPO: self.set_auto_expo}
        state = AEState.START
        self.publish(state)
        try:
            while state not in (AEState.FINISHED, AEState.ERROR):
                self.ctx.check()
                nxt = handlers[state]()
                if nxt is not state:
                    self.publish(nxt)
                state = nxt
        except Stopped:
            self.stopped()
            raise
        if state is AEState.ERROR:
            self.error_state()
            code, source = error_code_source(self.error) if self.error else (AE_NOT_FOUND, TEXTS[AE_NOT_FOUND])
            raise FunctionFailed(code, source, state=self.failed_state, report=self.report) from self.error
        self.finished()
        return self.report

    def publish(self, state: AEState, **data: Any) -> None:
        extra: dict[str, Any] = {}
        if state is AEState.ERROR and self.error is not None:
            code, source = error_code_source(self.error)
            extra = {"code": code, "error": source, "failed_state": self.failed_state}
        self.ctx.set_state(str(state), wl_nm=self.report.wl_nm, **extra, **data)

    def fail(self, exc: BaseException, state: AEState) -> AEState:
        self.error = exc
        self.failed_state = str(state)
        log.warning("auto exposure: %s failed: %s", state, exc)
        if self.s.log is not None:
            self.s.log.error(f"Auto-Exposure ({state}): {exc}")
        return AEState.ERROR

    # ---------------------------------------------------------------------------------------------- devices
    @property
    def camera(self) -> Any:
        return self.s.camera(self.o.camera)

    @property
    def wl(self) -> Any:
        return self.s.device("wl")

    def set_exposure_us(self, value: float) -> float:
        """``Set Camera Exposure`` then ``Get Camera Exposure`` (the value the camera accepted)."""
        camera = self.camera
        self.previous_exposure = float(camera.exposure())
        camera.set_exposure(float(value))
        return float(camera.exposure())

    def current_wavelength(self) -> int:
        """monochromator ``Get Last Position`` (a read when unknown)."""
        wl = self.wl
        last = getattr(wl, "last_position", None)
        return int(last) if last is not None else int(wl.position())

    def roi_circle(self, pixels: np.ndarray) -> Circle:
        """The ROI: ``ROI Diameter (µm)/2/pixel size`` around the image centre, or around ``Circle In`` when
        ``BypassCircle`` (sm d79)."""
        radius = self.p.roi_diameter_um / 2.0 / pixel_size_um(self.s.settings, self.o.camera)
        if self.p.bypass_circle and self.p.circle_in is not None:
            return Circle(self.p.circle_in.x, self.p.circle_in.y, radius)
        h, w = pixels.shape[:2]
        return Circle(w / 2.0, h / 2.0, radius)

    # ---------------------------------------------------------------------------------------------- states
    # "Init State machine here ..." (sm d5, d8)
    def start(self) -> AEState:
        if self.p.bypass_circle and is_empty_circle(self.p.circle_in):
            return self.fail(AutoExposureError(AE_ROI, "invalid input circle"), AEState.START)
        try:
            if self.p.mode is AutoExposureMode.CURRENT_WAVELENGTH:
                self.session.wavelengths = [self.current_wavelength()]
            else:
                self.session.wavelengths = list(self.s.exposures.wavelengths())
        except LV_ERRORS as exc:
            return self.fail(_wrap(exc, AE_MONOCHROMATOR), AEState.START)
        if not self.session.wavelengths:
            return self.fail(AutoExposureError(AE_DATA, "no wavelength in the exposure table"), AEState.START)
        self.session.t0 = self.s.clock()
        return AEState.STARTED

    # "Store Initial WL position & status", "Store Initial Exposure & status" (sm d16..d33)
    def started(self) -> AEState:
        ss = self.session
        try:
            serial = self.wl.serial()
            if not serial or serial == "N/A":
                raise AutoExposureError(AE_MONOCHROMATOR, "no serial number")
            ss.init_wl, ss.init_wl_ok = self.current_wavelength(), True
        except LV_ERRORS as exc:
            return self.fail(_wrap(exc, AE_MONOCHROMATOR), AEState.STARTED)
        try:
            ss.init_exposure, ss.init_expo_ok = float(self.camera.exposure()), True
        except LV_ERRORS as exc:
            return self.fail(_wrap(exc, CAMERA_MODULE), AEState.STARTED)
        if self.p.mode is AutoExposureMode.CURRENT_WAVELENGTH:
            self.report = replace(self.report, wl_nm=ss.wavelengths[0])                # fix: LabVIEW kept 0
            return AEState.SET_EXPOSURE
        return AEState.SET_WAVELENGTH

    # "Set Wavelength" (sm d0)
    def set_wavelength(self) -> AEState:
        ss = self.session
        if ss.index == 0:
            start = float(self.s.profile.get("wavelength.start", DEFAULT_START_WL))
            end = float(self.s.profile.get("wavelength.end", 950.0))
            ss.wavelengths = select_wavelengths(ss.wavelengths, start, end)
            if self.s.log is not None:
                self.s.log.info("Auto-Exposure WL table: " + "".join(f"{w}nm," for w in ss.wavelengths))
            if not ss.wavelengths:
                return self.fail(AutoExposureError(AE_DATA, f"no table wavelength in [{start}, {end}] nm"),
                                 AEState.SET_WAVELENGTH)
        target = ss.wavelengths[ss.index]
        nxt = ss.wavelengths[ss.index + 1] if ss.index + 1 < len(ss.wavelengths) else 0
        self.report = replace(self.report, wl_nm=target, working_wl_nm=nxt)
        try:
            self.wl.goto(target)
        except LV_ERRORS as exc:
            return self.fail(_wrap(exc, AE_MONOCHROMATOR, f"Set WL: {nxt}nm"), AEState.SET_WAVELENGTH)
        ss.check_t0 = self.s.clock()
        return AEState.CHECK_WAVELENGTH

    # "Check Wavelength position" (sm d41): read every 250 ms until equal
    def check_wavelength(self) -> AEState:
        ss = self.session
        target = ss.wavelengths[ss.index]
        try:
            reached = int(self.wl.position()) == target
        except LV_ERRORS as exc:
            return self.fail(_wrap(exc, AE_MONOCHROMATOR, f"Check WL NOK: {target}"), AEState.CHECK_WAVELENGTH)
        self.pause(self.o.wl_check_period_s)
        if reached:
            return AEState.SET_EXPOSURE
        if self.s.clock() - (ss.check_t0 or 0.0) > self.o.wl_check_timeout_s:          # fix: LabVIEW looped forever
            return self.fail(AutoExposureError(AE_MONOCHROMATOR, f"{target} nm not reached"),
                             AEState.CHECK_WAVELENGTH)
        return AEState.CHECK_WAVELENGTH

    def _bounds(self) -> tuple[float, float]:
        """Exposure Min / Max: the profile ("Current Wavelength") or the table's ExpoMin / ExpoMax (sm d48)."""
        if self.p.mode is AutoExposureMode.CURRENT_WAVELENGTH:
            return self.p.exposure_min_us, self.p.exposure_max_us
        _th, data = self.s.exposures.auto_expo(self.session.wavelengths[self.session.index])
        return data.expo_min, data.expo_max

    # "Set Exposure 1 (Min)" / "Set Exposure 2 (Max)" (sm d47, d48)
    def set_exposure(self) -> AEState:
        ss = self.session
        if ss.count >= 2:                                                            # "2..": unexpected
            return AEState.FINISHED
        measure = ss.count + 1
        try:
            low, high = self._bounds()
            if measure in ss.retried:
                value = self.report.exposure1 if measure == 1 else self.report.exposure2
            else:
                value = low if measure == 1 else high
            read = self.set_exposure_us(value)
        except LV_ERRORS as exc:
            what = "Set Expo Min" if measure == 1 else "Set Expo Max"
            return self.fail(_wrap(exc, CAMERA_MODULE, what), AEState.SET_EXPOSURE)
        if measure == 1:
            self.report = replace(self.report, exposure1=read)
        else:
            self.report = replace(self.report, exposure2=read)
        ss.count = measure
        self.pause(self.o.set_exposure_pause_s)
        return AEState.WAIT

    # "Wait 2 x Exposure Time (make sure the next frame will be correctly exposed)" (sm d62..d71)
    def wait(self) -> AEState:
        ss = self.session
        if ss.count not in (1, 2):
            return AEState.FINISHED
        exposure = self.report.exposure1 if ss.count == 1 else self.report.exposure2
        if math.isnan(exposure):
            return self.fail(AutoExposureError(AE_WAVELENGTH, "WL Error (NaN exposure)"), AEState.WAIT)
        if self.o.wait_rule is WaitRule.AS_DRAWN:
            seconds = ae_wait_s(exposure, ss.count)
        else:
            seconds = settle_s(self.o.wait_rule, self.previous_exposure, exposure)
        self.pause(seconds)
        return AEState.MEASURE_EXPO

    # "Measure brightness at two different exposures (dark/bright)" (sm d74..d79)
    def measure_expo(self) -> AEState:
        ss = self.session
        measure = ss.count
        if measure not in (1, 2):
            return AEState.FINISHED                                                  # "Unexpected state"
        try:
            pixels = self.f.acquire(self.ctx)
        except LV_ERRORS as exc:
            name = "Measure Brightness Min" if measure == 1 else "Measure Brightness Max"
            return self.fail(_wrap(exc, CAMERA_MODULE, name), AEState.MEASURE_EXPO)
        self.circle = self.roi_circle(pixels)
        c = self.circle
        mean = masked_mean(pixels, oval_mask(pixels.shape, c.x, c.y, c.radius))
        o = self.o
        retry = mean > o.retry_brightness
        saturated = mean > o.saturated_brightness
        if measure == 1:
            self.report = replace(self.report, brightness1=mean)
        else:
            self.report = replace(self.report, brightness2=mean)
        self.publish(AEState.MEASURE_EXPO, measure=measure, brightness=mean,
                     exposure=self.report.exposure1 if measure == 1 else self.report.exposure2)
        if not retry:
            return AEState.SET_EXPOSURE if measure == 1 else AEState.MEASURE_FINISHED
        if measure == 1:
            factor = o.saturated_factor1 if saturated else o.retry_factor
            lo, hi = o.retry_limits1_us
            self.report = replace(self.report, exposure1=min(max(self.report.exposure1 * factor, lo), hi))
        else:
            lo, hi = o.retry_limits2_us
            value = o.saturated_exposure2_us if saturated else min(max(self.report.exposure2 * o.retry_factor, lo), hi)
            self.report = replace(self.report, exposure2=value)
        ss.retry += 1
        if ss.retry > o.max_retries:
            if measure == 2:                                                         # fix: LabVIEW "finished"
                return self.fail(AutoExposureError(AE_SATURATION, f"brightness {mean:.1f} after {o.max_retries} "
                                                   "retries"), AEState.MEASURE_EXPO)
            return AEState.SET_EXPOSURE                                              # count kept: go to measure 2
        ss.retried.add(measure)
        ss.count = measure - 1
        return AEState.SET_EXPOSURE

    # "Measure Finished, compute auto exposure time for specified target brightness" (sm d82)
    def measure_finished(self) -> AEState:
        o, r, ss = self.o, self.report, self.session
        a, b = simple_line_from_two_points((0.0, 0.0), (r.exposure2, r.brightness2))
        valid = math.isfinite(a)                                                     # NOT(NaN OR ±Inf)
        lo, hi = o.exposure_limits_us

        def solve(y: float) -> float:
            with np.errstate(divide="ignore", invalid="ignore"):
                x = float((np.float64(y) - b) / np.float64(a))
            return x if math.isnan(x) else min(max(x, lo), hi)

        target = float(self.p.target_brightness)
        e_lo, e_hi = (solve(y) for y in o.table_brightness)
        if e_lo == e_hi:
            e_lo, e_hi = o.default_table_exposures_us
        exposure = solve(target) if valid else ss.init_exposure
        self.report = replace(
            r, exposure=exposure, brightness=target, found_exposure=valid, exposure1=e_lo, exposure2=e_hi,
            brightness1=o.table_brightness[0], brightness2=o.table_brightness[1],
            process_time_ms=(self.s.clock() - ss.t0) * 1000.0)
        if not valid:
            log.warning("Auto-Exposure, out of range brightness (%.1f, %.1f)", 0.0, r.brightness2)     # 9091 logged
            if self.s.log is not None:
                self.s.log.warning(f"Auto-Exposure, out of range brightness (0.0, {r.brightness2:.1f})")
        self.publish(AEState.MEASURE_FINISHED, event="measure_finished", report=asdict(self.report))
        return AEState.SET_AUTO_EXPO

    # "Store/Set found auto exposure time" (sm d91..d127)
    def set_auto_expo(self) -> AEState:
        ss, r = self.session, self.report
        wavelength = ss.wavelengths[ss.index]
        # frame 0: apply and/or store
        if not r.found_exposure:
            if self.p.mode is AutoExposureMode.CURRENT_WAVELENGTH:
                self._restore()                                                     # "restore original WL & exposure"
            return self.fail(AutoExposureError(AE_NOT_FOUND, f"{wavelength} nm"), AEState.SET_AUTO_EXPO)
        ss.index += 1
        ss.retry = ss.count = 0
        ss.retried.clear()
        nxt = AEState.FINISHED if ss.index >= len(ss.wavelengths) else AEState.SET_WAVELENGTH
        try:
            camera = self.camera
            old = float(camera.exposure())
            camera.set_exposure(r.exposure)
        except LV_ERRORS as exc:
            return self.fail(_wrap(exc, CAMERA_MODULE, "Set Auto-Exposure"), AEState.SET_AUTO_EXPO)
        if not math.isnan(r.exposure):                                              # "Wait Next Frame"
            as_drawn = (r.exposure / 1000.0 * 2.0 + 100.0) / 1000.0
            self.pause(as_drawn if self.o.wait_rule is WaitRule.AS_DRAWN else settle_s(self.o.wait_rule, old,
                                                                                       r.exposure))
        # frame 1: update the table; frame 2: "Save calib values"
        data = AutoExposureData(r.exposure, r.exposure1, r.exposure2, r.brightness1, r.brightness2)
        try:
            self.s.exposures.set_auto_expo(wavelength, data)
            self._save_calib_values()
        except LV_ERRORS as exc:                                                    # LabVIEW: logged, not fatal
            log.warning("Auto-Exposure (Update Auto-Expo. Table) %d nm: %s", wavelength, exc)
        # frame 3: grab with the new exposure, overlay, display; frames 4, 5: files; frame 6: exposure again
        try:
            pixels = self.f.acquire(self.ctx)
            self._overlay(pixels)
            self._save_image(pixels)
        except LV_ERRORS as exc:
            log.warning("Auto-Exposure (Set Auto-Expo.): %s", exc)
        self._save_data()
        try:
            self.camera.set_exposure(r.exposure)                                    # "Restore framerate"
        except LV_ERRORS as exc:
            log.warning("Auto-Exposure (Restore framerate): %s", exc)
        return nxt

    def _save_calib_values(self) -> None:
        table = self.s.exposures
        if not self.o.calib_finite_only:
            table.save_calib_values()
            return
        rows = table.table()
        if rows and all(len(row) >= 7 and math.isfinite(row[-1]) for row in rows):
            table.save_calib_values()
            return
        for row in rows:                                                            # fix: keep ExpoTh of NaN rows
            if len(row) > 1 and math.isfinite(row[-1]):
                row[1] = row[-1]
        table.store(rows)

    def _overlay(self, pixels: np.ndarray) -> None:
        shapes: list[Any] = []
        c = self.circle or self.roi_circle(pixels)
        if self.p.overlay_roi:
            shapes.append(CircleShape(c.x, c.y, c.radius, color=(0, 255, 0), width=2))
        if self.p.overlay_result:
            r = self.report
            ok = r.found_exposure
            text = f"AUTO-EXPOSURE: {r.exposure:.0f} µs" if ok else "AUTO-EXPOSURE NOT FOUND"
            shapes.append(Text(50, 50, text, color=(0, 255, 0) if ok else (255, 0, 0), size=24))
        self.s.images.set_overlay("autoexposure", shapes)

    def _save_image(self, pixels: np.ndarray) -> None:
        """``log.saveautoexpoimages`` (sm d119..d121): ``<data>/images/autoexpo/<yymmdd_HHMMSS>_autoexpo.png``."""
        folders = self.s.folders
        if folders is None or not as_bool(self.s.settings.get("log.saveautoexpoimages", False)):
            return
        path = Path(folders.image_folder("autoexpo")) / (timestamp_name(local_now(), "autoexpo") + ".png")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            write_png(path, np.asarray(pixels))
        except (OSError, ValueError) as exc:
            log.warning("Auto-Exposure (Log Image) %s: %s", path, exc)

    def _save_data(self) -> None:
        """``log.saveautoexpodata`` (sm d124, ``tools_save_auto_exposure_data``): the report values, tab-delimited,
        ``<data>/files/autoexpo/<yymmdd_HHMMSS>_autoexpo.ae`` (file name inferred)."""
        folders = self.s.folders
        if folders is None or not as_bool(self.s.settings.get("log.saveautoexpodata", False)):
            return
        path = Path(folders.file_folder("autoexpo")) / (timestamp_name(local_now(), "autoexpo") + ".ae")
        values = [float(v) for v in asdict(self.report).values()]
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\t".join(f"{v:.3f}" for v in values) + "\r\n", encoding="cp1252")
        except OSError as exc:
            log.warning("Auto-Exposure (Log Data) %s: %s", path, exc)

    # ---------------------------------------------------------------------------------------------- end states
    def _restore(self) -> None:
        """Initial wavelength (when read) and initial exposure (when read)."""
        ss = self.session
        if ss.init_wl_ok:
            try:
                self.wl.goto(ss.init_wl)
            except LV_ERRORS as exc:
                log.warning("Auto-Exposure (Restore Original WL) %d nm: %s", ss.init_wl, exc)
        if ss.init_expo_ok:
            try:
                self.camera.set_exposure(ss.init_exposure)
            except LV_ERRORS as exc:
                log.warning("Auto-Exposure (Restore Original Exposure) %g µs: %s", ss.init_exposure, exc)

    def _refresh_display(self) -> None:
        """"Get Image with found auto-exposure, update Display" (profile exposure mode)."""
        try:
            self.f.acquire(self.ctx, None)
        except LV_ERRORS as exc:
            log.debug("auto exposure: display refresh failed: %s", exc)

    def finished(self) -> None:
        """``Finished`` (sm d128..d143): restore the initial wavelength, grab with the found exposure."""
        ss = self.session
        if ss.init_wl_ok:
            try:
                self.wl.goto(ss.init_wl)
            except LV_ERRORS as exc:
                log.warning("Auto-Exposure (Restore Original WL) %d nm: %s", ss.init_wl, exc)
        self._refresh_display()
        self.ctx.set_state(str(AEState.FINISHED), event="finished", report=asdict(self.report))

    def error_state(self) -> None:
        """``Error`` (sm d144..d158): restore, refresh the display."""
        self._restore_for_end()
        self._refresh_display()

    def stopped(self) -> None:
        """``Stopped`` (sm d159..d173): as ``Error``, without the error."""
        self._restore_for_end()
        self.ctx.set_state(str(AEState.STOPPED))

    def _restore_for_end(self) -> None:
        ss = self.session
        if ss.init_wl_ok:
            try:
                self.wl.goto(ss.init_wl)
            except LV_ERRORS as exc:
                log.warning("Auto-Exposure (Restore Original WL) %d nm: %s", ss.init_wl, exc)
        if self.o.restore_exposure and ss.init_expo_ok:
            try:
                self.camera.set_exposure(ss.init_exposure)
            except LV_ERRORS as exc:
                log.warning("Auto-Exposure: restoring %g µs failed: %s", ss.init_exposure, exc)


def _wrap(exc: BaseException, code: int, detail: str = "") -> BaseException:
    """A device or table error as the function's LabVIEW code (``ALOX ERROR`` nodes), keeping the cause's text."""
    if isinstance(exc, AloxError):
        return exc
    error = AloxError(code, TEXTS.get(code, "error") + (f" ({detail})" if detail else "") + f": {exc}")
    error.__cause__ = exc
    return error
