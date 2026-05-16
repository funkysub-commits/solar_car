#!/usr/bin/env python3
"""
Smart BMS 4.0 — BLE Communication Library
==========================================
Reverse-engineered from the SMART BMS v4.0.51 Android app (com.inuker.bluetooth.daliy).

Protocol summary
-----------------
The BMS communicates over Bluetooth Low Energy using a custom serial protocol:

  Service UUID :  0000fff0-0000-1000-8000-00805f9b34fb
  Notify char  :  0000fff1-0000-1000-8000-00805f9b34fb   (subscribe here → BMS responses)
  Write  char  :  0000fff2-0000-1000-8000-00805f9b34fb   (send commands here)
  Name   char  :  0000fff3-0000-1000-8000-00805f9b34fb   (AT commands: rename, etc.)

Frame format (hex strings, all values big-endian):

  READ request :  D203  <addr_hi><addr_lo>  <len_hi><len_lo>  <crc_hi><crc_lo>
  READ response:  D203  <addr_hi><addr_lo>  <payload …>       <crc_hi><crc_lo>
  WRITE command :  D206  <addr_hi><addr_lo>  <data …>          <crc_hi><crc_lo>

CRC-16/MODBUS (polynomial 0xA001), bytes swapped to big-endian before appending.

The app advertises with name prefix "DL" or "SmartBMS" — scan for those.

Requirements
------------
    pip install bleak

Usage
-----
    python smart_bms_ble.py                  # scan, connect to first BMS, print live data
    python smart_bms_ble.py --address XX:XX:XX:XX:XX:XX   # connect to a specific MAC
"""

import asyncio
import argparse
import struct
import sys
from datetime import datetime

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    print("Please install bleak:  pip install bleak")
    sys.exit(1)


# ── BLE UUIDs (from BluetoothRegulate.java) ──────────────────────────────────
SERVICE_UUID = "0000fff0-0000-1000-8000-00805f9b34fb"
CHAR_NOTIFY  = "0000fff1-0000-1000-8000-00805f9b34fb"  # subscribe for responses
CHAR_WRITE   = "0000fff2-0000-1000-8000-00805f9b34fb"  # send commands
CHAR_NAME    = "0000fff3-0000-1000-8000-00805f9b34fb"  # AT commands

# ── Protocol constants (from BaseVolume.java) ─────────────────────────────────
CMD_READ       = 0xD203
CMD_WRITE      = 0xD206
CMD_WRITE_ALL  = 0xD210
CMD_OTA_HEAD   = 0xA5FF


# ── CRC-16/MODBUS (from BaseVolume$Companion.getCRC) ─────────────────────────
def crc16_modbus(data: bytes) -> int:
    """CRC-16 with polynomial 0xA001 (reflected), init 0xFFFF."""
    crc = 0xFFFF
    for b in data:
        crc ^= b & 0xFF
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    # The app swaps hi/lo before appending (big-endian)
    return ((crc & 0xFF) << 8) | ((crc >> 8) & 0xFF)


def build_read_cmd(address: int, length: int) -> bytes:
    """Build a D203 read command for a register range.

    Parameters
    ----------
    address : int   – start register address (e.g. 0x0000)
    length  : int   – number of registers to read (e.g. 0x007E = 126)

    Returns
    -------
    bytes ready to write to CHAR_WRITE.
    """
    payload = struct.pack(">HHH", CMD_READ, address, length)
    crc = crc16_modbus(payload)
    return payload + struct.pack(">H", crc)


def build_write_cmd(address: int, data: bytes) -> bytes:
    """Build a D206 write command."""
    header = struct.pack(">HH", CMD_WRITE, address)
    payload = header + data
    crc = crc16_modbus(payload)
    return payload + struct.pack(">H", crc)


def verify_crc(frame: bytes) -> bool:
    """Check the CRC of a received frame."""
    if len(frame) < 4:
        return False
    payload = frame[:-2]
    expected = struct.unpack(">H", frame[-2:])[0]
    return crc16_modbus(payload) == expected


