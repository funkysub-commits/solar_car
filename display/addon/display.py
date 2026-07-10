#!/usr/bin/env python3
"""
Solar Car e-ink dashboard - Waveshare 7.5" V2 (800x480, 1-bit).

This is the add-on entry point: hardware init, signal handling, the poll /
assemble / push loop, and the Home Assistant power-toggle. The rest lives in
focused modules, all copied flat next to this file in the container:

  config.py     all options/env parsing
  layout.py     geometry, fonts, logo, partial-refresh regions
  units.py      clamp + unit conversions
  ha_client.py  Home Assistant REST access + typed readers
  alerts.py     staleness, the prioritised warning list, HA publishing
  render.py     pure-PIL frame drawing
  panel.py      Waveshare driver guard, GPIO diagnostics, refresh state ops

Layout
  Header        : team logo + title + HA IP / "Pi Offline" line + clock
  Left          : analog speedometer gauge (value exactly as the configured HA
                  entity reports it - no numeric conversion here; the unit label
                  can be relabelled with the speed_unit option)
  Right-top     : battery icon (state of charge, + a lightning bolt while
                  charging) + pack voltage + a small "AUX nn%" 12V reading
  Right-bottom  : four vertical temperature bar graphs (motor / EZkontrol / battery / Pi)
  Bottom band   : a small centred notification "toast" that only appears while a
                  warning is active. All warnings - a CAN device off the bus, an
                  aux battery off the bus, or a high temperature - flow through
                  this one box. The single most important warning is shown; if
                  more than one is active a small badge shows the total count.

  A small "!" mark is drawn next to a value only when the device that feeds it
  is off the bus. A value that has merely stopped *changing* - a parked car's
  speed, a settled temperature - is never marked, because that is normal.

  The clock ticks seconds (12-hour, no AM/PM), which makes it obvious at a
  glance whether the panel is still being refreshed.

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
"""
import logging
import signal
import sys
import time
from datetime import datetime

import config
import layout
import panel
import ha_client
from alerts import (build_warnings, compute_stale, device_marks, device_status,
                    fit_hidden, merge_device_stale, publish_warnings)
from ha_client import (ha_get, read_charging, read_health, read_hidden,
                       read_message, read_number, read_temp_c, set_hidden)
from panel import full_refresh, push_region, region_snaps, settle_and_sleep
from render import render, render_splash


def fmt_temps(temps):
    return {k: (None if v is None else round(v, 1)) for k, v in temps.items()}


def now_clock():
    """12-hour clock with seconds and no AM/PM, e.g. "2:32:07". The seconds tick
    is the at-a-glance proof that the panel is still being refreshed. %I is
    zero-padded, so strip that leading zero (12 and 10-11 are unaffected)."""
    return datetime.now().strftime("%I:%M:%S").lstrip("0")


