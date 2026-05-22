#!/usr/bin/env python3
"""
Solar Car e-ink dashboard - Waveshare 7.5" V2 (800x480, 1-bit).

Layout
  Header        : team logo + title + clock
  Left-top      : analog speedometer gauge (mph / km/h / rpm)
  Left-bottom   : messages area (temperature warnings + messages from HA)
  Right-top     : battery icon (state of charge) + pack voltage
  Right-bottom  : four vertical temperature bar graphs (motor / EZkontrol / battery / Pi)

Refresh strategy & panel longevity
  E-ink wears a little with every refresh, and Waveshare explicitly warns the
  panel must NOT be left powered/active during long idle periods. This driver:
    * updates only when a value actually changes - a parked car with steady
      readings produces no refreshes at all;
    * refreshes just the screen region that changed (partial refresh) - gentle
      and flash-free - so untouched panels never ghost;
    * does an occasional fast full refresh (every FULL_REFRESH_EVERY partial
      pushes) only to clear the ghosting that partial refresh leaves behind;
    * after IDLE_SLEEP seconds with no telemetry change it settles the image
      with one clean full refresh and puts the panel into deep sleep - the
      image stays visible with zero power draw and zero wear, and the panel
      wakes automatically on the next change.
  The speedometer is sampled every SPEED_POLL seconds, temps/SoC/messages
  every SLOW_POLL seconds.

Configuration
  All settings come from environment variables. When run as a Home Assistant
  add-on, run.sh fills them in from the add-on options (Settings > Add-ons >
  Solar Car E-Ink Display > Configuration) and points HA_URL / HA_TOKEN at the
  Supervisor proxy (no long-lived token needed).
"""
import os
import sys
import math
import time
import signal
import logging
from datetime import datetime

import requests

sys.path.append('/e-Paper/RaspberryPi_JetsonNano/python/lib')
from waveshare_epd import epd7in5_V2
from PIL import Image, ImageDraw, ImageFont

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

# --------------------------------------------------------------------------
# Configuration (all overridable via environment variables / add-on options)
# --------------------------------------------------------------------------
HA_URL = os.environ.get("HA_URL", "http://192.168.0.243:8123").rstrip("/")
HA_TOKEN = os.environ.get("HA_TOKEN", "")
TITLE = os.environ.get("TITLE", "SOLAR STORMS")

SPEED_POLL = float(os.environ.get("SPEED_POLL", "2.5"))      # seconds between speedometer updates
SLOW_POLL = float(os.environ.get("SLOW_POLL", "6"))          # seconds between temp/SoC/message updates
FULL_REFRESH_EVERY = int(os.environ.get("FULL_REFRESH_EVERY", "90"))  # partial pushes between de-ghost full refreshes
IDLE_SLEEP = float(os.environ.get("IDLE_SLEEP", "180"))      # seconds of no change before the panel deep-sleeps

# Speed: the source entity reports motor rpm; the speedometer can show that
# raw rpm, or convert to mph / km/h using the drive wheel size and gear ratio.
SPEED_UNIT = os.environ.get("SPEED_UNIT", "mph").strip().lower()
WHEEL_DIAMETER_IN = float(os.environ.get("WHEEL_DIAMETER_IN", "20"))   # drive wheel diameter, inches
GEAR_RATIO = float(os.environ.get("GEAR_RATIO", "1") or "1")           # motor revs per wheel rev
SPEED_MAX = float(os.environ.get("SPEED_MAX", "40"))         # speedometer full-scale, in SPEED_UNIT
TEMP_MAX = float(os.environ.get("TEMP_MAX", "80"))           # temperature bar full-scale, degrees C
TEMP_WARN = float(os.environ.get("TEMP_WARN", "65"))         # temperature warning threshold, degrees C

SPEED_LABEL = {"rpm": "rpm", "mph": "mph", "kmh": "km/h", "km/h": "km/h"}.get(SPEED_UNIT, SPEED_UNIT)

LOGO_PATH = os.environ.get("LOGO_PATH", "/logo.png")

