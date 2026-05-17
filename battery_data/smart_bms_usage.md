# smart_bms — Python Library for Smart BMS 4.0

A reverse-engineered BLE communication library for Smart BMS battery management systems. Connects over Bluetooth Low Energy and exposes every function available in the official Smart BMS Android app (v4.0.51).

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

```python
from smart_bms import scan_for_bms

devices = await scan_for_bms(timeout=10.0)
for d in devices:
    print(f"{d.name}  {d.address}  RSSI={d.rssi}")
```

The scanner filters for BLE names containing `DL`, `SmartBMS`, `BMS`, `JBD`, or `SP1`. If your BMS advertises a different name, pass the MAC address directly to `SmartBMS()`.

---

## All Available Functions

### Connection

| Method | Description |
|--------|-------------|
| `connect()` | Connect and subscribe to BLE notifications |
| `disconnect()` | Gracefully disconnect |
| `is_connected` | Property — whether BLE link is active |

Also supports `async with SmartBMS(mac) as bms:` context manager.

### Bulk Data Reads

| Method | Returns | Description |
|--------|---------|-------------|
| `refresh()` | `BMSInfo` | Read all runtime data (cells, voltage, current, SOC, temps, protection, balance) |
| `refresh_settings()` | `BMSInfo` | Read configuration block (OVP, UVP, OCP thresholds, capacity) |
| `refresh_all()` | `BMSInfo` | Read both runtime + settings in one call |
| `info` | `BMSInfo` | Property — last read data, no BLE traffic |

### Runtime Getters

Each triggers a fresh BLE read and returns the parsed value.

| Method | Returns | Description |
|--------|---------|-------------|
| `get_soc()` | `float` | State of charge (0–100%) |
| `get_battery_percent()` | `float` | Alias for `get_soc()` |
| `get_total_voltage()` | `float` | Pack voltage in volts |
| `get_current()` | `float` | Current in amps (+ charging, − discharging) |
| `get_power()` | `float` | Instantaneous power in watts |
| `get_remaining_capacity()` | `float` | Remaining capacity in Ah |
| `get_nominal_capacity()` | `float` | Full capacity in Ah (from settings block) |
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
| `get_is_balancing()` | `bool` | Whether balancing is active |

### Settings Getters

| Method | Returns | Description |
|--------|---------|-------------|
| `get_cell_ovp()` | `float` | Cell over-voltage protection threshold (V) |
| `get_cell_uvp()` | `float` | Cell under-voltage protection threshold (V) |
| `get_charge_ocp()` | `float` | Charge over-current protection (A) |
| `get_discharge_ocp()` | `float` | Discharge over-current protection (A) |
| `get_password()` | `str` | Current control password |

### Control Commands

All return `True` if the BMS acknowledged.

| Method | Description |
|--------|-------------|
| `set_discharge_mos(on)` | Turn discharge MOSFET on/off |
| `set_charge_mos(on)` | Turn charge MOSFET on/off |
| `set_balance(on)` | Enable/disable active balancing |
| `set_heating(on)` | Turn heating pad on/off |
| `set_force_start(on)` | Force-start / wake from sleep |
| `set_password(new_pwd)` | Change control password (max 6 ASCII chars) |
| `sync_time(y,m,d,h,m,s)` | Sync BMS real-time clock |
| `set_cell_ovp(mv)` | Set cell OVP threshold (millivolts) |
| `set_cell_uvp(mv)` | Set cell UVP threshold (millivolts) |
| `set_charge_ocp(mA)` | Set charge OCP (milliamps) |
| `set_discharge_ocp(mA)` | Set discharge OCP (milliamps) |
| `set_comm_mode(mode)` | Set communication protocol mode |

### AT / Identity Commands

| Method | Description |
|--------|-------------|
| `rename_device(name)` | Change BLE advertised name (causes disconnect) |
| `set_baud_rate(baud)` | Change UART baud rate |
| `query_firmware_version()` | Query BLE module firmware version |

### Raw Register Access

