"""End to end in simulation: the whole application (core, devices, real measurement functions) on the simulated wafer
scene of :mod:`alox.devices.scene`, driven as the supervisor drives it (``Method.MEASURE_*`` with a
``core.measure_request``) and as the operator does (the window's start buttons through
:class:`alox.ui.controller.Controller`), checked against what the scene holds (tilt, mesa and aperture diameters,
bowing).

The jobs run in one application (module fixture), in the order of a process: the operator's SINGLESHOT MEASURE on the
mesa under the camera (the local start: no Move Start, no Search Object; the demo starts on the wafer-centre mesa),
the tilt, then a Singleshot mesa with the tilt correction (Move Start, Search Object on the cross, offset to the mesa,
autofocus, refocus, Detect Circle, tracking, Search Wavelength, Measure Oxyd), then the bowing (nine points,
autofocus + refocus each). The recipe is the demo's (``src/alox/devices/scene_demo``). The websocket events are read
on the event bus (``ws.<event>``: what the websocket server sends).

About 65 s (operator mesa 10 s, tilt 6 s, mesa 10 s, bowing 35 s: nine 2 s autofocus scans and refocuses); marked
``slow``. The scene renders frames in real time, so as on the machine the results depend on the frames being fresh: a
heavily loaded computer can make them less accurate.
"""

from __future__ import annotations

import math
import time
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest

from alox.api.backend import Measure
from alox.app import AppConfig, Application
from alox.core.commands import Method
from alox.core.functions import FunctionName, JobInfo
from alox.core.status import MesaStatus
from alox.devices.scene import Scene, make_demo
from alox.ui.controller import Controller
from alox.vision.wafer import ChuckProperties, compute_bowing

pytestmark = pytest.mark.slow


class Jobs:
    """The application and the websocket events of the jobs posted through the core."""

    def __init__(self, app: Application) -> None:
        self.app = app
        self.events = app.events.subscribe(("ws",))

    @property
    def scene(self) -> Scene:
        return self.app.scene

    def run(self, method: Method, measure: Measure, overrides: dict[str, Any], function: str,
            timeout: float = 90.0) -> dict[str, list[dict[str, Any]]]:
        """Post the job, return its websocket events by name once ``finished`` (or ``failed``) arrived."""
        core = self.app.core
        assert core is not None
        self.events.drain()                                           # drop what earlier jobs left
        core.post(method, core.measure_request(measure, JobInfo(wafer_id="SIM-1"), overrides))
        return self.wait(function, timeout)

    def press(self, controller: Controller, key: str, function: str,
              timeout: float = 90.0) -> dict[str, list[dict[str, Any]]]:
        """Click the window's start button ``key`` (as the operator), then as :meth:`run`."""
        self.events.drain()
        result = controller.press(key)
        assert result.ok, result.message
        return self.wait(function, timeout)

    def wait(self, function: str, timeout: float) -> dict[str, list[dict[str, Any]]]:
        seen: dict[str, list[dict[str, Any]]] = {}
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            event = self.events.get(timeout=0.5)
            if event is None:
                continue
            name = event.topic.removeprefix("ws.")
            seen.setdefault(name, []).append(dict(event.data))
            if name in ("finished", "failed") and event.data.get("Function") == function:
                return seen
        raise AssertionError(f"{function} did not finish in {timeout} s: {sorted(seen)}")


@pytest.fixture(scope="module")
def jobs(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Jobs]:
    setup = make_demo(tmp_path_factory.mktemp("alox-sim"))
    config = AppConfig(simulate=True, core=True, setup_ini=setup, api=False)
    with Application(config) as app:
        states = app.start()
        assert states == {"z": "ready", "xy": "ready", "wl": "ready", "highmag": "ready", "lowmag": "ready"}
        assert app.core is not None and app.core.profile_manager.current == "DEMO"
        assert all(outcome == "registered" for outcome in app.functions.values()), app.functions
        bowing = app.core.functions.function(FunctionName.BOWING_MEASURE)
        bowing.options = replace(bowing.options, ui_pause_s=0.0, finished_pause_s=0.0)   # UI pauses: not tested
        yield Jobs(app)


def _f(value: Any) -> float:
    return float(value)


def test_operator_singleshot_measures_the_mesa_under_the_camera(jobs: Jobs):
    """The window's SINGLESHOT MEASURE on the demo as it starts (first test: the stage where the application put it,
    on the wafer-centre mesa; the operator's rights of the demo settings): the local start (no tilt correction, no
    position control) measures the mesa under the camera without Move Start nor Search Object."""
    controller = Controller(jobs.app.core)
    assert controller.wafer_position() == pytest.approx((0.0, 0.0), abs=1e-3)
    seen = jobs.press(controller, "MES_MESA_RUN_SINGLE_BTN", "measure_mesa")
    assert "failed" not in seen and "error" not in seen, seen.get("error")
    codes = {int(e["State"]) for e in seen["mesa_status"]}
    assert MesaStatus.AUTOFOCUS_1 in codes                          # Start → Autofocus Large (Low Mag) on the spot
    assert not codes & {MesaStatus.MOVE_START, MesaStatus.WAIT_MOVE_START, MesaStatus.SEARCH_OBJECT}
    assert MesaStatus.REFOCUS_1 in codes                            # the High Mag refocus follows it (demo recipe)
    event, scene = seen["measure_mesa"][-1], jobs.scene
    assert _f(event["External_Diameter"]["Fitted"]) == pytest.approx(scene.params.mesa_diameter_um, abs=0.6)
    assert _f(event["Oxyd_Diameter"]["Fitted"]) == pytest.approx(scene.aperture_um(_f(event["Timestamp"]["Unix"])),
                                                                 abs=0.4)
    position = (_f(event["Mesa_Positions"]["x"]), _f(event["Mesa_Positions"]["y"]))
    assert math.dist(position, (0.0, 0.0)) < 0.003                 # the mesa at the wafer centre, tracked
    assert seen["finished"][-1]["Job"]["Wafer ID"] == ""            # a local start has no SEMI job


