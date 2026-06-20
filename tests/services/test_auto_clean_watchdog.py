"""Tests for the auto-clean watchdog firing logic."""

from __future__ import annotations

from services.auto_clean_watchdog import AutoCleanConfig, AutoCleanWatchdog


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _watchdog(config: AutoCleanConfig):
    fires: list[float] = []
    clock = _Clock()
    wd = AutoCleanWatchdog(config, on_fire=lambda: fires.append(clock.t), clock=clock)
    return wd, clock, fires


def test_disabled_by_default_never_fires() -> None:
    wd, clock, fires = _watchdog(AutoCleanConfig())  # disabled
    for _ in range(100):
        clock.t += 5
        wd.observe(99.0)
    assert fires == []


def test_fires_after_debounce_then_respects_cooldown() -> None:
    config = AutoCleanConfig(
        enabled=True, threshold_percent=85.0, debounce_seconds=30.0, cooldown_seconds=300.0,
    )
    wd, clock, fires = _watchdog(config)

    # Stays above threshold; first sample only marks the start.
    clock.t = 0.0
    assert wd.observe(90.0) is False
    clock.t = 20.0
    assert wd.observe(90.0) is False  # not sustained long enough
    clock.t = 35.0
    assert wd.observe(90.0) is True   # 35s >= 30s debounce -> fire
    assert fires == [35.0]

    # Within cooldown, even sustained pressure does not re-fire.
    clock.t = 120.0
    assert wd.observe(90.0) is False
    # Once the cooldown has elapsed and pressure is still sustained, it fires.
    clock.t = 400.0
    assert wd.observe(90.0) is True
    assert fires == [35.0, 400.0]


def test_dropping_below_threshold_resets_debounce() -> None:
    config = AutoCleanConfig(enabled=True, threshold_percent=85.0, debounce_seconds=30.0)
    wd, clock, fires = _watchdog(config)
    clock.t = 0.0
    wd.observe(90.0)
    clock.t = 20.0
    wd.observe(50.0)   # dropped below -> reset
    clock.t = 40.0
    assert wd.observe(90.0) is False  # debounce restarted at 40s
    clock.t = 75.0
    assert wd.observe(90.0) is True
    assert fires == [75.0]


def test_update_config_disable_clears_state() -> None:
    config = AutoCleanConfig(enabled=True, threshold_percent=85.0, debounce_seconds=30.0)
    wd, clock, fires = _watchdog(config)
    clock.t = 0.0
    wd.observe(90.0)
    wd.update_config(AutoCleanConfig(enabled=False))
    clock.t = 100.0
    assert wd.observe(95.0) is False
    assert fires == []
