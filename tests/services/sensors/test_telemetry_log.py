"""Tests for telemetry logging: format, append-only guarantee, bounded rotation."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from services.sensors.models import SensorReading
from services.sensors.telemetry_log import CSV_HEADER, TelemetryLog


def _reading(taken_at: float = 0.0, **kwargs) -> SensorReading:
    return SensorReading(taken_at=taken_at, **kwargs)


def test_csv_writes_header_and_row(tmp_path):
    path = tmp_path / "t.csv"
    log = TelemetryLog(str(path), fmt="csv")
    log.append(_reading(1.0, cpu_temp_c=50.0, ssd_temp_c=45.0, backend_id="x"))
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == CSV_HEADER
    assert lines[1].startswith("1.0,50.0,")
    assert lines[1].endswith(",x")


def test_jsonl_writes_object(tmp_path):
    path = tmp_path / "t.jsonl"
    log = TelemetryLog(str(path), fmt="jsonl")
    log.append(_reading(2.0, gpu_temp_c=61.0, backend_id="nvml"))
    obj = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert obj["gpu_temp_c"] == 61.0
    assert obj["backend_id"] == "nvml"


def test_rotation_creates_bak_and_fresh_file(tmp_path):
    path = tmp_path / "t.csv"
    log = TelemetryLog(str(path), fmt="csv", retention_rows=3)
    for i in range(5):
        log.append(_reading(float(i)))
    bak = tmp_path / "t.csv.bak"
    assert bak.exists(), "rotation should produce a .bak file"
    bak_lines = bak.read_text(encoding="utf-8").splitlines()
    assert bak_lines[0] == CSV_HEADER
    assert len(bak_lines) == 1 + 3  # header + 3 rows
    cur_lines = path.read_text(encoding="utf-8").splitlines()
    assert cur_lines[0] == CSV_HEADER
    assert len(cur_lines) == 1 + 2  # header + rows 3,4


def test_rotation_preserves_header_on_fresh_file(tmp_path):
    path = tmp_path / "t.csv"
    log = TelemetryLog(str(path), fmt="csv", retention_rows=2)
    for i in range(3):
        log.append(_reading(float(i)))
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == CSV_HEADER


def test_no_full_file_read_during_steady_appends(tmp_path):
    """After the handle is opened, subsequent appends must not read the file."""
    path = tmp_path / "t.csv"
    log = TelemetryLog(str(path), fmt="csv", retention_rows=100)
    # First append triggers _open_handle (reads existing file, which is new so no read)
    log.append(_reading(0.0))
    # Spy on open() calls that would indicate a file read
    real_open = open
    read_calls: list[str] = []

    def spy_open(file, mode="r", **kwargs):
        if str(file) == str(path) and "r" in str(mode) and "w" not in str(mode):
            read_calls.append(str(mode))
        return real_open(file, mode, **kwargs)

    with patch("builtins.open", spy_open):
        for i in range(1, 20):
            log.append(_reading(float(i)))

    assert not read_calls, f"file was read during appends: {read_calls}"


def test_jsonl_rotation_no_header(tmp_path):
    path = tmp_path / "t.jsonl"
    log = TelemetryLog(str(path), fmt="jsonl", retention_rows=2)
    for i in range(3):
        log.append(_reading(float(i)))
    bak = tmp_path / "t.jsonl.bak"
    assert bak.exists()
    bak_lines = bak.read_text(encoding="utf-8").splitlines()
    assert len(bak_lines) == 2
    # No CSV header in JSONL
    assert all(line.startswith("{") for line in bak_lines)
