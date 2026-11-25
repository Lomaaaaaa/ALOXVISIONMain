"""``tools/xy_stage_check.py``: the protocol probes (both daisy-chain variants), the read-only information of both
controllers, and the guided motion tests with their safety rules, against the emulated daisy chain.

Most tests run the tool's ``main()`` in process with virtual time: the tool's ``sleep`` / ``monotonic``, the XY device's
and the emulated controllers' clock are one :class:`FakeTime`, so the moves take no real time and every result is
deterministic. The protocol probes wait in real time (a probe the emulation ignores costs ``--probe-timeout-ms``). One
subprocess test runs ``python tools/xy_stage_check.py`` as the owner will, without PYTHONPATH.
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import xy_stage_check
from xy_stage_check import (
    INFO_STEPS,
    Environment,
    main,
    resolve_target,
    start_fake_server,
)

from alox.devices.fake_chain import FakeDaisyChainServer, chain_identity

pytest.importorskip("serial")

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "tools" / "xy_stage_check.py"
REAL_INI = REPO / "tests" / "config" / "data" / "real" / "alox.ini"
START = 75.0                                     # both emulated stages start referenced at mid-travel
PROBE_MS = "200"
#: Frames that move a stage, change a setting or stop / start a macro (as the fake chain records them).
CHANGING = re.compile(r"(?:^|\s)(?:MOV|MVR|VEL|SVO|STP|FRF|FNL|FPL|JON|JAX|RON|MAC)(?:\s|$)|#24")
PROBES_DEFAULT = ["*IDN?", "ERR?", "#5", "#7", "1 *IDN?", "1 0 *IDN?", "2 *IDN?", "2 0 *IDN?",
                  "1 ERR?", "1 #5", "1 #7", "1 SRG? 1 1", "1 RMC?", "1 ERR?",
                  "2 ERR?", "2 #5", "2 #7", "2 SRG? 1 1", "2 RMC?", "2 ERR?"]


def info_frames(prefix: str = "") -> list[str]:
    frames = []
    for device in (1, 2):
        frames += [f"{device} {prefix}{step.frame}" for step in INFO_STEPS if device == 1 or not step.x_only]
    return frames


def changing(frames: list[str]) -> list[str]:
    return [f for f in frames if CHANGING.search(f)]


class FakeTime:
    """Virtual time shared by the tool, the XY device's worker and the emulated controllers; ``on_sleep`` runs after
    every sleep of the tool's own thread (to inject a Ctrl+C or a runaway stage while it watches a move)."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start
        self._lock = threading.Lock()
        self.tool_thread = threading.current_thread()
        self.on_sleep: Callable[[], None] | None = None

    def monotonic(self) -> float:
        with self._lock:
            return self.now

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self.now += max(0.0, seconds)
        if self.on_sleep is not None and threading.current_thread() is self.tool_thread:
            self.on_sleep()


def answers_from(answers: list[Any]) -> Callable[[], str]:
    it: Iterator[Any] = iter(answers)

    def read_line() -> str:
        try:
            answer = next(it)
        except StopIteration:
            raise EOFError from None
        return str(answer)

    return read_line


@dataclass
class Rig:
    time: FakeTime
    server: FakeDaisyChainServer
    out_dir: Path
    answers: list[Any] = field(default_factory=list)
    out: io.StringIO = field(default_factory=io.StringIO)

    def model(self, device: int) -> Any:
        return self.server.chain.device(device).model

    @property
    def frames(self) -> list[str]:
        """What the emulated chain received, addresses included."""
        return self.server.chain.frames

    @property
    def output(self) -> str:
        return self.out.getvalue()

    def run(self, *argv: str) -> int:
        env = Environment(read_line=answers_from(self.answers), output=self.out, sleep=self.time.sleep,
                          monotonic=self.time.monotonic, fake_server=self.server)
        return main(["--fake", "--probe-timeout-ms", PROBE_MS, *argv, "--out", str(self.out_dir)], env=env)

    def report(self) -> dict[str, Any]:
        files = sorted(self.out_dir.glob("xy_stage_report_*.json"))
        assert len(files) == 1, files
        return json.loads(files[0].read_text(encoding="utf-8"))

    def steps(self) -> dict[str, dict[str, Any]]:
        return {t["key"]: t for t in self.report()["motion"]["tests"]}


