"""``tools/wl_check.py``: the information mode (exactly the documented read-only requests) and the guided moves,
against the CM11x emulator served on a loopback ``socket://`` port (pyserial, as on the COM port).

The emulator moves fast (50 000 nm/s) so the tests take little real time. One subprocess test runs
``python tools/wl_check.py`` as the owner will, without PYTHONPATH (the tool finds ``<repo>/src`` by itself).
"""

from __future__ import annotations

import io
import json
import os
import re
import socket
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import wl_check
from wl_check import (
    INFO_QUERIES,
    INFO_REQUESTS,
    Environment,
    main,
    plan_targets,
    resolve_target,
)

from alox.devices.cm11x_sim import Cm11xEmulatorServer, SimOptions

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "tools" / "wl_check.py"
MOVING = (16, 13, 12, 18, 26, 50, 255)                 # GOTO, SPEED, SCAN, CALIBRATE, SELECT, UNITS, RESET


def answers_from(answers: list[Any]) -> Callable[[], str]:
    it: Iterator[Any] = iter(answers)

    def read_line() -> str:
        try:
            answer = next(it)
        except StopIteration:
            raise EOFError from None
        if isinstance(answer, BaseException) or (isinstance(answer, type) and issubclass(answer, BaseException)):
            raise answer
        return str(answer)

    return read_line


class Rig:
    def __init__(self, out_dir: Path, **options: Any) -> None:
        options.setdefault("position_nm", 750.0)
        options.setdefault("slew_nm_per_s", 50000.0)
        options.setdefault("reset_s", 0.05)
        self.server = Cm11xEmulatorServer(SimOptions(**options)).start()
        self.out_dir = out_dir
        self.out = io.StringIO()

    @property
    def emulator(self) -> Any:
        return self.server.emulator

    @property
    def received(self) -> list[bytes]:
        return list(self.emulator.received)

    def moving(self) -> list[str]:
        return [c for r, c in zip(self.received, self.emulator.commands, strict=True) if r[0] in MOVING]

    def run(self, *argv: str, answers: list[Any] | None = None) -> int:
        env = Environment(read_line=answers_from(answers or []), output=self.out, fake_server=self.server)
        return main([*argv, "--out", str(self.out_dir)], env=env)

    @property
    def output(self) -> str:
        return self.out.getvalue()

    def report(self) -> dict[str, Any]:
        files = sorted(self.out_dir.glob("wl_report_*.json"))
        assert len(files) == 1, files
        return json.loads(files[0].read_text(encoding="utf-8"))


@pytest.fixture
def make_rig(tmp_path: Path) -> Iterator[Callable[..., Rig]]:
    rigs: list[Rig] = []

    def factory(**options: Any) -> Rig:
        rig = Rig(tmp_path / f"out{len(rigs)}", **options)
        rigs.append(rig)
        return rig

    yield factory
    for rig in rigs:
        rig.server.close()


