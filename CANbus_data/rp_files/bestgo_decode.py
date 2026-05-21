"""Live dashboard + ASC logger for the BESTGO battery (Lithium Valley BMS).

Raspberry Pi / SocketCAN version. See pc_files/bestgo_decode.py for the
Windows (gs_usb-direct) version.

Usage:
    python3 bestgo_decode.py [DURATION_SEC] [LOG_PATH]
    python3 bestgo_decode.py -bestgo_dummy

DURATION_SEC = 0 means run until Ctrl+C. LOG_PATH defaults to
logs/bestgo-decode-<timestamp>.asc and is in Vector ASC format.

Pass -bestgo_dummy to drive the dashboard from simulated BMS data when
the battery isn't connected. No bus is opened and no log is written then.

The BESTGO pack speaks the SMA / Pylontech-compatible CAN BMS protocol:
500 kbps, standard 11-bit IDs, ~1 s transmit cycle, little-endian. See
specs/bestgo_spec.txt for the full frame breakdown.

Reads the bus through SocketCAN: the SH-C31G is a gs_usb/candleLight
adapter, which the Linux kernel exposes as a network interface (can0).
Bring the interface up first (this also sets the bitrate) -- see
can_up.sh:

    sudo ip link set can0 type can bitrate 500000
    sudo ip link set can0 up

Override the interface name with the CAN_CHANNEL environment variable.

NOTE: only lightly tested -- captured frames decode correctly against the
spec, but the alarm/warning bit map (0x35A) is unverified, so this shows
the raw alarm/warning bytes rather than naming individual bits.
"""
import os
import sys
import time
import math
import random
import signal
from collections import deque
from datetime import datetime

import can

CAN_CHANNEL = os.environ.get("CAN_CHANNEL", "can0")

DUMMY = "-bestgo_dummy" in sys.argv
if DUMMY:
    sys.argv = [a for a in sys.argv if a != "-bestgo_dummy"]

duration_sec = float(sys.argv[1]) if len(sys.argv) > 1 else 0.0
if duration_sec == 0.0:
    duration_sec = None

if DUMMY:
    log_path = "(dummy mode - logging disabled)"
elif len(sys.argv) > 2:
    log_path = sys.argv[2]
else:
    os.makedirs("logs", exist_ok=True)
    log_path = f"logs/bestgo-decode-{datetime.now():%Y%m%d-%H%M%S}.asc"

# --- BESTGO / SMA-Pylontech frame IDs (standard 11-bit) ---------------------
ID_LIMITS   = 0x351   # charge/discharge V & I limits
ID_SOC      = 0x355   # SOC / SOH / hi-res SOC
ID_MEAS     = 0x356   # pack voltage / current / temperature
ID_ALARMS   = 0x35A   # alarm + warning bitfields
ID_MFR      = 0x35E   # manufacturer name (ASCII)
ID_INFO     = 0x35F   # chemistry / firmware version / capacity
ID_NAME0    = 0x370   # battery name chars 0-7 (ASCII)
ID_NAME1    = 0x371   # battery name chars 8-15 (ASCII)
ID_CELLEXT  = 0x373   # cell V & T min/max
ID_CELL_VLO = 0x374   # ID of min-voltage cell
ID_CELL_VHI = 0x375   # ID of max-voltage cell
ID_CELL_TLO = 0x376   # ID of min-temperature cell
ID_CELL_THI = 0x377   # ID of max-temperature cell
ID_CAPACITY = 0x379   # installed (rated) capacity

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


def parse_limits(data):
    return {
        "cvl": u16(data, 0) * 0.1,   # charge voltage limit
        "ccl": s16(data, 2) * 0.1,   # charge current limit
        "dcl": s16(data, 4) * 0.1,   # discharge current limit
        "dvl": u16(data, 6) * 0.1,   # discharge voltage limit
    }


def parse_soc(data):
    return {
        "soc":     u16(data, 0),
        "soh":     u16(data, 2),
        "soc_hi":  u16(data, 4) * 0.01,
    }


def parse_meas(data):
    return {
        "pack_v": s16(data, 0) * 0.01,
        "pack_i": s16(data, 2) * 0.1,
        "pack_t": s16(data, 4) * 0.1,
    }


def parse_alarms(data):
    alarm = data[0:4]
    warn = data[4:8]
    return {
        "alarms":   "OK" if not any(alarm) else alarm.hex(),
        "warnings": "OK" if not any(warn) else warn.hex(),
    }


def parse_info(data):
    ver = u16(data, 2)
    return {
        "chem":    f"0x{u16(data, 0):04X}",
        "fw":      f"v{ver >> 8}.{ver & 0xFF}",
        "cap_nom": u16(data, 4),
    }


def parse_cellext(data):
    out = {
        "cell_vmin": u16(data, 0),
        "cell_vmax": u16(data, 2),
    }
    tmin, tmax = u16(data, 4), u16(data, 6)
    # Cell temps are in kelvin; 0 means "not reported".
    if tmin:
        out["cell_tmin"] = tmin - KELVIN
    if tmax:
        out["cell_tmax"] = tmax - KELVIN
    return out


