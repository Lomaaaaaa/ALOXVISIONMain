"""The GenICam camera backend (``alox.devices.camera_genicam``) on the fake Harvesters stack
(:mod:`alox.devices.fake_harvesters`: Harvester, image acquirer, GenApi nodes with selectors, context-managed buffers).
"""

from __future__ import annotations

import os
import sys
import threading
import time
import types
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from alox.devices.base import DeviceState
from alox.devices.camera import Camera
from alox.devices.camera_genicam import (
    DeviceInfo,
    GenICamBackend,
    GenICamFeature,
    HarvestersAcquirer,
    RawImage,
    find_gentl_producers,
    genicam_feature_names,
    match_device,
    mono_pixels,
    serial_number_words,
)
from alox.devices.fake_harvesters import (
    FakeAcquirer,
    FakeCamera,
    FakeGenApiError,
    FakeGenTLError,
    FakeHarvester,
)
from aloxcam.camera.attributes import (
    DEVICE_TEMPERATURE,
    LINE_INVERTER,
    LINE_SELECTOR,
    LINE_SOURCE,
    TRIGGER_MODE,
    TRIGGER_SOFTWARE,
    USER_OUTPUT_SELECTOR,
    USER_OUTPUT_VALUE,
)
from aloxcam.camera.backend import ValueType
from aloxcam.camera.sim import (
    SIM_EXPOSURE,
    SIM_EXPOSURE_TIME,
    SIM_FRAME_RATE,
    SIM_GAIN,
    SIM_GAIN_ANALOG,
)
from aloxcam.errors import CameraError, Code
from aloxcam.settings import CameraSettings, LightSettings

DBL, I64, U32, BOOL, STRING = ValueType.DBL, ValueType.I64, ValueType.U32, ValueType.BOOL, ValueType.STRING
WIDTH = "CameraAttributes::ImageFormatControl::Width"
USER_ID = "CameraAttributes::DeviceControl::DeviceUserID"


def two_cameras() -> list[FakeCamera]:
    return [FakeCamera("HighMag", "4103000001"), FakeCamera("LowMag", "4103000002")]


