"""Candidate scoring for memory reclaim and safe throttling."""

from __future__ import annotations

import psutil

from services.resource_control.constants import (
    GB,
    HOT_CPU_PERCENT,
    HOT_DISK_MB_S,
    HOT_OTHER_MB_S,
    MIN_ESTIMATED_RECLAIM_MB,
    PROTECTED_NAMES,
    PROTECTED_USERS,
    TOP_STATUS_BONUS,
    WARM_CPU_PERCENT,
    WARM_DISK_MB_S,
    WARM_OTHER_MB_S,
    WINDOWS_DIR,
)
from services.resource_control.models import ProcessCandidate, ProcessTelemetry, ResourcePlan
from services.resource_control.profiles import ResourceProfile


class CandidateScorer:
    """Builds ranked process candidates from raw telemetry."""

    def build_candidate(
        self,
        proc: psutil.Process,
        info: dict,
        telemetry: ProcessTelemetry,
        plan: ResourcePlan,
        now_wall: float,
        foreground_pid: int | None,
        profile: ResourceProfile,
        spared_pids: frozenset[int] = frozenset(),
        own_username: str | None = None,
    ) -> ProcessCandidate | None:
        pid = int(info["pid"])
        name = (info.get("name") or "").lower()
        if pid <= 4 or name in PROTECTED_NAMES:
            return None
        is_foreground = pid == foreground_pid
        if profile.protect_foreground and is_foreground:
            return None
        if not plan.allow_foreground_trim and is_foreground:
            return None
        username = (info.get("username") or "").lower()
        if username in PROTECTED_USERS:
            return None
        exe = (info.get("exe") or "").lower()
        if exe.startswith(WINDOWS_DIR):
            return None
        memory_info = info.get("memory_info")
        if memory_info is None:
            return None
        rss_gb = float(memory_info.rss) / GB
        age_seconds = self._get_age_seconds(info.get("create_time"), now_wall)
        is_spared = pid in spared_pids or is_foreground

        # Kill eligibility: not spared, not protected, owned by current user.
        # Same-user check matters because PROCESS_TERMINATE on cross-user PIDs
        # is access-denied and would just produce noise.
        same_user = bool(own_username and username and username == own_username.lower())
        old_enough = (
            age_seconds is None
            or age_seconds >= profile.new_process_grace_seconds
            or plan.aggressive
        )
        kill_eligible = (
            profile.enable_kill and not is_spared and same_user and old_enough
        )

        if (
            age_seconds is not None
            and age_seconds < profile.new_process_grace_seconds
            and not plan.aggressive
        ):
            return None
        cpu_percent = self._effective_cpu_percent(telemetry, age_seconds)
        disk_gb_s = self._effective_disk_gb_s(telemetry, age_seconds)
        other_gb_s = self._effective_other_gb_s(telemetry, age_seconds)
        max_activity = max(cpu_percent, disk_gb_s, other_gb_s)
        if rss_gb < plan.trim_threshold_gb and max_activity <= 0.0 and not kill_eligible:
            return None
        uss_gb = self._get_uss_gb(proc)
        estimated = self._estimate_reclaimable_gb(rss_gb, uss_gb, plan.trim_threshold_gb)
        tags = () if is_spared else self._hot_tags(
            proc, cpu_percent, disk_gb_s, other_gb_s, plan, is_foreground,
        )
        if (
            estimated < (MIN_ESTIMATED_RECLAIM_MB / 1024.0)
            and rss_gb < plan.trim_threshold_gb
            and not tags
            and not kill_eligible
        ):
            return None
        reclaim_score = estimated * self._coldness_score(
            cpu_percent, disk_gb_s, other_gb_s, info.get("status"), age_seconds,
        )
        if is_foreground:
            reclaim_score *= 0.60
        return ProcessCandidate(
            pid=pid,
            name=name,
            rss_gb=rss_gb,
            uss_gb=uss_gb,
            cpu_percent=cpu_percent,
            disk_gb_s=disk_gb_s,
            other_gb_s=other_gb_s,
            age_seconds=age_seconds,
            estimated_reclaim_gb=estimated,
            reclaim_score=reclaim_score,
            throttle_score=self._throttle_score(cpu_percent, disk_gb_s, other_gb_s, tags),
            throttle_tags=tags,
            is_spared=is_spared,
            kill_eligible=kill_eligible,
        )

    def select_trim_targets(self, candidates: list[ProcessCandidate], plan: ResourcePlan) -> list[ProcessCandidate]:
        selected: list[ProcessCandidate] = []
        estimated_total = 0.0
        for candidate in sorted(candidates, key=lambda item: item.reclaim_score, reverse=True):
            selected.append(candidate)
            estimated_total += candidate.estimated_reclaim_gb
            if len(selected) >= plan.max_trimmed_processes:
                break
            if estimated_total >= plan.reclaim_target_gb * (1.30 if plan.aggressive else 1.15):
                break
        return selected

    def select_throttle_targets(self, candidates: list[ProcessCandidate], plan: ResourcePlan) -> list[ProcessCandidate]:
        hot = [item for item in candidates if item.throttle_tags]
        return sorted(hot, key=lambda item: item.throttle_score, reverse=True)[:plan.max_throttled_processes]

    def _hot_tags(
        self,
        proc: psutil.Process,
        cpu_percent: float,
        disk_gb_s: float,
        other_gb_s: float,
        plan: ResourcePlan,
        is_foreground: bool,
    ) -> tuple[str, ...]:
        if is_foreground:
            return ()
        tags: list[str] = []
        if plan.cpu_pressure and cpu_percent >= HOT_CPU_PERCENT:
            tags.append("cpu")
        if plan.disk_pressure and disk_gb_s >= (HOT_DISK_MB_S / 1024.0):
            tags.append("disk")
        if plan.network_pressure and other_gb_s >= (HOT_OTHER_MB_S / 1024.0) and self._has_connections(proc):
            tags.append("network")
        return tuple(tags)

    def _has_connections(self, proc: psutil.Process) -> bool:
        try:
            return any(conn.status == psutil.CONN_ESTABLISHED for conn in proc.net_connections(kind="inet"))
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            return False

    def _get_age_seconds(self, create_time: float | None, now_wall: float) -> float | None:
        return None if create_time is None else max(now_wall - float(create_time), 0.0)

    def _get_uss_gb(self, proc: psutil.Process) -> float | None:
        try:
            full_info = proc.memory_full_info()
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            return None
        uss = getattr(full_info, "uss", None)
        return None if uss is None else float(uss) / GB

    def _effective_cpu_percent(self, telemetry: ProcessTelemetry, age_seconds: float | None) -> float:
        if telemetry.cpu_percent is not None:
            return telemetry.cpu_percent
        cpus = max(psutil.cpu_count() or 1, 1)
        return 0.0 if not age_seconds else (telemetry.total_cpu_time / age_seconds / cpus) * 100.0

    def _effective_disk_gb_s(self, telemetry: ProcessTelemetry, age_seconds: float | None) -> float:
        total = telemetry.read_bytes + telemetry.write_bytes
        if telemetry.disk_gb_s > 0.0:
            return telemetry.disk_gb_s
        return 0.0 if not age_seconds else float(total) / age_seconds / GB

    def _effective_other_gb_s(self, telemetry: ProcessTelemetry, age_seconds: float | None) -> float:
        if telemetry.other_gb_s > 0.0:
            return telemetry.other_gb_s
        return 0.0 if not age_seconds else float(telemetry.other_bytes) / age_seconds / GB

    def _estimate_reclaimable_gb(self, rss_gb: float, uss_gb: float | None, trim_threshold_gb: float) -> float:
        private_gb = uss_gb if uss_gb is not None and uss_gb > 0.0 else (rss_gb * 0.50)
        excess_gb = max(rss_gb - (trim_threshold_gb * 0.65), 0.0)
        estimate = min(rss_gb * 0.55, private_gb * 0.75)
        return max(estimate, min(excess_gb, rss_gb * 0.45)) if excess_gb else max(estimate, 0.0)

    def _coldness_score(
        self,
        cpu_percent: float,
        disk_gb_s: float,
        other_gb_s: float,
        status: str | None,
        age_seconds: float | None,
    ) -> float:
        score = TOP_STATUS_BONUS.get(status, 1.0) if status is not None else 1.0
        if age_seconds is not None:
            score *= 0.80 if age_seconds < 300.0 else (1.05 if age_seconds > 3600.0 else 1.0)
        score *= 1.35 if cpu_percent <= 1.0 else (1.0 if cpu_percent <= WARM_CPU_PERCENT else 0.35)
        score *= 1.15 if disk_gb_s <= (1.0 / 1024.0) else (1.0 if disk_gb_s <= (WARM_DISK_MB_S / 1024.0) else 0.40)
        score *= 1.10 if other_gb_s <= (0.5 / 1024.0) else (1.0 if other_gb_s <= (WARM_OTHER_MB_S / 1024.0) else 0.45)
        return max(score, 0.10)

    def _throttle_score(
        self,
        cpu_percent: float,
        disk_gb_s: float,
        other_gb_s: float,
        tags: tuple[str, ...],
    ) -> float:
        return 0.0 if not tags else (
            (cpu_percent * 1.4 if "cpu" in tags else 0.0)
            + ((disk_gb_s * 1024.0) * 6.0 if "disk" in tags else 0.0)
            + ((other_gb_s * 1024.0) * 8.0 if "network" in tags else 0.0)
        )
