"""Resource-control profiles: tunable presets the user can switch between."""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import Iterable

from PyQt6.QtCore import QSettings

# QSettings keys
SETTINGS_GROUP = "resource_control"
KEY_ACTIVE_SMART = "active_smart_profile"
KEY_ACTIVE_AGGRESSIVE = "active_aggressive_profile"
KEY_CUSTOM_PREFIX = "custom_profile/"

# Standby-cache flush policy values
FLUSH_NEVER = "never"
FLUSH_CRITICAL_ONLY = "critical_only"
FLUSH_ALWAYS = "always"
FLUSH_MODES = (FLUSH_NEVER, FLUSH_CRITICAL_ONLY, FLUSH_ALWAYS)


@dataclass(frozen=True)
class ResourceProfile:
    """All tunable knobs that drive a resource-release run.

    Default values match the legacy "smart" mode so existing behaviour is
    preserved when no profile is supplied. Presets below override fields.
    """

    name: str = "Balanced"

    # Drives several legacy code paths (foreground trim, override cooldowns,
    # bigger reclaim caps, etc). Keep this for backward compatibility.
    aggressive: bool = False

    # Memory pressure threshold (percent used) before heavy actions kick in.
    # Below this, only the lightest tier of work runs.
    pressure_threshold_percent: float = 80.0

    # Working-set trim
    enable_trim: bool = True
    trim_threshold_mb: float = 200.0
    max_trim_per_run: int = 4
    trim_cooldown_seconds: float = 180.0
    min_reclaim_mb: float = 128.0
    max_reclaim_mb: float = 3072.0

    # Standby cache flush — biggest contributor to disk R/W spikes.
    flush_standby: str = FLUSH_CRITICAL_ONLY

    # Process throttling (priority, IO priority, affinity)
    enable_throttle: bool = True
    max_throttle_per_run: int = 2
    throttle_cooldown_seconds: float = 240.0

    # Process protection
    protect_foreground: bool = True
    new_process_grace_seconds: float = 90.0

    # Whether to run gc.collect() on the host process
    run_gc: bool = True

    # Termination — Nuclear-tier; off by default everywhere else.
    # When enable_kill is True, candidates that aren't spared (visible window,
    # tray icon, protected name/user, system dir) get terminated to free
    # their entire working set, not just trim it.
    enable_kill: bool = False
    always_spare_names: str = ""
    spare_visible_windows: bool = True
    spare_tray_icons: bool = True
    confirm_before_kill: bool = True

    # System-wide reclaim (require admin / SeProfileSingleProcessPrivilege).
    # Silently no-op when the privilege is unavailable.
    empty_all_working_sets: bool = False
    flush_modified_pages: bool = False

    def with_overrides(self, **overrides) -> "ResourceProfile":
        """Return a copy with the given fields replaced."""
        return replace(self, **overrides)

    def keep_list_entries(self) -> tuple[str, ...]:
        """Return normalized app names that must always be spared."""
        entries = [item.strip().lower() for item in self.always_spare_names.split(",")]
        return tuple(item for item in entries if item)


# ---------------------------------------------------------------------------
# Built-in presets
# ---------------------------------------------------------------------------

GENTLE = ResourceProfile(
    name="Gentle",
    aggressive=False,
    pressure_threshold_percent=88.0,
    enable_trim=True,
    trim_threshold_mb=400.0,
    max_trim_per_run=2,
    trim_cooldown_seconds=600.0,
    min_reclaim_mb=64.0,
    max_reclaim_mb=1024.0,
    flush_standby=FLUSH_NEVER,
    enable_throttle=False,
    max_throttle_per_run=0,
    throttle_cooldown_seconds=600.0,
    protect_foreground=True,
    new_process_grace_seconds=180.0,
    run_gc=True,
)

BALANCED = ResourceProfile(
    name="Balanced",
    aggressive=False,
    pressure_threshold_percent=80.0,
    enable_trim=True,
    trim_threshold_mb=200.0,
    max_trim_per_run=4,
    trim_cooldown_seconds=180.0,
    min_reclaim_mb=128.0,
    max_reclaim_mb=3072.0,
    flush_standby=FLUSH_CRITICAL_ONLY,
    enable_throttle=True,
    max_throttle_per_run=2,
    throttle_cooldown_seconds=240.0,
    protect_foreground=True,
    new_process_grace_seconds=90.0,
    run_gc=True,
)

AGGRESSIVE = ResourceProfile(
    name="Aggressive",
    aggressive=True,
    pressure_threshold_percent=70.0,
    enable_trim=True,
    trim_threshold_mb=150.0,
    max_trim_per_run=6,
    trim_cooldown_seconds=60.0,
    min_reclaim_mb=384.0,
    max_reclaim_mb=6144.0,
    flush_standby=FLUSH_CRITICAL_ONLY,
    enable_throttle=True,
    max_throttle_per_run=3,
    throttle_cooldown_seconds=90.0,
    protect_foreground=False,
    new_process_grace_seconds=30.0,
    run_gc=True,
)

