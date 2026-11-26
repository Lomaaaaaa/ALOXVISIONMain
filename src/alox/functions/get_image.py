"""Get Image: the low-level function that acquires one processed image, and the live display that runs it continuously.

Spec: ``docs/vision-core/map.md`` Part 3 §2.1 (common pattern), §3.1 (Get Image), Part 4 §3.5 (overlays
``alox_vision_overlay_hardware_infos``, ``alox_vision_overlay_scale``, ``tools_best_scale_width``), typedefs
``alox_typedef_get_image_function_parameters`` / ``_state`` / ``_exposure_mode``; diagrams of
``alox_function_ll_get_image_state_machine`` (cited ``sm dN``), ``alox_function_ll_get_image`` (``wrapper dN``),
``alox_function_ll_get_image_bundle_profile_parameters d`` (``bundle d``), ``tools_alox_crop_size_definition d``,
``alox_vision_overlay_hardware_infos d..d11``, ``alox_vision_overlay_scale d..d4``, ``tools_best_scale_width d``.

The state machine runs in the runner's thread, one state per loop iteration, ``ctx.check()`` between states and
``ctx.set_state`` on every change (as :mod:`alox.functions.focus`): ``Start`` → ``Started`` → ``Setup Hardware`` →
``Acquire`` → ``Finished`` (``Error`` on a failure; a stop or an abort raises :class:`alox.core.functions.Stopped`).

What each state does (observed):

- ``Start`` (sm d8): system status 204 "Low-Level func running" when it was 500 and the call is not "Live Mode", back
  to 500 in ``Finished``/``Error``/``Stopped`` (sm d116, d123, d130). Here the core does it for the ``Get Image``
  command (``Core._cmd_get_image`` and its function-finished callback); a call from another function or from the live
  display leaves the status alone, as LabVIEW.
- ``Setup Hardware`` (sm d, d16..d39), only for Exposure Mode ``Fixed`` (the default case); the ``Auto`` and
  ``Current`` cases hold only disabled code (sm d40, d41, d57..d59): nothing is changed. Frame 1: current exposure
  and ``hardware.camera.<cam>.exposuretolerance``. Frame 2 (LED): Low Mag: read the LED, write ``Led``, wait 100 ms,
  "LED changed" = read ≠ ``Led``; High Mag: switch the low-mag LED off (sm d21, d24), no wait. Frame 3: the new
  exposure is ``Low Mag Exposure`` for the low-mag camera, else the exposure table at the monochromator's last
  position (``Get Exposure(WL)``); NaN → 9014; written only when the current one is outside ``new ± tolerance``
  (sm d27, log 008001/008101). Frame 4: when the LED or the exposure changed, wait ``max(new, old)`` (ms) + 100 ms
  for a valid frame (sm d30, d32; the ``× 0,11`` and the ``÷ 5`` of the 2000 ms coercion are not wired to the wait).
- ``Acquire`` (sm d75, "ACQ => ROI => SUBSAMPLING => BCG"): grab; ``ZOOM`` ≠ 1: centred crop by
  ``tools_alox_crop_size_definition`` + ``IMAQ Extract 2`` (sm d79): margins ``w − w/(2 − ZOOM)`` and ``h − h/(2 −
  ZOOM)`` on each side, so ``ZOOM`` < 1 enlarges the centre and ``ZOOM`` ≥ 1 keeps the whole image; (Brightness,
  Contrast, Gamma) ≠ (128, 45, 1): ``IMAQ BCGLookup`` (sm d81); Gain ∉ {0, 1}: multiplication (sm d87); then the
  overlay loop of 8 cases (sm d88..d112): 0 clear, 1 ``name``, 2 ``type`` + a filled 6 px dot at the image centre, 3
  ``timestamp``, 4 ``buffer`` (custom data of the camera path, text at ``(50, k · line space)``), 5 hardware infos,
  6 scale bar (``250`` px, multiple ``10``), 7 calibration. The ``Optional Rectangle`` is only tested (4 elements,
  width + height > 0), never applied (sm d75): kept as a parameter, not applied.
- Hardware infos (``alox_vision_overlay_hardware_infos d..d11``): 5 lines at x = 50, line k at ``H − 6·ls + k·ls``:
  ``X (mm): %.3f``, ``Y (mm): %.3f`` (XY in wafer coordinates), ``Z (mm): %.3f``, ``Expo (µs): %.1f``, ``WL (nm):
  %d``; ``-`` when a read fails. Scale (``alox_vision_overlay_scale d``, ``tools_best_scale_width d``): ``um = pixel
  size · 250``, ``nearest = ceil(um / 10) · 10``, ``bar = nearest · 250 / um`` px; double arrow from ``W − bar − 100``
  to ``W − 100`` at ``y = 0.98·H``; text ``"%d %s"`` (units ``pixels``/``µm``/``mm``) centred at ``H − 2·ls``.
- Text overlays use ``alox.ini [overlays]`` ``fontsize``, ``fontlinespace``, ``fontcolor`` (sm d14 "Get text overlays
  properties").
- Parameters from the profile (``bundle d``): ``get.image.<highmag|lowmag>.led`` (F), ``exposuremode`` (0 Fixed),
  ``exposure`` (1), ``roileft/roitop/roiwidth/roiheight`` (0), ``subsampling`` (1 = ZOOM), ``brightness`` (128),
  ``contrast`` (45), ``gamma`` (1), ``simulation`` (F), ``simulationfolder``, ``simulationtimer`` (0),
  ``overlayname/type/index/time/hwinfos/scale`` (F), ``gain`` (1); ``general.setLEDcam`` (Low Mag) = the camera that
  drives the LED.

Reproduced: the states and their order, the exposure decision and tolerance, the LED handling and its 100 ms, the
valid-frame wait, the processing chain and its conditions, the overlay texts, positions and formats, error codes
9010 (processing), 9012 (acquisition, when the device gives no code) and 9014 (invalid exposure).

Fixed:

- ``tools_alox_crop_size_definition`` returns ``[top, left, width, height]`` that ``IMAQ Extract 2`` reads as
  ``[left, top, right, bottom]``: X and Y margins were swapped on a non-square sensor; the crop is centred here.
  ``ZOOM`` ≤ 0 (an empty or reversed rectangle, an IMAQ error) is 9010.
- No 5 ms pause between states (sm d: ``Wait 5 ms`` per iteration); the live display has its own interval.
- The camera without lights: when the camera of ``Camera LEDcmd`` does not drive the LED but the other one does, the
  other one is used; when none does (a system without LED wiring), the LED step is skipped.

Not ported: ``Simulation`` (image files and the overlays stored in them, sm d88..d111): the simulated devices replace
it; the parameter is accepted and ignored with a warning. The functions status "Get Image Run Once" special case
(``alox_manager_functions_status d10..d12``, UI only).

Continuous Get Image (changelog 1.0.0.11: "'get image': now automatically runs continuously, when no other function is
running"; ``alox_function_ll_get_image`` description: "'Live Mode': acquires a live image (same as 'Run Once' but
without updating the function status). Used to display an image continuously when no other function is running"):
:class:`LiveView`. In LabVIEW the wrapper's ``Start`` runs the state machine with ``Run Loop`` = T (wrapper d5; after
``Finished`` or ``Error`` it goes back to ``Start``, sm d116, d123); the caller of the live mode was not found in the
diagrams read, so its period is a parameter (default 25 ms ≈ the 5 states × 5 ms of the LabVIEW loop).
"""

