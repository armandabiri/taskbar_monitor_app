"""Tests for the concurrent USS prefetch + cache used by the scan."""

from __future__ import annotations

from types import SimpleNamespace

from services.resource_control import uss_prefetch
from services.resource_control.candidate_scorer import CandidateScorer
from services.resource_control.models import ProcessTelemetry, ResourcePlan
from services.resource_control.profiles import BALANCED


class FakeProc:
    def __init__(self, pid: int, uss_bytes: int) -> None:
        self.pid = pid
        self._uss = uss_bytes
        self.calls = 0

    def memory_full_info(self):
        self.calls += 1
        return SimpleNamespace(uss=self._uss)

    def net_connections(self, kind: str = "inet"):  # noqa: ARG002
        return []


def test_prefetch_resolves_all_pids() -> None:
    procs = [FakeProc(1, 100), FakeProc(2, 200), FakeProc(3, 300)]
    cache = uss_prefetch.prefetch_uss(procs)
    assert set(cache) == {1, 2, 3}
    assert cache[2] == 200 / (1024 ** 3)


def _plan() -> ResourcePlan:
    return ResourcePlan(
        aggressive=False, level="high", trim_threshold_gb=0.1, reclaim_target_gb=1.0,
        desired_available_gb=4.0, should_flush_standby=False, allow_foreground_trim=False,
        allow_recently_trimmed=False, allow_recently_throttled=False, max_trimmed_processes=4,
        max_throttled_processes=2, cpu_pressure=False, disk_pressure=False, network_pressure=False,
    )


def _telemetry() -> ProcessTelemetry:
    return ProcessTelemetry(0.0, 0.0, 0.0, 0.0, 0, 0, 0)


def _info(pid: int):
    return {
        "pid": pid, "name": "userapp.exe", "exe": r"C:\Users\me\userapp.exe",
        "username": "me", "memory_info": SimpleNamespace(rss=512 * 1024 * 1024),
        "create_time": 1.0, "status": "sleeping",
    }


def test_cached_uss_matches_serial_and_avoids_extra_syscall() -> None:
    scorer = CandidateScorer()
    serial_proc = FakeProc(7, 256 * 1024 * 1024)
    serial = scorer.evaluate_candidate(
        serial_proc, _info(7), _telemetry(), _plan(), now_wall=500.0,
        foreground_pid=None, profile=BALANCED, own_username="me",
    )

    cached_proc = FakeProc(7, 256 * 1024 * 1024)
    cache = uss_prefetch.prefetch_uss([cached_proc])
    syscalls_after_prefetch = cached_proc.calls
    cached = scorer.evaluate_candidate(
        cached_proc, _info(7), _telemetry(), _plan(), now_wall=500.0,
        foreground_pid=None, profile=BALANCED, own_username="me", uss_cache=cache,
    )

    # Same USS feeds the same reclaim score (ranking is unchanged).
    assert serial.candidate is not None and cached.candidate is not None
    assert cached.candidate.uss_gb == serial.candidate.uss_gb
    assert cached.candidate.reclaim_score == serial.candidate.reclaim_score
    # Scoring with a warm cache did not issue another memory_full_info call.
    assert cached_proc.calls == syscalls_after_prefetch


def test_cached_none_is_respected() -> None:
    scorer = CandidateScorer()
    proc = FakeProc(9, 0)
    # Pretend USS could not be read; cache the None and confirm no recompute.
    cache = {9: None}
    proc.calls = 0
    scorer.evaluate_candidate(
        proc, _info(9), _telemetry(), _plan(), now_wall=500.0,
        foreground_pid=None, profile=BALANCED, own_username="me", uss_cache=cache,
    )
    assert proc.calls == 0
