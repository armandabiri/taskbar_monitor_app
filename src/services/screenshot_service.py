"""Screenshot helpers and scrolling capture coordinator."""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes
from dataclasses import dataclass

from PyQt6.QtCore import QObject, QPoint, QRect, QSize, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication, QImage, QPainter, QPixmap, QScreen
from PyQt6.QtWidgets import QApplication

LOGGER = logging.getLogger(__name__)

_CAPTUREBLT = 0x40000000
_SRCCOPY = 0x00CC0020
_DIB_RGB_COLORS = 0
_BI_RGB = 0
_VK_NEXT = 0x22
_KEYEVENTF_KEYUP = 0x0002
_GA_ROOT = 2
_SW_RESTORE = 9
_ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _RGBQUAD(ctypes.Structure):
    _fields_ = [
        ("rgbBlue", ctypes.c_ubyte),
        ("rgbGreen", ctypes.c_ubyte),
        ("rgbRed", ctypes.c_ubyte),
        ("rgbReserved", ctypes.c_ubyte),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", _BITMAPINFOHEADER),
        ("bmiColors", _RGBQUAD * 1),
    ]


@dataclass(frozen=True)
class _NativeMonitor:
    device: str
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


def _get_user32():
    try:
        return ctypes.windll.user32
    except AttributeError:
        return None


def _get_gdi32():
    try:
        return ctypes.windll.gdi32
    except AttributeError:
        return None


def _get_kernel32():
    try:
        return ctypes.windll.kernel32
    except AttributeError:
        return None


def get_window_rect_dwm(hwnd: int) -> wintypes.RECT | None:
    """Get the visible native-pixel bounds of a window."""
    rect = wintypes.RECT()
    try:
        ctypes.windll.dwmapi.DwmGetWindowAttribute.argtypes = [
            wintypes.HWND,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            hwnd,
            9,  # DWMWA_EXTENDED_FRAME_BOUNDS
            ctypes.byref(rect),
            ctypes.sizeof(rect),
        )
        if hr == 0:
            return rect
    except (AttributeError, OSError) as exc:
        LOGGER.debug("DwmGetWindowAttribute failed: %s", exc)

    user32 = _get_user32()
    if user32 is None:
        return None
    try:
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
        if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return rect
    except OSError as exc:
        LOGGER.debug("GetWindowRect failed: %s", exc)

    return None


def _monitor_name_key(name: str) -> str:
    return name.replace("\\\\.\\", "").strip().lower()


def _native_monitors() -> list[_NativeMonitor]:
    """Return native monitor rectangles in physical pixels."""
    user32 = _get_user32()
    if user32 is None or not hasattr(ctypes, "WINFUNCTYPE"):
        return []

    class _MONITORINFOEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
            ("szDevice", wintypes.WCHAR * 32),
        ]

    monitors: list[_NativeMonitor] = []
    monitor_enum_proc = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HMONITOR,
        wintypes.HDC,
        ctypes.POINTER(wintypes.RECT),
        wintypes.LPARAM,
    )

    def callback(hmonitor, _hdc, _rect, _lparam) -> bool:
        info = _MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(info)
        if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            rect = info.rcMonitor
            monitors.append(
                _NativeMonitor(
                    device=info.szDevice,
                    left=int(rect.left),
                    top=int(rect.top),
                    right=int(rect.right),
                    bottom=int(rect.bottom),
                )
            )
        return True

    try:
        user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]
        user32.GetMonitorInfoW.restype = wintypes.BOOL
        user32.EnumDisplayMonitors.restype = wintypes.BOOL
        user32.EnumDisplayMonitors(0, 0, monitor_enum_proc(callback), 0)
    except OSError as exc:
        LOGGER.debug("EnumDisplayMonitors failed: %s", exc)
        return []
    return monitors