from __future__ import annotations

import logging
import math
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import Any, Self

import numpy as np

from alox.core.core import PIXEL_UNITS
from alox.core.functions import (
    FunctionContext,
    FunctionName,
    FunctionRunner,
    MeasurementFunction,
)
from alox.devices.base import DeviceError
from alox.functions.focus import CameraType
from alox.functions.services import (
    CAMERA_DEVICES,
    Circle,
    Line,
    Services,
    Shape,
    Text,
    camera_device_name,
)
from aloxcam.camera.pipeline import build_custom_data
from aloxcam.errors import AloxError, FocusError

__all__ = ["ExposureMode", "GIState", "GetImage", "GetImageError", "GetImageFailed", "GetImageOptions",
           "GetImageParams", "GetImageResult", "LiveView", "best_scale", "stage_to_wafer", "zoom_crop"]

log = logging.getLogger(__name__)

INVALID_ARGUMENTS = 9002            # "invalid arguments error"
PROCESSING_ERROR = 9010             # "get image error (processing)"
ACQUISITION_ERROR = 9012            # "get image error (acquisition)"
INVALID_EXPOSURE = 9014             # "get image error (invalid auto expo)"

DEFAULT_BCG = (128, 45, 1.0)        # IMAQ BCGLookup neutral values = the profile defaults (bundle d)
DEFAULT_FONT_COLOR = (0x33, 0x99, 0xFF)


# ================================================================================================== enums
class ExposureMode(IntEnum):
    """``alox_typedef_get_image_exposure_mode``."""

    FIXED = 0
    AUTO = 1
    CURRENT = 2

    @property
    def label(self) -> str:
        return self.name.capitalize()

    @classmethod
    def coerce(cls, value: Any) -> Self:
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            for member in cls:
                if value.strip().lower() in (member.label.lower(), member.name.lower()):
                    return member
        elif isinstance(value, int | np.integer) and not isinstance(value, bool):
            try:
                return cls(int(value))
            except ValueError:
                pass
        raise GetImageError(INVALID_ARGUMENTS, f"Exposure Mode: invalid value {value!r}")


class GIState(StrEnum):
    """``alox_typedef_get_image_function_state`` (published by ``ctx.set_state``)."""

    START = "Start"
    STARTED = "Started"
    SETUP_HARDWARE = "Setup Hardware"
    ACQUIRE = "Acquire"
    FINISHED = "Finished"
    ERROR = "Error"
    STOPPED = "Stopped"


