"""Human-facing rendering of a :class:`ReleaseResult`.

Kept separate from the data model so ``models.py`` stays a thin dataclass
module and the (sizable) summary/detail/plain-language text lives in one place.
All functions take a result-like object and read its attributes; they never
mutate it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from services.resource_control.skip_reasons import SkipReason

if TYPE_CHECKING:  # pragma: no cover - typing only
    from services.resource_control.models import ReleaseResult

_SNAPSHOT_MODE = "snapshot_extras"


def render_summary(result: "ReleaseResult") -> str:
    """One-line headline describing what the run accomplished."""
    if result.mode == _SNAPSHOT_MODE:
        if result.processes_cleaned_total == 0:
            dominant = result.dominant_skip_reason
            base = (
                f"No processes cleaned. Snapshot extras: {result.snapshot_extras_found} found, "
                f"{result.snapshot_extras_selected} selected."
            )
            if dominant is not None:
                return f"{base[:-1]}. Blocked mostly by {dominant[0]} ({dominant[1]})."
            return base
        return (
            f"Snapshot extras: {result.snapshot_extras_found} found, "
            f"{result.snapshot_extras_selected} selected, {result.processes_killed} killed"
        )

    base = (
        f"Cleaned {result.processes_cleaned_total} process(es): "
        f"{result.processes_trimmed} trimmed, {result.processes_killed} killed, "
        f"{result.processes_throttled} throttled"
    )
    if result.processes_cleaned_total == 0:
        dominant = result.dominant_skip_reason
        if dominant is not None:
            return f"{base}. Blocked mostly by {dominant[0]} ({dominant[1]})."
        return f"{base}. No eligible process actions were executed."
    return base


def render_details(result: "ReleaseResult") -> str:
    """Multi-line diagnostic block shown in the result dialog and the log."""
    lines = [result.summary]
    if result.profile_name:
        lines.append(f"Profile: {result.profile_name}")
    if result.snapshot_name:
        lines.append(f"Snapshot: {result.snapshot_name}")
    if result.was_forced:
        lines.append("Forced: pressure threshold was bypassed for this run.")
    lines.append(
        f"Mode: {result.mode} | Pressure: {result.pressure_level} | "
        f"Target ~{result.reclaim_target_gb:.2f} GB | Candidates {result.candidates_considered}"
    )
    if result.memory_before_gb is not None or result.memory_after_gb is not None:
        before = "?" if result.memory_before_gb is None else f"{result.memory_before_gb:.2f} GB"
        after = "?" if result.memory_after_gb is None else f"{result.memory_after_gb:.2f} GB"
        line = f"Available RAM: {before} -> {after} | Estimated freed ~{result.ram_freed_gb:.2f} GB"
        if result.system_freed_gb is not None:
            line += f" | Measured system delta ~{result.system_freed_gb:+.2f} GB"
        lines.append(line)
    if result.mode == _SNAPSHOT_MODE:
        lines.append(
            f"Snapshot live diff: matched {result.snapshot_matched_count}, "
            f"extras {result.snapshot_extras_found}, selected {result.snapshot_extras_selected}, "
            f"identity collisions {result.snapshot_identity_collisions}"
        )
    else:
        lines.append(
            f"Kill candidates {result.kill_candidates_found} | "
            f"Throttle CPU {result.cpu_throttled} | Disk {result.disk_throttled} | "
            f"Net {result.network_throttled}"
        )
    if result.top_block_reasons():
        formatted = ", ".join(f"{label} ({count})" for label, count in result.top_block_reasons())
        lines.append(f"Top block reasons: {formatted}")
    if result.trimmed_process_names:
        lines.append(f"Trimmed: {', '.join(sorted(set(result.trimmed_process_names))[:10])}")
    if result.killed_process_names:
        lines.append(f"Killed: {', '.join(sorted(set(result.killed_process_names))[:10])}")
    if result.throttled_process_names:
        lines.append(f"Throttled: {', '.join(sorted(set(result.throttled_process_names))[:10])}")
    if result.notes:
        lines.append(f"Notes: {' | '.join(result.notes[:5])}")
    if result.errors:
        lines.append(f"Issues: {' | '.join(result.errors[:5])}")
    return "\n".join(lines)


def render_plain_reason(result: "ReleaseResult") -> str:
    """A single plain-language sentence the user can act on.

    Designed for the common confusing case: a run that cleaned nothing. It
    translates the dominant skip reason into everyday words and, where useful,
    hints at the remedy (forcing a full pass).
    """
    if result.processes_cleaned_total > 0:
        freed = result.system_freed_gb
        if freed is None:
            freed = result.ram_freed_gb
        return (
            f"Cleaned {result.processes_cleaned_total} process(es) and freed about "
            f"{max(freed, 0.0):.2f} GB."
        )

    below_pressure = result.blocked_reason_counts.get(SkipReason.BELOW_PRESSURE_THRESHOLD.value, 0)
    if below_pressure and not result.was_forced:
        return (
            "Nothing needed cleaning: system memory is below the profile's pressure "
            "threshold, so only a light pass ran. Use Force a full pass to reclaim anyway."
        )

    if result.mode == _SNAPSHOT_MODE and result.snapshot_extras_found == 0:
        return "No extra processes appeared since this snapshot, so there was nothing to clean."

    dominant = result.dominant_skip_reason
    if dominant is not None:
        suffix = "" if result.was_forced else " You can still try Force a full pass."
        return (
            f"Nothing was cleaned — most candidates were skipped by {dominant[0]} "
            f"({dominant[1]})." + suffix
        )

    if result.was_forced:
        return "Forced run found no eligible processes to clean."
    return "No eligible processes were found to clean."
