"""Combined live dashboard for both solar-car CAN devices.

Shows the EZkontrol B48800 motor controller and the BESTGO battery (Lithium
Valley BMS) in one stacked dashboard.

Both devices sit on a SINGLE shared CAN bus at 500 kbps. They coexist
because their ID ranges don't overlap: the EZkontrol sends 29-bit extended
IDs (0x180117EF / 0x180217EF) and the BESTGO sends 11-bit standard IDs
(0x351..0x379). One gs_usb adapter on that bus sees, and decodes, both.

Usage:
    python monitor.py [DURATION_SEC]
    python monitor.py -ezkontrol_dummy -bestgo_dummy   (no hardware at all)
    python monitor.py -bestgo_dummy                    (EZkontrol live only)
    python monitor.py -ezkontrol_dummy                 (BESTGO live only)

DURATION_SEC = 0 / omitted means run until Ctrl+C.

A -*_dummy flag simulates that device instead of decoding it from the bus
-- use it for whichever device isn't connected yet. With both flags no
adapter is opened at all. This tool does not write ASC logs; use
bestgo_decode.py / ezkontrol_decode.py to log the bus to a file.

Talks to gs_usb directly because python-can 4.6.1 can't compute valid
timing for this adapter's 170 MHz CAN clock. Timing registers:
sync+prop+phase1+phase2 = 17 tq; brp=20 => 170e6/(20*17) = 500000 bps.
"""
import os
import sys
import time
import math
import random
import signal
from collections import deque

import usb.core
import libusb_package

_LIBUSB_BACKEND = libusb_package.get_libusb1_backend()
_orig_find = usb.core.find
def _find_with_libusb_package(*args, **kwargs):
    kwargs.setdefault("backend", _LIBUSB_BACKEND)
    return _orig_find(*args, **kwargs)
usb.core.find = _find_with_libusb_package

from gs_usb.gs_usb import GsUsb
from gs_usb.gs_usb_frame import GsUsbFrame

# --- command line -----------------------------------------------------------
EZ_DUMMY = "-ezkontrol_dummy" in sys.argv
BG_DUMMY = "-bestgo_dummy" in sys.argv
_args = [a for a in sys.argv[1:] if a not in ("-ezkontrol_dummy", "-bestgo_dummy")]
duration_sec = float(_args[0]) if _args else 0.0
if duration_sec == 0.0:
    duration_sec = None

T0 = time.monotonic()

# --- EZkontrol decode -------------------------------------------------------
EZ_MSG1 = 0x180117EF   # voltage / current / speed
EZ_MSG2 = 0x180217EF   # temps / status / errors

GEAR_NAMES = {0: "NO", 1: "R", 2: "N", 3: "D1", 4: "D2", 5: "D3", 6: "S", 7: "P"}
OP_MODE_NAMES = {0: "Normal", 2: "Cruise", 3: "EBS", 4: "Hold"}
ERRORS_A = ["Overcurrent", "Overload", "Overvolt", "Undervolt",
            "CtrlOT", "MotorOT", "Stalled", "OutOfPhase"]
ERRORS_B = ["MotorSens", "MotorAUX", "EncMis", "AntiRunaway",
            "MainAccel", "AuxAccel", "PreCharge", "DCCont"]
ERRORS_C = ["PowerValve", "CurrSens", "AutoTune", "RS485", "CAN", "Software"]


def ez_parse_msg1(data):
    return {
        "v":      int.from_bytes(data[0:2], "little") * 0.1,
        "ibus":   int.from_bytes(data[2:4], "little") * 0.1 - 3200,
        "iphase": int.from_bytes(data[4:6], "little") * 0.1 - 3200,
        "rpm":    int.from_bytes(data[6:8], "little") - 32000,
    }


