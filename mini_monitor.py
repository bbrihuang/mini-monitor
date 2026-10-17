"""
Mini Monitor - a compact, always-on-top system stats widget (NZXT CAM "mini mode" style).

Install:  pip install PySide6 psutil nvidia-ml-py
Run:      pythonw mini_monitor.py        (pythonw = no console window)

Controls:
  - Left-drag anywhere to move it (position is remembered)
  - Scroll wheel to change opacity
  - Right-click for: Always on top, Start with Windows, Network Scanner, Quit
"""
import json
import os
import re
import subprocess
import sys
import threading
import time

import psutil
from PySide6.QtCore import QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QHeaderView, QLabel,
    QMenu, QProgressBar, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

# NVIDIA GPU stats via NVML (ships with the NVIDIA driver)
try:
    import pynvml

    pynvml.nvmlInit()
    GPU = pynvml.nvmlDeviceGetHandleByIndex(0)
except Exception:
    GPU = None

# ---------- CPU temperature helpers ----------
_cpu_temp_cache = None  # refreshed each tick


def _read_cpu_temp():
    """Try multiple WMI methods to read CPU temperature on Windows."""
    # Method 1: built-in ACPI thermal zone (no extra software)
    try:
        import wmi
        w = wmi.WMI(namespace=r"root\wmi")
        zones = w.MSAcpi_ThermalZoneTemperature()
        if zones:
            celsius = (zones[0].CurrentTemperature / 10.0) - 273.15
            if 0 < celsius < 150:
                return round(celsius)
    except Exception:
        pass
    # Method 2: OpenHardwareMonitor WMI provider
    try:
        import wmi
        w = wmi.WMI(namespace=r"root\OpenHardwareMonitor")
        for s in w.Sensor():
            if s.SensorType == "Temperature" and "CPU" in s.Name:
                return round(float(s.Value))
    except Exception:
        pass
    # Method 3: LibreHardwareMonitor WMI provider
    try:
        import wmi
        w = wmi.WMI(namespace=r"root\LibreHardwareMonitor")
        for s in w.Sensor():
            if s.SensorType == "Temperature" and "CPU" in s.Name:
                return round(float(s.Value))
    except Exception:
        pass
    return None


# ---------- Network scanner helpers ----------
def _get_local_prefix():
    """Return the /24 prefix of the primary local IP, e.g. '192.168.1'."""
    for _iface, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family.name == "AF_INET" and not addr.address.startswith("127."):
                parts = addr.address.rsplit(".", 1)
                if len(parts) == 2:
                    return parts[0]
    return None


