# Pi zero-RX — slcan did NOT fix it (2026-06-24/25)

> **HEADLINE: the "kernel gs_usb regression" root cause is OVERTURNED.**
> The adapter was reflashed to slcan firmware (a completely different USB
> stack) and the Pi **still receives 0 frames**, while the same adapter
> decodes both devices perfectly on a laptop. So the fault is **not** the
> gs_usb driver. It is Pi-host-specific and **still open.**

This supersedes the conclusion of `SLCAN_MIGRATION_PLAN.md` and
`DEBUG-pi-rx-plan-20260613.md`, both of which treated the gs_usb hardware-
timestamp regression as the proven cause and slcan as the fix. Step 0 of the
migration plan (validate slcan RX on the Pi) was finally run — and it failed.

---

## UPDATE 2026-06-26 — T0 done: clean latest-HAOS also fails (confound-free)

Ran T0 (the planned fresh-install test): new SD, **latest HAOS, kernel
`6.18.33-haos-raspi`**, adapter on slcan (`16d0:117e` → `/dev/ttyACM0`), **no CAN
add-on**, throwaway `python:3.12-slim`+python-can container. **Pi: 0 frames.**
Immediately moved the SAME adapter to the laptop on the SAME bus/battery:
**156 frames, all 14 BESTGO IDs** (`0x351`–`0x379`; battery proven live — the
laptop was ACKing it). So:

- **Software/config cruft RULED OUT** — a pristine install on the *newest*
  kernel still gets 0. T0's "best case" did not happen.
- **Battery RULED OUT** (the historical confound) — the laptop decoded it fine
  at the same instant.
- First **fully confound-free** capture of the Pi zero-RX: clean OS, live bus
  proven by an independent receiver, same adapter both sides.

Cause is firmly **Pi-host-specific and present on latest HAOS** (kernel/USB
stack or the Pi-4 VL805 controller). Next: **T1 (older HAOS)** to test the
kernel-regression theory; cheap interim split = a **raw-serial read on the Pi**
(does the CDC port deliver *any* bytes, or is it the python-can slcan backend?).

**Raw-serial split — done 2026-06-26 (rules out python-can):** opened
`/dev/ttyACM0` with pyserial directly on the Pi. The slcan **`V` version query
round-tripped** (`16e7497-dirty …canable2.git`) — so the Pi↔adapter USB-CDC
**control** path works (write command, read reply): not a dead port, not
permissions, not the wrong device. But after `C`/`S6`/`O`, **0 raw bytes**
arrived in 6 s while the battery was streaming (laptop confirmed 156 frames
minutes earlier). So no CAN frame data reaches the Pi at the *raw byte* level,
not just via python-can — the backend is exonerated. Points at the **Pi USB
host / VL805 / HAOS kernel** handling of this adapter's data, consistent with
gs_usb having failed the same way (same USB host, different driver).

> **Correction (CDC-ACM detail):** the `V` reply does *not* prove "only control
> transfers work" — in CDC-ACM the serial payload (including the `V` response)
> travels over the **bulk** IN endpoint, same as the CAN frame stream. So the
> Pi *can* receive some bulk-IN data; what fails is **sustained/streamed bulk-IN
> after the channel is opened**. The accurate split is *low-duty request/
> response bulk-IN works, continuous streaming bulk-IN does not* — exactly what
> a `usbmon` capture can confirm (URBs submitted but never completing after `O`).

**Outside analysis + next-step plan:** an independent LLM review of the full
write-up is saved as **`USB-CAN-RPi4-HAOS-debug-plan.md`** (the prompt that
produced it is **`DEBUG-prompt-for-llm.md`**). It agrees the prime suspect is a
**Pi-4 VL805/xHCI/HAOS-kernel receive-side USB completion problem** and that the
6.6 rollback is the right next test. Its highest-value additions: capture
**`usbmon`** during the failure (does the bulk-IN URB stop *completing* after
`O`?), grab `lsusb -vv` endpoint descriptors (is the adapter full-speed/12M?
split-transaction behind the internal hub?), and try a **powered external
USB-2 hub** (changes the transaction-translator/topology).

