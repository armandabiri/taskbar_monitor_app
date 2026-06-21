"""Bounded cleanup: deadline + kill budget + per-process terminate caps."""

from __future__ import annotations

import time

from services.resource_control import runner_common, service, system_reclaim
from services.resource_control.bounds import CleanupBounds
from services.resource_control.models import (
    CandidateDecision,
    ProcessCandidate,
    SystemSnapshot,
)
from services.resource_control.profiles import NUCLEAR


def _high_pressure_snapshot(now=None):
    return SystemSnapshot(now or 0.0, 92.0, 1.0, 16.0, 5.0, 0.0, 0.0)


def _stub_env(monkeypatch) -> None:
    monkeypatch.setattr(service, "append_history", lambda result: None)
    monkeypatch.setattr(service, "_sample_available_gb", lambda: 8.0)
    monkeypatch.setattr(service, "_SETTLE_SECONDS", 0.0)
    monkeypatch.setattr(runner_common.TRACKER, "sample_system", _high_pressure_snapshot)
    monkeypatch.setattr(
        runner_common, "collect_ui_guard_pids", lambda profile: (frozenset(), frozenset()),
    )
    monkeypatch.setattr(runner_common, "safe_own_username", lambda: "current-user")
    monkeypatch.setattr(runner_common.OPERATOR, "get_foreground_pid", lambda: None)


def _kill_decision(pid: int) -> CandidateDecision:
    return CandidateDecision(
        pid=pid,
        name=f"hog{pid}.exe",
        candidate=ProcessCandidate(
            pid, f"hog{pid}.exe", 2.0, 1.5, 0.0, 0.0, 0.0, None, 1.0, 5.0, 5.0,
            (), False, True,
        ),
        eligible_for_trim=False,
        eligible_for_throttle=False,
        eligible_for_kill=True,
    )


def test_kill_phase_stops_at_kill_budget(monkeypatch) -> None:
    _stub_env(monkeypatch)
    kills: list[int] = []

    def _slow_terminate(pid, **_kw):
        kills.append(pid)
        time.sleep(0.15)
        return True

    monkeypatch.setattr(runner_common.OPERATOR, "terminate_process", _slow_terminate)
    monkeypatch.setattr(
        system_reclaim, "scan_system_reclaim",
        lambda **kw: [_kill_decision(p) for p in range(10)],
    )

    bounds = CleanupBounds(
        deadline_s=10.0, kill_budget_s=0.3,
        per_kill_graceful_s=0.05, per_kill_force_s=0.05,
    )
    nuclear_no_confirm = NUCLEAR.with_overrides(confirm_before_kill=False)
    start = time.monotonic()
    result = service.release_resources(profile=nuclear_no_confirm, bounds=bounds)
    elapsed = time.monotonic() - start

    assert len(kills) < 10, "kill phase did not stop at budget"
    assert elapsed < 2.0, f"kill phase ignored budget: elapsed={elapsed:.2f}s"
    assert any("budget" in n or "deadline" in n for n in result.notes)


def test_max_candidates_caps_scan_results(monkeypatch) -> None:
    _stub_env(monkeypatch)
    monkeypatch.setattr(runner_common.OPERATOR, "trim_workingset", lambda pid: 0.0)
    monkeypatch.setattr(
        system_reclaim, "scan_system_reclaim",
        lambda **kw: [
            CandidateDecision(
                pid=i, name=f"p{i}.exe",
                candidate=ProcessCandidate(
                    i, f"p{i}.exe", 0.5, 0.4, 0.0, 0.0, 0.0, None, 0.3, 1.0, 0.0,
                ),
                eligible_for_trim=True,
                eligible_for_throttle=False,
                eligible_for_kill=False,
            )
            for i in range(50)
        ],
    )

    bounds = CleanupBounds(max_candidates=5)
    result = service.release_resources(bounds=bounds)

    assert any("Capped candidates at 5" in note for note in result.notes)