def ez_parse_msg2(data):
    sb = data[3]
    errs = []
    for bit, name in enumerate(ERRORS_A):
        if data[4] & (1 << bit): errs.append(name)
    for bit, name in enumerate(ERRORS_B):
        if data[5] & (1 << bit): errs.append(name)
    for bit, name in enumerate(ERRORS_C):
        if data[6] & (1 << bit): errs.append(name)
    return {
        "tctrl":     data[0] - 40,
        "tmot":      data[1] - 40,
        "accel":     data[2],
        "gear":      GEAR_NAMES.get(sb & 0x07, str(sb & 0x07)),
        "brake":     "ON" if (sb >> 3) & 1 else "off",
        "mode":      OP_MODE_NAMES.get((sb >> 4) & 7, f"?({(sb>>4)&7})"),
        "contactor": "ON" if (sb >> 7) & 1 else "off",
        "errors":    ",".join(errs) if errs else "OK",
        "life":      data[7] >> 4,
    }


def ez_handle(panel, arb, data):
    """Decode an EZkontrol frame. Returns True if the ID belongs to it."""
    if arb == EZ_MSG1:
        if len(data) >= 8:
            panel.update(ez_parse_msg1(data))
        return True
    if arb == EZ_MSG2:
        if len(data) >= 8:
            panel.update(ez_parse_msg2(data), no_highlight={"life"})
        return True
    return False


def ez_dummy_frames():
    """One simulated MCU broadcast cycle: (0x180117EF, 0x180217EF)."""
    t = time.monotonic() - T0
    rpm = max(0, int(1500 + 1350 * math.sin(t / 7.0)))
    throttle = min(100, max(0, int(rpm / 30)))
    ibus = round(2.0 + throttle * 0.85 + random.uniform(-1.5, 1.5), 1)
    iphase = round(ibus * 1.6, 1)
    v = round(72.0 - ibus * 0.03, 1)
    tctrl = int(32 + throttle * 0.12)
    tmot = int(38 + throttle * 0.18)
    brake = 1 if math.sin(t / 4.0) > 0.8 else 0
    sb = (4 & 7) | (brake << 3) | (1 << 7)        # gear D2, contactor ON
    life = int(t * 10) & 0x0F

    m1 = bytearray(8)
    m1[0:2] = int(round(v / 0.1)).to_bytes(2, "little")
    m1[2:4] = int(round((ibus + 3200) / 0.1)).to_bytes(2, "little")
    m1[4:6] = int(round((iphase + 3200) / 0.1)).to_bytes(2, "little")
    m1[6:8] = (rpm + 32000).to_bytes(2, "little")

    m2 = bytearray(8)
    m2[0] = tctrl + 40
    m2[1] = tmot + 40
    m2[2] = throttle
    m2[3] = sb
    m2[7] = (life & 0x0F) << 4
    return [(EZ_MSG1, bytes(m1)), (EZ_MSG2, bytes(m2))]


# --- BESTGO decode ----------------------------------------------------------
BG_LIMITS, BG_SOC, BG_MEAS, BG_ALARMS = 0x351, 0x355, 0x356, 0x35A
BG_MFR, BG_INFO, BG_NAME0, BG_NAME1 = 0x35E, 0x35F, 0x370, 0x371
BG_CELLEXT, BG_CAPACITY = 0x373, 0x379
BG_IDS = {BG_LIMITS, BG_SOC, BG_MEAS, BG_ALARMS, BG_MFR, BG_INFO,
          BG_NAME0, BG_NAME1, BG_CELLEXT, BG_CAPACITY}
KELVIN = 273.15


def u16(data, off):
    return int.from_bytes(data[off:off + 2], "little")


def s16(data, off):
    return int.from_bytes(data[off:off + 2], "little", signed=True)


def ascii_clean(raw):
    out = []
    for b in raw:
        if b == 0:
            break
        out.append(chr(b) if 32 <= b < 127 else ".")
    return "".join(out).strip()


