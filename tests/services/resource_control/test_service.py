from __future__ import annotations

from types import SimpleNamespace

from services.resource_control import service
from services.resource_control.models import (
    CandidateDecision,
    CleanupMode,
    CleanupScope,
    ProcessCandidate,
    ResourcePlan,
    SkipReason,
    SystemSnapshot,
)
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


def test_release_resources_runs_low_pressure_pass_below_threshold(monkeypatch) -> None:
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
    monkeypatch.setattr(service, "_collect_ui_guard_pids", lambda profile: (frozenset(), frozenset()))
    monkeypatch.setattr(service, "_safe_own_username", lambda: "current-user")
    monkeypatch.setattr(service._OPERATOR, "get_foreground_pid", lambda: None)
    monkeypatch.setattr(
        service,
        "_scan_system_reclaim",
        lambda **kwargs: [
            CandidateDecision(
                pid=22,
                name="userapp.exe",
                candidate=ProcessCandidate(
                    22,
                    "userapp.exe",
                    1.0,
                    0.8,
                    0.0,
                    0.0,
                    0.0,
                    None,
                    0.5,
                    1.2,
                    0.0,
                ),
                eligible_for_trim=True,
                eligible_for_throttle=False,
                eligible_for_kill=False,
            )
        ],
    )
    monkeypatch.setattr(
        service._OPERATOR,
        "trim_workingset",
        lambda pid: 512.0 if pid == 22 else 0.0,
    )

    result = service.release_resources(profile=BALANCED)

    assert result.processes_cleaned_total == 1
    assert result.blocked_reason_counts[SkipReason.BELOW_PRESSURE_THRESHOLD.value] == 1
    assert "low-pressure cleanup pass will run" in result.details


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


def test_trim_phase_continues_after_first_candidate_fails(monkeypatch) -> None:
    attempts: list[int] = []

    def fake_trim(candidate: ProcessCandidate, now_mono: float, result) -> bool:
        del now_mono
        attempts.append(candidate.pid)
        if candidate.pid == 1:
            result.record_skip(SkipReason.EXECUTION_FAILED)
            result.processes_skipped += 1
            return False
        result.record_cleaned(candidate.pid, "trimmed", candidate.name)
        return True

    monkeypatch.setattr(service, "_do_trim", fake_trim)
    plan = ResourcePlan(
        aggressive=True,
        level="low",
        trim_threshold_gb=0.25,
        reclaim_target_gb=0.38,
        desired_available_gb=8.0,
        should_flush_standby=False,
        allow_foreground_trim=True,
        allow_recently_trimmed=True,
        allow_recently_throttled=True,
        max_trimmed_processes=2,
        max_throttled_processes=0,
        cpu_pressure=True,
        disk_pressure=True,
        network_pressure=True,
    )
    candidates = [
        ProcessCandidate(1, "memcompression", 12.0, None, 0.0, 0.0, 0.0, None, 5.4, 7.7, 0.0),
        ProcessCandidate(2, "userapp.exe", 2.9, 2.8, 0.0, 0.0, 0.0, None, 1.6, 2.2, 0.0),
    ]
    result = service.ReleaseResult(profile_name="Aggressive")

    service._execute_trim_phase(candidates, plan, 0.0, result)

    assert attempts == [1, 2]
    assert result.processes_trimmed == 1
    assert result.processes_cleaned_total == 1


def test_trim_phase_uses_full_budget_for_aggressive_profiles(monkeypatch) -> None:
    attempts: list[int] = []

    def fake_trim(candidate: ProcessCandidate, now_mono: float, result) -> bool:
        del now_mono
        attempts.append(candidate.pid)
        result.record_cleaned(candidate.pid, "trimmed", candidate.name)
        return True

    monkeypatch.setattr(service, "_do_trim", fake_trim)
    plan = ResourcePlan(
        aggressive=True,
        level="low",
        trim_threshold_gb=0.25,
        reclaim_target_gb=0.38,
        desired_available_gb=8.0,
        should_flush_standby=False,
        allow_foreground_trim=True,
        allow_recently_trimmed=True,
        allow_recently_throttled=True,
        max_trimmed_processes=2,
        max_throttled_processes=0,
        cpu_pressure=True,
        disk_pressure=True,
        network_pressure=True,
    )
    candidates = [
        ProcessCandidate(1, "memcompression", 12.0, None, 0.0, 0.0, 0.0, None, 5.4, 7.7, 0.0),
        ProcessCandidate(2, "userapp.exe", 2.9, 2.8, 0.0, 0.0, 0.0, None, 1.6, 2.2, 0.0),
    ]
    result = service.ReleaseResult(profile_name="Aggressive")

    service._execute_trim_phase(candidates, plan, 0.0, result)

    assert attempts == [1, 2]
    assert result.processes_trimmed == 2
    assert result.processes_cleaned_total == 2
