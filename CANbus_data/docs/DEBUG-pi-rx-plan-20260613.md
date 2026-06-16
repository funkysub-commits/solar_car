# Pi zero-RX — next debug session plan (2026-06-13)

**Supersedes the conclusion of `DEBUG-pi-can-rx-20260608.md`.** That doc
blamed a missing common ground; the **TX-ACK test on 2026-06-13 disproves
it**.

---
## RESULT 2026-06-15 — software fully ruled out; it's electrical (Pi host)

> **PARTLY SUPERSEDED — read SESSION 2 at the bottom first.** The "ground/
> common-mode offset" conclusion here is undercut by two later facts: the
> adapter is **galvanically isolated** (so host ground can't couple to the CAN
> side), and the **car was being switched off mid-session** (so some "0 RX"
> reads were a quiet bus, not a dead receiver). The software-is-ruled-out part
> still stands; the electrical conclusion does not.

Ran the plan at the shop with both devices live (continuous traffic):

- **berr-reporting** ctrlmode is **not supported** by this gs_usb device, so
  that test couldn't run — but it's moot: the controller sits ERROR-ACTIVE
  with 0 errors on a busy bus for minutes, which already rules out
  mis-sampling (timing would pile up TEC/REC → error-passive). **Timing/
  sampling: OUT.**
