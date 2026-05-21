# Raspberry Pi CAN tools — setup

SocketCAN ports of the `pc_files/` tools, for a Raspberry Pi 4 with the
SH-C31G USB-CAN adapter. The adapter is a gs_usb/candleLight device; the
Linux kernel's `gs_usb` module claims it automatically and exposes it as
a network interface named `can0`.

## 1. One-time setup

```sh
# system packages (can-utils is optional but handy: candump/cansniffer/cangen)
sudo apt update && sudo apt install -y can-utils python3-venv

# Python environment
cd ~/CANbus_data                 # wherever this repo is checked out
python3 -m venv ~/canbus-venv
~/canbus-venv/bin/pip install -r rp_files/requirements.txt
```

Confirm the kernel sees the adapter (plug it in, then):

```sh
dmesg | grep -i gs_usb           # should show the adapter being registered
ip link show can0                # the can0 interface should exist
```

## 2. Bring the CAN interface up

The bitrate is a property of the interface, not the tools. The lab bus is
500 kbps:

```sh
cd rp_files
./can_up.sh                      # 500000 bps on can0 (defaults)
./can_up.sh 250000               # or 250 kbps
```

`can0` stays up until reboot or `sudo ip link set can0 down`.

## 3. Run the tools

```sh
cd rp_files
PY=~/canbus-venv/bin/python3

$PY monitor.py                   # combined dashboard, both devices
$PY ezkontrol_decode.py          # EZkontrol dashboard + ASC log
$PY bestgo_decode.py             # BESTGO dashboard + ASC log
$PY testing/smoke_test.py        # is can0 up and carrying traffic?
$PY testing/sniff.py             # raw frame dump + log
```

No hardware? Every dashboard takes dummy flags and needs no interface:

```sh
$PY monitor.py -ezkontrol_dummy -bestgo_dummy
$PY ezkontrol_decode.py -ezkontrol_dummy
$PY bestgo_decode.py -bestgo_dummy
```

Using a different interface name? Set `CAN_CHANNEL`, e.g.
`CAN_CHANNEL=can1 $PY monitor.py`.

## Notes

- These are standalone copies of the `pc_files/` tools; the decode logic
  is duplicated by design. Keep the two in sync when a spec changes.
- ASC logs land in a `logs/` directory next to wherever you run the tool.
- To bring `can0` up automatically at boot, add a systemd-networkd unit or
  an `@reboot` cron entry that calls `can_up.sh`.
- `probe_timing.py` from `pc_files/testing/` is intentionally not ported:
  the bit-timing problem it diagnoses is a Windows/gs_usb-userspace issue
  that does not exist on Linux SocketCAN.
