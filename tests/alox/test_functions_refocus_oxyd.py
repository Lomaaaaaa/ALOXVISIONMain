"""Refocus Oxyd as a measurement function (``alox.functions.refocus_oxyd``).

The Z stage is the real :class:`ZStage` on ``SimStage`` with a fake clock (as ``test_functions_focus.py``). The camera
double renders a random pattern whose square around the mesa is sharpest at ``Z_OXYD`` and the rest of the image at
``Z_OTHER``: the refocus finds ``Z_OXYD`` only if the metric runs on the Oxyd ROI.
"""

from __future__ import annotations

import struct
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from alox.core.functions import FunctionContext, FunctionName, FunctionRunner, Stopped
from alox.devices.camera import CameraImage
from alox.devices.zstage import ZStage
from alox.events import Event
from alox.functions.detect_circle import FunctionError
from alox.functions.refocus_oxyd import (
    RefocusOxyd,
    RefocusOxydParams,
    RefocusOxydReport,
    oxyd_roi,
    refocus_filter,
)
from alox.functions.services import Circle as CircleShape
from alox.functions.services import Services, Text
from alox.vision.circles import Circle
from aloxcam.imaging.png import read_png
from aloxcam.settings import SimulationOptions, StageOptions, ZStageSettings
from aloxcam.zstage.sim import SimStage

Z_OXYD, Z_OTHER = 1.20, 1.70
SHAPE = (96, 128)
CIRCLE = Circle(70.0, 48.0, 20.0)
ROI_UM = 30                                     # 1 µm per pixel: the ROI square is [55, 33, 85, 63]


class Store(dict):
    def get(self, key: str, default: Any = None) -> Any:          # type: ignore[override]
        return super().get(key, default)


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0
        self._lock = threading.Lock()

    def monotonic(self) -> float:
        with self._lock:
            return self.now

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self.now += max(0.0, seconds)


class TwoFocusCamera:
    """``grab`` renders the pattern blurred by σ = 100·|Z − Z_focus| px, Z_focus = Z_OXYD in the ROI square."""

    name = "highmag"

    def __init__(self, sim: SimStage, clock: FakeTime) -> None:
        self.sim, self.clock = sim, clock
        self.spectrum = np.fft.fft2(np.random.default_rng(5).uniform(0, 255, SHAPE))
        fy, fx = np.fft.fftfreq(SHAPE[0])[:, None], np.fft.fftfreq(SHAPE[1])[None, :]
        self.f2 = fx ** 2 + fy ** 2
        self.mask = np.zeros(SHAPE, dtype=bool)
        self.mask[33:63, 55:85] = True
        self.grabs = 0

    def render(self, z: float, z_focus: float) -> np.ndarray:
        sigma = 100.0 * abs(z - z_focus)
        return np.fft.ifft2(self.spectrum * np.exp(-2 * np.pi ** 2 * sigma ** 2 * self.f2)).real

    def grab(self, *, newest: bool = True, timeout_ms: int | None = None) -> CameraImage:
        self.grabs += 1
        self.clock.sleep(0.01)
        z = self.sim.position
        img = np.where(self.mask, self.render(z, Z_OXYD), self.render(z, Z_OTHER))
        pixels = np.clip(np.rint(img), 0, 255).astype(np.uint8)
        return CameraImage(pixels, self.grabs, self.clock.monotonic(), self.name, 1000.0, 0.0, False)


class FakeFolders:
    def __init__(self, root: Path) -> None:
        self.root = root

    def image_folder(self, name: str) -> Path:
        return self.root / "images" / name

    def file_folder(self, name: str) -> Path:
        return self.root / "files" / name


