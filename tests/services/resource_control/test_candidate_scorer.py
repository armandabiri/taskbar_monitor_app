from __future__ import annotations

from types import SimpleNamespace

from services.resource_control.candidate_scorer import CandidateScorer
from services.resource_control.models import ProcessTelemetry, ResourcePlan, SkipReason
from services.resource_control.profiles import BALANCED, NUCLEAR


class FakeProc:
    def __init__(self, pid: int, *, uss_bytes: int = 0) -> None:
        self.pid = pid
        self._uss_bytes = uss_bytes

    def memory_full_info(self):
        return SimpleNamespace(uss=self._uss_bytes)

    def net_connections(self, kind: str = "inet"):  # noqa: ARG002
        return []


def _plan() -> ResourcePlan:
    return ResourcePlan(
        aggressive=False,
        level="high",
        trim_threshold_gb=0.1,
        reclaim_target_gb=1.0,
        desired_available_gb=4.0,
        should_flush_standby=False,
        allow_foreground_trim=False,
        allow_recently_trimmed=False,
        allow_recently_throttled=False,
        max_trimmed_processes=4,
        max_throttled_processes=2,
        cpu_pressure=False,
        disk_pressure=False,
        network_pressure=False,
    )


def _telemetry() -> ProcessTelemetry:
    return ProcessTelemetry(
        cpu_percent=0.0,
        disk_gb_s=0.0,
        other_gb_s=0.0,
        total_cpu_time=0.0,
        read_bytes=0,
        write_bytes=0,
        other_bytes=0,
    )


def test_candidate_scorer_marks_visible_window_process_as_skipped() -> None:
    scorer = CandidateScorer()
    decision = scorer.evaluate_candidate(
        FakeProc(42),
        {
            "pid": 42,
            "name": "python.exe",
            "exe": r"C:\Users\me\python.exe",
            "username": "me",
            "memory_info": SimpleNamespace(rss=512 * 1024 * 1024),
            "create_time": 1.0,
            "status": "sleeping",
        },
        _telemetry(),
        _plan(),
        now_wall=500.0,
        foreground_pid=None,
        profile=BALANCED,
        visible_window_pids=frozenset({42}),
        own_username="me",
    )

    assert decision.skip_reason == SkipReason.VISIBLE_WINDOW
    assert decision.candidate is None


def test_candidate_scorer_disables_kill_for_different_user() -> None:
    scorer = CandidateScorer()
    decision = scorer.evaluate_candidate(
        FakeProc(77, uss_bytes=256 * 1024 * 1024),
        {
            "pid": 77,
            "name": "python.exe",
            "exe": r"C:\Users\other\python.exe",
            "username": "other-user",
            "memory_info": SimpleNamespace(rss=512 * 1024 * 1024),
            "create_time": 1.0,
            "status": "sleeping",
        },
        _telemetry(),
        _plan(),
        now_wall=500.0,
        foreground_pid=None,
        profile=NUCLEAR,
        own_username="current-user",
    )

    assert decision.skip_reason is None
    assert decision.candidate is not None
    assert decision.eligible_for_kill is False


def test_nuclear_preset_disables_visible_and_tray_sparing() -> None:
    assert NUCLEAR.protect_foreground is False
    assert NUCLEAR.spare_visible_windows is False
    assert NUCLEAR.spare_tray_icons is False
