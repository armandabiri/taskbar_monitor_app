"""Lazy UI Automation (UIA) helpers used by the smart-capture features.

This module is an *optional enhancement layer*. Every public entry point returns
``None`` (or a falsy result) when UIA / ``comtypes`` is unavailable, so callers
must always keep a non-UIA fallback path. UIA gives us three things the raw
HWND + mouse-wheel approach cannot:

* the precise bounding rectangle of the element under the cursor (smart element
  capture, and the *real* scrollable viewport instead of the whole window), and
* a cursor-free way to scroll (``ScrollPattern``) that works on WPF / WinUI /
  UWP / Win32 common controls without clicking or moving the mouse, and
* the exact scroll position (``VerticalScrollPercent``) so capture can stop
  precisely at the bottom instead of guessing from image similarity.

All COM access is wrapped so a failure degrades to the legacy behavior rather
than breaking capture.
"""

from __future__ import annotations

import logging
import sys
import threading
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any

LOGGER = logging.getLogger(__name__)

# Sentinel passed to SetScrollPercent for the axis we do not want to move.
_NO_SCROLL = -1.0
# Treat anything within this margin of 0/100% as fully scrolled to that end.
_PERCENT_EPSILON = 0.5

_lock = threading.Lock()
_uia_module = None  # generated comtypes module (UIAutomationClient)
_automation = None  # IUIAutomation instance
_init_failed = False


def _ensure_automation():
    """Return a cached IUIAutomation instance, or None if UIA is unavailable."""
    global _uia_module, _automation, _init_failed
    if _automation is not None:
        return _automation
    if _init_failed:
        return None
    with _lock:
        if _automation is not None:
            return _automation
        if _init_failed:
            return None
        try:
            import comtypes
            import comtypes.client as cc

            # In a frozen build the on-disk comtypes gen cache may be read-only
            # or absent; generating in-memory avoids first-run write failures.
            if getattr(sys, "frozen", False):
                cc.gen_dir = None

            cc.GetModule("UIAutomationCore.dll")
            from comtypes.gen import UIAutomationClient as uia  # noqa: N813

            # comtypes initializes COM on the importing (main/GUI) thread, but be
            # explicit and tolerant of an already-initialized apartment.
            try:
                comtypes.CoInitializeEx(comtypes.COINIT_APARTMENTTHREADED)
            except OSError:
                pass

            automation = cc.CreateObject(
                uia.CUIAutomation,
                interface=uia.IUIAutomation,
            )
            _uia_module = uia
            _automation = automation
            return automation
        except Exception as exc:  # pylint: disable=broad-exception-caught
            LOGGER.info("UI Automation unavailable; using legacy capture: %s", exc)
            _init_failed = True
            return None


def is_available() -> bool:
    """Return True when UIA can be used on this machine."""
    return _ensure_automation() is not None


def _rect_from_bounding(bounding) -> wintypes.RECT | None:
    try:
        left = int(bounding.left)
        top = int(bounding.top)
        right = int(bounding.right)
        bottom = int(bounding.bottom)
    except (AttributeError, ValueError, TypeError):
        return None
    if right <= left or bottom <= top:
        return None
    return wintypes.RECT(left, top, right, bottom)


@dataclass(frozen=True)
class UiaElement:
    """Thin, defensive wrapper over an IUIAutomationElement pointer."""

    _ptr: Any

    @property
    def name(self) -> str:
        try:
            return str(self._ptr.CurrentName or "")
        except Exception:  # pylint: disable=broad-exception-caught
            return ""

    @property
    def control_type_name(self) -> str:
        try:
            return str(self._ptr.CurrentLocalizedControlType or "")
        except Exception:  # pylint: disable=broad-exception-caught
            return ""

    @property
    def native_hwnd(self) -> int:
        try:
            return int(self._ptr.CurrentNativeWindowHandle or 0)
        except Exception:  # pylint: disable=broad-exception-caught
            return 0

    @property
    def bounding_rect(self) -> wintypes.RECT | None:
        try:
            return _rect_from_bounding(self._ptr.CurrentBoundingRectangle)
        except Exception:  # pylint: disable=broad-exception-caught
            return None

    def parent(self) -> "UiaElement | None":
        automation = _ensure_automation()
        if automation is None:
            return None
        try:
            walker = automation.RawViewWalker
            parent_ptr = walker.GetParentElement(self._ptr)
        except Exception:  # pylint: disable=broad-exception-caught
            return None
        if not parent_ptr:
            return None
        return UiaElement(parent_ptr)

    def _scroll_pattern(self):
        if _uia_module is None:
            return None
        try:
            raw = self._ptr.GetCurrentPattern(_uia_module.UIA_ScrollPatternId)
        except Exception:  # pylint: disable=broad-exception-caught
            return None
        if not raw:
            return None
        try:
            return raw.QueryInterface(_uia_module.IUIAutomationScrollPattern)
        except Exception:  # pylint: disable=broad-exception-caught
            return None

    def as_scroll_target(self) -> "UiaScrollTarget | None":
        pattern = self._scroll_pattern()
        if pattern is None:
            return None
        return UiaScrollTarget(self, pattern)


