"""Per-phase cancel: trim/kill loops and the post-run settle honor the token."""

from __future__ import annotations

import time

from services.resource_control import runner_common, service, system_reclaim
from services.resource_control.bounds import CleanupBounds
from services.resource_control.cancel import CancelToken
from services.resource_control.models import (
    CandidateDecision,
    ProcessCandidate,
    ReleaseResult,
    SystemSnapshot,
)
from services.resource_control.profiles import NUCLEAR


def _high_pressure_snapshot(now=None):
    return SystemSnapshot(now or 0.0, 95.0, 0.5, 16.0, 5.0, 0.0, 0.0)


def _stub_env(monkeypatch) -> None:
    monkeypatch.setattr(service, "append_history", lambda result: None)
    monkeypatch.setattr(service, "_sample_available_gb", lambda: 8.0)
    monkeypatch.setattr(service, "_SETTLE_SECONDS", 5.0)
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


def test_cancel_during_kill_phase_stops_promptly(monkeypatch) -> None:
    _stub_env(monkeypatch)
    token = CancelToken()
    kills: list[int] = []

    def _terminate(pid, **_kw):
        kills.append(pid)
        # Cancel after the first kill — no further kills must occur.
        if len(kills) == 1:
            token.cancel()
        return True

    monkeypatch.setattr(runner_common.OPERATOR, "terminate_process", _terminate)
    monkeypatch.setattr(
        system_reclaim, "scan_system_reclaim",
        lambda **kw: [_kill_decision(p) for p in range(5)],
    )

    nuclear_no_confirm = NUCLEAR.with_overrides(confirm_before_kill=False)
    service.release_resources(
        profile=nuclear_no_confirm, cancel=token,
        bounds=CleanupBounds(deadline_s=5.0, kill_budget_s=5.0),
    )

    assert kills == [kills[0]], f"kill loop ignored cancel: {kills}"


def test_settle_aborts_immediately_on_cancel(monkeypatch) -> None:
    monkeypatch.setattr(service, "_sample_available_gb", lambda: 8.5)
    monkeypatch.setattr(service, "_SETTLE_SECONDS", 5.0)
    token = CancelToken()
    token.cancel()
    result = ReleaseResult()
    result.memory_before_gb = 8.0

    start = time.monotonic()
    service._measure_system_freed(result, None, cancel=token)
    elapsed = time.monotonic() - start

    # Cancelled settle must not pay the 5-second sleep.
    assert elapsed < 0.5, f"settle did not abort on cancel: {elapsed:.2f}s"
    assert result.system_freed_gb is None
