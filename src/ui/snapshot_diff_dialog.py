"""Snapshot diff viewer.

Compare two snapshots side by side. Each row is a (name, exe) group
aggregated across all instances.

Color coding (in the right-hand "Δ" column):
  • Status=added            → solid red.
  • Status=changed, severity → blue (no change) → red (≥1000% relative
                               increase in CPU or RAM, whichever is higher).
The blue→red gradient encodes how much *more* a process is consuming
than it did in the older snapshot.
"""

from __future__ import annotations

import logging
import time

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from services.process_snapshot import (
    DiffEntry,
    diff_snapshots,
    list_snapshots,
    load_snapshot,
)

LOGGER = logging.getLogger(__name__)

_DIALOG_STYLE = """
QDialog { background-color: #1a1a1a; color: #ddd; }
QLabel { color: #ddd; }
QLabel#hint { color: #888; font-size: 11px; }
QLabel#legend { color: #aaa; font-size: 11px; padding-top: 2px; }
QComboBox, QDoubleSpinBox {
    background-color: #2a2a2a; color: #eee; border: 1px solid #444;
    border-radius: 3px; padding: 3px 6px; min-width: 220px;
}
QComboBox QAbstractItemView { background-color: #2a2a2a; color: #eee; selection-background-color: #444; }
QTableWidget {
    background-color: #1f1f1f; color: #eee; gridline-color: #2a2a2a;
    border: 1px solid #333;
}
QHeaderView::section { background-color: #2a2a2a; color: #aaa; padding: 4px; border: 0; }
QPushButton {
    background-color: #2a2a2a; color: #eee; border: 1px solid #444;
    border-radius: 3px; padding: 5px 12px;
}
QPushButton:hover { background-color: #3a3a3a; }
QCheckBox { color: #ddd; }
"""

_BLUE = (0x4D, 0x9F, 0xFF)
_RED = (0xFF, 0x4D, 0x4D)
_ADDED_COLOR = (0xFF, 0x44, 0x44)


def _severity_to_qcolor(entry: DiffEntry, min_sev: float, max_sev: float) -> QColor:
    """Map ``entry.severity`` onto the blue→red gradient.

    The gradient is relative to the visible data: ``min_sev`` is solid blue,
    ``max_sev`` is solid red, with linear interpolation between. ``added``
    entries always render as solid red (they have severity=∞ and represent
    the strongest possible change).
    """
    if entry.status == "added":
        r, g, b = _ADDED_COLOR
        return QColor(r, g, b)
    if entry.status == "removed":
        return QColor(0x55, 0x55, 0x55)
    span = max_sev - min_sev
    if span <= 1e-6:
        # Every visible row has the same severity — pick the midpoint colour.
        t = 0.5
    else:
        t = (max(entry.severity, min_sev) - min_sev) / span
        t = max(0.0, min(t, 1.0))
    r = int(_BLUE[0] + t * (_RED[0] - _BLUE[0]))
    g = int(_BLUE[1] + t * (_RED[1] - _BLUE[1]))
    b = int(_BLUE[2] + t * (_RED[2] - _BLUE[2]))
    return QColor(r, g, b)


def _fmt_delta_pct(pct: float) -> str:
    if pct == float("inf"):
        return "NEW activity"
    if pct == float("-inf"):
        return "stopped"
    sign = "+" if pct >= 0 else ""
    # Show one decimal under 10%, integer for the rest — keeps small jitter
    # readable without flooding the column with noise digits.
    if abs(pct) < 10:
        return f"{sign}{pct:,.1f}%"
    return f"{sign}{pct:,.0f}%"


def _fmt_mb(mb: float) -> str:
    if mb < 1024:
        return f"{mb:,.0f} MB"
    return f"{mb / 1024:.2f} GB"


