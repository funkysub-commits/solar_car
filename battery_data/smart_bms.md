# smart_bms — Python Library for Smart BMS 4.0

A reverse-engineered BLE communication library for Smart BMS battery management systems. Connects over Bluetooth Low Energy and exposes every function available in the official Smart BMS Android app.

## Requirements

- Python 3.10+
- [Bleak](https://github.com/hbldh/bleak) — cross-platform BLE library

```bash
pip install bleak
```

Place `smart_bms.py` in your project directory or on your `PYTHONPATH`.

---

## Quick Start

```python
import asyncio
from smart_bms import SmartBMS

async def main():
    async with SmartBMS("AA:BB:CC:DD:EE:FF") as bms:
        print(f"Battery: {await bms.get_battery_percent()}%")
        print(f"Voltage: {await bms.get_total_voltage()} V")
        print(f"Current: {await bms.get_current()} A")
        print(f"Cells:   {await bms.get_cell_voltages()}")
        print(f"Temps:   {await bms.get_temperatures()}")

asyncio.run(main())
```

The `async with` block handles connecting and disconnecting automatically. You can also manage the lifecycle manually:

```python
bms = SmartBMS("AA:BB:CC:DD:EE:FF")
await bms.connect()
# ... do work ...
await bms.disconnect()
```

---

## Scanning for Devices

Find all Smart BMS units advertising nearby:

```python
from smart_bms import scan_for_bms

devices = await scan_for_bms(timeout=10.0)
for d in devices:
    print(f"{d.name}  {d.address}  RSSI={d.rssi}")
```

The scanner filters for BLE names containing `DL`, `SmartBMS`, `BMS`, `JBD`, or `SP1`. If your BMS advertises a different prefix, pass the MAC address directly to `SmartBMS()`.

---

## Reading Data

### Individual Getters

Each getter sends a fresh BLE read command and returns the parsed value. Use these when you only need one or two fields:

| Method | Returns | Description |
|--------|---------|-------------|
| `get_soc()` | `int` | State of charge (0–100%) |
| `get_battery_percent()` | `int` | Alias for `get_soc()` |
| `get_total_voltage()` | `float` | Pack voltage in volts |
| `get_current()` | `float` | Current in amps (+ charging, − discharging) |
| `get_power()` | `float` | Instantaneous power in watts |
| `get_remaining_capacity()` | `float` | Remaining capacity in Ah |
| `get_nominal_capacity()` | `float` | Full/nominal capacity in Ah |
| `get_cycle_count()` | `int` | Charge/discharge cycle count |
| `get_cell_count()` | `int` | Number of cells in the pack |
| `get_cell_voltages()` | `list[float]` | Per-cell voltages in volts |
| `get_min_cell_voltage()` | `(int, float)` | (cell_number, voltage) of lowest cell |
| `get_max_cell_voltage()` | `(int, float)` | (cell_number, voltage) of highest cell |
| `get_delta_cell_voltage()` | `float` | Max − min cell voltage in volts |
| `get_temperatures()` | `list[float]` | NTC temperature readings in °C |
| `get_mos_temperature()` | `float` | MOSFET temperature in °C |
| `get_protection_status()` | `ProtectionStatus` | Active protection flags |
| `get_charge_mos_state()` | `bool` | Whether charge FET is on |
| `get_discharge_mos_state()` | `bool` | Whether discharge FET is on |
| `get_balance_status()` | `int` | Bitmask of cells being balanced |
| `get_is_balancing()` | `bool` | Whether any cell is balancing |

### Settings Getters

These read the configuration register block:

| Method | Returns | Description |
|--------|---------|-------------|
| `get_cell_ovp()` | `float` | Cell over-voltage protection (V) |
| `get_cell_uvp()` | `float` | Cell under-voltage protection (V) |
| `get_charge_ocp()` | `float` | Charge over-current protection (A) |
| `get_discharge_ocp()` | `float` | Discharge over-current protection (A) |
| `get_password()` | `str` | Current control password |

### Bulk Reads

When you need multiple fields, use bulk reads to minimize BLE round-trips:

```python
# Read all runtime data in one command
info = await bms.refresh()
print(info.soc, info.total_voltage, info.cell_voltages)

# Read settings/configuration
info = await bms.refresh_settings()
print(info.cell_ovp, info.cell_uvp)

# Read everything (runtime + settings)
info = await bms.refresh_all()
```

After any `refresh*()` call, the parsed data is also available on `bms.info` without triggering another read:

```python
await bms.refresh()
print(bms.info.soc)            # no BLE traffic
print(bms.info.cell_voltages)  # no BLE traffic
```

---

## Writing / Control Commands

All write methods return `True` if the BMS acknowledged the command.

### MOS Control

```python
await bms.set_discharge_mos(True)   # enable discharge FET
await bms.set_discharge_mos(False)  # disable discharge FET

await bms.set_charge_mos(True)      # enable charge FET
await bms.set_charge_mos(False)     # disable charge FET
```

### Balancing

```python
await bms.set_balance(True)    # enable active balancing
await bms.set_balance(False)   # disable active balancing
```

### Heating

```python
await bms.set_heating(True)    # turn heating pad on
await bms.set_heating(False)   # turn heating pad off
```

### Force Start

Wake the BMS from sleep / force-start charging:

```python
await bms.set_force_start(True)
```

### Protection Thresholds

```python
# Cell over-voltage protection (millivolts)
await bms.set_cell_ovp(3650)       # 3.650 V

# Cell under-voltage protection (millivolts)
await bms.set_cell_uvp(2800)       # 2.800 V

# Charge over-current protection (value × 100)
await bms.set_charge_ocp(3000)     # 30.00 A

# Discharge over-current protection (value × 100)
await bms.set_discharge_ocp(6000)  # 60.00 A
```

### Password

```python
await bms.set_password("654321")   # max 6 ASCII characters
```

### Time Sync

Synchronise the BMS real-time clock to the current time:

```python
from datetime import datetime

now = datetime.now()
await bms.sync_time(now.year, now.month, now.day,
                    now.hour, now.minute, now.second)
```

### Communication Mode

```python
await bms.set_comm_mode(0x01)   # set protocol/comm mode
```

---

## AT Commands (BLE Module)

These write directly to the BLE module's AT-command characteristic:

```python
# Rename the BLE device (causes disconnect + re-advertise)
await bms.rename_device("MyBattery")

# Change UART baud rate
await bms.set_baud_rate(115200)

# Query BLE module firmware version (may return None on older boards)
version = await bms.query_firmware_version()
print(version)
```

---

## Raw Register Access

For advanced use or debugging, you can read/write arbitrary registers:

```python
# Read 10 registers starting at address 0x0000
raw = await bms.read_registers(0x0000, 10)
print(raw.hex())

# Write a single 16-bit value to a register
await bms.write_register(0x00D8, 0x0001)

# Write multiple registers (hex string payload)
await bms.write_registers(0x00C9, 3, "313233343536")

# Read the history/fault log block
history = await bms.read_history()
print(history.hex())
```

---

## The BMSInfo Dataclass

All parsed data lives in a `BMSInfo` dataclass. Here's the full list of fields:

### Pack-Level
- `total_voltage` — pack voltage (V)
- `current` — current (A, positive = charging)
- `power` — instantaneous power (W)
- `remaining_capacity` — remaining Ah
- `nominal_capacity` — full capacity Ah
- `soc` — state of charge (%)
- `soh` — state of health (%)
- `cycle_count` — charge cycles
- `cycle_capacity` — cumulative Ah throughput

### Cells
- `cell_count` — number of cells
- `cell_voltages` — list of per-cell volts
- `min_cell_voltage` / `max_cell_voltage` — extremes
- `delta_cell_voltage` — max − min
- `avg_cell_voltage` — average
- `min_cell_number` / `max_cell_number` — 1-indexed cell numbers
- `balance_status` — bitmask of active balancing

### Temperatures
- `temperatures` — list of NTC readings (°C)
- `mos_temperature` — MOSFET temp (°C)
- `env_temperature` — environment temp (°C)

### Protection & MOS
- `protection_status` — `ProtectionStatus` flags
- `charge_mos_on` / `discharge_mos_on` — FET states
- `balance_active` — whether balancing is active

### Settings
- `cell_ovp` / `cell_ovp_recovery` — OVP threshold and recovery (V)
- `cell_uvp` / `cell_uvp_recovery` — UVP threshold and recovery (V)
- `pack_ovp` / `pack_uvp` — pack-level voltage protection (V)
- `charge_ocp` / `discharge_ocp` — over-current protection (A)
- `charge_otp` / `charge_utp` — charge temp limits (°C)
- `discharge_otp` / `discharge_utp` — discharge temp limits (°C)
- `balance_start_voltage` — voltage above which balancing begins (V)
- `balance_delta` — cell delta that triggers balancing (V)

### Identity
- `password` — control password
- `production_date` / `sn_code` — manufacturing info
- `mcu_version` / `ble_version` / `machine_version` — firmware versions

### Heating / Force-Start
- `heating_on` / `heating_start_temp` / `heating_stop_temp`
- `force_start_on`

### Communication
- `comm_protocol_type` / `comm_mode`

### Raw Data
- `raw_runtime_hex` — hex string of the raw runtime register block
- `raw_settings_hex` — hex string of the raw settings register block

---

## Protection Status Flags

The `ProtectionStatus` enum supports bitwise operations:

```python
status = await bms.get_protection_status()

if status & ProtectionStatus.CELL_OVP:
    print("Cell over-voltage protection active!")

if status == ProtectionStatus.NONE:
    print("No active protections")

# List all active protections
for flag in ProtectionStatus:
    if flag and status & flag:
        print(f"  Active: {flag.name}")
```

Available flags: `CELL_OVP`, `CELL_UVP`, `PACK_OVP`, `PACK_UVP`, `CHARGE_OTP`, `CHARGE_UTP`, `DISCHARGE_OTP`, `DISCHARGE_UTP`, `CHARGE_OCP`, `DISCHARGE_OCP`, `SHORT_CIRCUIT`, `IC_ERROR`, `MOS_LOCK`.

---

## Polling Loop

Monitor your battery continuously:

```python
async def monitor(address, interval=5.0):
    async with SmartBMS(address) as bms:
        while True:
            info = await bms.refresh()
            print(f"{info.soc}%  {info.total_voltage}V  {info.current}A  "
                  f"Δ{info.delta_cell_voltage*1000:.0f}mV  "
                  f"{'⚡' if info.balance_active else '—'}")
            await asyncio.sleep(interval)
```

---

## Command-Line Interface

The library doubles as a CLI tool:

```bash
# Scan for BMS devices
python smart_bms.py --scan

# Read once from a specific device
python smart_bms.py -a AA:BB:CC:DD:EE:FF

# Poll every 5 seconds
python smart_bms.py -a AA:BB:CC:DD:EE:FF --loop 5
```

---

## Troubleshooting

**Values don't match the app** — The register byte offsets were reverse-engineered and may vary between BMS board revisions. Use `bms.info.raw_runtime_hex` and `bms.info.raw_settings_hex` to compare the raw data against what the app displays, then adjust offsets in `_parse_runtime()` / `_parse_settings()` if needed.

**Connection timeouts** — Increase the timeout: `SmartBMS("AA:BB:CC:DD:EE:FF", timeout=15.0)`. Make sure no other app (including the Smart BMS app) is connected — BLE only allows one connection at a time.

**Device not found during scan** — Your BMS may advertise a name not in the default filter. Pass the MAC address directly to `SmartBMS()` instead of relying on `scan_for_bms()`.

**AT commands don't work** — Some BMS boards don't expose the FFF3 characteristic. `rename_device()` and `set_baud_rate()` will raise an exception in that case.

**Permission errors on Linux** — BLE scanning usually requires root or membership in the `bluetooth` group. Run with `sudo` or add your user: `sudo usermod -aG bluetooth $USER`.

---

## Protocol Reference

For anyone modifying the library, the wire protocol works as follows:

- **Service UUID**: `0000FFF0-0000-1000-8000-00805F9B34FB`
- **Notify char (FFF1)**: subscribe here for responses
- **Write char (FFF2)**: send commands here
- **Read command (D203)**: `D203` + address (2 bytes) + length (2 bytes) + CRC-16 (2 bytes)
- **Write single (D206)**: `D206` + address (2 bytes) + value (2 bytes) + CRC-16 (2 bytes)
- **Write multi (D210)**: `D210` + address (2 bytes) + count (2 bytes) + data + CRC-16 (2 bytes)
- **CRC**: CRC-16/MODBUS (polynomial 0xA001, init 0xFFFF), byte-swapped to big-endian

All multi-byte values are big-endian.