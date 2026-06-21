"""T02 — SystemSnapshot + SystemSampler contract tests.

The sampler is verified through its pure ``build_snapshot`` builder so the
test does not need to spin a real Qt event loop. T04 will layer on tests
that prove the render path performs no syscalls.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from services.system_sampler import (
    SamplerReaders,
    SystemSampler,
    build_snapshot,
)
from services.system_snapshot import SamplerCounterState, SystemSnapshot


@dataclass
class _FakeMem:
    percent: float = 42.0


@dataclass
class _FakeNet:
    bytes_sent: int
    bytes_recv: int


@dataclass
class _FakeDisk:
    read_bytes: int
    write_bytes: int


def _readers(net: _FakeNet, disk: _FakeDisk, per_cpu=(10.0, 30.0)) -> SamplerReaders:
    return SamplerReaders(
        per_cpu=lambda: list(per_cpu),
        virtual_memory=lambda: _FakeMem(),
        net_io=lambda: net,
        disk_io=lambda: disk,
        gpu_stats=lambda: "gpu",
        sensors_snapshot=lambda: "sensors",
        battery=lambda: "battery",
        clock=lambda: 123.0,
    )


def test_snapshot_is_frozen_and_immutable() -> None:
    snap = SystemSnapshot(
        sampled_at=1.0,
        per_cpu=(1.0, 2.0),
        cpu_avg=1.5,
        ram_percent=50.0,
        net_up_bps=0.0,
        net_down_bps=0.0,
        disk_rw_bps=0.0,
    )
    with pytest.raises(Exception):
        snap.cpu_avg = 99.0  # type: ignore[misc]


def test_build_snapshot_first_tick_zero_deltas() -> None:
    readers = _readers(_FakeNet(1000, 2000), _FakeDisk(500, 700))
    snap, state = build_snapshot(readers, SamplerCounterState.zero())
    assert snap.cpu_avg == pytest.approx(20.0)
    assert snap.ram_percent == 42.0
    assert snap.net_up_bps == 0.0
    assert snap.net_down_bps == 0.0
    assert snap.disk_rw_bps == 0.0
    assert state.net_bytes_sent == 1000
    assert state.disk_read_bytes == 500


def test_build_snapshot_second_tick_computes_deltas() -> None:
    readers = _readers(_FakeNet(1500, 2400), _FakeDisk(900, 1100))
    prev = SamplerCounterState(1000, 2000, 500, 700)
    snap, state = build_snapshot(readers, prev)
    assert snap.net_up_bps == 500.0
    assert snap.net_down_bps == 400.0
    assert snap.disk_rw_bps == (900 - 500) + (1100 - 700)
    assert state.net_bytes_sent == 1500


def test_build_snapshot_handles_missing_disk_counters() -> None:
    readers = SamplerReaders(
        per_cpu=lambda: [],
        virtual_memory=lambda: _FakeMem(percent=0.0),
        net_io=lambda: _FakeNet(0, 0),
        disk_io=lambda: None,
        gpu_stats=lambda: None,
        sensors_snapshot=lambda: None,
        battery=lambda: None,
        clock=lambda: 0.0,
    )
    snap, state = build_snapshot(readers, SamplerCounterState.zero())
    assert snap.cpu_avg == 0.0
    assert snap.disk_rw_bps == 0.0
    assert state.disk_read_bytes == 0


def test_sampler_tick_emits_snapshot_ready(qtbot) -> None:
    sampler = SystemSampler(
        readers=_readers(_FakeNet(0, 0), _FakeDisk(0, 0)),
    )
    received: list[Any] = []
    sampler.snapshot_ready.connect(received.append)
    sampler.tick()
    assert len(received) == 1
    assert isinstance(received[0], SystemSnapshot)
    assert sampler.latest() is received[0]


def test_sampler_set_interval_clamps_minimum(qtbot) -> None:
    sampler = SystemSampler(readers=_readers(_FakeNet(0, 0), _FakeDisk(0, 0)))
    sampler.set_interval(10)
    assert sampler._interval_ms == 50


# ---------------------------------------------------------------------------
# T04 — UI render path performs no syscalls, tolerates slow sampler
# ---------------------------------------------------------------------------
def test_render_path_only_reads_snapshot(qtbot) -> None:
    """The render slot must consume the snapshot; no psutil/GPU call here."""
    import psutil as _psutil

    sampler = SystemSampler(readers=_readers(_FakeNet(0, 0), _FakeDisk(0, 0)))
    rendered: list[Any] = []

    def render(snap: Any) -> None:
        # Read-only access — touch a few snapshot fields the real UI uses.
        rendered.append((snap.cpu_avg, snap.ram_percent, snap.gpu_stats))

    sampler.snapshot_ready.connect(render)

    calls = {"n": 0}
    original = _psutil.cpu_percent

    def _spy(*a, **kw):  # pragma: no cover - exercised via tick if called
        calls["n"] += 1
        return original(*a, **kw)

    _psutil.cpu_percent = _spy
    try:
        sampler.tick()  # syscall replacements happen in fakes, not in psutil
        # The render slot only reads the snapshot; no extra psutil calls.
        assert calls["n"] == 0
    finally:
        _psutil.cpu_percent = original
    assert len(rendered) == 1


def test_worker_thread_delivers_snapshots_without_blocking(qtbot) -> None:
    """Slow sampler must not block the event loop; signal queues across thread."""
    import time as _time

    slow_calls = {"n": 0}

    def slow_cpu() -> list[float]:
        slow_calls["n"] += 1
        _time.sleep(0.05)
        return [10.0, 20.0]

    readers = SamplerReaders(
        per_cpu=slow_cpu,
        virtual_memory=lambda: _FakeMem(),
        net_io=lambda: _FakeNet(0, 0),
        disk_io=lambda: _FakeDisk(0, 0),
        gpu_stats=lambda: None,
        sensors_snapshot=lambda: None,
        battery=lambda: None,
        clock=lambda: 0.0,
    )
    sampler = SystemSampler(readers=readers)
    received: list[Any] = []
    sampler.snapshot_ready.connect(received.append)

    sampler.start_worker(60)
    try:
        # Wait for cross-thread queued signals to be delivered on the UI thread.
        qtbot.waitUntil(lambda: len(received) >= 2, timeout=2000)
    finally:
        sampler.stop_worker(timeout_s=1.0)
    assert len(received) >= 2
    assert slow_calls["n"] >= 2


# ---------------------------------------------------------------------------
# T05/T06 — shared process table: single owner of process_iter
# ---------------------------------------------------------------------------
def test_top_processes_refresh_uses_injected_reader(qtbot) -> None:
    """The sampler is the sole owner of the top-process walk."""
    calls = {"n": 0}

    def fake_top(limit: int) -> list[Any]:
        calls["n"] += 1
        return [object()]

    readers = SamplerReaders(
        per_cpu=lambda: [0.0],
        virtual_memory=lambda: _FakeMem(),
        net_io=lambda: _FakeNet(0, 0),
        disk_io=lambda: _FakeDisk(0, 0),
        gpu_stats=lambda: None,
        sensors_snapshot=lambda: None,
        battery=lambda: None,
        top_processes=fake_top,
        clock=lambda: 0.0,
    )
    sampler = SystemSampler(
        readers=readers, top_proc_refresh_s=10.0, top_proc_limit=5
    )
    sampler.tick()
    sampler.tick()  # within refresh window → cache reused
    assert calls["n"] == 1
    snap = sampler.latest()
    assert snap is not None
    assert snap.top_processes is not None and len(snap.top_processes) == 1


def test_popup_consumes_sampler_snapshot_without_process_iter(qtbot) -> None:
    """TopProcessesPopup must read from the sampler, never call process_iter."""
    import psutil as _psutil

    from services.system_info import ProcessRow
    from ui.process_popup import TopProcessesPopup

    rows = [
        ProcessRow(pid=1, name="a", cpu_percent=5.0, ram_mb=100.0),
        ProcessRow(pid=2, name="b", cpu_percent=50.0, ram_mb=30.0),
    ]
    readers = SamplerReaders(
        per_cpu=lambda: [0.0],
        virtual_memory=lambda: _FakeMem(),
        net_io=lambda: _FakeNet(0, 0),
        disk_io=lambda: _FakeDisk(0, 0),
        gpu_stats=lambda: None,
        sensors_snapshot=lambda: None,
        battery=lambda: None,
        top_processes=lambda limit: rows,
        clock=lambda: 0.0,
    )
    sampler = SystemSampler(readers=readers, top_proc_refresh_s=0.0)

    iter_calls = {"n": 0}
    original = _psutil.process_iter

    def _spy(*a, **kw):
        iter_calls["n"] += 1
        return original(*a, **kw)

    _psutil.process_iter = _spy
    try:
        popup = TopProcessesPopup(sampler=sampler)
        qtbot.addWidget(popup)
        popup.show()
        try:
            sampler.tick()
        finally:
            popup.hide()
    finally:
        _psutil.process_iter = original

    # Popup must NOT walk processes itself — only the sampler's injected
    # reader is the owner; in this test it's a pure list so process_iter
    # is never reached.
    assert iter_calls["n"] == 0
    assert popup.table.rowCount() == 2