class SnapshotDiffDialog(QDialog):
    """Side-by-side snapshot comparison."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        initial_old: str | None = None,
        initial_new: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Compare snapshots")
        self.setStyleSheet(_DIALOG_STYLE)
        self.setMinimumSize(880, 560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        # ---- Picker row ---------------------------------------------------
        picker_row = QHBoxLayout()
        picker_row.addWidget(QLabel("From:"))
        self._old_combo = QComboBox()
        picker_row.addWidget(self._old_combo, 1)
        picker_row.addWidget(QLabel("To:"))
        self._new_combo = QComboBox()
        picker_row.addWidget(self._new_combo, 1)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._compute)
        picker_row.addWidget(refresh_btn)
        layout.addLayout(picker_row)

        # ---- Filters row --------------------------------------------------
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Min Δ:"))
        self._min_severity = QDoubleSpinBox()
        self._min_severity.setRange(0.0, 1000.0)
        self._min_severity.setSuffix(" %")
        self._min_severity.setDecimals(0)
        self._min_severity.setSingleStep(10)
        self._min_severity.setValue(10.0)
        self._min_severity.setToolTip(
            "Hide 'changed' rows whose max(CPU Δ, RAM Δ) is below this threshold."
        )
        self._min_severity.valueChanged.connect(lambda _v: self._compute())
        filter_row.addWidget(self._min_severity)

        self._show_removed = QCheckBox("Show removed")
        self._show_removed.toggled.connect(lambda _c: self._compute())
        filter_row.addWidget(self._show_removed)

        filter_row.addStretch(1)
        legend = QLabel(
            "● added  •  blue → red = smallest → largest visible change"
        )
        legend.setObjectName("legend")
        filter_row.addWidget(legend)
        layout.addLayout(filter_row)

        # ---- Table --------------------------------------------------------
        self._table = QTableWidget(0, 7, self)
        self._table.setHorizontalHeaderLabels([
            "Process", "Status", "Instances", "CPU (old → new)",
            "RAM (old → new)", "CPU Δ%", "RAM Δ%",
        ])
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, 7):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self._table, 1)

        # ---- Summary row --------------------------------------------------
        self._summary = QLabel("")
        self._summary.setObjectName("hint")
        layout.addWidget(self._summary)

        # ---- Close button -------------------------------------------------
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        self._populate_pickers(initial_old, initial_new)
        self._old_combo.currentIndexChanged.connect(lambda _i: self._compute())
        self._new_combo.currentIndexChanged.connect(lambda _i: self._compute())
        self._compute()

    # ------------------------------------------------------------------
    def _populate_pickers(self, initial_old: str | None, initial_new: str | None) -> None:
        snapshots = list_snapshots()
        if not snapshots:
            return
        for combo in (self._old_combo, self._new_combo):
            combo.blockSignals(True)
            combo.clear()
            for snap in snapshots:
                label = f"{snap.name}    ({time.strftime('%Y-%m-%d %H:%M', time.localtime(snap.taken_at))})"
                combo.addItem(label, snap.path)
            combo.blockSignals(False)
        # Default: oldest as 'from', newest as 'to'.
        if len(snapshots) >= 2:
            self._old_combo.setCurrentIndex(len(snapshots) - 1)
            self._new_combo.setCurrentIndex(0)
        if initial_old is not None:
            self._select_path(self._old_combo, initial_old)
        if initial_new is not None:
            self._select_path(self._new_combo, initial_new)

    def _select_path(self, combo: QComboBox, path: str) -> None:
        for i in range(combo.count()):
            if combo.itemData(i) == path:
                combo.setCurrentIndex(i)
                return

    def _selected_path(self, combo: QComboBox) -> str | None:
        idx = combo.currentIndex()
        if idx < 0:
            return None
        return combo.itemData(idx)

    # ------------------------------------------------------------------
    def _compute(self) -> None:
        old_path = self._selected_path(self._old_combo)
        new_path = self._selected_path(self._new_combo)
        if not old_path or not new_path:
            self._table.setRowCount(0)
            self._summary.setText("Pick two snapshots above.")
            return
        if old_path == new_path:
            self._table.setRowCount(0)
            self._summary.setText("Pick two *different* snapshots to compare.")
            return

        try:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            old = load_snapshot(old_path)
            new = load_snapshot(new_path)
        except OSError as exc:
            LOGGER.warning("Failed to load snapshots: %s", exc)
            QApplication.restoreOverrideCursor()
            return
        finally:
            QApplication.restoreOverrideCursor()

        entries = diff_snapshots(
            old, new,
            include_removed=self._show_removed.isChecked(),
            min_severity=float(self._min_severity.value()),
        )
        self._render_rows(entries)

        added = sum(1 for e in entries if e.status == "added")
        changed = sum(1 for e in entries if e.status == "changed")
        removed = sum(1 for e in entries if e.status == "removed")
        delta_seconds = max(new.taken_at - old.taken_at, 0.0)
        self._summary.setText(
            f"From '{old.name}' → '{new.name}'   ({_fmt_duration(delta_seconds)})   "
            f"•  {added} added, {changed} changed, {removed} removed"
        )

    def _render_rows(self, entries: list[DiffEntry]) -> None:
        # Bounds for the gradient are taken from finite severities only —
        # 'added' rows have severity=∞ and are always solid red.
        finite_severities = [
            max(e.severity, 0.0) for e in entries
            if e.status == "changed" and e.severity != float("inf")
        ]
        min_sev = min(finite_severities) if finite_severities else 0.0
        max_sev = max(finite_severities) if finite_severities else 0.0

        self._table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            color = _severity_to_qcolor(entry, min_sev, max_sev)
            brush_for_delta = QBrush(color)

            name_item = QTableWidgetItem(entry.name)
            name_item.setToolTip(entry.exe or "(no exe path)")
            self._table.setItem(row, 0, name_item)

            status_text = {"added": "ADDED", "removed": "removed", "changed": "changed"}[entry.status]
            status_item = QTableWidgetItem(status_text)
            if entry.status in ("added",):
                status_item.setForeground(brush_for_delta)
            self._table.setItem(row, 1, status_item)

            inst_text = f"{entry.old_instances} → {entry.new_instances}"
            inst_item = QTableWidgetItem(inst_text)
            inst_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 2, inst_item)

            cpu_item = QTableWidgetItem(
                f"{entry.old_cpu:6.1f}% → {entry.new_cpu:6.1f}%"
            )
            cpu_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(row, 3, cpu_item)

            ram_item = QTableWidgetItem(
                f"{_fmt_mb(entry.old_rss_mb)} → {_fmt_mb(entry.new_rss_mb)}"
            )
            ram_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(row, 4, ram_item)

            cpu_delta = QTableWidgetItem(
                "NEW" if entry.status == "added" else _fmt_delta_pct(entry.cpu_delta_pct)
            )
            cpu_delta.setForeground(brush_for_delta)
            cpu_delta.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(row, 5, cpu_delta)

            ram_delta = QTableWidgetItem(
                "NEW" if entry.status == "added" else _fmt_delta_pct(entry.mem_delta_pct)
            )
            ram_delta.setForeground(brush_for_delta)
            ram_delta.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(row, 6, ram_delta)


def _fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f} min"
    if seconds < 86400:
        return f"{seconds / 3600:.1f} h"
    return f"{seconds / 86400:.1f} days"


def open_snapshot_diff_dialog(
    parent: QWidget | None = None,
    *,
    initial_old: str | None = None,
    initial_new: str | None = None,
) -> None:
    dialog = SnapshotDiffDialog(parent=parent, initial_old=initial_old, initial_new=initial_new)
    dialog.exec()
