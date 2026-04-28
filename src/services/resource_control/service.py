"""High-level resource release orchestration."""

from __future__ import annotations

import gc
import logging
import os
import time
from typing import Callable, Optional

import psutil

from services.resource_control.candidate_scorer import CandidateScorer
from services.resource_control.constants import (
    MAX_REPORTED_ERRORS,
    PROTECTED_NAMES,
    PROTECTED_USERS,
    WINDOWS_DIR,
)
from services.resource_control.history import append_history
from services.resource_control.models import (
    CandidateDecision,
    CleanupMode,
    CleanupScope,
    ProcessCandidate,
    ReleaseResult,
    SkipReason,
)
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

ConfirmKillCallback = Callable[[list[ProcessCandidate]], Optional[list[ProcessCandidate]]]


def release_resources(
    profile: ResourceProfile | None = None,
    *,
    aggressive: bool | None = None,
    trim_threshold_mb: float | None = None,
    flush_cache: bool | None = None,
    run_gc: bool | None = None,
    confirm_kill: ConfirmKillCallback | None = None,
    snapshot_spare_keys: frozenset[tuple[str, str]] | None = None,
    scope: CleanupScope | None = None,
) -> ReleaseResult:
    """Release RAM and safely throttle/terminate hot background processes."""

    profile = _resolve_profile(profile, aggressive, trim_threshold_mb, flush_cache, run_gc)
    scope = _normalize_scope(scope)
    result = ReleaseResult(
        mode=scope.mode,
        profile_name=profile.name,
        snapshot_name=scope.snapshot_name,
        snapshot_matched_count=scope.snapshot_matched_count,
        snapshot_identity_collisions=scope.snapshot_identity_collisions,
    )
    result.memory_before_gb = _sample_available_gb()
    if profile.run_gc and scope.mode == CleanupMode.SYSTEM_RECLAIM.value:
        result.gc_collected = gc.collect()

    try:
        if scope.mode == CleanupMode.SNAPSHOT_EXTRAS.value:
            _run_snapshot_cleanup(profile, scope, confirm_kill, result)
        else:
            _run_system_reclaim(
                profile,
                scope,
                confirm_kill,
                snapshot_spare_keys or frozenset(),
                result,
            )
    finally:
        result.memory_after_gb = _sample_available_gb()
        try:
            append_history(result)
        except OSError:
            LOGGER.exception("Failed to append cleanup history")

    LOGGER.info(
        "Cleanup run=%s mode=%s profile=%s: %s",
        result.run_id,
        result.mode,
        profile.name,
        result.details.replace("\n", " | "),
    )
    return result


plan_cleanup = release_resources


def _resolve_profile(
    profile: ResourceProfile | None,
    aggressive: bool | None,
    trim_threshold_mb: float | None,
    flush_cache: bool | None,
    run_gc: bool | None,
) -> ResourceProfile:
    profile = profile or BALANCED
    if (
        aggressive is not None
        or trim_threshold_mb is not None
        or flush_cache is not None
        or run_gc is not None
    ):
        overrides: dict[str, object] = {}
        if aggressive is not None:
            overrides["aggressive"] = aggressive
        if trim_threshold_mb is not None:
            overrides["trim_threshold_mb"] = trim_threshold_mb
        if flush_cache is not None and not flush_cache:
            overrides["flush_standby"] = FLUSH_NEVER
        if run_gc is not None:
            overrides["run_gc"] = run_gc
        profile = profile.with_overrides(**overrides)
    return profile


def _normalize_scope(scope: CleanupScope | None) -> CleanupScope:
    if scope is not None:
        return scope
    return CleanupScope()


