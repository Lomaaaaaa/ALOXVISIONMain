"""The dialogs of the properties panel: the pixel-size calibration (``alox_calibration_ui``) and the XYZ stages setup
(``alox_xyz_stages_ui``), on the models of :mod:`alox.ui.properties_panel`.

**Calibration** ("ALOX VISION CALIBRATION", CALIBRATION on the CAMERA page): the image of the chosen camera on the
left (LabVIEW grabs one at opening: the LED as ``general.setLEDcam``, then Get Image with the recipe's parameters; here
the ``Get Image`` command of the core, at opening when the camera has no image yet and with GET IMAGE), the line tool
of the main display; on the right LENGTH (REAL-WORLD), REAL-WORLD UNITS (MICROMETERS / MILLIMETERS), LENGTH (PIXELS)
of the line drawn, PIXEL SIZE (REAL-WORLD) = real / pixels and VALID; OK stores ``hardware.camera.<camera>.pixelsize``
and ``units`` (1 µm, 2 mm), CANCEL leaves them. An invalid line or length keeps the dialog open ("INVALID ROI/CALIB").

**XYZ setup** ("ALOX XYZ STAGES SETUP", XYZ SETUP on the XYZ STAGES page, not for Operator): the autofocus boundaries
``hardware.z.zminautofocus`` / ``zmaxautofocus`` with the current Z ("set from the current Z") and Z moves (MOVE Z to
a position, Z − / Z + by a step) through the Z device and its software range, then the stage limits, axes and speeds
(next start). OK checks Z MIN STAGE < Z MIN AUTOFOCUS < Z MAX AUTOFOCUS < Z MAX STAGE and saves ``alox.ini``. Device
calls run through ``submit(fn, on_done)`` (the window's worker; default: at once).
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
from typing import Any

import numpy as np
from PIL import Image
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from alox.ui import theme
from alox.ui.bridge import to_qimage
from alox.ui.properties_panel import (
    CALIBRATION_UNITS,
    CAMERAS,
    XYZ_SETUP,
    PropertiesModel,
    SaveResult,
    XyzSetupModel,
    pixel_size,
)
from alox.ui.settings_form import FieldForm
from alox.ui.settings_panels import Field
from alox.ui.widgets import ImageView, Tool

__all__ = ["CalibrationDialog", "XyzSetupDialog", "array_to_qimage"]

#: ``submit(fn, on_done)``: run ``fn`` (maybe in a worker) and call ``on_done(future)`` in the UI thread.
Submit = Callable[[Callable[[], Any], Callable[["Future[Any]"], None]], Any]
INSTRUCTIONS = ("1. Using the mouse: draw into the image a line corresponding to a standard object of known size.\n"
                "2. Enter the corresponding length in real-world units.\n"
                "3. Select the units.\n"
                "4. Press OK to store the new calibration.")


def run_now(fn: Callable[[], Any], on_done: Callable[[Future[Any]], None]) -> Future[Any]:
    """The default ``submit``: run ``fn`` at once."""
    future: Future[Any] = Future()
    try:
        future.set_result(fn())
    except Exception as exc:                              # noqa: BLE001 - given to on_done
        future.set_exception(exc)
    on_done(future)
    return future


def array_to_qimage(pixels: Any) -> QImage | None:
    """A camera image (2-D grey or RGB array, 8 or 16 bits) as a QImage (16 bits scaled to the image maximum)."""
    if pixels is None:
        return None
    array = np.asarray(pixels)
    if array.dtype != np.uint8:
        top = float(array.max()) if array.size else 0.0
        array = (array.astype(np.float64) * (255.0 / top)).astype(np.uint8) if top > 0 else array.astype(np.uint8)
    return to_qimage(Image.fromarray(np.ascontiguousarray(array)))


def _readonly(width: int | None = None) -> QLineEdit:
    line = QLineEdit()
    line.setReadOnly(True)
    line.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
    if width is not None:
        line.setFixedWidth(width)
    return line


def _label(text: str, role: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("role", role)
    return label


def _message(label: QLabel, text: str, *, error: bool = False) -> None:
    label.setText(text)
    label.setVisible(bool(text))
    theme.repolish(label, tone="error" if error else "")


# ================================================================================================== calibration
class CalibrationDialog(QDialog):
    """``alox_calibration_ui`` (module docstring). ``grab(camera)`` asks for one image of the camera (the window
    posts the ``Get Image`` command; None: no grab); ``image_period_ms`` polls the image store (0: tests show by
    hand with :meth:`show_image`)."""

    def __init__(self, model: PropertiesModel, camera: str = "highmag", *, grab: Callable[[str], Any] | None = None,
                 image_period_ms: int = 200, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("ALOX VISION CALIBRATION")
        self.model = model
        self._grab = grab
        self._seen = -1
        self.result_calibration: tuple[float, int] | None = None
        layout = QHBoxLayout(self)
        left = QVBoxLayout()
        self.view = ImageView()
        self.view.set_tool(Tool.LINE)
        self.view.setMinimumSize(520, 420)
        self.view.line_drawn.connect(lambda _length: self.update_values())
        left.addWidget(self.view, 1)
        tools = QHBoxLayout()
        self.grab_button = QPushButton("GET IMAGE")
        self.grab_button.setEnabled(grab is not None)
        self.grab_button.clicked.connect(self.grab_image)
        fit = QPushButton("ZOOM TO FIT")
        fit.clicked.connect(self.view.zoom_fit)
        tools.addWidget(self.grab_button)
        tools.addStretch(1)
        tools.addWidget(fit)
        left.addLayout(tools)
        layout.addLayout(left, 3)

        right = QVBoxLayout()
        title = QLabel("ALOX VISION CALIBRATION")
        title.setProperty("role", "title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right.addWidget(title)
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        self.camera = QComboBox()
        for value, text in CAMERAS:
            self.camera.addItem(text, value)
        self.camera.setCurrentIndex(max(0, self.camera.findData(camera)))
        self.camera.currentIndexChanged.connect(self._camera_changed)
        self.real = QDoubleSpinBox()
        self.real.setRange(0.0, 1_000_000.0)
        self.real.setDecimals(4)
        self.real.setKeyboardTracking(False)
        self.real.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.real.valueChanged.connect(lambda _v: self.update_values())
        self.units = QComboBox()
        for code, text in CALIBRATION_UNITS:
            self.units.addItem(text, code)
        self.pixels, self.size = _readonly(), _readonly()
        self.valid = QLabel()
        self.current = _readonly()
        for row, (caption, widget) in enumerate((
                ("CAMERA", self.camera), ("LENGTH (REAL-WORLD)", self.real), ("REAL-WORLD UNITS", self.units),
                ("LENGTH (PIXELS)", self.pixels), ("PIXEL SIZE (REAL-WORLD)", self.size), ("", self.valid),
                ("CURRENT CALIBRATION", self.current))):
            grid.addWidget(_label(caption, "field"), row, 0)
            grid.addWidget(widget, row, 1)
        grid.setColumnStretch(1, 1)
        right.addLayout(grid)
        steps = _label(INSTRUCTIONS, "muted")
        steps.setWordWrap(True)
        right.addWidget(steps)
        right.addStretch(1)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.hide()
        right.addWidget(self.message)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.ok = QPushButton("OK")
        self.ok.setProperty("kind", "primary")
        self.ok.clicked.connect(self.submit)
        self.cancel = QPushButton("CANCEL")
        self.cancel.clicked.connect(self.reject)
        buttons.addWidget(self.ok)
        buttons.addWidget(self.cancel)
        right.addLayout(buttons)
        layout.addLayout(right, 2)
        self.timer = QTimer(self)
        if image_period_ms > 0:
            self.timer.setInterval(image_period_ms)
            self.timer.timeout.connect(self.refresh_image)
            self.timer.start()
        self._camera_changed()

    @property
    def camera_name(self) -> str:
        return str(self.camera.currentData())

    def _camera_changed(self, _index: int = 0) -> None:
        self._seen = -1
        size, units = self.model.calibration(self.camera_name)
        unit = {0: "pixels", 1: "µm", 2: "mm"}.get(units, str(units))
        self.current.setText(f"{size:g} {unit} / pixel")
        index = self.units.findData(units)
        if index >= 0:
            self.units.setCurrentIndex(index)
        self.view.clear_roi()
        if not self.refresh_image() and self._grab is not None:
            self.grab_image()                                        # LabVIEW grabs one image at opening
        self.update_values()

    def grab_image(self) -> None:
        """GET IMAGE: one image of the camera (shown when it arrives)."""
        if self._grab is not None:
            self._grab(self.camera_name)

    def refresh_image(self) -> bool:
        """Show the camera's last image when a new one is stored; True when an image is shown."""
        count, pixels = self.model.camera_image(self.camera_name)
        if pixels is None:
            if self._seen != count:
                self.view.set_image(None)
                self._seen = count
            return False
        if count != self._seen:
            self._seen = count
            self.show_image(pixels)
        return True

    def show_image(self, pixels: Any) -> None:
        self.view.set_image(array_to_qimage(pixels))

    def calibration(self) -> tuple[float | None, int]:
        """PIXEL SIZE (REAL-WORLD) (None: not VALID) and the units code."""
        return pixel_size(self.real.value(), self.view.line_length()), int(self.units.currentData())

    def update_values(self) -> None:
        pixels = self.view.line_length()
        self.pixels.setText("" if pixels is None else f"{pixels:.2f}")
        size, _units = self.calibration()
        self.size.setText("" if size is None else f"{size:.6f}")
        self.valid.setText("VALID" if size is not None else "NOT VALID")
        theme.repolish(self.valid, tone="" if size is not None else "error")

    def submit(self) -> bool:
        """OK: stores the calibration when VALID (the dialog then closes); True when stored."""
        size, units = self.calibration()
        if size is None:
            _message(self.message, "INVALID ROI/CALIB: draw a line on the image and enter its real length.",
                     error=True)
            return False
        error = self.model.store_calibration(self.camera_name, size, units)
        if error is not None:
            _message(self.message, error, error=True)
            return False
        self.result_calibration = (size, units)
        self.timer.stop()
        self.accept()
        return True

    def reject(self) -> None:
        self.timer.stop()
        super().reject()


