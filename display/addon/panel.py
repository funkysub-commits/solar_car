"""E-ink panel layer: the guarded Waveshare driver import, GPIO conflict
diagnostics, per-region change detection and the partial/full refresh calls.
Everything that touches the physical panel lives here."""
import logging
import sys

from PIL import Image

import layout
from units import to_display_temp

# The panel is mounted upside-down relative to the rendered frame, so every
# buffer sent to the hardware is rotated 180 degrees at the push layer. Doing it
# here (rather than in render.py) keeps the layout/region geometry in natural
# reading coordinates while both full and partial refreshes stay consistent.
# For a 180 flip, logical point (x, y) maps to panel point (W-1-x, H-1-y), so a
# region box (x0, y0, x1, y1) maps to (W-x1, H-y1, W-x0, H-y0). All region x
# coordinates and W are multiples of 8, so the flipped columns stay byte-aligned.
FLIP_180 = True

# The Waveshare driver only exists on the Raspberry Pi. Import it lazily so the
# rendering code can be imported and unit-tested on a PC. Note the driver
# claims the e-ink control GPIOs at import time (epdconfig instantiates
# RaspberryPi()), so a GPIO conflict surfaces here as the import failing.
EPD_IMPORT_ERROR = None
try:
    sys.path.append('/e-Paper/RaspberryPi_JetsonNano/python/lib')
    from waveshare_epd import epd7in5_V2
except Exception as _e:        # pragma: no cover - hardware-only path
    epd7in5_V2 = None
    EPD_IMPORT_ERROR = _e

# BCM pins the Waveshare 7.5" HAT driver uses (per epdconfig). Used only to
# produce a helpful log line when the panel can't be claimed.
EINK_GPIO = {17: "RST", 25: "DC", 18: "PWR", 24: "BUSY",
             8: "CS", 11: "SPI_CLK", 10: "SPI_MOSI"}


def diagnose_gpio():
    """When the driver can't claim the panel (e.g. OSError 16 'Resource busy'),
    query the GPIO chip to log which e-ink lines are already in use and by which
    consumer - that names the conflicting process (a fan overlay, a stray
    ESPHome/other driver, etc.) directly in the add-on log."""
    try:
        import gpiod
    except Exception as e:
        logging.error(f"GPIO diagnostic skipped - gpiod unavailable: {e}")
        return
    for path in ("/dev/gpiochip0", "/dev/gpiochip4"):
        try:
            chip = gpiod.Chip(path)
        except Exception:
            continue
        try:
            busy = []
            for off, label in EINK_GPIO.items():
                try:
                    info = chip.get_line_info(off)
                    if getattr(info, "used", False):
                        busy.append(f"GPIO{off}({label}) held by "
                                    f"'{getattr(info, 'consumer', '?') or '?'}'")
                except Exception:
                    pass
            if busy:
                logging.error(f"{path}: e-ink GPIO line(s) already in use -> "
                              + "; ".join(busy)
                              + " . Free that consumer (stop the other driver / "
                                "move the fan to a different GPIO) and restart.")
            else:
                logging.info(f"{path}: none of the e-ink GPIO lines report as in use")
        finally:
            try:
                chip.close()
            except Exception:
                pass


def region_snaps(speed, speed_unit, temps, soc, voltage, warnings, stale,
                 ha_msg, clock_str):
    """Per-region coarse snapshot - a region is only refreshed when its tuple
    changes. speed/speed_unit come straight from the HA entity. Stale flags are
    included so a value's "!" mark appearing/clearing triggers a refresh.

    The header IP line is deliberately absent: it has no partial-refresh region
    and repaints only on full-screen refreshes, so it never drives a refresh."""
    return {
        "speed": (None if speed is None else round(speed), speed_unit,
                  stale.get("speed", False)),
        "msg": ha_msg,
        "batt": (None if soc is None else round(soc),
                 None if voltage is None else round(voltage, 1),
                 stale.get("soc", False), stale.get("voltage", False)),
        "temps": tuple((None if temps.get(k) is None else round(to_display_temp(temps.get(k))),
                        stale.get(k, False))
                       for k in ("t_motor", "t_ezk", "t_batt", "t_pi")),
        # the bar renders deterministically from this ordered text list, so
        # any add/remove/reorder/text-change refreshes it
        "warn": tuple(w["text"] for w in warnings),
        "clock": clock_str,
    }


def region_buffer(region_img):
    """Pack a 1-bit region image into an e-paper buffer (same format as
    epd.getbuffer, which is what display_Partial expects)."""
    buf = bytearray(region_img.convert('1').tobytes('raw'))
    for i in range(len(buf)):
        buf[i] ^= 0xFF
    return list(buf)


def _oriented(img):
    """The full frame as the panel should receive it (180-flipped when the
    display is mounted upside-down)."""
    return img.transpose(Image.ROTATE_180) if FLIP_180 else img


def push_region(epd, img, name):
    """Partial-refresh just one region of the full frame onto the panel. When the
    panel is flipped, both the region's tile and its panel coordinates rotate."""
    x0, y0, x1, y1 = layout.REGIONS[name]
    tile = img.crop((x0, y0, x1, y1))
    if FLIP_180:
        tile = tile.transpose(Image.ROTATE_180)
        x0, y0, x1, y1 = layout.W - x1, layout.H - y1, layout.W - x0, layout.H - y0
    epd.display_Partial(region_buffer(tile), x0, y0, x1, y1)


def full_refresh(epd, img):
    """Fast full-screen refresh (~2s) that clears partial-mode ghosting, then
    return to flash-free partial mode. Also wakes the panel from deep sleep."""
    epd.init_fast()
    epd.display(epd.getbuffer(_oriented(img)))
    epd.init_part()


def settle_and_sleep(epd, img):
    """Clear ghosting with one clean full refresh, then deep-sleep the panel.
    The image stays visible with no power; the panel must not be left active."""
    epd.init_fast()
    epd.display(epd.getbuffer(_oriented(img)))
    epd.sleep()
