"""The Qt form of a function settings panel, built from its description (:mod:`alox.ui.settings_panels`).

Layout as the LabVIEW ``_ui_config`` panels: the title, the selector when the panel has one (Get Image: CAMERA),
the LabVIEW tabs (SETTINGS, DISPLAY, ...) with their titled groups, the groups shown under the tabs, then a status
line and APPLY / SAVE / CANCEL / CLOSE. Numbers are spin boxes limited to the field's range, Booleans check boxes,
rings combo boxes with the LabVIEW item texts, paths a line with a browse button. Every edit goes through the
:class:`~alox.ui.settings_panels.SettingsModel` (validation); the inputs and APPLY / SAVE / CANCEL are enabled only
with the panel's right (:meth:`SettingsPanel.refresh_rights`).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from alox.ui import theme
from alox.ui.settings_panels import ApplyResult, Field, Group, Kind, SettingsModel

__all__ = ["SettingsPanel"]

_COLUMNS = 2                       # label/input pairs per row of a group
_INT_LIMIT = 2_147_483_647


class _PathEdit(QWidget):
    """A path control: the line and a browse button (file, or folder for ``directory`` fields)."""

    def __init__(self, f: Field, on_change: Callable[[str], None]) -> None:
        super().__init__()
        self.field = f
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.line = QLineEdit()
        self.line.editingFinished.connect(lambda: on_change(self.line.text()))
        self.browse = QToolButton()
        self.browse.setText("…")
        self.browse.setToolTip(f"Choose the {f.label.lower()}")
        self.browse.clicked.connect(lambda _c=False: self._browse(on_change))
        layout.addWidget(self.line, 1)
        layout.addWidget(self.browse)

    def _browse(self, on_change: Callable[[str], None]) -> None:
        start = self.line.text()
        if self.field.directory:
            chosen = QFileDialog.getExistingDirectory(self, self.field.label, start)
        else:
            chosen = QFileDialog.getOpenFileName(self, self.field.label, start)[0]
        if chosen:
            self.line.setText(chosen)
            on_change(chosen)

    def text(self) -> str:
        return self.line.text()

    def setText(self, text: str) -> None:
        self.line.setText(text)


class SettingsPanel(QFrame):
    """One function settings panel on ``model``. ``on_close()`` is called by CLOSE; ``running()`` tells whether the
    function runs (the status line then says that the changes apply to its next run); ``confirm(question)`` asks
    before CLOSE drops pending edits."""

    def __init__(self, model: SettingsModel, *, on_close: Callable[[], None] | None = None,
                 running: Callable[[], bool] = lambda: False,
                 confirm: Callable[[str], bool] = lambda _question: True,
                 on_result: Callable[[str], None] | None = None) -> None:
        super().__init__()
        self.model = model
        self.spec = model.spec
        self._on_close = on_close
        self._running = running
        self._confirm = confirm
        self._on_result = on_result
        self._loading = False
        self.inputs: dict[str, QWidget] = {}            # key template → input
        self.labels: dict[str, QLabel] = {}
        self.buttons: dict[str, QPushButton] = {}
        self.selector: QComboBox | None = None
        self.setFrameShape(QFrame.Shape.NoFrame)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(8)
        title = QLabel(self.spec.title)
        title.setProperty("role", "title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        if self.spec.selector is not None:
            row = QHBoxLayout()
            row.setSpacing(8)
            caption = QLabel(self.spec.selector.label)
            caption.setProperty("role", "field")
            row.addWidget(caption)
            self.selector = QComboBox()
            for value, text in self.spec.selector.items:
                self.selector.addItem(text, value)
            self.selector.setCurrentIndex(self.selector.findData(model.selection))
            self.selector.currentIndexChanged.connect(self._select)
            row.addWidget(self.selector)
            row.addStretch(1)
            layout.addLayout(row)
        self.pages = QTabWidget()
        for page in self.spec.pages:
            self.pages.addTab(self._page(page.groups), page.title)
        layout.addWidget(self.pages, 1)
        for group in self.spec.common:
            layout.addWidget(self._group(group))
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        for name, slot, tip in (("APPLY", self.apply, "Write the changes into the active profile (next run)"),
                                ("SAVE", self.save, "Apply, then save the recipe"),
                                ("CANCEL", self.cancel, "Drop the changes and read the profile again"),
                                ("CLOSE", self.close_panel, "Close the panel")):
            button = QPushButton(name)
            button.setToolTip(tip)
            button.setMinimumWidth(88)
            if name == "APPLY":
                button.setProperty("kind", "primary")
            button.clicked.connect(lambda _c=False, s=slot: s())
            buttons.addWidget(button)
            self.buttons[name] = button
        layout.addLayout(buttons)
        self.refresh()

    # ============================================================================================== building
    def _page(self, groups: tuple[Group, ...]) -> QWidget:
        inner = QWidget()
        column = QVBoxLayout(inner)
        column.setContentsMargins(8, 4, 8, 8)
        column.setSpacing(4)
        for group in groups:
            column.addWidget(self._group(group))
        column.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(inner)
        return scroll

    def _group(self, group: Group) -> QGroupBox:
        box = QGroupBox(group.title)
        grid = QGridLayout(box)
        grid.setContentsMargins(2, 0, 2, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        for index, f in enumerate(group.fields):
            line, pair = divmod(index, _COLUMNS)
            label = QLabel(f.caption)
            label.setProperty("role", "field")
            widget = self._input(f)
            grid.addWidget(label, line, 2 * pair)
            grid.addWidget(widget, line, 2 * pair + 1)
            self.labels[f.key] = label
            self.inputs[f.key] = widget
        for pair in range(_COLUMNS):
            grid.setColumnStretch(2 * pair + 1, 1)
            if pair:
                grid.setColumnMinimumWidth(2 * pair, 120)
        return box

    def _input(self, f: Field) -> QWidget:
        key = f.key
        if f.kind is Kind.CHECK:
            box = QCheckBox()
            box.toggled.connect(lambda on, k=key: self._edit(k, bool(on)))
            return box
        if f.kind is Kind.ENUM:
            combo = QComboBox()
            for code, text in f.items:
                combo.addItem(text, code)
            combo.currentIndexChanged.connect(lambda _i, k=key, c=combo: self._edit(k, c.currentData()))
            return combo
        if f.kind is Kind.PATH:
            return _PathEdit(f, lambda text, k=key: self._edit(k, text))
        spin: QSpinBox | QDoubleSpinBox
        if f.integer:
            spin = QSpinBox()
            spin.setRange(int(max(f.minimum or 0, -_INT_LIMIT)), int(min(f.maximum or 0, _INT_LIMIT)))
            spin.setSingleStep(max(1, int(f.step)))
        else:
            spin = QDoubleSpinBox()
            spin.setDecimals(f.decimals)
            spin.setRange(float(f.minimum or 0.0), float(f.maximum or 0.0))
            spin.setSingleStep(f.step)
        spin.setKeyboardTracking(False)
        spin.setAlignment(Qt.AlignmentFlag.AlignRight)
        if f.unit:
            spin.setToolTip(f"{f.caption}: {f.minimum:g} … {f.maximum:g}")
        spin.valueChanged.connect(lambda value, k=key: self._edit(k, value))
        return spin

    # ============================================================================================== values
    def refresh(self) -> None:
        """Show the model's values (the current selection) and the rights."""
        self._loading = True
        try:
            for f, resolved in self.model.entries():
                self._show(f, self.inputs[f.key], self.model.value(resolved))
                self.labels[f.key].setToolTip(resolved)
                self.inputs[f.key].setToolTip(resolved if f.kind is not Kind.NUMBER
                                              else f"{resolved}: {f.minimum:g} … {f.maximum:g}")
        finally:
            self._loading = False
        self.refresh_rights()
        self._status()

    @staticmethod
    def _show(f: Field, widget: QWidget, value: Any) -> None:
        if isinstance(widget, QCheckBox):
            widget.setChecked(bool(value))
        elif isinstance(widget, QComboBox):
            index = widget.findData(value)
            if index < 0:                                         # a code the ring does not have
                widget.addItem(f"{value} (unknown)", value)
                index = widget.count() - 1
            widget.setCurrentIndex(index)
        elif isinstance(widget, _PathEdit):
            widget.setText(str(value))
        elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
            widget.setValue(value)                                # type: ignore[arg-type]

    def refresh_rights(self) -> None:
        """Enable the inputs and APPLY / SAVE / CANCEL with the right of the current user (``User Rights``)."""
        editable = self.model.editable
        for widget in self.inputs.values():
            widget.setEnabled(editable)
        if self.selector is not None:
            self.selector.setEnabled(self.model.load_error is None)
        dirty = self.model.dirty
        self.buttons["APPLY"].setEnabled(editable and dirty)
        self.buttons["SAVE"].setEnabled(editable)
        self.buttons["CANCEL"].setEnabled(editable and dirty)

    def _edit(self, key: str, value: Any) -> None:
        if self._loading:
            return
        error = self.model.set(key, value)
        if error is not None:
            self._message(error, error=True)
            self._loading = True
            try:
                self._show(self.model.field(key), self.inputs[key], self.model.value(key))
            finally:
                self._loading = False
        else:
            self._status()
        self.refresh_rights()

    def _select(self, _index: int) -> None:
        if self.selector is not None:
            self.model.select(self.selector.currentData())
            self.refresh()

    def _status(self) -> None:
        if self.model.load_error is not None:
            self._message(self.model.load_error, error=True)
            return
        if not self.model.allowed:
            self._message("Read only: no right to change these settings for this user.")
            return
        problems = self.model.problems()
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

    # ============================================================================================== buttons
    def _done(self, result: ApplyResult, verb: str) -> None:
        self.refresh()
        if result.ok:
            text = f"{self.spec.name}: {len(result.written)} setting(s) {verb}"
            if result.archive is not None:
                text += f", recipe saved ({result.archive.name})"
            if result.written and self._running():
                text += "; the function is running: they apply to its next run"
            self._message(text + ".")
        else:
            self._message("; ".join(result.errors.values()), error=True)
            text = f"{self.spec.name}: " + "; ".join(result.errors.values())
        if self._on_result is not None:
            self._on_result(text)

    def apply(self) -> ApplyResult:
        """APPLY: the pending edits into the active profile."""
        result = self.model.apply()
        self._done(result, "applied")
        return result

    def save(self) -> ApplyResult:
        """SAVE: apply, then save the recipe."""
        result = self.model.save()
        self._done(result, "applied")
        return result

    def cancel(self) -> None:
        """CANCEL: drop the pending edits, read the profile again."""
        self.model.cancel()
        self.refresh()

    def reload(self) -> None:
        """The active profile changed (VISION PROFILE): read it again (pending edits are dropped)."""
        self.cancel()

    def close_panel(self) -> bool:
        """CLOSE: asks before dropping pending edits; False when the operator keeps the panel open."""
        if self.model.dirty and not self._confirm(f"Discard the changes of {self.spec.name}?"):
            return False
        if self._on_close is not None:
            self._on_close()
        return True
