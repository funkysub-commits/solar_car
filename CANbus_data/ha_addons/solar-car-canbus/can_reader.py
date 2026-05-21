#!/usr/bin/env python3
"""Solar Car CANbus reader for Home Assistant.

Reads two devices from ONE shared SocketCAN bus and pushes named sensor
states to HA via the REST API:

  * EZkontrol B48800 motor controller  -- 29-bit extended IDs (0x1801xxxx)
  * BESTGO battery (Lithium Valley BMS) -- 11-bit standard IDs (0x351..0x379)

The ID ranges don't overlap, so a single socket sees and decodes both. Each
device has an independent push interval and dummy-mode flag, set in the
add-on options and passed through by run.sh as environment variables.
"""
import os
import time
import struct
import logging
import random

import requests

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s')

HA_URL = os.environ.get("HA_URL", "http://supervisor/core")
HA_TOKEN = os.environ.get("HA_TOKEN", "")
CAN_INTERFACE = os.environ.get("CAN_INTERFACE", "can0")

EZKONTROL_DUMMY = os.environ.get("EZKONTROL_DUMMY", "false").lower() == "true"
EZKONTROL_PUSH_INTERVAL = int(os.environ.get("EZKONTROL_PUSH_INTERVAL", "2"))
BESTGO_DUMMY = os.environ.get("BESTGO_DUMMY", "false").lower() == "true"
BESTGO_PUSH_INTERVAL = int(os.environ.get("BESTGO_PUSH_INTERVAL", "5"))

HEADERS = {
    "Authorization": f"Bearer {HA_TOKEN}",
    "Content-Type": "application/json",
}


# ===========================================================================
# EZkontrol B48800 motor controller  (MCU-to-METER protocol)
# ===========================================================================
EZ_MSG1_ID = 0x180117EF  # voltage, current, speed
EZ_MSG2_ID = 0x180217EF  # temps, status, errors

GEAR_MAP = {0: "None", 1: "R", 2: "N", 3: "D1", 4: "D2", 5: "D3", 6: "S", 7: "P"}

ERROR_BITS_BYTE4 = [
    "Overcurrent", "Overload", "Overvoltage", "Undervoltage",
    "Controller Overheat", "Motor Overheat", "Motor Stalled", "Motor Out of Phase",
]
ERROR_BITS_BYTE5 = [
    "Motor Sensor", "Motor AUX Sensor", "Encoder Misaligned", "Anti-Runaway",
    "Main Accelerator", "AUX Accelerator", "Pre-charge", "DC Contactor",
]
# Spec defines only bits 0-5 of byte 6; 6-7 are reserved.
ERROR_BITS_BYTE6 = [
    "Power Valve", "Current Sensor", "Auto-tune", "RS485", "CAN", "Software",
]


def ez_decode_msg1(data):
    # Speed spec says 0.1 rpm/bit but +/-32000 rpm can't fit a uint16 at that
    # resolution. Empirically (motor at rest reads 0) it's 1 rpm/bit.
    bus_v_raw   = struct.unpack_from('<H', data, 0)[0]
    bus_i_raw   = struct.unpack_from('<H', data, 2)[0]
    phase_i_raw = struct.unpack_from('<H', data, 4)[0]
    speed_raw   = struct.unpack_from('<H', data, 6)[0]
    return {
        "bus_voltage":   round(bus_v_raw * 0.1, 1),
        "bus_current":   round(bus_i_raw * 0.1 - 3200, 1),
        "phase_current": round(phase_i_raw * 0.1 - 3200, 1),
        "motor_speed":   speed_raw - 32000,
    }


