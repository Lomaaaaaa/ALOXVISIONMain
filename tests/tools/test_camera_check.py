"""``tools/camera_check.py``: the information mode (read-only but for the selectors, which are put back), the timed
grabs, the report, and the guided actions with their typed ``yes``, on the fake Harvesters stack
(:mod:`alox.devices.fake_harvesters`). One subprocess test runs ``python tools/camera_check.py`` as the owner will,
without PYTHONPATH.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from camera_check import (
    EXIT_OK,
    EXIT_PROBLEM,
    EXIT_USAGE,
    Environment,
    main,
    report_paths,
)

from alox.devices.fake_harvesters import FakeHarvester, fake_cameras, gradient_image
from aloxcam.imaging.png import read_png

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "tools" / "camera_check.py"
GIGE_IDS_INI = REPO / "tests" / "config" / "data" / "real" / "alox_GiGeIDS.ini"      # High Mag name_STR = "cam2"


def small_harvester(*, incomplete: bool = False) -> FakeHarvester:
    """The two fake cameras with 64 x 48 frames every 2 ms."""
    cameras = fake_cameras(free_run_period=0.002, image=gradient_image)
    for camera in cameras:
        assert camera.nodes is not None
        camera.nodes["Width"]._value = 64
        camera.nodes["Height"]._value = 48
    cameras[0].incomplete = incomplete
    return FakeHarvester(cameras)


def run_tool(out: Path, *argv: str, harvester: FakeHarvester | None = None,
             answers: tuple[Any, ...] = ()) -> tuple[int, dict[str, Any], str, FakeHarvester]:
    queue = list(answers)

    def read_line() -> str:
        if not queue:
            raise EOFError
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item
    harvester = harvester or small_harvester()
    output = io.StringIO()
    code = main([*argv, "--out", str(out)], env=Environment(read_line=read_line, output=output,
                                                             fake_harvester=harvester))
    reports = sorted(out.glob("camera_report_*.json"))
    data = json.loads(reports[-1].read_text(encoding="utf-8")) if reports else {}
    return code, data, output.getvalue(), harvester


def nodes(harvester: FakeHarvester, index: int = 0) -> dict[str, Any]:
    result = harvester.cameras[index].nodes
    assert result is not None
    return result


def topics(data: dict[str, Any], camera: str) -> dict[str, str]:
    return {f["topic"]: f["answer"] for f in data["findings"] if f["camera"] == camera}


# ================================================================================================== information
def test_information_rehearsal_is_read_only_and_reports(tmp_path):
    code, data, text, harvester = run_tool(tmp_path, "--fake", "--frames", "3")
    assert code == EXIT_OK and "PLEASE SEND BACK" in text
    assert [c["serial_number"] for c in data["cameras_seen"]] == ["4103000001", "4103000002"]
    assert data["cameras_seen"][0]["vendor"].startswith("IDS") and data["cameras_seen"][0]["user_defined_name"]
    hm, lm = data["checks"]
    assert (hm["name"], lm["name"]) == ("HighMag", "LowMag") and hm["network"]["ip"] == "192.168.10.11"
    features = {f["feature"]: f for f in hm["features"]}
    assert features["ExposureTime"]["min"] == 28.0 and features["ExposureTime"]["unit"] == "us"
    assert {"name": "UserOutput0", "value": 1} in features["LineSource"]["entries"]
    assert not features["ExposureTimeAbs"]["present"] and features["TriggerSoftware"]["access"] == "WO"
    lines = {row["entry"]: row for row in hm["selectors"]["LineSelector"]["entries"]}
    assert lines["Line1"]["LineMode"] == "Output" and hm["selectors"]["LineSelector"]["restored"] == "Line0"
    app = {row["what"]: row for row in hm["application"]}
    assert app["exposure (exposure_STR)"]["feature"] == "ExposureTime" and app["temperature"]["value"] == 41.5
    assert app["frame rate max (MaxDBL)"]["value"] == 60.0
    grab = hm["grab"]
    assert grab["frames_received"] == 3 and grab["timeouts"] == 0 and grab["rate_fps"] > 0
    assert grab["image"]["dtype"] == "uint16" and grab["image"]["max"] > grab["image"]["min"]
    png = read_png(hm["png"])
    assert png.shape == (48, 64) and str(png.dtype) == "uint16"
    found = topics(data, "HighMag")
    assert "'UserOutput0' on this camera (LabVIEW: UserOutput0)" in found["LineSource 1"]
    assert "use DBL" in found["Gain type"] and "Mono12" in found["Pixel format"]
    assert "8996: allowed" in found["Packet size"] and "Output on: Line1" in found["Output lines"]
    assert any("64 x 48 is not the full IMX264 sensor" in p for p in data["problems"])
    # read-only: nothing kept by the camera changed, the selectors are back
    n = nodes(harvester)
    assert n["PixelFormat"].value == "Mono12" and n["GevSCPSPacketSize"].value == 1440
    assert n["ExposureTime"].value == 10000.0 and n["TriggerMode"].value == "Off"
    assert n["LineSelector"].value == "Line0" and n["UserOutputSelector"].value == "UserOutput0"
    assert n["DeviceTemperatureSelector"].value == "Mainboard" and n["GainSelector"].value == "AnalogAll"
    assert n["LineSource"].per == {} and n["UserOutputValue"].per == {} and n["LineInverter"].per == {}
    txt = Path(data["send_back"]).with_suffix(".txt").read_text(encoding="utf-8")
    assert txt.startswith("CAMERA CHECK REPORT") and "LineSelector (restored to 'Line0')" in txt


def test_alox_ini_names_and_name_map(tmp_path):
    code, data, _, _ = run_tool(tmp_path / "a", "--fake", "--alox-ini", str(GIGE_IDS_INI), "--frames", "1")
    assert code == EXIT_PROBLEM                                         # "cam2" is an NI MAX alias
    assert data["alox_ini"]["high_mag"]["name"] == "cam2" and data["alox_ini"]["lights_camera"] == "cam2"
    assert any("--name-map cam2=<serial>" in p for p in data["problems"])
    assert data["checks"][1]["name"] == "LowMag" and data["checks"][1]["grab"]["frames_received"] == 1
    code, data, _, _ = run_tool(tmp_path / "b", "--fake", "--alox-ini", str(GIGE_IDS_INI), "--frames", "1",
                                "--name-map", "cam2=4103000001")
    assert code == EXIT_OK and data["checks"][0]["matched"].startswith("#0 GV-5280CP-M #4103000001")
    app = {row["what"]: row for row in data["checks"][0]["application"]}
    assert app["gain (gain_STR)"]["attribute"] == "CameraAttributes::AnalogControl::Gain"
    assert app["gain (gain_STR)"]["feature"] == "Gain" and app["gain (gain_STR)"]["type"] == "I64"


def test_lost_frames_reveal_the_jumbo_frame_problem(tmp_path):
    code, data, _, _ = run_tool(tmp_path, "--fake", "--camera", "HighMag", "--frames", "3", "--timeout-ms", "30",
                                harvester=small_harvester(incomplete=True))
    assert code == EXIT_OK
    grab = data["checks"][0]["grab"]
    assert grab["frames_received"] == 0 and grab["timeouts"] == 2 and grab["incomplete"] > 0   # stops after 2
    assert "png" not in data["checks"][0]
    assert any("enable jumbo frames" in p for p in data["problems"])


def test_list_only_and_unknown_camera(tmp_path):
    code, data, _, _ = run_tool(tmp_path / "a", "--fake", "--list")
    assert code == EXIT_OK and len(data["cameras_seen"]) == 2 and data["checks"] == []
    code, data, text, _ = run_tool(tmp_path / "b", "--fake", "--camera", "Nope")
    assert code == EXIT_PROBLEM and "not found" in data["checks"][0]["open_error"]
    assert any("--name-map Nope=<serial>" in p for p in data["problems"]) and "DeviceUserID" in text


def test_no_producer_and_no_harvesters(tmp_path, monkeypatch):
    monkeypatch.delenv("GENICAM_GENTL64_PATH", raising=False)
    code, data, text, _ = run_tool(tmp_path / "a")
    assert code == EXIT_PROBLEM and "--producer" in text and data["producer"]["files"] == []
    cti = tmp_path / "ids_gevgentlk.cti"
    cti.write_bytes(b"")
    monkeypatch.setitem(sys.modules, "harvesters", None)
    monkeypatch.setitem(sys.modules, "harvesters.core", None)
    code, data, _, _ = run_tool(tmp_path / "b", "--producer", str(cti))
    assert code == EXIT_PROBLEM and data["producer"]["files"] == [str(cti)]
    assert any("pip install harvesters" in p for p in data["problems"])


@pytest.mark.parametrize("argv", [["--yes"], ["--fake", "--producer", "x.cti"], ["--fake", "--frames", "0"],
                                  ["--fake", "--name-map", "cam2"], ["--fake", "--alox-ini", "missing.ini"],
                                  ["--fake", "--test-exposure", "0"], ["--producer", "missing.cti"]])
def test_usage_errors(tmp_path, argv):
    output = io.StringIO()
    assert main([*argv, "--out", str(tmp_path)], env=Environment(output=output)) == EXIT_USAGE
    assert output.getvalue().startswith("error: ") and not list(tmp_path.iterdir())


def test_report_paths(tmp_path):
    started = datetime(2026, 11, 25, 9, 5, 7, tzinfo=UTC)
    assert report_paths(tmp_path, started)[0] == tmp_path / "camera_report_20261125-090507.json"
    assert report_paths(tmp_path / "x.json", started) == (tmp_path / "x.json", tmp_path / "x.txt")


# ================================================================================================== guided actions
def test_actions_with_yes(tmp_path):
    code, data, _, harvester = run_tool(tmp_path, "--fake", "--camera", "HighMag", "--frames", "2", "--actions",
                                        "--yes")
    size_problem = "HighMag: image size 64 x 48 is not the full IMX264 sensor (ROI or binning left in the camera?)"
    assert code == EXIT_OK and data["problems"] == [size_problem]
    exposure, packet, lights = data["actions"]
    assert exposure["status"] == "done" and exposure["written"] == 5000.0 and exposure["read_back"] == 5000.0
    assert exposure["restored"] == 10000.0
    assert exposure["grab"]["image"]["mean"] < data["checks"][0]["grab"]["image"]["mean"]   # half the exposure
    assert packet["status"] == "done" and packet["written"] == 8996 and packet["restored"] == 1440
    assert packet["grab"]["frames_received"] == 2
    assert lights["status"] == "done" and lights["read_on"] is True and lights["read_off"] is False
    assert lights["io_on"]["UserOutputValue"] is True and lights["io_on"]["LineSource"] == "UserOutput0"
    n = nodes(harvester)
    assert n["ExposureTime"].value == 10000.0 and n["GevSCPSPacketSize"].value == 1440
    assert n["UserOutputValue"].per == {0: False}                       # switched off at the end
    assert [a["meaning"] for a in data["operator_answers"]] == ["yes", "yes", "yes", "text", "text"]


def test_actions_need_a_typed_yes(tmp_path):
    code, data, _, harvester = run_tool(tmp_path, "--fake", "--camera", "HighMag", "--frames", "1", "--actions",
                                        answers=("no", "quit"))
    assert code == EXIT_OK
    assert [a["status"] for a in data["actions"]] == ["skipped", "skipped (quit)", "skipped (quit)"]
    n = nodes(harvester)
    assert n["ExposureTime"].value == 10000.0 and n["GevSCPSPacketSize"].value == 1440
    assert n["UserOutputValue"].per == {} and n["LineSource"].per == {}


def test_ctrl_c_with_the_led_on_switches_it_off(tmp_path):
    code, data, text, harvester = run_tool(tmp_path, "--fake", "--camera", "HighMag", "--frames", "1", "--actions",
                                           answers=("no", "no", "yes", KeyboardInterrupt()))
    assert code == EXIT_PROBLEM and "Interrupted (Ctrl+C)" in text and "interrupted by Ctrl+C" in data["problems"]
    assert nodes(harvester)["UserOutputValue"].per == {0: False}
    assert harvester.cameras[0].acquirer is not None and harvester.cameras[0].acquirer.destroyed


def test_lights_on_a_usb_camera(tmp_path):
    ini = tmp_path / "alox.ini"
    ini.write_text('[general]\nsetLEDcam_I32 = 0\n\n[hardware.camera.highmag]\nname_STR = "HighMag"\n'
                   'cameraconnectortype_STR = "USB"\nsetLEDline_I32 = 2\nsetBULBline_I32 = 3\n\n'
                   '[hardware.camera]\nlightStatusInverter_BOOL = TRUE\n', encoding="utf-8")
    code, data, _, harvester = run_tool(tmp_path / "out", "--fake", "--alox-ini", str(ini), "--camera", "HighMag",
                                        "--frames", "1", "--actions", "--yes")
    assert code == EXIT_OK
    assert [a["action"] for a in data["actions"]] == ["exposure", "LED / bulb"]   # no packet size on USB
    lights = data["actions"][1]
    assert lights["read_on"] is True and lights["read_off"] is False
    assert nodes(harvester)["LineInverter"].per == {2: False, 3: True}          # LED line off, bulb line inverted


# ================================================================================================== as the owner runs
def test_runs_as_a_script_without_pythonpath(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    done = subprocess.run([sys.executable, str(TOOL), "--fake", "--list", "--out", str(tmp_path)], cwd=tmp_path,
                          env=env, capture_output=True, text=True, timeout=60, check=False)
    assert done.returncode == EXIT_OK, done.stdout + done.stderr
    assert "2 camera(s) seen" in done.stdout and list(tmp_path.glob("camera_report_*.json"))
