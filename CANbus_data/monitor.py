"""Combined live dashboard for both solar-car CAN devices.

Shows the EZkontrol B48800 motor controller and the BESTGO battery (Lithium
Valley BMS) in one stacked dashboard. Works on Windows (gs_usb direct) and
the Raspberry Pi (SocketCAN) -- solarcar_can.transport picks the right path.

Both devices sit on a SINGLE shared CAN bus at 500 kbps. They coexist
because their ID ranges don't overlap: the EZkontrol sends 29-bit extended
IDs (0x180117EF / 0x180217EF) and the BESTGO sends 11-bit standard IDs
(0x351..0x379). One adapter on that bus sees, and decodes, both.

Usage:
    python monitor.py [DURATION_SEC]
    python monitor.py -ezkontrol_dummy -bestgo_dummy   (no hardware at all)
    python monitor.py -bestgo_dummy                    (EZkontrol live only)
    python monitor.py -ezkontrol_dummy                 (BESTGO live only)

DURATION_SEC = 0 / omitted means run until Ctrl+C.

A -*_dummy flag simulates that device instead of decoding it from the bus
-- use it for whichever device isn't connected yet. With both flags no
adapter is opened at all. This tool does not write ASC logs; use
bestgo_decode.py / ezkontrol_decode.py to log the bus to a file.

On Linux, bring the interface up first (see can_up.sh) and override its
name with the CAN_CHANNEL environment variable if it isn't can0.
"""
import sys
import time
import signal
from collections import deque

from solarcar_can import bestgo, ezkontrol
from solarcar_can.bestgo import BestgoDecoder
from solarcar_can.ezkontrol import EzkontrolDecoder
from solarcar_can.transport import open_transport, DummyFrameSource, TransportError
import tui

# --- command line -------------------------------------------------------------
EZ_DUMMY = "-ezkontrol_dummy" in sys.argv
BG_DUMMY = "-bestgo_dummy" in sys.argv
_args = [a for a in sys.argv[1:] if a not in ("-ezkontrol_dummy", "-bestgo_dummy")]
duration_sec = float(_args[0]) if _args else 0.0
if duration_sec == 0.0:
    duration_sec = None

BITRATE = 500_000   # the shared lab bus


class Channel:
    """One device on the shared bus: panel + decoder (+ dummy source)."""

    def __init__(self, name, dummy, decoder, dummy_gen, dummy_period,
                 renderer, no_highlight=()):
        self.name = name
        self.dummy = dummy
        self.decoder = decoder
        self.renderer = renderer
        self.source = DummyFrameSource(dummy_gen, dummy_period) if dummy else None
        self.panel = tui.Panel(no_highlight)
        self.rate_window = deque()   # frame monotonic timestamps, last ~1 s
        self.last_frame = 0.0

    def handle(self, arb, data):
        """Decode a frame into the panel. Returns True if the ID was ours."""
        fields = self.decoder.decode(arb, data)
        if fields is None:
            return False
        self.panel.update(fields)
        return True

    def mark(self, now):
        self.rate_window.append(now)
        cutoff = now - 1.0
        while self.rate_window and self.rate_window[0] < cutoff:
            self.rate_window.popleft()
        self.last_frame = now

    def fps(self):
        return len(self.rate_window)


ez = Channel("EZkontrol", EZ_DUMMY, EzkontrolDecoder(),
             ezkontrol.dummy_frames, ezkontrol.DUMMY_PERIOD,
             tui.render_ezkontrol, no_highlight={"life"})
bg = Channel("BESTGO", BG_DUMMY, BestgoDecoder(),
             bestgo.dummy_frames, bestgo.DUMMY_PERIOD,
             tui.render_bestgo, no_highlight={"soc_hi"})
channels = [ez, bg]

geo = tui.Geometry(total_w=58)

# --- open the shared-bus adapter (unless both devices are simulated) ----------
transport = None
if not (EZ_DUMMY and BG_DUMMY):
    try:
        transport = open_transport(bitrate=BITRATE)
    except TransportError as e:
        sys.exit(str(e))
    decoded = " + ".join(ch.name for ch in channels if not ch.dummy)
    print(f"listening on {transport.describe()} (shared 500 kbps bus), "
          f"decoding: {decoded}")
    time.sleep(0.8)   # let the user read the line before the screen clears


def channel_loc(ch):
    """Where this panel's data comes from, shown in the title bar."""
    return "[DUMMY]" if ch.dummy else transport.describe()


def render_screen(now):
    L = (ez.renderer(ez.panel, geo, f"EZkontrol B48800   {channel_loc(ez)}")
         + [""]
         + bg.renderer(bg.panel, geo, f"BESTGO Battery   {channel_loc(bg)}"))

    def stat(ch):
        age = (now - ch.last_frame) * 1000 if ch.last_frame else 0.0
        return f"{ch.fps():3d} Hz  last {age:5.0f} ms"

    L.append(f"  EZkontrol: {stat(ez)}      BESTGO: {stat(bg)}")
    L.append("  Ctrl+C to stop")
    return "\n".join(L)


# --- run ------------------------------------------------------------------------
tui.enable_vt()

stop = False
def _handle_sigint(signum, frame):
    global stop
    stop = True
signal.signal(signal.SIGINT, _handle_sigint)

REDRAW_PERIOD = 0.1
READ_BUDGET = 60        # max frames to drain from the bus per loop
last_redraw = 0.0
deadline = (time.monotonic() + duration_sec) if duration_sec else None

tui.screen_begin()
try:
    while not stop:
        now = time.monotonic()
        if deadline and now >= deadline:
            break

        # Simulated devices: release their dummy frames.
        for ch in channels:
            if not ch.dummy:
                continue
            frame = ch.source.read()
            if frame is not None:
                ch.handle(*frame)
                ch.mark(now)

        # Real shared bus: read frames, route each by ID to its panel.
        if transport is not None:
            for _ in range(READ_BUDGET):
                fr = transport.recv(0.002)
                if fr is None:
                    break
                tnow = time.monotonic()
                for ch in channels:
                    if not ch.dummy and ch.handle(fr.arbitration_id, fr.data):
                        ch.mark(tnow)
                        break

        if now - last_redraw >= REDRAW_PERIOD:
            tui.screen_update(render_screen(now))
            last_redraw = now

        time.sleep(0.01)
finally:
    tui.screen_end()
    print("\nshutting down...")
    if transport is not None:
        transport.close()