# ── Data parsing ─────────────────────────────────────────────────────────────
class BMSData:
    """Parses the register dump returned by the BMS (addresses 0x00–0x7E)."""

    def __init__(self):
        self.total_voltage = 0.0       # V
        self.current = 0.0             # A  (positive = charging)
        self.remaining_capacity = 0.0  # Ah
        self.nominal_capacity = 0.0    # Ah
        self.cycle_count = 0
        self.soc = 0                   # %
        self.soh = 0                   # %
        self.cell_count = 0
        self.cell_voltages = []        # list of V
        self.temperatures = []         # list of °C
        self.balance_status = 0        # bitmask
        self.protection_status = 0     # bitmask
        self.mos_status = 0            # charge/discharge MOS
        self.power = 0.0               # W
        self.min_cell_voltage = 0.0
        self.max_cell_voltage = 0.0
        self.delta_cell_voltage = 0.0
        self.raw_hex = ""

    def parse_response(self, hex_data: str):
        """Parse a complete D203 response payload (after header, before CRC).

        The exact register map varies between BMS models, but the most common
        layout found in the app's LiveDataActivity and DataAnalysisHelper is:

        The data at addresses 0x00-0x7E typically contains:
          Bytes 0-3   : Total voltage (uint32, /1000 → V  or uint16 /100)
          Bytes 4-7   : Current (int32, /1000 → A  or int16 /100)
          Bytes 8-11  : Remaining capacity (uint32, /1000 → Ah)
          Bytes 12-15 : Nominal capacity (uint32, /1000 → Ah)
          Bytes 16-17 : Cycle count
          Then cell voltages (16-bit each, /1000 → V), temperatures, etc.

        This is a best-effort parser — your BMS model may use a slightly
        different layout.  Print raw_hex and compare with the app to fine-tune.
        """
        self.raw_hex = hex_data
        data = bytes.fromhex(hex_data)

        if len(data) < 30:
            return

        # Common SMART BMS register layout (JBD / Daly-style compatible)
        # Many "Smart BMS" devices use the following at address range 0x00:
        self.total_voltage = struct.unpack(">H", data[0:2])[0] / 100.0
        raw_current = struct.unpack(">h", data[2:4])[0]  # signed
        self.current = raw_current / 100.0
        self.remaining_capacity = struct.unpack(">H", data[4:6])[0] / 100.0
        self.nominal_capacity = struct.unpack(">H", data[6:8])[0] / 100.0
        self.cycle_count = struct.unpack(">H", data[8:10])[0]

        # Production date / protection / balance packed in next bytes
        if len(data) > 12:
            self.protection_status = struct.unpack(">H", data[10:12])[0]

        if len(data) > 14:
            self.soc = data[13]  # byte

        # Cell count typically at a fixed offset
        if len(data) > 15:
            self.cell_count = data[14]

        # Cell voltages start after the header block
        cell_offset = 16
        self.cell_voltages = []
        for i in range(self.cell_count):
            idx = cell_offset + i * 2
            if idx + 2 <= len(data):
                mv = struct.unpack(">H", data[idx:idx + 2])[0]
                self.cell_voltages.append(mv / 1000.0)

        if self.cell_voltages:
            self.min_cell_voltage = min(self.cell_voltages)
            self.max_cell_voltage = max(self.cell_voltages)
            self.delta_cell_voltage = self.max_cell_voltage - self.min_cell_voltage

        # Temperatures usually follow cell voltages (NTC values)
        temp_offset = cell_offset + self.cell_count * 2
        self.temperatures = []
        ntc_count = data[temp_offset] if temp_offset < len(data) else 0
        for i in range(min(ntc_count, 8)):
            idx = temp_offset + 1 + i * 2
            if idx + 2 <= len(data):
                raw_t = struct.unpack(">H", data[idx:idx + 2])[0]
                self.temperatures.append((raw_t - 2731) / 10.0)  # kelvin*10 → °C

        self.power = self.total_voltage * abs(self.current)

    def __repr__(self):
        lines = [
            f"═══════════════════════════════════════════════",
            f"  Smart BMS Live Data  ({datetime.now():%H:%M:%S})",
            f"═══════════════════════════════════════════════",
            f"  Total Voltage : {self.total_voltage:>8.2f} V",
            f"  Current       : {self.current:>8.2f} A  ({'charging' if self.current > 0 else 'discharging' if self.current < 0 else 'idle'})",
            f"  Power         : {self.power:>8.1f} W",
            f"  SOC           : {self.soc:>8d} %",
            f"  Remaining     : {self.remaining_capacity:>8.2f} Ah",
            f"  Nominal       : {self.nominal_capacity:>8.2f} Ah",
            f"  Cycles        : {self.cycle_count:>8d}",
            f"  Protection    : 0x{self.protection_status:04X}",
            f"  Cell count    : {self.cell_count}",
        ]
        if self.cell_voltages:
            lines.append(f"  ─── Cell Voltages ───")
            for i, v in enumerate(self.cell_voltages):
                bar = "█" * int(v / self.max_cell_voltage * 20) if self.max_cell_voltage else ""
                lines.append(f"  Cell {i+1:>2d} : {v:.3f} V  {bar}")
            lines.append(f"  Min / Max / Δ : {self.min_cell_voltage:.3f} / {self.max_cell_voltage:.3f} / {self.delta_cell_voltage*1000:.0f} mV")
        if self.temperatures:
            lines.append(f"  ─── Temperatures ───")
            for i, t in enumerate(self.temperatures):
                lines.append(f"  NTC {i+1:>2d}  : {t:.1f} °C")
        lines.append(f"═══════════════════════════════════════════════")
        return "\n".join(lines)


