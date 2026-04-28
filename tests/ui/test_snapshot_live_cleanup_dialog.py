from __future__ import annotations

from PyQt6.QtCore import QSettings

from services.resource_control.models import SkipReason
from services.resource_control.snapshot_scope import LiveSnapshotExtra, SnapshotLiveDiff
from ui.snapshot_live_cleanup_dialog import SnapshotLiveCleanupDialog


def test_snapshot_live_cleanup_dialog_uses_background_only_defaults(qtbot, tmp_path) -> None:
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    diff = SnapshotLiveDiff(
        snapshot_name="baseline",
        extra_processes=[
            LiveSnapshotExtra(
                pid=1,
                name="worker.exe",
                exe=r"C:\Apps\worker.exe",
                username="me",
                cmdline="worker",
                rss_gb=0.25,
                create_time=1.0,
                has_visible_window=False,
                has_tray_icon=False,
                default_selected=True,
            ),
            LiveSnapshotExtra(
                pid=2,
                name="discord.exe",
                exe=r"C:\Apps\discord.exe",
                username="me",
                cmdline="discord",
                rss_gb=0.50,
                create_time=2.0,
                has_visible_window=True,
                has_tray_icon=False,
                default_selected=False,
                default_block_reason=SkipReason.VISIBLE_WINDOW,
            ),
        ],
    )

    dialog = SnapshotLiveCleanupDialog(settings, diff)
    qtbot.addWidget(dialog)

    assert dialog.selected_pids == frozenset({1})