---

## UPDATE 2026-06-27 — rolled back to 6.6 (T1), staged + dry-run ready

Did T1 **in-place**: `ha os update --version 15.0` → Pi now on **HAOS 15.0,
kernel `6.6.74-haos-raspi`** (slot B; 18.0 kept on slot A as auto-rollback).
The major downgrade **wiped the custom `slcan-smoketest` Docker image** (rebuilt
it; `/config/slcan_smoketest.sh` survived on the data partition). The adapter
came up in **DFU on the reboot, cleared by a simple replug** — owner confirms
it is **not** a loose DIP/BOOT switch (correction to the old theory; looks like
cold-boot/power-sequencing DFU entry, fixed by replug). **Dry-run on 6.6
passed** (slcan bus opens, `recv` returns `None` with no battery) — so the path
is ready; only the battery is untested. **Shop test pending:** connect battery
→ `bash /config/slcan_smoketest.sh`.

**USB descriptor capture (via sysfs; `lsusb -vv` returns nothing in this
add-on) — supports the split-transaction hypothesis:**
- The adapter is a **full-speed (12 Mbit/s) USB 2.0 device** (`bcdUSB 2.00`,
  ep0 maxpkt 64).
- Endpoints: **Bulk IN `ep81`, 64 B** (the CAN-frame stream — the one that
  never delivers), Bulk OUT `ep01` 64 B (slcan commands), Interrupt IN `ep82`
  8 B/16 ms (CDC notify).
- It enumerates **behind the Pi-4's internal high-speed hub `2109:3431`**, so a
  full-speed device behind a high-speed hub means the hub's **transaction
  translator (TT)** does split transactions for it. A 6.6→6.12+ regression in
  xHCI/VL805 split-transaction or bulk-IN scheduling would hit exactly this
  endpoint — and it's why a **powered external USB-2 hub** (different TT) is a
  high-value workaround to try. Good artifact for a bug report.

**Powered-hub topology verified 2026-06-27 (battery-free):** put the adapter
behind a powered **GenesysLogic** hub → Pi. New chain: VL805 xHCI → Pi internal
hub `2109:3431` → **GenesysLogic hub `05e3:0610`** → CANable (`1-1.1.1`, still
12 Mbps full-speed). So the full-speed adapter's **split transactions are now
served by the GenesysLogic hub's TT instead of the Pi internal-hub TT** — the
exact thing that would bypass an internal-hub/VL805 TT bug. Dry-run through the
hub opens + reads cleanly. **At the shop, if battery-direct on 6.6 still gives 0,
test battery-through-this-hub next** — it's the cheapest shot at the TT
hypothesis. (Caveat: won't help if the fault is in the xHCI host's split-
completion handling regardless of which hub does the TT.)

---

## What was tested (shop, 2026-06-25)

- Adapter found in **DFU mode** again (`0483:df11`) — the recurring loose
  BOOT-switch gremlin. Flipped the DIP switches, replugged → came up as slcan
  (`16d0:117e` CANable2, CDC-serial at `/dev/ttyACM0`). Pi at `10.116.80.162`.
- **Laptop smoke test (control): PASS, twice.** python-can 4.6.1 + pyserial
  3.5, slcan on `COM5`, `BestgoDecoder` → 92 then 86 frames, SOC 54%,
  ~52.4 V, cells ~3276–3286 mV (1 mV spread), all 14 BESTGO IDs
  `0x351`–`0x379`. The slcan software stack is correct end-to-end.
- **Pi-side RX (identical python-can slcan path, staged into `/tmp/pylib`):
  0 frames**, repeatedly — including on a blue USB-3 port. No telemetry.

## Why this kills the gs_usb-regression theory

slcan and gs_usb are **different USB device classes and different kernel
paths**:

