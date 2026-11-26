r"""A simulated wafer scene: what the simulated cameras of ``alox --simulate`` see.

LabVIEW shipped a separate ``AloxSim.exe`` with fake measurement functions. Here the real functions run on the
simulated devices, and the simulated cameras (:class:`aloxcam.camera.sim.SimCameraBackend` with a ``frame_source``)
render a physically plausible scene from the current state of the other simulated devices:

- **Wafer** (diameter ``general.wafersize``, centre at the stage position ``hardware.xy.waferxorg/yorg``, stage
  coordinate system ``hardware.xy.stagescoordsys``), rotated by :attr:`SceneParams.tilt_deg` about its centre, with
  a straight flat at the bottom (design frame) and the chuck around it. The wafer carries a square grid of VCSEL
  mesas (bright discs of :attr:`~SceneParams.mesa_diameter_um`, pitch :attr:`~SceneParams.mesa_pitch_um`; a mesa at
  the wafer centre) with a darker oxide ring whose inner (aperture) diameter shrinks with the wall-clock time at
  :attr:`~SceneParams.oxidation_rate_um_min` (diameter µm per minute) from :attr:`~SceneParams.aperture_start_um` at
  the oxidation start, and alignment marks (:class:`Mark`: a cross, a corner) for Search Object and the tilt.
- **Camera view**: the field of view is the image size × ``hardware.camera.<highmag|lowmag>.pixelsize`` (``units``
  0 pixels = µm, 1 µm, 2 mm), centred on the XY stage position; the raw image shows wafer X to the right and wafer Y
  up whatever the stage coordinate system (the convention of :func:`alox.vision.wafer.tracking_move`; the camera
  device applies its flips afterwards). Both cameras look at the same point (coaxial, parfocal unless
  ``*_focus_offset_mm``).
- **Focus**: the blur (a Gaussian PSF, rendered analytically: the edges are error-function steps, the textures are
  attenuated) grows with ``|Z − Z_focus|`` (``*_blur_per_um`` µm of blur radius per µm of defocus, plus the optical
  PSF and the pixel). ``Z_focus`` of the wafer surface is :attr:`~SceneParams.z_focus_mm` at the centre plus a
  paraboloid bowing of :attr:`~SceneParams.bowing_um` at the wafer edge and a wedge (µm per mm); the chuck is flat at
  :attr:`~SceneParams.chuck_dz_mm` from the wafer centre (parallelism). :meth:`Scene.focus_z` is the reference the
  bowing measurement should find.
- **Light**: the monochromator wavelength (emulator position; 0 nm = zero order, white) lights the High Mag view; the
  Low Mag view is lit by the LED (``device.<camera>.led`` events) and a fraction of the monochromator light. The
  oxide/aperture contrast peaks at :attr:`~SceneParams.contrast_peak_nm` (Search Wavelength finds it). Grey levels are
  proportional to the exposure and the gain (dB), with a dark level, read and shot noise, saturating at 255 (Mono8).

Rendering is vectorised NumPy over the field of view only (a few ms for the default 612 × 512 frame, tens of ms at the
real 2448 × 2048); a frame is re-rendered only when an input changed (positions, wavelength, LED, exposure, aperture
to 1 nm), otherwise only the noise is new. Deterministic for a given seed and frame number.

**Demo** (synthetic values; ``src/alox/devices/scene_demo``: ``alox_default.ini`` with the simulated hardware and
``[simulation.scene]``, ``demo.ini`` the recipe)::

    python -m alox.devices.scene --make-demo /tmp/alox-demo       # folders, settings, the DEMO profile, the models
    alox --simulate --setup-ini /tmp/alox-demo/alox_setup.ini     # REST 8002, websocket 6123 (add --ui for the window)

then post the supervisor jobs, e.g. ``PUT /alox/v1/functions/measure_mesa`` (Singleshot or continuous: the aperture
shrinks at 0.5 µm/min, so the rate and the remaining time to the 10 µm target are meaningful), ``measure_bowing``
(20 µm at the wafer edge), ``measure_tilt``. ``python -m alox.devices.scene --render highmag out.png`` writes one frame.
"""

from __future__ import annotations

import argparse
import math
import shutil
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import numpy as np

from aloxcam.camera.sim import SIM_EXPOSURE, SIM_GAIN, SimCameraBackend, SimCameraDevice
from aloxcam.settings import SimulationOptions

__all__ = [
    "CAMERAS",
    "DEMO_FOLDER",
    "Mark",
    "Scene",
    "SceneCameraBackend",
    "SceneParams",
    "make_demo",
]

CAMERAS = ("highmag", "lowmag")
#: The demo settings and recipe.
DEMO_FOLDER = Path(__file__).with_name("scene_demo")
SETTINGS_SECTION = "simulation.scene"

_SQRT2 = math.sqrt(2.0)
_TILE = 1024                     # texture tile side (samples), periodic
_TILE_SAMPLES_PER_CELL = 4
_NOISE_FRAMES = 8


