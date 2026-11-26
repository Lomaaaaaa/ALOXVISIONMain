"""The results panels of the LOW-LEVEL / HIGH-LEVEL MEASUREMENTS tabs (the ``alox_function_*_ui_run.vi`` run panels),
thin over the Qt-free models of :mod:`alox.ui.results`.

:class:`ResultsPage` (one per tab) shows the panel of the model it is given: :class:`MesaPanel` (oxidation chart,
rate, remaining time, diameters, wavelength, position), :class:`BowingPanel` (the nine Z on a wafer map, Bowing /
Parallelism A and B with their validity), :class:`TiltPanel` (angle, method, uncertainty, validity),
:class:`FocusPanel` (Z / score curve, best Z), :class:`WavelengthPanel` (contrast spectrum, optimum),
:class:`ExposurePanel` (the two measures and the resulting exposure), :class:`CirclePanel` and :class:`OxydPanel`
(diameters tables). The panels draw with the theme's style sheet properties (``role``, ``status``) and, for the
charts and the wafer map, the current theme's tokens; they are only touched in the UI thread.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import ClassVar

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from alox.core.functions import FunctionName
from alox.ui import theme
from alox.ui.chart import Chart, Marker, Series, series_colour, time_ticks
from alox.ui.results import (
    DASH,
    HIGH,
    BowingResults,
    CircleResults,
    ExposureResults,
    FocusResults,
    MesaResults,
    OxydResults,
    Results,
    TableRow,
    TiltResults,
    Validity,
    WavelengthResults,
    fmt,
    fmt_time,
)

__all__ = ["BowingPanel", "CirclePanel", "ExposurePanel", "FocusPanel", "MesaPanel", "OxydPanel", "ResultsPage",
           "TiltPanel", "WaferMap", "WavelengthPanel"]

#: The badge (``status`` style property) of a validity.
VALIDITY_BADGE = {Validity.VALID: "finished", Validity.INVALID: "error", Validity.UNKNOWN: "off-ok"}


def _label(text: str = "", role: str | None = None) -> QLabel:
    label = QLabel(text)
    if role:
        label.setProperty("role", role)
    return label


def _value_field() -> QLineEdit:
    field = QLineEdit()
    field.setReadOnly(True)
    field.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
    field.setMinimumWidth(70)
    return field


class Badge(QLabel):
    """A pill in the theme's status badge colours (``VALID`` green, ``INVALID`` red, ``RUNNING`` amber ...)."""

    def __init__(self, text: str = DASH, key: str = "off-ok", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedHeight(22)
        self.setMinimumWidth(72)
        self.key = ""
        self.set(text, key)

    def set(self, text: str, key: str) -> None:
        if self.text() != text:
            self.setText(text)
        if key != self.key:
            self.key = key
            theme.repolish(self, status=key)

    def set_validity(self, validity: Validity) -> None:
        self.set(validity.value, VALIDITY_BADGE[validity])


class ProgressLine(QWidget):
    """A thin progress bar (0–100 %) in the theme's colours."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.value = 0.0
        self.setFixedHeight(8)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_value(self, percent: float) -> None:
        value = min(100.0, max(0.0, percent)) if math.isfinite(percent) else 0.0
        if value != self.value:
            self.value = value
            self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:
        t = theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(t.border))
        painter.drawRoundedRect(rect, 4, 4)
        if self.value > 0:
            done = QRectF(rect.left(), rect.top(), max(rect.height(), rect.width() * self.value / 100.0),
                          rect.height())
            painter.setBrush(QColor(t.accent))
            painter.drawRoundedRect(done, 4, 4)
        painter.end()


class Fields(QWidget):
    """Captioned read-only values on ``columns`` columns, (re)built when the captions change."""

    def __init__(self, columns: int = 2, wide: Sequence[str] = ("POSITION (mm)", "MESSAGE", "LOST"),
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.columns = columns
        self.wide = frozenset(wide)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(10)
        self.grid.setVerticalSpacing(2)
        self.captions: list[str] = []
        self.values: dict[str, QLineEdit] = {}

    def set_fields(self, fields: Sequence[tuple[str, str]]) -> None:
        captions = [c for c, _ in fields]
        if captions != self.captions:
            while self.grid.count():
                item = self.grid.takeAt(0)
                if item.widget() is not None:
                    item.widget().deleteLater()
            self.values.clear()
            row = col = 0
            for caption in captions:
                span = 1
                if caption in self.wide:
                    row, col, span = row + (col > 0), 0, self.columns
                self.grid.addWidget(_label(caption, "field"), row * 2, col, 1, span)
                field = _value_field()
                self.grid.addWidget(field, row * 2 + 1, col, 1, span)
                self.values[caption] = field
                col += span
                if col >= self.columns:
                    row, col = row + 1, 0
            self.grid.setRowStretch(2 * (row + (col > 0)), 1)
            self.captions = captions
        for caption, text in fields:
            field = self.values[caption]
            if field.text() != text:
                field.setText(text)
                field.setCursorPosition(0)

    def text(self, caption: str) -> str:
        return self.values[caption].text()


class Table(QWidget):
    """A small table of labels: a header row, then one row per :class:`alox.ui.results.TableRow` (the
    highlighted one in bold)."""

    def __init__(self, columns: Sequence[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.columns = tuple(columns)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(14)
        self.grid.setVerticalSpacing(3)
        for c, name in enumerate(("", *self.columns)):
            head = _label(name, "field")
            head.setAlignment(Qt.AlignmentFlag.AlignRight if c else Qt.AlignmentFlag.AlignLeft)
            self.grid.addWidget(head, 0, c)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Maximum)
        self.cells: list[list[QLabel]] = []
        self.empty = _label("No result", "muted")
        self.grid.addWidget(self.empty, 1, 0, 1, len(self.columns) + 1)

    def set_rows(self, rows: Sequence[TableRow]) -> None:
        while len(self.cells) < len(rows):
            r = len(self.cells) + 1
            line = []
            for c in range(len(self.columns) + 1):
                cell = _label("", None if c else "name")
                cell.setAlignment((Qt.AlignmentFlag.AlignRight if c else Qt.AlignmentFlag.AlignLeft)
                                  | Qt.AlignmentFlag.AlignVCenter)
                self.grid.addWidget(cell, r, c)
                line.append(cell)
            self.cells.append(line)
        self.grid.setRowStretch(len(self.cells) + 1, 1)
        for i, line in enumerate(self.cells):
            visible = i < len(rows)
            for c, cell in enumerate(line):
                cell.setVisible(visible)
                if not visible:
                    continue
                row = rows[i]
                cell.setText(row.label if c == 0 else row.cells[c - 1])
                font = QFont(cell.font())
                font.setBold(row.highlight)
                cell.setFont(font)
        self.empty.setVisible(not rows)

    def cell(self, row: int, column: int) -> str:
        return self.cells[row][column].text()


# ================================================================================================== panels
class ResultsPanel(QWidget):
    """Title, outcome badge, the SEMI job line (high level), a body and the FUNCTION STATE line."""

    show_job = False

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        column = QVBoxLayout(self)
        column.setContentsMargins(12, 10, 12, 8)
        column.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(10)
        self.title = _label("", "title")
        head.addWidget(self.title)
        self.job_labels: dict[str, QLabel] = {}
        if self.show_job:
            head.addSpacing(10)
            for caption in ("PROCESS JOB", "LOT", "WAFER", "ZONE"):
                head.addWidget(_label(caption, "field"))
                value = _label(DASH, "name")
                value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                head.addWidget(value)
                head.addSpacing(6)
                self.job_labels[caption] = value
        head.addStretch(1)
        self.badge = Badge("IDLE", "off-ok")
        head.addWidget(self.badge)
        column.addLayout(head)
        self.body = QHBoxLayout()
        self.body.setSpacing(14)
        column.addLayout(self.body, 1)
        foot = QHBoxLayout()
        foot.setSpacing(8)
        foot.addWidget(_label("FUNCTION STATE", "field"))
        self.state = _label(DASH, "name")
        self.state.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        foot.addWidget(self.state, 1)
        self.progress: ProgressLine | None = None
        column.addLayout(foot)
        self.foot = foot

    def add_progress(self) -> ProgressLine:
        self.foot.addWidget(_label("PROGRESS", "field"))
        self.progress = ProgressLine()
        self.progress.setFixedWidth(160)
        self.foot.addWidget(self.progress)
        return self.progress

    def refresh(self, model: Results) -> None:
        self.title.setText(model.title)
        self.badge.set(model.badge_text(), model.badge())
        for caption, text in model.job_fields():
            if caption in self.job_labels:
                self.job_labels[caption].setText(text)
        self.state.setText(model.state_text())
        self.state.setToolTip(model.state_text())
        self.update_body(model)

    def update_body(self, model: Results) -> None:
        """Subclasses."""


class MesaPanel(ResultsPanel):
    """MESA MEASUREMENT: OXIDATION GRAPH (the oxide diameter, or depth / side by the report's measure type, over
    time and the fitted rate line, the target as a dashed line when in range) and the values."""

    show_job = True

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.chart = Chart("TIME", "OXIDE DIAMETER (µm)", x_format=fmt_time, x_ticks=time_ticks,
                           empty_text="No measurement yet")
        self.body.addWidget(self.chart, 3)
        self.fields = Fields(2)
        self.body.addWidget(self.fields, 2)

    def update_body(self, model: Results) -> None:
        assert isinstance(model, MesaResults)
        self.chart.y_label = model.value_label()
        series = [Series("OXIDE", model.samples, "points", 0), Series("FIT", model.fit_line(), "line", 1)]
        markers = []
        if model.samples and model.target > 0:
            ys = [d for _t, d in model.samples]
            span = max(ys) - min(ys) or 1.0
            if min(ys) - span <= model.target <= max(ys) + span:
                markers.append(Marker(y=model.target, label=f"TARGET {fmt(model.target, 2)} µm"))
        self.chart.set_data(series, markers)
        self.fields.set_fields(model.fields())


class WaferMap(QWidget):
    """The bowing points on a wafer drawing (as the LabVIEW panel: ZW2 at the centre, ZW1…ZW5 on the two axes
    inside the wafer, the chuck points ZC1…ZC5 outside); measured points filled, the current one ringed."""

    #: (row, unit direction x, y (up), radius factor) of each point.
    LAYOUT: ClassVar[dict[str, tuple[int, int, float]]] = {
        "ZW2": (0, 0, 0.0), "ZW3": (1, 1, 0.62), "ZC3": (1, 1, 1.32), "ZW1": (-1, -1, 0.62), "ZC1": (-1, -1, 1.32),
        "ZW4": (1, -1, 0.62), "ZC4": (1, -1, 1.32), "ZW5": (-1, 1, 0.62), "ZC5": (-1, 1, 1.32)}

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.points: list[tuple[str, str, float]] = []
        self.current: str | None = None
        self.setMinimumSize(260, 180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def sizeHint(self) -> QSize:
        return QSize(380, 240)

    def set_points(self, points: Sequence[tuple[str, str, float]], current: str | None) -> None:
        self.points, self.current = list(points), current
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:
        t = theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        small = QFont(self.font())
        small.setPixelSize(10)
        bold = QFont(small)
        bold.setBold(True)
        fm = QFontMetrics(small)
        w, h = self.width(), self.height()
        centre = QPointF(w / 2, h / 2 + 2)
        radius = min(h * 0.33, w * 0.24)
        diag = radius / math.sqrt(2)
        # the wafer and the two axes
        painter.setPen(QPen(QColor(t.border), 1.2))
        painter.setBrush(QColor(t.surface_alt))
        painter.drawEllipse(centre, radius, radius)
        axis_pen = QPen(QColor(t.muted), 1, Qt.PenStyle.DashLine)
        painter.setPen(axis_pen)
        reach = 1.32 * diag
        painter.drawLine(QPointF(centre.x() - reach, centre.y() + reach), QPointF(centre.x() + reach,
                                                                                    centre.y() - reach))
        painter.drawLine(QPointF(centre.x() - reach, centre.y() - reach), QPointF(centre.x() + reach,
                                                                                    centre.y() + reach))
        painter.setFont(bold)
        painter.setPen(QColor(t.muted))
        for name, side in (("A", 1), ("B", -1)):           # beside each axis, near the centre
            at = QPointF(centre.x() + side * (0.35 * diag + 7), centre.y() - 0.35 * diag + 7)
            painter.drawText(QRectF(at.x() - 8, at.y() - 8, 16, 16), Qt.AlignmentFlag.AlignCenter, name)
        measured_colour = series_colour(0, t)
        running = QColor(t.running.border)
        values = {row: z for row, _name, z in self.points}
        for row, (dx, dy, k) in self.LAYOUT.items():
            p = QPointF(centre.x() + dx * diag * k, centre.y() - dy * diag * k)
            z = values.get(row, math.nan)
            measured = math.isfinite(z)
            chuck = row.startswith("ZC")
            if row == self.current:
                painter.setPen(QPen(running, 2.5))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(p, 9, 9)
            painter.setPen(QPen(measured_colour if measured else QColor(t.muted), 1.5))
            painter.setBrush(measured_colour if measured else QColor(t.surface))
            if chuck:
                painter.drawRect(QRectF(p.x() - 4.5, p.y() - 4.5, 9, 9))
            else:
                painter.drawEllipse(p, 4.5, 4.5)
            value = f"{z:.4f}" if measured else DASH
            tw = max(fm.horizontalAdvance(value), fm.horizontalAdvance(row)) + 2
            if dx == 0:
                box = QRectF(p.x() - tw / 2, p.y() + 9, tw, 2 * fm.height())
                align = Qt.AlignmentFlag.AlignHCenter
            elif dx > 0:
                box = QRectF(p.x() + 11, p.y() - fm.height(), tw, 2 * fm.height())
                align = Qt.AlignmentFlag.AlignLeft
            else:
                box = QRectF(p.x() - 11 - tw, p.y() - fm.height(), tw, 2 * fm.height())
                align = Qt.AlignmentFlag.AlignRight
            painter.setFont(bold)
            painter.setPen(QColor(t.muted))
            painter.drawText(QRectF(box.left(), box.top(), box.width(), fm.height()), align, row)
            painter.setFont(small)
            painter.setPen(QColor(t.text))
            painter.drawText(QRectF(box.left(), box.top() + fm.height(), box.width(), fm.height()), align, value)
        painter.setFont(small)
        painter.setPen(QColor(t.muted))
        painter.drawText(QRectF(4, h - fm.height() - 2, w - 8, fm.height()), Qt.AlignmentFlag.AlignLeft,
                         "Z (mm)   ● wafer   ■ chuck")
        painter.end()


class BowingPanel(ResultsPanel):
    """BOWING MEASUREMENT: Bowing / Parallelism A and B (µm) with validity, the wafer map of the nine Z."""

    show_job = True

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        left = QWidget()
        grid = QGridLayout(left)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)
        grid.addWidget(_label("AXIS A", "field"), 0, 1, Qt.AlignmentFlag.AlignHCenter)
        grid.addWidget(_label("AXIS B", "field"), 0, 2, Qt.AlignmentFlag.AlignHCenter)
        self.values: dict[str, QLineEdit] = {}
        self.badges: dict[str, Badge] = {}
        for r, (caption, key) in enumerate((("BOWING (µm)", "bowing"), ("PARALLELISM (µm)", "parallelism"))):
            grid.addWidget(_label(caption, "field"), 1 + 2 * r, 0)
            for c, axis in enumerate(("a", "b")):
                field = _value_field()
                field.setAlignment(Qt.AlignmentFlag.AlignRight)
                grid.addWidget(field, 1 + 2 * r, 1 + c)
                badge = Badge()
                grid.addWidget(badge, 2 + 2 * r, 1 + c, Qt.AlignmentFlag.AlignHCenter)
                self.values[f"{key}_{axis}"] = field
                self.badges[f"{key}_{axis}"] = badge
        grid.addWidget(_label("POINT", "field"), 5, 0)
        self.point = _label(DASH, "name")
        grid.addWidget(self.point, 5, 1, 1, 2)
        grid.setRowStretch(6, 1)
        self.body.addWidget(left, 2)
        self.map = WaferMap()
        self.body.addWidget(self.map, 3)
        self.add_progress()

    def update_body(self, model: Results) -> None:
        assert isinstance(model, BowingResults)
        for axis in ("a", "b"):
            bowing, parallelism, bowing_ok, parallelism_ok = model.result(axis)
            self.values[f"bowing_{axis}"].setText(fmt(bowing, 3))
            self.values[f"parallelism_{axis}"].setText(fmt(parallelism, 3))
            self.badges[f"bowing_{axis}"].set_validity(bowing_ok)
            self.badges[f"parallelism_{axis}"].set_validity(parallelism_ok)
        current = model.current_row()
        self.point.setText(f"{current}  ({model.measured} / 9 measured)" if current else f"{model.measured} / 9 "
                                                                                          "measured")
        self.map.set_points(model.points(), current)
        assert self.progress is not None
        self.progress.set_value(model.progress)


class TiltPanel(ResultsPanel):
    """TILT MEASUREMENT: the angle (large), its validity, the method, the uncertainty and the values."""

    show_job = True

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        left = QVBoxLayout()
        left.setSpacing(6)
        left.addWidget(_label("TILT ANGLE", "field"))
        self.angle = QLabel(DASH)
        font = QFont(self.angle.font())
        font.setPixelSize(34)
        font.setBold(True)
        self.angle.setFont(font)
        left.addWidget(self.angle)
        self.valid = Badge()
        left.addWidget(self.valid, 0, Qt.AlignmentFlag.AlignLeft)
        left.addStretch(1)
        self.body.addLayout(left, 2)
        self.fields = Fields(2)
        self.body.addWidget(self.fields, 3)

    def update_body(self, model: Results) -> None:
        assert isinstance(model, TiltResults)
        self.angle.setText(fmt(model.angle, 4, "°").replace(" °", "°"))
        self.valid.set_validity(model.validity())
        self.fields.set_fields(model.fields())


class _ChartPanel(ResultsPanel):
    """A chart on the left, the values (and a validity badge) on the right."""

    validity_caption = "FOUND"
    has_progress = True

    def __init__(self, x_label: str, y_label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.chart = Chart(x_label, y_label, empty_text="No measurement yet")
        self.body.addWidget(self.chart, 3)
        side = QVBoxLayout()
        side.setSpacing(6)
        self.fields = Fields(2)
        side.addWidget(self.fields)
        line = QHBoxLayout()
        line.addWidget(_label(self.validity_caption, "field"))
        self.valid = Badge()
        line.addWidget(self.valid)
        line.addStretch(1)
        side.addLayout(line)
        side.addStretch(1)
        self.body.addLayout(side, 2)
        if self.has_progress:
            self.add_progress()


class FocusPanel(_ChartPanel):
    """AUTOFOCUS / REFOCUS: the score against Z, the best Z marked."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Z POSITION (mm)", "SCORE", parent)

    def update_body(self, model: Results) -> None:
        assert isinstance(model, FocusResults)
        best = model.best()
        markers = [] if best is None else [Marker(x=best[0], label=f"BEST {best[0]:.4f} mm", colour=1)]
        self.chart.set_data([Series("FOCUS", model.curve(), "both", 0)], markers)
        self.fields.set_fields(model.fields())
        self.valid.set_validity(model.found())
        assert self.progress is not None
        self.progress.set_value(model.progress)


