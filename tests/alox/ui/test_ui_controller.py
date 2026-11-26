"""The operator window's logic (``alox.ui.controller``, ``alox.ui.adapters``) on a booted core, without Qt."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from alox.api.backend import Measure
from alox.core.core import Core, MeasureRequest
from alox.core.functions import FunctionName, FunctionStatus
from alox.core.users import UserType
from alox.events import Event
from alox.ui.adapters import (
    build_params,
    image_key,
    params_class,
    stage_to_wafer,
    wafer_to_stage,
)
from alox.ui.controller import ROWS, STATUS_COLOURS, Controller, Tab, look_of


@pytest.fixture
def register(fakes: SimpleNamespace) -> Callable[..., dict[FunctionName, Any]]:
    """``register(core, *names, block=True)``: register a :class:`Probe` for each name."""
    def add(core: Core, *names: FunctionName, block: bool = True) -> dict[FunctionName, Any]:
        probes = {name: fakes.Probe(name, block=block) for name in names}
        for probe in probes.values():
            core.functions.register(probe)
        return probes
    return add


@pytest.fixture
def wait_until(fakes: SimpleNamespace) -> Callable[..., None]:
    return fakes.wait_until


# ============================================================================================== panel and looks
def test_rows_are_those_of_the_front_panel():
    keys = [button.key for row in ROWS for button in row.starts]
    assert keys == ["GET_IMAGE_RUN_BTN", "GET_IMAGE_START_BTN", "SEARCH_OBJ_RUN_BTN", "AF_RUN_BTN", "AF_OXYD_RUN_BTN",
                    "AF_FINE_RUN_BTN", "DET_CIRCLE_RUN_BTN", "TRACK_RUN_BTN", "AUTO_EXPO_RUN_BTN",
                    "AUTO_EXPO_RUN_AUTO_BTN", "WL_RUN_BTN", "WL_RUN_AUTO_BTN", "MES_VCSEL_RUN_BTN", "MES_BOW_RUN_BTN",
                    "MES_TILT_RUN_BTN", "MES_MESA_RUN_SINGLE_BTN", "MES_MESA_RUN_CONT_BTN"]
    assert {row.function for row in ROWS} == set(FunctionName) - {FunctionName.REFOCUS_OXYD}
    mesa = Controller.row("MES_MESA")
    single, cont = mesa.starts
    assert single.overrides == {"tilt": False, "positionsctrl": False, "analysis": False, "singleshot": True}
    assert cont.overrides["singleshot"] is False and single.measure is Measure.MESA


def test_status_looks_use_labview_colours_and_texts(core: Core, register: Callable[..., Any]):
    assert STATUS_COLOURS[FunctionStatus.OFF] == "#ff9900" and STATUS_COLOURS[FunctionStatus.OFF_COND_OK] is None
    controller = Controller(core)
    af = Controller.row("AF")
    assert controller.look(af).status is FunctionStatus.OFF                       # not registered
    register(core, FunctionName.AUTOFOCUS_LARGE, block=False)
    look = controller.look(af)
    assert (look.text, look.background, look.foreground) == ("OFF OK", None, "#000000")
    assert look_of(FunctionStatus.FAILED).text == "ERROR"                           # alox_en.lang 000043
    assert look_of(FunctionStatus.RUNNING, lambda _i: None) == look_of(FunctionStatus.RUNNING)
    assert look_of(FunctionStatus.FINISHED).background == "#66ff00"


def test_start_buttons_follow_rights_registration_and_condition(core: Core, register: Callable[..., Any],
                                                                wait_until: Callable[..., None]):
    probes = register(core, FunctionName.AUTOFOCUS_LARGE, FunctionName.REFOCUS)
    c = Controller(core)
    assert c.user is UserType.OPERATOR                                              # owner decision B6
    assert c.start_enabled("AF_RUN_BTN")
    assert not c.start_enabled("AF_FINE_RUN_BTN")                                   # no "refocus" right
    assert not c.start_enabled("DET_CIRCLE_RUN_BTN")                                # right, but not registered
    assert not c.options_enabled(Controller.row("AF")) and c.options_enabled(Controller.row("GET_IMAGE"))
    assert not c.start_enabled("GET_IMAGE_START_BTN")                               # no live display
    core.functions.start(FunctionName.AUTOFOCUS_LARGE)
    assert not c.start_enabled("AF_RUN_BTN")                                        # Get Condition OK FALSE
    assert c.stop_enabled(Controller.row("AF"))
    probes[FunctionName.AUTOFOCUS_LARGE].release.set()
    wait_until(lambda: core.functions.running is None)
    assert c.start_enabled("AF_RUN_BTN")


# ============================================================================================== starts and stops
def test_low_level_start_bundles_the_profile_parameters(core: Core, devices: dict[str, Any],
                                                        register: Callable[..., Any], fakes: SimpleNamespace):
    probe = register(core, FunctionName.AUTOFOCUS_LARGE)[FunctionName.AUTOFOCUS_LARGE]
    core.profile_manager.set("probe.value", 7.25)
    core.properties.set("general.camera", 0)
    c = Controller(core)
    result = c.press("AF_RUN_BTN")
    assert result.ok and result.tab is Tab.LOW_LEVEL
    assert probe.started.wait(5)
    assert probe.seen == [fakes.ProbeParams(camera_type=1, method=0, value=7.25)]        # Low Mag. over the profile
    assert core.properties.get("general.camera") == 1                               # "Set default camera" Low Mag.
    assert devices["z"].joystick is False and devices["xy"].joystick is True        # Z joystick off only
    assert any(line.text == "Autofocus (start clicked)" for line in core.log.lines())
    assert c.look(Controller.row("AF")).text == "RUNNING"
    again = c.press("AF_RUN_BTN")
    assert not again.ok and "running" in again.message
    stopped = c.stop("AF")
    assert stopped.ok and core.functions.running is None
    assert c.status(Controller.row("AF")) is FunctionStatus.OFF_COND_OK


def test_refused_without_the_right(core: Core, register: Callable[..., Any]):
    probe = register(core, FunctionName.REFOCUS)[FunctionName.REFOCUS]
    c = Controller(core)
    result = c.press("AF_FINE_RUN_BTN")
    assert not result.ok and "Operator" in result.message and not probe.seen
    assert not c.stop("AF_FINE").ok
    core.users.change_password(UserType.ENGINEER, "test-only")
    assert not c.login(UserType.ENGINEER, "wrong").ok and c.user is UserType.OPERATOR
    assert c.login("engineer", "test-only").ok and c.user is UserType.ENGINEER
    assert c.press("AF_FINE_RUN_BTN").ok
    assert probe.started.wait(5) and probe.seen[0].camera_type == 0                 # High Mag.
    c.stop("AF_FINE")
    assert c.logout().ok and c.user is UserType.OPERATOR


def test_refocus_low_mag_uses_sobel_and_the_refocus_oxyd_right(core: Core, register: Callable[..., Any]):
    core.properties.set("user.rights.operator.refocusoxyd", True)
    probe = register(core, FunctionName.REFOCUS)[FunctionName.REFOCUS]
    c = Controller(core)
    assert c.press("AF_OXYD_RUN_BTN").ok and probe.started.wait(5)
    assert (probe.seen[0].camera_type, probe.seen[0].method) == (1, 1)
    c.stop("AF_OXYD")


def test_high_level_start_goes_through_the_command_bus(core: Core, devices: dict[str, Any],
                                                       register: Callable[..., Any],
                                                       wait_until: Callable[..., None]):
    probes = register(core, FunctionName.MESA_MEASURE, FunctionName.TILT_MEASURE)
    c = Controller(core)
    result = c.press("MES_MESA_RUN_SINGLE_BTN")
    assert result.ok and result.tab is Tab.HIGH_LEVEL and isinstance(result.data, MeasureRequest)
    mesa = probes[FunctionName.MESA_MEASURE]
    assert mesa.started.wait(5)
    request = mesa.seen[0]
    assert request.measure is Measure.MESA and request.profile == "DEFAULT"
    assert dict(request.overrides) == {"tilt": False, "singleshot": True, "positionsctrl": False, "analysis": False}
    assert devices["xy"].joystick is False and devices["z"].joystick is False
    wait_until(lambda: core.system.code == 203)                                       # Mesa running
    assert core.measure_status(Measure.MESA)[0] == 200
    mesa.release.set()
    wait_until(lambda: core.functions.running is None)
    assert c.press("MES_TILT_RUN_BTN").ok
    tilt = probes[FunctionName.TILT_MEASURE]
    assert tilt.started.wait(5)
    x, y = stage_to_wafer(9.0, 18.0, (10.0, 20.0), 3)                                 # current XY in wafer coords
    assert dict(tilt.seen[0].overrides) == {"x": x, "y": y} == {"x": 1.0, "y": 2.0}
    assert core.properties.get("general.camera") == 1
    tilt.release.set()


def test_get_image_run_posts_the_command_with_the_selected_camera(core: Core, register: Callable[..., Any]):
    probe = register(core, FunctionName.GET_IMAGE, block=False)[FunctionName.GET_IMAGE]
    c = Controller(core)
    assert c.select_camera(0).ok and c.selected_camera() == 0
    assert c.press("GET_IMAGE_RUN_BTN").ok
    assert probe.started.wait(5) and probe.seen == [0]
    assert not c.press("GET_IMAGE_START_BTN").ok                                    # no live display


def test_get_image_start_and_stop_drive_the_live_display(core: Core, register: Callable[..., Any]):
    class Live:
        running = False

        def start(self) -> None:
            self.running = True

        def stop(self) -> None:
            self.running = False

    register(core, FunctionName.GET_IMAGE, block=False)
    live = Live()
    c = Controller(core, live=live)
    row = Controller.row("GET_IMAGE")
    assert c.start_enabled("GET_IMAGE_START_BTN") and c.look(row).text == "OFF OK"
    assert c.press("GET_IMAGE_START_BTN").ok and live.running
    assert c.look(row).text == "RUNNING" and not c.start_enabled("GET_IMAGE_START_BTN")
    assert c.stop("GET_IMAGE").ok and not live.running


def test_options_settings_profiles_and_exit(core: Core, wait_until: Callable[..., None]):
    c = Controller(core)
    assert not c.open_options("AF").ok                                               # no "autofocus setup" right
    result = c.open_options("GET_IMAGE")
    assert result.ok and result.tab is Tab.SETTINGS and result.data == "Get Image"
    assert c.open_settings().tab is Tab.SETTINGS
    assert c.profiles() == ["DEFAULT"] and c.current_profile() == "DEFAULT"
    assert c.load_profile("DEFAULT").ok
    assert not c.save_profile().ok and not c.edit_profiles().ok                      # no "edit profile" right
    asked: list[bool] = []
    core.on_shutdown = lambda: asked.append(True)
    assert c.request_exit().ok
    wait_until(lambda: asked == [True])
    assert "PROFILE DEFAULT" in c.title() and "USER Operator" in c.title()
    assert c.core_infos() == ("CORE #1", "LOCAL")


# ============================================================================================== devices
def test_moves_wavelength_joysticks_and_refresh(core: Core, devices: dict[str, Any]):
    ticks = iter([10.0, 10.25, 11.0])
    c = Controller(core, clock=lambda: next(ticks))
    assert c.wafer_position() == (1.0, 2.0)
    assert c.move("x", 3.0, 4.0).ok and c.move("y", 3.0, 4.0).ok
    assert devices["xy"].moves == [{"args": (), "x": 7.0, "wait": False, "timeout": c.DEVICE_TIMEOUT_S},
                                   {"args": (), "y": 16.0, "wait": False, "timeout": c.DEVICE_TIMEOUT_S}]
    assert c.set_wavelength(912.4).ok and devices["wl"].targets == [912]
    assert c.set_joystick("z", False).ok and devices["z"].joystick is False
    first, second = c.poll(), c.poll()
    assert (first.xy_joystick, first.z_joystick, first.wavelength_nm, first.joystick_ok) == (True, False, 912, True)
    assert first.period_ms == 0.0 and second.period_ms == pytest.approx(250.0)
    devices["xy"].state = "fault"
    assert not c.move("x", 0.0, 0.0).ok and c.poll().xy_joystick is None


def test_joystick_refused_while_a_high_level_function_runs(core: Core, devices: dict[str, Any],
                                                           register: Callable[..., Any]):
    probe = register(core, FunctionName.BOWING_MEASURE)[FunctionName.BOWING_MEASURE]
    c = Controller(core)
    core.functions.start(FunctionName.BOWING_MEASURE)
    assert not c.set_joystick("xy", True).ok and not c.poll().joystick_ok
    probe.release.set()


# ============================================================================================== events
def test_handle_events():
    c = Controller.__new__(Controller)
    Controller.__init__(c, core=None)                                                # type: ignore[arg-type]
    assert c.handle(Event("status.system", "core", {"code": 500})) == {"system", "functions"}
    assert c.handle(Event("status.mesa", "core", {"code": 200})) == {"measures"}
    assert c.handle(Event("function.autofocus_large.started", "core")) == {"functions"}
    assert c.take_tab() is None
    assert c.handle(Event("function.mesa_measure.started", "core")) == {"functions", "tab"}
    assert c.take_tab() is Tab.HIGH_LEVEL and c.take_tab() is None
    assert "alarm" in c.handle(Event("function.refocus.failed", "core", {"error": "x"}))
    assert c.handle(Event("core.profile", "core", {"profile": "P"})) == {"profile", "title"}
    assert c.handle(Event("core.show_ui", "core", {"visible": False})) == {"visible"} and c.visible_request is False
    assert c.handle(Event("device.xy.state", "xy", {"state": "fault"})) == {"devices", "functions"}


# ============================================================================================== adapters
@pytest.mark.parametrize("system", [0, 1, 2, 3])
def test_wafer_and_stage_coordinates_are_inverse(system: int):
    origin = (101.5, 52.25)
    for point in ((0.0, 0.0), (12.5, -3.0), (-40.0, 7.75)):
        wafer = stage_to_wafer(*point, origin, system)
        assert wafer_to_stage(*wafer, origin, system) == pytest.approx(point)
    assert stage_to_wafer(100.0, 50.0, origin, 3) == (1.5, 2.25)


def test_build_params_from_profile_defaults_and_overrides(fakes: SimpleNamespace):
    warnings: list[str] = []

    class Profile:
        def get(self, key: str, default: Any = None) -> Any:
            return {"probe.value": 3.0}.get(key, default)

    probe = fakes.Probe(FunctionName.TRACKING)
    assert params_class(probe) is fakes.ProbeParams
    built = build_params(probe, Profile(), {"method": 2, "nope": 1}, warn=warnings.append)
    assert built == fakes.ProbeParams(0, 2, 3.0)
    assert warnings and "nope" in warnings[0]
    assert build_params(probe, None) == fakes.ProbeParams()

    from alox.functions.focus import Autofocus, AutofocusParams, RefocusParams

    assert params_class(Autofocus.__new__(Autofocus)) is AutofocusParams
    built = build_params(Autofocus.__new__(Autofocus), Profile(), {"camera_type": 1})
    assert built == dataclasses.replace(AutofocusParams.from_profile(Profile()), camera_type=1)   # the recipe
    assert RefocusParams(method=1).method.label == "Sobel"

    class Bare:
        pass
    assert params_class(Bare()) is None and build_params(Bare(), Profile(), {"x": 1}, warn=warnings.append) is None


def test_image_key_follows_the_image_store():
    pytest.importorskip("PIL")
    from alox.core.images import ImageStore
    from alox.functions.services import Circle

    store = ImageStore()
    try:
        assert image_key(store) is None and image_key(None) is None
        store.put("highmag", np.zeros((8, 10), np.uint8))
        first = image_key(store)
        assert first is not None and image_key(store) == first
        store.set_overlay("detectcircle", [Circle(4, 4, 2)])
        second = image_key(store)
        assert second != first
        store.put("highmag", np.ones((8, 10), np.uint8))
        assert image_key(store) not in (first, second)
    finally:
        store.close()