def test_tilt_job_measures_the_wafer_rotation_on_the_flat(jobs: Jobs):
    seen = jobs.run(Method.MEASURE_TILT, Measure.TILT, {}, "measure_tilt")
    assert "failed" not in seen and "error" not in seen, seen.get("error")
    angle = _f(seen["measure_tilt"][-1]["Tilt"]["Angle"])
    assert angle == pytest.approx(jobs.scene.params.tilt_deg, abs=0.03)
    assert jobs.app.core.tilt() == pytest.approx(angle, abs=1e-6)                 # used by the mesa job


def test_singleshot_mesa_job_measures_the_mesa_and_its_aperture(jobs: Jobs):
    seen = jobs.run(Method.MEASURE_MESA, Measure.MESA, {"singleshot": True, "tilt": True}, "measure_mesa")
    assert "failed" not in seen and "error" not in seen, seen.get("error")
    event = seen["measure_mesa"][-1]
    scene = jobs.scene
    assert _f(event["External_Diameter"]["Fitted"]) == pytest.approx(scene.params.mesa_diameter_um, abs=0.6)
    aperture = scene.aperture_um(_f(event["Timestamp"]["Unix"]))
    oxyd = event["Oxyd_Diameter"]
    assert _f(oxyd["Fitted"]) == pytest.approx(aperture, abs=0.4)
    assert _f(oxyd["Inscribed"]) <= _f(oxyd["Fitted"]) <= _f(oxyd["Circumscribed"])
    assert _f(event["Wavelength"]["wl"]) == pytest.approx(scene.params.contrast_peak_nm, abs=10)   # the optimum
    position = (_f(event["Mesa_Positions"]["x"]), _f(event["Mesa_Positions"]["y"]))
    mesa = scene.mesa_near(*position)                                # the cross + the Search Object offset
    assert mesa is not None and math.dist(position, mesa) < 0.003
    assert seen["finished"][-1]["Job"]["Wafer ID"] == "SIM-1"


def test_bowing_job_finds_the_scene_bowing_and_parallelism(jobs: Jobs):
    seen = jobs.run(Method.MEASURE_BOWING, Measure.BOWING, {}, "measure_bowing", timeout=120.0)
    assert "failed" not in seen and "error" not in seen, seen.get("error")
    event = seen["measure_bowing"][-1]
    scene, core = jobs.scene, jobs.app.core
    get = core.properties.get
    distance, angle = float(core.profile_manager.get("measure.bowing.distance")), math.radians(get("general.bowingAngle"))
    radius = get("general.wafersize") / 2.0 + get("general.bowingOffset")
    c, s = math.cos(angle), math.sin(angle)
    points = {"zwafer2": (0.0, 0.0), "zwafer3": (distance * c, distance * s), "zchuck3": (radius * c, radius * s),
              "zwafer1": (-distance * c, -distance * s), "zchuck1": (-radius * c, -radius * s),
              "zwafer4": (distance * c, -distance * s), "zchuck4": (radius * c, -radius * s),
              "zwafer5": (-distance * c, distance * s), "zchuck5": (-radius * c, radius * s)}
    measured = {k.lower(): _f(v) for k, v in {**event["Bowing"], **event["Parallelism"]}.items()
                if k.lower() in points}
    truth = {name: scene.focus_z(*xy) for name, xy in points.items()}
    for name, z in truth.items():
        assert measured[name] == pytest.approx(z, abs=0.0025), name              # µm-level autofocus + refocus
    rows = [[*points[n], truth[n]] for n in ("zwafer2", "zwafer3", "zchuck3", "zwafer1", "zchuck1", "zwafer4",
                                              "zchuck4", "zwafer5", "zchuck5")]
    expected = compute_bowing(rows, measure_distance_mm=distance, max_bowing_um=100.0,
                              chuck=ChuckProperties(wafer_diameter_mm=get("general.wafersize")))
    bowing_edge = scene.params.bowing_um * distance / scene.wafer_radius_mm()     # the paraboloid, linearised
    assert expected.bow_a_um == pytest.approx(bowing_edge, rel=1e-6) == pytest.approx(expected.bow_b_um, rel=1e-6)
    assert _f(event["Bowing"]["bowinga"]) == pytest.approx(expected.bow_a_um, abs=2.5)
    assert _f(event["Bowing"]["bowingb"]) == pytest.approx(expected.bow_b_um, abs=2.5)
    assert _f(event["Parallelism"]["parallelisma"]) == pytest.approx(expected.para_a_um, abs=3.0)
    assert _f(event["Parallelism"]["parallelismb"]) == pytest.approx(expected.para_b_um, abs=3.0)
    assert any(core.folders.data.rglob("*.bow"))                                # the data file of the job
