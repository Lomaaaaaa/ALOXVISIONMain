"""The icons of the operator window, drawn in code (vector paths on a 24 × 24 grid, no image files).

:func:`icon` paints one of :data:`NAMES` in a colour (the theme's text, accent or danger colour) at the usual sizes,
with a faded variant for the disabled state; the window re-makes them when the theme changes.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QIcon,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)

__all__ = ["NAMES", "icon", "image", "pixmap"]

_SIZES = (16, 20, 24, 32, 48)
_STROKE = 1.8

Painter = Callable[[QPainter, QColor], None]


def _pen(colour: QColor, width: float = _STROKE) -> QPen:
    pen = QPen(colour, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _stroke(p: QPainter, colour: QColor, path: QPainterPath, width: float = _STROKE) -> None:
    p.setPen(_pen(colour, width))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)


def _fill(p: QPainter, colour: QColor, path: QPainterPath) -> None:
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(colour)
    p.drawPath(path)


def _poly(*points: tuple[float, float], close: bool = False) -> QPainterPath:
    path = QPainterPath(QPointF(*points[0]))
    for point in points[1:]:
        path.lineTo(*point)
    if close:
        path.closeSubpath()
    return path


def _lines(*segments: tuple[float, float, float, float]) -> QPainterPath:
    path = QPainterPath()
    for x1, y1, x2, y2 in segments:
        path.moveTo(x1, y1)
        path.lineTo(x2, y2)
    return path


def _circle(x: float, y: float, r: float) -> QPainterPath:
    path = QPainterPath()
    path.addEllipse(QPointF(x, y), r, r)
    return path


# ================================================================================================== the icons
def _play(p: QPainter, c: QColor) -> None:
    path = _poly((8, 5.5), (18.5, 12), (8, 18.5), close=True)
    p.setPen(_pen(c, 2.2))
    p.setBrush(c)
    p.drawPath(path)


def _stop(p: QPainter, c: QColor) -> None:
    path = QPainterPath()
    path.addRoundedRect(QRectF(5.5, 5.5, 13, 13), 2.5, 2.5)
    _fill(p, c, path)


def _gear(p: QPainter, c: QColor) -> None:
    body = _circle(12, 12, 6.6)
    for k in range(8):
        tooth = QPainterPath()
        tooth.addRoundedRect(QRectF(-1.9, -9.6, 3.8, 4.6), 0.9, 0.9)
        angle = math.radians(45 * k)
        cos, sin = math.cos(angle), math.sin(angle)
        rotated = QPainterPath()
        poly = tooth.toFillPolygon()
        rotated.addPolygon(QPolygonF([QPointF(12 + pt.x() * cos - pt.y() * sin, 12 + pt.x() * sin + pt.y() * cos)
                                      for pt in poly]))
        body = body.united(rotated)
    _fill(p, c, body.subtracted(_circle(12, 12, 2.9)))


def _save(p: QPainter, c: QColor) -> None:
    _stroke(p, c, _poly((5, 4), (16, 4), (20, 8), (20, 20), (4, 20), (4, 5), (5, 4), close=True))
    _stroke(p, c, _poly((8, 20), (8, 14), (16, 14), (16, 20)))
    _stroke(p, c, _poly((8, 4), (8, 8.5), (14, 8.5), (14, 4)))


def _user(p: QPainter, c: QColor) -> None:
    _stroke(p, c, _circle(12, 8.2, 3.7))
    path = QPainterPath(QPointF(4.8, 20))
    path.cubicTo(4.8, 15.6, 8.2, 13.6, 12, 13.6)
    path.cubicTo(15.8, 13.6, 19.2, 15.6, 19.2, 20)
    _stroke(p, c, path)


def _sliders(p: QPainter, c: QColor) -> None:
    _stroke(p, c, _lines((4, 7, 7, 7), (11.5, 7, 20, 7), (4, 12, 13, 12), (17.5, 12, 20, 12),
                         (4, 17, 6, 17), (10.5, 17, 20, 17)))
    for x, y in ((9.25, 7), (15.25, 12), (8.25, 17)):
        _stroke(p, c, _circle(x, y, 2.25))


def _log(p: QPainter, c: QColor) -> None:
    frame = QPainterPath()
    frame.addRoundedRect(QRectF(5, 3.5, 14, 17), 2.2, 2.2)
    _stroke(p, c, frame)
    _stroke(p, c, _lines((8.5, 8.5, 15.5, 8.5), (8.5, 12, 15.5, 12), (8.5, 15.5, 13, 15.5)))


def _info(p: QPainter, c: QColor) -> None:
    _stroke(p, c, _circle(12, 12, 8.6))
    _stroke(p, c, _lines((12, 11, 12, 16.5)), 2.0)
    _fill(p, c, _circle(12, 7.9, 1.25))


def _power(p: QPainter, c: QColor) -> None:
    rect = QRectF(4.5, 5, 15, 15)
    path = QPainterPath()
    path.arcMoveTo(rect, 125)
    path.arcTo(rect, 125, 290)
    _stroke(p, c, path)
    _stroke(p, c, _lines((12, 3.5, 12, 11)))


def _bell_off(p: QPainter, c: QColor) -> None:
    bell = QPainterPath(QPointF(5.5, 16.5))
    bell.cubicTo(6.8, 15.2, 7.2, 13.8, 7.2, 11)
    bell.cubicTo(7.2, 7.6, 9.3, 5.2, 12, 5.2)
    bell.cubicTo(14.7, 5.2, 16.8, 7.6, 16.8, 11)
    bell.cubicTo(16.8, 13.8, 17.2, 15.2, 18.5, 16.5)
    bell.closeSubpath()
    _stroke(p, c, bell)
    _stroke(p, c, _lines((10.3, 19.5, 13.7, 19.5), (4, 4, 20, 20)))


def _eye(p: QPainter, c: QColor) -> None:
    path = QPainterPath(QPointF(2.8, 12))
    path.cubicTo(5.2, 7.6, 8.4, 5.8, 12, 5.8)
    path.cubicTo(15.6, 5.8, 18.8, 7.6, 21.2, 12)
    path.cubicTo(18.8, 16.4, 15.6, 18.2, 12, 18.2)
    path.cubicTo(8.4, 18.2, 5.2, 16.4, 2.8, 12)
    _stroke(p, c, path)
    _stroke(p, c, _circle(12, 12, 2.8))


def _zoom(p: QPainter, c: QColor) -> None:
    _stroke(p, c, _circle(10.5, 10.5, 6.2))
    _stroke(p, c, _lines((15, 15, 20, 20), (8, 10.5, 13, 10.5), (10.5, 8, 10.5, 13)))


def _pan(p: QPainter, c: QColor) -> None:
    _stroke(p, c, _lines((12, 3.5, 12, 20.5), (3.5, 12, 20.5, 12)))
    for path in (_poly((9.5, 6), (12, 3.5), (14.5, 6)), _poly((9.5, 18), (12, 20.5), (14.5, 18)),
                 _poly((6, 9.5), (3.5, 12), (6, 14.5)), _poly((18, 9.5), (20.5, 12), (18, 14.5))):
        _stroke(p, c, path)


def _point(p: QPainter, c: QColor) -> None:
    _stroke(p, c, _lines((12, 3.5, 12, 9), (12, 15, 12, 20.5), (3.5, 12, 9, 12), (15, 12, 20.5, 12)))
    _fill(p, c, _circle(12, 12, 1.4))


def _line(p: QPainter, c: QColor) -> None:
    _stroke(p, c, _lines((6, 18, 18, 6)))
    _fill(p, c, _circle(6, 18, 2.2))
    _fill(p, c, _circle(18, 6, 2.2))


def _rect(p: QPainter, c: QColor) -> None:
    pen = _pen(c)
    pen.setCapStyle(Qt.PenCapStyle.FlatCap)
    pen.setDashPattern([2.2, 1.6])
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRect(QRectF(4.5, 6, 15, 12))


def _fit(p: QPainter, c: QColor) -> None:
    for path in (_poly((4, 9), (4, 4), (9, 4)), _poly((15, 4), (20, 4), (20, 9)),
                 _poly((20, 15), (20, 20), (15, 20)), _poly((9, 20), (4, 20), (4, 15))):
        _stroke(p, c, path)
    frame = QPainterPath()
    frame.addRoundedRect(QRectF(8.5, 9, 7, 6), 1, 1)
    _stroke(p, c, frame, 1.5)


def _refresh(p: QPainter, c: QColor) -> None:
    rect = QRectF(5, 5, 14, 14)
    start = 35.0
    path = QPainterPath()
    path.arcMoveTo(rect, start)
    path.arcTo(rect, start, 285)
    _stroke(p, c, path)
    a = math.radians(start)
    x, y = 12 + 7 * math.cos(a), 12 - 7 * math.sin(a)
    d = (math.sin(a), math.cos(a))                     # clockwise tangent (screen coordinates)
    n = (math.cos(a), -math.sin(a))                    # outwards
    tip = (x + 3.2 * d[0], y + 3.2 * d[1])
    _fill(p, c, _poly(tip, (x + 2.8 * n[0], y + 2.8 * n[1]), (x - 2.8 * n[0], y - 2.8 * n[1]), close=True))


def _edit(p: QPainter, c: QColor) -> None:
    _stroke(p, c, _poly((15, 4.5), (19.5, 9), (9, 19.5), (4.5, 19.5), (4.5, 15), close=True))
    _stroke(p, c, _lines((12.5, 7, 17, 11.5)))


def _folder(p: QPainter, c: QColor) -> None:
    _stroke(p, c, _poly((3.5, 6), (9.5, 6), (11.5, 8.5), (20.5, 8.5), (20.5, 18.5), (3.5, 18.5), close=True))


def _chevron_down(p: QPainter, c: QColor) -> None:
    _stroke(p, c, _poly((5.5, 9), (12, 15.5), (18.5, 9)), 2.6)


def _chevron_up(p: QPainter, c: QColor) -> None:
    _stroke(p, c, _poly((5.5, 15), (12, 8.5), (18.5, 15)), 2.6)


def _check(p: QPainter, c: QColor) -> None:
    _stroke(p, c, _poly((5, 12.5), (10, 17.5), (19, 7)), 3.0)


def _moon(p: QPainter, c: QColor) -> None:
    _fill(p, c, _circle(12, 12, 8).subtracted(_circle(16.5, 8, 6.5)))


def _sun(p: QPainter, c: QColor) -> None:
    _fill(p, c, _circle(12, 12, 4.2))
    rays = []
    for k in range(8):
        a = math.radians(45 * k)
        rays.append((12 + 6.8 * math.cos(a), 12 + 6.8 * math.sin(a), 12 + 9 * math.cos(a), 12 + 9 * math.sin(a)))
    _stroke(p, c, _lines(*rays), 2.0)


_PAINTERS: dict[str, Painter] = {
    "play": _play, "stop": _stop, "gear": _gear, "save": _save, "user": _user, "sliders": _sliders, "log": _log,
    "info": _info, "power": _power, "bell-off": _bell_off, "eye": _eye, "zoom": _zoom, "pan": _pan,
    "point": _point, "line": _line, "rect": _rect, "fit": _fit, "refresh": _refresh, "edit": _edit,
    "folder": _folder, "moon": _moon, "sun": _sun, "chevron-down": _chevron_down, "chevron-up": _chevron_up,
    "check": _check}
#: The icon names.
NAMES = tuple(_PAINTERS)


def image(name: str, colour: str | QColor, size: int = 24, ratio: float = 1.0) -> QImage:
    """``name`` painted in ``colour`` on a transparent ``size`` × ``size`` image (device pixel ``ratio``)."""
    painter_fn = _PAINTERS[name]
    img = QImage(round(size * ratio), round(size * ratio), QImage.Format.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(ratio)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.scale(size / 24.0, size / 24.0)
        painter_fn(p, QColor(colour))
    finally:
        p.end()
    return img


def pixmap(name: str, colour: str | QColor, size: int = 24, ratio: float = 1.0) -> QPixmap:
    """:func:`image` as a pixmap."""
    return QPixmap.fromImage(image(name, colour, size, ratio))


def icon(name: str, colour: str, disabled: str | None = None) -> QIcon:
    """The icon ``name`` in ``colour``; disabled: ``disabled`` (default: ``colour`` at 40 % opacity, so a red stop
    stays red when it cannot be pressed)."""
    faded = QColor(disabled or colour)
    if disabled is None:
        faded.setAlphaF(0.4)
    app = QGuiApplication.instance()
    ratio = max(1.0, app.devicePixelRatio()) if app is not None else 1.0       # type: ignore[attr-defined]
    result = QIcon()
    for size in _SIZES:
        result.addPixmap(pixmap(name, colour, size, ratio), QIcon.Mode.Normal)
        result.addPixmap(pixmap(name, faded, size, ratio), QIcon.Mode.Disabled)
    return result
