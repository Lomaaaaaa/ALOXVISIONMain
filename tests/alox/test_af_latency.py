"""The Fast autofocus latency measurement (owner B8, ``alox.functions.af_latency``)."""

from __future__ import annotations

import math

import pytest

from alox.core.functions import FunctionName, FunctionRunner
from alox.functions.af_latency import measure_fast_latency, peak_z
from alox.functions.focus import AutofocusParams, AutofocusSpeed, FocusResult
from alox.functions.registry import register_functions
from alox.functions.services import Services


def gaussian_table(peak: float, zs: list[float]) -> tuple[tuple[float, float], ...]:
    return tuple((z, math.exp(-((z - peak) / 0.05) ** 2)) for z in zs)


def result(table: tuple[tuple[float, float], ...]) -> FocusResult:
    return FocusResult(table=table, best_z=None, best_score=None, best_index=None, position=None, progress=100.0)


def test_peak_z_refines_between_the_steps():
    zs = [1.0 + 0.01 * i for i in range(21)]
    assert peak_z(gaussian_table(1.1037, zs)) == pytest.approx(1.1037, abs=1e-3)
    assert peak_z([(1.0, 5.0), (1.1, 1.0)]) == 1.0                       # at an end: the best point
    assert math.isnan(peak_z([]))


def test_latency_from_slow_and_fast_peaks():
    """Fast peak 30 µm above the Slow peak at 0.1 mm/s → 0.3 s."""
    calls: list[AutofocusParams] = []

    def run_once(name: FunctionName, params: AutofocusParams) -> FocusResult:
        calls.append(params)
        assert name is FunctionName.AUTOFOCUS_LARGE
        zs = [4.8 + 0.005 * i for i in range(81)]
        return result(gaussian_table(5.0 if params.speed is AutofocusSpeed.SLOW else 5.03, zs))

    estimate = measure_fast_latency(run_once, AutofocusParams(z_min=4.8, z_max=5.2), step=0.005, duration_s=4, runs=2)
    assert estimate.speed_mm_s == pytest.approx(0.1)
    assert estimate.latency_s == pytest.approx(0.3, abs=0.01) and estimate.spread_s == pytest.approx(0.0, abs=1e-9)
    assert [p.speed for p in calls] == [AutofocusSpeed.SLOW, AutofocusSpeed.FAST] * 2
    assert calls[0].step == 0.005 and calls[1].duration_s == 4
    assert estimate.ini_line().startswith("fastaflatency_DBL = 0,3")
    with pytest.raises(ValueError):
        measure_fast_latency(run_once, AutofocusParams(z_min=5.0, z_max=4.0), step=0.005, duration_s=4)


def test_the_registered_autofocus_uses_the_measured_latency():
    class Settings:
        def get(self, dotted, default=None):
            return 0.042 if dotted == "hardware.z.fastaflatency" else default

    runner = FunctionRunner()
    register_functions(runner, Services(devices={"z": object(), "highmag": object()}, settings=Settings()))
    assert runner.function(FunctionName.AUTOFOCUS_LARGE).options.fast_latency_s == pytest.approx(0.042)
