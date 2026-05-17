# BMS GUI Usage

A real-time telemetry dashboard for the solar car's battery management system and solar array, built with [Dear PyGui](https://github.com/hoffstadt/DearPyGui).

## Installation

```
pip install dearpygui
```

## Quick Start

The dashboard must run on the **main thread**. Your data-producing code runs in a background worker passed via `worker_callback`.

```python
import time
import random
from display import bms_gui

def my_data_loop():
    while True:
        bms_gui.battery_soc(0.85)
        bms_gui.battery_temp(28.4)
        bms_gui.battery_cells([3.7 + random.uniform(-0.1, 0.1) for _ in range(16)])
        bms_gui.solar_performance(array_watts=450.0, bus_amps=12.3, mppt_efficiency_pct=96.5)
        time.sleep(0.5)

bms_gui.start_dashboard(num_cells=16, worker_callback=my_data_loop)
```

## API Reference

### `start_dashboard(num_cells=16, worker_callback=None)`

Initializes the Dear PyGui context, builds the dashboard layout, and enters the render loop. Blocks until the window is closed, then calls `sys.exit()`.

- `num_cells` — number of cells displayed in the telemetry matrix.
- `worker_callback` — a zero-argument function. Started in a daemon thread before the render loop begins. Use it to push telemetry into the GUI via the write functions below.

### Write functions (call from your worker thread)

All write functions are thread-safe.

| Function | Description |
| --- | --- |
| `battery_soc(percentage_decimal)` | State of charge as a 0.0–1.0 decimal. Clamped to range. |
| `battery_temp(celsius_value)` | Pack core temperature in °C. Display unit toggles via the Fahrenheit checkbox. |
| `battery_cells(voltage_list)` | Per-cell voltages. Extra entries beyond `num_cells` are ignored. |
| `solar_performance(array_watts, bus_amps, mppt_efficiency_pct)` | Solar array input power, bus current, and MPPT efficiency percentage. |
| `update_screen()` | No-op. The render loop redraws every frame automatically. |

## Dashboard Sections

- **System Options** — toggle temperature units between °C and °F.
- **Solar Array & Powertrain Performance** — array watts, bus amps, MPPT efficiency.
- **Battery Pack Status** — SoC progress bar and pack temperature.
- **Individual Cell Telemetry Matrix** — per-cell voltage with color-coded balance status:
  - Green "Nominal" — ≥ 3.5 V
  - Orange "Low Balance" — 3.0–3.5 V
  - Red "CRITICAL LOW" — < 3.0 V

## Notes

- Dear PyGui requires the render loop on the main thread — do not call `start_dashboard` from a background thread.
- The worker thread is a daemon, so it exits when the main process exits.
- The internal cache initializes to a full pack (SoC 1.0, 3.7 V per cell, 25 °C) until your worker pushes real data.
