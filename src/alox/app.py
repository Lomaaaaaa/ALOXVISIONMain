"""Entry point of the integrated ALOX Vision Core application.

Phase 3 (``docs/vision-core/phase3-skeleton.md``): the application container with its event bus and its devices; the Z
stage is the only device so far. The core, the API, the functions and the UI come in the next phases.

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
from alox.devices.base import Device, DeviceError, DeviceState
from alox.devices.zstage import ZStage, build_stage_backend
from alox.events import Event, EventBus
from aloxcam.config.alox_ini import load_alox_ini
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
    """What the application needs to start. ``alox_ini`` None = built-in defaults."""

    alox_ini: Path | None = None
    simulate: bool = False
    port_map: dict[str, str] = field(default_factory=dict)
    always_reference: bool = True
    open_timeout_s: float | None = None


class Application:
    """The container: event bus, settings, devices. ``start`` opens the devices (a device that fails stays in
    ``fault`` and is reported; it does not stop the application); ``stop`` shuts them down."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.events = EventBus()
        self.settings = self._load_settings(config.alox_ini)
        options = StageOptions(port_map=dict(config.port_map), always_reference=config.always_reference)
        backend = build_stage_backend(simulate=config.simulate, simulation=SimulationOptions(stage=True))
        self.z = ZStage(backend, self.settings.z, options, publish=self.events.publish)
        self.devices: dict[str, Device] = {self.z.name: self.z}

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
        if isinstance(device, ZStage):                 # the set-up may include a reference move
            return device.options.ready_timeout_s + device.options.wait_timeout_s
        return device.default_timeout_s

    def stop(self) -> None:
        """Shut every device down (idempotent)."""
        for device in self.devices.values():
            device.shutdown()

    def status(self) -> dict[str, Any]:
        """Version and per-device status, for ``--check`` and later for the API."""
        return {"version": __version__, "simulate": self.config.simulate,
                "devices": {self.z.name: asdict(self.z.status(timeout=5.0))}}

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
    config = AppConfig(alox_ini=args.alox_ini, simulate=args.simulate, port_map=_port_map(args.port_map))
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