# ================================================================================================== parameters
@dataclass(frozen=True)
class Mark:
    """An alignment object (design coordinates of the wafer, mm): ``cross`` (two bars of ``size_um`` × ``width_um``
    crossing at the point) or ``corner`` (an L: two bars of ``size_um`` from the point, to +X and to +Y)."""

    kind: str = "cross"
    x_mm: float = 0.0
    y_mm: float = 0.0
    size_um: float = 300.0
    width_um: float = 40.0

    def rects(self) -> list[tuple[float, float, float, float]]:
        """The bars as ``(x0, x1, y0, y1)`` µm relative to the point, then their overlap (counted twice)."""
        s, h = float(self.size_um), float(self.width_um) / 2.0
        if self.kind == "corner":
            return [(-h, s, -h, h), (-h, h, -h, s), (-h, h, -h, h)]
        return [(-s / 2, s / 2, -h, h), (-h, h, -s / 2, s / 2), (-h, h, -h, h)]

    @property
    def radius_um(self) -> float:
        return math.hypot(float(self.size_um), float(self.width_um))


def _marks(text: str) -> tuple[Mark, ...]:
    """``"cross,x,y[,size,width];corner,x,y"`` → marks (the ``[simulation.scene] marks`` key)."""
    out: list[Mark] = []
    for item in str(text).split(";"):
        parts = [p.strip() for p in item.split(",") if p.strip()]
        if not parts:
            continue
        values = [float(p) for p in parts[1:]]
        out.append(Mark(parts[0].lower(), *values))
    return tuple(out)


@dataclass(frozen=True)
class SceneParams:
    """The scene. ``[simulation.scene] <field>`` of ``alox.ini`` overrides a default (:meth:`from_settings`;
    ``marks`` as ``"cross,x_mm,y_mm[,size_um,width_um];corner,…"``). Lengths: µm unless ``_mm``."""

    seed: int = 7
    image_width: int = 612                   # the simulated sensor (real cameras 2448 × 2048)
    image_height: int = 512
    frame_rate_max: float = 20.0             # the simulated cameras' maximum frame rate
    # wafer
    wafer_diameter_mm: float = 0.0           # 0: general.wafersize (76.2)
    tilt_deg: float = 0.0                    # rotation of the wafer about its centre (counter-clockwise)
    flat_length_mm: float = 22.0             # 0: no flat
    edge_exclusion_mm: float = 2.0           # no mesa closer to the edge
    # mesas
    mesa_diameter_um: float = 30.0
    mesa_pitch_um: float = 250.0
    # oxidation: aperture diameter = start − rate · minutes since the start, bounded below by aperture_min_um
    aperture_start_um: float = 24.0
    aperture_min_um: float = 0.0
    oxidation_rate_um_min: float = 0.5
    oxidation_start_s: float = 0.0           # wall clock (s since the epoch); 0: when the scene is created
    # alignment marks (design mm); mesas are left out around them
    marks: tuple[Mark, ...] = (Mark("cross", -0.5, 0.0), Mark("corner", 0.5, 0.0))
    # heights (Z stage mm at best focus)
    z_focus_mm: float = 12.6                 # wafer centre
    bowing_um: float = 20.0                  # paraboloid: + bowing·(r/R)² µm
    wedge_x_um_mm: float = 0.2               # wafer surface slope vs the chuck (parallelism)
    wedge_y_um_mm: float = -0.1
    chuck_dz_mm: float = -0.25               # chuck surface, from the wafer centre
    highmag_focus_offset_mm: float = 0.0
    lowmag_focus_offset_mm: float = 0.0
    # optics: blur sigma (µm) = hypot(psf, blur_per_um·|ΔZ µm|, pixel/2)
    highmag_blur_per_um: float = 0.5
    lowmag_blur_per_um: float = 0.2
    highmag_psf_um: float = 0.4
    lowmag_psf_um: float = 2.0
    # reflectances (0…1) and textures
    reflectance_field: float = 0.15
    reflectance_aperture: float = 0.80
    reflectance_mark: float = 0.95
    reflectance_chuck: float = 0.45                # polished chuck, brighter than the etched field
    texture_wafer: float = 0.03              # amplitude (std, reflectance) and grain (µm): fine, seen in High Mag
    texture_wafer_um: float = 1.5
    texture_wafer_coarse: float = 0.03       # and coarse, seen in Low Mag
    texture_wafer_coarse_um: float = 8.0
    texture_chuck: float = 0.1
    texture_chuck_um: float = 2.0
    # oxide contrast: R_oxide = R_aperture · (1 − c(λ)), c = min + (max − min)·exp(−((λ − peak)/width)²)
    contrast_peak_nm: float = 850.0
    contrast_width_nm: float = 60.0
    contrast_max: float = 0.55
    contrast_min: float = 0.08
    contrast_white: float = 0.25             # zero order (0 nm) and LED
    # light and sensor: grey = 255·gain·light·R·exposure/full_scale + dark (+ noise)
    full_scale_exposure_us: float = 10000.0
    lamp_peak_nm: float = 800.0
    lamp_width_nm: float = 350.0
    led_level: float = 0.6                   # Low Mag LED
    lowmag_monochromator: float = 0.3        # part of the monochromator light seen by the Low Mag camera
    dark_level: float = 4.0
    read_noise: float = 1.5                  # grey levels (std)
    shot_noise: float = 0.04                 # variance per grey level

    @classmethod
    def from_settings(cls, settings: Any, **overrides: Any) -> SceneParams:
        """The defaults with the ``[simulation.scene]`` keys of ``settings`` (anything with ``get(dotted,
        default)``: ``alox.ini`` as :class:`alox.config.TypedIni` or the core's properties) and ``overrides``."""
        values: dict[str, Any] = {}
        if settings is not None:
            for f in fields(cls):
                try:
                    value = settings.get(f"{SETTINGS_SECTION}.{f.name}", None)
                except Exception:                            # noqa: BLE001 - an unreadable key keeps the default
                    value = None
                if value is None:
                    continue
                if f.name == "marks":
                    values[f.name] = _marks(value)
                elif f.name in ("seed", "image_width", "image_height"):
                    values[f.name] = int(value)
                else:
                    values[f.name] = float(value)
        values.update(overrides)
        return cls(**values)


