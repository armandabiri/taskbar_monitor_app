"""Resolve nested UIA scroll containers under a screen point."""

from __future__ import annotations

from services.uia_service import UiaScrollTarget, element_from_native_point


def resolve_scroll_target_from_native_point(
    x: int,
    y: int,
    *,
    max_depth: int = 16,
) -> "UiaScrollTarget | None":
    """Walk ancestors from (x, y) and return the smallest vertically scrollable element."""
    element = element_from_native_point(x, y)
    depth = 0
    best: UiaScrollTarget | None = None
    best_area: int | None = None
    while element is not None and depth < max_depth:
        target = element.as_scroll_target()
        if target is not None and target.vertically_scrollable():
            rect = target.bounding_rect
            if rect is not None:
                area = max(1, int(rect.right - rect.left)) * max(1, int(rect.bottom - rect.top))
            else:
                area = 10**12
            if best is None or (best_area is not None and area < best_area):
                best = target
                best_area = area
        element = element.parent()
        depth += 1
    return best