# Source entities. Defaults are the real integration entities - the EZkontrol
# CAN reader (ezkontrol_*) and the bestgo BLE BMS (bestgo_*) - which carry
# dummy values until the hardware is connected, then real data.
ENTITIES = {
    "speed":   os.environ.get("ENT_SPEED",   "sensor.ezkontrol_motor_speed"),
    "t_motor": os.environ.get("ENT_T_MOTOR", "sensor.ezkontrol_motor_temp"),
    "t_ezk":   os.environ.get("ENT_T_EZK",   "sensor.ezkontrol_controller_temp"),
    "t_batt":  os.environ.get("ENT_T_BATT",  "sensor.bestgo_pack_temp"),
    "t_pi":    os.environ.get("ENT_T_PI",    "sensor.system_monitor_processor_temperature"),
    "soc":     os.environ.get("ENT_SOC",     "sensor.bestgo_soc"),
    "voltage": os.environ.get("ENT_VOLTAGE", "sensor.bestgo_pack_voltage"),
    "message": os.environ.get("ENT_MESSAGE", "input_text.eink_message"),
}
REFRESH_BUTTON = "input_button.eink_refresh"
POWER_TOGGLE = os.environ.get("ENT_POWER", "input_boolean.eink_display")

HEADERS = {"Authorization": f"Bearer {HA_TOKEN}"}

# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------
W, H = 800, 480
HEAD_H = 48
DIV_X = 452          # vertical divider between left column and right column
MSG_DIV_Y = 318      # left column: speedometer above, messages below
BAT_DIV_Y = 262      # right column: battery above, temperatures below

# Independent partial-refresh regions, (x0, y0, x1, y1).
# x coordinates MUST be multiples of 8 - the panel only refreshes byte-aligned
# columns. Regions stay clear of the frame/divider lines so those never ghost.
REGIONS = {
    "speed": (8, 50, 448, 316),
    "msg":   (8, 320, 448, 476),
    "batt":  (456, 50, 792, 260),
    "temps": (456, 264, 792, 476),
    "clock": (608, 4, 792, 46),
}
# Regions that count as real telemetry: a change here keeps the panel awake.
# The clock is redrawn alongside telemetry but never wakes the panel by itself.
DATA_REGIONS = ("speed", "msg", "batt", "temps")

FONT_DIR = "/usr/share/fonts/dejavu"


def _font(name, size):
    try:
        return ImageFont.truetype(f"{FONT_DIR}/{name}", size)
    except Exception:
        return ImageFont.load_default()


F_TITLE = _font("DejaVuSans-Bold.ttf", 28)
F_LABEL = _font("DejaVuSans-Bold.ttf", 19)
F_SPEED = _font("DejaVuSans-Bold.ttf", 58)
F_UNIT  = _font("DejaVuSans.ttf", 22)
F_SOC   = _font("DejaVuSans-Bold.ttf", 56)
F_TEMP  = _font("DejaVuSans-Bold.ttf", 26)
F_MSG   = _font("DejaVuSans.ttf", 19)
F_MSG_B = _font("DejaVuSans-Bold.ttf", 19)
F_SMALL = _font("DejaVuSans.ttf", 17)

LOGO_H = 40


def _load_logo():
    """Load the team logo as a bold 1-bit silhouette for the header - anything
    that is not near-white background becomes solid black, so it stays visible
    on the e-ink panel (a plain threshold would drop the light-coloured sun)."""
    try:
        src = Image.open(LOGO_PATH).convert("RGBA")
        bg = Image.new("RGBA", src.size, (255, 255, 255, 255))
        bg.alpha_composite(src)
        gray = bg.convert("L")
        w = max(1, round(gray.width * LOGO_H / gray.height))
        gray = gray.resize((w, LOGO_H), Image.LANCZOS)
        return gray.point(lambda p: 0 if p < 242 else 255).convert("1", dither=Image.Dither.NONE)
    except Exception as e:
        logging.warning(f"logo load failed: {e}")
        return None


