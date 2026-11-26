"""The core of the integrated application: the managers of ``alox.core`` assembled, the command consumer, the
services, and the :class:`alox.api.backend.ApiBackend` the REST and websocket API talk to.

LabVIEW splits this between the boot cycle, the init cycle, the producer/consumer loops of ``alox_core_ui`` and a few
background services (``docs/vision-core/map.md`` Part 1 §2.3–§2.6, Part 2 §2.6). Here:

- :meth:`Core.boot` is the boot cycle without the hardware: system status 10, the folders (frame 14), ``alox.ini``
  copied from ``alox_default.ini`` when missing and completed with its missing keys ("Load" + "Check", frame 2), the
  language (frames 4, 5), the log (frame 9), the users. The dongle check (frames 6, 7) becomes the signed licence
  file of :mod:`alox.licence`, checked only when ``[general] licence_BOOL`` is TRUE (owner L1: off by default).
- :meth:`Core.start` is the init cycle: statuses 50..57 while the devices open (cameras/Z, XY, monochromator), 58 and
  the active profile ("Init Profile", ``general.profile``), the default exposure table when the profile has none (frame
  26), then 100 "System Initialized" (frame 34). A device that did not open sets its hardware bits right after. Then
  the command consumer, the heartbeat service (``alox_heartbeat_service``: Unix seconds every ``heartbeat.frequency``
  ms) and the hardware supervision (:mod:`alox.core.supervision`) start.
- The commands of ``alox.process.queue`` (``Method`` 0..21) are handled by one consumer thread
  (:class:`alox.core.commands.CommandBus`), case for case as in ``alox_core_ui d444..d535`` (see :meth:`_register`).
- Every status change and function event is turned into its websocket event (``ws.*`` topics, :mod:`alox.api.reports`).
  "finished" is sent by the core after the function has really ended (``<X> Job Finished``, map Part 1 §8 note 4).

Not here yet: the measurement functions themselves (registered by the application on :attr:`Core.functions`), the
image manager behind :meth:`jpeg` and the stream (``images``), the user interface (event ``core.show_ui``).
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

from alox import __version__
from alox.api import reports
from alox.api.backend import Measure, VersionInfo
from alox.config.ini_writer import IniDocument, format_value
from alox.core.commands import Command, CommandBus, Method
from alox.core.exposures import ExposureTable
from alox.core.folders import SETUP_INI_NAME, Folders, create_folders
from alox.core.functions import (
    FunctionBusy,
    FunctionName,
    FunctionRunner,
    FunctionStatus,
    JobInfo,
)
from alox.core.log import AppLog
from alox.core.profiles import DEFAULT_PROFILE, ProfileError, ProfileManager
from alox.core.properties import Properties
from alox.core.status import (
    FunctionStatusID,
    HardwareBit,
    StatusRange,
    SystemCode,
    SystemStatus,
)
from alox.core.supervision import MODULES, HardwareSupervisor
from alox.core.translations import DEFAULT_LANGUAGE, Translations
from alox.core.users import Users
from alox.devices.base import Device, DeviceError, DeviceState
from alox.events import Event, EventBus

__all__ = ["CAMERA_TYPES", "Core", "CoreConfig", "ImageSource", "MeasureRequest"]

log = logging.getLogger(__name__)

#: ``Measure`` → high-level function, status name, ``<X> Job Finished`` method, report name, system running code.
_MEASURES: dict[Measure, tuple[FunctionName, str, Method, str, SystemCode]] = {
    Measure.MESA: (FunctionName.MESA_MEASURE, "mesa", Method.MESA_JOB_FINISHED, "measure_mesa",
                   SystemCode.MESA_RUNNING),
    Measure.TILT: (FunctionName.TILT_MEASURE, "tilt", Method.TILT_JOB_FINISHED, "measure_tilt",
                   SystemCode.TILT_RUNNING),
    Measure.BOWING: (FunctionName.BOWING_MEASURE, "bowing", Method.BOWING_JOB_FINISHED, "measure_bowing",
                     SystemCode.BOWING_RUNNING),
}
_BY_FUNCTION = {fn: measure for measure, (fn, *_rest) in _MEASURES.items()}
_BY_JOB_FINISHED = {method: measure for measure, (_fn, _s, method, *_rest) in _MEASURES.items()}

#: ``alox_typedef_camera_type`` names of the API (``Type`` of the camera status).
CAMERA_TYPES = {"highmag": "High Mag.", "lowmag": "Low Mag."}
#: Pixel units of the calibration (``PixelUnits`` of the camera status; index order **inferred**).
PIXEL_UNITS = ("pixels", "um", "mm")
#: Init statuses while the devices open (init cycle frames 2..22).
_OPEN_STATUS = {"highmag": SystemCode.INIT_HIGH_MAG_CAMERA, "lowmag": SystemCode.INIT_LOW_MAG_CAMERA,
                "z": SystemCode.INIT_Z_STAGE, "xy": SystemCode.INIT_XY_STAGES, "wl": SystemCode.INIT_MONOCHROMATOR}
_MODULE_STATUS = {"camz": SystemCode.INIT_CAMERAS_Z_MODULE, "xy": SystemCode.INIT_XY_MODULE,
                  "wl": SystemCode.INIT_MONOCHROMATOR_MODULE}


class ImageSource(Protocol):
    """The image manager as the core uses it (:class:`alox.core.images.ImageStore`). The overlay switch is
    ``enable_overlay``: ``set_overlay(name, shapes)`` is the functions' side of the same manager
    (:class:`alox.functions.services.ImageSink`)."""

    def image_info(self, camera: str) -> tuple[int, float | None]:
        """``(images acquired, time of the last one)`` of ``camera``."""

    def jpeg(self, quality: int, width: int, height: int) -> bytes:
        ...

    def stream_start(self, quality: int, width: int, height: int) -> None:
        ...

    def stream_stop(self) -> None:
        ...

    def overlay(self, name: str) -> bool:
        ...

    def enable_overlay(self, name: str, on: bool) -> None:
        ...

    def overlays(self) -> list[str]:
        ...


@dataclass(frozen=True)
class CoreConfig:
    """``translations``: the ``.lang`` folder (default ``<exe>/translations``). ``hostname``: the name given in the
    events URL (default: this computer's). ``supervise_hardware``: run :mod:`alox.core.supervision`."""

    translations: Path | None = None
    hostname: str | None = None
    version: VersionInfo | None = None
    supervise_hardware: bool = True
    heartbeat: bool = True


@dataclass(frozen=True)
class MeasureRequest:
    """The payload of a ``Measure <X>`` command: the SEMI job, the active profile and the request's overrides of the
    profile parameters (keys of map Part 5 Appendix A: ``x``, ``y``, ``target``, ``tilt``, ``singleshot``,
    ``positionsctrl``, ``analysis``, ``indexID``). The high-level function bundles the profile parameters itself."""

    measure: Measure
    job: JobInfo
    profile: str | None
    overrides: Mapping[str, Any] = field(default_factory=dict)


def _version_info(text: str) -> VersionInfo:
    parts = [int(p) if p.isdigit() else 0 for p in text.split(".")[:3]]
    parts += [0] * (3 - len(parts))
    return VersionInfo(parts[0], parts[1], parts[2], 1, time.strftime("%d/%m/%Y - %H:%M"))


class Core:
    """The core. ``devices`` are the application's devices by name (``highmag``, ``lowmag``, ``z``, ``xy``, ``wl``;
    any may be missing). Call :meth:`boot`, then :meth:`start`; :meth:`stop` at the end."""

    def __init__(self, folders: Folders, *, events: EventBus | None = None,
                 devices: Mapping[str, Device] | None = None, config: CoreConfig | None = None,
                 images: ImageSource | None = None) -> None:
        self.folders = folders
        self.events = events or EventBus()
        self.devices: dict[str, Device] = dict(devices or {})
        self.config = config or CoreConfig()
        self.images = images
        publish = self.events.publish
        self.system = SystemStatus(publish=publish)
        self.measures = {m: FunctionStatusID(name, publish=publish) for m, (_f, name, *_r) in _MEASURES.items()}
        self.translations = Translations(self.config.translations or folders.exe / "translations")
        self.commands = CommandBus(publish=publish)
        self.functions = FunctionRunner(publish=publish)
        self.functions.on_finished(self._function_finished)
        self.properties = Properties()
        self.profile_manager = ProfileManager.from_folders(folders)
        self.exposures = ExposureTable(self.profile_manager)
        self.users = Users(self.properties)
        self.log = AppLog(translations=self.translations)
        self.supervisor: HardwareSupervisor | None = None
        #: called (from the command thread) for ``System Shutdown`` / ``Exit``; the application stops itself.
        self.on_shutdown: Callable[[], None] | None = None
        self._hostname = self.config.hostname or socket.gethostname()
        self._version = self.config.version or _version_info(__version__)
        self._heartbeat = 0
        self._tilt = 0.0
        self._lock = threading.Lock()
        self._removers: list[Callable[[], None]] = []
        self._stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        self._started = False
        self._register()

    # ============================================================================================== boot
    def boot(self) -> None:
        """The boot cycle (module docstring). Raises when ``alox.ini`` can neither be read nor created."""
        self._bridge()
        self.system.set(SystemCode.BOOTING)
        create_folders(self.folders)
        self.properties = self._load_properties()
        self.users = Users(self.properties)
        self._set_language(self.properties.get("general.language", DEFAULT_LANGUAGE))
        self._check_licence()
        self.log = AppLog(int(self.properties.get("log.maxlines", 1000) or 1000), translations=self.translations,
                          daily_dir=self.folders.logs)
        self.system.set(SystemCode.INIT_HEARTBEAT)
        self.log.info(f"alox {__version__} booting")

    def _check_licence(self) -> None:
        """Boot frames 6, 7 with the new licence (owner L1): status 11, then 1513/1514/1515 and
        :class:`alox.licence.LicenceError` when the check is enabled and fails."""
        if not self.properties.get("general.licence", False):
            return
        from alox.licence import LicenceError, check_licence

        self.system.set(SystemCode.CHECKING_DONGLE)
        status = check_licence(self.folders.exe)
        if not status.valid:
            self.system.set(status.code)
            log.error("licence: %s", status.reason)
            raise LicenceError(status.code, status.reason)
        days = f", {status.remaining_days} day(s) left" if status.remaining_days is not None else ""
        log.info("licence valid (%s%s)", status.customer or "no customer name", days)

    def _load_properties(self) -> Properties:
        defaults = self.folders.defaults_ini
        try:
            props = Properties.load(self.folders.alox_ini, defaults)
        except FileNotFoundError:
            log.error("%s and %s are missing: empty settings, saved at exit", self.folders.alox_ini, defaults)
            return Properties(self.folders.alox_ini)
        if defaults.is_file():
            added = props.missing_from(defaults)
            if added:
                log.info("alox.ini: %d missing key(s) added from %s", len(added), defaults.name)
        return props

    def _set_language(self, language: Any) -> None:
        try:
            self.translations.set_language(str(language or DEFAULT_LANGUAGE))
        except ValueError as exc:
            log.warning("general.language: %s; %s used", exc, DEFAULT_LANGUAGE)
            self.translations.set_language(DEFAULT_LANGUAGE)

    # ============================================================================================== start / stop
    def start(self, open_device: Callable[[Device], None] | None = None) -> None:
        """The init cycle (module docstring), then the services. ``open_device`` opens one device (default
        ``device.open()``); a failure is logged and leaves the device in fault."""
        opener = open_device or (lambda device: device.open())
        for module in MODULES:
            names = [n for n in module.devices if n in self.devices]
            if not names:
                continue
            self.system.set(_MODULE_STATUS[module.name])
            for name in names:
                self.system.set(_OPEN_STATUS[name])
                device = self.devices[name]
                try:
                    opener(device)
                except Exception as exc:                          # noqa: BLE001 - reported below, the core still runs
                    self.log.error(f"{name}: {exc}")
        self.system.set(SystemCode.INIT_PROFILE)
        profile_ok = self._init_profile()
        self.system.set(SystemCode.INITIALIZED if profile_ok else SystemCode.PROFILE_ERROR)
        self._report_failed_devices()
        self.commands.start()
        if self.config.heartbeat:
            self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, name="core-heartbeat", daemon=True)
            self._heartbeat_thread.start()
        if self.config.supervise_hardware and self.devices:
            self.supervisor = HardwareSupervisor(self.devices, self.system, publish=self.events.publish,
                                                 setting=self.properties.get,
                                                 on_reset=lambda module: self.post(Method.MODULE_RESETTED, module))
            self.supervisor.start()
        self._started = True
        self.log.info("system initialized")

    def _init_profile(self) -> bool:
        """"Init Profile" (frames 25, 26): load ``general.profile`` (the default profile is created when the profiles
        folder is empty, boot frame 14), then create the default exposure table if the profile has none."""
        try:
            self.profile_manager.ensure_default_profile(properties=self.properties)
            name = str(self.properties.get("general.profile", DEFAULT_PROFILE) or DEFAULT_PROFILE)
            if not self.profile_manager.exists(name):
                self.log.warning(f"profile {name} not found; {DEFAULT_PROFILE} loaded")
                name = DEFAULT_PROFILE if self.profile_manager.exists(DEFAULT_PROFILE) else self.profile_manager.list()[0]
            self.load_profile(name)
            if not self.exposures.wavelengths():
                self.exposures.create_default()
                self.log.info("default exposure table created")
        except (ProfileError, OSError, ValueError, IndexError) as exc:
            self.log.error(f"profile: {exc}")
            return False
        return True

    def _report_failed_devices(self) -> None:
        failed = [name for name, device in self.devices.items() if device.state != DeviceState.READY]
        for name in failed:
            for module in MODULES:
                if name in module.devices:
                    source, bits = module.devices[name]
                    self.system.hardware_failed(bits)
                    self.events.publish(reports.hardware_event("hardware_error", [source], 1))

    def stop(self) -> None:
        """Stop the running function, the services and the command consumer (the devices belong to the application).
        Saves ``alox.ini`` (exit cycle "EXIT: Saving Calibration": properties "Save")."""
        if self.functions.running is not None:
            self.functions.stop()
        self._stop.set()
        if self.supervisor is not None:
            self.supervisor.stop()
        self.commands.stop()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(5.0)
            self._heartbeat_thread = None
        if self.properties.path is not None and (self.properties.dirty or not self.properties.path.exists()):
            try:
                self.properties.save()
            except OSError as exc:
                log.error("alox.ini not saved: %s", exc)
        for remove in self._removers:
            remove()
        self._removers.clear()
        if self._started:
            self.system.set(SystemCode.EXIT)

    # ============================================================================================== services
    def _heartbeat_loop(self) -> None:
        while True:
            self.beat()
            period = max(0.1, float(self.properties.get("heartbeat.frequency", 1000) or 1000) / 1000.0)
            if self._stop.wait(period):
                return

    def beat(self) -> int:
        """One heartbeat: global "Core Heartbeat" = Unix seconds, and the websocket ``heartbeat`` event."""
        value = int(time.time())
        with self._lock:
            self._heartbeat = value
        self.events.publish(reports.heartbeat_event(value))
        return value

    def _bridge(self) -> None:
        """Status and function events → websocket events."""
        if self._removers:
            return
        self._removers.append(self.events.on(("status",), self._on_status))
        self._removers.append(self.events.on(("function",), self._on_function))

    def _on_status(self, event: Event) -> None:
        code = int(event.data["code"])
        if event.topic == "status.system":
            self.events.publish(reports.status_event(code))
        else:
            self.events.publish(reports.measure_status_event(event.topic.removeprefix("status."), code))

    def _on_function(self, event: Event) -> None:
        _, name, kind = event.topic.split(".", 2)
        try:
            function = FunctionName[name.upper()]
        except KeyError:
            return
        measure = _BY_FUNCTION.get(function)
        if measure is None or kind not in ("started", "failed"):
            return
        report = _MEASURES[measure][3]
        self.events.publish(reports.function_event(kind, report, event.data.get("job")))

    def _function_finished(self, name: FunctionName, job: JobInfo | None, status: FunctionStatus,
                           result: Any) -> None:
        """Runner callback (function thread): back to idle; a finished high-level measurement queues its
        ``<X> Job Finished`` so that the websocket ``finished`` comes after the function has ended."""
        if self.system.range() in (StatusRange.STARTING, StatusRange.RUNNING):
            self.system.set(SystemCode.IDLE)
        measure = _BY_FUNCTION.get(name)
        if measure is None:
            return
        if measure is Measure.TILT and status == FunctionStatus.FINISHED:
            angle = getattr(result, "angle_deg", result if isinstance(result, int | float) else None)
            if angle is not None:
                with self._lock:
                    self._tilt = float(angle)
        if status == FunctionStatus.FINISHED:
            self.post(_MEASURES[measure][2], job)

    # ============================================================================================== commands
    def post(self, method: Method, payload: Any = None) -> None:
        self.commands.post(method, payload, source="api")

    def _register(self) -> None:
        bus = self.commands
        for measure in _MEASURES:
            bus.register(Method(int(measure)), self._cmd_measure)
        for method in _BY_JOB_FINISHED:
            bus.register(method, self._cmd_job_finished)
        bus.register(Method.STOP, self._cmd_stop)
        bus.register(Method.ABORT, self._cmd_abort)
        bus.register(Method.LOAD_PROFILE, lambda c: self.load_profile(str(c.payload)))
        bus.register(Method.GET_IMAGE, self._cmd_get_image)
        bus.register(Method.SYSTEM_RESET, self._cmd_reset)
        bus.register(Method.SYSTEM_SHUTDOWN, self._cmd_shutdown)
        bus.register(Method.EXIT, self._cmd_shutdown)
        bus.register(Method.SHOW_UI, lambda c: self.events.publish(Event("core.show_ui", "core",
                                                                          {"visible": bool(c.payload)})))
        for method in (Method.PROFILES_FOLDER_CHANGED, Method.REFRESH_PROFILES):
            bus.register(method, lambda _c: self.events.publish(Event("core.profiles", "core",
                                                                      {"profiles": self.profile_manager.list()})))
        bus.register(Method.REFRESH_CORE_INFOS, lambda _c: self.events.publish(Event("core.infos", "core", {
            "mode": self.properties.get("general.mode", 0), "core": self.properties.get("general.core", 1)})))
        bus.register(Method.MODULE_RESETTED, lambda c: self.log.warning(f"hardware module {c.payload} restarted"))
        for method in (Method.INIT, Method.MODULE_HEARTBEAT, Method.LOG_WINDOW_HIDDEN, Method.AXIS_REFERENCING):
            bus.register(method, lambda _c: None)                    # empty cases in LabVIEW, or handled elsewhere

    def _cmd_measure(self, command: Command) -> None:
        request: MeasureRequest = command.payload
        function, _name, _method, report, running = _MEASURES[request.measure]
        status = self.measures[request.measure]
        if self.properties.get("log.webapi", False):
            self.log.info(f"function '{report}' starts")
        status.set(200)
        self.system.set(SystemCode.STARTING)
        try:
            self.functions.start(function, request, job=request.job)
        except (FunctionBusy, KeyError) as exc:
            status.set(10)
            self.system.set(SystemCode.IDLE)
            self.log.error(f"{report}: {exc}")
            self.events.publish(reports.function_event("failed", report, request.job))
            raise
        self.system.set(running)

    def _cmd_job_finished(self, command: Command) -> None:
        measure = _BY_JOB_FINISHED[command.method]
        report = _MEASURES[measure][3]
        self.events.publish(reports.function_event("finished", report, command.payload))
        self.log.info(f"{measure.name.capitalize()} (Finished)")

    def _cmd_stop(self, _command: Command) -> None:
        """``Stop`` (``alox_core_ui`` consumer, d473): status 802, log 001651, stop the function, Z joystick off and the
        camera goes up (Z to 0), then idle (**inferred**: the end status is not visible)."""
        self.system.set(SystemCode.STOP)
        self.log.info(self.translations.text(1651))
        self.functions.stop()
        z = self.devices.get("z")
        if z is not None and z.state == DeviceState.READY:
            try:
                z.set_joystick(False)                                   # type: ignore[attr-defined]
                z.move_absolute(0.0)                                    # type: ignore[attr-defined]
                self.log.info("Camera goes up")
            except (DeviceError, ValueError) as exc:
                self.log.error(f"stop: Z: {exc}")
        self.system.set(SystemCode.IDLE)

    def _cmd_abort(self, _command: Command) -> None:
        """``Abort``: "Emergency stop of all running functions" (status 803), then every motion stopped."""
        self.system.set(SystemCode.ABORT)
        self.functions.abort()
        for name in ("xy", "z", "wl"):
            device = self.devices.get(name)
            stop = getattr(device, "stop", None)
            if device is not None and stop is not None and device.state == DeviceState.READY:
                try:
                    stop()
                except DeviceError as exc:
                    self.log.error(f"abort: {name}: {exc}")
        self.log.warning("abort")
        self.system.set(SystemCode.IDLE)

    def _cmd_get_image(self, command: Command) -> None:
        self.functions.start(FunctionName.GET_IMAGE, command.payload)
        self.system.set(SystemCode.LOW_LEVEL_RUNNING)

    def _cmd_reset(self, _command: Command) -> None:
        """``System Reset``: status 801, log, status 500 (the re-init call is gone in LabVIEW, d525)."""
        self.system.set(SystemCode.RESET)
        self.log.info("system reset")
        self.system.set(SystemCode.IDLE)

    def _cmd_shutdown(self, _command: Command) -> None:
        self.log.info("shutdown")
        if self.on_shutdown is not None:
            self.on_shutdown()

    # ============================================================================================== profiles
    def load_profile(self, name: str) -> None:
        """``Load Profile``: load and check the recipe, ``general.profile`` = name, event ``core.profile``."""
        self.profile_manager.load(name)
        self.profile_manager.check()
        self.properties.set("general.profile", name)
        self.events.publish(Event("core.profile", "core", {"profile": name}))
        self.log.info(f"Profile loaded: {name}")

    # ============================================================================================== ApiBackend
    def system_status(self) -> tuple[int, str]:
        code = self.system.code
        return code, self.system.description(self.translations, code)

    def measure_status(self, measure: Measure) -> tuple[int, str]:
        holder = self.measures[measure]
        code = holder.code
        return code, holder.description(self.translations, code)

    def heartbeat(self) -> int:
        with self._lock:
            return self._heartbeat

    def get_property(self, dotted: str, default: Any = None) -> Any:
        return self.properties.get(dotted, default)

    def set_property(self, dotted: str, value: Any) -> None:
        self.properties.set(dotted, value)
        if self.properties.path is not None:
            self.properties.save()

    def version(self) -> VersionInfo:
        return self._version

    def text(self, code: int) -> str:
        return self.translations.text(code)

    def hostname(self) -> str:
        return self._hostname

    def profiles(self) -> list[str]:
        return self.profile_manager.list()

    def current_profile(self) -> tuple[str, str]:
        return str(self.profile_manager.profiles), self.profile_manager.current or ""

    def profiles_folder(self) -> str:
        return str(self.profile_manager.profiles)

    def set_profiles_folder(self, path: str) -> None:
        """Write ``alox_setup.ini [folders] profiles`` and use the folder; :class:`ValueError` when it does not
        exist."""
        folder = Path(path)
        if not folder.is_dir():
            raise ValueError(f"profiles folder {path!r} does not exist")
        setup = self.folders.exe / SETUP_INI_NAME
        doc = IniDocument.load(setup) if setup.is_file() else IniDocument.parse("")
        doc.set("folders", "profiles", format_value("PATH", str(folder)))
        doc.save(setup)
        self.folders = replace(self.folders, profiles=folder)
        self.profile_manager.profiles = folder
        self.log.info(f"profiles folder: {folder}")

    def images_folder(self) -> str:
        return str(self.folders.images)

    def can_start(self, measure: Measure | None) -> bool:
        return self.functions.condition_ok()

    def not_ready(self, measure: Measure) -> None:
        self.measures[measure].set(10)

    def measure_request(self, measure: Measure, job: JobInfo, overrides: Mapping[str, Any]) -> MeasureRequest:
        return MeasureRequest(measure, job, self.profile_manager.current, dict(overrides))

    def tilt(self) -> float:
        with self._lock:
            return self._tilt

    def reset_tilt(self) -> None:
        with self._lock:
            self._tilt = 0.0

    def camera_status(self, camera: str) -> Mapping[str, str]:
        """``Type``, ``Status``, ``ImageTotal``, ``Timestamp``, ``PixelSize``, ``PixelUnits``, ``ExposureTime``
        (strings). Pixel size and units from ``hardware.camera.<camera>.pixelsize`` / ``units``."""
        device = self.devices.get(camera)
        running, exposure = False, None
        if device is not None and device.state == DeviceState.READY:
            try:
                status = device.status()                                  # type: ignore[attr-defined]
                running, exposure = bool(status.acquiring), status.exposure_us
            except DeviceError as exc:
                log.warning("%s status: %s", camera, exc)
        total, stamp = self.images.image_info(camera) if self.images is not None else (0, None)
        units = self.properties.get(f"hardware.camera.{camera}.units", 0)
        if isinstance(units, int) and 0 <= units < len(PIXEL_UNITS):
            units = PIXEL_UNITS[units]
        return {"Type": CAMERA_TYPES.get(camera, camera), "Status": "Running" if running else "Stopped",
                "ImageTotal": str(int(total)),
                "Timestamp": "" if stamp is None else time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stamp)),
                "PixelSize": f"{float(self.properties.get(f'hardware.camera.{camera}.pixelsize', 1.0)):f}",
                "PixelUnits": str(units), "ExposureTime": "" if exposure is None else f"{float(exposure):f}"}

    def overlays(self) -> list[str]:
        return self._images().overlays()

    def overlay(self, name: str) -> bool:
        return self._images().overlay(name)

    def set_overlay(self, name: str, on: bool) -> None:
        self._images().enable_overlay(name, on)

    def jpeg(self, quality: int, width: int, height: int) -> bytes:
        return self._images().jpeg(quality, width, height)

    def stream_start(self, quality: int, width: int, height: int) -> None:
        self._images().stream_start(quality, width, height)

    def stream_stop(self) -> None:
        self._images().stream_stop()

    def _images(self) -> ImageSource:
        if self.images is None:
            raise RuntimeError("no image manager")
        return self.images

    # ============================================================================================== helpers
    def hardware_bits(self) -> HardwareBit:
        return self.system.hardware_bits

