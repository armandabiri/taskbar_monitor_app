"""Post-capture screenshot editor with a simple annotation toolbar."""

from __future__ import annotations

import math

from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmap, QPolygonF
from PyQt6.QtWidgets import (
    QButtonGroup,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

_DIALOG_STYLE = """
QDialog { background-color: #1a1a1a; color: #ddd; }
QLabel { color: #ccc; }
QScrollArea { background-color: #202020; border: 1px solid #333; }
QPushButton {
    background-color: #2a2a2a; color: #eee; border: 1px solid #444;
    border-radius: 3px; padding: 4px 12px;
}
QPushButton:hover { background-color: #3a3a3a; border-color: #55efc4; }
QPushButton:pressed { background-color: #4a4a4a; }
QPushButton:checked { background-color: #55efc4; color: #111; border-color: #55efc4; }
"""

_TOOLS = ("Arrow", "Text", "Rectangle", "Blur", "Crop")
_MAX_DISPLAY = 1100


class _ImageCanvas(QLabel):
    """QLabel that displays the working image and reports image-space drags."""

    def __init__(self, dialog: ScreenshotEditorDialog) -> None:
        super().__init__()
        self._dialog = dialog
        self.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.setMouseTracking(True)
        self._start: QPoint | None = None

    def _to_image(self, pos: QPoint) -> QPoint:
        scale = self._dialog.display_scale
        return QPoint(int(pos.x() / scale), int(pos.y() / scale))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._start = self._to_image(event.position().toPoint())
        self._dialog.on_press(self._start)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._start is not None:
            self._dialog.on_move(self._start, self._to_image(event.position().toPoint()))

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._start is not None:
            self._dialog.on_release(self._start, self._to_image(event.position().toPoint()))
            self._start = None


class ScreenshotEditorDialog(QDialog):
    """Edit a QImage with arrow, text, rectangle, blur, and crop tools."""

    def __init__(self, image: QImage, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Screenshot")
        self.setStyleSheet(_DIALOG_STYLE)
        self._working = image.convertToFormat(QImage.Format.Format_ARGB32)
        self._active_tool = "Arrow"
        self._color = QColor("#ff5555")
        self.display_scale = self._compute_scale()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        layout.addLayout(self._build_toolbar())

        self._canvas = _ImageCanvas(self)
        self._scroll = QScrollArea()
        self._scroll.setWidget(self._canvas)
        self._scroll.setWidgetResizable(False)
        layout.addWidget(self._scroll, 1)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        save_btn = button_box.button(QDialogButtonBox.StandardButton.Save)
        if save_btn is not None:
            save_btn.setText("Save")
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self._refresh()

    def _build_toolbar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)
        self._tool_group = QButtonGroup(self)
        self._tool_group.setExclusive(True)
        for name in _TOOLS:
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.clicked.connect(lambda _checked, n=name: self.set_active_tool(n))
            self._tool_group.addButton(btn)
            row.addWidget(btn)
            if name == self._active_tool:
                btn.setChecked(True)
        row.addStretch(1)
        color_btn = QPushButton("Color…")
        color_btn.clicked.connect(self._choose_color)
        row.addWidget(color_btn)
        return row

    def _compute_scale(self) -> float:
        longest = max(self._working.width(), self._working.height(), 1)
        if longest <= _MAX_DISPLAY:
            return 1.0
        return _MAX_DISPLAY / longest

    def _refresh(self) -> None:
        pixmap = QPixmap.fromImage(self._working)
        if self.display_scale != 1.0:
            pixmap = pixmap.scaled(
                int(self._working.width() * self.display_scale),
                int(self._working.height() * self.display_scale),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        self._canvas.setPixmap(pixmap)
        self._canvas.resize(pixmap.size())

    def set_active_tool(self, name: str) -> None:
        if name in _TOOLS:
            self._active_tool = name

    @property
    def active_tool(self) -> str:
        return self._active_tool

    def _choose_color(self) -> None:
        chosen = QColorDialog.getColor(self._color, self, "Annotation Color")
        if chosen.isValid():
            self._color = chosen

    def _pen(self, width: int = 3) -> QPen:
        pen = QPen(self._color)
        pen.setWidth(width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        return pen

    def on_press(self, point: QPoint) -> None:
        if self._active_tool == "Text":
            self._draw_text(point)

    def on_move(self, start: QPoint, current: QPoint) -> None:
        # Previews are intentionally omitted to keep the working copy authoritative.
        return

    def on_release(self, start: QPoint, end: QPoint) -> None:
        if self._active_tool == "Arrow":
            self._draw_arrow(start, end)
        elif self._active_tool == "Rectangle":
            self._draw_rectangle(start, end)
        elif self._active_tool == "Blur":
            self._apply_blur(QRect(start, end).normalized())
        elif self._active_tool == "Crop":
            self._apply_crop(QRect(start, end).normalized())
        self._refresh()

    def _draw_arrow(self, start: QPoint, end: QPoint) -> None:
        painter = QPainter(self._working)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(self._pen())
        painter.drawLine(start, end)
        angle = math.atan2(end.y() - start.y(), end.x() - start.x())
        size = 16.0
        spread = math.pi / 7
        tip = QPolygonF([
            end.toPointF(),
            QPoint(
                int(end.x() - size * math.cos(angle - spread)),
                int(end.y() - size * math.sin(angle - spread)),
            ).toPointF(),
            QPoint(
                int(end.x() - size * math.cos(angle + spread)),
                int(end.y() - size * math.sin(angle + spread)),
            ).toPointF(),
        ])
        painter.setBrush(self._color)
        painter.drawPolygon(tip)
        painter.end()

    def _draw_rectangle(self, start: QPoint, end: QPoint) -> None:
        painter = QPainter(self._working)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(self._pen())
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRect(start, end).normalized())
        painter.end()

    def _draw_text(self, point: QPoint) -> None:
        text, ok = QInputDialog.getText(self, "Add Text", "Text:")
        if not ok or not text:
            return
        painter = QPainter(self._working)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = painter.font()
        font.setPointSize(18)
        painter.setFont(font)
        painter.setPen(self._pen(1))
        painter.drawText(point, text)
        painter.end()
        self._refresh()

    def _apply_blur(self, rect: QRect) -> None:
        region = rect.intersected(self._working.rect())
        if region.width() < 2 or region.height() < 2:
            return
        patch = self._working.copy(region)
        factor = max(1, min(region.width(), region.height()) // 8)
        small = patch.scaled(
            max(1, region.width() // factor),
            max(1, region.height() // factor),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        blurred = small.scaled(
            region.width(),
            region.height(),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        painter = QPainter(self._working)
        painter.drawImage(region.topLeft(), blurred)
        painter.end()

    def _apply_crop(self, rect: QRect) -> None:
        region = rect.intersected(self._working.rect())
        if region.width() < 2 or region.height() < 2:
            return
        self._working = self._working.copy(region)
        self.display_scale = self._compute_scale()

    def result_image(self) -> QImage:
        return self._working

    @classmethod
    def edit(cls, image: QImage, parent: QWidget | None = None) -> QImage | None:
        dialog = cls(image, parent=parent)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return dialog.result_image()
        return None
