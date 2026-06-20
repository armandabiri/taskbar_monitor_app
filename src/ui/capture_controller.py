"""Screenshot capture flows (menu, hotkeys, overlays)."""

from __future__ import annotations

import logging
from typing import Callable

from PyQt6.QtCore import QRect, QTimer
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication

from core.config import APP_NAME
from services.notification_service import NotificationService
from services.screenshot.output_pipeline import deliver_capture
from services.screenshot_service import (
    ScrollingScreenshotCoordinator,
    get_foreground_window,
    grab_screen_region,
    grab_screen_snapshot,
    grab_virtual_desktop,
    grab_window_pixmap,
    is_valid_capture_window,
)
from services.screenshot_settings import load_screenshot_settings, scroll_debug_dir
from ui.capture_collection import (
    CaptureCollection,
    CaptureCollectionMixin,
    CollectionBadge,
    SequentialImagePaster,
)
from ui.capture_delay_overlay import run_with_delay
from ui.capture_selectors import CaptureSelectorsMixin
from ui.scroll_capture_progress import ScrollCaptureProgress

LOGGER = logging.getLogger(__name__)


class CaptureController(CaptureSelectorsMixin, CaptureCollectionMixin):
    """Owns screenshot mode entry, scroll coordinator, and output routing."""

    def __init__(self, monitor) -> None:
        self._monitor = monitor
        shot = load_screenshot_settings(monitor.settings)
        self.scrolling_coordinator = ScrollingScreenshotCoordinator(
            monitor,
            scroll_delay_ms=shot.scroll_delay_ms,
            debug_dir=scroll_debug_dir(monitor.settings),
        )
        self.scrolling_coordinator.finished.connect(self._on_scrolling_capture_finished)
        self.scrolling_coordinator.failed.connect(self._on_scrolling_capture_failed)
        self.scrolling_coordinator.cancelled.connect(self._on_scrolling_capture_cancelled)
        self._scroll_progress = ScrollCaptureProgress(monitor)
        self._scroll_progress.bind_coordinator(self.scrolling_coordinator)
        self._scroll_progress.cancel_clicked.connect(self._on_scroll_progress_cancel)
        self._last_image: QImage | None = None
        self._pinned: list = []
        self._delay_overlay = None
        self._collection = CaptureCollection(monitor)
        self._collection_badge = CollectionBadge(monitor)
        self._collection_badge.bind(self._collection, monitor)
        self._paster = SequentialImagePaster(monitor)
        self._paster.finished.connect(self._on_paste_finished)

    def reload_scroll_settings(self) -> None:
        """Apply screenshot settings to the scroll coordinator."""
        shot = load_screenshot_settings(self._monitor.settings)
        self.scrolling_coordinator.scroll_delay_ms = shot.scroll_delay_ms
        self.scrolling_coordinator.debug_dir = scroll_debug_dir(self._monitor.settings)

    # --- delay scheduling (wraps every capture mode) ---------------------

    def _launch(self, run_mode: Callable[[], None]) -> None:
        """Run a capture mode now, or after an on-screen countdown when set."""
        delay = load_screenshot_settings(self._monitor.settings).capture_delay_s
        if delay <= 0:
            run_mode()
            return
        self._delay_overlay = run_with_delay(delay, run_mode, parent=None)

    def capture_regional(self) -> None:
        self._launch(self._capture_regional_now)

    def capture_element(self) -> None:
        self._launch(self._capture_element_now)

    def capture_scrolling(self) -> None:
        self._launch(self._capture_scrolling_now)

    def capture_active_window(self) -> None:
        self._launch(self._capture_active_window_now)

    def capture_full_screen(self) -> None:
        self._launch(self._capture_full_screen_now)

    def capture_full_desktop(self) -> None:
        self._launch(self._capture_full_desktop_now)

    # --- scroll progress / coordinator slots -----------------------------

    def _on_scroll_progress_cancel(self) -> None:
        self.scrolling_coordinator.cancel()
        self._scroll_progress.hide()
        self._restore_after_screenshot()

    def _on_scrolling_capture_cancelled(self) -> None:
        self._scroll_progress.hide()

    def _restore_after_screenshot(self) -> None:
        self._monitor.show()
        self._monitor.raise_()
        self._monitor._apply_win32_topmost()

    def _close_screenshot_selectors(self, *, restore: bool) -> None:
        for selector in list(self._monitor.selectors):
            selector.close()
        self._monitor.selectors.clear()
        if restore:
            self._restore_after_screenshot()

    def _screen_by_name(self, screen_name: str):
        for screen in QApplication.screens():
            if screen.name() == screen_name:
                return screen
        return QApplication.primaryScreen()

    def _store_last_capture_region(self, screen, local_rect: QRect) -> None:
        self._monitor.last_capture_rect = QRect(local_rect)
        self._monitor.last_capture_screen_name = screen.name()
        self._monitor.settings.setValue("last_capture_rect_x", local_rect.x())
        self._monitor.settings.setValue("last_capture_rect_y", local_rect.y())
        self._monitor.settings.setValue("last_capture_rect_w", local_rect.width())
        self._monitor.settings.setValue("last_capture_rect_h", local_rect.height())
        self._monitor.settings.setValue("last_capture_screen_name", screen.name())
        self._monitor.settings.sync()

    # --- output routing (editor, clipboard, save, pin) -------------------

    def _finalize_image(self, image: QImage) -> QImage | None:
        """Optionally run the post-capture editor; None means the user cancelled."""
        if not load_screenshot_settings(self._monitor.settings).auto_open_editor:
            return image
        from ui.screenshot_editor_dialog import ScreenshotEditorDialog

        edited = ScreenshotEditorDialog.edit(image, parent=self._monitor)
        return edited

    def _deliver_image(self, image: QImage, failure_message: str) -> bool:
        try:
            if image.isNull():
                LOGGER.warning("%s", failure_message)
                NotificationService.notify(APP_NAME, failure_message)
                return False
            settings = load_screenshot_settings(self._monitor.settings)
            collecting = self._collection.active
            if not settings.copy_enabled and not settings.save_enabled and not collecting:
                NotificationService.notify(
                    APP_NAME,
                    "Enable clipboard and/or file save in Screenshot Settings.",
                )
                return False
            final = self._finalize_image(image)
            if final is None or final.isNull():
                return False
            self._last_image = QImage(final)
            if collecting:
                self._collection.add(final)
            ok = True
            if settings.copy_enabled or settings.save_enabled:
                ok, saved_path = deliver_capture(self._monitor.clipboard, final, settings)
                if saved_path is not None:
                    LOGGER.info("Screenshot saved to %s", saved_path)
            return ok
        finally:
            self._restore_after_screenshot()

    def _deliver_pixmap(self, pixmap, failure_message: str) -> bool:
        if pixmap.isNull():
            return self._deliver_image(QImage(), failure_message)
        return self._deliver_image(pixmap.toImage(), failure_message)

    def _copy_pixmap_to_clipboard(self, pixmap, failure_message: str) -> bool:
        return self._deliver_pixmap(pixmap, failure_message)

    def _copy_region_to_clipboard(self, screen, local_rect: QRect) -> bool:
        QApplication.processEvents()
        pixmap = grab_screen_region(screen, local_rect)
        return self._copy_pixmap_to_clipboard(pixmap, "Failed to capture screenshot region.")

    def pin_last_capture(self) -> None:
        """Pin the most recent capture on screen as an always-on-top overlay."""
        if self._last_image is None or self._last_image.isNull():
            NotificationService.notify(APP_NAME, "No capture available to pin yet.")
            return
        from ui.pinned_capture_overlay import PinnedCaptureOverlay

        overlay = PinnedCaptureOverlay.pin(QImage(self._last_image))
        self._pinned.append(overlay)
        overlay.destroyed.connect(lambda *_: self._forget_pin(overlay))

    def _forget_pin(self, overlay) -> None:
        if overlay in self._pinned:
            self._pinned.remove(overlay)

    # --- simple (non-overlay) capture modes ------------------------------

    def capture_last_region(self) -> None:
        """Repeat screenshot of the last captured region."""
        if self._monitor.last_capture_rect is None or not self._monitor.last_capture_screen_name:
            NotificationService.notify(APP_NAME, "No previous regional screenshot found to repeat.")
            return

        target_screen = self._screen_by_name(str(self._monitor.last_capture_screen_name))
        if target_screen is None:
            NotificationService.notify(APP_NAME, "No screen found for repeating screenshot.")
            return

        local_rect = QRect(self._monitor.last_capture_rect)
        self._monitor.hide()
        QApplication.processEvents()
        QTimer.singleShot(80, lambda: self._copy_region_to_clipboard(target_screen, local_rect))

    def _capture_active_window_now(self) -> None:
        """Capture the currently active foreground window to the clipboard."""
        hwnd = get_foreground_window()

        try:
            own_hwnd = int(self._monitor.winId())
        except (AttributeError, ValueError):
            own_hwnd = 0

        if not is_valid_capture_window(hwnd, own_hwnd):
            LOGGER.warning("Active window is invalid for screenshot.")
            NotificationService.notify(APP_NAME, "No active window found for screenshot.")
            return

        self._monitor.hide()
        QApplication.processEvents()
        QTimer.singleShot(50, lambda: self._capture_window_to_clipboard(hwnd))

    def _capture_window_to_clipboard(self, hwnd: int) -> None:
        pixmap = grab_window_pixmap(hwnd)
        if pixmap.isNull():
            LOGGER.warning("Active window screenshot returned a null pixmap.")
            NotificationService.notify(APP_NAME, "Failed to capture active window.")
            self._restore_after_screenshot()
            return
        self._deliver_pixmap(pixmap, "Failed to capture active window.")

    def _capture_full_screen_now(self) -> None:
        """Capture the screen under the cursor to clipboard and/or disk."""
        from PyQt6.QtGui import QCursor, QGuiApplication

        cursor_screen = QGuiApplication.screenAt(QCursor.pos())
        screen = cursor_screen or QGuiApplication.primaryScreen()
        if screen is None:
            NotificationService.notify(APP_NAME, "No screen available for screenshot.")
            return
        self._monitor.hide()
        QApplication.processEvents()
        QTimer.singleShot(50, lambda: self._deliver_pixmap(
            grab_screen_snapshot(screen), "Failed to capture full screen."
        ))

    def _capture_full_desktop_now(self) -> None:
        """Capture every monitor stitched into a single wide image."""
        self._monitor.hide()
        QApplication.processEvents()
        QTimer.singleShot(50, lambda: self._deliver_image(
            grab_virtual_desktop(), "Failed to capture the desktop."
        ))

    # --- scrolling capture completion ------------------------------------

    def _on_scrolling_capture_finished(self, image: QImage) -> None:
        """Called when scrolling capture sequence completes successfully."""
        self._scroll_progress.hide()
        self._deliver_image(image, "Scrolling screenshot produced no image.")

    def _on_scrolling_capture_failed(self, reason: str) -> None:
        """Called when scrolling capture sequence fails."""
        self._scroll_progress.hide()
        NotificationService.notify(APP_NAME, f"Scrolling screenshot failed: {reason}")
        self._restore_after_screenshot()
