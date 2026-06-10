"""BESTGO battery (Lithium Valley BMS) frame decoding.

The pack speaks the SMA / Pylontech-compatible CAN BMS protocol: 500 kbps,
standard 11-bit IDs, ~1 s transmit cycle, little-endian. See
specs/bestgo_spec.txt for the full frame breakdown.

NOTE: captured frames decode correctly against the spec, but the
alarm/warning bit map (0x35A) is unverified, so the decoder reports the raw
alarm/warning bytes (hex) rather than naming individual bits.
"""
import math
import random
import logging

log = logging.getLogger(__name__)

# --- frame IDs (standard 11-bit) ---------------------------------------------
ID_LIMITS   = 0x351   # charge/discharge V & I limits
ID_SOC      = 0x355   # SOC / SOH / hi-res SOC
ID_MEAS     = 0x356   # pack voltage / current / temperature
ID_ALARMS   = 0x35A   # alarm + warning bitfields
ID_MFR      = 0x35E   # manufacturer name (ASCII)
ID_INFO     = 0x35F   # chemistry / firmware version / capacity
ID_NAME0    = 0x370   # battery name chars 0-7 (ASCII)
ID_NAME1    = 0x371   # battery name chars 8-15 (ASCII)
ID_CELLEXT  = 0x373   # cell V & T min/max
ID_CELL_VLO = 0x374   # ID of min-voltage cell      (not decoded yet)
ID_CELL_VHI = 0x375   # ID of max-voltage cell      (not decoded yet)
ID_CELL_TLO = 0x376   # ID of min-temperature cell  (not decoded yet)
ID_CELL_THI = 0x377   # ID of max-temperature cell  (not decoded yet)
ID_CAPACITY = 0x379   # installed (rated) capacity

# IDs the decoder claims (a claimed ID never falls through to other decoders)
BG_IDS = {ID_LIMITS, ID_SOC, ID_MEAS, ID_ALARMS, ID_MFR, ID_INFO,
          ID_NAME0, ID_NAME1, ID_CELLEXT, ID_CAPACITY}

KELVIN = 273.15


def u16(data, off):
    return int.from_bytes(data[off:off + 2], "little")


def s16(data, off):
    return int.from_bytes(data[off:off + 2], "little", signed=True)


def ascii_clean(raw):
    """Printable ASCII from a byte string, stopping at the first NUL."""
    out = []
    for b in raw:
        if b == 0:
            break
        out.append(chr(b) if 32 <= b < 127 else ".")
    return "".join(out).strip()


