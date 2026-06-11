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
  Header        : team logo + title + clock
  Left          : analog speedometer gauge (value + unit exactly as the
                  configured HA entity reports them - no conversion here)
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
"""
import logging
import signal
import sys
import time
from datetime import datetime

import config
import layout
import panel
from alerts import build_warnings, compute_stale, publish_warnings
from ha_client import (entity_age_seconds, ha_get, read_hidden, read_message,
                       read_number, read_temp_c, set_hidden)
from panel import full_refresh, push_region, region_snaps, settle_and_sleep
from render import render


def fmt_temps(temps):
    return {k: (None if v is None else round(v, 1)) for k, v in temps.items()}


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
    speed_unit = speed_unit or ""
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
    ha_msg = read_message(config.ENTITIES["message"])
    hidden = read_hidden()

    def can_all_stale():
        return all(last_iso[k] is None
                   or entity_age_seconds(last_iso[k]) > config.STALE_AGE
                   for k in config.CAN_KEYS)

    def assemble():
        """Compute (stale map, visible warnings) and keep the published HA
        warning list in sync. The hidden-key set is owned and pruned by the
        slow-poll step (sync_hidden) so there is exactly one writer of
        input_text.eink_hidden - this just reads it."""
        nonlocal _pub_sig, _pub_time
        stale = compute_stale(last_iso)
        all_ws = build_warnings(temps, stale, can_all_stale(), ha_msg)
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

    stale, visible = assemble()
    clock = datetime.now().strftime("%H:%M")
    powered = ha_get(config.POWER_TOGGLE)[0] != "off"   # default ON if the toggle is absent
    if powered:
        img = render(speed, speed_unit, temps, soc, voltage, voltage_unit, visible, stale, clock)
        full_refresh(epd, img)            # clean base frame, then partial mode
        logging.info("initial frame drawn")
    else:
        epd.sleep()                       # already cleared above; just sleep the panel
        logging.info("display starts OFF (HA toggle)")

    last_snaps = region_snaps(speed, speed_unit, temps, soc, voltage, visible, stale, clock)
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
            if su:
                speed_unit = su
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
                prev_msg = ha_msg
                ha_msg = read_message(config.ENTITIES["message"])
                sync_hidden(ha_msg != prev_msg)   # single writer of eink_hidden
                last_slow = t0

            # manual refresh button forces a full (de-ghosting) refresh
            btn, _, _ = ha_get(config.REFRESH_BUTTON)
            force = btn is not None and btn != last_button
            if btn is not None:
                last_button = btn

            stale, visible = assemble()
            clock = datetime.now().strftime("%H:%M")

            snaps = region_snaps(speed, speed_unit, temps, soc, voltage, visible, stale, clock)
            changed = [r for r in layout.REGIONS if snaps[r] != last_snaps.get(r)]
            data_changed = any(r in layout.DATA_REGIONS for r in changed)

            if data_changed or force or turning_on:
                img = render(speed, speed_unit, temps, soc, voltage, voltage_unit, visible, stale, clock)
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
                img = render(speed, speed_unit, temps, soc, voltage, voltage_unit, visible, stale, clock)
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
        logging.info("stopping - panel to sleep")
        try:
            if awake:
                epd.sleep()
        except Exception:
            pass


if __name__ == "__main__":
    main()
