"""Check the SocketCAN interface for the SH-C31G is up and carrying traffic.

Usage:
    python3 smoke_test.py [SECONDS]

Verifies the interface exists, is up, opens via python-can, then counts
frames for a few seconds (default 3). Override the interface name with
the CAN_CHANNEL environment variable.
"""
import os
import sys
import time

import can

CAN_CHANNEL = os.environ.get("CAN_CHANNEL", "can0")
listen_sec = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0

sysdir = f"/sys/class/net/{CAN_CHANNEL}"
print(f"interface: {CAN_CHANNEL}")
if not os.path.isdir(sysdir):
    print("  NOT FOUND -- plug in the SH-C31G and check 'dmesg | grep -i gs_usb'")
    sys.exit(1)

state = "?"
try:
    with open(f"{sysdir}/operstate") as f:
        state = f.read().strip()
except OSError:
    pass
print(f"  operstate: {state}")
if state == "down":
    print("  interface is DOWN -- run ../can_up.sh first")
    sys.exit(1)

try:
    bus = can.Bus(channel=CAN_CHANNEL, interface="socketcan")
except Exception as e:
    print(f"  open FAILED: {type(e).__name__}: {e}")
    sys.exit(1)
print("  opened OK")

print(f"\nlistening {listen_sec:.0f}s for frames...")
count = 0
ids = set()
deadline = time.monotonic() + listen_sec
while time.monotonic() < deadline:
    msg = bus.recv(timeout=0.5)
    if msg is not None:
        count += 1
        ids.add(msg.arbitration_id)
bus.shutdown()

print(f"  {count} frames, {len(ids)} unique IDs")
if ids:
    print("  IDs: " + ", ".join(f"0x{i:X}" for i in sorted(ids)))
    print("\nsmoke test OK")
else:
    print("  (no traffic -- bus may be idle, or nothing else is connected)")
    print("\ninterface OK, but the bus is quiet")
