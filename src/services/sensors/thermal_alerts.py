"""Per-sensor thermal threshold alerting.

Evaluates a ``SensorReading`` against per-sensor Celsius thresholds and fires a
debounced, cooldown-limited callback (a toast in the app) when a sensor stays
above its threshold. The Crucial T700 throttles at 80°C, so the SSD default is
80; other defaults are conservative.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from services.sensors.models import SensorKind, SensorReading

LOGGER = logging.getLogger(__name__)

DEFAULT_THRESHOLDS_C: dict[SensorKind, int] = {
    SensorKind.CPU_TEMP: 95,
    SensorKind.RAM_TEMP: 70,
    SensorKind.GPU_TEMP: 90,
    SensorKind.SSD_TEMP: 80,
}

_LABELS: dict[SensorKind, str] = {
    SensorKind.CPU_TEMP: "CPU",
    SensorKind.RAM_TEMP: "RAM",
    SensorKind.GPU_TEMP: "GPU",
    SensorKind.SSD_TEMP: "SSD",
}

# A sensor must stay above threshold for this long before alerting, and a fired
# alert is silenced for the cooldown so a borderline sensor does not spam toasts.
DEBOUNCE_SECONDS = 5.0
COOLDOWN_SECONDS = 300.0


@dataclass
class _SensorState:
    over_since: float | None = None
    last_alert_at: float | None = None


class ThermalAlerts:
    """Stateful threshold evaluator. One instance per running monitor."""

    def __init__(
        self,
        notify: Callable[[str, str], None],
        *,
        clock: Callable[[], float] = time.monotonic,
        debounce: float = DEBOUNCE_SECONDS,
        cooldown: float = COOLDOWN_SECONDS,
    ) -> None:
        self._notify = notify
        self._clock = clock
        self._debounce = debounce
        self._cooldown = cooldown
        self._state: dict[SensorKind, _SensorState] = {
            kind: _SensorState() for kind in DEFAULT_THRESHOLDS_C
        }

    def evaluate(
        self,
        reading: SensorReading,
        thresholds: dict[SensorKind, int],
        *,
        enabled: bool = True,
    ) -> list[SensorKind]:
        """Return the sensors that fired an alert this call (also calls notify)."""
        if not enabled:
            for state in self._state.values():
                state.over_since = None
            return []
        now = self._clock()
        fired: list[SensorKind] = []
        for kind, state in self._state.items():
            limit = thresholds.get(kind, DEFAULT_THRESHOLDS_C[kind])
            value = reading.value(kind)
            if value is None or value < limit:
                state.over_since = None
                continue
            if state.over_since is None:
                state.over_since = now
            if now - state.over_since < self._debounce:
                continue
            if state.last_alert_at is not None and now - state.last_alert_at < self._cooldown:
                continue
            state.last_alert_at = now
            fired.append(kind)
            label = _LABELS[kind]
            self._notify(
                "Temperature alert",
                f"{label} temperature {int(round(value))}°C is at or above {limit}°C.",
            )
        return fired

    def breached(
        self, reading: SensorReading, thresholds: dict[SensorKind, int]
    ) -> set[SensorKind]:
        """Return sensors currently at/above threshold (for red-trace styling)."""
        out: set[SensorKind] = set()
        for kind in DEFAULT_THRESHOLDS_C:
            value = reading.value(kind)
            limit = thresholds.get(kind, DEFAULT_THRESHOLDS_C[kind])
            if value is not None and value >= limit:
                out.add(kind)
        return out
