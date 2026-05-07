"""Enumerate Windows processes by Win32_CommandLine via PowerShell/CIM."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from typing import Any

import psutil
from PyQt6.QtCore import QSettings

LOGGER = logging.getLogger(__name__)

SETTINGS_KEY_PATTERN = "cmdline_kill_pattern"
SETTINGS_KEY_REMEMBER = "cmdline_kill_remember_pattern"
DEFAULT_PATTERN = "isqlv|app_isqlv|intelag_sql_studio"


def load_saved_pattern(settings: QSettings) -> str:
    raw = settings.value(SETTINGS_KEY_PATTERN, DEFAULT_PATTERN)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return DEFAULT_PATTERN


def load_remember_pattern(settings: QSettings) -> bool:
    v = settings.value(SETTINGS_KEY_REMEMBER, 1)
    if isinstance(v, str):
        return v not in ("0", "false", "False")
    return bool(int(v)) if isinstance(v, (int, float)) else True


def save_pattern_preferences(
    settings: QSettings, pattern: str, *, remember: bool
) -> None:
    if remember:
        settings.setValue(SETTINGS_KEY_PATTERN, pattern.strip())
    settings.setValue(SETTINGS_KEY_REMEMBER, 1 if remember else 0)
    settings.sync()


def query_processes_by_commandline_regex(pattern: str) -> list[tuple[int, str, str]]:
    """Return (pid, image_name, command_line) for processes whose CommandLine matches.

    Uses Get-CimInstance Win32_Process and PowerShell's -match (regex), same idea as
    ``Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match $pat }``.
    """
    if sys.platform != "win32":
        return []

    pat = pattern.strip()
    if not pat:
        return []

    ps_pat = pat.replace("'", "''")
    ps_script = (
        "$ErrorActionPreference='Stop'; "
        f"$pat = '{ps_pat}'; "
        "$procs = @(Get-CimInstance Win32_Process | "
        "Where-Object { $null -ne $_.CommandLine -and ($_.CommandLine -match $pat) } | "
        "Select-Object ProcessId, Name, CommandLine); "
        "$procs | ConvertTo-Json -Compress -Depth 5"
    )
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            creationflags=creationflags,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        LOGGER.warning("WMI query failed: %s", exc)
        raise RuntimeError(f"Could not query processes: {exc}") from exc

    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or "").strip()
        LOGGER.warning("PowerShell exited %s: %s", completed.returncode, err)
        raise RuntimeError(err or "PowerShell query failed")

    raw = (completed.stdout or "").strip()
    if not raw:
        return []

    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        LOGGER.warning("Invalid JSON from PowerShell: %s", raw[:500])
        raise RuntimeError("Unexpected output from process query") from exc

    if isinstance(data, dict):
        rows = [data]
    elif isinstance(data, list):
        rows = data
    else:
        return []

    out: list[tuple[int, str, str]] = []
    seen: set[int] = set()
    self_pid = os.getpid()
    for row in rows:
        try:
            pid = int(row["ProcessId"])
        except (KeyError, TypeError, ValueError):
            continue
        if pid == self_pid or pid in seen:
            continue
        seen.add(pid)
        name = str(row.get("Name") or "")
        cl = str(row.get("CommandLine") or "")
        out.append((pid, name, cl))
    return out


def terminate_pids(pids: list[int]) -> tuple[int, list[str]]:
    """Terminate PIDs via psutil. Returns (count_terminated_or_gone, error_messages)."""
    errors: list[str] = []
    ok = 0
    for pid in pids:
        try:
            psutil.Process(pid).kill()
            ok += 1
        except psutil.NoSuchProcess:
            ok += 1
        except psutil.Error as exc:
            errors.append(f"PID {pid}: {exc}")
    return ok, errors
