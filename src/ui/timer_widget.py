"""Countdown timer widget for the taskbar monitor.

Features:
- Right-click context menu to pick 5, 10, 15, 30, or 60 minutes.
- Countdown display in MM:SS format.
- When time expires: visual alarm (flashing), optional system beep,
  and negative elapsed-time display (e.g. -01:23).
- Click the timer to cancel / reset.
"""

import logging

from PyQt6.QtCore import QPointF, QRectF, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import (
    QAction,
    QColor,
    QContextMenuEvent,
    QFont,
    QMouseEvent,
    QPaintEvent,
    QPainter,
    QPen,
)
from PyQt6.QtWidgets import QMenu, QWidget

try:
    import winsound as _winsound
except ImportError:
    _winsound = None

LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TIMER_FONT = "Segoe UI"
TIMER_FONT_SIZE = 9
TIMER_MIN_WIDTH = 60
TIMER_MIN_HEIGHT = 20

TIMER_PRESETS: tuple[tuple[str, int], ...] = (
    ("5 min", 5),
    ("10 min", 10),
    ("15 min", 15),
    ("30 min", 30),
    ("60 min", 60),
)

ALARM_FLASH_INTERVAL_MS = 500
ALARM_BEEP_FREQ = 1200
ALARM_BEEP_DURATION_MS = 150
ALARM_BEEP_REPEAT_INTERVAL_MS = 3000

COLOR_IDLE = QColor(120, 120, 120)
COLOR_RUNNING = QColor(85, 239, 196)       # green
COLOR_ALARM_ON = QColor(255, 118, 117)      # red
COLOR_ALARM_OFF = QColor(80, 30, 30)
COLOR_OVERTIME = QColor(253, 203, 110)      # amber

MENU_STYLESHEET = """
QMenu { background-color: #1a1a1a; color: white; border: 1px solid #333; padding: 5px; }
QMenu::item:selected { background-color: #333; }
"""