NUCLEAR = ResourceProfile(
    name="Nuclear",
    aggressive=True,
    pressure_threshold_percent=60.0,
    enable_trim=True,
    trim_threshold_mb=100.0,
    max_trim_per_run=10,
    trim_cooldown_seconds=30.0,
    min_reclaim_mb=512.0,
    max_reclaim_mb=8192.0,
    flush_standby=FLUSH_ALWAYS,
    enable_throttle=True,
    max_throttle_per_run=5,
    throttle_cooldown_seconds=45.0,
    protect_foreground=True,
    new_process_grace_seconds=15.0,
    run_gc=True,
    enable_kill=True,
    spare_visible_windows=True,
    spare_tray_icons=True,
    confirm_before_kill=True,
    empty_all_working_sets=True,
    flush_modified_pages=True,
)

BUILTIN_PRESETS: tuple[ResourceProfile, ...] = (GENTLE, BALANCED, AGGRESSIVE, NUCLEAR)
BUILTIN_NAMES = frozenset(p.name for p in BUILTIN_PRESETS)
DEFAULT_SMART_NAME = BALANCED.name
DEFAULT_AGGRESSIVE_NAME = AGGRESSIVE.name


def get_preset(name: str) -> ResourceProfile | None:
    for preset in BUILTIN_PRESETS:
        if preset.name == name:
            return preset
    return None


def all_profiles(settings: QSettings) -> list[ResourceProfile]:
    """All available profiles: built-ins first, then any user-customised ones."""
    customs = load_custom_profiles(settings)
    custom_by_name = {p.name: p for p in customs}
    # Customs with the same name as a builtin override the builtin
    return [
        custom_by_name.get(p.name, p) for p in BUILTIN_PRESETS
    ] + [p for p in customs if p.name not in BUILTIN_NAMES]


def load_profile(settings: QSettings, name: str) -> ResourceProfile:
    """Resolve a profile by name, preferring customised versions."""
    custom = _load_custom_profile(settings, name)
    if custom is not None:
        return custom
    preset = get_preset(name)
    if preset is not None:
        return preset
    return BALANCED


def load_active_smart_profile(settings: QSettings) -> ResourceProfile:
    name = settings.value(f"{SETTINGS_GROUP}/{KEY_ACTIVE_SMART}", DEFAULT_SMART_NAME)
    return load_profile(settings, str(name))


def load_active_aggressive_profile(settings: QSettings) -> ResourceProfile:
    name = settings.value(
        f"{SETTINGS_GROUP}/{KEY_ACTIVE_AGGRESSIVE}", DEFAULT_AGGRESSIVE_NAME
    )
    return load_profile(settings, str(name))


def set_active_smart_profile(settings: QSettings, name: str) -> None:
    settings.setValue(f"{SETTINGS_GROUP}/{KEY_ACTIVE_SMART}", name)
    settings.sync()


def set_active_aggressive_profile(settings: QSettings, name: str) -> None:
    settings.setValue(f"{SETTINGS_GROUP}/{KEY_ACTIVE_AGGRESSIVE}", name)
    settings.sync()


def save_custom_profile(settings: QSettings, profile: ResourceProfile) -> None:
    """Persist a customised profile under its name."""
    base_key = f"{SETTINGS_GROUP}/{KEY_CUSTOM_PREFIX}{profile.name}"
    for f in fields(profile):
        if f.name == "name":
            continue
        settings.setValue(f"{base_key}/{f.name}", getattr(profile, f.name))
    settings.sync()


def reset_custom_profile(settings: QSettings, name: str) -> None:
    """Drop a customisation so the built-in defaults apply again."""
    settings.beginGroup(f"{SETTINGS_GROUP}/{KEY_CUSTOM_PREFIX}{name}")
    settings.remove("")
    settings.endGroup()
    settings.sync()


def load_custom_profiles(settings: QSettings) -> list[ResourceProfile]:
    settings.beginGroup(f"{SETTINGS_GROUP}/{KEY_CUSTOM_PREFIX[:-1]}")
    names = list(settings.childGroups())
    settings.endGroup()
    return [p for p in (_load_custom_profile(settings, n) for n in names) if p is not None]


def _load_custom_profile(settings: QSettings, name: str) -> ResourceProfile | None:
    base_key = f"{SETTINGS_GROUP}/{KEY_CUSTOM_PREFIX}{name}"
    settings.beginGroup(base_key)
    keys = set(settings.childKeys())
    settings.endGroup()
    if not keys:
        return None
    base = get_preset(name) or BALANCED
    overrides: dict = {}
    for f in fields(base):
        if f.name == "name" or f.name not in keys:
            continue
        raw = settings.value(f"{base_key}/{f.name}")
        coerced = _coerce(raw, f.type, getattr(base, f.name))
        if coerced is not None:
            overrides[f.name] = coerced
    if not overrides:
        return base.with_overrides(name=name)
    return base.with_overrides(name=name, **overrides)


def _coerce(raw, annotation, default):
    if raw is None:
        return None
    target = _resolve_type(annotation, default)
    try:
        if target is bool:
            if isinstance(raw, bool):
                return raw
            return str(raw).strip().lower() in {"1", "true", "yes", "on"}
        if target is int:
            return int(raw)
        if target is float:
            return float(raw)
        if target is str:
            return str(raw)
    except (TypeError, ValueError):
        return default
    return raw


def _resolve_type(annotation, default):
    if isinstance(annotation, type):
        return annotation
    return type(default)


def list_active_names(profiles: Iterable[ResourceProfile]) -> list[str]:
    return [p.name for p in profiles]
