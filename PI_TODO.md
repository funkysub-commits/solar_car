# Running to-do list — next time the Raspberry Pi is powered up

Keep this list current: check items off / delete them when done, add new ones
as work on the PC piles up changes that need the Pi.

## Security (from Phase 0 — tokens are revoked-on-paper only until this is done)

- [ ] Revoke **both** HA long-lived tokens: HA profile (bottom-left) →
      Security → Long-Lived Access Tokens → delete all listed tokens.
      (Two different tokens were committed to GitHub — one in the old README,
      one in `CANbus_data/HA_TOKEN.txt`. Both are still valid until deleted
      here. Nothing in the add-ons uses them; only PC-side scripts, which
      read a fresh token from the `HA_TOKEN` env var.)
- [ ] Change the `sct` HA user's password (was also committed).
- [ ] If the PC scripts (`ha_push.py` / simulator) are still needed, generate
      one new token and set it as `HA_TOKEN` in the PC environment — do not
      write it to a file in the repo.

## Deploy + verify CANbus add-on 0.4.0 (Phase 2 consolidation)

- [ ] Copy the updated add-on to the Pi: `CANbus_data/ha_addons/solar-car-canbus/`
      → `/addons/solar-car-canbus/`. **Must include the new `solarcar_can/`
      subfolder** (vendored decoder package) — the build fails without it.
- [ ] Rebuild + restart: `ha apps rebuild local_solarcar_canbus` then
      `ha apps restart local_solarcar_canbus` (or uninstall/reinstall from
      Settings → Apps → Local apps if rebuild doesn't pick up the version bump
      to 0.4.0).
- [ ] Check logs: `ha apps logs local_solarcar_canbus` — expect the usual
      startup lines and periodic `BESTGO: V=... SOC=...%` summaries.
- [ ] Verify BESTGO sensors still update (Developer Tools → States,
      `sensor.bestgo_pack_voltage`, `sensor.bestgo_soc`, ...). This is the
      proven-working path — it must behave exactly as before.
- [ ] `sensor.ezkontrol_op_mode` now reads `"Normal"/"Cruise"/"EBS"/"Hold"`
      instead of `0/2/3/4` (intentional). Check HA automations/dashboard
      conditions for numeric comparisons against it and update any found.
- [ ] Glance at the e-ink display — it reads the same sensors, so it should be
      unaffected, but confirm after the add-on rebuild.

## Still outstanding from before the refactor

- [ ] EZkontrol live decode has **never** been tested on the Pi. With the
      motor controller wired to the shared 500K bus (EZ-Tune protocol = 101):
      `candump can0` should show `180117EF`/`180217EF` frames, then check the
      `sensor.ezkontrol_*` entities update.

## Optional / nice-to-have on the Pi

- [ ] Try the unified CLI tools over SocketCAN: copy `CANbus_data/` to the Pi,
      `pip install -r requirements-pi.txt` in a venv, `./can_up.sh`, then
      `python monitor.py` (see `CANbus_data/SETUP.md`). The old `rp_files/`
      copies these replace were Pi-tested; the unified ones are PC-tested +
      golden-tested but not yet run on the Pi.

## Anytime (no Pi needed)

- [ ] Push `main` to GitHub (commits `13159d9`, `ebdf292`, … are local-only).
- [ ] Set the repo git identity if desired:
      `git config user.name "..."` / `git config user.email funkysub@gmail.com`
      (current commits are auto-attributed to the Windows account).
