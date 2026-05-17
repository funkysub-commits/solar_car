"""
smart_bms - Bluetooth Low Energy library for Smart BMS 4.0 batteries
=====================================================================
Reverse-engineered from SMART BMS v4.0.51 (com.inuker.bluetooth.daliy).

Usage
-----
    import asyncio
    from smart_bms import SmartBMS

    async def main():
        bms = SmartBMS("AA:BB:CC:DD:EE:FF")
        await bms.connect()

        print(await bms.get_soc())               # 87
        print(await bms.get_total_voltage())      # 52.34
        print(await bms.get_cell_voltages())      # [3.271, 3.280, ...]
        print(await bms.get_temperatures())       # [24.5, 23.1]

        await bms.set_discharge_mos(True)
        await bms.disconnect()

    asyncio.run(main())

Requirements: pip install bleak
"""

from __future__ import annotations

import asyncio
import struct
import logging
from dataclasses import dataclass, field
from enum import IntFlag, IntEnum
from typing import Optional

from bleak import BleakClient, BleakScanner, BLEDevice

__all__ = [
    "SmartBMS",
    "BMSInfo",
    "ProtectionStatus",
    "scan_for_bms",
]

log = logging.getLogger("smart_bms")

mac = "41:18:12:01:18:4B"

# ─── BLE identifiers (BluetoothRegulate.java) ───────────────────────────────
SERVICE_UUID    = "0000fff0-0000-1000-8000-00805f9b34fb"
CHAR_NOTIFY     = "0000fff1-0000-1000-8000-00805f9b34fb"
CHAR_WRITE      = "0000fff2-0000-1000-8000-00805f9b34fb"
CHAR_NAME       = "0000fff3-0000-1000-8000-00805f9b34fb"
CHAR_NAME_B35   = "0000fffa-0000-1000-8000-00805f9b34fb"

# Secondary service for OTA / secret-key on newer boards (B40+)
SERVICE_V_UUID  = "02f00000-0000-0000-0000-00000000fe00"
CHAR_V_RW       = "02f00000-0000-0000-0000-00000000ff04"
CHAR_V_SK       = "02f00000-0000-0000-0000-00000000ff05"
CHAR_OTA_READ   = "02f00000-0000-0000-0000-00000000ff02"
CHAR_OTA_WRITE  = "02f00000-0000-0000-0000-00000000ff01"

# ─── Protocol constants (BaseVolume.java) ────────────────────────────────────
_HDR_READ       = "D203"
_HDR_WRITE      = "D206"
_HDR_WRITE_MULTI = "D210"
_HDR_OTA_S19    = "A5FF"
_HDR_OTA_BIN    = "8110"
_HDR_OTA_BIN_R  = "5110"

# ─── Register map (reverse-engineered from the app) ─────────────────────────
# Read ranges
REG_RUNTIME_START   = 0x0000   # live data block
REG_RUNTIME_LEN     = 0x007E   # 126 registers → 252 bytes
REG_SETTINGS_START  = 0x0080   # settings / configuration block
REG_SETTINGS_LEN    = 0x0029   # 41 registers (MainControlActivity timer)
REG_SETTINGS2_START = 0x00DF   # extended settings
REG_SETTINGS2_LEN   = 0x0010   # 16 registers
REG_LAST_BATT_START = 0x003E   # last battery info
REG_LAST_BATT_LEN   = 0x0008   # 8 registers
REG_HISTORY_START   = 0x0063   # history block
REG_HISTORY_LEN     = 0x001B   # 27 registers

# Write addresses (from D206/D210 handlers in analysisPressValueByCan)
REG_PASSWORD        = 0x00C9   # 3 bytes (6 ASCII chars)
REG_COMM_MODE       = 0x00D1   # communication mode & protocol type
REG_TIME_SYNC       = 0x00D2   # time sync (read response)
REG_TIME_WRITE      = 0x00D4   # time sync (write)
REG_BALANCE_CURRENT = 0x00D7   # balance current setting
REG_BALANCE_STATE   = 0x00D8   # balance on/off state
REG_HEATING         = 0x00E3   # heating settings
REG_FORCE_START     = 0x00E4   # force start / force charge


class ProtectionStatus(IntFlag):
    """Protection flag bits from the BMS status register."""
    NONE                    = 0
    CELL_OVP               = 1 << 0   # cell over-voltage
    CELL_UVP               = 1 << 1   # cell under-voltage
    PACK_OVP               = 1 << 2   # pack over-voltage
    PACK_UVP               = 1 << 3   # pack under-voltage
    CHARGE_OTP             = 1 << 4   # charge over-temperature
    CHARGE_UTP             = 1 << 5   # charge under-temperature
    DISCHARGE_OTP          = 1 << 6   # discharge over-temperature
    DISCHARGE_UTP          = 1 << 7   # discharge under-temperature
    CHARGE_OCP             = 1 << 8   # charge over-current
    DISCHARGE_OCP          = 1 << 9   # discharge over-current
    SHORT_CIRCUIT          = 1 << 10  # short circuit
    IC_ERROR               = 1 << 11  # front-end IC error
    MOS_LOCK               = 1 << 12  # MOS software lock