# ================================================================================================== XYZ setup
class XyzSetupDialog(FieldForm, QDialog):
    """``alox_xyz_stages_ui`` (module docstring) on ``model``; ``submit(fn, on_done)`` runs the device calls."""

    def __init__(self, model: XyzSetupModel, *, submit: Submit | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(XYZ_SETUP.title)
        self.model = model
        self._submit: Submit = submit or run_now
        self._loading = False
        self.inputs: dict[str, QWidget] = {}
        self.labels: dict[str, QLabel] = {}
        self.buttons: dict[str, QPushButton] = {}
        layout = QVBoxLayout(self)
        title = QLabel(XYZ_SETUP.title)
        title.setProperty("role", "title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        inner = QWidget()
        column = QVBoxLayout(inner)
        column.setContentsMargins(4, 4, 4, 4)
        af, *others = XYZ_SETUP.groups
        column.addWidget(self._group(af))
        column.addWidget(self._z_group())
        for group in others:
            column.addWidget(self._group(group))
        note = _label("↻ Read by the devices at start-up: applies at the next start of the application. The autofocus "
                      "boundaries apply at the next autofocus.", "muted")
        note.setWordWrap(True)
        column.addWidget(note)
        column.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(inner)
        layout.addWidget(scroll, 1)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.hide()
        layout.addWidget(self.message)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        ok = self._button("OK", self.submit, "Check the Z values, store them and save alox.ini")
        ok.setProperty("kind", "primary")
        buttons.addWidget(ok)
        buttons.addWidget(self._button("CANCEL", self.reject, "Close without storing"))
        layout.addLayout(buttons)
        self.resize(720, 640)
        self.refresh()
        self.refresh_z()

    def _caption(self, f: Field) -> str:
        return f"{f.caption} ↻" if f.key in XYZ_SETUP.restart else f.caption

    def _button(self, name: str, slot: Callable[[], Any], tip: str) -> QPushButton:
        button = QPushButton(name)
        button.setToolTip(tip)
        button.clicked.connect(lambda _c=False: slot())
        self.buttons[name] = button
        return button

    def _z_group(self) -> QWidget:
        box = QGroupBox("Z STAGE")
        grid = QGridLayout(box)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        self.current_z = _readonly(120)
        self.z_range = _label("", "muted")
        grid.addWidget(_label("CURRENT Z (mm)", "field"), 0, 0)
        grid.addWidget(self.current_z, 0, 1)
        grid.addWidget(self._button("REFRESH", self.refresh_z, "Read the current Z position"), 0, 2)
        grid.addWidget(self.z_range, 0, 3, 1, 2)
        grid.addWidget(self._button("Z MIN AUTOFOCUS = CURRENT Z",
                                    lambda: self.from_current("hardware.z.zminautofocus"),
                                    "Set Z MIN AUTOFOCUS to the current Z (not stored until OK)"), 1, 0, 1, 2)
        grid.addWidget(self._button("Z MAX AUTOFOCUS = CURRENT Z",
                                    lambda: self.from_current("hardware.z.zmaxautofocus"),
                                    "Set Z MAX AUTOFOCUS to the current Z (not stored until OK)"), 1, 2, 1, 3)
        self.target = self._spin(3)
        grid.addWidget(_label("MOVE TO (mm)", "field"), 2, 0)
        grid.addWidget(self.target, 2, 1)
        grid.addWidget(self._button("MOVE Z", self.move, "Move Z to this position (within the Z software range)"),
                       2, 2)
        self.step = self._spin(4)
        self.step.setRange(0.0001, 10.0)
        self.step.setValue(0.01)
        grid.addWidget(_label("JOG STEP (mm)", "field"), 3, 0)
        grid.addWidget(self.step, 3, 1)
        grid.addWidget(self._button("Z −", lambda: self.jog(-1), "Move Z down by the step"), 3, 2)
        grid.addWidget(self._button("Z +", lambda: self.jog(+1), "Move Z up by the step"), 3, 3)
        grid.setColumnStretch(4, 1)
        return box

    @staticmethod
    def _spin(decimals: int) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(-1000.0, 1000.0)
        spin.setDecimals(decimals)
        spin.setKeyboardTracking(False)
        spin.setAlignment(Qt.AlignmentFlag.AlignRight)
        return spin

    # ============================================================================================== values
    def refresh(self) -> None:
        settings = self.model.settings
        self._loading = True
        try:
            for f, resolved in settings.entries():
                self._show(f, self.inputs[f.key], settings.value(resolved))
                self.labels[f.key].setToolTip(resolved)
        finally:
            self._loading = False
        allowed = self.model.allowed
        for widget in self.inputs.values():
            widget.setEnabled(allowed)
        for name, button in self.buttons.items():
            button.setEnabled(allowed or name in ("CANCEL", "REFRESH"))
        limits = self.model.z_limits()
        self.z_range.setText("" if limits is None else f"software range {limits[0]:g} … {limits[1]:g} mm")

    def _edit(self, key: str, value: Any) -> None:
        if self._loading:
            return
        error = self.model.settings.set(key, value)
        if error is not None:
            _message(self.message, error, error=True)
            self.refresh()
        else:
            _message(self.message, "")

    def _run(self, fn: Callable[[], Any], done: Callable[[Any], None], what: str) -> None:
        def finished(future: Future[Any]) -> None:
            error = future.exception()
            if error is not None:
                _message(self.message, f"{what}: {error}", error=True)
                return
            done(future.result())
        self._submit(fn, finished)

    def _show_z(self, position: float) -> None:
        if not self.current_z.text():                         # the first reading: MOVE TO starts there
            self.target.setValue(position)
        self.current_z.setText(f"{position:.4f}")

    def refresh_z(self) -> None:
        """REFRESH: the current Z position."""
        self._run(self.model.z_position, self._show_z, "current Z")

    def from_current(self, key: str) -> None:
        def done(error: str | None) -> None:
            if error is not None:
                _message(self.message, error, error=True)
            else:
                _message(self.message, "")
            self.refresh()
        self._run(lambda: self.model.set_from_current(key), done, "current Z")

    def move(self) -> None:
        """MOVE Z (the button: the only way the dialog moves Z, with the jogs)."""
        target = self.target.value()
        self._run(lambda: self.model.move_z(target), self._moved, "Z move")

    def jog(self, sign: int) -> None:
        delta = sign * self.step.value()
        self._run(lambda: self.model.jog_z(delta), self._moved, "Z jog")

    def _moved(self, position: float) -> None:
        self._show_z(position)
        _message(self.message, f"Z at {position:.4f} mm")

    def submit(self) -> SaveResult:
        """OK: check, store, save; the dialog closes when it worked."""
        result = self.model.save()
        if not result.ok:
            _message(self.message, "; ".join(result.errors.values()), error=True)
            return result
        self.accept()
        return result
