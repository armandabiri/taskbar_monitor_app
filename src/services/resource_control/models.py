"""Dataclasses used by resource control."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ReleaseResult:
    ram_freed_gb: float = 0.0
    processes_trimmed: int = 0
    trimmed_process_names: list[str] = field(default_factory=list)
    processes_throttled: int = 0
    throttled_process_names: list[str] = field(default_factory=list)
    processes_killed: int = 0
    killed_process_names: list[str] = field(default_factory=list)
    kill_confirmed: bool | None = None  # None = no kill phase, True/False = user choice
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

    @property
    def summary(self) -> str:
        parts = [
            f"Freed ~{self.ram_freed_gb:.2f} GB",
            f"{self.processes_trimmed} procs trimmed",
        ]
        if self.processes_killed:
            parts.append(f"{self.processes_killed} procs killed")
        parts.append(f"{self.processes_throttled} procs throttled")
        parts.append(f"{self.pressure_level} pressure")
        if self.standby_flushed:
            parts.append("standby flushed")
        if self.working_sets_emptied:
            parts.append("WS emptied")
        if self.modified_pages_flushed:
            parts.append("modified flushed")
        if self.gc_collected:
            parts.append(f"GC collected {self.gc_collected}")
        if self.errors:
            parts.append(f"{len(self.errors)} errors")
        return " | ".join(parts)

    @property
    def details(self) -> str:
        lines = [
            self.summary,
            f"Target ~{self.reclaim_target_gb:.2f} GB | {self.candidates_considered} candidates",
            (
                f"Throttle CPU {self.cpu_throttled}"
                f" | Disk {self.disk_throttled}"
                f" | Net {self.network_throttled}"
            ),
        ]
        if self.trimmed_process_names:
            lines.append(f"Trimmed: {', '.join(sorted(set(self.trimmed_process_names))[:10])}")
        if self.killed_process_names:
            lines.append(f"Killed: {', '.join(sorted(set(self.killed_process_names))[:10])}")
        if self.throttled_process_names:
            lines.append(f"Throttled: {', '.join(sorted(set(self.throttled_process_names))[:10])}")
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
    is_spared: bool = False  # has visible window or tray icon — never trim/throttle/kill
    kill_eligible: bool = False  # not spared, not protected, owned by current user


@dataclass
class ThrottleAction:
    priority_class: Optional[int]
    io_priority: Optional[int]
    affinity_limit: Optional[int]
