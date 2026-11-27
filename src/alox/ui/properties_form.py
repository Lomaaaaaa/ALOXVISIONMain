"""The Qt form of the properties panel ("Vision Core", ``alox_properties_ui``) on
:class:`alox.ui.properties_panel.PropertiesModel`.

Layout as the LabVIEW panel: the title ALOX VISION CORE PROPERTIES, the Tab Control STATUS, SYSTEM, HARDWARE DEVICES
(its pages CAMERA, XYZ STAGES, MONOCHROMATOR, PYROMETER), DISPLAY, NETWORK, MODULES, LOGGING, ADMIN; a page without
the user's right is disabled and grayed (frame 1), its inputs read-only. STATUS shows the system, tilt, bowing and
mesa status ID and description and the current tilt, refreshed every 300 ms (the event loop's ``Timeout``) and on the
status events of the bus, with RESET JOB INFORMATIONS. The fields are built as the function settings panels
(:class:`alox.ui.settings_form.FieldForm`); a value used only at start-up is marked ↻ and the page says it applies at
the next start. Under the pages: a status line and APPLY / SAVE / CANCEL / EXIT. CALIBRATION (CAMERA) and XYZ SETUP
(XYZ STAGES) open the dialogs of :mod:`alox.ui.properties_dialogs`; SET PASSWORD (ADMIN) the change-password dialog.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from alox.core.users import UserType
from alox.ui import theme
from alox.ui.properties_dialogs import CalibrationDialog, Submit, XyzSetupDialog
from alox.ui.properties_panel import (
    ADMIN,
    CAMERA,
    CAMERAS,
    HARDWARE,
    HARDWARE_PAGES,
    PANEL_NAME,
    STATUS_PERIOD_MS,
    TITLE,
    TOP_PAGES,
    PropertiesModel,
    PropertiesPage,
    SaveResult,
)
from alox.ui.settings_form import FieldForm
from alox.ui.settings_panels import Field

__all__ = ["RESTART_MARK", "ChangePasswordDialog", "PropertiesPanel"]

RESTART_MARK = "↻"
RESTART_NOTE = (f"{RESTART_MARK} Read by the devices at start-up: applies at the next start of the application "
                "(the devices are not reconfigured while it runs).")


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


class ChangePasswordDialog(QDialog):
    """``alox_change_password_ui``: USER, NEW PASSWORD, CONFIRM PASSWORD, OK / CANCEL. OK calls ``submit(user,
    password, confirm)``, which returns the refusal (shown, the dialog stays open) or None (the dialog closes)."""

    def __init__(self, submit: Callable[[UserType, str, str], str | None], user: UserType = UserType.OPERATOR,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("CHANGE PASSWORD")
        self._submit = submit
        layout = QVBoxLayout(self)
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        self.user = QComboBox()
        for u in UserType:
            self.user.addItem("ADMINISTRATOR" if u is UserType.ADMIN else u.name, int(u))
        self.user.setCurrentIndex(self.user.findData(int(user)))
        self.password = QLineEdit()
        self.confirm = QLineEdit()
        for line in (self.password, self.confirm):
            line.setEchoMode(QLineEdit.EchoMode.Password)
        for row, (caption, widget) in enumerate((("USER", self.user), ("NEW PASSWORD", self.password),
                                                 ("CONFIRM PASSWORD", self.confirm))):
            grid.addWidget(_label(caption, "field"), row, 0)
            grid.addWidget(widget, row, 1)
        layout.addLayout(grid)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.hide()
        layout.addWidget(self.message)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.ok = QPushButton("OK")
        self.ok.setProperty("kind", "primary")
        self.ok.setDefault(True)
        self.ok.clicked.connect(self.submit)
        self.cancel = QPushButton("CANCEL")
        self.cancel.clicked.connect(self.reject)
        buttons.addWidget(self.ok)
        buttons.addWidget(self.cancel)
        layout.addLayout(buttons)

    def selected(self) -> UserType:
        return UserType(int(self.user.currentData()))

    def submit(self) -> bool:
        """OK: the new password when both entries match; True when it was stored."""
        error = self._submit(self.selected(), self.password.text(), self.confirm.text())
        if error is not None:
            self.message.setText(error)
            self.message.show()
            theme.repolish(self.message, tone="error")
            return False
        self.password.clear()
        self.confirm.clear()
        self.accept()
        return True


class PropertiesPanel(FieldForm, QFrame):
    """The properties panel on ``model`` (module docstring). ``on_close()`` is called by EXIT; ``confirm(question)``
    asks before pending edits are dropped; ``on_result(text)`` reports the outcome of a button (status bar);
    ``reset_jobs()`` and ``chuck_calibration()`` are the window's actions for RESET JOB INFORMATIONS and CHUCK
    CALIBRATION; ``grab(camera)`` asks for one image (calibration); ``submit(fn, on_done)`` runs the device calls of
    the XYZ setup; ``status_period_ms``: the STATUS refresh (0: none, the tests refresh by hand)."""

    def __init__(self, model: PropertiesModel, *, on_close: Callable[[], None] | None = None,
                 confirm: Callable[[str], bool] = lambda _question: True,
                 on_result: Callable[[str], None] | None = None,
                 reset_jobs: Callable[[], Any] | None = None,
                 chuck_calibration: Callable[[], Any] | None = None,
                 grab: Callable[[str], Any] | None = None, submit: Submit | None = None,
                 status_period_ms: int = STATUS_PERIOD_MS) -> None:
        super().__init__()
        self.model = model
        self._on_close = on_close
        self._confirm = confirm
        self._on_result = on_result
        self._reset_jobs = reset_jobs
        self._chuck_calibration = chuck_calibration
        self._grab = grab
        self._submit = submit
        self._loading = False
        self.inputs: dict[str, QWidget] = {}
        self.labels: dict[str, QLabel] = {}
        self._page_of: dict[str, str] = {}               # field key → page key
        self.buttons: dict[str, QPushButton] = {}
        self.page_widgets: dict[str, QWidget] = {}
        self.status_ids: dict[str, QLineEdit] = {}
        self.status_texts: dict[str, QLineEdit] = {}
        self.selector: QComboBox | None = None
        self.setFrameShape(QFrame.Shape.NoFrame)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(8)
        title = QLabel(TITLE)
        title.setProperty("role", "title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        self.pages = QTabWidget()
        self.hardware = QTabWidget()
        for page in HARDWARE_PAGES:
            self.hardware.addTab(self._page(page), page.title)
        for page in TOP_PAGES:
            widget = self.hardware if page is HARDWARE else self._page(page)
            self.page_widgets[page.key] = widget
            self.pages.addTab(widget, page.title)
        layout.addWidget(self.pages, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        for name, slot, tip in (("APPLY", self.apply, "Write the changes into the settings in memory (next use)"),
                                ("SAVE", self.save, "Apply, then write the changed keys into alox.ini"),
                                ("CANCEL", self.cancel, "Drop the changes and read the settings again"),
                                ("EXIT", self.close_panel, "Save alox.ini and close the panel")):
            button = QPushButton(name)
            button.setToolTip(tip)
            button.setMinimumWidth(88)
            if name == "APPLY":
                button.setProperty("kind", "primary")
            button.clicked.connect(lambda _c=False, s=slot: s())
            buttons.addWidget(button)
            self.buttons[name] = button
        layout.addLayout(buttons)
        top, hardware = model.initial_page()
        self.show_page(top, hardware)
        self.refresh()
        self.refresh_status()
        self.timer = QTimer(self)
        if status_period_ms > 0:
            self.timer.setInterval(status_period_ms)
            self.timer.timeout.connect(self.refresh_status)
            self.timer.start()

    @property
    def panel_name(self) -> str:
        return PANEL_NAME

    @property
    def dirty(self) -> bool:
        return self.model.dirty

    # ============================================================================================== building
    def _caption(self, f: Field) -> str:
        caption = f.caption
        return f"{caption} {RESTART_MARK}" if self.model.restart_key(f.key) else caption

    def _page(self, page: PropertiesPage) -> QWidget:
        inner = QWidget()
        column = QVBoxLayout(inner)
        column.setContentsMargins(8, 6, 8, 8)
        column.setSpacing(6)
        extra = getattr(self, f"_extra_{page.key}", None)
        if page.selector is not None:
            row = QHBoxLayout()
            row.addWidget(_label(page.selector.label, "field"))
            self.selector = QComboBox()
            for value, text in page.selector.items:
                self.selector.addItem(text, value)
            self.selector.currentIndexChanged.connect(self._select)
            row.addWidget(self.selector)
            row.addStretch(1)
            column.addLayout(row)
        for group in page.groups:
            box = self._group(group)
            for f in group.fields:
                self._page_of[f.key] = page.key
            column.addWidget(box)
        if extra is not None:
            extra(column)
        if page.restart:
            note = _label(RESTART_NOTE, "muted")
            note.setWordWrap(True)
            column.addWidget(note)
        if page.omitted:
            note = _label("Not here: " + "; ".join(page.omitted) + ".", "muted")
            note.setWordWrap(True)
            note.setToolTip("\n".join(page.omitted))
            column.addWidget(note)
        column.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(inner)
        self.page_widgets[page.key] = scroll
        return scroll

    def _button(self, name: str, slot: Callable[[], Any] | None, tip: str) -> QPushButton:
        button = QPushButton(name)
        button.setToolTip(tip)
        if slot is None:
            button.setEnabled(False)
        else:
            button.clicked.connect(lambda _c=False: slot())
        self.buttons[name] = button
        return button

    def _row(self, column: QVBoxLayout, *buttons: QPushButton) -> None:
        row = QHBoxLayout()
        for button in buttons:
            row.addWidget(button)
        row.addStretch(1)
        column.addLayout(row)

    def _extra_status(self, column: QVBoxLayout) -> None:
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)
        grid.addWidget(_label("STATUS ID", "field"), 0, 1)
        grid.addWidget(_label("STATUS DESCRIPTION", "field"), 0, 2)
        line = 1
        for title in ("SYSTEM", "TILT FUNCTION", "BOWING FUNCTION", "MESA FUNCTION"):
            grid.addWidget(_label(title, "section"), line, 0)
            self.status_ids[title] = _readonly(96)
            self.status_texts[title] = _readonly()
            grid.addWidget(self.status_ids[title], line, 1)
            grid.addWidget(self.status_texts[title], line, 2)
            line += 1
        grid.addWidget(_label("CURRENT TILT (°)", "section"), line, 0)
        self.current_tilt = _readonly(96)
        grid.addWidget(self.current_tilt, line, 1)
        grid.setColumnStretch(2, 1)
        column.addLayout(grid)
        self._row(column, self._button("RESET JOB INFORMATIONS", self.reset_jobs,
                                       "Clear the SEMI job informations shown by the results panels"))

    def _extra_system(self, column: QVBoxLayout) -> None:
        self._row(column,
                  self._button("CHUCK CALIBRATION", self.chuck_calibration if self._chuck_calibration else None,
                               "Bowing measurement with the chuck calibration (right Bowing)"))

    def _extra_camera(self, column: QVBoxLayout) -> None:
        column.addWidget(_label("PIXEL SIZE CALIBRATION", "section"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        self.calibrations: dict[str, QLineEdit] = {}
        for row, (camera, text) in enumerate(CAMERAS):
            self.calibrations[camera] = _readonly(220)
            grid.addWidget(_label(text, "field"), row, 0)
            grid.addWidget(self.calibrations[camera], row, 1, Qt.AlignmentFlag.AlignLeft)
        grid.setColumnStretch(2, 1)
        column.addLayout(grid)
        self._row(column, self._button("CALIBRATION", self.open_calibration,
                                       "Pixel size calibration: a line of known length on an image"))

    def _extra_xyz(self, column: QVBoxLayout) -> None:
        self._row(column, self._button("XYZ SETUP", self.open_xyz_setup,
                                       "Autofocus boundaries (with the current Z), stage limits, axes and speeds"))

    def _extra_network(self, column: QVBoxLayout) -> None:
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        self.web_url, self.ws_url = _readonly(), _readonly()
        grid.addWidget(_label("WEB SERVER URL", "field"), 0, 0)
        grid.addWidget(self.web_url, 0, 1)
        grid.addWidget(_label("WEB SOCKET URL", "field"), 1, 0)
        grid.addWidget(self.ws_url, 1, 1)
        grid.setColumnStretch(1, 1)
        column.addLayout(grid)

    def _extra_modules(self, column: QVBoxLayout) -> None:
        column.addWidget(_label("DEVICES", "section"))
        self.modules_grid = QGridLayout()
        self.modules_grid.setHorizontalSpacing(12)
        self.module_states: dict[str, QLineEdit] = {}
        column.addLayout(self.modules_grid)

    def _extra_admin(self, column: QVBoxLayout) -> None:
        self._row(column, self._button("SET PASSWORD", self.set_password, "Change the password of a user"),
                  self._button("SAVE AS DEFAULTS", self.save_as_defaults,
                               "Save alox.ini, then copy it over alox_default.ini (SAVE alox.ini)"))

    # ============================================================================================== showing
    def show_page(self, key: str, hardware: str | None = None) -> None:
        """Select the page ``key`` (and the HARDWARE DEVICES page ``hardware``)."""
        self.pages.setCurrentWidget(self.page_widgets[key] if key != HARDWARE.key else self.hardware)
        if hardware is not None:
            self.hardware.setCurrentWidget(self.page_widgets[hardware])

    def refresh(self) -> None:
        """Show the values of every page and the rights."""
        self._loading = True
        try:
            for settings in self.model.models.values():
                for f, resolved in settings.entries():
                    widget = self.inputs[f.key]
                    self._show(f, widget, settings.value(resolved))
                    self.labels[f.key].setToolTip(resolved)
                    widget.setToolTip(resolved if f.minimum is None else f"{resolved}: {f.minimum:g} … {f.maximum:g}")
            if self.selector is not None:
                self.selector.setCurrentIndex(self.selector.findData(self.model.models[ADMIN.key].selection))
        finally:
            self._loading = False
        self.refresh_rights()
        self._status()

    def refresh_rights(self) -> None:
        """Enable each page with its right (``user.rights.<user>.properties<tab>``), the inputs and the buttons."""
        for index, page in enumerate(TOP_PAGES):
            self.pages.setTabEnabled(index, self.model.allowed(page))
        for index, page in enumerate(HARDWARE_PAGES):
            self.hardware.setTabEnabled(index, self.model.allowed(page))
        for key, widget in self.inputs.items():
            widget.setEnabled(self.model.models[self._page_of[key]].editable)
        self.buttons["CALIBRATION"].setEnabled(self.model.allowed(CAMERA))
        self.buttons["XYZ SETUP"].setEnabled(self.model.xyz_setup_allowed())
        admin = self.model.allowed(ADMIN)
        for name in ("SET PASSWORD", "SAVE AS DEFAULTS"):
            self.buttons[name].setEnabled(admin)
        if self.selector is not None:
            self.selector.setEnabled(admin)
        dirty = self.model.dirty
        self.buttons["APPLY"].setEnabled(dirty)
        self.buttons["CANCEL"].setEnabled(dirty)

    def refresh_status(self) -> None:
        """STATUS (the 300 ms ``Timeout``), the NETWORK URLs and the MODULES states."""
        try:
            lines = self.model.status()
            tilt = self.model.tilt()
        except Exception as exc:                          # noqa: BLE001 - a bad read must not stop the panel
            self._message(f"status: {exc}", error=True)
            return
        for line in lines:
            self.status_ids[line.title].setText(str(line.code))
            self.status_texts[line.title].setText(line.text)
        self.current_tilt.setText(f"{tilt:.4f}")
        for camera, field in self.calibrations.items():
            size, units = self.model.calibration(camera)
            field.setText(f"{size:g} {({0: 'pixels', 1: 'µm', 2: 'mm'}).get(units, units)} / pixel")
        web, ws = self.model.urls()
        self.web_url.setText(web)
        self.ws_url.setText(ws)
        for row, (name, state) in enumerate(self.model.modules()):
            field = self.module_states.get(name)
            if field is None:
                field = self.module_states[name] = _readonly(160)
                self.modules_grid.addWidget(_label(name.upper(), "field"), row, 0)
                self.modules_grid.addWidget(field, row, 1, Qt.AlignmentFlag.AlignLeft)
            field.setText(state)

    def reload(self) -> None:
        """The active profile changed: nothing of this panel depends on it (pending edits are kept)."""
        self.refresh_status()

    # ============================================================================================== editing
    def _edit(self, key: str, value: Any) -> None:
        if self._loading:
            return
        settings = self.model.models[self._page_of[key]]
        error = settings.set(key, value)
        if error is not None:
            self._message(error, error=True)
            self._loading = True
            try:
                self._show(settings.field(key), self.inputs[key], settings.value(key))
            finally:
                self._loading = False
        else:
            self._status()
        self.refresh_rights()

    def _select(self, _index: int) -> None:
        if self._loading or self.selector is None:
            return
        self.model.models[ADMIN.key].select(self.selector.currentData())
        self.refresh()

    def _status(self) -> None:
        problems = [p for settings in self.model.models.values() if settings.allowed for p in settings.problems()]
        if problems:
            self._message("; ".join(problems), error=True)
        elif self.model.dirty:
            self._message(f"{len(self.model.changes())} change(s) not applied.")
        else:
            self._message("")

    def _message(self, text: str, *, error: bool = False) -> None:
        self.status.setText(text)
        self.status.setVisible(bool(text))
        theme.repolish(self.status, tone="error" if error else "")

    def _report(self, text: str, *, error: bool = False) -> None:
        self._message(text, error=error)
        if self._on_result is not None:
            self._on_result(text)

    def _done(self, result: SaveResult, verb: str) -> None:
        self.refresh()
        if not result.ok:
            self._report(f"{PANEL_NAME}: " + "; ".join(result.errors.values()), error=True)
            return
        text = f"{PANEL_NAME}: {len(result.written)} setting(s) {verb}"
        if result.saved is not None:
            text += f" ({result.saved.name})"
        if result.restart:
            text += f"; {len(result.restart)} of them apply at the next start of the application"
        self._report(text + ".")

    # ============================================================================================== buttons
    def apply(self) -> SaveResult:
        """APPLY: the pending edits into the settings in memory."""
        result = self.model.apply()
        self._done(result, "applied")
        return result

    def save(self) -> SaveResult:
        """SAVE: apply, then the changed keys into ``alox.ini``."""
        result = self.model.save()
        self._done(result, "saved")
        return result

    def cancel(self) -> None:
        """CANCEL: drop the pending edits, read the settings again."""
        self.model.cancel()
        self.refresh()

    def close_panel(self) -> bool:
        """EXIT: asks before dropping pending edits, then properties "Save" (the keys changed in memory) and "Close
        Panel"; False when the operator keeps the panel open."""
        if self.model.dirty and not self._confirm(f"Discard the changes of {PANEL_NAME}?"):
            return False
        self.model.cancel()
        try:
            self.model.save_file()
        except (OSError, ValueError) as exc:
            self._report(f"alox.ini not saved: {exc}", error=True)
        self.timer.stop()
        if self._on_close is not None:
            self._on_close()
        return True

    def reset_jobs(self) -> None:
        """RESET JOB INFORMATIONS."""
        if self._reset_jobs is not None:
            self._reset_jobs()

    def chuck_calibration(self) -> None:
        """CHUCK CALIBRATION (the window starts it in its worker)."""
        if self._chuck_calibration is not None:
            self._chuck_calibration()

    def calibration_dialog(self, camera: str = "highmag") -> CalibrationDialog:
        """The pixel-size calibration dialog (``alox_calibration_ui``)."""
        return CalibrationDialog(self.model, camera, grab=self._grab, parent=self)

    def open_calibration(self) -> None:
        """CALIBRATION: the dialog; the new calibration is reported when stored."""
        dialog = self.calibration_dialog()
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.result_calibration is not None:
            size, _units = dialog.result_calibration
            self._report(f"Calibration {dialog.camera_name}: {size:g} {dialog.units.currentText().lower()} per pixel")
        self.refresh_status()

    def xyz_setup_dialog(self) -> XyzSetupDialog:
        """The XYZ stages setup dialog (``alox_xyz_stages_ui``)."""
        return XyzSetupDialog(self.model.xyz_setup(), submit=self._submit, parent=self)

    def open_xyz_setup(self) -> None:
        """XYZ SETUP: the dialog; the values stored are reported."""
        dialog = self.xyz_setup_dialog()
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._report("XYZ setup saved")

    def password_dialog(self) -> ChangePasswordDialog:
        """The change-password dialog for the user selected on ADMIN."""
        selected = UserType.parse(self.model.models[ADMIN.key].selection or "operator")

        def submit(user: UserType, password: str, confirm: str) -> str | None:
            error = self.model.change_password(user, password, confirm)
            if error is None:
                self._report(f"Password changed ({user.label})")
            return error
        return ChangePasswordDialog(submit, selected, self)

    def set_password(self) -> None:
        """SET PASSWORD (``alox_change_password_ui``)."""
        self.password_dialog().exec()

    def save_as_defaults(self) -> None:
        """SAVE AS DEFAULTS (LabVIEW "SAVE alox.ini"): after a confirmation, ``alox.ini`` over ``alox_default.ini``."""
        if self.model.dirty and not self._confirm("Pending changes are not applied: save the file as it is?"):
            return
        if not self._confirm("Copy alox.ini over alox_default.ini (the settings of a new installation)?"):
            return
        try:
            target = self.model.save_as_defaults()
        except (OSError, ValueError) as exc:
            self._report(f"defaults not saved: {exc}", error=True)
            return
        self._report(f"alox.ini saved as {target}")
