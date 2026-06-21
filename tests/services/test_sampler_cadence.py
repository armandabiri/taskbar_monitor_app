"""T08 — Cadence adapts to visibility/power state; settings round-trip."""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QSettings

from services.system_sampler import SystemSampler, SamplerReaders, choose_interval
from services.system_snapshot import SamplerCounterState


# ---------------------------------------------------------------------------
# Fake readers
# ---------------------------------------------------------------------------

from dataclasses import dataclass


@dataclass
class _FakeMem:
    percent: float = 42.0


@dataclass
class _FakeNet:
    bytes_sent: int = 0
    bytes_recv: int = 0


@dataclass
class _FakeDisk:
    read_bytes: int = 0
    write_bytes: int = 0


def _fake_readers() -> SamplerReaders:
    return SamplerReaders(
        per_cpu=lambda: [10.0, 20.0],
        virtual_memory=_FakeMem,
        net_io=lambda: _FakeNet(),
        disk_io=lambda: _FakeDisk(),
        gpu_stats=lambda: None,
        sensors_snapshot=lambda: None,
        battery=lambda: None,
    )


# ---------------------------------------------------------------------------
# choose_interval pure function
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("visible,on_battery,pause,expected", [
    (True,  False, False, 1000),   # visible, AC → active
    (True,  False, True,  1000),   # visible, AC, pause enabled → still active
    (True,  True,  False, 1000),   # visible, battery, pause disabled → active
    (True,  True,  True,  5000),   # visible, battery, pause enabled → hidden rate
    (False, False, False, 5000),   # hidden, AC → hidden rate
    (False, True,  True,  5000),   # hidden, battery → hidden rate
])
def test_choose_interval(visible, on_battery, pause, expected):
    result = choose_interval(1000, 5000, visible=visible, on_battery=on_battery, pause_on_battery=pause)
    assert result == expected


# ---------------------------------------------------------------------------
# SystemSampler.set_interval
# ---------------------------------------------------------------------------

@pytest.fixture()
def sampler(qtbot):
    s = SystemSampler(readers=_fake_readers())
    yield s
    s.stop_worker()


def test_set_interval_stores_value(sampler):
    sampler.set_interval(2000)
    with sampler._lock:
        assert sampler._interval_ms == 2000


def test_set_interval_clamps_minimum(sampler):
    sampler.set_interval(0)
    with sampler._lock:
        assert sampler._interval_ms == 50


def test_start_worker_picks_up_new_interval(sampler, qtbot):
    sampler.start_worker(500)
    sampler.set_interval(300)
    with sampler._lock:
        assert sampler._interval_ms == 300
    sampler.stop_worker()


# ---------------------------------------------------------------------------
# QSettings round-trip for cadence keys
# ---------------------------------------------------------------------------

def test_cadence_settings_roundtrip(tmp_path):
    path = str(tmp_path / "cadence_test.ini")
    s = QSettings(path, QSettings.Format.IniFormat)
    s.setValue("sampler/active_interval_ms", 1500)
    s.setValue("sampler/hidden_interval_ms", 8000)
    s.setValue("sampler/pause_on_battery", 1)
    s.sync()

    s2 = QSettings(path, QSettings.Format.IniFormat)
    assert int(s2.value("sampler/active_interval_ms", 1000)) == 1500
    assert int(s2.value("sampler/hidden_interval_ms", 5000)) == 8000
    assert int(s2.value("sampler/pause_on_battery", 0)) == 1
