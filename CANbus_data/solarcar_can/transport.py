"""Uniform CAN frame source, transport selected by the CAN_TRANSPORT env var.

  * slcan     -- DEFAULT (since 2026-06). The SH-C31G runs slcan firmware and
                 enumerates as a CDC-serial port (COMx on Windows,
                 /dev/ttyACM0 on Linux); python-can's slcan backend speaks the
                 ASCII slcan protocol over it. We moved to slcan because the
                 gs_usb path stopped delivering RX on the HAOS 6.12 kernel --
                 see docs/SLCAN_MIGRATION_PLAN.md and
                 docs/DEBUG-pi-rx-plan-20260613.md for the full story.

  * gsusb     -- SH-C31G on the factory candleLight/gs_usb firmware, via direct
                 libusb (no SocketCAN). Kept for reflash-back. python-can
                 4.6.1 can't compute valid timing at this adapter's 170 MHz CAN
                 clock, so we set the registers explicitly:
                 sync=1 prop=1 phase1=13 phase2=2 sjw=2 => 17 tq;
                 brp=20 -> 500000 bps, brp=40 -> 250000 bps.

  * socketcan -- the kernel gs_usb module exposes the adapter as can0 (Linux).
                 Kept for reflash-back; the bitrate is set at interface
                 bring-up (see can_up.sh / the add-on's run.sh).

open_transport() returns the selected transport. All expose
recv(timeout)/describe()/close() and yield Frame(arbitration_id, data,
is_extended).
"""
import os
import time
from collections import namedtuple

Frame = namedtuple("Frame", "arbitration_id data is_extended")

GSUSB_FCLK = 170_000_000   # the SH-C31G's reported CAN clock
GSUSB_TQ = 17              # sync(1) + prop(1) + phase1(13) + phase2(2)


class TransportError(Exception):
    """Raised when a CAN transport can't be opened; message is user-facing."""


class SocketCanTransport:
    """SocketCAN via python-can. The interface must already be up (the
    bitrate is set at bring-up -- see can_up.sh / the add-on's run.sh)."""

    def __init__(self, channel):
        import can
        ensure_socketcan(channel)
        try:
            self.bus = can.Bus(channel=channel, interface="socketcan")
        except Exception as e:
            raise TransportError(f"Could not open CAN interface '{channel}': {e}")
        self.channel = channel

    def describe(self):
        return self.channel

    def recv(self, timeout):
        """Return the next Frame, or None if `timeout` seconds pass."""
        msg = self.bus.recv(timeout=timeout)
        if msg is None:
            return None
        return Frame(msg.arbitration_id, bytes(msg.data), msg.is_extended_id)

    def close(self):
        try:
            self.bus.shutdown()
        except Exception:
            pass


class GsUsbTransport:
    """Direct gs_usb access (Windows), with explicit bit timing."""

    def __init__(self, bitrate):
        # Route pyusb through the bundled libusb so no system install is needed.
        import usb.core
        import libusb_package
        backend = libusb_package.get_libusb1_backend()
        orig_find = usb.core.find

        def find_with_backend(*args, **kwargs):
            kwargs.setdefault("backend", backend)
            return orig_find(*args, **kwargs)
        usb.core.find = find_with_backend

        from gs_usb.gs_usb import GsUsb
        from gs_usb.gs_usb_frame import GsUsbFrame

        if GSUSB_FCLK % (bitrate * GSUSB_TQ):
            raise TransportError(
                f"{bitrate} bps is not reachable with {GSUSB_TQ} tq at "
                f"{GSUSB_FCLK / 1e6:.0f} MHz (use 500000 or 250000)")
        brp = GSUSB_FCLK // (bitrate * GSUSB_TQ)

        devs = GsUsb.scan()
        if not devs:
            raise TransportError(
                "No gs_usb adapter found. Plug in the SH-C31G, or simulate "
                "the devices with the -*_dummy flags.")
        self.dev = devs[0]
        self.dev.set_timing(prop_seg=1, phase_seg1=13, phase_seg2=2,
                            sjw=2, brp=brp)
        self.dev.start()
        self.bitrate = bitrate
        self._frame_cls = GsUsbFrame
        self._fr = GsUsbFrame()
        self._usb_error = __import__("usb.core", fromlist=["USBError"]).USBError

    def describe(self):
        return f"gs_usb {self.bitrate // 1000} kbps"

    def _safe_read(self, timeout_ms):
        """Workaround for gs_usb 0.3.1: dev.read() passes the exact expected
        frame size to the USB transfer, so a short packet from the adapter
        raises struct.error in unpack_into. Request a buffer larger than any
        single frame and dispatch on the actual length returned."""
        try:
            data = self.dev.gs_usb.read(0x81, 64, timeout_ms)
        except self._usb_error:
            return False
        n = len(data)
        fr = self._fr
        if n == fr.__sizeof__(False):
            self._frame_cls.unpack_into(fr, bytes(data), False)
            return True
        if n == fr.__sizeof__(True):
            self._frame_cls.unpack_into(fr, bytes(data), True)
            return True
        return False

    def recv(self, timeout):
        """Return the next Frame, or None if `timeout` seconds pass."""
        if not self._safe_read(max(1, int(timeout * 1000))):
            return None
        fr = self._fr
        return Frame(fr.arbitration_id, bytes(fr.data[:fr.can_dlc]),
                     fr.is_extended_id)

    def close(self):
        try:
            self.dev.stop()
        except Exception:
            pass


