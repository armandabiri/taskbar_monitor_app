"""Popup window listing the top processes by CPU / RAM.

Process iteration runs on a background QThread so the UI never blocks
while psutil walks ~300 processes (each cpu_percent/memory_info call
can take a few milliseconds and quickly add up on the UI thread).
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from services.system_info import ProcessRow, get_top_processes

POPUP_REFRESH_MS = 2500


class _ProcessFetcher(QThread):
    """Background worker that polls top processes and emits rows."""

    results_ready = pyqtSignal(list)  # list[ProcessRow]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._sort_by: str = "cpu"
        self._limit: int = 10
        self._running = True
        self._interval_ms = POPUP_REFRESH_MS

    def set_sort(self, mode: str) -> None:
        self._sort_by = mode

    def stop(self) -> None:
        self._running = False
        # Wake the thread if it's in msleep() so it exits promptly.
        self.requestInterruption()

    def run(self) -> None:
        while self._running and not self.isInterruptionRequested():
            try:
                rows = get_top_processes(limit=self._limit, sort_by=self._sort_by)
                self.results_ready.emit(rows)
            except Exception:  # pylint: disable=broad-exception-caught
                self.results_ready.emit([])
            # Sleep in small chunks so stop() wakes us quickly
            total = 0
            while (total < self._interval_ms
                   and self._running
                   and not self.isInterruptionRequested()):
                self.msleep(100)
                total += 100


class TopProcessesPopup(QWidget):
    """Small floating window showing the top processes."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setWindowTitle("Top Processes")
        self.setMinimumSize(320, 220)
        self.setStyleSheet(
            """
            QWidget { background-color: #111; color: #ddd; }
            QTableWidget { background-color: #111; color: #ddd; gridline-color: #222; }
            QHeaderView::section { background-color: #1a1a1a; color: #aaa; padding: 4px; border: 0; }
            QPushButton { background-color: #2a2a2a; color: #ddd; border: 1px solid #333;
                          padding: 3px 8px; border-radius: 3px; }
            QPushButton:checked { background-color: #55efc4; color: #111; }
            """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        controls = QHBoxLayout()
        self.btn_cpu = QPushButton("By CPU")
        self.btn_cpu.setCheckable(True)
        self.btn_cpu.setChecked(True)
        self.btn_ram = QPushButton("By RAM")
        self.btn_ram.setCheckable(True)
        self.btn_cpu.clicked.connect(lambda: self._set_sort("cpu"))
        self.btn_ram.clicked.connect(lambda: self._set_sort("ram"))
        controls.addWidget(self.btn_cpu)
        controls.addWidget(self.btn_ram)
        controls.addStretch(1)
        close_btn = QPushButton("×")
        close_btn.setFixedWidth(22)
        close_btn.clicked.connect(self.hide)
        controls.addWidget(close_btn)
        layout.addLayout(controls)

        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels(["Process", "CPU %", "RAM MB"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setFont(QFont("Segoe UI", 9))
        layout.addWidget(self.table)

        # Placeholder row so the popup shows immediately, before the first
        # worker result arrives.
        self.table.setRowCount(1)
        self.table.setItem(0, 0, QTableWidgetItem("Loading…"))

        self._fetcher: _ProcessFetcher | None = None

    def _set_sort(self, mode: str) -> None:
        self.btn_cpu.setChecked(mode == "cpu")
        self.btn_ram.setChecked(mode == "ram")
        if self._fetcher is not None:
            self._fetcher.set_sort(mode)

    def _apply_rows(self, rows: list[ProcessRow]) -> None:
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(row.name))
            self.table.setItem(i, 1, QTableWidgetItem(f"{row.cpu_percent:.1f}"))
            self.table.setItem(i, 2, QTableWidgetItem(f"{row.ram_mb:.0f}"))

    def showEvent(self, a0) -> None:  # pylint: disable=invalid-name
        """Spawn a background fetcher while the popup is visible."""
        if self._fetcher is None or not self._fetcher.isRunning():
            self._fetcher = _ProcessFetcher(self)
            self._fetcher.results_ready.connect(self._apply_rows)
            self._fetcher.set_sort("cpu" if self.btn_cpu.isChecked() else "ram")
            # Run below normal priority so heavy process enumeration yields
            # to the rest of the UI and the system.
            self._fetcher.start(QThread.Priority.LowPriority)
        super().showEvent(a0)

    def hideEvent(self, a0) -> None:  # pylint: disable=invalid-name
        """Stop the background thread so it doesn't keep polling processes."""
        if self._fetcher is not None:
            self._fetcher.stop()
            # Don't block the UI — give the thread ~500ms to exit, then move on
            QTimer.singleShot(500, self._reap_fetcher)
        super().hideEvent(a0)

    def _reap_fetcher(self) -> None:
        if self._fetcher is not None and self._fetcher.isRunning():
            self._fetcher.wait(200)
        self._fetcher = None

    def closeEvent(self, a0) -> None:  # pylint: disable=invalid-name
        """Ensure the fetcher thread is stopped on close."""
        if self._fetcher is not None:
            self._fetcher.stop()
            self._fetcher.wait(500)
            self._fetcher = None
        super().closeEvent(a0)