def ez_decode_msg2(data):
    sb = data[3]
    errors = []
    for i, name in enumerate(ERROR_BITS_BYTE4):
        if data[4] & (1 << i): errors.append(name)
    for i, name in enumerate(ERROR_BITS_BYTE5):
        if data[5] & (1 << i): errors.append(name)
    for i, name in enumerate(ERROR_BITS_BYTE6):
        if data[6] & (1 << i): errors.append(name)
    errors_str = ", ".join(errors) if errors else "None"
    if len(errors_str) > 250:                    # HA state values cap at 255
        errors_str = errors_str[:247] + "..."
    return {
        "controller_temp": data[0] - 40,
        "motor_temp":      data[1] - 40,
        "throttle":        data[2],
        "gear":            GEAR_MAP.get(sb & 0x07, "Unknown"),
        "brake":           "On" if (sb >> 3) & 1 else "Off",
        "op_mode":         (sb >> 4) & 0x07,
        "dc_contactor":    "On" if (sb >> 7) & 1 else "Off",
        "errors":          errors_str,
        "error_count":     len(errors),
    }


def ez_decode(arb_id, data):
    """Return decoded fields if the frame is EZkontrol's, else None."""
    if arb_id == EZ_MSG1_ID and len(data) >= 8:
        return ez_decode_msg1(data)
    if arb_id == EZ_MSG2_ID and len(data) >= 8:
        return ez_decode_msg2(data)
    return None


def ez_dummy():
    return {
        "bus_voltage":     round(random.uniform(48.0, 54.0), 1),
        "bus_current":     round(random.uniform(0.0, 25.0), 1),
        "phase_current":   round(random.uniform(0.0, 50.0), 1),
        "motor_speed":     random.randint(0, 3000),
        "controller_temp": random.randint(20, 45),
        "motor_temp":      random.randint(25, 55),
        "throttle":        random.randint(0, 100),
        "gear":            random.choice(["D1", "D2", "D3", "N"]),
        "brake":           random.choice(["Off", "Off", "Off", "On"]),
        "op_mode":         0,
        "dc_contactor":    "On",
        "errors":          "None",
        "error_count":     0,
    }


EZ_SENSORS = {
    "bus_voltage":     {"unit": "V",   "icon": "mdi:flash",             "device_class": "voltage"},
    "bus_current":     {"unit": "A",   "icon": "mdi:current-dc",        "device_class": "current"},
    "phase_current":   {"unit": "A",   "icon": "mdi:current-ac",        "device_class": "current"},
    "motor_speed":     {"unit": "rpm", "icon": "mdi:speedometer",       "device_class": None},
    "controller_temp": {"unit": "°C",  "icon": "mdi:thermometer",       "device_class": "temperature"},
    "motor_temp":      {"unit": "°C",  "icon": "mdi:thermometer-alert", "device_class": "temperature"},
    "throttle":        {"unit": "%",   "icon": "mdi:gauge",             "device_class": None},
    "gear":            {"unit": None,  "icon": "mdi:car-shift-pattern", "device_class": None},
    "brake":           {"unit": None,  "icon": "mdi:car-brake-alert",   "device_class": None},
    "op_mode":         {"unit": None,  "icon": "mdi:cog",               "device_class": None},
    "dc_contactor":    {"unit": None,  "icon": "mdi:electric-switch",   "device_class": None},
    "errors":          {"unit": None,  "icon": "mdi:alert-circle",      "device_class": None},
    "error_count":     {"unit": None,  "icon": "mdi:counter",           "device_class": None},
}


# ===========================================================================
# BESTGO battery (Lithium Valley BMS)  -- SMA/Pylontech-compatible protocol
# ===========================================================================
BG_LIMITS, BG_SOC, BG_MEAS, BG_ALARMS = 0x351, 0x355, 0x356, 0x35A
BG_MFR, BG_INFO, BG_NAME0, BG_NAME1 = 0x35E, 0x35F, 0x370, 0x371
BG_CELLEXT, BG_CAPACITY = 0x373, 0x379
BG_IDS = {BG_LIMITS, BG_SOC, BG_MEAS, BG_ALARMS, BG_MFR, BG_INFO,
          BG_NAME0, BG_NAME1, BG_CELLEXT, BG_CAPACITY}
KELVIN = 273.15

_bg_name = {}   # name-frame halves: {0: bytes from 0x370, 1: bytes from 0x371}


def _u16(data, off):
    return int.from_bytes(data[off:off + 2], "little")


def _s16(data, off):
    return int.from_bytes(data[off:off + 2], "little", signed=True)


