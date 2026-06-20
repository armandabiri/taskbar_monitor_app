"""Concurrent USS (unique set size) lookups for the scan.

``memory_full_info()`` is the most expensive psutil call on Windows — it opens
the process and walks its working-set list. Done serially for every large
process, it dominates scan time. This module reads USS for a batch of processes
concurrently via a bounded thread pool and returns a pid -> USS(GB) cache the
scorer consults, so the same value is used (ranking stays identical) without
paying the syscall on the critical path.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import psutil

from services.resource_control.constants import GB

# Cap concurrent USS lookups: enough to overlap syscall latency, bounded so a
# busy system with hundreds of processes doesn't spawn a thread per process.
MAX_USS_WORKERS = 8


def read_uss_gb(proc: psutil.Process) -> float | None:
    """Read a single process's USS in GB, or None if unavailable."""
    try:
        full_info = proc.memory_full_info()
    except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
        return None
    uss = getattr(full_info, "uss", None)
    return None if uss is None else float(uss) / GB


def prefetch_uss(
    procs: list[psutil.Process], *, max_workers: int = MAX_USS_WORKERS,
) -> dict[int, float | None]:
    """Resolve USS for ``procs`` concurrently. Returns a pid -> USS(GB) cache."""
    cache: dict[int, float | None] = {}
    if not procs:
        return cache
    workers = max(1, min(max_workers, len(procs)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="uss") as pool:
        for pid, uss in pool.map(_pid_and_uss, procs):
            cache[pid] = uss
    return cache


def _pid_and_uss(proc: psutil.Process) -> tuple[int, float | None]:
    return proc.pid, read_uss_gb(proc)
