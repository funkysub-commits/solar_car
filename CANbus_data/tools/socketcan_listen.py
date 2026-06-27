"""Listen on can0 (SocketCAN) and report received CAN frames.

Shop battery test for the SH-C31G on candlelight/gs_usb firmware (which the
kernel exposes as can0). Battery must be powered and on the bus. Any frames
(expect BESTGO IDs 0x351-0x379) = the Pi receives. Zero = the USB/VL805
receive issue (see docs/DEBUG-pi-rx-slcan-20260624.md).

Run inside a --net=host --privileged container that has python-can, e.g. the
`slcan-smoketest` image; can0 must already be up (see canbus_smoketest.sh).
"""
import time, can

b = can.Bus(channel="can0", interface="socketcan")
print("listening on can0 @ 500k for 12s (battery must be powered + on the bus)...")
seen, n, t0 = {}, 0, time.time()
while time.time() - t0 < 12:
    m = b.recv(timeout=1)
    if m is None:
        continue
    n += 1
    seen[m.arbitration_id] = seen.get(m.arbitration_id, 0) + 1
    if n <= 14:
        print("  0x%X  [%d]  %s" % (m.arbitration_id, m.dlc, bytes(m.data).hex()))
b.shutdown()
print()
if n:
    print("RESULT: RX WORKS - %d frames, %d IDs: %s"
          % (n, len(seen), sorted("0x%X" % i for i in seen)))
else:
    print("RESULT: 0 frames on can0. Confirm battery ON + termination ~60 ohm. "
          "If the bus is live (check on the laptop), this is the USB/VL805 "
          "receive issue - see docs/DEBUG-pi-rx-slcan-20260624.md.")