LOGO = _load_logo()


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def rpm_to_speed(rpm):
    """Convert raw motor rpm into the configured speedometer unit."""
    if rpm is None:
        return None
    if SPEED_UNIT == "rpm":
        return rpm
    wheel_rpm = rpm / GEAR_RATIO if GEAR_RATIO else rpm
    inches_per_min = wheel_rpm * math.pi * WHEEL_DIAMETER_IN
    if SPEED_UNIT in ("kmh", "km/h"):
        return inches_per_min * 60.0 * 0.0254 / 1000.0      # km/h
    return inches_per_min * 60.0 / 63360.0                  # mph


def wrap_text(text, width):
    """Crude word-wrap by character count; returns at most 3 lines."""
    words = text.split()
    lines, cur = [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w) if cur else w
    if cur:
        lines.append(cur)
    return lines[:3]


# --------------------------------------------------------------------------
# Home Assistant access
# --------------------------------------------------------------------------
def ha_get(entity):
    """Return (state, attributes) for an entity, or (None, {}) on any failure."""
    try:
        r = requests.get(f"{HA_URL}/api/states/{entity}", headers=HEADERS, timeout=5)
        r.raise_for_status()
        j = r.json()
        return j.get("state"), j.get("attributes", {})
    except Exception as e:
        logging.debug(f"fetch {entity} failed: {e}")
        return None, {}


def read_number(entity):
    """Return (float value, unit) for a numeric entity, or (None, unit)."""
    state, attrs = ha_get(entity)
    unit = attrs.get("unit_of_measurement", "")
    if state in (None, "", "unknown", "unavailable"):
        return None, unit
    try:
        return float(state), unit
    except (TypeError, ValueError):
        return None, unit


def read_temp_c(entity):
    """Read a temperature entity and normalise to degrees Celsius."""
    val, unit = read_number(entity)
    if val is None:
        return None
    if unit and "F" in unit.upper():       # Pi sensor reports Fahrenheit
        val = (val - 32.0) * 5.0 / 9.0
    return val


def read_message(entity):
    """Read the free-text message entity (input_text), or '' if unset."""
    state, _ = ha_get(entity)
    if state in (None, "", "unknown", "unavailable"):
        return ""
    return str(state).strip()


# --------------------------------------------------------------------------
# Messages
# --------------------------------------------------------------------------
def hot_temps(temps):
    """Return the list of (label, value) temps at or above the warning threshold."""
    out = []
    for lbl, key in (("MOTOR", "t_motor"), ("EZK", "t_ezk"),
                      ("BATT", "t_batt"), ("PI", "t_pi")):
        v = temps.get(key)
        if v is not None and v >= TEMP_WARN:
            out.append((lbl, v))
    return out


def build_messages(temps, ha_msg):
    """Build the message lines: (text, bold). Temp warnings first, then the HA message."""
    lines = []
    hot = hot_temps(temps)
    if hot:
        lines.append(("! HIGH TEMP  " + "  ".join(f"{l} {v:.0f}°" for l, v in hot), True))
    if ha_msg:
        for chunk in wrap_text(ha_msg, 36):
            lines.append((chunk, False))
    return lines


# --------------------------------------------------------------------------
# Drawing
# --------------------------------------------------------------------------
def draw_speedometer(d, speed):
    """speed is already in display units (mph / km/h / rpm)."""
    cx, cy, r = 228, 198, 104

    d.text((cx, 58), "SPEED", font=F_LABEL, fill=0, anchor="ma")
    d.arc((cx - r, cy - r, cx + r, cy + r), 180, 360, fill=0, width=4)

    # tick marks around the arc (0..10, major every 5)
    for i in range(11):
        a = math.radians(180 + 180 * (i / 10.0))
        ca, sa = math.cos(a), math.sin(a)
        major = (i % 5 == 0)
        r2 = r - 16 if major else r - 10
        d.line((cx + r * ca, cy + r * sa, cx + r2 * ca, cy + r2 * sa),
               fill=0, width=3 if major else 2)

    # scale end labels
    d.text((cx - r, cy + 8), "0", font=F_SMALL, fill=0, anchor="ma")
    d.text((cx + r, cy + 8), f"{SPEED_MAX:.0f}", font=F_SMALL, fill=0, anchor="ma")

    # needle
    val = 0.0 if speed is None else clamp(speed, 0, SPEED_MAX)
    a = math.radians(180 + 180 * (val / SPEED_MAX))
    ca, sa = math.cos(a), math.sin(a)
    rn = r - 22
    d.line((cx, cy, cx + rn * ca, cy + rn * sa), fill=0, width=5)
    d.ellipse((cx - 9, cy - 9, cx + 9, cy + 9), fill=0)

    # large numeric readout
    num = "--" if speed is None else f"{speed:.0f}"
    d.text((cx, cy + 56), num, font=F_SPEED, fill=0, anchor="mm")
    d.text((cx, cy + 96), SPEED_LABEL, font=F_UNIT, fill=0, anchor="mm")