# ── BLE Communication ────────────────────────────────────────────────────────
class SmartBMS:
    """Async BLE client for the Smart BMS."""

    def __init__(self):
        self.client: BleakClient | None = None
        self._rx_buffer = bytearray()
        self._response_event = asyncio.Event()
        self._latest_response = b""
        self.bms_data = BMSData()

    # ── Scanning ──────────────────────────────────────────────────────────
    @staticmethod
    async def scan(timeout: float = 10.0) -> list:
        """Scan for Smart BMS devices (name prefix 'DL' or 'SmartBMS' or 'Smart BMS')."""
        print(f"Scanning for Smart BMS devices ({timeout}s)...")
        devices = await BleakScanner.discover(timeout=timeout)
        bms_devices = []
        for d in devices:
            name = d.name or ""
            if any(prefix in name for prefix in ("DL", "SmartBMS", "Smart BMS", "BMS", "JBD")):
                bms_devices.append(d)
                print(f"  Found: {name} [{d.address}]  RSSI={d.rssi}")
        if not bms_devices:
            print("  No BMS devices found. Showing all BLE devices:")
            for d in sorted(devices, key=lambda x: x.rssi or -999, reverse=True)[:15]:
                print(f"    {d.name or '(unknown)':30s} [{d.address}]  RSSI={d.rssi}")
        return bms_devices

    # ── Connection ────────────────────────────────────────────────────────
    async def connect(self, address: str):
        """Connect to a BMS by MAC address and subscribe to notifications."""
        print(f"Connecting to {address}...")
        self.client = BleakClient(address, timeout=15.0)
        await self.client.connect()
        print(f"Connected!  MTU={self.client.mtu_size}")

        # Subscribe to the notify characteristic (FFF1)
        await self.client.start_notify(CHAR_NOTIFY, self._notification_handler)
        print("Subscribed to BMS notifications on FFF1.")

    async def disconnect(self):
        if self.client and self.client.is_connected:
            await self.client.disconnect()
            print("Disconnected.")

    # ── Notification handler ──────────────────────────────────────────────
    def _notification_handler(self, sender, data: bytearray):
        """Called every time the BMS sends a BLE notification.

        The BMS may split a single response across multiple BLE packets.
        We accumulate bytes and look for complete frames based on the
        protocol headers (D203, D206, etc.).
        """
        self._rx_buffer.extend(data)
        hex_str = self._rx_buffer.hex()

        # Try to find a complete frame
        # D203 responses: header(2) + addr(2) + payload + crc(2)
        # We look for known headers and try to extract complete messages
        while len(hex_str) >= 14:  # minimum frame = 7 bytes = 14 hex chars
            header = hex_str[:4].upper()

            if header == "D203":
                # Read response: D203 + addr(2B) + data + CRC(2B)
                # The length depends on how many registers were requested
                # For now, accumulate until we get a valid CRC or a timeout
                frame_bytes = bytes.fromhex(hex_str)
                if len(frame_bytes) >= 7 and verify_crc(frame_bytes):
                    self._latest_response = frame_bytes
                    self._response_event.set()
                    self._rx_buffer.clear()
                    return
                elif len(frame_bytes) < 260:
                    # Still accumulating
                    return
                else:
                    # Too long without valid CRC, skip 1 byte
                    hex_str = hex_str[2:]
                    self._rx_buffer = bytearray.fromhex(hex_str)

            elif header == "D206":
                # Write acknowledgement
                frame_bytes = bytes.fromhex(hex_str)
                if len(frame_bytes) >= 7 and verify_crc(frame_bytes):
                    self._latest_response = frame_bytes
                    self._response_event.set()
                    self._rx_buffer.clear()
                    return
                elif len(frame_bytes) < 260:
                    return
                else:
                    hex_str = hex_str[2:]
                    self._rx_buffer = bytearray.fromhex(hex_str)
            else:
                # Unknown header — skip one byte and retry
                hex_str = hex_str[2:]
                self._rx_buffer = bytearray.fromhex(hex_str)

    # ── Send & receive ────────────────────────────────────────────────────
    async def send_command(self, cmd: bytes, timeout: float = 5.0) -> bytes:
        """Send a command and wait for the response."""
        self._response_event.clear()
        self._rx_buffer.clear()
        await self.client.write_gatt_char(CHAR_WRITE, cmd, response=True)
        try:
            await asyncio.wait_for(self._response_event.wait(), timeout)
        except asyncio.TimeoutError:
            # Return whatever we have in the buffer
            if self._rx_buffer:
                return bytes(self._rx_buffer)
            return b""
        return self._latest_response

    # ── High-level commands ───────────────────────────────────────────────
    async def read_registers(self, address: int = 0x0000, length: int = 0x007E) -> bytes:
        """Read a range of BMS registers.

        Default reads addresses 0x00 to 0x7E (the main live data block).
        """
        cmd = build_read_cmd(address, length)
        print(f"  → Sending read cmd: {cmd.hex()}")
        response = await self.send_command(cmd)
        if response:
            print(f"  ← Received {len(response)} bytes: {response[:20].hex()}...")
        return response

    async def read_live_data(self) -> BMSData:
        """Read and parse the main live data block (0x00–0x7E)."""
        response = await self.read_registers(0x0000, 0x007E)
        if response and len(response) > 6:
            # Strip header (D203 + addr = 4 bytes) and CRC (2 bytes)
            payload = response[4:-2]
            self.bms_data.parse_response(payload.hex())
        return self.bms_data

    async def read_settings(self) -> bytes:
        """Read the settings/configuration block (0x80–0xEF)."""
        return await self.read_registers(0x0080, 0x006F)

    async def write_register(self, address: int, data: bytes) -> bytes:
        """Write data to a BMS register."""
        cmd = build_write_cmd(address, data)
        print(f"  → Sending write cmd: {cmd.hex()}")
        return await self.send_command(cmd)


