"""The properties panel ("Vision Core", ``alox.ui.properties_panel`` / ``alox.ui.properties_form``): the description
against the LabVIEW panel, the model on the core's ``alox.ini`` store (rights, apply, save, password), the controller's
actions and the panel in the window (offscreen). Synthetic settings only."""

from __future__ import annotations

import base64
import time
from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from alox.api.backend import Measure
from alox.core.commands import Method
from alox.core.core import Core
from alox.core.functions import FunctionName
from alox.core.users import PropertiesTab, UserAction, UserType
from alox.events import Event
from alox.ui.controller import Controller, Tab
from alox.ui.properties_panel import (
    ADMIN,
    HARDWARE_PAGES,
    PAGES,
    PANEL_NAME,
    RIGHT_TEXTS,
    TOP_PAGES,
    PropertiesModel,
)
from alox.ui.settings_panels import Field, Kind, colour, text

ALL_TABS = tuple(PropertiesTab)


def grant(core: Core, *tabs: PropertiesTab, user: str = "operator", on: bool = True) -> None:
    for tab in tabs:
        core.properties.set(f"user.rights.{user}.properties{tab.value}", on)


def ini_text(core: Core) -> str:
    return core.folders.alox_ini.read_text(encoding="cp1252")


# ================================================================================================== description
def test_pages_are_those_of_the_labview_tab_controls():
    assert [p.title for p in TOP_PAGES] == ["STATUS", "SYSTEM", "HARDWARE DEVICES", "DISPLAY", "NETWORK", "MODULES",
                                            "LOGGING", "ADMIN"]
    assert [p.title for p in HARDWARE_PAGES] == ["CAMERA", "XYZ STAGES", "MONOCHROMATOR", "PYROMETER"]
    rights = {p.key: p.right for p in PAGES.values()}
    assert rights == {"status": None, "system": PropertiesTab.CHAMBER, "hardware": None,
                      "display": PropertiesTab.OVERLAYS, "network": PropertiesTab.NETWORK,
                      "modules": PropertiesTab.MODULES, "logging": PropertiesTab.DATALOGGING,
                      "admin": PropertiesTab.ADMIN, "camera": PropertiesTab.CAMERA, "xyz": PropertiesTab.XYZSTAGES,
                      "monochromator": PropertiesTab.MONOCHROMATOR, "pyrometer": PropertiesTab.MONOCHROMATOR}
    assert set(rights.values()) - {None} == set(PropertiesTab)
    keys = [key for page in PAGES.values() for key in page.field_keys()]
    assert len(keys) == len(set(keys)), "a key is edited on two pages"
    for page in PAGES.values():
        assert page.restart <= set(page.field_keys())
    assert {"hardware.xy.comport", "hardware.z.comport", "hardware.monochromator.comport", "hardware.camera.port",
            "webservice.port", "hardware.camera.highmag.name"} <= {k for p in PAGES.values() for k in p.restart}
    assert "general.wafersize" in PAGES["system"].restart and "overlays.fontsize" not in PAGES["display"].restart


def test_user_actions_list_has_the_41_rights_in_labview_order():
    assert len(RIGHT_TEXTS) == 41
    keys = [key for _text, key in RIGHT_TEXTS]
    assert keys[:32] == [action.key for action in UserAction]
    assert keys[32:] == [f"properties{t.value}" for t in (
        PropertiesTab.CHAMBER, PropertiesTab.CAMERA, PropertiesTab.MONOCHROMATOR, PropertiesTab.XYZSTAGES,
        PropertiesTab.OVERLAYS, PropertiesTab.NETWORK, PropertiesTab.MODULES, PropertiesTab.DATALOGGING,
        PropertiesTab.ADMIN)]
    assert RIGHT_TEXTS[0][0] == "Exit Application" and RIGHT_TEXTS[7][0] == "Edit 'Get Image' function settings"
    assert [f.key for g in ADMIN.groups for f in g.fields] == [f"user.rights.{{user}}.{k}" for k in keys]
    assert [value for value, _t in ADMIN.selector.items] == ["operator", "engineer", "admin"]


