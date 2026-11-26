"""The image manager: the current image, its overlays, the JPEG snapshot and the websocket image stream.

Replaces ``alox_manager_image`` (the main display image), the overlay part of NI Vision (overlays stored with the
image, merged at display time) and ``alox_manager_websocket_images_stream``; spec ``docs/vision-core/map.md`` Part 4
§3.5, Part 5 §2.2, notes 14-15, Appendix A.3 and B.

Observed in the diagrams:

- ``alox_manager_image d0..d8``: one main image (``ALOX_MAIN_IMAGE``, U8) = "the last image acquired by the 'Get Image'
  function and associated datas (image custom datas)"; "Get Merged Overlays" copies it and merges its overlays into an
  RGB image (``ALOX_MAIN_OVERLAYS_IMAGE``).
- ``alox_api_v1_get_camera_jpegimage d``: "Get Merged Overlays", then, when the size differs from ``width`` x ``height``,
  ``IMAQ Resample`` **Zero Order** to exactly that size (the aspect ratio is not kept), then ``IMAQ Write JPEG String``
  with ``quality`` (0..1000; defaults 750, 612, 512). The route always answers 200 (an empty body on error).
- ``alox_manager_websocket_images_stream d0..d8``: Init creates the images and the options {Width 1024, Height 768,
  Quality 750}, Started? = F; Start Stream stores the options and sets Started?; Stop Stream clears it; "Stream Image"
  runs only when the websocket server runs, a client is connected and the stream is started: merge overlays, then when
  the size differs a centred crop **without scaling** (formula node ``left = (xRes - xWanted)/2``, ``top = (yRes -
  yWanted)/2``, ``right = left + xWanted``, ``bottom = top + yWanted``, int32), ``IMAQ Write JPEG String``, Base64,
  websocket ``image`` event ``{width, height, quality, jpeg}`` with the requested width/height/quality.
- There is **no period** in the stream manager: "Stream Image" is called with the current image by the function state
  machines and the core UI, i.e. one frame per new image. Here every :meth:`ImageStore.put` (and every overlay change)
  marks a frame due; a background thread encodes the newest state and publishes it, so frames that arrive while one is
  being encoded are coalesced (latest wins, as LabVIEW's notifier). ``stream_interval_s`` (default 0 = one frame per
  image, as LabVIEW) sets a minimum time between two frames.

Overlay names and switches (Appendix A.3, Part 5 §4): each name of :data:`OVERLAY_PROPERTIES` (the 21 names of
``alox.api.rest.OVERLAYS``) stands for one or more boolean properties of the **active profile**: ``GET
/v1/camera/overlay/<name>`` reports the first one, ``PUT`` writes all of them (in the profile working copy; it is saved
with the profile). In LabVIEW these are parameters of the functions, which then do not draw what is off; here the
functions describe what they found with :meth:`ImageStore.set_overlay` and the image manager draws only the groups
whose switch is on, so a switch also applies at once to the overlays already drawn. Defaults when the profile has no
such property: the Get Image ones (``highmag*``/``lowmag*``) off, as the defaults of
``alox_function_ll_get_image_bundle_profile_parameters d`` (``F``); the others on (their bundle VIs were not read).

Overlays belong to the image, as NI overlays: a new image (:meth:`ImageStore.put`) starts without overlays (Get Image
clears the overlay in its loop case 0, ``alox_function_ll_get_image_state_machine d88, d89``), so a function grabs its
image first, then sets its overlays. ``keep_overlays=True`` keeps them across images instead. Owner's decision I2 (the
application's setting, ``keep_results=True``): the result overlays of the functions stay on the following images (the
live display) until :meth:`ImageStore.clear_results`, called when the next function starts; only the camera overlays
(:data:`CAMERA_OVERLAYS`, redrawn by Get Image) belong to each image.

JPEG quality: NI's 0..1000 is mapped to Pillow's quality as ``round(q / 10)`` clamped to 1..95 (NI's scale is ten
times the IJG quality; 750 → 75, Pillow's default; above 95 Pillow disables chroma subsampling and the file grows
without visible gain). Pixels: ``uint8`` images are shown as they are; other types are clipped to 0..255; 2-D images
are grey, ``H x W x 3`` arrays RGB.

Fixed: a stream size larger than the image gave a negative crop rectangle (note 14, IMAQ error); the image is now
centred on a black background of exactly the requested size. Thread-safe: functions write from their threads, the API
reads from its own.
"""