def _native_monitor_for_screen(screen: QScreen) -> _NativeMonitor | None:
    monitors = _native_monitors()
    if not monitors:
        return None

    wanted = _monitor_name_key(screen.name())
    for monitor in monitors:
        if _monitor_name_key(monitor.device) == wanted:
            return monitor

    screens = QGuiApplication.screens()
    try:
        index = screens.index(screen)
    except ValueError:
        return None
    if index < len(monitors):
        return monitors[index]
    return None


def screen_at_qt_point(point: QPoint) -> QScreen | None:
    """Find the Qt screen that contains a global Qt logical point."""
    screen = QGuiApplication.screenAt(point)
    if screen is not None:
        return screen
    for candidate in QGuiApplication.screens():
        if candidate.geometry().contains(point):
            return candidate
    return QGuiApplication.primaryScreen()


def qt_screen_local_rect_to_native(screen: QScreen, rect: QRect) -> tuple[int, int, int, int]:
    """Map a screen-local Qt logical rectangle to native physical coordinates."""
    normalized = QRect(rect).normalized().intersected(QRect(QPoint(0, 0), screen.geometry().size()))
    if normalized.isEmpty():
        return (0, 0, 0, 0)

    monitor = _native_monitor_for_screen(screen)
    if monitor is None:
        ratio = screen.devicePixelRatio()
        geom = screen.geometry()
        return (
            int((geom.x() + normalized.x()) * ratio),
            int((geom.y() + normalized.y()) * ratio),
            max(1, int(normalized.width() * ratio)),
            max(1, int(normalized.height() * ratio)),
        )

    geom = screen.geometry()
    scale_x = monitor.width / max(1, geom.width())
    scale_y = monitor.height / max(1, geom.height())
    x = monitor.left + round(normalized.x() * scale_x)
    y = monitor.top + round(normalized.y() * scale_y)
    width = max(1, round(normalized.width() * scale_x))
    height = max(1, round(normalized.height() * scale_y))
    return (x, y, width, height)


def qt_global_point_to_native(point: QPoint) -> tuple[int, int]:
    """Map a global Qt logical point to native physical coordinates."""
    screen = screen_at_qt_point(point)
    if screen is None:
        return (point.x(), point.y())

    local = point - screen.geometry().topLeft()
    x, y, _, _ = qt_screen_local_rect_to_native(screen, QRect(local.x(), local.y(), 1, 1))
    return (x, y)


def window_from_qt_point(point: QPoint) -> int:
    """Return the root HWND under a Qt global point."""
    user32 = _get_user32()
    if user32 is None:
        return 0
    native_x, native_y = qt_global_point_to_native(point)
    try:
        user32.WindowFromPoint.argtypes = [_POINT]
        user32.WindowFromPoint.restype = wintypes.HWND
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        hwnd = int(user32.WindowFromPoint(_POINT(native_x, native_y)))
        if not hwnd:
            return 0
        root = int(user32.GetAncestor(hwnd, _GA_ROOT))
        return root or hwnd
    except OSError as exc:
        LOGGER.debug("WindowFromPoint failed: %s", exc)
        return 0


def is_valid_capture_window(hwnd: int, own_hwnd: int = 0) -> bool:
    """Return True when an HWND is suitable for window capture."""
    if not hwnd:
        return False
    user32 = _get_user32()
    if user32 is None:
        return False
    try:
        user32.GetShellWindow.restype = wintypes.HWND
        user32.GetDesktopWindow.restype = wintypes.HWND
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        if hwnd in (int(user32.GetShellWindow()), int(user32.GetDesktopWindow())):
            return False
        if own_hwnd and hwnd == own_hwnd:
            return False
        if not user32.IsWindowVisible(hwnd):
            return False
    except OSError:
        return False
    return True


def get_foreground_window() -> int:
    """Return the current foreground HWND without truncating the handle."""
    user32 = _get_user32()
    if user32 is None:
        return 0
    try:
        user32.GetForegroundWindow.restype = wintypes.HWND
        return int(user32.GetForegroundWindow() or 0)
    except OSError:
        return 0


