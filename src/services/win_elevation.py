"""Detect whether the current process is running elevated (Administrator).

LibreHardwareMonitor needs its kernel driver to read CPU package, memory, and
NVMe/SSD temperatures; that driver only loads when the process is elevated. GPU
temperature works without elevation.
"""

from __future__ import annotations

import ctypes
import logging

LOGGER = logging.getLogger(__name__)


def is_elevated() -> bool:
    """Return True when the process has Administrator rights (False on error)."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception as exc:  # pylint: disable=broad-exception-caught
        LOGGER.debug("elevation check failed: %s", exc)
        return False