@pytest.fixture(autouse=True)
def _short_quiet_period(monkeypatch: pytest.MonkeyPatch) -> None:
    """A probe answer without final LF is complete after QUIET_S without a byte: 50 ms is plenty on loopback."""
    monkeypatch.setattr(xy_stage_check, "QUIET_S", 0.05)


@pytest.fixture
def make_rig(tmp_path: Path) -> Iterator[Callable[..., Rig]]:
    servers: list[FakeDaisyChainServer] = []

    def factory(**fake: Any) -> Rig:
        clock = FakeTime()
        server = start_fake_server(clock=clock.monotonic, wait=clock.sleep, **fake)
        servers.append(server)
        out_dir = tmp_path / f"out{len(servers)}"
        out_dir.mkdir()
        rig = Rig(clock, server, out_dir)
        assert [rig.model(d).position for d in (1, 2)] == [pytest.approx(START)] * 2
        return rig

    yield factory
    for server in servers:
        server.close()


@pytest.fixture
def rig(make_rig: Callable[..., Rig]) -> Rig:
    return make_rig()


# ---------------------------------------------------------------------------------------------- probes + information
def test_information_probes_and_reads_both_controllers_read_only(rig: Rig) -> None:
    before = [(rig.model(d).position, rig.model(d).velocity, rig.model(d).referenced) for d in (1, 2)]
    assert rig.run() == 0
    assert rig.frames == PROBES_DEFAULT + info_frames()
    assert changing(rig.frames) == []
    assert [(rig.model(d).position, rig.model(d).velocity, rig.model(d).referenced) for d in (1, 2)] == before

    report = rig.report()
    chain = report["daisy_chain"]
    assert (chain["address_mode"], chain["char_mode"]) == ("target_only", "addressed")
    assert chain["code"].endswith("address_mode='target_only', char_mode='addressed')")
    probes = {p["key"]: p for p in report["probes"]["exchanges"]}
    assert probes["target_only:2:*IDN?"]["received"] == f"0 2 {chain_identity(2)}\\n"
    assert probes["target_only:1:#7"]["value"] == {"ready": True, "lf": True}
    assert probes["target_only:2:RMC?"]["meaning"] == "empty answer, as '0 2 \\n'"
    assert probes["target_only:2:SRG? 1 1"]["value"] == 0x9000          # servo on, on target
    for axis, device in (("x", 1), ("y", 2)):
        info = report["info"][axis]
        assert info["device"] == device and all(q["ok"] for q in info["queries"])
        values = {q["key"]: q["value"] for q in info["queries"]}
        assert values["idn"] == chain_identity(device) and values["pos"] == START and values["tmx"] == 150.0
        assert values["cst"] == "SIM-STAGE" and values["frf"] is True and values["jon"] == 0 and values["err"] == 0
        assert ("jbs" in values) == (axis == "x")
    topics = {f["topic"]: f["answer"] for f in report["findings"]}
    assert topics["Daisy-chain settings for the application"] == chain["code"]
    assert topics["Stage models (CST? 1)"] == "X: 'SIM-STAGE'; Y: 'SIM-STAGE'"
    assert "followed by LF" in topics["#7 answer (addressed)"]
    assert "bit 12 matches SVO? 1 = 1" in topics["SRG? 1 1 bits, device 2"]
    assert report["motion"] is None and report["result"]["exit_code"] == 0 and report["problems"] == []
    sent = [f["cmd"] for f in report["frames"] if f["dir"] == "tx"]
    assert sent[:5] == ["*IDN?", "ERR?", "#5", "#7", "1 *IDN?"] and "2 #5" in sent

    text = next(rig.out_dir.glob("*.txt")).read_text(encoding="utf-8")
    assert text.startswith("XY STAGE CHECK REPORT\nSend this report back: ")
    assert "DAISY-CHAIN VARIANT (for the developers)\n  GcsChain(" in text
    assert "sent '2 *IDN?\\n'  received '0 2 " in text
    assert "PLEASE SEND THIS FILE BACK" in rig.output


