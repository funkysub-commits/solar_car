"""BESTGO battery CAN decode test over the USERSPACE gs_usb path.

Same decoder and output as bestgo_logtest.py, but it talks to the SH-C31G
adapter directly through the gs_usb userspace library (pyusb + libusb)
instead of the kernel SocketCAN/gs_usb driver.

Why: on the HA OS box (HAOS kernel 6.12.47, CANable2 / STM32G431 FDCAN
adapter) the kernel gs_usb + SocketCAN RX path receives 0 frames even with
correct bit-timing, while this exact userspace path receives fine on the PC
(see pc_files/bestgo_probe.py). This test reuses that working transport so
the bus can be confirmed on the Pi, and doubles as the prototype for fixing
the solar-car-canbus add-on.

The kernel gs_usb driver claims the adapter (that's what creates can0), so
libusb cannot open it until that driver is unbound -- entrypoint_gsusb.sh
does the unbind before this runs.

Usage:
    python3 bestgo_gsusb_test.py [DURATION_SEC]      # default 15, 0 = forever
    python3 bestgo_gsusb_test.py -dummy [DURATION]   # synthetic frames, no bus

500 kbps timing for the SH-C31G's 170 MHz CAN clock (sample point 88.2%):
    prop_seg=1, phase_seg1=13, phase_seg2=2, sjw=2, brp=20
"""
import sys
import time

import usb.core
import libusb_package

# Route pyusb through libusb-package's bundled libusb (no system install).
_LIBUSB_BACKEND = libusb_package.get_libusb1_backend()
_orig_find = usb.core.find
def _find_with_libusb_package(*args, **kwargs):
    kwargs.setdefault("backend", _LIBUSB_BACKEND)
    return _orig_find(*args, **kwargs)
usb.core.find = _find_with_libusb_package

from gs_usb.gs_usb import GsUsb
from gs_usb.gs_usb_frame import GsUsbFrame
from gs_usb.constants import GS_CAN_MODE_NORMAL

# Reuse the proven decoder / summary / dummy frames -- only the transport
# differs from bestgo_logtest.py.
import bestgo_logtest as bg

BRP_500K = 20

DUMMY = "-dummy" in sys.argv
_args = [a for a in sys.argv[1:] if a != "-dummy"]
duration = float(_args[0]) if _args else 15.0


def safe_read(dev, frame, timeout_ms, stats):
    """Workaround for gs_usb 0.3.1: dev.read() passes the exact expected frame
    size to the USB transfer, so a short packet from the adapter raises
    struct.error in unpack_into. Request a 64-byte buffer and dispatch on the
    actual length returned (copied from pc_files/bestgo_probe.py).

    A libusb TIMEOUT (errno 110) is the normal "no frame this poll" case on a
    quiet bus and is counted separately from real USB errors (busy / no-device
    / pipe), which would mean the interface isn't actually readable."""
    try:
        data = dev.gs_usb.read(0x81, 64, timeout_ms)
    except usb.core.USBError as e:
        msg = str(e).lower()
        if e.errno == 110 or "time" in msg:
            stats["timeouts"] += 1
        else:
            stats["errors"] += 1
            if stats["first_error"] is None:
                stats["first_error"] = repr(e)
        return False
    n = len(data)
    if n == frame.__sizeof__(False):
        GsUsbFrame.unpack_into(frame, bytes(data), False)
        return True
    if n == frame.__sizeof__(True):
        GsUsbFrame.unpack_into(frame, bytes(data), True)
        return True
    stats["shortreads"] += 1
    return False


def run_real():
    devs = GsUsb.scan()
    if not devs:
        print("No gs_usb device found (1d50:606f). Is the SH-C31G plugged in, "
              "out of DFU mode, and the kernel gs_usb driver unbound?", flush=True)
        return 2
    dev = devs[0]
    # Belt-and-suspenders: free the interface from any kernel driver still bound
    # (entrypoint_gsusb.sh normally already unbound it via sysfs).
    try:
        if dev.gs_usb.is_kernel_driver_active(0):
            dev.gs_usb.detach_kernel_driver(0)
    except Exception:
        pass

    cap = dev.device_capability
    print(f"BESTGO decode test -- userspace gs_usb  {dev}", flush=True)
    print(f"  fclk_can = {cap.fclk_can:_} Hz   timing: brp={BRP_500K} "
          f"(500 kbps, sample point 88.2%)", flush=True)
    print(f"running for {'until Ctrl+C' if duration == 0 else f'{duration:.0f} s'}",
          flush=True)

    try:
        dev.stop()
    except Exception:
        pass
    dev.set_timing(prop_seg=1, phase_seg1=13, phase_seg2=2, sjw=2, brp=BRP_500K)
    dev.start(GS_CAN_MODE_NORMAL)

    fr = GsUsbFrame()
    stats = {"timeouts": 0, "errors": 0, "shortreads": 0, "first_error": None}
    t0 = time.monotonic()
    frames = 0
    last_print = 0.0
    named = False
    try:
        while duration == 0 or time.monotonic() - t0 < duration:
            if safe_read(dev, fr, 50, stats):
                bg.decode(fr.arbitration_id, bytes(fr.data[:fr.can_dlc]))
                frames += 1
            elapsed = time.monotonic() - t0
            if not named and "n0" in bg.state and "n1" in bg.state:
                name = " ".join(p for p in (bg.txt(bg.state["n0"]),
                                            bg.txt(bg.state["n1"])) if p) or "?"
                print(f"  battery: {name}  mfr={bg.state.get('mfr', '?')}  "
                      f"fw={bg.state.get('fw', '?')}  "
                      f"capacity={bg.state.get('cap', '?')} Ah", flush=True)
                named = True
            if elapsed - last_print >= 2.0:
                print("  " + bg.summary(elapsed, frames), flush=True)
                last_print = elapsed
    finally:
        try:
            dev.stop()
        except Exception:
            pass

    print(f"done -- {frames} frames, {len(bg.seen)}/10 BESTGO IDs seen", flush=True)
    print(f"  read stats: timeouts={stats['timeouts']} usb_errors={stats['errors']} "
          f"shortreads={stats['shortreads']}", flush=True)
    if stats["first_error"]:
        print(f"  first usb error: {stats['first_error']}", flush=True)
    if frames == 0:
        if stats["errors"] and not stats["timeouts"]:
            print("NO FRAMES -- and every read raised a USB error (not a timeout). "
                  "The interface is not actually readable: libusb claim / kernel "
                  "unbind problem inside the container, NOT the bus.", flush=True)
        else:
            print("NO FRAMES -- reads are timing out cleanly, so the adapter is "
                  "open and listening but hears nothing. The bus is silent from "
                  "the Pi adapter's view (battery not transmitting, or a physical "
                  "/ grounding / power difference vs the PC). Re-run the PC probe "
                  "to confirm the battery is still transmitting at all.", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    if DUMMY:
        # Decoder-only sanity check; delegate to the SocketCAN test's dummy path.
        sys.exit(bg.main())
    sys.exit(run_real())
