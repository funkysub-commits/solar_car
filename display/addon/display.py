#!/usr/bin/env python3
"""
Solar Car e-ink dashboard - Waveshare 7.5" V2 (800x480, 1-bit).

Layout
  Header        : team logo + title + clock
  Left          : analog speedometer gauge (mph / km/h / rpm)
  Right-top     : battery icon (state of charge) + pack voltage
  Right-bottom  : four vertical temperature bar graphs (motor / EZkontrol / battery / Pi)
  Bottom band   : a small centred notification "toast" that only appears while a
                  warning is active. All warnings - CAN bus not connected, a
                  sensor that has stopped updating, a high temperature, or a
                  user message typed in Home Assistant - flow through this one
                  box. The single most important warning is shown; if more than
                  one is active a small badge shows the total count.

  Any value whose source entity has stopped updating (its last_reported stops
  advancing) gets a small "!" warning mark drawn next to it - a steady value
  that is genuinely unchanging is *not* marked, only data that isn't arriving.

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

Home Assistant integration
  * Reads the source sensors (speed / temps / SoC / voltage) and the free-text
    user message (input_text.eink_message).
  * Publishes the current list of active warnings to sensor.eink_warnings so a
    Home Assistant dashboard can show every message with a "hide" button.
  * Reads input_text.eink_hidden - a comma-separated list of warning keys the
    user has chosen to hide - and removes those from the e-paper toast. Keys
    whose warning is no longer active are pruned automatically, so a warning
    that clears and later returns is shown again.

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
from datetime import datetime, timezone

import requests

from PIL import Image, ImageDraw, ImageFont

# The Waveshare driver only exists on the Raspberry Pi. Import it lazily so the
# rendering code in this module can be imported and unit-tested on a PC. Note the
# driver claims the e-ink control GPIOs at import time (epdconfig instantiates
# RaspberryPi()), so a GPIO conflict surfaces here as the import failing.
try:
    sys.path.append('/e-Paper/RaspberryPi_JetsonNano/python/lib')
    from waveshare_epd import epd7in5_V2
except Exception as _e:        # pragma: no cover - hardware-only path
    epd7in5_V2 = None
    _EPD_IMPORT_ERROR = _e

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

# BCM pins the Waveshare 7.5" HAT driver uses (per epdconfig). Used only to
# produce a helpful log line when the panel can't be claimed.
EINK_GPIO = {17: "RST", 25: "DC", 18: "PWR", 24: "BUSY",
             8: "CS", 11: "SPI_CLK", 10: "SPI_MOSI"}


def diagnose_gpio():
    """When the driver can't claim the panel (e.g. OSError 16 'Resource busy'),
    query the GPIO chip to log which e-ink lines are already in use and by which
    consumer - that names the conflicting process (a fan overlay, a stray
    ESPHome/other driver, etc.) directly in the add-on log."""
    try:
        import gpiod
    except Exception as e:
        logging.error(f"GPIO diagnostic skipped - gpiod unavailable: {e}")
        return
    for path in ("/dev/gpiochip0", "/dev/gpiochip4"):
        try:
            chip = gpiod.Chip(path)
        except Exception:
            continue
        try:
            busy = []
            for off, label in EINK_GPIO.items():
                try:
                    info = chip.get_line_info(off)
                    if getattr(info, "used", False):
                        busy.append(f"GPIO{off}({label}) held by "
                                    f"'{getattr(info, 'consumer', '?') or '?'}'")
                except Exception:
                    pass
            if busy:
                logging.error(f"{path}: e-ink GPIO line(s) already in use -> "
                              + "; ".join(busy)
                              + " . Free that consumer (stop the other driver / "
                                "move the fan to a different GPIO) and restart.")
            else:
                logging.info(f"{path}: none of the e-ink GPIO lines report as in use")
        finally:
            try:
                chip.close()
            except Exception:
                pass

# --------------------------------------------------------------------------
# Configuration (all overridable via environment variables / add-on options)
# --------------------------------------------------------------------------
HA_URL = os.environ.get("HA_URL", "http://192.168.0.243:8123").rstrip("/")
HA_TOKEN = os.environ.get("HA_TOKEN", "")
TITLE = os.environ.get("TITLE", "SOLAR STORMS")

# Poll intervals are clamped to sane minimums so a mistyped (e.g. negative)
# option can't turn the loop into a Home Assistant-hammering busy spin.
SPEED_POLL = max(0.2, float(os.environ.get("SPEED_POLL", "2.5")))  # seconds between speedometer updates
SLOW_POLL = max(1.0, float(os.environ.get("SLOW_POLL", "6")))      # seconds between temp/SoC/message updates
FULL_REFRESH_EVERY = int(os.environ.get("FULL_REFRESH_EVERY", "90"))  # partial pushes between de-ghost full refreshes
IDLE_SLEEP = float(os.environ.get("IDLE_SLEEP", "180"))      # seconds of no change before the panel deep-sleeps

# Speed: the source entity reports motor rpm; the speedometer can show that
# raw rpm, or convert to mph / km/h using the drive wheel size and gear ratio.
SPEED_UNIT = os.environ.get("SPEED_UNIT", "mph").strip().lower()
WHEEL_DIAMETER_IN = float(os.environ.get("WHEEL_DIAMETER_IN", "20"))   # drive wheel diameter, inches
GEAR_RATIO = float(os.environ.get("GEAR_RATIO", "1") or "1")           # motor revs per wheel rev
SPEED_MAX = float(os.environ.get("SPEED_MAX", "40"))         # speedometer full-scale, in SPEED_UNIT

# Temperatures: read internally as degrees Celsius, displayed in TEMP_UNIT.
# TEMP_MAX and TEMP_WARN are interpreted in the *display* unit (so for "F" the
# user sets them in F as well).
TEMP_UNIT = os.environ.get("TEMP_UNIT", "C").strip().upper()
if TEMP_UNIT not in ("C", "F"):
    TEMP_UNIT = "C"
TEMP_MAX = float(os.environ.get("TEMP_MAX", "80"))           # temperature bar full-scale, in TEMP_UNIT
TEMP_WARN = float(os.environ.get("TEMP_WARN", "65"))         # temperature warning threshold, in TEMP_UNIT

# Source-data freshness: an entity whose last_reported has not advanced within
# STALE_AGE seconds is treated as "not updating" (gets a "!" mark). When *every*
# CAN-fed entity is stale, the "CAN bus not connected" warning is raised.
STALE_AGE = float(os.environ.get("STALE_AGE", "60"))

# Re-publish sensor.eink_warnings at least this often (seconds) even when the
# warning list is unchanged, so it self-heals after a Home Assistant restart.
PUBLISH_EVERY = float(os.environ.get("PUBLISH_EVERY", "30"))

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

# Where the driver publishes its live warning list, and where it reads the
# user's "hidden" selection back from.
WARN_SENSOR = os.environ.get("ENT_WARN_SENSOR", "sensor.eink_warnings")
ENT_HIDDEN = os.environ.get("ENT_HIDDEN", "input_text.eink_hidden")

HEADERS = {"Authorization": f"Bearer {HA_TOKEN}"}

# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------
W, H = 800, 480
HEAD_H = 48
DIV_X = 452          # vertical divider between left column and right column
BAT_DIV_Y = 262      # right column: battery above, temperatures below
CONTENT_BOT = 432    # main content ends here; below is the notification band

# Independent partial-refresh regions, (x0, y0, x1, y1).
# x coordinates MUST be multiples of 8 - the panel only refreshes byte-aligned
# columns. Regions stay clear of the frame/divider lines so those never ghost.
REGIONS = {
    "speed":  (8, 50, 448, CONTENT_BOT),
    "batt":   (456, 50, 792, 260),
    "temps":  (456, 264, 792, CONTENT_BOT),
    "notify": (8, 436, 792, 476),
    "clock":  (608, 4, 792, 46),
}
# Regions that count as real telemetry: a change here keeps the panel awake.
# The clock is redrawn alongside telemetry but never wakes the panel by itself.
DATA_REGIONS = ("speed", "batt", "temps", "notify")

FONT_DIR = "/usr/share/fonts/dejavu"

# Keys that carry a displayed numeric value and therefore can show a "!" mark.
STALE_KEYS = ("speed", "t_motor", "t_ezk", "t_batt", "t_pi", "soc", "voltage")
# CAN-bus-fed entities: if *all* of these go stale, the bus is "not connected".
CAN_KEYS = ("speed", "t_motor", "t_ezk", "t_batt", "soc", "voltage")


def _font(name, size):
    try:
        return ImageFont.truetype(f"{FONT_DIR}/{name}", size)
    except Exception:
        return ImageFont.load_default()


F_TITLE  = _font("DejaVuSans-Bold.ttf", 28)
F_LABEL  = _font("DejaVuSans-Bold.ttf", 19)
F_SPEED  = _font("DejaVuSans-Bold.ttf", 64)
F_UNIT   = _font("DejaVuSans.ttf", 22)
F_SOC    = _font("DejaVuSans-Bold.ttf", 56)
F_TEMP   = _font("DejaVuSans-Bold.ttf", 26)
F_SMALL  = _font("DejaVuSans.ttf", 17)
F_NOTIFY = _font("DejaVuSans-Bold.ttf", 22)
F_BADGE  = _font("DejaVuSans-Bold.ttf", 16)

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


# --------------------------------------------------------------------------
# Home Assistant access
# --------------------------------------------------------------------------
def ha_get(entity):
    """Return (state, attributes, last_reported_iso) for an entity. last_reported
    is preferred over last_updated because it advances on every push, even when
    the state value hasn't changed - which is what we want for staleness."""
    try:
        r = requests.get(f"{HA_URL}/api/states/{entity}", headers=HEADERS, timeout=5)
        r.raise_for_status()
        j = r.json()
        return (j.get("state"), j.get("attributes", {}),
                j.get("last_reported") or j.get("last_updated"))
    except Exception as e:
        logging.debug(f"fetch {entity} failed: {e}")
        return None, {}, None


