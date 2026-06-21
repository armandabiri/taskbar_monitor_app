"""Watchdog: tripping cancel + notifying when a cleanup worker overruns."""

from __future__ import annotations

from types import SimpleNamespace

from services.resource_control.bounds import CleanupBounds
from ui import cleanup_controller as cc


def test_watchdog_trips_cancel_and_notifies(monkeypatch) -> None:
    cancelled: list[bool] = []
    notifications: list[tuple[str, str]] = []

    runner = SimpleNamespace(
        bounds=CleanupBounds(deadline_s=0.01),
        cancel=lambda: cancelled.append(True),
    )
    monkeypatch.setattr(
        cc.NotificationService, "notify",
        staticmethod(lambda app, msg: notifications.append((app, msg))),
    )

    controller = cc.CleanupController.__new__(cc.CleanupController)
    controller._in_flight = True
    controller._runner = runner
    controller._watchdog_tripped = False

    controller._on_watchdog_overrun(runner)

    assert cancelled == [True]
    assert controller._watchdog_tripped is True
    assert notifications, "watchdog must notify the user"
    assert "too long" in notifications[0][1].lower()


def test_watchdog_noop_when_not_in_flight(monkeypatch) -> None:
    cancelled: list[bool] = []
    runner = SimpleNamespace(
        bounds=CleanupBounds(deadline_s=0.01),
        cancel=lambda: cancelled.append(True),
    )
    monkeypatch.setattr(
        cc.NotificationService, "notify", staticmethod(lambda app, msg: None),
    )

    controller = cc.CleanupController.__new__(cc.CleanupController)
    controller._in_flight = False
    controller._runner = runner
    controller._watchdog_tripped = False

    controller._on_watchdog_overrun(runner)

    assert cancelled == []
    assert controller._watchdog_tripped is False


def test_watchdog_noop_for_different_runner(monkeypatch) -> None:
    cancelled: list[bool] = []
    other = SimpleNamespace(
        bounds=CleanupBounds(deadline_s=0.01),
        cancel=lambda: cancelled.append(True),
    )
    current = SimpleNamespace(
        bounds=CleanupBounds(deadline_s=0.01),
        cancel=lambda: cancelled.append(False),
    )
    monkeypatch.setattr(
        cc.NotificationService, "notify", staticmethod(lambda app, msg: None),
    )

    controller = cc.CleanupController.__new__(cc.CleanupController)
    controller._in_flight = True
    controller._runner = current
    controller._watchdog_tripped = False

    controller._on_watchdog_overrun(other)

    assert cancelled == []
