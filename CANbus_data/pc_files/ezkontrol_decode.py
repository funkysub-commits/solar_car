"""Live dashboard + ASC logger for EZkontrol B48800 MCU CAN messages.

Usage:
    python ezkontrol_decode.py [DURATION_SEC] [LOG_PATH]
    python ezkontrol_decode.py -250                (force 250 kbps)
    python ezkontrol_decode.py -ezkontrol_dummy

DURATION_SEC = 0 means run until Ctrl+C. LOG_PATH defaults to
logs/decode-<timestamp>.asc and is in Vector ASC format.

The bus rate defaults to 500 kbps; pass -250 for 250 kbps (the rate in
the EZkontrol MCU-to-Meter spec). Pass -ezkontrol_dummy to drive the
dashboard from simulated MCU data when the controller (or the SH-C31G
adapter) isn't connected -- no device is opened and no log is written.

Talks to gs_usb directly because python-can 4.6.1's BitTiming.from_sample_point
cannot find a valid (BRP, TSEG) at this adapter's reported 170 MHz CAN
clock. We set the bit-timing registers explicitly:

    sync=1, prop=1, phase1=13, phase2=2, sjw=2  => 17 tq, 88.2% sample point
    brp=20 => 170e6 / (20 * 17) = 500000 bps exactly
    brp=40 => 170e6 / (40 * 17) = 250000 bps exactly
"""
import os
import sys
import time
import math
import random
import signal
from collections import deque
from datetime import datetime

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
import can

DUMMY = "-ezkontrol_dummy" in sys.argv
BITRATE = 250_000 if "-250" in sys.argv else 500_000
BRP = 40 if BITRATE == 250_000 else 20
RATE_LABEL = f"{BITRATE // 1000} kbps"
sys.argv = [sys.argv[0]] + [a for a in sys.argv[1:]
                            if a not in ("-ezkontrol_dummy", "-250", "-500")]

duration_sec = float(sys.argv[1]) if len(sys.argv) > 1 else 0.0
if duration_sec == 0.0:
    duration_sec = None

if DUMMY:
    log_path = "(dummy mode - logging disabled)"
elif len(sys.argv) > 2:
    log_path = sys.argv[2]
else:
    os.makedirs("logs", exist_ok=True)
    log_path = f"logs/decode-{datetime.now():%Y%m%d-%H%M%S}.asc"

GEAR_NAMES = {0: "NO", 1: "R", 2: "N", 3: "D1", 4: "D2", 5: "D3", 6: "S", 7: "P"}
OP_MODE_NAMES = {0: "Normal", 2: "Cruise", 3: "EBS", 4: "Hold"}
ERRORS_A = ["Overcurrent", "Overload", "Overvolt", "Undervolt",
            "CtrlOT", "MotorOT", "Stalled", "OutOfPhase"]
ERRORS_B = ["MotorSens", "MotorAUX", "EncMis", "AntiRunaway",
            "MainAccel", "AuxAccel", "PreCharge", "DCCont"]
ERRORS_C = ["PowerValve", "CurrSens", "AutoTune", "RS485", "CAN", "Software"]


def parse_msg_i(data):
    return {
        "v":      int.from_bytes(data[0:2], "little") * 0.1,
        "ibus":   int.from_bytes(data[2:4], "little") * 0.1 - 3200,
        "iphase": int.from_bytes(data[4:6], "little") * 0.1 - 3200,
        "rpm":    int.from_bytes(data[6:8], "little") - 32000,
    }


def parse_msg_ii(data):
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


# --- Dummy-data generator (for -ezkontrol_dummy, no hardware needed) --------
_dummy_t0 = time.monotonic()
_dummy_queue = deque()
_dummy_next = 0.0
DUMMY_PERIOD = 0.1   # one simulated MCU broadcast cycle every 100 ms (as real)


def dummy_frames():
    """Build one simulated MCU broadcast cycle: (0x180117EF, 0x180217EF)."""
    t = time.monotonic() - _dummy_t0
    rpm = max(0, int(1500 + 1350 * math.sin(t / 7.0)))
    throttle = min(100, max(0, int(rpm / 30)))
    ibus = round(2.0 + throttle * 0.85 + random.uniform(-1.5, 1.5), 1)
    iphase = round(ibus * 1.6, 1)
    v = round(72.0 - ibus * 0.03, 1)
    tctrl = int(32 + throttle * 0.12)
    tmot = int(38 + throttle * 0.18)
    brake = 1 if math.sin(t / 4.0) > 0.8 else 0
    gear = 4                       # D2
    contactor = 1
    sb = (gear & 7) | (brake << 3) | (contactor << 7)
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
    return [(0x180117EF, bytes(m1)), (0x180217EF, bytes(m2))]


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
# Fields where we never bother highlighting (intentionally always-changing)
NO_HIGHLIGHT = {"life"}


def update_state(fields):
    now = time.monotonic()
    for name, new_value in fields.items():
        old = state.get(name)
        state[name] = new_value
        if name not in NO_HIGHLIGHT and old is not None and old != new_value:
            last_change[name] = now


