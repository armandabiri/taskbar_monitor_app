"""Theme and painting logic for TaskbarMonitor."""

from PyQt6.QtCore import Qt, QRect
from PyQt6.QtGui import QColor, QPainter
from core.config import BACKGROUND_RED, BACKGROUND_GREEN, BACKGROUND_BLUE, PERCENT_MAX


class ThemeEngine:
    """Helper class for consistent UI painting and coloring."""

    @staticmethod
    def get_dynamic_color(value: float) -> QColor:
        """Map utilization percent to a blue-to-red gradient."""
        ratio = min(max(value / PERCENT_MAX, 0.0), 1.0)
        red = int(45 + (231 - 45) * ratio)
        green = int(133 + (76 - 133) * ratio)
        blue = int(219 + (60 - 219) * ratio)
        return QColor(red, green, blue)

    @staticmethod
    def paint_background(painter: QPainter, rect: QRect, opacity: int) -> None:
        """Paint the translucent monitor background."""
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(BACKGROUND_RED, BACKGROUND_GREEN, BACKGROUND_BLUE, opacity))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(rect)
