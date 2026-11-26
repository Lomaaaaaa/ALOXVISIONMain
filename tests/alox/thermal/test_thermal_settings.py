"""The ``alox.ini`` keys of the thermal map (``alox.thermal.settings``)."""

from __future__ import annotations

import pytest

from alox.config import TypedIni
from alox.core.properties import Properties
from alox.thermal.settings import (
    MAX_CIRCLES,
    ThermalSettings,
    load_thermal_config,
    load_thermal_settings,
    write_back,
    write_origin,
)

INI = """[hardware.bandit]
ipadress_STR = " 192.168.1.2"
minVoltage_I32 = 0
maxVoltage_I32 = 5
minTemperature_I32 = 300
maxTemperature_I32 = 500
startregister_I32 = 10
enableBandit_BOOL = TRUE
diam1_DBL = 0,000000
diam2_DBL = 20,000000
diam3_DBL = 60,000000
nbpoints1_U32 = 1
nbpoints2_U32 = 8
nbpoints3_U32 = 8
numberMeasure_U32 = 5
offsetAngle_U32 = 0
wait_I32 = 1500
ScriptPythonPath_STR = "py readdata_arg.py --path "
[hardware.pyro]
comport_I32 = 7
baudrate_I32 = 115200
[hardware.xy]
chuckxorg_DBL = 77,628868
chuckyorg_DBL = 88,223190
stagescoordsys_U32 = 3
"""


def test_load_from_the_ini_with_labview_defaults_for_missing_keys() -> None:
    config = load_thermal_config(TypedIni.parse(INI))
    s = config.settings
    assert s.diameters[:3] == (0.0, 20.0, 60.0) and s.diameters[3:] == (10.0,) * 6
    assert s.nb_points[:3] == (1, 8, 8) and s.nb_points[3:] == (10,) * 6
    assert (s.samples, s.offset_angle, s.wait_ms, s.bandit_enabled) == (5, 0, 1500, True)
    assert s.script_command == "py readdata_arg.py --path "
    assert s.origin == pytest.approx((77.628868, 88.22319)) and s.origin_defined and s.coordinate_system == 3
    assert config.pyro.port_name == "COM7" and config.bandit.host == "192.168.1.2"


def test_empty_ini_gives_the_diagram_defaults() -> None:
    s = load_thermal_settings(TypedIni.parse(""))
    assert s == ThermalSettings()
    assert (s.samples, s.offset_angle, s.wait_ms, s.origin_defined) == (10, 10, 2000, False)


def test_invalid_values_take_their_default_with_a_warning() -> None:
    warnings: list[str] = []
    ini = {"hardware.bandit.numberMeasure": 0, "hardware.bandit.diam2": -3.0, "hardware.bandit.wait": -5,
           "hardware.xy.stagescoordsys": 7, "hardware.bandit.nbpoints3": "many"}
    s = load_thermal_settings(ini, warn=warnings.append)
    assert (s.samples, s.diameters[1], s.wait_ms, s.coordinate_system, s.nb_points[2]) == (1, 0.0, 0, 3, 10)
    assert len(warnings) == 5


def test_settings_are_validated() -> None:
    with pytest.raises(ValueError):
        ThermalSettings(diameters=(1.0,) * (MAX_CIRCLES + 1))
    with pytest.raises(ValueError):
        ThermalSettings(samples=0)
    with pytest.raises(ValueError):
        ThermalSettings(diameters=(-1.0,))
    assert ThermalSettings().with_circles([5, 6], [1, 2]).nb_points == (1, 2)


def test_write_back_keeps_the_key_types_and_writes_the_ninth_circle() -> None:
    store = Properties.parse(INI)
    settings = load_thermal_settings(store).with_circles([0.0, 25.0, 50.0, 0, 0, 0, 0, 0, 30.0],
                                                         [1, 6, 12, 0, 0, 0, 0, 0, 4])
    written = write_back(store, settings)
    assert len(written) == 2 * MAX_CIRCLES + 3
    assert store.get("hardware.bandit.diam2") == 25.0 and store.suffix_of("hardware.bandit.diam2") == "DBL"
    assert store.get("hardware.bandit.nbpoints3") == 12 and store.suffix_of("hardware.bandit.nbpoints3") == "U32"
    assert store.get("hardware.bandit.diam9") == 30.0 and store.get("hardware.bandit.nbpoints9") == 4
    assert store.get("hardware.bandit.numberMeasure") == 5
    assert store.get("hardware.bandit.ScriptPythonPath") == "py readdata_arg.py --path "


def test_write_back_skips_what_the_store_refuses() -> None:
    store = Properties.parse(INI)
    warnings: list[str] = []
    settings = ThermalSettings(offset_angle=-10)
    written = write_back(store, settings, warn=warnings.append)
    assert "hardware.bandit.offsetAngle" not in written and store.get("hardware.bandit.offsetAngle") == 0
    assert any("offsetAngle" in w for w in warnings)
    assert write_origin(store, (80.5, 90.25)) == ["hardware.xy.chuckxorg", "hardware.xy.chuckyorg"]
    assert store.get("hardware.xy.chuckxorg") == 80.5
