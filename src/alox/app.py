"""Entry point of the integrated ALOX Vision Core application.

The application container (``docs/vision-core/phase3-skeleton.md``): the event bus, the devices (the Z stage, the XY
stages, the monochromator and the two cameras), the core (:mod:`alox.core.core`) and the supervisor API (REST on 8002,
websocket on 6123). The measurement functions and the UI are added phase by phase.

Command line::

    alox [--setup-ini PATH] [--settings-dir DIR] [--alox-ini PATH] [--simulate]
         [--port-map COM2=socket://127.0.0.1:50000] [--no-reference] [--devices z,xy,wl,highmag,lowmag]
         [--rest-port 8002] [--ws-port 6123] [--host 0.0.0.0] [--no-api]
         [--log-dir DIR] [--log-level LEVEL] [--check]

Without ``--check`` the whole application runs (folders from ``alox_setup.ini``, settings in ``<settings>/alox.ini``)
until Ctrl+C or the supervisor's ``PUT /v1/system/shutdown``. ``--check`` only opens the devices, prints their status
as JSON and exits (0 when every device is ready).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Self

from alox import __version__
from alox.config import TypedIni
from alox.core.core import Core
from alox.core.folders import resolve_folders
from alox.core.images import ImageStore
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
    SDK for the real cameras.

    ``core`` runs the core and (``api``) the supervisor API: the folders come from ``setup_ini`` (default
    ``<exe>/alox_setup.ini``), ``settings_dir`` replaces ``[folders] settings``, and ``alox_ini`` defaults to
    ``<settings>/alox.ini``. Port 0 picks a free port (tests)."""

    alox_ini: Path | None = None
    simulate: bool = False
    port_map: dict[str, str] = field(default_factory=dict)
    always_reference: bool = True
    open_timeout_s: float | None = None
    devices: tuple[str, ...] = ("z", "xy", "wl", "highmag", "lowmag")
    genicam_producer: Path | None = None
    core: bool = False
    setup_ini: Path | None = None
    settings_dir: Path | None = None
    api: bool = True
    host: str = "0.0.0.0"
    rest_port: int = 8002
    ws_port: int = 6123


class _UnavailableCameras(CameraBackend):
    """Stands in for the camera driver when it cannot be loaded: every camera fails to open, with the reason."""

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def open_camera(self, name: str) -> CameraDevice:
        raise CameraError(Code.CAMERA_SDK, f"camera '{name}': {self.reason}")

    def close(self) -> None:
        return None


class _RestServer:
    """uvicorn in a background thread (the rest of the application is synchronous)."""

    def __init__(self, app: Any, host: str, port: int) -> None:
        import uvicorn

        self._server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning",
                                                     access_log=False, lifespan="off"))
        self._thread: threading.Thread | None = None
        self.port = port

    def start(self, timeout: float = 10.0) -> None:
        self._thread = threading.Thread(target=self._server.run, name="rest-server", daemon=True)
        self._thread.start()
        end = time.monotonic() + timeout
        while not self._server.started:
            if not self._thread.is_alive() or time.monotonic() > end:
                raise RuntimeError(f"REST server did not start on port {self.port}")
            time.sleep(0.02)
        servers = getattr(self._server, "servers", [])
        if servers and servers[0].sockets:
            self.port = servers[0].sockets[0].getsockname()[1]

    def stop(self, timeout: float = 10.0) -> None:
        self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None