@pytest.fixture
def rig() -> Iterator[Any]:
    clock = FakeTime()
    sim = SimStage(SimulationOptions(), clock=clock.monotonic, dh_running=False)
    z = ZStage(sim, ZStageSettings(min_pos=0.0, max_pos=20.0), StageOptions(), sleep=clock.sleep,
               monotonic=clock.monotonic, reconnect_delay_s=0.0)
    z.open(timeout=10)
    events: list[Event] = []
    camera = TwoFocusCamera(sim, clock)
    settings = Store({"hardware.camera.highmag.pixelsize": 1.0, "hardware.camera.highmag.units": 1})
    services = Services(devices={"highmag": camera, "z": z}, settings=settings, clock=clock.monotonic,
                        sleep=clock.sleep)
    runner = FunctionRunner(publish=events.append)

    class Rig:
        def __init__(self) -> None:
            self.z, self.sim, self.camera, self.events, self.services = z, sim, camera, events, services
            self.function = RefocusOxyd(services)

        def ctx(self) -> FunctionContext:
            return FunctionContext(FunctionName.REFOCUS_OXYD, None, runner)

        def run(self, start_z: float = Z_OXYD - 0.01, **params: Any) -> RefocusOxydReport:
            self.z.move_absolute(start_z, wait=True)
            values: dict[str, Any] = {"method": "Sobel", "steps": 4, "step_mm": 0.01, "roi_diameter_um": ROI_UM,
                                      "circle": CIRCLE}
            values.update(params)
            return self.function.run(RefocusOxydParams(**values), self.ctx())

        def states(self) -> list[str]:
            return [e.data["state"] for e in events if e.topic == "function.refocus_oxyd.state"]

    yield Rig()
    z.shutdown(timeout=5)


def test_refocus_scores_the_oxyd_roi_and_moves_to_the_best_z(rig):
    report = rig.run(overlay_position=True, overlay_score=True, overlay_roi=True)
    assert len(report.zs) == len(report.scores) == 9
    assert report.zs[0] == pytest.approx(Z_OXYD - 0.05)
    assert report.best_index == 5 and report.best_z == pytest.approx(Z_OXYD)
    assert report.best_score == max(report.scores) and report.valid and report.progress == 100.0
    assert report.roi == (55, 33, 85, 63) and report.roi_diameter_px == 30
    rig.z.wait_end_move()
    assert rig.z.position() == pytest.approx(Z_OXYD, abs=1e-3)
    states = rig.states()
    assert [x for i, x in enumerate(states) if i == 0 or x != states[i - 1]] == ["Start", "Started", "Measure",
                                                                                 "Finished"]
    inner = [e.data["refocus_state"] for e in rig.events if "refocus_state" in e.data]
    assert inner[0] == "Start" and inner.count("Mesure Position") == 9 and inner[-1] == "Finished"
    shapes = rig.services.images.overlays["refocusoxyd"]
    assert isinstance(shapes[0], Text) and shapes[0].text == f"Autofocus Pos. (mm): {Z_OXYD:.3f}"
    assert shapes[1].text.startswith("Autofocus Score (mm): ")
    assert shapes[2] == CircleShape(70.0, 48.0, 15.0, (255, 0, 255), 5)
    assert rig.services.images.last("highmag") is not None              # new image of Finished
    assert rig.function.last_report is report


def test_best_position_at_the_scan_edges_is_an_error(rig):
    with pytest.raises(FunctionError) as info:
        rig.run(start_z=Z_OXYD - 0.10)                                   # scan 1.05 … 1.13: best is the last point
    assert info.value.code == 9035 and info.value.state == "Measure"
    report = info.value.report
    assert not report.valid and report.best_index == 8
    assert rig.states()[-1] == "Error"


def test_input_and_device_errors(rig):
    with pytest.raises(FunctionError) as info:
        rig.run(circle=Circle(0, 0, 0))
    assert info.value.code == 9037 and info.value.state == "Start"
    rig.z.set_joystick(True)
    with pytest.raises(FunctionError) as info:
        rig.function.run(RefocusOxydParams(circle=CIRCLE), rig.ctx())
    assert info.value.code == 9036 and info.value.state == "Started"
    rig.z.set_joystick(False)
    rig.services.settings["hardware.camera.highmag.units"] = 0
    with pytest.raises(FunctionError) as info:
        rig.run()
    assert info.value.code == 9080 and info.value.state == "Measure"
    del rig.services.devices["z"]
    with pytest.raises(FunctionError) as info:
        rig.function.run(RefocusOxydParams(circle=CIRCLE), rig.ctx())
    assert info.value.code == 9030


