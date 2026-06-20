"""Skip-reason enum and user-facing labels for cleanup decisions."""

from __future__ import annotations

from enum import Enum


class SkipReason(str, Enum):
    """Why a process was not acted on."""

    OWN_PROCESS = "own_process"
    PROTECTED_NAME = "protected_name"
    KEEP_LIST = "keep_list"
    PROTECTED_USER = "protected_user"
    WINDOWS_BINARY = "windows_binary"
    FOREGROUND_PROCESS = "foreground_process"
    VISIBLE_WINDOW = "visible_window"
    TRAY_ICON = "tray_icon"
    NEW_PROCESS_GRACE = "new_process_grace"
    DIFFERENT_USER = "different_user"
    RECENTLY_TRIMMED = "recently_trimmed"
    RECENTLY_THROTTLED = "recently_throttled"
    BELOW_TRIM_THRESHOLD = "below_trim_threshold"
    NO_RECLAIM_VALUE = "no_reclaim_value"
    SNAPSHOT_BASELINE_MATCH = "snapshot_baseline_match"
    SNAPSHOT_NOT_SELECTED = "snapshot_not_selected"
    SNAPSHOT_NOT_EXTRA = "snapshot_not_extra"
    BELOW_PRESSURE_THRESHOLD = "below_pressure_threshold"
    ACCESS_DENIED = "access_denied"
    EXECUTION_FAILED = "execution_failed"


_SKIP_REASON_LABELS: dict[SkipReason, str] = {
    SkipReason.OWN_PROCESS: "own process",
    SkipReason.PROTECTED_NAME: "protected name",
    SkipReason.KEEP_LIST: "user keep-list",
    SkipReason.PROTECTED_USER: "protected user",
    SkipReason.WINDOWS_BINARY: "Windows binary",
    SkipReason.FOREGROUND_PROCESS: "foreground process",
    SkipReason.VISIBLE_WINDOW: "visible-window protection",
    SkipReason.TRAY_ICON: "tray-icon protection",
    SkipReason.NEW_PROCESS_GRACE: "new-process grace period",
    SkipReason.DIFFERENT_USER: "different user",
    SkipReason.RECENTLY_TRIMMED: "recently trimmed",
    SkipReason.RECENTLY_THROTTLED: "recently throttled",
    SkipReason.BELOW_TRIM_THRESHOLD: "below trim threshold",
    SkipReason.NO_RECLAIM_VALUE: "no reclaim value",
    SkipReason.SNAPSHOT_BASELINE_MATCH: "snapshot baseline match",
    SkipReason.SNAPSHOT_NOT_SELECTED: "snapshot extra not selected",
    SkipReason.SNAPSHOT_NOT_EXTRA: "not an extra process",
    SkipReason.BELOW_PRESSURE_THRESHOLD: "below pressure threshold",
    SkipReason.ACCESS_DENIED: "access denied",
    SkipReason.EXECUTION_FAILED: "execution failed",
}


def format_skip_reason(reason: SkipReason | str) -> str:
    """Return a user-facing label for a skip reason."""

    if isinstance(reason, SkipReason):
        return _SKIP_REASON_LABELS.get(reason, reason.value.replace("_", " "))
    try:
        enum_reason = SkipReason(reason)
    except ValueError:
        return str(reason).replace("_", " ")
    return _SKIP_REASON_LABELS.get(enum_reason, enum_reason.value.replace("_", " "))
