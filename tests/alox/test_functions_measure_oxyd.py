"""Measure Oxyd (LabVIEW "Measure Mesa") as a measurement function (``alox.functions.measure_oxyd``) on a synthetic
mesa: background 40, mesa (oxidised ring) 120, oxide aperture 200, optics blur and noise; 0.5 µm per pixel."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest
from scipy import ndimage

from alox.core.functions import FunctionContext, FunctionName, FunctionRunner
from alox.events import Event
from alox.functions.detect_circle import DetectCircle, DetectCircleParams, FunctionError
from alox.functions.measure_oxyd import (
    DiametersReport,
    MeasureOxyd,
    MeasureOxydParams,
    MeasureOxydReport,
    MesaShapeType,
)
from alox.functions.services import Services
from alox.vision.circles import Circle
from alox.vision.edges import SearchDirection
from alox.vision.stats import OxideMeasureType

PX_UM = 0.5
CX, CY = 181.4, 152.6
R_MESA, R_APERTURE = 100.0, 60.0                # px: diameters 100 µm and 60 µm


class Store(dict):
    def get(self, key: str, default: Any = None) -> Any:          # type: ignore[override]
        return super().get(key, default)


def mesa_image(*, r_aperture: float | None = R_APERTURE, h: int = 310, w: int = 370, seed: int = 4) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w]
    d = np.hypot(xx - CX, yy - CY)
    img = np.full((h, w), 40.0)
    img[d <= R_MESA] = 120.0
    if r_aperture:
        img[d <= r_aperture] = 200.0
    img = ndimage.gaussian_filter(img, 1.0) + np.random.default_rng(seed).normal(0.0, 2.0, (h, w))
    return np.clip(np.rint(img), 0, 255).astype(np.uint8)


GOOD = {"mesa_min_diam_um": 80.0, "mesa_max_diam_um": 120.0, "oxyd_max_diam_um": 90.0}


@pytest.fixture
def rig() -> Any:
    events: list[Event] = []
    services = Services(settings=Store({"hardware.camera.highmag.pixelsize": PX_UM,
                                        "hardware.camera.highmag.units": 1}))
    runner = FunctionRunner(publish=events.append)

    class Rig:
        def __init__(self) -> None:
            self.events, self.services, self.runner = events, services, runner
            self.function = MeasureOxyd(services)

        def run(self, **params: Any) -> MeasureOxydReport:
            values: dict[str, Any] = {"circle": Circle(CX + 2.0, CY - 1.5, 98.0), "image": mesa_image()}
            values.update(params)
            ctx = FunctionContext(FunctionName.MEASURE_OXYD, None, runner)
            return self.function.run(MeasureOxydParams(**values), ctx)

        def states(self) -> list[str]:
            return [e.data["state"] for e in events if e.topic == "function.measure_oxyd.state"]

    return Rig()


def test_measures_mesa_and_oxide_diameters_in_pixels(rig):
    r = rig.run(**GOOD)
    assert r.mesa_found and r.oxyd_found
    assert (r.external_circle.x, r.external_circle.y, r.external_circle.radius) == pytest.approx(
        (CX, CY, R_MESA), abs=0.3)
    assert (r.oxyd_circle.x, r.oxyd_circle.y, r.oxyd_circle.radius) == pytest.approx((CX, CY, R_APERTURE), abs=0.3)
    d = r.diameters
    assert d.external == pytest.approx(200.0, abs=0.6)
    for value in (d.fit, d.mean, d.median):
        assert value == pytest.approx(120.0, abs=0.6)
    assert d.surface == pytest.approx(120.0, abs=1.5)                   # polygon through the edge points
    assert d.inscribed <= d.circumscribed and d.circumscribed - d.inscribed < 3.0
    assert d.circ_insc_ratio == pytest.approx(d.circumscribed / d.inscribed)
    assert d.accuracy == pytest.approx(100.0 * (d.circumscribed - d.inscribed) / d.inscribed)
    assert d.stddev < 1.0
    assert len(r.diameter_lines) == len(r.diameter_lengths) == len(r.oxyd_points) > 50
    assert r.fit_score > 900 and r.gradient_values and r.mesa_search_lines and r.oxyd_search_lines
    um = r.diameters_um
    assert um.fit == pytest.approx(60.0, abs=0.3) and um.external == pytest.approx(100.0, abs=0.3)
    assert um.circ_insc_ratio == d.circ_insc_ratio
    assert rig.states() == ["Start", "Started", "Measure", "Measure", "Finished"]
    assert rig.function.last_report is r


def test_overlays_follow_the_switches(rig):
    rig.run(**GOOD)
    overlays = rig.services.images.overlays
    assert {"mesa", "oxyd", "edges", "diameters", "target"} <= set(overlays)
    assert overlays["target"][0].r == pytest.approx(25.0 / 2 / PX_UM)
    assert overlays["oxyd"][-1].text == f"Oxyd Diam.: {rig.function.last_report.diameters_um.mean:.2f} µm"
    rig.run(overlay_external=False, overlay_mesa_search_lines=False, overlay_mesa_edges=False, overlay_oxyd=False,
            overlay_oxyd_measure=False, overlay_oxyd_edges=False, overlay_oxyd_diameters=False, overlay_target=False,
            **GOOD)
    assert all(shapes == () for shapes in overlays.values())


def test_default_mesa_maximum_finds_no_mesa_error_9022(rig):
    with pytest.raises(FunctionError) as info:
        rig.run(oxyd_max_diam_um=90.0)                                  # measure.vcsel.maxMesaDiam default 0
    assert info.value.code == 9022
    report = info.value.report
    assert not report.mesa_found and report.oxyd_found
    assert rig.states()[-1] == "Error"


def test_mesa_not_mandatory_uses_the_input_circle(rig):
    r = rig.run(oxyd_max_diam_um=90.0, mesa_mandatory=False)
    assert not r.mesa_found and r.external_circle == Circle(CX + 2.0, CY - 1.5, 98.0)
    assert math.isnan(r.diameters.external)                              # bounded: 98 µm not in [5, 0]
    assert r.diameters.fit == pytest.approx(120.0, abs=0.6)


def test_missing_oxide(rig):
    image = mesa_image(r_aperture=None)
    with pytest.raises(FunctionError) as info:
        rig.run(image=image, **GOOD)
    assert info.value.code == 9021 and not info.value.report.oxyd_found
    r = rig.run(image=image, oxyd_mandatory=False, **GOOD)
    assert r.mesa_found and not r.oxyd_found and r.oxyd_circle.is_nan
    assert math.isnan(r.diameters.fit) and r.diameters.external == pytest.approx(200.0, abs=0.6)


def test_offset_is_added_to_the_oxide_diameters_only(rig):
    base = rig.run(**GOOD).diameters
    shifted = rig.run(oxid_result_offset_um=1.0, **GOOD).diameters           # 1 µm = 2 px
    for name in ("fit", "median", "mean", "surface", "inscribed", "circumscribed"):
        assert getattr(shifted, name) == pytest.approx(getattr(base, name) + 2.0)
    assert (shifted.external, shifted.circ_insc_ratio, shifted.stddev, shifted.accuracy) == (
        base.external, base.circ_insc_ratio, base.stddev, base.accuracy)


def test_outside_in_search_and_circumscribed_type(rig):
    r = rig.run(oxyd_search_dir=SearchDirection.OUTSIDE_TO_INSIDE, oxyd_measure_type="Circumscribed", **GOOD)
    assert r.oxyd_circle.radius == pytest.approx(R_APERTURE, abs=0.5)  # lines start at 45 µm, inside the mesa
    beyond = rig.run(oxyd_search_dir=0, **{**GOOD, "oxyd_max_diam_um": 150.0})
    assert beyond.oxyd_circle.radius == pytest.approx(R_MESA, abs=0.5)   # lines start outside: the mesa edge first
    assert r.diameters.selected(OxideMeasureType.CIRCUMSCRIBED) == r.diameters.circumscribed


def test_without_outlier_rejection_every_point_is_used(rig):
    image = mesa_image()
    image[150:156, 230:250] = 255                                       # a defect inside the aperture
    robust = rig.run(image=image, **GOOD)
    plain = rig.run(image=image, reject_outliers=False, **GOOD)
    assert len(plain.oxyd_points) > len(robust.oxyd_points)
    assert robust.diameters.fit == pytest.approx(120.0, abs=0.6)


def test_input_errors(rig):
    with pytest.raises(FunctionError) as info:
        rig.run(circle=None, **GOOD)
    assert info.value.code == 9023 and info.value.state == "Start"
    with pytest.raises(FunctionError) as info:
        rig.run(mesa_shape_type=MesaShapeType.LINE, **GOOD)
    assert info.value.code == 9021
    with pytest.raises(FunctionError) as info:
        rig.function.run(MeasureOxydParams(circle=(10, 10, 5), **GOOD),
                         FunctionContext(FunctionName.MEASURE_OXYD, None, rig.runner))
    assert info.value.code == 9023                                       # no image in the store
    with pytest.raises(FunctionError) as info:
        MeasureOxydParams(oxyd_measure_type="Largest")
    assert info.value.code == 9002


def test_profile_keys_and_defaults():
    p = MeasureOxydParams.from_profile(Store({"measure.oxyd.direction": 0, "measure.oxyd.measuretype": 2,
                                              "measure.vcsel.maxMesaDiam": 120.0,
                                              "measure.vcsel.MeasMesaMandatory": False}))
    assert p.oxyd_search_dir is SearchDirection.OUTSIDE_TO_INSIDE and p.oxyd_measure_type is OxideMeasureType.FIT
    assert p.mesa_max_diam_um == 120.0 and p.mesa_mandatory is False
    d = MeasureOxydParams.from_profile(Store())
    assert (d.mesa_strength, d.mesa_kernel, d.mesa_width, d.mesa_step, d.mesa_min_diam_um) == (10.0, 3, 3, 5.0, 5.0)
    assert (d.oxyd_search_dir, d.oxyd_max_diam_um, d.target_diam_um, d.fit_max_dist, d.fit_min_score,
            d.fit_max_iterations) == (SearchDirection.INSIDE_TO_OUTSIDE, 50.0, 25.0, 3, 500.0, 500)
    assert d.mesa_max_diam_um == 0.0 and d.oxyd_min_diam_um == 0.0 and d.oxyd_mandatory


def test_detect_circle_then_measure_oxyd_data_flow(rig):
    """The HL mesa chain: Detect Circle's best circle is Measure Oxyd's ``Circle In``."""
    image = mesa_image()
    detect = DetectCircle(rig.services)
    found = detect.run(DetectCircleParams(image=image, radius_min_um=40, radius_max_um=60, ellipses=False),
                       FunctionContext(FunctionName.DETECT_CIRCLE, None, rig.runner))
    r = rig.run(circle=found.best_circle, image=image, **GOOD)
    assert r.diameters_um.mean == pytest.approx(60.0, abs=0.3)


def test_diameters_report_scaling():
    d = DiametersReport(1, 2, 3, 4, 5, 6, 7, 1.5, 0.2, 3.0).scaled(2.0)
    assert d == DiametersReport(2, 4, 6, 8, 10, 12, 14, 1.5, 0.2, 3.0)
