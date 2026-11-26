"""Detect Circle as a measurement function (``alox.functions.detect_circle``) on synthetic mesa images."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest
from scipy import ndimage

from alox.core.functions import FunctionContext, FunctionName, FunctionRunner, Stopped
from alox.events import Event
from alox.functions.detect_circle import (
    AlgoMethod,
    BestCircleMethod,
    DetectCircle,
    DetectCircleParams,
    DetectCircleReport,
    Downsampling,
    FunctionError,
    OutputImageType,
    pixel_size_um,
)
from alox.functions.services import Circle as CircleShape
from alox.functions.services import Services
from alox.vision.circles import Circle
from aloxcam.imaging.png import write_png

PX_UM = 0.5                                     # µm per pixel


class Store(dict):
    """A profile / settings reader (``get(dotted, default)``)."""

    def get(self, key: str, default: Any = None) -> Any:          # type: ignore[override]
        return super().get(key, default)


def disc_image(discs: list[tuple[float, float, float, float]], h: int = 300, w: int = 360, *, noise: float = 2.0,
               seed: int = 1) -> np.ndarray:
    """Background 40; each disc ``(cx, cy, r, level)``; optics blur σ 1 px; noise."""
    yy, xx = np.mgrid[0:h, 0:w]
    img = np.full((h, w), 40.0)
    for cx, cy, r, level in discs:
        img[np.hypot(xx - cx, yy - cy) <= r] = level
    img = ndimage.gaussian_filter(img, 1.0) + np.random.default_rng(seed).normal(0.0, noise, (h, w))
    return np.clip(np.rint(img), 0, 255).astype(np.uint8)


MESA = (182.3, 148.7, 60.0, 150.0)              # 30 µm radius


@pytest.fixture
def rig() -> Any:
    events: list[Event] = []
    settings = Store({"hardware.camera.highmag.pixelsize": PX_UM, "hardware.camera.highmag.units": 1})
    services = Services(settings=settings)
    runner = FunctionRunner(publish=events.append)

    class Rig:
        def __init__(self) -> None:
            self.events, self.services, self.runner = events, services, runner
            self.function = DetectCircle(services)

        def ctx(self) -> FunctionContext:
            return FunctionContext(FunctionName.DETECT_CIRCLE, None, runner)

        def run(self, **params: Any) -> DetectCircleReport:
            return self.function.run(DetectCircleParams(**params), self.ctx())

        def states(self) -> list[str]:
            return [e.data["state"] for e in events if e.topic == "function.detect_circle.state"]

    return Rig()


def test_shape_finds_the_mesa_and_draws_the_best_circle(rig):
    report = rig.run(image=disc_image([MESA]), overlay_best_circle=True, overlay_all_circles=True)
    assert report.found and report.best_index == 0
    c = report.best_circle
    assert (c.x, c.y, c.radius) == pytest.approx(MESA[:3], abs=0.5)
    assert report.pixel_size_um == PX_UM and report.image_size == (360, 300)
    assert len(report.all_circles) == 2                         # circles, then ellipses (LabVIEW concatenates)
    assert report.all_circles[1].radius == pytest.approx(60.0, abs=1.0)
    assert report.process_time_ms >= 0
    shapes = rig.services.images.overlays["detectcircle"]
    assert shapes[-1] == CircleShape(c.x, c.y, c.radius, (255, 0, 0), 10)       # best: red, 10
    assert shapes[0].width == 1 and shapes[0].color == (0, 0, 255)              # the others: blue, 1
    assert rig.states() == ["Start", "Started", "Detect Circle", "Detect Circle", "Finished"]
    assert rig.function.last_report is report


def test_overlays_off_clear_the_group(rig):
    rig.services.images.set_overlay("detectcircle", [CircleShape(1, 1, 1)])
    rig.run(image=disc_image([MESA]))
    assert rig.services.images.overlays["detectcircle"] == ()


def test_no_circle_is_error_9015_with_the_report(rig):
    with pytest.raises(FunctionError) as info:
        rig.run(image=disc_image([]))
    assert info.value.code == 9015 and info.value.state == "Detect Circle"
    assert not info.value.report.found and info.value.report.best_index == -1
    assert info.value.report.best_circle is None
    assert rig.states()[-1] == "Error"
    assert rig.function.last_report is info.value.report


def test_radius_range_is_in_micrometres(rig):
    image = disc_image([MESA])
    assert rig.run(image=image, radius_min_um=25, radius_max_um=35).found
    with pytest.raises(FunctionError):
        rig.run(image=image, radius_min_um=10, radius_max_um=20)          # 20…40 px: the 60 px mesa is outside


def test_best_circle_methods(rig):
    image = disc_image([(90.0, 150.0, 40.0, 150.0), (250.0, 150.0, 70.0, 150.0)], w=360)
    common = {"image": image, "radius_min_um": 15, "radius_max_um": 40, "ellipses": False}
    largest = rig.run(best_circle_method="Largest", **common)
    assert largest.best_circle.radius == pytest.approx(70, abs=1)
    centered = rig.run(best_circle_method=BestCircleMethod.CENTERED, **common)
    assert centered.best_circle.x == pytest.approx(250, abs=1)          # 70 px from the centre (180, 150) vs 90
    scores = [c.score for c in centered.all_circles]
    by_score = rig.run(best_circle_method=1, **common)
    assert by_score.best_index == int(np.argmax(scores))                 # Fixed: LabVIEW kept the lowest score


def test_downsampling_keeps_full_resolution_coordinates(rig):
    report = rig.run(image=disc_image([MESA]), downsampling="50%", ellipses=False)
    c = report.best_circle
    assert (c.x, c.y, c.radius) == pytest.approx(MESA[:3], abs=1.5)
    assert Downsampling.P75.factor == 0.25


def test_morphology_and_output_image(rig):
    image = disc_image([MESA])
    report = rig.run(image=image, morph_open=3, morph_close=4, output_image_type="Pre-Processed", ellipses=False)
    assert report.found
    shown = rig.services.images.images["highmag"]
    assert shown.shape == image.shape and not np.array_equal(shown, image)


def test_blobs_method(rig):
    image = disc_image([MESA], noise=1.0)
    report = rig.run(image=image, algo_method=AlgoMethod.BLOBS, window_size=301, blob_method=1, binary_invert=False,
                     small_iterations=2, output_image_type=OutputImageType.BINARIZED)
    c = report.best_circle
    assert (c.x, c.y, c.radius) == pytest.approx(MESA[:3], abs=1.5)
    shown = rig.services.images.images["highmag"]
    assert set(np.unique(shown)) == {0, 255}
    with pytest.raises(FunctionError):                                    # inverted: the background touches the border
        rig.run(image=image, algo_method="Blobs", window_size=301, blob_method=1, binary_invert=True)


def test_obj_learning_matches_the_model(rig, tmp_path):
    image = disc_image([MESA], noise=1.0)
    model = image[89:210, 122:243].copy()                               # centred on (182, 149)
    write_png(tmp_path / "mesa_model.png", model)
    report = rig.run(image=image, algo_method="Obj learning", model_path=tmp_path / "mesa_model.png")
    c = report.best_circle
    assert (c.x, c.y) == pytest.approx((182, 149), abs=1.0)
    assert c.radius == pytest.approx(60.5)
    with pytest.raises(FunctionError) as info:
        rig.run(image=image, algo_method="Obj learning", model_path=tmp_path / "missing.png")
    assert info.value.code == 9015


def test_image_from_the_store_and_missing_image(rig):
    with pytest.raises(FunctionError) as info:
        rig.run()
    assert info.value.code == 9015 and info.value.state == "Start"
    rig.services.images.put("highmag", disc_image([MESA]))
    assert rig.run(ellipses=False).found


def test_profile_keys_defaults_and_coercion():
    params = DetectCircleParams.from_profile(Store({"detect.vcsel.method": 2, "detect.vcsel.minradius": "12,5",
                                                   "detect.vcsel.overlaybestcircle": "TRUE"}))
    assert params.algo_method is AlgoMethod.BLOBS and params.radius_min_um == 12.5
    assert params.overlay_best_circle is True
    defaults = DetectCircleParams.from_profile(Store())
    assert (defaults.radius_min_um, defaults.radius_max_um, defaults.min_score, defaults.edge_threshold) == (
        20.0, 50.0, 70.0, 15)
    assert (defaults.circ_factor_min, defaults.circ_factor_max, defaults.obj_score) == (0.75, 1.25, 75.0)
    with pytest.raises(FunctionError) as info:
        DetectCircleParams(algo_method="Hough")
    assert info.value.code == 9002


def test_calibration_units():
    s = Services(settings=Store({"hardware.camera.lowmag.pixelsize": 0.0007, "hardware.camera.lowmag.units": 2}))
    assert pixel_size_um(s, "lowmag") == pytest.approx(0.7)
    s = Services(settings=Store({"hardware.camera.lowmag.pixelsize": 2.0, "hardware.camera.lowmag.units": 0}))
    assert pixel_size_um(s, "lowmag") == 2.0
    with pytest.raises(FunctionError) as info:
        pixel_size_um(s, "lowmag", pixels_ok=False)
    assert info.value.code == 9080


def test_stop_ends_in_stopped(rig):
    ctx = rig.ctx()
    ctx.stop_event.set()
    with pytest.raises(Stopped):
        rig.function.run(DetectCircleParams(image=disc_image([MESA])), ctx)
    assert rig.states() == ["Start", "Stopped"]


def test_force_results_and_runner(rig):
    forced = rig.function.force_results(Circle(1224, 1024, 30))
    assert forced.found and rig.function.last_report.best_circle == Circle(1224, 1024, 30)
    rig.runner.register(rig.function)
    report = rig.runner.run_once(FunctionName.DETECT_CIRCLE, DetectCircleParams(image=disc_image([MESA])), timeout=30)
    assert report.found and math.isclose(report.best_circle.radius, 60, abs_tol=0.5)
