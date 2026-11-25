"""Entry point of the integrated ALOX Vision Core application.

The application container with its event bus and its devices (``docs/vision-core/phase3-skeleton.md``): the Z stage, the
XY stages, the monochromator and the two cameras; the core, the API, the functions and the UI are added phase by phase.

Command line::

    alox [--alox-ini PATH] [--simulate] [--port-map COM2=socket://127.0.0.1:50000] [--no-reference]
         [--log-dir DIR] [--log-level LEVEL] [--check]

``--check`` opens every device, prints their status as JSON and exits (0 when every device is ready). Without it the
application runs until Ctrl+C.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Self

from alox import __version__
from alox.config import TypedIni
from alox.devices.base import Device, DeviceError, DeviceState
from alox.devices.camera import Camera
from alox.devices.monochromator import (
    Monochromator,
    MonochromatorSettings,
    build_monochromator_backend,
    load_monochromator_settings,
)
from alox.devices.xy import XYSettings, XYStage, build_xy_backends, load_xy_settings
from alox.devices.zstage import ZStage, build_stage_backend
from alox.events import Event, EventBus
from aloxcam.camera.backend import CameraBackend, CameraDevice
from aloxcam.camera.sim import SimCameraBackend
from aloxcam.config.alox_ini import load_alox_ini
from aloxcam.errors import CameraError, Code
from aloxcam.eventlog import DailyFileHandler
from aloxcam.settings import (
    AloxSettings,
    CameraOptions,
    SimulationOptions,
    StageOptions,
)

__all__ = ["AppConfig", "Application", "main"]

log = logging.getLogger("alox")


@dataclass(frozen=True)
class AppConfig:
    """What the application needs to start. ``alox_ini`` None = built-in defaults. ``devices`` selects the devices to
    build (``z``, ``xy``, ``wl`` (monochromator), ``highmag``, ``lowmag``); ``genicam_producer`` is the GenTL producer (``.cti``) of the camera
    SDK for the real cameras."""

    alox_ini: Path | None = None
    simulate: bool = False
    port_map: dict[str, str] = field(default_factory=dict)
    always_reference: bool = True
    open_timeout_s: float | None = None
    devices: tuple[str, ...] = ("z", "xy", "wl", "highmag", "lowmag")
    genicam_producer: Path | None = None


class _UnavailableCameras(CameraBackend):
    """Stands in for the camera driver when it cannot be loaded: every camera fails to open, with the reason."""

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def open_camera(self, name: str) -> CameraDevice:
        raise CameraError(Code.CAMERA_SDK, f"camera '{name}': {self.reason}")

    def close(self) -> None:
        return None


class Application:
    """The container: event bus, settings, devices. ``start`` opens the devices (a device that fails stays in
    ``fault`` and is reported; it does not stop the application); ``stop`` shuts them down."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.events = EventBus()
        self.settings = self._load_settings(config.alox_ini)
        self.typed_ini = TypedIni.load(config.alox_ini) if config.alox_ini and config.alox_ini.exists() else None
        self.devices: dict[str, Device] = {}
        self._camera_backend: CameraBackend | None = None
        wanted = set(config.devices)
        if "z" in wanted:
            options = StageOptions(port_map=dict(config.port_map), always_reference=config.always_reference)
            backend = build_stage_backend(simulate=config.simulate, simulation=SimulationOptions(stage=True))
            self.add(ZStage(backend, self.settings.z, options, publish=self.events.publish))
        if "xy" in wanted:
            self.add(self._build_xy())
        if "wl" in wanted:
            self.add(self._build_monochromator())
        for name, camera_settings in (("highmag", self.settings.high_mag), ("lowmag", self.settings.low_mag)):
            if name in wanted:
                drives_lights = self.settings.light.connector.upper() == ("HM" if name == "highmag" else "LM")
                self.add(Camera(self._cameras(), camera_settings, name=name,
                                lights=self.settings.light if drives_lights else None, publish=self.events.publish))

    def add(self, device: Device) -> Device:
        self.devices[device.name] = device
        return device

    @property
    def z(self) -> ZStage:
        return self.devices["z"]                      # type: ignore[return-value]

    def _build_xy(self) -> XYStage:
        settings = load_xy_settings(self.typed_ini) if self.typed_ini is not None else XYSettings()
        if self.config.simulate:
            backends = build_xy_backends(simulate=True)
        else:
            from alox.devices.gcs_chain import GcsChain
            port_name = f"COM{settings.x.com if settings.x.com > 0 else 1}"
            port = self.config.port_map.get(port_name, port_name)
            chain = GcsChain(port, settings.x.baudrate, 5.0)
            backends = build_xy_backends(simulate=False, chain=chain, devices=(settings.x.device, settings.y.device))
        options = StageOptions(port_map=dict(self.config.port_map), always_reference=False)
        return XYStage(*backends, settings, options, publish=self.events.publish)

    def _build_monochromator(self) -> Monochromator:
        def warn(message: str) -> None:
            log.warning("%s", message)

        settings = (load_monochromator_settings(self.typed_ini, warn=warn) if self.typed_ini is not None
                    else MonochromatorSettings())
        mapped = self.config.port_map.get(settings.port_name.upper())
        if mapped is not None:
            settings = replace(settings, port=mapped)
        backend = build_monochromator_backend(simulate=self.config.simulate)
        return Monochromator(backend, settings, publish=self.events.publish)

    def _cameras(self) -> CameraBackend:
        """One driver for both cameras: simulated, or the GenICam backend on the configured GenTL producer."""
        if self._camera_backend is None:
            if self.config.simulate:
                self._camera_backend = SimCameraBackend(SimulationOptions(cameras=True))
            else:
                try:
                    from alox.devices.camera_genicam import GenICamBackend
                    self._camera_backend = GenICamBackend(self.config.genicam_producer)
                except Exception as exc:                     # noqa: BLE001 - reported by the cameras' open
                    log.error("camera driver unavailable: %s", exc)
                    self._camera_backend = _UnavailableCameras(str(exc))
        return self._camera_backend

    @staticmethod
    def _load_settings(path: Path | None) -> AloxSettings:
        if path is None:
            return AloxSettings()
        return load_alox_ini(path, CameraOptions(), warn=lambda message: log.warning("alox.ini: %s", message))

    def start(self) -> dict[str, str]:
        """Start and open every device; returns ``{device: state}``."""
        for device in self.devices.values():
            device.start()
            try:
                device.open(timeout=self.config.open_timeout_s or self._open_timeout(device))
            except DeviceError as exc:
                log.error("%s did not open: %s", device.name, exc)
        return {name: str(device.state) for name, device in self.devices.items()}

    @staticmethod
    def _open_timeout(device: Device) -> float:
        if isinstance(device, ZStage | XYStage):          # the set-up may include reference moves
            return 2 * (device.options.ready_timeout_s + device.options.wait_timeout_s)
        return device.default_timeout_s

    def stop(self) -> None:
        """Shut every device down (idempotent), then release the camera driver."""
        for device in self.devices.values():
            device.shutdown()
        if self._camera_backend is not None:
            try:
                self._camera_backend.close()
            except CameraError as exc:
                log.warning("camera driver release failed: %s", exc)

    def status(self) -> dict[str, Any]:
        """Version and per-device status, for ``--check`` and later for the API."""
        devices: dict[str, Any] = {}
        for name, device in self.devices.items():
            reader = getattr(device, "status", None)
            try:
                devices[name] = asdict(reader()) if reader is not None else {"state": str(device.state)}
            except Exception as exc:                         # noqa: BLE001 - a status must always be reported
                devices[name] = {"state": str(device.state), "error": str(exc)}
        return {"version": __version__, "simulate": self.config.simulate, "devices": devices}

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()


