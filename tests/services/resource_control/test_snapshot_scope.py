from __future__ import annotations

from types import SimpleNamespace

from services.process_snapshot import ProcessSnapshot, ProcessSnapshotEntry
from services.resource_control.models import SkipReason
from services.resource_control.snapshot_scope import diff_snapshot_to_live


class FakeProc:
    def __init__(
        self,
        pid: int,
        *,
        name: str,
        exe: str,
        username: str,
        cmdline: str,
        rss_mb: int = 128,
        create_time: float = 1.0,
    ) -> None:
        self.pid = pid
        self.info = {
            "pid": pid,
            "name": name,
            "exe": exe,
            "username": username,
            "cmdline": cmdline.split(),
            "memory_info": SimpleNamespace(rss=rss_mb * 1024 * 1024),
            "create_time": create_time,
        }


def test_diff_snapshot_to_live_uses_multiset_matching_for_duplicate_instances() -> None:
    snapshot = ProcessSnapshot(
        name="baseline",
        taken_at=1.0,
        path="baseline.csv",
        entries=[
            ProcessSnapshotEntry(1, "chrome.exe", r"C:\Chrome\chrome.exe", "me", 0.0, 100.0, None, 1.0, "sleeping", 1, "--profile 1"),
            ProcessSnapshotEntry(2, "chrome.exe", r"C:\Chrome\chrome.exe", "me", 0.0, 100.0, None, 1.0, "sleeping", 1, "--profile 2"),
        ],
    )
    live = [
        FakeProc(10, name="chrome.exe", exe=r"C:\Chrome\chrome.exe", username="me", cmdline="--profile 1"),
        FakeProc(11, name="chrome.exe", exe=r"C:\Chrome\chrome.exe", username="me", cmdline="--profile 2"),
        FakeProc(12, name="chrome.exe", exe=r"C:\Chrome\chrome.exe", username="me", cmdline="--profile 3"),
    ]

    diff = diff_snapshot_to_live(snapshot, live, visible_window_pids=set(), tray_icon_pids=set())

    assert diff.matched_count == 2
    assert [extra.pid for extra in diff.extra_processes] == [12]


def test_diff_snapshot_to_live_marks_visible_and_tray_extras_unchecked() -> None:
    snapshot = ProcessSnapshot(name="baseline", taken_at=1.0, path="baseline.csv", entries=[])
    live = [
        FakeProc(20, name="discord.exe", exe=r"C:\Apps\discord.exe", username="me", cmdline="discord"),
        FakeProc(21, name="worker.exe", exe=r"C:\Apps\worker.exe", username="me", cmdline="worker"),
    ]

    diff = diff_snapshot_to_live(
        snapshot,
        live,
        visible_window_pids={20},
        tray_icon_pids={21},
    )

    by_pid = {extra.pid: extra for extra in diff.extra_processes}
    assert by_pid[20].default_selected is False
    assert by_pid[20].default_block_reason == SkipReason.VISIBLE_WINDOW
    assert by_pid[21].default_selected is False
    assert by_pid[21].default_block_reason == SkipReason.TRAY_ICON
