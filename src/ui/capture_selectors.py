"""Interactive overlay capture flows (region, element, scrolling)."""

from __future__ import annotations

import logging

from PyQt6.QtCore import QPoint, QRect, QTimer
from PyQt6.QtWidgets import QApplication

from core.config import APP_NAME
from services.notification_service import NotificationService
from services.screenshot_service import (
    crop_screen_snapshot,
    element_rects_for_screen,
    grab_screen_snapshot,
    is_valid_capture_window,
    window_selections_from_qt_point,
)

LOGGER = logging.getLogger(__name__)


class CaptureSelectorsMixin:
    """Per-screen interactive selector flows for the capture controller."""

    def _capture_regional_now(self) -> None:
        """Trigger regional screenshot using a custom interactive capture overlay."""
        self._close_screenshot_selectors(restore=False)
        self._monitor.hide()
        QApplication.processEvents()
        QTimer.singleShot(50, self._show_region_selectors)

    def _show_region_selectors(self) -> None:
        from PyQt6.QtGui import QGuiApplication

        from ui.screenshot_overlay import RegionSelector

        screens = QGuiApplication.screens()
        if not screens:
            NotificationService.notify(APP_NAME, "No screens available for screenshot.")
            self._restore_after_screenshot()
            return

        screen_snapshots = []
        for screen in screens:
            snapshot = grab_screen_snapshot(screen)
            if not snapshot.isNull():
                screen_snapshots.append((screen, snapshot))

        if not screen_snapshots:
            NotificationService.notify(APP_NAME, "Failed to capture screen for selection.")
            self._restore_after_screenshot()
            return

        def on_selected(local_rect: QRect, screen, snapshot) -> None:
            selected_rect = QRect(local_rect)
            selected_screen = screen
            selected_snapshot = snapshot
            self._close_screenshot_selectors(restore=False)

            def capture_after_overlay_closes() -> None:
                pixmap = crop_screen_snapshot(
                    selected_snapshot,
                    selected_screen,
                    selected_rect,
                )
                copied = self._copy_pixmap_to_clipboard(
                    pixmap,
                    "Failed to capture screenshot region.",
                )
                if copied and not selected_rect.isEmpty():
                    self._store_last_capture_region(selected_screen, selected_rect)

            QTimer.singleShot(20, capture_after_overlay_closes)

        def on_cancelled() -> None:
            self._close_screenshot_selectors(restore=True)

        for screen, snapshot in screen_snapshots:
            selector = RegionSelector(screen, snapshot, on_selected, on_cancelled)
            self._monitor.selectors.append(selector)
            selector.show()

    def _capture_element_now(self) -> None:
        """Trigger smart element capture using a hover-highlight overlay."""
        self._close_screenshot_selectors(restore=False)
        self._monitor.hide()
        QApplication.processEvents()
        QTimer.singleShot(80, self._show_element_selectors)

    def _show_element_selectors(self) -> None:
        from PyQt6.QtGui import QGuiApplication

        from services.uia_service import collect_element_rects
        from ui.screenshot_overlay import ElementSelector

        screens = QGuiApplication.screens()
        if not screens:
            NotificationService.notify(APP_NAME, "No screens available for screenshot.")
            self._restore_after_screenshot()
            return

        # Resolve element rectangles once, while the desktop is still uncovered,
        # then hit-test them locally on each per-screen overlay.
        native_rects = collect_element_rects()
        screen_data = []
        for screen in screens:
            snapshot = grab_screen_snapshot(screen)
            if not snapshot.isNull():
                rects = element_rects_for_screen(screen, native_rects)
                screen_data.append((screen, snapshot, rects))

        if not screen_data:
            NotificationService.notify(APP_NAME, "Failed to capture screen for selection.")
            self._restore_after_screenshot()
            return

        def on_selected(local_rect: QRect, screen, snapshot) -> None:
            selected_rect = QRect(local_rect)
            selected_screen = screen
            selected_snapshot = snapshot
            self._close_screenshot_selectors(restore=False)

            def capture_after_overlay_closes() -> None:
                pixmap = crop_screen_snapshot(
                    selected_snapshot,
                    selected_screen,
                    selected_rect,
                )
                copied = self._copy_pixmap_to_clipboard(
                    pixmap,
                    "Failed to capture element.",
                )
                if copied and not selected_rect.isEmpty():
                    self._store_last_capture_region(selected_screen, selected_rect)

            QTimer.singleShot(20, capture_after_overlay_closes)

        def on_cancelled() -> None:
            self._close_screenshot_selectors(restore=True)

        for screen, snapshot, rects in screen_data:
            selector = ElementSelector(screen, snapshot, rects, on_selected, on_cancelled)
            self._monitor.selectors.append(selector)
            selector.show()

    def _capture_scrolling_now(self) -> None:
        """Trigger scrolling screenshot by first letting the user click a target window."""
        self._close_screenshot_selectors(restore=False)
        self._monitor.hide()
        QApplication.processEvents()
        QTimer.singleShot(50, self._show_scroll_selectors)

    def _show_scroll_selectors(self) -> None:
        from PyQt6.QtGui import QGuiApplication

        from ui.screenshot_overlay import ScrollSelector

        screens = QGuiApplication.screens()
        if not screens:
            NotificationService.notify(APP_NAME, "No screens available for screenshot.")
            self._restore_after_screenshot()
            return

        def on_selected(
            global_pos: QPoint,
            viewport_rect: QRect | None,
            viewport_screen,
        ) -> None:
            clicked_pos = QPoint(global_pos)
            selected_viewport_rect = QRect(viewport_rect) if viewport_rect is not None else QRect()
            selected_viewport_screen = viewport_screen
            selector_hwnds: set[int] = set()
            for selector in self._monitor.selectors:
                try:
                    selector_hwnds.add(int(selector.winId()))
                except (AttributeError, ValueError):
                    pass
            self._close_screenshot_selectors(restore=False)

            def start_after_overlay_closes() -> None:
                try:
                    own_hwnd = int(self._monitor.winId())
                except (AttributeError, ValueError):
                    own_hwnd = 0
                excluded_hwnds = set(selector_hwnds)
                if own_hwnd:
                    excluded_hwnds.add(own_hwnd)

                for selection in window_selections_from_qt_point(clicked_pos):
                    if (
                        selection.capture_hwnd in excluded_hwnds
                        or selection.scroll_hwnd in excluded_hwnds
                    ):
                        continue
                    if not is_valid_capture_window(selection.capture_hwnd, own_hwnd):
                        continue
                    if self.scrolling_coordinator.start(
                        selection.capture_hwnd,
                        selection.scroll_hwnd,
                        selected_viewport_screen,
                        selected_viewport_rect,
                    ):
                        self._scroll_progress.show_near_parent(self._monitor)
                    return

                NotificationService.notify(APP_NAME, "No window found at clicked location.")
                self._restore_after_screenshot()

            QTimer.singleShot(120, start_after_overlay_closes)

        def on_cancelled() -> None:
            self._close_screenshot_selectors(restore=True)

        for screen in screens:
            selector = ScrollSelector(screen, on_selected, on_cancelled)
            self._monitor.selectors.append(selector)
            selector.show()