from __future__ import annotations

import io
import logging
import math
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from alox.api import reports
from alox.events import Publish
from alox.functions.services import Circle, Line, Points, Rect, Shape, Text

__all__ = ["DEFAULT_STREAM", "GET_IMAGE_OVERLAYS", "OVERLAY_PROPERTIES", "ImageStore", "StreamOptions",
           "center_crop", "pillow_quality", "render", "to_display"]

log = logging.getLogger(__name__)

#: Overlay name → the boolean profile properties it stands for (Appendix A.3, in ``alox.api.rest.OVERLAYS`` order).
OVERLAY_PROPERTIES: dict[str, tuple[str, ...]] = {
    "highmagcamera": ("get.image.highmag.overlayname", "get.image.highmag.overlaytype",
                      "get.image.highmag.overlayindex", "get.image.highmag.overlaytime"),
    "lowmagcamera": ("get.image.lowmag.overlayname", "get.image.lowmag.overlaytype", "get.image.lowmag.overlayindex",
                     "get.image.lowmag.overlaytime"),
    "highmaginfos": ("get.image.highmag.overlayhwinfos",),
    "lowmaginfos": ("get.image.lowmag.overlayhwinfos",),
    "highmagscale": ("get.image.highmag.overlayscale",),
    "lowmagscale": ("get.image.lowmag.overlayscale",),
    "bowing": ("measure.bowing.overlaybowing", "measure.bowing.overlayparallelism"),
    "tilt": ("measure.tilt.overlaymatches", "measure.tilt.overlaystraightedge", "measure.tilt.overlayedges",
             "measure.tilt.overlaytilt"),
    "mesa": ("measure.oxyd.overlaymesa",),
    "oxyd": ("measure.oxyd.overlayoxyd",),
    "target": ("measure.oxyd.overlaytarget",),
    "diameters": ("measure.oxyd.overlaydiameters",),
    "edges": ("measure.oxyd.overlaypoints",),
    "autofocus": ("autofocus.overlayscore", "autofocus.overlayposition"),
    "refocus": ("refocus.overlayscore", "refocus.overlayposition", "refocus.overlayroi"),
    "refocusoxyd": ("refocus.oxyd.overlayscore", "refocus.oxyd.overlayposition", "refocus.oxyd.overlayroi"),
    "detectcircle": ("detect.vcsel.overlaybestcircle", "detect.vcsel.overlayallcircles", "detect.vcsel.overlayobjects"),
    "tracking": ("tracking.overlayzone",),
    "wavelength": ("wavelength.overlayzones", "wavelength.overlayresults"),
    "detectobject": ("search.object.overlaymatch", "search.object.overlaycontours"),
    "autoexposure": ("autoexpo.overlayroi", "autoexpo.overlayresults"),
}
#: The overlays drawn by Get Image (default off, ``alox_function_ll_get_image_bundle_profile_parameters d``).
GET_IMAGE_OVERLAYS = frozenset(name for name in OVERLAY_PROPERTIES if name.startswith(("highmag", "lowmag")))

_MISSING = object()


@dataclass(frozen=True)
class StreamOptions:
    """``alox_typedef_websocket_image_stream_options``."""

    width: int
    height: int
    quality: int


#: Options set by the stream manager's Init (``alox_manager_websocket_images_stream d3``).
DEFAULT_STREAM = StreamOptions(1024, 768, 750)


