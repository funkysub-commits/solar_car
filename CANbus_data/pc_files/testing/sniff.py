"""Live CAN sniffer + logger for the SH-C31G on Windows.

Usage:
    python sniff.py [BITRATE] [LOG_PATH] [DURATION_SEC]

Defaults: 250000 bps, logs/sniff-<timestamp>.asc, no duration limit (Ctrl+C to stop).
"""
import os
import sys
import time
import signal
from datetime import datetime

import usb.core
import libusb_package

_LIBUSB_BACKEND = libusb_package.get_libusb1_backend()
_orig_find = usb.core.find
def _find_with_libusb_package(*args, **kwargs):
    kwargs.setdefault("backend", _LIBUSB_BACKEND)
    return _orig_find(*args, **kwargs)
usb.core.find = _find_with_libusb_package

import can
import can.bit_timing
# python-can's strict mode caps brp at 32 (ISO 11898 "minimum required range"),
# which rejects the only viable 250 kbps solution at this device's 170 MHz clock
# (brp=40). The SH-C31G actually supports brp up to 512.
can.bit_timing.BitTiming._restrict_to_minimum_range = lambda self: None

BITRATE = int(sys.argv[1]) if len(sys.argv) > 1 else 250_000

if len(sys.argv) > 2:
    log_path = sys.argv[2]
else:
    log_path = f"logs/sniff-{datetime.now():%Y%m%d-%H%M%S}.asc"
parent = os.path.dirname(log_path)
if parent:
    os.makedirs(parent, exist_ok=True)

duration_sec = float(sys.argv[3]) if len(sys.argv) > 3 else None

print(f"python-can {can.__version__}  |  bitrate={BITRATE}  |  log={log_path}")

bus = can.Bus(interface="gs_usb", channel=0, bitrate=BITRATE)
print(f"bus open: {bus.channel_info!r}, state={bus.state!r}")
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