class GetImageError(AloxError):
    """A Get Image error (codes 9002, 9009..9014 of ``alox_en.lang``, or the device's code)."""


class GetImageFailed(GetImageError):
    """The run reached ``Error``; ``state`` is the state that failed."""

    def __init__(self, code: int, source: str, *, state: str) -> None:
        super().__init__(code, source)
        self.state = state


def _camera_type(value: Any, what: str) -> CameraType:
    """A :class:`CameraType`, its code, its label or a device name (``"lowmag"``); else 9002."""
    try:
        if isinstance(value, str) and not isinstance(value, CameraType):
            value = CAMERA_DEVICES.index(camera_device_name(value))
        return CameraType.coerce(value, what)
    except (FocusError, ValueError):
        raise GetImageError(INVALID_ARGUMENTS, f"{what}: invalid value {value!r}") from None


# ================================================================================================== parameters
def _set(obj: object, name: str, value: Any) -> None:
    object.__setattr__(obj, name, value)


@dataclass(frozen=True)
class GetImageParams:
    """``Get Image Params`` in cluster order; defaults = the profile defaults of the bundle VI. Exposures in µs."""

    camera_type: CameraType = CameraType.HIGH_MAG
    led: bool = False
    exposure_mode: ExposureMode = ExposureMode.FIXED
    low_mag_exposure: float = 1.0
    optional_rectangle: tuple[int, ...] = (0, 0, 0, 0)     # [left, top, width, height]: tested, never applied
    zoom: float = 1.0
    brightness: int = 128
    contrast: int = 45
    gamma: float = 1.0
    simulation: bool = False                               # not ported (module docstring)
    simulation_path: str = ""
    simulation_timer_ms: int = 0
    overlay_name: bool = False
    overlay_type: bool = False
    overlay_timestamp: bool = False
    overlay_index: bool = False
    overlay_hw_infos: bool = False
    overlay_scale: bool = False
    led_camera: CameraType = CameraType.LOW_MAG            # "Camera LEDcmd" (general.setLEDcam)
    gain: float = 1.0

    def __post_init__(self) -> None:
        _set(self, "camera_type", _camera_type(self.camera_type, "Camera Type"))
        _set(self, "led_camera", _camera_type(self.led_camera, "Camera LEDcmd"))
        _set(self, "exposure_mode", ExposureMode.coerce(self.exposure_mode))
        try:
            for name in ("low_mag_exposure", "zoom", "gamma", "gain"):
                _set(self, name, float(getattr(self, name)))
            for name in ("brightness", "contrast", "simulation_timer_ms"):
                _set(self, name, int(getattr(self, name)))
            _set(self, "optional_rectangle", tuple(int(v) for v in (self.optional_rectangle or ())))
        except (TypeError, ValueError) as exc:
            raise GetImageError(INVALID_ARGUMENTS, f"Get Image parameters: {exc}") from None
        for name in ("led", "simulation", "overlay_name", "overlay_type", "overlay_timestamp", "overlay_index",
                     "overlay_hw_infos", "overlay_scale"):
            _set(self, name, bool(getattr(self, name)))

    @property
    def camera(self) -> str:
        """The device name (``highmag``/``lowmag``)."""
        return camera_device_name(int(self.camera_type))


@dataclass(frozen=True)
class GetImageOptions:
    """Server-side settings (defaults: the LabVIEW values)."""

    led_settle_s: float = 0.100            # Low Mag: after Set LED (sm d24)
    frame_margin_s: float = 0.100          # added to max(new, old) exposure before the first valid frame (sm d30)
    newest_frame: bool = True              # Camera.grab(newest=…)
    scale_approx_px: float = 250.0         # alox_vision_overlay_scale inputs (sm d108)
    scale_multiple: float = 10.0
    text_x: int = 50                       # "Start X" of the text overlays
    center_mark_px: int = 3                # the dot of the "type" overlay: X/2 ± 3, Y/2 ± 3 (sm d96)
    arrow_px: float = 10.0                 # arrow heads of the scale bar (alox_vision_overlay_line_with_arrows_simple)
    gain_on_camera: bool = True            # owner R15: the recipe Gain through the camera's gain, digital fallback


@dataclass(frozen=True)
class GetImageResult:
    """The processed image (``Get Image`` method of the wrapper): pixels, the camera device name, the camera's buffer
    number and time, the exposure, the metadata stored with it and the overlays drawn by Get Image."""

    pixels: np.ndarray
    camera: str
    number: int
    time: float
    exposure_us: float
    metadata: Mapping[str, Any] = field(default_factory=dict)
    overlays: Mapping[str, tuple[Shape, ...]] = field(default_factory=dict)


