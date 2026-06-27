# slcan migration plan (2026-06-18)

> **⚠️ OUTDATED PREMISE — slcan did NOT fix the Pi (tested 2026-06-25).**
> Step 0 below was finally run: slcan RX on the Pi = **0 frames**, while the
> same adapter decodes both devices on a laptop. Because slcan is a different
> USB stack from gs_usb and *also* fails, the "gs_usb regression" premise this
> plan is built on is **overturned** — the cause is host-specific and still
> open. See **`DEBUG-pi-rx-slcan-20260624.md`**. The software changes below
> were still built and are still valid as a uniform slcan code path (and are
> the right thing to run once the Pi can receive at all); they just do **not**
> resolve the zero-RX problem. Read the new doc before acting on this one.
>
> **STATUS: software BUILT 2026-06-19** (hardware-tested 2026-06-25 — slcan RX
> on the Pi FAILED, see above). Done:
> `SlcanTransport` + `find_slcan_port` + `CAN_TRANSPORT` selector (default
> slcan) in `transport.py`; add-on **0.8.0** (`can_reader.py` opens slcan and
> auto-detects the serial port, simplified `run.sh`, `config.yaml` gains
> `uart: true` + a `can_port` option and drops NET_ADMIN/can_interface,
> Dockerfile/requirements gain `pyserial`); gs_usb + SocketCAN kept for
> reflash-back; vendored copy re-synced. Verified off-hardware: compiles,
> golden tests 7/7, dummy modes, transport selector + error paths. PC slcan
> path verified on the laptop 2026-06-25 (decodes both devices). **Open: the
> Pi receives 0 frames over slcan — root cause unresolved (not this plan).**

## Why

Multi-session debugging (`docs/DEBUG-pi-rx-plan-20260613.md`) proved the Pi
**receives + ACKs** CAN frames on the SH-C31G but never delivers them to
software — a **kernel gs_usb / hardware-timestamp regression** specific to
this STM32G431 candleLight adapter on the HAOS 6.12 kernel. Not electrical,
not wiring, not interfering software (all ruled out, incl. a clean-replug
test). There's no in-place knob to disable it on HAOS.

