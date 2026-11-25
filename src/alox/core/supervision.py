"""Hardware supervision: what LabVIEW's modules heartbeat service did for the three server processes, done on the
in-process devices (``docs/vision-core/map.md`` Part 2 §2.6 "Server liveness", ``alox_modules_heartbeat_service``).

LabVIEW ran one loop per module (cameras/Z, XY, monochromator). Every ``hardware.<m>.heartbeat`` ms it queried the
module's server; a failed query incremented the module's ``ERROR COUNT`` and a successful one reset it to 0. While the
count was not 0 it sent ``hardware_warning`` (``hardware_error`` once the count reached ``hardware.<m>.maxfails``), the
system status took the failed-hardware bits (``tools_update_system_status_from_*_state``), and at ``maxfails`` with
``hardware.<m>.watchdog`` it restarted the server and re-initialised the hardware, then sent ``hardware_reset`` and
queued ``Module Resetted``.

Here a module is a group of devices of the application (cameras/Z: ``highmag``, ``lowmag``, ``z``; XY: ``xy``;
monochromator: ``wl``). A check reads each device's status; a device that is not ``ready`` afterwards (a link failure
puts it in ``fault``) or whose status read raises has failed. The restart closes and re-opens the module's devices.

Kept: the settings and their defaults, the counting, the warning/error/reset events with LabVIEW's source names, the
hardware bits of the system status. Changed: there is no server process to kill; a device that is back clears its bits
(:meth:`alox.core.status.SystemStatus.hardware_failed`) and the warning stops.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from alox.api import reports
from alox.core.status import HardwareBit, SystemStatus
from alox.devices.base import Device, DeviceState
from alox.events import Publish

__all__ = ["MODULES", "HardwareSupervisor", "Module", "ModuleReport"]

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Module:
    """One hardware module: its devices, the ``alox.ini`` section of its settings, its ``hardware_reset`` name, and per
    device the websocket source name and the hardware bits."""

    name: str
    section: str
    reset_source: str
    devices: Mapping[str, tuple[str, HardwareBit]]


#: The three modules (source names: map Part 5 Appendix B, ``send_camz_warning_event d``, ``send_xy_warning_event d``;
#: the monochromator names are inferred).
MODULES: tuple[Module, ...] = (
    Module("camz", "hardware.camera", "cameras_z_stage_module", {
        "highmag": ("camera_high_mag_status", HardwareBit.HIGH_MAG),
        "lowmag": ("camera_low_mag_status", HardwareBit.LOW_MAG),
        "z": ("z_stage_status", HardwareBit.Z)}),
    Module("xy", "hardware.xy", "xy_stages_module", {
        "xy": ("x_stage_status", HardwareBit.X | HardwareBit.Y)}),
    Module("wl", "hardware.monochromator", "monochromator_module", {
        "wl": ("monochromator_status", HardwareBit.MONOCHROMATOR)}),
)

#: LabVIEW defaults of the diagrams (the shipped alox.ini has 60000 / 10 / TRUE).
DEFAULT_PERIOD_MS = 30000
DEFAULT_MAXFAILS = 3


@dataclass
class ModuleReport:
    """The state of one module after its last check (LabVIEW ``alox_typedef_hardware_module_heartbeat_report``)."""

    module: str
    count: int = 0
    failed: list[str] = field(default_factory=list)          # device names
    last_check: float | None = None
    restarts: int = 0


class HardwareSupervisor:
    """Checks the devices of each module every ``hardware.<m>.heartbeat`` ms (:meth:`start`), or on demand
    (:meth:`check`). ``setting(dotted, default)`` reads ``alox.ini``; ``on_reset(module)`` is called after a restart
    (the core queues ``Module Resetted``)."""

    def __init__(self, devices: Mapping[str, Device], status: SystemStatus, *, publish: Publish | None = None,
                 setting: Callable[[str, Any], Any] | None = None, on_reset: Callable[[str], None] | None = None,
                 monotonic: Callable[[], float] = time.monotonic, tick_s: float = 0.1) -> None:
        self._devices = devices
        self._status = status
        self._publish = publish
        self._setting = setting or (lambda _name, default: default)
        self._on_reset = on_reset
        self._monotonic = monotonic
        self._tick_s = tick_s
        self._lock = threading.Lock()
        self._reports = {m.name: ModuleReport(m.name) for m in MODULES if self._present(m)}
        self._due: dict[str, float] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ============================================================================================== settings
    def _present(self, module: Module) -> bool:
        return any(name in self._devices for name in module.devices)

    def period_s(self, module: Module) -> float:
        value = self._setting(f"{module.section}.heartbeat", DEFAULT_PERIOD_MS)
        return max(1.0, float(value) / 1000.0)

    def maxfails(self, module: Module) -> int:
        return max(1, int(self._setting(f"{module.section}.maxfails", DEFAULT_MAXFAILS)))

    def restart_enabled(self, module: Module) -> bool:
        return bool(self._setting(f"{module.section}.watchdog", True))

    # ============================================================================================== reports
    def report(self, module: str) -> ModuleReport:
        with self._lock:
            r = self._reports[module]
            return ModuleReport(r.module, r.count, list(r.failed), r.last_check, r.restarts)

    def reports(self) -> dict[str, ModuleReport]:
        return {name: self.report(name) for name in self._reports}

    # ============================================================================================== service
    def start(self) -> None:
        if self._thread is not None:
            return
        now = self._monotonic()
        self._due = {m.name: now + self.period_s(m) for m in MODULES if m.name in self._reports}
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="hardware-supervision", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.wait(self._tick_s):
            now = self._monotonic()
            for module in MODULES:
                due = self._due.get(module.name)
                if due is None or now < due:
                    continue
                try:
                    self.check(module.name)
                except Exception:
                    log.exception("hardware check of module %s failed", module.name)
                self._due[module.name] = self._monotonic() + self.period_s(module)

    # ============================================================================================== one check
    def check(self, module_name: str) -> ModuleReport:
        """Check the module's devices now, publish the events, update the system status, restart at ``maxfails``."""
        module = next(m for m in MODULES if m.name == module_name)
        failed = [name for name in module.devices if name in self._devices and not self._healthy(self._devices[name])]
        with self._lock:
            report = self._reports[module.name]
            previous = report.count
            report.count = report.count + 1 if failed else 0
            report.failed = failed
            report.last_check = self._monotonic()
            count = report.count
        maxfails = self.maxfails(module)
        bits_failed = HardwareBit(0)
        for name in failed:
            bits_failed |= module.devices[name][1]
        bits_all = HardwareBit(0)
        for name in module.devices:
            if name in self._devices:
                bits_all |= module.devices[name][1]
        if failed:
            sources = [module.devices[name][0] for name in failed]
            kind = "hardware_error" if count >= maxfails else "hardware_warning"
            log.warning("%s: %s (count %d/%d): %s", module.name, kind, count, maxfails, ", ".join(failed))
            self._emit(reports.hardware_event(kind, sources, count))
            if count >= maxfails:
                self._status.hardware_failed(bits_failed)
                if self.restart_enabled(module):
                    self._restart(module)
        elif previous:
            log.info("%s: hardware back", module.name)
            self._status.hardware_failed(bits_all, failed=False)
        return self.report(module.name)

    def _healthy(self, device: Device) -> bool:
        if device.state != DeviceState.READY:
            return False
        reader = getattr(device, "status", None)
        if reader is None:
            return True
        try:
            reader()
        except Exception as exc:                                   # noqa: BLE001 - any failure counts as one miss
            log.warning("%s: status read failed: %s", device.name, exc)
            return False
        return device.state == DeviceState.READY

    def _restart(self, module: Module) -> None:
        """Close and re-open the module's devices ("Kill / Load Server", manager Init, Setup)."""
        log.warning("restarting %s module", module.name)
        names = [name for name in module.devices if name in self._devices]
        ok = True
        for name in names:
            device = self._devices[name]
            try:
                device.close()
                device.open()
            except Exception as exc:                               # noqa: BLE001 - reported, counted at next check
                ok = False
                log.error("%s: restart failed: %s", name, exc)
        with self._lock:
            report = self._reports[module.name]
            report.restarts += 1
            if ok:
                report.count = 0
                report.failed = []
        if ok:
            bits = HardwareBit(0)
            for name in names:
                bits |= module.devices[name][1]
            self._status.hardware_failed(bits, failed=False)
        self._emit(reports.hardware_reset_event(module.reset_source))
        if self._on_reset is not None:
            self._on_reset(module.name)

    def _emit(self, event: Any) -> None:
        if self._publish is not None:
            self._publish(event)
