"""Lightweight overlay for scrolling capture progress and cancel."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget


class ScrollCaptureProgress(QWidget):
    """Small always-on-top strip showing scroll phase and frame count."""

    cancel_clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setStyleSheet(
            "background-color: rgba(20, 20, 20, 220); color: #eee; "
            "border: 1px solid #444; border-radius: 6px; padding: 8px;"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        self._label = QLabel("Scrolling capture…")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._label)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.cancel_clicked.emit)
        layout.addWidget(cancel_btn)
        self.adjustSize()

    def bind_coordinator(self, coordinator) -> None:
        coordinator.progress.connect(self._on_progress)
        coordinator.finished.connect(self.hide)
        coordinator.failed.connect(self.hide)
        coordinator.cancelled.connect(self.hide)

    def _on_progress(self, phase: str, frame_index: int) -> None:
        if phase == "to_top":
            self._label.setText("Scrolling to top…")
        else:
            self._label.setText(f"Capturing frame {frame_index}…")
        self.adjustSize()

    def show_near_parent(self, parent: QWidget) -> None:
        geom = parent.geometry()
        self.move(geom.x() + max(8, geom.width() - self.width() - 8), geom.y() + 8)
        self.show()