| Method | Returns | Description |
|--------|---------|-------------|
| `read_registers(addr, len)` | `bytes` | Read arbitrary registers |
| `write_register(addr, val)` | `bool` | Write a single 16-bit register |
| `write_registers(addr, count, hex)` | `bool` | Write multiple registers (D210) |
| `read_history()` | `bytes` | Read history/fault log block |

### Module-Level

| Function | Returns | Description |
|----------|---------|-------------|
| `scan_for_bms(timeout)` | `list[BLEDevice]` | Scan for Smart BMS devices |

---

## Reading Data

### Individual Getters

Use these when you only need one or two fields. Each sends a fresh BLE read:

```python
soc = await bms.get_battery_percent()    # 56.2
volts = await bms.get_total_voltage()     # 53.2
amps = await bms.get_current()            # -12.5 (discharging)
```

### Bulk Reads

When you need multiple fields, use bulk reads to minimize BLE round-trips:

```python
info = await bms.refresh()
print(info.soc, info.total_voltage, info.current)
print(info.cell_voltages)
print(info.temperatures)

# Settings need a separate read
info = await bms.refresh_settings()
print(info.cell_ovp, info.nominal_capacity)

# Or grab everything at once
info = await bms.refresh_all()
```

After any `refresh*()` call, the data stays on `bms.info` without triggering another read:

```python
await bms.refresh()
print(bms.info.soc)            # no BLE traffic
print(bms.info.cell_voltages)  # no BLE traffic
```

---

## Writing / Control Commands

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

```python
await bms.set_force_start(True)   # wake BMS from sleep
```

### Protection Thresholds

```python
await bms.set_cell_ovp(3650)       # cell OVP = 3.650 V
await bms.set_cell_uvp(2800)       # cell UVP = 2.800 V
await bms.set_charge_ocp(28800)    # charge OCP = 28.8 A
await bms.set_discharge_ocp(31500) # discharge OCP = 31.5 A
```

### Password

```python
await bms.set_password("654321")   # max 6 ASCII characters
```

### Time Sync

```python
from datetime import datetime

now = datetime.now()
await bms.sync_time(now.year, now.month, now.day,
                    now.hour, now.minute, now.second)
```

---

## Protection Status Flags

```python
status = await bms.get_protection_status()

if status & ProtectionStatus.CELL_OVP:
    print("Cell over-voltage protection active!")

if status == ProtectionStatus.NONE:
    print("No active protections")

for flag in ProtectionStatus:
    if flag and status & flag:
        print(f"  Active: {flag.name}")
```

Available flags: `CELL_OVP`, `CELL_UVP`, `PACK_OVP`, `PACK_UVP`, `CHARGE_OTP`, `CHARGE_UTP`, `DISCHARGE_OTP`, `DISCHARGE_UTP`, `CHARGE_OCP`, `DISCHARGE_OCP`, `SHORT_CIRCUIT`, `IC_ERROR`, `MOS_LOCK`.

---

## Polling Loop

```python
async def monitor(address, interval=5.0):
    async with SmartBMS(address) as bms:
        while True:
            info = await bms.refresh()
            print(f"{info.soc:.1f}%  {info.total_voltage:.1f}V  "
                  f"{info.current:.1f}A  Δ{info.delta_cell_voltage*1000:.0f}mV  "
                  f"MOS {info.mos_temperature}°C  "
                  f"{'⚡' if info.balance_active else '—'}")
            await asyncio.sleep(interval)
```

---

## The BMSInfo Dataclass

All parsed data lives in a `BMSInfo` dataclass. Full field reference:

### Pack-Level
- `total_voltage` — pack voltage (V), raw/10
- `current` — current (A), (raw−30000)/10, positive = charging
- `power` — instantaneous power (W)
- `remaining_capacity` — remaining Ah, raw/10
- `nominal_capacity` — full capacity Ah (from settings reg 0x80), raw/10
- `soc` — state of charge (%), raw/10
- `cycle_count` — charge cycles