class WavelengthPanel(_ChartPanel):
    """SEARCH WAVELENGTH: NON OXID / OXID intensities and the contrast against the wavelength, the optimum and the
    minimum contrast marked."""

    validity_caption = "VALID"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("WAVELENGTH (nm)", "INTENSITY / CONTRAST", parent)

    def update_body(self, model: Results) -> None:
        assert isinstance(model, WavelengthResults)
        series = [Series(name, points, "line", i) for i, (name, points) in enumerate(model.series().items())]
        markers = []
        optimum = model.optimum()
        if optimum is not None:
            markers.append(Marker(x=optimum[0], label=f"OPTIMUM {optimum[0]:.0f} nm", colour=2))
        if math.isfinite(model.min_contrast) and model.min_contrast > 0:
            markers.append(Marker(y=model.min_contrast, label="MIN. CONTRAST"))
        self.chart.set_data(series, markers)
        self.fields.set_fields(model.fields())
        self.valid.set_validity(model.validity())
        assert self.progress is not None
        self.progress.set_value(model.progress)


class ExposurePanel(_ChartPanel):
    """AUTO EXPOSURE: brightness against exposure (the two measures, the line through them), the exposure found."""

    has_progress = False

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("EXPOSURE TIME (µs)", "BRIGHTNESS (grayscale)", parent)

    def update_body(self, model: Results) -> None:
        assert isinstance(model, ExposureResults)
        series = [Series("MEASURES", model.points(), "points", 0), Series("LINE", model.line(), "line", 0,
                                                                          dashed=True)]
        result = model.result_point()
        markers = []
        if result is not None:
            series.append(Series("RESULT", [result], "points", 1))
            markers.append(Marker(x=result[0], label=f"{result[0]:.0f} µs", colour=1))
        self.chart.set_data(series, markers)
        self.fields.set_fields(model.fields())
        self.valid.set_validity(model.validity())


