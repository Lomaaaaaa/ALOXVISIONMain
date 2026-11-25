"""The core (``alox.core.core``) and the hardware supervision (``alox.core.supervision``), without hardware."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from alox.api.backend import Measure
from alox.core.commands import Method
from alox.core.core import Core, CoreConfig, MeasureRequest
from alox.core.folders import Folders
from alox.core.functions import (
    FunctionContext,
    FunctionName,
    JobInfo,
    MeasurementFunction,
)
from alox.core.status import HardwareBit, SystemCode, SystemStatus
from alox.core.supervision import HardwareSupervisor
from alox.devices.base import Device, DeviceError, DeviceState
from alox.devices.zstage import ZStage
from alox.events import Event, EventBus
from aloxcam.settings import SimulationOptions, StageOptions, ZStageSettings
from aloxcam.zstage.sim import SimStage

SYSTEM_LANG = "000010;Booting\n000100;System initialized\n000500;Idle\n000802;Stop\n001001;ERROR (High mag)\n"
ALOX_LANG = "009000;no error\n001651;stop current function\n"
DEFAULTS_INI = "[general]\nprofile_STR = DEFAULT\nmode_I32 = 0\nlanguage_STR = en\n[heartbeat]\nfrequency_I32 = 50\n"


def make_folders(tmp_path: Path) -> Folders:
    exe = tmp_path / "exe"
    (exe / "translations").mkdir(parents=True)
    (exe / "translations" / "system_en.lang").write_text(SYSTEM_LANG, encoding="cp1252")
    (exe / "translations" / "alox_en.lang").write_text(ALOX_LANG, encoding="cp1252")
    (exe / "alox_default.ini").write_text(DEFAULTS_INI, encoding="cp1252")
    root = tmp_path / "alox"
    return Folders(exe=exe, settings=root / "settings", vision=root / "vision", data=root / "data",
                   profiles=root / "profiles", exposures=root / "exposures", jobhistory=root / "jobhistory")


class Recorder:
    """The events of the bus, for assertions (thread-safe)."""

    def __init__(self, bus: EventBus) -> None:
        self.events: list[Event] = []
        self._lock = threading.Lock()
        bus.on((), self._add)

    def _add(self, event: Event) -> None:
        with self._lock:
            self.events.append(event)

    def topics(self, prefix: str = "") -> list[str]:
        with self._lock:
            return [e.topic for e in self.events if e.topic.startswith(prefix)]

    def of(self, topic: str) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(e.data) for e in self.events if e.topic == topic]

    def wait_for(self, topic: str, count: int = 1, timeout: float = 5.0) -> list[dict[str, Any]]:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            found = self.of(topic)
            if len(found) >= count:
                return found
            time.sleep(0.01)
        raise AssertionError(f"{topic} not seen {count} time(s); seen: {self.topics()}")


class FakeMeasure(MeasurementFunction):
    """A high-level function that waits for ``release`` (or a stop) and returns ``result``."""

    def __init__(self, name: FunctionName, result: Any = None) -> None:
        self.name = name
        self.result = result
        self.release = threading.Event()
        self.seen: list[Any] = []

    def run(self, params: Any, ctx: FunctionContext) -> Any:
        self.seen.append(params)
        while not self.release.wait(0.01):
            ctx.check()
        return self.result


@pytest.fixture
def core(tmp_path: Path) -> Iterator[Core]:
    c = Core(make_folders(tmp_path), config=CoreConfig(hostname="alox-pc", supervise_hardware=False,
                                                       heartbeat=False))
    yield c
    c.stop()


def booted(core: Core, open_device: Callable[[Device], None] | None = None) -> Recorder:
    rec = Recorder(core.events)
    core.boot()
    core.start(open_device)
    return rec


# ============================================================================================== boot and start
def test_boot_creates_the_folders_settings_and_default_profile(core: Core):
    rec = booted(core)
    f = core.folders
    assert f.alox_ini.is_file() and f.profile.is_dir() and f.logs.is_dir() and f.data_jobhistory.is_dir()
    assert core.properties.get("general.profile") == "DEFAULT"
    assert (f.profiles / "default.zip").is_file()
    assert core.profile_manager.current == "DEFAULT"
    assert core.exposures.wavelengths()                                   # default table created (frame 26)
    assert core.system_status() == (100, "System initialized")
    codes = [d["code"] for d in rec.of("status.system")]
    assert codes[0] == SystemCode.BOOTING and codes[-1] == SystemCode.INITIALIZED
    assert [d["State"] for d in rec.of("ws.status")][-1] == "100"


def test_start_opens_the_devices_with_their_statuses_and_reports_failures(core: Core):
    opened: list[str] = []

    class Failing(Device):
        def _open(self) -> None:
            raise DeviceError(self.name, "no camera")

    z = ZStage(SimStage(SimulationOptions(stage_reference_s=0.01), dh_running=False),
               ZStageSettings(min_pos=0.0, max_pos=20.0, speed=20.0),
               StageOptions(always_reference=False), publish=core.events.publish)
    core.devices.update({"highmag": Failing("highmag"), "z": z})

    def opener(device: Device) -> None:
        opened.append(device.name)
        device.open(timeout=10)

    rec = booted(core, opener)
    assert opened == ["highmag", "z"]                                     # cameras/Z module order
    codes = [d["code"] for d in rec.of("status.system")]
    assert codes.index(SystemCode.INIT_HIGH_MAG_CAMERA) < codes.index(SystemCode.INIT_Z_STAGE)
    assert core.system.code == 1000 + int(HardwareBit.HIGH_MAG)
    assert rec.of("ws.hardware_error") == [{"source": ["camera_high_mag_status"], "count": "1"}]
    assert rec.of("ws.status")[-1] == {"State": "500"}                    # hardware errors are sent as 500
    z.shutdown()


def test_a_missing_default_ini_gives_empty_settings(tmp_path: Path):
    folders = make_folders(tmp_path)
    (folders.exe / "alox_default.ini").unlink()
    c = Core(folders, config=CoreConfig(supervise_hardware=False, heartbeat=False))
    try:
        c.boot()
        c.start()
        assert c.system.code == SystemCode.INITIALIZED
    finally:
        c.stop()
    assert folders.alox_ini.is_file()                                     # saved at exit


def test_heartbeat_publishes_unix_seconds(core: Core):
    rec = Recorder(core.events)
    value = core.beat()
    assert abs(value - time.time()) < 2 and core.heartbeat() == value
    assert rec.of("ws.heartbeat") == [{"timestamp": str(value)}]


def test_heartbeat_service_follows_the_frequency(tmp_path: Path):
    c = Core(make_folders(tmp_path), config=CoreConfig(supervise_hardware=False))
    rec = booted(c)
    try:
        rec.wait_for("ws.heartbeat", 3, timeout=3)                       # frequency 50 ms in the defaults
    finally:
        c.stop()


# ============================================================================================== measurements
def test_measure_runs_the_function_and_sends_started_then_finished(core: Core):
    function = FakeMeasure(FunctionName.MESA_MEASURE)
    core.functions.register(function)
    rec = booted(core)
    job = JobInfo(control_job_id="CJ1", wafer_id="W7")
    assert core.can_start(Measure.MESA)
    request = core.measure_request(Measure.MESA, job, {"x": 1.5, "singleshot": True})
    assert request == MeasureRequest(Measure.MESA, job, "DEFAULT", {"x": 1.5, "singleshot": True})
    core.post(Method.MEASURE_MESA, request)
    started = rec.wait_for("ws.started")
    assert started[0]["Function"] == "measure_mesa" and started[0]["Job"]["Wafer ID"] == "W7"
    assert rec.wait_for("status.mesa")[0]["code"] == 200
    assert not core.can_start(Measure.TILT)
    end = time.monotonic() + 5
    while core.system.code != SystemCode.MESA_RUNNING and time.monotonic() < end:
        time.sleep(0.01)
    assert core.system.code == SystemCode.MESA_RUNNING
    assert rec.of("ws.finished") == []
    function.release.set()
    finished = rec.wait_for("ws.finished")
    assert finished[0] == {"Function": "measure_mesa", "Job": started[0]["Job"]}
    assert core.system.code == SystemCode.IDLE
    assert function.seen == [request]


def test_tilt_result_becomes_the_tilt_value(core: Core):
    function = FakeMeasure(FunctionName.TILT_MEASURE, result=0.25)
    function.release.set()
    core.functions.register(function)
    rec = booted(core)
    core.post(Method.MEASURE_TILT, core.measure_request(Measure.TILT, JobInfo(), {}))
    rec.wait_for("ws.finished")
    assert core.tilt() == pytest.approx(0.25)
    core.reset_tilt()
    assert core.tilt() == 0.0


def test_measure_without_its_function_fails_cleanly(core: Core):
    rec = booted(core)
    future = core.commands.post(Method.MEASURE_BOWING, core.measure_request(Measure.BOWING, JobInfo(), {}))
    with pytest.raises(KeyError):
        future.result(5)
    assert rec.of("ws.failed")[0]["Function"] == "measure_bowing"
    assert core.measures[Measure.BOWING].code == 10
    assert core.system.code == SystemCode.IDLE


def test_not_ready_sets_the_measure_status_10(core: Core):
    booted(core)
    core.not_ready(Measure.TILT)
    assert core.measure_status(Measure.TILT)[0] == 10


def test_stop_stops_the_function_and_raises_the_camera(core: Core):
    z = ZStage(SimStage(SimulationOptions(stage_reference_s=0.01), dh_running=False),
               ZStageSettings(min_pos=0.0, max_pos=20.0, speed=20.0),
               StageOptions(always_reference=False), publish=core.events.publish)
    core.devices["z"] = z
    function = FakeMeasure(FunctionName.MESA_MEASURE)
    core.functions.register(function)
    rec = booted(core, lambda d: d.open(timeout=10))
    try:
        z.move_absolute(3.0)
        core.post(Method.MEASURE_MESA, core.measure_request(Measure.MESA, JobInfo(), {}))
        rec.wait_for("ws.started")
        core.commands.post(Method.STOP).result(10)
        assert core.functions.running is None
        assert z.position() == pytest.approx(0.0, abs=1e-3)
        assert SystemCode.STOP in [d["code"] for d in rec.of("status.system")]
        assert core.system.code == SystemCode.IDLE
        assert rec.of("ws.finished") == []                               # a stopped function is not "finished"
    finally:
        z.shutdown()


def test_abort_and_reset_statuses(core: Core):
    rec = booted(core)
    core.commands.post(Method.ABORT).result(5)
    core.commands.post(Method.SYSTEM_RESET).result(5)
    codes = [d["code"] for d in rec.of("status.system")]
    assert codes[-4:] == [SystemCode.ABORT, SystemCode.IDLE, SystemCode.RESET, SystemCode.IDLE]


def test_shutdown_command_calls_the_application(core: Core):
    booted(core)
    called = threading.Event()
    core.on_shutdown = called.set
    core.commands.post(Method.SYSTEM_SHUTDOWN).result(5)
    assert called.is_set()


# ============================================================================================== profiles, settings
def test_load_profile_command(core: Core):
    rec = booted(core)
    core.profile_manager.copy("DEFAULT", "R1")
    core.commands.post(Method.LOAD_PROFILE, "R1").result(5)
    assert core.profile_manager.current == "R1" and core.properties.get("general.profile") == "R1"
    assert rec.of("core.profile") == [{"profile": "DEFAULT"}, {"profile": "R1"}]
    core.commands.post(Method.REFRESH_PROFILES).result(5)
    assert rec.of("core.profiles")[-1] == {"profiles": ["DEFAULT", "R1"]}


def test_set_profiles_folder_writes_alox_setup_ini(core: Core, tmp_path: Path):
    booted(core)
    other = tmp_path / "other profiles"
    with pytest.raises(ValueError):
        core.set_profiles_folder(str(other))
    other.mkdir()
    core.set_profiles_folder(str(other))
    assert core.profiles_folder() == str(other) and core.profile_manager.list() == []
    text = (core.folders.exe / "alox_setup.ini").read_text(encoding="cp1252")
    assert "[folders]" in text and "profiles" in text


def test_set_property_saves_alox_ini(core: Core):
    booted(core)
    core.set_property("general.mode", 1)
    assert "mode_I32 = 1" in core.folders.alox_ini.read_text(encoding="cp1252")
    assert core.get_property("general.mode") == 1


def test_api_on_the_core(core: Core):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from alox.api.rest import create_app

    booted(core)
    client = TestClient(create_app(core, log_requests=False))
    assert client.get("/alox/v1/system/status").json() == {"status": 100, "description": "System initialized"}
    assert client.get("/alox/v1/system/profiles").json() == {"profiles": ["DEFAULT"]}
    assert client.get("/alox/v1/system/events").json()["url"] == "ws://alox-pc:6123"
    reply = client.put("/alox/v1/functions/measuretilt", params={"x": "1.5"})
    assert reply.status_code == 200 and reply.text == "no error"          # queued; the function is missing


# ============================================================================================== supervision
class Flaky(Device):
    """A device whose status read fails while ``broken``; its open fails while ``dead``."""

    def __init__(self, name: str) -> None:
        super().__init__(name, default_timeout_s=5.0)
        self.broken = False
        self.dead = False

    def _open(self) -> None:
        if self.dead:
            raise DeviceError(self.name, "dead")

    def status(self) -> str:
        def read() -> str:
            if self.broken:
                raise DeviceError(self.name, "no answer")
            return "ok"
        return self.call("status", read)


def test_supervision_warns_then_errors_then_restarts():
    bus = EventBus()
    rec = Recorder(bus)
    status = SystemStatus(publish=bus.publish, initial=500)
    cam = Flaky("highmag")
    cam.open()
    resets: list[str] = []
    settings = {"hardware.camera.maxfails": 2, "hardware.camera.watchdog": True}
    sup = HardwareSupervisor({"highmag": cam}, status, publish=bus.publish,
                             setting=lambda name, default: settings.get(name, default), on_reset=resets.append)
    assert sup.check("camz").count == 0
    cam.broken = True
    assert sup.check("camz").count == 1
    assert rec.of("ws.hardware_warning") == [{"source": ["camera_high_mag_status"], "count": "1"}]
    assert status.code == 500
    report = sup.check("camz")                                            # count 2 = maxfails: error + restart
    assert rec.of("ws.hardware_error") == [{"source": ["camera_high_mag_status"], "count": "2"}]
    assert report.restarts == 1 and report.count == 0
    assert rec.of("ws.hardware_reset") == [{"source": "cameras_z_stage_module"}]
    assert resets == ["camz"] and status.code == 500                     # bits cleared by the successful restart
    cam.shutdown()


def test_supervision_keeps_the_bits_while_the_device_is_down():
    bus = EventBus()
    status = SystemStatus(publish=bus.publish, initial=500)
    xy = Flaky("xy")
    xy.open()
    sup = HardwareSupervisor({"xy": xy}, status, publish=bus.publish,
                             setting=lambda name, default: {"hardware.xy.maxfails": 1}.get(name, default))
    xy.broken = True
    xy.dead = True
    sup.check("xy")
    assert status.hardware_bits == HardwareBit.X | HardwareBit.Y
    xy.broken = False
    xy.dead = False
    xy.open()
    sup.check("xy")
    assert status.code == 500                                             # back: bits cleared
    xy.shutdown()


def test_supervision_without_restart():
    bus = EventBus()
    status = SystemStatus(publish=bus.publish, initial=500)
    wl = Flaky("wl")
    wl.open()
    settings = {"hardware.monochromator.maxfails": 1, "hardware.monochromator.watchdog": False}
    sup = HardwareSupervisor({"wl": wl}, status, setting=lambda name, default: settings.get(name, default))
    wl.broken = True
    assert sup.check("wl").restarts == 0
    assert status.hardware_bits == HardwareBit.MONOCHROMATOR
    assert wl.state == DeviceState.READY
    wl.shutdown()


def test_supervision_service_runs_on_its_period():
    bus = EventBus()
    rec = Recorder(bus)
    cam = Flaky("lowmag")
    cam.open()
    cam.broken = True
    sup = HardwareSupervisor({"lowmag": cam}, SystemStatus(initial=500), publish=bus.publish,
                             setting=lambda name, default: {"hardware.camera.heartbeat": 1000,
                                                            "hardware.camera.watchdog": False}.get(name, default),
                             tick_s=0.01)
    sup.start()
    try:
        rec.wait_for("ws.hardware_warning", timeout=3)
    finally:
        sup.stop()
        cam.shutdown()
