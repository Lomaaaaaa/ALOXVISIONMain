"""``tools/thermal_check.py``: read-only checks of the pyrometer and the BandiT, rehearsed with ``--fake``."""

from __future__ import annotations

import io
import json
from pathlib import Path

from thermal_check import PYRO_REQUESTS, Environment, main

from alox.thermal.modbus_sim import FakeModbusServer

WRITE_COMMANDS = (0x84, 0x85, 0xA5)                    # Set Emissivity, Set Transmissivity, Set Spot Illumination


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(0.0, seconds)


def run(args: list[str], tmp_path: Path, **env_kwargs: object) -> tuple[int, dict, str, Environment]:
    clock = Clock()
    output = io.StringIO()
    env = Environment(output=output, sleep=clock.sleep, monotonic=clock.monotonic, **env_kwargs)  # type: ignore[arg-type]
    code = main([*args, "--out", str(tmp_path / "report.json")], env=env)
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    return code, report, output.getvalue(), env


def test_fake_rehearsal_reads_both_probes_and_never_writes(tmp_path: Path) -> None:
    code, report, text, env = run(["--fake", "--probe", "both", "--samples", "3"], tmp_path)
    assert code == 0, text
    assert report["pyrometer"]["samples"] == [25.0, 25.0, 25.0]
    reads = {r["read"]: r.get("value") for r in report["pyrometer"]["reads"]}
    assert [r[0] for r in PYRO_REQUESTS] == list(reads)
    assert reads["emissivity"] == 0.95 and reads["serial"] == 1234567
    assert env.emulator is not None
    sent = env.emulator.received
    assert not [r for r in sent if r[0] in WRITE_COMMANDS]
    assert sent[:3] == [b"\x01\x01"] * 3
    bandit = report["bandit"]
    assert bandit["register"] == 12298 and [s["millivolts"] for s in bandit["samples"]] == [2500] * 3
    assert bandit["samples"][0]["temperature"] == 400.0
    assert {i["read"] for i in bandit["information"]} == {"function 4 (input register)", "function 3, unit 0",
                                                          "function 3, unit 1", "function 3, unit 255"}
    assert report["result"] == {"exit_code": 0} and (tmp_path / "report.txt").exists()
    assert "PLEASE SEND THIS FILE BACK" in text


def test_unreachable_bandit_and_usage_errors(tmp_path: Path) -> None:
    server = FakeModbusServer({}).start()
    port = server.port
    server.close()
    code, report, _, _ = run(["--probe", "bandit", "--host", "127.0.0.1", "--modbus-port", str(port)], tmp_path)
    assert code == 1 and report["problems"]
    assert main(["--samples", "0"], env=Environment(output=io.StringIO())) == 2
    assert main(["--probe", "nothing"], env=Environment(output=io.StringIO())) == 2


def test_the_bandit_exception_is_reported(tmp_path: Path) -> None:
    with FakeModbusServer({1: 5}) as server:
        code, report, _, _ = run(["--probe", "bandit", "--host", "127.0.0.1", "--modbus-port", str(server.port),
                                  "--samples", "1"], tmp_path)
    assert code == 1
    assert report["bandit"]["samples"][0]["code"] == 102                     # illegal data address
