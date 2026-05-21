import sys
import usb.core
import libusb_package

_LIBUSB_BACKEND = libusb_package.get_libusb1_backend()
_orig_find = usb.core.find
def _find_with_libusb_package(*args, **kwargs):
    kwargs.setdefault("backend", _LIBUSB_BACKEND)
    return _orig_find(*args, **kwargs)
usb.core.find = _find_with_libusb_package

import can

print(f"python-can {can.__version__}")

try:
    bus = can.Bus(interface="gs_usb", channel=0, bitrate=500000)
except Exception as e:
    print(f"OPEN FAILED: {type(e).__name__}: {e}")
    sys.exit(1)

print(f"opened: channel_info={bus.channel_info!r}")
print(f"state: {bus.state!r}")
bus.shutdown()
print("closed cleanly")
