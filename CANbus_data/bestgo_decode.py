"""Live dashboard + ASC logger for the BESTGO battery (Lithium Valley BMS).

Works on Windows (gs_usb direct) and the Raspberry Pi (SocketCAN) --
solarcar_can.transport picks the right path.

Usage:
    python bestgo_decode.py [DURATION_SEC] [LOG_PATH]
    python bestgo_decode.py -bestgo_dummy

DURATION_SEC = 0 means run until Ctrl+C. LOG_PATH defaults to
logs/bestgo-decode-<timestamp>.asc and is in Vector ASC format.

Pass -bestgo_dummy to drive the dashboard from simulated BMS data when
the battery (or the adapter) isn't connected. No device is opened and no
log file is written in that mode.

The protocol lives in solarcar_can/bestgo.py (SMA/Pylontech-compatible:
500 kbps, standard 11-bit IDs 0x351..0x379, ~1 s cycle, little-endian).
On Linux, bring the interface up first (see can_up.sh).
"""
import os
import sys
import time
import signal
from collections import deque
from datetime import datetime

import can

from solarcar_can import bestgo
from solarcar_can.bestgo import BestgoDecoder
from solarcar_can.transport import open_transport, DummyFrameSource, TransportError
import tui

DUMMY = "-bestgo_dummy" in sys.argv
if DUMMY:
    sys.argv = [a for a in sys.argv if a != "-bestgo_dummy"]

duration_sec = float(sys.argv[1]) if len(sys.argv) > 1 else 0.0
if duration_sec == 0.0:
    duration_sec = None

if DUMMY:
    log_path = "(dummy mode - logging disabled)"
elif len(sys.argv) > 2:
    log_path = sys.argv[2]
else:
    os.makedirs("logs", exist_ok=True)
    log_path = f"logs/bestgo-decode-{datetime.now():%Y%m%d-%H%M%S}.asc"

decoder = BestgoDecoder()
panel = tui.Panel(no_highlight={"soc_hi"})
geo = tui.Geometry(total_w=58)

if DUMMY:
    transport = None
    source = DummyFrameSource(bestgo.dummy_frames, bestgo.DUMMY_PERIOD)
    asc_writer = None
    title = "BESTGO Battery   [DUMMY]"
else:
    try:
        transport = open_transport(bitrate=500_000)
    except TransportError as e:
        sys.exit(str(e))
    source = None
    asc_writer = can.ASCWriter(log_path)
    title = f"BESTGO Battery   {transport.describe()}"

stop = False
def _handle_sigint(signum, frame):
    global stop
    stop = True
signal.signal(signal.SIGINT, _handle_sigint)

rate_window = deque(maxlen=100)
last_frame_mono = 0.0
last_redraw = 0.0
REDRAW_PERIOD = 0.1  # 10 Hz

deadline = (time.monotonic() + duration_sec) if duration_sec else None

tui.enable_vt()
tui.screen_begin()

try:
    while not stop:
        now_mono = time.monotonic()
        if deadline and now_mono >= deadline:
            break

        if DUMMY:
            frame = source.read()
            if frame is None:
                time.sleep(0.02)
            else:
                fields = decoder.decode(*frame)
                if fields:
                    panel.update(fields)
                last_frame_mono = now_mono
                rate_window.append(now_mono)
        else:
            fr = transport.recv(0.05)  # short timeout so we can redraw smoothly
            if fr is not None:
                asc_writer.on_message_received(can.Message(
                    timestamp=time.time(),
                    arbitration_id=fr.arbitration_id,
                    is_extended_id=fr.is_extended,
                    dlc=len(fr.data),
                    data=fr.data,
                ))
                fields = decoder.decode(fr.arbitration_id, fr.data)
                if fields:
                    panel.update(fields)
                last_frame_mono = now_mono
                rate_window.append(now_mono)

        if now_mono - last_redraw >= REDRAW_PERIOD:
            if len(rate_window) >= 2:
                span = rate_window[-1] - rate_window[0]
                fps = (len(rate_window) - 1) / span if span > 0 else 0.0
            else:
                fps = 0.0
            last_age = (now_mono - last_frame_mono) if last_frame_mono else 0.0
            rows = tui.render_bestgo(panel, geo, title)
            rows.append(f"  {fps:5.1f} Hz   last frame {last_age*1000:5.0f} ms ago")
            rows.append(f"  log: {log_path}")
            rows.append("  Ctrl+C to stop")
            tui.screen_update("\n".join(rows))
            last_redraw = now_mono
finally:
    tui.screen_end()
    print("\nshutting down...")
    try:
        if asc_writer is not None:
            asc_writer.stop()
            print(f"log saved: {log_path}")
    except Exception:
        pass
    if transport is not None:
        transport.close()
