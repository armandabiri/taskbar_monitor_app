from __future__ import annotations

from types import SimpleNamespace

from services.resource_control import service
from services.resource_control.models import CleanupMode, CleanupScope, SkipReason, SystemSnapshot
from services.resource_control.profiles import BALANCED


class FakeLiveProc:
    def __init__(self, pid: int, name: str, username: str = "current-user") -> None:
        self.pid = pid
        self.info = {
            "pid": pid,
            "name": name,
            "username": username,
            "exe": rf"C:\Users\me\{name}",
            "memory_info": SimpleNamespace(rss=256 * 1024 * 1024),
            "create_time": 100.0,
        }


def test_release_resources_reports_below_threshold_zero_action(monkeypatch) -> None:
    monkeypatch.setattr(service, "append_history", lambda result: None)
    monkeypatch.setattr(service, "_sample_available_gb", lambda: 8.0)
    monkeypatch.setattr(
        service._TRACKER,
        "sample_system",
        lambda now=None: SystemSnapshot(
            sampled_at=now or 0.0,
            memory_percent=35.0,
            available_gb=8.0,
            total_gb=16.0,
            cpu_percent=5.0,
            disk_gb_s=0.0,
            net_gb_s=0.0,
        ),
    )

    result = service.release_resources(profile=BALANCED)

    assert result.processes_cleaned_total == 0
    assert result.blocked_reason_counts[SkipReason.BELOW_PRESSURE_THRESHOLD.value] == 1
    assert "below the configured threshold" in result.details


def test_snapshot_cleanup_kills_only_selected_extra_pids(monkeypatch) -> None:
    killed: list[int] = []
    monkeypatch.setattr(service, "append_history", lambda result: None)
    monkeypatch.setattr(service, "_sample_available_gb", lambda: 8.0)
    monkeypatch.setattr(service, "_safe_own_username", lambda: "current-user")
    monkeypatch.setattr(service._OPERATOR, "get_foreground_pid", lambda: None)
    monkeypatch.setattr(
        service._OPERATOR,
        "terminate_process",
        lambda pid, graceful_timeout=1.5, force_timeout=1.0: killed.append(pid) or True,
    )
    monkeypatch.setattr(
        service.psutil,
        "process_iter",
        lambda attrs=None, ad_value=None: [
            FakeLiveProc(11, "python.exe"),
            FakeLiveProc(12, "notepad.exe"),
        ],
    )

    scope = CleanupScope(
        mode=CleanupMode.SNAPSHOT_EXTRAS.value,
        snapshot_name="Baseline",
        candidate_pids=frozenset({11, 12}),
        target_pids=frozenset({11}),
        snapshot_matched_count=4,
    )
    result = service.release_resources(profile=BALANCED, scope=scope)

    assert killed == [11]
    assert result.processes_killed == 1
    assert result.snapshot_extras_found == 2
    assert result.snapshot_extras_selected == 1
    assert result.blocked_reason_counts[SkipReason.SNAPSHOT_NOT_SELECTED.value] == 1