def bg_handle(panel, arb, data):
    """Decode a BESTGO frame. Returns True if the ID belongs to it."""
    if arb not in BG_IDS:
        return False
    try:
        if arb == BG_LIMITS and len(data) >= 8:
            panel.update({"cvl": u16(data, 0) * 0.1, "ccl": s16(data, 2) * 0.1,
                          "dcl": s16(data, 4) * 0.1, "dvl": u16(data, 6) * 0.1})
        elif arb == BG_SOC and len(data) >= 6:
            panel.update({"soc": u16(data, 0), "soh": u16(data, 2),
                          "soc_hi": u16(data, 4) * 0.01}, no_highlight={"soc_hi"})
        elif arb == BG_MEAS and len(data) >= 6:
            panel.update({"pack_v": s16(data, 0) * 0.01,
                          "pack_i": s16(data, 2) * 0.1,
                          "pack_t": s16(data, 4) * 0.1})
        elif arb == BG_ALARMS and len(data) >= 8:
            alarm, warn = data[0:4], data[4:8]
            panel.update({"alarms": "OK" if not any(alarm) else alarm.hex(),
                          "warnings": "OK" if not any(warn) else warn.hex()})
        elif arb == BG_INFO and len(data) >= 6:
            ver = u16(data, 2)
            panel.update({"fw": f"v{ver >> 8}.{ver & 0xFF}",
                          "cap_nom": u16(data, 4)})
        elif arb == BG_MFR:
            panel.update({"mfr": ascii_clean(data)})
        elif arb == BG_NAME0:
            panel.update({"_name0": data})
        elif arb == BG_NAME1:
            panel.update({"_name1": data})
        elif arb == BG_CELLEXT and len(data) >= 8:
            f = {"cell_vmin": u16(data, 0), "cell_vmax": u16(data, 2)}
            tmin, tmax = u16(data, 4), u16(data, 6)
            if tmin:
                f["cell_tmin"] = tmin - KELVIN
            if tmax:
                f["cell_tmax"] = tmax - KELVIN
            panel.update(f)
        elif arb == BG_CAPACITY and len(data) >= 2:
            panel.update({"cap_inst": u16(data, 0)})
    except Exception:
        pass  # a malformed frame must not kill the dashboard
    return True


def _le(value, width, signed):
    return int(round(value)).to_bytes(width, "little", signed=signed)


def bg_dummy_frames():
    """One simulated BMS broadcast cycle (the full SMA/Pylontech set)."""
    t = time.monotonic() - T0
    soc = int(55 + 25 * math.sin(t / 30.0))
    pack_i = round(45 * math.sin(t / 18.0), 1)
    pack_v = round(51.2 + pack_i * 0.012, 2)
    pack_t = round(24.0 + 4 * math.sin(t / 40.0), 1)
    cell_avg = pack_v / 16 * 1000
    vmin = int(cell_avg - random.randint(3, 9))
    vmax = int(cell_avg + random.randint(3, 9))
    tmin, tmax = pack_t - 1.0, pack_t + 1.5
    return [
        (BG_LIMITS,   _le(576, 2, False) + _le(1500, 2, True)
                      + _le(2000, 2, True) + _le(448, 2, False)),
        (BG_SOC,      _le(soc, 2, False) + _le(100, 2, False)
                      + _le(soc * 100, 2, False) + b"\x00\x00"),
        (BG_MEAS,     _le(pack_v / 0.01, 2, True) + _le(pack_i / 0.1, 2, True)
                      + _le(pack_t / 0.1, 2, True) + b"\x00\x00"),
        (BG_ALARMS,   bytes(8)),
        (BG_MFR,      b"LVaiiey\x00"),
        (BG_INFO,     _le(0, 2, False) + bytes([0x01, 0x01])
                      + _le(56, 2, False) + b"\x00\x00"),
        (BG_NAME0,    b"Lithium\x00"),
        (BG_NAME1,    b"Valley\x00\x00"),
        (BG_CELLEXT,  _le(vmin, 2, False) + _le(vmax, 2, False)
                      + _le(tmin + KELVIN, 2, False) + _le(tmax + KELVIN, 2, False)),
        (BG_CAPACITY, _le(56, 2, False) + bytes(6)),
    ]


