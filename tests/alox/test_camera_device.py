"""The camera device (``alox.devices.camera``) on the camera server's simulated IMAQdx backend."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from alox.devices.base import DeviceError, DeviceState
from alox.devices.camera import Camera
from alox.events import Event
from aloxcam.camera.attributes import LINE_INVERTER, USER_OUTPUT_VALUE
from aloxcam.camera.sim import SIM_FRAME_RATE, SimCameraBackend, frame_number_of
from aloxcam.imaging.png import write_png
from aloxcam.settings import PLUG_USB, CameraSettings, LightSettings, SimulationOptions

SIM = SimulationOptions(image_width=64, image_height=48, frame_rate=50.0)


@pytest.fixture
def events() -> list[Event]:
    return []


@pytest.fixture
def make_camera(events: list[Event]) -> Iterator[Callable[..., tuple[Camera, SimCameraBackend]]]:
    cameras: list[Camera] = []

    def factory(*, settings: CameraSettings | None = None, lights: LightSettings | None = None,
                open_: bool = True, **kwargs: Any) -> tuple[Camera, SimCameraBackend]:
        backend = SimCameraBackend(kwargs.pop("sim", SIM))
        camera = Camera(backend, settings or CameraSettings.default_high_mag(), name="hm", lights=lights,
                        publish=events.append, sleep=lambda _s: None, **kwargs)
        cameras.append(camera)
        if open_:
            camera.open(timeout=10)
        return camera, backend

    yield factory
    for camera in cameras:
        camera.shutdown(timeout=5)


def test_open_configures_and_starts_acquisition(make_camera, events):
    camera, backend = make_camera()
    assert camera.state == DeviceState.READY
    device = backend.devices["HighMag"]
    assert device.configured and device.continuous and device.packet_size == 8996
    status = camera.status()
    assert status.acquiring is True and status.temperature_c == pytest.approx(35.0)
    assert camera.serial_number and camera.bus_type
    assert any(e.topic == "device.hm.opened" for e in events)


def test_grab_returns_new_frames(make_camera):
    camera, _ = make_camera()
    first = camera.grab()
    second = camera.grab()
    assert first.pixels.shape == (48, 64) and first.pixels.dtype == np.uint8
    assert second.number > first.number
    assert frame_number_of(second.pixels) == second.number


def test_flip_is_applied(make_camera):
    """LabVIEW's "horizontal" flip mirrors top and bottom (``docs/05`` §7.2)."""
    camera, _ = make_camera()
    camera.grab()
    camera.set_flip(horizontal=True, vertical=False)
    image = camera.grab()
    assert image.number > 0
    assert frame_number_of(np.flipud(image.pixels)) == image.number
    assert frame_number_of(image.pixels) != image.number


def test_exposure_is_clamped_and_sets_the_frame_rate(make_camera, events):
    camera, backend = make_camera()
    assert camera.set_exposure(50.0) == pytest.approx(177.0)            # lower limit of the settings
    assert camera.exposure() == pytest.approx(177.0)
    assert camera.set_exposure(20_000.0) == pytest.approx(20_000.0)
    rate = backend.devices["HighMag"].attribute_value(SIM_FRAME_RATE)
    assert rate == pytest.approx(0.95 / 0.02)                           # 47.5 frames/s
    assert events[-1].topic == "device.hm.exposure"
    with pytest.raises(DeviceError):
        camera.set_exposure(float("nan"))


def test_gain(make_camera):
    camera, _ = make_camera()
    camera.set_gain(3)
    assert camera.gain() == pytest.approx(3.0)


def test_flat_field_correction(make_camera, tmp_path: Path):
    camera, _ = make_camera()
    flat, dark = tmp_path / "flat.png", tmp_path / "dark.png"
    write_png(flat, np.full((48, 64), 200, np.uint8))
    write_png(dark, np.full((48, 64), 10, np.uint8))
    camera.set_flat_field(True, flat_path=flat, dark_path=dark, factor=1.0)
    image = camera.grab()
    assert image.flat_field
    with pytest.raises(DeviceError):
        camera.set_flat_field(True, flat_path=tmp_path / "missing.png", dark_path=dark)
    assert camera.settings.flat_field_path == flat                     # failed reload keeps the previous images


def test_leds_on_a_gige_camera(make_camera, events):
    camera, backend = make_camera(lights=LightSettings(led_status_inverter=True))
    camera.set_led(True)
    assert camera.led() is True
    assert backend.devices["HighMag"].attribute_value(USER_OUTPUT_VALUE, 0) is True
    camera.set_led(False)
    assert camera.led() is False
    assert [e.data["on"] for e in events if e.topic == "device.hm.led"] == [True, False]


def test_leds_on_a_usb_camera(make_camera):
    settings = CameraSettings(camera_name="HighMag", plug_type=PLUG_USB)
    lights = LightSettings(camera_sort_led=PLUG_USB, hm_led_line=1, hm_bulb_line=2, led_status_inverter=True)
    camera, backend = make_camera(settings=settings, lights=lights)
    camera.set_led(True)
    assert backend.devices["HighMag"].attribute_value(LINE_INVERTER, 1) is True
    assert camera.led() is True


def test_led_needs_the_lights_camera(make_camera):
    camera, _ = make_camera()
    with pytest.raises(DeviceError):
        camera.set_led(True)


def test_unknown_camera_name_does_not_open(make_camera):
    camera, _ = make_camera(sim=SimulationOptions(camera_names=("Other",)), open_=False)
    with pytest.raises(DeviceError):
        camera.open(timeout=5)
    assert camera.state == DeviceState.FAULT