@dataclass(frozen=True)
class UiaScrollTarget:
    """A scrollable element plus its ScrollPattern, for cursor-free scrolling."""

    element: UiaElement
    _pattern: Any

    @property
    def bounding_rect(self) -> wintypes.RECT | None:
        return self.element.bounding_rect

    @property
    def native_hwnd(self) -> int:
        return self.element.native_hwnd

    def vertically_scrollable(self) -> bool:
        try:
            return bool(self._pattern.CurrentVerticallyScrollable)
        except Exception:  # pylint: disable=broad-exception-caught
            return False

    def vertical_percent(self) -> float | None:
        """Current vertical scroll position, 0..100, or None if unknown."""
        try:
            value = float(self._pattern.CurrentVerticalScrollPercent)
        except Exception:  # pylint: disable=broad-exception-caught
            return None
        # UIA reports -1 (UIA_ScrollPatternNoScroll) when the axis can't scroll.
        if value < 0:
            return None
        return value

    def vertical_view_size(self) -> float | None:
        """Fraction of content visible in the viewport, as a percent (0..100)."""
        try:
            value = float(self._pattern.CurrentVerticalViewSize)
        except Exception:  # pylint: disable=broad-exception-caught
            return None
        if value <= 0:
            return None
        return value

    def at_top(self) -> bool:
        percent = self.vertical_percent()
        return percent is not None and percent <= _PERCENT_EPSILON

    def at_bottom(self) -> bool:
        percent = self.vertical_percent()
        return percent is not None and percent >= 100.0 - _PERCENT_EPSILON

    def set_vertical_percent(self, percent: float) -> bool:
        clamped = max(0.0, min(100.0, percent))
        try:
            self._pattern.SetScrollPercent(_NO_SCROLL, clamped)
            return True
        except Exception as exc:  # pylint: disable=broad-exception-caught
            LOGGER.debug("SetScrollPercent failed: %s", exc)
            return False

    def scroll_to_top(self) -> bool:
        return self.set_vertical_percent(0.0)

    def scroll_down_increment(self) -> bool:
        """Scroll down one large increment (≈ one page) via ScrollPattern.Scroll."""
        if _uia_module is None:
            return False
        try:
            self._pattern.Scroll(
                _uia_module.ScrollAmount_NoAmount,
                _uia_module.ScrollAmount_LargeIncrement,
            )
            return True
        except Exception as exc:  # pylint: disable=broad-exception-caught
            LOGGER.debug("ScrollPattern.Scroll failed: %s", exc)
            return False


def element_from_native_point(x: int, y: int) -> UiaElement | None:
    """Return the UIA element at a native (physical-pixel) screen point."""
    automation = _ensure_automation()
    if automation is None:
        return None
    try:
        point = wintypes.POINT(int(x), int(y))
        ptr = automation.ElementFromPoint(point)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        LOGGER.debug("ElementFromPoint failed at (%s, %s): %s", x, y, exc)
        return None
    if not ptr:
        return None
    return UiaElement(ptr)


@dataclass(frozen=True)
class ElementRect:
    """A native-pixel element rectangle with a short label, for hit-testing."""

    left: int
    top: int
    right: int
    bottom: int
    name: str
    control_type: str

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def area(self) -> int:
        return self.width * self.height


def collect_element_rects(
    *,
    max_depth: int = 8,
    max_total: int = 2500,
    min_size: int = 6,
) -> list[ElementRect]:
    """Enumerate on-screen UIA element rectangles for hover hit-testing.

    Walks the control view of every top-level window breadth-first, bounded by
    ``max_depth`` / ``max_total`` so even a browser's huge accessibility tree
    can't stall the capture. Returns native (physical-pixel) rectangles; the
    caller maps them to a screen. Empty when UIA is unavailable.
    """
    automation = _ensure_automation()
    if automation is None:
        return []
    try:
        walker = automation.ControlViewWalker
        root = automation.GetRootElement()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        LOGGER.debug("collect_element_rects: no control walker: %s", exc)
        return []

    rects: list[ElementRect] = []
    # (element, depth) frontier; start with the top-level windows.
    frontier: list[tuple[Any, int]] = []
    try:
        child = walker.GetFirstChildElement(root)
    except Exception:  # pylint: disable=broad-exception-caught
        child = None
    while child:
        frontier.append((child, 0))
        try:
            child = walker.GetNextSiblingElement(child)
        except Exception:  # pylint: disable=broad-exception-caught
            break

    while frontier and len(rects) < max_total:
        element, depth = frontier.pop(0)
        try:
            if bool(element.CurrentIsOffscreen):
                continue
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        wrapped = UiaElement(element)
        rect = wrapped.bounding_rect
        if rect is not None:
            width = int(rect.right) - int(rect.left)
            height = int(rect.bottom) - int(rect.top)
            if width >= min_size and height >= min_size:
                rects.append(
                    ElementRect(
                        int(rect.left),
                        int(rect.top),
                        int(rect.right),
                        int(rect.bottom),
                        wrapped.name[:80],
                        wrapped.control_type_name[:40],
                    )
                )
        if depth >= max_depth:
            continue
        try:
            grandchild = walker.GetFirstChildElement(element)
        except Exception:  # pylint: disable=broad-exception-caught
            grandchild = None
        while grandchild:
            frontier.append((grandchild, depth + 1))
            try:
                grandchild = walker.GetNextSiblingElement(grandchild)
            except Exception:  # pylint: disable=broad-exception-caught
                break
    return rects


def scroll_target_from_native_point(
    x: int,
    y: int,
    *,
    max_depth: int = 16,
) -> UiaScrollTarget | None:
    """Walk ancestors from (x, y) and return the smallest vertically scrollable element."""
    from services.uia_scroll_targets import (  # noqa: PLC0415
        resolve_scroll_target_from_native_point,
    )

    return resolve_scroll_target_from_native_point(x, y, max_depth=max_depth)