- **Userspace gs_usb on the Pi (T2, the big one): 0 frames.** Clean — read
  stats `timeouts=296, usb_errors=0, shortreads=0`, with PC-identical timing
  (brp=20, 88.2%) and the EZkontrol transmitting continuously. Bypasses the
  kernel CAN stack entirely and STILL hears nothing. **Kernel gs_usb driver:
  OUT. Software: OUT.** (Also kills the std-vs-extended filter idea — userspace
  would have counted the EZkontrol's extended frames too; it saw zero.)

**Conclusion: Pi-host electrical, almost certainly a ground / common-mode
OFFSET (not a *missing* ground).** Coherent with all the evidence:
- PC (floating, battery) + same adapter/wiring/timing → RX works.
- Pi (earthed wall supply) → RX = 0 both kernel and userspace.
- Pi TX-ACK works: when the Pi *drives* the bus it's referenced to the Pi's
  own ground, so it reads its bits + the ACK; when a *device* drives a frame
  it's referenced to the device's ground, and if the Pi sits offset beyond the
  transceiver common-mode window, the Pi can't see those frames → 0 RX, 0
  errors, clean timeouts. Asymmetric exactly as observed.

### Decisive physical tests (shop, hands-on)
1. **Power the Pi from a battery / USB power bank** (floating, like the
   laptop) and re-run. RX comes alive ⇒ confirmed ground/earth-loop. Fastest
   decisive test.
2. **Multimeter**: DC volts between a Pi GND (GPIO GND / USB shell) and the
   bus CAN_GND (EZkontrol CN2-22 / battery CAN GND). >~few-hundred mV = smoking
   gun.

### Fix direction if confirmed
**Galvanically isolated USB-CAN adapter** (removes the ground-offset
dependency), or isolate / single-point-ground the Pi's CAN reference, or run
the Pi floating. No software change needed — the SocketCAN path is fine once
the analog RX is in range (consistent with the early add-on having worked when
the grounding happened to be OK / the Pi was powered differently).

### Note
The userspace test unbinds the kernel gs_usb driver → `can0` disappears until
a replug/reboot (happens naturally when switching to the power bank).

---

## Where we are (hard facts)

- Same adapter (CANable2 / STM32G431 **FDCAN**, gs_usb/candleLight, `1d50:606f`,
  170 MHz CAN clock) + same bus + same wiring **works on the PC** (userspace
  libusb gs_usb, brp=20 / 17 tq / sample-point 0.882) — decodes EZkontrol +
  BESTGO reliably.
- On the **Pi** (kernel SocketCAN gs_usb): `can0` UP, **ERROR-ACTIVE, 500000,
  0 RX, 0 errors**. Kernel auto-picked brp=2 / 170 tq / sample-point 0.870.
- **TX-ACK test (the key new result):** `cansend can0 100#0011223344556677`
  → `tx_packets` 0→1, **0 errors, still ERROR-ACTIVE**. A clean TX means a
  device ACKed it AND the Pi read the ACK bit back.
- Bus is genuinely live during the test: EZkontrol (20 Hz) + BESTGO ACK each
  other, independent of the Pi — no battery-silence confound (which tainted
  the 2026-06-08 tests, battery-only → bus-off in ~3 s).
- Ground IS connected (and was on the PC too).

### What that rules in / out
- **OUT:** common ground/mode (would be symmetric — TX would fail), gross
  bitrate error (500 k ACK landed in the window), termination, dead bus,
  addon socket filters (interface `rx_packets` counts upstream of per-socket
  filters, = 0).
- **IN:** RX **delivery** failure — 0 errors *with traffic present* means the
  controller isn't mis-sampling frames, it's not being handed them (or is
  rejecting them pre-count). Receive path, not the wire.

## Ranked hypotheses
1. **FDCAN RX acceptance filter = reject-all** (top). ACK is protocol-level,
   *before* acceptance filtering → ACKs all, stores none, 0 errors. Could
   differ PC/Pi if the kernel passes a GS_CAN_MODE flag (HW-timestamp / FD)
   the userspace lib doesn't, changing firmware filter init.
2. **Kernel gs_usb RX path not delivering** (bulk-IN) on the HAOS kernel for
   this FDCAN device. The PC never uses the kernel driver (userspace libusb),
   so this path is untested on a known-good host.
3. **USB transport / power on the Pi** — VL805/USB3 gs_usb quirks,
   autosuspend, under-voltage.
4. **Bit-timing hard-sync to external frames** — lower (mis-sampling should
   make errors; none seen), but cheap to test.
5. **Ground loop / common-mode offset** — low (TX-ACK shows CM OK during TX).

## Ordered tests (each narrows it). Addon is STOPPED; use its image for can-utils:
`IMG=local/aarch64-addon-solarcar_canbus:0.7.0`
`DIAG="sudo docker run --rm --network host --privileged --entrypoint sh $IMG -c"`
First: `ha apps stop local_solarcar_canbus` (free can0). Restart it at the end.

### T0. Confirm bus is live (kill the confound once)
Quick: leave both devices powered (mutual ACK). Optional hard proof: move
adapter to PC for 5 s, see frames, move back — but that's the slow path.

### T1. berr-reporting ON — **splits sampling vs delivery** (do first)
```
$DIAG 'ip link set can0 down; ip link set can0 type can bitrate 500000 berr-reporting on; ip link set can0 up; sleep 5; ip -s -d link show can0 | sed -n "1,12p"'
```
- Errors climb (stuff/form/crc/bus-errors) ⇒ controller *is* receiving bits but
  failing to validate ⇒ **TIMING** (go T3).
- Still 0 errors ⇒ sees no frame activity to error on ⇒ **FILTER / FIFO / USB
  delivery** (go T2).

### T2. Userspace gs_usb on the Pi — **splits kernel-driver vs firmware/USB** (highest value)
Reuse the archived test (was image `bestgo-gsusb-test`; rebuild from
`archive/bestgo_docker_test/` — `Dockerfile.gsusb` + `bestgo_gsusb_test.py`,
entrypoint unbinds kernel gs_usb so libusb can claim it). Run with both
devices live:
```
sudo docker build -t bestgo-gsusb-test -f Dockerfile.gsusb .   # in archive/bestgo_docker_test/ copied to the Pi
sudo docker run --rm --privileged -v /dev/bus/usb:/dev/bus/usb bestgo-gsusb-test 15
```
- RX > 0 ⇒ **kernel SocketCAN/gs_usb path is the culprit.** Fix: port the
  userspace gs_usb transport (we already have it + safe_read in
  `solarcar_can/transport.py`'s GsUsbTransport) into the addon's `can_reader.py`
  as a Linux option. Clean, known path.
- RX = 0 (clean bus this time) ⇒ NOT the kernel CAN stack ⇒ firmware filter /
  USB / electrical (go T3, T4, T5).

### T3. Bit-timing sweep (cheap; do regardless)
Watch for `rx` jumping above 0 (counters are cumulative — note deltas):
```
$DIAG 'for c in "tq 117 prop-seg 1 phase-seg1 13 phase-seg2 2 sjw 2" "bitrate 500000 sample-point 0.750" "bitrate 500000 sample-point 0.875 sjw 4"; do ip link set can0 down; ip link set can0 type can $c; ip link set can0 up; sleep 4; echo "[$c] rx=$(cat /sys/class/net/can0/statistics/rx_packets)"; done'
```
Any timing with rx>0 ⇒ pin those exact `ip link` args in the addon `run.sh`
(replace the bare `bitrate` line) and re-test the addon.

### T4. USB environment (addresses #2/#3)
```
$DIAG 'dmesg | grep -iE "gs_usb|under-voltage|vl805|usb 1-1" | tail -25; echo ---; uname -r; lsusb -t'
```
Then physically: **try a USB-2 port** (not USB-3/VL805), and a **powered USB
hub**. Disable autosuspend for the device if present.

### T5. Cross-isolation (when at the shop, if gear available)
- **Adapter on ANY other Linux box** (`ip link set can0 up type can bitrate
  500000; candump can0`). RX works ⇒ Pi-specific (USB/power). RX 0 ⇒
  kernel-gs_usb/firmware (not just the Pi).
- **A different CAN adapter on the Pi** (MCP2515 SPI HAT or other USB-CAN). RX
  works ⇒ it's the CANable2+gs_usb combo, not the Pi.
- **Reflash candleLight firmware** on the CANable2 (last resort for an FDCAN
  filter-init bug).

## If T2 says "kernel driver": the fix is already 90% written
`solarcar_can/transport.py` `GsUsbTransport` is the userspace gs_usb reader
(with the gs_usb 0.3.1 short-read `safe_read` workaround) and runs today on
the PC. Porting it as the Linux path in the addon means unbinding the kernel
`gs_usb` driver in `run.sh` and using libusb — the same approach as the
archived gsusb test. That sidesteps the kernel CAN stack entirely.

## Remember
- Addon left **STOPPED** — `ha apps start local_solarcar_canbus` to restore.
- Pi at 10.66.76.162; SSH via `ha_run.py` (`HA_PWD` from the Windows registry).

---
## SESSION 2 (later 2026-06-15) — CONFOUND: car power kept being switched OFF

Big caveat on everything above: **the car was powered off during part of the
session** (twice, unannounced). EZkontrol only broadcasts telemetry when the
car is on, so an unknown number of the "0 RX" reads were against a **quiet
bus**, not a broken receiver. Do not treat "Pi can't receive" as proven.

What we did this session:
- **Adapter is galvanically ISOLATED** (user confirmed). This rules out the
  ground/common-mode/earth-loop theories hard — the isolation barrier
  decouples the CAN side from the Pi's ground entirely. Multimeter GND(bus)↔
  GND(Pi) ≈ 0 V also (consistent).
- Termination switch ON, USB blue (USB3) port, splitter cable removed → all
  still 0 RX. None of these changed anything.
- Set EZkontrol to **protocol 1 (250 k MCU-to-Meter)** via EZ-Tune, battery
  unplugged, direct 2-node bus. TX-ACK at 250 k works (controller alive at
  250 k). RX still 0 (one fluke frame once, then 0 over 6 s).
- THEN learned the car had been turned off → results suspect.

### The key logical fork (unresolved)
TX-ACK proving the EZkontrol is "alive + ACKing" does NOT prove it's
"broadcasting." Two scenarios both fit 0-RX + working-TX-ACK:
  A) EZkontrol IS broadcasting and the Pi receives the frames (it must be
     ACKing them, else a lone EZkontrol bus-offs in ~3 s) but DISCARDS them →
     points at an FDCAN acceptance-filter / firmware quirk on Linux.
  B) EZkontrol is NOT broadcasting (car off, or protocol-1 not fully applied)
     → nothing to receive; the Pi receiver may be fine.
