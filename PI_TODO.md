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
- [ ] **Pi zero-RX root cause — STILL OPEN (gs_usb-regression theory
      OVERTURNED 2026-06-25).** The 2026-06-18 conclusion was a **kernel
      gs_usb / hardware-timestamp regression** (full arc:
      `CANbus_data/docs/DEBUG-pi-rx-plan-20260613.md`). That is now **wrong**:
      the adapter was reflashed to slcan (a different USB stack that never
      touches gs_usb) and the Pi **still gets 0 RX** (2026-06-25), so the
      gs_usb driver cannot be the cause. The fault is host-specific and shared
      by both USB paths — leading suspects: the **HAOS kernel/USB stack**, the
      **Pi-4 VL805 USB host controller**, or python-can's slcan backend. Still
      ruled out: electrical, ground (isolated), wiring, termination, bitrate,
      software (two independent stacks fail), port contention, undervoltage.
      ⚠️ The "worked on the Pi 2026-05-30" baseline was **never logged** (no
      candump/sensor/kernel capture — only a verbal "it's working"; the
      `uname` turn was interrupted), so even the regression *premise* (older
      kernel worked) has no data. Full writeup + next experiments (can-utils,
      powered hub, fresh Pi OS, MCP2515 SPI HAT, 2nd-adapter loopback):
      **`CANbus_data/docs/DEBUG-pi-rx-slcan-20260624.md`**.
- [x] ~~Reflash adapter to slcan firmware~~ — DONE 2026-06-18. The SH-C31G is
      now on **slcan** (CDC-serial), not gs_usb; DSD TECH's documented Linux
      route. **NOTE:** this did *not* fix the Pi — slcan RX on the Pi is also 0
      (2026-06-25). It sidesteps the gs_usb *driver*, but the real fault is
      upstream of it (host USB stack / VL805). The reflash is still fine to
      keep; it just isn't the cure it was expected to be.
- [x] ~~Build the slcan software~~ — DONE 2026-06-19 (commit, not yet
      deployed). `SlcanTransport` + `find_slcan_port` + `CAN_TRANSPORT`
      selector (default slcan) in transport.py; PC CLIs unchanged (use the
      abstraction); add-on **0.8.0** (can_reader opens slcan/serial + auto-
      detects port, simplified run.sh, `uart: true`, `can_port` option,
      dropped NET_ADMIN/can_interface, +pyserial). gs_usb/socketcan kept for
      reflash-back. Compiles, golden tests 7/7, dummy modes OK, transport
      selector + error paths verified. NOT yet run against real hardware.
- [x] ~~Step 0 — validate slcan RX on the Pi~~ — DONE 2026-06-25, **FAILED**:
      `recv(timeout=5)` returned None / 0 frames (incl. on a USB-3 port). Same
      adapter decoded 86–92 frames on the laptop, so the slcan *software* is
      good — the **Pi receive path is broken regardless of firmware**.
- [x] ~~PC slcan test~~ — DONE 2026-06-25: laptop `monitor.py`/BestgoDecoder on
      `COM5` decoded the battery cleanly (SOC 54%, ~52.4 V, 14 IDs). The slcan
      0.8.0 code path is validated end-to-end off the Pi.