def test_probes_find_the_other_variant(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(address_mode="target_sender", char_mode="text_equivalents")
    assert rig.run() == 0
    report = rig.report()
    chain = report["daisy_chain"]
    assert (chain["address_mode"], chain["char_mode"]) == ("target_sender", "text_equivalents")
    assert "target_only: answered by device(s) none" in chain["notes"]
    probes = {p["key"]: p for p in report["probes"]["exchanges"]}
    assert probes["target_only:1:*IDN?"]["timed_out"] and probes["target_sender:2:SRG? 1 1"]["ok"]
    assert probes["target_sender:1:ERR?"]["value"] == 2            # "1 *IDN?" was not understood
    assert not probes["target_sender:2:#5"]["ok"] and "flush after target_sender:2:#5" in probes
    assert rig.frames[-len(info_frames("0 ")):] == info_frames("0 ")   # information in the variant found
    assert all(q["ok"] for axis in ("x", "y") for q in report["info"][axis]["queries"])
    topics = {f["topic"]: f["answer"] for f in report["findings"]}
    assert "char_mode='text_equivalents'" in topics["Single-byte commands"]


def test_ready_byte_without_lf_is_recorded(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(ready_lf=False)
    assert rig.run() == 0
    report = rig.report()
    probes = {p["key"]: p for p in report["probes"]["exchanges"]}
    assert probes["target_only:2:#7"]["received"] == "0 2 \\xb1"
    assert probes["target_only:2:#7"]["value"] == {"ready": True, "lf": False}
    assert report["daisy_chain"]["char_mode"] == "addressed"


def test_a_missing_device_stops_after_the_probes(rig: Rig, tmp_path: Path) -> None:
    ini = tmp_path / "alox.ini"
    ini.write_text("[hardware.xy]\ncomport_I32 = 3\nbaudrate_I32 = 3\nxaxis_I32 = 1\nyaxis_I32 = 3\n",
                   encoding="cp1252")
    assert rig.run("--alox-ini", str(ini)) == 1
    report = rig.report()
    assert report["devices"] == {"x": 1, "y": 3}
    assert report["daisy_chain"]["address_mode"] is None and report["info"]["x"]["queries"] == []
    assert any("no address form was answered by both controllers" in p for p in report["problems"])
    assert changing(rig.frames) == []


def test_a_failed_query_is_followed_by_err(rig: Rig) -> None:
    rig.server.chain.device(2).fail_next("TRS?", 15)            # no answer: −7, then ERR? reads the reason
    assert rig.run("--timeout-ms", "300") == 0
    i = rig.frames.index("2 TRS? 1")
    assert rig.frames[i + 1:i + 3] == ["2 ERR?", "2 LIM? 1"]
    entry = next(q for q in rig.report()["info"]["y"]["queries"] if q["frame"] == "TRS? 1")
    assert not entry["ok"] and entry["code"] == -7 and entry["err_after"] == "15  Invalid axis identifier"


# ---------------------------------------------------------------------------------------------- motion
@pytest.mark.parametrize("variant", [{}, {"address_mode": "target_sender", "char_mode": "text_equivalents"}])
def test_motion_with_fake_and_yes(make_rig: Callable[..., Rig], variant: dict[str, Any]) -> None:
    rig = make_rig(**variant)
    assert rig.run("--motion", "--yes") == 0
    report = rig.report()
    steps = rig.steps()
    assert list(steps) == ["setup", "relative_x", "relative_y", "wafer_refusal", "stp_during_move"]
    assert {s["status"] for s in steps.values()} == {"done"}
    assert steps["relative_x"]["out"]["reached_mm"] == [76.0, START] and steps["relative_x"]["back"]["ok"]
    assert steps["relative_y"]["out"]["reached_mm"] == [START, 76.0]
    wafer = steps["wafer_refusal"]
    assert wafer["error_code"] == 9404 and wafer["moving_frames"] == [] and wafer["position_mm"] == [START, START]
    stp = steps["stp_during_move"]
    assert stp["answer"].startswith("STP STOPPED X") and START < stp["final_mm"][0] < 76.0
    sender = "0 " if variant else ""
    assert f"1 {sender}SVO 1 1" in rig.frames and f"2 {sender}SVO 1 1" in rig.frames
    assert f"1 {sender}STP 1" in rig.frames and not any("FNL" in f or "FRF 1" in f for f in rig.frames)
    if variant:                                                      # waits through the status register
        assert f"1 {sender}SRG? 1 1" in rig.frames[len(PROBES_DEFAULT):]
    motion = report["motion"]
    assert motion["move_back"]["final_mm"] == [START, START] and motion["end_mm"] == [START, START]
    assert [r["result"].split(",")[0] for r in motion["restore"]] == ["restored: VEL? 1 = 10.0000 mm/s"] * 2
    assert [rig.model(d).velocity for d in (1, 2)] == [10.0, 10.0]
    assert motion["emergency_stops"] == [] and report["result"]["exit_code"] == 0


@pytest.mark.parametrize("answers", [["y"], ["yes", "no"], ["YES please"]])
def test_motion_needs_typed_yes_and_declining_moves_nothing(rig: Rig, answers: list[str]) -> None:
    rig.answers = answers
    assert rig.run("--motion") == 0
    assert changing(rig.frames) == []
    assert [rig.model(d).position for d in (1, 2)] == [START, START]


def test_skipping_every_step_after_the_setup_moves_nothing(rig: Rig) -> None:
    rig.answers = ["yes", "yes", "no", "no", "no", "no"]
    assert rig.run("--motion") == 0
    assert not any(re.search(r"\b(MOV|MVR)\b", f) for f in rig.frames)
    assert {s["status"] for k, s in rig.steps().items() if k != "setup"} == {"skipped"}
    assert [rig.model(d).velocity for d in (1, 2)] == [10.0, 10.0]   # the test speed set by the set-up is restored


def test_an_unreferenced_axis_needs_a_second_yes(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    rig.model(2).power_cycle()                                       # Y unreferenced, servo off
    rig.answers = ["yes", "yes", "no"]
    assert rig.run("--motion") == 0
    assert changing(rig.frames) == [] and rig.steps()["setup"]["status"] == "skipped (no reference move)"

    rig2 = make_rig()
    rig2.model(2).power_cycle()
    assert rig2.run("--motion", "--yes") == 0
    assert "2 FNL 1" in rig2.frames and "1 FNL 1" not in rig2.frames
    report = rig2.report()
    assert report["motion"]["start_after_reference"] is True and report["motion"]["start_mm"] == [START, 0.0]
    assert rig2.steps()["relative_y"]["out"]["reached_mm"] == [START, 1.0]


def test_a_runaway_stage_sends_stop_all_to_both(rig: Rig) -> None:
    model = rig.model(1)

    def runaway() -> None:
        if model.velocity < 10.0 and model.position > START + 0.2:  # during the first X move: a far target
            rig.time.on_sleep = None
            model.mov("1", 100.0)

    rig.time.on_sleep = runaway
    assert rig.run("--motion", "--yes") == 1
    report = rig.report()
    stops = report["motion"]["emergency_stops"]
    assert len(stops) == 1 and "X left the allowed range" in stops[0]["reason"] and stops[0]["sent"]
    assert "1 #24" in rig.frames and "2 #24" in rig.frames
    assert rig.model(1).position < 76.0 + 0.1 and not rig.model(1).is_moving()
    assert "move_back" not in report["motion"] and report["motion"]["aborted"]
    assert [rig.model(d).velocity for d in (1, 2)] == [10.0, 10.0]


def test_ctrl_c_during_a_move_sends_stop_all(rig: Rig) -> None:
    def interrupt() -> None:
        if rig.model(1).is_moving():
            rig.time.on_sleep = None
            raise KeyboardInterrupt

    rig.time.on_sleep = interrupt
    assert rig.run("--motion", "--yes") == 1
    report = rig.report()
    assert report["motion"]["aborted"] == "Ctrl+C pressed by the operator"
    assert "1 #24" in rig.frames and "2 #24" in rig.frames and not rig.model(1).is_moving()


def test_motion_is_refused_with_the_joystick_on(rig: Rig) -> None:
    rig.model(2).jon(1, True)
    assert rig.run("--motion", "--yes") == 0
    assert "the joystick is on (Y JON? 1 = 1)" in rig.report()["motion"]["refused"]
    assert changing(rig.frames) == []


# ---------------------------------------------------------------------------------------------- command line
@pytest.mark.parametrize("argv, text", [
    (["--port", "COM3", "--yes"], "--yes is only accepted together with --fake"),
    (["--fake", "--port", "COM3"], "--fake and --port cannot be used together"),
    ([], "say which port to use"),
    (["--port", "COM3", "--fake-char-mode", "text_equivalents"], "need --fake"),
    (["--fake", "--step", "2", "--window", "1"], "--step must not be larger than --window"),
    (["--port", "COM3", "--speed", "6"], "--speed above 5 mm/s is refused"),
    (["--port", "COM3", "--window", "20"], "--window above 10 mm is refused"),
])
def test_usage_errors_open_nothing(tmp_path: Path, argv: list[str], text: str) -> None:
    out = io.StringIO()
    assert main([*argv, "--out", str(tmp_path)], env=Environment(output=out)) == 2
    assert text in out.getvalue() and list(tmp_path.iterdir()) == []


def test_target_from_alox_ini_and_port() -> None:
    args = xy_stage_check.build_parser().parse_args(["--alox-ini", str(REAL_INI)])
    target = resolve_target(args)
    assert (target.port, target.baudrate, target.devices) == ("COM3", 115200, (1, 2))   # the first xaxis_I32
    assert target.ranges == {"x": (0.0, 150.0), "y": (0.0, 150.0)}                       # the _DBL keys
    args = xy_stage_check.build_parser().parse_args(["--alox-ini", str(REAL_INI), "--port", "5", "--baud", "38400"])
    assert (resolve_target(args).port, resolve_target(args).baudrate) == ("COM5", 38400)


def test_an_unopenable_port_gives_advice(tmp_path: Path) -> None:
    out = io.StringIO()
    assert main(["--port", "socket://127.0.0.1:1", "--out", str(tmp_path)], env=Environment(output=out)) == 1
    assert "could not be opened. Close the LabVIEW XY server" in out.getvalue()
    assert len(list(tmp_path.glob("xy_stage_report_*.json"))) == 1


def test_frames_are_listed_in_the_docstring_and_the_help(capsys: pytest.CaptureFixture[str]) -> None:
    doc = xy_stage_check.__doc__ or ""
    info = doc[doc.index("3. Information per controller"):doc.index("4. Only when")]
    assert re.findall(r"``([^`]+)``", info)[:len(INFO_STEPS)] == [step.frame for step in INFO_STEPS]
    probes = doc[doc.index("2. Protocol probes"):doc.index("3. Information per controller")]
    assert {"*IDN?", "ERR?", "#5", "#7", "d *IDN?", "d 0 *IDN?", "SRG? 1 1", "RMC?"} <= set(
        re.findall(r"``([^`]+)``", probes))
    assert main(["--help"]) == 0
    help_text = " ".join(capsys.readouterr().out.split())
    assert ", ".join(step.frame for step in INFO_STEPS) in help_text and "--yes" in help_text


def test_runs_from_a_checkout_without_installing(tmp_path: Path) -> None:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    result = subprocess.run([sys.executable, str(TOOL), "--fake", "--out", str(tmp_path)], capture_output=True,
                            text=True, env=env, cwd=tmp_path, timeout=120, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DAISY-CHAIN VARIANT: GcsChain(" in result.stdout and "PLEASE SEND THIS FILE BACK" in result.stdout
    assert len(list(tmp_path.glob("xy_stage_report_*.json"))) == 1