# ================================================================================================== helpers
class _NoSettings:
    def get(self, dotted: str, default: Any = None) -> Any:
        return default


def _erf(x: np.ndarray) -> np.ndarray:
    """``erf`` to 4·10⁻⁴ (``tanh(x·(2/√π + 0.10094·x²))``), 10 × faster than :func:`scipy.special.erf` in float32."""
    x = np.clip(x, -6.0, 6.0)
    return np.tanh(x * (np.float32(1.1283792) + np.float32(0.10094) * x * x))


def _step(d: np.ndarray | float, sigma: float) -> Any:
    """Coverage of a blurred half-plane at signed distance ``d`` inside (Gaussian PSF ``sigma``)."""
    return 0.5 * (1.0 + _erf(np.asarray(d, dtype=np.float32) * np.float32(1.0 / (_SQRT2 * sigma))))


def _box(d: np.ndarray, lo: float, hi: float, sigma: float) -> np.ndarray:
    """Blurred 1-D box ``[lo, hi]`` (exact for a Gaussian PSF)."""
    k = np.float32(1.0 / (_SQRT2 * sigma))
    return 0.5 * (_erf((d - np.float32(lo)) * k) - _erf((d - np.float32(hi)) * k))


def _disc(d: np.ndarray, radius: float, sigma: float) -> np.ndarray:
    """Blurred disc coverage at distance ``d`` from its centre: the edge step, scaled so that the centre value is the
    exact ``1 − exp(−R²/2σ²)`` (a small disc fades when it is out of focus)."""
    if radius <= 0:
        return np.zeros_like(d)
    centre = 1.0 - math.exp(-(radius * radius) / (2.0 * sigma * sigma))
    edge = 0.5 * (1.0 + math.erf(radius / (_SQRT2 * sigma)))
    return _step(np.float32(radius) - d, sigma) * np.float32(centre / edge)


def _tile(rng: np.random.Generator) -> np.ndarray:
    """A periodic smooth random texture (unit std), ``_TILE`` samples a side, grain ``_TILE_SAMPLES_PER_CELL``."""
    white = rng.standard_normal((_TILE, _TILE))
    f = np.fft.fftfreq(_TILE)
    sigma = _TILE_SAMPLES_PER_CELL / 2.0
    gauss = np.exp(-2.0 * (math.pi * sigma) ** 2 * (f[:, None] ** 2 + f[None, :] ** 2))
    smooth = np.real(np.fft.ifft2(np.fft.fft2(white) * gauss))
    smooth -= smooth.mean()
    return (smooth / smooth.std()).astype(np.float32).ravel()


def stage_to_wafer(x: float, y: float, origin: Sequence[float], system: int) -> tuple[float, float]:
    """XY stages → wafer mm (``alox_manager_xy_coordinate_system`` "Convert", as
    :func:`alox.functions.get_image.stage_to_wafer`)."""
    ox, oy = float(origin[0]), float(origin[1])
    wx = ox - x if system in (1, 3) else x - ox
    wy = oy - y if system in (2, 3) else y - oy
    return wx, wy


def wafer_to_stage(wx: float, wy: float, origin: Sequence[float], system: int) -> tuple[float, float]:
    """Wafer mm → XY stages (the inverse of :func:`stage_to_wafer`)."""
    ox, oy = float(origin[0]), float(origin[1])
    x = ox - wx if system in (1, 3) else wx + ox
    y = oy - wy if system in (2, 3) else wy + oy
    return x, y


