#!/usr/bin/env python3
"""Solar Car CANbus reader for Home Assistant.

Reads two devices from ONE shared CAN bus (an SH-C31G on slcan firmware,
opened as a serial port via python-can) and pushes named sensor states to HA
via the REST API:

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
import glob
import time
import socket
import struct
import logging
import threading

import requests

from solarcar_can import bestgo, ezkontrol
from solarcar_can.bestgo import BestgoDecoder, BG_SENSORS
from solarcar_can.ezkontrol import EzkontrolDecoder, EZ_SENSORS

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s')

HA_URL = os.environ.get("HA_URL", "http://supervisor/core")
HA_TOKEN = os.environ.get("HA_TOKEN", "")
CAN_PORT = os.environ.get("CAN_PORT", "")   # slcan serial port; "" = auto-detect
CAN_BITRATE = os.environ.get("CAN_BITRATE", "500000")

BUS_RETRY_SEC = 10       # how often to retry opening a lost/missing CAN bus
STATUS_MISS_INTERVALS = 3  # device status drops to 0 after this many silent push intervals
NET_INTERVAL = 10        # seconds between host-network checks (runs off-thread)
NET_TIMEOUT = 1.5        # per-probe TCP connect timeout

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
        self.good_data = False
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


# ===========================================================================
# Host network monitoring
#
# Runs on its own thread (NetworkMonitor) so its blocking TCP probes never
# stall the CAN read loop. The add-on has host_network, so these see the
# host's real interfaces -- the address other machines reach HA at, not HA
# core's internal container IP (which the built-in local_ip sensor reports).
# ===========================================================================
def host_ip():
    """Primary LAN IPv4 of the host, via the default-route source-IP trick
    (no packets sent). None when there's no usable LAN address."""
    ip = None
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("1.1.1.1", 80))   # picks the source IP for the default route
        ip = s.getsockname()[0]
    except OSError:
        pass
    finally:
        s.close()
    if ip and not ip.startswith("127."):
        return ip
    try:                              # fallback when there is no default route
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            cand = info[4][0]
            if not cand.startswith("127."):
                return cand
    except OSError:
        pass
    return None


def default_gateway():
    """The host's default-route gateway IPv4 (read live from /proc/net/route,
    so it tracks network changes -- router vs hotspot vs ethernet). None if
    there is no default route."""
    try:
        with open("/proc/net/route") as f:
            for line in f.readlines()[1:]:
                p = line.split()
                if len(p) > 3 and p[1] == "00000000" and int(p[3], 16) & 0x2:
                    return socket.inet_ntoa(struct.pack("<L", int(p[2], 16)))
    except OSError:
        pass
    return None


def tcp_reachable(host, ports, timeout=NET_TIMEOUT):
    """True if `host` answers at L3 on any of `ports`. A refused connection
    (RST) still proves reachability, so this works against gateways with no
    open ports -- only a timeout / no-route counts as unreachable."""
    if not host:
        return False
    for port in ports:
        try:
            socket.create_connection((host, port), timeout=timeout).close()
            return True
        except ConnectionRefusedError:
            return True
        except OSError:
            continue
    return False


def push_connectivity(entity_id, ok, name, icon_on, icon_off):
    """Push a binary_sensor (connectivity) as on/off."""
    push_state(entity_id, "on" if ok else "off", {
        "friendly_name": name,
        "device_class": "connectivity",
        "icon": icon_on if ok else icon_off,
        "source": "solar_car_canbus",
    })


