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

from PyQt6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from services.screenshot_service import ScrollingScreenshotCoordinator  # noqa: E402


def marker_stripe_color(row: int) -> QColor:
    """Unique, machine-decodable stripe color for a content row.

    The row index is byte-encoded into the red/green channels so any row
    count up to 65536 stays unambiguous; blue is a constant tag that keeps
    stripe pixels far from the separator-line grays.
    """
    return QColor(row % 256, row // 256, 137)


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
            stripe = marker_stripe_color(row)
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


class ScrollGate(QObject):
    """Application-level wheel gate that mimics a Chromium/Electron pane.

    Real editors (VS Code, browsers) drop a synthetic mouse wheel until the pane
    is activated by a real click. A plain ``QScrollArea`` instead scrolls on
    hover, which is exactly why the old harness passed while real apps failed.
    This filter swallows every wheel event until a mouse press is observed so the
    harness reproduces — and regression-tests — the real-app behavior.

    Modes:
      * ``click``          - scroll only after a real click (the real behavior)
      * ``always-blocked`` - never scroll, even after a click (a target our
                              click can't activate; proves the harness now
                              *detects* a no-scroll failure)
      * ``off``            - original hover-scrolls-immediately behavior
    """

    def __init__(self, mode: str) -> None:
        super().__init__()
        self.mode = mode
        self.activated = mode == "off"
        self.wheel_events_swallowed = 0
        self.clicks_seen = 0

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        del obj
        etype = event.type()
        if etype == QEvent.Type.MouseButtonPress:
            self.clicks_seen += 1
            if self.mode == "click":
                self.activated = True
        elif etype == QEvent.Type.Wheel and self.mode != "off" and not self.activated:
            self.wheel_events_swallowed += 1
            return True
        return False


class ControlsPanel(QWidget):
    """Static controls beside the scroll area, for a realistic window."""

    def __init__(self, height: int) -> None:
        super().__init__()
        self.setFixedWidth(220)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        title = QLabel("Inspector")
        title.setStyleSheet("font-weight: bold;")
        layout.addWidget(title)
        for label in ("Open", "Save", "Run", "Format", "Settings"):
            layout.addWidget(QPushButton(label))
        listing = QListWidget()
        for index in range(max(6, height // 40)):
            listing.addItem(f"symbol {index:02d}")
        layout.addWidget(listing, 1)
        layout.addWidget(QLabel("status: ready"))


class HarnessWindow(QMainWindow):
    """Realistic window: a scrollable text pane beside static controls."""

    def __init__(
        self,
        *,
        rows: int,
        row_height: int,
        content_width: int,
        viewport_height: int,
    ):
        super().__init__()
        self.setWindowTitle("Taskbar Monitor Scroll Capture Harness")
        self.content = MarkerContent(rows=rows, row_height=row_height, width=content_width)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(False)
        self.scroll_area.setWidget(self.content)
        self.scroll_area.viewport().setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.scroll_area.setFixedSize(content_width + 22, viewport_height + 2)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)
        layout.addWidget(self.scroll_area)
        layout.addWidget(ControlsPanel(viewport_height))
        self.setCentralWidget(central)
        self.resize(content_width + 22 + 252, viewport_height + 60)

    def viewport_target(self) -> tuple[object, QRect]:
        viewport = self.scroll_area.viewport()
        top_left = viewport.mapToGlobal(QPoint(0, 0))
        center = top_left + QPoint(viewport.width() // 2, viewport.height() // 2)
        screen = QGuiApplication.screenAt(center) or QGuiApplication.primaryScreen()
        if screen is None:
            raise RuntimeError("No screen available for harness")
        local_top_left = top_left - screen.geometry().topLeft()
        return screen, QRect(local_top_left, viewport.size())


def decode_row_sequence(image: QImage, *, rows: int, viewport_width_logical: int) -> list[int]:
    """Decode the order of content rows from the stripe colors in a capture.

    Each content row paints a unique solid color into its left stripe, so the
    captured image can be checked row-band by row-band for exact top-to-bottom
    coverage (missing, duplicated, or out-of-order content).
    """
    palette: list[tuple[int, int, int, int]] = []
    exact: dict[tuple[int, int, int], int] = {}
    for row in range(rows):
        color = marker_stripe_color(row)
        rgb = (color.red(), color.green(), color.blue())
        exact[rgb] = row
        palette.append((*rgb, row))

    scale = image.width() / max(1, viewport_width_logical)
    x = max(0, min(image.width() - 1, round(13 * scale)))

    sequence: list[int] = []
    current: int | None = None
    run = 0
    for y in range(image.height()):
        pixel = image.pixel(x, y)
        rgb = ((pixel >> 16) & 255, (pixel >> 8) & 255, pixel & 255)
        row = exact.get(rgb)
        if row is None:
            best, best_diff = None, 1 << 30
            for red, green, blue, candidate in palette:
                diff = abs(rgb[0] - red) + abs(rgb[1] - green) + abs(rgb[2] - blue)
                if diff < best_diff:
                    best, best_diff = candidate, diff
            if best is None or best_diff > 30:
                continue  # separator line or anti-aliased edge pixel
            row = best
        if row == current:
            run += 1
        else:
            current, run = row, 1
        if run == 4 and (not sequence or sequence[-1] != row):
            sequence.append(row)
    return sequence


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
    parser.add_argument("--max-pages", type=int, default=60)
    parser.add_argument(
        "--start-scroll",
        type=int,
        default=-1,
        help="Scroll offset applied before capture; -1 starts midway to prove "
        "the coordinator returns to the top on its own.",
    )
    parser.add_argument(
        "--gate",
        choices=("click", "always-blocked", "off"),
        default="click",
        help="Wheel-activation model for the text pane. 'click' mimics "
        "Chromium/Electron (must be clicked before it scrolls); "
        "'always-blocked' never scrolls (proves the harness detects a "
        "no-scroll failure); 'off' is the legacy hover-scroll behavior.",
    )
    parser.add_argument(
        "--prefer-input",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Scroll via SendInput cursor-wheel injection (the path real "
        "Chromium/Electron targets use) instead of PostMessage.",
    )
    parser.add_argument("--out-dir", default=str(ROOT / ".intelag" / "reports" / "scroll_harness"))
    args = parser.parse_args()

    os.environ.pop("QT_QPA_PLATFORM", None)
    app = QApplication.instance() or QApplication(sys.argv)
    gate = ScrollGate(args.gate)
    app.installEventFilter(gate)
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
    initial_scroll = {"value": 0, "history_start": 0}

    result: dict[str, object] = {"status": "pending"}
    coordinator = ScrollingScreenshotCoordinator(
        window,
        max_pages=args.max_pages,
        allow_self_capture=True,
        # Scroll with SendInput cursor-wheel injection, the path real
        # Chromium/Electron targets (VS Code, browsers) actually use; the
        # async PostMessage path is unrepresentative and unstable here.
        prefer_input_injection=True,
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
        observed_values = scroll_values[initial_scroll["history_start"]:] or [scroll_bar.value()]
        reached_top = min(observed_values) == 0
        row_sequence = decode_row_sequence(
            image,
            rows=args.rows,
            viewport_width_logical=viewport_rect.width(),
        )
        row_coverage_ok = row_sequence == list(range(args.rows))
        validation_ok = (
            len(coordinator.images) > 1
            and reached_top
            and reached_bottom
            and row_coverage_ok
            and 0.97 <= scaled_height_ratio <= 1.03
            and scaled_sample_diff < 0.25
        )
        metrics = {
            "status": "ok",
            "validation_ok": validation_ok,
            "gate": args.gate,
            "gate_clicks_seen": gate.clicks_seen,
            "gate_wheel_swallowed": gate.wheel_events_swallowed,
            "initial_scroll_value": initial_scroll["value"],
            "reached_top": reached_top,
            "row_coverage_ok": row_coverage_ok,
            "row_sequence_length": len(row_sequence),
            "row_sequence_first": row_sequence[0] if row_sequence else None,
            "row_sequence_last": row_sequence[-1] if row_sequence else None,
            "row_missing": sorted(set(range(args.rows)) - set(row_sequence)),
            "row_order_breaks": sum(
                1
                for left, right in zip(row_sequence, row_sequence[1:])
                if right != left + 1
            ),
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
        maximum = scroll_bar.maximum()
        wanted = maximum // 2 if args.start_scroll < 0 else args.start_scroll
        scroll_bar.setValue(max(0, min(wanted, maximum)))
        initial_scroll["value"] = scroll_bar.value()
        initial_scroll["history_start"] = len(scroll_values)
        scroll_values.append(scroll_bar.value())

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
