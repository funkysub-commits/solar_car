"""Probe an unknown CAN device for bitrate and confirm comms.

Usage:
    python bestgo_probe.py

Sweeps common BMS bitrates in LISTEN-ONLY mode (so a wrong rate can't disturb
the bus). The first rate that yields valid frames is reported. Then re-opens
the bus in NORMAL mode at that rate and listens 10 s, printing every unique
arbitration ID with one sample payload and an observed period.

LISTEN-ONLY means the adapter will NOT ACK. On a 2-node bus that has only the
DUT and us, the DUT will retransmit each frame and eventually error-passive;
that's fine for the few-second probe, and we flip to NORMAL mode immediately
after locking on.

Bit timing for the SH-C31G's STM32G431 at fclk_can=170 MHz:
    tq = prop(1) + phase1(13) + phase2(2) + sync(1) = 17
    sample point = (1+1+13)/17 = 88.2%
    bitrate = 170e6 / (brp * 17)
        brp= 10 -> 1_000_000
        brp= 20 ->   500_000
        brp= 40 ->   250_000
        brp= 80 ->   125_000
        brp=100 ->   100_000
"""
import os
import sys
import time
from collections import defaultdict
from datetime import datetime

import usb.core
import libusb_package

_LIBUSB_BACKEND = libusb_package.get_libusb1_backend()
_orig_find = usb.core.find
def _find_with_libusb_package(*args, **kwargs):
    kwargs.setdefault("backend", _LIBUSB_BACKEND)
    return _orig_find(*args, **kwargs)
usb.core.find = _find_with_libusb_package

from gs_usb.gs_usb import GsUsb
from gs_usb.gs_usb_frame import GsUsbFrame
from gs_usb.constants import GS_CAN_MODE_LISTEN_ONLY, GS_CAN_MODE_NORMAL

RATES = [
    (500_000, 20),
    (250_000, 40),
    (125_000, 80),
    (100_000, 100),
    (1_000_000, 10),
]

PROBE_SECONDS = 2.5
CONFIRM_SECONDS = 10.0


def open_bus(dev, brp, listen_only):
    try:
        dev.stop()
    except Exception:
        pass
    dev.set_timing(prop_seg=1, phase_seg1=13, phase_seg2=2, sjw=2, brp=brp)
    flags = GS_CAN_MODE_LISTEN_ONLY if listen_only else GS_CAN_MODE_NORMAL
    dev.start(flags)


def drain(dev, seconds):
    """Read frames for `seconds` and return list of (ts_mono, arb, is_ext, dlc, data)."""
    frames = []
    fr = GsUsbFrame()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if dev.read(fr, 50):
            frames.append((
                time.monotonic(),
                fr.arbitration_id,
                fr.is_extended_id,
                fr.can_dlc,
                bytes(fr.data[:fr.can_dlc]),
            ))
    return frames


def fmt_id(arb, is_ext):
    return f"0x{arb:08X}" if is_ext else f"    0x{arb:03X}"


def main():
    devs = GsUsb.scan()
    if not devs:
        print("No gs_usb device found. Is the SH-C31G plugged in?", file=sys.stderr)
        return 2
    dev = devs[0]
    print(f"Device: {dev}")
    cap = dev.device_capability
    print(f"  fclk_can = {cap.fclk_can:_} Hz")
    print()

    print("=== Bitrate sweep (LISTEN-ONLY) ===")
    found = None
    for bps, brp in RATES:
        print(f"  {bps:>7} bps (brp={brp}) ... ", end="", flush=True)
        try:
            open_bus(dev, brp, listen_only=True)
        except Exception as e:
            print(f"open failed: {e}")
            continue
        frames = drain(dev, PROBE_SECONDS)
        ids = {f[1] for f in frames}
        print(f"{len(frames):>4} frames, {len(ids):>2} unique IDs")
        if frames and found is None:
            found = (bps, brp, frames)

    try:
        dev.stop()
    except Exception:
        pass

    if not found:
        print()
        print("No frames at any rate. Things to check:")
        print("  - BMS power: is the pack on / wake pin asserted?")
        print("  - Wiring: CAN_H/CAN_L not swapped, both 120 ohm terminations present")
        print("  - Some BMS only TX after a wake/handshake frame from a master")
        print("  - Try lower rates (50k, 20k) if this is an industrial pack")
        return 1

    bps, brp, _ = found
    print()
    print(f"=== Locked on {bps} bps. Reopening in NORMAL mode for {CONFIRM_SECONDS:.0f}s ===")
    open_bus(dev, brp, listen_only=False)
    frames = drain(dev, CONFIRM_SECONDS)
    try:
        dev.stop()
    except Exception:
        pass

    if not frames:
        print("0 frames in normal mode (saw frames in listen-only). The BMS may have")
        print("gone bus-off from un-ACKed retries during probing. Unplug+replug the BMS")
        print("CAN side or power-cycle it, then rerun.")
        return 1

    by_id = defaultdict(list)
    for ts, arb, ext, dlc, data in frames:
        by_id[(arb, ext, dlc)].append((ts, data))

    print(f"  {len(frames)} frames, {len(by_id)} unique IDs over {CONFIRM_SECONDS:.0f}s")
    print()
    print(f"  {'ID':<14} {'DLC':>3}  {'count':>6}  {'period_ms':>9}  payload (first sample)")
    print(f"  {'-'*14} {'-'*3}  {'-'*6}  {'-'*9}  ----------------")
    for (arb, ext, dlc), entries in sorted(by_id.items(), key=lambda kv: -len(kv[1])):
        n = len(entries)
        if n >= 2:
            span = entries[-1][0] - entries[0][0]
            period_ms = (span / (n - 1)) * 1000.0
            period_s = f"{period_ms:9.1f}"
        else:
            period_s = "     n/a"
        sample = entries[0][1].hex()
        print(f"  {fmt_id(arb, ext)} {dlc:>3}  {n:>6}  {period_s}  {sample}")

    os.makedirs("logs", exist_ok=True)
    log_path = f"logs/bestgo-probe-{datetime.now():%Y%m%d-%H%M%S}.txt"
    with open(log_path, "w") as f:
        f.write(f"BESTGO probe @ {bps} bps  fclk_can={cap.fclk_can}\n")
        f.write(f"Captured {len(frames)} frames over ~{CONFIRM_SECONDS:.0f}s in NORMAL mode\n\n")
        for ts, arb, ext, dlc, data in frames:
            tag = "X" if ext else "S"
            f.write(f"{ts:.6f} {tag} {fmt_id(arb, ext)} [{dlc}] {data.hex()}\n")
    print()
    print(f"  raw log: {log_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
