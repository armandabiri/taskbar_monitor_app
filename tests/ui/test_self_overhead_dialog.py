"""Tests for SelfOverheadDialog."""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

from ui.self_overhead_dialog import SelfOverheadDialog, _fmt_cleanup
from services.app_metrics_probe import AppMetrics
from services.resource_control.models import CleanupHistoryEntry


def _fake_entry(ts: float | None = None) -> CleanupHistoryEntry:
    return CleanupHistoryEntry(
        run_id="x1",
        timestamp=ts if ts is not None else time.time() - 30,
        mode="system_reclaim",
        profile_name="default",
        snapshot_name=None,
        processes_cleaned_total=5,
        processes_trimmed=3,
        processes_killed=2,
        processes_throttled=0,
        kill_candidates_found=2,
        snapshot_extras_found=0,
        snapshot_extras_selected=0,
        blocked_reason_counts={},
        errors=[],
        summary="freed 0.2 GB",
    )


def _fake_metrics(cpu: float = 1.0, rss_mb: float = 80.0, last_cleanup=None) -> AppMetrics:
    return AppMetrics(cpu_percent=cpu, rss_mb=rss_mb, last_cleanup=last_cleanup)


def test_fmt_cleanup_no_history():
    metrics = _fake_metrics(last_cleanup=None)
    assert _fmt_cleanup(metrics) == "No cleanup runs recorded"


def test_fmt_cleanup_with_entry():
    entry = _fake_entry(ts=time.time() - 30)
    metrics = _fake_metrics(last_cleanup=entry)
    text = _fmt_cleanup(metrics)
    assert "freed 0.2 GB" in text
    assert "system_reclaim" in text
    assert "cleaned=5" in text


def test_fmt_cleanup_shows_minutes_for_old_entry():
    entry = _fake_entry(ts=time.time() - 90)
    metrics = _fake_metrics(last_cleanup=entry)
    text = _fmt_cleanup(metrics)
    assert "m ago" in text


@patch("ui.self_overhead_dialog.AppMetricsProbe")
def test_dialog_renders_fields(mock_probe_cls, qtbot):
    probe = MagicMock()
    probe.sample.return_value = _fake_metrics(cpu=3.2, rss_mb=120.5)
    mock_probe_cls.return_value = probe

    dlg = SelfOverheadDialog()
    qtbot.addWidget(dlg)

    assert "3.2" in dlg._cpu_lbl.text()
    assert "120.5" in dlg._ram_lbl.text()
    assert dlg._cleanup_lbl.text() == "No cleanup runs recorded"


@patch("ui.self_overhead_dialog.AppMetricsProbe")
def test_dialog_refresh_updates_labels(mock_probe_cls, qtbot):
    probe = MagicMock()
    probe.sample.return_value = _fake_metrics(cpu=0.5, rss_mb=55.0)
    mock_probe_cls.return_value = probe

    dlg = SelfOverheadDialog()
    qtbot.addWidget(dlg)

    probe.sample.return_value = _fake_metrics(cpu=9.9, rss_mb=200.0)
    dlg._refresh()

    assert "9.9" in dlg._cpu_lbl.text()
    assert "200.0" in dlg._ram_lbl.text()