class BestgoDecoder:
    """Stateful decoder: holds the two battery-name halves between frames.

    decode() returns:
      * None     -- the frame is not a BESTGO frame
      * a dict   -- decoded fields (possibly empty for a recognised ID with a
                    short payload; the ID is still claimed either way)

    Field names are the canonical set the HA sensors are built from
    (sensor.bestgo_<field>).
    """

    def __init__(self):
        self._name = {}   # name-frame halves: {0: bytes, 1: bytes}

    def _battery_name(self):
        parts = [ascii_clean(self._name.get(0, b"")),
                 ascii_clean(self._name.get(1, b""))]
        return " ".join(p for p in parts if p) or "?"

    def decode(self, arb_id, data):
        if arb_id not in BG_IDS:
            return None
        f = {}
        try:
            if arb_id == ID_LIMITS and len(data) >= 8:
                f["charge_voltage_limit"]    = round(u16(data, 0) * 0.1, 1)
                f["charge_current_limit"]    = round(s16(data, 2) * 0.1, 1)
                f["discharge_current_limit"] = round(s16(data, 4) * 0.1, 1)
                f["discharge_voltage_limit"] = round(u16(data, 6) * 0.1, 1)
            elif arb_id == ID_SOC and len(data) >= 4:
                f["soc"] = u16(data, 0)
                f["soh"] = u16(data, 2)
                if len(data) >= 6:
                    f["soc_hi"] = round(u16(data, 4) * 0.01, 2)
            elif arb_id == ID_MEAS and len(data) >= 6:
                f["pack_voltage"] = round(s16(data, 0) * 0.01, 2)
                f["pack_current"] = round(s16(data, 2) * 0.1, 1)
                f["pack_temp"]    = round(s16(data, 4) * 0.1, 1)
            elif arb_id == ID_ALARMS and len(data) >= 8:
                alarm, warn = data[0:4], data[4:8]
                f["alarms"]   = "OK" if not any(alarm) else alarm.hex()
                f["warnings"] = "OK" if not any(warn) else warn.hex()
            elif arb_id == ID_MFR:
                f["manufacturer"] = ascii_clean(data)
            elif arb_id == ID_INFO and len(data) >= 6:
                ver = u16(data, 2)
                f["chemistry"]        = f"0x{u16(data, 0):04X}"
                f["firmware"]         = f"v{ver >> 8}.{ver & 0xFF}"
                f["nominal_capacity"] = u16(data, 4)
            elif arb_id == ID_NAME0:
                self._name[0] = bytes(data)
                f["battery_name"] = self._battery_name()
            elif arb_id == ID_NAME1:
                self._name[1] = bytes(data)
                f["battery_name"] = self._battery_name()
            elif arb_id == ID_CELLEXT and len(data) >= 8:
                vmin, vmax = u16(data, 0), u16(data, 2)
                f["cell_voltage_min"]   = vmin
                f["cell_voltage_max"]   = vmax
                f["cell_voltage_delta"] = vmax - vmin
                tmin, tmax = u16(data, 4), u16(data, 6)
                # Cell temps are in kelvin; 0 means "not reported".
                if tmin:
                    f["cell_temp_min"] = round(tmin - KELVIN, 1)
                if tmax:
                    f["cell_temp_max"] = round(tmax - KELVIN, 1)
            elif arb_id == ID_CAPACITY and len(data) >= 2:
                f["installed_capacity"] = u16(data, 0)
        except Exception:
            log.warning("malformed BESTGO frame 0x%X: %s", arb_id,
                        bytes(data).hex(), exc_info=True)
        return f


# --- HA sensor metadata (sensor.bestgo_<field>) ------------------------------
# Only fields listed here are pushed to HA; extra decoded fields (soc_hi,
# chemistry) stay dashboard-only so the published sensor set doesn't change.
BG_SENSORS = {
    "soc":                     {"unit": "%",  "icon": "mdi:battery-50",         "device_class": "battery"},
    "soh":                     {"unit": "%",  "icon": "mdi:battery-heart",      "device_class": None},
    "pack_voltage":            {"unit": "V",  "icon": "mdi:flash",              "device_class": "voltage"},
    "pack_current":            {"unit": "A",  "icon": "mdi:current-dc",         "device_class": "current"},
    "pack_temp":               {"unit": "°C", "icon": "mdi:thermometer",        "device_class": "temperature"},
    "cell_voltage_min":        {"unit": "mV", "icon": "mdi:battery-low",        "device_class": "voltage"},
    "cell_voltage_max":        {"unit": "mV", "icon": "mdi:battery-high",       "device_class": "voltage"},
    "cell_voltage_delta":      {"unit": "mV", "icon": "mdi:delta",              "device_class": None},
    "cell_temp_min":           {"unit": "°C", "icon": "mdi:thermometer-low",    "device_class": "temperature"},
    "cell_temp_max":           {"unit": "°C", "icon": "mdi:thermometer-high",   "device_class": "temperature"},
    "charge_voltage_limit":    {"unit": "V",  "icon": "mdi:battery-charging",   "device_class": "voltage"},
    "charge_current_limit":    {"unit": "A",  "icon": "mdi:current-dc",         "device_class": "current"},
    "discharge_current_limit": {"unit": "A",  "icon": "mdi:current-dc",         "device_class": "current"},
    "discharge_voltage_limit": {"unit": "V",  "icon": "mdi:battery-arrow-down", "device_class": "voltage"},
    "alarms":                  {"unit": None, "icon": "mdi:alert-circle",       "device_class": None},
    "warnings":                {"unit": None, "icon": "mdi:alert",              "device_class": None},
    "firmware":                {"unit": None, "icon": "mdi:chip",               "device_class": None},
    "nominal_capacity":        {"unit": "Ah", "icon": "mdi:battery",            "device_class": None},
    "installed_capacity":      {"unit": "Ah", "icon": "mdi:battery",            "device_class": None},
    "manufacturer":            {"unit": None, "icon": "mdi:factory",            "device_class": None},
    "battery_name":            {"unit": None, "icon": "mdi:tag",                "device_class": None},
}


