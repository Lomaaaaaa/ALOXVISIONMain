"""The application container and its command line (``alox.app``), in simulation."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from alox.app import AppConfig, Application, _port_map, main
from alox.devices.base import DeviceState


@pytest.fixture(autouse=True)
def _restore_logging():
    """``main`` configures the root logger; restore it after each test."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for handler in list(root.handlers):
        if handler not in handlers:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(level)


def test_application_starts_the_simulated_z_stage():
    with Application(AppConfig(simulate=True)) as app:
        seen = app.events.subscribe(("device.z",))
        assert app.start() == {"z": "ready"}
        status = app.status()
        assert status["simulate"] is True
        assert status["devices"]["z"]["state"] == "ready"
        assert status["devices"]["z"]["referenced"] is True
        app.z.move_absolute(2.0)
        assert app.z.position() == pytest.approx(2.0)
        assert any(e.topic == "device.z.position" for e in seen.drain())
    assert app.z.state == DeviceState.SHUTDOWN


def test_application_reads_the_z_settings_from_alox_ini(tmp_path: Path):
    ini = tmp_path / "alox.ini"
    ini.write_text("[hardware.z]\ncomport_I32 = 7\nspeed_DBL = 4,500000\nzmin_DBL = 1,000000\nzmax_DBL = 9,000000\n",
                   encoding="cp1252")
    with Application(AppConfig(alox_ini=ini, simulate=True)) as app:
        assert app.z.settings.com == 7
        assert app.z.settings.speed == pytest.approx(4.5)
        assert app.z.limits == (1.0, 9.0)


def test_check_command_prints_the_status(capsys):
    assert main(["--simulate", "--check", "--log-level", "WARNING"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["devices"]["z"]["state"] == "ready"


def test_check_fails_when_a_device_does_not_open(capsys, tmp_path: Path):
    """Real hardware on a port that does not exist: the device stays in fault, ``--check`` exits 1."""
    pytest.importorskip("serial")
    code = main(["--check", "--log-level", "CRITICAL", "--port-map", f"COM2={tmp_path / 'no-such-port'}"])
    assert code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["devices"]["z"]["state"] == "fault"


def test_port_map_parsing():
    assert _port_map(["com2=socket://127.0.0.1:5000", " COM3 = /dev/ttyUSB0 "]) == {
        "COM2": "socket://127.0.0.1:5000", "COM3": "/dev/ttyUSB0"}
    with pytest.raises(SystemExit):
        _port_map(["COM2"])
