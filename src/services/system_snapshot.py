"""Immutable system snapshot produced by SystemSampler.

A SystemSnapshot is the single value that flows from the sampler worker to
the UI thread per tick. It is intentionally a frozen dataclass so it can be
passed across threads via a Qt signal without shared mutable state.

The snapshot intentionally carries raw values, not formatted strings — the
UI is responsible for rendering. ``top_processes`` is left as ``None`` by
the foundation contract; C5 (T05) populates it from the shared process table.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SystemSnapshot:
    """One tick of system telemetry, immutable and thread-safe to publish."""

    sampled_at: float
    per_cpu: tuple[float, ...]
    cpu_avg: float
    ram_percent: float
    net_up_bps: float
    net_down_bps: float
    disk_rw_bps: float
    gpu_stats: Any = None
    sensors: Any = None
    battery: Any = None
    top_processes: tuple[Any, ...] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SamplerCounterState:
    """Previous net/disk counters used to compute per-tick deltas."""

    net_bytes_sent: int
    net_bytes_recv: int
    disk_read_bytes: int
    disk_write_bytes: int

    @classmethod
    def zero(cls) -> "SamplerCounterState":
        return cls(0, 0, 0, 0)