# ---------------------------------------------------------------------------------------------- information
def test_info_sends_exactly_the_documented_read_only_requests(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    assert rig.run("--fake") == 0
    assert rig.received == list(INFO_REQUESTS)
    assert rig.moving() == []
    report = rig.report()
    queries = report["info"]["queries"]
    assert len(queries) == 22 and all(q["ok"] for q in queries)
    assert queries[1] == {"request": "QUERY 19", "label": "serial number", "ok": True, "value": 12345,
                          "shown": "12345", "raw": "30 39 00 18"}
    topics = {f["topic"] for f in report["findings"]}
    assert {"Serial number", "Grating 1", "Grating 2", "Units", "Range used by the application"} <= topics
    assert report["frames"][0]["cmd"] == "ECHO"
    assert "Information done: 22 requests, 0 failed." in rig.output
    assert "PLEASE SEND THIS FILE BACK" in rig.output


def test_the_documented_queries_are_in_the_docstring_and_the_help(capsys: pytest.CaptureFixture[str]) -> None:
    doc = " ".join((wl_check.__doc__ or "").split())
    listed = doc[doc.index("then ``QUERY`` (56, n) for n =") + 30:doc.index(": serial number")]
    assert [int(n) for n in re.findall(r"\d+", listed)] == list(INFO_QUERIES)
    assert main(["--help"]) == 0
    assert ", ".join(map(str, INFO_QUERIES)) in capsys.readouterr().out


def test_a_garbled_answer_is_recorded_and_the_next_query_sent(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    rig.emulator.bad_end_next()                        # the answer to QUERY 19 ends with 0, not 24
    assert rig.run("--fake") == 0
    report = rig.report()
    q19 = report["info"]["queries"][1]
    assert not q19["ok"] and q19["raw"] == "30 39 00 00"
    assert all(q["ok"] for q in report["info"]["queries"][2:])
    assert report["problems"]


def test_a_large_serial_number_is_read_and_reported(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(serial_number=40000)
    assert rig.run("--fake") == 0
    finding = next(f for f in rig.report()["findings"] if f["ref"] == "D1")
    assert ">= 32768" in finding["answer"]


def test_no_answer_stops_after_three_and_gives_advice(tmp_path: Path) -> None:
    with socket.create_server(("127.0.0.1", 0)) as silent:          # accepts the connection, never answers
        port = silent.getsockname()[1]
        out = io.StringIO()
        code = main(["--port", f"socket://127.0.0.1:{port}", "--timeout-ms", "100", "--out", str(tmp_path)],
                    env=Environment(output=out))
    assert code == 1
    assert "did not answer in time" in out.getvalue()
    report = json.loads(next(tmp_path.glob("wl_report_*.json")).read_text(encoding="utf-8"))
    assert len(report["info"]["queries"]) == 3


def test_open_failure_is_explained(tmp_path: Path) -> None:
    out = io.StringIO()
    assert main(["--port", "socket://127.0.0.1:1", "--out", str(tmp_path)], env=Environment(output=out)) == 1
    assert "could not be opened" in out.getvalue()


# ---------------------------------------------------------------------------------------------- moves
def test_motion_rehearsal_with_yes(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    assert rig.run("--fake", "--motion", "--yes") == 0
    assert rig.moving() == ["GOTO 760", "GOTO 750", "GOTO 700", "GOTO 950", "GOTO 750"]
    assert rig.received[:len(INFO_REQUESTS)] == list(INFO_REQUESTS)
    motion = rig.report()["motion"]
    assert [t["status"] for t in motion["tests"]] == ["done", "done"]
    assert motion["tests"][1]["moves"][1]["reached_nm"] == 950
    assert motion["move_back"].startswith("reached 750 nm")
    assert rig.emulator.position_nm == 750


def test_moves_need_a_typed_yes(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    assert rig.run("--fake", "--motion", answers=["y", ""]) == 0
    assert rig.moving() == []
    assert [t["status"] for t in rig.report()["motion"]["tests"]] == ["skipped", "skipped"]


def test_quit_skips_the_remaining_tests(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    assert rig.run("--fake", "--motion", "--reset", answers=["yes", "quit"]) == 0
    assert rig.moving() == ["GOTO 760", "GOTO 750"]
    motion = rig.report()["motion"]
    assert [t["status"] for t in motion["tests"]] == ["done", "quit"]
    assert motion["move_back"] == "already at the start position 750 nm"


def test_move_back_needs_a_yes(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    assert rig.run("--fake", "--motion", answers=["no", "yes", "no"]) == 0
    assert rig.moving() == ["GOTO 700", "GOTO 950"]
    assert rig.report()["motion"]["move_back"] == "declined; left at 950 nm"


def test_units_are_set_to_nanometres_only_after_a_yes(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(units=2)
    assert rig.run("--fake", "--motion", answers=["no"]) == 1
    assert rig.moving() == []
    rig2 = make_rig(units=2)
    assert rig2.run("--fake", "--motion", answers=["yes", "skip", "skip"]) == 0
    assert rig2.moving() == ["UNITS 1"]


def test_reset_test_reports_the_echo_time(make_rig: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wl_check, "RESET_POLL_S", 0.01)
    monkeypatch.setattr(wl_check, "RESET_ANSWER_S", 0.05)
    rig = make_rig()
    assert rig.run("--fake", "--motion", "--reset", "--yes") == 0
    assert "RESET" in rig.moving()
    report = rig.report()
    assert [t["status"] for t in report["motion"]["tests"]] == ["done", "done", "done"]
    assert any(f["ref"] == "Q2" for f in report["findings"])
    assert rig.moving()[-1] == "GOTO 750"            # moved back after the reset (position 0)


def test_a_range_outside_the_grating_is_refused(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    assert rig.run("--fake", "--motion", "--yes", "--range", "700", "1600") == 1
    assert rig.moving() == []
    assert "not inside 0 .. 1500" in rig.report()["motion"]["refused"]


def test_a_move_that_stops_short_is_a_problem(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    rig.emulator.stop_short_next(3)
    assert rig.run("--fake", "--motion", "--yes") == 1
    assert any("ended at 757 nm" in p for p in rig.report()["problems"])


def test_plan_targets() -> None:
    assert plan_targets(750, 1500, 10, 700, 950) == (760, 700, 950)
    assert plan_targets(1495, 1500, 10, 700, 950) == (1485, 700, 950)
    with pytest.raises(ValueError):
        plan_targets(750, 1500, 10, 700, 1600)


# ---------------------------------------------------------------------------------------------- command line
@pytest.mark.parametrize("argv, text", [
    ([], "say which port"),
    (["--port", "COM9", "--yes"], "--yes is only accepted together with --fake"),
    (["--fake", "--port", "COM9"], "cannot be used together"),
    (["--fake", "--reset"], "--reset is a move test"),
    (["--fake", "--range", "900", "800"], "--range LO HI"),
    (["--fake", "--step", "0"], "--step"),
])
def test_usage_errors_open_nothing(tmp_path: Path, argv: list[str], text: str) -> None:
    out = io.StringIO()
    assert main([*argv, "--out", str(tmp_path)], env=Environment(output=out)) == 2
    assert text in out.getvalue()
    assert not list(tmp_path.glob("wl_report_*"))


def test_target_from_alox_ini(tmp_path: Path) -> None:
    ini = tmp_path / "alox.ini"
    ini.write_text("[hardware.monochromator]\r\nport_I32 = 505    \r\nbaudrate_I32 = 0    \r\ncomport_I32 = 1    \r\n"
                   "timeout_I32 = 5000    \r\nwaitposition_U32 = 500    \r\n", encoding="cp1252")
    args = wl_check.build_parser().parse_args(["--alox-ini", str(ini)])
    target = resolve_target(args)
    assert (target.port, target.baudrate, target.timeout_ms) == ("COM1", 9600, 5000)
    assert target.ini_values["waitposition_ms"] == 500
    args = wl_check.build_parser().parse_args(["--alox-ini", str(ini), "--port", "4", "--timeout-ms", "800"])
    target = resolve_target(args)
    assert (target.port, target.timeout_ms) == ("COM4", 800)


def test_runs_from_a_checkout_without_installing(tmp_path: Path) -> None:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    result = subprocess.run([sys.executable, str(TOOL), "--fake", "--out", str(tmp_path)], capture_output=True,
                            text=True, env=env, cwd=tmp_path, timeout=120, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Information done: 22 requests, 0 failed." in result.stdout
    assert len(list(tmp_path.glob("wl_report_*.json"))) == 1