# ================================================================================================== command line
def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="alox", description="ALOX Vision Core (integrated application, phase 3)")
    p.add_argument("--alox-ini", type=Path, help="the LabVIEW alox.ini to read the hardware settings from")
    p.add_argument("--simulate", action="store_true", help="simulated devices (no hardware)")
    p.add_argument("--port-map", action="append", default=[], metavar="COMn=URL",
                   help="map a COM port of alox.ini to another device or a pyserial URL (repeatable)")
    p.add_argument("--no-reference", action="store_true",
                   help="do not force a reference move at set-up when the axis is already referenced")
    p.add_argument("--devices", default="z,xy,wl,highmag,lowmag",
                   help="comma-separated devices to use (default: z,xy,wl,highmag,lowmag)")
    p.add_argument("--genicam-producer", type=Path, help="GenTL producer (.cti) of the camera SDK (real cameras)")
    p.add_argument("--log-dir", type=Path, help="also write the log to daily files in this folder")
    p.add_argument("--log-level", default="INFO", help="DEBUG, INFO, WARNING, ERROR (default INFO)")
    p.add_argument("--check", action="store_true", help="open the devices, print their status as JSON and exit")
    p.add_argument("--version", action="version", version=f"alox {__version__}")
    return p


def _port_map(items: Sequence[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items:
        name, sep, url = item.partition("=")
        if not sep or not name.strip() or not url.strip():
            raise SystemExit(f"--port-map: expected COMn=URL, got {item!r}")
        out[name.strip().upper()] = url.strip()
    return out


def _setup_logging(level: str, log_dir: Path | None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_dir is not None:
        handlers.append(DailyFileHandler(log_dir, "alox"))
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    root = logging.getLogger()
    for handler in handlers:
        handler.setFormatter(fmt)
        root.addHandler(handler)
    root.setLevel(level.upper())


def _log_event(event: Event) -> None:
    data = ", ".join(f"{k}={v}" for k, v in event.data.items())
    log.info("[event] %s %s", event.topic, data)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _setup_logging(args.log_level, args.log_dir)
    devices = tuple(d.strip().lower() for d in args.devices.split(",") if d.strip())
    config = AppConfig(alox_ini=args.alox_ini, simulate=args.simulate, port_map=_port_map(args.port_map),
                       devices=devices, genicam_producer=args.genicam_producer)
    if args.no_reference:
        config = replace(config, always_reference=False)
    log.info("alox %s starting (%s)", __version__, "simulation" if config.simulate else "hardware")
    with Application(config) as app:
        app.events.on(("device",), _log_event)
        states = app.start()
        if args.check:
            print(json.dumps(app.status(), indent=2))
            return 0 if all(s == DeviceState.READY for s in states.values()) else 1
        log.info("running; press Ctrl+C to stop")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            log.info("stopping")
    return 0


if __name__ == "__main__":
    sys.exit(main())
