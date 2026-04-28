"""Dataclasses and enums used by resource control."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class CleanupMode(str, Enum):
    """High-level cleanup execution mode."""

    SYSTEM_RECLAIM = "system_reclaim"
    SNAPSHOT_EXTRAS = "snapshot_extras"


class SkipReason(str, Enum):
    """Why a process was not acted on."""

    OWN_PROCESS = "own_process"
    PROTECTED_NAME = "protected_name"
    KEEP_LIST = "keep_list"
    PROTECTED_USER = "protected_user"
    WINDOWS_BINARY = "windows_binary"
    FOREGROUND_PROCESS = "foreground_process"
    VISIBLE_WINDOW = "visible_window"
    TRAY_ICON = "tray_icon"
    NEW_PROCESS_GRACE = "new_process_grace"
    DIFFERENT_USER = "different_user"
    RECENTLY_TRIMMED = "recently_trimmed"
    RECENTLY_THROTTLED = "recently_throttled"
    BELOW_TRIM_THRESHOLD = "below_trim_threshold"
    NO_RECLAIM_VALUE = "no_reclaim_value"
    SNAPSHOT_BASELINE_MATCH = "snapshot_baseline_match"
    SNAPSHOT_NOT_SELECTED = "snapshot_not_selected"
    SNAPSHOT_NOT_EXTRA = "snapshot_not_extra"
    BELOW_PRESSURE_THRESHOLD = "below_pressure_threshold"
    ACCESS_DENIED = "access_denied"
    EXECUTION_FAILED = "execution_failed"


_SKIP_REASON_LABELS: dict[SkipReason, str] = {
    SkipReason.OWN_PROCESS: "own process",
    SkipReason.PROTECTED_NAME: "protected name",
    SkipReason.KEEP_LIST: "user keep-list",
    SkipReason.PROTECTED_USER: "protected user",
    SkipReason.WINDOWS_BINARY: "Windows binary",
    SkipReason.FOREGROUND_PROCESS: "foreground process",
    SkipReason.VISIBLE_WINDOW: "visible-window protection",
    SkipReason.TRAY_ICON: "tray-icon protection",
    SkipReason.NEW_PROCESS_GRACE: "new-process grace period",
    SkipReason.DIFFERENT_USER: "different user",
    SkipReason.RECENTLY_TRIMMED: "recently trimmed",
    SkipReason.RECENTLY_THROTTLED: "recently throttled",
    SkipReason.BELOW_TRIM_THRESHOLD: "below trim threshold",
    SkipReason.NO_RECLAIM_VALUE: "no reclaim value",
    SkipReason.SNAPSHOT_BASELINE_MATCH: "snapshot baseline match",
    SkipReason.SNAPSHOT_NOT_SELECTED: "snapshot extra not selected",
    SkipReason.SNAPSHOT_NOT_EXTRA: "not an extra process",
    SkipReason.BELOW_PRESSURE_THRESHOLD: "below pressure threshold",
    SkipReason.ACCESS_DENIED: "access denied",
    SkipReason.EXECUTION_FAILED: "execution failed",
}


def format_skip_reason(reason: SkipReason | str) -> str:
    """Return a user-facing label for a skip reason."""

    if isinstance(reason, SkipReason):
        return _SKIP_REASON_LABELS.get(reason, reason.value.replace("_", " "))
    try:
        enum_reason = SkipReason(reason)
    except ValueError:
        return str(reason).replace("_", " ")
    return _SKIP_REASON_LABELS.get(enum_reason, enum_reason.value.replace("_", " "))


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
        if self.mode == CleanupMode.SNAPSHOT_EXTRAS.value:
            if self.processes_cleaned_total == 0:
                dominant = self.dominant_skip_reason
                if dominant is not None:
                    return (
                        f"No processes cleaned. Snapshot extras: {self.snapshot_extras_found} found, "
                        f"{self.snapshot_extras_selected} selected. Blocked mostly by {dominant[0]} "
                        f"({dominant[1]})."
                    )
                return (
                    f"No processes cleaned. Snapshot extras: {self.snapshot_extras_found} found, "
                    f"{self.snapshot_extras_selected} selected."
                )
            return (
                f"Snapshot extras: {self.snapshot_extras_found} found, "
                f"{self.snapshot_extras_selected} selected, {self.processes_killed} killed"
            )

        base = (
            f"Cleaned {self.processes_cleaned_total} process(es): "
            f"{self.processes_trimmed} trimmed, {self.processes_killed} killed, "
            f"{self.processes_throttled} throttled"
        )
        if self.processes_cleaned_total == 0:
            dominant = self.dominant_skip_reason
            if dominant is not None:
                return f"{base}. Blocked mostly by {dominant[0]} ({dominant[1]})."
            return f"{base}. No eligible process actions were executed."
        return base

    @property
    def details(self) -> str:
        lines = [self.summary]
        if self.profile_name:
            lines.append(f"Profile: {self.profile_name}")
        if self.snapshot_name:
            lines.append(f"Snapshot: {self.snapshot_name}")
        lines.append(
            f"Mode: {self.mode} | Pressure: {self.pressure_level} | "
            f"Target ~{self.reclaim_target_gb:.2f} GB | Candidates {self.candidates_considered}"
        )
        if self.memory_before_gb is not None or self.memory_after_gb is not None:
            before = "?" if self.memory_before_gb is None else f"{self.memory_before_gb:.2f} GB"
            after = "?" if self.memory_after_gb is None else f"{self.memory_after_gb:.2f} GB"
            lines.append(f"Available RAM: {before} -> {after} | Estimated freed ~{self.ram_freed_gb:.2f} GB")
        if self.mode == CleanupMode.SNAPSHOT_EXTRAS.value:
            lines.append(
                f"Snapshot live diff: matched {self.snapshot_matched_count}, "
                f"extras {self.snapshot_extras_found}, selected {self.snapshot_extras_selected}, "
                f"identity collisions {self.snapshot_identity_collisions}"
            )
        else:
            lines.append(
                f"Kill candidates {self.kill_candidates_found} | "
                f"Throttle CPU {self.cpu_throttled} | Disk {self.disk_throttled} | Net {self.network_throttled}"
            )
        if self.top_block_reasons():
            formatted = ", ".join(f"{label} ({count})" for label, count in self.top_block_reasons())
            lines.append(f"Top block reasons: {formatted}")
        if self.trimmed_process_names:
            lines.append(f"Trimmed: {', '.join(sorted(set(self.trimmed_process_names))[:10])}")
        if self.killed_process_names:
            lines.append(f"Killed: {', '.join(sorted(set(self.killed_process_names))[:10])}")
        if self.throttled_process_names:
            lines.append(f"Throttled: {', '.join(sorted(set(self.throttled_process_names))[:10])}")
        if self.notes:
            lines.append(f"Notes: {' | '.join(self.notes[:5])}")
        if self.errors:
            lines.append(f"Issues: {' | '.join(self.errors[:5])}")
        return "\n".join(lines)


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
