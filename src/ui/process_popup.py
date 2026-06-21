"""Popup window listing the top processes by CPU / RAM.

The popup is a pure consumer of the shared ``SystemSampler`` snapshot —
it never walks ``psutil.process_iter`` itself. When visible it listens for
``snapshot_ready`` and re-renders from ``snap.top_processes``; when hidden
it disconnects to avoid pointless work.
"""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt
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

from services.system_info import ProcessRow

POPUP_ROW_LIMIT = 10


class TopProcessesPopup(QWidget):
    """Small floating window showing the top processes from the shared sampler."""

    def __init__(self, sampler: Any = None, parent: QWidget | None = None) -> None:
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

        self.table.setRowCount(1)
        self.table.setItem(0, 0, QTableWidgetItem("Loading…"))

        self._sampler = sampler
        self._sort_by = "cpu"
        self._connected = False

    def _set_sort(self, mode: str) -> None:
        self.btn_cpu.setChecked(mode == "cpu")
        self.btn_ram.setChecked(mode == "ram")
        self._sort_by = mode
        if self._sampler is not None:
            latest = self._sampler.latest()
            if latest is not None and latest.top_processes is not None:
                self.apply_snapshot(latest)

    def apply_snapshot(self, snap: Any) -> None:
        """Render the popup from a SystemSnapshot.top_processes payload."""
        rows: list[ProcessRow] = list(snap.top_processes or [])
        if self._sort_by == "ram":
            rows.sort(key=lambda r: r.ram_mb, reverse=True)
        else:
            rows.sort(key=lambda r: r.cpu_percent, reverse=True)
        rows = rows[:POPUP_ROW_LIMIT]
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(row.name))
            self.table.setItem(i, 1, QTableWidgetItem(f"{row.cpu_percent:.1f}"))
            self.table.setItem(i, 2, QTableWidgetItem(f"{row.ram_mb:.0f}"))

    def showEvent(self, a0) -> None:  # pylint: disable=invalid-name
        if self._sampler is not None and not self._connected:
            self._sampler.snapshot_ready.connect(self.apply_snapshot)
            self._connected = True
            latest = self._sampler.latest()
            if latest is not None and latest.top_processes is not None:
                self.apply_snapshot(latest)
        super().showEvent(a0)

    def hideEvent(self, a0) -> None:  # pylint: disable=invalid-name
        if self._sampler is not None and self._connected:
            try:
                self._sampler.snapshot_ready.disconnect(self.apply_snapshot)
            except (TypeError, RuntimeError):
                pass
            self._connected = False
        super().hideEvent(a0)
