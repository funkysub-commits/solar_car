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

# Labels for the high-temperature warnings.
TEMP_WARN_LABELS = {
    "t_motor": "Motor",
    "t_ezk":   "EZkontrol",
    "t_batt":  "Battery",
    "t_pi":    "Pi",
}


def compute_stale(last_iso):
    """Map each displayed value to True when its entity has stopped updating.
    Based on last_reported age, so a steady-but-fresh value is NOT stale.

    This is an INTERNAL signal only: it feeds the CAN-device inference in
    device_status() and suppresses "high temp" warnings raised off a frozen
    reading. It deliberately no longer reaches the screen - see device_marks()."""
    return {k: (last_iso.get(k) is None
                or ha_client.entity_age_seconds(last_iso.get(k)) > config.STALE_AGE)
            for k in config.STALE_KEYS}


def device_status(stale, health):
    """Decide (adapter_down, batt_down, ezk_down) for the three CAN devices,
    each INDEPENDENTLY (no folding) so each gets its own warning:

      adapter = sensor.canadapter_status (the USB-CAN bus itself)
      batt    = sensor.bestgo_status     (the battery BMS)
      ezk     = sensor.ezkontrol_status  (the motor controller)

    health maps {"bus","batt","ezk"} to the tri-state read of those sensors
    (True up / False down / None unknown). An explicit False wins outright;
    while a sensor is unknown (not published yet, or HA unreachable) the same
    fact is inferred from staleness - a device is presumed off the bus when
    every value it feeds has stopped updating, and the adapter when ALL CAN
    values have."""
    a, b, e = health.get("bus"), health.get("batt"), health.get("ezk")
    adapter_down = (a is False) or (a is None and all(stale.get(k) for k in config.CAN_KEYS))
    batt_down = (b is False) or (b is None and all(stale.get(k) for k in config.BATT_KEYS))
    ezk_down = (e is False) or (e is None and all(stale.get(k) for k in config.EZK_KEYS))
    return adapter_down, batt_down, ezk_down


def merge_device_stale(stale, adapter_down, batt_down, ezk_down):
    """Force the "!" mark onto every value fed by a device that is off the
    bus - and only those values. A battery dropout marks exactly the three
    battery-fed values; the EZkontrol values stay clean (and vice versa); the
    adapter being down marks every CAN value."""
    out = dict(stale)
    down_keys = set()
    if adapter_down:
        down_keys |= set(config.CAN_KEYS)
    if batt_down:
        down_keys |= set(config.BATT_KEYS)
    if ezk_down:
        down_keys |= set(config.EZK_KEYS)
    for k in down_keys:
        out[k] = True
    return out


def device_marks(adapter_down, batt_down, ezk_down):
    """The "!" marks actually drawn on screen: ONLY values fed by a CAN device
    that is off the bus. A value that has merely stopped *changing* - a parked
    car's speed, a settled temperature - is never marked, because a steady
    reading is normal and marking it would cry wolf. A real dropout still shows,
    since a disconnected device marks every value it feeds."""
    return merge_device_stale({k: False for k in config.STALE_KEYS},
                              adapter_down, batt_down, ezk_down)


def build_warnings(temps, stale, status, ha_down=False):
    """Build the ordered list of active WARNINGS (highest priority first). The
    plain user message is NOT a warning - it lives in its own message box - so
    it is not produced here.

    status is the (adapter_down, batt_down, ezk_down) triple; stale should
    already have device outages merged in (merge_device_stale).

    ha_down means Home Assistant itself is unreachable. That makes every
    CAN/staleness deduction unknowable (the data stops at HA, not at the bus),
    so those warnings are replaced by a single accurate one - otherwise an HA
    outage would masquerade as a CAN fault and send whoever is debugging to the
    wrong subsystem.

    Each warning is a dict {key, text, priority, icon}; 'key' is stable so the
    HA dashboard can hide an individual warning."""
    if ha_down:
        return [{"key": "ha", "text": "Home Assistant unreachable",
                 "priority": 110, "icon": "warn"}]
    adapter_down, batt_down, ezk_down = status
    ws = []
    # Three INDEPENDENT device warnings; the adapter takes priority.
    if adapter_down:
        ws.append({"key": "can_adapter", "text": "CAN adapter disconnected",
                   "priority": 100, "icon": "warn"})
    if batt_down:
        ws.append({"key": "can_bestgo", "text": "BESTGO disconnected",
                   "priority": 96, "icon": "warn"})
    if ezk_down:
        ws.append({"key": "can_ezk", "text": "EZkontrol disconnected",
                   "priority": 95, "icon": "warn"})
    # high temps (live readings only) - capped below the device warnings
    for k, lbl in TEMP_WARN_LABELS.items():
        if stale.get(k):
            continue                      # don't warn "high temp" off a frozen reading
        v_c = temps.get(k)
        if v_c is None:
            continue
        v = units.to_display_temp(v_c)
        if v >= config.TEMP_WARN:
            ws.append({"key": f"temp_{k}",
                       "text": f"High temp: {lbl} {v:.0f}°{config.TEMP_UNIT}",
                       "priority": 70 + min(20, v - config.TEMP_WARN), "icon": "warn"})
    # NOTE: there is deliberately no "<value> not updating" warning. A value that
    # stops changing is expected (a stopped car, a settled temperature); only a
    # device that is actually off the bus is worth warning about, and that is
    # already covered by the three device warnings above.
    ws.sort(key=lambda w: -w["priority"])
    return ws


def fit_hidden(keys, all_ws, limit=255):
    """input_text caps its value at 255 chars, and a blind mid-key cut would
    corrupt the whole hidden list. Drop the lowest-priority keys until the
    CSV fits, keeping the hides that matter most. Returns (kept, dropped)."""
    prio = {w["key"]: w["priority"] for w in all_ws}
    keep = set(keys)
    dropped = set()
    while keep and len(",".join(sorted(keep))) > limit:
        weakest = min(keep, key=lambda k: (prio.get(k, 0), k))
        keep.discard(weakest)
        dropped.add(weakest)
    return keep, dropped


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
        "friendly_name": "E-Ink Warnings",
        "icon": "mdi:alert",
        "count": len(visible),
        "total": len(all_ws),
        "warnings": items,
        "lines": "\n".join(lines) if lines else "_No active warnings_",
        # convenience lists the dashboard's per-message hide buttons key off
        "keys_visible": [w["key"] for w in visible],
        "keys_hidden": [w["key"] for w in all_ws if w["key"] in hidden],
    }
    ha_client.ha_post_state(config.WARN_SENSOR, len(visible), attrs)