def _run_system_reclaim(
    profile: ResourceProfile,
    scope: CleanupScope,
    confirm_kill: ConfirmKillCallback | None,
    snapshot_spare_keys: frozenset[tuple[str, str]],
    result: ReleaseResult,
) -> None:
    del scope
    now_mono = time.monotonic()
    now_wall = time.time()
    system = _TRACKER.sample_system(now_mono)
    plan = _PLANNER.build_plan(system, profile)
    result.pressure_level = plan.level
    result.reclaim_target_gb = plan.reclaim_target_gb

    if not profile.aggressive and system.memory_percent < profile.pressure_threshold_percent:
        result.record_skip(SkipReason.BELOW_PRESSURE_THRESHOLD)
        result.notes.append(
            f"System memory {system.memory_percent:.0f}% is below the configured threshold "
            f"{profile.pressure_threshold_percent:.0f}%."
        )
        return

    own_pid = os.getpid()
    own_username = _safe_own_username()
    foreground_pid = _OPERATOR.get_foreground_pid()
    visible_window_pids, tray_icon_pids = _collect_ui_guard_pids(profile)

    decisions = _scan_system_reclaim(
        profile=profile,
        plan=plan,
        now_mono=now_mono,
        now_wall=now_wall,
        own_pid=own_pid,
        own_username=own_username,
        foreground_pid=foreground_pid,
        visible_window_pids=visible_window_pids,
        tray_icon_pids=tray_icon_pids,
        snapshot_spare_keys=snapshot_spare_keys,
        result=result,
    )
    accepted = [decision.candidate for decision in decisions if decision.candidate is not None]
    candidates = [candidate for candidate in accepted if candidate is not None]
    result.candidates_considered = len(decisions)
    result.kill_candidates_found = len(_select_kill_targets(candidates, profile))

    if profile.enable_trim:
        for candidate in _SCORER.select_trim_targets(candidates, plan):
            _do_trim(candidate, now_mono, result)

    if profile.enable_throttle:
        for candidate in _SCORER.select_throttle_targets(candidates, plan):
            _do_throttle(candidate, plan, now_mono, result)

    if profile.enable_kill:
        kill_targets = _select_kill_targets(candidates, profile)
        if kill_targets and confirm_kill is not None:
            approved = confirm_kill(kill_targets)
            if approved is None:
                result.kill_confirmed = False
                result.notes.append("Kill phase was cancelled by the user.")
                kill_targets = []
            else:
                result.kill_confirmed = True
                kill_targets = approved
        else:
            result.kill_confirmed = bool(kill_targets) if kill_targets else None
        for candidate in kill_targets:
            _do_kill(candidate, result)

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
            available_gb = _sample_available_gb() or 0.0
            if profile.flush_standby == FLUSH_ALWAYS or available_gb < plan.desired_available_gb:
                result.standby_flushed = _OPERATOR.flush_standby_cache()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            _append_error(result, f"flush_standby: {exc}")


def _run_snapshot_cleanup(
    profile: ResourceProfile,
    scope: CleanupScope,
    confirm_kill: ConfirmKillCallback | None,
    result: ReleaseResult,
) -> None:
    result.snapshot_extras_found = len(scope.candidate_pids)
    result.snapshot_extras_selected = len(scope.target_pids)
    result.kill_confirmed = True if scope.target_pids else None
    result.notes.append(
        "Snapshot cleanup ignores memory-pressure thresholds and targets only the selected extra PIDs."
    )

    if not scope.candidate_pids:
        result.record_skip(SkipReason.SNAPSHOT_NOT_EXTRA)
        result.notes.append("No extra live processes were found for the selected snapshot.")
        return

    foreground_pid = _OPERATOR.get_foreground_pid()
    own_pid = os.getpid()
    own_username = _safe_own_username()
    keep_list = set(profile.keep_list_entries())
    live_targets: list[ProcessCandidate] = []
    seen_candidate_pids: set[int] = set()

    for proc in psutil.process_iter(
        ["pid", "name", "memory_info", "username", "exe", "create_time"],
        ad_value=None,
    ):
        try:
            info = proc.info
            pid = int(info["pid"])
            if pid not in scope.candidate_pids:
                continue
            seen_candidate_pids.add(pid)
            result.candidates_considered += 1
            if pid not in scope.target_pids:
                result.record_skip(SkipReason.SNAPSHOT_NOT_SELECTED)
                result.processes_skipped += 1
                continue
            decision = _snapshot_candidate_decision(
                info=info,
                own_pid=own_pid,
                own_username=own_username,
                foreground_pid=foreground_pid,
                keep_list=keep_list,
            )
            if decision.skip_reason is not None:
                result.record_skip(decision.skip_reason)
                result.processes_skipped += 1
                continue
            if decision.candidate is not None:
                live_targets.append(decision.candidate)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            result.record_skip(SkipReason.ACCESS_DENIED)
            result.processes_skipped += 1

    missing_candidates = sorted(scope.candidate_pids - seen_candidate_pids)
    if missing_candidates:
        result.notes.append(
            f"{len(missing_candidates)} snapshot extra process(es) exited before execution."
        )
    result.kill_candidates_found = len(live_targets)

    if live_targets and confirm_kill is not None:
        approved = confirm_kill(live_targets)
        if approved is None:
            result.kill_confirmed = False
            result.notes.append("Snapshot kill phase was cancelled by the user.")
            live_targets = []
        else:
            live_targets = approved
            result.snapshot_extras_selected = len(approved)

    for candidate in live_targets:
        _do_kill(candidate, result)


