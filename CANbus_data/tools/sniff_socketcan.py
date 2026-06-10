"""Live CAN sniffer + ASC logger for the Raspberry Pi (SocketCAN).

Usage:
    python3 sniff.py [LOG_PATH] [DURATION_SEC]

Prints every frame on the bus and logs it in Vector ASC format. LOG_PATH
defaults to logs/sniff-<timestamp>.asc. DURATION_SEC omitted = run until
Ctrl+C. Override the interface name with the CAN_CHANNEL env var.

Bring the interface up first -- see ../can_up.sh.

For a quick look without Python, the can-utils package also works:
    candump can0           # every frame
    cansniffer can0        # live-updating per-ID view
"""
import os
import sys
import time
import signal
from datetime import datetime

import can

CAN_CHANNEL = os.environ.get("CAN_CHANNEL", "can0")

if len(sys.argv) > 1:
    log_path = sys.argv[1]
else:
    log_path = f"logs/sniff-{datetime.now():%Y%m%d-%H%M%S}.asc"
duration_sec = float(sys.argv[2]) if len(sys.argv) > 2 else None

parent = os.path.dirname(log_path)
if parent:
    os.makedirs(parent, exist_ok=True)

try:
    bus = can.Bus(channel=CAN_CHANNEL, interface="socketcan")
except Exception as e:
    sys.exit(f"Could not open CAN interface '{CAN_CHANNEL}': {e}\n"
             f"Bring it up first with ../can_up.sh")

print(f"sniffing {CAN_CHANNEL}  |  log={log_path}")
print("Ctrl+C to stop.\n")

logger = can.Logger(log_path)
printer = can.Printer()
notifier = can.Notifier(bus, [printer, logger])

stop = False
def _handle_sigint(signum, frame):
    global stop
    stop = True
signal.signal(signal.SIGINT, _handle_sigint)

deadline = (time.monotonic() + duration_sec) if duration_sec else None
try:
    while not stop:
        if deadline and time.monotonic() >= deadline:
            break
        time.sleep(0.2)
finally:
    print("\nshutting down...")
    notifier.stop()
    logger.stop()
    bus.shutdown()
    print(f"log saved: {log_path}")
