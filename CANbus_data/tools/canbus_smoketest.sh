#!/usr/bin/env bash
# Solar Car - SocketCAN battery smoke test for the SH-C31G on candlelight/gs_usb
# firmware (kernel gs_usb exposes it as can0). This is the shop battery test
# when the adapter is on candlelight (not slcan).
#
# PRECONDITIONS:
#   - Adapter on candlelight/gs_usb (lsusb: 1d50:606f), NOT DFU (0483:df11) and
#     NOT slcan (16d0:117e). Plug it DIRECT into a Pi USB port (the powered hub
#     made delivery WORSE - see docs/DEBUG-pi-rx-slcan-20260624.md).
#   - Battery POWERED and on the bus. Battery-only = 2 nodes => adapter 120 ohm
#     termination ON (~60 ohm across CAN_H/CAN_L).
#   - Advanced SSH add-on with Protection Mode OFF (Docker + host access).
#
# A deployed copy lives at /config/canbus_smoketest.sh on the Pi:
#   bash /config/canbus_smoketest.sh
echo "bringing up can0 @ 500k (gs_usb)..."
sudo docker run --rm --privileged --pid=host alpine nsenter -t 1 -m -u -n -i sh -c \
  'ip link set can0 down 2>/dev/null; ip link set can0 type can bitrate 500000 && ip link set can0 up && echo "can0 up" || echo "can0 bring-up FAILED - adapter in DFU (replug) or gs_usb not bound?"'
echo "listening on can0..."
sudo docker run --rm --net=host --privileged \
  -v /mnt/data/supervisor/homeassistant:/cfg slcan-smoketest python /cfg/socketcan_listen.py