# --- Dummy-data generator (for -bestgo_dummy, no hardware needed) -----------
_dummy_t0 = time.monotonic()
_dummy_queue = deque()
_dummy_next = 0.0
DUMMY_PERIOD = 1.0   # the BMS broadcasts its full frame set once per second


def _le(value, width, signed):
    return int(round(value)).to_bytes(width, "little", signed=signed)


def dummy_frames():
    """Build one simulated BMS broadcast cycle (the full SMA/Pylontech set)."""
    t = time.monotonic() - _dummy_t0
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


def dummy_read():
    """Return the next simulated frame (arb, data), paced to real timing."""
    global _dummy_next
    now = time.monotonic()
    if not _dummy_queue and now >= _dummy_next:
        _dummy_queue.extend(dummy_frames())
        _dummy_next = now + DUMMY_PERIOD
    return _dummy_queue.popleft() if _dummy_queue else None


# State + change tracking
state = {}
last_change = {}
HIGHLIGHT_SEC = 0.5
NO_HIGHLIGHT = {"soc_hi"}


def update_state(fields):
    now = time.monotonic()
    for name, new_value in fields.items():
        old = state.get(name)
        state[name] = new_value
        if name not in NO_HIGHLIGHT and old is not None and old != new_value:
            last_change[name] = now


def is_fresh(name):
    return (time.monotonic() - last_change.get(name, 0)) < HIGHLIGHT_SEC


def battery_name():
    """Combine the two name frames once both have arrived."""
    n0 = state.get("_name0", b"")
    n1 = state.get("_name1", b"")
    name = ascii_clean(n0 + n1)
    return name or "?"


TOTAL_W = 58              # full row width including the | borders
CONTENT_W = TOTAL_W - 4   # area between '| ' and ' |'  (= 54)
BORDER_W = TOTAL_W - 2    # dashes between '+' and '+'  (= 56)
SLOT_W = (CONTENT_W - 2) // 2  # two slots with 2-space separator (= 26)


def slot(label, value, name):
    mark = "*" if is_fresh(name) else " "
    # label(12) + value(right-aligned to SLOT_W-14) + ' ' + mark = SLOT_W
    return f"{label:<12}{value:>{SLOT_W - 14}} {mark}"


def row2(slot_a, slot_b):
    return f"| {slot_a}  {slot_b} |"


def row_wide(label, value, name):
    mark = "*" if is_fresh(name) else " "
    inner = f"{label:<12}{value} {mark}"
    return f"| {inner:<{CONTENT_W}} |"


def title_bar(title):
    core = f" {title} "
    pad = BORDER_W - 2 - len(core)
    return "+--" + core + "-" * pad + "+"


def plain_bar():
    return "+" + "-" * BORDER_W + "+"


def empty_row():
    return "| " + " " * CONTENT_W + " |"


def render(fps, last_age):
    s = state
    L = [title_bar("BESTGO Battery   "
                   + ("[DUMMY]" if DUMMY else CAN_CHANNEL))]

    if "_name0" in s or "_name1" in s or "mfr" in s:
        ident = battery_name()
        if s.get("mfr"):
            ident = f"{ident}  ({s['mfr']})"
        L.append(row_wide("Battery", ident, "_name0"))
    else:
        L.append(row_wide("Battery", "(waiting for name frames...)", "_w"))

    if "soc" in s:
        L.append(row2(slot("SOC",  f"{s['soc']} %", "soc"),
                      slot("SOH",  f"{s['soh']} %", "soh")))
    else:
        L.append(empty_row())

    if "pack_v" in s:
        L.append(row2(slot("Pack V", f"{s['pack_v']:.2f} V",  "pack_v"),
                      slot("Pack I", f"{s['pack_i']:+.1f} A", "pack_i")))
        cap = f"{s['cap_nom']} Ah" if "cap_nom" in s else "--"
        L.append(row2(slot("Pack T", f"{s['pack_t']:.1f} C",  "pack_t"),
                      slot("Capacity", cap,                   "cap_nom")))
    else:
        L.append(empty_row())
        L.append(empty_row())

    if "cvl" in s:
        L.append(row_wide("Charge lim",  f"{s['cvl']:.1f} V   {s['ccl']:.1f} A", "cvl"))
        L.append(row_wide("Dischg lim",  f"{s['dvl']:.1f} V   {s['dcl']:.1f} A", "dvl"))
    else:
        L.append(empty_row())
        L.append(empty_row())

    if "cell_vmin" in s:
        delta = s["cell_vmax"] - s["cell_vmin"]
        L.append(row_wide("Cell V",
                           f"{s['cell_vmin']}-{s['cell_vmax']} mV  (d={delta} mV)",
                           "cell_vmax"))
    else:
        L.append(empty_row())

    if "cell_tmin" in s and "cell_tmax" in s:
        ct = f"{s['cell_tmin']:.1f}-{s['cell_tmax']:.1f} C"
    else:
        ct = "--"
    fw = s.get("fw", "--")
    L.append(row2(slot("Cell T", ct, "cell_tmax"),
                  slot("Firmware", fw, "fw")))

    L.append(row_wide("Alarms",   s.get("alarms",   "--"), "alarms"))
    L.append(row_wide("Warnings", s.get("warnings", "--"), "warnings"))

    L.append(plain_bar())
    L.append(f"  {fps:5.1f} Hz   last frame {last_age*1000:5.0f} ms ago")
    L.append(f"  log: {log_path}")
    L.append("  Ctrl+C to stop")
    return "\n".join(L)


