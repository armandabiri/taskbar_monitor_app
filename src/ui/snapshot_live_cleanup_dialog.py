"""Preview and approve extra live processes relative to a snapshot."""

from __future__ import annotations

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.config import (
    DEFAULT_SNAPSHOT_PRESELECT_BACKGROUND_ONLY,
    DEFAULT_SNAPSHOT_SHOW_TRAY_EXTRAS,
    DEFAULT_SNAPSHOT_SHOW_VISIBLE_EXTRAS,
)
from services.resource_control.models import format_skip_reason
from services.resource_control.snapshot_scope import LiveSnapshotExtra, SnapshotLiveDiff

SETTINGS_GROUP = "snapshot_cleanup"
KEY_PRESELECT_BACKGROUND_ONLY = "preselect_background_only"
KEY_SHOW_VISIBLE_EXTRAS = "show_visible_extras"
KEY_SHOW_TRAY_EXTRAS = "show_tray_extras"

_DIALOG_STYLE = """
QDialog { background-color: #1a1a1a; color: #ddd; }
QLabel { color: #ddd; }
QLabel#hint { color: #888; font-size: 11px; }
QLabel#summary { color: #55efc4; font-weight: bold; }
QTableWidget {
    background-color: #1f1f1f; color: #eee; gridline-color: #2a2a2a;
    border: 1px solid #333; selection-background-color: #2c2c2c;
}
QHeaderView::section { background-color: #2a2a2a; color: #aaa; padding: 4px; border: 0; }
QCheckBox { color: #ddd; }
QPushButton {
    background-color: #2a2a2a; color: #eee; border: 1px solid #444;
    border-radius: 3px; padding: 5px 12px;
}
QPushButton:hover { background-color: #3a3a3a; border-color: #55efc4; }
QPushButton#kill { background-color: #5a1f1f; border-color: #ff7675; color: #ffaaaa; }
QPushButton#kill:hover { background-color: #7a2a2a; color: white; }
"""


