"""The Fast autofocus latency tool, rehearsed on simulated devices."""

from __future__ import annotations

import json
from pathlib import Path

import af_latency_check


def test_rehearsal_on_simulated_devices(tmp_path: Path):
    lines: list[str] = []
    args = af_latency_check.parse_args(["--simulate", "--z-min", "1.0", "--z-max", "1.2", "--step", "0.05",
                                        "--duration", "1", "--runs", "1", "--yes", "--out", str(tmp_path)])
    estimate = af_latency_check.run(args, out=lines.append)
    assert estimate is not None and len(estimate.runs) == 1
    assert any(line.startswith("add under [hardware.z]") for line in lines)
    report = json.loads(next(tmp_path.glob("af_latency_report_*.json")).read_text(encoding="utf-8"))
    assert report["setting"] == "hardware.z.fastaflatency" and report["camera"] == "highmag"


def test_nothing_moves_without_yes(tmp_path: Path):
    lines: list[str] = []
    args = af_latency_check.parse_args(["--simulate", "--z-min", "1.0", "--z-max", "1.2", "--out", str(tmp_path)])
    assert af_latency_check.run(args, ask=lambda _prompt: "no", out=lines.append) is None
    assert lines[-1] == "nothing moved" and not list(tmp_path.glob("*.json"))
