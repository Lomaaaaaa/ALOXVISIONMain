"""The widgets of the operator window: status indicator, image display with its tools, login and log dialogs.

The image display replaces LabVIEW's image control and its tools palette (zoom, pan, point, line, rectangle ROI, and
the ZOOM FIT button): the image (``ImageStore.merged()``, overlays included) in a :class:`QGraphicsView`, the tools
drawn by Qt on top. The line and the ROI are measured in image pixels (the manual measurement in µm through the
calibration, ``alox_manager_manual_measure``, is a later step).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from enum import IntEnum

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QImage,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGraphicsLineItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from alox.core.users import UserType
from alox.ui.controller import StatusLook

__all__ = ["ImageView", "LogWindow", "LoginDialog", "StatusIndicator", "Tool"]


class StatusIndicator(QLabel):
    """A function status: black text on the status colour (transparent for "OFF OK")."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(104, 22)
        self.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self.look: StatusLook | None = None

    def set_look(self, look: StatusLook) -> None:
        if look == self.look:
            return
        self.look = look
        self.setText(look.text)
        background = look.background or "transparent"
        self.setStyleSheet(f"QLabel {{ background-color: {background}; color: {look.foreground}; "
                           f"border: 1px solid #8c8c8c; padding-left: 4px; }}")


class Tool(IntEnum):
    """The tools of the display palette (top to bottom)."""

    ZOOM = 0
    PAN = 1
    POINT = 2
    LINE = 3
    RECT = 4

    @property
    def label(self) -> str:
        return ("Zoom", "Pan", "Point", "Line", "Rectangle ROI")[self.value]