def ha_post_state(entity, state, attributes):
    """Create/update an HA entity's state via the REST API. Used to publish the
    live warning list to sensor.eink_warnings (states POSTed this way are
    transient - they vanish on HA restart and are simply re-published)."""
    try:
        requests.post(f"{HA_URL}/api/states/{entity}",
                      headers={**HEADERS, "Content-Type": "application/json"},
                      json={"state": str(state), "attributes": attributes}, timeout=5)
    except Exception as e:
        logging.debug(f"publish {entity} failed: {e}")


def ha_call_service(domain, service, data):
    """Call an HA service via the REST API (e.g. input_text.set_value)."""
    try:
        requests.post(f"{HA_URL}/api/services/{domain}/{service}",
                      headers={**HEADERS, "Content-Type": "application/json"},
                      json=data, timeout=5)
    except Exception as e:
        logging.debug(f"service {domain}.{service} failed: {e}")


def entity_age_seconds(last_iso):
    """How long since this HA timestamp, in seconds. inf if missing/bad."""
    if not last_iso:
        return float("inf")
    try:
        ts = datetime.fromisoformat(last_iso.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds()
    except Exception:
        return float("inf")


def read_number(entity):
    """Return (float value, unit, last_iso) for a numeric entity."""
    state, attrs, lu = ha_get(entity)
    unit = attrs.get("unit_of_measurement", "")
    if state in (None, "", "unknown", "unavailable"):
        return None, unit, lu
    try:
        return float(state), unit, lu
    except (TypeError, ValueError):
        return None, unit, lu


def read_temp_c(entity):
    """Read a temperature entity and normalise to degrees Celsius.
    Returns (value_c or None, last_iso)."""
    val, unit, lu = read_number(entity)
    if val is None:
        return None, lu
    if unit and "F" in unit.upper():       # Pi sensor reports Fahrenheit
        val = (val - 32.0) * 5.0 / 9.0
    return val, lu


def read_message(entity):
    """Read the free-text message entity (input_text), or '' if unset."""
    state, _, _ = ha_get(entity)
    if state in (None, "", "unknown", "unavailable"):
        return ""
    return str(state).strip()


def read_hidden():
    """Return the set of warning keys the user has chosen to hide (read from the
    comma-separated input_text.eink_hidden helper)."""
    state, _, _ = ha_get(ENT_HIDDEN)
    if not state or state in ("unknown", "unavailable"):
        return set()
    return {p.strip() for p in str(state).split(",") if p.strip()}


def set_hidden(keys):
    """Write the hidden-key set back to input_text.eink_hidden."""
    ha_call_service("input_text", "set_value",
                    {"entity_id": ENT_HIDDEN, "value": ",".join(sorted(keys))[:255]})


def to_display_temp(t_c):
    """Convert a temperature in degrees Celsius to the configured display unit."""
    if t_c is None:
        return None
    return t_c if TEMP_UNIT == "C" else (t_c * 9.0 / 5.0 + 32.0)


# --------------------------------------------------------------------------
# Warnings / messages
# --------------------------------------------------------------------------
# Friendly labels for the "<thing> not updating" stale warnings.
STALE_WARN_LABELS = {
    "speed":   "Speed",
    "t_motor": "Motor temp",
    "t_ezk":   "EZkontrol temp",
    "t_batt":  "Battery temp",
    "t_pi":    "Pi temp",
    "soc":     "Battery SOC",
    "voltage": "Pack voltage",
}
# Labels for the high-temperature warnings.
TEMP_WARN_LABELS = {
    "t_motor": "Motor",
    "t_ezk":   "EZkontrol",
    "t_batt":  "Battery",
    "t_pi":    "Pi",
}


def compute_stale(last_iso):
    """Map each displayed value to True when its entity has stopped updating.
    Based on last_reported age, so a steady-but-fresh value is NOT stale."""
    return {k: (last_iso.get(k) is None
                or entity_age_seconds(last_iso.get(k)) > STALE_AGE)
            for k in STALE_KEYS}


def build_warnings(temps, stale, can_all_stale, ha_msg):
    """Build the ordered list of active warnings (highest priority first).

    Each warning is a dict: {key, text, priority, icon}. 'key' is stable so the
    Home Assistant side can hide an individual warning. 'icon' is "warn" for
    alarms and "info" for the user message."""
    ws = []
    if can_all_stale:
        ws.append({"key": "can", "text": "CAN bus not connected",
                   "priority": 100, "icon": "warn"})
        # The single "not connected" stands in for every CAN sensor, but a
        # non-CAN sensor (e.g. the Pi's own temp) still reports its own
        # staleness - "CAN bus not connected" doesn't explain a frozen Pi value.
        for k, lbl in STALE_WARN_LABELS.items():
            if k not in CAN_KEYS and stale.get(k):
                ws.append({"key": f"stale_{k}", "text": f"{lbl} not updating",
                           "priority": 50, "icon": "warn"})
    else:
        # bus is alive: each stalled sensor gets its own warning
        for k, lbl in STALE_WARN_LABELS.items():
            if stale.get(k):
                ws.append({"key": f"stale_{k}", "text": f"{lbl} not updating",
                           "priority": 50, "icon": "warn"})
    for k, lbl in TEMP_WARN_LABELS.items():
        if stale.get(k):
            continue                      # don't warn "high temp" off a frozen reading
        v_c = temps.get(k)
        if v_c is None:
            continue
        v = to_display_temp(v_c)
        if v >= TEMP_WARN:
            # a live high temp is a safety issue: it outranks a single stale
            # sensor, and a hotter sensor sorts ahead of a cooler one
            ws.append({"key": f"temp_{k}",
                       "text": f"High temp: {lbl} {v:.0f}°{TEMP_UNIT}",
                       "priority": 70 + min(25, v - TEMP_WARN), "icon": "warn"})
    if ha_msg:
        ws.append({"key": "user", "text": ha_msg, "priority": 30, "icon": "info"})
    ws.sort(key=lambda w: -w["priority"])
    return ws


# --------------------------------------------------------------------------
# Drawing
# --------------------------------------------------------------------------
def draw_warn_mark(d, cx, cy, h=18):
    """A small warning triangle with an exclamation - drawn next to any value
    whose data has stopped arriving, and used as the toast's alarm icon."""
    half = h / 2.0
    top = (cx, cy - half)
    bl = (cx - half * 1.06, cy + half)
    br = (cx + half * 1.06, cy + half)
    d.polygon([top, bl, br], fill=255)
    d.line([top, bl, br, top], fill=0, width=max(2, int(h * 0.12)), joint="curve")
    bw = max(2, int(h * 0.12))
    d.line((cx, cy - half * 0.28, cx, cy + half * 0.30), fill=0, width=bw)
    dr = max(1.3, h * 0.07)
    dy = cy + half * 0.62
    d.ellipse((cx - dr, dy - dr, cx + dr, dy + dr), fill=0)


def draw_info_mark(d, cx, cy, h=18):
    """A small circled "i" - the toast icon for a plain user message."""
    r = h / 2.0
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=255, outline=0, width=2)
    dr = max(1.3, h * 0.08)
    dy = cy - r * 0.42
    d.ellipse((cx - dr, dy - dr, cx + dr, dy + dr), fill=0)
    d.line((cx, cy - r * 0.08, cx, cy + r * 0.52), fill=0, width=max(2, int(h * 0.12)))


