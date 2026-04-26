"""High-level resource release orchestration."""

from __future__ import annotations

import gc
import logging
import os
import time
from typing import Callable, Optional

import psutil

from services.resource_control.candidate_scorer import CandidateScorer
from services.resource_control.constants import MAX_REPORTED_ERRORS
from services.resource_control.models import ProcessCandidate, ReleaseResult
from services.resource_control.planner import ResourcePlanner
from services.resource_control.profiles import (
    BALANCED,
    FLUSH_ALWAYS,
    FLUSH_NEVER,
    ResourceProfile,
)
from services.resource_control.tracker import ActivityTracker
from services.resource_control.windows_ops import WindowsProcessOperator

LOGGER = logging.getLogger(__name__)

_TRACKER = ActivityTracker()
_PLANNER = ResourcePlanner()
_SCORER = CandidateScorer()
_OPERATOR = WindowsProcessOperator()

# Callback signature: receives the kill candidate list. Returns the filtered
# list of candidates the user approved; or None to skip the kill phase entirely.
ConfirmKillCallback = Callable[[list[ProcessCandidate]], Optional[list[ProcessCandidate]]]


def release_resources(
    profile: ResourceProfile | None = None,
    *,
    aggressive: bool | None = None,
    trim_threshold_mb: float | None = None,
    flush_cache: bool | None = None,
    run_gc: bool | None = None,
    confirm_kill: ConfirmKillCallback | None = None,
) -> ReleaseResult:
    """Release RAM and safely throttle/terminate hot background processes.

    The behaviour is driven entirely by ``profile``. When ``profile`` is None,
    the BALANCED preset is used. Legacy keyword arguments (aggressive,
    trim_threshold_mb, flush_cache, run_gc) override the corresponding profile
    fields and are kept for backward compatibility.

    When ``profile.enable_kill`` is True, ``confirm_kill`` (if provided) is
    invoked with the kill candidate list before any process is terminated.
    Returning False from the callback skips the kill phase entirely.
    """
    profile = profile or BALANCED
    if (aggressive is not None or trim_threshold_mb is not None
            or flush_cache is not None or run_gc is not None):
        overrides: dict = {}
        if aggressive is not None:
            overrides["aggressive"] = aggressive
        if trim_threshold_mb is not None:
            overrides["trim_threshold_mb"] = trim_threshold_mb
        if flush_cache is not None and not flush_cache:
            overrides["flush_standby"] = FLUSH_NEVER
        if run_gc is not None:
            overrides["run_gc"] = run_gc
        profile = profile.with_overrides(**overrides)

    result = ReleaseResult()
    if profile.run_gc:
        result.gc_collected = gc.collect()
    now_mono = time.monotonic()
    now_wall = time.time()
    system = _TRACKER.sample_system(now_mono)
    plan = _PLANNER.build_plan(system, profile)
    result.pressure_level = plan.level
    result.reclaim_target_gb = plan.reclaim_target_gb

    # Below threshold + not aggressive → skip the heavy per-process scan.
    if not profile.aggressive and system.memory_percent < profile.pressure_threshold_percent:
        LOGGER.info(
            "Resource release (%s): below pressure threshold %.0f%% — skipping scan",
            profile.name, profile.pressure_threshold_percent,
        )
        return result

    own_pid = os.getpid()
    own_username = _safe_own_username()
    foreground_pid = _OPERATOR.get_foreground_pid()
    spared_pids = _build_spare_set(profile, own_pid, foreground_pid)

    candidates = _scan_processes(
        profile, plan, now_mono, now_wall, own_pid, own_username,
        foreground_pid, spared_pids, result,
    )
    result.candidates_considered = len(candidates)

    # 1) Trim phase
    if profile.enable_trim:
        for candidate in _SCORER.select_trim_targets(
            [c for c in candidates if not c.is_spared], plan,
        ):
            _do_trim(candidate, now_mono, result)

    # 2) Throttle phase
    if profile.enable_throttle:
        for candidate in _SCORER.select_throttle_targets(
            [c for c in candidates if not c.is_spared], plan,
        ):
            _do_throttle(candidate, plan, now_mono, result)

    # 3) Kill phase — Nuclear-tier
    if profile.enable_kill:
        kill_targets = _select_kill_targets(candidates)
        if kill_targets and confirm_kill is not None:
            approved = confirm_kill(kill_targets)
            if approved is None:
                result.kill_confirmed = False
                kill_targets = []
            else:
                result.kill_confirmed = True
                kill_targets = approved
        else:
            result.kill_confirmed = bool(kill_targets) if kill_targets else None
        for candidate in kill_targets:
            _do_kill(candidate, result)

    # 4) System-wide reclaim (admin-only ops are best-effort and silent on failure)
    if profile.flush_modified_pages:
        try:
            result.modified_pages_flushed = _OPERATOR.flush_modified_pages()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            _append_error(result, f"flush_modified_pages: {exc}")

    if profile.empty_all_working_sets:
        try:
            result.working_sets_emptied = _OPERATOR.empty_all_working_sets()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            _append_error(result, f"empty_all_working_sets: {exc}")

    if _should_flush_standby(profile, plan):
        try:
            available_gb = psutil.virtual_memory().available / (1024 * 1024 * 1024)
            if profile.flush_standby == FLUSH_ALWAYS or available_gb < plan.desired_available_gb:
                result.standby_flushed = _OPERATOR.flush_standby_cache()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            _append_error(result, f"flush_standby: {exc}")

    LOGGER.info("Resource release (%s): %s", profile.name, result.summary)
    return result


