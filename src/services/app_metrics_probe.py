"""Lightweight probe for the app's own process footprint and last cleanup outcome."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import psutil

from services.resource_control.history import read_history
from services.resource_control.models import CleanupHistoryEntry


@dataclass(frozen=True)
class AppMetrics:
    cpu_percent: float
    rss_mb: float
    last_cleanup: Optional[CleanupHistoryEntry]


class AppMetricsProbe:
    """Reads own-process CPU/RAM and the most recent cleanup history entry."""

    def __init__(self, pid: int | None = None) -> None:
        self._proc = psutil.Process(pid if pid is not None else os.getpid())

    def sample(self) -> AppMetrics:
        with self._proc.oneshot():
            cpu = self._proc.cpu_percent(interval=None)
            rss_mb = self._proc.memory_info().rss / (1024 * 1024)
        history = read_history(limit=1)
        last = history[0] if history else None
        return AppMetrics(cpu_percent=cpu, rss_mb=rss_mb, last_cleanup=last)