def _ellipsize(d, text, font, max_w):
    """Trim text with a trailing ellipsis so it fits within max_w pixels."""
    if d.textlength(text, font=font) <= max_w:
        return text
    ell = "…"
    while text and d.textlength(text + ell, font=font) > max_w:
        text = text[:-1]
    return (text + ell) if text else ell


def draw_speedometer(d, speed, stale=False):
    """speed is already in display units (mph / km/h / rpm)."""
    cx, cy, r = 228, 206, 120

    d.text((cx, 62), "SPEED", font=F_LABEL, fill=0, anchor="ma")
    d.arc((cx - r, cy - r, cx + r, cy + r), 180, 360, fill=0, width=4)

    # tick marks around the arc (0..10, major every 5)
    for i in range(11):
        a = math.radians(180 + 180 * (i / 10.0))
        ca, sa = math.cos(a), math.sin(a)
        major = (i % 5 == 0)
        r2 = r - 18 if major else r - 11
        d.line((cx + r * ca, cy + r * sa, cx + r2 * ca, cy + r2 * sa),
               fill=0, width=3 if major else 2)

    # scale end labels
    d.text((cx - r, cy + 8), "0", font=F_SMALL, fill=0, anchor="ma")
    d.text((cx + r, cy + 8), f"{SPEED_MAX:.0f}", font=F_SMALL, fill=0, anchor="ma")

    # needle
    val = 0.0 if speed is None else clamp(speed, 0, SPEED_MAX)
    a = math.radians(180 + 180 * (val / SPEED_MAX))
    ca, sa = math.cos(a), math.sin(a)
    rn = r - 26
    d.line((cx, cy, cx + rn * ca, cy + rn * sa), fill=0, width=5)
    d.ellipse((cx - 9, cy - 9, cx + 9, cy + 9), fill=0)

    # large numeric readout
    num = "--" if speed is None else f"{speed:.0f}"
    d.text((cx, cy + 70), num, font=F_SPEED, fill=0, anchor="mm")
    d.text((cx, cy + 116), SPEED_LABEL, font=F_UNIT, fill=0, anchor="mm")

    if stale:
        # mark next to the SPEED title - the value isn't being updated
        w = d.textlength("SPEED", font=F_LABEL)
        draw_warn_mark(d, cx + w / 2 + 18, 72, 22)