# ================================================================================================== scene
class Scene:
    """The scene and its renderer. ``settings`` (``get(dotted, default)``) gives the wafer size and origin, the stage
    coordinate system and the pixel sizes, read at each frame as the functions read them; ``stage_xy``, ``stage_z``
    and ``wavelength`` read the simulated devices (never through the device workers); ``clock`` is the wall clock of
    the oxidation."""

    def __init__(self, params: SceneParams | None = None, *, settings: Any = None,
                 stage_xy: Callable[[], tuple[float, float]] | None = None,
                 stage_z: Callable[[], float] | None = None,
                 wavelength: Callable[[], float] | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.params = params or SceneParams()
        self.settings = settings if settings is not None else _NoSettings()
        self.clock = clock
        self._stage_xy = stage_xy
        self._stage_z = stage_z
        self._wavelength = wavelength
        self.led = False
        self.oxidation_start = self.params.oxidation_start_s or clock()
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[tuple[Any, ...], np.ndarray]] = {}
        self._noise: dict[tuple[int, int], np.ndarray] = {}
        rng = np.random.default_rng(self.params.seed)
        self._tile = _tile(rng)
        self._noise_seed = int(rng.integers(0, 2**31))

    # ---------------------------------------------------------------------------------------------- settings
    def setting(self, key: str, default: Any) -> Any:
        try:
            value = self.settings.get(key, default)
        except Exception:                                    # noqa: BLE001 - a bad setting reads as its default
            return default
        return default if value is None else value

    def origin(self) -> tuple[float, float]:
        """Stage position (mm) of the wafer centre."""
        return (float(self.setting("hardware.xy.waferxorg", 0.0)), float(self.setting("hardware.xy.waferyorg", 0.0)))

    def coordinate_system(self) -> int:
        return int(self.setting("hardware.xy.stagescoordsys", 0))

    def wafer_radius_mm(self) -> float:
        d = self.params.wafer_diameter_mm or float(self.setting("general.wafersize", 76.2))
        return d / 2.0

    def pixel_size_um(self, camera: str) -> float:
        """``hardware.camera.<camera>.pixelsize`` in µm (``units`` 2 = mm; 0 pixels and 1 µm as is), as the
        functions read it (default 1.0)."""
        size = float(self.setting(f"hardware.camera.{camera}.pixelsize", 1.0))
        units = str(self.setting(f"hardware.camera.{camera}.units", 1)).strip().lower()
        return size * 1000.0 if units in ("2", "mm") else size

    # ---------------------------------------------------------------------------------------------- geometry
    def stage_to_wafer(self, x: float, y: float) -> tuple[float, float]:
        return stage_to_wafer(x, y, self.origin(), self.coordinate_system())

    def wafer_to_stage(self, wx: float, wy: float) -> tuple[float, float]:
        return wafer_to_stage(wx, wy, self.origin(), self.coordinate_system())

    def to_design(self, wx: float, wy: float) -> tuple[float, float]:
        """Wafer (stage-aligned) mm → design mm (the wafer before its rotation by the tilt)."""
        t = math.radians(self.params.tilt_deg)
        c, s = math.cos(t), math.sin(t)
        return wx * c + wy * s, -wx * s + wy * c

    def from_design(self, dx: float, dy: float) -> tuple[float, float]:
        """Design mm → wafer mm (:func:`alox.vision.wafer.tilted_coordinates` with the scene's tilt)."""
        t = math.radians(self.params.tilt_deg)
        c, s = math.cos(t), math.sin(t)
        return dx * c - dy * s, dx * s + dy * c

    def flat_distance_mm(self) -> float:
        """Distance from the wafer centre to the flat (design −Y); infinite without a flat."""
        r, half = self.wafer_radius_mm(), self.params.flat_length_mm / 2.0
        return math.sqrt(max(r * r - half * half, 0.0)) if 0 < half < r else math.inf

    def on_wafer(self, wx: float, wy: float) -> bool:
        dx, dy = self.to_design(wx, wy)
        return math.hypot(dx, dy) <= self.wafer_radius_mm() and dy >= -self.flat_distance_mm()

    def mesa_near(self, wx: float, wy: float) -> tuple[float, float] | None:
        """The wafer position (mm) of the mesa nearest to ``(wx, wy)``, None when there is none there."""
        p = self.params
        dx, dy = self.to_design(wx, wy)
        pitch = p.mesa_pitch_um / 1000.0
        cx, cy = round(dx / pitch) * pitch, round(dy / pitch) * pitch
        if not self._mesa_allowed(cx * 1000.0, cy * 1000.0):
            return None
        return self.from_design(cx, cy)

    def _mesa_allowed(self, x_um: float, y_um: float) -> bool:
        p = self.params
        limit = (self.wafer_radius_mm() - p.edge_exclusion_mm) * 1000.0
        if math.hypot(x_um, y_um) > limit or y_um < (-self.flat_distance_mm() + p.edge_exclusion_mm) * 1000.0:
            return False
        clear = p.mesa_diameter_um
        return all(math.hypot(x_um - m.x_mm * 1000.0, y_um - m.y_mm * 1000.0) > m.radius_um + clear
                   for m in p.marks)

    # ---------------------------------------------------------------------------------------------- heights
    def wafer_focus_z(self, wx: float, wy: float) -> float:
        """Z (mm) at best focus on the wafer surface at wafer ``(wx, wy)`` mm."""
        p = self.params
        r = self.wafer_radius_mm()
        bow = p.bowing_um * (wx * wx + wy * wy) / (r * r)
        return p.z_focus_mm + (bow + p.wedge_x_um_mm * wx + p.wedge_y_um_mm * wy) / 1000.0

    def chuck_focus_z(self, wx: float = 0.0, wy: float = 0.0) -> float:
        """Z (mm) at best focus on the (flat) chuck."""
        return self.params.z_focus_mm + self.params.chuck_dz_mm

    def focus_z(self, wx: float, wy: float, camera: str = "highmag") -> float:
        """Z (mm) at best focus at wafer ``(wx, wy)``: the wafer surface, or the chuck beyond the wafer."""
        z = self.wafer_focus_z(wx, wy) if self.on_wafer(wx, wy) else self.chuck_focus_z(wx, wy)
        return z + float(getattr(self.params, f"{camera}_focus_offset_mm", 0.0))

    def blur_um(self, camera: str, defocus_mm: float) -> float:
        p = self.params
        per_um = float(getattr(p, f"{camera}_blur_per_um"))
        psf = float(getattr(p, f"{camera}_psf_um"))
        return math.sqrt(psf * psf + (per_um * defocus_mm * 1000.0) ** 2 + (0.5 * self.pixel_size_um(camera)) ** 2)

    # ---------------------------------------------------------------------------------------------- oxidation, light
    def aperture_um(self, t: float | None = None) -> float:
        """The oxide aperture diameter (µm) at wall-clock ``t`` (default now)."""
        p = self.params
        minutes = ((self.clock() if t is None else t) - self.oxidation_start) / 60.0
        value = p.aperture_start_um - p.oxidation_rate_um_min * max(minutes, 0.0)
        return float(min(max(value, p.aperture_min_um, 0.0), p.mesa_diameter_um))

    def oxide_contrast(self, wavelength_nm: float) -> float:
        p = self.params
        if wavelength_nm < 1.0:
            return p.contrast_white
        x = (wavelength_nm - p.contrast_peak_nm) / p.contrast_width_nm
        return p.contrast_min + (p.contrast_max - p.contrast_min) * math.exp(-x * x)

    def lamp(self, wavelength_nm: float) -> float:
        """Monochromator output (relative): white at the zero order, a broad lamp × sensor curve otherwise."""
        if wavelength_nm < 1.0:
            return 1.0
        p = self.params
        x = (wavelength_nm - p.lamp_peak_nm) / p.lamp_width_nm
        return math.exp(-x * x)

    def light(self, camera: str, wavelength_nm: float, led: bool) -> tuple[float, float]:
        """``(illumination, oxide contrast)`` seen by ``camera``."""
        p = self.params
        mono = self.lamp(wavelength_nm) * (1.0 if camera == "highmag" else p.lowmag_monochromator)
        white = p.led_level if (led and camera == "lowmag") else 0.0
        total = mono + white
        if total <= 0:
            return 0.0, 0.0
        contrast = (mono * self.oxide_contrast(wavelength_nm) + white * p.contrast_white) / total
        return total, contrast

    def exposure_for(self, wavelength_nm: float, grey: float = 170.0, camera: str = "highmag",
                     led: bool = False) -> float:
        """The exposure (µs) that brings a non-oxidised mesa to ``grey`` at ``wavelength_nm`` (for exposure tables)."""
        p = self.params
        light, _ = self.light(camera, wavelength_nm, led)
        signal = 255.0 * light * p.reflectance_aperture
        return p.full_scale_exposure_us * (grey - p.dark_level) / signal if signal > 0 else math.inf

    def exposure_table(self, wavelengths: Sequence[float], grey: float = 170.0) -> list[list[float]]:
        """A 7-column exposure table (``general.exposures``) fitting the scene: ExpoTh = :meth:`exposure_for`,
        ExpoMin/Max ∓ 50 %, BMin/BMax 60/220, ExpoAuto = ExpoTh."""
        rows = []
        for wl in wavelengths:
            th = round(self.exposure_for(wl, grey), 0)
            rows.append([float(wl), th, round(th * 0.5, 0), round(th * 2.0, 0), 60.0, 220.0, th])
        return rows

    def on_event(self, event: Any) -> None:
        """Event-bus callback: ``device.<camera>.led`` switches the LED."""
        if str(getattr(event, "topic", "")).endswith(".led"):
            self.led = bool(getattr(event, "data", {}).get("on", False))

    # ---------------------------------------------------------------------------------------------- rendering
    def state(self) -> tuple[tuple[float, float], float, float]:
        """``((x, y) stage mm, z mm, wavelength nm)`` of the simulated devices now."""
        xy = self._stage_xy() if self._stage_xy is not None else self.origin()
        z = self._stage_z() if self._stage_z is not None else self.params.z_focus_mm
        wl = self._wavelength() if self._wavelength is not None else self.params.contrast_peak_nm
        return (float(xy[0]), float(xy[1])), float(z), float(wl)

    def render(self, camera: str, *, exposure_us: float, gain_db: float = 0.0, width: int | None = None,
               height: int | None = None, number: int = 0, stage_xy: tuple[float, float] | None = None,
               z_mm: float | None = None, wavelength_nm: float | None = None, led: bool | None = None,
               t: float | None = None, noise: bool = True) -> np.ndarray:
        """One Mono8 frame (``height × width``) of ``camera`` (``highmag``/``lowmag``). Missing inputs are read from
        the devices (:meth:`state`), the LED from :attr:`led` and ``t`` from the clock; ``number`` selects the
        noise."""
        p = self.params
        w, h = int(width or p.image_width), int(height or p.image_height)
        if stage_xy is None or z_mm is None or wavelength_nm is None:
            xy, z, wl = self.state()
        xy = stage_xy if stage_xy is not None else xy
        z = z_mm if z_mm is not None else z
        wl = wavelength_nm if wavelength_nm is not None else wl
        on = self.led if led is None else bool(led)
        centre = self.stage_to_wafer(float(xy[0]), float(xy[1]))
        aperture = round(self.aperture_um(t), 3)
        px = self.pixel_size_um(camera)
        key = (w, h, px, centre, round(z, 6), round(wl, 3), on, aperture, float(exposure_us), float(gain_db),
               self.wafer_radius_mm())
        with self._lock:
            cached = self._cache.get(camera)
        if cached is not None and cached[0] == key:
            clean = cached[1]
        else:
            clean = self._clean(camera, w, h, px, centre, z, wl, on, aperture, float(exposure_us), float(gain_db))
            with self._lock:
                self._cache[camera] = (key, clean)
        img = clean
        if noise:
            sigma = np.sqrt(np.float32(p.read_noise ** 2) + np.float32(p.shot_noise) * np.maximum(clean, 0.0))
            img = clean + self._noise_frame(w, h, number, camera) * sigma
        return np.clip(np.rint(img), 0, 255).astype(np.uint8)

    def _noise_frame(self, w: int, h: int, number: int, camera: str) -> np.ndarray:
        with self._lock:
            bank = self._noise.get((w, h))
            if bank is None:
                rng = np.random.default_rng(self._noise_seed)
                bank = rng.standard_normal((_NOISE_FRAMES, h, w), dtype=np.float32)
                self._noise[(w, h)] = bank
        n = int(number) + (0 if camera == "highmag" else 5)
        frame = bank[n % _NOISE_FRAMES]
        flip = (n // _NOISE_FRAMES) % 4                                    # 4 × more patterns, as free views
        return frame[::-1 if flip & 1 else 1, ::-1 if flip & 2 else 1]

    def _texture(self, x_um: float, y_um: float, du: np.ndarray, dv: np.ndarray, cell_um: float, sigma: float,
                 offset: int) -> np.ndarray:
        """A random texture of grain ``cell_um`` fixed on the wafer, at design ``(x_um + du, y_um + dv)``, blurred by
        ``sigma`` µm: a Gaussian-correlated texture keeps its statistics with the grain ``√(c² + 2σ²)`` and the
        amplitude ``c² / (c² + 2σ²)`` (so it is never sampled finer than the blur: no aliasing)."""
        blurred = cell_um * cell_um + 2.0 * sigma * sigma
        cell_um = math.sqrt(blurred)
        gain = np.float32((cell_um * cell_um - 2.0 * sigma * sigma) / blurred)
        step = cell_um / _TILE_SAMPLES_PER_CELL
        fx, fy = x_um / step, y_um / step
        mask = _TILE - 1
        ix = (np.floor(np.float32(fx - math.floor(fx)) + du * np.float32(1.0 / step)).astype(np.int32)
              + ((math.floor(fx) + offset) & mask)) & mask
        iy = (np.floor(np.float32(fy - math.floor(fy)) + dv * np.float32(1.0 / step)).astype(np.int32)
              + ((math.floor(fy) + 3 * offset) & mask)) & mask
        return np.take(self._tile, (iy << 10) | ix) * gain

    def _clean(self, camera: str, w: int, h: int, px: float, centre: tuple[float, float], z: float, wl: float,
               led: bool, aperture_um: float, exposure_us: float, gain_db: float) -> np.ndarray:
        """The noiseless frame (float32 grey levels)."""
        p = self.params
        cx, cy = centre
        dxc, dyc = self.to_design(cx, cy)
        x0, y0 = dxc * 1000.0, dyc * 1000.0                                   # design µm of the image centre
        t = math.radians(p.tilt_deg)
        c, s = np.float32(math.cos(t)), np.float32(math.sin(t))
        u = ((np.arange(w, dtype=np.float32) - np.float32(w / 2.0)) * np.float32(px))[None, :]
        v = (-(np.arange(h, dtype=np.float32) - np.float32(h / 2.0)) * np.float32(px))[:, None]
        du = u * c + v * s                                                    # design µm from the image centre
        dv = v * c - u * s
        reach = 0.5 * math.hypot(w, h) * px                                   # half diagonal of the view, µm
        offset = float(getattr(p, f"{camera}_focus_offset_mm", 0.0))
        sw = self.blur_um(camera, z - (self.wafer_focus_z(cx, cy) + offset))
        sc = self.blur_um(camera, z - (self.chuck_focus_z(cx, cy) + offset))
        shape = (h, w)

        # wafer coverage: edge circle and flat
        r_um = self.wafer_radius_mm() * 1000.0
        rc = math.hypot(x0, y0)
        cover: np.ndarray | float
        if rc + reach + 5 * sw < r_um:
            cover = 1.0
        elif rc - reach - 5 * sw > r_um:
            cover = 0.0
        else:
            cover = _step(np.float32(r_um) - np.hypot(np.float32(x0) + du, np.float32(y0) + dv), sw)
        flat_um = self.flat_distance_mm() * 1000.0                          # flat: the line y = −flat (design)
        if math.isfinite(flat_um) and y0 + reach + 5 * sw < -flat_um:
            cover = 0.0
        elif math.isfinite(flat_um) and y0 - reach - 5 * sw < -flat_um:
            cover = cover * _step(np.float32(y0 + flat_um) + dv, sw)

        light, contrast = self.light(camera, wl, led)
        scale = np.float32(255.0 * light * exposure_us / p.full_scale_exposure_us * 10.0 ** (gain_db / 20.0))
        out = np.full(shape, np.float32(p.dark_level), dtype=np.float32)
        if scale <= 0:
            return out

        refl: np.ndarray | float = 0.0
        if not (np.isscalar(cover) and cover == 0.0):
            wafer = self._wafer(x0, y0, du, dv, sw, px, reach, aperture_um, contrast)
            refl = wafer * cover
        if not (np.isscalar(cover) and cover == 1.0):
            chuck = np.float32(p.reflectance_chuck) * (
                1.0 + np.float32(p.texture_chuck) * self._texture(x0, y0, du, dv, p.texture_chuck_um, sc, 101))
            refl = refl + chuck * (1.0 - cover)
        return out + np.asarray(refl, dtype=np.float32) * scale

    def _wafer(self, x0: float, y0: float, du: np.ndarray, dv: np.ndarray, sigma: float, px: float, reach: float,
               aperture_um: float, contrast: float) -> np.ndarray:
        """Reflectance of the wafer surface (field, mesas, marks, texture) blurred by ``sigma`` µm."""
        p = self.params
        texture = (np.float32(p.texture_wafer) * self._texture(x0, y0, du, dv, p.texture_wafer_um, sigma, 0)
                   + np.float32(p.texture_wafer_coarse) * self._texture(x0, y0, du, dv, p.texture_wafer_coarse_um,
                                                                         sigma, 37))
        refl = np.float32(p.reflectance_field) + texture
        # mesas: nearest grid node of each pixel
        pitch = p.mesa_pitch_um
        rm = p.mesa_diameter_um / 2.0
        limit = (self.wafer_radius_mm() - p.edge_exclusion_mm) * 1000.0
        if math.hypot(x0, y0) - reach < limit + pitch:
            qx, qy = x0 / pitch, y0 / pitch
            fx, fy = math.floor(qx), math.floor(qy)
            tx = np.float32(qx - fx) + du * np.float32(1.0 / pitch)
            ty = np.float32(qy - fy) + dv * np.float32(1.0 / pitch)
            nx, ny = np.rint(tx), np.rint(ty)
            d = np.hypot((tx - nx) * np.float32(pitch), (ty - ny) * np.float32(pitch))
            r_ox = p.reflectance_aperture * (1.0 - contrast)
            mesa = _disc(d, rm, sigma) * np.float32(r_ox - p.reflectance_field)
            mesa = mesa - texture * _disc(d, rm, sigma)
            if aperture_um > 0:
                mesa = mesa + _disc(d, aperture_um / 2.0, sigma) * np.float32(p.reflectance_aperture - r_ox)
            # nodes without a mesa (edge exclusion, flat, around the marks): a lookup table over the nodes in view
            ax, ay = int(nx.min()), int(ny.min())
            allowed = np.array([[self._mesa_allowed((fx + i) * pitch, (fy + j) * pitch)
                                 for i in range(ax, int(nx.max()) + 1)] for j in range(ay, int(ny.max()) + 1)])
            if not allowed.all():
                mesa = mesa * allowed[ny.astype(np.int64) - ay, nx.astype(np.int64) - ax]
            refl = refl + mesa
        # marks (bright metal), rendered on the part of the view they can reach
        h, w = du.shape
        t = math.radians(p.tilt_deg)
        c, s = math.cos(t), math.sin(t)
        for m in p.marks:
            mx, my = m.x_mm * 1000.0 - x0, m.y_mm * 1000.0 - y0              # design µm from the image centre
            extent = m.radius_um + 5 * sigma
            if math.hypot(mx, my) > reach + extent:
                continue
            col = w / 2.0 + (mx * c - my * s) / px                            # the mark's centre in the image
            row = h / 2.0 - (mx * s + my * c) / px
            r0, r1 = max(0, int(row - extent / px) - 1), min(h, int(row + extent / px) + 2)
            c0, c1 = max(0, int(col - extent / px) - 1), min(w, int(col + extent / px) + 2)
            if r0 >= r1 or c0 >= c1:
                continue
            if np.isscalar(refl) or np.ndim(refl) == 0:
                refl = np.full(du.shape, refl, dtype=np.float32)
            ex, ey = du[r0:r1, c0:c1] - np.float32(mx), dv[r0:r1, c0:c1] - np.float32(my)
            a, b, c_ = m.rects()
            cover = (_box(ex, a[0], a[1], sigma) * _box(ey, a[2], a[3], sigma)
                     + _box(ex, b[0], b[1], sigma) * _box(ey, b[2], b[3], sigma)
                     - _box(ex, c_[0], c_[1], sigma) * _box(ey, c_[2], c_[3], sigma))
            window = refl[r0:r1, c0:c1]
            refl[r0:r1, c0:c1] = window + cover * (np.float32(p.reflectance_mark) - window)
        return refl

    # ---------------------------------------------------------------------------------------------- cameras
    def frame_source(self, names: Mapping[str, str] | None = None) -> Callable[[SimCameraDevice, int], np.ndarray]:
        """The ``frame_source`` of a :class:`SimCameraBackend`: the device's driver name → ``highmag``/``lowmag``
        through ``names`` (else the name itself, lower-cased), its exposure and gain attributes, the image size of
        the scene."""
        mapping = {k: v for k, v in (names or {}).items()}

        def source(device: SimCameraDevice, number: int) -> np.ndarray:
            camera = mapping.get(device.name) or device.name.lower()
            if camera not in CAMERAS:
                camera = "highmag"
            return self.render(camera, exposure_us=float(device.attribute_value(SIM_EXPOSURE)),
                               gain_db=float(device.attribute_value(SIM_GAIN)), number=number)
        return source

    def model_image(self, mark: int = 0, camera: str = "lowmag", margin_um: float = 60.0) -> np.ndarray:
        """An in-focus, noiseless picture of mark ``mark`` as ``camera`` sees it (the learned model of Search Object
        and of the Dual Objects tilt), cropped to the mark plus ``margin_um``, at the wavelength of the contrast peak
        and an exposure giving a mesa grey of 170."""
        m = self.params.marks[mark]
        px = self.pixel_size_um(camera)
        x0, x1, y0, y1 = (min(r[0] for r in m.rects()), max(r[1] for r in m.rects()),
                          min(r[2] for r in m.rects()), max(r[3] for r in m.rects()))
        side_x = math.ceil((x1 - x0 + 2 * margin_um) / px) | 1
        side_y = math.ceil((y1 - y0 + 2 * margin_um) / px) | 1
        cxd, cyd = m.x_mm + (x0 + x1) / 2000.0, m.y_mm + (y0 + y1) / 2000.0
        wx, wy = self.from_design(cxd, cyd)
        sx, sy = self.wafer_to_stage(wx, wy)
        wl = self.params.contrast_peak_nm
        z = self.focus_z(wx, wy, camera)
        led = camera == "lowmag"
        return self.render(camera, exposure_us=self.exposure_for(wl, camera=camera, led=led), width=side_x,
                           height=side_y,
                           stage_xy=(sx, sy), z_mm=z, wavelength_nm=wl, led=led, noise=False)


# ================================================================================================== cameras
class SceneCameraBackend(SimCameraBackend):
    """The simulated camera driver whose frames are the :class:`Scene` (sensor size and maximum frame rate of the
    scene's params)."""

    def __init__(self, scene: Scene, names: Mapping[str, str] | None = None) -> None:
        p = scene.params
        options = SimulationOptions(cameras=True, image_width=p.image_width, image_height=p.image_height,
                                    max_frame_rate=p.frame_rate_max, frame_rate=p.frame_rate_max)
        super().__init__(options, frame_source=scene.frame_source(names))
        self.scene = scene

    @classmethod
    def for_application(cls, app: Any) -> SceneCameraBackend:
        """The cameras of an :class:`alox.app.Application` in simulation: the scene reads the settings (the core's
        properties, else ``alox.ini``), the simulators of ``app.simulators`` (``x``, ``y``, ``z``: ``SimStage``;
        ``wl``: the monochromator emulator) directly, and the LED events of ``app.events``."""
        core = getattr(app, "core", None)
        settings = core.properties if core is not None else getattr(app, "typed_ini", None)
        params = SceneParams.from_settings(settings)
        sims: Mapping[str, Any] = getattr(app, "simulators", {})
        scene: Scene

        def xy() -> tuple[float, float]:
            x, y = sims.get("x"), sims.get("y")
            ox, oy = scene.origin()
            return (x.position if x is not None else ox, y.position if y is not None else oy)

        def z() -> float:
            stage = sims.get("z")
            return stage.position if stage is not None else params.z_focus_mm

        def wl() -> float:
            emulator = sims.get("wl")
            return float(emulator.position_nm) if emulator is not None else params.contrast_peak_nm

        scene = Scene(params, settings=settings, stage_xy=xy, stage_z=z, wavelength=wl)
        app.events.on(("device",), scene.on_event)
        s = app.settings
        return cls(scene, {s.high_mag.camera_name: "highmag", s.low_mag.camera_name: "lowmag"})


# ================================================================================================== demo
def make_demo(folder: str | Path, *, recipe: Mapping[str, Any] | None = None,
              settings_text: str | None = None) -> Path:
    """Write a runnable simulation into ``folder``: ``alox_setup.ini`` (its sub-folders), ``alox_default.ini`` (the
    demo's, or ``settings_text``), and the ``DEMO`` profile (``demo.ini`` + ``recipe`` overrides, the exposure table of
    the scene, the mark models ``cross.png`` / ``corner.png``). Returns the ``alox_setup.ini`` path."""
    from alox.config import TypedIni
    from alox.core.profiles import ProfileManager
    from aloxcam.imaging.png import write_png

    root = Path(folder)
    sub = {name: root / name for name in ("settings", "vision", "data", "profiles", "exposures", "jobs")}
    for path in sub.values():
        path.mkdir(parents=True, exist_ok=True)
    setup = root / "alox_setup.ini"
    setup.write_text("[folders]\n" + "".join(f'{key} = "{sub[name].as_posix()}"\n' for key, name in (
        ("settings", "settings"), ("vision", "vision"), ("data", "data"), ("profiles", "profiles"),
        ("exposures", "exposures"), ("jobhistory", "jobs"))), encoding="cp1252")
    defaults = root / "alox_default.ini"
    if settings_text is None:
        shutil.copyfile(DEMO_FOLDER / "alox_default.ini", defaults)
    else:
        defaults.write_text(settings_text, encoding="cp1252")
    settings = TypedIni.load(defaults)
    scene = Scene(SceneParams.from_settings(settings), settings=settings)
    profile = ProfileManager(sub["profiles"], sub["settings"] / "profile", sub["settings"] / "profile_temp")
    store = profile.new("DEMO", DEMO_FOLDER / "demo.ini")
    wavelengths = [float(v) for v in range(700, 1001, 25)]
    store.set("general.exposures", scene.exposure_table(wavelengths))
    for key, value in (recipe or {}).items():
        store.set(key, value)
    for index, name in enumerate(("cross.png", "corner.png")):
        if index < len(scene.params.marks):
            write_png(profile.work / name, scene.model_image(index))
    profile.save("DEMO")
    return setup


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m alox.devices.scene", description="ALOX simulated wafer scene")
    parser.add_argument("--make-demo", type=Path, metavar="DIR", help="write a runnable demo into DIR")
    parser.add_argument("--render", nargs=2, metavar=("CAMERA", "PNG"), help="write one frame at the wafer centre")
    args = parser.parse_args(argv)
    if args.make_demo:
        setup = make_demo(args.make_demo)
        print(f"demo written; run:  alox --simulate --setup-ini {setup}")
    if args.render:
        from alox.config import TypedIni
        from aloxcam.imaging.png import write_png

        settings = TypedIni.load(DEMO_FOLDER / "alox_default.ini")
        scene = Scene(SceneParams.from_settings(settings), settings=settings)
        camera, out = args.render
        wl = scene.params.contrast_peak_nm
        led = camera == "lowmag"
        image = scene.render(camera, exposure_us=scene.exposure_for(wl, camera=camera, led=led), wavelength_nm=wl,
                             led=led)
        write_png(Path(out), image)
        print(f"{camera}: {image.shape[1]}x{image.shape[0]} written to {out}")
    if not (args.make_demo or args.render):
        parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(_main())