def _scan_system_reclaim(
    *,
    profile: ResourceProfile,
    plan,
    now_mono: float,
    now_wall: float,
    own_pid: int,
    own_username: str | None,
    foreground_pid: int | None,
    visible_window_pids: frozenset[int],
    tray_icon_pids: frozenset[int],
    snapshot_spare_keys: frozenset[tuple[str, str]],
    result: ReleaseResult,
) -> list[CandidateDecision]:
    decisions: list[CandidateDecision] = []
    active_pids: set[int] = set()
    keep_list = set(profile.keep_list_entries())

    for proc in psutil.process_iter(
        ["pid", "name", "memory_info", "create_time", "status", "username", "exe"],
        ad_value=None,
    ):
        try:
            info = proc.info
            pid = int(info["pid"])
            name = (info.get("name") or "").lower()
            if pid == own_pid:
                result.record_skip(SkipReason.OWN_PROCESS)
                result.processes_skipped += 1
                continue
            active_pids.add(pid)
            if snapshot_spare_keys:
                key = (name, (info.get("exe") or "").lower())
                if key in snapshot_spare_keys:
                    result.record_skip(SkipReason.SNAPSHOT_BASELINE_MATCH)
                    result.processes_skipped += 1
                    continue
            if _matches_keep_list(name, str(info.get("exe") or ""), keep_list):
                result.record_skip(SkipReason.KEEP_LIST)
                result.processes_skipped += 1
                continue
            if not plan.allow_recently_trimmed and _TRACKER.recently_trimmed(pid, now_mono, profile):
                result.record_skip(SkipReason.RECENTLY_TRIMMED)
                result.processes_skipped += 1
                continue
            if not plan.allow_recently_throttled and _TRACKER.recently_throttled(pid, now_mono, profile):
                result.record_skip(SkipReason.RECENTLY_THROTTLED)
                result.processes_skipped += 1
                continue
            telemetry = _TRACKER.sample_process(proc, now_mono)
            decision = _SCORER.evaluate_candidate(
                proc,
                info,
                telemetry,
                plan,
                now_wall,
                foreground_pid,
                profile,
                visible_window_pids=visible_window_pids,
                tray_icon_pids=tray_icon_pids,
                own_username=own_username,
            )
            decisions.append(decision)
            if decision.skip_reason is not None:
                result.record_skip(decision.skip_reason)
                result.processes_skipped += 1
            elif decision.eligible_for_kill is False and profile.enable_kill:
                username = (info.get("username") or "").lower()
                if own_username and username and username != own_username.lower():
                    result.record_skip(SkipReason.DIFFERENT_USER)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            result.record_skip(SkipReason.ACCESS_DENIED)
            result.processes_skipped += 1
        except Exception as exc:  # pylint: disable=broad-exception-caught
            _append_error(result, f"Unexpected candidate error: {exc}")

    _TRACKER.prune(active_pids, now_mono)
    return decisions


def _collect_ui_guard_pids(profile: ResourceProfile) -> tuple[frozenset[int], frozenset[int]]:
    visible: set[int] = set()
    tray: set[int] = set()
    if profile.spare_visible_windows:
        try:
            visible = _OPERATOR.enumerate_visible_window_pids()
        except OSError as exc:
            LOGGER.warning("enumerate_visible_window_pids failed: %s", exc)
    if profile.spare_tray_icons:
        try:
            tray = _OPERATOR.enumerate_tray_icon_pids()
        except OSError as exc:
            LOGGER.warning("enumerate_tray_icon_pids failed: %s", exc)
    return frozenset(visible), frozenset(tray)


def _snapshot_candidate_decision(
    *,
    info: dict,
    own_pid: int,
    own_username: str | None,
    foreground_pid: int | None,
    keep_list: set[str],
) -> CandidateDecision:
    pid = int(info["pid"])
    name = (info.get("name") or "").lower()
    exe = (info.get("exe") or "").lower()
    username = (info.get("username") or "").lower()
    if pid == own_pid:
        return CandidateDecision(pid, name, None, False, False, False, SkipReason.OWN_PROCESS, "extra")
    if pid == foreground_pid:
        return CandidateDecision(pid, name, None, False, False, False, SkipReason.FOREGROUND_PROCESS, "extra")
    if pid <= 4 or name in PROTECTED_NAMES:
        return CandidateDecision(pid, name, None, False, False, False, SkipReason.PROTECTED_NAME, "extra")
    if username in PROTECTED_USERS:
        return CandidateDecision(pid, name, None, False, False, False, SkipReason.PROTECTED_USER, "extra")
    if exe.startswith(WINDOWS_DIR):
        return CandidateDecision(pid, name, None, False, False, False, SkipReason.WINDOWS_BINARY, "extra")
    if _matches_keep_list(name, exe, keep_list):
        return CandidateDecision(pid, name, None, False, False, False, SkipReason.KEEP_LIST, "extra")
    if own_username and username and username != own_username.lower():
        return CandidateDecision(pid, name, None, False, False, False, SkipReason.DIFFERENT_USER, "extra")

    memory_info = info.get("memory_info")
    rss_bytes = float(getattr(memory_info, "rss", 0)) if memory_info is not None else 0.0
    candidate = ProcessCandidate(
        pid=pid,
        name=name,
        rss_gb=rss_bytes / (1024 * 1024 * 1024),
        uss_gb=None,
        cpu_percent=0.0,
        disk_gb_s=0.0,
        other_gb_s=0.0,
        age_seconds=None,
        estimated_reclaim_gb=rss_bytes / (1024 * 1024 * 1024),
        reclaim_score=rss_bytes / (1024 * 1024 * 1024),
        throttle_score=0.0,
        throttle_tags=(),
        is_spared=False,
        kill_eligible=True,
    )
    return CandidateDecision(pid, name, candidate, False, False, True, None, "extra")