ANSI_HOME = "\x1b[H"
ANSI_CLEAR = "\x1b[2J"
ANSI_CLEAR_DOWN = "\x1b[J"
ANSI_HIDE_CURSOR = "\x1b[?25l"
ANSI_SHOW_CURSOR = "\x1b[?25h"


def ensure_can(channel):
    """Exit with a clear message if the SocketCAN interface isn't ready."""
    sysdir = f"/sys/class/net/{channel}"
    if not os.path.isdir(sysdir):
        sys.exit(f"CAN interface '{channel}' not found. Plug in the SH-C31G "
                 f"and bring the bus up (see can_up.sh), or use -bestgo_dummy.")
    try:
        with open(f"{sysdir}/operstate") as f:
            if f.read().strip() == "down":
                sys.exit(f"CAN interface '{channel}' is down. Run:  ./can_up.sh")
    except OSError:
        pass


if DUMMY:
    bus = None
    asc_writer = None
else:
    ensure_can(CAN_CHANNEL)
    try:
        bus = can.Bus(channel=CAN_CHANNEL, interface="socketcan")
    except Exception as e:
        sys.exit(f"Could not open CAN interface '{CAN_CHANNEL}': {e}")
    asc_writer = can.ASCWriter(log_path)

stop = False
def _handle_sigint(signum, frame):
    global stop
    stop = True
signal.signal(signal.SIGINT, _handle_sigint)

rate_window = deque(maxlen=100)
last_frame_mono = 0.0
last_redraw = 0.0
REDRAW_PERIOD = 0.1  # 10 Hz

deadline = (time.monotonic() + duration_sec) if duration_sec else None

sys.stdout.write(ANSI_CLEAR + ANSI_HOME + ANSI_HIDE_CURSOR)
sys.stdout.flush()

try:
    while not stop:
        now_mono = time.monotonic()
        if deadline and now_mono >= deadline:
            break

        if DUMMY:
            frame = dummy_read()
            got = frame is not None
            if not got:
                time.sleep(0.02)
        else:
            msg = bus.recv(timeout=0.05)  # short timeout so we redraw smoothly
            got = msg is not None

        if got:
            if DUMMY:
                arb, data = frame
            else:
                arb = msg.arbitration_id
                data = bytes(msg.data)
                asc_writer.on_message_received(msg)
            try:
                if arb == ID_LIMITS and len(data) >= 8:
                    update_state(parse_limits(data))
                elif arb == ID_SOC and len(data) >= 6:
                    update_state(parse_soc(data))
                elif arb == ID_MEAS and len(data) >= 6:
                    update_state(parse_meas(data))
                elif arb == ID_ALARMS and len(data) >= 8:
                    update_state(parse_alarms(data))
                elif arb == ID_INFO and len(data) >= 6:
                    update_state(parse_info(data))
                elif arb == ID_MFR:
                    update_state({"mfr": ascii_clean(data)})
                elif arb == ID_NAME0:
                    update_state({"_name0": data})
                elif arb == ID_NAME1:
                    update_state({"_name1": data})
                elif arb == ID_CELLEXT and len(data) >= 8:
                    update_state(parse_cellext(data))
                elif arb == ID_CAPACITY and len(data) >= 2:
                    update_state({"cap_inst": u16(data, 0)})
            except Exception:
                # A malformed frame must not kill the dashboard.
                pass
            last_frame_mono = now_mono
            rate_window.append(now_mono)

        if now_mono - last_redraw >= REDRAW_PERIOD:
            if len(rate_window) >= 2:
                span = rate_window[-1] - rate_window[0]
                fps = (len(rate_window) - 1) / span if span > 0 else 0.0
            else:
                fps = 0.0
            last_age = (now_mono - last_frame_mono) if last_frame_mono else 0.0
            sys.stdout.write(ANSI_HOME + render(fps, last_age) + ANSI_CLEAR_DOWN)
            sys.stdout.flush()
            last_redraw = now_mono
finally:
    sys.stdout.write(ANSI_SHOW_CURSOR + "\n")
    sys.stdout.flush()
    print("\nshutting down...")
    try:
        if asc_writer is not None:
            asc_writer.stop()
            print(f"log saved: {log_path}")
    except Exception:
        pass
    try:
        if bus is not None:
            bus.shutdown()
    except Exception:
        pass