def test_saved_data_and_images(rig, tmp_path):
    rig.services.folders = FakeFolders(tmp_path)
    rig.services.settings.update({"log.saveautofocusdata": True, "log.saverefocusimages": True})
    report = rig.run()
    (focf,) = (tmp_path / "files" / "refocus").glob("*.focf")
    raw = focf.read_bytes()
    count = struct.unpack(">i", raw[:4])[0]
    values = struct.unpack(f">{2 * count}d", raw[4:])
    assert count == 9 and values[0::2] == pytest.approx(report.zs) and values[1::2] == pytest.approx(report.scores)
    lines = focf.with_suffix(".focfraw").read_text().splitlines()
    assert lines[0] == f"{report.zs[0]:.3f}\t{report.scores[0]:.3f}"
    pngs = sorted((tmp_path / "images" / "Misc" / "refocus").glob("*.png"))
    assert len(pngs) == 9 and read_png(pngs[0]).shape == SHAPE
    assert report.images and len(report.images) == 9


def test_stop_before_start(rig):
    ctx = rig.ctx()
    ctx.stop_event.set()
    with pytest.raises(Stopped):
        rig.function.run(RefocusOxydParams(circle=CIRCLE), ctx)
    assert rig.states() == ["Start", "Stopped"]


# ================================================================================================== helpers
SCORES = [6.66758e6, 6.62449e6, 6.94874e6, 7.244e6, 7.57286e6, 7.89416e6, 8.27311e6, 8.69038e6, 9.12995e6,
          9.47796e6, 9.68564e6, 9.63979e6, 9.22146e6, 8.66823e6, 7.96451e6, 7.4536e6, 7.0042e6, 6.67484e6,
          6.40988e6, 6.25672e6, 6.07518e6]
ZS = [36.6245 + 0.003 * i for i in range(21)]


def test_refocus_filter_reproduces_the_vi_front_panel():
    """``alox_function_II_refocus_filter p``: No filter FALSE → Index Pos 11, Focus Pos 36.6575, Max Score 9.65507E+6
    (the smoothing ``(s[i−1] + 2 s[i])/3``)."""
    index, z, score = refocus_filter(ZS, SCORES, no_filter=False)
    assert index == 11 and z == pytest.approx(36.6575) and score == pytest.approx(9.65507e6, rel=1e-5)
    assert refocus_filter(ZS, SCORES) == (10, pytest.approx(36.6545), 9.68564e6)       # No filter (LabVIEW caller)


def test_refocus_filter_other_cases():
    zs = [float(i) for i in range(9)]
    two_peaks = [1, 2, 3, 9, 4, 5, 8.5, 3, 2]
    assert refocus_filter(zs, two_peaks, no_filter=False) == (4, 4.0, 4)           # round(mean(3, 6)) = 4 (even)
    edge = [9, 8, 1, 1, 1, 1, 1, 1, 1]
    assert refocus_filter(zs, edge, no_filter=False) == (0, 0.0, 9)                 # at an end: unfiltered
    assert refocus_filter([], [], no_filter=False)[0] == 0


def test_oxyd_roi():
    assert oxyd_roi(128, 96, CIRCLE, 30) == (55, 33, 85, 63)
    assert oxyd_roi(2448, 2048, Circle(0, 0, 30), 256) == (1096, 896, 1352, 1152)     # no circle: image centre


def test_profile_keys_and_defaults():
    p = RefocusOxydParams.from_profile(Store({"refocus.oxyd.nbsteps": 6, "refocus.oxyd.step": "0,02",
                                              "refocus.oxyd.method": 1}), circle=(10, 20, 5))
    assert (p.steps, p.step_mm, p.method.label, p.circle) == (6, 0.02, "Sobel", Circle(10, 20, 5))
    d = RefocusOxydParams.from_profile(Store())
    assert (d.camera_type.label, d.method.label, d.steps, d.step_mm, d.roi_diameter_um, d.wait_ms) == (
        "High Mag.", "FFT", 4, 0.05, 40, 0)
    assert not (d.show_ui or d.overlay_score or d.overlay_position or d.overlay_roi or d.filter_scores)
    with pytest.raises(FunctionError) as info:
        RefocusOxydParams(steps=-1)
    assert info.value.code == 9002