def draw_battery(d, soc, voltage, vunit, stale_soc=False, stale_v=False):
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
    if stale_soc:
        w = d.textlength(soc_txt, font=F_SOC)
        draw_warn_mark(d, x + w + 22, 222, 26)
    if voltage is not None:
        vtxt = f"{voltage:.1f} {vunit or 'V'}"
        d.text((W - 20, 234), vtxt, font=F_SMALL, fill=0, anchor="rm")
        if stale_v:
            w = d.textlength(vtxt, font=F_SMALL)
            draw_warn_mark(d, W - 20 - w - 16, 234, 20)
    elif stale_v:
        draw_warn_mark(d, W - 30, 234, 20)


def draw_temps(d, temps, stale):
    d.text((DIV_X + 22, 270), "TEMPERATURES", font=F_LABEL, fill=0, anchor="la")

    items = [
        ("MOTOR", "t_motor"),
        ("EZK",   "t_ezk"),
        ("BATT",  "t_batt"),
        ("PI",    "t_pi"),
    ]
    area_x0, area_x1 = DIV_X + 10, W - 12
    slot = (area_x1 - area_x0) / len(items)
    half = 25
    base_y, top_y = 406, 330
    bar_h = base_y - top_y

    for i, (lbl, key) in enumerate(items):
        val_c = temps.get(key)
        val = to_display_temp(val_c)              # convert C -> display unit
        cx = area_x0 + slot * i + slot / 2
        d.rectangle((cx - half, top_y, cx + half, base_y), outline=0, width=2)
        if val is not None:
            fh = bar_h * clamp(val, 0, TEMP_MAX) / TEMP_MAX
            if fh > 0:
                d.rectangle((cx - half, base_y - fh, cx + half, base_y), fill=0)
        vtxt = "--" if val is None else f"{val:.0f}°{TEMP_UNIT}"
        d.text((cx, top_y - 6), vtxt, font=F_TEMP, fill=0, anchor="md")
        d.text((cx, base_y + 7), lbl, font=F_SMALL, fill=0, anchor="ma")
        if stale.get(key):
            draw_warn_mark(d, cx + half + 9, top_y + 12, 17)


