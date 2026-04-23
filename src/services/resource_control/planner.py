"""High-level planning for reclaim and throttling."""

from __future__ import annotations

from services.resource_control.constants import (
    AGGRESSIVE_MAX_RECLAIM_MB,
    AGGRESSIVE_MIN_RECLAIM_MB,
    SMART_MAX_RECLAIM_MB,
    SMART_MIN_RECLAIM_MB,
    VERY_HOT_CPU_PERCENT,
    VERY_HOT_DISK_MB_S,
)
from services.resource_control.models import ProcessCandidate, ResourcePlan, SystemSnapshot, ThrottleAction
import psutil


class ResourcePlanner:
    """Maps current system pressure to concrete reclaim and throttle actions."""

    def build_plan(self, system: SystemSnapshot, trim_threshold_mb: float, aggressive: bool) -> ResourcePlan:
        trim_threshold_gb = trim_threshold_mb / 1024.0
        available_ratio = (system.available_gb / system.total_gb) if system.total_gb else 0.0
        if system.memory_percent >= 93.0 or available_ratio <= 0.06:
            level, threshold, desired_ratio, trims, throttles, flush = "critical", 96.0, 0.16, 6, 4, True
        elif system.memory_percent >= 88.0 or available_ratio <= 0.10:
            level, threshold, desired_ratio, trims, throttles, flush = "high", 160.0, 0.14, 5, 3, True
        elif system.memory_percent >= 80.0 or available_ratio <= 0.16:
            level, threshold, desired_ratio, trims, throttles, flush = "elevated", 200.0, 0.12, 4, 2, aggressive
        else:
            level, threshold, desired_ratio, trims, throttles, flush = "low", 256.0, 0.12, 2, 1, False
        dynamic_threshold = (threshold / 1024.0) if aggressive else max(trim_threshold_gb, threshold / 1024.0)
        reclaim_floor = (AGGRESSIVE_MIN_RECLAIM_MB if aggressive else SMART_MIN_RECLAIM_MB) / 1024.0
        reclaim_cap = (AGGRESSIVE_MAX_RECLAIM_MB if aggressive else SMART_MAX_RECLAIM_MB) / 1024.0
        desired_available_gb = system.total_gb * (0.20 if aggressive else desired_ratio)
        reclaim_target_gb = min(
            max(desired_available_gb - system.available_gb, reclaim_floor),
            reclaim_cap,
        )
        return ResourcePlan(
            aggressive=aggressive,
            level=level,
            trim_threshold_gb=dynamic_threshold,
            reclaim_target_gb=reclaim_target_gb,
            desired_available_gb=desired_available_gb,
            should_flush_standby=flush,
            allow_foreground_trim=aggressive,
            allow_recently_trimmed=aggressive or level == "critical",
            allow_recently_throttled=aggressive or level == "critical",
            max_trimmed_processes=trims + (2 if aggressive else 0),
            max_throttled_processes=throttles + (1 if aggressive else 0),
            cpu_pressure=system.cpu_percent >= 80.0 or aggressive,
            disk_pressure=system.disk_gb_s >= (48.0 / 1024.0) or aggressive,
            network_pressure=system.net_gb_s >= (12.0 / 1024.0) or aggressive,
        )

    def build_throttle_action(self, candidate: ProcessCandidate, plan: ResourcePlan) -> ThrottleAction:
        priority = getattr(psutil, "BELOW_NORMAL_PRIORITY_CLASS", None)
        if "cpu" in candidate.throttle_tags and (plan.aggressive or candidate.cpu_percent >= VERY_HOT_CPU_PERCENT):
            priority = getattr(psutil, "IDLE_PRIORITY_CLASS", priority)
        io_priority = None
        if {"disk", "network"} & set(candidate.throttle_tags):
            low = getattr(psutil, "IOPRIO_LOW", None)
            very_low = getattr(psutil, "IOPRIO_VERYLOW", low)
            io_priority = very_low if (plan.aggressive or candidate.disk_gb_s >= (VERY_HOT_DISK_MB_S / 1024.0)) else low
        affinity_limit = None
        if "cpu" in candidate.throttle_tags and (plan.aggressive or plan.cpu_pressure):
            cpus = psutil.cpu_count() or 1
            affinity_limit = 2 if cpus >= 4 else 1
        return ThrottleAction(priority_class=priority, io_priority=io_priority, affinity_limit=affinity_limit)
