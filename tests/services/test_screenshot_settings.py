from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QColor, QImage

from services.screenshot.output_pipeline import deliver_capture, save_screenshot
from services.screenshot_settings import (
    ScreenshotSettings,
    load_screenshot_settings,
    save_screenshot_settings,
    scroll_debug_dir,
)


class _FakeClipboard:
    def __init__(self) -> None:
        self.image: QImage | None = None

    def setImage(self, image: QImage) -> None:  # noqa: N802 (mirrors QClipboard API)
        self.image = image


def test_scroll_debug_dir_off_by_default(tmp_path) -> None:
    ini = tmp_path / "qs.ini"
    settings = QSettings(str(ini), QSettings.Format.IniFormat)
    assert scroll_debug_dir(settings) is None


def test_save_screenshot_png(tmp_path) -> None:
    image = QImage(4, 4, QImage.Format.Format_ARGB32)
    image.fill(QColor("white").rgb())
    settings = ScreenshotSettings(output_dir=str(tmp_path), save_enabled=True, copy_enabled=False)
    path = save_screenshot(image, settings)
    assert path.exists()
    assert path.suffix == ".png"


def test_load_screenshot_settings_defaults() -> None:
    settings = QSettings()
    loaded = load_screenshot_settings(settings)
    assert loaded.copy_enabled is True
    assert loaded.debug_frames is False
    assert loaded.capture_delay_s == 0
    assert loaded.auto_open_editor is False


def test_screenshot_settings_round_trip(tmp_path) -> None:
    ini = tmp_path / "qs.ini"
    settings = QSettings(str(ini), QSettings.Format.IniFormat)
    save_screenshot_settings(
        settings,
        ScreenshotSettings(
            image_format="jpeg",
            save_enabled=True,
            scroll_delay_ms=600,
            capture_delay_s=3,
            auto_open_editor=True,
        ),
    )
    loaded = load_screenshot_settings(settings)
    assert loaded.image_format == "jpeg"
    assert loaded.save_enabled is True
    assert loaded.scroll_delay_ms == 600
    assert loaded.capture_delay_s == 3
    assert loaded.auto_open_editor is True


def test_capture_delay_clamped() -> None:
    assert ScreenshotSettings(capture_delay_s=99).normalized().capture_delay_s == 10
    assert ScreenshotSettings(capture_delay_s=-5).normalized().capture_delay_s == 0


def test_deliver_capture_copy_only() -> None:
    clipboard = _FakeClipboard()
    image = QImage(2, 2, QImage.Format.Format_ARGB32)
    image.fill(QColor("red").rgb())
    settings = ScreenshotSettings(copy_enabled=True, save_enabled=False)
    ok, path = deliver_capture(clipboard, image, settings)
    assert ok is True
    assert path is None
    assert clipboard.image is not None


def test_deliver_capture_save_only(tmp_path) -> None:
    clipboard = _FakeClipboard()
    image = QImage(2, 2, QImage.Format.Format_ARGB32)
    image.fill(QColor("blue").rgb())
    settings = ScreenshotSettings(
        output_dir=str(tmp_path),
        copy_enabled=False,
        save_enabled=True,
    )
    ok, path = deliver_capture(clipboard, image, settings)
    assert ok is True
    assert path is not None and path.exists()
    assert clipboard.image is None


def test_deliver_capture_dual_destination(tmp_path) -> None:
    clipboard = _FakeClipboard()
    image = QImage(2, 2, QImage.Format.Format_ARGB32)
    image.fill(QColor("green").rgb())
    settings = ScreenshotSettings(
        output_dir=str(tmp_path),
        copy_enabled=True,
        save_enabled=True,
    )
    ok, path = deliver_capture(clipboard, image, settings)
    assert ok is True
    assert path is not None and path.exists()
    assert clipboard.image is not None