def test_text_colour_and_value_ring_fields():
    name = text("NAME", "a.name", "HighMag")
    assert name.convert(None) == "" and name.validate("LowMag") == "LowMag"
    col = colour("COLOR", "a.colour", "#3399ff")
    assert col.default == "3399FF" and col.validate("#00ff7f") == "00FF7F" and col.validate(0x102030) == "102030"
    with pytest.raises(ValueError):
        col.validate("blue")
    size = Field("WAFER SIZE", "w", Kind.ENUM, 100.0, items=((76.2, "3"), (100.0, "4"), (150.0, "6")))
    assert size.validate(76.2) == 76.2 and size.validate("150") == 150.0
    with pytest.raises(ValueError):
        size.validate(125.0)


# ================================================================================================== model
def test_model_reads_edits_applies_and_saves_only_the_changed_keys(core: Core):
    grant(core, *ALL_TABS)
    core.properties.save()
    before = ini_text(core)
    model = PropertiesModel(core)
    xyz = model.models["xyz"]
    assert xyz.value("hardware.xy.stagescoordsys") == 3 and xyz.value("hardware.xy.waferxorg") == 10.0
    assert xyz.set("hardware.xy.comport", 7) is None and xyz.set("hardware.xy.waferxorg", 12.5) is None
    assert model.models["display"].set("overlays.fontcolor", "#ff0000") is None
    assert model.dirty and model.changes() == {"hardware.xy.comport": 7, "hardware.xy.waferxorg": 12.5,
                                               "overlays.fontcolor": "FF0000"}
    applied = model.apply()
    assert applied.ok and applied.restart == ("hardware.xy.comport",) and applied.saved is None
    assert core.properties.get("hardware.xy.waferxorg") == 12.5 and ini_text(core) == before    # in memory only
    assert model.models["network"].set("webservice.port", 8101) is None
    saved = model.save()
    assert saved.ok and saved.saved == core.folders.alox_ini and saved.restart == ("webservice.port",)
    after = ini_text(core)
    assert "waferxorg_DBL = 12,500000" in after and "comport_I32 = 7" in after and "port_I32 = 8101" in after
    assert 'fontcolor_STR = "FF0000"' in after
    kept = [line for line in before.splitlines() if "waferxorg" not in line]
    assert all(line in after.splitlines() for line in kept)                       # the other lines are untouched
    assert not model.dirty


def test_pages_follow_the_tab_rights_of_the_current_user(core: Core):
    model = PropertiesModel(core)
    assert model.allowed(PAGES["status"]) and model.allowed("hardware")
    assert not model.allowed("camera") and not model.models["camera"].editable
    assert model.models["camera"].set("hardware.camera.highmag.hflip", True) is None
    refused = model.apply()
    assert not refused.ok and core.properties.get("hardware.camera.highmag.hflip") is None
    grant(core, PropertiesTab.CAMERA)
    assert model.allowed("camera") and model.apply().ok
    assert core.properties.get("hardware.camera.highmag.hflip") is True
    grant(core, PropertiesTab.MONOCHROMATOR)
    assert model.allowed("pyrometer")                                    # PYROMETER uses the monochromator right
    grant(core, PropertiesTab.ADMIN, user="engineer")
    assert not model.allowed("admin")
    core.users.login(UserType.ENGINEER, "")                              # no password property: refused
    assert core.users.current is UserType.OPERATOR


def test_initial_page_is_xyz_for_an_operator_when_the_admin_xyz_right_is_set(core: Core):
    model = PropertiesModel(core)
    assert model.initial_page() == ("status", None)
    grant(core, PropertiesTab.XYZSTAGES, user="admin")                  # the key drawn in frame 1.3
    assert model.initial_page() == ("hardware", "xyz")


def test_admin_rights_of_a_selected_user(core: Core):
    grant(core, PropertiesTab.ADMIN)
    model = PropertiesModel(core)
    admin = model.models["admin"]
    admin.select("engineer")
    assert admin.value("user.rights.{user}.refocus") is True
    assert admin.set("user.rights.{user}.mesasetup", True) is None
    admin.select("operator")
    assert admin.set("user.rights.{user}.exitapp", False) is None
    assert model.save().ok
    assert core.users.has_right(UserAction.MESA_SETUP, UserType.ENGINEER)
    assert not core.users.has_right(UserAction.EXIT_APP, UserType.OPERATOR)


