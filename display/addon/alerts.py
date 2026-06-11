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
            if k not in config.CAN_KEYS and stale.get(k):
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
