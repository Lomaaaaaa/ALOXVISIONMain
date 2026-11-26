"""The REST API and the websocket events (``alox.api``), on a fake backend."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("websockets")

from fastapi.testclient import TestClient
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from alox.api import reports
from alox.api.backend import Measure, VersionInfo
from alox.api.rest import OVERLAYS, create_app
from alox.api.websocket import WebsocketServer, encode
from alox.core.commands import Method
from alox.core.functions import JobInfo
from alox.events import EventBus

TEXTS = {9000: "no error", 9001: "unknown error", 9002: "invalid arguments error",
         9005: "function not ready to start, or already running", 9013: "get image error (not ready condition)"}


class FakeBackend:
    def __init__(self) -> None:
        self.posted: list[tuple[Method, Any]] = []
        self.props: dict[str, Any] = {"general.mode": 0, "heartbeat.frequency": 1000,
                                      "general.watchdogmaxfails": 3, "general.watchdogui": False}
        self.ready = True
        self.refused: list[Measure] = []
        self.overlay_state: dict[str, bool] = {}
        self.requests: list[tuple[Measure, JobInfo, dict[str, Any]]] = []
        self.profiles_dir = "D:/alox/profiles"

    def system_status(self) -> tuple[int, str]:
        return 500, "idle"

    def measure_status(self, measure: Measure) -> tuple[int, str]:
        return 0, f"{measure.name.lower()} initial"

    def heartbeat(self) -> int:
        return 1234567

    def get_property(self, dotted: str, default: Any = None) -> Any:
        return self.props.get(dotted, default)

    def set_property(self, dotted: str, value: Any) -> None:
        self.props[dotted] = value

    def version(self) -> VersionInfo:
        return VersionInfo(1, 2, 6, 2, "25/11/2026 - 10:00")

    def text(self, code: int) -> str:
        return TEXTS[code]

    def hostname(self) -> str:
        return "alox-pc"

    def profiles(self) -> list[str]:
        return ["DEFAULT", "R1"]

    def current_profile(self) -> tuple[str, str]:
        return "D:/alox/profiles", "DEFAULT"

    def profiles_folder(self) -> str:
        return self.profiles_dir

    def set_profiles_folder(self, path: str) -> None:
        self.profiles_dir = path

    def images_folder(self) -> str:
        return "D:/alox/images"

    def post(self, method: Method, payload: Any = None) -> None:
        self.posted.append((method, payload))

    def can_start(self, measure: Measure | None) -> bool:
        return self.ready

    def not_ready(self, measure: Measure) -> None:
        self.refused.append(measure)

    def measure_request(self, measure: Measure, job: JobInfo, overrides: Mapping[str, Any]) -> Any:
        self.requests.append((measure, job, dict(overrides)))
        return {"measure": measure.name, "job": job, **overrides}

    def tilt(self) -> float:
        return 0.125

    def reset_tilt(self) -> None:
        self.props["tilt.reset"] = True

    def camera_status(self, camera: str) -> Mapping[str, str]:
        return {"Type": camera, "Status": "Running", "ImageTotal": "12", "Timestamp": "10:00:00:000",
                "PixelSize": "0.5", "PixelUnits": "um", "ExposureTime": "10000"}

    def overlays(self) -> list[str]:
        return list(OVERLAYS)

    def overlay(self, name: str) -> bool:
        return self.overlay_state.get(name, False)

    def set_overlay(self, name: str, on: bool) -> None:
        self.overlay_state[name] = on

    def jpeg(self, quality: int, width: int, height: int) -> bytes:
        return f"JPEG {quality} {width}x{height}".encode()

    def stream_start(self, quality: int, width: int, height: int) -> None:
        self.props["stream"] = (quality, width, height)

    def stream_stop(self) -> None:
        self.props["stream"] = None


@pytest.fixture
def api() -> tuple[TestClient, FakeBackend]:
    backend = FakeBackend()
    return TestClient(create_app(backend, websocket_port=6123, log_requests=False)), backend


# ============================================================================================== system
def test_status_routes(api):
    client, _ = api
    assert client.get("/alox/v1/system/status").json() == {"status": 500, "description": "idle"}
    assert client.get("/alox/v1/system/bowingstatus").json() == {"status": 0, "description": "bowing initial"}
    assert client.get("/alox/v1/system/heartbeat").json() == {"Heartbeat": "1234567"}
    assert client.get("/alox/v1/system/heartbeatfreq").json() == {"Frequency": "1000"}
    assert client.get("/alox/v1/system/heartbeatretries").json() == {"Retries": "3"}
    assert client.get("/alox/v1/watchdog/visibility").json() == {"visible": False}


def test_version_only_reads(api):
    client, backend = api
    assert client.get("/alox/v1/system/version").json() == {"major": 1, "minor": 2, "build": 6, "api": 2,
                                                            "release": "25/11/2026 - 10:00"}
    assert backend.posted == []                              # LabVIEW also queued an Abort here (fixed)


def test_mode(api):
    client, backend = api
    assert client.get("/alox/v1/system/mode").json() == {"mode": "local"}
    response = client.put("/alox/v1/system/mode?type=remote")
    assert response.status_code == 200 and response.text == "no error"
    assert backend.props["general.mode"] == 1 and backend.posted == [(Method.REFRESH_CORE_INFOS, None)]
    bad = client.put("/alox/v1/system/mode?type=auto")
    assert bad.status_code == 500 and bad.text == "invalid arguments error"


def test_profiles(api):
    client, backend = api
    assert client.get("/alox/v1/system/profiles").json() == {"profiles": ["DEFAULT", "R1"]}
    assert client.get("/alox/v1/system/profile").json() == {"path": "D:/alox/profiles", "filename": "DEFAULT"}
    assert client.put("/alox/v1/system/profile", data={"filename": "R1"}).status_code == 200   # form body
    assert backend.posted == [(Method.LOAD_PROFILE, "R1")]
    assert client.put("/alox/v1/system/profile?filename=NOPE").status_code == 500


def test_configuration_and_folders(api):
    client, backend = api
    assert client.get("/alox/v1/system/configuration").json() == {"path": "D:/alox/profiles"}
    assert client.put("/alox/v1/system/configuration?path=E:/recipes").status_code == 200
    assert backend.profiles_dir == "E:/recipes" and backend.posted == [(Method.PROFILES_FOLDER_CHANGED, None)]
    backend.profiles_dir = ""
    assert client.get("/alox/v1/system/configuration").status_code == 500
    assert client.get("/alox/alox_api_v1_get_system_imagesfolder/").json() == {"path": "D:/alox/images"}


def test_events_tilt_and_system_commands(api):
    client, backend = api
    events = client.get("/alox/v1/system/events").json()
    assert events["url"] == "ws://alox-pc:6123" and "measure_mesa" in events["events"]
    assert client.get("/alox/v1/system/tilt").json() == {"Tilt": "0.125000", "Units": "degrees"}
    client.put("/alox/v1/system/tiltreset")
    client.put("/alox/v1/system/reset")
    client.put("/alox/v1/system/shutdown")
    client.put("/alox/v1/system/showui?visible=on")
    client.put("/alox/v1/system/showui?visible=no")
    assert backend.posted == [(Method.SYSTEM_RESET, None), (Method.SYSTEM_SHUTDOWN, None), (Method.SHOW_UI, True),
                              (Method.SHOW_UI, False)]
    assert backend.props["tilt.reset"] is True


# ============================================================================================== functions
def test_measure_mesa_queues_the_request(api):
    client, backend = api
    response = client.put("/alox/v1/functions/measuremesa?ctrljobid=CJ1&procjobid=PJ2&lotid=L3&waferid=W4&zoneid=0"
                          "&x=12.5&y=-3&target=8.25&tilt=on&singleshot=off&profile=R1")
    assert response.status_code == 200 and response.text == "no error"
    (measure, job, overrides), = backend.requests
    assert measure == Measure.MESA
    assert job == JobInfo("CJ1", "PJ2", "L3", "W4", "0")
    assert overrides == {"x": 12.5, "y": -3.0, "target": 8.25, "tilt": True, "singleshot": False}
    assert [m for m, _ in backend.posted] == [Method.LOAD_PROFILE, Method.MEASURE_MESA]


def test_measure_refused_when_a_function_runs(api):
    client, backend = api
    backend.ready = False
    response = client.put("/alox/v1/functions/measuretilt?x=1")
    assert response.status_code == 503 and response.text == "function not ready to start, or already running"
    assert backend.refused == [Measure.TILT] and backend.posted == []


def test_measure_with_invalid_number_or_profile(api):
    client, backend = api
    assert client.put("/alox/v1/functions/measurebowing?x=12,5").status_code == 500    # '.' decimals only
    assert client.put("/alox/v1/functions/measurebowing?x=nan").status_code == 500
    assert client.put("/alox/v1/functions/measuremesa?profile=NOPE").status_code == 500
    assert backend.posted == []


def test_stop_and_abort(api):
    client, backend = api
    assert client.put("/alox/v1/functions/stop").status_code == 200
    assert client.get("/alox/v1/functions/abort").status_code == 200          # abort stays a GET
    assert backend.posted == [(Method.STOP, None), (Method.ABORT, None)]


# ============================================================================================== cameras
def test_camera_routes(api):
    client, backend = api
    assert client.get("/alox/v1/camera/highmagstatus").json()["Type"] == "highmag"
    assert client.put("/alox/v1/camera/grablowmag").status_code == 200
    assert backend.posted == [(Method.GET_IMAGE, "Low Mag.")]
    backend.ready = False
    refused = client.put("/alox/v1/camera/grabhighmag")
    assert refused.status_code == 503 and refused.text == "get image error (not ready condition)"


def test_images(api):
    client, backend = api
    jpeg = client.get("/alox/v1/camera/jpegimage?quality=900&width=320&height=240")
    assert jpeg.headers["content-type"] == "image/jpeg" and jpeg.content == b"JPEG 900 320x240"
    assert client.get("/alox/v1/camera/jpegimage").content == b"JPEG 750 612x512"          # defaults
    live = client.get("/alox/v1/camera/liveimage")
    assert live.headers["content-type"].startswith("text/html") and "jpegimage?quality=750&width=640" in live.text
    client.put("/alox/v1/camera/streamstart?quality=500")
    assert backend.props["stream"] == (500, 612, 512)


def test_overlays(api):
    client, _ = api
    assert client.get("/alox/v1/camera/overlays").json()["overlays"][:2] == ["highmagcamera", "lowmagcamera"]
    assert client.put("/alox/v1/camera/overlay/mesa?state=on").status_code == 200
    assert client.get("/alox/v1/camera/overlay/mesa").json() == {"mesa": "on"}
    assert client.put("/alox/v1/camera/overlay/mesa?state=maybe").status_code == 500
    assert client.get("/alox/v1/camera/overlay/unknown").status_code == 404


def test_backend_failure_is_a_500(api):
    client, backend = api

    def broken() -> tuple[int, str]:
        raise RuntimeError("status manager down")

    backend.system_status = broken
    response = client.get("/alox/v1/system/status")
    assert response.status_code == 500 and response.text == "unknown error"


# ============================================================================================== websocket payloads
def test_payloads_keep_labview_keys():
    job = JobInfo("CJ", "PJ", "L", "W", "Z")
    assert json.loads(encode(reports.status_event(1033))) == {"event": "status", "State": "500"}
    assert json.loads(encode(reports.status_event(203)))["State"] == "203"
    finished = json.loads(encode(reports.function_event("finished", "measure_mesa", job)))
    assert list(finished) == ["event", "Function", "Job"]
    assert finished["Job"] == {"Control Job ID": "CJ", "Process Job ID": "PJ", "Lot ID": "L", "Wafer ID": "W",
                               "Zone ID": "Z"}
    tilt = json.loads(encode(reports.tilt_event(job, 0.5)))
    assert tilt["Tilt"] == {"Angle": "0.500000", "Unit": "degrees"} and tilt["Job"]["Control_Job_ID"] == "CJ"
    bowing = json.loads(encode(reports.bowing_event(job, zwafer=[1, 2, 3, 4, 5], bowing_a=10, bowing_b=11,
                                                    zchuck=[1, 3, 4, 5], parallelism_a=1, parallelism_b=2)))
    assert bowing["Bowing"]["bowinga"] == "10.000000" and bowing["Bowing"]["bowingb"] == "11.000000"
    assert bowing["Parallelism"]["parallelisma"] == "1.000000"
    image = json.loads(encode(reports.image_event(b"\xff\xd8", 612, 512, 750)))
    assert image == {"event": "image", "width": 612, "height": 512, "quality": 750, "jpeg": "/9g="}


# ============================================================================================== websocket server
def test_websocket_server_pushes_events_to_one_client():
    bus = EventBus()
    server = WebsocketServer(bus, host="127.0.0.1", port=0)
    server.start()
    try:
        with connect(f"ws://127.0.0.1:{server.port}/anything") as ws:
            deadline = time.monotonic() + 2
            while server.clients == 0 and time.monotonic() < deadline:
                time.sleep(0.01)
            bus.publish(reports.heartbeat_event(42))
            bus.publish(reports.status_event(500))
            assert json.loads(ws.recv(timeout=2)) == {"event": "heartbeat", "timestamp": "42"}
            assert json.loads(ws.recv(timeout=2)) == {"event": "status", "State": "500"}
            with connect(f"ws://127.0.0.1:{server.port}") as second:                 # one client at a time
                with pytest.raises(ConnectionClosed) as info:
                    second.recv(timeout=2)
                assert info.value.rcvd is not None and info.value.rcvd.code == 1013
    finally:
        server.stop()


def test_numbers_are_formatted_as_labview_including_nan():
    """LabVIEW ``%f`` writes NaN and infinities as ``NaN`` / ``Inf`` / ``-Inf`` (an unmeasured diameter, no rate)."""
    event = reports.tilt_event(None, float("nan"))
    assert event.data["Tilt"]["Angle"] == "NaN"
    assert reports.tilt_event(None, float("-inf")).data["Tilt"]["Angle"] == "-Inf"
    assert reports.tilt_event(None, 1.5).data["Tilt"]["Angle"] == "1.500000"
