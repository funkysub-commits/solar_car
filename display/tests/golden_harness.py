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
    alerts module (device_status / merge_device_stale / build_warnings).
    Each kwargs dict carries warnings (the bar), ha_msg (the message box) and
    a stale map - kept separate, mirroring the redesign."""
    temps_ok = {"t_motor": 40.0, "t_ezk": 35.0, "t_batt": 30.0, "t_pi": 48.0}
    temps_hot1 = {**temps_ok, "t_motor": 72.0}
    temps_hot2 = {**temps_ok, "t_motor": 74.0, "t_batt": 68.0}
    temps_none = {k: None for k in temps_ok}
    no_stale = _mkstale(D)
    HEALTH_OK = {"bus": True, "batt": True, "ezk": True}
    HEALTH_UNKNOWN = {"bus": None, "batt": None, "ezk": None}   # sensors absent

    def assess(temps, stale, health, hidden=()):
        """Mirror the main loop: status -> merged stale (internal, for the
        high-temp guard) -> warnings, and the on-screen "!" marks, which come
        from device_marks() so a merely-unchanging value is never marked.
        Returns (marks, visible warning list)."""
        status = A.device_status(stale, health)
        merged = A.merge_device_stale(stale, *status)
        ws = A.build_warnings(temps, merged, status)
        return A.device_marks(*status), [w for w in ws if w["key"] not in set(hidden)]

    def S(name, speed=22, temps=None, soc=78, voltage=58.4, warnings=None,
          stale=None, ha_msg="", clock_str="14:32",
          header_lines=(("Router", "192.168.1.50:8123"),
                        ("Hotspot", "203.0.113.7:8123")), charging=False, aux_soc=87):
        return name, dict(speed=speed, temps=temps if temps is not None else temps_ok,
                          soc=soc, voltage=voltage, warnings=warnings or [],
                          stale=stale if stale is not None else no_stale,
                          ha_msg=ha_msg, clock_str=clock_str,
                          header_lines=list(header_lines), charging=charging,
                          aux_soc=aux_soc)

    # --- nominal -----------------------------------------------------------
    yield S("normal")
    yield S("speed_none", speed=None)
    yield S("msg_only", ha_msg="Box this lap - watch turn 3")
    yield S("msg_long", ha_msg="Pit window opens lap 12. Save battery on the "
            "back straight and watch the kerbs through the chicane, they are bumpy today.")
    # --- single warning + message together --------------------------------
    st, ws = assess(temps_hot1, no_stale, HEALTH_OK)
    yield S("temp_plus_msg", temps=temps_hot1, warnings=ws, stale=st,
            ha_msg="Driver change next stop")
    # --- per-device disconnects (explicit health), scoped "!" marks --------
    st, ws = assess(temps_ok, no_stale, {"bus": False, "batt": True, "ezk": True})
    yield S("adapter_down", warnings=ws, stale=st)        # marks ALL can values
    st, ws = assess(temps_ok, no_stale, {"bus": True, "batt": False, "ezk": True})
    yield S("bestgo_down", warnings=ws, stale=st)         # marks only batt values
    st, ws = assess(temps_ok, no_stale, {"bus": True, "batt": True, "ezk": False})
    yield S("ezk_down", warnings=ws, stale=st)            # marks only ezk values
    # --- all three down: bar fills, overflow '+N' if they don't all fit ----
    st, ws = assess(temps_ok, no_stale, {"bus": False, "batt": False, "ezk": False})
    yield S("all_can_down", warnings=ws, stale=st)
    # --- forced overflow: more warnings than fit -> '+N' pill --------------
    st = _mkstale(D, ("t_pi",))
    overflow_ws = A.build_warnings({**temps_hot2, "t_pi": 95.0}, st,
                                   (False, False, False)) + [
        {"key": "x", "text": "EZkontrol disconnected", "priority": 95, "icon": "warn"},
        {"key": "y", "text": "BESTGO disconnected", "priority": 96, "icon": "warn"},
        {"key": "z", "text": "CAN adapter disconnected", "priority": 100, "icon": "warn"}]
    overflow_ws.sort(key=lambda w: -w["priority"])
    # no device is down, so nothing is marked - a stale-but-connected t_pi
    # no longer earns a "!" (it only suppresses its own high-temp warning above)
    yield S("overflow", warnings=overflow_ws, stale=A.device_marks(False, False, False))
    # --- HA unreachable ----------------------------------------------------
    # every value stale + health unknown => the adapter is inferred down, which
    # is what marks the CAN values (the Pi temp stays unmarked).
    st = _mkstale(D, D.STALE_KEYS)
    ws = A.build_warnings(temps_ok, st, (False, False, False), ha_down=True)
    yield S("ha_down", warnings=ws, stale=A.device_marks(True, False, False),
            header_lines=[("", "Pi Offline")])
    # --- inference fallback (health sensors absent) ------------------------
    st, ws = assess(temps_none, _mkstale(D, D.STALE_KEYS), HEALTH_UNKNOWN)
    yield S("can_down_inferred", speed=None, temps=temps_none, soc=None,
            voltage=None, warnings=ws, stale=st)
    # --- hide: a warning hidden from the bar -------------------------------
    st, ws = assess(temps_hot1, no_stale, HEALTH_OK, hidden=("temp_t_motor",))
    yield S("hidden", temps=temps_hot1, warnings=ws, stale=st)
    # --- SoC boundaries ----------------------------------------------------
    yield S("soc_0", speed=0, soc=0, voltage=42.0, clock_str="23:59")
    yield S("soc_15", speed=38.6, soc=15, voltage=46.1, clock_str="00:00")
    yield S("soc_100", soc=100, voltage=None, clock_str="09:05")
    # --- charging: lightning bolt over the battery icon --------------------
    yield S("charging", speed=0, soc=64, charging=True)
    # --- aux battery: placeholder entity absent -> "AUX --" ----------------
    yield S("aux_missing", aux_soc=None)


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
                       kw["warnings"], kw["stale"], kw["ha_msg"], kw["clock_str"],
                       kw["header_lines"], kw["charging"], kw["aux_soc"])
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