def draw_notify(d, warnings):
    """Draw the bottom notification toast: a small centred box showing the most
    important active warning, with a count badge if more than one is active.
    Draws nothing when there are no active warnings."""
    if not warnings:
        return
    top = warnings[0]
    count = len(warnings)

    cy = 456
    box_h = 34
    pad = 14
    icon_sz = 22
    gap = 9
    badge_d = 26 if count > 1 else 0
    badge_gap = 9 if count > 1 else 0

    max_box_w = W - 80
    max_text_w = max_box_w - (pad * 2 + icon_sz + gap + badge_gap + badge_d)
    text = _ellipsize(d, top["text"], F_NOTIFY, max_text_w)
    tw = d.textlength(text, font=F_NOTIFY)

    box_w = int(pad * 2 + icon_sz + gap + tw + badge_gap + badge_d)
    bx0 = (W - box_w) // 2
    bx1 = bx0 + box_w
    by0 = cy - box_h // 2
    by1 = cy + box_h // 2

    # white fill clears whatever was beneath, then a rounded outline = a chip
    d.rounded_rectangle((bx0, by0, bx1, by1), radius=9, fill=255, outline=0, width=2)

    ix = bx0 + pad + icon_sz // 2
    if top.get("icon") == "info":
        draw_info_mark(d, ix, cy, icon_sz)
    else:
        draw_warn_mark(d, ix, cy, icon_sz)

    tx = bx0 + pad + icon_sz + gap
    d.text((tx, cy), text, font=F_NOTIFY, fill=0, anchor="lm")

    if count > 1:
        r = badge_d // 2
        bcx = bx1 - pad - r
        d.ellipse((bcx - r, cy - r, bcx + r, cy + r), fill=0)
        d.text((bcx, cy - 1), str(count), font=F_BADGE, fill=255, anchor="mm")


