from __future__ import annotations

from types import SimpleNamespace

from services.resource_control import runner_common, service, snapshot_reclaim, system_reclaim
from services.resource_control.models import (
    CandidateDecision,
    CleanupMode,
    CleanupScope,
    ProcessCandidate,
    ReleaseResult,
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
    monkeypatch.setattr(service, "_SETTLE_SECONDS", 0.0)
    monkeypatch.setattr(
        runner_common.TRACKER,
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
    monkeypatch.setattr(
        runner_common, "collect_ui_guard_pids", lambda profile: (frozenset(), frozenset()),
    )
    monkeypatch.setattr(runner_common, "safe_own_username", lambda: "current-user")
    monkeypatch.setattr(runner_common.OPERATOR, "get_foreground_pid", lambda: None)
    monkeypatch.setattr(
        system_reclaim,
        "scan_system_reclaim",
        lambda **kwargs: [
            CandidateDecision(
                pid=22,
                name="userapp.exe",
                candidate=ProcessCandidate(
                    22, "userapp.exe", 1.0, 0.8, 0.0, 0.0, 0.0, None, 0.5, 1.2, 0.0,
                ),
                eligible_for_trim=True,
                eligible_for_throttle=False,
                eligible_for_kill=False,
            )
        ],
    )
    monkeypatch.setattr(
        runner_common.OPERATOR,
        "trim_workingset",
        lambda pid: 512.0 if pid == 22 else 0.0,
    )

    result = service.release_resources(profile=BALANCED)

    assert result.processes_cleaned_total == 1
    assert result.blocked_reason_counts[SkipReason.BELOW_PRESSURE_THRESHOLD.value] == 1
    assert "low-pressure cleanup pass" in result.details


def test_force_run_bypasses_pressure_threshold(monkeypatch) -> None:
    monkeypatch.setattr(service, "append_history", lambda result: None)
    monkeypatch.setattr(service, "_sample_available_gb", lambda: 8.0)
    monkeypatch.setattr(service, "_SETTLE_SECONDS", 0.0)
    monkeypatch.setattr(
        runner_common.TRACKER,
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
    monkeypatch.setattr(
        runner_common, "collect_ui_guard_pids", lambda profile: (frozenset(), frozenset()),
    )
    monkeypatch.setattr(runner_common, "safe_own_username", lambda: "current-user")
    monkeypatch.setattr(runner_common.OPERATOR, "get_foreground_pid", lambda: None)
    monkeypatch.setattr(
        system_reclaim,
        "scan_system_reclaim",
        lambda **kwargs: [
            CandidateDecision(
                pid=33,
                name="userapp.exe",
                candidate=ProcessCandidate(
                    33, "userapp.exe", 1.0, 0.8, 0.0, 0.0, 0.0, None, 0.5, 1.2, 0.0,
                ),
                eligible_for_trim=True,
                eligible_for_throttle=False,
                eligible_for_kill=False,
            )
        ],
    )
    monkeypatch.setattr(runner_common.OPERATOR, "trim_workingset", lambda pid: 256.0)

    result = service.release_resources(profile=BALANCED, force=True)

    assert result.was_forced is True
    # Force floors the plan tier to "elevated" even on a healthy system.
    assert result.pressure_level == "elevated"
    # No BELOW_PRESSURE_THRESHOLD skip is recorded when forced.
    assert SkipReason.BELOW_PRESSURE_THRESHOLD.value not in result.blocked_reason_counts
    assert result.processes_cleaned_total == 1


def test_plan_only_executes_nothing(monkeypatch) -> None:
    trims: list[int] = []
    monkeypatch.setattr(service, "append_history", lambda result: None)
    monkeypatch.setattr(service, "_sample_available_gb", lambda: 8.0)
    monkeypatch.setattr(
        runner_common.TRACKER,
        "sample_system",
        lambda now=None: SystemSnapshot(0.0, 85.0, 2.0, 16.0, 5.0, 0.0, 0.0),
    )
    monkeypatch.setattr(
        runner_common, "collect_ui_guard_pids", lambda profile: (frozenset(), frozenset()),
    )
    monkeypatch.setattr(runner_common, "safe_own_username", lambda: "current-user")
    monkeypatch.setattr(runner_common.OPERATOR, "get_foreground_pid", lambda: None)
    monkeypatch.setattr(
        runner_common.OPERATOR, "trim_workingset", lambda pid: trims.append(pid) or 0.0,
    )
    candidate = ProcessCandidate(44, "userapp.exe", 1.0, 0.8, 0.0, 0.0, 0.0, None, 0.5, 1.2, 0.0)
    monkeypatch.setattr(
        system_reclaim,
        "scan_system_reclaim",
        lambda **kwargs: [
            CandidateDecision(44, "userapp.exe", candidate, True, False, False),
        ],
    )

    result = service.release_resources(profile=BALANCED, plan_only=True)

    assert result.plan_only is True
    assert trims == []  # nothing executed
    assert [c.pid for c in result.preview_candidates] == [44]
    assert result.system_freed_gb is None  # no measurement during preview


def test_snapshot_cleanup_kills_only_selected_extra_pids(monkeypatch) -> None:
    killed: list[int] = []
    monkeypatch.setattr(service, "append_history", lambda result: None)
    monkeypatch.setattr(service, "_sample_available_gb", lambda: 8.0)
    monkeypatch.setattr(runner_common, "safe_own_username", lambda: "current-user")
    monkeypatch.setattr(runner_common.OPERATOR, "get_foreground_pid", lambda: None)
    monkeypatch.setattr(
        runner_common.OPERATOR,
        "terminate_process",
        lambda pid, graceful_timeout=1.5, force_timeout=1.0: killed.append(pid) or True,
    )
    monkeypatch.setattr(
        snapshot_reclaim.psutil,
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


def _trim_plan() -> ResourcePlan:
    return ResourcePlan(
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

    monkeypatch.setattr(runner_common, "do_trim", fake_trim)
    candidates = [
        ProcessCandidate(1, "memcompression", 12.0, None, 0.0, 0.0, 0.0, None, 5.4, 7.7, 0.0),
        ProcessCandidate(2, "userapp.exe", 2.9, 2.8, 0.0, 0.0, 0.0, None, 1.6, 2.2, 0.0),
    ]
    result = ReleaseResult(profile_name="Aggressive")

    system_reclaim.execute_trim_phase(candidates, _trim_plan(), 0.0, result)

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

    monkeypatch.setattr(runner_common, "do_trim", fake_trim)
    candidates = [
        ProcessCandidate(1, "memcompression", 12.0, None, 0.0, 0.0, 0.0, None, 5.4, 7.7, 0.0),
        ProcessCandidate(2, "userapp.exe", 2.9, 2.8, 0.0, 0.0, 0.0, None, 1.6, 2.2, 0.0),
    ]
    result = ReleaseResult(profile_name="Aggressive")

    system_reclaim.execute_trim_phase(candidates, _trim_plan(), 0.0, result)

    assert attempts == [1, 2]
    assert result.processes_trimmed == 2
    assert result.processes_cleaned_total == 2
