"""Minimal BESTGO battery CAN decode test -- SocketCAN, plain-text output.

Runs in a basic Docker container on the HA OS box to confirm the BESTGO
BMS frames decode correctly. No dashboard, no Home Assistant: it just
prints decoded values, so the result is readable in plain `docker run`
output / `docker logs`.

Usage:
    python3 bestgo_logtest.py [DURATION_SEC]       # default 30, 0 = forever
    python3 bestgo_logtest.py -dummy [DURATION]    # synthetic frames, no bus

Environment:
    CAN_CHANNEL   SocketCAN interface name (default can0)

See specs/bestgo_spec.txt for the frame definitions.
"""
import os
import sys
import time

CAN_CHANNEL = os.environ.get("CAN_CHANNEL", "can0")
DUMMY = "-dummy" in sys.argv
_args = [a for a in sys.argv[1:] if a != "-dummy"]
duration = float(_args[0]) if _args else 30.0

KELVIN = 273.15
ID_NAMES = {0x351: "limits", 0x355: "soc", 0x356: "meas", 0x35A: "alarms",
            0x35E: "mfr", 0x35F: "info", 0x370: "name0", 0x371: "name1",
            0x373: "cellext", 0x379: "capacity"}

# Captured real BESTGO frames (logs/bestgo-probe-20260518-195228.txt) -- used
# as the -dummy payload so a no-hardware run still exercises the real decoder.
DUMMY_FRAMES = [
    (0x351, bytes.fromhex("4002dc05d007c001")),
    (0x355, bytes.fromhex("38006400e0150000")),
    (0x356, bytes.fromhex("a0140000dc000000")),
    (0x35A, bytes(8)),
    (0x35E, b"LVaiiey\x00"),
    (0x35F, bytes.fromhex("0000010138000000")),
    (0x370, b"Lithium\x00"),
    (0x371, b"Valley\x00\x00"),
    (0x373, bytes.fromhex("e80cea0c27012801")),
    (0x379, bytes.fromhex("3800000000000000")),
]


def u16(d, o):
    return int.from_bytes(d[o:o + 2], "little")


def s16(d, o):
    return int.from_bytes(d[o:o + 2], "little", signed=True)


def txt(raw):
    out = []
    for b in raw:
        if b == 0:
            break
        out.append(chr(b) if 32 <= b < 127 else ".")
    return "".join(out).strip()


state = {}
seen = set()


def decode(arb, d):
    """Update `state` from one BESTGO frame; record the ID as seen."""
    if arb not in ID_NAMES:
        return
    seen.add(arb)
    if arb == 0x351 and len(d) >= 8:
        state.update(cvl=u16(d, 0) * 0.1, ccl=s16(d, 2) * 0.1,
                     dcl=s16(d, 4) * 0.1, dvl=u16(d, 6) * 0.1)
    elif arb == 0x355 and len(d) >= 4:
        state.update(soc=u16(d, 0), soh=u16(d, 2))
    elif arb == 0x356 and len(d) >= 6:
        state.update(v=s16(d, 0) * 0.01, i=s16(d, 2) * 0.1, t=s16(d, 4) * 0.1)
    elif arb == 0x35A and len(d) >= 8:
        a, w = d[0:4], d[4:8]
        state.update(alarms="OK" if not any(a) else a.hex(),
                     warns="OK" if not any(w) else w.hex())
    elif arb == 0x35E:
        state["mfr"] = txt(d)
    elif arb == 0x35F and len(d) >= 6:
        ver = u16(d, 2)
        state.update(fw=f"v{ver >> 8}.{ver & 0xFF}", cap=u16(d, 4))
    elif arb == 0x370:
        state["n0"] = d
    elif arb == 0x371:
        state["n1"] = d
    elif arb == 0x373 and len(d) >= 8:
        state.update(vmin=u16(d, 0), vmax=u16(d, 2),
                     tmin=u16(d, 4) - KELVIN, tmax=u16(d, 6) - KELVIN)
    elif arb == 0x379 and len(d) >= 2:
        state["cap_inst"] = u16(d, 0)


def summary(elapsed, frames):
    s = state
    parts = [f"t={elapsed:4.0f}s", f"frames={frames}", f"ids={len(seen)}/10"]
    if "v" in s:
        parts.append(f"V={s['v']:.2f}")
    if "i" in s:
        parts.append(f"I={s['i']:+.1f}A")
    if "soc" in s:
        parts.append(f"SOC={s['soc']}%")
    if "t" in s:
        parts.append(f"T={s['t']:.1f}C")
    if "vmin" in s:
        parts.append(f"cell={s['vmin']}-{s['vmax']}mV")
    if "tmin" in s:
        parts.append(f"cellT={s['tmin']:.1f}-{s['tmax']:.1f}C")
    if "alarms" in s:
        parts.append(f"alarms={s['alarms']}")
    return "  ".join(parts)


def main():
    mode = "DUMMY (synthetic frames)" if DUMMY else f"SocketCAN {CAN_CHANNEL}"
    print(f"BESTGO decode test -- {mode}", flush=True)
    print(f"running for {'until Ctrl+C' if duration == 0 else f'{duration:.0f} s'}",
          flush=True)

    bus = None
    if not DUMMY:
        import can
        try:
            bus = can.Bus(channel=CAN_CHANNEL, interface="socketcan")
        except Exception as e:
            print(f"FAILED to open {CAN_CHANNEL}: {e}", flush=True)
            return 1

    t0 = time.monotonic()
    frames = 0
    last_print = 0.0
    dummy_next = 0.0
    named = False

    while duration == 0 or time.monotonic() - t0 < duration:
        if DUMMY:
            now = time.monotonic()
            if now >= dummy_next:
                for arb, data in DUMMY_FRAMES:
                    decode(arb, data)
                    frames += 1
                dummy_next = now + 1.0
            time.sleep(0.05)
        else:
            msg = bus.recv(timeout=0.5)
            if msg is not None:
                decode(msg.arbitration_id, bytes(msg.data))
                frames += 1

        elapsed = time.monotonic() - t0
        if not named and "n0" in state and "n1" in state:
            # The two name frames are NUL-padded individually, so decode each
            # then join -- concatenating raw would terminate at the first NUL.
            name = " ".join(p for p in (txt(state["n0"]), txt(state["n1"])) if p) or "?"
            print(f"  battery: {name}  mfr={state.get('mfr', '?')}  "
                  f"fw={state.get('fw', '?')}  capacity={state.get('cap', '?')} Ah",
                  flush=True)
            named = True
        if elapsed - last_print >= 2.0:
            print("  " + summary(elapsed, frames), flush=True)
            last_print = elapsed

    if bus is not None:
        bus.shutdown()

    print(f"done -- {frames} frames, {len(seen)}/10 BESTGO IDs seen", flush=True)
    if not DUMMY and frames == 0:
        print("NO FRAMES: check the bus is wired, terminated, and the BESTGO "
              "is powered.", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
