"""Time/cardinality bounds applied to a cleanup run.

Threaded through ``release_resources`` so the kill phase cannot block for
tens of seconds, system-wide flushes cannot hang the kernel, and the
worker watchdog has a deadline it can enforce.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CleanupBounds:
    """All wall-clock and count caps for one cleanup run."""

    # Overall run wall-clock budget. Phases that would start after this
    # point are skipped; in-flight items finish but no new ones begin.
    deadline_s: float = 30.0
    # Total wall-clock the kill phase may consume across all targets.
    kill_budget_s: float = 8.0
    # Per-process graceful/forced terminate waits. Sum * targets must fit
    # under ``kill_budget_s``; these are the upper bounds per item.
    per_kill_graceful_s: float = 0.8
    per_kill_force_s: float = 0.5
    # Cap on candidates returned by the scan, after sorting. Protects
    # downstream phases from huge process tables.
    max_candidates: int = 256
    # Per-call watchdog for system-wide flush ops.
    flush_timeout_s: float = 5.0
    # Master gate for ``empty_all_working_sets`` / ``flush_modified_pages``.
    # Off by default — they pause the kernel and were the dominant hang
    # cause. Standby-cache flush stays governed by the profile.
    enable_system_flush: bool = False

    @classmethod
    def default(cls) -> "CleanupBounds":
        return cls()
