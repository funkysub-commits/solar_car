#!/usr/bin/env bash
# Bring up the SocketCAN interface for the SH-C31G USB-CAN adapter.
#
# Usage:  ./can_up.sh [BITRATE] [INTERFACE]
# Defaults: 500000 bps on can0 (the shared lab bus).
#
# The SH-C31G is a gs_usb/candleLight device; the Linux kernel claims it
# automatically and exposes it as a network interface. This script just
# sets the bitrate and brings the interface up. It stays up until reboot
# or 'sudo ip link set <iface> down'.
set -e

BITRATE="${1:-500000}"
IFACE="${2:-can0}"

if ! ip link show "$IFACE" >/dev/null 2>&1; then
    echo "Interface '$IFACE' not found. Is the SH-C31G plugged in?" >&2
    echo "Check:  dmesg | grep -i gs_usb" >&2
    exit 1
fi

sudo ip link set "$IFACE" down 2>/dev/null || true
sudo ip link set "$IFACE" type can bitrate "$BITRATE"
sudo ip link set "$IFACE" up

echo "$IFACE is up at ${BITRATE} bps"
ip -details -statistics link show "$IFACE" | sed -n '1,3p'
