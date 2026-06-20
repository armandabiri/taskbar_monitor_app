"""Opt-in watchdog that fires a cleanup when RAM stays under pressure.

The taskbar monitor already samples system RAM on its stats timer (~2.5s). This
watchdog observes those samples and, when memory *used* stays at/above a
configurable threshold for a debounce window, triggers a forced Smart cleanup —
then respects a cooldown so it never loops. Disabled by default.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from PyQt6.QtCore import QSettings

_GROUP = "resource_control/auto_clean"
_KEY_ENABLED = f"{_GROUP}/enabled"
_KEY_THRESHOLD = f"{_GROUP}/threshold_percent"
_KEY_DEBOUNCE = f"{_GROUP}/debounce_seconds"
_KEY_COOLDOWN = f"{_GROUP}/cooldown_seconds"


@dataclass(frozen=True)
class AutoCleanConfig:
    """User-tunable auto-clean behaviour."""

    enabled: bool = False
    # Fire when memory *used* percent is at or above this value.
    threshold_percent: float = 85.0
    # Memory must stay above the threshold this long before firing.
    debounce_seconds: float = 30.0
    # Minimum gap between two auto-clean fires.
    cooldown_seconds: float = 300.0


class AutoCleanWatchdog:
    """Decides when to auto-fire a cleanup from a stream of RAM samples."""

    def __init__(
        self,
        config: AutoCleanConfig,
        on_fire: Callable[[], None],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._on_fire = on_fire
        self._clock = clock
        self._above_since: float | None = None
        self._last_fire: float | None = None

    def update_config(self, config: AutoCleanConfig) -> None:
        self._config = config
        if not config.enabled:
            self._above_since = None

    def observe(self, ram_used_percent: float, now: float | None = None) -> bool:
        """Feed one RAM-used sample. Returns True if a cleanup was fired."""
        config = self._config
        if not config.enabled:
            self._above_since = None
            return False

        moment = self._clock() if now is None else now
        if ram_used_percent < config.threshold_percent:
            self._above_since = None
            return False

        if self._above_since is None:
            self._above_since = moment
        sustained = (moment - self._above_since) >= config.debounce_seconds
        cooled = self._last_fire is None or (moment - self._last_fire) >= config.cooldown_seconds
        if sustained and cooled:
            self._last_fire = moment
            self._above_since = None
            self._on_fire()
            return True
        return False


def load_auto_clean_config(settings: QSettings) -> AutoCleanConfig:
    """Read the auto-clean configuration from QSettings (defaults if absent)."""
    default = AutoCleanConfig()
    return AutoCleanConfig(
        enabled=_as_bool(settings.value(_KEY_ENABLED), default.enabled),
        threshold_percent=_as_float(settings.value(_KEY_THRESHOLD), default.threshold_percent),
        debounce_seconds=_as_float(settings.value(_KEY_DEBOUNCE), default.debounce_seconds),
        cooldown_seconds=_as_float(settings.value(_KEY_COOLDOWN), default.cooldown_seconds),
    )


def save_auto_clean_config(settings: QSettings, config: AutoCleanConfig) -> None:
    """Persist the auto-clean configuration to QSettings."""
    settings.setValue(_KEY_ENABLED, config.enabled)
    settings.setValue(_KEY_THRESHOLD, config.threshold_percent)
    settings.setValue(_KEY_DEBOUNCE, config.debounce_seconds)
    settings.setValue(_KEY_COOLDOWN, config.cooldown_seconds)
    settings.sync()


def _as_bool(raw: object, default: bool) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _as_float(raw: object, default: float) -> float:
    if raw is None:
        return default
    try:
        return float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
