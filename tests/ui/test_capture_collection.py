from __future__ import annotations

from PyQt6.QtGui import QColor, QImage

from ui.capture_collection import CaptureCollection, SequentialImagePaster


def _image(color: str) -> QImage:
    img = QImage(4, 4, QImage.Format.Format_ARGB32)
    img.fill(QColor(color).rgb())
    return img


def test_collection_session_lifecycle() -> None:
    events: list[tuple[bool, int]] = []
    coll = CaptureCollection()
    coll.changed.connect(lambda active, count: events.append((active, count)))

    assert coll.active is False
    assert coll.toggle() is True  # start
    assert coll.active is True
    coll.add(_image("red"))
    coll.add(_image("green"))
    assert coll.count == 2
    assert coll.toggle() is False  # stop keeps the stack
    assert coll.active is False
    assert coll.count == 2
    assert len(coll.images) == 2
    assert (True, 0) in events and (False, 2) in events


def test_start_clears_previous_stack() -> None:
    coll = CaptureCollection()
    coll.start()
    coll.add(_image("red"))
    coll.stop()
    assert coll.count == 1
    coll.start()  # new session resets
    assert coll.count == 0


def test_add_ignores_null_image() -> None:
    coll = CaptureCollection()
    coll.start()
    coll.add(QImage())
    assert coll.count == 0


class _FakeClipboard:
    def __init__(self) -> None:
        self.images: list[QImage] = []

    def setImage(self, image: QImage) -> None:  # noqa: N802 (mirrors QClipboard)
        self.images.append(image)


def test_paster_walks_every_image(qtbot) -> None:
    paster = SequentialImagePaster(gap_ms=10)
    clipboard = _FakeClipboard()
    images = [_image("red"), _image("green"), _image("blue")]

    done: list[int] = []
    paster.finished.connect(done.append)
    assert paster.paste(images, clipboard) is True

    qtbot.waitUntil(lambda: bool(done), timeout=3000)
    # Every image was placed on the clipboard before its Ctrl+V (send may no-op
    # off-Windows, but clipboard cycling must still cover all three frames).
    assert len(clipboard.images) == 3


def test_paste_empty_is_noop() -> None:
    paster = SequentialImagePaster()
    assert paster.paste([], _FakeClipboard()) is False
