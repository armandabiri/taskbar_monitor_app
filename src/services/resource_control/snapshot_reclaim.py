"""Snapshot-extras cleanup: kill only the processes that appeared post-baseline.

Extracted from the former monolithic ``service`` module. Unlike system reclaim,
this path ignores memory-pressure thresholds entirely and targets only the
user-selected extra PIDs from a process snapshot diff.
"""

from __future__ import annotations

import os
import time

import psutil

from services.resource_control import runner_common as rc
from services.resource_control.constants import (
    PROTECTED_NAMES,
    PROTECTED_USERS,
    WINDOWS_DIR,
)
from services.resource_control.models import (
    CandidateDecision,
    ProcessCandidate,
    ReleaseResult,
    SkipReason,
)
from services.resource_control.profiles import ResourceProfile

_GB = 1024 * 1024 * 1024


class SnapshotCleanupRunner:
    """Owns the snapshot-extras kill sequence."""

    def run(
        self,
        profile: ResourceProfile,
        scope,
        confirm_kill,
        result: ReleaseResult,
    ) -> None:
        result.snapshot_extras_found = len(scope.candidate_pids)
        result.snapshot_extras_selected = len(scope.target_pids)
        result.kill_confirmed = True if scope.target_pids else None
        result.notes.append(
            "Snapshot cleanup ignores memory-pressure thresholds and targets only "
            "the selected extra PIDs."
        )

        if not scope.candidate_pids:
            result.record_skip(SkipReason.SNAPSHOT_NOT_EXTRA)
            result.notes.append("No extra live processes were found for the selected snapshot.")
            return

        foreground_pid = rc.OPERATOR.get_foreground_pid()
        own_pid = os.getpid()
        own_username = rc.safe_own_username()
        keep_list = set(profile.keep_list_entries())
        live_targets: list[ProcessCandidate] = []
        seen_candidate_pids: set[int] = set()

        seen = 0
        for proc in psutil.process_iter(
            ["pid", "name", "memory_info", "username", "exe", "create_time"],
            ad_value=None,
        ):
            seen += 1
            if seen % 25 == 0:
                time.sleep(0.001)
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
            rc.do_kill(candidate, result)


def _snapshot_skip(pid: int, name: str, reason: SkipReason) -> CandidateDecision:
    return CandidateDecision(pid, name, None, False, False, False, reason, "extra")


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
        return _snapshot_skip(pid, name, SkipReason.OWN_PROCESS)
    if pid == foreground_pid:
        return _snapshot_skip(pid, name, SkipReason.FOREGROUND_PROCESS)
    if pid <= 4 or name in PROTECTED_NAMES:
        return _snapshot_skip(pid, name, SkipReason.PROTECTED_NAME)
    if username in PROTECTED_USERS:
        return _snapshot_skip(pid, name, SkipReason.PROTECTED_USER)
    if exe.startswith(WINDOWS_DIR):
        return _snapshot_skip(pid, name, SkipReason.WINDOWS_BINARY)
    if rc.matches_keep_list(name, exe, keep_list):
        return _snapshot_skip(pid, name, SkipReason.KEEP_LIST)
    if own_username and username and username != own_username.lower():
        return _snapshot_skip(pid, name, SkipReason.DIFFERENT_USER)

    memory_info = info.get("memory_info")
    rss_bytes = float(getattr(memory_info, "rss", 0)) if memory_info is not None else 0.0
    candidate = ProcessCandidate(
        pid=pid,
        name=name,
        rss_gb=rss_bytes / _GB,
        uss_gb=None,
        cpu_percent=0.0,
        disk_gb_s=0.0,
        other_gb_s=0.0,
        age_seconds=None,
        estimated_reclaim_gb=rss_bytes / _GB,
        reclaim_score=rss_bytes / _GB,
        throttle_score=0.0,
        throttle_tags=(),
        is_spared=False,
        kill_eligible=True,
    )
    return CandidateDecision(pid, name, candidate, False, False, True, None, "extra")
