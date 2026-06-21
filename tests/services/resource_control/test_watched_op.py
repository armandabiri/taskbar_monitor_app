"""Tests for the WatchedSystemOp timeout wrapper."""

from __future__ import annotations

import time

from services.resource_control.watched_op import OpResult, WatchedSystemOp


def test_fast_op_reports_ok() -> None:
    outcome = WatchedSystemOp.run(lambda: True, timeout_s=1.0)

    assert isinstance(outcome, OpResult)
    assert outcome.ok is True
    assert outcome.timed_out is False
    assert outcome.error is None
    assert outcome.elapsed_s < 1.0


def test_slow_op_times_out_within_budget() -> None:
    def _slow() -> bool:
        time.sleep(1.0)
        return True

    start = time.monotonic()
    outcome = WatchedSystemOp.run(_slow, timeout_s=0.1)
    elapsed = time.monotonic() - start

    assert outcome.timed_out is True
    assert outcome.ok is False
    # The watchdog must not block for the full slow-op duration.
    assert elapsed < 0.6


def test_op_exception_reported_not_raised() -> None:
    def _boom() -> bool:
        raise OSError("nope")

    outcome = WatchedSystemOp.run(_boom, timeout_s=1.0)

    assert outcome.ok is False
    assert outcome.timed_out is False
    assert outcome.error is not None
    assert "nope" in outcome.error


def test_falsey_return_reports_not_ok() -> None:
    outcome = WatchedSystemOp.run(lambda: False, timeout_s=1.0)

    assert outcome.ok is False
    assert outcome.timed_out is False