**Resolution:** the adapter has been **reflashed from candleLight/gs_usb to
slcan firmware** (DSD TECH's documented Linux route). slcan presents the
adapter as a CDC-serial port and uses a completely different USB path
(serial, not gs_usb bulk), sidestepping the broken driver entirely.

This plan migrates the software from gs_usb to slcan on **both** the PC tools
and the Pi add-on. The gs_usb/SocketCAN code is **kept** (not deleted) so the
adapter can be reflashed back without losing support.

## Step 0 — VALIDATE slcan RX first (gate; do before any code)

> **RESULT 2026-06-25: FAILED.** Ran exactly this against the live battery —
> the Pi returned **None / 0 frames** (incl. on a USB-3 port), while the same
> adapter on the laptop decoded 86–92 frames. slcan does **not** fix the Pi
> zero-RX. The gate did its job, just after the build instead of before it.
> Full analysis + the remaining-suspects list + next experiments are in
> **`DEBUG-pi-rx-slcan-20260624.md`**. Do not deploy the add-on expecting
> telemetry until the Pi can receive over *some* path.

Before building anything, confirm slcan actually receives on the Pi (the
whole point). Quick check with python-can's slcan interface against the live
battery (500 k):

```
# on the Pi, in a python-can container (adapter = /dev/ttyACM0):
python3 -c "import can; b=can.Bus(interface='slcan', channel='/dev/ttyACM0', bitrate=500000); print(b.recv(timeout=5))"
```

A decoded frame (not None) = slcan RX works → proceed. None/0 = stop and
rethink (don't build the integration). Also confirm the serial device path
(`ls /dev/ttyACM*` and `/dev/serial/by-id/`).

## Design choice: python-can `slcan` on BOTH platforms (uniform)

Use python-can's built-in `slcan` interface everywhere, via the existing
`transport.py` abstraction:

- **Windows (PC):** `can.Bus(interface='slcan', channel='COMx', bitrate=500000)`
- **Linux (Pi):** `can.Bus(interface='slcan', channel='/dev/ttyACM0', bitrate=500000)`

This is cleaner than the `slcand → can0` alternative: one code path for both
platforms, no `slcand` process to manage, and a much simpler add-on `run.sh`
(no `ip link`/NET_ADMIN dance). python-can maps `bitrate=500000` to the slcan
`S6` command internally; standard + extended IDs both supported.

(Alternative considered — `slcand` creates a SocketCAN `can0`, keeping
`can_reader.py` on SocketCAN — rejected: `can_reader`'s recovery still needs
changes either way, PC can't use slcand, and it adds a process to babysit.)

## Changes

### 1. `solarcar_can/transport.py` — add `SlcanTransport`
- New `SlcanTransport(port, bitrate)`: opens `can.Bus(interface='slcan',
  channel=port, bitrate=bitrate)`; wraps `recv()→Frame`, `describe()`,
  `close()` like the others.
- **Port auto-detect** `find_slcan_port()`: pyserial `list_ports` — match the
  adapter (USB CDC; by VID/PID or "USB Serial"/STM descriptor). Override with
  `CAN_PORT` env var. Windows → `COMx`, Linux → `/dev/ttyACM0` (or
  `/dev/serial/by-id/...` for stability).
- `open_transport()`: select transport via `CAN_TRANSPORT` env
  (`slcan` | `gsusb` | `socketcan`), **default `slcan`** now. Keep the gs_usb
  and SocketCAN branches for reflash-back.
- Update the module docstring (no longer "gs_usb vs SocketCAN").

### 2. PC CLI tools (`monitor.py`, `bestgo_decode.py`, `ezkontrol_decode.py`)
- They already call `open_transport()` → mostly automatic once it defaults to
  slcan. Verify the `-250` / bitrate plumbing still passes through.
- Title/`describe()` now shows e.g. `slcan COM5 500 kbps`.

### 3. Pi add-on (`ha_addons/solar-car-canbus/`)
- **`can_reader.py`:** replace the SocketCAN `open_bus()` / `_iface_is_up()` /
  `ensure_socketcan` path with an slcan open on the serial port (reuse
  `SlcanTransport`, or `can.Bus(interface='slcan', channel=<port>,
  bitrate=CAN_BITRATE)`). The retry loop + `canadapter_status` now key off
  "serial port present & bus open" instead of can0 up. **Decode, push,
  dummy mode, the 3 device-status sensors, and the network-monitor thread are
  all unchanged.**
- **`run.sh`:** drop the `ip link` / can0 bring-up. Find the serial device
  (auto-detect `/dev/ttyACM*` or `/dev/serial/by-id`), export it as
  `CAN_PORT`, exec `can_reader.py`. Keep the uhubctl DFU-recovery block
  (the BOOT-switch DFU gremlin is firmware-independent), but key it off the
  serial device missing instead of can0.
- **`config.yaml`:** add **`uart: true`** (exposes `/dev/ttyACM*`/serial
  devices to the container). Can drop `NET_ADMIN` (no SocketCAN bring-up) but
  **keep `host_network: true`** (the network-monitor thread needs the host's
  real IP). Bump **version → 0.8.0**.
- **`Dockerfile`:** still needs `python-can` + `requests`; add `pyserial`
  (python-can slcan dep). `can-utils` no longer required but harmless.
- Re-run `python sync_addon.py` (vendored `solarcar_can` copy).

### 4. Dependencies
- Add **`pyserial`** to `requirements.txt` / `requirements-pi.txt` and the
  add-on Dockerfile (python-can's slcan backend imports it). gs-usb/pyusb/
  libusb-package can stay for the gs_usb fallback.

### 5. Docs / config
- `can_up.sh` is gs_usb/SocketCAN-only — mark it legacy (slcan needs no
  bring-up).
- Update `CANbus_data/README.md` / `docs/` to describe the slcan path and how
  to flash back to gs_usb.

## Rollout / testing order
1. Step 0 validation (above) — proves slcan RX on the Pi.
2. `transport.py` + PC tools → test `monitor.py` on the **PC** with slcan
   (both devices) — confirms the abstraction end-to-end before touching the Pi.
3. Golden tests still pass (`tests/test_decoders.py` — decode is untouched).
4. Add-on 0.8.0 → push, rebuild, start, verify `sensor.bestgo_*` /
   `sensor.ezkontrol_*` update from the real bus, and the health sensors.
5. Bump display add-on? No — it only reads HA sensors; unaffected.

## Rollback
gs_usb/SocketCAN code stays in `transport.py` (selectable via
`CAN_TRANSPORT`). To revert: reflash candleLight firmware and set
`CAN_TRANSPORT=gsusb` (PC) / restore the can0 `run.sh` (Pi). The add-on is
versioned, so the previous image can be reinstalled.