def network_monitor(stop):
    """Daemon loop: every NET_INTERVAL, publish the host IP and the LAN/WAN
    reachability sensors. Kept off the main loop so the TCP probes' timeouts
    can never delay CAN frame reading."""
    last = None
    while not stop.is_set():
        ip = host_ip()
        gw = default_gateway()
        lan = tcp_reachable(gw, (80, 443, 53)) if gw else bool(ip)
        wan = tcp_reachable("1.1.1.1", (53,)) or tcp_reachable("8.8.8.8", (53,))

        push_state("sensor.haos_ip_address", ip or "unknown", {
            "friendly_name": "HAOS IP Address",
            "icon": "mdi:ip-network",
            "source": "solar_car_canbus",
        })
        push_state("sensor.network_status", 1 if ip else 0, {
            "friendly_name": "Network Status",
            "icon": "mdi:lan-connect" if ip else "mdi:lan-disconnect",
            "source": "solar_car_canbus",
        })
        push_connectivity("binary_sensor.lan_connected", lan, "LAN Connected",
                          "mdi:lan-connect", "mdi:lan-disconnect")
        push_connectivity("binary_sensor.wan_connected", wan, "WAN Connected",
                          "mdi:web", "mdi:web-off")

        cur = (ip, lan, wan)
        if cur != last:
            logging.info(f"network: ip={ip or 'none'} "
                         f"lan={'up' if lan else 'down'} "
                         f"wan={'up' if wan else 'down'}")
            last = cur
        stop.wait(NET_INTERVAL)


def can0_ready():
    """True if can0 exists and isn't down. The SH-C31G on candlelight/gs_usb
    firmware is exposed by the kernel gs_usb driver as SocketCAN can0; run.sh
    sets its bitrate and brings it up. Re-checked each retry so a replugged
    adapter is picked up without restarting the add-on. (CAN links report
    operstate 'unknown' when UP, so anything but 'down' counts as ready.)"""
    try:
        with open("/sys/class/net/can0/operstate") as f:
            return f.read().strip() != "down"
    except OSError:
        return False


def open_bus():
    """Open can0 via SocketCAN with python-can. Returns a Bus or None.

    The SH-C31G runs candlelight/gs_usb firmware; the kernel gs_usb driver
    exposes it as can0 and run.sh sets the bitrate + brings it up. (The earlier
    "gs_usb/SocketCAN is broken on HAOS" theory was a sleeping-battery confound
    -- once the BMS is broadcasting, can0 receives fine, confirmed 2026-06-29.)
    Returns None if can0 isn't ready -- the caller keeps retrying and reports
    canadapter_status=0 meanwhile."""
    import can
    if not can0_ready():
        return None
    try:
        return can.Bus(interface="socketcan", channel="can0")
    except Exception as e:
        logging.debug(f"socketcan open failed on can0: {e}")
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

    # Host network monitoring runs on its own thread so its blocking probes
    # never delay CAN reads.
    net_stop = threading.Event()
    threading.Thread(target=network_monitor, args=(net_stop,),
                     name="network-monitor", daemon=True).start()

    if live:
        bus = open_bus()
        if bus is not None:
            logging.info(f"can0 open (SocketCAN @ {CAN_BITRATE} bps); "
                         "listening (live: " + ", ".join(d.name for d in live) + ")")
        else:
            logging.error("can0 not ready; will keep retrying "
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
                    logging.info("can0 recovered; listening again")

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
                    # A frame belongs to exactly one device (disjoint arbitration
                    # ids), so stop at the owner. Only SET the owner's flag - never
                    # touch the others, or a frame for one device would clear the
                    # other's "fresh data" flag and stall its pushes.
                    for d in live:
                        if d.decode(msg.arbitration_id, raw):
                            d.good_data = True
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
                    d.good_data = True        # dummy data is always fresh
                if d.good_data:
                    push_device(d)
                    d.good_data = False       # consume: the next push needs a new frame
                    logging.info(f"{d.name}: {d.summary_fn(d.data)}")
                # Status is pushed every interval even with no data, so a
                # silent device reads 0 instead of having missing sensors.
                push_device_status(d, now)
    except KeyboardInterrupt:
        logging.info("Shutting down")
    finally:
        net_stop.set()
        if bus is not None:
            bus.shutdown()


if __name__ == "__main__":
    main()