# --- dummy data (no hardware needed) -----------------------------------------
DUMMY_PERIOD = 1.0   # the BMS broadcasts its full frame set once per second


def _le(value, width, signed):
    return int(round(value)).to_bytes(width, "little", signed=signed)


def dummy_frames(t):
    """One simulated BMS broadcast cycle (the full SMA/Pylontech frame set).

    `t` is elapsed seconds; drives slow sinusoidal variation so the
    dashboards show changing values.
    """
    soc = int(55 + 25 * math.sin(t / 30.0))            # 30..80 %
    pack_i = round(45 * math.sin(t / 18.0), 1)         # +-45 A
    pack_v = round(51.2 + pack_i * 0.012, 2)
    pack_t = round(24.0 + 4 * math.sin(t / 40.0), 1)
    cell_avg = pack_v / 16 * 1000                      # mV per cell, 16S pack
    vmin = int(cell_avg - random.randint(3, 9))
    vmax = int(cell_avg + random.randint(3, 9))
    tmin, tmax = pack_t - 1.0, pack_t + 1.5
    return [
        (ID_LIMITS,   _le(576, 2, False) + _le(1500, 2, True)
                      + _le(2000, 2, True) + _le(448, 2, False)),
        (ID_SOC,      _le(soc, 2, False) + _le(100, 2, False)
                      + _le(soc * 100, 2, False) + b"\x00\x00"),
        (ID_MEAS,     _le(pack_v / 0.01, 2, True) + _le(pack_i / 0.1, 2, True)
                      + _le(pack_t / 0.1, 2, True) + b"\x00\x00"),
        (ID_ALARMS,   bytes(8)),
        (ID_MFR,      b"LVaiiey\x00"),
        (ID_INFO,     _le(0, 2, False) + bytes([0x01, 0x01])
                      + _le(56, 2, False) + b"\x00\x00"),
        (ID_NAME0,    b"Lithium\x00"),
        (ID_NAME1,    b"Valley\x00\x00"),
        (ID_CELLEXT,  _le(vmin, 2, False) + _le(vmax, 2, False)
                      + _le(tmin + KELVIN, 2, False) + _le(tmax + KELVIN, 2, False)),
        (ID_CAPACITY, _le(56, 2, False) + bytes(6)),
    ]


def dummy_fields():
    """One random snapshot of decoded fields (for the HA add-on's dummy mode)."""
    soc = random.randint(45, 85)
    pack_i = round(random.uniform(-30.0, 30.0), 1)
    pack_v = round(52.0 + pack_i * 0.01, 2)
    vmin = random.randint(3290, 3305)
    vmax = vmin + random.randint(2, 12)
    tmin = round(random.uniform(20.0, 23.0), 1)
    tmax = round(tmin + random.uniform(1.0, 4.0), 1)
    return {
        "soc": soc,
        "soh": 100,
        "pack_voltage": pack_v,
        "pack_current": pack_i,
        "pack_temp": round(random.uniform(21.0, 27.0), 1),
        "charge_voltage_limit": 57.6,
        "charge_current_limit": 150.0,
        "discharge_current_limit": 200.0,
        "discharge_voltage_limit": 44.8,
        "cell_voltage_min": vmin,
        "cell_voltage_max": vmax,
        "cell_voltage_delta": vmax - vmin,
        "cell_temp_min": tmin,
        "cell_temp_max": tmax,
        "alarms": "OK",
        "warnings": "OK",
        "firmware": "v1.1",
        "nominal_capacity": 56,
        "installed_capacity": 56,
        "manufacturer": "LVaiiey",
        "battery_name": "Lithium Valley",
    }