def ensure_socketcan(channel):
    """Raise TransportError with a clear message if `channel` isn't ready."""
    sysdir = f"/sys/class/net/{channel}"
    if not os.path.isdir(sysdir):
        raise TransportError(
            f"CAN interface '{channel}' not found. Plug in the SH-C31G and "
            f"bring the bus up (see can_up.sh), or simulate the devices "
            f"with the -*_dummy flags.")
    try:
        with open(f"{sysdir}/operstate") as f:
            if f.read().strip() == "down":
                raise TransportError(
                    f"CAN interface '{channel}' is down. Run:  ./can_up.sh")
    except OSError:
        pass


class SlcanTransport:
    """slcan (serial-line CAN) via python-can. The SH-C31G on slcan firmware
    enumerates as a CDC-serial port; python-can's slcan backend speaks the
    ASCII slcan protocol over it. `bitrate` is sent as the slcan S-command
    (500000 -> S6, 250000 -> S5)."""

    def __init__(self, port, bitrate):
        import can
        try:
            self.bus = can.Bus(interface="slcan", channel=port, bitrate=bitrate)
        except Exception as e:
            raise TransportError(f"Could not open slcan adapter on '{port}': {e}")
        self.port = port
        self.bitrate = bitrate

    def describe(self):
        return f"slcan {self.port} {self.bitrate // 1000} kbps"

    def recv(self, timeout):
        """Return the next Frame, or None if `timeout` seconds pass."""
        msg = self.bus.recv(timeout=timeout)
        if msg is None:
            return None
        return Frame(msg.arbitration_id, bytes(msg.data), msg.is_extended_id)

    def close(self):
        try:
            self.bus.shutdown()
        except Exception:
            pass


def find_slcan_port():
    """Locate the slcan adapter's serial port. Honors the CAN_PORT env var;
    otherwise scans pyserial's port list, preferring a CANable/STM/USB-serial
    device, then any /dev/ttyACM* or COM port. Returns None if none found."""
    port = os.environ.get("CAN_PORT")
    if port:
        return port
    try:
        from serial.tools import list_ports
    except ImportError:
        return None
    ports = list(list_ports.comports())

    def looks_like_adapter(p):
        blob = " ".join(filter(None, (
            p.description, getattr(p, "manufacturer", None),
            getattr(p, "product", None)))).lower()
        return any(k in blob for k in
                   ("canable", "slcan", "stm", "usb serial", "cdc"))

    for p in ports:                       # best: a recognisable adapter
        if looks_like_adapter(p):
            return p.device
    for p in ports:                       # next: any USB-serial / ACM / COM
        d = p.device
        if d.startswith("/dev/ttyACM") or d.upper().startswith("COM"):
            return d
    return ports[0].device if ports else None


def open_transport(bitrate=500_000, channel=None):
    """Open the CAN transport selected by CAN_TRANSPORT (default 'slcan').

    slcan     -> SlcanTransport on the serial port (CAN_PORT, else auto-detect).
    gsusb     -> GsUsbTransport (direct libusb) at `bitrate`.
    socketcan -> SocketCanTransport on `channel` (CAN_CHANNEL, default can0).
    """
    transport = os.environ.get("CAN_TRANSPORT", "slcan").lower()
    if transport == "slcan":
        port = find_slcan_port()
        if not port:
            raise TransportError(
                "No slcan serial port found. Plug in the SH-C31G (slcan "
                "firmware), set CAN_PORT, or simulate with the -*_dummy flags.")
        return SlcanTransport(port, bitrate)
    if transport == "gsusb":
        return GsUsbTransport(bitrate)
    if transport == "socketcan":
        channel = channel or os.environ.get("CAN_CHANNEL", "can0")
        return SocketCanTransport(channel)
    raise TransportError(
        f"Unknown CAN_TRANSPORT '{transport}' (use slcan, gsusb, or socketcan)")


class DummyFrameSource:
    """Releases simulated broadcast cycles paced like the real device.

    `gen` is a device module's dummy_frames(t) generator; `period` its
    broadcast cycle time (bestgo.DUMMY_PERIOD / ezkontrol.DUMMY_PERIOD).
    """

    def __init__(self, gen, period):
        self._gen = gen
        self._period = period
        self._t0 = time.monotonic()
        self._queue = []
        self._next = 0.0

    def read(self):
        """Return the next simulated (arb_id, data), or None if not yet due."""
        now = time.monotonic()
        if not self._queue and now >= self._next:
            self._queue.extend(self._gen(now - self._t0))
            self._next = now + self._period
        return self._queue.pop(0) if self._queue else None