def _ascii(raw):
    out = []
    for b in raw:
        if b == 0:
            break
        out.append(chr(b) if 32 <= b < 127 else ".")
    return "".join(out).strip()


def _bg_battery_name():
    parts = [_ascii(_bg_name.get(0, b"")), _ascii(_bg_name.get(1, b""))]
    return " ".join(p for p in parts if p) or "?"


def bg_decode(arb_id, data):
    """Return decoded fields if the frame is BESTGO's, else None.

    A recognised ID with a too-short payload returns an empty dict -- the ID
    is still claimed, so it won't fall through to the EZkontrol decoder.
    """
    if arb_id not in BG_IDS:
        return None
    f = {}
    try:
        if arb_id == BG_LIMITS and len(data) >= 8:
            f["charge_voltage_limit"]    = round(_u16(data, 0) * 0.1, 1)
            f["charge_current_limit"]    = round(_s16(data, 2) * 0.1, 1)
            f["discharge_current_limit"] = round(_s16(data, 4) * 0.1, 1)
            f["discharge_voltage_limit"] = round(_u16(data, 6) * 0.1, 1)
        elif arb_id == BG_SOC and len(data) >= 4:
            f["soc"] = _u16(data, 0)
            f["soh"] = _u16(data, 2)
        elif arb_id == BG_MEAS and len(data) >= 6:
            f["pack_voltage"] = round(_s16(data, 0) * 0.01, 2)
            f["pack_current"] = round(_s16(data, 2) * 0.1, 1)
            f["pack_temp"]    = round(_s16(data, 4) * 0.1, 1)
        elif arb_id == BG_ALARMS and len(data) >= 8:
            alarm, warn = data[0:4], data[4:8]
            f["alarms"]   = "OK" if not any(alarm) else alarm.hex()
            f["warnings"] = "OK" if not any(warn) else warn.hex()
        elif arb_id == BG_MFR:
            f["manufacturer"] = _ascii(data)
        elif arb_id == BG_INFO and len(data) >= 6:
            ver = _u16(data, 2)
            f["firmware"]         = f"v{ver >> 8}.{ver & 0xFF}"
            f["nominal_capacity"] = _u16(data, 4)
        elif arb_id == BG_NAME0:
            _bg_name[0] = bytes(data)
            f["battery_name"] = _bg_battery_name()
        elif arb_id == BG_NAME1:
            _bg_name[1] = bytes(data)
            f["battery_name"] = _bg_battery_name()
        elif arb_id == BG_CELLEXT and len(data) >= 8:
            vmin, vmax = _u16(data, 0), _u16(data, 2)
            f["cell_voltage_min"]   = vmin
            f["cell_voltage_max"]   = vmax
            f["cell_voltage_delta"] = vmax - vmin
            tmin, tmax = _u16(data, 4), _u16(data, 6)
            if tmin:
                f["cell_temp_min"] = round(tmin - KELVIN, 1)
            if tmax:
                f["cell_temp_max"] = round(tmax - KELVIN, 1)
        elif arb_id == BG_CAPACITY and len(data) >= 2:
            f["installed_capacity"] = _u16(data, 0)
    except Exception:
        logging.debug("malformed BESTGO frame 0x%X", arb_id, exc_info=True)
    return f


def bg_dummy():
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
    "warnings":                {"unit": None, "icon": "mdi:alert",             "device_class": None},
    "firmware":                {"unit": None, "icon": "mdi:chip",              "device_class": None},
    "nominal_capacity":        {"unit": "Ah", "icon": "mdi:battery",           "device_class": None},
    "installed_capacity":      {"unit": "Ah", "icon": "mdi:battery",           "device_class": None},
    "manufacturer":            {"unit": None, "icon": "mdi:factory",           "device_class": None},
    "battery_name":            {"unit": None, "icon": "mdi:tag",              "device_class": None},
}