@dataclass(frozen=True)
class _Current:
    camera: str
    pixels: np.ndarray
    metadata: Mapping[str, Any]


# ================================================================================================== pure helpers
def pillow_quality(quality: int) -> int:
    """NI JPEG quality 0..1000 → Pillow 1..95 (module docstring)."""
    return min(max(round(float(quality) / 10.0), 1), 95)


def to_display(pixels: np.ndarray) -> Image.Image:
    """The pixels as an RGB Pillow image (``uint8`` unchanged, other types clipped to 0..255)."""
    arr = np.asarray(pixels)
    if arr.dtype == np.bool_:
        arr = arr.astype(np.uint8) * 255
    elif arr.dtype != np.uint8:
        arr = np.clip(np.nan_to_num(arr.astype(np.float64)), 0, 255).astype(np.uint8)
    if arr.ndim == 2:
        return Image.fromarray(np.ascontiguousarray(arr), "L").convert("RGB")
    if arr.ndim == 3 and arr.shape[2] in (3, 4):
        return Image.fromarray(np.ascontiguousarray(arr[..., :3]), "RGB")
    raise ValueError(f"not an image: shape {arr.shape}")


@lru_cache(maxsize=16)
def _font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    try:
        return ImageFont.load_default(size=max(1, int(size)))
    except TypeError:                                   # Pillow < 10.1: the fixed bitmap font
        return ImageFont.load_default()


def _xy(x: float, y: float) -> tuple[float, float]:
    return float(x), float(y)


def _draw(draw: ImageDraw.ImageDraw, shape: Shape) -> None:
    if isinstance(shape, Line):
        draw.line([_xy(shape.x1, shape.y1), _xy(shape.x2, shape.y2)], fill=shape.color, width=max(1, shape.width))
    elif isinstance(shape, Circle):
        box = [shape.cx - shape.r, shape.cy - shape.r, shape.cx + shape.r, shape.cy + shape.r]
        draw.ellipse(box, outline=shape.color, width=max(1, shape.width), fill=shape.color if shape.fill else None)
    elif isinstance(shape, Rect):
        if shape.angle_deg % 360.0 == 0.0:
            box = [min(shape.left, shape.right), min(shape.top, shape.bottom),
                   max(shape.left, shape.right), max(shape.top, shape.bottom)]
            draw.rectangle(box, outline=shape.color, width=max(1, shape.width))
        else:
            cx, cy = (shape.left + shape.right) / 2.0, (shape.top + shape.bottom) / 2.0
            a = math.radians(shape.angle_deg)
            ca, sa = math.cos(a), math.sin(a)
            corners = [(shape.left, shape.top), (shape.right, shape.top), (shape.right, shape.bottom),
                       (shape.left, shape.bottom)]
            pts = [(cx + (x - cx) * ca - (y - cy) * sa, cy + (x - cx) * sa + (y - cy) * ca) for x, y in corners]
            draw.polygon(pts, outline=shape.color, width=max(1, shape.width))
    elif isinstance(shape, Points):
        pts = [_xy(x, y) for x, y in shape.xy]
        if shape.marker > 0:
            half = shape.marker / 2.0
            for x, y in pts:
                draw.line([(x - half, y), (x + half, y)], fill=shape.color, width=max(1, shape.width))
                draw.line([(x, y - half), (x, y + half)], fill=shape.color, width=max(1, shape.width))
        elif len(pts) == 1:
            draw.point(pts, fill=shape.color)
        elif pts:
            draw.line(pts + pts[:1] if shape.closed else pts, fill=shape.color, width=max(1, shape.width))
    elif isinstance(shape, Text):
        draw.text(_xy(shape.x, shape.y), shape.text, fill=shape.color, font=_font(shape.size), anchor=shape.anchor)
    else:
        raise TypeError(f"not an overlay shape: {shape!r}")