def wait_until(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "condition not reached"
        time.sleep(0.001)


@pytest.fixture
def cti(tmp_path: Path) -> Path:
    path = tmp_path / "ids_gevgentlk.cti"
    path.write_bytes(b"")
    return path


@pytest.fixture
def stack(cti: Path) -> Iterator[Callable[..., tuple[GenICamBackend, FakeHarvester]]]:
    backends: list[GenICamBackend] = []

    def make(cameras: list[FakeCamera] | None = None, **kwargs: Any) -> tuple[GenICamBackend, FakeHarvester]:
        harvester = FakeHarvester(two_cameras() if cameras is None else cameras)
        kwargs.setdefault("poll_s", 0.005)
        backend = GenICamBackend(cti, harvester_factory=lambda: harvester, **kwargs)
        backends.append(backend)
        return backend, harvester

    yield make
    for backend in backends:
        backend.close()


def started(stack: Callable[..., tuple[GenICamBackend, FakeHarvester]], num_buffers: int = 3, *,
            continuous: bool = True) -> tuple[Any, FakeAcquirer, FakeCamera]:
    backend, harvester = stack()
    device = backend.open_camera("HighMag")
    device.configure(continuous, num_buffers)
    device.start()
    camera = harvester.cameras[0]
    assert camera.acquirer is not None
    return device, camera.acquirer, camera


# ================================================================================================== driver, names
def test_without_harvesters_the_backend_reports_it(monkeypatch, cti):
    monkeypatch.setitem(sys.modules, "harvesters", None)
    monkeypatch.setitem(sys.modules, "harvesters.core", None)
    backend = GenICamBackend(cti)
    with pytest.raises(CameraError) as err:
        backend.open_camera("HighMag")
    assert err.value.code == Code.CAMERA_SDK and "pip install harvesters" in err.value.source
    backend.close()


def test_harvesters_is_imported_lazily(monkeypatch, cti):
    harvester = FakeHarvester(two_cameras())
    core = types.ModuleType("harvesters.core")
    core.Harvester = lambda: harvester                         # type: ignore[attr-defined]
    package = types.ModuleType("harvesters")
    package.core = core                                        # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "harvesters", package)
    monkeypatch.setitem(sys.modules, "harvesters.core", core)
    backend = GenICamBackend([cti], update_timeout_ms=3000)
    try:
        device = backend.open_camera("LowMag")
        assert harvester.files == [str(cti)] and harvester.timeout_for_update == 3000
        assert device.info.serial_number == "4103000002"
    finally:
        backend.close()
    assert harvester.resets == 1


def test_producer_files(monkeypatch, tmp_path, cti):
    folder = tmp_path / "cti"
    folder.mkdir()
    for name in ("ids_u3vgentl.cti", "ids_gevgentl.cti", "readme.txt"):
        (folder / name).write_bytes(b"")
    env = {"GENICAM_GENTL64_PATH": f"{tmp_path / 'missing'}{os.pathsep}{folder}"}
    assert [p.name for p in find_gentl_producers(env)] == ["ids_gevgentl.cti", "ids_u3vgentl.cti"]
    monkeypatch.delenv("GENICAM_GENTL64_PATH", raising=False)
    for backend, text in ((GenICamBackend(None, harvester_factory=lambda: None), "no GenTL producer"),
                          (GenICamBackend(tmp_path / "no.cti", harvester_factory=lambda: None), "not found")):
        with pytest.raises(CameraError, match=text):
            backend.open_camera("HighMag")


def test_match_device_order():
    infos = [
        DeviceInfo(0, id="dev-a", model="GV-5280CP-M", display_name="IDS A", user_defined_name="HighMag",
                   serial_number="4103000001"),
        DeviceInfo(1, id="dev-b", model="GV-5280CP-M", display_name="IDS B", user_defined_name="LowMag",
                   serial_number="4103000002"),
        DeviceInfo(2, id="dev-a-k", model="GV-5280CP-M", display_name="IDS A (GEVK)", serial_number="4103000001"),
    ]
    assert match_device("HighMag", infos).index == 0           # the camera seen by two producers counts once
    assert match_device("4103000002", infos).index == 1
    assert match_device("IDS B", infos).index == 1
    assert match_device("dev-b", infos).index == 1
    assert match_device("lowmag", infos).index == 1            # case-insensitive pass
    assert match_device("cam0", infos, {"cam0": "4103000002"}).index == 1
    assert match_device("HighMag", infos, {"HighMag": "4103000002"}).index == 1   # name_map first
    by_user = [DeviceInfo(0, user_defined_name="4103000002", serial_number="1"),
               DeviceInfo(1, serial_number="4103000002")]
    assert match_device("4103000002", by_user).index == 0      # user-defined name before serial number
    with pytest.raises(CameraError, match="matches 2 cameras by model"):
        match_device("GV-5280CP-M", infos)
    with pytest.raises(CameraError, match="'cam0' not found.*HighMag"):
        match_device("cam0", infos)
    with pytest.raises(CameraError, match="serial number 999 in name_map"):
        match_device("cam1", infos, {"cam1": "999"})


def test_open_by_name_exclusive_and_without_rescan(stack):
    backend, harvester = stack(name_map={"cam0": 4103000002})
    hm = backend.open_camera("HighMag")
    lm = backend.open_camera("cam0")
    assert lm.info.user_defined_name == "LowMag" and set(backend.devices) == {"HighMag", "cam0"}
    assert harvester.updates == 1 and not harvester.cameras[0].acquirer.destroyed   # update() would destroy hm
    assert harvester.cameras[0].nodes["PixelFormat"].value == "Mono8"                # written at open
    assert hm.bus_type() == "Ethernet" and hm.serial_number() == serial_number_words("4103000001") == (0, 4103000001)
    assert serial_number_words("00A1B2C3D4E5") == (0xA1, 0xB2C3D4E5)
    with pytest.raises(CameraError, match="already open as 'HighMag'"):
        backend.open_camera("4103000001")
    with pytest.raises(CameraError, match="not refreshed"):
        backend.open_camera("Other")
    hm.close()
    assert backend.open_camera("HighMag") is not hm and harvester.updates == 1
    backend.close()
    with pytest.raises(CameraError, match="released"):
        backend.open_camera("HighMag")
    assert harvester.resets == 1 and backend.devices == {}


def test_a_camera_in_use_does_not_open(stack):
    cameras = two_cameras()
    cameras[0].in_use = True
    backend, _ = stack(cameras)
    with pytest.raises(CameraError) as err:
        backend.open_camera("HighMag")
    assert err.value.code == Code.CAMERA_SDK
    assert err.value.source.startswith("IMAQdx Open Camera") and "AccessDeniedException" in err.value.source
    assert backend.devices == {}


# ================================================================================================== attributes
def test_feature_names():
    assert genicam_feature_names(SIM_EXPOSURE) == ("ExposureTime", "ExposureTimeAbs")
    assert genicam_feature_names(SIM_EXPOSURE_TIME) == ("ExposureTime", "ExposureTimeAbs")
    assert genicam_feature_names(SIM_GAIN) == genicam_feature_names(SIM_GAIN_ANALOG) == ("Gain",)
    assert genicam_feature_names(SIM_FRAME_RATE) == ("AcquisitionFrameRate", "AcquisitionFrameRateAbs")
    assert genicam_feature_names("PacketSize") == ("GevSCPSPacketSize",)
    assert genicam_feature_names(LINE_SELECTOR) == ("LineSelector",)
    assert genicam_feature_names(SIM_GAIN, {SIM_GAIN: ("GainRaw", "Gain")}) == ("GainRaw", "Gain")
    with pytest.raises(CameraError, match="aliases"):
        genicam_feature_names("CameraAttributes::Shutter::Value")


def test_alias_fallback_and_custom_alias(stack):
    cameras = two_cameras()
    nodes = cameras[0].nodes
    nodes["ExposureTimeAbs"] = nodes.pop("ExposureTime")              # a pre-SFNC 2.0 camera
    backend, _ = stack(cameras, aliases={"CameraAttributes::Custom::Level": "Gain"})
    device = backend.open_camera("HighMag")
    assert device.feature_name(SIM_EXPOSURE) == "ExposureTimeAbs"
    device.write_attribute("CameraAttributes::Custom::Level", DBL, 2.5)
    assert nodes["Gain"].value == 2.5
    with pytest.raises(CameraError, match="unknown attribute.*Bar"):
        device.read_attribute("CameraAttributes::Foo::Bar", DBL)


def test_numeric_attributes(stack):
    backend, harvester = stack()
    device = backend.open_camera("HighMag")
    nodes = harvester.cameras[0].nodes
    assert device.read_attribute(SIM_EXPOSURE_TIME, DBL) == 10000.0
    device.write_attribute(SIM_EXPOSURE, DBL, 2500.5)
    assert nodes["ExposureTime"].value == 2500.5
    assert device.read_attribute(SIM_EXPOSURE, I64) == 2500 and device.read_attribute(SIM_EXPOSURE, U32) == 2500
    device.write_attribute(SIM_GAIN, I64, 3)                           # integer into a float feature
    assert nodes["Gain"].value == 3.0 and isinstance(nodes["Gain"].value, float)
    assert device.read_attribute(SIM_GAIN_ANALOG, DBL) == 3.0
    nodes["Gain"]._value = -2.4
    assert device.read_attribute(SIM_GAIN, I64) == -2 and device.read_attribute(SIM_GAIN, U32) == 0
    device.write_attribute(WIDTH, DBL, 1000.5)                         # DBL into an integer feature: half to even
    assert nodes["Width"].value == 1000 and device.read_attribute(WIDTH, DBL) == 1000.0
    assert device.read_attribute_max(SIM_FRAME_RATE) == 60.0 and device.read_attribute_max(WIDTH) == 2448.0
    assert device.read_attribute(DEVICE_TEMPERATURE, DBL) == 41.5
    for vtype, value, text in ((DBL, 5e6, "out of range"), (DBL, float("nan"), "invalid value"),
                               (BOOL, True, "ValueBOOL does not match the float feature ExposureTime"),
                               (DBL, True, "does not match"), (STRING, "1", "does not match")):
        with pytest.raises(CameraError, match=text):
            device.write_attribute(SIM_EXPOSURE, vtype, value)
    with pytest.raises(CameraError, match="does not match"):
        device.read_attribute(SIM_EXPOSURE, STRING)


def test_enumerations_strings_and_selectors(stack):
    backend, harvester = stack()
    device = backend.open_camera("HighMag")
    nodes = harvester.cameras[0].nodes
    assert device.read_attribute(TRIGGER_MODE, STRING) == "Off"
    device.write_attribute(TRIGGER_MODE, STRING, "On")
    assert nodes["TriggerMode"].value == "On" and device.read_attribute(TRIGGER_MODE, U32) == 1
    assert device.read_attribute(TRIGGER_MODE, DBL) == 1.0
    device.write_attribute(TRIGGER_MODE, U32, 0)                       # by integer value, as IMAQdx
    assert nodes["TriggerMode"].value == "Off"
    with pytest.raises(CameraError, match="one of Off, On"):
        device.write_attribute(TRIGGER_MODE, STRING, "Maybe")
    with pytest.raises(CameraError, match="no entry with integer value 5 .entries Off, On"):
        device.write_attribute(TRIGGER_MODE, I64, 5)
    with pytest.raises(CameraError, match="does not match"):
        device.write_attribute(TRIGGER_MODE, BOOL, True)
    assert device.read_attribute(USER_ID, STRING) == "HighMag"
    device.write_attribute(USER_ID, STRING, "HM")
    assert nodes["DeviceUserID"].value == "HM"
    with pytest.raises(CameraError, match="does not match"):
        device.write_attribute(USER_ID, DBL, 1.0)
    # selectors are features of the camera: LineInverter follows LineSelector
    device.write_attribute(LINE_SELECTOR, U32, 1)
    device.write_attribute(LINE_INVERTER, BOOL, True)
    device.write_attribute(LINE_SELECTOR, U32, 2)
    assert device.read_attribute(LINE_INVERTER, BOOL) is False
    device.write_attribute(LINE_SELECTOR, U32, 1)
    assert device.read_attribute(LINE_INVERTER, BOOL) is True and nodes["LineInverter"].per == {1: True}
    device.write_attribute(LINE_SOURCE, U32, 1)
    assert nodes["LineSource"].per[1] == "UserOutput0"
    device.write_attribute(USER_OUTPUT_SELECTOR, U32, 1)
    device.write_attribute(USER_OUTPUT_VALUE, BOOL, True)
    assert nodes["UserOutputValue"].per == {1: True}
    assert device.read_attribute(USER_OUTPUT_SELECTOR, STRING) == "UserOutput1"
    with pytest.raises(CameraError, match="no maximum"):
        device.read_attribute_max(TRIGGER_MODE)


def test_enum_values_translate_integers(stack):
    backend, harvester = stack(enum_values={"LineSource": {1: "UserOutput1"}})
    device = backend.open_camera("HighMag")
    device.write_attribute(LINE_SELECTOR, U32, 1)
    device.write_attribute(LINE_SOURCE, U32, 1)
    assert harvester.cameras[0].nodes["LineSource"].per[1] == "UserOutput1"
    assert device.read_attribute(LINE_SOURCE, U32) == 1
    device.write_attribute(LINE_SOURCE, U32, 0)                        # not in the table: the camera's integer
    assert device.read_attribute(LINE_SOURCE, STRING) == "Off"


def test_commands(stack):
    backend, harvester = stack()
    device = backend.open_camera("HighMag")
    node = harvester.cameras[0].nodes["TriggerSoftware"]
    device.write_attribute(TRIGGER_SOFTWARE, BOOL, True)
    device.write_attribute(TRIGGER_SOFTWARE, BOOL, False)
    assert node.executed == 1
    with pytest.raises(CameraError, match="does not match"):
        device.write_attribute(TRIGGER_SOFTWARE, I64, 1)
    with pytest.raises(CameraError, match="not readable"):
        device.read_attribute(TRIGGER_SOFTWARE, BOOL)
    node.fail = FakeGenApiError("AccessException: trigger not available")
    with pytest.raises(CameraError, match=r"TriggerSoftware -> TriggerSoftware\): FakeGenApiError: AccessExc"):
        device.write_attribute(TRIGGER_SOFTWARE, BOOL, True)


def test_driver_errors_become_camera_errors(stack):
    backend, harvester = stack()
    device = backend.open_camera("HighMag")
    nodes = harvester.cameras[0].nodes
    with pytest.raises(CameraError) as err:
        device.write_attribute(DEVICE_TEMPERATURE, DBL, 20.0)
    assert err.value.code == Code.CAMERA_SDK and "AccessException" in err.value.source
    nodes["ExposureTime"].fail = FakeGenApiError("TimeoutException: GVCP read timeout")
    with pytest.raises(CameraError) as err:
        device.read_attribute(SIM_EXPOSURE, DBL)
    assert err.value.source == ("IMAQdx Property Node (CameraAttributes::Exposure::Value -> ExposureTime): "
                                "FakeGenApiError: TimeoutException: GVCP read timeout")


def test_feature_adapter_duck_typing():
    kinds = {"float": SimpleNamespace(value=1.5), "int": SimpleNamespace(value=3), "bool": SimpleNamespace(value=True),
             "string": SimpleNamespace(value="x"), "enum": SimpleNamespace(value="A", symbolics=["A"]),
             "command": SimpleNamespace(execute=lambda: None), "other": SimpleNamespace(value=None)}
    for kind, node in kinds.items():
        assert GenICamFeature("X", node).kind == kind
    node_map = SimpleNamespace(Gain=SimpleNamespace(value=1))                  # no get_node: attribute access
    acquirer = HarvestersAcquirer(SimpleNamespace(remote_device=SimpleNamespace(node_map=node_map)))
    assert acquirer.feature("Gain").kind == "int" and acquirer.feature("Missing") is None


def test_packet_size(stack):
    backend, harvester = stack()
    device = backend.open_camera("HighMag")
    node = harvester.cameras[0].nodes["GevSCPSPacketSize"]
    device.set_packet_size(8996)
    assert node.value == 8996 and device.packet_size == 8996
    with pytest.raises(CameraError, match="PacketSize: value 20000 out of range"):
        device.set_packet_size(20000)
    for override, expected in ((1500, 1500), (0, 1440)):
        backend, harvester = stack(packet_size=override)
        backend.open_camera("HighMag").set_packet_size(8996)
        assert harvester.cameras[0].nodes["GevSCPSPacketSize"].value == expected
    cameras = two_cameras()
    del cameras[0].nodes["GevSCPSPacketSize"]
    backend, _ = stack(cameras)
    with pytest.raises(CameraError, match="unknown attribute.*GevSCPSPacketSize"):
        backend.open_camera("HighMag").set_packet_size(8996)


# ================================================================================================== acquisition
def test_ring_numbering_and_buffer_number_mode(stack):
    backend, harvester = stack()
    device = backend.open_camera("HighMag")
    with pytest.raises(CameraError, match="not configured"):
        device.start()
    device.configure(True, 3)
    acquirer = harvester.cameras[0].acquirer
    assert acquirer.num_buffers == 3 and device.buffer_count() == 3 and device.last_buffer_number() is None
    with pytest.raises(CameraError, match="acquisition not in progress"):
        device.get_last_new(10)
    device.start()
    assert device.acquisition_in_progress() is True and acquirer.starts == 1
    with pytest.raises(CameraError, match="acquisition in progress"):
        device.configure(True, 3)
    for _ in range(5):
        acquirer.emit()
    wait_until(lambda: device.last_buffer_number() == 4)
    frame = device.get_buffer(4, 100)
    assert frame.number == 4 and frame.pixels.shape == (6, 8) and frame.pixels.dtype == np.uint8
    assert int(frame.pixels[0, 0]) == 4
    assert device.get_buffer(1, 100).number == 4                       # overwritten → newest
    assert device.get_buffer(None, 100).number == 4
    assert device.get_buffer(2, 100).number == 2                       # still in the ring
    timer = threading.Timer(0.02, acquirer.emit)
    timer.start()
    assert device.get_buffer(5, 2000).number == 5                      # waits for a future buffer
    timer.join()
    with pytest.raises(CameraError) as err:
        device.get_buffer(9, 20)
    assert err.value.code == Code.CAMERA_SDK and err.value.source == "IMAQdx Get Image2: timeout after 20 ms"
    assert acquirer.outstanding == 0 and acquirer.requeued == 6        # every buffer went back to the producer


def test_next_and_last_new(stack):
    device, acquirer, _ = started(stack, 5)
    acquirer.emit()
    acquirer.emit()
    wait_until(lambda: device.last_buffer_number() == 1)
    assert device.get_next(100).number == 0 and device.get_next(100).number == 1
    with pytest.raises(CameraError, match="timeout"):
        device.get_next(20)
    with pytest.raises(CameraError, match="timeout"):
        device.get_last_new(20)                                        # newest already returned
    for _ in range(3):
        acquirer.emit()
    wait_until(lambda: device.last_buffer_number() == 4)
    assert device.get_last_new(100).number == 4
    acquirer.emit()
    assert device.get_next(1000).number == 5


def test_one_shot_sequence(stack):
    device, acquirer, _ = started(stack, 2, continuous=False)
    for _ in range(4):
        acquirer.emit()
    wait_until(lambda: acquirer.requeued == 4)
    assert device.frames_acquired == 2 and device.last_buffer_number() == 1
    assert device.acquisition_in_progress() is True
    device.stop()
    device.start()                                                     # a new sequence, numbering continues
    acquirer.emit()
    wait_until(lambda: device.last_buffer_number() == 2)
    assert device.get_last_new(100).number == 2 and device.frames_acquired == 3


def test_pixel_formats():
    mono8 = mono_pixels(RawImage(3, 2, "Mono8", np.arange(6, dtype=np.uint8)))
    assert mono8.dtype == np.uint8 and mono8.tolist() == [[0, 1, 2], [3, 4, 5]]
    mono12 = mono_pixels(RawImage(2, 1, "Mono12", np.array([4095, 7], np.uint16)))
    assert mono12.dtype == np.uint16 and mono12.tolist() == [[4095, 7]]
    raw16 = mono_pixels(RawImage(2, 1, "Mono16", np.array([1, 0, 0, 1], np.uint8)))      # little-endian bytes
    assert raw16.dtype == np.uint16 and raw16.tolist() == [[1, 256]]
    padded = mono_pixels(RawImage(3, 2, "Mono8", np.array([1, 2, 3, 0, 4, 5, 6, 0], np.uint8)))
    assert padded.tolist() == [[1, 2, 3], [4, 5, 6]] and padded.flags.c_contiguous
    for image, text in ((RawImage(2, 1, "Mono10p", np.zeros(3, np.uint8)), "packed"),
                        (RawImage(4, 4, "Mono8", np.zeros(8, np.uint8)), "does not hold"),
                        (RawImage(2, 2, "BayerRG8", np.zeros(4, np.uint8)), "not supported")):
        with pytest.raises(CameraError, match=text):
            mono_pixels(image)


def test_grab_thread_survives_bad_frames_and_driver_errors(stack):
    device, acquirer, _ = started(stack)
    acquirer.emit(fmt="RGB8")
    wait_until(lambda: acquirer.requeued == 1)
    with pytest.raises(CameraError, match=r"timeout after 10 ms \(last driver error: pixel format 'RGB8'"):
        device.get_last_new(10)
    acquirer.fetch_errors.append(FakeGenTLError("IoException: stream channel lost"))
    acquirer.emit(np.full(48, 1000, np.uint16), fmt="Mono12")
    frame = device.get_last_new(1000)
    assert frame.number == 0 and frame.pixels.dtype == np.uint16 and int(frame.pixels[5, 7]) == 1000
    assert "IoException" in device.last_grab_error


def test_stop_unconfigure_and_close(stack):
    device, acquirer, _ = started(stack)
    acquirer.emit()
    assert device.get_last_new(1000).number == 0
    timer = threading.Timer(0.02, device.stop)
    timer.start()
    with pytest.raises(CameraError, match="acquisition stopped while waiting"):
        device.get_last_new(2000)
    timer.join()
    assert acquirer.stops == 1 and not acquirer.stopped_with_outstanding and acquirer.outstanding == 0
    assert not any(t.name == "alox-genicam-HighMag" for t in threading.enumerate())
    assert device.acquisition_in_progress() is False
    with pytest.raises(CameraError, match="not in progress"):
        device.get_next(10)
    device.stop()                                                      # not acquiring: nothing to do
    assert acquirer.stops == 1
    device.start()
    device.unconfigure()
    assert acquirer.stops == 2 and not device.configured and device.buffer_count() == 0
    with pytest.raises(CameraError, match="not configured"):
        device.start()
    device.close()
    device.close()                                                     # idempotent
    assert acquirer.destroyed and device.closed
    for call in (device.bus_type, device.acquisition_in_progress, device.start,
                 lambda: device.read_attribute(SIM_EXPOSURE, DBL), lambda: device.get_next(10)):
        with pytest.raises(CameraError) as err:
            call()
        assert err.value.code == Code.IMAQDX_INVALID_SESSION and "invalid camera session" in err.value.source


def test_start_and_stop_failures(stack):
    backend, harvester = stack()
    device = backend.open_camera("HighMag")
    device.configure(True, 5)
    acquirer = harvester.cameras[0].acquirer
    acquirer.fail_start = FakeGenTLError("ResourceInUseException: stream busy")
    with pytest.raises(CameraError) as err:
        device.start()
    assert err.value.code == Code.CAMERA_SDK and err.value.source.startswith("IMAQdx Start Acquisition: ")
    assert device.acquisition_in_progress() is False
    acquirer.fail_start = None
    device.start()
    acquirer.fail_stop = FakeGenTLError("IoException: AcquisitionStop not acknowledged")
    with pytest.raises(CameraError, match="IMAQdx Stop Acquisition: FakeGenTLError: IoException"):
        device.stop()
    assert device.acquisition_in_progress() is True                  # the acquirer still runs: stop again
    device.stop()
    assert device.acquisition_in_progress() is False
    with pytest.raises(CameraError, match="invalid number of buffers"):
        device.configure(True, 0)


# ================================================================================================== Camera device
def test_camera_device_on_the_genicam_backend(stack):
    cameras = [FakeCamera("HighMag", "4103000001", free_run_period=0.002)]
    backend, _ = stack(cameras)
    camera = Camera(backend, CameraSettings.default_high_mag(), name="hm",
                    lights=LightSettings(led_status_inverter=True), sleep=lambda _s: None)
    nodes = cameras[0].nodes
    try:
        camera.open(timeout=10)
        assert camera.state == DeviceState.READY
        acquirer = cameras[0].acquirer
        assert acquirer.num_buffers == 5 and acquirer.is_acquiring()
        assert nodes["GevSCPSPacketSize"].value == 8996 and nodes["TriggerMode"].value == "Off"
        assert nodes["PixelFormat"].value == "Mono8"
        first, second = camera.grab(), camera.grab()
        assert first.pixels.shape == (6, 8) and first.pixels.dtype == np.uint8 and second.number > first.number
        assert camera.set_exposure(20_000.0) == 20_000.0
        assert nodes["ExposureTime"].value == 20_000.0
        assert nodes["AcquisitionFrameRate"].value == pytest.approx(47.5)       # 0.95 / 20 ms, ≤ the feature max
        camera.set_gain(3)
        assert camera.gain() == 3.0
        camera.set_led(True)                                                    # GigE: Line1 / UserOutput0
        assert nodes["LineSource"].per == {1: "UserOutput0"} and nodes["UserOutputValue"].per == {0: True}
        assert camera.led() is True
        camera.set_led(False)
        assert camera.led() is False and nodes["UserOutputValue"].per == {0: False}
        status = camera.status()
        assert status.acquiring is True and status.temperature_c == 41.5 and status.bus_type == "Ethernet"
        assert camera.serial_number == f"{0:08X}{4103000001:08X}"
    finally:
        camera.shutdown(timeout=5)
    assert acquirer.destroyed and not acquirer.stopped_with_outstanding
