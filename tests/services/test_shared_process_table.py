"""T06 — shared process table: single owner of psutil.process_iter.

The sampler is the sole producer of the top-process list; the popup and the
auto-clean watchdog consume the cached table via ``SystemSnapshot``. This
test fails if any consumer reintroduces its own ``process_iter`` walk.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import psutil

from services.system_info import ProcessRow
from services.system_sampler import SamplerReaders, SystemSampler
from ui.process_popup import TopProcessesPopup


@dataclass
class _Mem:
    percent: float = 50.0


@dataclass
class _Net:
    bytes_sent: int = 0
    bytes_recv: int = 0


@dataclass
class _Disk:
    read_bytes: int = 0
    write_bytes: int = 0


def _readers(rows: list[ProcessRow]) -> SamplerReaders:
    return SamplerReaders(
        per_cpu=lambda: [0.0],
        virtual_memory=lambda: _Mem(),
        net_io=lambda: _Net(),
        disk_io=lambda: _Disk(),
        gpu_stats=lambda: None,
        sensors_snapshot=lambda: None,
        battery=lambda: None,
        top_processes=lambda limit: rows,
        clock=lambda: 0.0,
    )


def test_popup_and_watchdog_read_cache_not_process_iter(qtbot) -> None:
    rows = [ProcessRow(pid=10, name="x", cpu_percent=1.0, ram_mb=20.0)]
    sampler = SystemSampler(readers=_readers(rows), top_proc_refresh_s=0.0)

    iter_calls = {"n": 0}
    original = psutil.process_iter

    def _spy(*a, **kw):
        iter_calls["n"] += 1
        return original(*a, **kw)

    psutil.process_iter = _spy
    try:
        popup = TopProcessesPopup(sampler=sampler)
        qtbot.addWidget(popup)
        popup.show()
        try:
            sampler.tick()
            sampler.tick()
        finally:
            popup.hide()
    finally:
        psutil.process_iter = original

    # Popup is a pure cache consumer.
    assert iter_calls["n"] == 0
    snap = sampler.latest()
    assert snap is not None and snap.top_processes is not None
    assert len(snap.top_processes) == 1
    assert popup.table.rowCount() == 1


def test_sampler_refreshes_top_processes_at_configured_cadence(qtbot) -> None:
    calls = {"n": 0}
    now = {"t": 0.0}

    def _top(limit: int) -> list[Any]:
        calls["n"] += 1
        return []

    readers = SamplerReaders(
        per_cpu=lambda: [0.0],
        virtual_memory=lambda: _Mem(),
        net_io=lambda: _Net(),
        disk_io=lambda: _Disk(),
        gpu_stats=lambda: None,
        sensors_snapshot=lambda: None,
        battery=lambda: None,
        top_processes=_top,
        clock=lambda: now["t"],
    )
    sampler = SystemSampler(readers=readers, top_proc_refresh_s=1.0)

    sampler.tick()
    now["t"] = 0.5
    sampler.tick()
    now["t"] = 1.5
    sampler.tick()

    # First tick refreshes, second is within window (cached), third crosses it.
    assert calls["n"] == 2