class AlarmLevel(IntEnum):
    NONE = 0
    LEVEL1 = 1
    LEVEL2 = 2


@dataclass
class BMSInfo:
    """All data fields the BMS can report.

    Populated by :meth:`SmartBMS.refresh`.
    """
    # ── Pack-level ──
    total_voltage: float = 0.0          # V
    current: float = 0.0                # A  (+ = charging, − = discharging)
    power: float = 0.0                  # W
    remaining_capacity: float = 0.0     # Ah
    nominal_capacity: float = 0.0       # Ah
    soc: float = 0.0                        # %  state of charge
    soh: int = 0                        # %  state of health
    cycle_count: int = 0
    cycle_capacity: float = 0.0         # Ah  cumulative charge throughput

    # ── Cells ──
    cell_count: int = 0
    cell_voltages: list[float] = field(default_factory=list)   # V per cell
    min_cell_voltage: float = 0.0
    max_cell_voltage: float = 0.0
    delta_cell_voltage: float = 0.0     # V  max − min
    avg_cell_voltage: float = 0.0
    min_cell_number: int = 0
    max_cell_number: int = 0
    balance_status: int = 0             # bitmask, bit N = cell N balancing

    # ── Temperatures ──
    temperatures: list[float] = field(default_factory=list)    # °C
    mos_temperature: float = 0.0        # °C
    env_temperature: float = 0.0        # °C

    # ── Protection & MOS ──
    protection_status: ProtectionStatus = ProtectionStatus.NONE
    charge_mos_on: bool = False
    discharge_mos_on: bool = False
    balance_active: bool = False

    # ── Alarms ──
    alarm_info: list[str] = field(default_factory=list)

    # ── Settings (from 0x80 block) ──
    cell_ovp: float = 0.0              # V  cell over-voltage protect
    cell_ovp_recovery: float = 0.0     # V
    cell_uvp: float = 0.0              # V  cell under-voltage protect
    cell_uvp_recovery: float = 0.0     # V
    pack_ovp: float = 0.0              # V  pack over-voltage protect
    pack_uvp: float = 0.0              # V  pack under-voltage protect
    charge_ocp: float = 0.0            # A  charge over-current protect
    discharge_ocp: float = 0.0         # A  discharge over-current protect
    charge_otp: float = 0.0            # °C
    charge_utp: float = 0.0            # °C
    discharge_otp: float = 0.0         # °C
    discharge_utp: float = 0.0         # °C
    balance_start_voltage: float = 0.0 # V  start balancing above this
    balance_delta: float = 0.0         # V  balance when delta exceeds this
    short_circuit_delay: int = 0       # µs
    ocp_delay: int = 0                 # ms

    # ── Identity ──
    password: str = "123456"
    production_date: str = ""
    sn_code: str = ""
    mcu_version: str = ""
    ble_version: str = ""
    machine_version: str = ""

    # ── Heating / force-start ──
    heating_on: bool = False
    heating_start_temp: float = 0.0
    heating_stop_temp: float = 0.0
    force_start_on: bool = False

    # ── Communication ──
    comm_protocol_type: int = 0
    comm_mode: int = 0

    # ── Raw ──
    raw_runtime_hex: str = ""
    raw_settings_hex: str = ""


# ─── CRC-16/MODBUS (BaseVolume$Companion.getCRC) ────────────────────────────
def _crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b & 0xFF
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return ((crc & 0xFF) << 8) | ((crc >> 8) & 0xFF)   # byte-swap


def _build_read(address: int, length: int) -> bytes:
    """Build a D203 read-registers command."""
    payload = bytes.fromhex(f"{_HDR_READ}{address:04X}{length:04X}")
    return payload + struct.pack(">H", _crc16(payload))


def _build_write_single(address: int, value: int) -> bytes:
    """Build a D206 write-single-register command."""
    payload = bytes.fromhex(f"{_HDR_WRITE}{address:04X}{value:04X}")
    return payload + struct.pack(">H", _crc16(payload))


def _build_write_multi(address: int, count: int, data_hex: str) -> bytes:
    """Build a D210 write-multiple-registers command."""
    payload = bytes.fromhex(f"{_HDR_WRITE_MULTI}{address:04X}{count:04X}{data_hex}")
    return payload + struct.pack(">H", _crc16(payload))


def _verify_crc(frame: bytes) -> bool:
    if len(frame) < 4:
        return False
    return _crc16(frame[:-2]) == struct.unpack(">H", frame[-2:])[0]


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from(">H", data, offset)[0]


def _s16(data: bytes, offset: int) -> int:
    return struct.unpack_from(">h", data, offset)[0]


