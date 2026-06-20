"""Scroll coordinator phase handlers (capture loop and stitching)."""

from __future__ import annotations

import logging

from PyQt6.QtCore import QSize

from services.screenshot.scroll_coordinator_methods import ScrollCoordinatorMethods
from services.screenshot.stitch_alignment import (
    _stride_notches,
    are_images_similar,
    find_vertical_offset,
    stitch_images,
)
from services.screenshot.window_targets import is_valid_capture_window

LOGGER = logging.getLogger(__name__)


class ScrollCoordinatorPhases(ScrollCoordinatorMethods):
    def _on_timer_tick(self) -> None:
        if getattr(self, "_cancel_requested", False):
            return
        if self._phase == self._PHASE_TO_TOP:
            self._scroll_to_top_step()
        elif self._phase == self._PHASE_CAPTURE:
            self._capture_step()

    def _scroll_to_top_step(self) -> None:
        """Drive the scroll target back to its top with upward wheel bursts.

        Doubles as a probe for a working scroll method: once content visibly
        moves, the current method is locked in for the capture phase.
        """
        if not is_valid_capture_window(self.hwnd, self._own_capture_hwnd()):
            self._fail("Target window is no longer available.")
            return

        frame = self._grab_frame()
        if frame is None:
            self._fail("Screen capture failed.")
            return

        moved = (
            self._top_last_frame is None
            or self._top_last_frame.size() != frame.size()
            or not are_images_similar(self._top_last_frame, frame)
        )
        click_to_activate = False
        if moved:
            if self._top_last_frame is not None:
                self.scroll_method_locked = True
            self._top_last_frame = frame
            self._top_bursts += 1
            if self._top_bursts > self.to_top_max_bursts:
                LOGGER.info("Scroll-to-top burst limit reached. Starting capture.")
                self._begin_capture()
                return
        elif not self.scroll_method_locked and self._advance_scroll_method():
            # The current method moved nothing; re-click to activate the pane
            # before trying the next one.
            LOGGER.info("Scroll-to-top is probing a fallback scroll method.")
            click_to_activate = True
        else:
            self._begin_capture()
            return

        self._activate_scroll_target(click=click_to_activate)
        if not self._send_scroll(upward=True, notches=self.to_top_notches):
            self._begin_capture()
            return
        self.timer.start(self.to_top_delay_ms)

    def _begin_capture(self) -> None:
        if not self.scroll_method_locked:
            self.scroll_method_index = 0
        self._top_last_frame = None
        self.images = []
        self.offsets = []
        self.last_size = QSize()
        self._px_per_notch = None
        self._last_sent_notches = self.scroll_notches
        self._phase = self._PHASE_CAPTURE
        self.progress.emit(self._PHASE_CAPTURE, 0)
        self.timer.start(self.initial_delay_ms)

    def _capture_step(self) -> None:
        if not is_valid_capture_window(self.hwnd, self._own_capture_hwnd()):
            LOGGER.info("Target window became invalid during scrolling screenshot.")
            self._finish()
            return

        new_img = self._grab_frame()
        if new_img is None:
            self._fail("Screen capture failed.")
            return

        if not self.images:
            self.images.append(new_img)
            self.last_size = new_img.size()
            self.progress.emit(self._PHASE_CAPTURE, len(self.images))
            self._scroll_and_continue()
            return

        if new_img.size() != self.last_size:
            LOGGER.info("Window resized during scrolling screenshot. Finishing.")
            self._finish()
            return

        if are_images_similar(self.images[-1], new_img):
            if len(self.images) == 1 and self._try_next_scroll_method():
                return
            LOGGER.info("Content similarity suggests bottom of window is reached.")
            self._finish()
            return

        expected_offset = None
        if self.offsets:
            sorted_offsets = sorted(self.offsets)
            expected_offset = sorted_offsets[len(sorted_offsets) // 2]
        elif not self._is_uia_method() and (
            self._px_per_notch is not None and self._last_sent_notches > 0
        ):
            expected_offset = max(1, round(self._px_per_notch * self._last_sent_notches))
        offset = find_vertical_offset(self.images[-1], new_img, expected_offset)
        if offset is None:
            LOGGER.warning("Stitching alignment failed. Finishing.")
            self._finish()
            return

        self.images.append(new_img)
        self.offsets.append(offset)
        self.progress.emit(self._PHASE_CAPTURE, len(self.images))
        if not self._is_uia_method():
            per_notch = offset / max(1, self._last_sent_notches)
            if self._px_per_notch is None:
                self._px_per_notch = per_notch
            else:
                self._px_per_notch = (self._px_per_notch + per_notch) / 2.0

        if len(self.images) >= self.max_pages:
            LOGGER.info("Reached maximum page limit.")
            self._finish()
            return

        self._scroll_and_continue()

    def _scroll_and_continue(self) -> None:
        # UIA ScrollPattern path: cursor-free, and it knows exactly when the
        # content is at the bottom, so no activation click or stride math.
        if self._is_uia_method():
            if self._uia_target is not None and self._uia_target.at_bottom():
                LOGGER.info("UIA reports scroll position at bottom. Finishing.")
                self._finish()
                return
            if not self._send_scroll():
                self._finish()
                return
            self.timer.start(self.scroll_delay_ms)
            return

        self._activate_scroll_target()
        notches = _stride_notches(
            self.last_size.height() if not self.last_size.isEmpty() else 0,
            self._px_per_notch,
            self.step_margin_px,
            self.scroll_notches,
        )
        self._last_sent_notches = notches
        if not self._send_scroll(notches=notches):
            self._finish()
            return
        self.timer.start(self.scroll_delay_ms)

    def _finish(self) -> None:
        self.timer.stop()
        self._phase = self._PHASE_IDLE
        self._restore_cursor()
        if not self.images:
            self.failed.emit("No frames captured.")
            return

        stitched = stitch_images(self.images, self.offsets)
        self._dump_debug(stitched)
        if stitched is None:
            self.failed.emit("Stitching failed.")
            return
        if len(self.images) <= 1:
            LOGGER.warning(
                "Scrolling capture produced a single frame — the target never "
                "scrolled. Returning a static screenshot of the visible area."
            )
        self.finished.emit(stitched)

    def _fail(self, reason: str) -> None:
        self.timer.stop()
        self._phase = self._PHASE_IDLE
        self._restore_cursor()
        self.failed.emit(reason)
