#!/usr/bin/env python3
"""Solar Car CANbus reader for Home Assistant.

Reads two devices from ONE shared SocketCAN bus and pushes named sensor
states to HA via the REST API:

  * EZkontrol B48800 motor controller  -- 29-bit extended IDs (0x1801xxxx)
  * BESTGO battery (Lithium Valley BMS) -- 11-bit standard IDs (0x351..0x379)

The ID ranges don't overlap, so a single socket sees and decodes both. Each
device has an independent push interval and dummy-mode flag, set in the
add-on options and passed through by run.sh as environment variables.

The frame protocols live in the shared solarcar_can package; the copy next
to this file is VENDORED from CANbus_data/solarcar_can/ by sync_addon.py
(HA builds local add-ons with this folder as the Docker context, so the
package has to be carried inside it). Edit the original, then re-sync.
"""
import os
import time
import logging

import requests

from solarcar_can import bestgo, ezkontrol
from solarcar_can.bestgo import BestgoDecoder, BG_SENSORS
from solarcar_can.ezkontrol import EzkontrolDecoder, EZ_SENSORS

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


class Device:
    """One CAN device: its decoder, dummy generator, sensors and push timer."""

    def __init__(self, name, prefix, dummy, push_interval,
                 decoder, dummy_fn, sensors, summary_fn):
        self.name = name
        self.prefix = prefix
        self.dummy = dummy
        self.push_interval = push_interval
        self.decoder = decoder
        self.dummy_fn = dummy_fn
        self.sensors = sensors
        self.summary_fn = summary_fn
        self.data = {}
        self.last_push = 0.0

    def decode(self, arb_id, raw):
        """Decode a frame into self.data. Return True if it belonged here."""
        fields = self.decoder.decode(arb_id, raw)
        if fields is None:
            return False
        self.data.update(fields)
        return True


def push_device(device):
    """POST the decoded fields of `device` to the HA REST API as sensors.

    Only fields listed in the device's sensor table are pushed; extra
    decoded fields (soc_hi, chemistry, life) stay dashboard-only so the
    published sensor set doesn't change.
    """
    for key, value in device.data.items():
        cfg = device.sensors.get(key)
        if cfg is None:
            continue
        entity_id = f"sensor.{device.prefix}_{key}"
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
    decoder=EzkontrolDecoder(), dummy_fn=ezkontrol.dummy_fields,
    sensors=EZ_SENSORS,
    summary_fn=lambda d: (f"V={d.get('bus_voltage')} I={d.get('bus_current')} "
                          f"RPM={d.get('motor_speed')} CT={d.get('controller_temp')}"),
)
BESTGO = Device(
    name="BESTGO", prefix="bestgo",
    dummy=BESTGO_DUMMY, push_interval=BESTGO_PUSH_INTERVAL,
    decoder=BestgoDecoder(), dummy_fn=bestgo.dummy_fields,
    sensors=BG_SENSORS,
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
