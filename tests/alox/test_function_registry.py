"""Registration of the measurement functions (``alox.functions.registry``)."""

from __future__ import annotations

from alox.core.functions import FunctionName, FunctionRunner, MeasurementFunction
from alox.functions.registry import register_functions
from alox.functions.services import Services


class Dummy(MeasurementFunction):
    name = FunctionName.TRACKING

    def run(self, params, ctx):
        return params


def test_focus_functions_need_the_z_stage_and_a_camera():
    runner = FunctionRunner()
    outcome = register_functions(runner, Services(devices={"z": object(), "lowmag": object()}))
    assert outcome[FunctionName.AUTOFOCUS_LARGE] == "registered"
    assert outcome[FunctionName.REFOCUS] == "registered"
    assert runner.function(FunctionName.REFOCUS).camera_for(1) is not None
    outcome = register_functions(FunctionRunner(), Services(devices={"lowmag": object()}))
    assert outcome[FunctionName.AUTOFOCUS_LARGE].startswith("skipped")


def test_missing_modules_and_failing_factories_are_reported():
    def broken(_services: Services) -> MeasurementFunction:
        raise LookupError("device 'xy' is not available")

    runner = FunctionRunner()
    outcome = register_functions(runner, Services(), {
        FunctionName.TRACKING: lambda _s: Dummy(),
        FunctionName.SEARCH_OBJECT: broken,
        FunctionName.MESA_MEASURE: lambda _s: __import__("alox.functions.no_such_module"),
    })
    assert outcome == {FunctionName.TRACKING: "registered",
                       FunctionName.SEARCH_OBJECT: "skipped: device 'xy' is not available",
                       FunctionName.MESA_MEASURE: "not available (alox.functions.no_such_module)"}
    assert runner.function(FunctionName.TRACKING).run(3, None) == 3


def test_services_of_a_core_carry_the_measure_statuses(tmp_path):
    from alox.core.core import Core, CoreConfig
    from alox.core.folders import Folders
    from alox.functions.registry import build_services

    root = tmp_path / "alox"
    folders = Folders(exe=tmp_path, settings=root / "s", vision=root / "v", data=root / "d", profiles=root / "p",
                      exposures=root / "e", jobhistory=root / "j")
    core = Core(folders, config=CoreConfig(supervise_hardware=False, heartbeat=False))
    services = build_services(core)
    assert sorted(services.statuses) == ["bowing", "mesa", "tilt"]
    services.set_status("mesa", 404)
    assert core.measures[next(m for m in core.measures if m.name == "MESA")].code == 404


def test_the_mesa_gets_the_core_tilt_and_search_wavelength_the_mesa_hook():
    from alox.functions.mesa import locate_mesa

    runner = FunctionRunner()
    devices = {"z": object(), "highmag": object(), "wl": object(), "xy": object()}
    outcome = register_functions(runner, Services(devices=devices, tilt=lambda: 0.75))
    assert outcome[FunctionName.MESA_MEASURE] == "registered"
    mesa = runner.function(FunctionName.MESA_MEASURE)
    assert any(getattr(mesa, name, None) is not None and callable(getattr(mesa, name)) and getattr(mesa, name)() == 0.75
               for name in ("tilt", "_tilt"))
    if outcome[FunctionName.SEARCH_WAVELENGTH] == "registered":
        wavelength = runner.function(FunctionName.SEARCH_WAVELENGTH)
        assert locate_mesa in (getattr(wavelength, "locate", None), getattr(wavelength, "_locate", None))
