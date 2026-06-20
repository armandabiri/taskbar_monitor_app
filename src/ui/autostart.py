"""Windows autostart (Run-key) management for TaskbarMonitor."""

from __future__ import annotations

import logging
import os
import sys

from core.config import AUTOSTART_NAME, RUN_KEY_PATH, WINREG

LOGGER = logging.getLogger(__name__)


class AutostartManager:
    """Manages Windows registry keys for autostart."""

    @staticmethod
    def is_enabled() -> bool:
        """Check if autostart is enabled in registry."""
        if WINREG is None:
            return False
        try:
            with WINREG.OpenKey(
                WINREG.HKEY_CURRENT_USER,
                RUN_KEY_PATH,
                0,
                WINREG.KEY_READ,
            ) as registry_key:
                WINREG.QueryValueEx(registry_key, AUTOSTART_NAME)
            return True
        except OSError:
            return False

    @staticmethod
    def toggle() -> None:
        """Toggle autostart entry in the registry."""
        if WINREG is None:
            return

        is_enabled = AutostartManager.is_enabled()
        if is_enabled:
            try:
                with WINREG.OpenKey(
                    WINREG.HKEY_CURRENT_USER,
                    RUN_KEY_PATH,
                    0,
                    WINREG.KEY_SET_VALUE,
                ) as registry_key:
                    WINREG.DeleteValue(registry_key, AUTOSTART_NAME)
            except OSError:
                LOGGER.exception("Failed to disable autostart")
        else:
            try:
                command = f'"{sys.executable}" "{os.path.abspath(sys.argv[0])}"'
                with WINREG.OpenKey(
                    WINREG.HKEY_CURRENT_USER,
                    RUN_KEY_PATH,
                    0,
                    WINREG.KEY_SET_VALUE,
                ) as registry_key:
                    WINREG.SetValueEx(registry_key, AUTOSTART_NAME, 0, WINREG.REG_SZ, command)
            except OSError:
                LOGGER.exception("Failed to enable autostart")
