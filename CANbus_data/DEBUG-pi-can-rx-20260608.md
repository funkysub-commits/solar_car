# Debug capture — BESTGO CAN works on PC, 0 frames on the Pi (2026-06-08)

## TL;DR
The BESTGO battery transmits fine at 500 kbps (14 IDs, decodes correctly). The
**PC receives it reliably; the Raspberry Pi receives 0 frames** with the *same*
adapter and *same* wiring. Both the kernel SocketCAN path and a new userspace
`gs_usb` path fail identically on the Pi (adapter opens, reads cleanly, hears
nothing). **The fault is Pi-side and electrical** — leading hypothesis: a missing
common CAN ground / 0 V reference between the adapter and the battery (a floating
laptop tolerates an H/L-only tap; a separately-grounded Pi does not). Not fixed yet.

## Setup
- HAOS `10.126.155.163:8123`, core 2026.5.3. SSH via `ha_run.py`/`ha_push.py`
  (paramiko, user `hassio`, run with the repo venv python).
- Adapter: DSD TECH SH-C31G = CANable2, STM32G431 **FDCAN**, USB `1d50:606f`,
  `fclk_can = 170 MHz`.
- Bus: 500 kbps. Battery: Lithium Valley BMS (`LVaiiey`), SOC ~54 %, ~52.6 V.
  14 standard IDs at ~1 s period: 0x351 0x355 0x356 0x35A 0x35E 0x35F 0x370 0x371
  0x373 0x374 0x375 0x376 0x377 0x379.
- Reader add-on `local_solarcar_canbus` (v0.3.0) was **stopped** during testing.

## What was tested and found

### PC (userspace gs_usb, explicit bit-timing) — WORKS, repeatably
`pc_files/bestgo_probe.py`: locks on 500 kbps, 40–44 frames in listen-only, 142–156
frames in normal mode, payloads decode correctly ("Lithium Valley", 57.6 V charge
limit, etc.). Confirmed multiple times, including a final back-to-back check.

### Pi, kernel SocketCAN (`ip link set can0 type can bitrate 500000`) — 0 RX
- `can0` comes up clean: `operstate=up`, `carrier=1`, **0 RX, 0 errors, ERROR-ACTIVE**
  (not bus-off).
- Kernel auto-picked `brp 2` (170 tq/bit, sample-point 0.870). Forcing the PC's
  exact timing (`tq 117 prop-seg 1 phase-seg1 13 phase-seg2 2 sjw 2` = `brp 20`,
  sample-point 0.882) **still 0 RX**. Listen-only mode also 0. => not a timing issue.

### Pi, userspace gs_usb (NEW test, bypasses the kernel CAN stack) — 0 RX
Built `bestgo_docker_test/{bestgo_gsusb_test.py,Dockerfile.gsusb,entrypoint_gsusb.sh}`
(image `bestgo-gsusb-test`, reuses the `bestgo_logtest.py` decoder; entrypoint
unbinds the kernel `gs_usb` driver via `/sys` so libusb can claim the adapter).
- Device opens perfectly (capability read, timing set, started), but **0 frames**
  with **timeouts=159–198, usb_errors=0, shortreads=0** — i.e. the adapter is open
  and *listening cleanly* but hears nothing.
- A 180 s continuous listen (NORMAL mode, so it would ACK) while the battery was
  "woken" → still 0. Power-cycling the adapter on the Pi → still 0.

### The clincher
Back-to-back, no wiring change: **Pi = 0 frames → move adapter to PC = 142 frames,
battery confirmed transmitting.** Same adapter, same CAN harness.

## Ruled out
- **Battery / wiring / adapter / bitrate** — all proven good by the PC (incl. final
  live read).
- **Bit-timing** — forcing PC-identical timing on the Pi still gave 0.
- **Software / driver path** — kernel *and* userspace transports fail identically on
  the Pi; the userspace path works on the PC. The Pi software opens and reads fine.

## Important confound (kept us honest)
With a single CAN adapter, "Pi can't receive" and "battery is silent" look
identical. The BESTGO BMS **bus-offs within ~3 s** whenever it transmits with
nothing ACKing it, so every adapter move left it un-ACKed and it often went silent
between tests (the PC saw 0 twice for this reason). The final Pi→PC back-to-back
with the battery confirmed live is what makes the Pi-side conclusion solid.

## Leading hypothesis: missing common CAN ground on the Pi side
CAN is differential but both nodes' grounds must sit within the receiver's
common-mode range. The laptop floats (own battery / non-earthed), so the adapter's
ground drifts to the bus reference and RX works. The Pi is on a different supply, so
the adapter ground is pinned elsewhere; with no GND/0 V wire tying the adapter to
the battery's CAN connector, the differential sits outside range → 0 RX, 0 errors —
exactly the symptom. (User's earlier "maybe wasn't grounded properly" instinct, but
at the CAN reference, not the power lead.)

## Recommended next steps (in order)
1. **Add a GND / 0 V wire** from the SH-C31G's ground terminal to the BESTGO CAN
   connector's GND pin (H + L are landed, GND likely isn't). Most probable fix.
2. If awkward: try a **powered USB hub** or a **different Pi USB port** (USB
   under-power to the transceiver); check Pi logs for under-voltage.
3. To remove the last doubt that the battery is live during Pi tests, confirm via
   the **independent BLE channel** — HA's BLE Battery integration entities, or
   `battery_data/smart_bms.py` (bleak) from a PC (only one BLE connection at a time;
   release HA / the phone app first).
4. Once the Pi receives: if the kernel SocketCAN path still fails (FDCAN/gs_usb
   driver), port the userspace `gs_usb` reader into the `solar-car-canbus` add-on
   (`can_reader.py`); if the ground fix also restores SocketCAN, no port needed.

## Reproduce
- PC:  `venv\Scripts\python.exe CANbus_data\pc_files\bestgo_probe.py`
- Pi:  `sudo docker run --rm --privileged -v /dev/bus/usb:/dev/bus/usb bestgo-gsusb-test 10`
  (image built from `/share/bestgo-test`; `-dummy 12` runs the decoder offline).

## Artifacts produced this session
- New userspace-gs_usb test: `CANbus_data/bestgo_docker_test/bestgo_gsusb_test.py`,
  `Dockerfile.gsusb`, `entrypoint_gsusb.sh` (image `bestgo-gsusb-test` on the Pi).
- Probe captures: PC `logs/bestgo-probe-2026060*-*.txt`.
- Unrelated fix made earlier: `HA_URL` default `192.168.0.243` → `10.126.155.163`
  in `simulator/solar_sim.py` and `display/addon/display.py`.

## Adapter / Pi state left behind
Reader add-on `local_solarcar_canbus` is **stopped**. The gsusb test unbinds the
kernel `gs_usb` driver (removes `can0`) — replug the adapter or restart the add-on
(run.sh's uhubctl recovery recreates `can0`) to return to normal operation.