### Cells
- `cell_count` — number of cells
- `cell_voltages` — list of per-cell volts, raw/1000
- `min_cell_voltage` / `max_cell_voltage` — extremes (V)
- `delta_cell_voltage` — max − min (V)
- `avg_cell_voltage` — average (V)
- `min_cell_number` / `max_cell_number` — 1-indexed cell numbers
- `balance_status` — bitmask of active balancing
- `balance_active` — whether balancing is on

### Temperatures
- `temperatures` — list of NTC readings (°C), formula: raw − 40
- `mos_temperature` — MOSFET temp (°C), formula: raw − 40
- `env_temperature` — min temp reading (°C)

### Protection & MOS
- `protection_status` — `ProtectionStatus` flags
- `charge_mos_on` / `discharge_mos_on` — FET states
- `alarm_info` — list of active alarm strings

### Settings
- `cell_ovp` / `cell_ovp_recovery` — OVP threshold and recovery (V)
- `cell_uvp` / `cell_uvp_recovery` — UVP threshold and recovery (V)
- `pack_ovp` / `pack_uvp` — pack-level voltage protection (V)
- `charge_ocp` / `discharge_ocp` — over-current protection (A)
- `charge_otp` / `discharge_otp` — over-temperature protection (°C)
- `balance_start_voltage` — voltage above which balancing begins (V)
- `balance_delta` — cell delta that triggers balancing (V)
- `short_circuit_delay` — SC protect delay (µs)
- `ocp_delay` — OCP delay (ms)

### Identity
- `sn_code` — serial number (ASCII from regs 0x57–0x62)
- `password` — control password
- `production_date` — manufacturing date
- `mcu_version` / `ble_version` / `machine_version` — firmware versions

### Raw Data
- `raw_runtime_hex` — hex string of the full runtime register block
- `raw_settings_hex` — hex string of the full settings register block

---

## Command-Line Interface

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

**Connection timeouts** — Increase the timeout: `SmartBMS("...", timeout=20.0)`. Make sure no other app (including the Smart BMS app) is connected — BLE only allows one active connection at a time.

**Device not found during scan** — Your BMS may advertise a name not in the default filter. Pass the MAC address directly to `SmartBMS()`.

**Permission errors on Linux** — BLE scanning requires root or `bluetooth` group membership. Run with `sudo` or: `sudo usermod -aG bluetooth $USER`.

**Debugging** — Enable debug logging to see every BLE chunk arrive:
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

---

## Verified Register Map

The following register layout was verified against both the decompiled APK source code and live hardware data. All values confirmed to match the Smart BMS 4.0 app display.

### Runtime Block (D203 read from 0x0000, length 0x7E)

| Register | Byte Offset | Field | Formula | Verified |
|----------|-------------|-------|---------|----------|
| 0x00–0x1F | 0–63 | Cell voltages 1–32 | raw × 0.001 → V | ✓ APK `analysisRunDataInfo` |
| 0x20–0x27 | 64–79 | Temperatures 1–8 | raw − 40 → °C, 0xFF = no sensor | ✓ APK `batteryTemperArray` |
| 0x28 | 80 | Total voltage | raw / 10 → V | ✓ hardware: 532 → 53.2V |
| 0x29 | 82 | Current | (raw − 30000) / 10 → A | ✓ APK `electricity` formula |
| 0x2A | 84 | SOC | raw / 10 → % | ✓ hardware: 562 → 56.2% |
| 0x2B | 86 | Max cell voltage | raw → mV | ✓ |
| 0x2C | 88 | Min cell voltage | raw → mV | ✓ |
| 0x2D | 90 | Max temperature | raw − 40 → °C | ✓ |
| 0x2E | 92 | Min temperature | raw − 40 → °C | ✓ |
| 0x30 | 96 | Remaining capacity | raw / 10 → Ah | ✓ 562 → 56.2 Ah at 56.2% of 100 Ah |
| 0x31 | 98 | Cell count | raw | ✓ 16 |
| 0x32 | 100 | Temp sensor count | raw | ✓ 2 |
| 0x33 | 102 | MOS status | bitmask (bit0=CHG, bit1=DSG) | ✓ |
| 0x34 | 104 | Protection status | bitmask | ✓ |
| 0x37 | 110 | Average cell voltage | raw → mV | ✓ |
| 0x38 | 112 | Cell voltage delta | raw → mV | ✓ 3 mV |
| 0x3A–0x3D | 116–123 | Alarm info 1–4 | binary flags | ✓ APK `alarmInfo1–4` |
| 0x3E | 124 | Cycle count | raw | ✓ APK `strLiChengHex` |
| 0x3F | 126 | Balance on/off | 1 = on | ✓ APK `strJunHengOpenHex` |
| 0x40 | 128 | Balance current | (raw − 30000) / 10 → A | ✓ APK `fJunHengDianLiu` |
| 0x41 | 130 | Balance position | raw | ✓ APK `iJunHengWeiZhi` |
| 0x42 | 132 | MOS temperature | raw − 40 → °C | ✓ APK `analyMOSTemperature` |
| 0x57–0x62 | 174–197 | Serial number | ASCII | ✓ APK `analySNCode` |