# ================================================================================================== pure helpers
def zoom_crop(pixels: np.ndarray, zoom: float) -> np.ndarray:
    """``tools_alox_crop_size_definition`` + ``IMAQ Extract 2`` (module docstring): margins ``round(w − w/(2 −
    zoom))`` on the left and right, same for Y; ``zoom`` ≥ 1 keeps the whole image; ``zoom`` ≤ 0 → :class:`ValueError`."""
    if not math.isfinite(zoom) or zoom <= 0:
        raise ValueError(f"ZOOM must be > 0, got {zoom!r}")
    if zoom >= 1:
        return pixels
    h, w = pixels.shape[:2]
    mx = max(0, round(w - w / (2.0 - zoom)))
    my = max(0, round(h - h / (2.0 - zoom)))
    if w - 2 * mx < 1 or h - 2 * my < 1:
        raise ValueError(f"ZOOM {zoom} leaves no pixel")
    return pixels[my:h - my, mx:w - mx]


def bcg_lookup(pixels: np.ndarray, brightness: int, contrast: int, gamma: float) -> np.ndarray:
    """``IMAQ BCGLookup`` on 8-bit values: ``out = (255·(p/255)^γ − 128)·tan(contrast°) + brightness``, clipped
    (neutral at 128, 45, 1; NI's exact table is not in the diagrams: **inferred** from its documentation)."""
    if not (math.isfinite(gamma) and gamma > 0) or not 0 <= contrast < 90:
        raise ValueError(f"BCG: invalid contrast {contrast} or gamma {gamma}")
    p = np.arange(256, dtype=np.float64)
    table = (255.0 * (p / 255.0) ** gamma - 128.0) * math.tan(math.radians(contrast)) + brightness
    lut = np.clip(np.rint(table), 0, 255).astype(np.uint8)
    arr = np.asarray(pixels)
    if arr.dtype != np.uint8:
        arr = np.clip(arr, 0, 255).astype(np.uint8)
    return lut[arr]


def apply_gain(pixels: np.ndarray, gain: float) -> np.ndarray:
    """``IMAQ Multiply`` by a constant, saturated to the pixel type (sm d87)."""
    if not math.isfinite(gain) or gain < 0:
        raise ValueError(f"Gain must be finite and >= 0, got {gain!r}")
    arr = np.asarray(pixels)
    out = arr.astype(np.float64) * gain
    if np.issubdtype(arr.dtype, np.integer):
        info = np.iinfo(arr.dtype)
        return np.clip(np.rint(out), info.min, info.max).astype(arr.dtype)
    return out.astype(arr.dtype)


def best_scale(pixel_size: float, approx_px: float = 250.0, multiple: float = 10.0) -> tuple[float, float]:
    """``tools_best_scale_width``: ``(nearest multiple (units), its length in pixels)``."""
    um = pixel_size * approx_px
    nearest = math.ceil(um / multiple) * multiple
    return nearest, nearest * approx_px / um


def stage_to_wafer(x: float, y: float, origin: tuple[float, float], system: int) -> tuple[float, float]:
    """``alox_manager_xy_coordinate_system`` "Convert" Stages → Wafer (``docs/vision-core/xy-stage.md`` §9): systems
    0..3 = #1..#4; X indirect for #2 and #4, Y indirect for #3 and #4 (``w = O − p``, else ``w = p − O``)."""
    ox, oy = origin
    wx = ox - x if system in (1, 3) else x - ox
    wy = oy - y if system in (2, 3) else y - oy
    return wx, wy


def _rgb(value: Any) -> tuple[int, int, int]:
    """An ``[overlays] fontcolor`` (``"3399FF"``) or a U32 colour → (r, g, b)."""
    try:
        number = int(value, 16) if isinstance(value, str) else int(value)
    except (TypeError, ValueError):
        return DEFAULT_FONT_COLOR
    return (number >> 16) & 0xFF, (number >> 8) & 0xFF, number & 0xFF


def _arrow(x1: float, y1: float, x2: float, y2: float, head: float, color: tuple[int, int, int],
           width: int = 1) -> list[Shape]:
    """A line with an arrow head at both ends (``alox_vision_overlay_line_with_arrows_simple``; head size
    **inferred**)."""
    shapes: list[Shape] = [Line(x1, y1, x2, y2, color, width)]
    angle = math.atan2(y2 - y1, x2 - x1)
    for (px, py), base in (((x1, y1), angle), ((x2, y2), angle + math.pi)):
        for side in (-math.pi / 6, math.pi / 6):
            a = base + side
            shapes.append(Line(px, py, px + head * math.cos(a), py + head * math.sin(a), color, width))
    return shapes