class CountdownTimerWidget(QWidget):
    """Compact countdown timer with right-click duration picker."""

    # Emitted when the alarm first fires
    alarm_triggered = pyqtSignal()
    # Thread-safe signals for global shortcuts
    request_start = pyqtSignal(int)
    request_stop = pyqtSignal()
    request_adjust = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the countdown timer widget."""
        super().__init__(parent)
        self.setMinimumSize(TIMER_MIN_WIDTH, TIMER_MIN_HEIGHT)
        self.setToolTip("Timer [Presets: 1-5, Stop: 0, Adjust: +/-] (Ctrl+Shift+Alt)")

        # Timer state
        self._remaining_seconds: int = 0
        self._is_running: bool = False
        self._is_alarm: bool = False
        self._overtime_seconds: int = 0
        self._flash_on: bool = False
        self._last_preset_minutes: int = 5  # Default increment/decrement

        # Connect thread-safe signals
        self.request_start.connect(self._handle_request_start)
        self.request_stop.connect(self.stop)
        self.request_adjust.connect(self.adjust_time)

        # Tick timer — fires every second
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(1000)
        self._tick_timer.timeout.connect(self.tick)

        # Flash timer — fires during alarm phase
        self._flash_timer = QTimer(self)
        self._flash_timer.setInterval(ALARM_FLASH_INTERVAL_MS)
        self._flash_timer.timeout.connect(self._on_flash)

        # Beep timer — periodic alarm sound
        self._beep_timer = QTimer(self)
        self._beep_timer.setInterval(ALARM_BEEP_REPEAT_INTERVAL_MS)
        self._beep_timer.timeout.connect(self._play_beep)

    def _handle_request_start(self, minutes: int) -> None:
        """Handle signal to start a new countdown and track preset."""
        self._last_preset_minutes = minutes
        self.start_countdown(minutes, orchestrated=True)

    def adjust_time(self, minutes: int) -> None:
        """Add or subtract minutes from the remaining/overtime count."""
        if not self._is_running:
            # If not running, start it with the adjustment amount if positive
            if minutes > 0:
                self.start_countdown(minutes, orchestrated=True)
            return

        delta = minutes * 60
        if self._remaining_seconds > 0:
            self._remaining_seconds = max(0, self._remaining_seconds + delta)
            if self._remaining_seconds == 0 and delta < 0:
                self._trigger_alarm()
        else:
            # Adjust overtime
            # If we add minutes to overtime, we actually reduce overtime or go back to countdown
            total_seconds = (-self._overtime_seconds) + delta
            if total_seconds > 0:
                self._remaining_seconds = total_seconds
                self._overtime_seconds = 0
                self._is_alarm = False
                self._flash_timer.stop()
                self._beep_timer.stop()
            else:
                self._overtime_seconds = max(0, -total_seconds)
        
        self.update()
        LOGGER.info("Timer adjusted by %d minutes. New state: %s", minutes, self._format_time())

    @property
    def last_preset_minutes(self) -> int:
        """Return the last selected preset duration or default."""
        return self._last_preset_minutes

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def start_countdown(self, minutes: int, orchestrated: bool = False) -> None:
        """Start a new countdown for the given number of minutes.

        If orchestrated is True, the caller is responsible for calling tick() every second.
        """
        self.stop()
        self._remaining_seconds = minutes * 60
        self._overtime_seconds = 0
        self._is_running = True
        self._is_alarm = False
        if not orchestrated:
            self._tick_timer.start()
        self.update()
        LOGGER.info("Countdown started: %d minutes (orchestrated=%s)", minutes, orchestrated)

    def stop(self) -> None:
        """Stop and reset the timer."""
        self._tick_timer.stop()
        self._flash_timer.stop()
        self._beep_timer.stop()
        self._is_running = False
        self._is_alarm = False
        self._remaining_seconds = 0
        self._overtime_seconds = 0
        self._flash_on = False
        self.update()

    def tick(self) -> None:
        """Manually tick the timer by one second (useful for orchestration)."""
        if not self._is_running:
            return

        if self._remaining_seconds > 0:
            self._remaining_seconds -= 1
            if self._remaining_seconds == 0:
                self._trigger_alarm()
        else:
            # Overtime mode
            self._overtime_seconds += 1
        self.update()

    def _trigger_alarm(self) -> None:
        """Start the alarm phase."""
        self._is_alarm = True
        self._flash_timer.start()
        self._beep_timer.start()
        self._play_beep()
        self.alarm_triggered.emit()
        LOGGER.info("Timer alarm triggered")

    def _on_flash(self) -> None:
        """Toggle flash state during alarm."""
        self._flash_on = not self._flash_on
        self.update()

    @staticmethod
    def _play_beep() -> None:
        """Play a system beep (Windows only)."""
        if _winsound is not None:
            try:
                _winsound.Beep(ALARM_BEEP_FREQ, ALARM_BEEP_DURATION_MS)
            except (RuntimeError, OSError):
                pass

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------
    def _format_time(self) -> str:
        """Return a display string for the current timer state."""
        if not self._is_running:
            return "--:--"
        if self._remaining_seconds > 0:
            mins, secs = divmod(self._remaining_seconds, 60)
            return f"{mins:02d}:{secs:02d}"
        # Overtime — show negative time
        mins, secs = divmod(self._overtime_seconds, 60)
        return f"-{mins:02d}:{secs:02d}"

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------
    def paintEvent(self, a0: QPaintEvent | None) -> None:  # pylint: disable=invalid-name
        """Render the timer text with appropriate color."""
        del a0
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Choose color
        if not self._is_running:
            color = COLOR_IDLE
        elif self._is_alarm:
            color = COLOR_ALARM_ON if self._flash_on else COLOR_ALARM_OFF
        elif self._remaining_seconds <= 0:
            color = COLOR_OVERTIME
        else:
            color = COLOR_RUNNING

        # 1. Draw SVG-like Clock Icon
        icon_size = 14
        icon_rect = QRectF(4, (self.height() - icon_size) / 2, icon_size, icon_size)
        
        painter.setPen(QPen(color, 1.2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # Outer circle
        painter.drawEllipse(icon_rect)
        # Clock hands
        center = icon_rect.center()
        painter.drawLine(center, QPointF(center.x(), center.y() - icon_size/4)) # hour
        painter.drawLine(center, QPointF(center.x() + icon_size/4, center.y())) # minute

        # 2. Draw Text
        font = QFont(TIMER_FONT, TIMER_FONT_SIZE, QFont.Weight.Medium)
        painter.setFont(font)
        text = self._format_time()

        text_rect = self.rect().adjusted(22, 0, 0, -1)
        align = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft

        # Shadow
        painter.setPen(QColor(0, 0, 0, 180))
        painter.drawText(text_rect.translated(1, 1), align, text)

        # Foreground
        painter.setPen(color)
        painter.drawText(text_rect, align, text)

        # Pulsing ring indicator when alarm is active
        if self._is_alarm and self._flash_on:
            ring_pen = QPen(COLOR_ALARM_ON, 1.5)
            painter.setPen(ring_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            # Draw animated ring around text
            center = self.rect().center()
            radius = min(self.width(), self.height()) // 2 - 2
            phase = (self._overtime_seconds % 4) * 90
            painter.drawArc(
                center.x() - radius,
                center.y() - radius,
                radius * 2,
                radius * 2,
                phase * 16,
                270 * 16,
            )

    # ------------------------------------------------------------------
    # Mouse events
    # ------------------------------------------------------------------
    def mousePressEvent(self, a0: QMouseEvent | None) -> None:  # pylint: disable=invalid-name
        """Left-click stops the timer if running."""
        if a0 is not None and a0.button() == Qt.MouseButton.LeftButton and self._is_running:
            self.stop()
            return
        super().mousePressEvent(a0)

    def contextMenuEvent(self, a0: QContextMenuEvent | None) -> None:  # pylint: disable=invalid-name
        """Show right-click menu to select countdown duration."""
        if a0 is None:
            return
        menu = QMenu(self)
        menu.setStyleSheet(MENU_STYLESHEET)

        for i, (label, minutes) in enumerate(TIMER_PRESETS, 1):
            action = QAction(f"⏲ {label}   [{i}]", self)
            # Use a closure to capture m correctly
            def make_handler(m_val: int):
                def handler(_checked: bool):
                    self._handle_request_start(m_val)
                return handler
            action.triggered.connect(make_handler(minutes))
            menu.addAction(action)

        if self._is_running:
            menu.addSeparator()
            stop_action = QAction("⏹ Stop Timer   [0]", self)
            stop_action.triggered.connect(self.stop)
            menu.addAction(stop_action)

        menu.exec(a0.globalPos())
