# Solar Car — CAN bus

This directory decodes the two CAN devices on the solar car — the **EZkontrol
B48800** motor controller and the **BESTGO** battery (Lithium Valley BMS) —
which share **one 500 kbps bus**. They coexist because their IDs don't
overlap: EZkontrol uses 29-bit extended IDs (`0x1801xxxx`), BESTGO uses 11-bit
standard IDs (`0x351`–`0x379`).

The same decoders are used three ways:

- **CLI dashboards** (`monitor.py` etc.) — watch the bus live on a laptop or
  the Pi, for bench testing and debugging.
- **The Home Assistant add-on** (`ha_addons/solar-car-canbus/`) — the
  production service on the car: decodes the bus and pushes sensors to HA.

All of them share one decode library, [`solarcar_can/`](solarcar_can/) — the
**single source of truth** for both protocols. Edit it there and nowhere else.

> For the full system (Home Assistant, the e-ink display, wiring, the sensor
> list), see the **[top-level README](../README.md)**, section 6.

## Directory map

| Path | What it is |
| --- | --- |
| [`solarcar_can/`](solarcar_can/) | **The decoders** — `bestgo.py`, `ezkontrol.py`, and `transport.py` (the gs_usb-vs-SocketCAN layer). Edit here. |
| `monitor.py` | Combined live dashboard — both devices at once. |
| `bestgo_decode.py` | Battery-only dashboard, writes an ASC log. |
| `ezkontrol_decode.py` | Motor-controller-only dashboard, writes an ASC log. |
| `tui.py` | Shared console-dashboard rendering for the three CLIs above. |
| `can_up.sh` | Pi only: bring the `can0` interface up at a bitrate. |
| `sync_addon.py` | Copy `solarcar_can/` into the add-on (run after editing the package). |
| `requirements.txt` / `requirements-pi.txt` | Python deps for the PC (gs_usb) / the Pi (SocketCAN). |
| `ha_addons/solar-car-canbus/` | The production HA add-on (carries a vendored copy of `solarcar_can/`). |
| [`tools/`](tools/) | Bus diagnostics + SSH/deploy helpers — see the table at the bottom. |
| [`tests/`](tests/) | Golden-master decoder tests + captured bus fixtures. |
| [`specs/`](specs/) | Vendor protocol PDFs + extracted text notes. |
| [`docs/`](docs/) | Debugging history / known issues. |
| `logs/` | Local ASC captures from the decoders (git-ignored). |

## Quick start — run a live dashboard

### On a Windows PC (USB-CAN adapter direct)

```powershell
py -3.12 -m venv C:\projects\solar_car\venv
C:\projects\solar_car\venv\Scripts\python.exe -m pip install -r CANbus_data\requirements.txt
```

Plug in the SH-C31G and run a tool — no interface setup needed (the tools set
the gs_usb bit timing themselves):

```sh
python monitor.py            # combined dashboard, both devices
python ezkontrol_decode.py   # EZkontrol dashboard + ASC log
python bestgo_decode.py      # BESTGO dashboard + ASC log
```

### On the Raspberry Pi (SocketCAN)

```sh
sudo apt update && sudo apt install -y can-utils python3-venv   # can-utils optional but handy
cd ~/CANbus_data                  # wherever this directory lives
python3 -m venv ~/canbus-venv
~/canbus-venv/bin/pip install -r requirements-pi.txt

dmesg | grep -i gs_usb            # confirm the kernel sees the adapter
./can_up.sh                       # bring up can0 at 500000 bps (or: ./can_up.sh 250000)
~/canbus-venv/bin/python3 monitor.py
```

`can0` stays up until reboot or `sudo ip link set can0 down`.

> **Seeing 0 frames? Wake the battery first.** The BESTGO BMS only broadcasts
> CAN when active — under load (driving) or woken via the Smart BMS Bluetooth
> app. An idle bench pack is CAN-silent, so a listener reads **0 frames on
> every host** (Pi *and* PC alike) even with the adapter, bus, and ~60 Ω
> termination all correct. This mimics a CAN/USB/VL805 fault but isn't — it
> drove a multi-week hardware hunt, resolved 2026-06-29 by simply waking the
> pack. Always confirm a known-good listener sees frames with the pack awake
> before suspecting the adapter, kernel, or USB host.

