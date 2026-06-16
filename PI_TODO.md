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

- [x] ~~Copy the updated add-on to the Pi~~ — done 2026-06-11 via `ha_push.py`
      (incl. `solarcar_can/`).
- [x] ~~Rebuild~~ — done 2026-06-11. Required `ha supervisor update` first
      (an outdated Supervisor blocks all store operations), then
      `ha store reload` + `ha apps update local_solarcar_canbus`.
- [x] ~~Verify the new code runs~~ — done 2026-06-11 in **dummy mode**: all
      34 sensors push (13 ezkontrol + 21 bestgo), `sensor.ezkontrol_op_mode`
      reads `Normal`. Options reverted to live mode; add-on left **stopped**.
- [x] ~~Live BESTGO decode through the new code~~ — verified **on the PC**
      2026-06-12: real battery decodes correctly through the shared
      `solarcar_can` package (SOC 54%, pack 52.5 V, cells 3281-3282 mV @ 1 mV
      spread, all internally consistent), 176 frames across all 14 IDs at
      ~14 Hz. First real-hardware decode through the refactored package.
- [x] ~~Full shared-bus decode (both devices, one adapter)~~ — PASSED on the
      **PC** 2026-06-13: `monitor.py` decoded EZkontrol (22 Hz) and BESTGO
      (11 Hz) simultaneously, clean, no errors, bus voltages agree. This is
      the real race configuration. **Termination finding:** with BOTH devices
      connected, the adapter's 120R switch should be **OFF** (the EZkontrol
      and battery terminate the two ends) — verified working that way.
- [ ] **Pi zero-RX = asymmetric RX failure (2026-06-13). NOT ground, NOT
      termination, NOT bitrate.** Smoke test (both devices, EZkontrol 20 Hz
      continuous — no battery-silence confound): Pi `can0` healthy (500k,
      ERROR-ACTIVE, **0 RX, 0 errors**) but hears nothing, while the same
      adapter+bus decoded both on the PC minutes earlier.
      **TX-ACK test overturns the old `DEBUG-pi-can-rx-20260608.md` ground
      theory:** `cansend can0 100#..` from the Pi → tx_packets 0→1, **0
      errors, ERROR-ACTIVE**. A clean TX means a device ACKed it AND the Pi
      read the ACK bit back — so the adapter IS electrically on the bus and
      its RX works at the bit level. Not common-mode/ground (would be
      symmetric; TX would fail). So: can transmit-and-get-ACKed but receives
      0 whole frames = RX-specific. Prime suspects: bit-timing sync to
      externally-initiated frames, or a driver/firmware RX bug. (The old
      "forced PC timing still 0 RX" was confounded — battery-only, likely
      bus-off/silent.)
      **UPDATE 2026-06-15 (session 2) — DON'T trust today's 0-RX yet; the car
      power kept getting switched OFF.** EZkontrol only broadcasts when the car
      is on, so some "0 RX" reads were a QUIET BUS, not a dead receiver. Also:
      the adapter is **galvanically ISOLATED** → ground/common-mode/earth-loop
      theories are OUT (the earlier "it's electrical/ground" call is wrong).
      Termination ON, USB3 port, splitter removed → none changed anything.
      **RESUME (decisive, do first):** car ON *and confirmed on*, EZkontrol at
      250k alone → move adapter to the **PC**, run `python ezkontrol_decode.py
      -250`. PC sees frames ⇒ it IS broadcasting ⇒ real Pi receive-but-discard
      problem (chase FDCAN filter/firmware). PC sees nothing ⇒ not broadcasting
      ⇒ Pi was never the problem (power-cycle EZkontrol). Full arc +
      reasoning: `CANbus_data/docs/DEBUG-pi-rx-plan-20260613.md` (SESSION 2).
      ⚠️ **EZkontrol is currently at protocol 1 (250k) — set it BACK to 101
      (500k)** before rejoining the shared bus with the battery. Battery
      unplugged, splitter removed, adapter term ON. gsusb test unbound the
      kernel driver → `can0` gone until replug/reboot; addon still STOPPED.
- [ ] Check HA automations/dashboards for numeric comparisons against
      `sensor.ezkontrol_op_mode` (now `"Normal"/"Cruise"/"EBS"/"Hold"`,
      was `0/2/3/4`) and update any found.
- [x] ~~E-ink display recheck~~ — done 2026-06-11: its `error` state was the
      OLD pre-add-on `epaper-display` container resurrecting on boot and
      holding the GPIO lines (`Errno 16 Resource busy`). That container is
      now `docker rm`'d for good. App verified end-to-end: simulator →
      HA → panel partial refreshes (speed/batt/temps/clock regions).