def _force_foreground(hwnd: int) -> bool:
    user32 = _get_user32()
    kernel32 = _get_kernel32()
    if user32 is None or kernel32 is None or not hwnd:
        return False
    try:
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.IsIconic.restype = wintypes.BOOL
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.BringWindowToTop.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.restype = wintypes.BOOL
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
        user32.AttachThreadInput.restype = wintypes.BOOL
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD

        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, _SW_RESTORE)

        foreground_hwnd = get_foreground_window()
        target_thread = int(user32.GetWindowThreadProcessId(hwnd, None))
        foreground_thread = (
            int(user32.GetWindowThreadProcessId(foreground_hwnd, None))
            if foreground_hwnd
            else 0
        )
        current_thread = int(kernel32.GetCurrentThreadId())
        attached_foreground = False
        attached_target = False
        if foreground_thread and foreground_thread != current_thread:
            attached_foreground = bool(
                user32.AttachThreadInput(current_thread, foreground_thread, True)
            )
        if target_thread and target_thread != current_thread:
            attached_target = bool(user32.AttachThreadInput(current_thread, target_thread, True))
        try:
            user32.BringWindowToTop(hwnd)
            return bool(user32.SetForegroundWindow(hwnd))
        finally:
            if attached_foreground:
                user32.AttachThreadInput(current_thread, foreground_thread, False)
            if attached_target:
                user32.AttachThreadInput(current_thread, target_thread, False)
    except OSError as exc:
        LOGGER.debug("SetForegroundWindow failed for hwnd=%s: %s", hwnd, exc)
        return False


def _grab_native_rect(
    x: int,
    y: int,
    width: int,
    height: int,
    *,
    include_layered_windows: bool = True,
) -> QImage:
    """Capture a native-pixel desktop rectangle using GDI."""
    if width <= 0 or height <= 0:
        return QImage()

    user32 = _get_user32()
    gdi32 = _get_gdi32()
    if user32 is None or gdi32 is None:
        return QImage()

    screen_dc = mem_dc = bitmap = old_obj = None
    try:
        user32.GetDC.restype = wintypes.HDC
        user32.GetDC.argtypes = [wintypes.HWND]
        user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        gdi32.CreateCompatibleDC.restype = wintypes.HDC
        gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
        gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
        gdi32.SelectObject.restype = wintypes.HGDIOBJ
        gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        gdi32.BitBlt.argtypes = [
            wintypes.HDC,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HDC,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.DWORD,
        ]
        gdi32.GetDIBits.argtypes = [
            wintypes.HDC,
            wintypes.HBITMAP,
            wintypes.UINT,
            wintypes.UINT,
            ctypes.c_void_p,
            ctypes.POINTER(_BITMAPINFO),
            wintypes.UINT,
        ]
        gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        gdi32.DeleteObject.restype = wintypes.BOOL
        gdi32.DeleteDC.argtypes = [wintypes.HDC]
        gdi32.DeleteDC.restype = wintypes.BOOL

        screen_dc = user32.GetDC(None)
        if not screen_dc:
            return QImage()
        mem_dc = gdi32.CreateCompatibleDC(screen_dc)
        bitmap = gdi32.CreateCompatibleBitmap(screen_dc, width, height)
        if not mem_dc or not bitmap:
            return QImage()

        old_obj = gdi32.SelectObject(mem_dc, bitmap)
        raster_op = _SRCCOPY
        if include_layered_windows:
            raster_op |= _CAPTUREBLT

        if not gdi32.BitBlt(
            mem_dc,
            0,
            0,
            width,
            height,
            screen_dc,
            x,
            y,
            raster_op,
        ):
            return QImage()

        bitmap_info = _BITMAPINFO()
        bitmap_info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bitmap_info.bmiHeader.biWidth = width
        bitmap_info.bmiHeader.biHeight = -height
        bitmap_info.bmiHeader.biPlanes = 1
        bitmap_info.bmiHeader.biBitCount = 32
        bitmap_info.bmiHeader.biCompression = _BI_RGB

        buffer = ctypes.create_string_buffer(width * height * 4)
        rows = gdi32.GetDIBits(
            mem_dc,
            bitmap,
            0,
            height,
            buffer,
            ctypes.byref(bitmap_info),
            _DIB_RGB_COLORS,
        )
        if rows != height:
            return QImage()

        return QImage(
            buffer,
            width,
            height,
            width * 4,
            QImage.Format.Format_RGB32,
        ).copy()
    except (AttributeError, OSError) as exc:
        LOGGER.debug("GDI screen capture failed: %s", exc)
        return QImage()
    finally:
        try:
            if old_obj and mem_dc:
                gdi32.SelectObject(mem_dc, old_obj)
            if bitmap:
                gdi32.DeleteObject(bitmap)
            if mem_dc:
                gdi32.DeleteDC(mem_dc)
            if screen_dc:
                user32.ReleaseDC(None, screen_dc)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            LOGGER.debug("GDI cleanup failed: %s", exc)


