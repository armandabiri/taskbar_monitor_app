"""Dialog: kill processes by matching Win32_Process CommandLine (regex), with saved pattern."""

from __future__ import annotations

import logging
import sys

import psutil
from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from core.config import APP_NAME
from services.cmdline_kill_wmi import (
    load_remember_pattern,
    load_saved_pattern,
    query_processes_by_commandline_regex,
    save_pattern_preferences,
    terminate_pids,
)
from services.notification_service import NotificationService
from services.resource_control.models import ProcessCandidate
from ui.kill_confirm_dialog import confirm_kill

LOGGER = logging.getLogger(__name__)

_STYLE = """
QDialog { background-color: #1a1a1a; color: #ddd; }
QLabel { color: #ddd; }
QLabel#hint { color: #aaa; font-size: 11px; }
QLineEdit {
    background-color: #1f1f1f; color: #eee; border: 1px solid #444;
    border-radius: 3px; padding: 6px;
}
QCheckBox { color: #ddd; }
QDialogButtonBox QPushButton {
    background-color: #2a2a2a; color: #eee; border: 1px solid #444;
    border-radius: 3px; padding: 6px 14px; min-width: 88px;
}
QDialogButtonBox QPushButton:hover { background-color: #3a3a3a; }
"""


def open_cmdline_kill_dialog(settings: QSettings, parent: QWidget | None) -> None:
    """Modal flow: edit regex, query CIM, confirm checkboxes, kill selected PIDs."""
    if not _is_windows():
        QMessageBox.information(
            parent,
            APP_NAME,
            "Killing by WMI command line is only supported on Windows.",
        )
        return

    dialog = QDialog(parent)
    dialog.setWindowTitle("Kill by command line (WMI)")
    dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    dialog.setStyleSheet(_STYLE)
    dialog.setMinimumWidth(520)

    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(10)

    hint = QLabel(
        "PowerShell regex is matched against each process's full CommandLine "
        "(same as Get-CimInstance Win32_Process). "
        "Example: isqlv|app_isqlv|intelag_sql_studio"
    )
    hint.setObjectName("hint")
    hint.setWordWrap(True)
    layout.addWidget(hint)

    form = QFormLayout()
    pattern_edit = QLineEdit(load_saved_pattern(settings))
    pattern_edit.setPlaceholderText("Regex for CommandLine…")
    form.addRow("Match pattern:", pattern_edit)
    layout.addLayout(form)

    remember_cb = QCheckBox("Remember pattern for next time")
    remember_cb.setChecked(load_remember_pattern(settings))
    layout.addWidget(remember_cb)

    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok,
    )
    buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Find matching…")
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)

    _position_above_parent(dialog, parent)

    def _on_ok() -> None:
        pattern = pattern_edit.text().strip()
        if not pattern:
            QMessageBox.warning(dialog, APP_NAME, "Enter a non-empty regex pattern.")
            return

        save_pattern_preferences(
            settings,
            pattern,
            remember=remember_cb.isChecked(),
        )

        try:
            rows = query_processes_by_commandline_regex(pattern)
        except RuntimeError as exc:
            LOGGER.warning("Command-line process query failed: %s", exc)
            QMessageBox.critical(
                dialog,
                APP_NAME,
                f"Could not query processes:\n{exc}",
            )
            return

        if not rows:
            QMessageBox.information(
                dialog,
                APP_NAME,
                "No processes matched that pattern on the command line.",
            )
            return

        candidates = [_row_to_candidate(pid, name, cmd) for pid, name, cmd in rows]
        approved = confirm_kill(
            parent=dialog,
            candidates=candidates,
            title="Confirm kill by command line",
            warning_prefix="matching",
            info_text=(
                "These processes were selected because their WMI CommandLine matches "
                "your pattern. Uncheck any row to spare it. Unsaved data will be lost."
            ),
        )
        if not approved:
            return

        pids = [c.pid for c in approved]
        ok, errors = terminate_pids(pids)
        msg = f"Terminated {ok} process(es)."
        if errors:
            msg += "\n\nErrors:\n" + "\n".join(errors[:8])
            if len(errors) > 8:
                msg += f"\n… and {len(errors) - 8} more."
        NotificationService.notify(APP_NAME, msg)
        dialog.accept()

    buttons.accepted.connect(_on_ok)

    dialog.exec()


def _is_windows() -> bool:
    return sys.platform == "win32"


def _row_to_candidate(pid: int, name: str, command_line: str) -> ProcessCandidate:
    rss_gb = 0.0
    try:
        rss_gb = psutil.Process(pid).memory_info().rss / (1024.0 ** 3)
    except psutil.Error:
        pass

    display_name = name
    short_cmd = command_line.strip()
    if len(short_cmd) > 120:
        short_cmd = short_cmd[:117] + "…"
    if short_cmd:
        display_name = f"{name} — {short_cmd}"

    return ProcessCandidate(
        pid=pid,
        name=display_name,
        rss_gb=rss_gb,
        uss_gb=None,
        cpu_percent=0.0,
        disk_gb_s=0.0,
        other_gb_s=0.0,
        age_seconds=None,
        estimated_reclaim_gb=rss_gb,
        reclaim_score=0.0,
        throttle_score=0.0,
    )


def _position_above_parent(dialog: QDialog, parent: QWidget | None, *, gap: int = 20) -> None:
    size = dialog.sizeHint().expandedTo(dialog.minimumSize())
    dialog.resize(size)
    if parent is None:
        return
    parent_rect = parent.frameGeometry()
    screen = parent.screen() or QApplication.primaryScreen()
    available = screen.availableGeometry() if screen is not None else parent_rect
    frame = dialog.frameGeometry()
    max_x = max(available.left(), available.right() - frame.width() + 1)
    x_pos = max(
        available.left(),
        min(parent_rect.center().x() - (frame.width() // 2), max_x),
    )
    above_y = parent_rect.top() - frame.height() - gap
    if above_y >= available.top():
        y_pos = above_y
    else:
        max_y = max(available.top(), available.bottom() - frame.height() + 1)
        y_pos = max(available.top(), min(parent_rect.bottom() + gap, max_y))
    dialog.move(x_pos, y_pos)