# ─── Scanning ────────────────────────────────────────────────────────────────
async def scan_for_bms(timeout: float = 10.0) -> list[BLEDevice]:
    """Scan for Smart BMS devices and return a list of BleakScanner results."""
    devices = await BleakScanner.discover(timeout=timeout)
    hits = []
    for d in devices:
        name = d.name or ""
        if any(p in name for p in ("DL", "SmartBMS", "Smart BMS", "BMS", "JBD", "SP1")):
            hits.append(d)
    return hits


# ─── Main class ──────────────────────────────────────────────────────────────
class SmartBMS:
    """Bluetooth connection to a Smart BMS battery.

    Parameters
    ----------
    address : str
        Bluetooth MAC address (e.g. ``"AA:BB:CC:DD:EE:FF"``).
    timeout : float
        BLE operation timeout in seconds.
    """

    def __init__(self, address: str, *, timeout: float = 15.0):
        self._address = address
        self._timeout = timeout
        self._client: Optional[BleakClient] = None
        self._rx_buf = bytearray()
        self._evt = asyncio.Event()
        self._last_resp = b""
        self._info = BMSInfo()
        self._lock = asyncio.Lock()

    # ── connection ───────────────────────────────────────────────────────
    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    async def connect(self) -> None:
        """Connect to the BMS and subscribe to notifications."""
        self._client = BleakClient(self._address, timeout=self._timeout)
        await self._client.connect()
        # Small delay to let the BLE stack settle before subscribing
        await asyncio.sleep(0.5)
        await self._client.start_notify(CHAR_NOTIFY, self._on_notify)
        log.info("Connected to %s", self._address)

    async def disconnect(self) -> None:
        """Gracefully disconnect."""
        if self._client and self._client.is_connected:
            await self._client.stop_notify(CHAR_NOTIFY)
            await self._client.disconnect()
        log.info("Disconnected")

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *exc):
        await self.disconnect()

    # ── low-level transport ──────────────────────────────────────────────
    def _on_notify(self, _sender, data: bytearray):
        log.debug("BLE RX chunk: %d bytes → buf now %d bytes",
                  len(data), len(self._rx_buf) + len(data))
        self._rx_buf.extend(data)
        self._try_parse()

    def _try_parse(self):
        raw = self._rx_buf
        while len(raw) >= 4:
            # Read the 2-byte header as a 16-bit int for reliable matching
            hdr = (raw[0] << 8) | raw[1]

            if hdr == 0xD203:
                # D203 read response: D203 + count(1B) + data(count B) + CRC(2B)
                if len(raw) < 5:
                    return   # need at least header + count + CRC
                data_len = raw[2]                  # number of DATA BYTES
                frame_len = 2 + 1 + data_len + 2   # hdr + count + data + crc
                if len(raw) < frame_len:
                    return   # wait for more BLE chunks
                frame = bytes(raw[:frame_len])
                if _verify_crc(frame):
                    self._last_resp = frame
                    self._evt.set()
                    del raw[:frame_len]
                    return
                else:
                    del raw[0]
                    continue

            elif hdr == 0xD206 or hdr == 0xD210:
                # Write responses are always 8 bytes: hdr(2) + addr(2) + val(2) + crc(2)
                if len(raw) < 8:
                    return
                frame = bytes(raw[:8])
                if _verify_crc(frame):
                    self._last_resp = frame
                    self._evt.set()
                    del raw[:8]
                    return
                else:
                    del raw[0]
                    continue

            elif hdr == 0xA5FF:
                # OTA S19 response – 13 bytes
                if len(raw) < 13:
                    return
                self._last_resp = bytes(raw[:13])
                self._evt.set()
                del raw[:13]
                return

            elif hdr == 0x5110:
                # OTA BIN response – 8 bytes
                if len(raw) < 8:
                    return
                self._last_resp = bytes(raw[:8])
                self._evt.set()
                del raw[:8]
                return

            else:
                # Unknown byte – skip and resync
                del raw[0]

    async def _send(self, cmd: bytes) -> bytes:
        async with self._lock:
            self._evt.clear()
            self._rx_buf.clear()
            self._last_resp = b""
            # Try write-with-response first; fall back to write-without-response
            try:
                await self._client.write_gatt_char(CHAR_WRITE, cmd, response=True)
            except Exception:
                await self._client.write_gatt_char(CHAR_WRITE, cmd, response=False)
            try:
                await asyncio.wait_for(self._evt.wait(), self._timeout)
            except asyncio.TimeoutError:
                log.warning("Timeout waiting for BMS response (%d bytes in buffer, cmd=%s)",
                            len(self._rx_buf), cmd[:4].hex())
                # Try parsing whatever we have in case it arrived but didn't parse
                self._try_parse()
                if self._last_resp:
                    return self._last_resp
                return b""
            return self._last_resp

    async def _read_registers(self, addr: int, length: int) -> bytes:
        resp = await self._send(_build_read(addr, length))
        if resp and len(resp) > 5 and (resp[0] << 8 | resp[1]) == 0xD203:
            return resp[3:-2]  # strip hdr(2) + len(1) ... crc(2)
        return resp

    async def _write_at_command(self, cmd: str) -> None:
        """Send an AT command on the FFF3 name-characteristic."""
        try:
            await self._client.write_gatt_char(CHAR_NAME, cmd.encode(), response=True)
        except Exception:
            await self._client.write_gatt_char(CHAR_NAME, cmd.encode(), response=False)

    # ── refresh / parse ──────────────────────────────────────────────────
    # Register layout (from diagnostic dump of actual BMS hardware):
    #
    #   RUNTIME BLOCK  (read 0x0000, len 0x7E = 126 regs = 252 bytes)
    #   ─────────────────────────────────────────────────────────────
    #   Reg 0x00‑0x1F  (byte  0‑ 63): Cell voltages 1‑32  (u16 mV each, 0 = unused slot)
    #   Reg 0x20‑0x27  (byte 64‑ 79): Temperatures 1‑8    (u16, 0xFF = no sensor)
    #   Reg 0x28       (byte 80‑ 81): Total pack voltage   (u16 /10 → V)
    #   Reg 0x29       (byte 82‑ 83): Nominal capacity     (u16, unit TBD)
    #   Reg 0x2A       (byte 84‑ 85): SOC                  (u16 /10 → %)
    #   Reg 0x2B       (byte 86‑ 87): Max cell voltage     (u16 mV)
    #   Reg 0x2C       (byte 88‑ 89): Min cell voltage     (u16 mV)
    #   Reg 0x2D       (byte 90‑ 91): Max temperature      (u16, same unit as temps)
    #   Reg 0x2E       (byte 92‑ 93): Min temperature      (u16, same unit as temps)
    #   Reg 0x2F       (byte 94‑ 95): Current              (s16 /100 → A, + = charge)
    #   Reg 0x30       (byte 96‑ 97): Remaining capacity   (u16, same unit as nominal)
    #   Reg 0x31       (byte 98‑ 99): Cell count            (u16)
    #   Reg 0x32       (byte100‑101): Temp sensor count     (u16)
    #   Reg 0x33       (byte102‑103): MOS status            (u16 bitmask)
    #   Reg 0x34       (byte104‑105): Protection status     (u16 bitmask)
    #   Reg 0x35       (byte106‑107): Fault / alarm         (u16)
    #   Reg 0x36       (byte108‑109): Balance status        (u16 bitmask)
    #   Reg 0x37       (byte110‑111): Average cell voltage  (u16 mV)
    #   Reg 0x38       (byte112‑113): Cell voltage delta    (u16 mV)
    #   Reg 0x39‑0x3D  (byte114‑123): Reserved (zeros)
    #   Reg 0x3E+      (byte124+)   : History / last‑battery / SN

    _MAX_CELLS = 32       # slots 0x00–0x1F
    _MAX_TEMPS = 8        # slots 0x20–0x27
    _PACK_BASE = 80       # byte offset where pack‑level data starts (reg 0x28)

    def _parse_runtime(self, data: bytes):
        """Parse the 0x00–0x7E runtime data block into self._info."""
        if len(data) < self._PACK_BASE + 34:      # need at least through reg 0x38
            log.warning("Runtime data too short: %d bytes", len(data))
            return
        info = self._info
        info.raw_runtime_hex = data.hex()
        pb = self._PACK_BASE

        # ── Pack‑level fields ────────────────────────────────────────
        info.total_voltage      = _u16(data, pb + 0) / 10.0        # reg 0x28
        info.current            = (_u16(data, pb + 2) - 30000) / 10.0  # reg 0x29: offset encoding!
        info.soc                = _u16(data, pb + 4) / 10.0        # reg 0x2A
        info.max_cell_voltage   = _u16(data, pb + 6) / 1000.0      # reg 0x2B
        info.min_cell_voltage   = _u16(data, pb + 8) / 1000.0      # reg 0x2C
        info.mos_temperature    = _u16(data, pb + 10) - 40          # reg 0x2D  (max temp)
        info.env_temperature    = _u16(data, pb + 12) - 40          # reg 0x2E  (min temp)
        #                       pb + 14 = reg 0x2F (reserved)
        info.remaining_capacity = _u16(data, pb + 16) / 10.0       # reg 0x30
        info.cell_count         = _u16(data, pb + 18)               # reg 0x31
        ntc_count               = _u16(data, pb + 20)               # reg 0x32
        mos                     = _u16(data, pb + 22)               # reg 0x33
        info.protection_status  = ProtectionStatus(_u16(data, pb + 24))  # reg 0x34
        #                       pb + 26 = reg 0x35 (fault flag)
        #                       pb + 28 = reg 0x36 (balance flag)
        info.avg_cell_voltage   = _u16(data, pb + 30) / 1000.0     # reg 0x37
        info.delta_cell_voltage = _u16(data, pb + 32) / 1000.0     # reg 0x38

        # Alarm info (regs 0x3A-0x3D = absolute byte offsets 116-123)
        info.alarm_info = []
        for i in range(4):
            off = 116 + i * 2
            if off + 2 <= len(data):
                val = _u16(data, off)
                if val:
                    info.alarm_info.append(f"alarm{i+1}=0x{val:04X}")

        # Balance state (regs 0x3E-0x41 = absolute byte offsets 124-131)
        if 132 <= len(data):
            info.cycle_count   = _u16(data, 124)                     # reg 0x3E (里程)
            balance_on_flag    = _u16(data, 126)                     # reg 0x3F
            # reg 0x40 = balance current: (raw-30000)*0.1
            info.balance_status = _u16(data, 130)                    # reg 0x41
            info.balance_active = balance_on_flag == 1

        # MOS temperature (reg 0x42 = absolute byte offset 132)
        if 134 <= len(data):
            info.mos_temperature = _u16(data, 132) - 40              # reg 0x42

        info.charge_mos_on      = bool(mos & 0x01)
        info.discharge_mos_on   = bool(mos & 0x02)
        info.balance_active     = info.balance_status != 0
        info.power              = round(info.total_voltage * abs(info.current), 2)

        # ── Cell voltages (regs 0x00–0x1F, 32 slots) ────────────────
        info.cell_voltages = []
        for i in range(min(info.cell_count, self._MAX_CELLS)):
            off = i * 2
            if off + 2 <= len(data):
                mv = _u16(data, off)
                info.cell_voltages.append(mv / 1000.0)
        if info.cell_voltages:
            info.min_cell_voltage = min(info.cell_voltages)
            info.max_cell_voltage = max(info.cell_voltages)
            info.delta_cell_voltage = round(info.max_cell_voltage - info.min_cell_voltage, 4)
            info.avg_cell_voltage = round(sum(info.cell_voltages) / len(info.cell_voltages), 4)
            info.min_cell_number = info.cell_voltages.index(info.min_cell_voltage) + 1
            info.max_cell_number = info.cell_voltages.index(info.max_cell_voltage) + 1

        # ── Temperatures (regs 0x20–0x27, 8 slots) ──────────────────
        info.temperatures = []
        for i in range(min(ntc_count, self._MAX_TEMPS)):
            off = 64 + i * 2     # byte 64 = reg 0x20
            if off + 2 <= len(data):
                raw = _u16(data, off)
                if raw != 0xFF and raw != 0xFFFF:
                    info.temperatures.append(raw - 40)

        # ── SN code (regs 0x57–0x5D, ASCII) ──────────────────────────
        sn_off = 0x57 * 2  # byte offset 174
        if sn_off + 14 <= len(data):
            raw_sn = data[sn_off:sn_off + 14]
            info.sn_code = raw_sn.decode("ascii", errors="replace").rstrip("\x00")

    def _parse_settings(self, data: bytes):
        """Parse the 0x80 settings block into self._info.

        Settings layout (from diagnostic dump, BMS regs 0x80–0xA8):
          Reg 0x80 (byte  0): capacity / scaling (1000)
          Reg 0x81 (byte  2): balance voltage    (/1000 V)
          Reg 0x83 (byte  6): cell count config
          Reg 0x86 (byte 12): NTC count config
          Reg 0x8A (byte 20): cell OVP           (/1000 V)
          Reg 0x8B (byte 22): cell OVP recovery  (/1000 V)
          Reg 0x8D (byte 26): cell UVP recovery  (/1000 V)
          Reg 0x8E (byte 28): cell UVP           (/1000 V)
          Reg 0x8F (byte 30): pack OVP           (/10 V)
          Reg 0x91 (byte 34): pack UVP           (/10 V)
          Reg 0x93 (byte 38): charge OCP         (/1000 A)
          Reg 0x95 (byte 42): discharge OCP      (/1000 A)
          Reg 0x97 (byte 46): charge OTP         (°C − 40)
          Reg 0x9F (byte 62): SC protect delay   (µs)
          Reg 0xA0 (byte 64): OCP delay          (ms)
          Reg 0xA3 (byte 70): balance start volt (/1000 V)
          Reg 0xA4 (byte 72): balance delta      (mV)
        """
        if len(data) < 72:
            return
        info = self._info
        info.raw_settings_hex = data.hex()

        info.nominal_capacity    = _u16(data, 0) / 10.0              # reg 0x80
        info.balance_start_voltage = _u16(data, 2) / 1000.0          # reg 0x81
        info.cell_ovp            = _u16(data, 20) / 1000.0           # reg 0x8A
        info.cell_ovp_recovery   = _u16(data, 22) / 1000.0           # reg 0x8B
        info.cell_uvp_recovery   = _u16(data, 26) / 1000.0           # reg 0x8D
        info.cell_uvp            = _u16(data, 28) / 1000.0           # reg 0x8E
        info.pack_ovp            = _u16(data, 30) / 10.0             # reg 0x8F
        info.pack_uvp            = _u16(data, 34) / 10.0             # reg 0x91
        info.charge_ocp          = _u16(data, 38) / 1000.0           # reg 0x93
        info.discharge_ocp       = _u16(data, 42) / 1000.0           # reg 0x95
        info.charge_otp          = _u16(data, 46) - 40               # reg 0x97
        info.discharge_otp       = _u16(data, 54) - 40               # reg 0x9B
        info.short_circuit_delay = _u16(data, 62)                    # reg 0x9F  (µs)
        info.ocp_delay           = _u16(data, 64)                    # reg 0xA0  (ms)

        if len(data) > 72:
            info.balance_start_voltage = _u16(data, 70) / 1000.0     # reg 0xA3
            info.balance_delta = _u16(data, 72) / 1000.0             # reg 0xA4

    # ── high-level data getters ──────────────────────────────────────────

    async def refresh(self) -> BMSInfo:
        """Read all runtime data from the BMS and return the parsed info.

        This is the equivalent of what the app does every polling cycle.
        """
        data = await self._read_registers(REG_RUNTIME_START, REG_RUNTIME_LEN)
        if data:
            self._parse_runtime(data)
        return self._info

    async def refresh_settings(self) -> BMSInfo:
        """Read the settings / configuration block."""
        data = await self._read_registers(REG_SETTINGS_START, REG_SETTINGS_LEN)
        if data:
            self._parse_settings(data)
        return self._info

    async def refresh_all(self) -> BMSInfo:
        """Read both runtime data and settings in one call."""
        await self.refresh()
        await asyncio.sleep(0.3)
        await self.refresh_settings()
        return self._info

    @property
    def info(self) -> BMSInfo:
        """Return the most recently read data (no BLE traffic)."""
        return self._info

    # ── Individual getters (each triggers a fresh read) ──────────────────

    async def get_soc(self) -> float:
        """Battery state of charge in percent (0–100)."""
        await self.refresh()
        return self._info.soc

    async def get_battery_percent(self) -> float:
        """Alias for :meth:`get_soc`."""
        return await self.get_soc()

    async def get_total_voltage(self) -> float:
        """Total pack voltage in volts."""
        await self.refresh()
        return self._info.total_voltage

    async def get_current(self) -> float:
        """Pack current in amps (positive = charging)."""
        await self.refresh()
        return self._info.current

    async def get_power(self) -> float:
        """Instantaneous power in watts."""
        await self.refresh()
        return self._info.power

    async def get_remaining_capacity(self) -> float:
        """Remaining capacity in Ah."""
        await self.refresh()
        return self._info.remaining_capacity

    async def get_nominal_capacity(self) -> float:
        """Nominal (full) capacity in Ah."""
        await self.refresh()
        return self._info.nominal_capacity

    async def get_cycle_count(self) -> int:
        """Number of charge/discharge cycles."""
        await self.refresh()
        return self._info.cycle_count

    async def get_cell_count(self) -> int:
        """Number of cells in the pack."""
        await self.refresh()
        return self._info.cell_count

    async def get_cell_voltages(self) -> list[float]:
        """List of individual cell voltages in volts."""
        await self.refresh()
        return list(self._info.cell_voltages)

    async def get_min_cell_voltage(self) -> tuple[int, float]:
        """(cell_number, voltage) of the lowest cell."""
        await self.refresh()
        return self._info.min_cell_number, self._info.min_cell_voltage

    async def get_max_cell_voltage(self) -> tuple[int, float]:
        """(cell_number, voltage) of the highest cell."""
        await self.refresh()
        return self._info.max_cell_number, self._info.max_cell_voltage

    async def get_delta_cell_voltage(self) -> float:
        """Difference between highest and lowest cell in volts."""
        await self.refresh()
        return self._info.delta_cell_voltage

    async def get_temperatures(self) -> list[float]:
        """List of NTC temperature readings in °C."""
        await self.refresh()
        return list(self._info.temperatures)

    async def get_mos_temperature(self) -> float:
        """MOS FET temperature in °C."""
        await self.refresh()
        return self._info.mos_temperature

    async def get_protection_status(self) -> ProtectionStatus:
        """Current protection flags."""
        await self.refresh()
        return self._info.protection_status

    async def get_charge_mos_state(self) -> bool:
        """Whether the charge MOSFET is on."""
        await self.refresh()
        return self._info.charge_mos_on

    async def get_discharge_mos_state(self) -> bool:
        """Whether the discharge MOSFET is on."""
        await self.refresh()
        return self._info.discharge_mos_on

    async def get_balance_status(self) -> int:
        """Bitmask of which cells are currently being balanced."""
        await self.refresh()
        return self._info.balance_status

    async def get_is_balancing(self) -> bool:
        """Whether any cell is currently being balanced."""
        await self.refresh()
        return self._info.balance_active

    # ── Settings getters ─────────────────────────────────────────────────

    async def get_cell_ovp(self) -> float:
        """Cell over-voltage protection threshold in volts."""
        await self.refresh_settings()
        return self._info.cell_ovp

    async def get_cell_uvp(self) -> float:
        """Cell under-voltage protection threshold in volts."""
        await self.refresh_settings()
        return self._info.cell_uvp

    async def get_charge_ocp(self) -> float:
        """Charge over-current protection in amps."""
        await self.refresh_settings()
        return self._info.charge_ocp

    async def get_discharge_ocp(self) -> float:
        """Discharge over-current protection in amps."""
        await self.refresh_settings()
        return self._info.discharge_ocp

    async def get_password(self) -> str:
        """Current control password."""
        await self.refresh_settings()
        return self._info.password

    # ── Write / control commands ─────────────────────────────────────────

    async def set_discharge_mos(self, on: bool) -> bool:
        """Turn the discharge MOSFET on or off.

        Returns True if the BMS acknowledged.
        """
        value = 0x0001 if on else 0x0000
        resp = await self._send(_build_write_single(REG_FORCE_START, value))
        return len(resp) >= 8

    async def set_charge_mos(self, on: bool) -> bool:
        """Turn the charge MOSFET on or off."""
        value = 0x0001 if on else 0x0000
        resp = await self._send(_build_write_single(REG_FORCE_START + 1, value))
        return len(resp) >= 8

    async def set_balance(self, on: bool) -> bool:
        """Enable or disable active balancing.

        Maps to ZhuDongJunHengActivity's balance on/off toggle (reg 0xD8).
        """
        value = 0x0001 if on else 0x0000
        resp = await self._send(_build_write_single(REG_BALANCE_STATE, value))
        return len(resp) >= 8

    async def set_heating(self, on: bool) -> bool:
        """Turn the heating pad on or off (reg 0xE3)."""
        value = 0x0001 if on else 0x0000
        resp = await self._send(_build_write_single(REG_HEATING, value))
        return len(resp) >= 8

    async def set_force_start(self, on: bool) -> bool:
        """Force-start the BMS (wake from sleep)."""
        value = 0x0001 if on else 0x0000
        resp = await self._send(_build_write_single(REG_FORCE_START, value))
        return len(resp) >= 8

    async def set_password(self, new_password: str) -> bool:
        """Change the BMS control password (max 6 ASCII characters).

        Uses the D210 write-multiple command to address 0x00C9 with 3 registers.
        """
        pwd_bytes = new_password.encode("ascii")[:6].ljust(6, b"0")
        data_hex = pwd_bytes.hex()
        cmd = _build_write_multi(REG_PASSWORD, 3, data_hex)
        resp = await self._send(cmd)
        return len(resp) >= 8

    async def sync_time(self, year: int, month: int, day: int,
                        hour: int, minute: int, second: int) -> bool:
        """Synchronise the BMS real-time clock.

        Matches CreateCtrDataHelper.getWriteTime (D210 to REG_TIME_WRITE).
        Year is stored as offset from 2000 (e.g. 2024 → 24).
        """
        data_hex = (
            f"{year - 2000:02X}{month:02X}{day:02X}"
            f"{hour:02X}{minute:02X}{second:02X}"
        )
        cmd = _build_write_multi(REG_TIME_WRITE, 3, data_hex)
        resp = await self._send(cmd)
        return len(resp) >= 8

    async def set_cell_ovp(self, voltage_mv: int) -> bool:
        """Set cell over-voltage protection threshold (millivolts)."""
        resp = await self._send(_build_write_single(0x008A, voltage_mv))
        return len(resp) >= 8

    async def set_cell_uvp(self, voltage_mv: int) -> bool:
        """Set cell under-voltage protection threshold (millivolts)."""
        resp = await self._send(_build_write_single(0x008E, voltage_mv))
        return len(resp) >= 8

    async def set_charge_ocp(self, current_x1000: int) -> bool:
        """Set charge over-current protection (value in mA, e.g. 28800 = 28.8 A)."""
        resp = await self._send(_build_write_single(0x0093, current_x1000))
        return len(resp) >= 8

    async def set_discharge_ocp(self, current_x1000: int) -> bool:
        """Set discharge over-current protection (value in mA, e.g. 31500 = 31.5 A)."""
        resp = await self._send(_build_write_single(0x0095, current_x1000))
        return len(resp) >= 8

    async def set_comm_mode(self, mode: int) -> bool:
        """Set communication mode / protocol type (reg 0xD1).

        From SetValueActivity's communicationModeProtocolTypeInit.
        """
        resp = await self._send(_build_write_single(REG_COMM_MODE, mode))
        return len(resp) >= 8

    # ── Device identity / AT commands ────────────────────────────────────

    async def rename_device(self, new_name: str) -> None:
        """Change the BLE advertised name (AT+NAME=…).

        The BMS will disconnect and re-advertise with the new name.
        """
        await self._write_at_command(f"AT+NAME={new_name}")

    async def set_baud_rate(self, baud: int) -> None:
        """Change the UART baud rate via AT command (AT+BAND=…)."""
        await self._write_at_command(f"AT+BAND={baud}")

    async def query_firmware_version(self) -> Optional[str]:
        """Query the BLE module firmware version (AT+VER=?).

        This writes to the secondary service (02f00000…fe00 / ff04) if available.
        """
        try:
            await self._client.write_gatt_char(
                CHAR_V_RW, b"AT+VER=?\r\n", response=True
            )
            await asyncio.sleep(1.0)
            return self._last_resp.decode("utf-8", errors="replace").strip()
        except Exception:
            return None

    # ── Raw register access ──────────────────────────────────────────────

    async def read_registers(self, address: int, length: int) -> bytes:
        """Read arbitrary registers. Returns the raw payload bytes."""
        return await self._read_registers(address, length)

    async def write_register(self, address: int, value: int) -> bool:
        """Write a single 16-bit register. Returns True on ACK."""
        resp = await self._send(_build_write_single(address, value))
        return len(resp) >= 8

    async def write_registers(self, address: int, count: int, data_hex: str) -> bool:
        """Write multiple registers using D210. Returns True on ACK."""
        resp = await self._send(_build_write_multi(address, count, data_hex))
        return len(resp) >= 8

    # ── History ──────────────────────────────────────────────────────────

    async def read_history(self) -> bytes:
        """Read the history/fault log block (0x63–0x7E).

        Returns raw bytes – interpretation depends on your BMS model.
        """
        return await self._read_registers(REG_HISTORY_START, REG_HISTORY_LEN)


