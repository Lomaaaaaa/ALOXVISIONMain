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
