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
import subprocess

import requests

from solarcar_can import bestgo, ezkontrol
from solarcar_can.bestgo import BestgoDecoder, BG_SENSORS
from solarcar_can.ezkontrol import EzkontrolDecoder, EZ_SENSORS

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s')

HA_URL = os.environ.get("HA_URL", "http://supervisor/core")
HA_TOKEN = os.environ.get("HA_TOKEN", "")
CAN_INTERFACE = os.environ.get("CAN_INTERFACE", "can0")
CAN_BITRATE = os.environ.get("CAN_BITRATE", "500000")

BUS_RETRY_SEC = 10       # how often to retry opening a lost/missing CAN bus
STATUS_MISS_INTERVALS = 3  # device status drops to 0 after this many silent push intervals

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
        self.last_rx = 0.0       # when this device last claimed a frame
        self.last_status = None  # last pushed status value (for change logging)

    def decode(self, arb_id, raw):
        """Decode a frame into self.data. Return True if it belonged here."""
        fields = self.decoder.decode(arb_id, raw)
        if fields is None:
            return False
        self.data.update(fields)
        self.last_rx = time.time()
        return True

    def status(self, now):
        """1 if this device is alive: dummy mode counts as alive; live mode
        requires a frame within STATUS_MISS_INTERVALS push intervals."""
        if self.dummy:
            return 1
        return 1 if (now - self.last_rx) <= STATUS_MISS_INTERVALS * self.push_interval else 0


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
        push_state(entity_id, value, attrs)


def push_state(entity_id, value, attrs):
    """POST one entity state to the HA REST API."""
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


def push_device_status(device, now):
    """Push sensor.<prefix>_status (1 = alive, 0 = silent). Logs transitions."""
    val = device.status(now)
    if val != device.last_status:
        logging.info(f"{device.name} status -> {val}"
                     + ("" if val else f" (no frames for {STATUS_MISS_INTERVALS}x{device.push_interval}s)"))
        device.last_status = val
    push_state(f"sensor.{device.prefix}_status", val, {
        "friendly_name": f"{device.name} Status",
        "icon": "mdi:check-network" if val else "mdi:close-network",
        "source": "solar_car_canbus",
    })


def push_adapter_status(val):
    """Push sensor.canadapter_status (1 = CAN bus open, 0 = adapter missing/lost)."""
    push_state("sensor.canadapter_status", val, {
        "friendly_name": "CAN Adapter Status",
        "icon": "mdi:usb-port" if val else "mdi:usb-off",
        "source": "solar_car_canbus",
    })


def _iface_is_up():
    """True if CAN_INTERFACE exists and is administratively up (IFF_UP).

    A SocketCAN bind succeeds even on a *down* interface (recv then fails),
    so we can't rely on can.Bus() raising to detect a dropped link — we have
    to check IFF_UP ourselves before opening."""
    try:
        with open(f"/sys/class/net/{CAN_INTERFACE}/flags") as f:
            return bool(int(f.read().strip(), 16) & 0x1)   # IFF_UP
    except OSError:
        return False   # interface missing (adapter unplugged)


def open_bus():
    """Bring CAN_INTERFACE up if it's down or missing, then open SocketCAN.

    The add-on has NET_ADMIN and iproute2, so a downed or replugged adapter
    heals without restarting the add-on. Returns None if the interface can't
    be brought up (e.g. adapter physically unplugged) — the caller keeps
    retrying and reports canadapter_status=0 meanwhile."""
    import can
    if not _iface_is_up():
        try:
            subprocess.run(["ip", "link", "set", CAN_INTERFACE, "down"],
                           capture_output=True)
            subprocess.run(["ip", "link", "set", CAN_INTERFACE, "type", "can",
                            "bitrate", CAN_BITRATE], check=True, capture_output=True)
            subprocess.run(["ip", "link", "set", CAN_INTERFACE, "up"],
                           check=True, capture_output=True)
        except Exception as e:
            logging.debug(f"CAN bring-up failed: {e}")
            return None
    try:
        return can.interface.Bus(channel=CAN_INTERFACE, interface='socketcan')
    except Exception as e:
        logging.debug(f"CAN open failed: {e}")
        return None


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
    bus_retry_at = 0.0
    adapter_pushed = None    # last pushed canadapter_status value
    adapter_push_at = 0.0
    adapter_interval = min(d.push_interval for d in devices)

    if live:
        logging.info(f"Opening SocketCAN {CAN_INTERFACE}")
        bus = open_bus()
        if bus is not None:
            logging.info("Listening (live: " + ", ".join(d.name for d in live) + ")")
        else:
            logging.error(f"{CAN_INTERFACE} not available; will keep retrying "
                          f"every {BUS_RETRY_SEC}s (canadapter_status=0)")
    else:
        logging.info("All devices in dummy mode; CAN bus not opened")

    try:
        while True:
            now = time.time()
            if live and bus is None and now >= bus_retry_at:
                bus_retry_at = now + BUS_RETRY_SEC
                bus = open_bus()
                if bus is not None:
                    logging.info(f"{CAN_INTERFACE} recovered; listening again")

            if bus is not None:
                try:
                    msg = bus.recv(timeout=0.2)
                except Exception as e:
                    logging.error(f"CAN bus lost: {e}")
                    try:
                        bus.shutdown()
                    except Exception:
                        pass
                    bus = None
                    msg = None
                if msg is not None:
                    raw = bytes(msg.data)
                    for d in live:
                        if d.decode(msg.arbitration_id, raw):
                            break
            else:
                time.sleep(0.2)

            now = time.time()

            # canadapter_status: 1 while the bus is open (all-dummy counts
            # as 1). Pushed on every change and at least every push interval.
            adapter_ok = 1 if (not live or bus is not None) else 0
            if adapter_ok != adapter_pushed or now - adapter_push_at >= adapter_interval:
                if adapter_ok != adapter_pushed:
                    logging.info(f"CAN adapter status -> {adapter_ok}")
                push_adapter_status(adapter_ok)
                adapter_pushed = adapter_ok
                adapter_push_at = now

            for d in devices:
                if now - d.last_push < d.push_interval:
                    continue
                d.last_push = now
                if d.dummy:
                    d.data = d.dummy_fn()
                if d.data:
                    push_device(d)
                    logging.info(f"{d.name}: {d.summary_fn(d.data)}")
                # Status is pushed every interval even with no data, so a
                # silent device reads 0 instead of having missing sensors.
                push_device_status(d, now)
    except KeyboardInterrupt:
        logging.info("Shutting down")
    finally:
        if bus is not None:
            bus.shutdown()


if __name__ == "__main__":
    main()
