"""Tests for AppMetricsProbe."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from services.app_metrics_probe import AppMetrics, AppMetricsProbe
from services.resource_control.models import CleanupHistoryEntry


def _fake_entry() -> CleanupHistoryEntry:
    return CleanupHistoryEntry(
        run_id="abc",
        timestamp=1000.0,
        mode="system_reclaim",
        profile_name="default",
        snapshot_name=None,
        processes_cleaned_total=3,
        processes_trimmed=2,
        processes_killed=1,
        processes_throttled=0,
        kill_candidates_found=1,
        snapshot_extras_found=0,
        snapshot_extras_selected=0,
        blocked_reason_counts={},
        errors=[],
        summary="freed 0.5 GB",
    )


@patch("services.app_metrics_probe.read_history")
@patch("services.app_metrics_probe.psutil.Process")
def test_sample_returns_own_metrics(mock_proc_cls, mock_history):
    proc = MagicMock()
    proc.__enter__ = lambda s: s
    proc.__exit__ = MagicMock(return_value=False)
    proc.cpu_percent.return_value = 2.5
    mem = MagicMock()
    mem.rss = 50 * 1024 * 1024
    proc.memory_info.return_value = mem
    mock_proc_cls.return_value = proc
    mock_history.return_value = []

    probe = AppMetricsProbe(pid=999)
    result = probe.sample()

    assert result.cpu_percent == pytest.approx(2.5)
    assert result.rss_mb == pytest.approx(50.0)
    assert result.last_cleanup is None


@patch("services.app_metrics_probe.read_history")
@patch("services.app_metrics_probe.psutil.Process")
def test_sample_includes_last_cleanup(mock_proc_cls, mock_history):
    proc = MagicMock()
    proc.__enter__ = lambda s: s
    proc.__exit__ = MagicMock(return_value=False)
    proc.cpu_percent.return_value = 0.0
    mem = MagicMock()
    mem.rss = 1024 * 1024
    proc.memory_info.return_value = mem
    mock_proc_cls.return_value = proc
    entry = _fake_entry()
    mock_history.return_value = [entry]

    probe = AppMetricsProbe(pid=999)
    result = probe.sample()

    assert result.last_cleanup is entry
    mock_history.assert_called_once_with(limit=1)
