"""All add-on configuration, parsed from environment variables in one place.

When run as a Home Assistant add-on, run.sh fills the environment from the
add-on options (Settings > Add-ons > Solar Car E-Ink Display > Configuration)
and points HA_URL / HA_TOKEN at the Supervisor proxy (no long-lived token
needed). Everything is overridable directly via env for PC-side testing.
"""
import logging
import os

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

HA_URL = os.environ.get("HA_URL", "http://supervisor/core").rstrip("/")
HA_TOKEN = os.environ.get("HA_TOKEN", "")
TITLE = os.environ.get("TITLE", "SOLAR STORMS")

# The Supervisor API (separate from the HA Core proxy above) is used only to
# discover the Pi's LAN IP for the header line. SUPERVISOR_TOKEN is injected
# into every add-on container; reaching /network/info also needs
# `hassio_api: true` in config.yaml. HA_PORT is the port shown after the IP
# (Home Assistant's web UI, 8123 by default).
SUPERVISOR_URL = os.environ.get("SUPERVISOR_URL", "http://supervisor").rstrip("/")
SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", HA_TOKEN)
HA_PORT = os.environ.get("HA_PORT", "8123")

# Poll intervals are clamped to sane minimums so a mistyped (e.g. negative)
# option can't turn the loop into a Home Assistant-hammering busy spin.
SPEED_POLL = max(0.2, float(os.environ.get("SPEED_POLL", "2.5")))  # seconds between speedometer updates
SLOW_POLL = max(1.0, float(os.environ.get("SLOW_POLL", "6")))      # seconds between temp/SoC/message updates
FULL_REFRESH_EVERY = int(os.environ.get("FULL_REFRESH_EVERY", "90"))  # partial pushes between de-ghost full refreshes
IDLE_SLEEP = float(os.environ.get("IDLE_SLEEP", "180"))      # seconds of no change before the panel deep-sleeps

# Speed: the VALUE is shown exactly as the source entity reports it - the add-on
# does no numeric conversion. Point ent_speed at a sensor in the unit you want on
# the gauge and set speed_max to match.
SPEED_MAX = float(os.environ.get("SPEED_MAX", "40"))         # speedometer full-scale, in the entity's unit
# Relabel the gauge's unit text only (e.g. show "mph" where the entity reports
# "rpm"). Purely cosmetic - the number is untouched - so set it only when the
# entity already reports the value in the unit you want to name. Empty = use
# whatever unit Home Assistant reports.
SPEED_UNIT = os.environ.get("SPEED_UNIT", "").strip()
if SPEED_UNIT.lower() in ("null", "none"):   # bashio yields "null" for an unset option
    SPEED_UNIT = ""

# Temperatures: read internally as degrees Celsius, displayed in TEMP_UNIT.
# TEMP_MAX and TEMP_WARN are interpreted in the *display* unit (so for "F" the
# user sets them in F as well).
TEMP_UNIT = os.environ.get("TEMP_UNIT", "C").strip().upper()
if TEMP_UNIT not in ("C", "F"):
    TEMP_UNIT = "C"
TEMP_MAX = float(os.environ.get("TEMP_MAX", "80"))           # temperature bar full-scale, in TEMP_UNIT
TEMP_WARN = float(os.environ.get("TEMP_WARN", "65"))         # temperature warning threshold, in TEMP_UNIT

# Source-data freshness: an entity whose last_reported has not advanced within
# STALE_AGE seconds is treated as "not updating". This is an internal signal
# only - it never puts a "!" on screen or raises a warning by itself, because a
# value that stops changing is normal (a parked car, a settled temperature). It
# is used to infer which CAN device is off the bus when the health sensors say
# nothing, and to stop a frozen reading raising a "high temp" warning.
STALE_AGE = float(os.environ.get("STALE_AGE", "60"))

# Re-publish sensor.eink_warnings at least this often (seconds) even when the
# warning list is unchanged, so it self-heals after a Home Assistant restart.
PUBLISH_EVERY = float(os.environ.get("PUBLISH_EVERY", "30"))

# Written into the message helper once, at start-up, so the MESSAGE box always
# begins from a known state instead of whatever was left there last run. Empty
# clears the box on boot.
STARTUP_MESSAGE = os.environ.get("STARTUP_MESSAGE", "")
if STARTUP_MESSAGE.strip().lower() in ("null", "none"):   # unset bashio option
    STARTUP_MESSAGE = ""

LOGO_PATH = os.environ.get("LOGO_PATH", "/logo.png")
FONT_DIR = os.environ.get("FONT_DIR", "/usr/share/fonts/dejavu")

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
    # Battery charging state: when "on"/"charging"/true a lightning bolt is drawn
    # over the battery icon. Point this at a binary_sensor (or any on/off-ish
    # entity) that is on while the pack is charging.
    "charging": os.environ.get("ENT_CHARGING", "binary_sensor.bestgo_charging"),
    # Auxiliary (12V) battery state of charge, shown as a small "AUX nn%" above
    # the right of the main battery bar. Placeholder entity until the real one
    # exists; reads "--" while it is missing/unavailable.
    "aux_soc": os.environ.get("ENT_AUX_SOC", "sensor.aux_battery_soc"),
}
REFRESH_BUTTON = "input_button.eink_refresh"
POWER_TOGGLE = os.environ.get("ENT_POWER", "input_boolean.eink_display")

# Where the driver publishes its live warning list, and where it reads the
# user's "hidden" selection back from.
WARN_SENSOR = os.environ.get("ENT_WARN_SENSOR", "sensor.eink_warnings")
ENT_HIDDEN = os.environ.get("ENT_HIDDEN", "input_text.eink_hidden")

# CAN health sensors, published by the solar-car-canbus app (1 = up, 0 = down):
# canadapter_status (USB-CAN bus open), bestgo_status (battery sending frames),
# ezkontrol_status (controller sending frames). If one reads unknown OR has
# itself gone stale (the canbus app stopped pushing it), the display falls
# back to inferring the same fact from per-sensor staleness, so it is robust
# either way.
ENT_CAN_BUS = os.environ.get("ENT_CAN_BUS", "sensor.canadapter_status")
ENT_CAN_BATT = os.environ.get("ENT_CAN_BATT", "sensor.bestgo_status")
ENT_CAN_EZK = os.environ.get("ENT_CAN_EZK", "sensor.ezkontrol_status")

HEADERS = {"Authorization": f"Bearer {HA_TOKEN}"}

# Keys that carry a displayed numeric value and therefore can show a "!" mark.
STALE_KEYS = ("speed", "t_motor", "t_ezk", "t_batt", "t_pi", "soc", "voltage")
# Which displayed values come from which CAN device. This scoping drives the
# "!" marks: if (say) only the battery drops off the bus, exactly its three
# values are marked - the EZkontrol values stay clean. It also decides which
# per-sensor stale warnings a device-level warning replaces.
EZK_KEYS = ("speed", "t_motor", "t_ezk")
BATT_KEYS = ("t_batt", "soc", "voltage")
# All CAN-bus-fed entities (everything except the Pi's own temperature).
CAN_KEYS = EZK_KEYS + BATT_KEYS
