#!/usr/bin/env python3
"""EZkontrol B48800 CAN reader for Home Assistant.

Listens for two periodic MCU frames on SocketCAN, decodes them, and pushes
named sensor states to HA via the REST API. Auth + URL are provided by the
add-on environment (see run.sh).
"""
import os
import sys
import time
import struct
import logging
import random

import requests

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

HA_URL = os.environ.get("HA_URL", "http://supervisor/core")
HA_TOKEN = os.environ.get("HA_TOKEN", "")
DUMMY_MODE = os.environ.get("DUMMY_MODE", "false").lower() == "true"
CAN_BITRATE = int(os.environ.get("CAN_BITRATE", "250000"))
CAN_INTERFACE = os.environ.get("CAN_INTERFACE", "can0")
PUSH_INTERVAL = int(os.environ.get("PUSH_INTERVAL", "2"))

HEADERS = {
    "Authorization": f"Bearer {HA_TOKEN}",
    "Content-Type": "application/json",
}

# EZkontrol MCU-to-METER message IDs (29-bit extended)
MSG1_ID = 0x180117EF  # voltage, current, speed
MSG2_ID = 0x180217EF  # temps, status, errors

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


def decode_msg1(data):
    # Speed spec says 0.1 rpm/bit but range +/-32000 rpm can't fit a uint16 at
    # that resolution. Empirically (motor at rest reads 0) it's 1 rpm/bit.
    bus_v_raw       = struct.unpack_from('<H', data, 0)[0]
    bus_i_raw       = struct.unpack_from('<H', data, 2)[0]
    phase_i_raw     = struct.unpack_from('<H', data, 4)[0]
    speed_raw       = struct.unpack_from('<H', data, 6)[0]
    return {
        "bus_voltage":   round(bus_v_raw * 0.1, 1),
        "bus_current":   round(bus_i_raw * 0.1 - 3200, 1),
        "phase_current": round(phase_i_raw * 0.1 - 3200, 1),
        "motor_speed":   speed_raw - 32000,
    }


def decode_msg2(data):
    sb = data[3]
    errors = []
    for i, name in enumerate(ERROR_BITS_BYTE4):
        if data[4] & (1 << i): errors.append(name)
    for i, name in enumerate(ERROR_BITS_BYTE5):
        if data[5] & (1 << i): errors.append(name)
    for i, name in enumerate(ERROR_BITS_BYTE6):
        if data[6] & (1 << i): errors.append(name)
    return {
        "controller_temp": data[0] - 40,
        "motor_temp":      data[1] - 40,
        "throttle":        data[2],
        "gear":            GEAR_MAP.get(sb & 0x07, "Unknown"),
        "brake":           "On" if (sb >> 3) & 1 else "Off",
        "op_mode":         (sb >> 4) & 0x07,
        "dc_contactor":    "On" if (sb >> 7) & 1 else "Off",
        "errors":          ", ".join(errors) if errors else "None",
        "error_count":     len(errors),
    }


def generate_dummy_data():
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


SENSOR_CONFIG = {
    "bus_voltage":      {"unit": "V",   "icon": "mdi:flash",             "device_class": "voltage"},
    "bus_current":      {"unit": "A",   "icon": "mdi:current-dc",        "device_class": "current"},
    "phase_current":    {"unit": "A",   "icon": "mdi:current-ac",        "device_class": "current"},
    "motor_speed":      {"unit": "rpm", "icon": "mdi:speedometer",       "device_class": None},
    "controller_temp":  {"unit": "°C",  "icon": "mdi:thermometer",       "device_class": "temperature"},
    "motor_temp":       {"unit": "°C",  "icon": "mdi:thermometer-alert", "device_class": "temperature"},
    "throttle":         {"unit": "%",   "icon": "mdi:gauge",             "device_class": None},
    "gear":             {"unit": None,  "icon": "mdi:car-shift-pattern", "device_class": None},
    "brake":            {"unit": None,  "icon": "mdi:car-brake-alert",   "device_class": None},
    "op_mode":          {"unit": None,  "icon": "mdi:cog",               "device_class": None},
    "dc_contactor":     {"unit": None,  "icon": "mdi:electric-switch",   "device_class": None},
    "errors":           {"unit": None,  "icon": "mdi:alert-circle",      "device_class": None},
    "error_count":      {"unit": None,  "icon": "mdi:counter",           "device_class": None},
}


def push_to_ha(sensor_data):
    for key, value in sensor_data.items():
        entity_id = f"sensor.ezkontrol_{key}"
        cfg = SENSOR_CONFIG.get(key, {})
        attrs = {
            "friendly_name": f"EZkontrol {key.replace('_', ' ').title()}",
            "icon":          cfg.get("icon", "mdi:information"),
            "source":        "can_reader",
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


def run_dummy_mode():
    logging.info("DUMMY mode - generating fake sensor data")
    while True:
        data = generate_dummy_data()
        logging.info(f"V={data['bus_voltage']}V I={data['bus_current']}A "
                     f"RPM={data['motor_speed']} CT={data['controller_temp']}C")
        push_to_ha(data)
        time.sleep(PUSH_INTERVAL)


def run_can_mode():
    import can
    logging.info(f"Opening SocketCAN {CAN_INTERFACE}")
    bus = can.interface.Bus(channel=CAN_INTERFACE, interface='socketcan')
    logging.info("Listening...")

    sensor_data = {}
    last_push = 0.0

    try:
        while True:
            msg = bus.recv(timeout=1.0)
            if msg is None:
                continue
            if msg.arbitration_id == MSG1_ID:
                sensor_data.update(decode_msg1(msg.data))
            elif msg.arbitration_id == MSG2_ID:
                sensor_data.update(decode_msg2(msg.data))

            now = time.time()
            if now - last_push >= PUSH_INTERVAL and sensor_data:
                logging.info(f"V={sensor_data.get('bus_voltage')}V "
                             f"I={sensor_data.get('bus_current')}A "
                             f"RPM={sensor_data.get('motor_speed')} "
                             f"CT={sensor_data.get('controller_temp')}C")
                push_to_ha(sensor_data)
                last_push = now
    except KeyboardInterrupt:
        logging.info("Shutting down")
    finally:
        bus.shutdown()


if __name__ == "__main__":
    logging.info(f"EZkontrol CAN Reader starting (dummy={DUMMY_MODE})")
    if not HA_TOKEN:
        logging.warning("HA_TOKEN is empty; REST pushes will fail")
    if DUMMY_MODE:
        run_dummy_mode()
    else:
        run_can_mode()