def _select_kill_targets(
    candidates: list[ProcessCandidate], profile: ResourceProfile,
) -> list[ProcessCandidate]:
    eligible = [c for c in candidates if c.kill_eligible]
    keep_list = set(profile.keep_list_entries())
    filtered = [c for c in eligible if not _matches_keep_list(c.name, "", keep_list)]
    return sorted(filtered, key=lambda c: c.rss_gb, reverse=True)


def _do_trim(candidate: ProcessCandidate, now_mono: float, result: ReleaseResult) -> None:
    try:
        freed = _OPERATOR.trim_workingset(candidate.pid)
        result.ram_freed_gb += freed / 1024.0
        result.record_cleaned(candidate.pid, "trimmed", candidate.name)
        _TRACKER.note_trimmed(candidate.pid, now_mono)
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError) as exc:
        result.record_skip(SkipReason.EXECUTION_FAILED)
        result.processes_skipped += 1
        _append_error(result, str(exc))


def _do_throttle(candidate: ProcessCandidate, plan, now_mono: float, result: ReleaseResult) -> None:
    try:
        tags = _OPERATOR.apply_throttle(
            psutil.Process(candidate.pid),
            _PLANNER.build_throttle_action(candidate, plan),
        )
        if not tags:
            return
        result.record_cleaned(candidate.pid, "throttled", candidate.name)
        result.cpu_throttled += int("cpu" in candidate.throttle_tags and "cpu" in tags)
        result.disk_throttled += int("disk" in candidate.throttle_tags and "disk" in tags)
        result.network_throttled += int(
            "network" in candidate.throttle_tags and ("cpu" in tags or "disk" in tags)
        )
        _TRACKER.note_throttled(candidate.pid, now_mono)
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError) as exc:
        result.record_skip(SkipReason.EXECUTION_FAILED)
        result.processes_skipped += 1
        _append_error(result, str(exc))


def _do_kill(candidate: ProcessCandidate, result: ReleaseResult) -> None:
    try:
        rss_gb_before = candidate.rss_gb
        if _OPERATOR.terminate_process(candidate.pid):
            result.record_cleaned(candidate.pid, "killed", candidate.name)
            result.ram_freed_gb += rss_gb_before
            _TRACKER.forget(candidate.pid)
        else:
            result.record_skip(SkipReason.ACCESS_DENIED)
            result.processes_skipped += 1
    except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
        result.record_skip(SkipReason.ACCESS_DENIED)
        result.processes_skipped += 1
        _append_error(result, f"kill {candidate.name}: {exc}")


def _matches_keep_list(name: str, exe: str, keep_list: set[str]) -> bool:
    if not keep_list:
        return False
    lower_name = (name or "").lower()
    lower_exe = os.path.basename(exe or "").lower()
    return lower_name in keep_list or lower_exe in keep_list


def _safe_own_username() -> str | None:
    try:
        return psutil.Process(os.getpid()).username()
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return None


def _sample_available_gb() -> float | None:
    try:
        return psutil.virtual_memory().available / (1024 * 1024 * 1024)
    except psutil.Error:
        return None


def _should_flush_standby(profile: ResourceProfile, plan) -> bool:
    if profile.flush_standby == FLUSH_NEVER:
        return False
    if profile.flush_standby == FLUSH_ALWAYS:
        return True
    return plan.should_flush_standby


def _append_error(result: ReleaseResult, message: str) -> None:
    if message in result.errors or len(result.errors) >= MAX_REPORTED_ERRORS:
        return
    result.errors.append(message)