def grab_screen_region(screen: QScreen, local_rect: QRect) -> QPixmap:
    """Capture a screen-local Qt logical region."""
    geom = screen.geometry()
    rect = QRect(local_rect).normalized()
    pixmap = screen.grabWindow(
        0,
        geom.x() + rect.x(),
        geom.y() + rect.y(),
        rect.width(),
        rect.height(),
    )
    if not pixmap.isNull() and not _pixmap_is_probably_blank(pixmap):
        return pixmap

    x, y, width, height = qt_screen_local_rect_to_native(screen, local_rect)
    image = _grab_native_rect(
        x,
        y,
        width,
        height,
        include_layered_windows=False,
    )
    if not image.isNull():
        return QPixmap.fromImage(image)
    return pixmap


def grab_screen_snapshot(screen: QScreen) -> QPixmap:
    """Capture one screen before any screenshot overlay is shown."""
    pixmap = screen.grabWindow(0)
    if not pixmap.isNull() and not _pixmap_is_probably_blank(pixmap):
        return pixmap

    full_screen = QRect(QPoint(0, 0), screen.geometry().size())
    x, y, width, height = qt_screen_local_rect_to_native(screen, full_screen)
    image = _grab_native_rect(
        x,
        y,
        width,
        height,
        include_layered_windows=False,
    )
    if not image.isNull():
        return QPixmap.fromImage(image)
    return pixmap


def crop_screen_snapshot(snapshot: QPixmap, screen: QScreen, local_rect: QRect) -> QPixmap:
    """Crop a screen-local logical rectangle from a previously captured screen snapshot."""
    if snapshot.isNull():
        return QPixmap()

    screen_size = screen.geometry().size()
    if screen_size.width() <= 0 or screen_size.height() <= 0:
        return QPixmap()

    rect = QRect(local_rect).normalized().intersected(QRect(QPoint(0, 0), screen_size))
    if rect.isEmpty():
        return QPixmap()

    scale_x = snapshot.width() / screen_size.width()
    scale_y = snapshot.height() / screen_size.height()
    source_rect = QRect(
        round(rect.x() * scale_x),
        round(rect.y() * scale_y),
        max(1, round(rect.width() * scale_x)),
        max(1, round(rect.height() * scale_y)),
    ).intersected(snapshot.rect())
    if source_rect.isEmpty():
        return QPixmap()
    return snapshot.copy(source_rect)


def _pixmap_is_probably_blank(pixmap: QPixmap) -> bool:
    """Detect compositor/overlay failures without rejecting ordinary dark regions too eagerly."""
    image = pixmap.toImage()
    if image.isNull():
        return True

    width = image.width()
    height = image.height()
    if width <= 0 or height <= 0:
        return True

    samples = 0
    blank = 0
    steps = 8
    for row in range(steps):
        y = min(height - 1, round((row + 1) * height / (steps + 1)))
        for col in range(steps):
            x = min(width - 1, round((col + 1) * width / (steps + 1)))
            color = image.pixelColor(x, y)
            samples += 1
            if color.alpha() <= 2 or (
                color.red() <= 2 and color.green() <= 2 and color.blue() <= 2
            ):
                blank += 1
    return samples > 0 and (blank / samples) > 0.98


