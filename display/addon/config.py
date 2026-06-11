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

# Poll intervals are clamped to sane minimums so a mistyped (e.g. negative)
# option can't turn the loop into a Home Assistant-hammering busy spin.
SPEED_POLL = max(0.2, float(os.environ.get("SPEED_POLL", "2.5")))  # seconds between speedometer updates
SLOW_POLL = max(1.0, float(os.environ.get("SLOW_POLL", "6")))      # seconds between temp/SoC/message updates
FULL_REFRESH_EVERY = int(os.environ.get("FULL_REFRESH_EVERY", "90"))  # partial pushes between de-ghost full refreshes
IDLE_SLEEP = float(os.environ.get("IDLE_SLEEP", "180"))      # seconds of no change before the panel deep-sleeps

# Speed: shown exactly as the source entity reports it - value AND unit come
# from Home Assistant (no conversion in the add-on). Point ent_speed at a
# sensor in the unit you want on the gauge and set speed_max to match.
SPEED_MAX = float(os.environ.get("SPEED_MAX", "40"))         # speedometer full-scale, in the entity's unit

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
}
REFRESH_BUTTON = "input_button.eink_refresh"
POWER_TOGGLE = os.environ.get("ENT_POWER", "input_boolean.eink_display")

# Where the driver publishes its live warning list, and where it reads the
# user's "hidden" selection back from.
WARN_SENSOR = os.environ.get("ENT_WARN_SENSOR", "sensor.eink_warnings")
ENT_HIDDEN = os.environ.get("ENT_HIDDEN", "input_text.eink_hidden")

HEADERS = {"Authorization": f"Bearer {HA_TOKEN}"}

# Keys that carry a displayed numeric value and therefore can show a "!" mark.
STALE_KEYS = ("speed", "t_motor", "t_ezk", "t_batt", "t_pi", "soc", "voltage")
# CAN-bus-fed entities: if *all* of these go stale, the bus is "not connected".
CAN_KEYS = ("speed", "t_motor", "t_ezk", "t_batt", "soc", "voltage")
