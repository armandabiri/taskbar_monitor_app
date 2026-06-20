"""Load the embedded LibreHardwareMonitorLib.dll through the .NET CLR (pythonnet).

This module resolves the bundled DLL path (dev tree or PyInstaller ``_MEIPASS``),
loads it via ``clr``, opens a ``Computer`` with CPU/memory/GPU/storage enabled,
and fails soft: a missing DLL or missing .NET runtime returns ``None`` and logs a
single clear reason instead of crashing startup.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
from pathlib import Path

LOGGER = logging.getLogger(__name__)

_DLL_RELPATH = os.path.join("assets", "sensors", "LibreHardwareMonitorLib.dll")

# SHA-256 of the trusted DLL. Empty means the pin is unset; loading then proceeds
# without a checksum check (a warning is logged once). Keep in sync with
# scripts/fetch_sensor_dll.py.
EXPECTED_SHA256 = "a0f2728f1734c236a9d02d9e25a88bc4f8cb7bd1faff1770726beb7af06bf8dc"

_pin_warned = False


def dll_path() -> Path:
    """Return the absolute path to the bundled sensor DLL (dev or frozen)."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / _DLL_RELPATH
    # src/services/sensors/lhm_clr_loader.py -> src/
    src_root = Path(__file__).resolve().parents[2]
    return src_root / _DLL_RELPATH


def verify_pin(path: Path) -> bool:
    """Return True when the DLL matches the pin (or the pin is unset)."""
    global _pin_warned
    if not EXPECTED_SHA256:
        if not _pin_warned:
            LOGGER.warning("sensors: clr dll pin is unset; loading %s without checksum", path)
            _pin_warned = True
        return True
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != EXPECTED_SHA256:
        LOGGER.warning("sensors: clr dll checksum mismatch for %s", path)
        return False
    return True


def load_computer() -> object | None:
    """Load the DLL and return an opened LHM ``Computer``, or ``None`` on failure."""
    path = dll_path()
    if not path.exists():
        LOGGER.info("sensors: clr backend unavailable: dll missing at %s", path)
        return None
    if not verify_pin(path):
        LOGGER.info("sensors: clr backend unavailable: dll pin mismatch")
        return None
    try:
        # The pinned DLL is a .NET Framework (net472) build; pythonnet 3 defaults
        # to .NET Core, which cannot load it. Force the .NET Framework runtime
        # (always present on Windows 11) before importing clr.
        os.environ.setdefault("PYTHONNET_RUNTIME", "netfx")
        import clr  # type: ignore

        clr.AddReference(str(path))
        from LibreHardwareMonitor.Hardware import Computer  # type: ignore

        computer = Computer()
        computer.IsCpuEnabled = True
        computer.IsMemoryEnabled = True
        computer.IsGpuEnabled = True
        computer.IsStorageEnabled = True
        computer.Open()
        return computer
    except Exception as exc:  # pylint: disable=broad-exception-caught
        LOGGER.info("sensors: clr backend unavailable: %s", exc)
        return None


def close_computer(computer: object | None) -> None:
    """Best-effort close of an opened ``Computer``."""
    if computer is None:
        return
    try:
        computer.Close()  # type: ignore[attr-defined]
    except Exception as exc:  # pylint: disable=broad-exception-caught
        LOGGER.debug("sensors: clr computer close failed: %s", exc)