def _scan_arp_table():
    """Parse the Windows ARP table for active hosts. Returns list of (IP, MAC, type)."""
    hosts = []
    try:
        result = subprocess.run(
            ["arp", "-a"], capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        prefix = _get_local_prefix()
        for line in result.stdout.splitlines():
            line = line.strip()
            m = re.match(
                r"(\d+\.\d+\.\d+\.\d+)\s+"
                r"([\da-fA-F]{2}[:-][\da-fA-F]{2}[:-][\da-fA-F]{2}[:-]"
                r"[\da-fA-F]{2}[:-][\da-fA-F]{2}[:-][\da-fA-F]{2})\s+(\w+)",
                line,
            )
            if m:
                ip, mac, typ = m.groups()
                mac = mac.replace("-", ":").lower()
                if mac == "ff:ff:ff:ff:ff:ff":
                    continue
                if prefix and ip.rsplit(".", 1)[0] == prefix:
                    hosts.append((ip, mac, typ))
    except Exception:
        pass
    return hosts


def _ping_sweep(prefix, on_done=None):
    """Ping all /24 hosts to populate the ARP table, then call on_done."""
    try:
        cmd = f"for /L %i in (1,1,254) do @start /b ping -n 1 -w 200 {prefix}.%i >nul 2>&1"
        subprocess.run(
            ["cmd", "/c", cmd], capture_output=True, timeout=90,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception:
        pass
    if on_done:
        on_done()


def _get_active_host_count():
    return len(_scan_arp_table())


# ---------- look & feel (tweak freely) ----------
BG = QColor(22, 22, 28, 235)
TRACK = QColor(255, 255, 255, 28)
TEXT = QColor("#f2f2f5")
SUB = QColor("#8b8b99")
HOT = QColor("#ff4d5e")  # ring turns this color under heavy load / high temp
ACCENTS = {
    "CPU": QColor("#7b5cff"),
    "GPU": QColor("#22c7a9"),
    "RAM": QColor("#ff8a3d"),
    "NET": QColor("#3d9eff"),
}
ICONS = {"CPU": "\uE950", "GPU": "\uE7F8", "RAM": "\uE964", "NET": "\uE968"}
ICON_FONT = "Segoe MDL2 Assets"
FONT = "Segoe UI"

RING = 76        # gauge diameter (px)
THICK = 7        # ring thickness
PAD = 16         # outer padding
GAP = 18         # space between gauges
RADIUS = 18      # panel corner radius
LABEL_H = 38     # room for label + subtext under each ring
REFRESH_MS = 1000
HOT_LOAD = 90    # % load that turns a ring red
HOT_TEMP = 80    # °C that turns a ring red
MAX_NET_SPEED = 1000  # Mbps — 100% on the NET ring (1 Gbps)

NUM_GAUGES = 4   # CPU, GPU, RAM, NET

CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mini_monitor.json")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "MiniMonitor"

# ---------- network speed tracking ----------
_prev_net_io = None
_prev_net_time = None
_net_down_mbps = 0.0
_net_up_mbps = 0.0


def _update_net_speed():
    """Sample psutil.net_io_counters() and compute Mbps since last call."""
    global _prev_net_io, _prev_net_time, _net_down_mbps, _net_up_mbps
    counters = psutil.net_io_counters()
    now = time.time()
    if _prev_net_io is not None and _prev_net_time is not None:
        dt = now - _prev_net_time
        if dt > 0:
            _net_down_mbps = (counters.bytes_recv - _prev_net_io.bytes_recv) * 8 / dt / 1_000_000
            _net_up_mbps = (counters.bytes_sent - _prev_net_io.bytes_sent) * 8 / dt / 1_000_000
    _prev_net_io = counters
    _prev_net_time = now


def _fmt_speed_compact(mbps):
    """Format speed for the ring center (e.g. 0 Mbps, 12 Mbps, 1.2 Gbps)."""
    if mbps < 0.05:
        return "0 Mbps"
    elif mbps < 1.0:
        return f"{mbps:.1f} Mbps"
    elif mbps < 1000.0:
        return f"{mbps:.0f} Mbps"
    else:
        return f"{mbps / 1000.0:.1f} Gbps"


def _fmt_rate(mbps):
    """Format speed for subtext (e.g. 0K, 450K, 12M, 1.2G)."""
    if mbps < 0.05:
        return "0K"
    elif mbps < 1.0:
        return f"{mbps * 1000:.0f}K"
    elif mbps < 100.0:
        return f"{mbps:.1f}M"
    elif mbps < 1000.0:
        return f"{mbps:.0f}M"
    else:
        return f"{mbps / 1000.0:.1f}G"


# ---------- stats ----------
def read_stats():
    """Returns a list of (label, percent, center_val_str, subtext, is_hot)."""
    global _cpu_temp_cache

    _update_net_speed()

    cpu = psutil.cpu_percent(interval=None)

    # CPU temperature (cached so a slow WMI call doesn't block painting)
    temp = _read_cpu_temp()
    if temp is not None:
        _cpu_temp_cache = temp
    cpu_sub = f"{_cpu_temp_cache}°C · {cpu:.0f}%" if _cpu_temp_cache is not None else f"--°C · {cpu:.0f}%"
    cpu_hot = cpu >= HOT_LOAD or (_cpu_temp_cache is not None and _cpu_temp_cache >= HOT_TEMP)

    if GPU is not None:
        try:
            util = float(pynvml.nvmlDeviceGetUtilizationRates(GPU).gpu)
            gpu_temp = pynvml.nvmlDeviceGetTemperature(GPU, pynvml.NVML_TEMPERATURE_GPU)
            vram = pynvml.nvmlDeviceGetMemoryInfo(GPU).used / 2**30
            gpu = (util, f"{util:.0f}%", f"{gpu_temp}°C · {vram:.1f} GB", util >= HOT_LOAD or gpu_temp >= HOT_TEMP)
        except pynvml.NVMLError:
            gpu = (0.0, "0%", "unavailable", False)
    else:
        gpu = (0.0, "0%", "no NVIDIA GPU", False)

    vm = psutil.virtual_memory()

    total_mbps = _net_down_mbps + _net_up_mbps
    if total_mbps <= 0.05:
        net_pct = 0.0
    elif total_mbps <= 100.0:
        net_pct = min(80.0, max(5.0, (total_mbps / 100.0) * 80.0))
    else:
        net_pct = min(100.0, 80.0 + ((total_mbps - 100.0) / 900.0) * 20.0)

    net_val = _fmt_speed_compact(total_mbps)
    net_sub = f"↓{_fmt_rate(_net_down_mbps)}  ↑{_fmt_rate(_net_up_mbps)}"

    return [
        ("CPU", cpu, f"{cpu:.0f}%", cpu_sub, cpu_hot),
        ("GPU", *gpu),
        ("RAM", vm.percent, f"{vm.percent:.0f}%", f"{vm.used / 2**30:.1f} / {vm.total / 2**30:.0f} GB", vm.percent >= HOT_LOAD),
        ("NET", net_pct, net_val, net_sub, False),
    ]


# ---------- Network Scanner dialog ----------
class NetworkScannerDialog(QDialog):
    """Pop-up window listing all discovered devices on the local network."""

    _BG = "#16161c"
    _TBL = "#1e1e26"
    _HDR = "#252530"
    _ALT = "#1a1a24"
    _ACC = "#3d9eff"
    _TXT = "#f2f2f5"
    _DIM = "#8b8b99"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Network Scanner")
        self.setMinimumSize(520, 380)
        self.setStyleSheet(f"""
            QDialog {{ background:{self._BG}; color:{self._TXT}; font-family:'Segoe UI'; }}
            QLabel {{ color:{self._TXT}; }}
            QPushButton {{
                background:{self._ACC}; color:#fff; border:none; border-radius:6px;
                padding:7px 18px; font-weight:bold; font-size:12px;
            }}
            QPushButton:hover {{ background:#5bb0ff; }}
            QPushButton:disabled {{ background:#555; }}
            QTableWidget {{
                background:{self._TBL}; color:{self._TXT}; border:1px solid #333;
                border-radius:6px; gridline-color:#2a2a36; font-size:12px;
                selection-background-color:#2a2a46; alternate-background-color:{self._ALT};
            }}
            QTableWidget::item {{ padding:4px 8px; }}
            QHeaderView::section {{
                background:{self._HDR}; color:{self._DIM}; border:none;
                padding:6px 8px; font-weight:bold; font-size:11px;
            }}
            QProgressBar {{
                border:none; border-radius:4px; background:#252530;
                text-align:center; color:{self._TXT}; font-size:11px;
            }}
            QProgressBar::chunk {{ background:{self._ACC}; border-radius:4px; }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        hdr = QHBoxLayout()
        title = QLabel("🔍  Network Devices")
        title.setStyleSheet("font-size:16px; font-weight:bold;")
        self.status_label = QLabel("")
        self.status_label.setStyleSheet(f"color:{self._DIM}; font-size:11px;")
        self.scan_btn = QPushButton("Scan Network")
        self.scan_btn.clicked.connect(self.start_scan)
        hdr.addWidget(title)
        hdr.addStretch()
        hdr.addWidget(self.status_label)
        hdr.addWidget(self.scan_btn)
        layout.addLayout(hdr)

        self.progress = QProgressBar()
        self.progress.setFixedHeight(6)
        self.progress.setRange(0, 0)
        self.progress.hide()
        layout.addWidget(self.progress)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["#", "IP Address", "MAC Address", "Type"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table)

        self._populate_from_arp()

    def _populate_from_arp(self):
        hosts = _scan_arp_table()
        self._fill_table(hosts)
        self.status_label.setText(f"{len(hosts)} devices (from ARP cache)")

    def _fill_table(self, hosts):
        hosts.sort(key=lambda h: [int(o) for o in h[0].split(".")])
        self.table.setRowCount(len(hosts))
        for row, (ip, mac, typ) in enumerate(hosts):
            idx = QTableWidgetItem(str(row + 1))
            idx.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 0, idx)
            self.table.setItem(row, 1, QTableWidgetItem(ip))
            self.table.setItem(row, 2, QTableWidgetItem(mac))
            self.table.setItem(row, 3, QTableWidgetItem(typ))

    def start_scan(self):
        prefix = _get_local_prefix()
        if not prefix:
            self.status_label.setText("Could not determine local subnet")
            return
        self.scan_btn.setEnabled(False)
        self.scan_btn.setText("Scanning…")
        self.progress.show()
        self.status_label.setText(f"Pinging {prefix}.1-254 …")

        def on_done():
            from PySide6.QtCore import QMetaObject, Qt as _Qt
            QMetaObject.invokeMethod(self, "_scan_finished", _Qt.QueuedConnection)

        threading.Thread(target=_ping_sweep, args=(prefix, on_done), daemon=True).start()

    def _scan_finished(self):
        hosts = _scan_arp_table()
        self._fill_table(hosts)
        self.progress.hide()
        self.scan_btn.setEnabled(True)
        self.scan_btn.setText("Scan Network")
        self.status_label.setText(f"{len(hosts)} devices found")


# ---------- start with Windows (HKCU Run key, no admin needed) ----------
def startup_cmd():
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        exe = exe[: -len("python.exe")] + "pythonw.exe"
    return f'"{exe}" "{os.path.abspath(__file__)}"'


def startup_enabled():
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
        return True
    except (ImportError, OSError):
        return False


def set_startup(enabled):
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if enabled:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, startup_cmd())
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except FileNotFoundError:
                pass


# ---------- widget ----------
class MiniMonitor(QWidget):
    def __init__(self):
        super().__init__()
        self.cfg = self.load_cfg()
        self.on_top = self.cfg.get("on_top", True)
        self.drag = None
        self.scanner_dialog = None

        self.setWindowFlags(self.window_flags())
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowOpacity(self.cfg.get("opacity", 1.0))
        self.setFixedSize(
            PAD * 2 + RING * NUM_GAUGES + GAP * (NUM_GAUGES - 1),
            PAD * 2 + RING + LABEL_H,
        )

        if "x" in self.cfg and QApplication.screenAt(QPoint(self.cfg["x"] + 20, self.cfg["y"] + 20)):
            self.move(self.cfg["x"], self.cfg["y"])

        self.stats = read_stats()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(REFRESH_MS)

    def window_flags(self):
        flags = Qt.FramelessWindowHint | Qt.Tool
        if self.on_top:
            flags |= Qt.WindowStaysOnTopHint
        return flags

    def refresh(self):
        self.stats = read_stats()
        self.update()

    # ----- drawing -----
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        p.setPen(Qt.NoPen)
        p.setBrush(BG)
        p.drawRoundedRect(QRectF(self.rect()), RADIUS, RADIUS)

        icon_font = QFont(ICON_FONT, 12)
        val_font_normal = QFont(FONT, 11, QFont.DemiBold)
        val_font_small = QFont(FONT, 9, QFont.Bold)
        label_font = QFont(FONT, 9, QFont.Bold)
        sub_font = QFont(FONT, 8)

        for i, (label, pct, val_str, sub, hot) in enumerate(self.stats):
            x = PAD + i * (RING + GAP)
            ring = QRectF(x + THICK / 2, PAD + THICK / 2, RING - THICK, RING - THICK)

            pen = QPen(TRACK, THICK)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(ring)

            accent = HOT if hot else ACCENTS[label]
            if pct > 0:
                pen.setColor(accent)
                p.setPen(pen)
                p.drawArc(ring, 90 * 16, int(-pct / 100 * 360 * 16))

            # Icon in center (upper portion)
            p.setFont(icon_font)
            p.setPen(accent)
            p.drawText(QRectF(x, PAD + 13, RING, 20), Qt.AlignCenter, ICONS.get(label, ""))

            # Value in center (lower portion)
            p.setFont(val_font_small if len(val_str) > 5 else val_font_normal)
            p.setPen(TEXT)
            p.drawText(QRectF(x, PAD + 33, RING, 22), Qt.AlignCenter, val_str)

            # Label below ring
            col = QRectF(x - GAP / 2, PAD + RING + 4, RING + GAP, 16)
            p.setFont(label_font)
            p.drawText(col, Qt.AlignHCenter, label)

            # Subtext below label
            p.setPen(SUB)
            p.setFont(sub_font)
            p.drawText(col.translated(0, 16), Qt.AlignHCenter, sub)

    # ----- interaction -----
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self.drag is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self.drag)

    def mouseReleaseEvent(self, _e):
        if self.drag is not None:
            self.drag = None
            self.save_cfg()

    def wheelEvent(self, e):
        step = 0.05 if e.angleDelta().y() > 0 else -0.05
        self.setWindowOpacity(min(1.0, max(0.3, self.windowOpacity() + step)))
        self.save_cfg()

    def contextMenuEvent(self, e):
        menu = QMenu(self)
        top = menu.addAction("Always on top")
        top.setCheckable(True)
        top.setChecked(self.on_top)

        startup = menu.addAction("Start with Windows")
        startup.setCheckable(True)
        startup.setChecked(startup_enabled())
        startup.setEnabled(sys.platform == "win32")

        menu.addSeparator()
        net_scan = menu.addAction("🔍 Network Scanner")
        menu.addSeparator()
        quit_action = menu.addAction("Quit")

        chosen = menu.exec(e.globalPos())
        if chosen is top:
            self.on_top = not self.on_top
            self.setWindowFlags(self.window_flags())
            self.show()
            self.save_cfg()
        elif chosen is startup:
            set_startup(not startup_enabled())
        elif chosen is net_scan:
            self._open_scanner()
        elif chosen is quit_action:
            self.save_cfg()
            QApplication.quit()

    def _open_scanner(self):
        if self.scanner_dialog is None or not self.scanner_dialog.isVisible():
            self.scanner_dialog = NetworkScannerDialog(self)
            self.scanner_dialog.setWindowFlags(
                self.scanner_dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint
            )
            self.scanner_dialog.show()
        else:
            self.scanner_dialog.activateWindow()

    # ----- settings -----
    def load_cfg(self):
        try:
            with open(CONFIG, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def save_cfg(self):
        data = {
            "x": self.x(),
            "y": self.y(),
            "opacity": round(self.windowOpacity(), 2),
            "on_top": self.on_top,
        }
        try:
            with open(CONFIG, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except OSError:
            pass


def main():
    app = QApplication(sys.argv)
    widget = MiniMonitor()
    widget.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