def render(pixels: np.ndarray, overlays: Sequence[Sequence[Shape]] = ()) -> Image.Image:
    """``Get Merged Overlays``: the image in RGB with the overlay shapes drawn on it (in order)."""
    image = to_display(pixels)
    draw = ImageDraw.Draw(image)
    for shapes in overlays:
        for shape in shapes:
            try:
                _draw(draw, shape)
            except (ValueError, TypeError, OSError) as exc:     # one bad shape must not cost the image
                log.warning("overlay shape not drawn (%s): %r", exc, shape)
    return image


def center_crop(image: Image.Image, width: int, height: int) -> Image.Image:
    """The stream's centred crop without scaling (``alox_manager_websocket_images_stream d8``): ``left = (xRes -
    xWanted)/2`` (int32, truncated towards zero), ``top`` likewise. A size larger than the image keeps it centred on
    black (fixed: LabVIEW's rectangle was negative)."""
    if image.size == (width, height):
        return image
    left = int((image.width - width) / 2)
    top = int((image.height - height) / 2)
    return image.crop((left, top, left + width, top + height))


def _encode(image: Image.Image, quality: int) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=pillow_quality(quality))
    return buffer.getvalue()


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().upper() in ("TRUE", "1", "ON", "T")
    return bool(value)


# ================================================================================================== the manager
#: The overlays Get Image draws on each image (cleared by every new image).
CAMERA_OVERLAYS = frozenset({"highmagcamera", "lowmagcamera", "highmaginfos", "lowmaginfos", "highmagscale",
                             "lowmagscale"})


