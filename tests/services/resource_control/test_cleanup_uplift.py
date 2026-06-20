"""Service-level tests for the cleanup uplift: cancel, progress, throttle restore,
measured reclaim, and dry-run preview contracts."""

from __future__ import annotations

from types import SimpleNamespace

from services.resource_control import runner_common, service, system_reclaim
from services.resource_control.cancel import CancelToken
from services.resource_control.models import (
    CandidateDecision,
    ProcessCandidate,
    ReleaseResult,
    SystemSnapshot,
)
from services.resource_control.profiles import BALANCED
from services.resource_control.progress import CleanupPhase
from services.resource_control.windows_ops import ThrottleState


def _high_pressure_snapshot(now=None):
    return SystemSnapshot(now or 0.0, 88.0, 1.5, 16.0, 5.0, 0.0, 0.0)


def _stub_environment(monkeypatch):
    monkeypatch.setattr(service, "append_history", lambda result: None)
    monkeypatch.setattr(service, "_sample_available_gb", lambda: 8.0)
    monkeypatch.setattr(service, "_SETTLE_SECONDS", 0.0)
    monkeypatch.setattr(runner_common.TRACKER, "sample_system", _high_pressure_snapshot)
    monkeypatch.setattr(
        runner_common, "collect_ui_guard_pids", lambda profile: (frozenset(), frozenset()),
    )
    monkeypatch.setattr(runner_common, "safe_own_username", lambda: "current-user")
    monkeypatch.setattr(runner_common.OPERATOR, "get_foreground_pid", lambda: None)


def _candidate(pid=50):
    return CandidateDecision(
        pid=pid,
        name="userapp.exe",
        candidate=ProcessCandidate(
            pid, "userapp.exe", 1.0, 0.8, 0.0, 0.0, 0.0, None, 0.5, 1.2, 0.0,
        ),
        eligible_for_trim=True,
        eligible_for_throttle=False,
        eligible_for_kill=False,
    )


def test_cancelled_run_executes_nothing(monkeypatch) -> None:
    _stub_environment(monkeypatch)
    trims: list[int] = []
    monkeypatch.setattr(
        runner_common.OPERATOR, "trim_workingset", lambda pid: trims.append(pid) or 0.0,
    )
    monkeypatch.setattr(system_reclaim, "scan_system_reclaim", lambda **kw: [_candidate()])

    token = CancelToken()
    token.cancel()  # cancelled before the run starts
    result = service.release_resources(profile=BALANCED, cancel=token)

    assert trims == []
    assert result.system_freed_gb is None  # verification skipped on cancel
    assert any("cancelled" in note.lower() for note in result.notes)


def test_progress_callback_fires_per_phase(monkeypatch) -> None:
    _stub_environment(monkeypatch)
    monkeypatch.setattr(runner_common.OPERATOR, "trim_workingset", lambda pid: 0.0)
    monkeypatch.setattr(system_reclaim, "scan_system_reclaim", lambda **kw: [])

    phases: list[CleanupPhase] = []
    service.release_resources(profile=BALANCED, progress=lambda p: phases.append(p.phase))

    # Balanced trims + throttles + flushes; the service then verifies.
    assert CleanupPhase.TRIMMING in phases
    assert CleanupPhase.FLUSHING in phases
    assert CleanupPhase.VERIFYING in phases


def test_plan_only_preview_lists_candidates_without_executing(monkeypatch) -> None:
    _stub_environment(monkeypatch)
    trims: list[int] = []
    monkeypatch.setattr(
        runner_common.OPERATOR, "trim_workingset", lambda pid: trims.append(pid) or 0.0,
    )
    monkeypatch.setattr(
        system_reclaim, "scan_system_reclaim", lambda **kw: [_candidate(60), _candidate(61)],
    )

    result = service.release_resources(profile=BALANCED, plan_only=True)

    assert result.plan_only is True
    assert trims == []
    assert {c.pid for c in result.preview_candidates} == {60, 61}


def test_measure_system_freed_records_positive_delta(monkeypatch) -> None:
    monkeypatch.setattr(service, "_SETTLE_SECONDS", 0.0)
    monkeypatch.setattr(service, "_sample_available_gb", lambda: 9.5)
    result = ReleaseResult()
    result.memory_before_gb = 8.0

    service._measure_system_freed(result, None)

    assert result.system_freed_gb == 1.5


def test_reset_throttled_processes_restores_and_clears(monkeypatch) -> None:
    runner_common.TRACKER.clear_throttle_journal()
    state = ThrottleState(priority=32, io_priority=None, affinity=(0, 1, 2, 3))
    runner_common.TRACKER.note_throttle_journal(123, "hog.exe", state)
    restored: list[int] = []
    monkeypatch.setattr(
        runner_common.OPERATOR, "restore_throttle",
        lambda pid, prior: restored.append(pid) or True,
    )

    count, attempted = service.reset_throttled_processes()

    assert (count, attempted) == (1, 1)
    assert restored == [123]
    assert service.throttled_process_count() == 0


def test_do_throttle_journals_prior_state(monkeypatch) -> None:
    runner_common.TRACKER.clear_throttle_journal()
    prior = ThrottleState(priority=32, io_priority=None, affinity=(0, 1))
    monkeypatch.setattr(runner_common.OPERATOR, "snapshot_throttle_state", lambda proc: prior)
    monkeypatch.setattr(runner_common.OPERATOR, "apply_throttle", lambda proc, action: ("cpu",))
    monkeypatch.setattr(runner_common.psutil, "Process", lambda pid: SimpleNamespace(pid=pid))

    candidate = ProcessCandidate(
        99, "hog.exe", 1.0, 0.5, 0.0, 0.0, 0.0, None, 0.5, 1.0, 5.0, ("cpu",),
    )
    result = ReleaseResult()
    runner_common.do_throttle(candidate, action=SimpleNamespace(), now_mono=0.0, result=result)

    journal = {pid: name for pid, name, _state in runner_common.TRACKER.throttle_journal()}
    assert journal == {99: "hog.exe"}
    assert result.processes_throttled == 1
    runner_common.TRACKER.clear_throttle_journal()
