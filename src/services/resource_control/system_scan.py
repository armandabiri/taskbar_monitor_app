"""The system-reclaim process scan (walk + score every candidate).

Split out of :mod:`system_reclaim` so each file stays small. The scan is the
hot loop: it walks ``psutil.process_iter`` once, applies the cheap protection
filters, then defers to the scorer for the expensive USS-based evaluation. It
emits progress and honours the cancel token at batch boundaries.
"""

from __future__ import annotations

import time

import psutil

from services.resource_control import progress as progress_mod
from services.resource_control import runner_common as rc
from services.resource_control.cancel import CancelToken
from services.resource_control.models import CandidateDecision, ReleaseResult, SkipReason
from services.resource_control.profiles import ResourceProfile
from services.resource_control.progress import CleanupPhase, CleanupProgress, ProgressCallback
from services.resource_control.uss_prefetch import prefetch_uss

# Yield to the UI thread / check cancel every this many scanned processes.
SCAN_BATCH = 25


def _cancelled(cancel: CancelToken | None) -> bool:
    return cancel is not None and cancel.cancelled


def scan_system_reclaim(
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
    cancel: CancelToken | None = None,
    progress: ProgressCallback | None = None,
) -> list[CandidateDecision]:
    decisions: list[CandidateDecision] = []
    active_pids: set[int] = set()
    keep_list = set(profile.keep_list_entries())

    # Minimum RSS a process must have before we even score it. Anything smaller
    # can never meaningfully contribute, so dropping it before the scorer's USS
    # lookup (an extra per-process syscall on Windows) is a big speedup.
    scan_floor_bytes = max(int(profile.trim_threshold_mb * 1024 * 1024) // 2, 16 * 1024 * 1024)

    # Materialize the walk once so we can prefetch USS for the big processes
    # concurrently before scoring (the expensive part), then score serially.
    procs = list(psutil.process_iter(
        ["pid", "name", "memory_info", "create_time", "status", "username", "exe"],
        ad_value=None,
    ))
    total = len(procs)
    uss_cache = {} if _cancelled(cancel) else _prefetch_uss(procs, profile)

    seen = 0
    for proc in procs:
        seen += 1
        if seen % SCAN_BATCH == 0:
            if _cancelled(cancel):
                result.notes.append(f"Scan cancelled after {seen} processes.")
                break
            progress_mod.emit(
                progress, CleanupProgress(CleanupPhase.SCANNING, scanned=seen, total=total),
            )
            time.sleep(0.001)
        try:
            _scan_one(
                proc=proc,
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
                keep_list=keep_list,
                scan_floor_bytes=scan_floor_bytes,
                active_pids=active_pids,
                decisions=decisions,
                result=result,
                uss_cache=uss_cache,
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            result.record_skip(SkipReason.ACCESS_DENIED)
            result.processes_skipped += 1
        except Exception as exc:  # pylint: disable=broad-exception-caught
            rc.append_error(result, f"Unexpected candidate error: {exc}")

    rc.TRACKER.prune(active_pids, now_mono)
    return decisions


def _prefetch_uss(procs: list, profile: ResourceProfile) -> dict[int, float | None]:
    """Resolve USS concurrently for the processes large enough to need it.

    Predicate is a superset of the scorer's ``needs_uss`` for the dominant
    (size-based) case, so anything the scorer later reads is a cache hit with
    the same value — ranking is unchanged, the syscalls just happen in parallel.
    """
    threshold_bytes = int(profile.trim_threshold_mb * 1024 * 1024)
    need = []
    for proc in procs:
        try:
            memory_info = proc.info.get("memory_info")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        rss = float(getattr(memory_info, "rss", 0)) if memory_info is not None else 0.0
        if rss >= threshold_bytes:
            need.append(proc)
    return prefetch_uss(need)


def _scan_one(
    *,
    proc,
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
    keep_list: set[str],
    scan_floor_bytes: int,
    active_pids: set[int],
    decisions: list[CandidateDecision],
    result: ReleaseResult,
    uss_cache: dict[int, float | None] | None = None,
) -> None:
    info = proc.info
    pid = int(info["pid"])
    name = (info.get("name") or "").lower()
    if pid == own_pid:
        result.record_skip(SkipReason.OWN_PROCESS)
        result.processes_skipped += 1
        return
    active_pids.add(pid)
    memory_info = info.get("memory_info")
    rss_bytes = float(getattr(memory_info, "rss", 0)) if memory_info is not None else 0.0
    if not profile.enable_kill and not snapshot_spare_keys and rss_bytes < scan_floor_bytes:
        result.record_skip(SkipReason.BELOW_TRIM_THRESHOLD)
        result.processes_skipped += 1
        return
    if snapshot_spare_keys and (name, (info.get("exe") or "").lower()) in snapshot_spare_keys:
        result.record_skip(SkipReason.SNAPSHOT_BASELINE_MATCH)
        result.processes_skipped += 1
        return
    if rc.matches_keep_list(name, str(info.get("exe") or ""), keep_list):
        result.record_skip(SkipReason.KEEP_LIST)
        result.processes_skipped += 1
        return
    if not plan.allow_recently_trimmed and rc.TRACKER.recently_trimmed(pid, now_mono, profile):
        result.record_skip(SkipReason.RECENTLY_TRIMMED)
        result.processes_skipped += 1
        return
    if not plan.allow_recently_throttled and rc.TRACKER.recently_throttled(pid, now_mono, profile):
        result.record_skip(SkipReason.RECENTLY_THROTTLED)
        result.processes_skipped += 1
        return
    telemetry = rc.TRACKER.sample_process(proc, now_mono)
    decision = rc.SCORER.evaluate_candidate(
        proc, info, telemetry, plan, now_wall, foreground_pid, profile,
        visible_window_pids=visible_window_pids,
        tray_icon_pids=tray_icon_pids,
        own_username=own_username,
        uss_cache=uss_cache,
    )
    decisions.append(decision)
    if decision.skip_reason is not None:
        result.record_skip(decision.skip_reason)
        result.processes_skipped += 1
    elif decision.eligible_for_kill is False and profile.enable_kill:
        username = (info.get("username") or "").lower()
        if own_username and username and username != own_username.lower():
            result.record_skip(SkipReason.DIFFERENT_USER)