## When the USB-CAN adapter is back (no battery/controller needed)

- [x] ~~PC transport test~~ — done 2026-06-11: adapter opened (after a
      DFU-mode replug — the BOOT switch strikes again), `monitor.py` /
      `bestgo_decode.py` (incl. ASC log) / mixed live+dummy all work on
      the empty bus.
- [ ] Pi: plug it into the Pi and start `local_solarcar_canbus` — run.sh
      should bring up can0 and the app should idle without frames. Then
      the CLI tools over SocketCAN (`./can_up.sh`, `python monitor.py`)
      per CANbus_data/README.md.

## Deploy + verify e-ink add-on 1.3.0 (Phase 3 refactor + speed/CAN-health rework)

- [ ] Copy `display/addon/` to the Pi's `/addons/solar-epaper/`, rebuild,
      start. Options to update after the rebuild: the three `ent_can_*`
      health sensors are PLACEHOLDER ids until the canbus app publishes real
      ones; `speed_unit`/`wheel_diameter_in`/`gear_ratio` are gone.
- [ ] Apply `display/ha/eink_messages.yaml` as a package (creates
      `input_text.eink_hidden`, hide scripts, and `sensor.solar_car_speed` -
      the rpm->mph template sensor) and set the add-on's `ent_speed` to
      `sensor.solar_car_speed` for mph on the gauge.
- [ ] Add the `display/ha/dashboard_messages_section.yaml` section to the
      dashboard (now includes Hide buttons for the new `can_batt`/`can_ezk`
      device warnings).
- [ ] Verify on the panel: toast + count badge, per-device "!" marks (battery
      vs EZkontrol), hide round-trip from the dashboard.
- [x] ~~CANbus app: publish the three CAN health sensors~~ — already published
      by canbus 0.7.0 (`sensor.canadapter_status` / `bestgo_status` /
      `ezkontrol_status`, 1/0). The display add-on now defaults `ent_can_*` to
      these real ids (no longer placeholders).

## Still outstanding from before the refactor

- [x] ~~EZkontrol live decode~~ — verified **on the PC** 2026-06-13 (first
      time ever): both frames decode at 500k (controller is already on
      EZ-Tune protocol 101, no change needed). 52.0 V bus matches the
      battery, gear D2 / contactor on / mode Normal / no errors, life
      counter in 0x180217EF byte 7 increments 0->F (proves liveness).
- [ ] EZkontrol live decode on the **Pi** still unconfirmed (same open
      question as BESTGO — see the zero-RX note above). `candump can0`
      should show `180117EF`/`180217EF`, then `sensor.ezkontrol_*` updates.

## E-ink: show network status (future — display code change)

- [ ] The add-on now publishes `sensor.haos_ip_address`,
      `sensor.network_status`, `binary_sensor.lan_connected`, and
      `binary_sensor.wan_connected`. To surface them on the e-ink (e.g. a
      footer line "HA 10.66.76.162  LAN● WAN●"), `display/addon/display.py`
      needs a small render addition + new `ent_*` options. Fits naturally
      into the Phase 3 display refactor (`display/PHASE3_PLAN.md`); until
      then the sensors are visible on the HA dashboard.

## Race prep (a few days before, on a network with internet)

- [ ] Power the Pi up early and let the HA Supervisor update (or run
      `ha supervisor update`). A stale Supervisor blocks all app
      install/update/rebuild operations until it's current (hit this
      2026-06-11) — running apps are unaffected, but don't discover that
      during an emergency fix at the track. See README §8 "Supervisor
      staleness" for details.
- [ ] After it's current: verify both apps start and push sensors.
- [ ] During the race: whenever the hotspot is up, keep the Supervisor
      updated (`ha supervisor info` / `ha supervisor update`, ideally while
      the car is stopped) so an emergency app fix never has to wait on a
      Supervisor update first.

## Optional / nice-to-have on the Pi

- [ ] Try the unified CLI tools over SocketCAN: copy `CANbus_data/` to the Pi,
      `pip install -r requirements-pi.txt` in a venv, `./can_up.sh`, then
      `python monitor.py` (see `CANbus_data/README.md`). The old `rp_files/`
      copies these replace were Pi-tested; the unified ones are PC-tested +
      golden-tested but not yet run on the Pi.

## Anytime (no Pi needed)

- [x] ~~Push `main` to GitHub~~ — done 2026-06-09 (`6f9faa0..814e1f8`).
- [ ] Set the repo git identity if desired:
      `git config user.name "..."` / `git config user.email funkysub@gmail.com`
      (current commits are auto-attributed to the Windows account).
