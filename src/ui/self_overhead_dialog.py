"""Read-only dialog showing this app's own CPU/RAM and last cleanup summary."""

from __future__ import annotations

import time

from PyQt6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from services.app_metrics_probe import AppMetrics, AppMetricsProbe

_STYLE = """
QDialog { background-color: #1a1a1a; color: #ddd; }
QLabel { color: #ddd; }
QLabel#header { color: #55efc4; font-weight: bold; font-size: 11pt; }
QPushButton {
    background-color: #2a2a2a; color: #eee; border: 1px solid #444;
    border-radius: 3px; padding: 5px 12px;
}
QPushButton:hover { background-color: #3a3a3a; border-color: #55efc4; }
"""


def _fmt_cleanup(metrics: AppMetrics) -> str:
    entry = metrics.last_cleanup
    if entry is None:
        return "No cleanup runs recorded"
    age_s = time.time() - entry.timestamp
    age_str = (
        f"{int(age_s // 60)}m ago"
        if age_s >= 60
        else f"{int(age_s)}s ago"
    )
    return (
        f"{age_str}  |  mode={entry.mode}  |  cleaned={entry.processes_cleaned_total}  "
        f"|  {entry.summary}"
    )


class SelfOverheadDialog(QDialog):
    """Display this app's own CPU/RAM and last cleanup run info."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("App Footprint")
        self.setStyleSheet(_STYLE)
        self.setMinimumWidth(480)
        self._probe = AppMetricsProbe()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)

        header = QLabel("App Self-Overhead")
        header.setObjectName("header")
        outer.addWidget(header)

        form = QFormLayout()
        form.setHorizontalSpacing(16)

        self._cpu_lbl = QLabel()
        self._ram_lbl = QLabel()
        self._cleanup_lbl = QLabel()
        self._cleanup_lbl.setWordWrap(True)

        form.addRow("CPU:", self._cpu_lbl)
        form.addRow("RAM:", self._ram_lbl)
        form.addRow("Last cleanup:", self._cleanup_lbl)
        outer.addLayout(form)

        btn_row = QHBoxLayout()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(refresh_btn)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        outer.addLayout(btn_row)

        self._refresh()

    def _refresh(self) -> None:
        metrics = self._probe.sample()
        self._cpu_lbl.setText(f"{metrics.cpu_percent:.1f}%")
        self._ram_lbl.setText(f"{metrics.rss_mb:.1f} MB")
        self._cleanup_lbl.setText(_fmt_cleanup(metrics))


def open_self_overhead_dialog(parent: QWidget | None = None) -> None:
    dlg = SelfOverheadDialog(parent=parent)
    dlg.exec()
