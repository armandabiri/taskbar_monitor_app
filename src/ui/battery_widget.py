"""Compact battery indicator widget."""

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPaintEvent, QPen
from PyQt6.QtWidgets import QWidget

from services.system_info import BatteryStats


BATTERY_WIDTH = 46
BATTERY_HEIGHT = 20


class BatteryWidget(QWidget):
    """Draws a battery icon with percent text and a plugged-in bolt."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(BATTERY_WIDTH, BATTERY_HEIGHT)
        self._stats: BatteryStats | None = None
        self.setToolTip("Battery")

    def update_stats(self, stats: BatteryStats | None) -> None:
        """Update the battery reading; hide the widget if there is no battery."""
        self._stats = stats
        if stats is None:
            self.hide()
        else:
            self.show()
            mins = stats.secs_left // 60 if stats.secs_left > 0 else None
            tip = f"Battery: {stats.percent:.0f}%"
            if stats.plugged:
                tip += " (charging)"
            elif mins is not None:
                tip += f" — {mins // 60}h {mins % 60:02d}m left"
            self.setToolTip(tip)
        self.update()

    def paintEvent(self, a0: QPaintEvent | None) -> None:  # pylint: disable=invalid-name
        del a0
        if self._stats is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        pct = max(0.0, min(self._stats.percent, 100.0))
        if self._stats.plugged:
            fill = QColor(85, 239, 196)
        elif pct < 15:
            fill = QColor(255, 118, 117)
        elif pct < 35:
            fill = QColor(253, 203, 110)
        else:
            fill = QColor(162, 155, 254)

        # Body (left chunk), tip (right small rectangle)
        body = QRectF(1, 4, 20, 12)
        tip = QRectF(21, 8, 2, 4)
        painter.setPen(QPen(QColor(220, 220, 220), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(body)
        painter.setBrush(QColor(220, 220, 220))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(tip)

        # Fill
        inner = QRectF(2, 5, 18 * (pct / 100.0), 10)
        painter.setBrush(fill)
        painter.drawRect(inner)

        # Bolt if charging
        if self._stats.plugged:
            painter.setPen(QPen(QColor(10, 10, 10), 1.2))
            painter.drawLine(12, 6, 9, 11)
            painter.drawLine(9, 11, 13, 11)
            painter.drawLine(13, 11, 10, 15)

        # Percent label
        painter.setPen(QColor(255, 255, 255))
        painter.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        painter.drawText(
            QRectF(24, 0, BATTERY_WIDTH - 24, BATTERY_HEIGHT),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            f"{int(pct)}%",
        )
