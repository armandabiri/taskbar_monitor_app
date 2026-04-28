"""Snapshot-to-live process diff helpers."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import logging
from typing import Iterable

import psutil

from services.process_snapshot import (
    ProcessSnapshot,
    build_entry_identity,
    build_live_process_identity,
)
from services.resource_control.models import SkipReason
from services.resource_control.windows_ops import WindowsProcessOperator

LOGGER = logging.getLogger(__name__)


@dataclass
class LiveSnapshotExtra:
    """A live process that was not present in the snapshot baseline."""

    pid: int
    name: str
    exe: str
    username: str
    cmdline: str
    rss_gb: float
    create_time: float
    has_visible_window: bool
    has_tray_icon: bool
    default_selected: bool
    default_block_reason: SkipReason | None = None


@dataclass
class SnapshotLiveDiff:
    """Exact live diff versus a snapshot baseline."""

    snapshot_name: str
    extra_processes: list[LiveSnapshotExtra] = field(default_factory=list)
    matched_count: int = 0
    missing_snapshot_count: int = 0
    identity_collisions: int = 0


def diff_snapshot_to_live(
    snapshot: ProcessSnapshot,
    live_processes: Iterable[psutil.Process] | None = None,
    *,
    visible_window_pids: set[int] | None = None,
    tray_icon_pids: set[int] | None = None,
    operator: WindowsProcessOperator | None = None,
) -> SnapshotLiveDiff:
    """Return an exact multiset diff between a snapshot and live processes."""

    operator = operator or WindowsProcessOperator()
    if visible_window_pids is None:
        try:
            visible_window_pids = operator.enumerate_visible_window_pids()
        except OSError as exc:
            LOGGER.warning("enumerate_visible_window_pids failed: %s", exc)
            visible_window_pids = set()
    if tray_icon_pids is None:
        try:
            tray_icon_pids = operator.enumerate_tray_icon_pids()
        except OSError as exc:
            LOGGER.warning("enumerate_tray_icon_pids failed: %s", exc)
            tray_icon_pids = set()

    baseline = Counter(build_entry_identity(entry) for entry in snapshot.entries)
    live_list = list(
        live_processes
        if live_processes is not None
        else psutil.process_iter(
            ["pid", "name", "exe", "username", "cmdline", "memory_info", "create_time"],
            ad_value=None,
        )
    )
    identity_counts: Counter[tuple[str, str, str, str]] = Counter()
    extras: list[LiveSnapshotExtra] = []
    matched_count = 0

    for proc in live_list:
        try:
            identity = build_live_process_identity(proc)
            identity_counts[identity] += 1
            remaining = baseline.get(identity, 0)
            if remaining > 0:
                baseline[identity] = remaining - 1
                matched_count += 1
                continue
            info = getattr(proc, "info", None) or {}
            memory_info = info.get("memory_info")
            rss_bytes = getattr(memory_info, "rss", 0) if memory_info is not None else 0
            cmdline = info.get("cmdline")
            if isinstance(cmdline, list):
                cmdline = " ".join(str(part) for part in cmdline)
            visible = proc.pid in visible_window_pids
            tray = proc.pid in tray_icon_pids
            default_reason = (
                SkipReason.VISIBLE_WINDOW if visible else (
                    SkipReason.TRAY_ICON if tray else None
                )
            )
            extras.append(
                LiveSnapshotExtra(
                    pid=proc.pid,
                    name=str(info.get("name") or ""),
                    exe=str(info.get("exe") or ""),
                    username=str(info.get("username") or ""),
                    cmdline=str(cmdline or ""),
                    rss_gb=float(rss_bytes) / (1024 * 1024 * 1024),
                    create_time=float(info.get("create_time") or 0.0),
                    has_visible_window=visible,
                    has_tray_icon=tray,
                    default_selected=not (visible or tray),
                    default_block_reason=default_reason,
                )
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    extras.sort(key=lambda item: (-item.create_time, -item.rss_gb, item.name.lower()))
    collisions = sum(max(count - 1, 0) for count in identity_counts.values())
    return SnapshotLiveDiff(
        snapshot_name=snapshot.name,
        extra_processes=extras,
        matched_count=matched_count,
        missing_snapshot_count=sum(baseline.values()),
        identity_collisions=collisions,
    )
