"""Timed scrolling screenshot orchestration."""

from __future__ import annotations

import logging

from PyQt6.QtCore import QObject, QRect, QSize, QTimer, pyqtSignal
from PyQt6.QtGui import QImage, QScreen
from PyQt6.QtWidgets import QApplication

from services import uia_service
from services.screenshot.scroll_coordinator_phases import ScrollCoordinatorPhases
from services.screenshot.win32_capture import grab_screen_region, grab_window_pixmap
from services.screenshot.win32_common import _get_user32
from services.screenshot.window_geometry import (
    hwnd_client_screen_local_rect,
    native_rect_to_screen_local_rect,
    screen_local_rect_center_to_global,
    screen_local_rect_center_to_native,
)
from services.screenshot.window_targets import (
    _current_cursor_native_point,
    get_foreground_window,
    is_valid_capture_window,
)

LOGGER = logging.getLogger(__name__)


class ScrollingScreenshotCoordinator(ScrollCoordinatorPhases, QObject):

    """Orchestrates scrolling screenshots step-by-step using a QTimer.

    The capture runs in two phases: the scroll target is first driven back to
    its top with upward wheel bursts (probing for a scroll method that
    actually moves the content), then frames are captured while scrolling
    down until the content stops changing, and stitched into one image.
    """

    finished = pyqtSignal(QImage)
    failed = pyqtSignal(str)
    progress = pyqtSignal(str, int)
    cancelled = pyqtSignal()

    _PHASE_IDLE = "idle"
    _PHASE_TO_TOP = "to_top"
    _PHASE_CAPTURE = "capture"

    def __init__(
        self,
        parent_widget,
        max_pages: int = 60,
        scroll_delay_ms: int = 380,
        initial_delay_ms: int = 180,
        allow_self_capture: bool = False,
        scroll_notches: int = 2,
        step_margin_px: int = 100,
        to_top_max_bursts: int = 60,
        to_top_delay_ms: int = 170,
        to_top_notches: int = 15,
        debug_dir: str | None = None,
        prefer_input_injection: bool = False,
    ) -> None:
        super().__init__(parent_widget)
        self.parent_widget = parent_widget
        self.max_pages = max_pages
        self.scroll_delay_ms = scroll_delay_ms
        self.initial_delay_ms = initial_delay_ms
        self.allow_self_capture = allow_self_capture
        self.debug_dir = debug_dir
        # Skip the (async, unreliable) PostMessage method and scroll purely with
        # SendInput cursor-wheel injection — the path that works on Chromium/
        # Electron targets like VS Code and browsers.
        self.prefer_input_injection = prefer_input_injection
        self.scroll_notches = scroll_notches
        self.step_margin_px = step_margin_px
        self.to_top_max_bursts = to_top_max_bursts
        self.to_top_delay_ms = to_top_delay_ms
        self.to_top_notches = to_top_notches

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._on_timer_tick)

        self.hwnd = 0
        self.scroll_hwnd = 0
        self.viewport_screen: QScreen | None = None
        self.viewport_rect = QRect()
        self.scroll_point_native: tuple[int, int] | None = None
        self.scroll_point_candidates: list[tuple[int, int]] = []
        self.scroll_plan: list[tuple[str, tuple[int, int]]] = []
        self.scroll_method_index = 0
        self.scroll_method_locked = False
        self.images: list[QImage] = []
        self.offsets: list[int] = []
        self.last_size = QSize()
        self._phase = self._PHASE_IDLE
        self._top_last_frame: QImage | None = None
        self._top_bursts = 0
        self._cursor_origin: tuple[int, int] | None = None
        self._cursor_moved = False
        self._px_per_notch: float | None = None
        self._last_sent_notches = scroll_notches
        # UIA ScrollPattern target: when present, scrolling is driven cursor-free
        # and termination uses the exact scroll percent instead of image guesses.
        self._uia_target: uia_service.UiaScrollTarget | None = None
        # Fraction of a viewport kept as overlap between UIA steps so adjacent
        # frames always share enough content to stitch reliably.
        self._uia_overlap = 0.18
        self._cancel_requested = False

    def cancel(self) -> None:
        """Abort an in-flight scroll capture and restore cursor state."""
        if self._phase == self._PHASE_IDLE:
            return
        self._cancel_requested = True
        self.timer.stop()
        self._phase = self._PHASE_IDLE
        self._restore_cursor()
        self.cancelled.emit()

    def start(
        self,
        target_hwnd: int = 0,
        scroll_hwnd: int = 0,
        viewport_screen: QScreen | None = None,
        viewport_rect: QRect | None = None,
    ) -> bool:
        """Initialize and start the scrolling screenshot capture sequence."""
        self._cancel_requested = False
        user32 = _get_user32()
        if user32 is None:
            self.failed.emit("Windows screen capture APIs are unavailable.")
            return False

        self.hwnd = target_hwnd or get_foreground_window()
        self.scroll_hwnd = scroll_hwnd or self.hwnd
        self.viewport_screen = viewport_screen
        self.viewport_rect = QRect(viewport_rect) if viewport_rect is not None else QRect()
        self.scroll_point_native = None
        self.scroll_point_candidates = []
        self.scroll_method_index = 0
        self.scroll_method_locked = False
        self._uia_target = None
        user_provided_viewport = (
            viewport_rect is not None and not QRect(viewport_rect).isEmpty()
        )

        if not is_valid_capture_window(self.hwnd, self._own_capture_hwnd()):
            self.failed.emit("Selected window is invalid for scrolling screenshot.")
            return False

        if self.viewport_screen is None or self.viewport_rect.isEmpty():
            viewport = (
                hwnd_client_screen_local_rect(self.scroll_hwnd)
                or hwnd_client_screen_local_rect(self.hwnd)
            )
            if viewport is not None:
                self.viewport_screen, self.viewport_rect = viewport

        if self.viewport_screen is None or self.viewport_rect.isEmpty():
            self.failed.emit("Could not determine scrollable capture area.")
            return False

        self.scroll_point_native = screen_local_rect_center_to_native(
            self.viewport_screen,
            self.viewport_rect,
        )
        # Ask UIA for the actual scrollable element under the target point. When
        # found it both fixes "captures the whole window" (we crop to the real
        # scroll container) and unlocks cursor-free, position-aware scrolling.
        self._acquire_uia_target(user_provided_viewport)
        qt_global_center = screen_local_rect_center_to_global(
            self.viewport_screen,
            self.viewport_rect,
        )
        self.scroll_point_candidates = [self.scroll_point_native]
        if qt_global_center != self.scroll_point_native:
            self.scroll_point_candidates.append(qt_global_center)
        self.scroll_plan = self._build_scroll_plan()

        self._cursor_origin = _current_cursor_native_point()
        self._cursor_moved = False
        # A real click is what activates Chromium/Electron panes so a synthetic
        # wheel is honored; without it those targets silently ignore scrolling.
        # The UIA ScrollPattern path needs no click (and clicking would only add
        # cursor jank), so suppress it when UIA is driving.
        self._activate_scroll_target(click=self._uia_target is None)
        QApplication.processEvents()

        self.images = []
        self.offsets = []
        self.last_size = QSize()
        self._top_last_frame = None
        self._top_bursts = 0
        self._phase = self._PHASE_TO_TOP
        self.progress.emit(self._PHASE_TO_TOP, 0)
        self.timer.start(self.initial_delay_ms)
        return True

    def _own_capture_hwnd(self) -> int:
        try:
            own_hwnd = int(self.parent_widget.winId())
        except (AttributeError, ValueError):
            own_hwnd = 0
        return 0 if self.allow_self_capture else own_hwnd

    def _acquire_uia_target(self, user_provided_viewport: bool) -> None:
        """Resolve the UIA scrollable element under the target point, if any.

        When the viewport was auto-derived (not drag-selected by the user), the
        scrollable element's own bounds replace it so the capture frames the real
        scroll container instead of the whole window and its chrome.
        """
        if not uia_service.is_available() or self.scroll_point_native is None:
            return

        target = uia_service.scroll_target_from_native_point(*self.scroll_point_native)
        if target is None or not target.vertically_scrollable():
            return
        self._uia_target = target

        if user_provided_viewport:
            return
        rect = target.bounding_rect
        if rect is None:
            return
        mapped = native_rect_to_screen_local_rect(rect)
        if mapped is None:
            return
        screen, local_rect = mapped
        if local_rect.width() < 40 or local_rect.height() < 40:
            return
        self.viewport_screen = screen
        self.viewport_rect = local_rect
        self.scroll_point_native = screen_local_rect_center_to_native(screen, local_rect)
        LOGGER.info(
            "Scrolling capture using UIA scroll container viewport %sx%s.",
            local_rect.width(),
            local_rect.height(),
        )

    def _grab_frame(self) -> QImage | None:
        if self.viewport_screen is not None and not self.viewport_rect.isEmpty():
            pixmap = grab_screen_region(self.viewport_screen, self.viewport_rect)
        else:
            pixmap = grab_window_pixmap(self.hwnd)
        if pixmap.isNull():
            return None
        image = pixmap.toImage()
        return None if image.isNull() else image
