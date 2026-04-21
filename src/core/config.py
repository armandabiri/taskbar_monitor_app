"""Global configuration and settings for TaskbarMonitor."""

import logging
from types import ModuleType
from PyQt6.QtCore import QSettings

try:
    import winreg as _winreg
except ImportError:
    _winreg = None

LOGGER = logging.getLogger(__name__)
WINREG: ModuleType | None = _winreg

APP_ORG = "Intelag"
APP_NAME = "TaskbarMonitor"
AUTOSTART_NAME = "IntelagTaskbarMonitor"
RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"

CPU_CELL_SIZE = 5
CPU_CELL_SPACING = 1
CPU_GRID_ROWS = 4

SCOPE_MIN_WIDTH = 70
SCOPE_MIN_HEIGHT = 24
SCOPE_HISTORY_SIZE = 120
SCOPE_POINT_STEP = 2
SCOPE_BOTTOM_PADDING = 2
SCOPE_VERTICAL_PADDING = 4
SCOPE_GRID_X_STEP = 10
SCOPE_GRID_Y_STEP = 6
SCOPE_LINE_WIDTH = 1.3
SCOPE_LABEL_FONT = "Segoe UI"
SCOPE_LABEL_FONT_SIZE = 7
SCOPE_TEXT_SHADOW_ALPHA = 180
SCOPE_GRID_ALPHA = 12

BACKGROUND_RED = 10
BACKGROUND_GREEN = 10
BACKGROUND_BLUE = 10

DEFAULT_INTERVAL_MS = 1000
DEFAULT_BG_OPACITY = 230
DEFAULT_POS = -1
DEFAULT_WIDTH = -1
DEFAULT_HEIGHT = -1
DEFAULT_FALLBACK_WIDTH = 500
DEFAULT_FALLBACK_HEIGHT = 40
DEFAULT_SCREEN_PAD = 40

MIN_WIDGET_WIDTH = 150
MIN_WIDGET_HEIGHT = 20
EDGE_MARGIN = 10

MIN_OPACITY = 50
MAX_OPACITY = 255
SLIDER_WIDTH = 120

KB = 1024
MB = KB * KB
PERCENT_MAX = 100.0
MIN_AUTOSCALE = 1.0
CPU_WARMUP_INTERVAL_SECONDS = 0.1

INTERVAL_OPTIONS: tuple[tuple[str, int], ...] = (
    ("0.1s (Ultra)", 100),
    ("0.5s (Fast)", 500),
    ("1.0s (Normal)", 1000),
    ("2.0s (Slow)", 2000),
    ("5.0s (Eco)", 5000),
)


def read_setting_int(settings: QSettings, key: str, default: int) -> int:
    """Read an integer setting safely with a default fallback."""
    value = settings.value(key, default)
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        LOGGER.warning("Invalid integer setting for key=%s, using default=%s", key, default)
        return default
