# CAN tools — setup

The dashboards and decoders in this directory run unchanged on a Windows PC
(direct gs_usb USB access) and on a Raspberry Pi (SocketCAN) — the
`solarcar_can.transport` module picks the right path automatically. The
frame protocols live once, in `solarcar_can/`; the HA add-on under
`ha_addons/solar-car-canbus/` uses a vendored copy of the same package
(see `sync_addon.py`).

## Windows PC

```powershell
py -3.12 -m venv C:\projects\solar_car\venv
C:\projects\solar_car\venv\Scripts\python.exe -m pip install -r CANbus_data\requirements.txt
```

Plug in the SH-C31G and run the tools (no interface bring-up needed; the
tools set the gs_usb bit timing themselves).

## Raspberry Pi

```sh
# system packages (can-utils is optional but handy: candump/cansniffer/cangen)
sudo apt update && sudo apt install -y can-utils python3-venv

cd ~/CANbus_data                 # wherever this directory is copied
python3 -m venv ~/canbus-venv
~/canbus-venv/bin/pip install -r requirements-pi.txt
```

Confirm the kernel sees the adapter (plug it in, then):

```sh
dmesg | grep -i gs_usb           # should show the adapter being registered
ip link show can0                # the can0 interface should exist
```

Bring the CAN interface up — the bitrate is a property of the interface on
Linux, not the tools. The lab bus is 500 kbps:

```sh
./can_up.sh                      # 500000 bps on can0 (defaults)
./can_up.sh 250000               # or 250 kbps
```

`can0` stays up until reboot or `sudo ip link set can0 down`. To bring it
up automatically at boot, add a systemd-networkd unit or an `@reboot` cron
entry that calls `can_up.sh`.

## Run the tools (both platforms)

```sh
python monitor.py                # combined dashboard, both devices
python ezkontrol_decode.py       # EZkontrol dashboard + ASC log
python bestgo_decode.py          # BESTGO dashboard + ASC log
```

No hardware? Every dashboard takes dummy flags and needs no interface:

```sh
python monitor.py -ezkontrol_dummy -bestgo_dummy
python ezkontrol_decode.py -ezkontrol_dummy
python bestgo_decode.py -bestgo_dummy
```

Using a different interface name on Linux? Set `CAN_CHANNEL`, e.g.
`CAN_CHANNEL=can1 python monitor.py`.

## Diagnostics (`tools/`)

| Tool | Platform | Purpose |
| --- | --- | --- |
| `sniff_gsusb.py` / `sniff_socketcan.py` | PC / Pi | raw frame dump + ASC log |
| `smoke_test_gsusb.py` / `smoke_test_socketcan.py` | PC / Pi | is the adapter/interface alive? |
| `bestgo_probe.py` | PC | BESTGO bus probing/calibration |
| `probe_timing.py` | PC | gs_usb bit-timing diagnosis (Windows-only problem) |

## Tests

```sh
python tests/test_decoders.py    # golden-master decoder tests (or: pytest tests/)
python sync_addon.py --check     # is the add-on's vendored package current?
```

After editing `solarcar_can/`, run `python sync_addon.py` and rebuild the
`solar-car-canbus` add-on on the Pi.
