"""System-wide memory reclaim: scan -> trim -> throttle -> kill -> flush.

Extracted from the former monolithic ``service`` module. The runner owns the
full system-reclaim sequence and now supports three orthogonal controls added
by the cleanup uplift:

* ``force``     – bypass the pressure-threshold gate and run a full pass.
* ``plan_only`` – scan + score only; execute nothing (dry-run preview).
* ``cancel`` / ``progress`` – cooperative cancel + per-phase progress reporting.
"""

from __future__ import annotations

import os
import time

from services.resource_control import progress as progress_mod
from services.resource_control import runner_common as rc
from services.resource_control.cancel import CancelToken
from services.resource_control.models import ProcessCandidate, ReleaseResult, SkipReason
from services.resource_control.profiles import FLUSH_ALWAYS, FLUSH_NEVER, ResourceProfile
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
    ) -> None:
        del scope
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
        result.candidates_considered = len(decisions)
        result.kill_candidates_found = len(rc.select_kill_targets(candidates, profile))

        if plan_only:
            result.preview_candidates = rc.SCORER.rank_trim_candidates(candidates)
            result.notes.append("Preview only — no processes were trimmed, throttled or killed.")
            progress_mod.emit(progress, CleanupProgress(CleanupPhase.DONE, executed=0))
            return

        if cancel is not None and cancel.cancelled:
            result.notes.append("Run cancelled before any action was taken.")
            return

        self._execute(
            profile, plan, candidates, confirm_kill, now_mono, result,
            cancel=cancel, progress=progress,
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
    ) -> None:
        if profile.enable_trim and not _cancelled(cancel):
            progress_mod.emit(progress, CleanupProgress(CleanupPhase.TRIMMING, executed=0))
            execute_trim_phase(candidates, plan, now_mono, result, cancel=cancel)

        if profile.enable_throttle and not _cancelled(cancel):
            progress_mod.emit(progress, CleanupProgress(CleanupPhase.THROTTLING, executed=0))
            for candidate in rc.SCORER.select_throttle_targets(candidates, plan):
                if _cancelled(cancel):
                    break
                rc.do_throttle(
                    candidate, rc.PLANNER.build_throttle_action(candidate, plan), now_mono, result,
                )

        if profile.enable_kill and not _cancelled(cancel):
            progress_mod.emit(progress, CleanupProgress(CleanupPhase.KILLING, executed=0))
            self._run_kill_phase(profile, candidates, confirm_kill, result)

        if not _cancelled(cancel):
            progress_mod.emit(progress, CleanupProgress(CleanupPhase.FLUSHING, executed=0))
            self._run_flush_phase(profile, plan, result)

    def _run_kill_phase(self, profile, candidates, confirm_kill, result: ReleaseResult) -> None:
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
        for candidate in kill_targets:
            rc.do_kill(candidate, result)

    def _run_flush_phase(self, profile: ResourceProfile, plan, result: ReleaseResult) -> None:
        if profile.flush_modified_pages:
            try:
                result.modified_pages_flushed = rc.OPERATOR.flush_modified_pages()
            except Exception as exc:  # pylint: disable=broad-exception-caught
                rc.append_error(result, f"flush_modified_pages: {exc}")
        if profile.empty_all_working_sets:
            try:
                result.working_sets_emptied = rc.OPERATOR.empty_all_working_sets()
            except Exception as exc:  # pylint: disable=broad-exception-caught
                rc.append_error(result, f"empty_all_working_sets: {exc}")
        if _should_flush_standby(profile, plan):
            try:
                available_gb = rc.sample_available_gb() or 0.0
                needed = available_gb < plan.desired_available_gb
                if profile.flush_standby == FLUSH_ALWAYS or needed:
                    result.standby_flushed = rc.OPERATOR.flush_standby_cache()
            except Exception as exc:  # pylint: disable=broad-exception-caught
                rc.append_error(result, f"flush_standby: {exc}")


def _cancelled(cancel: CancelToken | None) -> bool:
    return cancel is not None and cancel.cancelled


def execute_trim_phase(
    candidates: list[ProcessCandidate],
    plan,
    now_mono: float,
    result: ReleaseResult,
    *,
    cancel: CancelToken | None = None,
) -> None:
    """Trim candidates until the per-run budget is exhausted.

    Gentle profiles stop early once the estimated reclaim goal is met;
    aggressive profiles spend the full trim budget.
    """
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
        if _cancelled(cancel):
            break
        if not plan.aggressive and success_count > 0 and estimated_success_gb >= reclaim_goal_gb:
            break
        if attempt_count > attempt_budget:
            break
        if rc.do_trim(candidate, now_mono, result):
            success_count += 1
            estimated_success_gb += candidate.estimated_reclaim_gb
            # Pause briefly so the kernel writes evicted pages back to disk in a
            # steady trickle instead of one storm.
            time.sleep(0.03)


def _should_flush_standby(profile: ResourceProfile, plan) -> bool:
    if profile.flush_standby == FLUSH_NEVER:
        return False
    if profile.flush_standby == FLUSH_ALWAYS:
        return True
    return plan.should_flush_standby