# --- shared dashboard geometry ----------------------------------------------
TOTAL_W = 58
CONTENT_W = TOTAL_W - 4    # = 54
BORDER_W = TOTAL_W - 2     # = 56
SLOT_W = (CONTENT_W - 2) // 2   # = 26
HIGHLIGHT_SEC = 0.5


class Panel:
    """Decoded field store with per-field change highlighting."""

    def __init__(self):
        self.state = {}
        self.last_change = {}

    def update(self, fields, no_highlight=()):
        now = time.monotonic()
        for name, value in fields.items():
            old = self.state.get(name)
            self.state[name] = value
            if name not in no_highlight and old is not None and old != value:
                self.last_change[name] = now

    def fresh(self, name):
        return (time.monotonic() - self.last_change.get(name, 0)) < HIGHLIGHT_SEC


def slot(panel, label, value, name):
    mark = "*" if panel.fresh(name) else " "
    return f"{label:<12}{value:>{SLOT_W - 14}} {mark}"


def row2(slot_a, slot_b):
    return f"| {slot_a}  {slot_b} |"


def row_wide(panel, label, value, name):
    mark = "*" if panel.fresh(name) else " "
    inner = f"{label:<12}{value} {mark}"
    return f"| {inner:<{CONTENT_W}} |"


def text_row(text):
    return f"| {text:<{CONTENT_W}} |"


def empty_row():
    return "| " + " " * CONTENT_W + " |"


def title_bar(title):
    core = f" {title} "
    pad = BORDER_W - 2 - len(core)
    return "+--" + core + "-" * pad + "+"


def plain_bar():
    return "+" + "-" * BORDER_W + "+"


# --- panel renderers --------------------------------------------------------
def render_ez(ch):
    s, p = ch.panel.state, ch.panel
    tag = "   [DUMMY]" if ch.dummy else ""
    L = [title_bar(f"EZkontrol B48800   {ch.bitrate // 1000} kbps{tag}")]

    if "v" in s:
        L.append(row2(slot(p, "Battery", f"{s['v']:.1f} V",     "v"),
                      slot(p, "Bus I",   f"{s['ibus']:+.1f} A",  "ibus")))
        L.append(row2(slot(p, "Phase I", f"{s['iphase']:+.1f} A", "iphase"),
                      slot(p, "Speed",   f"{s['rpm']:+d} rpm",   "rpm")))
    else:
        L.append(text_row("(waiting for MCU frames 0x180117EF/0x180217EF...)"))
        L.append(empty_row())

    if "tctrl" in s:
        L.append(row2(slot(p, "Ctrl temp", f"{s['tctrl']:+d} C", "tctrl"),
                      slot(p, "Motor T",   f"{s['tmot']:+d} C",  "tmot")))
        L.append(row2(slot(p, "Accel",     f"{s['accel']} %",    "accel"),
                      slot(p, "Gear",      s['gear'],            "gear")))
        L.append(row2(slot(p, "Brake",     s['brake'],           "brake"),
                      slot(p, "Mode",      s['mode'],            "mode")))
        L.append(row2(slot(p, "Contactor", s['contactor'],       "contactor"),
                      slot(p, "Life",      f"0x{s['life']:X}",   "life")))
        err = s["errors"]
        err_max = CONTENT_W - 12 - 2
        if len(err) > err_max:
            err = err[:err_max - 3] + "..."
        L.append(row_wide(p, "Errors", err, "errors"))
    else:
        for _ in range(5):
            L.append(empty_row())

    L.append(plain_bar())
    return L


def battery_name(panel):
    name = ascii_clean(panel.state.get("_name0", b"") + panel.state.get("_name1", b""))
    return name or "?"


