"""Scroll coordinator method handlers (scroll plan, input, cursor, debug)."""

from __future__ import annotations

import json
import logging
import os

from PyQt6.QtGui import QImage

from services.screenshot.scroll_input import (
    _click_native_point,
    _post_wheel_scroll,
    _restore_cursor_position,
    _send_wheel_scroll_at,
)
from services.screenshot.window_targets import (
    _focus_child_window,
    _force_foreground,
)

LOGGER = logging.getLogger(__name__)


class ScrollCoordinatorMethods:
    """Scroll-method selection, wheel/UIA dispatch, cursor restore, debug dump."""

    def _build_scroll_plan(self) -> list[tuple[str, tuple[int, int]]]:
        """Ordered list of (method, native-point) scroll attempts.

        The UIA ScrollPattern (when the target exposes one) is tried first: it
        scrolls without moving the cursor and reports exact position. PostMessage
        to the control is next (cheap, no cursor move) unless input injection is
        forced, then SendInput wheel at each coordinate candidate. Probing walks
        this list until one moves content.
        """
        plan: list[tuple[str, tuple[int, int]]] = []
        if self._uia_target is not None and self.scroll_point_native is not None:
            plan.append(("uia", self.scroll_point_native))
        if (
            not self.prefer_input_injection
            and self.scroll_hwnd
            and self.scroll_point_native is not None
        ):
            plan.append(("post", self.scroll_point_native))
        for point in self.scroll_point_candidates:
            plan.append(("input", point))
        return plan

    def _is_uia_method(self) -> bool:
        entry = self._current_plan_entry()
        return entry is not None and entry[0] == "uia" and self._uia_target is not None

    def _uia_step_down(self) -> bool:
        """Advance the UIA scroll target down by nearly one viewport.

        Steps by a fraction of the view size so adjacent frames keep enough
        overlap to stitch, and clamps to 100% so the last partial page is still
        captured. Returns False when the content can advance no further.
        """
        target = self._uia_target
        if target is None:
            return False
        percent = target.vertical_percent()
        view = target.vertical_view_size()
        if percent is None or view is None:
            return target.scroll_down_increment()
        step = max(1.0, view * (1.0 - self._uia_overlap))
        next_percent = min(100.0, percent + step)
        if next_percent <= percent + 0.01:
            return False
        return target.set_vertical_percent(next_percent)

    def _current_plan_entry(self) -> tuple[str, tuple[int, int]] | None:
        if not self.scroll_plan:
            return None
        index = min(self.scroll_method_index, len(self.scroll_plan) - 1)
        return self.scroll_plan[index]

    def _advance_scroll_method(self) -> bool:
        if self.scroll_method_index >= len(self.scroll_plan) - 1:
            return False
        self.scroll_method_index += 1
        return True

    def _try_next_scroll_method(self) -> bool:
        if self.scroll_method_locked or not self._advance_scroll_method():
            return False
        LOGGER.info("Retrying scrolling screenshot with fallback scroll method.")
        self._activate_scroll_target(click=True)
        self._scroll_and_continue()
        return True

    def _send_scroll(self, *, upward: bool = False, notches: int | None = None) -> bool:
        count = self.scroll_notches if notches is None else notches
        entry = self._current_plan_entry()
        if entry is None:
            return False
        kind, (x, y) = entry
        if kind == "uia" and self._uia_target is not None:
            if upward:
                return self._uia_target.scroll_to_top()
            return self._uia_step_down()
        if kind == "post":
            return _post_wheel_scroll(self.scroll_hwnd, x, y, notches=count, upward=upward)
        self._cursor_moved = True
        return _send_wheel_scroll_at(x, y, notches=count, upward=upward)

    def _scroll_point_for_method(self) -> tuple[int, int] | None:
        """Native point the current scroll method targets.

        The activation click must land on the same point the wheel will use —
        otherwise the click can miss the pane on DPI-scaled/multi-monitor setups
        and the target is never activated for that method.
        """
        entry = self._current_plan_entry()
        if entry is not None:
            return entry[1]
        return self.scroll_point_native

    def _activate_scroll_target(self, *, click: bool = False) -> None:
        _force_foreground(self.hwnd)
        # The UIA path drives ScrollPattern directly, so it needs neither a
        # focus-stealing child focus nor an activation click (both add jank).
        if self._is_uia_method() and not click:
            return
        if self.scroll_hwnd:
            _focus_child_window(self.scroll_hwnd)
        point = self._scroll_point_for_method()
        if click and point is not None:
            _click_native_point(*point)
            self._cursor_moved = True
            if self.scroll_hwnd:
                _focus_child_window(self.scroll_hwnd)

    def _restore_cursor(self) -> None:
        if not self._cursor_moved or self._cursor_origin is None:
            return
        _restore_cursor_position(*self._cursor_origin)
        self._cursor_moved = False

    def _dump_debug(self, stitched: QImage | None) -> None:
        """Save raw frames, the stitched result, and metrics when debugging.

        Enabled by setting the TASKBAR_SCROLL_DEBUG_DIR environment variable;
        the dump shows whether frames actually moved between scroll steps.
        """
        debug_dir = os.environ.get("TASKBAR_SCROLL_DEBUG_DIR") or self.debug_dir
        if not debug_dir:
            return
        try:
            os.makedirs(debug_dir, exist_ok=True)
            for index, image in enumerate(self.images):
                image.save(os.path.join(debug_dir, f"frame_{index:03d}.png"))
            if stitched is not None:
                stitched.save(os.path.join(debug_dir, "stitched.png"))
            meta = {
                "frame_count": len(self.images),
                "offsets": self.offsets,
                "px_per_notch": self._px_per_notch,
                "scroll_method_index": self.scroll_method_index,
                "scroll_method_locked": self.scroll_method_locked,
                "uia_scroll": self._uia_target is not None,
                "scroll_hwnd": self.scroll_hwnd,
                "capture_hwnd": self.hwnd,
                "viewport_rect": [
                    self.viewport_rect.x(),
                    self.viewport_rect.y(),
                    self.viewport_rect.width(),
                    self.viewport_rect.height(),
                ],
            }
            with open(os.path.join(debug_dir, "debug.json"), "w", encoding="utf-8") as handle:
                json.dump(meta, handle, indent=2)
            LOGGER.info("Wrote scroll capture debug dump to %s", debug_dir)
        except OSError as exc:
            LOGGER.warning("Failed to write scroll debug dump: %s", exc)
