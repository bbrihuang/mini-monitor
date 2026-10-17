# 🖥️ Mini Monitor

A sleek, compact, always-on-top system stats widget for Windows — inspired by NZXT CAM's mini mode.

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Windows-0078D6?logo=windows)
![License](https://img.shields.io/badge/License-MIT-green)

<!-- Replace with an actual screenshot: -->
<!-- ![Mini Monitor Screenshot](screenshot.png) -->

## ✨ Features

| Gauge | What it shows |
|-------|---------------|
| **CPU** | Usage % · Temperature · Core count |
| **GPU** | Usage % · Temperature · VRAM usage |
| **RAM** | Usage % · Used / Total GB |
| **NET** | Active devices on your local network |

- 🎯 **Always on top** — stays visible over all windows
- 🖱️ **Draggable** — left-click and drag to reposition (position is remembered)
- 🔍 **Network Scanner** — right-click to open a full device scanner with ping sweep
- 🎚️ **Adjustable opacity** — scroll wheel to fade in/out
- 🔴 **Hot indicators** — rings turn red when CPU/GPU load ≥ 90% or temps ≥ 80°C
- 🚀 **Start with Windows** — optional auto-start via right-click menu
- 💾 **Persistent settings** — position, opacity, and preferences saved to JSON

## 📦 Installation

### Option 1: pip install (recommended)

```bash
pip install mini-monitor
```

Then run:

```bash
mini-monitor
```

### Option 2: Clone & run directly

```bash
git clone https://github.com/bbrihuang/mini-monitor.git
cd mini-monitor
pip install -r requirements.txt
pythonw mini_monitor.py
```

> **Tip:** Use `pythonw` instead of `python` to run without a console window.

## 🔧 Requirements

- **Python 3.10+**
- **Windows** (uses Windows-specific APIs for startup registry and ARP scanning)
- **NVIDIA GPU** (optional — GPU stats require an NVIDIA card with drivers installed)

### Python packages

| Package | Purpose |
|---------|---------|
| `PySide6` | Qt GUI framework |
| `psutil` | CPU, RAM, and network stats |
| `nvidia-ml-py` | NVIDIA GPU stats via NVML |
| `wmi` | CPU temperature reading (optional) |

## 🖱️ Controls

| Action | What it does |
|--------|-------------|
| **Left-drag** | Move the widget |
| **Scroll wheel** | Adjust opacity (30%–100%) |
| **Right-click** | Context menu |

### Context Menu

- **Always on top** — toggle whether the widget stays above other windows
- **Start with Windows** — add/remove from Windows startup (HKCU registry, no admin needed)
- **🔍 Network Scanner** — open the full network device scanner dialog
- **Quit** — save settings and exit

## 🌡️ CPU Temperature

CPU temperature on Windows requires one of these (tried in order):

1. **WMI ACPI Thermal Zone** — built-in, may require running as Administrator
2. **[OpenHardwareMonitor](https://openhardwaremonitor.org/)** — run in background with WMI enabled
3. **[LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor)** — modern fork, run in background

If none are available, the CPU gauge falls back to showing just the core count.

To enable WMI temperature reading:
```bash
pip install wmi
```

## 🔍 Network Scanner

The NET gauge shows a count of active devices on your local network, refreshed every 15 seconds from the system ARP table.

**Right-click → 🔍 Network Scanner** opens a detailed dialog where you can:
- View all discovered devices (IP, MAC address, type)
- Run a full **/24 ping sweep** to discover more devices

## ⚙️ Configuration

Settings are saved to `mini_monitor.json` in the same directory as the script:

```json
{
  "x": 100,
  "y": 200,
  "opacity": 0.85,
  "on_top": true
}
```

### Customizing appearance

Edit the constants at the top of `mini_monitor.py`:

```python
RING = 76        # gauge diameter (px)
THICK = 7        # ring thickness
PAD = 16         # outer padding
GAP = 18         # space between gauges
REFRESH_MS = 1000       # stats refresh interval
HOT_LOAD = 90           # % that turns a ring red
HOT_TEMP = 80           # °C that turns a ring red
MAX_NET_HOSTS = 30      # host count = 100% on NET ring
```

## 📄 License

[MIT](LICENSE) — do whatever you want with it.
