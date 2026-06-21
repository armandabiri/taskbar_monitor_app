"""System-wide memory reclaim: scan -> trim -> throttle -> kill -> flush.

The runner owns the full system-reclaim sequence and supports the
cleanup-safety controls added by the uplift:

* ``force``     – bypass the pressure-threshold gate.
* ``plan_only`` – scan + score only; execute nothing.
* ``cancel`` / ``progress`` – cooperative cancel + per-phase progress.
* ``bounds``    – wall-clock + cardinality caps (deadline, kill budget,
  max candidates, flush timeout, system-flush opt-in).

The flush phase + per-item kill bookkeeping live in :mod:`flush_phase` to
keep this module under the 300-line code-size cap.
"""

from __future__ import annotations

import os
import time

from services.resource_control import flush_phase as _flush
from services.resource_control import progress as progress_mod
from services.resource_control import runner_common as rc
from services.resource_control.bounds import CleanupBounds
from services.resource_control.cancel import CancelToken
from services.resource_control.models import ProcessCandidate, ReleaseResult, SkipReason
from services.resource_control.profiles import ResourceProfile
from services.resource_control.progress import CleanupPhase, CleanupProgress, ProgressCallback
from services.resource_control.system_scan import scan_system_reclaim


class SystemReclaimRunner:
    """Owns one system-reclaim pass over all running processes."""

    def run(
        self,
        profile: ResourceProfile,
        scope,
        confirm_kill,
        snapshot_spare_keys: frozenset[tuple[str, str]],
        result: ReleaseResult,
        *,
        force: bool = False,
        plan_only: bool = False,
        cancel: CancelToken | None = None,
        progress: ProgressCallback | None = None,
        bounds: CleanupBounds | None = None,
    ) -> None:
        del scope
        bounds = bounds or CleanupBounds()
        deadline_at = time.monotonic() + max(0.0, bounds.deadline_s)
        result.was_forced = force
        result.plan_only = plan_only
        now_mono = time.monotonic()
        now_wall = time.time()
        system = rc.TRACKER.sample_system(now_mono)
        plan = rc.PLANNER.build_plan(system, profile, force=force)
        result.pressure_level = plan.level
        result.reclaim_target_gb = plan.reclaim_target_gb

        below_threshold = (
            not profile.aggressive
            and system.memory_percent < profile.pressure_threshold_percent
        )
        if below_threshold and not force:
            result.record_skip(SkipReason.BELOW_PRESSURE_THRESHOLD)
            result.notes.append(
                f"System memory {system.memory_percent:.0f}% is below the configured threshold "
                f"{profile.pressure_threshold_percent:.0f}%, so only a low-pressure cleanup pass "
                "will run. Use Force Reclaim Now to run a full pass anyway."
            )
        elif below_threshold and force:
            result.notes.append(
                f"Forced run: system memory {system.memory_percent:.0f}% is below the "
                f"{profile.pressure_threshold_percent:.0f}% threshold, but a full pass was run."
            )

        own_pid = os.getpid()
        own_username = rc.safe_own_username()
        foreground_pid = rc.OPERATOR.get_foreground_pid()
        visible_window_pids, tray_icon_pids = rc.collect_ui_guard_pids(profile)

        decisions = scan_system_reclaim(
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
            cancel=cancel,
            progress=progress,
        )
        candidates = [d.candidate for d in decisions if d.candidate is not None]
        if bounds.max_candidates > 0 and len(candidates) > bounds.max_candidates:
            dropped = len(candidates) - bounds.max_candidates
            candidates = candidates[: bounds.max_candidates]
            result.notes.append(
                f"Capped candidates at {bounds.max_candidates}; "
                f"{dropped} lower-priority candidate(s) skipped."
            )
        result.candidates_considered = len(decisions)
        result.kill_candidates_found = len(rc.select_kill_targets(candidates, profile))

        if plan_only:
            result.preview_candidates = rc.SCORER.rank_trim_candidates(candidates)
            result.notes.append("Preview only — no processes were trimmed, throttled or killed.")
            progress_mod.emit(progress, CleanupProgress(CleanupPhase.DONE, executed=0))
            return

        if _flush.cancelled(cancel):
            result.notes.append("Run cancelled before any action was taken.")
            return

        self._execute(
            profile, plan, candidates, confirm_kill, now_mono, result,
            cancel=cancel, progress=progress,
            bounds=bounds, deadline_at=deadline_at,
        )

    def _execute(
        self,
        profile: ResourceProfile,
        plan,
        candidates: list[ProcessCandidate],
        confirm_kill,
        now_mono: float,
        result: ReleaseResult,
        *,
        cancel: CancelToken | None,
        progress: ProgressCallback | None,
        bounds: CleanupBounds,
        deadline_at: float,
    ) -> None:
        if profile.enable_trim and not _stop(cancel, deadline_at, result, "trim"):
            progress_mod.emit(progress, CleanupProgress(CleanupPhase.TRIMMING, executed=0))
            execute_trim_phase(
                candidates, plan, now_mono, result,
                cancel=cancel, deadline_at=deadline_at,
            )

        if profile.enable_throttle and not _stop(cancel, deadline_at, result, "throttle"):
            progress_mod.emit(progress, CleanupProgress(CleanupPhase.THROTTLING, executed=0))
            for candidate in rc.SCORER.select_throttle_targets(candidates, plan):
                if _stop(cancel, deadline_at, result, "throttle"):
                    break
                rc.do_throttle(
                    candidate, rc.PLANNER.build_throttle_action(candidate, plan), now_mono, result,
                )

        if profile.enable_kill and not _stop(cancel, deadline_at, result, "kill"):
            progress_mod.emit(progress, CleanupProgress(CleanupPhase.KILLING, executed=0))
            self._run_kill_phase(
                profile, candidates, confirm_kill, result,
                cancel=cancel, bounds=bounds, deadline_at=deadline_at,
            )

        if not _stop(cancel, deadline_at, result, "flush"):
            progress_mod.emit(progress, CleanupProgress(CleanupPhase.FLUSHING, executed=0))
            _flush.run_flush_phase(profile, plan, result, bounds=bounds, cancel=cancel)

    def _run_kill_phase(
        self, profile, candidates, confirm_kill, result: ReleaseResult,
        *, cancel: CancelToken | None,
        bounds: CleanupBounds, deadline_at: float,
    ) -> None:
        kill_targets = rc.select_kill_targets(candidates, profile)
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
        kill_budget_at = time.monotonic() + max(0.0, bounds.kill_budget_s)
        skipped = 0
        for candidate in kill_targets:
            if _flush.cancelled(cancel):
                skipped = len(kill_targets) - result.processes_killed
                result.notes.append("Kill phase stopped by cancel.")
                break
            now = time.monotonic()
            if now >= deadline_at or now >= kill_budget_at:
                skipped = len(kill_targets) - result.processes_killed
                result.notes.append(
                    f"Kill phase stopped at budget/deadline; {skipped} target(s) skipped."
                )
                break
            _flush.do_kill_bounded(candidate, result, bounds=bounds)
        if skipped:
            result.processes_skipped += skipped


