#!/usr/bin/env python3
"""Golden-image harness for the e-ink display add-on (Phase 3 safety net).

Renders a fixed matrix of synthetic telemetry states through the add-on's
render() and hashes the raw 1-bit framebuffer of each frame. Run it before a
refactor to record the hashes, and after to prove the output is byte-identical:

    python golden_harness.py write     # render + overwrite golden/hashes.txt + PNGs
    python golden_harness.py check     # render + compare against golden/hashes.txt

Hashes cover Image.tobytes() (the raw framebuffer), not the PNG encoding, so
they are stable across Pillow's PNG writer. They DO depend on the Pillow
text-rasteriser and the committed DejaVu fonts in tests/fonts/, so regenerate
the baseline on the same machine/Pillow you'll verify on (record shows
Pillow 11.3.0). The team logo is rendered from the real addon/logo.png.

Because the add-on reads its configuration from environment variables at
import time, each config group is rendered in a fresh subprocess (`--group`)
with the right env, and the parent just collects the results.
"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ADDON = HERE.parent / "addon"
GOLDEN = HERE / "golden"
HASHES = GOLDEN / "hashes.txt"

# Environment shared by every group: deterministic fonts + the real logo.
BASE_ENV = {
    "FONT_DIR": str(HERE / "fonts"),
    "LOGO_PATH": str(ADDON / "logo.png"),
    "TITLE": "SOLAR STORMS",
}

# Config groups: name -> (extra env, speedometer unit label). The add-on
# parses env at import time; the unit label is passed straight to render()
# the same way the main loop passes the HA entity's unit_of_measurement.
GROUPS = {
    "default": ({}, "mph"),                          # defaults, deg C
    "fahrenheit": ({"TEMP_UNIT": "F", "TEMP_MAX": "176", "TEMP_WARN": "149"}, "km/h"),
    "highscale": ({"SPEED_MAX": "3000"}, "rpm"),     # rpm-style full scale
}


def _mkstale(D, keys=()):
    return {k: (k in keys) for k in D.STALE_KEYS}


def scenarios(D, A):
    """Yield (name, kwargs-for-render). D = config-bearing module, A = the
    alerts module (device_status / merge_device_stale / build_warnings)."""
    temps_ok = {"t_motor": 40.0, "t_ezk": 35.0, "t_batt": 30.0, "t_pi": 48.0}
    temps_hot1 = {**temps_ok, "t_motor": 72.0}
    temps_hot2 = {**temps_ok, "t_motor": 74.0, "t_batt": 68.0}
    temps_none = {k: None for k in temps_ok}
    no_stale = _mkstale(D)
    all_stale = _mkstale(D, D.STALE_KEYS)
    HEALTH_OK = {"bus": True, "batt": True, "ezk": True}
    HEALTH_UNKNOWN = {"bus": None, "batt": None, "ezk": None}   # sensors absent

    def assess(temps, stale, health, msg, hidden=()):
        """Mirror the main loop's pipeline: status -> merged stale -> warnings.
        Returns (merged stale map, visible warning list)."""
        status = A.device_status(stale, health)
        merged = A.merge_device_stale(stale, *status)
        ws = A.build_warnings(temps, merged, status, msg)
        return merged, [w for w in ws if w["key"] not in set(hidden)]

    yield "normal", dict(speed=22, temps=temps_ok, soc=78, voltage=58.4,
                         warnings=[], stale=no_stale, clock_str="14:32")
    yield "speed_none", dict(speed=None, temps=temps_ok, soc=78, voltage=58.4,
                             warnings=[], stale=no_stale, clock_str="14:32")
    st, ws = assess(temps_hot1, no_stale, HEALTH_OK, "")
    yield "hot_one", dict(speed=22, temps=temps_hot1, soc=78, voltage=58.4,
                          warnings=ws, stale=st, clock_str="14:32")
    st, ws = assess(temps_hot2, _mkstale(D, ("voltage",)), HEALTH_OK,
                    "Pit stop in 2 laps - watch turn 3")
    yield "multi_badge", dict(speed=22, temps=temps_hot2, soc=78, voltage=58.4,
                              warnings=ws, stale=st, clock_str="14:32")
    st, ws = assess(temps_ok, all_stale, HEALTH_UNKNOWN, "")
    yield "can_down", dict(speed=22, temps=temps_ok, soc=78, voltage=58.4,
                           warnings=ws, stale=st, clock_str="14:32")
    st, ws = assess(temps_none, all_stale, HEALTH_UNKNOWN, "")
    yield "can_down_empty", dict(speed=None, temps=temps_none, soc=None, voltage=None,
                                 warnings=ws, stale=st, clock_str="14:32")
    # device-level outages from the explicit health sensors: only the values
    # fed by the down device get the "!" mark
    st, ws = assess(temps_ok, no_stale, {"bus": True, "batt": False, "ezk": True}, "")
    yield "batt_down", dict(speed=22, temps=temps_ok, soc=78, voltage=58.4,
                            warnings=ws, stale=st, clock_str="14:32")
    st, ws = assess(temps_ok, no_stale, {"bus": True, "batt": True, "ezk": False}, "")
    yield "ezk_down", dict(speed=22, temps=temps_ok, soc=78, voltage=58.4,
                           warnings=ws, stale=st, clock_str="14:32")
    st, ws = assess(temps_ok, no_stale, {"bus": True, "batt": False, "ezk": False}, "")
    yield "both_down", dict(speed=22, temps=temps_ok, soc=78, voltage=58.4,
                            warnings=ws, stale=st, clock_str="14:32")
    # HA itself unreachable: one accurate warning instead of bogus CAN noise
    st = _mkstale(D, D.STALE_KEYS)        # everything ages out during an outage
    ws = A.build_warnings(temps_ok, st, (False, False, False), "", ha_down=True)
    yield "ha_down", dict(speed=22, temps=temps_ok, soc=78, voltage=58.4,
                          warnings=ws, stale=st, clock_str="14:32")
    st, ws = assess(temps_ok, no_stale, HEALTH_OK, "Box this lap")
    yield "user_msg", dict(speed=22, temps=temps_ok, soc=78, voltage=58.4,
                           warnings=ws, stale=st, clock_str="14:32")
    st, ws = assess(temps_ok, no_stale, HEALTH_OK,
                    "A very long message that cannot possibly fit "
                    "in the little notification chip and must be "
                    "ellipsized cleanly at the right edge")
    yield "msg_ellipsis", dict(speed=22, temps=temps_ok, soc=78, voltage=58.4,
                               warnings=ws, stale=st, clock_str="14:32")
    st, ws = assess(temps_hot1, no_stale, HEALTH_OK, "", hidden=("temp_t_motor",))
    yield "hidden_all", dict(speed=22, temps=temps_hot1, soc=78, voltage=58.4,
                             warnings=ws, stale=st, clock_str="14:32")
    yield "soc_0", dict(speed=0, temps=temps_ok, soc=0, voltage=42.0,
                        warnings=[], stale=no_stale, clock_str="23:59")
    yield "soc_15", dict(speed=38.6, temps=temps_ok, soc=15, voltage=46.1,
                         warnings=[], stale=no_stale, clock_str="00:00")
    yield "soc_100", dict(speed=22, temps=temps_ok, soc=100, voltage=None,
                          warnings=[], stale=no_stale, clock_str="09:05")


def render_group(group):
    """Child mode: import the add-on code under this group's env (already set
    by the parent) and render every scenario. Emits 'name<TAB>sha256' lines."""
    sys.path.insert(0, str(ADDON))
    import display as D                       # noqa: E402
    # After the Phase 3 split these live in their own modules; before it they
    # are all attributes of display. Resolve with fallbacks so the same harness
    # proves the refactor.
    try:
        import render as R
    except ImportError:
        R = D
    try:
        import alerts as A
    except ImportError:
        A = D
    try:
        import config as C
    except ImportError:
        C = D
    if not hasattr(D, "STALE_KEYS"):          # post-split: config owns the keys
        D = C

    GOLDEN.mkdir(parents=True, exist_ok=True)
    unit = GROUPS[group][1]
    for name, kw in scenarios(D, A):
        img = R.render(kw["speed"], unit, kw["temps"], kw["soc"], kw["voltage"], "V",
                       kw["warnings"], kw["stale"], kw["clock_str"])
        digest = hashlib.sha256(img.tobytes()).hexdigest()
        img.save(GOLDEN / f"{group}_{name}.png")
        print(f"{group}/{name}\t{digest}")


def run_all():
    """Parent mode: run every group in a fresh subprocess, return dict."""
    out = {}
    for group, (extra, _unit) in GROUPS.items():
        env = {**os.environ, **BASE_ENV, **extra}
        r = subprocess.run([sys.executable, __file__, "--group", group],
                           env=env, capture_output=True, text=True)
        if r.returncode != 0:
            sys.stderr.write(r.stdout + r.stderr)
            raise SystemExit(f"group {group} failed")
        for line in r.stdout.strip().splitlines():
            name, digest = line.split("\t")
            out[name] = digest
    return out


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    if mode == "--group":
        render_group(sys.argv[2])
        return
    hashes = run_all()
    if mode == "write":
        HASHES.write_text("".join(f"{k}\t{v}\n" for k, v in sorted(hashes.items())))
        print(f"wrote {len(hashes)} golden hashes -> {HASHES}")
    elif mode == "check":
        want = dict(line.split("\t") for line in
                    HASHES.read_text().strip().splitlines())
        bad = {k for k in want if hashes.get(k) != want[k]}
        missing = set(want) - set(hashes)
        extra = set(hashes) - set(want)
        if bad or missing or extra:
            for k in sorted(bad):
                print(f"MISMATCH {k}")
            for k in sorted(missing):
                print(f"MISSING  {k}")
            for k in sorted(extra):
                print(f"EXTRA    {k} (not in baseline)")
            raise SystemExit(1)
        print(f"all {len(want)} golden hashes match")
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
