"""High-level resource release orchestration (thin facade).

The heavy lifting lives in :mod:`system_reclaim` and :mod:`snapshot_reclaim`;
this module resolves the profile/scope, drives the right runner, performs the
post-run measured-reclaim verification, and persists history. The public
``release_resources`` signature stays additive and backward-compatible.
"""

from __future__ import annotations

import gc
import logging
import time
from typing import Callable, Optional

from services.resource_control import runner_common as rc
from services.resource_control import system_reclaim as _system_reclaim
from services.resource_control.cancel import CancelToken
from services.resource_control.history import append_history
from services.resource_control.models import (
    CleanupMode,
    CleanupScope,
    ProcessCandidate,
    ReleaseResult,
)
from services.resource_control.profiles import (
    BALANCED,
    FLUSH_NEVER,
    ResourceProfile,
)
from services.resource_control.progress import CleanupPhase, CleanupProgress, ProgressCallback
from services.resource_control.snapshot_reclaim import SnapshotCleanupRunner
from services.resource_control.system_reclaim import SystemReclaimRunner

LOGGER = logging.getLogger(__name__)

# Shared singletons re-exported so existing call sites / tests that reach into
# ``service._TRACKER`` etc. keep working after the split.
_TRACKER = rc.TRACKER
_PLANNER = rc.PLANNER
_SCORER = rc.SCORER
_OPERATOR = rc.OPERATOR
_sample_available_gb = rc.sample_available_gb

_SYSTEM_RUNNER = SystemReclaimRunner()
_SNAPSHOT_RUNNER = SnapshotCleanupRunner()

# How long to let the OS settle before measuring the real available-RAM delta.
# Windows may re-page or lazily reclaim, so a brief pause gives a truer number.
_SETTLE_SECONDS = 0.4

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
    force: bool = False,
    plan_only: bool = False,
    cancel: CancelToken | None = None,
    progress: ProgressCallback | None = None,
) -> ReleaseResult:
    """Release RAM and safely throttle/terminate hot background processes.

    ``force`` bypasses the pressure-threshold gate and runs a full pass.
    ``plan_only`` scans + scores without executing anything (dry-run preview).
    ``cancel`` / ``progress`` enable cooperative cancellation and live progress.
    """

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
    is_system = scope.mode == CleanupMode.SYSTEM_RECLAIM.value
    if profile.run_gc and is_system and not plan_only:
        result.gc_collected = gc.collect()

    try:
        if scope.mode == CleanupMode.SNAPSHOT_EXTRAS.value:
            _SNAPSHOT_RUNNER.run(profile, scope, confirm_kill, result)
        else:
            _SYSTEM_RUNNER.run(
                profile,
                scope,
                confirm_kill,
                snapshot_spare_keys or frozenset(),
                result,
                force=force,
                plan_only=plan_only,
                cancel=cancel,
                progress=progress,
            )
            if is_system and not plan_only and not (cancel is not None and cancel.cancelled):
                _measure_system_freed(result, progress)
    finally:
        result.memory_after_gb = _sample_available_gb()
        if not plan_only:
            try:
                append_history(result)
            except OSError:
                LOGGER.exception("Failed to append cleanup history")

    LOGGER.info(
        "Cleanup run=%s mode=%s profile=%s force=%s plan_only=%s: %s",
        result.run_id,
        result.mode,
        profile.name,
        force,
        plan_only,
        result.details.replace("\n", " | "),
    )
    return result


plan_cleanup = release_resources


def _measure_system_freed(result: ReleaseResult, progress: ProgressCallback | None) -> None:
    """Re-sample available RAM after a short settle and record the real delta."""
    from services.resource_control import progress as progress_mod

    progress_mod.emit(progress, CleanupProgress(CleanupPhase.VERIFYING))
    if result.memory_before_gb is None:
        return
    time.sleep(_SETTLE_SECONDS)
    after = _sample_available_gb()
    if after is None:
        return
    # Positive = the system has more free RAM than before the run. Labelled a
    # "system delta" (not "freed by cleanup") since other processes also move.
    result.system_freed_gb = after - result.memory_before_gb


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


def reset_throttled_processes() -> tuple[int, int]:
    """Restore every journaled throttle. Returns (restored, attempted)."""
    journal = rc.TRACKER.throttle_journal()
    restored = 0
    for pid, _name, prior in journal:
        if rc.OPERATOR.restore_throttle(pid, prior):
            restored += 1
        rc.TRACKER.forget_throttle_journal(pid)
    return restored, len(journal)


def throttled_process_count() -> int:
    """Number of processes currently in the throttle-restore journal."""
    return rc.TRACKER.throttle_journal_size()


# Keep these names importable from ``service`` for callers/tests that used the
# pre-split module-level helpers.
scan_system_reclaim = _system_reclaim.scan_system_reclaim
execute_trim_phase = _system_reclaim.execute_trim_phase