def render_bg(ch):
    s, p = ch.panel.state, ch.panel
    tag = "   [DUMMY]" if ch.dummy else ""
    L = [title_bar(f"BESTGO Battery   {ch.bitrate // 1000} kbps{tag}")]

    if "_name0" in s or "_name1" in s or "mfr" in s:
        ident = battery_name(p)
        if s.get("mfr"):
            ident = f"{ident}  ({s['mfr']})"
        L.append(row_wide(p, "Battery", ident, "_name0"))
    else:
        L.append(text_row("(waiting for BMS frames 0x351/0x355/0x356...)"))

    if "soc" in s:
        L.append(row2(slot(p, "SOC", f"{s['soc']} %", "soc"),
                      slot(p, "SOH", f"{s['soh']} %", "soh")))
    else:
        L.append(empty_row())

    if "pack_v" in s:
        L.append(row2(slot(p, "Pack V", f"{s['pack_v']:.2f} V",  "pack_v"),
                      slot(p, "Pack I", f"{s['pack_i']:+.1f} A", "pack_i")))
        cap = f"{s['cap_nom']} Ah" if "cap_nom" in s else "--"
        L.append(row2(slot(p, "Pack T", f"{s['pack_t']:.1f} C", "pack_t"),
                      slot(p, "Capacity", cap,                  "cap_nom")))
    else:
        L.append(empty_row())
        L.append(empty_row())

    if "cvl" in s:
        L.append(row_wide(p, "Charge lim", f"{s['cvl']:.1f} V   {s['ccl']:.1f} A", "cvl"))
        L.append(row_wide(p, "Dischg lim", f"{s['dvl']:.1f} V   {s['dcl']:.1f} A", "dvl"))
    else:
        L.append(empty_row())
        L.append(empty_row())

    if "cell_vmin" in s:
        delta = s["cell_vmax"] - s["cell_vmin"]
        L.append(row_wide(p, "Cell V",
                          f"{s['cell_vmin']}-{s['cell_vmax']} mV  (d={delta} mV)",
                          "cell_vmax"))
    else:
        L.append(empty_row())

    if "cell_tmin" in s and "cell_tmax" in s:
        ct = f"{s['cell_tmin']:.1f}-{s['cell_tmax']:.1f} C"
    else:
        ct = "--"
    L.append(row2(slot(p, "Cell T", ct, "cell_tmax"),
                  slot(p, "Firmware", s.get("fw", "--"), "fw")))

    L.append(row_wide(p, "Alarms",   s.get("alarms",   "--"), "alarms"))
    L.append(row_wide(p, "Warnings", s.get("warnings", "--"), "warnings"))
    L.append(plain_bar())
    return L


# --- channels ---------------------------------------------------------------
class Channel:
    """One device on the shared bus: a dashboard panel plus its decoder."""

    def __init__(self, name, bitrate, dummy, gen, handler, dummy_period):
        self.name = name
        self.bitrate = bitrate
        self.dummy = dummy
        self.gen = gen
        self.handler = handler
        self.dummy_period = dummy_period
        self.dummy_next = 0.0
        self.dummy_queue = deque()
        self.next_release = 0.0
        self.frame_gap = 0.0
        self.panel = Panel()
        self.rate_window = deque()   # frame monotonic timestamps, last ~1 s
        self.last_frame = 0.0

    def mark(self, now):
        self.rate_window.append(now)
        cutoff = now - 1.0
        while self.rate_window and self.rate_window[0] < cutoff:
            self.rate_window.popleft()
        self.last_frame = now

    def fps(self):
        return len(self.rate_window)


ez = Channel("EZkontrol", 500_000, EZ_DUMMY, ez_dummy_frames, ez_handle, 0.1)
bg = Channel("BESTGO", 500_000, BG_DUMMY, bg_dummy_frames, bg_handle, 1.0)
channels = [ez, bg]