# ─── CLI helper ──────────────────────────────────────────────────────────────
async def _cli_main():
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="Smart BMS CLI")
    parser.add_argument("--address", "-a", help="BMS MAC address")
    parser.add_argument("--scan", action="store_true", help="Scan only")
    parser.add_argument("--loop", "-l", type=float, default=0,
                        help="Repeat every N seconds (0 = once)")
    args = parser.parse_args()

    if args.scan or not args.address:
        devices = await scan_for_bms()
        for d in devices:
            print(f"  {d.name or '???':30s}  {d.address}  RSSI={d.rssi}")
        if args.scan or not devices:
            return
        args.address = devices[0].address
        print(f"\nUsing {devices[0].name} [{args.address}]")

    async with SmartBMS(args.address) as bms:
        while True:
            info = await bms.refresh_all()
            print(f"\n{'═'*50}")
            print(f"  Voltage   : {info.total_voltage:.1f} V")
            print(f"  Current   : {info.current:.2f} A")
            print(f"  Power     : {info.power:.1f} W")
            print(f"  SOC       : {info.soc:.1f} %")
            print(f"  Capacity  : {info.remaining_capacity:.2f} / {info.nominal_capacity:.2f} Ah")
            print(f"  Cycles    : {info.cycle_count}")
            print(f"  Cells     : {info.cell_count}")
            for i, v in enumerate(info.cell_voltages):
                print(f"    Cell {i+1:2d} : {v:.3f} V")
            if info.cell_voltages:
                print(f"    Δ       : {info.delta_cell_voltage*1000:.0f} mV")
                print(f"    Avg     : {info.avg_cell_voltage:.3f} V")
            for i, t in enumerate(info.temperatures):
                print(f"  Temp {i+1:2d}   : {t} °C")
            print(f"  Protect   : {info.protection_status!r}")
            print(f"  CHG MOS   : {'ON' if info.charge_mos_on else 'OFF'}")
            print(f"  DSG MOS   : {'ON' if info.discharge_mos_on else 'OFF'}")
            print(f"  Balancing : {'YES' if info.balance_active else 'NO'}")
            if info.raw_settings_hex:
                print(f"  Cell OVP  : {info.cell_ovp:.3f} V")
                print(f"  Cell UVP  : {info.cell_uvp:.3f} V")
                print(f"  CHG OCP   : {info.charge_ocp:.1f} A")
                print(f"  DSG OCP   : {info.discharge_ocp:.1f} A")
                print(f"  Bal Start : {info.balance_start_voltage:.3f} V")
                print(f"  Bal Delta : {info.balance_delta*1000:.0f} mV")
            print(f"{'═'*50}")
            if not args.loop:
                break
            await asyncio.sleep(args.loop)


if __name__ == "__main__":
    asyncio.run(_cli_main())