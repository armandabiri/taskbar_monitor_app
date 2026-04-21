import sys
import os
import psutil
try:
    import winreg
except ImportError:
    winreg = None

from PyQt6.QtWidgets import QApplication, QWidget, QHBoxLayout, QMenu
from PyQt6.QtCore import Qt, QTimer, QPoint, QRectF, QSettings
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QAction, QFont, QPainterPath, QPixmap

class CPUBarWidget(QWidget):
    """Grid of squares representing CPU cores."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.cpu_usages = []
        self.cell_size = 5
        self.spacing = 1
        self.rows = 4
        self.setFixedHeight(self.rows * (self.cell_size + self.spacing))

    def update_usage(self, cores):
        self.cpu_usages = cores
        num_cols = (len(cores) + self.rows - 1) // self.rows
        self.setFixedWidth(num_cols * (self.cell_size + self.spacing))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        for i, usage in enumerate(self.cpu_usages):
            x = (i // self.rows) * (self.cell_size + self.spacing)
            y = (i % self.rows) * (self.cell_size + self.spacing)

            # Interpolate Blue to Red
            ratio = usage / 100.0
            color = QColor(int(45 + 186 * ratio), int(133 - 57 * ratio), int(219 - 159 * ratio))

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(40, 40, 40))
            painter.drawRect(x, y, self.cell_size, self.cell_size)

            if usage > 0:
                painter.setBrush(color)
                painter.drawRect(x, y, self.cell_size, self.cell_size)

            painter.setPen(QColor(255, 255, 255, 30))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(x, y, self.cell_size-1, self.cell_size-1)

class ScopeWidget(QWidget):
    """Generic oscilloscope style monitor for any metric."""
    def __init__(self, label, color, parent=None):
        super().__init__(parent)
        self.setMinimumSize(70, 24)
        self.history = [0.0] * 120
        self.label = label
        self.color = color
        self.display_text = ""
        self.max_val_in_history = 100.0 # Default for % metrics
        self.cached_path = QPainterPath()
        self.grid_pixmap = None

    def update_value(self, val, text, auto_scale=False):
        self.history.pop(0)
        self.history.append(val)
        self.display_text = text
        if auto_scale:
            self.max_val_in_history = max(max(self.history), 1.0)
            
        # Performance: Pre-calculate the path
        w, h = self.width(), self.height()
        path = QPainterPath()
        num_samples = min(len(self.history), w // 2)
        visible = self.history[-num_samples:]
        for i, v in enumerate(visible):
            x = w - (len(visible) - i) * 2
            y = h - 2 - (v / self.max_val_in_history * (h - 4))
            if i == 0: path.moveTo(x, y)
            else: path.lineTo(x, y)
        self.cached_path = path
        self.update()

    def resizeEvent(self, event):
        self.grid_pixmap = None # Invalidate grid cache on resize
        super().resizeEvent(event)

    def get_dynamic_color(self, value):
        # Interpolate from Blue (45, 133, 219) to Red (231, 76, 60)
        ratio = min(max(value / 100.0, 0.0), 1.0)
        r = int(45 + (231 - 45) * ratio)
        g = int(133 + (76 - 133) * ratio)
        b = int(219 + (60 - 219) * ratio)
        return QColor(r, g, b)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()

        # Performance: Use cached grid pixmap
        if not self.grid_pixmap or self.grid_pixmap.size() != self.size():
            self.grid_pixmap = QPixmap(self.size())
            self.grid_pixmap.fill(Qt.GlobalColor.transparent)
            gp = QPainter(self.grid_pixmap)
            gp.setPen(QPen(QColor(255, 255, 255, 12), 1))
            for x in range(0, w + 1, 10): gp.drawLine(x, 0, x, h)
            for y in range(0, h + 1, 6): gp.drawLine(0, y, w, y)
            gp.end()
        painter.drawPixmap(0, 0, self.grid_pixmap)

        # Performance: Draw cached wave
        if self.label in ["CPU", "RAM"]:
            line_color = self.get_dynamic_color(self.history[-1])
        else:
            line_color = QColor(self.color)

        painter.setPen(QPen(line_color, 1.3))
        painter.drawPath(self.cached_path)

        # Text Overlay
        painter.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        full_text = f"{self.label}: {self.display_text}"
        text_rect = self.rect().adjusted(0, 0, 0, -2)
        align = Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter

        painter.setPen(QColor(0, 0, 0, 180))
        painter.drawText(text_rect.translated(1, 1), align, full_text)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(text_rect, align, full_text)

class TaskbarMonitor(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)

        # Persistence & Timer
        self.settings = QSettings("Intelag", "TaskbarMonitor")
        self.interval = self.settings.value("interval", 1000, type=int)
        self.bg_opacity = self.settings.value("bg_opacity", 230, type=int)
        self.old_net = psutil.net_io_counters()

        # Build UI from Config
        self.setup_ui()
        
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_stats)
        self.timer.start(self.interval)
        self.m_drag = self.m_resize = False
        self.load_geometry()

    def setup_ui(self):
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(10, 5, 10, 5)
        self.layout.setSpacing(12)

        self.cpu_grid = CPUBarWidget()
        self.layout.addWidget(self.cpu_grid)

        # Config-driven Scopes
        self.scopes = {
            "cpu": ScopeWidget("CPU", "#4db8ff"),
            "ram": ScopeWidget("RAM", "#a29bfe"),
            "up":  ScopeWidget("UP", "#ff7675"),
            "dn":  ScopeWidget("DN", "#55efc4")
        }
        for s in self.scopes.values():
            self.layout.addWidget(s, 1)

        self.m_drag = self.m_resize = False
        self.load_geometry()

    def format_speed(self, b):
        if b >= 1024*1024: return f"{b/1024/1024:.1f}M"
        return f"{b/1024:.0f}K"

    def update_stats(self):
        try:
            self.raise_()
            # Hardware
            cpu, ram = psutil.cpu_percent(), psutil.virtual_memory().percent
            self.cpu_grid.update_usage(psutil.cpu_percent(percpu=True))
            self.scopes["cpu"].update_value(cpu, f"{int(cpu)}%")
            self.scopes["ram"].update_value(ram, f"{int(ram)}%")

            # Network
            new_net = psutil.net_io_counters()
            up, dn = new_net.bytes_sent - self.old_net.bytes_sent, new_net.bytes_recv - self.old_net.bytes_recv
            self.old_net = new_net
            self.scopes["up"].update_value(up, self.format_speed(up), True)
            self.scopes["dn"].update_value(dn, self.format_speed(dn), True)
        except: pass

    def load_geometry(self):
        x, y = self.settings.value("pos_x", -1, type=int), self.settings.value("pos_y", -1, type=int)
        w, h = self.settings.value("width", -1, type=int), self.settings.value("height", -1, type=int)
        if x != -1: self.move(x, y)
        else: self.move(QApplication.primaryScreen().availableGeometry().width()-500, QApplication.primaryScreen().availableGeometry().height()-40)
        if w != -1: self.resize(w, h)

    def save_geometry(self):
        p = self.pos()
        self.settings.setValue("pos_x", p.x()); self.settings.setValue("pos_y", p.y())
        self.settings.setValue("width", self.width()); self.settings.setValue("height", self.height())
        self.settings.setValue("interval", self.interval)
        self.settings.setValue("bg_opacity", self.bg_opacity)
        self.settings.sync()

    def set_interval(self, ms):
        self.interval = ms
        self.timer.setInterval(self.interval)
        self.settings.setValue("interval", self.interval)
        self.settings.sync()

    def update_opacity(self, value):
        self.bg_opacity = value
        self.update()
        self.settings.setValue("bg_opacity", self.bg_opacity)
        self.settings.sync()

    def is_autostart_enabled(self):
        if not winreg: return False
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
            winreg.QueryValueEx(key, "IntelagTaskbarMonitor")
            return True
        except: return False

    def toggle_autostart(self):
        if not winreg: return
        path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        if self.is_autostart_enabled():
            try:
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE)
                winreg.DeleteValue(key, "IntelagTaskbarMonitor")
            except: pass
        else:
            try:
                cmd = f'"{sys.executable}" "{os.path.abspath(sys.argv[0])}"'
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE)
                winreg.SetValueEx(key, "IntelagTaskbarMonitor", 0, winreg.REG_SZ, cmd)
            except: pass

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(10, 10, 10, self.bg_opacity))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRect(self.rect())

    def get_edge(self, p):
        m, w, h = 10, self.width(), self.height()
        e = ""
        if p.y() < m: e = "top"
        elif p.y() > h - m: e = "bottom"
        if p.x() < m: e += "left" if not e else "-left"
        elif p.x() > w - m: e += "right" if not e else "-right"
        return e if e else None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            e = self.get_edge(event.pos())
            if e: self.m_resize, self.m_ResizeEdge = True, e
            else: self.m_drag, self.m_DragPos = True, event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event):
        if self.m_resize:
            r, p = self.geometry(), event.globalPosition().toPoint()
            if "right" in self.m_ResizeEdge: r.setRight(p.x())
            if "bottom" in self.m_ResizeEdge: r.setBottom(p.y())
            if "left" in self.m_ResizeEdge: r.setLeft(p.x())
            if "top" in self.m_ResizeEdge: r.setTop(p.y())
            if r.width() > 150 and r.height() > 20: self.setGeometry(r)
        elif self.m_drag: self.move(event.globalPosition().toPoint() - self.m_DragPos)
        else:
            e = self.get_edge(event.pos())
            if e:
                if e in ["right", "left"]: self.setCursor(Qt.CursorShape.SizeHorCursor)
                elif e in ["bottom", "top"]: self.setCursor(Qt.CursorShape.SizeVerCursor)
                elif e in ["bottom-right", "top-left"]: self.setCursor(Qt.CursorShape.SizeFDiagCursor)
                else: self.setCursor(Qt.CursorShape.SizeBDiagCursor)
            else: self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, event):
        self.m_drag = self.m_resize = False
        self.save_geometry()

    def contextMenuEvent(self, event):
        from PyQt6.QtWidgets import QWidgetAction, QSlider, QVBoxLayout, QLabel
        m = QMenu(self)
        m.setStyleSheet("""
            QMenu { background-color: #1a1a1a; color: white; border: 1px solid #333; padding: 5px; }
            QMenu::item:selected { background-color: #333; }
            QLabel { color: #aaa; font-size: 10px; padding: 0 5px; }
        """)

        # Transparency Slider
        trans_action = QWidgetAction(self)
        container = QWidget()
        cl = QVBoxLayout(container)
        cl.setContentsMargins(10, 5, 10, 5)
        label = QLabel("Background Opacity")
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(50, 255)
        slider.setValue(self.bg_opacity)
        slider.setFixedWidth(120)
        slider.valueChanged.connect(self.update_opacity)
        cl.addWidget(label); cl.addWidget(slider)
        trans_action.setDefaultWidget(container)
        m.addAction(trans_action)

        m.addSeparator()

        # Interval Submenu
        int_menu = m.addMenu("Update Interval")
        intervals = [("0.1s (Ultra)", 100), ("0.5s (Fast)", 500), ("1.0s (Normal)", 1000), ("2.0s (Slow)", 2000), ("5.0s (Eco)", 5000)]
        for lbl, ms in intervals:
            act = QAction(lbl, self)
            act.setCheckable(True)
            act.setChecked(self.interval == ms)
            act.triggered.connect(lambda checked, v=ms: self.set_interval(v))
            int_menu.addAction(act)

        m.addSeparator()

        a = QAction("Auto Start with Windows", self)
        a.setCheckable(True); a.setChecked(self.is_autostart_enabled())
        a.triggered.connect(self.toggle_autostart)
        m.addAction(a); m.addSeparator()
        q = QAction("Exit", self); q.triggered.connect(QApplication.instance().quit)
        m.addAction(q); m.exec(event.globalPos())

if __name__ == '__main__':
    app = QApplication(sys.argv)
    psutil.cpu_percent(interval=0.1)
    monitor = TaskbarMonitor()
    monitor.show()
    sys.exit(app.exec())
