"""SystemSampler — single owner of per-tick system reads.

The sampler exposes ``start_worker(interval_ms)`` / ``stop_worker()`` to run
the read on a background thread, and a UI-thread-safe ``snapshot_ready``
signal (Qt auto-queues across threads). ``tick()`` remains for tests and
manual drives.

``build_snapshot`` is the pure builder used by both the production path and
unit tests; inject ``readers`` to fake any individual source.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

import psutil
from PyQt6.QtCore import QObject, pyqtSignal

from .system_snapshot import SamplerCounterState, SystemSnapshot

LOGGER = logging.getLogger(__name__)

# Process table is rebuilt at most every N seconds (heavy psutil walk).
_TOP_PROC_REFRESH_S = 2.0
_TOP_PROC_LIMIT = 20


@dataclass
class SamplerReaders:
    """Injectable read seams — defaults call the real psutil/sensors stack."""

    per_cpu: Callable[[], list[float]]
    virtual_memory: Callable[[], Any]
    net_io: Callable[[], Any]
    disk_io: Callable[[], Any]
    gpu_stats: Callable[[], Any]
    sensors_snapshot: Callable[[], Any]
    battery: Callable[[], Any]
    top_processes: Callable[[int], list[Any]] | None = None
    clock: Callable[[], float] = time.monotonic


def default_readers() -> SamplerReaders:
    """Production readers — psutil + sensors hub + gpu/battery."""
    from services.sensors import get_hub
    from services.system_info import get_battery, get_gpu_stats, get_top_processes

    return SamplerReaders(
        per_cpu=lambda: psutil.cpu_percent(percpu=True),
        virtual_memory=psutil.virtual_memory,
        net_io=psutil.net_io_counters,
        disk_io=psutil.disk_io_counters,
        gpu_stats=get_gpu_stats,
        sensors_snapshot=lambda: get_hub().snapshot(),
        battery=get_battery,
        top_processes=lambda limit: get_top_processes(limit=limit, sort_by="cpu"),
    )


def build_snapshot(
    readers: SamplerReaders,
    prev: SamplerCounterState,
    top_processes: tuple[Any, ...] | None = None,
) -> tuple[SystemSnapshot, SamplerCounterState]:
    """Pure builder: produce a SystemSnapshot and the next counter state."""
    per_cpu = list(readers.per_cpu())
    cpu_avg = sum(per_cpu) / len(per_cpu) if per_cpu else 0.0
    ram = float(readers.virtual_memory().percent)

    net = readers.net_io()
    up = float(net.bytes_sent - prev.net_bytes_sent) if prev.net_bytes_sent else 0.0
    down = float(net.bytes_recv - prev.net_bytes_recv) if prev.net_bytes_recv else 0.0

    disk = readers.disk_io()
    if disk is not None and prev.disk_read_bytes:
        r_diff = disk.read_bytes - prev.disk_read_bytes
        w_diff = disk.write_bytes - prev.disk_write_bytes
        disk_rw = float(r_diff + w_diff)
    else:
        disk_rw = 0.0

    snap = SystemSnapshot(
        sampled_at=readers.clock(),
        per_cpu=tuple(per_cpu),
        cpu_avg=cpu_avg,
        ram_percent=ram,
        net_up_bps=up,
        net_down_bps=down,
        disk_rw_bps=disk_rw,
        gpu_stats=readers.gpu_stats(),
        sensors=readers.sensors_snapshot(),
        battery=readers.battery(),
        top_processes=top_processes,
    )
    next_state = SamplerCounterState(
        net_bytes_sent=int(net.bytes_sent),
        net_bytes_recv=int(net.bytes_recv),
        disk_read_bytes=int(disk.read_bytes) if disk is not None else 0,
        disk_write_bytes=int(disk.write_bytes) if disk is not None else 0,
    )
    return snap, next_state


def choose_interval(
    active_ms: int,
    hidden_ms: int,
    *,
    visible: bool,
    on_battery: bool,
    pause_on_battery: bool,
) -> int:
    """Return the sampler interval to use given the current visibility/power state."""
    if (pause_on_battery and on_battery) or not visible:
        return hidden_ms
    return active_ms


class SystemSampler(QObject):
    """Owns the per-tick read and publishes SystemSnapshot via Qt signal.

    ``start_worker(interval_ms)`` spawns a daemon thread that builds snapshots
    off the UI thread and emits ``snapshot_ready`` (Qt queues the cross-thread
    delivery). ``tick()`` is retained for tests and explicit drives.
    """

    snapshot_ready = pyqtSignal(object)

    def __init__(
        self,
        readers: SamplerReaders | None = None,
        parent: QObject | None = None,
        *,
        top_proc_refresh_s: float = _TOP_PROC_REFRESH_S,
        top_proc_limit: int = _TOP_PROC_LIMIT,
    ) -> None:
        super().__init__(parent)
        self._readers = readers or default_readers()
        self._prev = SamplerCounterState.zero()
        self._interval_ms = 1000
        self._last: SystemSnapshot | None = None
        self._top_proc_refresh_s = float(top_proc_refresh_s)
        self._top_proc_limit = int(top_proc_limit)
        self._top_procs: tuple[Any, ...] | None = None
        self._top_procs_at: float = 0.0
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Worker lifecycle
    # ------------------------------------------------------------------
    def start_worker(self, interval_ms: int) -> None:
        """Begin sampling on a background daemon thread. Idempotent."""
        self.set_interval(interval_ms)
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="SystemSamplerWorker",
            daemon=True,
        )
        self._thread.start()

    def stop_worker(self, timeout_s: float = 2.0) -> None:
        """Signal the worker to exit and join it within ``timeout_s``."""
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout_s)
        self._thread = None

    def set_interval(self, interval_ms: int) -> None:
        with self._lock:
            self._interval_ms = max(50, int(interval_ms))

    def latest(self) -> SystemSnapshot | None:
        return self._last

    # ------------------------------------------------------------------
    # Tick
    # ------------------------------------------------------------------
    def tick(self) -> None:
        """Build a snapshot and emit it. Safe from any thread."""
        try:
            top = self._maybe_refresh_top_processes()
            snap, self._prev = build_snapshot(self._readers, self._prev, top)
        except (psutil.Error, RuntimeError):
            LOGGER.exception("SystemSampler tick failed")
            return
        self._last = snap
        self.snapshot_ready.emit(snap)

    def _maybe_refresh_top_processes(self) -> tuple[Any, ...] | None:
        reader = self._readers.top_processes
        if reader is None:
            return self._top_procs
        now = self._readers.clock()
        if self._top_procs is None or (now - self._top_procs_at) >= self._top_proc_refresh_s:
            try:
                rows = reader(self._top_proc_limit)
                self._top_procs = tuple(rows)
                self._top_procs_at = now
            except (psutil.Error, RuntimeError):
                LOGGER.exception("SystemSampler top-process refresh failed")
        return self._top_procs

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            self.tick()
            with self._lock:
                interval_s = self._interval_ms / 1000.0
            if self._stop_event.wait(interval_s):
                return
