"""Dialog for browsing recent cleanup runs."""

from __future__ import annotations

import time

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from services.resource_control.history import read_history
from services.resource_control.models import CleanupHistoryEntry, format_skip_reason

_DIALOG_STYLE = """
QDialog { background-color: #1a1a1a; color: #ddd; }
QLabel { color: #ddd; }
QTextEdit, QTableWidget {
    background-color: #1f1f1f; color: #eee; border: 1px solid #333;
    selection-background-color: #2c2c2c;
}
QHeaderView::section { background-color: #2a2a2a; color: #aaa; padding: 4px; border: 0; }
QPushButton {
    background-color: #2a2a2a; color: #eee; border: 1px solid #444;
    border-radius: 3px; padding: 5px 12px;
}
QPushButton:hover { background-color: #3a3a3a; border-color: #55efc4; }
"""


class CleanupHistoryDialog(QDialog):
    """Show recent cleanup runs stored on disk."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Cleanup History")
        self.setStyleSheet(_DIALOG_STYLE)
        self.setMinimumSize(920, 560)
        self._entries: list[CleanupHistoryEntry] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        hint = QLabel("Recent cleanup runs are stored locally and trimmed to a bounded retention size.")
        layout.addWidget(hint)

        self._table = QTableWidget(0, 5, self)
        self._table.setHorizontalHeaderLabels(["Time", "Mode", "Profile", "Snapshot", "Summary"])
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        vertical_header = self._table.verticalHeader()
        if vertical_header is not None:
            vertical_header.setVisible(False)
        header = self._table.horizontalHeader()
        if header is not None:
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self._table.currentCellChanged.connect(self._on_selection_changed)
        layout.addWidget(self._table, 1)

        self._details = QTextEdit(self)
        self._details.setReadOnly(True)
        layout.addWidget(self._details, 1)

        btn_row = QHBoxLayout()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh)
        btn_row.addWidget(refresh_btn)
        btn_row.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        self._refresh()

    def _refresh(self) -> None:
        self._entries = read_history()
        self._table.setRowCount(len(self._entries))
        for row, entry in enumerate(self._entries):
            self._table.setItem(
                row,
                0,
                QTableWidgetItem(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(entry.timestamp))),
            )
            self._table.setItem(row, 1, QTableWidgetItem(entry.mode))
            self._table.setItem(row, 2, QTableWidgetItem(entry.profile_name))
            self._table.setItem(row, 3, QTableWidgetItem(entry.snapshot_name or ""))
            self._table.setItem(row, 4, QTableWidgetItem(entry.summary))
        if self._entries:
            self._table.selectRow(0)
            self._render_details(self._entries[0])
        else:
            self._details.setPlainText("No cleanup history is available yet.")

    def _on_selection_changed(self, current_row: int, _current_column: int, _prev_row: int, _prev_col: int) -> None:
        if current_row < 0 or current_row >= len(self._entries):
            return
        self._render_details(self._entries[current_row])

    def _render_details(self, entry: CleanupHistoryEntry) -> None:
        blocked = ", ".join(
            f"{format_skip_reason(name)} ({count})"
            for name, count in sorted(entry.blocked_reason_counts.items(), key=lambda item: (-item[1], item[0]))[:5]
        ) or "None"
        issues = " | ".join(entry.errors[:5]) or "None"
        text = "\n".join(
            [
                entry.summary,
                f"Run ID: {entry.run_id}",
                f"Mode: {entry.mode}",
                f"Profile: {entry.profile_name}",
                f"Snapshot: {entry.snapshot_name or '-'}",
                (
                    f"Counts: cleaned={entry.processes_cleaned_total}, trimmed={entry.processes_trimmed}, "
                    f"killed={entry.processes_killed}, throttled={entry.processes_throttled}"
                ),
                (
                    f"Snapshot extras: found={entry.snapshot_extras_found}, "
                    f"selected={entry.snapshot_extras_selected}, kill candidates={entry.kill_candidates_found}"
                ),
                f"Top block reasons: {blocked}",
                f"Issues: {issues}",
            ]
        )
        self._details.setPlainText(text)


def open_cleanup_history_dialog(parent: QWidget | None = None) -> None:
    """Open the cleanup history dialog modally."""

    dialog = CleanupHistoryDialog(parent=parent)
    dialog.exec()
