"""The function settings panels (``alox.ui.settings_panels``): the description against the port's profile readers,
the editing model, and the panel in the window (offscreen)."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from alox.core.core import Core
from alox.core.images import GET_IMAGE_OVERLAYS, OVERLAY_PROPERTIES
from alox.ui.controller import ROWS
from alox.ui.settings_panels import (
    PANELS,
    Field,
    Kind,
    PanelSpec,
    SettingsModel,
    panel_by_name,
    panel_for,
)

_MISSING = object()


class Recording:
    """A profile that records the keys read (and the default each reader gives) and answers from ``values``."""

    def __init__(self, values: dict[str, Any] | None = None) -> None:
        self.values = dict(values or {})
        self.read: dict[str, Any] = {}

    def get(self, key: str, default: Any = None) -> Any:
        self.read.setdefault(key, default)
        return self.values.get(key, default)


def read_by(spec: PanelSpec, selection: str | None, values: dict[str, Any] | None = None) -> dict[str, Any]:
    """The keys the panel's readers read (key → the reader's default)."""
    profile = Recording(values)
    for reader in spec.readers:
        reader(profile, selection)
    return profile.read


def overlay_keys(spec: PanelSpec) -> set[str]:
    return {key for name in spec.overlays for key in OVERLAY_PROPERTIES[name]}


def cases() -> list[tuple[PanelSpec, str | None]]:
    return [(spec, selection) for spec in PANELS.values() for selection in spec.selections()]


def ids(case: tuple[PanelSpec, str | None]) -> str:
    return case[0].row + (f"-{case[1]}" if case[1] else "")


# ================================================================================================== description
def test_every_gear_button_of_the_window_has_its_panel():
    rows = [row for row in ROWS if row.options_key is not None]
    assert {row.key for row in rows} == set(PANELS)
    for row in rows:
        spec = panel_for(row.key)
        assert spec is not None and spec.name == row.panel and spec.action is row.setup
        assert panel_by_name(row.panel) is spec
    assert panel_for("AF_OXYD") is None and panel_by_name("Vision Core") is None


@pytest.mark.parametrize("case", cases(), ids=ids)
def test_every_key_of_a_panel_is_read_by_the_function(case: tuple[PanelSpec, str | None]):
    spec, selection = case
    assert spec.readers
    read = read_by(spec, selection)
    keys = spec.keys(selection)
    assert len(keys) == len(set(keys)), "a key is edited twice"
    allowed = set(read) | overlay_keys(spec)
    assert [key for key in keys if key not in allowed] == []
    if spec.selector is not None:
        assert all(f"{spec.selector.name}" not in key for key in keys)


@pytest.mark.parametrize("case", cases(), ids=ids)
def test_defaults_and_types_are_the_readers(case: tuple[PanelSpec, str | None]):
    """The default of a field is the default its reader gives (else the image store's overlay default); integer
    fields are the keys read as integers."""
    spec, selection = case
    read = read_by(spec, selection)
    for f in spec.fields():
        key = spec.key(f, selection)
        if key in read:
            expected = read[key]
            if f.kind is Kind.PATH:
                assert str(expected) == f.default, key
                continue
        else:
            group = next(name for name in spec.overlays if key in OVERLAY_PROPERTIES[name])
            expected = group not in GET_IMAGE_OVERLAYS
        assert f.default == expected and type(f.default) is type(expected), (key, f.default, expected)


@pytest.mark.parametrize("spec", PANELS.values(), ids=lambda s: s.row)
def test_fields_are_consistent(spec: PanelSpec):
    assert spec.pages and spec.title and spec.vi.endswith("_ui_config.vi")
    for f in spec.fields():
        assert f.label and f.key
        assert f.validate(f.default) == f.default, f.key
        if f.kind is Kind.NUMBER:
            assert f.minimum is not None and f.maximum is not None and f.minimum < f.maximum, f.key
            assert f.step > 0 and (f.decimals == 0) == f.integer, f.key
        if f.kind is Kind.ENUM:
            codes = [code for code, _text in f.items]
            assert codes == list(range(len(codes))) and len(codes) >= 2, f.key


def _limits(spec: PanelSpec) -> Iterator[tuple[Field, Any]]:
    for f in spec.fields():
        if f.kind is Kind.NUMBER:
            yield f, f.minimum if not f.integer else int(f.minimum)      # type: ignore[arg-type]
            yield f, f.maximum if not f.integer else int(f.maximum)      # type: ignore[arg-type]
        elif f.kind is Kind.ENUM:
            yield from ((f, code) for code, _t in f.items)
        elif f.kind is Kind.CHECK:
            yield from ((f, flag) for flag in (False, True))


@pytest.mark.parametrize("case", cases(), ids=ids)
def test_the_function_accepts_every_value_the_panel_accepts(case: tuple[PanelSpec, str | None]):
    """Each field at its minimum, its maximum and each item (the others at their default): the parameters build."""
    spec, selection = case
    defaults = {spec.key(f, selection): f.default for f in spec.fields()}
    for f, value in _limits(spec):
        read_by(spec, selection, {**defaults, spec.key(f, selection): value})


# ================================================================================================== model
class FakeProfile:
    def __init__(self, values: dict[str, Any] | None = None) -> None:
        self.values = dict(values or {})
        self.saved = 0
        self.refuse: set[str] = set()

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def set(self, key: str, value: Any) -> None:
        if key in self.refuse:
            raise TypeError(f"{key} is a STR property")
        self.values[key] = value

    def save(self) -> Path:
        self.saved += 1
        return Path("/profiles/default.zip")


def test_model_edits_validates_applies_saves_and_cancels():
    profile = FakeProfile({"tracking.diameter": 3.5, "tracking.overlayzone": "TRUE"})
    allowed = [True]
    model = SettingsModel(PANELS["TRACK"], profile, allowed=lambda: allowed[0])
    assert model.value("tracking.diameter") == 3.5 and model.value("tracking.overlayzone") is True
    assert not model.dirty and model.editable and model.problems() == []
    assert "below the minimum" in (model.set("tracking.diameter", -1) or "")
    assert "a number is expected" in (model.set("tracking.diameter", "abc") or "")
    assert model.set("tracking.diameter", "4,5") is None and model.value("tracking.diameter") == 4.5
    assert model.dirty and model.changes() == {"tracking.diameter": 4.5}
    model.cancel()
    assert not model.dirty and model.value("tracking.diameter") == 3.5
    model.set("tracking.diameter", 3.5)
    assert not model.dirty                                         # same as the profile: no edit
    model.set("tracking.diameter", 7)
    allowed[0] = False
    refused = model.apply()
    assert not refused.ok and "not allowed" in refused.errors[""] and model.dirty and not model.editable
    allowed[0] = True
    done = model.save()
    assert done.ok and done.written == ("tracking.diameter",) and done.archive is not None
    assert profile.values["tracking.diameter"] == 7.0 and profile.saved == 1 and not model.dirty
    profile.values["tracking.diameter"] = 9.0                      # changed elsewhere (another panel, the API)
    model.cancel()
    assert model.value("tracking.diameter") == 9.0


def test_model_reports_profile_problems_and_refused_values():
    profile = FakeProfile({"detect.vcsel.method": 7, "detect.vcsel.minradius": "x"})
    profile.refuse.add("detect.vcsel.maxradius")
    model = SettingsModel(PANELS["DET_CIRCLE"], profile)
    assert model.value("detect.vcsel.minradius") == 20.0 and model.missing("detect.vcsel.minradius")
    assert any("METHOD" in problem for problem in model.problems())
    assert model.set("detect.vcsel.method", "obj learning") is None and model.value("detect.vcsel.method") == 1
    assert model.set("detect.vcsel.morphopen", 2.5) is not None                    # an integer field
    model.set("detect.vcsel.maxradius", 60)
    result = model.apply()
    assert result.written == ("detect.vcsel.method",) and "STR" in result.errors["detect.vcsel.maxradius"]
    assert model.changes() == {"detect.vcsel.maxradius": 60.0}


def test_model_selection_keeps_the_edits_of_both_cameras():
    profile = FakeProfile({"get.image.highmag.gain": 2.0, "get.image.lowmag.gain": 3.0})
    model = SettingsModel(PANELS["GET_IMAGE"], profile)
    assert model.selection == "highmag" and model.value("get.image.{camera}.gain") == 2.0
    model.set("get.image.{camera}.gain", 2.5)
    model.select("lowmag")
    assert model.value("get.image.{camera}.gain") == 3.0
    model.set("get.image.{camera}.brightness", 100)
    assert model.apply().written == ("get.image.highmag.gain", "get.image.lowmag.brightness")
    assert profile.values["get.image.highmag.gain"] == 2.5 and profile.values["get.image.lowmag.brightness"] == 100
    with pytest.raises(ValueError):
        model.select("thermal")


def test_model_on_the_core_profile_is_read_by_the_next_run(core: Core):
    from alox.functions.tracking import TrackingParams

    model = SettingsModel(PANELS["TRACK"], core.profile_manager)
    model.set("tracking.diameter", 12.5)
    model.set("tracking.overlayzone", True)
    result = model.save()
    assert result.ok and result.archive is not None and result.archive.is_file()
    params = TrackingParams.from_profile(core.profile_manager)
    assert params.diameter_um == 12.5 and params.overlay_zone is True
    core.profile_manager.load(core.profile_manager.current or "DEFAULT")         # the saved recipe
    assert core.profile_manager.get("tracking.diameter") == 12.5


# ================================================================================================== window
@pytest.fixture
def window(core: Core) -> Iterator[Any]:
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from alox.ui.controller import Controller
    from alox.ui.window import MainWindow

    _app = QApplication.instance() or QApplication([])
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


def test_gear_opens_the_panel_one_at_a_time_and_edits_the_profile(window: Any, core: Core):
    from alox.ui.controller import Tab

    window.option_buttons["MES_MESA"].click()
    pump(window, lambda: window.settings_panel is not None and window.settings_panel.spec.row == "MES_MESA")
    panel = window.settings_panel
    assert window.tabs.tabText(0) == "SETTINGS (Mesa Measurement)" and window.tabs.currentIndex() == int(Tab.SETTINGS)
    assert [panel.pages.tabText(i) for i in range(panel.pages.count())] == ["SETTINGS", "DISPLAY"]
    averages = panel.inputs["measure.mesa.averages"]
    averages.setValue(4)
    assert panel.model.changes() == {"measure.mesa.averages": 4} and panel.buttons["APPLY"].isEnabled()
    panel.buttons["APPLY"].click()
    assert core.profile_manager.get("measure.mesa.averages") == 4 and not panel.model.dirty
    panel.inputs["measure.mesa.tracking"].setChecked(True)
    panel.buttons["CANCEL"].click()
    assert core.profile_manager.get("measure.mesa.tracking", False) is False
    assert not panel.inputs["measure.mesa.tracking"].isChecked()
    combo = panel.inputs["measure.mesa.wlmodetab"]
    assert [combo.itemText(i) for i in range(combo.count())] == ["MANUAL", "SCAN"]
    combo.setCurrentIndex(0)
    panel.buttons["SAVE"].click()
    assert core.profile_manager.get("measure.mesa.wlmodetab") == 0
    window.option_buttons["GET_IMAGE"].click()                     # one panel at a time
    pump(window, lambda: window.settings_panel is not None and window.settings_panel.spec.row == "GET_IMAGE")
    assert window.settings_panel is not panel and window.tabs.tabText(0) == "SETTINGS (Get Image)"
    get_image = window.settings_panel
    get_image.selector.setCurrentIndex(1)
    assert get_image.model.selection == "lowmag"
    get_image.buttons["CLOSE"].click()
    assert window.settings_panel is None and window.tabs.tabText(0) == "SETTINGS"


def test_the_panel_follows_the_right_of_the_user(window: Any, core: Core):
    window.option_buttons["GET_IMAGE"].click()
    pump(window, lambda: window.settings_panel is not None)
    panel = window.settings_panel
    assert panel.inputs["get.image.{camera}.gain"].isEnabled()
    core.properties.set("user.rights.operator.getimagesetup", False)
    window.refresh_functions()
    assert not panel.inputs["get.image.{camera}.gain"].isEnabled() and not panel.buttons["APPLY"].isEnabled()
    assert panel.buttons["CLOSE"].isEnabled()