class SnapshotLiveCleanupDialog(QDialog):
    """Preview live processes that were added after a snapshot."""

    def __init__(
        self,
        settings: QSettings,
        diff: SnapshotLiveDiff,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._diff = diff
        self._selected_pids: frozenset[int] = frozenset()
        self._rows: list[tuple[LiveSnapshotExtra, QCheckBox]] = []

        self.setWindowTitle(f"Kill Extra Processes vs '{diff.snapshot_name}'")
        self.setStyleSheet(_DIALOG_STYLE)
        self.setMinimumSize(980, 620)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        summary = QLabel(
            f"Snapshot '{diff.snapshot_name}' -> {len(diff.extra_processes)} extra live process(es), "
            f"{diff.matched_count} matched baseline, {diff.missing_snapshot_count} missing baseline entries."
        )
        summary.setObjectName("summary")
        summary.setWordWrap(True)
        layout.addWidget(summary)

        hint = QLabel(
            "Background extras are selected by default. Extras with a visible window or tray icon "
            "are shown but unchecked by default so the user must opt in explicitly."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        options_row = QHBoxLayout()
        self._preselect_background = QCheckBox("Preselect background-only extras")
        self._preselect_background.setChecked(
            bool(int(settings.value(
                f"{SETTINGS_GROUP}/{KEY_PRESELECT_BACKGROUND_ONLY}",
                DEFAULT_SNAPSHOT_PRESELECT_BACKGROUND_ONLY,
            )))
        )
        self._show_visible = QCheckBox("Show visible-window extras")
        self._show_visible.setChecked(
            bool(int(settings.value(
                f"{SETTINGS_GROUP}/{KEY_SHOW_VISIBLE_EXTRAS}",
                DEFAULT_SNAPSHOT_SHOW_VISIBLE_EXTRAS,
            )))
        )
        self._show_tray = QCheckBox("Show tray-icon extras")
        self._show_tray.setChecked(
            bool(int(settings.value(
                f"{SETTINGS_GROUP}/{KEY_SHOW_TRAY_EXTRAS}",
                DEFAULT_SNAPSHOT_SHOW_TRAY_EXTRAS,
            )))
        )
        options_row.addWidget(self._preselect_background)
        options_row.addWidget(self._show_visible)
        options_row.addWidget(self._show_tray)
        options_row.addStretch(1)
        layout.addLayout(options_row)

        self._table = QTableWidget(0, 8, self)
        self._table.setHorizontalHeaderLabels(
            ["Kill?", "Process", "PID", "RSS", "Window", "Tray", "User", "Executable"]
        )
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        vertical_header = self._table.verticalHeader()
        if vertical_header is not None:
            vertical_header.setVisible(False)
        header = self._table.horizontalHeader()
        if header is not None:
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._table, 1)

        self._selection_summary = QLabel("")
        self._selection_summary.setObjectName("hint")
        layout.addWidget(self._selection_summary)

        btn_row = QHBoxLayout()
        select_all_btn = QPushButton("Select all shown")
        select_all_btn.clicked.connect(lambda: self._set_visible_rows(True))
        btn_row.addWidget(select_all_btn)

        background_btn = QPushButton("Background only")
        background_btn.clicked.connect(self._apply_background_defaults)
        btn_row.addWidget(background_btn)

        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(lambda: self._set_all(False))
        btn_row.addWidget(clear_btn)

        btn_row.addStretch(1)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        self._kill_btn = QPushButton("Kill 0 Process(es)")
        self._kill_btn.setObjectName("kill")
        self._kill_btn.clicked.connect(self._accept_selection)
        btn_row.addWidget(self._kill_btn)
        layout.addLayout(btn_row)

        self._preselect_background.toggled.connect(self._on_preselect_toggled)
        self._show_visible.toggled.connect(lambda _checked: self._refresh_visibility())
        self._show_tray.toggled.connect(lambda _checked: self._refresh_visibility())

        self._populate_rows()
        self._refresh_visibility()
        self._refresh_selection_summary()

    @property
    def selected_pids(self) -> frozenset[int]:
        return self._selected_pids

    def _populate_rows(self) -> None:
        extras = self._diff.extra_processes
        self._table.setRowCount(len(extras))
        self._rows.clear()
        for row, extra in enumerate(extras):
            check = QCheckBox()
            check.setChecked(self._default_selected(extra))
            check.toggled.connect(lambda _checked: self._refresh_selection_summary())
            self._table.setCellWidget(row, 0, check)

            name_item = QTableWidgetItem(extra.name or "(unknown)")
            if extra.default_block_reason is not None:
                name_item.setToolTip(f"Default unchecked: {format_skip_reason(extra.default_block_reason)}")
            self._table.setItem(row, 1, name_item)

            pid_item = QTableWidgetItem(str(extra.pid))
            pid_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(row, 2, pid_item)

            rss_mb = extra.rss_gb * 1024.0
            rss_text = f"{rss_mb:,.0f} MB" if rss_mb < 1024 else f"{extra.rss_gb:.2f} GB"
            rss_item = QTableWidgetItem(rss_text)
            rss_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(row, 3, rss_item)

            visible_item = QTableWidgetItem("yes" if extra.has_visible_window else "")
            self._table.setItem(row, 4, visible_item)

            tray_item = QTableWidgetItem("yes" if extra.has_tray_icon else "")
            self._table.setItem(row, 5, tray_item)

            user_item = QTableWidgetItem(extra.username or "")
            self._table.setItem(row, 6, user_item)

            exe_item = QTableWidgetItem(extra.exe or extra.cmdline or "")
            exe_item.setToolTip(extra.cmdline or extra.exe or "")
            self._table.setItem(row, 7, exe_item)

            self._rows.append((extra, check))

    def _default_selected(self, extra: LiveSnapshotExtra) -> bool:
        if not self._preselect_background.isChecked():
            return False
        return extra.default_selected

    def _apply_background_defaults(self) -> None:
        for extra, check in self._rows:
            check.blockSignals(True)
            check.setChecked(self._default_selected(extra))
            check.blockSignals(False)
        self._refresh_selection_summary()

    def _on_preselect_toggled(self, checked: bool) -> None:
        self._settings.setValue(
            f"{SETTINGS_GROUP}/{KEY_PRESELECT_BACKGROUND_ONLY}",
            1 if checked else 0,
        )
        self._settings.sync()
        self._apply_background_defaults()

    def _refresh_visibility(self) -> None:
        self._settings.setValue(
            f"{SETTINGS_GROUP}/{KEY_SHOW_VISIBLE_EXTRAS}",
            1 if self._show_visible.isChecked() else 0,
        )
        self._settings.setValue(
            f"{SETTINGS_GROUP}/{KEY_SHOW_TRAY_EXTRAS}",
            1 if self._show_tray.isChecked() else 0,
        )
        self._settings.sync()
        for row, (extra, _check) in enumerate(self._rows):
            hidden = (
                (extra.has_visible_window and not self._show_visible.isChecked())
                or (extra.has_tray_icon and not self._show_tray.isChecked())
            )
            self._table.setRowHidden(row, hidden)
        self._refresh_selection_summary()

    def _set_all(self, state: bool) -> None:
        for _extra, check in self._rows:
            check.blockSignals(True)
            check.setChecked(state)
            check.blockSignals(False)
        self._refresh_selection_summary()

    def _set_visible_rows(self, state: bool) -> None:
        for row, (_extra, check) in enumerate(self._rows):
            if self._table.isRowHidden(row):
                continue
            check.blockSignals(True)
            check.setChecked(state)
            check.blockSignals(False)
        self._refresh_selection_summary()

    def _refresh_selection_summary(self) -> None:
        selected = []
        total_gb = 0.0
        shown = 0
        for row, (extra, check) in enumerate(self._rows):
            if not self._table.isRowHidden(row):
                shown += 1
            if check.isChecked():
                selected.append(extra.pid)
                total_gb += extra.rss_gb
        self._selected_pids = frozenset(selected)
        self._selection_summary.setText(
            f"Showing {shown} of {len(self._rows)} extra process(es). "
            f"Selected: {len(selected)} -> ~{total_gb:.2f} GB RSS."
        )
        self._kill_btn.setEnabled(bool(selected))
        self._kill_btn.setText(f"Kill {len(selected)} Process(es)" if selected else "Kill")

    def _accept_selection(self) -> None:
        self._refresh_selection_summary()
        if not self._selected_pids:
            return
        self.accept()


def select_snapshot_extra_processes(
    settings: QSettings,
    diff: SnapshotLiveDiff,
    *,
    parent: QWidget | None = None,
) -> frozenset[int] | None:
    """Open the snapshot cleanup preview and return selected PIDs."""

    dialog = SnapshotLiveCleanupDialog(settings, diff, parent=parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.selected_pids