### No hardware? Every dashboard has a dummy mode

```sh
python monitor.py -ezkontrol_dummy -bestgo_dummy
python ezkontrol_decode.py -ezkontrol_dummy
python bestgo_decode.py -bestgo_dummy
```

Using a non-default interface on Linux? Set `CAN_CHANNEL`, e.g.
`CAN_CHANNEL=can1 python monitor.py`.

## The Home Assistant add-on

`ha_addons/solar-car-canbus/` reads both devices off the shared bus and pushes
sensors to HA (see the [top-level README](../README.md) §6 for the full sensor
list and options).

**Important — the decoders are vendored.** The add-on folder contains a *copy*
of `solarcar_can/` because HA builds local add-ons with the add-on folder as
the Docker context (it can't reach files outside it). **Never edit the copy.**
Edit `CANbus_data/solarcar_can/` and run:

```sh
python sync_addon.py          # refresh the vendored copy
python sync_addon.py --check  # CI/sanity: is the copy current? (the tests check this too)
```

### Deploying an update to the Pi

1. Bump `version` in `ha_addons/solar-car-canbus/config.yaml`.
2. If you changed the package, `python sync_addon.py`.
3. Get the add-on folder onto the Pi at `/addons/solar-car-canbus/`. Either:
   - **Simple:** copy it there (Samba/SSH/VS Code), then in HA go to
     **Settings → Add-ons → (Local) Solar Car CANbus Reader → Rebuild**.
   - **Scripted from this repo** (needs `HA_HOST` + `HA_PWD` env set):
     ```sh
     # ha_push.py is NOT recursive — push the package subfolder separately if it changed
     MSYS_NO_PATHCONV=1 python tools/ha_push.py ha_addons/solar-car-canbus /addons/solar-car-canbus
     MSYS_NO_PATHCONV=1 python tools/ha_push.py ha_addons/solar-car-canbus/solarcar_can /addons/solar-car-canbus/solarcar_can
     ```
     then on the Pi: `ha store reload && ha apps update local_solarcar_canbus`.

> If `ha store`/`ha apps` operations fail with *"blocked … supervisor needs to
> be updated"*, the HAOS Supervisor is stale — run `ha supervisor update`
> first. Running add-ons keep working regardless; this only blocks rebuilds.

## Tests

```sh
python tests/test_decoders.py    # golden-master decode tests (or: pytest tests/)
python sync_addon.py --check     # is the add-on's vendored package current?
```

`tests/test_decoders.py` replays the captured fixtures in `tests/fixtures/`
through the decoders and asserts they match frozen reference output, so a
protocol edit can't silently change results.

## Diagnostics & helpers (`tools/`)

| Tool | Platform | Purpose |
| --- | --- | --- |
| `smoke_test_gsusb.py` / `smoke_test_socketcan.py` | PC / Pi | Does the adapter/interface open? |
| `sniff_gsusb.py` / `sniff_socketcan.py` | PC / Pi | Raw frame dump + ASC log. |
| `bestgo_probe.py` | PC | BESTGO bus probing / calibration. |
| `probe_timing.py` | PC | gs_usb bit-timing diagnosis (a Windows-only problem). |
| `ha_push.py` | PC | Push a folder to the Pi over SSH (deploy helper; see above). |
| `ha_run.py` | PC | Run one command on the Pi over SSH. |

## Known issues / debugging history

See [`docs/`](docs/). Most important right now: the **Pi receives 0 CAN
frames** while the same adapter + bus works on a PC — an open, well-documented
investigation in [`docs/DEBUG-pi-rx-plan-20260613.md`](docs/DEBUG-pi-rx-plan-20260613.md)
(with the ordered next-session test plan).
