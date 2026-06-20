"""Dataclasses and enums used by resource control."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from services.resource_control import result_render
from services.resource_control.skip_reasons import SkipReason, format_skip_reason

__all__ = [
    "CleanupMode",
    "SkipReason",
    "format_skip_reason",
    "CleanupScope",
    "ReleaseResult",
    "ActivitySnapshot",
    "SystemSnapshot",
    "ProcessTelemetry",
    "ResourcePlan",
    "ProcessCandidate",
    "CandidateDecision",
    "ThrottleAction",
    "CleanupHistoryEntry",
]


class CleanupMode(str, Enum):
    """High-level cleanup execution mode."""

    SYSTEM_RECLAIM = "system_reclaim"
    SNAPSHOT_EXTRAS = "snapshot_extras"


@dataclass(frozen=True)
class CleanupScope:
    """How a cleanup run should interpret candidates."""

    mode: str = CleanupMode.SYSTEM_RECLAIM.value
    snapshot_name: str | None = None
    candidate_pids: frozenset[int] = frozenset()
    target_pids: frozenset[int] = frozenset()
    snapshot_matched_count: int = 0
    snapshot_identity_collisions: int = 0

    @property
    def is_snapshot(self) -> bool:
        return self.mode == CleanupMode.SNAPSHOT_EXTRAS.value


@dataclass
class ReleaseResult:
    """Aggregated output of a cleanup run."""

    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    started_at: float = field(default_factory=time.time)
    mode: str = CleanupMode.SYSTEM_RECLAIM.value
    profile_name: str = ""
    snapshot_name: str | None = None
    ram_freed_gb: float = 0.0
    memory_before_gb: float | None = None
    memory_after_gb: float | None = None
    processes_trimmed: int = 0
    trimmed_process_names: list[str] = field(default_factory=list)
    processes_throttled: int = 0
    throttled_process_names: list[str] = field(default_factory=list)
    processes_killed: int = 0
    killed_process_names: list[str] = field(default_factory=list)
    kill_confirmed: bool | None = None
    kill_candidates_found: int = 0
    snapshot_extras_found: int = 0
    snapshot_extras_selected: int = 0
    snapshot_matched_count: int = 0
    snapshot_identity_collisions: int = 0
    cpu_throttled: int = 0
    disk_throttled: int = 0
    network_throttled: int = 0
    processes_skipped: int = 0
    errors: list[str] = field(default_factory=list)
    gc_collected: int = 0
    standby_flushed: bool = False
    working_sets_emptied: bool = False
    modified_pages_flushed: bool = False
    pressure_level: str = "low"
    reclaim_target_gb: float = 0.0
    candidates_considered: int = 0
    blocked_reason_counts: dict[str, int] = field(default_factory=dict)
    execution_reason_counts: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    cleaned_pids: set[int] = field(default_factory=set, repr=False)
    # Whether this run bypassed the pressure-threshold gate (Force Reclaim).
    was_forced: bool = False
    # Dry-run preview: scan + score only, no execution.
    plan_only: bool = False
    # Measured available-RAM delta after a post-run settle (None if not sampled).
    system_freed_gb: float | None = None
    # Ranked candidates produced by a plan_only/preview scan (not persisted).
    preview_candidates: list["ProcessCandidate"] = field(default_factory=list, repr=False)

    @property
    def processes_cleaned_total(self) -> int:
        return len(self.cleaned_pids)

    @property
    def dominant_skip_reason(self) -> tuple[str, int] | None:
        if not self.blocked_reason_counts:
            return None
        reason, count = max(
            self.blocked_reason_counts.items(),
            key=lambda item: (item[1], item[0]),
        )
        return format_skip_reason(reason), count

    def record_skip(self, reason: SkipReason, *, count: int = 1) -> None:
        key = reason.value
        self.blocked_reason_counts[key] = self.blocked_reason_counts.get(key, 0) + count

    def record_execution(self, label: str, *, count: int = 1) -> None:
        self.execution_reason_counts[label] = self.execution_reason_counts.get(label, 0) + count

    def record_cleaned(self, pid: int, action_name: str, process_name: str) -> None:
        self.cleaned_pids.add(pid)
        self.record_execution(action_name)
        if action_name == "trimmed":
            self.processes_trimmed += 1
            self.trimmed_process_names.append(process_name)
        elif action_name == "killed":
            self.processes_killed += 1
            self.killed_process_names.append(process_name)
        elif action_name == "throttled":
            self.processes_throttled += 1
            self.throttled_process_names.append(process_name)

    def top_block_reasons(self, limit: int = 5) -> list[tuple[str, int]]:
        ordered = sorted(
            self.blocked_reason_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )
        return [(format_skip_reason(reason), count) for reason, count in ordered[:limit]]

    @property
    def summary(self) -> str:
        return result_render.render_summary(self)

    @property
    def details(self) -> str:
        return result_render.render_details(self)

    def plain_reason(self) -> str:
        """Return a single plain-language sentence describing the outcome."""
        return result_render.render_plain_reason(self)


@dataclass
class ActivitySnapshot:
    sampled_at: float
    cpu_time_s: float
    read_bytes: int
    write_bytes: int
    other_bytes: int


@dataclass
class SystemSnapshot:
    sampled_at: float
    memory_percent: float
    available_gb: float
    total_gb: float
    cpu_percent: float
    disk_gb_s: float
    net_gb_s: float


@dataclass
class ProcessTelemetry:
    cpu_percent: Optional[float]
    disk_gb_s: float
    other_gb_s: float
    total_cpu_time: float
    read_bytes: int
    write_bytes: int
    other_bytes: int


@dataclass
class ResourcePlan:
    aggressive: bool
    level: str
    trim_threshold_gb: float
    reclaim_target_gb: float
    desired_available_gb: float
    should_flush_standby: bool
    allow_foreground_trim: bool
    allow_recently_trimmed: bool
    allow_recently_throttled: bool
    max_trimmed_processes: int
    max_throttled_processes: int
    cpu_pressure: bool
    disk_pressure: bool
    network_pressure: bool


@dataclass
class ProcessCandidate:
    pid: int
    name: str
    rss_gb: float
    uss_gb: Optional[float]
    cpu_percent: float
    disk_gb_s: float
    other_gb_s: float
    age_seconds: Optional[float]
    estimated_reclaim_gb: float
    reclaim_score: float
    throttle_score: float
    throttle_tags: tuple[str, ...] = ()
    is_spared: bool = False
    kill_eligible: bool = False


@dataclass
class CandidateDecision:
    """Decision for a single scanned process."""

    pid: int
    name: str
    candidate: ProcessCandidate | None
    eligible_for_trim: bool
    eligible_for_throttle: bool
    eligible_for_kill: bool
    skip_reason: SkipReason | None = None
    snapshot_status: str = "n/a"


@dataclass
class ThrottleAction:
    priority_class: Optional[int]
    io_priority: Optional[int]
    affinity_limit: Optional[int]


@dataclass
class CleanupHistoryEntry:
    """Persisted summary of a cleanup run."""

    run_id: str
    timestamp: float
    mode: str
    profile_name: str
    snapshot_name: str | None
    processes_cleaned_total: int
    processes_trimmed: int
    processes_killed: int
    processes_throttled: int
    kill_candidates_found: int
    snapshot_extras_found: int
    snapshot_extras_selected: int
    blocked_reason_counts: dict[str, int]
    errors: list[str]
    summary: str
    # Added by the cleanup uplift; optional so older history lines still load.
    system_freed_gb: float | None = None
    was_forced: bool = False
