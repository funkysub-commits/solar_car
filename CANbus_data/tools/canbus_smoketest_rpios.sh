#!/usr/bin/env bash
# Solar Car - CAN battery smoke test on Raspberry Pi OS (candlelight/gs_usb -> can0).
# The clean, non-HAOS test: does the Pi receive the BESTGO battery on RPi OS
# (kernel 6.18.x, latest VL805 fw)? Uses native can-utils, no containers.
#
# PRECONDITIONS:
#   - Adapter on candlelight/gs_usb (lsusb: 1d50:606f) -> kernel makes can0.
#   - Battery POWERED and on the bus. Battery-only = 2 nodes => adapter 120 ohm
#     termination ON (~60 ohm across CAN_H/CAN_L, measured power-off).
#
# Result reading:
#   frames (IDs 0x351-0x379) -> RX WORKS on RPi OS -> the HAOS failure is
#       HAOS-specific (config/containerization), NOT the VL805/kernel.
#   0 frames (and the laptop sees the bus) -> it's the VL805/kernel hardware,
#       confirmed on a non-HAOS OS too.
echo "bringing up can0 @ 500k..."
sudo ip link set can0 down 2>/dev/null
sudo ip link set can0 up type can bitrate 500000 || { echo "can0 bring-up FAILED (adapter in DFU? gs_usb not bound?)"; exit 1; }
echo "listening 12s on can0 (battery must be powered + on the bus)..."
cap=$(timeout 12 candump can0)
n=$(printf '%s\n' "$cap" | grep -c 'can0')
ids=$(printf '%s\n' "$cap" | awk '$1=="can0"{print $2}' | sort -u | tr '\n' ' ')
printf '%s\n' "$cap" | head -14
echo "----"
if [ "$n" -gt 0 ]; then
  echo "RESULT: RX WORKS - $n frames, IDs: $ids"
else
  echo "RESULT: 0 frames. Confirm battery ON + termination ~60 ohm. If the laptop"
  echo "sees this bus, 0 here means the VL805/kernel USB issue persists on RPi OS too."
fi