# ================================================================================================== the function
class GetImage(MeasurementFunction):
    """Get Image (module docstring). ``run(params, ctx)`` accepts :class:`GetImageParams`, a camera type (``0``,
    ``"High Mag."``, ``"lowmag"``…: the active profile's parameters of that camera, as the ``Get Image`` command of
    the API) or None (the camera of ``general.camera``). Returns a :class:`GetImageResult`; raises
    :class:`GetImageFailed` after ``Error``, :class:`GetImageError` 9002 for invalid parameters (no hardware touched)
    and :class:`alox.core.functions.Stopped` on stop/abort."""

    name = FunctionName.GET_IMAGE

    def __init__(self, services: Services, *, options: GetImageOptions | None = None,
                 sleep: Callable[[float], None] | None = None) -> None:
        if not any(name in services.devices for name in ("highmag", "lowmag")):
            raise LookupError("no camera")
        self.services = services
        self.options = options or GetImageOptions()
        self._sleep = sleep
        self._lock = threading.Lock()          # one cycle at a time (a run and the live display share the camera)

    # ---------------------------------------------------------------------------------------------- parameters
    def selected_camera(self) -> CameraType:
        """The display's camera: ``general.camera`` (0 High Mag., 1 Low Mag.; default Low Mag.)."""
        try:
            return _camera_type(self.services.settings.get("general.camera", 1), "general.camera")
        except GetImageError:
            return CameraType.LOW_MAG

    def params_for(self, camera_type: Any) -> GetImageParams:
        """``alox_function_ll_get_image_bundle_profile_parameters`` (``bundle d``)."""
        ct = _camera_type(camera_type, "Camera Type")
        prefix = f"get.image.{camera_device_name(int(ct))}."
        profile = self.services.profile

        def get(key: str, default: Any) -> Any:
            try:
                value = profile.get(prefix + key, default)
            except (RuntimeError, LookupError, TypeError, ValueError):   # no profile loaded
                return default
            return default if value is None else value

        led_camera = self.services.settings.get("general.setLEDcam", int(CameraType.LOW_MAG))
        return GetImageParams(
            camera_type=ct, led=get("led", False), exposure_mode=get("exposuremode", 0),
            low_mag_exposure=get("exposure", 1.0),
            optional_rectangle=(get("roileft", 0), get("roitop", 0), get("roiwidth", 0), get("roiheight", 0)),
            zoom=get("subsampling", 1.0), brightness=get("brightness", 128), contrast=get("contrast", 45),
            gamma=get("gamma", 1.0), simulation=get("simulation", False),
            simulation_path=str(get("simulationfolder", "")), simulation_timer_ms=get("simulationtimer", 0),
            overlay_name=get("overlayname", False), overlay_type=get("overlaytype", False),
            overlay_index=get("overlayindex", False), overlay_timestamp=get("overlaytime", False),
            overlay_hw_infos=get("overlayhwinfos", False), overlay_scale=get("overlayscale", False),
            led_camera=led_camera, gain=get("gain", 1.0))

    def coerce_params(self, params: Any) -> GetImageParams:
        if isinstance(params, GetImageParams):
            return params
        if params is None:
            return self.params_for(self.selected_camera())
        return self.params_for(params)

    # ---------------------------------------------------------------------------------------------- runs
    def run(self, params: Any, ctx: FunctionContext) -> GetImageResult:
        p = self.coerce_params(params)
        with self._lock:
            return _Job(self, p, ctx).execute()

    def run_live(self, params: Any = None) -> GetImageResult:
        """One "Live Mode" cycle: the same state machine without function state events."""
        p = self.coerce_params(params)
        with self._lock:
            return _Job(self, p, None).execute()