| | gs_usb (candleLight) | slcan |
| --- | --- | --- |
| USB class | vendor-specific, **bulk** transfers | **CDC-ACM** serial (ttyACM) |
| Kernel driver | `gs_usb` → SocketCAN | `cdc_acm` → tty → python-can userspace |

Both fail on the Pi with 0 RX. A bug in the `gs_usb` driver cannot explain a
failure on the slcan path that never touches `gs_usb`. The cause is therefore
**upstream of either CAN driver** — in the part of the chain both paths share.

## What was ruled out this session

- **Software / driver stack** — two independent stacks (gs_usb bulk, slcan
  CDC) both get 0; the decode code is proven good on the laptop.
- **Port contention** — `lsof` clean; no ModemManager / brltty grabbing the
  tty. (Caveat: the earlier `lsof` had run **inside the SSH add-on
  container**, not host-wide — re-checked host-wide here.)
- **USB autosuspend** — off for the device.
- **Ground / common-mode** — the adapter is **galvanically isolated**; bus-GND
  ↔ Pi-GND ≈ 0 V. (Already established in the gs_usb saga; still true.)
- **Open/reset timing, undervoltage** (`uv_sticky=0`), **xHCI/USB errors**
  (none logged in `dmesg`).

## Remaining suspects (all share the Pi's USB host path)

1. **HAOS kernel / USB stack** — the part of the receive path common to both
   drivers. A HAOS update could have regressed this without it being the
   gs_usb driver specifically. (Consistent with "worked earlier, broke later"
   — see the May-30 caveat below.)
2. **Pi-4 VL805 USB host controller** — a full-speed CDC / bulk quirk on the
   VL805 hub. **All four Pi USB-A ports hang off the one VL805**, so swapping
   ports proves nothing — they are the same controller.
3. **python-can's slcan backend** — a long shot, but it is the one piece not
   exercised on the laptop in exactly the Pi's environment (Linux tty timing).

The laptop uses a different USB host controller and works — so the asymmetry
is **host-only**: the adapter travels between machines intact.

## The 2026-05-30 "it worked on the Pi" baseline — re-examined

The entire regression narrative rested on one earlier data point: BESTGO
decoded live on the Pi on 2026-05-30 (memory `bestgo-pi-canbus-working.md`).
**Re-reading the 2026-05-30 transcript (session `029ea8a7`), that success was
never independently logged:**

- The assistant walked the user through the Pi test sequence (`candump can0`
  → hand to the add-on → check `sensor.bestgo_*`), but **no Pi-side output was
  ever captured** — no `candump`, no add-on log, no sensor state.
- The `uname -a` (which would have recorded the kernel) was issued over plink
  but **its output never came back** — that turn was interrupted, and the user
  took over the Pi directly.
- The only evidence is the user's verbal line: *"it is working now on the
  raspberry pi! nice work."*

**Consequences:**
- The **May-30 kernel version is unknown.** "It worked on an older kernel; a
  HAOS update broke it" has no logged kernel data on either side of the
  supposed change. (By 2026-06-06 the Pi was on `6.12.47-haos-raspi`.)
- The May-30 result is **suggestive, not proof** — the user owns the hardware
  and presumably saw sensors update or frames on `candump`, but we can't
  confirm what path, port, or kernel produced it.

This doesn't mean the Pi never worked; it means we have **no captured baseline
to diff against**. Establishing a *logged* known-good (even once) is now part
of the job.

## Experiment plan (next session, in order)

Strategy: a fresh SD wipes the accumulated debugging cruft on the current
install (unbound `gs_usb` driver, the 0.7.0 add-on's uhubctl USB power-cycling,
possible ModemManager/brltty tty grabbers) in one move — and it's
non-destructive (the current SD is untouched). Test the bare slcan receive path
with **no `solar-car-canbus` add-on**, so nothing fiddles the port.

### T0 — Clean latest-HAOS + slcan smoke test (DO FIRST)
Fresh SD, **latest HAOS**. Install only the **Advanced SSH & Web Terminal**
add-on with **Protection Mode OFF** (required — otherwise the SSH add-on can't
reach Docker or `/dev`; see README §3.2 / memory `eink-addon-protection-mode`).
Do **not** install the CAN add-on.

