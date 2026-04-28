"""Stateful activity sampling for reclaim and throttling decisions."""

from __future__ import annotations

import time

import psutil

from services.resource_control.constants import (
    ACTIVITY_CACHE_TTL_SECONDS,
    GB,
    LOGICAL_CPU_COUNT,
)
from services.resource_control.models import ActivitySnapshot, ProcessTelemetry, SystemSnapshot
from services.resource_control.profiles import ResourceProfile


class ActivityTracker:
    """Holds lightweight counters across cleanup runs."""

    def __init__(self) -> None:
        self._process_activity: dict[int, ActivitySnapshot] = {}
        self._system_activity: ActivitySnapshot | None = None
        self._trimmed_at: dict[int, float] = {}
        self._throttled_at: dict[int, float] = {}

    def sample_system(self, now: float | None = None) -> SystemSnapshot:
        current = now or time.monotonic()
        vm = psutil.virtual_memory()
        cpu_percent = float(psutil.cpu_percent(interval=None))
        disk = psutil.disk_io_counters()
        net = psutil.net_io_counters()
        disk_bytes = int((disk.read_bytes + disk.write_bytes) if disk else 0)
        net_bytes = int((net.bytes_sent + net.bytes_recv) if net else 0)
        disk_gb_s = 0.0
        net_gb_s = 0.0
        previous = self._system_activity
        if previous is not None and current > previous.sampled_at:
            elapsed = current - previous.sampled_at
            disk_gb_s = max(disk_bytes - previous.read_bytes, 0) / elapsed / GB
            net_gb_s = max(net_bytes - previous.write_bytes, 0) / elapsed / GB
        self._system_activity = ActivitySnapshot(current, 0.0, disk_bytes, net_bytes, 0)
        return SystemSnapshot(
            sampled_at=current,
            memory_percent=float(vm.percent),
            available_gb=float(vm.available) / GB,
            total_gb=float(vm.total) / GB,
            cpu_percent=cpu_percent,
            disk_gb_s=disk_gb_s,
            net_gb_s=net_gb_s,
        )

    def sample_process(self, proc: psutil.Process, now: float) -> ProcessTelemetry:
        cpu_times = proc.cpu_times()
        io_counters = proc.io_counters()
        total_cpu_time = float(cpu_times.user + cpu_times.system)
        read_bytes = int(getattr(io_counters, "read_bytes", 0))
        write_bytes = int(getattr(io_counters, "write_bytes", 0))
        other_bytes = int(getattr(io_counters, "other_bytes", 0))
        cpu_percent = None
        disk_gb_s = 0.0
        other_gb_s = 0.0
        previous = self._process_activity.get(proc.pid)
        self._process_activity[proc.pid] = ActivitySnapshot(
            sampled_at=now,
            cpu_time_s=total_cpu_time,
            read_bytes=read_bytes,
            write_bytes=write_bytes,
            other_bytes=other_bytes,
        )
        if previous is not None and now > previous.sampled_at:
            elapsed = now - previous.sampled_at
            cpu_delta = max(total_cpu_time - previous.cpu_time_s, 0.0)
            disk_delta = max(read_bytes - previous.read_bytes, 0) + max(
                write_bytes - previous.write_bytes, 0
            )
            other_delta = max(other_bytes - previous.other_bytes, 0)
            cpu_percent = (cpu_delta / (elapsed * LOGICAL_CPU_COUNT)) * 100.0
            disk_gb_s = float(disk_delta) / elapsed / GB
            other_gb_s = float(other_delta) / elapsed / GB
        return ProcessTelemetry(
            cpu_percent=cpu_percent,
            disk_gb_s=disk_gb_s,
            other_gb_s=other_gb_s,
            total_cpu_time=total_cpu_time,
            read_bytes=read_bytes,
            write_bytes=write_bytes,
            other_bytes=other_bytes,
        )

    def recently_trimmed(self, pid: int, now: float, profile: ResourceProfile) -> bool:
        trimmed_at = self._trimmed_at.get(pid)
        return trimmed_at is not None and (now - trimmed_at) < profile.trim_cooldown_seconds

    def recently_throttled(self, pid: int, now: float, profile: ResourceProfile) -> bool:
        throttled_at = self._throttled_at.get(pid)
        return throttled_at is not None and (now - throttled_at) < profile.throttle_cooldown_seconds

    def note_trimmed(self, pid: int, now: float) -> None:
        self._trimmed_at[pid] = now

    def note_throttled(self, pid: int, now: float) -> None:
        self._throttled_at[pid] = now

    def forget(self, pid: int) -> None:
        """Drop all cached state for a PID that is gone."""
        self._process_activity.pop(pid, None)
        self._trimmed_at.pop(pid, None)
        self._throttled_at.pop(pid, None)

    def prune(self, active_pids: set[int], now: float) -> None:
        for pid, sample in list(self._process_activity.items()):
            if pid not in active_pids or (now - sample.sampled_at) > ACTIVITY_CACHE_TTL_SECONDS:
                self._process_activity.pop(pid, None)
        for cache in (self._trimmed_at, self._throttled_at):
            for pid, stamped_at in list(cache.items()):
                if pid not in active_pids or (now - stamped_at) > ACTIVITY_CACHE_TTL_SECONDS:
                    cache.pop(pid, None)
