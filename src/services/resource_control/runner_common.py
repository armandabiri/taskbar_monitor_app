"""Shared state and low-level helpers for the cleanup runners.

Both the system-reclaim and snapshot-reclaim runners (and the thin
``service`` facade) build on the singletons and primitive operations defined
here. Keeping them in one module lets ``service.py`` stay a small entry point
while the two runners live in their own files, all sharing the same tracker,
planner, scorer and Win32 operator instances.
"""

from __future__ import annotations

import logging
import os

import psutil

from services.resource_control.candidate_scorer import CandidateScorer
from services.resource_control.constants import GB, MAX_REPORTED_ERRORS
from services.resource_control.models import ProcessCandidate, ReleaseResult, ThrottleAction
from services.resource_control.planner import ResourcePlanner
from services.resource_control.profiles import ResourceProfile
from services.resource_control.skip_reasons import SkipReason
from services.resource_control.tracker import ActivityTracker
from services.resource_control.windows_ops import WindowsProcessOperator

LOGGER = logging.getLogger(__name__)

# Shared singletons used across every cleanup run. They hold cross-run state
# (activity samples, trim/throttle cooldowns, throttle journal) so they must be
# process-wide, not per-run.
TRACKER = ActivityTracker()
PLANNER = ResourcePlanner()
SCORER = CandidateScorer()
OPERATOR = WindowsProcessOperator()


def sample_available_gb() -> float | None:
    try:
        return psutil.virtual_memory().available / GB
    except psutil.Error:
        return None


def safe_own_username() -> str | None:
    try:
        return psutil.Process(os.getpid()).username()
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return None


def collect_ui_guard_pids(profile: ResourceProfile) -> tuple[frozenset[int], frozenset[int]]:
    visible: set[int] = set()
    tray: set[int] = set()
    if profile.spare_visible_windows:
        try:
            visible = OPERATOR.enumerate_visible_window_pids()
        except OSError as exc:
            LOGGER.warning("enumerate_visible_window_pids failed: %s", exc)
    if profile.spare_tray_icons:
        try:
            tray = OPERATOR.enumerate_tray_icon_pids()
        except OSError as exc:
            LOGGER.warning("enumerate_tray_icon_pids failed: %s", exc)
    return frozenset(visible), frozenset(tray)


def matches_keep_list(name: str, exe: str, keep_list: set[str]) -> bool:
    if not keep_list:
        return False
    lower_name = (name or "").lower()
    lower_exe = os.path.basename(exe or "").lower()
    return lower_name in keep_list or lower_exe in keep_list


def select_kill_targets(
    candidates: list[ProcessCandidate], profile: ResourceProfile,
) -> list[ProcessCandidate]:
    eligible = [c for c in candidates if c.kill_eligible]
    keep_list = set(profile.keep_list_entries())
    filtered = [c for c in eligible if not matches_keep_list(c.name, "", keep_list)]
    return sorted(filtered, key=lambda c: c.rss_gb, reverse=True)


def append_error(result: ReleaseResult, message: str) -> None:
    if message in result.errors or len(result.errors) >= MAX_REPORTED_ERRORS:
        return
    result.errors.append(message)


def do_trim(candidate: ProcessCandidate, now_mono: float, result: ReleaseResult) -> bool:
    """Trim one process working set and record the result."""
    try:
        freed = OPERATOR.trim_workingset(candidate.pid)
        result.ram_freed_gb += freed / 1024.0
        result.record_cleaned(candidate.pid, "trimmed", candidate.name)
        TRACKER.note_trimmed(candidate.pid, now_mono)
        return True
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError) as exc:
        result.record_skip(SkipReason.EXECUTION_FAILED)
        result.processes_skipped += 1
        append_error(result, str(exc))
        return False


def do_throttle(
    candidate: ProcessCandidate,
    action: ThrottleAction,
    now_mono: float,
    result: ReleaseResult,
) -> None:
    """Throttle one process, journaling its prior state so it can be restored."""
    try:
        proc = psutil.Process(candidate.pid)
        prior = OPERATOR.snapshot_throttle_state(proc)
        tags = OPERATOR.apply_throttle(proc, action)
        if not tags:
            return
        if prior is not None:
            TRACKER.note_throttle_journal(candidate.pid, candidate.name, prior)
        result.record_cleaned(candidate.pid, "throttled", candidate.name)
        result.cpu_throttled += int("cpu" in candidate.throttle_tags and "cpu" in tags)
        result.disk_throttled += int("disk" in candidate.throttle_tags and "disk" in tags)
        result.network_throttled += int(
            "network" in candidate.throttle_tags and ("cpu" in tags or "disk" in tags)
        )
        TRACKER.note_throttled(candidate.pid, now_mono)
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError) as exc:
        result.record_skip(SkipReason.EXECUTION_FAILED)
        result.processes_skipped += 1
        append_error(result, str(exc))


def do_kill(candidate: ProcessCandidate, result: ReleaseResult) -> None:
    try:
        rss_gb_before = candidate.rss_gb
        if OPERATOR.terminate_process(candidate.pid):
            result.record_cleaned(candidate.pid, "killed", candidate.name)
            result.ram_freed_gb += rss_gb_before
            TRACKER.forget(candidate.pid)
        else:
            result.record_skip(SkipReason.ACCESS_DENIED)
            result.processes_skipped += 1
    except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
        result.record_skip(SkipReason.ACCESS_DENIED)
        result.processes_skipped += 1
        append_error(result, f"kill {candidate.name}: {exc}")
