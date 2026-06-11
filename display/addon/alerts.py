"""Warning model: staleness detection, the prioritised warning list, and
publishing it to Home Assistant.

(The Phase 3 plan called this module warnings.py; it is named alerts.py
because the add-on copies its modules flat into / on the container, where a
warnings.py would shadow the stdlib `warnings` module that requests/logging
import.)
"""
import config
import ha_client
import units

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
                or ha_client.entity_age_seconds(last_iso.get(k)) > config.STALE_AGE)
            for k in config.STALE_KEYS}


def device_status(stale, health):
    """Decide (bus_down, batt_down, ezk_down) for the CAN bus and the two
    devices on it.

    health maps {"bus", "batt", "ezk"} to the tri-state read of the CANbus
    app's connectivity sensors (True up / False down / None unknown). An
    explicit False wins outright; while a sensor is unknown (not published
    yet, or HA unreachable) the same fact is inferred from staleness: a
    device is presumed off the bus when every value it feeds has stopped
    updating. A bus-level failure implies both devices, so their individual
    flags are folded into bus_down rather than reported twice."""
    batt_stale = all(stale.get(k) for k in config.BATT_KEYS)
    ezk_stale = all(stale.get(k) for k in config.EZK_KEYS)
    bus, batt, ezk = health.get("bus"), health.get("batt"), health.get("ezk")
    bus_down = (bus is False) or (bus is None and batt_stale and ezk_stale)
    batt_down = (batt is False) or (batt is None and batt_stale)
    ezk_down = (ezk is False) or (ezk is None and ezk_stale)
    if bus_down:
        batt_down = ezk_down = False      # implied by the bus being down
    return bus_down, batt_down, ezk_down


def merge_device_stale(stale, bus_down, batt_down, ezk_down):
    """Force the "!" mark onto every value fed by a device that is off the
    bus - and only those values. A battery dropout marks exactly the three
    battery-fed values; the EZkontrol values stay clean (and vice versa)."""
    out = dict(stale)
    down_keys = ()
    if bus_down:
        down_keys = config.CAN_KEYS
    else:
        if batt_down:
            down_keys += config.BATT_KEYS
        if ezk_down:
            down_keys += config.EZK_KEYS
    for k in down_keys:
        out[k] = True
    return out


def build_warnings(temps, stale, status, ha_msg):
    """Build the ordered list of active warnings (highest priority first).
    status is the (bus_down, batt_down, ezk_down) triple from device_status;
    stale should already have device outages merged in (merge_device_stale).

    Each warning is a dict: {key, text, priority, icon}. 'key' is stable so the
    Home Assistant side can hide an individual warning. 'icon' is "warn" for
    alarms and "info" for the user message."""
    bus_down, batt_down, ezk_down = status
    ws = []
    explained = set()      # keys whose staleness a device warning already explains
    if bus_down:
        ws.append({"key": "can", "text": "CAN bus not connected",
                   "priority": 100, "icon": "warn"})
        explained.update(config.CAN_KEYS)
    if batt_down:
        ws.append({"key": "can_batt", "text": "Battery not on CAN bus",
                   "priority": 90, "icon": "warn"})
        explained.update(config.BATT_KEYS)
    if ezk_down:
        ws.append({"key": "can_ezk", "text": "EZkontrol not on CAN bus",
                   "priority": 90, "icon": "warn"})
        explained.update(config.EZK_KEYS)
    # Any remaining stalled sensor gets its own warning - e.g. the Pi's own
    # temperature, whose staleness no CAN warning explains.
    for k, lbl in STALE_WARN_LABELS.items():
        if k not in explained and stale.get(k):
            ws.append({"key": f"stale_{k}", "text": f"{lbl} not updating",
                       "priority": 50, "icon": "warn"})
    for k, lbl in TEMP_WARN_LABELS.items():
        if stale.get(k):
            continue                      # don't warn "high temp" off a frozen reading
        v_c = temps.get(k)
        if v_c is None:
            continue
        v = units.to_display_temp(v_c)
        if v >= config.TEMP_WARN:
            # a live high temp is a safety issue: it outranks a single stale
            # sensor, and a hotter sensor sorts ahead of a cooler one
            ws.append({"key": f"temp_{k}",
                       "text": f"High temp: {lbl} {v:.0f}°{config.TEMP_UNIT}",
                       "priority": 70 + min(25, v - config.TEMP_WARN), "icon": "warn"})
    if ha_msg:
        ws.append({"key": "user", "text": ha_msg, "priority": 30, "icon": "info"})
    ws.sort(key=lambda w: -w["priority"])
    return ws


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
    ha_client.ha_post_state(config.WARN_SENSOR, len(visible), attrs)