1. Confirm the adapter came up as slcan, not DFU: `lsusb` → `16d0:117e` and
   `ls /dev/ttyACM*` exists. (`0483:df11` = DFU mode, the loose BOOT-switch
   gremlin — flip switches, replug.)
2. Battery-only bus = 2 nodes → adapter 120 Ω **ON**; verify ~**60 Ω** across
   CAN_H/CAN_L (power off). 120 Ω = under-terminated, 40 Ω = over.
3. Throwaway container (nothing persists):
   ```
   docker run --rm -it --device=/dev/ttyACM0 python:3.12-slim sh -c \
     "pip install -q python-can pyserial && python -c \"import can; b=can.Bus(interface='slcan', channel='/dev/ttyACM0', bitrate=500000); print(b.recv(timeout=5))\""
   ```
   A frame (not `None`) = RX works.

- **Frame → it was software/config cruft on the old SD.** Best case: rebuild
  clean on latest HAOS, deploy add-on 0.8.0, done. **Image the SD immediately.**
  (The kernel was innocent.)
- **None / 0 → cruft is ruled out** and a clean install isn't the fix. Note:
  latest HAOS ≈ the same 6.12.x kernel as the broken one, so this does **not**
  clear the kernel-regression theory — go to T1.

### T1 — Older HAOS on a spare SD (only if T0 fails)
Flash a HAOS old enough to predate the 6.12 kernel; **verify with `uname -r`**
(don't trust the version number). Same T0 smoke test.
- **Frame → kernel / USB-stack regression confirmed.** You now have a working
  config — image it, then decide: freeze on the old HAOS for the race, or chase
  a permanent fix.
- **None → not the kernel → the Pi-4 VL805 USB hardware** → T3.

### T2 — Powered USB hub (cheap; can combine with T0/T1)
Rules out VL805 under-power / enumeration. Adapter via a powered hub, repeat
the smoke test.

### T3 — MCP2515 SPI CAN HAT (durable fallback)
Bypasses USB entirely. If this receives, ship it for race day — it sidesteps
the whole USB question.

### Sub-checks
- **Isolate python-can vs the kernel/USB:** raw can-utils —
  `slcand -o -s6 /dev/ttyACM0 can0 && candump can0`. Frames on `candump` but
  not python-can ⇒ blame the slcan backend, not USB.
- **Highest-leverage buy:** a **2nd USB-CAN adapter** for a confound-free
  loopback (two adapters back-to-back on the Pi). The SH-C31G's STM32G431
  supports HW loopback but the slcan firmware doesn't expose it, so a 2nd
  adapter is the clean way to test the Pi's receive path in isolation from the
  bus.
  - **Confirmed 2026-06-27:** internal-loopback (`OI`/`OE`) would let us test RX
    with no bus, and the upstream canable2-fw README documents those commands —
    but our **flashed build (`16e7497-dirty`) only responds to `V`** (no
    `OI`/`OE`/`F` acks, no looped frames), tested on both the laptop and the Pi.
    So on-adapter loopback needs a **firmware reflash** first; until then a 2nd
    adapter (or the real battery) is the only way to generate test traffic.

## State left

- slcan software stack **validated on the laptop**; **Pi RX still broken and
  unexplained**. No working Pi telemetry over either firmware.
- Adapter on slcan firmware, `/dev/ttyACM0`. EZkontrol left on **protocol 1
  (250 k)** from debugging — set back to **101 (500 k)** before the real
  shared bus. canbus add-on left **STOPPED**.
- **Watchdog risk:** the deployed gs_usb add-on (0.7.0) power-cycles the USB
  port via uhubctl whenever `can0` is missing — harmless while stopped, but
  don't let it auto-start until the slcan `run.sh` (0.8.0, no `can0`) is the
  one on the Pi.
