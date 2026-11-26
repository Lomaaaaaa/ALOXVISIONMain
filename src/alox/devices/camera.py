"""A camera as a device of the integrated application.

Hardware: two IDS GigE Vision cameras (High Mag and Low Mag; firmware ``GV-528x-M`` in the sources), specified for the
camera server in ``docs/05-camera-acquisition.md``. The driver is behind the camera server's
:class:`aloxcam.camera.backend.CameraBackend` interface (IMAQdx-like attribute paths ``CameraAttributes::…``):
:class:`aloxcam.camera.sim.SimCameraBackend` in simulation, a GenICam backend for the hardware
(:mod:`alox.devices.camera_genicam`). Images stay in memory as NumPy arrays: the NI image serialisation of the camera
server is not needed.

The device opens, configures (ring of 5 buffers, continuous) and starts the camera, and keeps it acquiring;
:meth:`Camera.grab` returns the newest frame after flip and flat-field correction, as the camera server's
``Cam Get Image``. Exposure, gain, flip, flat field and the lights (LED and bulb through the camera's digital outputs)
follow ``docs/05`` §5, §7, §9, with the camera server's tested helpers.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np

from alox.devices.base import Device, DeviceError
from alox.events import Publish
from aloxcam.camera.attributes import (
    DEVICE_TEMPERATURE,
    EXPOSURE_UPPER_LIMIT,
    FRAMERATE_LOWER_LIMIT,
    GIGE_PACKET_SIZE,
    LINE_INVERTER,
    LINE_MODE,
    LINE_SELECTOR,
    LINE_SOURCE,
    NUM_BUFFERS,
    TRIGGER_MODE,
    USER_OUTPUT_SELECTOR,
    USER_OUTPUT_VALUE,
    in_range_and_coerce,
    read_typed,
    write_typed,
)
from aloxcam.camera.backend import CameraBackend, CameraDevice, Frame, ValueType
from aloxcam.camera.pipeline import (
    flat_field_correct,
    flip_image,
    load_correction_image,
)
from aloxcam.errors import CameraError
from aloxcam.settings import PLUG_USB, CameraSettings, LightSettings

__all__ = ["Camera", "CameraImage", "CameraStatus"]

log = logging.getLogger(__name__)

U32_MASK = 0xFFFFFFFF


@dataclass(frozen=True)
class CameraImage:
    """One corrected image: ``pixels`` (2-D NumPy array, owned), the camera's buffer number, the acquisition time
    (wall clock, seconds since the epoch) and the exposure and gain in effect."""

    pixels: np.ndarray
    number: int
    time: float
    camera: str
    exposure_us: float
    gain: float
    flat_field: bool


@dataclass(frozen=True)
class CameraStatus:
    state: str
    acquiring: bool | None
    exposure_us: float | None
    gain: float | None
    temperature_c: float | None
    bus_type: str
    serial_number: str


class Camera(Device):
    """One camera. ``open_name`` is the driver name of the camera (``alox.ini`` ``name_STR``); ``settings`` its
    section of ``alox.ini``; ``lights`` is given to the camera whose digital outputs drive the LED and the bulb."""

    def __init__(self, backend: CameraBackend, settings: CameraSettings, *, name: str, open_name: str | None = None,
                 lights: LightSettings | None = None, publish: Publish | None = None,
                 sleep: Callable[[float], None] = time.sleep, grab_timeout_ms: int = 5000,
                 **device_options: Any) -> None:
        self.backend = backend
        self._settings = settings
        self.open_name = open_name or settings.camera_name
        self.lights = lights
        self.grab_timeout_ms = grab_timeout_ms
        self._device: CameraDevice | None = None
        self._flat: np.ndarray | None = None
        self._dark: np.ndarray | None = None
        self.bus_type = ""
        self.serial_number = ""
        self.frame_rate: float | None = None          # the last frame rate written by set_exposure (Hz)
        device_options.setdefault("default_timeout_s", grab_timeout_ms / 1000.0 + 10.0)
        super().__init__(name, publish=publish, sleep=sleep, **device_options)

    # ============================================================================================== settings
    @property
    def settings(self) -> CameraSettings:
        return self._settings

    @property
    def is_usb(self) -> bool:
        return self._settings.plug_type == PLUG_USB

    # ============================================================================================== device hooks
    def _open(self) -> None:
        device = self.backend.open_camera(self.open_name)
        self._device = device
        try:
            self.bus_type = device.bus_type()
        except CameraError as exc:
            log.debug("%s: bus type not readable: %s", self.name, exc)
        try:
            high, low = device.serial_number()
            self.serial_number = f"{high & U32_MASK:08X}{low & U32_MASK:08X}"
        except CameraError as exc:
            log.debug("%s: serial number not readable: %s", self.name, exc)
        if self.is_usb:
            self._sleep(0.1)                               # LabVIEW waits 100 ms after opening a USB camera
        else:
            device.set_packet_size(GIGE_PACKET_SIZE)
        device.configure(True, NUM_BUFFERS)
        self._load_corrections()
        if not self.is_usb:
            device.write_attribute(TRIGGER_MODE, ValueType.STRING, "Off")
        device.start()
        self.emit("opened", open_name=self.open_name, bus_type=self.bus_type, serial_number=self.serial_number)

    def _close(self) -> None:
        device, self._device = self._device, None
        if device is None:
            return
        for step in (device.stop, device.unconfigure, device.close):
            try:
                step()
            except CameraError as exc:
                log.debug("%s: %s failed while closing: %s", self.name, step.__name__, exc)

    def _is_link_failure(self, exc: BaseException) -> bool:
        return isinstance(exc, CameraError) and self._device is not None and not self._alive()

    def _translate(self, exc: BaseException, what: str) -> DeviceError:
        if isinstance(exc, DeviceError):
            return exc
        if isinstance(exc, CameraError):
            return DeviceError(self.name, f"{what}: {exc.source}", code=exc.code)
        return super()._translate(exc, what)

    def _alive(self) -> bool:
        device = self._device
        if device is None:
            return False
        try:
            device.acquisition_in_progress()
        except CameraError:
            return False
        return True

    def _require(self) -> CameraDevice:
        if self._device is None:
            raise DeviceError(self.name, "camera not open")
        return self._device

    def _load_corrections(self) -> None:
        s = self._settings
        self._flat = self._dark = None
        if not s.flat_field:
            return
        for label, path in (("flat-field", s.flat_field_path), ("dark-field", s.dark_current_path)):
            if path is None:
                raise DeviceError(self.name, f"flat field enabled but no {label} image path")
        self._flat = load_correction_image(Path(s.flat_field_path))          # type: ignore[arg-type]
        self._dark = load_correction_image(Path(s.dark_current_path))        # type: ignore[arg-type]

    # ============================================================================================== images
    def grab(self, *, newest: bool = True, timeout_ms: int | None = None) -> CameraImage:
        """The newest frame (``newest``) or the next one, flipped and flat-field corrected as configured."""
        limit = self.grab_timeout_ms if timeout_ms is None else timeout_ms

        def run() -> CameraImage:
            device = self._require()
            frame: Frame = device.get_last_new(limit) if newest else device.get_next(limit)
            return self._process(frame)
        return self.call("grab", run, timeout=limit / 1000.0 + 5.0)

    def _process(self, frame: Frame) -> CameraImage:
        s = self._settings
        pixels = flip_image(frame.pixels, s.horizontal_flip, s.vertical_flip)
        if s.flat_field:
            pixels = flat_field_correct(pixels, self._flat, self._dark, s.flat_field_factor)
        return CameraImage(pixels=pixels, number=frame.number, time=time.time(), camera=self.name,
                           exposure_us=self._read_exposure(), gain=self._read_gain(), flat_field=s.flat_field)

    # ============================================================================================== exposure, gain
    def exposure(self) -> float:
        """Exposure time in µs."""
        return self.call("exposure", self._read_exposure)

    def set_exposure(self, microseconds: float) -> float:
        """Clamp to ``[exposure_lower_limit, 2 s]``, write the exposure, then the frame rate
        ``0.95 / exposure`` clamped to ``[0.5, max frame rate]`` (``docs/05`` §5.2; the camera maximum is used when
        the settings give none). Returns the exposure written."""
        if not math.isfinite(microseconds):
            raise DeviceError(self.name, "set exposure: value must be finite")
        s = self._settings

        def run() -> float:
            device = self._require()
            exposure = in_range_and_coerce(microseconds, s.exposure_lower_limit, EXPOSURE_UPPER_LIMIT)
            write_typed(device, s.exposure_attribute, s.exposure_type, exposure)
            upper = s.max_framerate if s.max_framerate > 0 else self._attribute_max(s.framerate_attribute)
            rate = in_range_and_coerce(0.95 / (exposure / 1_000_000.0), FRAMERATE_LOWER_LIMIT, upper)
            try:
                write_typed(device, s.framerate_attribute, s.framerate_type, rate)
                self.frame_rate = rate
            except CameraError as exc:
                log.warning("%s: frame rate %g not written: %s", self.name, rate, exc)
            self.emit("exposure", exposure_us=exposure, framerate=rate)
            return exposure
        return self.call("set exposure", run)

    @property
    def frame_period_s(self) -> float:
        """1 / the frame rate written by :meth:`set_exposure` (0 before): longer than the exposure when the frame rate
        is capped by the camera's maximum."""
        return 1.0 / self.frame_rate if self.frame_rate else 0.0

    def gain(self) -> float:
        return self.call("gain", self._read_gain)

    def set_gain(self, value: float) -> None:
        if not math.isfinite(value):
            raise DeviceError(self.name, "set gain: value must be finite")
        s = self._settings
        self.call("set gain", lambda: write_typed(self._require(), s.gain_attribute, s.gain_type, value))

    def _read_exposure(self) -> float:
        s = self._settings
        return float(read_typed(self._require(), s.exposure_attribute, s.exposure_type))

    def _read_gain(self) -> float:
        s = self._settings
        return float(read_typed(self._require(), s.gain_attribute, s.gain_type))

    def _attribute_max(self, attribute: str) -> float:
        try:
            return float(self._require().read_attribute_max(attribute))
        except CameraError:
            return math.inf

    # ============================================================================================== flip, flat field
    def set_flip(self, *, horizontal: bool, vertical: bool) -> None:
        """Image flips applied to the next images (memory only)."""
        self._settings = replace(self._settings, horizontal_flip=horizontal, vertical_flip=vertical)

    def set_flat_field(self, enabled: bool, *, flat_path: Path | None = None, dark_path: Path | None = None,
                       factor: float | None = None) -> None:
        """Enable or disable the flat-field correction and (re)load its images."""
        changes: dict[str, Any] = {"flat_field": enabled}
        if flat_path is not None:
            changes["flat_field_path"] = Path(flat_path)
        if dark_path is not None:
            changes["dark_current_path"] = Path(dark_path)
        if factor is not None:
            changes["flat_field_factor"] = float(factor)
        previous = self._settings
        self._settings = replace(previous, **changes)
        try:
            self.call("set flat field", self._load_corrections)
        except DeviceError:
            self._settings = previous
            raise

    # ============================================================================================== lights
    def set_led(self, on: bool) -> None:
        """Switch the LED (and the bulb wired with it) through the digital outputs (``docs/05`` §9.2). Only on the
        camera that drives the lights."""
        light = self._light("set led")
        s = self._settings
        command = (not on) if s.invert_led else bool(on)
        off = not command

        def run() -> None:
            device = self._require()
            if light.camera_sort_led == PLUG_USB:
                for line, inverter in ((light.hm_led_line, light.led_status_inverter),
                                       (light.hm_bulb_line, light.bulb_status_inverter)):
                    device.write_attribute(LINE_SELECTOR, ValueType.U32, int(line) & U32_MASK)
                    device.write_attribute(LINE_MODE, ValueType.U32, 1)
                    device.write_attribute(LINE_SOURCE, ValueType.U32, 1)
                    device.write_attribute(LINE_INVERTER, ValueType.BOOL, command if inverter else off)
                changed = off
            else:
                target = command if light.led_status_inverter else off
                device.write_attribute(LINE_SELECTOR, ValueType.U32, 1)
                device.write_attribute(LINE_SOURCE, ValueType.U32, 1)
                device.write_attribute(USER_OUTPUT_SELECTOR, ValueType.U32, 0)
                try:
                    previous = bool(device.read_attribute(USER_OUTPUT_VALUE, ValueType.BOOL))
                except CameraError:
                    previous = False
                device.write_attribute(USER_OUTPUT_VALUE, ValueType.BOOL, target)
                changed = previous != target
            if changed and s.wait_led_ms > 0:
                self._sleep(s.wait_led_ms / 1000.0)
            self.emit("led", on=bool(on))
        self.call("set led", run)

    def led(self) -> bool:
        """The LED state as the camera server reads it (``docs/05`` §9.3): the LED line's output value, XOR
        ``invert_led``."""
        light = self._light("led")
        s = self._settings

        def run() -> bool:
            device = self._require()
            if light.camera_sort_led == PLUG_USB:
                device.write_attribute(LINE_SELECTOR, ValueType.U32, int(light.hm_led_line) & U32_MASK)
                device.write_attribute(LINE_MODE, ValueType.U32, 1)
                device.write_attribute(LINE_SOURCE, ValueType.U32, 0)
                value = bool(device.read_attribute(LINE_INVERTER, ValueType.BOOL))
            else:
                device.write_attribute(LINE_SELECTOR, ValueType.U32, 1)
                device.write_attribute(LINE_SOURCE, ValueType.U32, 1)
                device.write_attribute(USER_OUTPUT_SELECTOR, ValueType.U32, 0)
                value = bool(device.read_attribute(USER_OUTPUT_VALUE, ValueType.BOOL))
            return value != s.invert_led
        return self.call("led", run)

    def _light(self, what: str) -> LightSettings:
        if self.lights is None:
            raise DeviceError(self.name, f"{what}: this camera does not drive the lights")
        return self.lights

    # ============================================================================================== status
    def temperature(self) -> float:
        return self.call("temperature",
                         lambda: float(self._require().read_attribute(DEVICE_TEMPERATURE, ValueType.DBL)))

    def status(self) -> CameraStatus:
        def read() -> CameraStatus:
            device = self._require()
            values: list[Any] = []
            for reader in (device.acquisition_in_progress, self._read_exposure, self._read_gain,
                           lambda: float(device.read_attribute(DEVICE_TEMPERATURE, ValueType.DBL))):
                try:
                    values.append(reader())
                except CameraError:
                    values.append(None)
            return CameraStatus(str(self.state), *values, self.bus_type, self.serial_number)
        try:
            return self.call("status", read, timeout=5.0)
        except DeviceError:
            return CameraStatus(str(self.state), None, None, None, None, self.bus_type, self.serial_number)
