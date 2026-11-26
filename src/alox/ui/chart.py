"""A small XY chart drawn with QPainter in the colours of the current theme (:mod:`alox.ui.theme`).

One y axis, "nice" ticks (:func:`nice_ticks`), a recessive grid, the axis titles, a legend for two series or more,
optional markers (a vertical line at a best Z / an optimum, a horizontal line at a target) with a short label. The
series colours are a fixed categorical order (blue, orange, aqua, violet), stepped for each theme; the texts use
the theme's text tokens, never the series colours.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from alox.ui import theme

__all__ = ["SERIES_COLOURS", "Chart", "Marker", "Series", "nice_ticks", "series_colour"]

#: Categorical series colours (light, dark), in their fixed order.
SERIES_COLOURS: tuple[tuple[str, str], ...] = (
    ("#257de0", "#338ced"),     # blue
    ("#f47230", "#e76624"),     # orange
    ("#00b894", "#00a888"),     # aqua
    ("#6a4ce2", "#9887ef"),     # violet
)


def series_colour(index: int, current: theme.Theme | None = None) -> QColor:
    t = current or theme.current()
    light, dark = SERIES_COLOURS[index % len(SERIES_COLOURS)]
    return QColor(dark if t.name == "dark" else light)


def nice_ticks(lo: float, hi: float, count: int = 5) -> list[float]:
    """About ``count`` round tick values (1, 2, 2.5, 5 × 10^n steps) covering [lo, hi]."""
    if not (math.isfinite(lo) and math.isfinite(hi)):
        return []
    if hi < lo:
        lo, hi = hi, lo
    if hi == lo:
        return [lo]
    raw = (hi - lo) / max(1, count)
    power = 10 ** math.floor(math.log10(raw))
    step = next(m * power for m in (1, 2, 2.5, 5, 10) if m * power >= raw - 1e-12 * power)
    first = math.ceil(lo / step - 1e-9) * step
    ticks = []
    value = first
    while value <= hi + step * 1e-9:
        ticks.append(0.0 if abs(value) < step * 1e-9 else value)
        value = first + len(ticks) * step
    return ticks


def _padded(lo: float, hi: float, fraction: float = 0.06) -> tuple[float, float]:
    if hi == lo:
        span = abs(lo) * 0.05 or 1.0
        return lo - span, hi + span
    pad = (hi - lo) * fraction
    return lo - pad, hi + pad


@dataclass(frozen=True)
class Series:
    """Points ``(x, y)``; ``style``: ``line``, ``points`` or ``both``; ``colour``: the categorical slot."""

    name: str
    points: Sequence[tuple[float, float]]
    style: str = "line"
    colour: int = 0
    dashed: bool = False


@dataclass(frozen=True)
class Marker:
    """A vertical (``x``) or horizontal (``y``) line with a label; ``colour`` a slot, or -1 for the text colour."""

    x: float | None = None
    y: float | None = None
    label: str = ""
    colour: int = -1


def _default_format(step: float) -> Callable[[float], str]:
    if not math.isfinite(step) or step <= 0:
        return lambda v: f"{v:.4g}"
    power = math.floor(math.log10(step))
    decimals = max(0, -power + (1 if abs(step / 10 ** power - 2.5) < 1e-9 else 0))
    if step >= 1e5 or step < 1e-4:
        return lambda v: f"{v:.3g}"
    return lambda v: f"{v:.{decimals}f}"


class Chart(QWidget):
    """The chart. ``x_format(value) -> str`` formats the x tick labels (e.g. a time of day)."""

    def __init__(self, x_label: str = "", y_label: str = "", *, x_format: Callable[[float], str] | None = None,
                 empty_text: str = "No data", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.x_label, self.y_label = x_label, y_label
        self.x_format = x_format
        self.empty_text = empty_text
        self.series: list[Series] = []
        self.markers: list[Marker] = []
        self.x_range: tuple[float, float] | None = None
        self.y_range: tuple[float, float] | None = None
        self.setMinimumSize(220, 150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def sizeHint(self) -> QSize:
        return QSize(420, 240)

    def set_data(self, series: Sequence[Series], markers: Sequence[Marker] = (), *,
                 x_range: tuple[float, float] | None = None, y_range: tuple[float, float] | None = None) -> None:
        self.series = [s for s in series if s.points]
        self.markers = list(markers)
        self.x_range, self.y_range = x_range, y_range
        self.update()

    # ---------------------------------------------------------------------------------------------- geometry
    def ranges(self) -> tuple[tuple[float, float], tuple[float, float]] | None:
        """The plotted x and y ranges (data + markers, padded), None without data."""
        xs = [p[0] for s in self.series for p in s.points if math.isfinite(p[0]) and math.isfinite(p[1])]
        ys = [p[1] for s in self.series for p in s.points if math.isfinite(p[0]) and math.isfinite(p[1])]
        if not xs:
            return None
        xs += [m.x for m in self.markers if m.x is not None and math.isfinite(m.x)]
        ys += [m.y for m in self.markers if m.y is not None and math.isfinite(m.y)]
        xr = self.x_range or _padded(min(xs), max(xs), 0.03)
        yr = self.y_range or _padded(min(ys), max(ys), 0.08)
        return xr, yr

    # ---------------------------------------------------------------------------------------------- painting
    def paintEvent(self, _event: QPaintEvent) -> None:
        t = theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        text, muted, grid = QColor(t.text), QColor(t.muted), QColor(t.border)
        font = QFont(self.font())
        small = QFont(font)
        small.setPixelSize(10)
        painter.setFont(small)
        fm = QFontMetrics(small)
        ranges = self.ranges()
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if ranges is None:
            painter.setPen(muted)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.empty_text)
            painter.end()
            return
        (x0, x1), (y0, y1) = ranges
        xticks = [v for v in nice_ticks(x0, x1, max(2, int(rect.width() // 90))) if x0 <= v <= x1]
        yticks = [v for v in nice_ticks(y0, y1, max(2, int(rect.height() // 40))) if y0 <= v <= y1]
        xstep = xticks[1] - xticks[0] if len(xticks) > 1 else (x1 - x0 or 1.0)
        ystep = yticks[1] - yticks[0] if len(yticks) > 1 else (y1 - y0 or 1.0)
        xfmt = self.x_format or _default_format(xstep)
        yfmt = _default_format(ystep)
        ylabels = [yfmt(v) for v in yticks]
        left = 6 + (fm.height() + 4 if self.y_label else 0) + max((fm.horizontalAdvance(s) for s in ylabels),
                                                                  default=0) + 6
        bottom = fm.height() + 6 + (fm.height() + 4 if self.x_label else 0)
        legend_h = fm.height() + 6 if len(self.series) > 1 else 0
        plot = QRectF(rect.left() + left, rect.top() + 6 + legend_h, rect.width() - left - 14,
                      rect.height() - bottom - 10 - legend_h)
        if plot.width() < 20 or plot.height() < 20:
            painter.end()
            return

        def px(x: float) -> float:
            return plot.left() + (x - x0) / (x1 - x0) * plot.width()

        def py(y: float) -> float:
            return plot.bottom() - (y - y0) / (y1 - y0) * plot.height()

        # grid, ticks, labels
        painter.setPen(QPen(grid, 1))
        for v in yticks:
            painter.drawLine(QPointF(plot.left(), py(v)), QPointF(plot.right(), py(v)))
        for v in xticks:
            painter.drawLine(QPointF(px(v), plot.top()), QPointF(px(v), plot.bottom()))
        painter.setPen(QPen(muted, 1))
        painter.drawLine(plot.bottomLeft(), plot.bottomRight())
        painter.drawLine(plot.bottomLeft(), plot.topLeft())
        for v, label in zip(yticks, ylabels, strict=True):
            box = QRectF(rect.left(), py(v) - fm.height() / 2, left - 6, fm.height())
            painter.drawText(box, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, label)
        last_right = -1e9
        for v in xticks:
            label = xfmt(v)
            w = fm.horizontalAdvance(label)
            x = min(max(px(v) - w / 2, rect.left()), rect.right() - w)
            if x < last_right + 8:
                continue
            painter.drawText(QRectF(x, plot.bottom() + 4, w + 2, fm.height()), Qt.AlignmentFlag.AlignLeft, label)
            last_right = x + w
        if self.x_label:
            painter.drawText(QRectF(plot.left(), rect.bottom() - fm.height() - 1, plot.width(), fm.height()),
                             Qt.AlignmentFlag.AlignHCenter, self.x_label)
        if self.y_label:
            painter.save()
            painter.translate(rect.left() + 4 + fm.ascent(), plot.center().y())
            painter.rotate(-90)
            painter.drawText(QRectF(-plot.height() / 2, -fm.ascent(), plot.height(), fm.height()),
                             Qt.AlignmentFlag.AlignHCenter, self.y_label)
            painter.restore()

        # markers
        painter.save()
        painter.setClipRect(plot.adjusted(-1, -1, 1, 1))
        for marker in self.markers:
            colour = text if marker.colour < 0 else series_colour(marker.colour, t)
            pen = QPen(colour, 1.5, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            if marker.x is not None and math.isfinite(marker.x):
                painter.drawLine(QPointF(px(marker.x), plot.top()), QPointF(px(marker.x), plot.bottom()))
            if marker.y is not None and math.isfinite(marker.y):
                painter.drawLine(QPointF(plot.left(), py(marker.y)), QPointF(plot.right(), py(marker.y)))
        painter.restore()

        # series
        painter.save()
        painter.setClipRect(plot.adjusted(-5, -5, 5, 5))
        for s in self.series:
            colour = series_colour(s.colour, t)
            pts = [QPointF(px(x), py(y)) for x, y in s.points if math.isfinite(x) and math.isfinite(y)]
            if s.style in ("line", "both") and len(pts) > 1:
                path = QPainterPath(pts[0])
                for p in pts[1:]:
                    path.lineTo(p)
                pen = QPen(colour, 2, Qt.PenStyle.DashLine if s.dashed else Qt.PenStyle.SolidLine)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(path)
            if s.style in ("points", "both") or len(pts) == 1:
                painter.setPen(QPen(QColor(t.surface), 1.5))
                painter.setBrush(colour)
                radius = 3.5 if len(pts) < 60 else 2.5
                for p in pts:
                    painter.drawEllipse(p, radius, radius)
        painter.restore()

        # marker labels (text colour, on the surface)
        painter.setPen(text)
        for marker in self.markers:
            if not marker.label:
                continue
            w = fm.horizontalAdvance(marker.label) + 8
            if marker.x is not None and math.isfinite(marker.x):
                x = px(marker.x) + 4
                if x + w > plot.right():
                    x = px(marker.x) - 4 - w
                box = QRectF(x, plot.top() + 2, w, fm.height() + 2)
            elif marker.y is not None and math.isfinite(marker.y):
                box = QRectF(plot.right() - w - 2, py(marker.y) - fm.height() - 3, w, fm.height() + 2)
            else:
                continue
            painter.fillRect(box, QColor(t.surface))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, marker.label)

        # legend (two series or more): swatch + name, in the text colour
        if len(self.series) > 1:
            x = plot.right()
            y = rect.top() + 3
            for s in reversed(self.series):
                w = fm.horizontalAdvance(s.name)
                x -= w
                painter.setPen(text)
                painter.drawText(QRectF(x, y, w + 2, fm.height()), Qt.AlignmentFlag.AlignLeft, s.name)
                x -= 18
                colour = series_colour(s.colour, t)
                pen = QPen(colour, 2, Qt.PenStyle.DashLine if s.dashed else Qt.PenStyle.SolidLine)
                mid = y + fm.height() / 2
                if s.style == "points":
                    painter.setPen(QPen(QColor(t.surface), 1))
                    painter.setBrush(colour)
                    painter.drawEllipse(QPointF(x + 7, mid), 3.5, 3.5)
                else:
                    painter.setPen(pen)
                    painter.drawLine(QPointF(x, mid), QPointF(x + 14, mid))
                x -= 14
        painter.end()