def render_screen(now):
    L = render_ez(ez) + [""] + render_bg(bg)

    def stat(ch):
        age = (now - ch.last_frame) * 1000 if ch.last_frame else 0.0
        return f"{ch.fps():3d} Hz  last {age:5.0f} ms"

    L.append(f"  EZkontrol: {stat(ez)}      BESTGO: {stat(bg)}")
    L.append("  Ctrl+C to stop")
    return "\n".join(L)


# --- open the shared-bus adapter (unless both devices are simulated) --------
BUS_BRP = 20   # 170 MHz / (20 * 17 tq) = 500000 bps
dev = None
fr = GsUsbFrame()
if not (EZ_DUMMY and BG_DUMMY):
    devs = GsUsb.scan()
    if not devs:
        print("No gs_usb adapter found. Both devices share one 500 kbps bus;")
        print("connect the SH-C31G to that bus, or run with both")
        print("-ezkontrol_dummy and -bestgo_dummy to simulate them.")
        sys.exit(1)
    dev = devs[0]
    dev.set_timing(prop_seg=1, phase_seg1=13, phase_seg2=2, sjw=2, brp=BUS_BRP)
    dev.start()
    decoded = " + ".join(ch.name for ch in channels if not ch.dummy)
    print(f"listening on the shared 500 kbps bus, decoding: {decoded}")
    time.sleep(0.8)   # let the user read the line before the screen clears

# --- run --------------------------------------------------------------------
if os.name == "nt":
    os.system("")
ANSI_HOME = "\x1b[H"
ANSI_CLEAR = "\x1b[2J"
ANSI_CLEAR_DOWN = "\x1b[J"
ANSI_HIDE_CURSOR = "\x1b[?25l"
ANSI_SHOW_CURSOR = "\x1b[?25h"

stop = False
def _handle_sigint(signum, frame):
    global stop
    stop = True
signal.signal(signal.SIGINT, _handle_sigint)

REDRAW_PERIOD = 0.1
READ_BUDGET = 60        # max frames to drain from the bus per loop
last_redraw = 0.0
deadline = (time.monotonic() + duration_sec) if duration_sec else None

sys.stdout.write(ANSI_CLEAR + ANSI_HOME + ANSI_HIDE_CURSOR)
sys.stdout.flush()

try:
    while not stop:
        now = time.monotonic()
        if deadline and now >= deadline:
            break

        # Simulated devices: release their dummy frames.
        for ch in channels:
            if not ch.dummy:
                continue
            # Refill a cycle when the queue drains, then release its frames
            # evenly across dummy_period (so the rate readout is steady,
            # like a real bus interleaving its frames).
            if not ch.dummy_queue and now >= ch.dummy_next:
                cycle = ch.gen()
                ch.dummy_queue.extend(cycle)
                ch.frame_gap = ch.dummy_period / max(1, len(cycle))
                ch.dummy_next = now + ch.dummy_period
                ch.next_release = now
            if ch.dummy_queue and now >= ch.next_release:
                arb, data = ch.dummy_queue.popleft()
                ch.handler(ch.panel, arb, data)
                ch.mark(now)
                ch.next_release = now + ch.frame_gap

        # Real shared bus: read frames, route each by ID to its panel.
        if dev is not None:
            for _ in range(READ_BUDGET):
                if not dev.read(fr, 2):
                    break
                arb = fr.arbitration_id
                data = bytes(fr.data[:fr.can_dlc])
                tnow = time.monotonic()
                for ch in channels:
                    if not ch.dummy and ch.handler(ch.panel, arb, data):
                        ch.mark(tnow)
                        break

        if now - last_redraw >= REDRAW_PERIOD:
            sys.stdout.write(ANSI_HOME + render_screen(now) + ANSI_CLEAR_DOWN)
            sys.stdout.flush()
            last_redraw = now

        time.sleep(0.01)
finally:
    sys.stdout.write(ANSI_SHOW_CURSOR + "\n")
    sys.stdout.flush()
    print("\nshutting down...")
    if dev is not None:
        try:
            dev.stop()
        except Exception:
            pass
