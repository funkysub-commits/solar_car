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


def aux_is_low(aux_soc, levels):
    """True when the auxiliary battery SoC has dropped to/below the highest
    configured alert level - i.e. it has entered the alerting zone. Used to keep
    the "AUX battery low" warning on the panel; a None reading or no configured
    levels is never low."""
    return aux_soc is not None and bool(levels) and aux_soc <= levels[0]


def aux_low_levels_crossed(aux_soc, triggered, levels, rearm_margin=2):
    """Edge-detect the audible aux low-battery alarm.

    Returns (fire, new_triggered). A level fires (fire=True) the first time the
    SoC drops to/below it, and stays armed - so it does NOT re-fire every poll
    while the battery sits low. A level re-arms (and can fire again on a later
    drop) only once the SoC climbs back to level+rearm_margin, so a reading that
    jitters a percent or two around a threshold doesn't retrigger the sound.

    Pure and state-carrying: the caller owns `triggered` (the set of levels
    currently alerted) and passes the returned set back next time. A None SoC
    leaves the state untouched."""
    if aux_soc is None:
        return False, triggered
    new = set(triggered)
    fire = False
    for level in levels:
        if aux_soc <= level:
            if level not in new:
                new.add(level)
                fire = True                # newly crossed this level downward
        elif aux_soc >= level + rearm_margin:
            new.discard(level)             # recovered clear of it - re-arm
    return fire, new


def build_warnings(temps, stale, status, ha_down=False, aux_down=False,
                   aux_low=False, aux_soc=None):
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

    aux_down means the auxiliary battery's status sensor explicitly reads down.
    The caller only ever passes True while the aux battery is enabled, so a
    disabled (or merely absent/placeholder) aux battery raises no warning.

    aux_low means the aux battery SoC has fallen into the low-charge alerting
    zone (aux_is_low); aux_soc is that reading, shown in the warning text.

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
    # The keys stay can_bestgo / can_ezk - the HA dashboard's per-warning hide
    # buttons key off them, so renaming would un-hide whatever the user had hid.
    if batt_down:
        ws.append({"key": "can_bestgo", "text": "Battery disconnected",
                   "priority": 96, "icon": "warn"})
    if ezk_down:
        ws.append({"key": "can_ezk", "text": "Motor disconnected",
                   "priority": 95, "icon": "warn"})
    # aux battery - only ever passed True while config.AUX_ENABLED
    if aux_down:
        ws.append({"key": "aux_batt", "text": "AUX battery disconnected",
                   "priority": 94, "icon": "warn"})
    if aux_low:
        soc_txt = f" {aux_soc:.0f}%" if aux_soc is not None else ""
        ws.append({"key": "aux_low", "text": f"AUX battery low{soc_txt}",
                   "priority": 92, "icon": "warn"})
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