- [ ] **Find the Pi zero-RX cause (the real blocker).** slcan deploy is
      pointless until the Pi can receive at all. Full tree + commands in
      `CANbus_data/docs/DEBUG-pi-rx-slcan-20260624.md`:
      - **T0 (do first): fresh SD, latest HAOS, slcan smoke test.** A clean
        install wipes the debugging cruft (unbound driver, 0.7.0 add-on's
        uhubctl power-cycling). Install only Advanced SSH & Web Terminal
        (**Protection Mode OFF** — else no Docker/`/dev`), no CAN add-on;
        confirm adapter = slcan (`16d0:117e`, `/dev/ttyACM0`); battery-only bus
        with adapter 120 Ω **ON** (~60 Ω); then a throwaway `python:3.12-slim`
        container (`--device=/dev/ttyACM0`) running
        `can.Bus(interface='slcan', ...).recv(timeout=5)`. Frame ⇒ it was
        cruft → rebuild clean + **image the SD**. None ⇒ T1 (latest ≈ same
        kernel, so this clears "cruft" but not "kernel").
        - **PRE-STAGED 2026-06-26** on the clean Pi (`192.168.0.243`, kernel
          **6.18.33-haos-raspi**, adapter slcan `16d0:117e`→`/dev/ttyACM0`,
          Protection Mode OFF, no CAN add-on): offline image `slcan-smoketest`
          (python-can+pyserial baked in) built, and `/config/slcan_smoketest.sh`
          staged (repo copy: `CANbus_data/tools/slcan_smoketest.sh`). **Dry-run
          verified** the full path opens the bus + reads cleanly (0 frames, no
          battery yet). SSH user is non-root `hassio` → the script uses
          `sudo docker` (works). **At the shop: connect battery (term ON ~60 Ω)
          → `bash /config/slcan_smoketest.sh`** → prints the BESTGO IDs or 0.
        - **RAN 2026-06-26: T0 FAILED (confound-free).** Pi = **0 frames**;
          same adapter moved to the laptop on the same bus/battery = **156
          frames, all 14 IDs**. Clean install on the newest kernel still gets
          0 → **cruft + battery ruled out**. Cause is Pi-host-specific on latest
          HAOS → do **T1 (older HAOS)** next.
      - **T1: older HAOS** on a spare SD (verify pre-6.12 `uname -r`) — tests
        the kernel-regression theory. Frame ⇒ kernel; None ⇒ VL805 hardware.
        - **IN PROGRESS 2026-06-27:** rolled back in-place (`ha os update
          --version 15.0`) → Pi now on **kernel 6.6.74** (HAOS 15.0, slot B;
          18.0 kept on slot A as fallback). The downgrade **wiped the Docker
          image** (rebuilt it) but `/config/slcan_smoketest.sh` survived.
          **Pending at the shop:** recover the adapter from DFU (`0483:df11` —
          BOOT-switch gremlin) + connect battery → `bash
          /config/slcan_smoketest.sh`. Independent LLM review endorsing this +
          adding usbmon/powered-hub follow-ups: `docs/USB-CAN-RPi4-HAOS-debug-plan.md`.
      - **T2: powered USB hub** (ordered) — VL805 under-power.
      - **T3: MCP2515 SPI CAN HAT** — bypasses USB; race-day fallback.
      Highest-leverage buy: a **2nd USB-CAN adapter** for confound-free loopback.
- [ ] **Once the Pi can receive:** deploy add-on 0.8.0 — push
      `solar-car-canbus/` (incl. vendored `solarcar_can`) → rebuild →
      `ha apps update local_solarcar_canbus` → start → verify
      `sensor.bestgo_*`/`sensor.ezkontrol_*` + health sensors update.
      ⚠️ **EZkontrol is on protocol 1 (250k) from debugging — set it BACK to
      101 (500k)** before the real shared bus. Add-on currently STOPPED.
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

## Race prep — "update once before, then freeze" (README §8 reworked 2026-06-18)

Race rule was REWORKED: the old "keep the Supervisor updated during the race"
advice is gone — updating a working system mid-race is a risk, and you can't
rebuild apps without internet anyway. New rule: get current before, freeze at
the track.

- [ ] A few days before, on solid internet: let the HA Supervisor (the HAOS
      software, not a person) get current (or `ha supervisor update`), then
      verify both apps start and push sensors.
- [ ] At the track: **freeze** — don't update HAOS / Supervisor / apps.
- [ ] Only exception: if the **e-ink or CANbus app must be rebuilt mid-race**,
      a stale Supervisor blocks it (*"supervisor needs to be updated first"*),
      so you'd need `ha supervisor update` first — needs the hotspot up. Doing
      the pre-race update is what avoids this. See README §8.

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