class Application:
    """The container: event bus, settings, devices. ``start`` opens the devices (a device that fails stays in
    ``fault`` and is reported; it does not stop the application); ``stop`` shuts them down."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.events = EventBus()
        self.core: Core | None = None
        self.rest: _RestServer | None = None
        self.websocket: Any = None
        self.exit_requested = threading.Event()
        self.functions: dict[Any, str] = {}
        self.live: Any = None
        alox_ini = config.alox_ini
        if config.core:
            folders = resolve_folders(config.setup_ini, config.settings_dir)
            self.core = Core(folders, events=self.events)
            self.core.images = ImageStore(publish=self.events.publish, profile=self.core.profile_manager,
                                          has_client=lambda: self.websocket is not None and self.websocket.clients > 0)
            self.core.on_shutdown = self.exit_requested.set
            self.core.boot()                                  # creates alox.ini from alox_default.ini when missing
            if alox_ini is None:
                alox_ini = folders.alox_ini
        self.alox_ini = alox_ini
        self.settings = self._load_settings(alox_ini)
        self.typed_ini = TypedIni.load(alox_ini) if alox_ini and alox_ini.exists() else None
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
        if self.core is not None:
            self.core.devices = self.devices
            from alox.functions.registry import build_services, register_functions

            self.functions = register_functions(self.core.functions, build_services(self.core))

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
        """Open every device (with the core: its init cycle, then the API servers); returns ``{device: state}``."""
        if self.core is None:
            for device in self.devices.values():
                self._open_device(device)
        else:
            if self.config.api:
                self._start_api()
            self.core.start(self._open_device)
            self._start_live_view()
        return {name: str(device.state) for name, device in self.devices.items()}

    def _start_live_view(self) -> None:
        """The continuous Get Image of the display while no function runs (changelog 1.0.0.11)."""
        from alox.core.functions import FunctionName
        from alox.functions.get_image import GetImage, LiveView

        assert self.core is not None
        try:
            function = self.core.functions.function(FunctionName.GET_IMAGE)
        except KeyError:
            return
        if isinstance(function, GetImage):
            self.live = LiveView(function, self.core.functions)
            self.live.start()

    def _open_device(self, device: Device) -> None:
        device.start()
        try:
            device.open(timeout=self.config.open_timeout_s or self._open_timeout(device))
        except DeviceError as exc:
            log.error("%s did not open: %s", device.name, exc)

    def _start_api(self) -> None:
        """LabVIEW boot frames 26, 27: status 13 "Init webservice", 14 "Init websocket"."""
        from alox.api.rest import create_app
        from alox.api.websocket import WebsocketServer
        from alox.core.status import SystemCode

        assert self.core is not None
        self.core.system.set(SystemCode.INIT_WEB_SERVER)
        self.websocket = WebsocketServer(self.events, host=self.config.host, port=self.config.ws_port)
        self.websocket.start()
        self.rest = _RestServer(create_app(self.core, websocket_port=self.websocket.port), self.config.host,
                                self.config.rest_port)
        self.rest.start()
        self.core.system.set(SystemCode.INIT_WEBSOCKET)
        log.info("API: REST on port %d, websocket on port %d", self.rest.port, self.websocket.port)

    @staticmethod
    def _open_timeout(device: Device) -> float:
        if isinstance(device, ZStage | XYStage):          # the set-up may include reference moves
            return 2 * (device.options.ready_timeout_s + device.options.wait_timeout_s)
        return device.default_timeout_s

    def stop(self) -> None:
        """Stop the core and the API, shut every device down (idempotent), then release the camera driver."""
        if self.live is not None:
            self.live.stop()
            self.live = None
        if self.core is not None:
            self.core.stop()
            if self.core.images is not None:
                self.core.images.close()                         # type: ignore[attr-defined]
        if self.rest is not None:
            self.rest.stop()
            self.rest = None
        if self.websocket is not None:
            self.websocket.stop()
            self.websocket = None
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
    p = argparse.ArgumentParser(prog="alox", description="ALOX Vision Core (integrated application)")
    p.add_argument("--setup-ini", type=Path, help="alox_setup.ini with the [folders] (default: <exe>/alox_setup.ini)")
    p.add_argument("--settings-dir", type=Path, help="replaces [folders] settings (alox.ini, profile, profile_temp)")
    p.add_argument("--alox-ini", type=Path, help="the alox.ini to read the hardware settings from "
                   "(default: <settings>/alox.ini; with --check: built-in defaults)")
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
    p.add_argument("--host", default="0.0.0.0", help="address the API listens on (default 0.0.0.0)")
    p.add_argument("--rest-port", type=int, default=8002, help="REST API port (default 8002)")
    p.add_argument("--ws-port", type=int, default=6123, help="websocket events port (default 6123)")
    p.add_argument("--no-api", action="store_true", help="run without the REST and websocket servers")
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
                       devices=devices, genicam_producer=args.genicam_producer, core=not args.check,
                       setup_ini=args.setup_ini, settings_dir=args.settings_dir, api=not args.no_api,
                       host=args.host, rest_port=args.rest_port, ws_port=args.ws_port)
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
            while not app.exit_requested.wait(0.5):
                pass
        except KeyboardInterrupt:
            pass
        log.info("stopping")
    return 0


if __name__ == "__main__":
    sys.exit(main())