def draw_messages(d, lines):
    d.text((18, 330), "MESSAGES", font=F_LABEL, fill=0, anchor="la")
    y = 360
    if not lines:
        d.text((18, y), "- no messages -", font=F_MSG, fill=0, anchor="la")
        return
    for text, bold in lines[:4]:
        d.text((18, y), text, font=(F_MSG_B if bold else F_MSG), fill=0, anchor="la")
        y += 27


def draw_battery(d, soc, voltage, vunit):
    x = DIV_X + 22
    d.text((x, 60), "BATTERY", font=F_LABEL, fill=0, anchor="la")

    bx0, by0, bx1, by1 = x, 90, x + 232, 168
    d.rectangle((bx0, by0, bx1, by1), outline=0, width=3)
    nub_h = 30
    midy = (by0 + by1) // 2
    d.rectangle((bx1, midy - nub_h // 2, bx1 + 13, midy + nub_h // 2), fill=0)

    pad = 8
    if soc is not None:
        inner_w = (bx1 - bx0) - 2 * pad
        fw = int(inner_w * clamp(soc, 0, 100) / 100.0)
        if fw > 0:
            d.rectangle((bx0 + pad, by0 + pad, bx0 + pad + fw, by1 - pad), fill=0)

    soc_txt = "--" if soc is None else f"{soc:.0f}%"
    d.text((x, 222), soc_txt, font=F_SOC, fill=0, anchor="lm")
    if voltage is not None:
        d.text((W - 20, 234), f"{voltage:.1f} {vunit or 'V'}",
               font=F_SMALL, fill=0, anchor="rm")


def draw_temps(d, temps):
    d.text((DIV_X + 22, 274), "TEMPERATURES", font=F_LABEL, fill=0, anchor="la")

    items = [
        ("MOTOR", temps.get("t_motor")),
        ("EZK",   temps.get("t_ezk")),
        ("BATT",  temps.get("t_batt")),
        ("PI",    temps.get("t_pi")),
    ]
    area_x0, area_x1 = DIV_X + 10, W - 12
    slot = (area_x1 - area_x0) / len(items)
    half = 25
    base_y, top_y = 446, 330
    bar_h = base_y - top_y

    for i, (lbl, val) in enumerate(items):
        cx = area_x0 + slot * i + slot / 2
        d.rectangle((cx - half, top_y, cx + half, base_y), outline=0, width=2)
        if val is not None:
            fh = bar_h * clamp(val, 0, TEMP_MAX) / TEMP_MAX
            if fh > 0:
                d.rectangle((cx - half, base_y - fh, cx + half, base_y), fill=0)
        vtxt = "--" if val is None else f"{val:.0f}°"
        d.text((cx, top_y - 6), vtxt, font=F_TEMP, fill=0, anchor="md")
        d.text((cx, base_y + 7), lbl, font=F_SMALL, fill=0, anchor="ma")


def render(speed, temps, soc, voltage, voltage_unit, ha_msg, clock_str):
    """speed is already in display units (see rpm_to_speed)."""
    img = Image.new('1', (W, H), 255)
    d = ImageDraw.Draw(img)

    d.rectangle((1, 1, W - 2, H - 2), outline=0, width=2)
    d.line((2, HEAD_H, W - 3, HEAD_H), fill=0, width=2)
    d.line((DIV_X, HEAD_H, DIV_X, H - 3), fill=0, width=2)
    d.line((DIV_X, BAT_DIV_Y, W - 3, BAT_DIV_Y), fill=0, width=2)
    d.line((8, MSG_DIV_Y, 444, MSG_DIV_Y), fill=0, width=2)

    # header: logo + title + clock
    tx = 16
    if LOGO is not None:
        img.paste(LOGO, (14, 5))
        tx = 14 + LOGO.width + 12
    d.text((tx, 9), TITLE, font=F_TITLE, fill=0, anchor="la")
    d.text((W - 18, 9), clock_str, font=F_TITLE, fill=0, anchor="ra")

    draw_speedometer(d, speed)
    draw_messages(d, build_messages(temps, ha_msg))
    draw_battery(d, soc, voltage, voltage_unit)
    draw_temps(d, temps)
    return img


# --------------------------------------------------------------------------
# Change detection and panel updates
# --------------------------------------------------------------------------
def region_snaps(speed, temps, soc, voltage, ha_msg, clock_str):
    """Per-region coarse snapshot - a region is only refreshed when its tuple
    changes. speed is in display units."""
    return {
        "speed": None if speed is None else round(speed),
        "msg": (tuple((l, round(v)) for l, v in hot_temps(temps)), ha_msg),
        "batt": (None if soc is None else round(soc),
                 None if voltage is None else round(voltage, 1)),
        "temps": tuple(None if v is None else round(v)
                       for v in (temps.get("t_motor"), temps.get("t_ezk"),
                                 temps.get("t_batt"), temps.get("t_pi"))),
        "clock": clock_str,
    }


def region_buffer(region_img):
    """Pack a 1-bit region image into an e-paper buffer (same format as
    epd.getbuffer, which is what display_Partial expects)."""
    buf = bytearray(region_img.convert('1').tobytes('raw'))
    for i in range(len(buf)):
        buf[i] ^= 0xFF
    return list(buf)


def push_region(epd, img, name):
    """Partial-refresh just one region of the full frame onto the panel."""
    x0, y0, x1, y1 = REGIONS[name]
    epd.display_Partial(region_buffer(img.crop((x0, y0, x1, y1))), x0, y0, x1, y1)


def full_refresh(epd, img):
    """Fast full-screen refresh (~2s) that clears partial-mode ghosting, then
    return to flash-free partial mode. Also wakes the panel from deep sleep."""
    epd.init_fast()
    epd.display(epd.getbuffer(img))
    epd.init_part()


def settle_and_sleep(epd, img):
    """Clear ghosting with one clean full refresh, then deep-sleep the panel.
    The image stays visible with no power; the panel must not be left active."""
    epd.init_fast()
    epd.display(epd.getbuffer(img))
    epd.sleep()


def fmt_temps(temps):
    return {k: (None if v is None else round(v, 1)) for k, v in temps.items()}


# --------------------------------------------------------------------------
# Main loop
# --------------------------------------------------------------------------
def main():
    if not HA_TOKEN:
        logging.warning("HA_TOKEN is empty - all Home Assistant reads will fail")

    epd = epd7in5_V2.EPD()
    stop = {"flag": False}

    def on_term(*_):
        stop["flag"] = True
    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)

    logging.info(f"init + clear  (speed unit: {SPEED_LABEL})")
    epd.init()
    epd.Clear()

    speed, _ = read_number(ENTITIES["speed"])         # raw motor rpm
    temps = {k: None for k in ("t_motor", "t_ezk", "t_batt", "t_pi")}
    for k in temps:
        temps[k] = read_temp_c(ENTITIES[k])
    soc, _ = read_number(ENTITIES["soc"])
    voltage, voltage_unit = read_number(ENTITIES["voltage"])
    if not voltage_unit:
        voltage_unit = "V"
    ha_msg = read_message(ENTITIES["message"])

    disp = rpm_to_speed(speed)
    clock = datetime.now().strftime("%H:%M")
    powered = ha_get(POWER_TOGGLE)[0] != "off"   # default ON if the toggle is absent
    if powered:
        img = render(disp, temps, soc, voltage, voltage_unit, ha_msg, clock)
        full_refresh(epd, img)            # clean base frame, then partial mode
        logging.info("initial frame drawn")
    else:
        epd.sleep()                       # already cleared above; just sleep the panel
        logging.info("display starts OFF (HA toggle)")

    last_snaps = region_snaps(disp, temps, soc, voltage, ha_msg, clock)
    refresh_count = 0
    last_slow = time.time()
    last_button, _ = ha_get(REFRESH_BUTTON)
    awake = powered
    idle_since = time.time()

    try:
        while not stop["flag"]:
            t0 = time.time()

            # HA on/off toggle - clears the panel when switched off
            if ha_get(POWER_TOGGLE)[0] == "off":
                if powered:
                    epd.init()
                    epd.Clear()
                    epd.sleep()
                    powered, awake = False, False
                    logging.info("display turned OFF via HA - screen cleared")
                time.sleep(SPEED_POLL)
                continue
            turning_on = not powered
            powered = True

            # fast value - speed, every loop
            s, _ = read_number(ENTITIES["speed"])
            if s is not None:
                speed = s

            # slow values - temps / SoC / voltage / message, every SLOW_POLL seconds
            if t0 - last_slow >= SLOW_POLL:
                for k in temps:
                    tv = read_temp_c(ENTITIES[k])
                    if tv is not None:
                        temps[k] = tv
                sv, _ = read_number(ENTITIES["soc"])
                if sv is not None:
                    soc = sv
                vv, vu = read_number(ENTITIES["voltage"])
                if vv is not None:
                    voltage, voltage_unit = vv, (vu or voltage_unit)
                ha_msg = read_message(ENTITIES["message"])
                last_slow = t0

            # manual refresh button forces a full (de-ghosting) refresh
            btn, _ = ha_get(REFRESH_BUTTON)
            force = btn is not None and btn != last_button
            if btn is not None:
                last_button = btn

            disp = rpm_to_speed(speed)
            clock = datetime.now().strftime("%H:%M")
            snaps = region_snaps(disp, temps, soc, voltage, ha_msg, clock)
            changed = [r for r in REGIONS if snaps[r] != last_snaps[r]]
            data_changed = any(r in DATA_REGIONS for r in changed)

            if data_changed or force or turning_on:
                img = render(disp, temps, soc, voltage, voltage_unit, ha_msg, clock)
                if turning_on or not awake or force or refresh_count >= FULL_REFRESH_EVERY:
                    full_refresh(epd, img)            # power-on / wake / de-ghost
                    awake = True
                    refresh_count = 0
                    logging.info(f"{'display ON' if turning_on else 'full refresh'} - "
                                 f"speed={disp:.0f}{SPEED_LABEL} "
                                 f"temps={fmt_temps(temps)} soc={soc}")
                else:
                    for r in changed:                 # gentle per-region update
                        push_region(epd, img, r)
                        refresh_count += 1
                    logging.info(f"partial {changed} - speed={disp:.0f}{SPEED_LABEL} "
                                 f"(count {refresh_count}/{FULL_REFRESH_EVERY})")
                last_snaps = snaps
                idle_since = t0
            elif awake and (t0 - idle_since) >= IDLE_SLEEP:
                # no telemetry change for a while - settle the image and sleep
                # the panel (e-paper must not be left powered/active when idle)
                img = render(disp, temps, soc, voltage, voltage_unit, ha_msg, clock)
                settle_and_sleep(epd, img)
                awake = False
                last_snaps = snaps
                logging.info("idle - panel asleep")

            dt = time.time() - t0
            if dt < SPEED_POLL:
                time.sleep(SPEED_POLL - dt)
    except Exception as e:
        logging.error(f"loop crashed: {e}")
    finally:
        logging.info("stopping - panel to sleep")
        try:
            if awake:
                epd.sleep()
        except Exception:
            pass


if __name__ == "__main__":
    main()
