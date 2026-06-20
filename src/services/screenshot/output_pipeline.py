"""Save screenshot images to disk."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from PyQt6.QtGui import QImage

from services.screenshot_settings import ScreenshotSettings

LOGGER = logging.getLogger(__name__)


def _unique_path(directory: Path, stem: str, suffix: str) -> Path:
    candidate = directory / f"{stem}{suffix}"
    if not candidate.exists():
        return candidate
    for index in range(1, 10_000):
        candidate = directory / f"{stem}_{index:04d}{suffix}"
        if not candidate.exists():
            return candidate
    raise OSError(f"Could not allocate unique filename in {directory}")


def save_screenshot(image: QImage, settings: ScreenshotSettings) -> Path:
    """Write ``image`` to disk using screenshot settings."""
    normalized = settings.normalized()
    directory = Path(normalized.effective_output_dir())
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if normalized.image_format == "jpeg":
        path = _unique_path(directory, f"screenshot_{stamp}", ".jpg")
        if not image.save(str(path), "JPEG", 92):
            raise OSError(f"Failed to write JPEG to {path}")
    else:
        path = _unique_path(directory, f"screenshot_{stamp}", ".png")
        if not image.save(str(path), "PNG"):
            raise OSError(f"Failed to write PNG to {path}")
    LOGGER.info("Saved screenshot to %s", path)
    return path


def copy_image_to_clipboard(clipboard, image: QImage) -> None:
    clipboard.setImage(image.convertToFormat(QImage.Format.Format_RGB32))


def deliver_capture(
    clipboard,
    pixmap_or_image,
    settings: ScreenshotSettings,
) -> tuple[bool, Path | None]:
    """Route a capture to clipboard and/or file per settings."""
    from PyQt6.QtGui import QPixmap

    if isinstance(pixmap_or_image, QPixmap):
        image = pixmap_or_image.toImage()
    else:
        image = pixmap_or_image
    if image.isNull():
        return (False, None)

    normalized = settings.normalized()
    saved: Path | None = None
    if normalized.save_enabled:
        saved = save_screenshot(image, normalized)
    if normalized.copy_enabled:
        copy_image_to_clipboard(clipboard, image)
    return (normalized.copy_enabled or saved is not None, saved)
