# Archive — superseded but kept for reference

Nothing in here is part of the running system. It is kept because it
documents working fallbacks or hard-won debugging knowledge.

## `ble_bms/` — Bluetooth BMS desktop GUI (the pre-CAN battery path)

The original way to read the battery: a DearPyGui desktop dashboard
(`main.py` + `display/bms_gui.py`) talking to the SmartBMS over BLE, plus a
BLE scanner test script. Superseded by the CAN-bus path (`CANbus_data/`,
the `solar-car-canbus` HA add-on) in May 2026; the HA "BLE Battery
Management System" integration (README §3.3) remains the documented
hardware fallback.

**Note:** `main.py` imports `battery_data.smart_bms`, which was never
committed to this repo (it lived alongside the gitignored `SmartBMS_App/`).
As archived, the GUI does not run without recovering that package — another
reason it is here and not at the repo root.

## `bestgo_docker_test/` — standalone Pi-side decode validation containers

Minimal Docker containers (not HA add-ons) used in 2026-05/06 to prove
BESTGO frames decode on the HAOS box before/alongside the real add-on:
`bestgo_logtest.py` via SocketCAN, plus a userspace-gs_usb variant
(`bestgo_gsusb_test.py` / `Dockerfile.gsusb`) added while chasing Pi CAN RX
problems — see `CANbus_data/docs/DEBUG-pi-can-rx-20260608.md` for that journal.
The production add-on runs on SocketCAN. The decode logic embedded in these
scripts predates the shared `solarcar_can` package — do not copy from it.