def test_change_password_stores_base64_and_needs_the_admin_right(core: Core):
    model = PropertiesModel(core)
    assert model.change_password(UserType.ENGINEER, "s3cret", "s3cret") is not None      # no admin right
    grant(core, PropertiesTab.ADMIN)
    assert model.change_password(UserType.ENGINEER, "s3cret", "other") == "the passwords do not match"
    assert model.change_password(UserType.ENGINEER, "s3cret", "s3cret") is None
    assert core.properties.get("user.password.engineer") == base64.b64encode(b"s3cret").decode("ascii")
    assert "engineer_STR" in ini_text(core)                                               # saved
    assert core.users.login(UserType.ENGINEER, "s3cret") and core.users.current is UserType.ENGINEER


def test_save_as_defaults_copies_alox_ini(core: Core):
    model = PropertiesModel(core)
    with pytest.raises(ValueError):
        model.save_as_defaults()
    grant(core, PropertiesTab.ADMIN)
    target = model.save_as_defaults()
    assert target == core.folders.defaults_ini and target.read_bytes() == core.folders.alox_ini.read_bytes()


def test_status_urls_and_modules(core: Core, devices: dict[str, Any]):
    model = PropertiesModel(core)
    lines = model.status()
    assert [line.title for line in lines] == ["SYSTEM", "TILT FUNCTION", "BOWING FUNCTION", "MESA FUNCTION"]
    assert (lines[0].code, lines[0].text) == (100, "System initialized")
    (core.folders.exe / "translations" / "mesa_en.lang").write_text("000200;Mesa starting\n", encoding="cp1252")
    core.translations.set_language("en")
    core.measures[Measure.MESA].set(200)
    assert (model.status()[3].code, model.status()[3].text) == (200, "Mesa starting")
    assert model.tilt() == 0.0
    assert model.urls() == ("http://alox-pc:8001/alox/v1/", "ws://alox-pc:6123")
    assert model.modules() == [("wl", "READY"), ("xy", "READY"), ("z", "READY")]


# ================================================================================================== controller
def test_controller_opens_the_panel_and_runs_its_actions(core: Core, fakes: SimpleNamespace):
    c = Controller(core)
    result = c.open_settings()
    assert result.ok and result.data == PANEL_NAME and result.tab is Tab.SETTINGS
    assert isinstance(c.properties_model(), PropertiesModel)
    assert c.reset_job_info().ok
    assert not c.chuck_calibration().ok                                  # bowing not registered
    core.functions.register(fakes.Probe(FunctionName.BOWING_MEASURE))
    posted: list[Any] = []
    core.commands.post = lambda method, payload=None, **_kw: posted.append((method, payload))      # type: ignore
    started = c.chuck_calibration()
    assert started.ok and started.tab is Tab.HIGH_LEVEL
    method, request = posted[0]
    assert method is Method(int(Measure.BOWING)) and request.overrides == {"chuck_calibration": True}
    core.properties.set("user.rights.operator.bowing", False)
    assert not c.chuck_calibration().ok


def test_bowing_takes_the_chuck_calibration_of_a_request():
    from alox.core.core import MeasureRequest
    from alox.core.functions import JobInfo
    from alox.functions.bowing import BowingMeasurement

    stand_in = SimpleNamespace(services=SimpleNamespace(profile={}))
    request = MeasureRequest(Measure.BOWING, JobInfo(), None, {"chuck_calibration": True})
    assert BowingMeasurement.params_for(stand_in, request).chuck_calibration is True         # type: ignore[arg-type]
    plain = MeasureRequest(Measure.BOWING, JobInfo(), None, {})
    assert BowingMeasurement.params_for(stand_in, plain).chuck_calibration is False          # type: ignore[arg-type]


# ================================================================================================== window
@pytest.fixture
def window(core: Core) -> Iterator[Any]:
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from alox.ui.window import MainWindow

    QApplication.instance() or QApplication([])
    w = MainWindow(Controller(core), confirm=lambda _question: True, poll_interval_ms=100)
    w.resize(1400, 820)
    yield w
    w.allow_close()
    w.close()


