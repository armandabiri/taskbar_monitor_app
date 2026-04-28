"""Confirmation dialog shown before Nuclear cleanup terminates processes.

The dialog lists every kill candidate with a per-row checkbox — by default all
are selected, but the user can uncheck any process they want to spare. This
matters on Windows 11 where the tray uses XAML islands and there's no reliable
Win32 enumeration of tray icons, so visible-window detection misses some
genuinely-running tray apps.
"""

from __future__ import annotations

from typing import Iterable

from PyQt6.QtCore import Qt
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
)  # type: ignore[attr-defined]

from services.resource_control.models import ProcessCandidate

_DIALOG_STYLE = """
QDialog { background-color: #1a1a1a; color: #ddd; }
QLabel { color: #ddd; }
QLabel#warning { color: #ff7675; font-weight: bold; }
QLabel#summary { color: #aaa; }
QTableWidget {
    background-color: #1f1f1f; color: #eee; gridline-color: #2a2a2a;
    border: 1px solid #333; selection-background-color: #2c2c2c;
}
QHeaderView::section { background-color: #2a2a2a; color: #aaa; padding: 4px; border: 0; }
QCheckBox { color: #ddd; padding-left: 8px; }
QPushButton {
    background-color: #2a2a2a; color: #eee; border: 1px solid #444;
    border-radius: 3px; padding: 6px 16px; min-width: 80px;
}
QPushButton:hover { background-color: #3a3a3a; }
QPushButton#kill { background-color: #5a1f1f; border-color: #ff7675; color: #ffaaaa; }
QPushButton#kill:hover { background-color: #7a2a2a; color: white; }
"""

# Common tray-resident apps the user almost certainly wants to keep. We use
# this only to default-uncheck rows in the confirm dialog; the user can still
# override either way.
_LIKELY_TRAY_APPS = frozenset(
    name.lower() for name in (
        "discord.exe", "slack.exe", "teams.exe", "zoom.exe", "skype.exe",
        "spotify.exe", "steam.exe", "epicgameslauncher.exe", "battle.net.exe",
        "onedrive.exe", "googledrivefs.exe", "dropbox.exe",
        "1password.exe", "keepass.exe", "bitwarden.exe",
        "nordvpn.exe", "expressvpn.exe", "protonvpn.exe", "tailscale-ipn.exe",
        "rainmeter.exe", "f.lux.exe", "powertoys.exe",
        "razerappengine.exe", "nahimicservice.exe", "logioptionsplus.exe",
        "everything.exe", "quicklook.exe", "ditto.exe", "snipaste.exe",
        "msteams.exe", "outlook.exe", "obs64.exe", "obs32.exe",
    )
)


def confirm_kill(
    parent: QWidget | None,
    candidates: Iterable[ProcessCandidate],
    *,
    title: str = "Confirm Cleanup",
    warning_prefix: str = "background",
    info_text: str | None = None,
) -> list[ProcessCandidate] | None:
    """Modal dialog with per-row checkboxes.

    Returns the filtered list of approved candidates, or None if the user
    cancelled. An empty list means 'user kept the dialog open but unchecked
    everything' — treat that the same as cancel from the caller's perspective.
    """
    targets = list(candidates)
    if not targets:
        return None

    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setStyleSheet(_DIALOG_STYLE)
    dialog.setMinimumSize(620, 540)

    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(10)

    warning = QLabel(
        f"⚠ {len(targets)} {warning_prefix} process(es) will be terminated."
    )
    warning.setObjectName("warning")
    layout.addWidget(warning)

    info = QLabel(
        info_text
        or (
            "Visible windows are already spared. Uncheck any row to spare it now. "
            "Likely tray apps are unchecked by default — check them only if you "
            "really want to kill them. Unsaved data in killed apps will be lost."
        )
    )
    info.setWordWrap(True)
    layout.addWidget(info)

    sorted_targets = sorted(targets, key=lambda c: c.rss_gb, reverse=True)

    table = QTableWidget(len(sorted_targets), 4, dialog)
    table.setHorizontalHeaderLabels(["Kill?", "Process", "PID", "RSS"])
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
    table.verticalHeader().setVisible(False)
    header = table.horizontalHeader()
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)

    checkboxes: list[QCheckBox] = []
    for row, candidate in enumerate(sorted_targets):
        check = QCheckBox()
        likely_tray = candidate.name.lower() in _LIKELY_TRAY_APPS
        check.setChecked(not likely_tray)
        table.setCellWidget(row, 0, check)
        checkboxes.append(check)

        name_item = QTableWidgetItem(candidate.name)
        if likely_tray:
            name_item.setToolTip("Likely a tray-resident app — unchecked by default.")
        table.setItem(row, 1, name_item)

        pid_item = QTableWidgetItem(str(candidate.pid))
        pid_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        table.setItem(row, 2, pid_item)

        rss_mb = candidate.rss_gb * 1024.0
        rss_item = QTableWidgetItem(
            f"{rss_mb:,.0f} MB" if rss_mb < 1024 else f"{candidate.rss_gb:.2f} GB"
        )
        rss_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        table.setItem(row, 3, rss_item)

    layout.addWidget(table, 1)

    summary = QLabel("")
    summary.setObjectName("summary")
    layout.addWidget(summary)

    def _refresh_summary() -> None:
        selected = [
            sorted_targets[i] for i, cb in enumerate(checkboxes) if cb.isChecked()
        ]
        total_gb = sum(c.rss_gb for c in selected)
        summary.setText(f"Selected: {len(selected)} process(es) — ~{total_gb:.2f} GB")
        kill_btn.setEnabled(bool(selected))
        kill_btn.setText(f"Kill {len(selected)} Process(es)" if selected else "Kill")

    btn_row = QHBoxLayout()

    select_all_btn = QPushButton("Select all")
    select_all_btn.clicked.connect(lambda: _set_all(checkboxes, True))
    btn_row.addWidget(select_all_btn)

    clear_btn = QPushButton("Clear")
    clear_btn.clicked.connect(lambda: _set_all(checkboxes, False))
    btn_row.addWidget(clear_btn)

    btn_row.addStretch(1)

    cancel_btn = QPushButton("Cancel")
    cancel_btn.setDefault(True)
    cancel_btn.setAutoDefault(True)
    btn_row.addWidget(cancel_btn)

    kill_btn = QPushButton(f"Kill {len(sorted_targets)} Process(es)")
    kill_btn.setObjectName("kill")
    btn_row.addWidget(kill_btn)

    layout.addLayout(btn_row)

    cancel_btn.clicked.connect(dialog.reject)
    kill_btn.clicked.connect(dialog.accept)
    for cb in checkboxes:
        cb.toggled.connect(lambda _checked: _refresh_summary())
    _refresh_summary()

    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    keep = [sorted_targets[i] for i, cb in enumerate(checkboxes) if cb.isChecked()]
    return keep or None


def _set_all(checkboxes: list[QCheckBox], state: bool) -> None:
    for cb in checkboxes:
        cb.blockSignals(True)
        cb.setChecked(state)
        cb.blockSignals(False)
    if checkboxes:
        # Trigger one signal so the summary updates.
        checkboxes[0].toggled.emit(checkboxes[0].isChecked())