Today's car-off confound makes (B) very live.

### RESUME PLAN (do first, in order)
1. **Car ON and CONFIRMED on** (tape a note on the key — it kept getting shut
   off). EZkontrol at 250 k, alone, terminated.
2. **Back-to-back known-good check (decisive):** move the adapter to the PC,
   run `python ezkontrol_decode.py -250`.
   - PC sees frames ⇒ EZkontrol IS broadcasting at 250 k ⇒ scenario A ⇒ real
     Pi receive-but-discard problem (chase FDCAN filter / firmware; the
     archived userspace path and a reflash are the levers).
   - PC sees nothing ⇒ not broadcasting ⇒ power-cycle the EZkontrol / re-check
     protocol-1; the Pi was never the problem.
3. Only after the bus is PROVEN live (PC sees it) is a Pi 0-RX meaningful.

### STATE LEFT (for next time)
- **EZkontrol is at protocol 1 (250 k). MUST set back to 101 (500 k)** before
  rejoining the real shared bus with the battery.
- Battery (BESTGO) unplugged from the bus; splitter cable removed; adapter
  120 Ω termination ON; adapter on the Pi's blue USB-3 port.
- Kernel gs_usb driver was unbound earlier by the userspace test → can0 may be
  absent until a replug/reboot. Addon still STOPPED.
