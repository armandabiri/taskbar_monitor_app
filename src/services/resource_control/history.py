"""Persistent cleanup-history helpers."""

from __future__ import annotations

import json
import os
from dataclasses import asdict

from core.config import DEFAULT_CLEANUP_HISTORY_RETENTION, cleanup_history_path
from services.resource_control.models import CleanupHistoryEntry, ReleaseResult


def build_history_entry(result: ReleaseResult) -> CleanupHistoryEntry:
    """Convert a cleanup result into a persisted history record."""

    return CleanupHistoryEntry(
        run_id=result.run_id,
        timestamp=result.started_at,
        mode=result.mode,
        profile_name=result.profile_name,
        snapshot_name=result.snapshot_name,
        processes_cleaned_total=result.processes_cleaned_total,
        processes_trimmed=result.processes_trimmed,
        processes_killed=result.processes_killed,
        processes_throttled=result.processes_throttled,
        kill_candidates_found=result.kill_candidates_found,
        snapshot_extras_found=result.snapshot_extras_found,
        snapshot_extras_selected=result.snapshot_extras_selected,
        blocked_reason_counts=dict(result.blocked_reason_counts),
        errors=list(result.errors),
        summary=result.summary,
    )


def append_history(result: ReleaseResult, *, retention: int = DEFAULT_CLEANUP_HISTORY_RETENTION) -> None:
    """Append one result to the history file and trim old entries."""

    path = cleanup_history_path()
    entry = build_history_entry(result)
    lines = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            lines = [line.rstrip("\n") for line in fh if line.strip()]
    lines.append(json.dumps(asdict(entry), ensure_ascii=True))
    if retention > 0:
        lines = lines[-retention:]
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for line in lines:
            fh.write(line + "\n")


def read_history(*, limit: int | None = None) -> list[CleanupHistoryEntry]:
    """Read persisted cleanup history, newest first."""

    path = cleanup_history_path()
    if not os.path.exists(path):
        return []
    entries: list[CleanupHistoryEntry] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            raw = json.loads(line)
            entries.append(CleanupHistoryEntry(**raw))
    entries.reverse()
    return entries[:limit] if limit is not None else entries
