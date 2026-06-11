"""Panel geometry, fonts and the team logo - every cross-cutting pixel
coordinate gets a name here so the draw code and the partial-refresh regions
can never drift apart."""
import logging

from PIL import Image, ImageFont

import config

# Panel
W, H = 800, 480
HEAD_H = 48
DIV_X = 452          # vertical divider between left column and right column
BAT_DIV_Y = 262      # right column: battery above, temperatures below
CONTENT_BOT = 432    # main content ends here; below is the notification band

# Speedometer (left column)
SPEED_CX, SPEED_CY, SPEED_R = 228, 206, 120

# Battery (right-top)
BATT_X = DIV_X + 22          # left edge of the battery block
BATT_BOX_Y0, BATT_BOX_Y1 = 90, 168
BATT_BOX_W = 232
BATT_SOC_Y = 222             # SoC percentage baseline
BATT_VOLT_Y = 234            # voltage readout baseline

# Temperature bars (right-bottom)
TEMP_TOP_Y, TEMP_BASE_Y = 330, 406
TEMP_HALF = 25               # half-width of each bar

# Notification toast (bottom band)
NOTIFY_CY = 456
NOTIFY_H = 34

# Independent partial-refresh regions, (x0, y0, x1, y1).
# x coordinates MUST be multiples of 8 - the panel only refreshes byte-aligned
# columns. Regions stay clear of the frame/divider lines so those never ghost.
REGIONS = {
    "speed":  (8, 50, 448, CONTENT_BOT),
    "batt":   (456, 50, 792, 260),
    "temps":  (456, 264, 792, CONTENT_BOT),
    "notify": (8, 436, 792, 476),
    "clock":  (608, 4, 792, 46),
}
# Regions that count as real telemetry: a change here keeps the panel awake.
# The clock is redrawn alongside telemetry but never wakes the panel by itself.
DATA_REGIONS = ("speed", "batt", "temps", "notify")


_font_warned = set()


def _font(name, size):
    try:
        return ImageFont.truetype(f"{config.FONT_DIR}/{name}", size)
    except Exception as e:
        if name not in _font_warned:        # warn once per face, not 9 times
            _font_warned.add(name)
            logging.warning(f"font {config.FONT_DIR}/{name} unavailable ({e}) - "
                            "falling back to PIL's default; the dashboard will "
                            "render with wrong text sizes")
        return ImageFont.load_default()


F_TITLE  = _font("DejaVuSans-Bold.ttf", 28)
F_LABEL  = _font("DejaVuSans-Bold.ttf", 19)
F_SPEED  = _font("DejaVuSans-Bold.ttf", 64)
F_UNIT   = _font("DejaVuSans.ttf", 22)
F_SOC    = _font("DejaVuSans-Bold.ttf", 56)
F_TEMP   = _font("DejaVuSans-Bold.ttf", 26)
F_SMALL  = _font("DejaVuSans.ttf", 17)
F_NOTIFY = _font("DejaVuSans-Bold.ttf", 22)
F_BADGE  = _font("DejaVuSans-Bold.ttf", 16)

LOGO_H = 40


def _load_logo():
    """Load the team logo as a bold 1-bit silhouette for the header - anything
    that is not near-white background becomes solid black, so it stays visible
    on the e-ink panel (a plain threshold would drop the light-coloured sun)."""
    try:
        src = Image.open(config.LOGO_PATH).convert("RGBA")
        bg = Image.new("RGBA", src.size, (255, 255, 255, 255))
        bg.alpha_composite(src)
        gray = bg.convert("L")
        w = max(1, round(gray.width * LOGO_H / gray.height))
        gray = gray.resize((w, LOGO_H), Image.LANCZOS)
        return gray.point(lambda p: 0 if p < 242 else 255).convert("1", dither=Image.Dither.NONE)
    except Exception as e:
        logging.warning(f"logo load failed: {e}")
        return None


LOGO = _load_logo()