def grab_window_pixmap(hwnd: int) -> QPixmap:
    """Capture a top-level window's visible bounds."""
    rect = get_window_rect_dwm(hwnd)
    if rect is not None:
        width = int(rect.right - rect.left)
        height = int(rect.bottom - rect.top)
        image = _grab_native_rect(int(rect.left), int(rect.top), width, height)
        if not image.isNull():
            return QPixmap.fromImage(image)

    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return QPixmap()
    return screen.grabWindow(hwnd)


def _send_page_down() -> bool:
    user32 = _get_user32()
    if user32 is None:
        return False
    try:
        user32.keybd_event.argtypes = [
            wintypes.BYTE,
            wintypes.BYTE,
            wintypes.DWORD,
            _ULONG_PTR,
        ]
        user32.keybd_event(_VK_NEXT, 0, 0, 0)
        user32.keybd_event(_VK_NEXT, 0, _KEYEVENTF_KEYUP, 0)
        return True
    except OSError as exc:
        LOGGER.warning("Failed to send PageDown keypress: %s", exc)
        return False


def are_images_similar(img1: QImage, img2: QImage, threshold: float = 0.98) -> bool:
    """Compare two images on a small grid."""
    if img1.size() != img2.size():
        return False

    width = img1.width()
    height = img1.height()
    if width <= 0 or height <= 0:
        return True

    steps = 12
    matches = 0
    total = steps * steps

    for row in range(steps):
        y = min(height - 1, round((row + 1) * height / (steps + 1)))
        for col in range(steps):
            x = min(width - 1, round((col + 1) * width / (steps + 1)))
            if img1.pixel(x, y) == img2.pixel(x, y):
                matches += 1

    return (matches / total) >= threshold