### Settings Block (D203 read from 0x0080, length 0x29)

| Register | Byte Offset | Field | Formula | Verified |
|----------|-------------|-------|---------|----------|
| 0x80 | 0 | Nominal capacity | raw / 10 → Ah | ✓ 1000 → 100.0 Ah |
| 0x81 | 2 | Balance voltage | raw / 1000 → V | ✓ 3200 → 3.200V |
| 0x8A | 20 | Cell OVP | raw / 1000 → V | ✓ 3600 → 3.600V |
| 0x8B | 22 | Cell OVP recovery | raw / 1000 → V | ✓ 3500 → 3.500V |
| 0x8D | 26 | Cell UVP recovery | raw / 1000 → V | ✓ 3000 → 3.000V |
| 0x8E | 28 | Cell UVP | raw / 1000 → V | ✓ 2800 → 2.800V |
| 0x8F | 30 | Pack OVP | raw / 10 → V | ✓ 560 → 56.0V |
| 0x91 | 34 | Pack UVP | raw / 10 → V | ✓ 480 → 48.0V |
| 0x93 | 38 | Charge OCP | raw / 1000 → A | ✓ 28800 → 28.8A |
| 0x95 | 42 | Discharge OCP | raw / 1000 → A | ✓ 31500 → 31.5A |
| 0x97 | 46 | Charge OTP | raw − 40 → °C | ✓ |
| 0x9B | 54 | Discharge OTP | raw − 40 → °C | ✓ |
| 0x9F | 62 | SC protect delay | raw → µs | ✓ 500 µs |
| 0xA0 | 64 | OCP delay | raw → ms | ✓ 600 ms |
| 0xA3 | 70 | Balance start voltage | raw / 1000 → V | ✓ 3400 → 3.400V |
| 0xA4 | 72 | Balance delta | raw / 1000 → V | ✓ 20 → 0.020V |

---

## Protocol Reference

For anyone modifying the library:

- **Service UUID**: `0000FFF0-0000-1000-8000-00805F9B34FB`
- **Notify char (FFF1)**: subscribe here for BMS responses
- **Write char (FFF2)**: send commands here
- **AT char (FFF3)**: AT commands for BLE module (rename, baud)
- **Read command (D203)**: `D203` + address (2B) + length (2B) + CRC (2B)
- **Write single (D206)**: `D206` + address (2B) + value (2B) + CRC (2B)
- **Write multi (D210)**: `D210` + address (2B) + count (2B) + data + CRC (2B)
- **CRC**: CRC-16/MODBUS (poly 0xA001, init 0xFFFF), byte-swapped to big-endian
- **Current encoding**: offset by 30000 (30000 = 0A, 30100 = +10A, 29900 = −10A)
- **Temperature encoding**: raw − 40 = °C, 0xFF = no sensor
- **All multi-byte values**: big-endian