def render(speed, temps, soc, voltage, voltage_unit, warnings, stale, clock_str):
    """speed is already in display units (see rpm_to_speed). warnings is the
    visible (non-hidden) ordered warning list. stale maps value keys -> bool."""
    img = Image.new('1', (W, H), 255)
    d = ImageDraw.Draw(img)

    d.rectangle((1, 1, W - 2, H - 2), outline=0, width=2)
    d.line((2, HEAD_H, W - 3, HEAD_H), fill=0, width=2)
    d.line((DIV_X, HEAD_H, DIV_X, CONTENT_BOT), fill=0, width=2)
    d.line((DIV_X, BAT_DIV_Y, W - 3, BAT_DIV_Y), fill=0, width=2)

    # header: logo + title + clock
    tx = 16
    if LOGO is not None:
        img.paste(LOGO, (14, 5))
        tx = 14 + LOGO.width + 12
    d.text((tx, 9), TITLE, font=F_TITLE, fill=0, anchor="la")
    d.text((W - 18, 9), clock_str, font=F_TITLE, fill=0, anchor="ra")

    draw_speedometer(d, speed, stale.get("speed", False))
    draw_battery(d, soc, voltage, voltage_unit,
                 stale.get("soc", False), stale.get("voltage", False))
    draw_temps(d, temps, stale)
    draw_notify(d, warnings)
    return img


