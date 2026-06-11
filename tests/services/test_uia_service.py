"""Tests for the pure logic in the UI Automation capture helper.

These exercise the scroll-position math and rect geometry with fakes so they
run without a live UIA desktop. The COM plumbing itself degrades gracefully and
is covered by the integration paths.
"""

from services.uia_service import ElementRect, UiaElement, UiaScrollTarget, is_available


class _FakePattern:
    def __init__(self, percent: float, view: float, scrollable: bool = True) -> None:
        self.CurrentVerticalScrollPercent = percent
        self.CurrentVerticalViewSize = view
        self.CurrentVerticallyScrollable = scrollable
        self.set_calls: list[tuple[float, float]] = []

    def SetScrollPercent(self, horizontal: float, vertical: float) -> None:  # noqa: N802
        self.set_calls.append((horizontal, vertical))


def _target(percent: float, view: float, scrollable: bool = True) -> UiaScrollTarget:
    return UiaScrollTarget(UiaElement(None), _FakePattern(percent, view, scrollable))


def test_at_top_and_bottom_use_epsilon() -> None:
    assert _target(0.0, 20.0).at_top()
    assert _target(0.3, 20.0).at_top()
    assert not _target(5.0, 20.0).at_top()

    assert _target(100.0, 20.0).at_bottom()
    assert _target(99.7, 20.0).at_bottom()
    assert not _target(80.0, 20.0).at_bottom()


def test_vertical_percent_reports_none_when_not_scrollable() -> None:
    # UIA returns -1 (UIA_ScrollPatternNoScroll) for an axis that cannot scroll.
    assert _target(-1.0, 20.0).vertical_percent() is None
    assert _target(42.0, 20.0).vertical_percent() == 42.0


def test_vertical_view_size_rejects_non_positive() -> None:
    assert _target(10.0, 0.0).vertical_view_size() is None
    assert _target(10.0, 25.0).vertical_view_size() == 25.0


def test_set_vertical_percent_clamps_to_range() -> None:
    high = _target(50.0, 20.0)
    assert high.set_vertical_percent(150.0)
    assert high._pattern.set_calls[-1] == (-1.0, 100.0)

    low = _target(50.0, 20.0)
    assert low.set_vertical_percent(-10.0)
    assert low._pattern.set_calls[-1] == (-1.0, 0.0)


def test_scroll_to_top_sets_zero() -> None:
    target = _target(73.0, 20.0)
    assert target.scroll_to_top()
    assert target._pattern.set_calls[-1] == (-1.0, 0.0)


def test_element_rect_geometry() -> None:
    rect = ElementRect(10, 20, 110, 220, "Inbox", "list")
    assert rect.width == 100
    assert rect.height == 200
    assert rect.area == 20000


def test_is_available_returns_bool() -> None:
    # Must never raise, regardless of whether UIA/comtypes is present.
    assert isinstance(is_available(), bool)
