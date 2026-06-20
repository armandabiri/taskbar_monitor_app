"""Floating, frameless capture toolbar (FastStone-style) for the app.

This widget is intentionally decoupled from any capture business logic. It
emits no-argument pyqtSignals when its buttons are clicked; a controller is
expected to connect those signals to the actual capture routines.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QHBoxLayout, QPushButton, QWidget

_TOOLBAR_STYLE = (
    "QWidget { background-color: rgba(20, 20, 20, 230); border: 1px solid #444; "
    "border-radius: 8px; }"
    "QPushButton { background-color: rgba(50, 50, 50, 220); color: #eee; "
    "border: 1px solid #555; border-radius: 5px; padding: 6px 8px; font-size: 14px; }"
    "QPushButton:hover { background-color: rgba(80, 80, 80, 230); }"
    "QPushButton:pressed { background-color: rgba(110, 110, 110, 240); }"
)


class CaptureToolbar(QWidget):
    """Compact always-on-top row of capture buttons.

    Signals (all no-arg) let a controller react without this widget knowing
    anything about how captures are performed.
    """

    region_requested = pyqtSignal()
    element_requested = pyqtSignal()
    full_screen_requested = pyqtSignal()
    scrolling_requested = pyqtSignal()
    settings_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setStyleSheet(_TOOLBAR_STYLE)
        self._drag_offset = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)

        self.region_btn = self._make_button(
            "▭ Region", "Capture a rectangular region", self.region_requested
        )
        self.element_btn = self._make_button(
            "⊞ Element", "Capture a single UI element", self.element_requested
        )
        self.full_screen_btn = self._make_button(
            "🖥 Full Screen", "Capture the entire screen", self.full_screen_requested
        )
        self.scrolling_btn = self._make_button(
            "⬇ Scrolling", "Capture a scrolling window", self.scrolling_requested
        )
        self.settings_btn = self._make_button(
            "⚙ Settings", "Open capture settings", self.settings_requested
        )
        for btn in (
            self.region_btn,
            self.element_btn,
            self.full_screen_btn,
            self.scrolling_btn,
            self.settings_btn,
        ):
            layout.addWidget(btn)

        self.close_btn = QPushButton("✕")
        self.close_btn.setToolTip("Close toolbar")
        self.close_btn.clicked.connect(self.close)
        layout.addWidget(self.close_btn)

        self.adjustSize()

    def _make_button(
        self, label: str, tooltip: str, signal: pyqtSignal
    ) -> QPushButton:
        button = QPushButton(label)
        button.setToolTip(tooltip)
        button.clicked.connect(signal.emit)
        return button

    def dock_near(self, widget: QWidget) -> None:
        """Position this toolbar just below (or beside) ``widget``.

        Places the toolbar under the widget's bottom-left corner, nudging it
        sideways if it would otherwise run past the widget's right edge.
        """
        geom = widget.geometry()
        self.adjustSize()
        x = geom.x()
        y = geom.y() + geom.height() + 4
        right_limit = geom.x() + geom.width()
        if x + self.width() > right_limit:
            x = max(geom.x(), right_limit - self.width())
        self.move(x, y)

    # --- Frameless drag support -------------------------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_offset is not None and (
            event.buttons() & Qt.MouseButton.LeftButton
        ):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._drag_offset = None
        super().mouseReleaseEvent(event)