def _stop(
    cancel: CancelToken | None, deadline_at: float, result: ReleaseResult, phase: str,
) -> bool:
    if _flush.cancelled(cancel):
        return True
    if time.monotonic() >= deadline_at:
        note = f"{phase} phase skipped — run deadline reached."
        if note not in result.notes:
            result.notes.append(note)
        return True
    return False


def execute_trim_phase(
    candidates: list[ProcessCandidate],
    plan,
    now_mono: float,
    result: ReleaseResult,
    *,
    cancel: CancelToken | None = None,
    deadline_at: float | None = None,
) -> None:
    """Trim candidates until the per-run budget is exhausted."""
    ranked = rc.SCORER.rank_trim_candidates(candidates)
    if not ranked or plan.max_trimmed_processes <= 0:
        return

    success_count = 0
    estimated_success_gb = 0.0
    max_trims = plan.max_trimmed_processes
    attempt_budget = min(len(ranked), max(max_trims * 4, max_trims + 4))
    reclaim_goal_gb = plan.reclaim_target_gb * 1.15

    for attempt_count, candidate in enumerate(ranked, start=1):
        if success_count >= plan.max_trimmed_processes:
            break
        if _flush.cancelled(cancel):
            break
        if deadline_at is not None and time.monotonic() >= deadline_at:
            break
        if not plan.aggressive and success_count > 0 and estimated_success_gb >= reclaim_goal_gb:
            break
        if attempt_count > attempt_budget:
            break
        if rc.do_trim(candidate, now_mono, result):
            success_count += 1
            estimated_success_gb += candidate.estimated_reclaim_gb
            # Pause briefly so the kernel writes evicted pages back to disk
            # in a steady trickle instead of one storm.
            time.sleep(0.03)