# ---------------------------------------------------------------------------
# Phases
# ---------------------------------------------------------------------------

def _scan_processes(
    profile: ResourceProfile,
    plan,
    now_mono: float,
    now_wall: float,
    own_pid: int,
    own_username: str | None,
    foreground_pid: int | None,
    spared_pids: frozenset[int],
    result: ReleaseResult,
) -> list[ProcessCandidate]:
    candidates: list[ProcessCandidate] = []
    active_pids: set[int] = set()
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
            if not plan.allow_recently_trimmed and _TRACKER.recently_trimmed(pid, now_mono, profile):
                continue
            if not plan.allow_recently_throttled and _TRACKER.recently_throttled(pid, now_mono, profile):
                continue
            telemetry = _TRACKER.sample_process(proc, now_mono)
            candidate = _SCORER.build_candidate(
                proc, info, telemetry, plan, now_wall,
                foreground_pid, profile, spared_pids, own_username,
            )
            if candidate is not None:
                candidates.append(candidate)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            result.processes_skipped += 1
        except Exception as exc:  # pylint: disable=broad-exception-caught
            _append_error(result, f"Unexpected candidate error: {exc}")
    _TRACKER.prune(active_pids, now_mono)
    return candidates


def _select_kill_targets(candidates: list[ProcessCandidate]) -> list[ProcessCandidate]:
    """All kill-eligible candidates, sorted by RSS desc so the largest go first."""
    eligible = [c for c in candidates if c.kill_eligible and not c.is_spared]
    return sorted(eligible, key=lambda c: c.rss_gb, reverse=True)


def _do_trim(candidate: ProcessCandidate, now_mono: float, result: ReleaseResult) -> None:
    try:
        freed = _OPERATOR.trim_workingset(candidate.pid)
        result.ram_freed_gb += freed / 1024.0
        result.processes_trimmed += 1
        result.trimmed_process_names.append(candidate.name)
        _TRACKER.note_trimmed(candidate.pid, now_mono)
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError) as exc:
        result.processes_skipped += 1
        _append_error(result, str(exc))


def _do_throttle(
    candidate: ProcessCandidate, plan, now_mono: float, result: ReleaseResult,
) -> None:
    try:
        tags = _OPERATOR.apply_throttle(
            psutil.Process(candidate.pid),
            _PLANNER.build_throttle_action(candidate, plan),
        )
        if not tags:
            return
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


def _do_kill(candidate: ProcessCandidate, result: ReleaseResult) -> None:
    try:
        rss_gb_before = candidate.rss_gb
        if _OPERATOR.terminate_process(candidate.pid):
            result.processes_killed += 1
            result.killed_process_names.append(candidate.name)
            # Killing reclaims the entire RSS, not just the trim estimate.
            result.ram_freed_gb += rss_gb_before
        else:
            result.processes_skipped += 1
    except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
        result.processes_skipped += 1
        _append_error(result, f"kill {candidate.name}: {exc}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_spare_set(
    profile: ResourceProfile, own_pid: int, foreground_pid: int | None,
) -> frozenset[int]:
    """PIDs that must never be trimmed/throttled/killed."""
    spared: set[int] = {own_pid}
    if foreground_pid:
        spared.add(foreground_pid)
    if profile.spare_visible_windows:
        try:
            spared.update(_OPERATOR.enumerate_visible_window_pids())
        except OSError as exc:
            LOGGER.warning("enumerate_visible_window_pids failed: %s", exc)
    if profile.spare_tray_icons:
        try:
            spared.update(_OPERATOR.enumerate_tray_icon_pids())
        except OSError as exc:
            LOGGER.warning("enumerate_tray_icon_pids failed: %s", exc)
    return frozenset(spared)


def _safe_own_username() -> str | None:
    try:
        return psutil.Process(os.getpid()).username()
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return None


def _should_flush_standby(profile: ResourceProfile, plan) -> bool:
    if profile.flush_standby == FLUSH_NEVER:
        return False
    if profile.flush_standby == FLUSH_ALWAYS:
        return True
    return plan.should_flush_standby  # FLUSH_CRITICAL_ONLY → planner decides


def _append_error(result: ReleaseResult, message: str) -> None:
    if message in result.errors or len(result.errors) >= MAX_REPORTED_ERRORS:
        return
    result.errors.append(message)
