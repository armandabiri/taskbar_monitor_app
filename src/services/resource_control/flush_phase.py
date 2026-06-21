"""Bounded, gated, watchdog-wrapped system-flush phase.

Extracted from :mod:`system_reclaim` so the reclaim runner stays under the
300-line code-size cap. Wraps each ``NtSetSystemInformation`` call in
:class:`WatchedSystemOp` and respects the per-run ``CleanupBounds`` and
``CancelToken``.
"""

from __future__ import annotations

import psutil

from services.resource_control import runner_common as rc
from services.resource_control.bounds import CleanupBounds
from services.resource_control.cancel import CancelToken
from services.resource_control.models import ProcessCandidate, ReleaseResult, SkipReason
from services.resource_control.profiles import FLUSH_ALWAYS, FLUSH_NEVER, ResourceProfile
from services.resource_control.watched_op import OpResult, WatchedSystemOp


def cancelled(cancel: CancelToken | None) -> bool:
    return cancel is not None and cancel.cancelled


def record_flush_outcome(result: ReleaseResult, name: str, outcome: OpResult) -> None:
    if outcome.timed_out:
        result.notes.append(f"{name} timed out after {outcome.elapsed_s:.1f}s — abandoned.")
        rc.append_error(result, f"{name}: timed out after {outcome.elapsed_s:.1f}s")
        return
    if outcome.error:
        rc.append_error(result, f"{name}: {outcome.error}")


def do_kill_bounded(
    candidate: ProcessCandidate, result: ReleaseResult, *, bounds: CleanupBounds,
) -> None:
    """Like ``runner_common.do_kill`` but with per-item terminate-wait caps."""
    try:
        rss_gb_before = candidate.rss_gb
        ok = rc.OPERATOR.terminate_process(
            candidate.pid,
            graceful_timeout=bounds.per_kill_graceful_s,
            force_timeout=bounds.per_kill_force_s,
        )
        if ok:
            result.record_cleaned(candidate.pid, "killed", candidate.name)
            result.ram_freed_gb += rss_gb_before
            rc.TRACKER.forget(candidate.pid)
        else:
            result.record_skip(SkipReason.ACCESS_DENIED)
            result.processes_skipped += 1
    except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
        result.record_skip(SkipReason.ACCESS_DENIED)
        result.processes_skipped += 1
        rc.append_error(result, f"kill {candidate.name}: {exc}")


def should_flush_standby(profile: ResourceProfile, plan) -> bool:
    if profile.flush_standby == FLUSH_NEVER:
        return False
    if profile.flush_standby == FLUSH_ALWAYS:
        return True
    return plan.should_flush_standby


def run_flush_phase(
    profile: ResourceProfile, plan, result: ReleaseResult,
    *, bounds: CleanupBounds, cancel: CancelToken | None,
) -> None:
    if profile.flush_modified_pages:
        if not bounds.enable_system_flush:
            result.notes.append(
                "flush_modified_pages skipped — system-wide flush is disabled."
            )
        elif not cancelled(cancel):
            outcome = WatchedSystemOp.run(
                rc.OPERATOR.flush_modified_pages, bounds.flush_timeout_s,
            )
            record_flush_outcome(result, "flush_modified_pages", outcome)
            if outcome.ok:
                result.modified_pages_flushed = True
    if profile.empty_all_working_sets:
        if not bounds.enable_system_flush:
            result.notes.append(
                "empty_all_working_sets skipped — system-wide flush is disabled."
            )
        elif not cancelled(cancel):
            outcome = WatchedSystemOp.run(
                rc.OPERATOR.empty_all_working_sets, bounds.flush_timeout_s,
            )
            record_flush_outcome(result, "empty_all_working_sets", outcome)
            if outcome.ok:
                result.working_sets_emptied = True
    if should_flush_standby(profile, plan) and not cancelled(cancel):
        try:
            available_gb = rc.sample_available_gb() or 0.0
            needed = available_gb < plan.desired_available_gb
            if profile.flush_standby == FLUSH_ALWAYS or needed:
                outcome = WatchedSystemOp.run(
                    rc.OPERATOR.flush_standby_cache, bounds.flush_timeout_s,
                )
                record_flush_outcome(result, "flush_standby", outcome)
                if outcome.ok:
                    result.standby_flushed = True
        except Exception as exc:  # pylint: disable=broad-exception-caught
            rc.append_error(result, f"flush_standby: {exc}")
