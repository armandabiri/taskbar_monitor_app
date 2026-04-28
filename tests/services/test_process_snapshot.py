from __future__ import annotations

from services.process_snapshot import (
    ProcessSnapshot,
    ProcessSnapshotEntry,
    build_process_identity,
    diff_against_live,
)


class FakeProc:
    def __init__(self, pid: int, name: str, exe: str, username: str, cmdline: str) -> None:
        self.pid = pid
        self.info = {
            "name": name,
            "exe": exe,
            "username": username,
            "cmdline": cmdline.split(),
        }


def test_build_process_identity_normalizes_case_and_whitespace() -> None:
    identity = build_process_identity("  PYTHON.EXE ", " C:\\Apps\\Python.exe ", " ME ", " python   script.py ")

    assert identity == ("python.exe", "c:\\apps\\python.exe", "me", "python script.py")


def test_diff_against_live_respects_duplicate_snapshot_identities() -> None:
    snapshot = ProcessSnapshot(
        name="baseline",
        taken_at=1.0,
        path="baseline.csv",
        entries=[
            ProcessSnapshotEntry(1, "python.exe", r"C:\Apps\python.exe", "me", 0.0, 100.0, None, 1.0, "sleeping", 1, "python a.py"),
        ],
    )
    live = [
        FakeProc(10, "python.exe", r"C:\Apps\python.exe", "me", "python a.py"),
        FakeProc(11, "python.exe", r"C:\Apps\python.exe", "me", "python b.py"),
    ]

    extras = diff_against_live(snapshot, live)

    assert [proc.pid for proc in extras] == [11]
