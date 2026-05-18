# BMS GUI Usage

A real-time telemetry dashboard for the solar car's battery management system, built with [Dear PyGui](https://github.com/hoffstadt/DearPyGui).

The GUI is a thin viewer over [`BMSInfo`](../battery_data/smart_bms.py): the worker thread polls the BMS and pushes a `BMSInfo` snapshot via `bms_gui.update(info)`. Every field the BMS reports has a place in the dashboard.

## Installation

```
pip install dearpygui
```

## Quick Start

The dashboard must run on the **main thread**. Your data-producing code runs in a background worker passed via `worker_callback`.

```python
import asyncio
import time
from display import bms_gui
from battery_data.smart_bms import SmartBMS, mac

bms = SmartBMS(mac)

def data_loop():
    while True:
        info = asyncio.run(bms.refresh_all())
        bms_gui.update(info)
        time.sleep(0.1)

asyncio.run(bms.connect())
bms_gui.start_dashboard(
    num_cells=asyncio.run(bms.get_cell_count()),
    worker_callback=data_loop,
)
```

## API Reference

### `start_dashboard(num_cells=16, worker_callback=None, sections=None)`

Initializes the Dear PyGui context, builds the dashboard layout, and enters the render loop. Blocks until the window is closed, then calls `sys.exit()`.

- `num_cells` — number of cells displayed in the cell matrix.
- `worker_callback` — a zero-argument function. Started in a daemon thread before the render loop begins. Use it to push telemetry via `update()`.
- `sections` — iterable of section names to include. `None` (default) includes every section. Pass a subset to slim the dashboard down. Unknown names raise `ValueError`.

### `update(info: BMSInfo)`

Thread-safe. Replaces the current snapshot. Call this from your worker thread.

### `update_screen()`

No-op, retained for backwards compatibility. The render loop redraws every frame automatically.

## Sections

`ALL_SECTIONS` lists every selectable section:

| Section | Contents |
| --- | --- |
| `options` | Top bar with the °C / °F toggle. |
| `summary` | Top status header: pack voltage, current (signed), power, SoC bar. |
| `pack` | Total voltage, current, power, SoC, SoH, remaining/nominal capacity, cycle count, cycle throughput. |
| `cells` | Per-cell voltage table with min/max markers, balance indicator dots, and a summary row (min, max, avg, Δ). |
| `temperatures` | MOS temperature, environment temperature, all NTC sensor readings. |
| `mos` | Charge MOSFET, discharge MOSFET, active balancing state. |
| `protection` | Grid of every `ProtectionStatus` flag — red when active, gray when inactive. |
| `alarms` | Raw alarm strings reported by the BMS. |
| `settings` | Configured thresholds read from the BMS: cell/pack OVP/UVP, OCP, OTP/UTP, balance start & delta, short-circuit and OCP delays. |
| `identity` | Serial number, production date, MCU/BLE/machine firmware versions, comms protocol and mode. |
| `heating` | Heating on/off, force-start, heating start/stop temperatures. |

### Examples

Show everything (the default):

```python
bms_gui.start_dashboard(num_cells=16, worker_callback=data_loop)
```

Minimal race-day view — just the summary and cells:

```python
bms_gui.start_dashboard(
    num_cells=16,
    worker_callback=data_loop,
    sections=["options", "summary", "cells"],
)
```

Diagnostic view — everything except the heating tab:

```python
bms_gui.start_dashboard(
    num_cells=16,
    worker_callback=data_loop,
    sections=set(bms_gui.ALL_SECTIONS) - {"heating"},
)
```

## Cell colour coding

- Green "Nominal" — ≥ 3.3 V
- Orange "Low" — 3.0–3.3 V
- Red "CRITICAL" — < 3.0 V

A `●` in the Balancing column means the BMS is actively balancing that cell.

## Notes

- Dear PyGui requires the render loop on the main thread — never call `start_dashboard` from a background thread.
- The worker thread is a daemon, so it exits when the main process exits.
- The internal snapshot starts as a fresh `BMSInfo()` (zeros) until your worker pushes real data.
