"""High-level resource release orchestration."""

from __future__ import annotations

import gc
import logging
import os
import time

import psutil

from services.resource_control.constants import MAX_REPORTED_ERRORS
from services.resource_control.candidate_scorer import CandidateScorer
from services.resource_control.models import ReleaseResult
from services.resource_control.planner import ResourcePlanner
from services.resource_control.tracker import ActivityTracker
from services.resource_control.windows_ops import WindowsProcessOperator

LOGGER = logging.getLogger(__name__)

_TRACKER = ActivityTracker()
_PLANNER = ResourcePlanner()
_SCORER = CandidateScorer()
_OPERATOR = WindowsProcessOperator()


def release_resources(
    trim_threshold_mb: float = 200.0,
    flush_cache: bool = True,
    run_gc: bool = True,
    aggressive: bool = False,
) -> ReleaseResult:
    """Release RAM and safely throttle hot background processes."""
    result = ReleaseResult()
    if run_gc:
        result.gc_collected = gc.collect()
    now_mono = time.monotonic()
    now_wall = time.time()
    system = _TRACKER.sample_system(now_mono)
    plan = _PLANNER.build_plan(system, trim_threshold_mb, aggressive)
    result.pressure_level = plan.level
    result.reclaim_target_gb = plan.reclaim_target_gb
    own_pid = os.getpid()
    foreground_pid = _OPERATOR.get_foreground_pid()
    active_pids: set[int] = set()
    candidates = []
    for proc in psutil.process_iter(
        ["pid", "name", "memory_info", "create_time", "status", "username", "exe"],
        ad_value=None,
    ):
        try:
            info = proc.info
            pid = int(info["pid"])
            if pid == own_pid:
                continue
            active_pids.add(pid)
            if not plan.allow_recently_trimmed and _TRACKER.recently_trimmed(pid, now_mono, aggressive):
                continue
            if not plan.allow_recently_throttled and _TRACKER.recently_throttled(pid, now_mono, aggressive):
                continue
            telemetry = _TRACKER.sample_process(proc, now_mono)
            candidate = _SCORER.build_candidate(
                proc,
                info,
                telemetry,
                plan,
                now_wall,
                foreground_pid,
            )
            if candidate is not None:
                candidates.append(candidate)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            result.processes_skipped += 1
        except Exception as exc:  # pylint: disable=broad-exception-caught
            _append_error(result, f"Unexpected candidate error: {exc}")
    _TRACKER.prune(active_pids, now_mono)
    result.candidates_considered = len(candidates)
    for candidate in _SCORER.select_trim_targets(candidates, plan):
        try:
            freed = _OPERATOR.trim_workingset(candidate.pid)
            result.ram_freed_gb += freed / 1024.0
            result.processes_trimmed += 1
            result.trimmed_process_names.append(candidate.name)
            _TRACKER.note_trimmed(candidate.pid, now_mono)
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError) as exc:
            result.processes_skipped += 1
            _append_error(result, str(exc))
    for candidate in _SCORER.select_throttle_targets(candidates, plan):
        try:
            tags = _OPERATOR.apply_throttle(
                psutil.Process(candidate.pid),
                _PLANNER.build_throttle_action(candidate, plan),
            )
            if not tags:
                continue
            result.processes_throttled += 1
            result.throttled_process_names.append(candidate.name)
            result.cpu_throttled += int("cpu" in candidate.throttle_tags and "cpu" in tags)
            result.disk_throttled += int("disk" in candidate.throttle_tags and "disk" in tags)
            result.network_throttled += int(
                "network" in candidate.throttle_tags and ("cpu" in tags or "disk" in tags)
            )
            _TRACKER.note_throttled(candidate.pid, now_mono)
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError) as exc:
            result.processes_skipped += 1
            _append_error(result, str(exc))
    if flush_cache and plan.should_flush_standby:
        try:
            available_gb = psutil.virtual_memory().available / (1024 * 1024 * 1024)
            if aggressive or available_gb < plan.desired_available_gb:
                result.standby_flushed = _OPERATOR.flush_standby_cache()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            _append_error(result, f"Standby flush error: {exc}")
    LOGGER.info(
        "Resource release (%s): %s",
        "Aggressive" if aggressive else "AutoSmart",
        result.summary,
    )
    return result


def _append_error(result: ReleaseResult, message: str) -> None:
    if message in result.errors or len(result.errors) >= MAX_REPORTED_ERRORS:
        return
    result.errors.append(message)
