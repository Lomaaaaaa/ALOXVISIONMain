"""The simulated wafer scene (:mod:`alox.devices.scene`) and the ``frame_source`` hook of the simulated cameras."""

from __future__ import annotations

import math
import zipfile
from typing import Any

import numpy as np
import pytest

from alox.config import TypedIni
from alox.devices.scene import DEMO_FOLDER, Mark, Scene, SceneCameraBackend, SceneParams, make_demo
from alox.vision.focus import fin_roi, measure
from alox.vision.wafer import tracking_move
from aloxcam.camera.backend import ValueType
from aloxcam.camera.sim import SIM_EXPOSURE, SimCameraBackend, frame_number_of
from aloxcam.settings import SimulationOptions

PIXEL_UM = {"highmag": 0.68, "lowmag": 2.72}


class Settings(dict[str, Any]):
    def get(self, key: str, default: Any = None) -> Any:          # type: ignore[override]
        return super().get(key, default)


def settings(**extra: Any) -> Settings:
    values: dict[str, Any] = {"general.wafersize": 76.2, "hardware.xy.waferxorg": 100.0,
                              "hardware.xy.waferyorg": 100.0, "hardware.xy.stagescoordsys": 0,
                              "hardware.camera.highmag.pixelsize": PIXEL_UM["highmag"],
                              "hardware.camera.lowmag.pixelsize": PIXEL_UM["lowmag"]}
    values.update(extra)
    return Settings(values)


@pytest.fixture
def scene() -> Scene:
    return Scene(SceneParams(tilt_deg=0.0), settings=settings(), clock=lambda: 1000.0)


def frame(scene: Scene, camera: str = "highmag", *, at: tuple[float, float] = (0.0, 0.0), dz: float = 0.0,
          wl: float = 850.0, exposure: float | None = None, number: int = 0, **kwargs: Any) -> np.ndarray:
    """A frame with the view centred on wafer ``at`` (mm), ``dz`` mm from best focus."""
    stage = scene.wafer_to_stage(*at)
    z = scene.focus_z(*at, camera) + dz
    expo = scene.exposure_for(wl, camera=camera) if exposure is None else exposure
    return scene.render(camera, exposure_us=expo, stage_xy=stage, z_mm=z, wavelength_nm=wl, number=number,
                        **kwargs)


def bright_disc(img: np.ndarray, level: float) -> tuple[float, float, float]:
    """Centroid (x, y) and equivalent diameter (px) of the pixels above ``level``."""
    ys, xs = np.nonzero(img > level)
    return float(xs.mean()), float(ys.mean()), 2.0 * math.sqrt(xs.size / math.pi)


# ================================================================================================== rendering
def test_frames_are_mono8_deterministic_and_noisy_per_frame(scene: Scene):
    a, b = frame(scene, number=3), frame(scene, number=3)
    assert a.dtype == np.uint8 and a.shape == (512, 612)
    assert np.array_equal(a, b)
    again = Scene(SceneParams(tilt_deg=0.0), settings=settings(), clock=lambda: 1000.0)
    assert np.array_equal(frame(again, number=3), a)                       # same seed, same frame
    other = frame(scene, number=4)
    assert not np.array_equal(a, other) and abs(float(a.mean()) - float(other.mean())) < 0.5
    clean = frame(scene, noise=False)
    assert 1.0 < float(np.std(a.astype(float) - clean)) < 5.0


def test_a_mesa_sits_on_the_grid_with_its_aperture(scene: Scene):
    img = frame(scene, noise=False).astype(float)
    ring = img[256, 306 + round((scene.aperture_um() / 2 + 1.5) / 0.68)]   # the oxidised ring around it
    x, y, diameter = bright_disc(img, 0.5 * (img.max() + ring))            # the non-oxidised aperture
    assert (x, y) == pytest.approx((306.0, 256.0), abs=0.5)                # a mesa at the wafer centre
    assert diameter * PIXEL_UM["highmag"] == pytest.approx(scene.aperture_um(), abs=0.7)
    pitch = scene.params.mesa_pitch_um / 1000.0
    assert scene.mesa_near(0.26, -0.24) == pytest.approx((pitch, -pitch))
    assert scene.mesa_near(-0.5, 0.0) is None                              # cleared around the cross