def main():
    if panel.epd7in5_V2 is None:
        logging.error(f"Waveshare e-Paper library not available: {panel.EPD_IMPORT_ERROR}")
        panel.diagnose_gpio()
        sys.exit(1)
    if not config.HA_TOKEN:
        logging.warning("HA_TOKEN is empty - all Home Assistant reads will fail")

    epd = panel.epd7in5_V2.EPD()
    stop = {"flag": False}

    def on_term(*_):
        stop["flag"] = True
    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)

    logging.info(f"init + clear  (temp unit: {config.TEMP_UNIT}; "
                 f"speed value+unit come from {config.ENTITIES['speed']})")
    epd.init()
    epd.Clear()

    # Per-entity last_reported timestamps for the staleness check.
    last_iso = {k: None for k in config.STALE_KEYS}

    # A failed HA read returns last_iso=None; never let that overwrite a value's
    # freshness timestamp, or a single transient read error would instantly flag
    # the value stale (and could falsely flip the whole display to "CAN bus not
    # connected"). Only advance the timestamp on a successful read.
    speed, speed_unit, lu = read_number(config.ENTITIES["speed"])
    speed_unit = config.SPEED_UNIT or speed_unit or ""   # option relabels the gauge unit
    if lu is not None:
        last_iso["speed"] = lu
    temps = {k: None for k in ("t_motor", "t_ezk", "t_batt", "t_pi")}
    for k in temps:
        v_c, lu = read_temp_c(config.ENTITIES[k])
        temps[k] = v_c
        if lu is not None:
            last_iso[k] = lu
    soc, _, lu = read_number(config.ENTITIES["soc"])
    if lu is not None:
        last_iso["soc"] = lu
    voltage, voltage_unit, lu = read_number(config.ENTITIES["voltage"])
    if lu is not None:
        last_iso["voltage"] = lu
    if not voltage_unit:
        voltage_unit = "V"
    charging = read_charging(config.ENTITIES["charging"])
    aux_soc = read_number(config.ENTITIES["aux_soc"])[0] if config.AUX_ENABLED else None
    # Reset the MESSAGE box to the configured startup text on every boot, so a
    # note typed during the last run doesn't reappear. Read it back rather than
    # assuming, so a failed service call still shows whatever HA actually holds.
    ha_client.set_message(config.ENTITIES["message"], config.STARTUP_MESSAGE)
    ha_msg = read_message(config.ENTITIES["message"]) or ""
    hidden = read_hidden()
    # CAN connectivity, from the CANbus app's health sensors. Tri-state per
    # entry: True up / False down / None unknown (sensor not published yet) -
    # None falls back to staleness inference inside device_status().
    health = {"bus": read_health(config.ENT_CAN_BUS),
              "batt": read_health(config.ENT_CAN_BATT),
              "ezk": read_health(config.ENT_CAN_EZK),
              "aux": read_health(config.ENT_AUX_STATUS) if config.AUX_ENABLED else None}

    def aux_is_down():
        """Only an EXPLICIT "down" from the aux status sensor counts. A disabled
        aux battery, or the placeholder entity that doesn't exist yet, reads
        unknown and stays silent - no warning, no mark."""
        return config.AUX_ENABLED and health.get("aux") is False

    def current_alerts():
        """(on-screen "!" marks, full warning list). The user message is NOT a
        warning here - it goes to the MESSAGE box.

        Age-based staleness stays internal: it infers which CAN device is off the
        bus and stops a frozen reading raising a "high temp" warning. Only a
        device that is actually down puts a "!" on screen, so a parked car (speed
        stops changing) never lights up the display."""
        stale = compute_stale(last_iso)
        status = device_status(stale, health)
        merged = merge_device_stale(stale, *status)
        return device_marks(*status), build_warnings(
            temps, merged, status, ha_down=ha_client.ha_unreachable(),
            aux_down=aux_is_down())

    def assemble():
        """Compute (stale map, visible warnings) and keep the published HA
        warning list in sync. The hidden-key set is owned and pruned by the
        slow-poll step (sync_hidden) so there is exactly one writer of
        input_text.eink_hidden - this just reads it."""
        nonlocal _pub_sig, _pub_time
        stale, all_ws = current_alerts()
        visible = [w for w in all_ws if w["key"] not in hidden]
        # publish on change, and as a heartbeat every PUBLISH_EVERY seconds so the
        # REST-published sensor reappears within ~30s of a Home Assistant restart
        sig = (tuple(w["key"] for w in all_ws), tuple(sorted(hidden)))
        now = time.time()
        if sig != _pub_sig or (now - _pub_time) >= config.PUBLISH_EVERY:
            publish_warnings(all_ws, hidden)
            _pub_sig = sig
            _pub_time = now
        return stale, visible

    def sync_hidden():
        """Read the authoritative hidden set and rewrite it iff it needs changing:
        drop keys whose warning is no longer active (so a recurrence shows again).
        Reading immediately before writing keeps this the single writer and
        avoids clobbering a Hide the dashboard just applied."""
        nonlocal hidden
        cur = read_hidden()
        target = set(cur)
        _, all_ws = current_alerts()
        active_keys = {w["key"] for w in all_ws}
        target &= active_keys
        target, dropped = fit_hidden(target, all_ws)
        if dropped:
            logging.warning(f"hidden list over the 255-char input_text cap - "
                            f"un-hid lowest-priority keys: {sorted(dropped)}")
        if target != cur:
            set_hidden(target)
        hidden = target

    _pub_sig = None
    _pub_time = 0.0

    stale, visible = assemble()
    clock = now_clock()
    ha_client.refresh_network(force=True)               # first frame has the addresses
    header_lines = ha_client.connection_lines()
    powered = ha_get(config.POWER_TOGGLE)[0] != "off"   # default ON if the toggle is absent
    if powered:
        img = render(speed, speed_unit, temps, soc, voltage, voltage_unit,
                     visible, stale, ha_msg, clock, header_lines, charging, aux_soc,
                     config.AUX_ENABLED, aux_is_down())
        full_refresh(epd, img)            # clean base frame, then partial mode
        logging.info("initial frame drawn")
    else:
        epd.sleep()                       # already cleared above; just sleep the panel
        logging.info("display starts OFF (HA toggle)")

    last_snaps = region_snaps(speed, speed_unit, temps, soc, voltage, visible,
                              stale, ha_msg, clock, charging, aux_soc, aux_is_down())
    refresh_count = 0
    last_slow = time.time()
    last_button, _, _ = ha_get(config.REFRESH_BUTTON)
    awake = powered
    idle_since = time.time()

    try:
        while not stop["flag"]:
            t0 = time.time()

            # HA on/off toggle - clears the panel when switched off
            if ha_get(config.POWER_TOGGLE)[0] == "off":
                if powered:
                    epd.init()
                    epd.Clear()
                    epd.sleep()
                    powered, awake = False, False
                    logging.info("display turned OFF via HA - screen cleared")
                time.sleep(config.SPEED_POLL)
                continue
            turning_on = not powered
            powered = True

            # fast value - speed, every loop (keep prior age on a failed read)
            s, su, lu = read_number(config.ENTITIES["speed"])
            if s is not None:
                speed = s
            speed_unit = config.SPEED_UNIT or su or speed_unit
            if lu is not None:
                last_iso["speed"] = lu

            # slow values - temps / SoC / voltage / message / hidden, every SLOW_POLL seconds
            if t0 - last_slow >= config.SLOW_POLL:
                for k in temps:
                    tv, lu = read_temp_c(config.ENTITIES[k])
                    if tv is not None:
                        temps[k] = tv
                    if lu is not None:
                        last_iso[k] = lu
                sv, _, lu = read_number(config.ENTITIES["soc"])
                if sv is not None:
                    soc = sv
                if lu is not None:
                    last_iso["soc"] = lu
                vv, vu, lu = read_number(config.ENTITIES["voltage"])
                if vv is not None:
                    voltage, voltage_unit = vv, (vu or voltage_unit)
                if lu is not None:
                    last_iso["voltage"] = lu
                charging = read_charging(config.ENTITIES["charging"])
                if config.AUX_ENABLED:        # None when absent -> "AUX --"
                    aux_soc = read_number(config.ENTITIES["aux_soc"])[0]
                    health["aux"] = read_health(config.ENT_AUX_STATUS)
                health["bus"] = read_health(config.ENT_CAN_BUS)
                health["batt"] = read_health(config.ENT_CAN_BATT)
                health["ezk"] = read_health(config.ENT_CAN_EZK)
                m = read_message(config.ENTITIES["message"])
                if m is not None:                 # None = fetch failed; keep last
                    ha_msg = m
                sync_hidden()                     # single writer of eink_hidden
                ha_client.refresh_network()       # router/hotspot IPs, TTL-gated, off-loop
                last_slow = t0

            # manual refresh button forces a full (de-ghosting) refresh
            btn, _, _ = ha_get(config.REFRESH_BUTTON)
            force = btn is not None and btn != last_button
            if btn is not None:
                last_button = btn

            stale, visible = assemble()
            clock = now_clock()
            header_lines = ha_client.connection_lines()

            snaps = region_snaps(speed, speed_unit, temps, soc, voltage, visible,
                                 stale, ha_msg, clock, charging, aux_soc, aux_is_down())
            changed = [r for r in layout.REGIONS if snaps[r] != last_snaps.get(r)]
            data_changed = any(r in layout.DATA_REGIONS for r in changed)

            if data_changed or force or turning_on:
                img = render(speed, speed_unit, temps, soc, voltage, voltage_unit,
                             visible, stale, ha_msg, clock, header_lines, charging,
                             aux_soc, config.AUX_ENABLED, aux_is_down())
                spd_txt = "--" if speed is None else f"{speed:.0f}{speed_unit}"
                if turning_on or not awake or force or refresh_count >= config.FULL_REFRESH_EVERY:
                    full_refresh(epd, img)        # power-on / wake / de-ghost
                    awake = True
                    refresh_count = 0
                    logging.info(f"{'display ON' if turning_on else 'full refresh'} - "
                                 f"speed={spd_txt} "
                                 f"temps={fmt_temps(temps)} soc={soc} warn={len(visible)}")
                else:
                    for r in changed:             # gentle per-region update
                        push_region(epd, img, r)
                        refresh_count += 1
                    logging.info(f"partial {changed} - speed={spd_txt} "
                                 f"(count {refresh_count}/{config.FULL_REFRESH_EVERY})")
                last_snaps = snaps
                idle_since = t0
            elif awake and (t0 - idle_since) >= config.IDLE_SLEEP:
                # no telemetry change for a while - settle the image and sleep
                # the panel (e-paper must not be left powered/active when idle)
                img = render(speed, speed_unit, temps, soc, voltage, voltage_unit,
                             visible, stale, ha_msg, clock, header_lines, charging,
                             aux_soc, config.AUX_ENABLED, aux_is_down())
                settle_and_sleep(epd, img)
                awake = False
                last_snaps = snaps
                logging.info("idle - panel asleep")

            dt = time.time() - t0
            if dt < config.SPEED_POLL:
                time.sleep(config.SPEED_POLL - dt)
    except Exception as e:
        logging.error(f"loop crashed: {e}")
    finally:
        # On shutdown, leave the panel in a clean, intended state and deep-slept,
        # so it isn't caught mid-refresh (showing a garbled frame) when the Pi
        # cuts power. If the display was on, settle to a "POWERED OFF" splash;
        # if it was toggled off via HA, just leave it blank.
        try:
            if powered:
                settle_and_sleep(epd, render_splash())
                logging.info("stopping - shutdown splash shown, panel asleep")
            else:
                epd.sleep()
                logging.info("stopping - display was off, panel asleep")
        except Exception as e:
            logging.error(f"shutdown handling failed: {e}")
            try:
                epd.sleep()
            except Exception:
                pass


if __name__ == "__main__":
    main()