class _TablePanel(ResultsPanel):
    def __init__(self, columns: Sequence[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.table = Table(columns)
        box = QVBoxLayout()
        box.addWidget(self.table, 0, Qt.AlignmentFlag.AlignLeft)
        box.addStretch(1)
        self.body.addLayout(box, 3)
        self.side = QVBoxLayout()
        self.side.setSpacing(6)
        self.body.addLayout(self.side, 2)


class CirclePanel(_TablePanel):
    """DETECT CIRCLE: the circles found (best in bold), found and count."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(CircleResults.COLUMNS, parent)
        line = QHBoxLayout()
        line.addWidget(_label("FOUND", "field"))
        self.valid = Badge()
        line.addWidget(self.valid)
        line.addStretch(1)
        self.side.addLayout(line)
        self.fields = Fields(1)
        self.side.addWidget(self.fields)
        self.side.addStretch(1)

    def update_body(self, model: Results) -> None:
        assert isinstance(model, CircleResults)
        self.table.set_rows(model.rows())
        self.valid.set_validity(model.validity())
        self.fields.set_fields([("CIRCLES", str(model.count)), ("PIXEL SIZE (µm)", fmt(model.pixel_size, 4)),
                                ("PROCESS TIME (ms)", fmt(model.process_ms, 1))])


class OxydPanel(_TablePanel):
    """MEASURE OXYD: the diameters (pixels, µm; the fit in bold), the ratio, deviation, accuracy and found."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(OxydResults.COLUMNS, parent)
        line = QHBoxLayout()
        line.addWidget(_label("MESA", "field"))
        self.mesa = Badge()
        line.addWidget(self.mesa)
        line.addWidget(_label("OXIDE", "field"))
        self.oxyd = Badge()
        line.addWidget(self.oxyd)
        line.addStretch(1)
        self.side.addLayout(line)
        self.fields = Fields(2)
        self.side.addWidget(self.fields)
        self.side.addStretch(1)

    def update_body(self, model: Results) -> None:
        assert isinstance(model, OxydResults)
        self.table.set_rows(model.rows())
        mesa, oxyd = model.found()
        self.mesa.set_validity(mesa)
        self.oxyd.set_validity(oxyd)
        self.fields.set_fields(model.fields())


#: The panel of each function.
PANELS: dict[FunctionName, Callable[[], ResultsPanel]] = {
    FunctionName.MESA_MEASURE: MesaPanel,
    FunctionName.BOWING_MEASURE: BowingPanel,
    FunctionName.TILT_MEASURE: TiltPanel,
    FunctionName.AUTOFOCUS_LARGE: FocusPanel,
    FunctionName.REFOCUS: FocusPanel,
    FunctionName.SEARCH_WAVELENGTH: WavelengthPanel,
    FunctionName.AUTO_EXPOSURE: ExposurePanel,
    FunctionName.DETECT_CIRCLE: CirclePanel,
    FunctionName.MEASURE_OXYD: OxydPanel,
}


class ResultsPage(QStackedWidget):
    """A tab's content (``alox_manager_*_results_panels``): the panel of the function shown, created on first use;
    a note until a function has run."""

    def __init__(self, level: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.level = level
        which = "high-level" if level == HIGH else "low-level"
        self.placeholder = _label(f"No {which} measurement since the start.", "muted")
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.addWidget(self.placeholder)
        self.panels: dict[FunctionName, ResultsPanel] = {}
        self.function: FunctionName | None = None
        self.setFrameShape(QFrame.Shape.NoFrame)

    def show_model(self, model: Results | None) -> ResultsPanel | None:
        if model is None:
            self.setCurrentWidget(self.placeholder)
            self.function = None
            return None
        panel = self.panels.get(model.function)
        if panel is None:
            panel = PANELS[model.function]()
            self.panels[model.function] = panel
            self.addWidget(panel)
        panel.refresh(model)
        if self.currentWidget() is not panel:
            self.setCurrentWidget(panel)
        self.function = model.function
        return panel

    @property
    def panel(self) -> ResultsPanel | None:
        widget = self.currentWidget()
        return widget if isinstance(widget, ResultsPanel) else None

    def retheme(self) -> None:
        """Repaint the drawn parts (charts, wafer map, progress) in the current theme."""
        for panel in self.panels.values():
            for child in panel.findChildren(QWidget):
                if isinstance(child, (Chart, WaferMap, ProgressLine)):
                    child.update()