def test_the_view_follows_the_stage_as_the_tracking_expects(scene: Scene):
    img = frame(scene, at=(0.010, -0.006), noise=False).astype(float)       # the camera 10 µm right, 6 µm down
    x, y, _ = bright_disc(img, 0.5 * (img.max() + np.median(img)))
    assert x == pytest.approx(306.0 - 10.0 / 0.68, abs=0.5) and y == pytest.approx(256.0 - 6.0 / 0.68, abs=0.5)
    move = tracking_move((x, y), (612, 512), pixel_size=0.68, pixel_unit="µm", min_distance_mm=0.0,
                         coordinate_system=0)
    sx, sy = scene.wafer_to_stage(0.010, -0.006)
    assert scene.stage_to_wafer(sx + move.move_mm[0], sy + move.move_mm[1]) == pytest.approx((0, 0), abs=5e-4)


@pytest.mark.parametrize("system", [0, 1, 2, 3])
def test_stage_and_wafer_coordinates_are_inverse(system: int):
    s = Scene(settings=settings(**{"hardware.xy.stagescoordsys": system}))
    assert s.stage_to_wafer(*s.wafer_to_stage(3.0, -4.0)) == pytest.approx((3.0, -4.0))
    assert s.wafer_to_stage(0.0, 0.0) == (100.0, 100.0)


def test_the_aperture_shrinks_at_the_oxidation_rate():
    now = [0.0]
    s = Scene(SceneParams(aperture_start_um=24.0, oxidation_rate_um_min=0.5, oxidation_start_s=100.0),
              clock=lambda: now[0])
    assert s.aperture_um(50.0) == 24.0                                     # before the start
    assert s.aperture_um(100.0 + 600.0) == pytest.approx(19.0)             # 10 min at 0.5 µm/min
    assert s.aperture_um(100.0 + 3600.0 * 2) == 0.0                        # fully oxidised
    now[0] = 160.0
    assert s.aperture_um() == pytest.approx(23.5)


def test_focus_peaks_at_the_surface_height_with_bowing_and_chuck(scene: Scene):
    roi = fin_roi(612, 512, 200, 200)
    for at in ((0.0, 0.0), (-21.2, 21.2), (40.0, 0.0)):                    # centre, wafer, chuck
        scores = {k: measure(frame(scene, at=at, dz=k * 0.002, wl=0.0), "Sobel", roi, 0.5)[1] for k in range(-4, 5)}
        assert max(scores, key=scores.__getitem__) == 0, (at, scores)
        assert scores[0] > 1.5 * scores[4] and scores[0] > 1.5 * scores[-4]
    p = scene.params
    r = scene.wafer_radius_mm()
    assert scene.focus_z(0.0, 0.0) == pytest.approx(p.z_focus_mm)
    edge = scene.wafer_focus_z(r, 0.0) - (p.z_focus_mm + p.wedge_x_um_mm * r / 1000.0)
    assert edge * 1000.0 == pytest.approx(p.bowing_um)
    assert scene.focus_z(40.0, 0.0) == pytest.approx(p.z_focus_mm + p.chuck_dz_mm)
    assert not scene.on_wafer(0.0, -37.0) and scene.on_wafer(0.0, -36.0)    # the flat


def test_grey_levels_follow_exposure_gain_and_saturate(scene: Scene):
    base = float(frame(scene, exposure=4000.0, noise=False).mean())
    double = float(frame(scene, exposure=8000.0, noise=False).mean())
    dark = scene.params.dark_level
    assert (double - dark) == pytest.approx(2.0 * (base - dark), rel=0.02)
    assert float(frame(scene, exposure=4000.0, gain=2.0, noise=False).mean()) == pytest.approx(double, rel=0.02)
    assert np.array_equal(frame(scene, gain=0.0, noise=False), frame(scene, gain=1.0, noise=False))   # ≥ 1
    assert frame(scene, exposure=80000.0).max() == 255