def is_fresh(name):
    return (time.monotonic() - last_change.get(name, 0)) < HIGHLIGHT_SEC


TOTAL_W = 50              # full row width including the | borders
CONTENT_W = TOTAL_W - 4   # area between '| ' and ' |'  (= 46)
BORDER_W = TOTAL_W - 2    # dashes between '+' and '+'  (= 48)
SLOT_W = (CONTENT_W - 2) // 2  # two slots with 2-space separator (= 22)


def slot(label, value, name):
    mark = "*" if is_fresh(name) else " "
    # label(10) + value(right-aligned to SLOT_W-12) + ' ' + mark = SLOT_W
    return f"{label:<10}{value:>{SLOT_W - 12}} {mark}"


def row2(slot_a, slot_b):
    return f"| {slot_a}  {slot_b} |"


def row_wide(label, value, name):
    mark = "*" if is_fresh(name) else " "
    inner = f"{label:<10}{value} {mark}"
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
    L = [title_bar(f"EZkontrol B48800   {RATE_LABEL}"
                   + ("   [DUMMY]" if DUMMY else ""))]

    if "v" in s:
        L.append(row2(slot("Battery",  f"{s['v']:.1f} V",       "v"),
                      slot("Bus I",    f"{s['ibus']:+.1f} A",   "ibus")))
        L.append(row2(slot("Phase I",  f"{s['iphase']:+.1f} A", "iphase"),
                      slot("Speed",    f"{s['rpm']:+d} rpm",    "rpm")))
    else:
        L.append(row_wide("(waiting for 0x180117EF...)", "", "_w"))
        L.append(empty_row())

    if "tctrl" in s:
        L.append(row2(slot("Ctrl temp", f"{s['tctrl']:+d} C",   "tctrl"),
                      slot("Motor T",   f"{s['tmot']:+d} C",    "tmot")))
        L.append(row2(slot("Accel",     f"{s['accel']} %",      "accel"),
                      slot("Gear",      s['gear'],              "gear")))
        L.append(row2(slot("Brake",     s['brake'],             "brake"),
                      slot("Mode",      s['mode'],              "mode")))
        L.append(row2(slot("Contactor", s['contactor'],         "contactor"),
                      slot("Life",      f"0x{s['life']:X}",     "life")))
        err = s["errors"]
        err_max = CONTENT_W - 10 - 2   # label width + space + mark
        if len(err) > err_max:
            err = err[:err_max - 3] + "..."
        L.append(row_wide("Errors", err, "errors"))
    else:
        for _ in range(5):
            L.append(empty_row())

    L.append(plain_bar())
    L.append(f"  {fps:5.1f} Hz   last frame {last_age*1000:5.0f} ms ago")
    L.append(f"  log: {log_path}")
    L.append("  Ctrl+C to stop")
    return "\n".join(L)


# Enable VT (ANSI) on Windows consoles
if os.name == "nt":
    os.system("")
ANSI_HOME = "\x1b[H"
ANSI_CLEAR = "\x1b[2J"
ANSI_CLEAR_DOWN = "\x1b[J"
ANSI_HIDE_CURSOR = "\x1b[?25l"
ANSI_SHOW_CURSOR = "\x1b[?25h"


if DUMMY:
    dev = None
    asc_writer = None
else:
    devs = GsUsb.scan()
    if not devs:
        print("No gs_usb device found. "
              "(Use -ezkontrol_dummy to run without hardware.)")
        sys.exit(1)
    dev = devs[0]
    dev.set_timing(prop_seg=1, phase_seg1=13, phase_seg2=2, sjw=2, brp=BRP)
    dev.start()
    asc_writer = can.ASCWriter(log_path)

stop = False
def _handle_sigint(signum, frame):
    global stop
    stop = True
signal.signal(signal.SIGINT, _handle_sigint)

# Frame rate sliding window: timestamps of last N frames
rate_window = deque(maxlen=100)
last_frame_mono = 0.0
last_redraw = 0.0
REDRAW_PERIOD = 0.1  # 10 Hz

deadline = (time.monotonic() + duration_sec) if duration_sec else None
fr = GsUsbFrame()

# Initial paint
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
            got = dev.read(fr, 50)  # short timeout so we can redraw smoothly

        if got:
            if DUMMY:
                arb, data = frame
            else:
                arb = fr.arbitration_id
                data = bytes(fr.data[:fr.can_dlc])
                asc_writer.on_message_received(can.Message(
                    timestamp=time.time(),
                    arbitration_id=arb,
                    is_extended_id=fr.is_extended_id,
                    dlc=fr.can_dlc,
                    data=data,
                ))
            if arb == 0x180117EF:
                update_state(parse_msg_i(data))
            elif arb == 0x180217EF:
                update_state(parse_msg_ii(data))
            else:
                update_state({"unknown_id": f"0x{arb:08X}", "unknown_data": data.hex()})
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
        if dev is not None:
            dev.stop()
    except Exception:
        pass