class _Job:
    """One run of the state machine (the per-run "shift registers")."""

    def __init__(self, function: GetImage, p: GetImageParams, ctx: FunctionContext | None) -> None:
        self.f = function
        self.s = function.services
        self.options = function.options
        self.p = p
        self.ctx = ctx
        self.error: BaseException | None = None
        self.failed_state = ""
        self.result: GetImageResult | None = None

    # ---------------------------------------------------------------------------------------------- loop
    def execute(self) -> GetImageResult:
        handlers: dict[GIState, Callable[[], GIState]] = {
            GIState.START: lambda: GIState.STARTED, GIState.STARTED: lambda: GIState.SETUP_HARDWARE,
            GIState.SETUP_HARDWARE: self.setup_hardware, GIState.ACQUIRE: self.acquire}
        state = GIState.START
        self.publish(state)
        while state not in (GIState.FINISHED, GIState.ERROR):
            self.check()
            nxt = handlers[state]()
            if nxt is not state:
                self.publish(nxt)
            state = nxt
        if state is GIState.ERROR:
            exc = self.error
            if isinstance(exc, AloxError):
                code, source = exc.code, exc.source
            elif isinstance(exc, DeviceError) and exc.code is not None:
                code, source = int(exc.code), str(exc)
            else:
                code = ACQUISITION_ERROR if self.failed_state == GIState.ACQUIRE else PROCESSING_ERROR
                source = str(exc) or type(exc).__name__
            raise GetImageFailed(code, source, state=self.failed_state) from exc
        assert self.result is not None
        return self.result

    def check(self) -> None:
        if self.ctx is not None:
            self.ctx.check()

    def publish(self, state: GIState) -> None:
        if self.ctx is None:
            return
        extra: dict[str, Any] = {"camera": self.p.camera}
        if state is GIState.ERROR and self.error is not None:
            extra.update(error=str(self.error), failed_state=self.failed_state)
        self.ctx.set_state(str(state), **extra)

    def fail(self, exc: BaseException, state: GIState) -> GIState:
        self.error = exc
        self.failed_state = str(state)
        log.warning("get image: %s failed: %s", state, exc)
        if self.s.log is not None and self.ctx is not None:
            self.s.log.error(f"Get Image ({state}): {exc}")
        return GIState.ERROR

    def pause(self, seconds: float) -> None:
        if seconds <= 0:
            return
        if self.f._sleep is not None:
            self.check()
            self.f._sleep(seconds)
        elif self.ctx is not None:
            self.ctx.sleep(seconds)
        else:
            self.s.sleep(seconds)

    def setting(self, key: str, default: Any) -> Any:
        try:
            value = self.s.settings.get(key, default)
        except (RuntimeError, LookupError, TypeError, ValueError):
            return default
        return default if value is None else value

    # ---------------------------------------------------------------------------------------------- Setup Hardware
    def setup_hardware(self) -> GIState:
        """"Apply camera exposure time given the current wavelength and exposure mode (fixed/auto/current) ?"."""
        self.digital_gain = self.p.gain
        if self.options.gain_on_camera:
            self.digital_gain = self.camera_gain()
        if self.p.exposure_mode is not ExposureMode.FIXED:
            return GIState.ACQUIRE                               # "Auto", "Current": disabled code (sm d40, d58)
        try:
            camera = self.s.camera(int(self.p.camera_type))
            old = float(camera.exposure())                      # frame 1 "Get current exposure time"
            tolerance = float(self.setting(f"hardware.camera.{self.p.camera}.exposuretolerance", 0.0))
            led_changed = self.switch_led()                     # frame 2
            new = self.fixed_exposure()                         # frame 3
            in_range = new - tolerance <= old <= new + tolerance
            if not in_range:                                    # "Set new auto-exposure time"
                camera.set_exposure(new)
                if self.s.log is not None and self.ctx is not None:
                    self.s.log.info(f"set {self.p.camera_type.label} camera exposure time to '{new:f}'")
            if led_changed or not in_range:                     # frame 4 "Wait for valid frame"
                self.pause(max(new, old) / 1e6 + self.options.frame_margin_s)
        except (DeviceError, AloxError, LookupError, ValueError) as exc:
            return self.fail(exc, GIState.SETUP_HARDWARE)
        return GIState.ACQUIRE

    def camera_gain(self) -> float:
        """Owner's decision R15 ("correlate it to the camera Gain"): the camera's gain becomes the base gain of
        ``alox.ini`` ``hardware.camera.<cam>.gain`` (coerced 1..10, as the init cycle sets it) × the recipe Gain, and
        a new frame is awaited when it changes; the part the camera cannot apply stays digital. Returns the digital gain
        left to apply (1.0 when the camera applied everything; the recipe Gain when the camera gain cannot be set)."""
        recipe = self.p.gain if self.p.gain > 0 else 1.0
        raw = self.setting(f"hardware.camera.{self.p.camera}.gain", 1.0)
        try:                # without a Gain_DBL key the name reads the gain_STR attribute path: base gain 1
            base = min(10.0, max(1.0, float(raw or 1.0)))
        except (TypeError, ValueError):
            base = 1.0
        target = base * recipe
        try:
            camera = self.s.camera(int(self.p.camera_type))
            current = float(camera.gain())
            if abs(current - target) > 1e-6:
                camera.set_gain(target)
                applied = float(camera.gain())
                self.pause(float(camera.exposure()) / 1e6 + self.options.frame_margin_s)   # a frame with the new gain
            else:
                applied = current
        except (DeviceError, AloxError, LookupError, ValueError, TypeError, AttributeError) as exc:
            log.warning("get image: camera gain %g not set (%s): digital gain %g", target, exc, recipe)
            return recipe
        return target / applied if applied > 0 and target > applied * (1 + 1e-6) else 1.0

    def led_device(self) -> Any:
        """The camera of ``Camera LEDcmd``, or the other one when only that one drives the lights; None when no
        camera drives them."""
        devices = self.s.devices
        first = camera_device_name(int(self.p.led_camera))
        for name in (first, "lowmag" if first == "highmag" else "highmag"):
            device = devices.get(name)
            if device is not None and getattr(device, "lights", None) is not None:
                return device
        return None

    def switch_led(self) -> bool:
        """"Switch On/Off Low Mag LED"; returns "LED Changed"."""
        device = self.led_device()
        if device is None:
            log.debug("get image: no camera drives the LED; LED step skipped")
            return False
        if self.p.camera_type is CameraType.LOW_MAG:
            current = bool(device.led())                        # "Get Current Low Mag LED Status?"
            device.set_led(self.p.led)
            self.pause(self.options.led_settle_s)
            return current != self.p.led
        device.set_led(False)                                   # High Mag: "Switch Off Low Mag LED"
        return False

    def fixed_exposure(self) -> float:
        """``Low Mag Exposure`` (low-mag camera) or ``Get Exposure(WL)`` at the monochromator's last position."""
        if self.p.camera_type is CameraType.LOW_MAG:
            value = self.p.low_mag_exposure
        else:
            if self.s.exposures is None:
                raise GetImageError(INVALID_EXPOSURE, "get image error (invalid auto expo): no exposure table")
            try:
                value = float(self.s.exposures.exposure_for(self.wavelength()))
            except ValueError as exc:                           # ExposureError: empty table (LabVIEW: NaN)
                raise GetImageError(INVALID_EXPOSURE, f"get image error (invalid auto expo): {exc}") from exc
        if not math.isfinite(value):
            raise GetImageError(INVALID_EXPOSURE, "get image error (invalid auto expo)")
        return value

    def wavelength(self) -> float:
        """Monochromator "Get Last Position" (0, the register's initial value, without monochromator)."""
        mono = self.s.devices.get("wl")
        if mono is None:
            return 0.0
        last = getattr(mono, "last_position", None)
        return float(last) if last is not None else float(mono.position())

    # ---------------------------------------------------------------------------------------------- Acquire
    def acquire(self) -> GIState:
        """"Acquire image and applies calibration + pre-processings (ACQ => ROI => SUBSAMPLING => BCG)"."""
        p = self.p
        if p.simulation:
            log.warning("get image: 'simulation' is not ported (simulated devices replace it); ignored")
        try:
            camera = self.s.camera(int(p.camera_type))
            shot = camera.grab(newest=self.options.newest_frame)
        except (DeviceError, AloxError, LookupError) as exc:
            return self.fail(exc, GIState.ACQUIRE)
        try:
            pixels = np.asarray(shot.pixels)
            if p.zoom != 1:
                pixels = zoom_crop(pixels, p.zoom)
            if (p.brightness, p.contrast, p.gamma) != DEFAULT_BCG:
                pixels = bcg_lookup(pixels, p.brightness, p.contrast, p.gamma)
            gain = getattr(self, "digital_gain", p.gain)
            if gain not in (0.0, 1.0):
                pixels = apply_gain(pixels, gain)
            metadata = self.metadata(camera, shot)
            overlays = self.overlays(pixels, metadata, shot)
            self.s.images.put(p.camera, pixels, metadata=metadata)
            for name, shapes in overlays.items():
                self.s.images.set_overlay(name, shapes)
        except (ValueError, TypeError, IndexError) as exc:
            return self.fail(GetImageError(PROCESSING_ERROR, f"get image error (processing): {exc}"),
                             GIState.ACQUIRE)
        self.result = GetImageResult(pixels=pixels, camera=p.camera, number=int(shot.number), time=float(shot.time),
                                     exposure_us=float(shot.exposure_us), metadata=metadata, overlays=overlays)
        return GIState.FINISHED

    def metadata(self, camera: Any, shot: Any) -> dict[str, Any]:
        """The custom data of the camera path (``buffer``, ``timestamp``, ``name``, ``type``, ``simulation``,
        ``flatfield``) and the calibration (case 7 "Apply calibration": pixel size and units)."""
        settings = getattr(camera, "settings", None)
        if settings is not None:
            data: dict[str, Any] = build_custom_data(int(shot.number), settings, self.p.camera_type.label,
                                                     now=float(shot.time))
        else:
            data = {"buffer": str(int(shot.number)), "name": self.p.camera,
                    "type": "High-Mag" if self.p.camera_type is CameraType.HIGH_MAG else "Low-Mag"}
        units = self.setting(f"hardware.camera.{self.p.camera}.units", 0)
        if isinstance(units, int) and 0 <= units < len(PIXEL_UNITS):
            units = PIXEL_UNITS[units]
        data.update(time=float(shot.time), number=int(shot.number), exposure_us=float(shot.exposure_us),
                    pixel_size=float(self.setting(f"hardware.camera.{self.p.camera}.pixelsize", 1.0)),
                    units=str(units))
        return data

    # ---------------------------------------------------------------------------------------------- overlays
    def overlays(self, pixels: np.ndarray, metadata: Mapping[str, Any], shot: Any) -> dict[str, tuple[Shape, ...]]:
        """The three Get Image overlay groups of this camera (``<camera>camera``, ``<camera>infos``,
        ``<camera>scale``)."""
        p = self.p
        h, w = pixels.shape[:2]
        size = int(self.setting("overlays.fontsize", 50))
        ls = int(self.setting("overlays.fontlinespace", 50))
        color = _rgb(self.setting("overlays.fontcolor", "3399FF"))
        x0 = self.options.text_x
        camera: list[Shape] = []
        for k, (on, key) in enumerate(((p.overlay_name, "name"), (p.overlay_type, "type"),
                                       (p.overlay_timestamp, "timestamp"), (p.overlay_index, "buffer")), start=1):
            if on:
                camera.append(Text(x0, k * ls, str(metadata.get(key, "")), color, size, "ls"))
                if key == "type":
                    camera.append(Circle(w // 2, h // 2, self.options.center_mark_px, color, 1, fill=True))
        infos = self.hardware_infos(w, h, ls, size, color, shot) if p.overlay_hw_infos else []
        scale = self.scale(w, h, ls, size, color, metadata) if p.overlay_scale else []
        return {f"{p.camera}camera": tuple(camera), f"{p.camera}infos": tuple(infos), f"{p.camera}scale": tuple(scale)}

    def hardware_infos(self, w: int, h: int, ls: int, size: int, color: tuple[int, int, int],
                       shot: Any) -> list[Shape]:
        """``alox_vision_overlay_hardware_infos``: X, Y (wafer), Z, exposure, wavelength; ``-`` when unreadable."""
        x_text, y_text = "X (mm): -", "Y (mm): -"
        xy = self.s.devices.get("xy")
        if xy is not None:
            try:
                sx, sy = xy.position()
                origin = (float(self.setting("hardware.xy.waferxorg", 0.0)),
                          float(self.setting("hardware.xy.waferyorg", 0.0)))
                wx, wy = stage_to_wafer(float(sx), float(sy), origin,
                                        int(self.setting("hardware.xy.stagescoordsys", 3)))
                x_text, y_text = f"X (mm): {wx:.3f}", f"Y (mm): {wy:.3f}"
            except (DeviceError, AloxError, TypeError, ValueError) as exc:
                log.debug("hardware infos: XY: %s", exc)
        z_text = "Z (mm): -"
        z = self.s.devices.get("z")
        if z is not None:
            try:
                z_text = f"Z (mm): {float(z.position()):.3f}"
            except (DeviceError, AloxError, TypeError, ValueError) as exc:
                log.debug("hardware infos: Z: %s", exc)
        expo = getattr(shot, "exposure_us", None)
        expo_text = "Expo (µs): -" if expo is None or not math.isfinite(expo) else f"Expo (µs): {float(expo):.1f}"
        wl_text = "WL (nm): -"
        mono = self.s.devices.get("wl")
        last = getattr(mono, "last_position", None) if mono is not None else None
        if last is not None:
            wl_text = f"WL (nm): {int(last):d}"
        y0 = h - 6 * ls
        texts = (x_text, y_text, z_text, expo_text, wl_text)
        return [Text(self.options.text_x, y0 + k * ls, text, color, size, "ls") for k, text in enumerate(texts, 1)]

    def scale(self, w: int, h: int, ls: int, size: int, color: tuple[int, int, int],
              metadata: Mapping[str, Any]) -> list[Shape]:
        """``alox_vision_overlay_scale``: a double arrow ``bar`` px long ending 100 px from the right edge at
        ``0.98·H``, its length ``"%d %s"`` centred at ``H − 2·line space``."""
        pixel_size = float(metadata.get("pixel_size", 1.0))
        if not (math.isfinite(pixel_size) and pixel_size > 0):
            return []
        nearest, bar = best_scale(pixel_size, self.options.scale_approx_px, self.options.scale_multiple)
        units = {"um": "µm", "mm": "mm"}.get(str(metadata.get("units", "")), "pixels")
        y = 0.98 * h
        x1, x2 = w - bar - 100.0, w - 100.0
        shapes = _arrow(x1, y, x2, y, self.options.arrow_px, color)
        shapes.append(Text((x1 + x2) / 2.0, h - 2 * ls, f"{round(nearest):d} {units}", color, size, "ms"))
        return shapes


# ================================================================================================== live display
class LiveView:
    """The continuous Get Image of the display (module docstring): while no function runs, one "Live Mode" cycle of
    the camera ``camera()`` (default :meth:`GetImage.selected_camera`) every ``interval_s``; after a failure the next
    try waits ``error_interval_s`` (the failure is logged once until a cycle succeeds again)."""

    def __init__(self, function: GetImage, runner: FunctionRunner, *, camera: Callable[[], Any] | None = None,
                 interval_s: float = 0.025, error_interval_s: float = 1.0) -> None:
        for name, value in (("interval_s", interval_s), ("error_interval_s", error_interval_s)):
            if not (math.isfinite(value) and value >= 0):
                raise ValueError(f"{name} must be finite and >= 0, got {value!r}")
        self.function = function
        self.runner = runner
        self.camera = camera or function.selected_camera
        self.interval_s = interval_s
        self.error_interval_s = error_interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._failing = False

    def step(self) -> GetImageResult | None:
        """One cycle when no function runs (None otherwise)."""
        if not self.runner.condition_ok():
            return None
        return self.function.run_live(self.function.params_for(self.camera()))

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="get-image-live", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _loop(self) -> None:
        while not self._stop.is_set():
            wait = self.interval_s
            try:
                self.step()
                self._failing = False
            except (AloxError, DeviceError, LookupError, ValueError) as exc:
                if not self._failing:
                    log.warning("live image: %s", exc)
                self._failing = True
                wait = self.error_interval_s
            except Exception:
                log.exception("live image failed")
                wait = self.error_interval_s
            self._stop.wait(wait)
