"""The image manager (``alox.core.images``): current image, overlays and their switches, JPEG route, image stream."""

from __future__ import annotations

import base64
import io
import threading
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from alox.core.core import Core, CoreConfig
from alox.core.folders import Folders
from alox.core.images import (
    DEFAULT_STREAM,
    GET_IMAGE_OVERLAYS,
    OVERLAY_PROPERTIES,
    ImageStore,
    center_crop,
    pillow_quality,
    render,
    to_display,
)
from alox.events import EventBus
from alox.functions.services import Circle, ImageSink, Line, Points, Rect, Text

WHITE = (255, 255, 255)
RED = (255, 0, 0)


def black(h: int = 40, w: int = 60) -> np.ndarray:
    return np.zeros((h, w), np.uint8)


def decode(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


class FakeProfile:
    def __init__(self, values: dict[str, Any] | None = None, loaded: bool = True) -> None:
        self.values = dict(values or {})
        self.loaded = loaded

    def get(self, key: str, default: Any = None) -> Any:
        if not self.loaded:
            raise RuntimeError("no profile loaded")
        return self.values.get(key, default)

    def set(self, key: str, value: Any) -> None:
        if not self.loaded:
            raise RuntimeError("no profile loaded")
        self.values[key] = value


# ================================================================================================== pure helpers
def test_quality_mapping():
    assert pillow_quality(750) == 75 and pillow_quality(0) == 1 and pillow_quality(1000) == 95
    assert pillow_quality(504) == 50


def test_to_display_types():
    assert to_display(black()).mode == "RGB"
    assert to_display(np.full((4, 4), 300, np.uint16)).getpixel((0, 0)) == WHITE        # clipped
    rgb = np.zeros((4, 4, 3), np.uint8)
    rgb[..., 0] = 200
    assert to_display(rgb).getpixel((1, 1)) == (200, 0, 0)
    with pytest.raises(ValueError):
        to_display(np.zeros((2, 2, 2, 2), np.uint8))


def test_center_crop_crops_and_pads():
    image = Image.new("RGB", (10, 8), WHITE)
    image.putpixel((0, 0), RED)
    cropped = center_crop(image, 6, 4)                                   # left = 2, top = 2
    assert cropped.size == (6, 4) and cropped.getpixel((0, 0)) == WHITE
    assert center_crop(image, 9, 8).getpixel((0, 0)) == RED              # left = int(1 / 2) = 0
    padded = center_crop(image, 14, 12)                                  # larger: centred on black (fixed)
    assert padded.size == (14, 12)
    assert padded.getpixel((0, 0)) == (0, 0, 0) and padded.getpixel((2, 2)) == RED
    assert center_crop(image, 10, 8) is image


def test_render_draws_every_shape():
    shapes = [Line(0, 5, 59, 5, WHITE), Circle(30, 20, 5, RED, fill=True), Rect(5, 25, 15, 35, WHITE),
              Rect(40, 25, 50, 35, WHITE, angle_deg=45.0), Points(((2, 2), (8, 2), (8, 8)), WHITE, closed=True),
              Points(((50, 10),), WHITE, marker=4), Text(1, 39, "AB", WHITE, 10, "ls")]
    image = render(black(), [shapes])
    assert image.getpixel((30, 5)) == WHITE and image.getpixel((30, 20)) == RED
    assert image.getpixel((5, 30)) == WHITE and image.getpixel((50, 10)) == WHITE
    assert np.asarray(image)[30:40, 0:20].any()                           # the text


def test_render_skips_a_bad_shape():
    image = render(black(), [[Text(1, 1, "x", WHITE, 10, "zz"), Line(0, 0, 10, 0, WHITE)]])
    assert image.getpixel((5, 0)) == WHITE


# ================================================================================================== ImageSink
def test_store_is_an_image_sink_and_keeps_images_per_camera():
    store = ImageStore(clock=lambda: 123.0)
    sink: ImageSink = store                                               # the functions' interface
    assert sink is store
    assert store.last("highmag") is None and store.current() is None and store.image_info("highmag") == (0, None)
    source = black()
    store.put("highmag", source, metadata={"time": 10.5, "name": "HighMag"})
    store.put("lowmag", black(5, 5))
    store.put("highmag", np.ones((3, 3), np.uint8), metadata={"time": 11.0})
    assert store.image_info("highmag") == (2, 11.0) and store.image_info("lowmag") == (1, 123.0)
    assert store.last("lowmag").shape == (5, 5)
    camera, pixels, meta = store.current()
    assert camera == "highmag" and pixels.shape == (3, 3) and meta["time"] == 11.0
    with pytest.raises(ValueError):
        pixels[0, 0] = 9                                                  # read-only copy
    source[0, 0] = 7
    store.put("highmag", source)
    source[0, 0] = 8
    assert store.last("highmag")[0, 0] == 7
    with pytest.raises(ValueError):
        store.put("highmag", np.zeros((0, 3), np.uint8))


def test_a_new_image_starts_without_overlays_unless_kept():
    store = ImageStore()
    store.put("highmag", black())
    store.set_overlay("mesa", [Line(0, 0, 5, 5)])
    assert store.shapes("mesa")
    store.put("highmag", black())
    assert store.shapes("mesa") == ()
    keeping = ImageStore(keep_overlays=True)
    keeping.put("highmag", black())
    keeping.set_overlay("mesa", [Line(0, 0, 5, 5)])
    keeping.put("highmag", black())
    assert keeping.shapes("mesa")
    keeping.clear_overlay("mesa")
    keeping.clear_overlay("mesa")
    assert keeping.shapes("mesa") == ()
    with pytest.raises(TypeError):
        store.set_overlay("mesa", ["not a shape"])


# ================================================================================================== switches
def test_overlay_names_are_the_api_names():
    rest = pytest.importorskip("alox.api.rest")
    assert tuple(OVERLAY_PROPERTIES) == rest.OVERLAYS
    assert ImageStore().overlays() == list(rest.OVERLAYS)
    assert GET_IMAGE_OVERLAYS == {"highmagcamera", "lowmagcamera", "highmaginfos", "lowmaginfos", "highmagscale",
                                  "lowmagscale"}


def test_switches_default_and_live_in_the_profile():
    profile = FakeProfile({"measure.oxyd.overlaymesa": False, "get.image.highmag.overlayscale": "TRUE"})
    store = ImageStore(profile=profile)
    assert store.overlay("mesa") is False and store.overlay("highmagscale") is True
    assert store.overlay("tilt") is True and store.overlay("lowmagcamera") is False      # defaults
    store.enable_overlay("tilt", False)
    assert {k: v for k, v in profile.values.items() if k.startswith("measure.tilt")} == {
        "measure.tilt.overlaymatches": False, "measure.tilt.overlaystraightedge": False,
        "measure.tilt.overlayedges": False, "measure.tilt.overlaytilt": False}
    assert store.overlay("tilt") is False
    with pytest.raises(ValueError):
        store.overlay("nope")
    with pytest.raises(ValueError):
        store.enable_overlay("nope", True)


def test_switches_without_a_loaded_profile_stay_in_memory():
    for store in (ImageStore(), ImageStore(profile=FakeProfile(loaded=False))):
        assert store.overlay("highmaginfos") is False
        store.enable_overlay("highmaginfos", True)
        assert store.overlay("highmaginfos") is True


def test_merged_image_draws_only_enabled_groups():
    store = ImageStore(profile=FakeProfile())
    assert store.merged() is None
    store.put("lowmag", black())
    store.set_overlay("mesa", [Line(0, 10, 59, 10, WHITE)])
    store.set_overlay("lowmagscale", [Line(0, 20, 59, 20, RED)])          # Get Image group: off by default
    store.set_overlay("custom", [Line(0, 30, 59, 30, WHITE)])             # not an API name: always drawn
    merged = store.merged()
    assert merged.getpixel((5, 10)) == WHITE and merged.getpixel((5, 20)) == (0, 0, 0)
    assert merged.getpixel((5, 30)) == WHITE
    store.enable_overlay("lowmagscale", True)
    store.enable_overlay("mesa", False)
    merged = store.merged()
    assert merged.getpixel((5, 10)) == (0, 0, 0) and merged.getpixel((5, 20)) == RED


# ================================================================================================== JPEG route
def test_jpeg_resamples_to_the_requested_size():
    store = ImageStore()
    assert store.jpeg(750, 612, 512) == b""                               # no image yet: empty body
    pixels = black(40, 60)
    pixels[:, :30] = 255
    store.put("highmag", pixels)
    image = decode(store.jpeg(750, 20, 30))                               # aspect not kept
    assert image.format == "JPEG" and image.size == (20, 30)
    assert image.convert("L").getpixel((2, 15)) > 200 and image.convert("L").getpixel((17, 15)) < 50
    assert decode(store.jpeg(750, 60, 40)).size == (60, 40)
    small, large = store.jpeg(0, 60, 40), store.jpeg(1000, 60, 40)
    assert len(small) < len(large)
    with pytest.raises(ValueError):
        store.jpeg(750, 0, 10)


# ================================================================================================== stream
def stream_events(bus: EventBus, store: ImageStore, action) -> list:
    sub = bus.subscribe(("ws.image",))
    action()
    assert store.flush(5.0)
    events = sub.drain()
    sub.close()
    return events


def test_stream_sends_one_cropped_frame_per_image():
    bus = EventBus()
    store = ImageStore(publish=bus.publish)
    assert store.stream_options == DEFAULT_STREAM and not store.streaming
    try:
        assert stream_events(bus, store, lambda: store.put("highmag", black())) == []      # not started
        store.put("highmag", black())
        events = stream_events(bus, store, lambda: store.stream_start(500, 20, 10))
        assert events == []                                               # the next image, not the last one
        pixels = black(40, 60)
        pixels[15:25, 20:40] = 255                                        # the centre 20 x 10
        events = stream_events(bus, store, lambda: store.put("highmag", pixels))
        assert len(events) == 1
        data = events[0].data
        assert (data["width"], data["height"], data["quality"]) == (20, 10, 500)
        frame = decode(base64.b64decode(data["jpeg"])).convert("L")
        assert frame.size == (20, 10) and np.asarray(frame).min() > 200        # exactly the centre, no scaling
        events = stream_events(bus, store, lambda: store.set_overlay("mesa", [Line(0, 0, 1, 1)]))
        assert len(events) == 1                                            # an overlay change is a new frame
        store.stream_stop()
        assert not store.streaming
        assert stream_events(bus, store, lambda: store.put("highmag", pixels)) == []
    finally:
        store.close()


def test_stream_coalesces_and_respects_the_client_check():
    bus = EventBus()
    connected = threading.Event()
    store = ImageStore(publish=bus.publish, has_client=connected.is_set)
    try:
        store.stream_start(750, 8, 8)
        assert stream_events(bus, store, lambda: store.put("lowmag", black())) == []        # no client
        connected.set()

        def burst() -> None:
            for value in range(10):
                store.put("lowmag", np.full((8, 8), value * 25, np.uint8))
        events = stream_events(bus, store, burst)
        assert 1 <= len(events) <= 10
        last = decode(base64.b64decode(events[-1].data["jpeg"])).convert("L")
        assert abs(last.getpixel((4, 4)) - 225) < 10                       # the newest image wins
    finally:
        store.close()
    with pytest.raises(ValueError):
        ImageStore(stream_interval_s=-1)
    with pytest.raises(ValueError):
        ImageStore().stream_start(750, 0, 1)


def test_concurrent_writers_and_readers():
    store = ImageStore()
    errors: list[BaseException] = []

    def writer(camera: str) -> None:
        try:
            for i in range(30):
                store.put(camera, np.full((16, 16), i, np.uint8))
                store.set_overlay("mesa", [Line(0, 0, i % 16, 15)])
        except BaseException as exc:                                        # noqa: BLE001 - collected for the assert
            errors.append(exc)

    def reader() -> None:
        try:
            for _ in range(30):
                store.jpeg(750, 8, 8)
                store.image_info("highmag")
        except BaseException as exc:                                        # noqa: BLE001 - collected for the assert
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(c,)) for c in ("highmag", "lowmag")]
    threads += [threading.Thread(target=reader) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert not errors
    assert store.image_info("highmag")[0] == 30 and store.image_info("lowmag")[0] == 30


# ================================================================================================== core
def test_core_delegates_to_the_store(tmp_path: Path):
    root = tmp_path / "alox"
    folders = Folders(exe=tmp_path / "exe", settings=root / "settings", vision=root / "vision", data=root / "data",
                      profiles=root / "profiles", exposures=root / "exposures", jobhistory=root / "jobhistory")
    store = ImageStore()
    core = Core(folders, config=CoreConfig(supervise_hardware=False, heartbeat=False), images=store)
    assert core.overlay("highmagcamera") is False
    core.set_overlay("highmagcamera", True)
    assert store.overlay("highmagcamera") is True and core.overlays() == store.overlays()
    store.put("highmag", black())
    assert decode(core.jpeg(750, 30, 20)).size == (30, 20)
    core.stream_start(750, 10, 10)
    assert store.streaming
    core.stream_stop()
    store.close()
    assert core.camera_status("highmag")["ImageTotal"] == "1"
