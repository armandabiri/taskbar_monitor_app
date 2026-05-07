"""Theme and painting logic for TaskbarMonitor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QColor, QPainter

from core.config import PERCENT_MAX

ThemeMode = str  # "system" | "light" | "dark"
THEME_MODES: tuple[ThemeMode, ...] = ("system", "light", "dark")
DEFAULT_THEME_MODE: ThemeMode = "system"


@dataclass(frozen=True)
class Theme:
    """Resolved set of theme tokens used for painting and styling."""
    name: str  # "light" | "dark"
    background_rgb: tuple[int, int, int]
    text: QColor
    text_shadow: QColor
    grid_color: QColor
    button_qss: str
    mic_recording_qss: str
    battery_outline: QColor
    battery_label: QColor
    battery_bolt: QColor


_DARK_BUTTON_QSS = """
    QPushButton {
        background-color: rgba(40, 40, 40, 200);
        color: #55efc4;
        border: 1px solid #444;
        border-radius: 4px;
        font-size: 13px;
        padding: 0;
    }
    QPushButton:hover {
        background-color: rgba(70, 70, 70, 220);
        border-color: #55efc4;
    }
    QPushButton:pressed {
        background-color: rgba(85, 239, 196, 60);
    }
    QPushButton:disabled {
        color: #fdcb6e;
        background-color: rgba(60, 60, 60, 180);
    }
"""

_DARK_MIC_QSS = """
    QPushButton {
        background-color: rgba(60, 25, 25, 220);
        color: #ff7675;
        border: 1px solid #ff7675;
        border-radius: 4px;
        font-size: 13px;
        padding: 0;
    }
    QPushButton:hover {
        background-color: rgba(85, 35, 35, 235);
        border-color: #fab1a0;
    }
    QPushButton:pressed {
        background-color: rgba(255, 118, 117, 70);
    }
"""

_LIGHT_BUTTON_QSS = """
    QPushButton {
        background-color: rgba(255, 255, 255, 220);
        color: #0a8f63;
        border: 1px solid #c8c8c8;
        border-radius: 4px;
        font-size: 13px;
        padding: 0;
    }
    QPushButton:hover {
        background-color: rgba(235, 235, 235, 235);
        border-color: #0a8f63;
    }
    QPushButton:pressed {
        background-color: rgba(10, 143, 99, 60);
    }
    QPushButton:disabled {
        color: #b07a00;
        background-color: rgba(220, 220, 220, 200);
    }
"""

_LIGHT_MIC_QSS = """
    QPushButton {
        background-color: rgba(255, 235, 235, 230);
        color: #c0392b;
        border: 1px solid #c0392b;
        border-radius: 4px;
        font-size: 13px;
        padding: 0;
    }
    QPushButton:hover {
        background-color: rgba(255, 215, 215, 240);
        border-color: #a93226;
    }
    QPushButton:pressed {
        background-color: rgba(192, 57, 43, 60);
    }
"""

_DARK = Theme(
    name="dark",
    background_rgb=(10, 10, 10),
    text=QColor(255, 255, 255),
    text_shadow=QColor(0, 0, 0, 180),
    grid_color=QColor(255, 255, 255, 12),
    button_qss=_DARK_BUTTON_QSS,
    mic_recording_qss=_DARK_MIC_QSS,
    battery_outline=QColor(220, 220, 220),
    battery_label=QColor(255, 255, 255),
    battery_bolt=QColor(10, 10, 10),
)

_LIGHT = Theme(
    name="light",
    background_rgb=(245, 245, 245),
    text=QColor(20, 20, 20),
    text_shadow=QColor(255, 255, 255, 200),
    grid_color=QColor(0, 0, 0, 22),
    button_qss=_LIGHT_BUTTON_QSS,
    mic_recording_qss=_LIGHT_MIC_QSS,
    battery_outline=QColor(60, 60, 60),
    battery_label=QColor(20, 20, 20),
    battery_bolt=QColor(245, 245, 245),
)


def _detect_system_is_dark() -> bool:
    """Return True if the OS is currently in dark mode (best effort)."""
    try:
        from PyQt6.QtCore import Qt as _Qt
        from PyQt6.QtGui import QGuiApplication
        hints = QGuiApplication.styleHints()
        if hints is not None:
            scheme = getattr(hints, "colorScheme", None)
            if callable(scheme):
                cs = scheme()
                if cs == _Qt.ColorScheme.Dark:
                    return True
                if cs == _Qt.ColorScheme.Light:
                    return False
    except (ImportError, AttributeError):
        pass
    # Windows registry fallback
    try:
        import winreg  # type: ignore
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return int(value) == 0
    except OSError:
        return True  # default to dark


class ThemeEngine:
    """Centralized theme state and painting helpers."""

    _mode: ThemeMode = DEFAULT_THEME_MODE
    _listeners: list[Callable[[Theme], None]] = []

    # ------------------------------------------------------------------
    # Mode + resolution
    # ------------------------------------------------------------------
    @classmethod
    def set_mode(cls, mode: ThemeMode) -> None:
        """Set the active theme mode and notify listeners."""
        if mode not in THEME_MODES:
            return
        cls._mode = mode
        cls._notify()

    @classmethod
    def get_mode(cls) -> ThemeMode:
        return cls._mode

    @classmethod
    def current(cls) -> Theme:
        """Return the resolved Theme for the active mode."""
        if cls._mode == "light":
            return _LIGHT
        if cls._mode == "dark":
            return _DARK
        return _DARK if _detect_system_is_dark() else _LIGHT

    # ------------------------------------------------------------------
    # Listeners
    # ------------------------------------------------------------------
    @classmethod
    def add_listener(cls, callback: Callable[[Theme], None]) -> None:
        cls._listeners.append(callback)

    @classmethod
    def remove_listener(cls, callback: Callable[[Theme], None]) -> None:
        try:
            cls._listeners.remove(callback)
        except ValueError:
            pass

    @classmethod
    def system_scheme_changed(cls) -> None:
        """Hook to call when the OS color scheme changes."""
        if cls._mode == "system":
            cls._notify()

    @classmethod
    def _notify(cls) -> None:
        theme = cls.current()
        for cb in list(cls._listeners):
            try:
                cb(theme)
            except Exception:  # pylint: disable=broad-exception-caught
                pass

    # ------------------------------------------------------------------
    # Painting helpers (kept as static for paintEvent callsites)
    # ------------------------------------------------------------------
    @staticmethod
    def get_dynamic_color(value: float) -> QColor:
        """Map utilization percent to a blue-to-red gradient."""
        ratio = min(max(value / PERCENT_MAX, 0.0), 1.0)
        red = int(45 + (231 - 45) * ratio)
        green = int(133 + (76 - 133) * ratio)
        blue = int(219 + (60 - 219) * ratio)
        return QColor(red, green, blue)

    @staticmethod
    def paint_background(painter: QPainter, rect: QRect, opacity: int) -> None:
        """Paint the translucent monitor background using the active theme."""
        theme = ThemeEngine.current()
        red, green, blue = theme.background_rgb
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(red, green, blue, opacity))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(rect)