def pump(window: Any, predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    end = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > end:
            raise AssertionError("condition not reached")
        window.flush(0.5)
        time.sleep(0.01)


def open_panel(window: Any) -> Any:
    from alox.ui.properties_form import PropertiesPanel

    window.settings_action.trigger()
    pump(window, lambda: isinstance(window.settings_panel, PropertiesPanel))
    return window.settings_panel


def test_settings_button_shows_the_panel_with_its_pages_and_rights(window: Any, core: Core):
    panel = open_panel(window)
    assert window.tabs.tabText(0) == f"SETTINGS ({PANEL_NAME})" and window.tabs.currentIndex() == int(Tab.SETTINGS)
    assert [panel.pages.tabText(i) for i in range(panel.pages.count())] == [p.title for p in TOP_PAGES]
    enabled = [panel.pages.isTabEnabled(i) for i in range(panel.pages.count())]
    assert enabled == [True, False, True, False, False, False, False, False]           # STATUS, HARDWARE DEVICES
    assert not any(panel.hardware.isTabEnabled(i) for i in range(panel.hardware.count()))
    assert not panel.inputs["hardware.xy.comport"].isEnabled()
    assert panel.status_ids["SYSTEM"].text() == "100" and panel.status_texts["SYSTEM"].text() == "System initialized"
    assert panel.labels["hardware.xy.comport"].text().endswith("↻")
    assert not panel.labels["overlays.fontsize"].text().endswith("↻")
    grant(core, PropertiesTab.XYZSTAGES, PropertiesTab.OVERLAYS)
    window.refresh_functions()                                            # the rights follow the logged-in user
    assert panel.hardware.isTabEnabled(1) and panel.pages.isTabEnabled(3) and panel.inputs["hardware.xy.comport"].isEnabled()


def test_edit_save_and_exit(window: Any, core: Core):
    grant(core, PropertiesTab.XYZSTAGES, PropertiesTab.OVERLAYS)
    panel = open_panel(window)
    panel.inputs["hardware.xy.comport"].setValue(9)
    panel.inputs["general.saveOverlay"].setChecked(False)
    assert panel.buttons["APPLY"].isEnabled() and panel.dirty
    result = panel.save()
    assert result.ok and result.restart == ("hardware.xy.comport",)
    assert "next start" in panel.status.text()
    text = ini_text(core)
    assert "comport_I32 = 9" in text and "saveOverlay_BOOL = FALSE" in text
    panel.inputs["overlays.fontsize"].setValue(40)
    panel.buttons["CANCEL"].click()
    assert panel.inputs["overlays.fontsize"].value() == 24 and not panel.dirty
    panel.inputs["general.displaytimer"].setValue(300)
    asked: list[str] = []
    panel._confirm = lambda question: asked.append(question) or False
    assert not panel.close_panel() and window.settings_panel is panel                # kept open on "No"
    panel._confirm = lambda _question: True
    panel.buttons["EXIT"].click()
    assert window.settings_panel is None and window.tabs.tabText(0) == "SETTINGS"
    assert core.properties.get("general.displaytimer") == 200                         # dropped, not written


def test_one_panel_at_a_time_with_the_function_settings(window: Any):
    panel = open_panel(window)
    window.option_buttons["GET_IMAGE"].click()
    pump(window, lambda: window.settings_panel is not panel and window.settings_panel is not None)
    assert window.tabs.tabText(0) == "SETTINGS (Get Image)"
    assert open_panel(window) is not panel


def test_status_page_follows_the_status_events_and_resets_jobs(window: Any, core: Core):
    panel = open_panel(window)
    core.measures[Measure.TILT].set(200)
    window.on_event(Event("status.tilt", "core", {"code": 200}))
    assert panel.status_ids["TILT FUNCTION"].text() == "200"
    model = window.results.models[FunctionName.MESA_MEASURE]
    model.job = SimpleNamespace(lot_id="LOT1")
    panel.buttons["RESET JOB INFORMATIONS"].click()
    assert model.job is None
    window.flush()


def test_password_dialog_changes_the_password(window: Any, core: Core):
    grant(core, PropertiesTab.ADMIN)
    panel = open_panel(window)
    assert panel.buttons["SET PASSWORD"].isEnabled()
    panel.selector.setCurrentIndex(2)                                    # ADMINISTRATOR
    dialog = panel.password_dialog()
    assert dialog.selected() is UserType.ADMIN
    dialog.password.setText("new-pass")
    dialog.confirm.setText("typo")
    assert not dialog.submit() and dialog.message.text() == "the passwords do not match"
    dialog.confirm.setText("new-pass")
    assert dialog.submit()
    assert core.users.check_password(UserType.ADMIN, "new-pass")
    assert "Password changed" in panel.status.text()