# ===========================================================================
# Device model + HA push
# ===========================================================================
class Device:
    """One CAN device: its decoder, dummy generator, sensors and push timer."""

    def __init__(self, name, prefix, dummy, push_interval,
                 decode_fn, dummy_fn, sensors, summary_fn):
        self.name = name
        self.prefix = prefix
        self.dummy = dummy
        self.push_interval = push_interval
        self.decode_fn = decode_fn
        self.dummy_fn = dummy_fn
        self.sensors = sensors
        self.summary_fn = summary_fn
        self.data = {}
        self.last_push = 0.0

    def decode(self, arb_id, raw):
        """Decode a frame into self.data. Return True if it belonged here."""
        fields = self.decode_fn(arb_id, raw)
        if fields is None:
            return False
        self.data.update(fields)
        return True


def push_device(device):
    """POST every decoded field of `device` to the HA REST API as a sensor."""
    for key, value in device.data.items():
        entity_id = f"sensor.{device.prefix}_{key}"
        cfg = device.sensors.get(key, {})
        attrs = {
            "friendly_name": f"{device.name} {key.replace('_', ' ').title()}",
            "icon":          cfg.get("icon", "mdi:information"),
            "source":        "solar_car_canbus",
        }
        if cfg.get("unit"):
            attrs["unit_of_measurement"] = cfg["unit"]
        if cfg.get("device_class"):
            attrs["device_class"] = cfg["device_class"]
        try:
            r = requests.post(
                f"{HA_URL}/api/states/{entity_id}",
                headers=HEADERS,
                json={"state": str(value), "attributes": attrs},
                timeout=5,
            )
            if r.status_code not in (200, 201):
                logging.warning(f"{entity_id}: HTTP {r.status_code} {r.text[:200]}")
        except Exception as e:
            logging.error(f"{entity_id}: {e}")


EZKONTROL = Device(
    name="EZkontrol", prefix="ezkontrol",
    dummy=EZKONTROL_DUMMY, push_interval=EZKONTROL_PUSH_INTERVAL,
    decode_fn=ez_decode, dummy_fn=ez_dummy, sensors=EZ_SENSORS,
    summary_fn=lambda d: (f"V={d.get('bus_voltage')} I={d.get('bus_current')} "
                          f"RPM={d.get('motor_speed')} CT={d.get('controller_temp')}"),
)
BESTGO = Device(
    name="BESTGO", prefix="bestgo",
    dummy=BESTGO_DUMMY, push_interval=BESTGO_PUSH_INTERVAL,
    decode_fn=bg_decode, dummy_fn=bg_dummy, sensors=BG_SENSORS,
    summary_fn=lambda d: (f"V={d.get('pack_voltage')} I={d.get('pack_current')} "
                          f"SOC={d.get('soc')}% T={d.get('pack_temp')}"),
)


def main():
    logging.info("Solar Car CAN Reader starting")
    logging.info(f"  EZkontrol: dummy={EZKONTROL_DUMMY} push={EZKONTROL_PUSH_INTERVAL}s")
    logging.info(f"  BESTGO:    dummy={BESTGO_DUMMY} push={BESTGO_PUSH_INTERVAL}s")
    if not HA_TOKEN:
        logging.warning("HA_TOKEN is empty; REST pushes will fail")

    devices = [EZKONTROL, BESTGO]
    live = [d for d in devices if not d.dummy]

    bus = None
    if live:
        import can
        logging.info(f"Opening SocketCAN {CAN_INTERFACE}")
        bus = can.interface.Bus(channel=CAN_INTERFACE, interface='socketcan')
        logging.info("Listening (live: " + ", ".join(d.name for d in live) + ")")
    else:
        logging.info("All devices in dummy mode; CAN bus not opened")

    try:
        while True:
            if bus is not None:
                msg = bus.recv(timeout=0.2)
                if msg is not None:
                    raw = bytes(msg.data)
                    for d in live:
                        if d.decode(msg.arbitration_id, raw):
                            break
            else:
                time.sleep(0.2)

            now = time.time()
            for d in devices:
                if now - d.last_push < d.push_interval:
                    continue
                if d.dummy:
                    d.data = d.dummy_fn()
                if d.data:
                    push_device(d)
                    logging.info(f"{d.name}: {d.summary_fn(d.data)}")
                    d.last_push = now
    except KeyboardInterrupt:
        logging.info("Shutting down")
    finally:
        if bus is not None:
            bus.shutdown()


if __name__ == "__main__":
    main()