class ImageView(QGraphicsView):
    """The image display. :attr:`info` reports what the tools measure (cursor position and value, line length,
    ROI); :meth:`roi` is the last rectangle drawn ``(left, top, right, bottom)`` in image pixels."""

    info = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QBrush(QColor("#d6d6d6")))
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setFrameShape(QFrame.Shape.Panel)
        self.setFrameShadow(QFrame.Shadow.Sunken)
        self._pixmap = self.scene().addPixmap(QPixmap())
        self._placeholder = QGraphicsSimpleTextItem("NO IMAGE")
        self._placeholder.setBrush(QBrush(QColor("#7a7a7a")))
        self._placeholder.setFont(QFont("DejaVu Sans", 28))
        self.scene().addItem(self._placeholder)
        pen = QPen(QColor("#ff2020"))
        pen.setCosmetic(True)
        pen.setWidth(2)
        self._roi_item = QGraphicsRectItem()
        self._roi_item.setPen(pen)
        self._roi_item.setVisible(False)
        self._line_item = QGraphicsLineItem()
        self._line_item.setPen(pen)
        self._line_item.setVisible(False)
        self.scene().addItem(self._roi_item)
        self.scene().addItem(self._line_item)
        self._image: QImage | None = None
        self._fit = True
        self._tool = Tool.RECT
        self._anchor: QPointF | None = None
        self.set_tool(Tool.RECT)

    # ------------------------------------------------------------------------------------------ image
    @property
    def has_image(self) -> bool:
        return self._image is not None

    def set_image(self, image: QImage | None) -> None:
        """Show ``image`` (None: the placeholder). The first image, or one of another size, is fitted when the
        display is in "zoom to fit"."""
        if image is None or image.isNull():
            self._image = None
            self._pixmap.setPixmap(QPixmap())
            self._placeholder.setVisible(True)
            self.scene().setSceneRect(self._placeholder.boundingRect())
            self.zoom_fit()
            return
        resized = self._image is None or self._image.size() != image.size()
        self._image = image
        self._placeholder.setVisible(False)
        self._pixmap.setPixmap(QPixmap.fromImage(image))
        if resized:
            self.scene().setSceneRect(QRectF(0, 0, image.width(), image.height()))
            if self._fit:
                self.zoom_fit()

    def zoom_fit(self) -> None:
        """ZOOM FIT (LabVIEW "Zoom to Fit")."""
        self._fit = True
        rect = self.scene().sceneRect()
        if rect.width() > 0 and rect.height() > 0:
            self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)

    def zoom(self, factor: float) -> None:
        self._fit = False
        self.scale(factor, factor)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._fit:
            self.zoom_fit()

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.angleDelta().y() == 0:
            return
        self.zoom(1.25 if event.angleDelta().y() > 0 else 0.8)

    # ------------------------------------------------------------------------------------------ tools
    @property
    def tool(self) -> Tool:
        return self._tool

    def set_tool(self, tool: Tool) -> None:
        self._tool = Tool(tool)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag if tool is Tool.PAN
                         else QGraphicsView.DragMode.NoDrag)
        cursor = {Tool.ZOOM: Qt.CursorShape.PointingHandCursor, Tool.PAN: Qt.CursorShape.OpenHandCursor}
        self.viewport().setCursor(cursor.get(self._tool, Qt.CursorShape.CrossCursor))

    def roi(self) -> tuple[int, int, int, int] | None:
        if not self._roi_item.isVisible():
            return None
        r = self._roi_item.rect().normalized()
        return int(r.left()), int(r.top()), int(r.right()), int(r.bottom())

    def clear_roi(self) -> None:
        self._roi_item.setVisible(False)
        self._line_item.setVisible(False)

    def _clamped(self, point: QPointF) -> QPointF:
        rect = self.scene().sceneRect()
        return QPointF(min(max(point.x(), rect.left()), rect.right()), min(max(point.y(), rect.top()), rect.bottom()))

    def _pixel_text(self, point: QPointF) -> str:
        if self._image is None:
            return ""
        x, y = int(point.x()), int(point.y())
        if not (0 <= x < self._image.width() and 0 <= y < self._image.height()):
            return ""
        colour = QColor(self._image.pixel(x, y))
        value = (str(colour.red()) if self._image.format() == QImage.Format.Format_Grayscale8
                 else f"({colour.red()}, {colour.green()}, {colour.blue()})")
        return f"x {x}, y {y}: {value}"

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._tool is Tool.PAN or self._image is None:
            super().mousePressEvent(event)
            return
        point = self._clamped(self.mapToScene(event.position().toPoint()))
        if self._tool is Tool.ZOOM:
            zoom_out = (event.button() == Qt.MouseButton.RightButton
                        or bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier))
            self.zoom(0.5 if zoom_out else 2.0)
        elif self._tool is Tool.POINT:
            self.info.emit(self._pixel_text(point))
        elif event.button() == Qt.MouseButton.LeftButton:
            self._anchor = point
            if self._tool is Tool.LINE:
                self._line_item.setLine(point.x(), point.y(), point.x(), point.y())
                self._line_item.setVisible(True)
            else:
                self._roi_item.setRect(QRectF(point, point))
                self._roi_item.setVisible(True)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        super().mouseMoveEvent(event)
        if self._anchor is None or self._image is None:
            return
        point = self._clamped(self.mapToScene(event.position().toPoint()))
        if self._tool is Tool.LINE:
            self._line_item.setLine(self._anchor.x(), self._anchor.y(), point.x(), point.y())
        elif self._tool is Tool.RECT:
            self._roi_item.setRect(QRectF(self._anchor, point).normalized())

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        super().mouseReleaseEvent(event)
        if self._anchor is None:
            return
        self._anchor = None
        if self._tool is Tool.LINE:
            line = self._line_item.line()
            self.info.emit(f"line: {math.hypot(line.dx(), line.dy()):.1f} px")
        elif self._tool is Tool.RECT:
            roi = self.roi()
            if roi is not None:
                left, top, right, bottom = roi
                self.info.emit(f"ROI [{left}, {top}, {right}, {bottom}]: {right - left} x {bottom - top} px")


class LoginDialog(QDialog):
    """``alox_login_ui``: user and password (the password is never shown nor kept)."""

    def __init__(self, current: UserType, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("LOGIN")
        self.user = QComboBox()
        for user in UserType:
            self.user.addItem(user.label, int(user))
        self.user.setCurrentIndex(int(current))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.logout_requested = False
        form = QFormLayout()
        form.addRow("User", self.user)
        form.addRow("Password", self.password)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        logout = QPushButton("Logout")
        buttons.addButton(logout, QDialogButtonBox.ButtonRole.ResetRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        logout.clicked.connect(self._logout)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _logout(self) -> None:
        self.logout_requested = True
        self.accept()

    def selected(self) -> UserType:
        return UserType(int(self.user.currentData()))


class LogWindow(QDialog):
    """The floating log (``alox_log_ui``): the log window, newest line first, refreshed every second while shown."""

    def __init__(self, text: Callable[[], str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("LOG")
        self.resize(760, 420)
        self._text = text
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setFont(QFont("DejaVu Sans Mono", 9))
        layout = QVBoxLayout(self)
        layout.addWidget(self.view)
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.refresh)

    def refresh(self) -> None:
        text = self._text()
        if text != self.view.toPlainText():
            self.view.setPlainText(text)

    def showEvent(self, event) -> None:
        self.refresh()
        self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)