def test_the_oxide_contrast_peaks_at_its_wavelength(scene: Scene):
    def contrast(wl: float) -> float:
        img = frame(scene, wl=wl, noise=False).astype(float)
        aperture = img[256, 306]
        ring = img[256, 306 + round((scene.aperture_um() / 2 + 1.5) / 0.68)]   # inside the oxidised ring
        return (aperture - ring) / aperture

    values = {wl: contrast(wl) for wl in (750.0, 800.0, 850.0, 900.0, 950.0)}
    assert max(values, key=values.__getitem__) == 850.0
    assert values[850.0] > 2 * values[750.0]
    assert scene.oxide_contrast(0.0) == scene.params.contrast_white          # zero order: white light


def test_the_led_lights_the_low_mag_view_only(scene: Scene):
    assert float(frame(scene, "lowmag", led=True).mean()) > 1.5 * float(frame(scene, "lowmag", led=False).mean())
    assert np.array_equal(frame(scene, led=True), frame(scene, led=False))
    scene.on_event(type("E", (), {"topic": "device.highmag.led", "data": {"on": True}})())
    assert scene.led is True


def test_marks_and_tilt(scene: Scene):
    tilted = Scene(SceneParams(tilt_deg=2.0, marks=(Mark("cross", 0.0, 0.0, 300.0, 40.0),)), settings=settings())
    img = frame(tilted, "lowmag", wl=850.0, led=True, noise=False).astype(float)
    row = img[256, :]
    assert row.max() == pytest.approx(row[306], abs=1.0)                    # the bright cross on the centre
    bright = img > 0.5 * (img.max() + np.median(img))
    columns = [c for c in range(306 - 50, 306 + 51) if abs(c - 306) > 20]   # the horizontal arm, rotated by 2°
    centres = [float(np.nonzero(bright[236:277, c])[0].mean()) + 236 for c in columns]
    slope = np.polyfit(columns, centres, 1)[0]
    assert math.degrees(math.atan(-slope)) == pytest.approx(2.0, abs=0.2)
    model = scene.model_image(0)
    assert model.shape[0] % 2 == 1 and model.shape[1] % 2 == 1 and model.max() > 200


def test_params_from_the_settings():
    ini = TypedIni.load(DEMO_FOLDER / "alox_default.ini")
    p = SceneParams.from_settings(ini, seed=3)
    assert p.seed == 3 and p.image_width == 612 and p.mesa_diameter_um == 30.0
    assert p.marks == (Mark("cross", -0.5, 0.0), Mark("corner", 0.5, 0.0))
    assert SceneParams.from_settings(None) == SceneParams()


# ================================================================================================== cameras
def test_the_simulated_camera_takes_its_pixels_from_the_frame_source(scene: Scene):
    backend = SceneCameraBackend(scene, {"HM": "highmag"})
    device = backend.open_camera("HM")
    try:
        device.configure(True, 5)
        device.start()
        device.write_attribute(SIM_EXPOSURE, ValueType.DBL, 5000.0)
        pixels = device.emit_frame().pixels
        assert pixels.shape == (512, 612) and frame_number_of(pixels) >= 0
        expected = scene.render("highmag", exposure_us=5000.0, number=frame_number_of(pixels))
        assert np.array_equal(pixels[1:], expected[1:])
    finally:
        backend.close()


def test_the_default_simulated_camera_is_unchanged():
    backend = SimCameraBackend(SimulationOptions(image_width=64, image_height=8), manual=True)
    device = backend.open_camera("cam")
    device.configure(True, 2)
    device.start()
    pixels = device.emit_frame().pixels
    assert frame_number_of(pixels) == 0 and pixels[1, -1] == 255 and pixels[1, 0] == 0
    backend.close()


def test_make_demo_writes_the_settings_and_the_profile(tmp_path):
    setup = make_demo(tmp_path)
    assert setup.is_file() and (tmp_path / "alox_default.ini").is_file()
    with zipfile.ZipFile(tmp_path / "profiles" / "demo.zip") as archive:
        names = set(archive.namelist())
    assert {"demo.ini", "cross.png", "corner.png"} <= names
