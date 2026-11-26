"""The operator main window (``alox_core_ui.vi`` front panel), thin over :class:`alox.ui.controller.Controller`.

Layout as the LabVIEW panel: tool bar (optical viewfinder, save image, user, settings, log, about, exit, the alarm
line and CLEAR ALARM), the image display with its tools palette and ZOOM FIT on the left, X / Y moves, SET
WAVELENGTH, the status field and THERMAL MEASUREMENT under it; on the right IMAGE ACQUISITION, the low-level and
high-level function rows, the camera selector, the joysticks and F (ms), VISION PROFILE, then the SETTINGS /
LOW-LEVEL MEASUREMENTS / HIGH-LEVEL MEASUREMENTS tabs.

Look (:mod:`alox.ui.theme`): the panels are cards on the window background, the function rows a grid with aligned
columns (name, start buttons, stop, settings, status badge), the icons drawn by :mod:`alox.ui.icons` in the theme's
colours; the tool bar's "Dark theme" toggle switches the light / dark theme at run time and remembers it for the user.

Threads: bus events arrive through :class:`alox.ui.bridge.EventBridge` (queued to the UI thread); every click that
may block (devices, a function's stop) runs in the actions worker; the producer's ``Timeout`` refresh
(:meth:`Controller.poll`, every ``general.displaytimer`` ms) in the poll worker; the image (``merged()``) is rendered
in the image worker and shown when its key changes (:func:`alox.ui.adapters.image_key`, checked every 100 ms).

Function settings panels: a gear button opens the function's panel (:class:`alox.ui.settings_form.SettingsPanel`
on :mod:`alox.ui.settings_panels`) in the SETTINGS tab, one at a time as ``alox_manager_panels`` (opening another one
closes it, asking before pending edits are dropped); it follows the user's right and reloads when the active profile
changes.

Placeholders (next steps): the properties UI in the SETTINGS tab, the low- and high-level results panels (the tabs show the function states and the measurement statuses meanwhile),
the profiles editor, the optical viewfinder and the thermal measurement.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import Future
from typing import Any

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QFont
from PySide6.QtWidgets import (
    QAbstractButton,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from alox.api.backend import Measure
from alox.core.functions import FunctionName
from alox.ui import icons, theme
from alox.ui.adapters import image_key
from alox.ui.bridge import EventBridge, Worker, to_qimage
from alox.ui.controller import (
    ACQUISITION,
    BOWING_TILT,
    HIGH_MAG,
    LOW_MAG,
    MESA,
    TOPICS,
    ActionResult,
    Controller,
    FunctionRow,
    Snapshot,
    Tab,
)
from alox.ui.settings_form import SettingsPanel
from alox.ui.widgets import ImageView, LoginDialog, LogWindow, StatusIndicator, Tool

__all__ = ["NEXT_STEPS", "MainWindow"]

log = logging.getLogger(__name__)

#: The UI steps still to do, in order (shown in the placeholders and the report).
NEXT_STEPS = (
    ("Function settings panels: Refocus Oxyd (no ⚙ button on the LabVIEW panel), the panels' own tools (learning, "
     "exposure tables, test runs)"),
    "Properties UI (SETTINGS button: alox_properties_ui and its tabs, with the per-tab rights)",
    "Low-level results panels (LOW-LEVEL MEASUREMENTS tab: ui_run of each function)",
    "High-level results panels (HIGH-LEVEL MEASUREMENTS tab: mesa / tilt / bowing results and job infos)",
    "Profiles editor (EDIT PROFILES: alox_profiles_ui)",
    "Manual length measurement in µm with the calibration (display line tool), arrow-key XY jog",
    "Optical viewfinder, thermal measurement (if kept in scope), about box details, translations of the labels",
)

_GAP = 8                  # between the panels
_CONTROL_H = 28           # height of the buttons of the function rows
_NAME_W = 112             # width of the function names


def _label(text: str, role: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("role", role)
    return label


def _section_label(text: str) -> QLabel:
    label = _label(text.upper(), "section")
    font = QFont(label.font())
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.6)
    label.setFont(font)
    return label


def _card(layout: QLayout, margins: tuple[int, int, int, int] = (10, 8, 10, 10)) -> QFrame:
    """A panel holding ``layout``."""
    frame = QFrame()
    frame.setProperty("card", True)
    layout.setContentsMargins(*margins)
    frame.setLayout(layout)
    return frame


class MainWindow(QMainWindow):
    """The window. ``confirm(question) -> bool`` asks the operator (default: a Yes/No box; tests pass a stub).
    ``theme_name`` installs that theme (:mod:`alox.ui.theme`; default: the one installed, else the user's)."""

    IMAGE_INTERVAL_MS = 100

    def __init__(self, controller: Controller, *, confirm: Callable[[str], bool] | None = None,
                 poll_interval_ms: int | None = None, theme_name: str | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.confirm = confirm or self._ask
        self._allow_close = False
        self._image_key: Any = None
        self._rendering = False
        self._polling = False
        self.start_buttons: dict[str, QAbstractButton] = {}
        self.stop_buttons: dict[str, QAbstractButton] = {}
        self.option_buttons: dict[str, QAbstractButton] = {}
        self.indicators: dict[str, StatusIndicator] = {}
        self.actions_worker = Worker("ui-actions", self)
        self.poll_worker = Worker("ui-poll", self)
        self.image_worker = Worker("ui-image", self)
        self.log_window: LogWindow | None = None
        self.settings_panel: SettingsPanel | None = None

        self._themed_icons: list[tuple[Any, str, str]] = []
        if theme_name is not None or not theme.applied():
            theme.apply(None, theme_name)

        self.setWindowTitle(controller.title())
        self._build_toolbar()
        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(_GAP, _GAP, _GAP, _GAP)
        layout.setSpacing(_GAP)
        layout.addWidget(self._build_left(), 11)
        layout.addWidget(self._build_right(), 9)
        self.setCentralWidget(central)
        self._build_statusbar()
        self.apply_theme()

        self.bridge = EventBridge(controller.core.events, TOPICS, self)
        self.bridge.event.connect(self.on_event)
        self.poll_timer = QTimer(self)
        interval = poll_interval_ms if poll_interval_ms is not None else self._display_timer()
        self.poll_timer.setInterval(max(100, interval))
        self.poll_timer.timeout.connect(self.poll)
        self.image_timer = QTimer(self)
        self.image_timer.setInterval(self.IMAGE_INTERVAL_MS)
        self.image_timer.timeout.connect(self.refresh_image)
        self.refresh_all()
        self.poll_timer.start()
        self.image_timer.start()

    def _display_timer(self) -> int:
        try:
            return int(self.controller.core.properties.get("general.displaytimer", 1000) or 1000)
        except (TypeError, ValueError):
            return 1000

    # ============================================================================================== building
    def _themed(self, target: Any, name: str, colour: str = "text") -> None:
        """Give ``target`` (a QAction or a button) the icon ``name`` in the theme colour ``colour``, now and after
        each theme change."""
        self._themed_icons.append((target, name, colour))
        target.setIcon(icons.icon(name, getattr(theme.current(), colour)))

    def _icon_action(self, bar: QToolBar, text: str, icon: str, slot: Callable[[], None]) -> QAction:
        action = QAction(text, self)
        self._themed(action, icon)
        action.setToolTip(text)
        action.triggered.connect(slot)
        bar.addAction(action)
        return action

    def _build_toolbar(self) -> None:
        bar = QToolBar("main", self)
        bar.setMovable(False)
        bar.setIconSize(QSize(20, 20))
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.viewfinder_action = self._icon_action(bar, "Viewfinder", "eye", lambda: None)
        self.viewfinder_action.setEnabled(False)
        self.viewfinder_action.setToolTip("OPTICAL VIEWFINDER (not ported yet)")
        self.save_action = self._icon_action(bar, "Save", "save", self.save_image)
        self.user_action = self._icon_action(bar, "User", "user", self.login)
        self.settings_action = self._icon_action(bar, "Settings", "sliders", self.open_settings)
        self.log_action = self._icon_action(bar, "Log", "log", self.show_log)
        self.about_action = self._icon_action(bar, "About", "info", self.about)
        self.exit_action = self._icon_action(bar, "Exit", "power", self.exit)
        bar.addSeparator()
        self.alarm = QLineEdit()
        self.alarm.setReadOnly(True)
        self.alarm.setPlaceholderText("ALARM")
        self.alarm.setMinimumWidth(300)
        self.alarm.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        bar.addWidget(self.alarm)
        self.clear_alarm_action = self._icon_action(bar, "Clear alarm", "bell-off", self.clear_alarm)
        bar.addSeparator()
        self.theme_action = QAction("Dark theme", self)
        self.theme_action.setCheckable(True)
        self.theme_action.setToolTip("Dark theme (darkened room); remembered for this user")
        self._themed(self.theme_action, "moon")
        self.theme_action.toggled.connect(lambda on: self.set_theme(theme.DARK if on else theme.LIGHT))
        bar.addAction(self.theme_action)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, bar)
        self.toolbar = bar

    def _build_left(self) -> QWidget:
        left = QWidget()
        column = QVBoxLayout(left)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(_GAP)
        display = QHBoxLayout()
        display.setSpacing(8)
        palette = QVBoxLayout()
        palette.setSpacing(6)
        self.view = ImageView()
        self.view.info.connect(lambda text: self.statusBar().showMessage(text, 10000))
        group = QActionGroup(self)
        self.tool_buttons: dict[Tool, QToolButton] = {}
        for tool, icon in ((Tool.ZOOM, "zoom"), (Tool.PAN, "pan"), (Tool.POINT, "point"), (Tool.LINE, "line"),
                           (Tool.RECT, "rect")):
            action = QAction(tool.label, self)
            action.setCheckable(True)
            action.setToolTip(tool.label)
            action.setChecked(tool is self.view.tool)
            action.triggered.connect(lambda _checked=False, t=tool: self.view.set_tool(t))
            self._themed(action, icon)
            group.addAction(action)
            button = QToolButton()
            button.setDefaultAction(action)
            button.setFixedSize(_CONTROL_H + 4, _CONTROL_H + 4)
            button.setIconSize(QSize(20, 20))
            palette.addWidget(button)
            self.tool_buttons[tool] = button
        palette.addSpacing(14)
        self.zoom_fit_button = QToolButton()
        self.zoom_fit_button.setText("ZOOM FIT")
        self.zoom_fit_button.setToolTip("ZOOM FIT")
        self.zoom_fit_button.setFixedSize(_CONTROL_H + 4, _CONTROL_H + 4)
        self.zoom_fit_button.setIconSize(QSize(20, 20))
        self._themed(self.zoom_fit_button, "fit")
        self.zoom_fit_button.clicked.connect(self.view.zoom_fit)
        palette.addWidget(self.zoom_fit_button)
        palette.addStretch(1)
        display.addLayout(palette)
        display.addWidget(self.view, 1)
        column.addWidget(_card(display, (8, 8, 8, 8)), 1)
        column.addWidget(self._build_bottom())
        return left

    def _build_bottom(self) -> QFrame:
        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(8)
        self.x_ctrl, self.y_ctrl = QDoubleSpinBox(), QDoubleSpinBox()
        self.move_x, self.move_y = QPushButton("MOVE X"), QPushButton("MOVE Y")
        for row, (name, spin, button, axis) in enumerate((("X (mm)", self.x_ctrl, self.move_x, "x"),
                                                          ("Y (mm)", self.y_ctrl, self.move_y, "y"))):
            spin.setRange(-500.0, 500.0)
            spin.setDecimals(6)
            spin.setSingleStep(0.1)
            spin.setFixedWidth(112)
            spin.setAlignment(Qt.AlignmentFlag.AlignRight)
            button.clicked.connect(lambda _c=False, a=axis: self.move(a))
            grid.addWidget(_label(name, "field"), row, 0)
            grid.addWidget(spin, row, 1)
            grid.addWidget(button, row, 2)
        grid.setColumnMinimumWidth(3, 4)
        self.wl_ctrl = QSpinBox()
        self.wl_ctrl.setRange(0, 2000)
        self.wl_ctrl.setSuffix(" nm")
        self.wl_ctrl.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.set_wl = QPushButton("SET WAVELENGTH")
        self.set_wl.clicked.connect(self.set_wavelength)
        grid.addWidget(self.wl_ctrl, 0, 4)
        grid.addWidget(self.set_wl, 1, 4)
        grid.setColumnMinimumWidth(5, 4)
        self.status_field = QLineEdit()
        self.status_field.setReadOnly(True)
        self.status_field.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.status_field.setToolTip("System status")
        grid.addWidget(self.status_field, 0, 6)
        self.thermal = QPushButton("THERMAL MEASUREMENT")
        self.thermal.setEnabled(False)
        self.thermal.setToolTip("Thermal measurement: not ported (scope to confirm)")
        grid.addWidget(self.thermal, 1, 6, Qt.AlignmentFlag.AlignLeft)
        grid.setColumnStretch(6, 1)
        return _card(grid)

    def _function_rows(self, grid: QGridLayout, start: int, rows: list[FunctionRow]) -> int:
        """Lay the rows out: name (or the GET IMAGE button) | start buttons | ■ | ⚙ | status."""
        line = start
        for row in rows:
            first = line
            for index, button in enumerate(row.starts):
                long_label = len(button.label) > 6
                if button.run_once:
                    widget: QWidget = QPushButton(button.label)
                    widget.setMinimumWidth(_NAME_W)
                    widget.setFixedHeight(_CONTROL_H)
                    widget.clicked.connect(lambda _c=False, k=button.key: self.press(k))
                    self.start_buttons[button.key] = widget          # type: ignore[assignment]
                    grid.addWidget(widget, line, 0)
                    continue
                if index == 0 or long_label:
                    name = _label(button.label if long_label else row.label, "name")
                    name.setFixedHeight(_CONTROL_H)
                    grid.addWidget(name, line, 0)
                text = "" if long_label or not button.label else button.label
                tool = QToolButton()
                tool.setText(text or "▷")
                tool.setIconSize(QSize(14, 14))
                tool.setFixedHeight(_CONTROL_H)
                if text:
                    tool.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
                else:
                    tool.setFixedWidth(_CONTROL_H)
                self._themed(tool, "play", "accent")
                tool.setToolTip(button.log.replace(" clicked", "").replace("(", "").replace(")", "") or row.label)
                tool.clicked.connect(lambda _c=False, k=button.key: self.press(k))
                self.start_buttons[button.key] = tool
                cell = grid.itemAtPosition(line, 1)
                if cell is None:
                    holder = QWidget()
                    box = QHBoxLayout(holder)
                    box.setContentsMargins(0, 0, 0, 0)
                    box.setSpacing(4)
                    box.addStretch(1)
                    grid.addWidget(holder, line, 1)
                else:
                    box = cell.widget().layout()                    # type: ignore[assignment]
                box.addWidget(tool)
                if long_label and index < len(row.starts) - 1:
                    line += 1
            span = line - first + 1
            stop = QToolButton()
            stop.setText("■")
            stop.setProperty("kind", "stop")
            stop.setFixedSize(_CONTROL_H, _CONTROL_H)
            stop.setIconSize(QSize(16, 16))
            self._themed(stop, "stop", "danger")
            stop.setToolTip(f"Stop {row.label}")
            stop.clicked.connect(lambda _c=False, k=row.key: self.stop_function(k))
            grid.addWidget(stop, first, 2, span, 1)
            self.stop_buttons[row.key] = stop
            if row.options_key is not None:
                gear = QToolButton()
                gear.setText("⚙")
                gear.setFixedSize(_CONTROL_H, _CONTROL_H)
                gear.setIconSize(QSize(18, 18))
                self._themed(gear, "gear", "muted")
                gear.setToolTip(f"{row.panel} settings")
                gear.clicked.connect(lambda _c=False, k=row.key: self.open_options(k))
                grid.addWidget(gear, first, 3, span, 1)
                self.option_buttons[row.key] = gear
            if row.status:
                indicator = StatusIndicator()
                grid.addWidget(indicator, first, 4)
                self.indicators[row.key] = indicator
            line += 1
        return line

    def _section(self, grid: QGridLayout, line: int, title: str) -> int:
        if line > 0:
            grid.setRowMinimumHeight(line, 30)
            grid.addWidget(_section_label(title), line, 0, 1, 5, Qt.AlignmentFlag.AlignBottom)
        else:
            grid.addWidget(_section_label(title), line, 0, 1, 5)
        return line + 1

    @staticmethod
    def _rows_grid() -> QGridLayout:
        grid = QGridLayout()
        grid.setHorizontalSpacing(5)
        grid.setVerticalSpacing(4)
        grid.setColumnStretch(0, 1)
        grid.setColumnMinimumWidth(0, _NAME_W)
        return grid

    def _build_right(self) -> QWidget:
        right = QWidget()
        column = QVBoxLayout(right)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(_GAP)
        top = QHBoxLayout()
        top.setSpacing(_GAP)
        rows = {group: [r for r in self.controller.rows if r.group == group]
                for group in (ACQUISITION, LOW_MAG, HIGH_MAG, BOWING_TILT, MESA)}

        low = self._rows_grid()
        line = 0
        for group in (ACQUISITION, LOW_MAG, HIGH_MAG):
            line = self._section(low, line, group)
            line = self._function_rows(low, line, rows[group])
        low.setRowStretch(line, 1)
        top.addWidget(_card(low), 11)

        high = self._rows_grid()
        head = QHBoxLayout()
        head.setSpacing(8)
        self.camera = QComboBox()
        self.camera.addItems(["High", "Low"])
        self.camera.setToolTip("Camera type (general.camera)")
        self.camera.setMinimumWidth(84)
        self.camera.activated.connect(self.select_camera)
        head.addWidget(self.camera)
        brand = _label("ALOX VISION CORE", "brand")
        brand.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(brand, 1)
        high.addLayout(head, 0, 0, 1, 5)
        joy = QHBoxLayout()
        joy.setSpacing(6)
        self.xy_joystick = QPushButton("XY JOYSTICK")
        self.z_joystick = QPushButton("Z JOYSTICK")
        for button, name in ((self.xy_joystick, "xy"), (self.z_joystick, "z")):
            button.setCheckable(True)
            button.clicked.connect(lambda checked, n=name: self.set_joystick(n, checked))
            joy.addWidget(button)
        joy.addStretch(1)
        joy.addWidget(_label("F (ms)", "field"))
        self.f_ms = QLineEdit("0")
        self.f_ms.setReadOnly(True)
        self.f_ms.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.f_ms.setFixedWidth(64)
        self.f_ms.setAlignment(Qt.AlignmentFlag.AlignRight)
        joy.addWidget(self.f_ms)
        high.addLayout(joy, 1, 0, 1, 5)
        self._section(high, 2, "VISION PROFILE")
        profile = QHBoxLayout()
        profile.setSpacing(4)
        self.profile = QComboBox()
        self.profile.setMinimumWidth(160)
        self.profile.activated.connect(lambda _i: self.load_profile(self.profile.currentText()))
        profile.addWidget(self.profile, 1)
        self.profile_refresh = QToolButton()
        self.profile_save = QToolButton()
        self.profile_edit = QToolButton()
        for button, icon, tip, slot in ((self.profile_refresh, "refresh", "Refresh profiles", self.refresh_profiles),
                                        (self.profile_save, "save", "Save profile", self.save_profile),
                                        (self.profile_edit, "edit", "Edit profiles", self.edit_profiles)):
            self._themed(button, icon)
            button.setIconSize(QSize(18, 18))
            button.setFixedSize(_CONTROL_H + 2, _CONTROL_H + 2)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            profile.addWidget(button)
        high.addLayout(profile, 3, 0, 1, 5)
        line = 4
        for group in (BOWING_TILT, MESA):
            line = self._section(high, line, group)
            line = self._function_rows(high, line, rows[group])
        high.setRowStretch(line, 1)
        top.addWidget(_card(high), 9)
        column.addLayout(top)

        self.tabs = QTabWidget()
        self.settings_host = QWidget()
        self.settings_layout = QVBoxLayout(self.settings_host)
        self.settings_layout.setContentsMargins(6, 6, 6, 6)
        self.settings_page = QLabel()
        self.settings_page.setWordWrap(True)
        self.settings_page.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.settings_page.setContentsMargins(8, 6, 8, 6)
        self.settings_layout.addWidget(self.settings_page)
        self._settings_placeholder(None)
        self.low_page = QListWidget()
        self.low_page.setProperty("flat", True)
        self.high_page = QWidget()
        high_layout = QVBoxLayout(self.high_page)
        high_layout.setContentsMargins(14, 12, 14, 12)
        high_layout.setSpacing(6)
        self.measure_labels: dict[Measure, QLabel] = {}
        for measure in (Measure.MESA, Measure.TILT, Measure.BOWING):
            label = QLabel()
            high_layout.addWidget(label)
            self.measure_labels[measure] = label
        note = _label("Results panels: next step (" + NEXT_STEPS[3].split(" (")[0] + ").", "muted")
        high_layout.addWidget(note)
        high_layout.addStretch(1)
        self.tabs.addTab(self.settings_host, Tab.SETTINGS.label)
        self.tabs.addTab(self.low_page, Tab.LOW_LEVEL.label)
        self.tabs.addTab(self.high_page, Tab.HIGH_LEVEL.label)
        column.addWidget(self.tabs, 1)
        return right

    def _build_statusbar(self) -> None:
        bar = self.statusBar()
        bar.setSizeGripEnabled(False)
        self.user_label = QLabel()
        self.core_label = QLabel()
        self.wl_label = QLabel()
        for widget in (self.wl_label, self.core_label, self.user_label):
            widget.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
            bar.addPermanentWidget(widget)

    # ============================================================================================== theme
    def set_theme(self, name: str | theme.Theme) -> theme.Theme:
        """Switch the application to the theme ``name`` (``light`` / ``dark``) and remember it for the user."""
        chosen = theme.resolve(name)
        if chosen is not theme.current() or not theme.applied():
            theme.apply(None, chosen)
            theme.save(chosen)
        self.apply_theme()
        return chosen

    def apply_theme(self) -> None:
        """Re-make what the style sheet does not draw (icons, image background) in the current theme."""
        current = theme.current()
        for target, name, colour in self._themed_icons:
            target.setIcon(icons.icon(name, getattr(current, colour)))
        self.view.set_colours(current)
        self.theme_action.blockSignals(True)
        self.theme_action.setChecked(current is theme.DARK)
        self.theme_action.blockSignals(False)

    def _settings_placeholder(self, panel: str | None) -> None:
        self._drop_panel()
        steps = "".join(f"<li>{step}</li>" for step in NEXT_STEPS[:2])
        title = f"<b>{panel}</b>: settings panel not ported yet." if panel else "No settings panel open."
        self.settings_page.setText(f"<p>{title}</p><p>Next UI steps for this tab:</p><ul>{steps}</ul>")
        self.settings_page.show()
        self.tabs_caption(panel)

    # ============================================================================================== settings panels
    def show_settings(self, panel: str) -> None:
        """"Open Panel": the settings panel named ``panel`` in the SETTINGS tab (one at a time), else the
        placeholder. A panel with pending edits is asked about first and kept open on "No"."""
        current = self.settings_panel
        if current is not None:
            if current.spec.name == panel:
                return
            if current.model.dirty and not self.confirm(f"Discard the changes of {current.spec.name}?"):
                return
        model = self.controller.settings_model(panel)
        if model is None:
            self._settings_placeholder(panel)
            return
        self._drop_panel()
        self.settings_page.hide()
        widget = SettingsPanel(model, on_close=self.close_settings,
                               running=lambda row=model.spec.row: self.controller.function_running(row),
                               confirm=self.confirm,
                               on_result=lambda text: self.statusBar().showMessage(text, 8000))
        self.settings_layout.addWidget(widget, 1)
        self.settings_panel = widget
        self.tabs_caption(panel)

    def close_settings(self) -> None:
        """"Close Panel": back to the empty SETTINGS tab."""
        self._settings_placeholder(None)

    def _drop_panel(self) -> None:
        panel, self.settings_panel = self.settings_panel, None
        if panel is not None:
            self.settings_layout.removeWidget(panel)
            panel.setParent(None)
            panel.deleteLater()

    def tabs_caption(self, panel: str | None) -> None:
        """The SETTINGS tab caption "SETTINGS (<panel title>)" (producer ``Timeout`` frame 4)."""
        tabs = getattr(self, "tabs", None)
        if tabs is not None:
            tabs.setTabText(int(Tab.SETTINGS), Tab.SETTINGS.label + (f" ({panel})" if panel else ""))

    # ============================================================================================== refresh
    def refresh_all(self) -> None:
        self.refresh_functions()
        self.refresh_profiles_box()
        self.refresh_texts()
        self.refresh_measures()
        self.camera.setCurrentIndex(self.controller.selected_camera())
        self.wl_ctrl.setValue(self.controller.initial_wavelength())
        self.poll_worker.submit(self.controller.wafer_position, on_done=self._show_position)

    def _show_position(self, future: Future[tuple[float, float] | None]) -> None:
        """X_CTRL, Y_CTRL = the XY position in wafer coordinates (init cycle frame 27)."""
        if future.exception() is None and future.result() is not None:
            x, y = future.result()
            self.x_ctrl.setValue(x)
            self.y_ctrl.setValue(y)

    def refresh_functions(self) -> None:
        """"Update functions Status" and "Update Function Start Buttons states"."""
        c = self.controller
        for row in c.rows:
            if row.key in self.indicators:
                self.indicators[row.key].set_look(c.look(row))
            for button in row.starts:
                self.start_buttons[button.key].setEnabled(c.start_enabled(button.key))
            self.stop_buttons[row.key].setEnabled(c.stop_enabled(row))
            if row.key in self.option_buttons:
                self.option_buttons[row.key].setEnabled(c.options_enabled(row))
        self.camera.setEnabled(c.core.functions.condition_ok())
        if self.settings_panel is not None:
            self.settings_panel.refresh_rights()

    def refresh_texts(self) -> None:
        c = self.controller
        self.setWindowTitle(c.title())
        self.status_field.setText(c.system_text())
        self.alarm.setText(c.alarm_text())
        number, mode = c.core_infos()
        self.core_label.setText(f"{number}  {mode}")
        self.user_label.setText(f"USER: {c.user.label}")

    def refresh_profiles_box(self) -> None:
        names = self.controller.profiles()
        current = self.controller.current_profile()
        self.profile.blockSignals(True)
        self.profile.clear()
        self.profile.addItems(names)
        if current in names:
            self.profile.setCurrentIndex(names.index(current))
        self.profile.blockSignals(False)

    def refresh_measures(self) -> None:
        for measure, label in self.measure_labels.items():
            code, text = self.controller.core.measure_status(measure)
            label.setText(f"{measure.name.capitalize()} status: {code} : {text}")

    def on_event(self, event: Any) -> None:
        """A bus event, in the UI thread."""
        parts = self.controller.handle(event)
        if event.topic.startswith("function.") and event.topic.endswith(".state"):
            self._add_state(event)
        if "functions" in parts:
            self.refresh_functions()
        if parts & {"system", "alarm", "title", "infos"}:
            self.refresh_texts()
        if parts & {"profile", "profiles"}:
            self.refresh_profiles_box()
            self.refresh_texts()
        if "profile" in parts and self.settings_panel is not None:
            self.settings_panel.reload()
        if "measures" in parts:
            self.refresh_measures()
        if "tab" in parts:
            tab = self.controller.take_tab()
            if tab is not None:
                self.tabs.setCurrentIndex(int(tab))
        if "visible" in parts and self.controller.visible_request is not None:
            self.setVisible(self.controller.visible_request)

    def _add_state(self, event: Any) -> None:
        name = event.topic.split(".")[1]
        state = event.data.get("state", "")
        self.low_page.addItem(f"{name}: {state}")
        while self.low_page.count() > 200:
            self.low_page.takeItem(0)
        self.low_page.scrollToBottom()

    def poll(self) -> None:
        """The producer's ``Timeout`` case, in the poll worker; the snapshot is shown in :meth:`_apply_snapshot`."""
        self.refresh_functions()
        self.refresh_texts()
        if self._polling:
            return
        self._polling = True
        self.poll_worker.submit(self.controller.poll, on_done=self._apply_snapshot)

    def _apply_snapshot(self, future: Future[Snapshot]) -> None:
        self._polling = False
        if future.exception() is not None:
            return
        snap = future.result()
        for button, state in ((self.xy_joystick, snap.xy_joystick), (self.z_joystick, snap.z_joystick)):
            button.setEnabled(state is not None and snap.joystick_ok)
            if state is not None:
                button.setChecked(bool(state))
        self.f_ms.setText(f"{snap.period_ms:.0f}")
        self.wl_label.setText("WL: -" if snap.wavelength_nm is None else f"WL: {snap.wavelength_nm} nm")

    def refresh_image(self) -> None:
        provider = self.controller.images
        try:
            key = image_key(provider)
        except Exception:
            log.exception("image key")
            return
        if key == self._image_key or self._rendering:
            return
        self._image_key = key
        if key is None:
            self.view.set_image(None)
            return
        self._rendering = True
        self.image_worker.submit(self._render, provider, on_done=self._show_image)

    @staticmethod
    def _render(provider: Any) -> Any:
        merged = provider.merged()
        return None if merged is None else to_qimage(merged)

    def _show_image(self, future: Future[Any]) -> None:
        self._rendering = False
        if future.exception() is None:
            self.view.set_image(future.result())

    # ============================================================================================== actions
    def _act(self, fn: Callable[..., ActionResult], *args: Any,
             then: Callable[[ActionResult], None] | None = None) -> Future[ActionResult]:
        def done(future: Future[ActionResult]) -> None:
            error = future.exception()
            result = ActionResult(False, str(error)) if error is not None else future.result()
            self._apply(result)
            if then is not None:
                then(result)
        return self.actions_worker.submit(fn, *args, on_done=done)

    def _apply(self, result: ActionResult) -> None:
        if result.message:
            self.statusBar().showMessage(result.message, 8000)
        if result.tab is not None:
            self.tabs.setCurrentIndex(int(result.tab))
        if result.tab is Tab.SETTINGS and isinstance(result.data, str):
            self.show_settings(result.data)
        self.refresh_functions()
        self.refresh_texts()

    def press(self, key: str) -> Future[ActionResult]:
        return self._act(self.controller.press, key)

    def stop_function(self, row_key: str) -> Future[ActionResult]:
        return self._act(self.controller.stop, row_key)

    def open_options(self, row_key: str) -> Future[ActionResult]:
        return self._act(self.controller.open_options, row_key)

    def open_settings(self) -> Future[ActionResult]:
        return self._act(self.controller.open_settings)

    def select_camera(self, index: int) -> Future[ActionResult]:
        return self._act(self.controller.select_camera, int(index))

    def set_joystick(self, name: str, on: bool) -> Future[ActionResult]:
        return self._act(self.controller.set_joystick, name, bool(on), then=lambda _r: self.poll())

    def move(self, axis: str) -> Future[ActionResult]:
        return self._act(self.controller.move, axis, self.x_ctrl.value(), self.y_ctrl.value())

    def set_wavelength(self) -> Future[ActionResult]:
        return self._act(self.controller.set_wavelength, self.wl_ctrl.value())

    def load_profile(self, name: str) -> Future[ActionResult]:
        def back(result: ActionResult) -> None:
            if not result.ok:
                self.refresh_profiles_box()
        return self._act(self.controller.load_profile, name, then=back)

    def refresh_profiles(self) -> Future[ActionResult]:
        return self._act(self.controller.refresh_profiles)

    def save_profile(self) -> Future[ActionResult]:
        return self._act(self.controller.save_profile)

    def edit_profiles(self) -> Future[ActionResult]:
        def show(result: ActionResult) -> None:
            if result.ok:
                self.tabs.setCurrentIndex(int(Tab.SETTINGS))
                self.show_settings("Profiles editor")
        return self._act(self.controller.edit_profiles, then=show)

    def clear_alarm(self) -> None:
        self.controller.clear_alarm()
        self.refresh_texts()

    def save_image(self) -> None:
        folder = str(getattr(self.controller.core.folders, "images", ""))
        path, _filter = QFileDialog.getSaveFileName(self, "SAVE IMAGE", folder, "PNG image (*.png)")
        if path:
            self._act(self.controller.save_image, path)

    def login(self) -> None:
        dialog = LoginDialog(self.controller.user, self)
        if dialog.exec() != LoginDialog.DialogCode.Accepted:
            return
        if dialog.logout_requested:
            self._act(self.controller.logout)
        else:
            self._act(self.controller.login, dialog.selected(), dialog.password.text())
        dialog.password.clear()

    def show_log(self) -> None:
        if self.log_window is None:
            self.log_window = LogWindow(self.controller.log_text, self)
        self.log_window.show()
        self.log_window.raise_()

    def about(self) -> None:
        version = self.controller.core.version()
        QMessageBox.about(self, "ABOUT", f"ALOX Vision Core (Python)\nVersion {version.major}.{version.minor}."
                                         f"{version.build}")

    @staticmethod
    def _ask(question: str) -> bool:
        answer = QMessageBox.question(None, "ALOX", question)
        return answer == QMessageBox.StandardButton.Yes

    def exit(self) -> None:
        """EXIT: "CONFIRM EXIT ?", then the ``Exit`` command; without an application to stop, the window closes."""
        if not self.confirm("CONFIRM EXIT ?"):
            return

        def closed(result: ActionResult) -> None:
            if result.ok and self.controller.core.on_shutdown is None:
                self.allow_close()
                self.close()
        self._act(self.controller.request_exit, then=closed)

    def allow_close(self) -> None:
        self._allow_close = True

    def closeEvent(self, event: QCloseEvent) -> None:
        """``Panel Close?``: the same as EXIT unless the application is stopping."""
        if self._allow_close:
            self.shutdown()
            event.accept()
            return
        event.ignore()
        self.exit()

    def shutdown(self) -> None:
        """Stop the timers, the bus listener and the workers (idempotent)."""
        self.poll_timer.stop()
        self.image_timer.stop()
        self.bridge.close()
        for worker in (self.actions_worker, self.poll_worker, self.image_worker):
            worker.shutdown()

    # ============================================================================================== tests, tools
    def flush(self, timeout: float = 5.0) -> None:
        """Wait for the background calls and deliver their results and the queued events (tests, screenshots)."""
        from PySide6.QtCore import QCoreApplication, QEventLoop

        for _ in range(5):
            for worker in (self.actions_worker, self.poll_worker, self.image_worker):
                worker.wait(timeout)
            QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)

    def function_status_text(self, name: FunctionName) -> str:
        for row in self.controller.rows:
            if row.function == name and row.key in self.indicators:
                return self.indicators[row.key].text()
        raise KeyError(name)