def _row_signatures(image: QImage, columns: list[int]) -> list[tuple[int, ...]]:
    signatures: list[tuple[int, ...]] = []
    for y in range(image.height()):
        row: list[int] = []
        for x in columns:
            value = image.pixel(x, y)
            row.extend(((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF))
        signatures.append(tuple(row))
    return signatures


def find_vertical_offset(img1: QImage, img2: QImage) -> int | None:
    """Find the vertical pixel offset of img2 relative to img1."""
    height = img1.height()
    width = img1.width()
    if img1.size() != img2.size():
        return None
    if height <= 50 or width <= 50:
        return max(1, int(height * 0.8))

    column_count = min(9, max(3, width // 140))
    columns = [
        min(width - 1, round((index + 1) * width / (column_count + 1)))
        for index in range(column_count)
    ]
    sigs1 = _row_signatures(img1, columns)
    sigs2 = _row_signatures(img2, columns)

    min_dy = max(10, int(height * 0.08))
    max_dy = min(height - 1, int(height * 0.96))
    if max_dy <= min_dy:
        return max(1, int(height * 0.8))

    sample_step = max(2, height // 180)
    coarse_step = max(1, height // 220)

    def score(dy: int) -> float:
        overlap = height - dy
        if overlap <= 0:
            return float("inf")
        diff = 0
        comparisons = 0
        for row in range(0, overlap, sample_step):
            a = sigs1[dy + row]
            b = sigs2[row]
            diff += sum(abs(left - right) for left, right in zip(a, b))
            comparisons += len(a)
        return diff / max(1, comparisons)

    best_dy = min(range(min_dy, max_dy + 1, coarse_step), key=score)
    refine_start = max(min_dy, best_dy - coarse_step)
    refine_end = min(max_dy, best_dy + coarse_step)
    refined = min(range(refine_start, refine_end + 1), key=score)
    best_score = score(refined)

    if best_score < 18.0:
        return refined

    LOGGER.warning(
        "Stitching: best match had high difference: %.2f (dy=%s). Using default.",
        best_score,
        refined,
    )
    return max(1, int(height * 0.8))


def stitch_images(images: list[QImage], offsets: list[int]) -> QImage | None:
    """Stitch a sequence of images together using calculated offsets."""
    if not images:
        return None
    if len(images) == 1:
        return images[0]

    width = images[0].width()
    height = images[0].height()
    total_height = height + sum(offsets)

    stitched = QImage(width, total_height, QImage.Format.Format_ARGB32)
    stitched.fill(0)

    painter = QPainter(stitched)
    painter.drawImage(0, 0, images[0])

    y = 0
    for index, offset in enumerate(offsets):
        y += offset
        painter.drawImage(0, y, images[index + 1])

    painter.end()
    return stitched


class ScrollingScreenshotCoordinator(QObject):
    """Orchestrates scrolling screenshots step-by-step using a QTimer."""

    finished = pyqtSignal(QImage)
    failed = pyqtSignal(str)

    def __init__(
        self,
        parent_widget,
        max_pages: int = 8,
        scroll_delay_ms: int = 260,
        initial_delay_ms: int = 120,
    ) -> None:
        super().__init__(parent_widget)
        self.parent_widget = parent_widget
        self.max_pages = max_pages
        self.scroll_delay_ms = scroll_delay_ms
        self.initial_delay_ms = initial_delay_ms

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._capture_step)

        self.hwnd = 0
        self.images: list[QImage] = []
        self.offsets: list[int] = []
        self.last_size = QSize()

    def start(self, target_hwnd: int = 0) -> bool:
        """Initialize and start the scrolling screenshot capture sequence."""
        user32 = _get_user32()
        if user32 is None:
            self.failed.emit("Windows screen capture APIs are unavailable.")
            return False

        self.hwnd = target_hwnd or get_foreground_window()
        try:
            own_hwnd = int(self.parent_widget.winId())
        except (AttributeError, ValueError):
            own_hwnd = 0

        if not is_valid_capture_window(self.hwnd, own_hwnd):
            self.failed.emit("Selected window is invalid for scrolling screenshot.")
            return False

        _force_foreground(self.hwnd)
        QApplication.processEvents()

        self.images = []
        self.offsets = []
        self.last_size = QSize()
        self.timer.start(self.initial_delay_ms)
        return True

    def _capture_step(self) -> None:
        user32 = _get_user32()
        if user32 is None:
            self._fail("Windows screen capture APIs are unavailable.")
            return

        if get_foreground_window() != self.hwnd:
            LOGGER.info("Active window changed during capture. Finishing.")
            self._finish()
            return

        pixmap = grab_window_pixmap(self.hwnd)
        if pixmap.isNull():
            self._fail("Screen capture failed.")
            return

        new_img = pixmap.toImage()
        if new_img.isNull():
            self._fail("Screen capture failed.")
            return

        if not self.images:
            self.images.append(new_img)
            self.last_size = new_img.size()
            self._scroll_and_continue()
            return

        if new_img.size() != self.last_size:
            LOGGER.info("Window resized during scrolling screenshot. Finishing.")
            self._finish()
            return

        if are_images_similar(self.images[-1], new_img):
            LOGGER.info("Content similarity suggests bottom of window is reached.")
            self._finish()
            return

        offset = find_vertical_offset(self.images[-1], new_img)
        if offset is None:
            LOGGER.warning("Stitching alignment failed. Finishing.")
            self._finish()
            return

        self.images.append(new_img)
        self.offsets.append(offset)

        if len(self.images) >= self.max_pages:
            LOGGER.info("Reached maximum page limit.")
            self._finish()
            return

        self._scroll_and_continue()

    def _scroll_and_continue(self) -> None:
        if not _send_page_down():
            self._finish()
            return
        self.timer.start(self.scroll_delay_ms)

    def _finish(self) -> None:
        self.timer.stop()
        if not self.images:
            self.failed.emit("No frames captured.")
            return

        stitched = stitch_images(self.images, self.offsets)
        if stitched is None:
            self.failed.emit("Stitching failed.")
            return
        self.finished.emit(stitched)

    def _fail(self, reason: str) -> None:
        self.timer.stop()
        self.failed.emit(reason)