class ImageStore:
    """The image manager (module docstring). Implements :class:`alox.functions.services.ImageSink` (``put``,
    ``last``, ``set_overlay(name, shapes)``, ``clear_overlay``) for the functions and
    :class:`alox.core.core.ImageSource` (``image_info``, ``jpeg``, ``stream_start``/``stop``, ``overlay``,
    ``enable_overlay(name, on)``, ``overlays``) for the core and the API.

    ``publish``: the event bus (the stream's ``ws.image`` events). ``profile``: the active profile (``get``/``set``),
    which holds the overlay switches; without one they live in memory. ``has_client``: TRUE when a websocket client is
    connected (LabVIEW streams only then; default: always). ``clock``: wall clock for :meth:`image_info` when the
    metadata has no ``time``."""

    def __init__(self, *, publish: Publish | None = None, profile: Any = None,
                 has_client: Callable[[], bool] | None = None, stream_interval_s: float = 0.0,
                 keep_overlays: bool = False, keep_results: bool = False,
                 clock: Callable[[], float] = time.time) -> None:
        if not (math.isfinite(stream_interval_s) and stream_interval_s >= 0):
            raise ValueError(f"stream_interval_s must be finite and >= 0, got {stream_interval_s!r}")
        self.publish = publish
        self.profile = profile
        self.has_client = has_client
        self.stream_interval_s = float(stream_interval_s)
        self.keep_overlays = keep_overlays
        self.keep_results = keep_results
        self._clock = clock
        self._lock = threading.Condition()
        self._current: _Current | None = None
        self._last: dict[str, np.ndarray] = {}
        self._info: dict[str, tuple[int, float]] = {}
        self._shapes: dict[str, tuple[Shape, ...]] = {}
        self._flags: dict[str, bool] = {}
        self._stream = DEFAULT_STREAM
        self._streaming = False
        self._due = 0                                         # frames requested (generation counter)
        self._sent = 0                                        # generation of the last frame handled
        self._closing = False
        self._thread: threading.Thread | None = None

    # ============================================================================================== ImageSink
    def put(self, camera: str, image: np.ndarray, *, metadata: Mapping[str, Any] | None = None) -> None:
        """A new image of ``camera``: it becomes the current image (without overlays unless ``keep_overlays``)."""
        pixels = np.array(image, copy=True)
        if pixels.ndim not in (2, 3) or pixels.size == 0:
            raise ValueError(f"not an image: shape {pixels.shape}")
        pixels.flags.writeable = False
        meta = dict(metadata or {})
        stamp = meta.get("time")
        with self._lock:
            count = self._info.get(camera, (0, 0.0))[0] + 1
            self._info[camera] = (count, float(stamp) if isinstance(stamp, int | float) else self._clock())
            self._last[camera] = pixels
            self._current = _Current(camera, pixels, meta)
            if self.keep_results:
                for name in [n for n in self._shapes if n in CAMERA_OVERLAYS]:
                    del self._shapes[name]
            elif not self.keep_overlays:
                self._shapes.clear()
            self._request_frame()

    def clear_results(self) -> None:
        """Remove the result overlays of the functions (owner I2: when the next function starts)."""
        with self._lock:
            for name in [n for n in self._shapes if n not in CAMERA_OVERLAYS]:
                del self._shapes[name]
            self._request_frame()

    def last(self, camera: str) -> np.ndarray | None:
        with self._lock:
            return self._last.get(camera)

    def set_overlay(self, name: str, shapes: Sequence[Shape]) -> None:
        """Replace the shapes of overlay ``name`` (drawn on the current image while its switch is on)."""
        items = tuple(shapes)
        for shape in items:
            if not isinstance(shape, Line | Circle | Rect | Points | Text):
                raise TypeError(f"overlay {name!r}: not a shape: {shape!r}")
        with self._lock:
            self._shapes[name] = items
            self._request_frame()

    def clear_overlay(self, name: str) -> None:
        with self._lock:
            if self._shapes.pop(name, None) is not None:
                self._request_frame()

    # ============================================================================================== reading
    def current(self) -> tuple[str, np.ndarray, Mapping[str, Any]] | None:
        """``(camera, pixels, metadata)`` of the current image (the pixels are read-only)."""
        with self._lock:
            cur = self._current
        return None if cur is None else (cur.camera, cur.pixels, dict(cur.metadata))

    def shapes(self, name: str) -> tuple[Shape, ...]:
        with self._lock:
            return self._shapes.get(name, ())

    def image_info(self, camera: str) -> tuple[int, float | None]:
        """``(images stored for camera, time of the last one)``."""
        with self._lock:
            count, stamp = self._info.get(camera, (0, None))
        return count, stamp

    def merged(self) -> Image.Image | None:
        """"Get Merged Overlays": the current image with the overlays whose switch is on; None without image."""
        with self._lock:
            cur = self._current
            groups = list(self._shapes.items())
        if cur is None:
            return None
        shown = [shapes for name, shapes in groups if name not in OVERLAY_PROPERTIES or self.overlay(name)]
        return render(cur.pixels, shown)

    def jpeg(self, quality: int, width: int, height: int) -> bytes:
        """``GET /v1/camera/jpegimage``: merged overlays, zero-order resampled to ``width`` x ``height`` (aspect not
        kept), JPEG; ``b""`` when there is no image yet (LabVIEW answers 200 with an empty body)."""
        if width < 1 or height < 1:
            raise ValueError(f"invalid size {width} x {height}")
        image = self.merged()
        if image is None:
            return b""
        if image.size != (width, height):
            image = image.resize((int(width), int(height)), Image.Resampling.NEAREST)
        return _encode(image, quality)

    # ============================================================================================== overlay switches
    def overlays(self) -> list[str]:
        """``GET /v1/camera/overlays``."""
        return list(OVERLAY_PROPERTIES)

    def _keys(self, name: str) -> tuple[str, ...]:
        try:
            return OVERLAY_PROPERTIES[name]
        except KeyError:
            raise ValueError(f"unknown overlay {name!r}") from None

    def _read(self, key: str) -> Any:
        if self.profile is None:
            return _MISSING
        try:
            return self.profile.get(key, _MISSING)
        except (RuntimeError, LookupError, TypeError, ValueError) as exc:     # e.g. no profile loaded
            log.debug("overlay switch %s not readable: %s", key, exc)
            return _MISSING

    def overlay(self, name: str) -> bool:
        """``GET /v1/camera/overlay/<name>``: the first property of the group (default: module docstring)."""
        keys = self._keys(name)
        value = self._read(keys[0])
        if value is _MISSING:
            with self._lock:
                return self._flags.get(name, name not in GET_IMAGE_OVERLAYS)
        return _truthy(value)

    def enable_overlay(self, name: str, on: bool) -> None:
        """``PUT /v1/camera/overlay/<name>``: every property of the group in the active profile (in memory when there
        is no profile)."""
        keys = self._keys(name)
        with self._lock:
            self._flags[name] = bool(on)
        if self.profile is not None:
            for key in keys:
                try:
                    self.profile.set(key, bool(on))
                except (RuntimeError, LookupError, TypeError, ValueError) as exc:
                    log.warning("overlay %s: %s not written: %s", name, key, exc)
        with self._lock:
            self._request_frame()

    # ============================================================================================== stream
    @property
    def stream_options(self) -> StreamOptions:
        with self._lock:
            return self._stream

    @property
    def streaming(self) -> bool:
        """"Is Started?"."""
        with self._lock:
            return self._streaming

    def stream_start(self, quality: int, width: int, height: int) -> None:
        """"Start Stream": the options are stored; frames follow the next images."""
        if width < 1 or height < 1:
            raise ValueError(f"invalid size {width} x {height}")
        with self._lock:
            self._stream = StreamOptions(int(width), int(height), int(quality))
            self._streaming = True
            self._sent = self._due                            # LabVIEW streams the next image, not the last one
            if self._thread is None or not self._thread.is_alive():
                self._closing = False
                self._thread = threading.Thread(target=self._stream_loop, name="image-stream", daemon=True)
                self._thread.start()

    def stream_stop(self) -> None:
        """"Stop Stream"."""
        with self._lock:
            self._streaming = False
            self._lock.notify_all()

    def close(self, timeout: float = 5.0) -> None:
        """Stop the stream thread (exit cycle "image stream Exit")."""
        with self._lock:
            self._streaming = False
            self._closing = True
            self._lock.notify_all()
            thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def flush(self, timeout: float = 5.0) -> bool:
        """Wait until every frame requested so far has been handled (tests, shutdown)."""
        end = time.monotonic() + timeout
        with self._lock:
            while self._streaming and self._sent < self._due:
                left = end - time.monotonic()
                if left <= 0:
                    return False
                self._lock.wait(left)
        return True

    def stream_frame(self) -> Any:
        """One ``image`` event of the current state (merged, centred crop, JPEG, Base64), or None without image."""
        options = self.stream_options
        image = self.merged()
        if image is None:
            return None
        frame = center_crop(image, options.width, options.height)
        return reports.image_event(_encode(frame, options.quality), options.width, options.height, options.quality)

    def _request_frame(self) -> None:                         # with the lock held
        self._due += 1
        self._lock.notify_all()

    def _stream_loop(self) -> None:
        last = -math.inf
        while True:
            with self._lock:
                while not self._closing and not (self._streaming and self._sent < self._due):
                    self._lock.wait()
                if self._closing:
                    return
                wait = last + self.stream_interval_s - time.monotonic()
                if wait > 0:
                    self._lock.wait(wait)
                    continue
                generation = self._due
            try:
                if self.publish is not None and (self.has_client is None or self.has_client()):
                    event = self.stream_frame()
                    if event is not None:
                        self.publish(event)
            except Exception:
                log.exception("image stream frame failed")
            last = time.monotonic()
            with self._lock:
                self._sent = max(self._sent, generation)
                self._lock.notify_all()
