#!/usr/bin/env bash
# Solar Car - slcan RX smoke test for a clean HAOS (no CAN add-on installed).
#
# This is the T0 experiment from docs/DEBUG-pi-rx-slcan-20260624.md: does the Pi
# receive CAN over slcan on a fresh install, with all the old debugging cruft
# gone? It runs python-can's slcan backend in a throwaway Docker container
# (image `slcan-smoketest` = python:3.12-slim + python-can + pyserial, built
# offline-ready so the shop needs no internet) against the adapter on
# /dev/ttyACM0.
#
# PRECONDITIONS:
#   - Advanced SSH & Web Terminal add-on, Protection Mode OFF (Docker + /dev).
#   - Adapter on slcan firmware, enumerated as /dev/ttyACM0 (lsusb: 16d0:117e).
#   - Battery POWERED and on the bus. Battery-only = 2 nodes => adapter 120 ohm
#     termination ON (~60 ohm across CAN_H/CAN_L, measured power-off).
#   - The `slcan-smoketest` image present (sudo docker images | grep slcan).
#
# A deployed copy lives at /config/slcan_smoketest.sh on the prepped Pi; run it
# with:  bash /config/slcan_smoketest.sh
#
# Any frames printed (expect BESTGO IDs 0x351-0x379) = the Pi receives. Zero =
# still broken -> next is T1 (older HAOS) per the debug doc.
echo "slcan RX smoke test on /dev/ttyACM0 @ 500k - listening 10s..."
sudo docker run --rm -i --device=/dev/ttyACM0 slcan-smoketest python - <<'PY'
import time, can
b = can.Bus(interface="slcan", channel="/dev/ttyACM0", bitrate=500000)
print("bus opened; listening 10s...")
seen, n, t0 = {}, 0, time.time()
while time.time() - t0 < 10:
    f = b.recv(timeout=1)
    if f is None:
        continue
    n += 1
    seen[f.arbitration_id] = seen.get(f.arbitration_id, 0) + 1
    if n <= 12:
        print("  %s  [%d]  %s" % (hex(f.arbitration_id), f.dlc, f.data.hex()))
b.shutdown()
print()
if n:
    print("RESULT: RX WORKS - %d frames, %d IDs: %s" % (n, len(seen), sorted(hex(i) for i in seen)))
else:
    print("RESULT: 0 frames - Pi still not receiving. Confirm battery ON + termination ~60ohm; else T1 (older HAOS).")
PY
