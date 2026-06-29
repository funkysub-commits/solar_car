# Shop runbook — Pi-on-6.6 battery CAN test (2026-06-27)

Goal: does the Pi receive the BESTGO battery's CAN frames on **HAOS 15.0 /
kernel 6.6.74** (the kernel-rollback test)? This is the experiment that settles
whether the older kernel fixes the long-standing "Pi gets 0 CAN frames" problem.
Runs entirely from the **HA web terminal** — no laptop or SSH needed.

## 0. Setup (physical)
- Adapter on **candlelight/gs_usb** firmware, plugged **DIRECT into a Pi USB
  port** (NOT the powered hub).
- **Battery powered ON and on the bus.** Battery-only = 2 nodes ⇒ adapter 120 Ω
  termination **ON**; meter ~**60 Ω** across CAN_H/CAN_L (power off to measure).

## 1. Open the HA web terminal
HA UI → the Advanced SSH & Web Terminal add-on → **Web UI / Open Terminal**.
(Everything below is typed there.)

## 2. Confirm the adapter is healthy (not DFU)
```
lsusb | grep -iE '1d50|0483'
```
- `1d50:606f canable2 gs_usb` → good.
- `0483:df11 ... DFU` → it booted into DFU. **Unplug/replug** and re-check.

## 3. Run the test
```
bash /config/canbus_smoketest.sh
```
It brings up `can0` at 500k and listens 12 s.

## 4. Read the result
- **`RESULT: RX WORKS - N frames, ... IDs: ['0x351', ...]`** → 🎉 **6.6 fixes it.**
  Then: keep the Pi on 15.0, **disable HAOS *OS* auto-update** (so it can't bump
  back to a CAN-breaking kernel), deploy the `solar-car-canbus` add-on in
  **socketcan** mode, and **image the SD** as your known-good.
- **`RESULT: 0 frames ...`** → 6.6 didn't fix it on the Pi. Go to step 5 to
  confirm the battery is actually transmitting before concluding.

## 5. If 0 frames — confirm the battery is live (laptop control)
Move the adapter to the **laptop**, then (in the project folder):
```
CAN_TRANSPORT=gsusb python CANbus_data/monitor.py
```
(`CAN_TRANSPORT=gsusb` because the adapter is on candlelight; needs WinUSB bound
— already done via Zadig.)
- **Laptop decodes frames** → battery is fine; the Pi USB host (VL805) is the
  problem even on 6.6 → pivot to the **VL805/bootloader EEPROM update** (boot
  Raspberry Pi OS once, `rpi-eeprom-update -a`) or a **CAN-to-network bridge**
  (ESP32/MQTT). SPI CAN HAT is blocked by the e-ink HAT.
- **Laptop also 0** → the battery/bus is the issue: power-cycle the battery,
  recheck termination (~60 Ω), then redo from step 2.

## Revert to HAOS 18.0 anytime (no download)
The kernel isn't the differentiator if 6.6 fails:
```
ha os boot-slot A      # 18.0 is intact on slot A
ha host reboot
```

## Notes
- The Pi's IP changes per network; this runbook avoids needing it (web terminal).
- `canbus_smoketest.sh` uses `sudo docker` + the `slcan-smoketest` image (has
  python-can) + an alpine `nsenter` to bring up `can0`. Protection Mode must be
  OFF (it is).
