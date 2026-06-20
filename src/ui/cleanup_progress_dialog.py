"""Modeless progress overlay for a running cleanup, with a Cancel button.

Modeled on :class:`ui.scroll_capture_progress.ScrollCaptureProgress`: a small
always-on-top strip that shows the current phase (and scan progress) so a
cleanup never looks frozen, and lets the user stop it mid-run.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget

from services.resource_control.progress import CleanupPhase, CleanupProgress


class CleanupProgressDialog(QWidget):
    """Small always-on-top strip showing cleanup phase + scan progress."""

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
            "background-color: rgba(20, 20, 20, 230); color: #eee; "
            "border: 1px solid #444; border-radius: 6px; padding: 8px;"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        self._label = QLabel("Starting cleanup…")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setMinimumWidth(220)
        layout.addWidget(self._label)

        self._bar = QProgressBar()
        self._bar.setTextVisible(False)
        self._bar.setRange(0, 0)  # indeterminate until we know a total
        self._bar.setFixedHeight(8)
        layout.addWidget(self._bar)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self._on_cancel)
        layout.addWidget(self._cancel_btn)
        self.adjustSize()

    def reset(self) -> None:
        """Restore the dialog to its pre-run state for reuse."""
        self._label.setText("Starting cleanup…")
        self._bar.setRange(0, 0)
        self._bar.setValue(0)
        self._cancel_btn.setEnabled(True)
        self._cancel_btn.setText("Cancel")

    def on_progress(self, progress: object) -> None:
        """Slot for ``CleanupRunner.progress`` (emits a CleanupProgress)."""
        if not isinstance(progress, CleanupProgress):
            return
        if progress.phase == CleanupPhase.SCANNING and progress.total:
            text = f"{progress.phase_label}… ({progress.scanned}/{progress.total})"
            self._bar.setRange(0, progress.total)
            self._bar.setValue(min(progress.scanned, progress.total))
        else:
            text = f"{progress.phase_label}…"
            self._bar.setRange(0, 0)  # indeterminate for non-scan phases
        self._label.setText(text)
        self.adjustSize()

    def _on_cancel(self) -> None:
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.setText("Cancelling…")
        self._label.setText("Cancelling…")
        self.cancel_clicked.emit()

    def show_near_parent(self, parent: QWidget) -> None:
        self.reset()
        geom = parent.geometry()
        self.move(geom.x() + max(8, geom.width() - self.width() - 8), geom.y() + 8)
        self.show()