# ── Main ──────────────────────────────────────────────────────────────────────
async def main():
    parser = argparse.ArgumentParser(description="Smart BMS BLE Reader")
    parser.add_argument("--address", "-a", help="BMS Bluetooth MAC address")
    parser.add_argument("--scan-only", action="store_true", help="Just scan, don't connect")
    parser.add_argument("--interval", "-i", type=float, default=2.0,
                        help="Polling interval in seconds (default: 2)")
    parser.add_argument("--raw", action="store_true", help="Print raw hex data")
    parser.add_argument("--count", "-n", type=int, default=0,
                        help="Number of reads (0 = continuous)")
    args = parser.parse_args()

    bms = SmartBMS()

    if args.scan_only or not args.address:
        devices = await bms.scan()
        if args.scan_only:
            return
        if not devices:
            print("\nNo BMS found automatically. Use --address XX:XX:XX:XX:XX:XX")
            return
        args.address = devices[0].address
        print(f"\nUsing first found device: {devices[0].name} [{args.address}]")

    try:
        await bms.connect(args.address)
        print()

        iteration = 0
        while True:
            iteration += 1
            data = await bms.read_live_data()
            if args.raw:
                print(f"RAW: {data.raw_hex}")
            print(data)

            if args.count and iteration >= args.count:
                break
            await asyncio.sleep(args.interval)

    except KeyboardInterrupt:
        print("\nStopping...")
    except Exception as e:
        print(f"Error: {e}")
        import traceback; traceback.print_exc()
    finally:
        await bms.disconnect()


if __name__ == "__main__":
    asyncio.run(main())