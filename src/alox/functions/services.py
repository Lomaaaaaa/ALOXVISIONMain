"""What a measurement function receives, and the overlay shapes it draws (``docs/vision-core/map.md`` Part 3 §2.1).

In LabVIEW every state machine reaches the hardware and the shared data through global managers (camera, XY,
monochromator, exposure LUT, image, calibration, log, profile). Here a function receives one :class:`Services` object
at construction (constructor injection, as :mod:`alox.functions.focus`): the devices by name, the active profile, the
application settings, the exposure table, the image store and the folders. Tests pass fakes.

Images and overlays (map Part 4 §3.5, Part 5 ``jpegimage``): a function stores the image it grabbed with
:meth:`ImageSink.put` and describes what it found as overlay shapes under its overlay name (``detectcircle``,
``tracking``, ``mesa``, ...: :data:`alox.api.rest.OVERLAYS`) with :meth:`ImageSink.set_overlay`. The image manager
draws the enabled overlays on the current image for the JPEG route and the stream. Coordinates are image pixels
(x right, y down), colours ``(r, g, b)``.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np

from alox.events import Publish

__all__ = ["CAMERA_DEVICES", "Circle", "ImageSink", "Line", "Points", "ProfileReader", "Rect", "Services", "Shape",
           "Text", "camera_device_name"]

RGB = tuple[int, int, int]
GREEN: RGB = (0, 255, 0)

#: ``alox_typedef_camera_type`` value → device name.
CAMERA_DEVICES = ("highmag", "lowmag")


def camera_device_name(camera_type: Any) -> str:
    """``0`` / ``"High Mag."`` / ``CameraType.HIGH_MAG`` → ``"highmag"``; ``1`` / ``"Low Mag."`` → ``"lowmag"``;
    a device name is returned as is."""
    if isinstance(camera_type, str):
        text = camera_type.strip().lower().replace(" ", "").replace(".", "")
        if text in ("highmag", "high"):
            return "highmag"
        if text in ("lowmag", "low"):
            return "lowmag"
        raise ValueError(f"unknown camera {camera_type!r}")
    index = int(camera_type)
    if index not in (0, 1):
        raise ValueError(f"unknown camera type {camera_type!r}")
    return CAMERA_DEVICES[index]


# ================================================================================================== overlay shapes
@dataclass(frozen=True)
class Line:
    x1: float
    y1: float
    x2: float
    y2: float
    color: RGB = GREEN
    width: int = 1


@dataclass(frozen=True)
class Circle:
    cx: float
    cy: float
    r: float
    color: RGB = GREEN
    width: int = 1
    fill: bool = False


@dataclass(frozen=True)
class Rect:
    left: float
    top: float
    right: float
    bottom: float
    color: RGB = GREEN
    width: int = 1
    angle_deg: float = 0.0                        # rotated rectangle (NI ROI "Rotated Rectangle"), around its centre


@dataclass(frozen=True)
class Points:
    """Polyline (``closed``) or marks (``marker`` > 0: size of a ``+`` drawn at each point)."""

    xy: tuple[tuple[float, float], ...]
    color: RGB = GREEN
    width: int = 1
    closed: bool = False
    marker: int = 0


@dataclass(frozen=True)
class Text:
    x: float
    y: float
    text: str
    color: RGB = GREEN
    size: int = 14                                # pixel height
    anchor: str = "lt"                            # l/m/r + t/m/b, as Pillow


Shape = Line | Circle | Rect | Points | Text


# ================================================================================================== interfaces
class ProfileReader(Protocol):
    """The active profile (:class:`alox.core.profiles.ProfileManager`) or the settings (``Properties``)."""

    def get(self, dotted: str, default: Any = None) -> Any: ...


class ImageSink(Protocol):
    """The image manager as the functions use it (implemented by :mod:`alox.core.images`)."""

    def put(self, camera: str, image: np.ndarray, *, metadata: Mapping[str, Any] | None = None) -> None:
        """The newest image of ``camera`` (``highmag``/``lowmag``); it becomes the current displayed image."""

    def last(self, camera: str) -> np.ndarray | None:
        """The newest image stored for ``camera``."""

    def set_overlay(self, name: str, shapes: Sequence[Shape]) -> None:
        """Replace the shapes of overlay ``name`` (empty: nothing drawn)."""

    def clear_overlay(self, name: str) -> None:
        ...


class _NullImages:
    """Keeps the last image per camera, draws nothing (default when no image manager is given)."""

    def __init__(self) -> None:
        self.images: dict[str, np.ndarray] = {}
        self.overlays: dict[str, tuple[Shape, ...]] = {}

    def put(self, camera: str, image: np.ndarray, *, metadata: Mapping[str, Any] | None = None) -> None:
        self.images[camera] = image

    def last(self, camera: str) -> np.ndarray | None:
        return self.images.get(camera)

    def set_overlay(self, name: str, shapes: Sequence[Shape]) -> None:
        self.overlays[name] = tuple(shapes)

    def clear_overlay(self, name: str) -> None:
        self.overlays.pop(name, None)


class _NoProfile:
    def get(self, dotted: str, default: Any = None) -> Any:
        return default


@dataclass
class Services:
    """Everything a function may use. ``devices``: ``highmag``, ``lowmag``, ``z``, ``xy``, ``wl`` (any may be missing:
    :meth:`device` raises a clear error). ``profile``: the active recipe; ``settings``: ``alox.ini``; ``exposures``: an
    :class:`alox.core.exposures.ExposureTable`; ``folders``: :class:`alox.core.folders.Folders` (None in tests: no files
    written); ``publish``: the event bus; ``log``: the application log (``info``/``warning``/``error``);
    ``statuses``: the status IDs of the high-level measurements by name (``"mesa"``, ``"tilt"``, ``"bowing"``:
    :class:`alox.core.status.FunctionStatusID`, ``set(code)``), which the high-level functions update during their run
    (each change becomes the websocket ``<name>_status`` event); missing in tests unless given."""

    devices: Mapping[str, Any] = field(default_factory=dict)
    profile: ProfileReader = field(default_factory=_NoProfile)
    settings: ProfileReader = field(default_factory=_NoProfile)
    exposures: Any = None
    images: ImageSink = field(default_factory=_NullImages)
    folders: Any = None
    publish: Publish | None = None
    log: Any = None
    statuses: Mapping[str, Any] = field(default_factory=dict)
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep

    def device(self, name: str) -> Any:
        try:
            return self.devices[name]
        except KeyError:
            raise LookupError(f"device {name!r} is not available") from None

    def set_status(self, measure: str, code: int) -> None:
        """Set the status ID of a high-level measurement (``"mesa"``, ``"tilt"``, ``"bowing"``) when it is given."""
        holder = self.statuses.get(measure)
        if holder is not None:
            holder.set(int(code))

    def camera(self, camera_type: Any) -> Any:
        return self.device(camera_device_name(camera_type))
