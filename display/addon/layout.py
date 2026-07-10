"""Panel geometry, fonts and the team logo - every cross-cutting pixel
coordinate gets a name here so the draw code and the partial-refresh regions
can never drift apart.

Layout (800x480):
  Header (0-48): logo + title + clock
  Left  : speedometer | odometer (top, side by side)  +  MESSAGE box (bottom)
  Right : battery (top)      +  temperatures (bottom)
  Warning bar (428-476): full-width, fills left->right with active WARNINGS
                         (highest priority leftmost), '+N' overflow badge right.
The message box and the warning bar are deliberately separate: plain user
messages live in the box, only warnings use the bar.
"""
import logging

from PIL import Image, ImageFont

import config

# Panel
W, H = 800, 480
HEAD_H = 48
DIV_X = 452          # vertical divider between left column and right column
BAT_DIV_Y = 262      # right column: battery above, temperatures below
MSG_DIV_Y = 300      # left column: speedometer above, message box below
CONTENT_BOT = 424    # main content ends here; below is the warning bar

# Header connection block: a small "IP:" heading followed by up to two stacked
# rows (Router / Hotspot) centred in the gap between the title and the clock,
# e.g.   IP:  Router: 192.168.1.50:8123
#             Hotspot: 203.0.113.7:8123
# It has no partial-refresh region of its own, so it repaints only on
# full-screen refreshes - not on the regular per-region updates - which keeps it
# off the high-frequency refresh path.
HEAD_NET_CX, HEAD_NET_CY = 486, 24   # centre of the whole block in the header gap
HEAD_NET_MAXW = 300                  # widest the block may grow before ellipsizing
HEAD_NET_ROW_H = 17                  # vertical pitch between the two stacked rows

# Left-top is split into two side-by-side panes by a vertical divider. The
# divider sits in the 232..240 gap BETWEEN the two partial-refresh regions, so
# neither region ever repaints it and it can never ghost.
SPD_ODO_DIV_X = 236

# Speedometer (left-top, left pane: x 8..232)
SPEED_CX, SPEED_CY, SPEED_R = 120, 176, 86

# Odometer (left-top, right pane: x 240..448) - total distance travelled, shown
# as a boxed readout that echoes the speedometer's label / value / unit stack.
ODO_CX = 344
ODO_LABEL_Y = 54
ODO_BOX_HALF_W = 96
ODO_BOX_Y0, ODO_BOX_Y1 = 148, 208
ODO_VALUE_Y = 178          # centre of the boxed number
ODO_UNIT_Y = 232           # sits on the speedometer's number row
ODO_MAXW = 2 * ODO_BOX_HALF_W - 20   # widest the number may draw before ellipsizing

# Message box (left-bottom)
MSG_X = 18
MSG_LABEL_Y = 308
MSG_FIRST_LINE_Y = 338
MSG_LINE_H = 26

# Battery (right-top)
BATT_X = DIV_X + 22
BATT_BOX_Y0, BATT_BOX_Y1 = 90, 168
BATT_BOX_W = 232
BATT_SOC_Y = 222
BATT_VOLT_Y = 234
# Aux (12V) battery percentage: small text on the "BATTERY" heading row, right
# aligned above the right end of the battery bar. Inside the "batt" refresh
# region, so it repaints with the rest of the battery block.
AUX_X, AUX_Y = W - 20, 62

# Temperature bars (right-bottom)
TEMP_TOP_Y, TEMP_BASE_Y = 326, 398
TEMP_HALF = 25

# Warning bar (full-width bottom)
WARN_Y0, WARN_Y1 = 428, 476
WARN_CY = 452
WARN_CHIP_H = 34
WARN_X0, WARN_X1 = 16, 784      # usable horizontal span for chips + badge

# Independent partial-refresh regions, (x0, y0, x1, y1).
# x coordinates MUST be multiples of 8 - the panel only refreshes byte-aligned
# columns. Regions stay clear of the frame/divider lines so those never ghost.
REGIONS = {
    "speed": (8, 50, 232, 298),
    "odo":   (240, 50, 448, 298),
    "msg":   (8, 304, 448, CONTENT_BOT),
    "batt":  (456, 50, 792, 260),
    "temps": (456, 264, 792, CONTENT_BOT),
    "warn":  (8, WARN_Y0, 792, 476),
    "clock": (608, 4, 792, 46),
}
# Regions that count as real telemetry: a change here keeps the panel awake.
# The clock is redrawn alongside telemetry but never wakes the panel by itself.
DATA_REGIONS = ("speed", "odo", "msg", "batt", "temps", "warn")

_font_warned = set()


def _font(name, size):
    try:
        return ImageFont.truetype(f"{config.FONT_DIR}/{name}", size)
    except Exception as e:
        if name not in _font_warned:        # warn once per face, not many times
            _font_warned.add(name)
            logging.warning(f"font {config.FONT_DIR}/{name} unavailable ({e}) - "
                            "falling back to PIL's default; the dashboard will "
                            "render with wrong text sizes")
        return ImageFont.load_default()


F_TITLE  = _font("DejaVuSans-Bold.ttf", 28)
F_HEAD_LABEL = _font("DejaVuSans-Bold.ttf", 16)   # the "IP:" heading / "Pi Offline"
F_HEAD_NET = _font("DejaVuSans.ttf", 13)          # the stacked Router/Hotspot rows
F_LABEL  = _font("DejaVuSans-Bold.ttf", 19)
F_SPEED  = _font("DejaVuSans-Bold.ttf", 54)
F_ODO    = _font("DejaVuSans-Bold.ttf", 40)   # boxed odometer readout
F_UNIT   = _font("DejaVuSans.ttf", 20)
F_SOC    = _font("DejaVuSans-Bold.ttf", 56)
F_TEMP   = _font("DejaVuSans-Bold.ttf", 26)
F_SMALL  = _font("DejaVuSans.ttf", 17)
F_MSG    = _font("DejaVuSans.ttf", 19)
F_WARN   = _font("DejaVuSans-Bold.ttf", 20)
F_BADGE  = _font("DejaVuSans-Bold.ttf", 16)

# Shutdown splash (render_splash): a big centred "POWERED OFF" screen shown once
# when the add-on stops, then the panel deep-sleeps holding it.
F_SPLASH     = _font("DejaVuSans-Bold.ttf", 58)
F_SPLASH_SUB = _font("DejaVuSans.ttf", 24)

LOGO_H = 40
SPLASH_LOGO_H = 120


def _load_logo(h=LOGO_H):
    """Load the team logo as a bold 1-bit silhouette h pixels tall - anything
    that is not near-white background becomes solid black, so it stays visible
    on the e-ink panel (a plain threshold would drop the light-coloured sun)."""
    try:
        src = Image.open(config.LOGO_PATH).convert("RGBA")
        bg = Image.new("RGBA", src.size, (255, 255, 255, 255))
        bg.alpha_composite(src)
        gray = bg.convert("L")
        w = max(1, round(gray.width * h / gray.height))
        gray = gray.resize((w, h), Image.LANCZOS)
        return gray.point(lambda p: 0 if p < 242 else 255).convert("1", dither=Image.Dither.NONE)
    except Exception as e:
        logging.warning(f"logo load failed: {e}")
        return None


LOGO = _load_logo()
SPLASH_LOGO = _load_logo(SPLASH_LOGO_H)
