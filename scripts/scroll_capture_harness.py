"""Interactive harness for validating scrolling screenshot capture.

This opens a deterministic scrollable test window, runs the same screenshot
coordinator used by the app, saves the stitched output and a full-content
reference image, then reports simple image/height metrics.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from PyQt6.QtCore import QPoint, QRect, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QMainWindow, QScrollArea, QWidget

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from services.screenshot_service import ScrollingScreenshotCoordinator  # noqa: E402


class MarkerContent(QWidget):
    """Scrollable content with deterministic row markers."""

    def __init__(self, *, rows: int, row_height: int, width: int) -> None:
        super().__init__()
        self.rows = rows
        self.row_height = row_height
        self.content_width = width
        self.setFixedSize(width, rows * row_height)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.content_width, self.rows * self.row_height)

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        self.render_content(QPainter(self))

    def render_reference(self) -> QImage:
        image = QImage(self.width(), self.height(), QImage.Format.Format_RGB32)
        image.fill(QColor("white"))
        painter = QPainter(image)
        self.render_content(painter)
        painter.end()
        return image

    def render_content(self, painter: QPainter) -> None:
        font = QFont("Consolas", 11)
        painter.setFont(font)
        for row in range(self.rows):
            y = row * self.row_height
            bg = QColor(248, 250, 252) if row % 2 == 0 else QColor(235, 241, 249)
            stripe = QColor.fromHsv((row * 41) % 360, 170, 210)
            painter.fillRect(0, y, self.width(), self.row_height, bg)
            painter.fillRect(0, y, 26, self.row_height, stripe)
            painter.setPen(QPen(QColor(170, 180, 190)))
            painter.drawLine(0, y + self.row_height - 1, self.width(), y + self.row_height - 1)
            painter.setPen(QColor(25, 35, 45))
            marker = "".join(chr(ord("A") + ((row + index) % 26)) for index in range(20))
            painter.drawText(
                38,
                y + 22,
                f"LINE {row:03d} :: {marker} :: unique scroll marker {row * 7919:07d}",
            )


class HarnessWindow(QMainWindow):
    """Window containing the deterministic scroll area."""

    def __init__(self, *, rows: int, row_height: int, content_width: int, viewport_height: int):
        super().__init__()
        self.setWindowTitle("Taskbar Monitor Scroll Capture Harness")
        self.content = MarkerContent(rows=rows, row_height=row_height, width=content_width)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(False)
        self.scroll_area.setWidget(self.content)
        self.scroll_area.viewport().setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.scroll_area.setFixedSize(content_width + 22, viewport_height + 2)
        self.setCentralWidget(self.scroll_area)
        self.resize(content_width + 40, viewport_height + 48)

    def viewport_target(self) -> tuple[object, QRect]:
        viewport = self.scroll_area.viewport()
        top_left = viewport.mapToGlobal(QPoint(0, 0))
        center = top_left + QPoint(viewport.width() // 2, viewport.height() // 2)
        screen = QGuiApplication.screenAt(center) or QGuiApplication.primaryScreen()
        if screen is None:
            raise RuntimeError("No screen available for harness")
        local_top_left = top_left - screen.geometry().topLeft()
        return screen, QRect(local_top_left, viewport.size())


def sample_diff(a: QImage, b: QImage) -> float:
    width = min(a.width(), b.width())
    height = min(a.height(), b.height())
    if width <= 0 or height <= 0:
        return 1.0
    total = 0
    count = 0
    x_step = max(1, width // 64)
    y_step = max(1, height // 256)
    for y in range(0, height, y_step):
        for x in range(0, width, x_step):
            ca = a.pixelColor(x, y)
            cb = b.pixelColor(x, y)
            total += (
                abs(ca.red() - cb.red())
                + abs(ca.green() - cb.green())
                + abs(ca.blue() - cb.blue())
            )
            count += 3 * 255
    return total / max(1, count)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=120)
    parser.add_argument("--row-height", type=int, default=32)
    parser.add_argument("--content-width", type=int, default=760)
    parser.add_argument("--viewport-height", type=int, default=420)
    parser.add_argument("--max-pages", type=int, default=40)
    parser.add_argument("--out-dir", default=str(ROOT / ".intelag" / "reports" / "scroll_harness"))
    args = parser.parse_args()

    os.environ.pop("QT_QPA_PLATFORM", None)
    app = QApplication.instance() or QApplication(sys.argv)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    window = HarnessWindow(
        rows=args.rows,
        row_height=args.row_height,
        content_width=args.content_width,
        viewport_height=args.viewport_height,
    )
    window.move(120, 120)
    window.show()
    window.raise_()
    window.activateWindow()
    scroll_bar = window.scroll_area.verticalScrollBar()
    scroll_values = [scroll_bar.value()]
    scroll_bar.valueChanged.connect(scroll_values.append)

    result: dict[str, object] = {"status": "pending"}
    coordinator = ScrollingScreenshotCoordinator(
        window,
        max_pages=args.max_pages,
        allow_self_capture=True,
    )

    def finish(image: QImage) -> None:
        screen, viewport_rect = window.viewport_target()
        reference = window.content.render_reference()
        scaled_reference_width = max(1, image.width())
        scaled_reference_height = max(
            1,
            round(reference.height() * scaled_reference_width / max(1, reference.width())),
        )
        scaled_reference = reference.scaled(
            scaled_reference_width,
            scaled_reference_height,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        capture_path = out_dir / "captured.png"
        reference_path = out_dir / "reference.png"
        scaled_reference_path = out_dir / "reference_scaled.png"
        metrics_path = out_dir / "metrics.json"
        image.save(str(capture_path))
        reference.save(str(reference_path))
        scaled_reference.save(str(scaled_reference_path))
        scaled_height_ratio = image.height() / max(1, scaled_reference.height())
        scaled_sample_diff = sample_diff(image, scaled_reference)
        reached_bottom = scroll_bar.value() >= scroll_bar.maximum()
        validation_ok = (
            len(coordinator.images) > 1
            and reached_bottom
            and 0.97 <= scaled_height_ratio <= 1.03
            and scaled_sample_diff < 0.25
        )
        metrics = {
            "status": "ok",
            "validation_ok": validation_ok,
            "captured": str(capture_path),
            "reference": str(reference_path),
            "reference_scaled": str(scaled_reference_path),
            "captured_size": [image.width(), image.height()],
            "reference_size": [reference.width(), reference.height()],
            "scaled_reference_size": [scaled_reference.width(), scaled_reference.height()],
            "height_ratio": image.height() / max(1, reference.height()),
            "sample_diff": sample_diff(image, reference),
            "scaled_height_ratio": scaled_height_ratio,
            "scaled_sample_diff": scaled_sample_diff,
            "frame_count": len(coordinator.images),
            "offsets": coordinator.offsets,
            "viewport_rect": [
                viewport_rect.x(),
                viewport_rect.y(),
                viewport_rect.width(),
                viewport_rect.height(),
            ],
            "screen": screen.name(),
            "scroll_value_history": scroll_values,
            "scroll_value_final": scroll_bar.value(),
            "scroll_value_max": scroll_bar.maximum(),
            "reached_bottom": reached_bottom,
        }
        metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
        result.clear()
        result.update(metrics)
        app.quit()

    def fail(reason: str) -> None:
        result.clear()
        result.update({"status": "failed", "reason": reason})
        (out_dir / "metrics.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        app.quit()

    coordinator.finished.connect(finish)
    coordinator.failed.connect(fail)

    def start_capture() -> None:
        screen, viewport_rect = window.viewport_target()
        viewport = window.scroll_area.viewport()
        ok = coordinator.start(
            int(window.winId()),
            int(viewport.winId()),
            screen,
            viewport_rect,
        )
        if not ok and result.get("status") == "pending":
            fail("Coordinator refused to start")

    QTimer.singleShot(600, start_capture)
    app.exec()
    print(json.dumps(result, indent=2))
    return 0 if result.get("status") == "ok" and result.get("validation_ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
