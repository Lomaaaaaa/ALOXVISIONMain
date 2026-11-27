"""The dialogs of the properties panel (``alox.ui.properties_dialogs``): the pixel-size calibration
(``alox_calibration_ui``) and the XYZ stages setup (``alox_xyz_stages_ui``), on a booted core with fake devices and
synthetic images (offscreen)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import numpy as np
import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from alox.core.commands import Method
from alox.core.core import Core
from alox.core.users import PropertiesTab, UserType
from alox.ui.controller import Controller
from alox.ui.properties_dialogs import (
    CalibrationDialog,
    XyzSetupDialog,
    array_to_qimage,
)
from alox.ui.properties_form import PropertiesPanel
from alox.ui.properties_panel import Z_CHECK_TEXT, PropertiesModel, pixel_size


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])          # type: ignore[return-value]


def grant(core: Core, *tabs: PropertiesTab, user: str = "operator") -> None:
    for tab in tabs:
        core.properties.set(f"user.rights.{user}.properties{tab.value}", True)


def as_engineer(core: Core) -> None:
    core.users.change_password(UserType.ENGINEER, "pw")                   # synthetic password
    assert core.users.login(UserType.ENGINEER, "pw")


class Images:
    """The image store as the calibration reads it: ``image_info`` and ``last``."""

    def __init__(self) -> None:
        self.stored: dict[str, tuple[int, Any]] = {}

    def put(self, camera: str, pixels: Any) -> None:
        count = self.stored.get(camera, (0, None))[0] + 1
        self.stored[camera] = (count, pixels)

    def image_info(self, camera: str) -> tuple[int, float | None]:
        return self.stored.get(camera, (0, None))[0], None

    def last(self, camera: str) -> Any:
        return self.stored.get(camera, (0, None))[1]


class ZStage:
    """A Z stage with a software range, as the XYZ setup moves it."""

    def __init__(self) -> None:
        from alox.devices.base import DeviceState

        self.name = "z"
        self.state = DeviceState.READY
        self.pos = 33.5
        self.limits = (0.0, 40.0)
        self.moves: list[tuple[str, float]] = []

    def position(self, *, timeout: float | None = None) -> float:
        return self.pos

    def _check(self, target: float) -> None:
        if not self.limits[0] <= target <= self.limits[1]:
            raise ValueError(f"{target} outside the software range")

    def move_absolute(self, target: float, *, wait: bool = True, timeout: float | None = None) -> float:
        self._check(target)
        self.moves.append(("abs", target))
        self.pos = target
        return target

    def move_relative(self, delta: float, *, wait: bool = True, timeout: float | None = None) -> float:
        self._check(self.pos + delta)
        self.moves.append(("rel", delta))
        self.pos += delta
        return self.pos


# ================================================================================================== calibration
def test_pixel_size_is_the_real_length_over_the_line():
    assert pixel_size(100.0, 400.0) == 0.25
    assert pixel_size(100.0, None) is None and pixel_size(0.0, 10.0) is None and pixel_size(5.0, 0.0) is None


def test_store_calibration_writes_the_camera_keys(core: Core):
    model = PropertiesModel(core)
    assert model.store_calibration("highmag", 0.17, 1) is not None                # no camera right
    grant(core, PropertiesTab.CAMERA)
    assert model.store_calibration("highmag", 0.0, 1) == "invalid calibration"
    assert model.store_calibration("lowmag", 0.000681, 2) is None
    assert model.calibration("lowmag") == (0.000681, 2)
    text = core.folders.alox_ini.read_text(encoding="cp1252")
    assert "[hardware.camera.lowmag]" in text and "pixelsize_DBL" in text and "units_I32 = 2" in text


def test_calibration_dialog_measures_a_line_and_stores_the_pixel_size(qapp: QApplication, core: Core):
    grant(core, PropertiesTab.CAMERA)
    images, grabbed = Images(), []
    core.images = images                                                       # type: ignore[assignment]
    dialog = CalibrationDialog(PropertiesModel(core), "highmag", grab=grabbed.append, image_period_ms=0)
    assert grabbed == ["highmag"] and not dialog.view.has_image              # no image yet: one grab (LabVIEW)
    images.put("highmag", np.full((60, 80), 120, dtype=np.uint16))
    assert dialog.refresh_image() and dialog.view.has_image
    assert not dialog.submit() and "INVALID" in dialog.message.text()       # no line, no length
    dialog.view.set_line(10, 10, 40, 50)                                     # 50 px
    dialog.real.setValue(25.0)
    dialog.units.setCurrentIndex(dialog.units.findData(1))
    assert dialog.pixels.text() == "50.00" and dialog.valid.text() == "VALID" and dialog.size.text() == "0.500000"
    assert dialog.submit() and dialog.result_calibration == (0.5, 1)
    assert core.properties.get("hardware.camera.highmag.pixelsize") == 0.5
    assert core.properties.get("hardware.camera.highmag.units") == 1


def test_array_to_qimage_scales_16_bit_images():
    image = array_to_qimage(np.array([[0, 1000], [2000, 4000]], dtype=np.uint16))
    assert image is not None and image.width() == 2 and image.pixelColor(1, 1).red() == 255
    assert array_to_qimage(None) is None


def test_controller_grab_posts_get_image_of_the_camera(core: Core):
    posted: list[Any] = []
    core.commands.post = lambda method, payload=None, **_kw: posted.append((method, payload))   # type: ignore
    c = Controller(core)
    assert c.grab("lowmag").ok and c.grab("highmag").ok and not c.grab("other").ok
    assert posted == [(Method.GET_IMAGE, 1), (Method.GET_IMAGE, 0)]


# ================================================================================================== XYZ setup
def test_xyz_setup_is_not_for_operator(core: Core):
    grant(core, PropertiesTab.XYZSTAGES, user="operator")
    model = PropertiesModel(core)
    assert not model.xyz_setup_allowed()                                     # LabVIEW: disabled for Operator
    setup = model.xyz_setup()
    with pytest.raises(ValueError):
        setup.move_z(10.0)
    as_engineer(core)
    assert not model.xyz_setup_allowed()                                     # no XYZ right for the engineer
    grant(core, PropertiesTab.XYZSTAGES, user="engineer")
    assert model.xyz_setup_allowed()


def test_xyz_setup_checks_and_saves_the_autofocus_boundaries(core: Core):
    grant(core, PropertiesTab.XYZSTAGES, user="engineer")
    as_engineer(core)
    setup = PropertiesModel(core).xyz_setup()
    assert setup.settings.value("hardware.z.zminautofocus") == 33.0
    assert setup.settings.set("hardware.z.zmax", 40.0) is None
    assert setup.settings.set("hardware.z.zmaxautofocus", 45.0) is None
    refused = setup.save()
    assert not refused.ok and refused.errors[""] == Z_CHECK_TEXT
    assert setup.settings.set("hardware.z.zmaxautofocus", 36.0) is None
    assert setup.settings.set("hardware.xy.wafercheckfreq", 250) is None
    saved = setup.save()
    assert saved.ok and saved.saved == core.folders.alox_ini
    assert set(saved.restart) == {"hardware.z.zmax", "hardware.xy.wafercheckfreq"}
    assert core.properties.get("hardware.z.zmaxautofocus") == 36.0
    assert "zmaxautofocus_DBL = 36,000000" in core.folders.alox_ini.read_text(encoding="cp1252")


def test_xyz_setup_moves_only_through_the_z_device(core: Core):
    grant(core, PropertiesTab.XYZSTAGES, user="engineer")
    as_engineer(core)
    z = ZStage()
    core.devices["z"] = z                                                    # type: ignore[assignment]
    setup = PropertiesModel(core).xyz_setup()
    assert z.moves == [] and setup.z_position() == 33.5 and setup.z_limits() == (0.0, 40.0)
    assert setup.set_from_current("hardware.z.zminautofocus") is None
    assert setup.settings.changes() == {"hardware.z.zminautofocus": 33.5}
    assert setup.move_z(34.0) == 34.0 and setup.jog_z(-0.25) == 33.75
    assert z.moves == [("abs", 34.0), ("rel", -0.25)]
    with pytest.raises(ValueError):
        setup.move_z(55.0)                                                   # the device's software range
    assert setup.set_from_current("hardware.z.zmaxautofocus") is None
    assert setup.settings.value("hardware.z.zmaxautofocus") == 33.75


def test_xyz_setup_dialog(qapp: QApplication, core: Core):
    grant(core, PropertiesTab.XYZSTAGES, user="engineer")
    as_engineer(core)
    z = ZStage()
    core.devices["z"] = z                                                    # type: ignore[assignment]
    dialog = XyzSetupDialog(PropertiesModel(core).xyz_setup())
    assert dialog.current_z.text() == "33.5000" and "0 … 40 mm" in dialog.z_range.text()
    assert z.moves == []                                                     # nothing moves at opening
    assert dialog.labels["hardware.z.zmin"].text().endswith("↻")
    assert not dialog.labels["hardware.z.zminautofocus"].text().endswith("↻")
    dialog.target.setValue(35.0)
    dialog.buttons["MOVE Z"].click()
    dialog.step.setValue(0.5)
    dialog.buttons["Z +"].click()
    assert z.moves == [("abs", 35.0), ("rel", 0.5)] and dialog.current_z.text() == "35.5000"
    dialog.buttons["Z MAX AUTOFOCUS = CURRENT Z"].click()
    assert dialog.inputs["hardware.z.zmaxautofocus"].value() == 35.5
    dialog.target.setValue(80.0)
    dialog.buttons["MOVE Z"].click()
    assert "outside" in dialog.message.text() and z.pos == 35.5
    assert not dialog.submit().ok and dialog.isVisible() is False and "Zmax autofocus" in dialog.message.text()
    dialog.inputs["hardware.z.zmax"].setValue(40.0)                          # Z MAX STAGE above the boundary
    assert dialog.submit().ok and core.properties.get("hardware.z.zmaxautofocus") == 35.5


# ================================================================================================== panel
@pytest.fixture
def panel(qapp: QApplication, core: Core) -> Iterator[PropertiesPanel]:
    w = PropertiesPanel(PropertiesModel(core), status_period_ms=0)
    yield w
    w.close()


def test_panel_buttons_follow_the_owner_decisions(panel: PropertiesPanel, core: Core):
    assert "LOAD CHUCK REF" not in panel.buttons and "FLAT FIELD" not in panel.buttons         # P4, P5
    assert not panel.buttons["CALIBRATION"].isEnabled() and not panel.buttons["XYZ SETUP"].isEnabled()
    grant(core, PropertiesTab.CAMERA, PropertiesTab.XYZSTAGES)
    panel.refresh_rights()
    assert panel.buttons["CALIBRATION"].isEnabled() and not panel.buttons["XYZ SETUP"].isEnabled()   # Operator
    grant(core, PropertiesTab.XYZSTAGES, user="engineer")
    as_engineer(core)
    panel.refresh_rights()
    assert panel.buttons["XYZ SETUP"].isEnabled()
    assert isinstance(panel.xyz_setup_dialog(), XyzSetupDialog)
    assert isinstance(panel.calibration_dialog("lowmag"), CalibrationDialog)
    panel.refresh_status()
    assert panel.calibrations["highmag"].text() == "1 pixels / pixel"
