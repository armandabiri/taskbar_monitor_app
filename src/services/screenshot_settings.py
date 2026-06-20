"""Persistent screenshot output and capture options (QSettings)."""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QSettings

from core.config import recordings_dir

SETTINGS_GROUP = "screenshot"
KEY_OUTPUT_DIR = "output_dir"
KEY_FORMAT = "format"
KEY_COPY_CLIPBOARD = "copy_clipboard"
KEY_SAVE_FILE = "save_file"
KEY_INCLUDE_CURSOR = "include_cursor"
KEY_SCROLL_DELAY_MS = "scroll_delay_ms"
KEY_DEBUG_FRAMES = "debug_frames"
KEY_AUTO_OPEN_EDITOR = "auto_open_editor"
KEY_CAPTURE_DELAY_S = "capture_delay_s"


@dataclass
class ScreenshotSettings:
    output_dir: str = ""
    image_format: str = "png"
    copy_enabled: bool = True
    save_enabled: bool = False
    include_cursor: bool = False
    scroll_delay_ms: int = 380
    debug_frames: bool = False
    auto_open_editor: bool = False
    capture_delay_s: int = 0

    def normalized(self) -> ScreenshotSettings:
        fmt = (self.image_format or "png").strip().lower()
        if fmt not in ("png", "jpeg", "jpg"):
            fmt = "png"
        delay = max(120, min(2000, int(self.scroll_delay_ms)))
        return ScreenshotSettings(
            output_dir=(self.output_dir or "").strip(),
            image_format="jpeg" if fmt == "jpg" else fmt,
            copy_enabled=bool(self.copy_enabled),
            save_enabled=bool(self.save_enabled),
            include_cursor=bool(self.include_cursor),
            scroll_delay_ms=delay,
            debug_frames=bool(self.debug_frames),
            auto_open_editor=bool(self.auto_open_editor),
            capture_delay_s=max(0, min(10, int(self.capture_delay_s))),
        )

    def effective_output_dir(self) -> str:
        if self.output_dir:
            return self.output_dir
        return recordings_dir()


def _read_bool(settings: QSettings, key: str, default: bool) -> bool:
    value = settings.value(key, 1 if default else 0)
    if isinstance(value, bool):
        return value
    try:
        return int(value) != 0
    except (TypeError, ValueError):
        return default


def load_screenshot_settings(settings: QSettings) -> ScreenshotSettings:
    base = ScreenshotSettings()
    prefix = f"{SETTINGS_GROUP}/"
    loaded = ScreenshotSettings(
        output_dir=str(settings.value(f"{prefix}{KEY_OUTPUT_DIR}", base.output_dir) or ""),
        image_format=str(settings.value(f"{prefix}{KEY_FORMAT}", base.image_format) or "png"),
        copy_enabled=_read_bool(settings, f"{prefix}{KEY_COPY_CLIPBOARD}", base.copy_enabled),
        save_enabled=_read_bool(settings, f"{prefix}{KEY_SAVE_FILE}", base.save_enabled),
        include_cursor=_read_bool(settings, f"{prefix}{KEY_INCLUDE_CURSOR}", base.include_cursor),
        scroll_delay_ms=int(settings.value(f"{prefix}{KEY_SCROLL_DELAY_MS}", base.scroll_delay_ms)),
        debug_frames=_read_bool(settings, f"{prefix}{KEY_DEBUG_FRAMES}", base.debug_frames),
        auto_open_editor=_read_bool(
            settings, f"{prefix}{KEY_AUTO_OPEN_EDITOR}", base.auto_open_editor
        ),
        capture_delay_s=int(
            settings.value(f"{prefix}{KEY_CAPTURE_DELAY_S}", base.capture_delay_s)
        ),
    )
    return loaded.normalized()


def save_screenshot_settings(settings: QSettings, screenshot: ScreenshotSettings) -> None:
    normalized = screenshot.normalized()
    prefix = f"{SETTINGS_GROUP}/"
    settings.setValue(f"{prefix}{KEY_OUTPUT_DIR}", normalized.output_dir)
    settings.setValue(f"{prefix}{KEY_FORMAT}", normalized.image_format)
    settings.setValue(f"{prefix}{KEY_COPY_CLIPBOARD}", 1 if normalized.copy_enabled else 0)
    settings.setValue(f"{prefix}{KEY_SAVE_FILE}", 1 if normalized.save_enabled else 0)
    settings.setValue(f"{prefix}{KEY_INCLUDE_CURSOR}", 1 if normalized.include_cursor else 0)
    settings.setValue(f"{prefix}{KEY_SCROLL_DELAY_MS}", normalized.scroll_delay_ms)
    settings.setValue(f"{prefix}{KEY_DEBUG_FRAMES}", 1 if normalized.debug_frames else 0)
    settings.setValue(f"{prefix}{KEY_AUTO_OPEN_EDITOR}", 1 if normalized.auto_open_editor else 0)
    settings.setValue(f"{prefix}{KEY_CAPTURE_DELAY_S}", normalized.capture_delay_s)
    settings.sync()


def scroll_debug_dir(settings: QSettings) -> str | None:
    """Return debug dump directory when scroll frame dumps are enabled."""
    if not load_screenshot_settings(settings).debug_frames:
        return None
    from pathlib import Path

    return str(Path(__file__).resolve().parents[2] / ".intelag" / "reports" / "scroll_live")