# --------------------------------------------------------------------------
# Change detection and panel updates
# --------------------------------------------------------------------------
def region_snaps(speed, temps, soc, voltage, warnings, stale, clock_str):
    """Per-region coarse snapshot - a region is only refreshed when its tuple
    changes. speed is in display units. Stale flags are included so a value's
    "!" mark appearing/clearing triggers a refresh of that region."""
    return {
        "speed": (None if speed is None else round(speed), stale.get("speed", False)),
        "batt": (None if soc is None else round(soc),
                 None if voltage is None else round(voltage, 1),
                 stale.get("soc", False), stale.get("voltage", False)),
        "temps": tuple((None if temps.get(k) is None else round(to_display_temp(temps.get(k))),
                        stale.get(k, False))
                       for k in ("t_motor", "t_ezk", "t_batt", "t_pi")),
        "notify": (warnings[0]["text"] if warnings else None,
                   warnings[0].get("icon") if warnings else None,
                   len(warnings)),
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


def publish_warnings(all_ws, hidden):
    """Publish the full warning list (active + hidden) to sensor.eink_warnings so
    the Home Assistant dashboard can show every message with a hide control."""
    visible = [w for w in all_ws if w["key"] not in hidden]
    items, lines = [], []
    for w in all_ws:
        h = w["key"] in hidden
        items.append({"key": w["key"], "text": w["text"],
                      "icon": w["icon"], "hidden": h})
        lines.append(f"- {'(hidden) ' if h else ''}{w['text']}")
    attrs = {
        "friendly_name": "E-Ink Messages",
        "icon": "mdi:message-alert",
        "count": len(visible),
        "total": len(all_ws),
        "warnings": items,
        "lines": "\n".join(lines) if lines else "_No active messages_",
        # convenience lists the dashboard's per-message hide buttons key off
        "keys_visible": [w["key"] for w in visible],
        "keys_hidden": [w["key"] for w in all_ws if w["key"] in hidden],
    }
    ha_post_state(WARN_SENSOR, len(visible), attrs)


# --------------------------------------------------------------------------
# Main loop
# --------------------------------------------------------------------------
def main():
    if epd7in5_V2 is None:
        logging.error(f"Waveshare e-Paper library not available: {_EPD_IMPORT_ERROR}")
        diagnose_gpio()
        sys.exit(1)
    if not HA_TOKEN:
        logging.warning("HA_TOKEN is empty - all Home Assistant reads will fail")

    epd = epd7in5_V2.EPD()
    stop = {"flag": False}

    def on_term(*_):
        stop["flag"] = True
    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)

    logging.info(f"init + clear  (speed unit: {SPEED_LABEL}, temp unit: {TEMP_UNIT})")
    epd.init()
    epd.Clear()

    # Per-entity last_reported timestamps for the staleness check.
    last_iso = {k: None for k in STALE_KEYS}

    # A failed HA read returns last_iso=None; never let that overwrite a value's
    # freshness timestamp, or a single transient read error would instantly flag
    # the value stale (and could falsely flip the whole display to "CAN bus not
    # connected"). Only advance the timestamp on a successful read.
    speed, _, lu = read_number(ENTITIES["speed"])
    if lu is not None:
        last_iso["speed"] = lu
    temps = {k: None for k in ("t_motor", "t_ezk", "t_batt", "t_pi")}
    for k in temps:
        v_c, lu = read_temp_c(ENTITIES[k])
        temps[k] = v_c
        if lu is not None:
            last_iso[k] = lu
    soc, _, lu = read_number(ENTITIES["soc"])
    if lu is not None:
        last_iso["soc"] = lu
    voltage, voltage_unit, lu = read_number(ENTITIES["voltage"])
    if lu is not None:
        last_iso["voltage"] = lu
    if not voltage_unit:
        voltage_unit = "V"
    ha_msg = read_message(ENTITIES["message"])
    hidden = read_hidden()

    def can_all_stale():
        return all(last_iso[k] is None
                   or entity_age_seconds(last_iso[k]) > STALE_AGE
                   for k in CAN_KEYS)

    def assemble():
        """Compute (display speed, stale map, visible warnings) and keep the
        published HA warning list in sync. The hidden-key set is owned and pruned
        by the slow-poll step (sync_hidden) so there is exactly one writer of
        input_text.eink_hidden - this just reads it."""
        nonlocal _pub_sig, _pub_time
        stale = compute_stale(last_iso)
        all_ws = build_warnings(temps, stale, can_all_stale(), ha_msg)
        visible = [w for w in all_ws if w["key"] not in hidden]
        # publish on change, and as a heartbeat every PUBLISH_EVERY seconds so the
        # REST-published sensor reappears within ~30s of a Home Assistant restart
        sig = (tuple(w["key"] for w in all_ws), tuple(sorted(hidden)))
        now = time.time()
        if sig != _pub_sig or (now - _pub_time) >= PUBLISH_EVERY:
            publish_warnings(all_ws, hidden)
            _pub_sig = sig
            _pub_time = now
        return rpm_to_speed(speed), stale, visible

    def sync_hidden(msg_changed):
        """Read the authoritative hidden set and rewrite it iff it needs changing:
        drop keys whose warning is no longer active (so a recurrence shows again),
        and drop 'user' when the message text just changed (a new message must not
        stay silenced). Reading immediately before writing keeps this the single
        writer and avoids clobbering a Hide the dashboard just applied."""
        nonlocal hidden
        cur = read_hidden()
        target = set(cur)
        if msg_changed:
            target.discard("user")
        active_keys = {w["key"] for w in
                       build_warnings(temps, compute_stale(last_iso),
                                      can_all_stale(), ha_msg)}
        target &= active_keys
        if target != cur:
            set_hidden(target)
        hidden = target

    _pub_sig = None
    _pub_time = 0.0

    disp, stale, visible = assemble()
    clock = datetime.now().strftime("%H:%M")
    powered = ha_get(POWER_TOGGLE)[0] != "off"   # default ON if the toggle is absent
    if powered:
        img = render(disp, temps, soc, voltage, voltage_unit, visible, stale, clock)
        full_refresh(epd, img)            # clean base frame, then partial mode
        logging.info("initial frame drawn")
    else:
        epd.sleep()                       # already cleared above; just sleep the panel
        logging.info("display starts OFF (HA toggle)")

    last_snaps = region_snaps(disp, temps, soc, voltage, visible, stale, clock)
    refresh_count = 0
    last_slow = time.time()
    last_button, _, _ = ha_get(REFRESH_BUTTON)
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

            # fast value - speed, every loop (keep prior age on a failed read)
            s, _, lu = read_number(ENTITIES["speed"])
            if s is not None:
                speed = s
            if lu is not None:
                last_iso["speed"] = lu

            # slow values - temps / SoC / voltage / message / hidden, every SLOW_POLL seconds
            if t0 - last_slow >= SLOW_POLL:
                for k in temps:
                    tv, lu = read_temp_c(ENTITIES[k])
                    if tv is not None:
                        temps[k] = tv
                    if lu is not None:
                        last_iso[k] = lu
                sv, _, lu = read_number(ENTITIES["soc"])
                if sv is not None:
                    soc = sv
                if lu is not None:
                    last_iso["soc"] = lu
                vv, vu, lu = read_number(ENTITIES["voltage"])
                if vv is not None:
                    voltage, voltage_unit = vv, (vu or voltage_unit)
                if lu is not None:
                    last_iso["voltage"] = lu
                prev_msg = ha_msg
                ha_msg = read_message(ENTITIES["message"])
                sync_hidden(ha_msg != prev_msg)   # single writer of eink_hidden
                last_slow = t0

            # manual refresh button forces a full (de-ghosting) refresh
            btn, _, _ = ha_get(REFRESH_BUTTON)
            force = btn is not None and btn != last_button
            if btn is not None:
                last_button = btn

            disp, stale, visible = assemble()
            clock = datetime.now().strftime("%H:%M")

            snaps = region_snaps(disp, temps, soc, voltage, visible, stale, clock)
            changed = [r for r in REGIONS if snaps[r] != last_snaps.get(r)]
            data_changed = any(r in DATA_REGIONS for r in changed)

            if data_changed or force or turning_on:
                img = render(disp, temps, soc, voltage, voltage_unit, visible, stale, clock)
                if turning_on or not awake or force or refresh_count >= FULL_REFRESH_EVERY:
                    full_refresh(epd, img)        # power-on / wake / de-ghost
                    awake = True
                    refresh_count = 0
                    logging.info(f"{'display ON' if turning_on else 'full refresh'} - "
                                 f"speed={disp:.0f}{SPEED_LABEL} "
                                 f"temps={fmt_temps(temps)} soc={soc} warn={len(visible)}")
                else:
                    for r in changed:             # gentle per-region update
                        push_region(epd, img, r)
                        refresh_count += 1
                    logging.info(f"partial {changed} - speed={disp:.0f}{SPEED_LABEL} "
                                 f"(count {refresh_count}/{FULL_REFRESH_EVERY})")
                last_snaps = snaps
                idle_since = t0
            elif awake and (t0 - idle_since) >= IDLE_SLEEP:
                # no telemetry change for a while - settle the image and sleep
                # the panel (e-paper must not be left powered/active when idle)
                img = render(disp, temps, soc, voltage, voltage_unit, visible, stale, clock)
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
