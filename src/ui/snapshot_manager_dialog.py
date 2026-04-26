"""Process snapshot manager — take, rename, delete, and clean-using-as-reference.

The dialog lists every saved snapshot. From here the user can:
  • Take a fresh snapshot (with an optional custom name).
  • Rename / delete an existing snapshot.
  • 'Clean now' using a snapshot as the reference — everything whose
    (name, exe) is in the snapshot is spared; everything else (subject to the
    usual safety filters) goes through the kill confirmation dialog.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from services.process_snapshot import (
    ProcessSnapshot,
    delete_snapshot,
    list_snapshots,
    load_snapshot,
    rename_snapshot,
    snapshots_dir,
    take_snapshot,
)
from ui.snapshot_diff_dialog import open_snapshot_diff_dialog

LOGGER = logging.getLogger(__name__)

# Callback signature: receives the loaded snapshot, runs the clean flow,
# and returns when done. Used to integrate with the main app's release_resources.
CleanWithSnapshotCallback = Callable[[ProcessSnapshot], None]

_DIALOG_STYLE = """
QDialog { background-color: #1a1a1a; color: #ddd; }
QLabel { color: #ddd; }
QLabel#hint { color: #888; font-size: 11px; }
QLineEdit {
    background-color: #2a2a2a; color: #eee; border: 1px solid #444;
    border-radius: 3px; padding: 4px 8px;
}
QTableWidget {
    background-color: #1f1f1f; color: #eee; gridline-color: #2a2a2a;
    border: 1px solid #333; selection-background-color: #2c2c2c;
}
QHeaderView::section { background-color: #2a2a2a; color: #aaa; padding: 4px; border: 0; }
QPushButton {
    background-color: #2a2a2a; color: #eee; border: 1px solid #444;
    border-radius: 3px; padding: 5px 12px; min-width: 70px;
}
QPushButton:hover { background-color: #3a3a3a; border-color: #55efc4; }
QPushButton#clean { background-color: #2c4a2c; border-color: #55efc4; color: #b9f5b9; }
QPushButton#clean:hover { background-color: #3c5a3c; }
QPushButton#delete { color: #ff8888; }
QPushButton#delete:hover { background-color: #5a2828; border-color: #ff7675; }
"""


class SnapshotManagerDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None = None,
        on_clean: CleanWithSnapshotCallback | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Process Snapshots")
        self.setStyleSheet(_DIALOG_STYLE)
        self.setMinimumSize(640, 440)

        self._on_clean = on_clean

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        # ---- Header / take new snapshot row -------------------------------
        take_row = QHBoxLayout()
        take_row.addWidget(QLabel("Name:"))
        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText("Leave blank to use timestamp")
        take_row.addWidget(self._name_input, 1)
        take_btn = QPushButton("📸  Take snapshot")
        take_btn.clicked.connect(self._on_take)
        take_row.addWidget(take_btn)
        compare_btn = QPushButton("⇄  Compare snapshots…")
        compare_btn.setToolTip(
            "Open the diff viewer: pick two snapshots and see which processes "
            "were added (red) or grew in CPU/RAM (blue → red gradient)."
        )
        compare_btn.clicked.connect(self._on_compare)
        take_row.addWidget(compare_btn)
        layout.addLayout(take_row)

        hint = QLabel(
            f"Stored in: {snapshots_dir()}    •    "
            "'Clean now' kills everything that isn't in the snapshot, "
            "with the usual visible-window / tray protection still applied."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # ---- Snapshot table ------------------------------------------------
        self._table = QTableWidget(0, 5, self)
        self._table.setHorizontalHeaderLabels(["Name", "Taken", "Processes", "Size", "Actions"])
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self._table, 1)

        # ---- Close button --------------------------------------------------
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        self._refresh()

    # ------------------------------------------------------------------
    def _refresh(self) -> None:
        snapshots = list_snapshots()
        self._table.setRowCount(len(snapshots))
        for row, snap in enumerate(snapshots):
            self._table.setItem(row, 0, QTableWidgetItem(snap.name))
            self._table.setItem(
                row, 1, QTableWidgetItem(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(snap.taken_at))),
            )
            try:
                size_kb = os.path.getsize(snap.path) / 1024.0
                size_text = f"{size_kb:,.0f} KB"
            except OSError:
                size_text = "?"
            # Process count requires loading the file; cheap for a CSV but defer to row click.
            count_item = QTableWidgetItem("…")
            count_item.setData(Qt.ItemDataRole.UserRole, snap.path)
            self._table.setItem(row, 2, count_item)
            self._table.setItem(row, 3, QTableWidgetItem(size_text))

            actions = QWidget()
            row_layout = QHBoxLayout(actions)
            row_layout.setContentsMargins(2, 2, 2, 2)
            row_layout.setSpacing(4)

            open_btn = QPushButton("Open CSV")
            open_btn.setToolTip("Open the snapshot CSV in your default associated app.")
            open_btn.clicked.connect(lambda _checked=False, p=snap.path: self._on_open_csv(p))
            row_layout.addWidget(open_btn)

            clean_btn = QPushButton("Clean now")
            clean_btn.setObjectName("clean")
            clean_btn.setToolTip(
                "Run the kill cleanup using this snapshot as the reference.\n"
                "Anything in the snapshot is spared; new processes go through "
                "the kill confirmation dialog."
            )
            clean_btn.clicked.connect(lambda _checked=False, p=snap.path: self._on_clean_clicked(p))
            row_layout.addWidget(clean_btn)

            rename_btn = QPushButton("Rename")
            rename_btn.clicked.connect(lambda _checked=False, p=snap.path: self._on_rename(p))
            row_layout.addWidget(rename_btn)

            delete_btn = QPushButton("Delete")
            delete_btn.setObjectName("delete")
            delete_btn.clicked.connect(lambda _checked=False, p=snap.path: self._on_delete(p))
            row_layout.addWidget(delete_btn)

            self._table.setCellWidget(row, 4, actions)

        # Lazy-load process counts (cheap but I/O-bound, do after table is shown)
        for row in range(self._table.rowCount()):
            count_item = self._table.item(row, 2)
            if count_item is None:
                continue
            path = count_item.data(Qt.ItemDataRole.UserRole)
            try:
                snap = load_snapshot(path)
                count_item.setText(f"{snap.process_count}")
            except OSError as exc:
                LOGGER.warning("Failed to load %s: %s", path, exc)
                count_item.setText("?")

    def _selected_path(self) -> str | None:
        row = self._table.currentRow()
        if row < 0:
            return None
        item = self._table.item(row, 2)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    # ------------------------------------------------------------------
    def _on_take(self) -> None:
        name = self._name_input.text().strip() or None
        try:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            snap = take_snapshot(name)
        finally:
            QApplication.restoreOverrideCursor()
        self._name_input.clear()
        self._refresh()
        QMessageBox.information(
            self, "Snapshot saved",
            f"Captured {snap.process_count} processes ({snap.total_rss_gb:.2f} GB total RSS) "
            f"as '{snap.name}'.",
        )

    def _on_rename(self, path: str) -> None:
        snap = ProcessSnapshot(
            name=os.path.splitext(os.path.basename(path))[0],
            taken_at=os.path.getmtime(path),
            path=path,
        )
        new_name, ok = QInputDialog.getText(
            self, "Rename snapshot", "New name:", QLineEdit.EchoMode.Normal, snap.name,
        )
        if not ok or not new_name.strip():
            return
        try:
            rename_snapshot(snap, new_name)
        except (FileExistsError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "Rename failed", str(exc))
            return
        self._refresh()

    def _on_delete(self, path: str) -> None:
        name = os.path.splitext(os.path.basename(path))[0]
        confirm = QMessageBox.question(
            self, "Delete snapshot",
            f"Delete snapshot '{name}'? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        snap = ProcessSnapshot(name=name, taken_at=os.path.getmtime(path), path=path)
        delete_snapshot(snap)
        self._refresh()

    def _on_open_csv(self, path: str) -> None:
        """Open the snapshot CSV in the user's default associated app."""
        if not os.path.exists(path):
            QMessageBox.warning(self, "Not found", f"File no longer exists:\n{path}")
            return
        try:
            os.startfile(path)  # Windows-only — other platforms wouldn't ship this app
        except OSError as exc:
            QMessageBox.warning(self, "Open failed", str(exc))

    def _on_compare(self) -> None:
        """Open the snapshot diff viewer."""
        snapshots = list_snapshots()
        if len(snapshots) < 2:
            QMessageBox.information(
                self, "Need two snapshots",
                "Take at least two snapshots before comparing — the diff "
                "viewer needs a 'from' and a 'to'.",
            )
            return
        # Default 'to' = newest selected row if any, else the newest snapshot.
        selected = self._selected_path()
        open_snapshot_diff_dialog(parent=self, initial_new=selected)

    def _on_clean_clicked(self, path: str) -> None:
        if self._on_clean is None:
            QMessageBox.warning(self, "Not wired", "No cleanup callback is wired into this dialog.")
            return
        try:
            snap = load_snapshot(path)
        except OSError as exc:
            QMessageBox.warning(self, "Load failed", str(exc))
            return
        self._on_clean(snap)


def open_snapshot_manager(
    parent: QWidget | None = None,
    on_clean: CleanWithSnapshotCallback | None = None,
) -> None:
    dialog = SnapshotManagerDialog(parent=parent, on_clean=on_clean)
    dialog.exec()
