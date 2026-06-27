# Debug prompt — Pi zero-RX USB-CAN (for handing to another LLM / forum / issue)

Self-contained problem write-up for the Pi-4 + HAOS "0 CAN frames received"
issue. Paste the section below the line into another LLM (or adapt for a HA
community post / GitHub issue). The response we got is saved alongside as
`USB-CAN-RPi4-HAOS-debug-plan.md`. Live debugging state:
`DEBUG-pi-rx-slcan-20260624.md`.

---

You are an expert in Linux USB, the kernel USB stack (xHCI/CDC-ACM/usb-serial), SocketCAN, and Raspberry Pi embedded debugging. I have a USB-CAN receive problem that is host-specific and I need help isolating the root cause and finding a fix. Please reason carefully, propose concrete diagnostics, and rank likely causes.

## System

**Goal:** read a CAN bus on a Raspberry Pi and publish telemetry. It works on a laptop but not on the Pi.

**Hardware**
- Raspberry Pi 4 (4 GB) running **Home Assistant OS (HAOS)** — a locked-down, largely read-only appliance OS. I can run privileged Docker containers and edit the boot `config.txt`/`cmdline.txt`, but I cannot easily rebuild kernel modules in place.
- USB-CAN adapter: **DSD TECH SH-C31G**, which is a **CANable2 clone built on an STM32G431 (FDCAN core, 170 MHz CAN clock)**.
  - It can run two firmwares. I have tried both:
    - **candleLight / gs_usb** (USB vendor class, bulk transfers; enumerates as `1d50:606f`, kernel `gs_usb` driver → SocketCAN `can0`).
    - **slcan** (USB CDC-ACM serial; enumerates as `16d0:117e` "Openlight Labs CANable2", normaldotcom/canable2 firmware; kernel `cdc_acm` → `/dev/ttyACM0`; read in userspace via python-can's `slcan` backend).
- CAN bus: **500 kbps**, shared. For these tests it is a **2-node bus**: the adapter + a **BESTGO LiFePO4 battery BMS** that broadcasts 14 standard-ID frames (`0x351`–`0x379`) at ~14 Hz. Termination: the battery has its own 120 Ω; the adapter's 120 Ω switch is ON (~60 Ω total, meter-verified).

**Software**
- HAOS, currently kernel **6.18.33-haos-raspi** (HAOS 18.0). The problem was first seen on **6.12.47-haos-raspi**.
- Test path: a throwaway Docker container (`python:3.12-slim`) with `--device=/dev/ttyACM0`, running **python-can 4.6.1 + pyserial 3.5**: `can.Bus(interface='slcan', channel='/dev/ttyACM0', bitrate=500000)`.

## The problem

The **Pi receives 0 CAN frames** from a live bus. The bus opens cleanly with no errors — it just never delivers a frame. The **identical adapter on a Windows laptop** (same python-can slcan stack, COM port) decodes **86–156 frames, all 14 BESTGO IDs**, on the **same bus and same battery, minutes apart**. So the adapter, wiring, bus, termination, and battery are all proven good; the asymmetry is purely the **host**.

## Decisive, confound-free reproduction
- **Fresh, clean HAOS 18.0 install** (brand-new SD, no Home Assistant add-ons, nothing custom touching the port), adapter on slcan → **Pi: 0 frames.**
- Seconds before/after, the same adapter on the laptop on the same bus → **156 frames** (the laptop is also ACKing the battery, so the battery is definitely transmitting).

## The most interesting diagnostic clues
1. **Both USB firmware classes fail identically.** gs_usb (vendor class, bulk IN → SocketCAN) gets 0; slcan (CDC-ACM serial) gets 0. Different USB device classes, different kernel drivers, same result → points at the **shared USB host path** (the Pi-4's single **VIA VL805** xHCI controller and/or the HAOS kernel USB stack), not the CAN driver.
2. **gs_usb era:** the Pi could **transmit** a CAN frame and read its own **ACK bit** back (TX works), and a lone battery on the bus stayed alive **only because the Pi's adapter was ACKing it** — i.e. the controller was receiving + ACKing frames at the hardware/protocol level, but **the frames were never delivered to software** (interface `rx_packets` stayed 0, 0 errors, ERROR-ACTIVE).
3. **slcan raw-serial split (latest test):** opening `/dev/ttyACM0` directly with pyserial, the slcan **`V` (firmware version) command round-trips** — the Pi writes the command and reads the adapter's reply over USB CDC, so the **control/command path works bidirectionally**. But after the open sequence (`C`, `S6`, `O`), **0 raw bytes** of CAN-frame data arrive in 6 s while the battery is streaming. So small control transfers get through, but the **streamed CAN data does not** — and it's not a python-can issue (raw read also sees nothing).
4. The adapter currently sits on the Pi's **USB-2** ports (`Bus 001`, behind the VL805's internal 2.0 hub `2109:3431`); a **USB-3** (blue) port was also tried earlier — both fail. All four USB-A ports are behind the one VL805.

## What I have already ruled out
- **Battery silent / CAN bus-off** — ruled out (laptop decodes it at the same moment and ACKs it).
- **Accumulated software/config cruft** — ruled out (a pristine fresh OS install still gets 0).
- **python-can / slcan backend** — ruled out (raw pyserial also gets 0 bytes).
- **The CAN driver specifically** — ruled out (two different USB classes/drivers both fail).
- **Permissions / wrong device** — ruled out (port opens; `V` query round-trips).
- **Bitrate / bit timing** — ruled out (500 k works on the laptop; on gs_usb I also forced the laptop-identical timing registers and still got 0).
- **Termination** — ruled out (meter-verified ~60 Ω; laptop works on the same bus).
- **Ground / common-mode** — ruled out (the adapter is **galvanically isolated**; bus-GND ↔ Pi-GND ≈ 0 V).
- **USB under-voltage / power** — ruled out (`uv_sticky=0`, no under-voltage in dmesg).
- **ModemManager / another process grabbing the tty** — ruled out (no ModemManager; nothing holds `/dev/ttyACM0`).
- **USB autosuspend** — disabled.
- **USB errors / resets / disconnects** — none logged in dmesg during the tests; `cdc_acm` binds cleanly at boot.

## Kernel/version correlation
- **Fails:** 6.12.47-haos-raspi and 6.18.33-haos-raspi.
- **Reportedly worked** (the hardware owner is confident, but it was never logged): an earlier setup around 2026-05-30, on a kernel we never captured but that predates 6.12 — most likely the **6.6 LTS** line (HAOS ≤ 15.x). HAOS 16.0 was the 6.6 → 6.12 transition.
- Next planned test: roll back to **HAOS 15.0 (RPi kernel 6.6.74)** and re-run the smoke test.

## What I need from you
1. What mechanisms would cause a USB device to deliver **small control/interrupt transfers but not sustained bulk-IN streaming data**, specifically on a Pi-4 (VL805 xHCI) + recent kernel, while the same device streams fine on a different host? (CDC-ACM and gs_usb both affected.)
2. Is this consistent with a known **VL805 / xHCI bulk-IN bug**, a kernel USB regression, a CDC-ACM/usb-serial buffering or flow-control issue, or a power/quirk problem? What dmesg signatures or `lsusb -v` endpoint-descriptor details would confirm or deny each?
3. What concrete diagnostics should I run? (e.g. `usbmon` capture during a live bus, `lsusb -t`/`-v`, `/sys/.../urbnum` or error counters, forcing the port to USB-2/full-speed, `dwc_otg`/`xhci` or `usbcore.quirks` kernel cmdline options, trying a powered hub, etc.) Please give exact commands and what each would prove.
4. Given HAOS is locked down (privileged Docker + boot cmdline editable, but no easy module rebuild), which fixes/workarounds are actually feasible, and which require a different kernel or hardware (e.g. an MCP2515 SPI CAN HAT that bypasses USB entirely)?
5. Does the gs_usb "received + ACKed in hardware but not delivered to software" behavior change your hypothesis, and is the kernel-version rollback to 6.6 a sound next step?

Please give your best-ranked root-cause hypotheses and a prioritized test plan.
