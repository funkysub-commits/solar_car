"""Frame composition: pure-PIL drawing of the dashboard. No hardware, no
network - fully testable on a PC (the golden-image harness drives render())."""
import math

from PIL import Image, ImageDraw

import config
import layout
from layout import (W, H, HEAD_H, DIV_X, BAT_DIV_Y, CONTENT_BOT,
                    F_TITLE, F_LABEL, F_SPEED, F_UNIT, F_SOC, F_TEMP,
                    F_SMALL, F_NOTIFY, F_BADGE)
from units import clamp, to_display_temp


def draw_warn_mark(d, cx, cy, h=18):
    """A small warning triangle with an exclamation - drawn next to any value
    whose data has stopped arriving, and used as the toast's alarm icon."""
    half = h / 2.0
    top = (cx, cy - half)
    bl = (cx - half * 1.06, cy + half)
    br = (cx + half * 1.06, cy + half)
    d.polygon([top, bl, br], fill=255)
    d.line([top, bl, br, top], fill=0, width=max(2, int(h * 0.12)), joint="curve")
    bw = max(2, int(h * 0.12))
    d.line((cx, cy - half * 0.28, cx, cy + half * 0.30), fill=0, width=bw)
    dr = max(1.3, h * 0.07)
    dy = cy + half * 0.62
    d.ellipse((cx - dr, dy - dr, cx + dr, dy + dr), fill=0)


def draw_info_mark(d, cx, cy, h=18):
    """A small circled "i" - the toast icon for a plain user message."""
    r = h / 2.0
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=255, outline=0, width=2)
    dr = max(1.3, h * 0.08)
    dy = cy - r * 0.42
    d.ellipse((cx - dr, dy - dr, cx + dr, dy + dr), fill=0)
    d.line((cx, cy - r * 0.08, cx, cy + r * 0.52), fill=0, width=max(2, int(h * 0.12)))


def _ellipsize(d, text, font, max_w):
    """Trim text with a trailing ellipsis so it fits within max_w pixels."""
    if d.textlength(text, font=font) <= max_w:
        return text
    ell = "…"
    while text and d.textlength(text + ell, font=font) > max_w:
        text = text[:-1]
    return (text + ell) if text else ell


def draw_speedometer(d, speed, unit, stale=False):
    """speed and its unit label are shown exactly as Home Assistant reports
    them - the add-on does no unit conversion."""
    cx, cy, r = layout.SPEED_CX, layout.SPEED_CY, layout.SPEED_R

    d.text((cx, 62), "SPEED", font=F_LABEL, fill=0, anchor="ma")
    d.arc((cx - r, cy - r, cx + r, cy + r), 180, 360, fill=0, width=4)

    # tick marks around the arc (0..10, major every 5)
    for i in range(11):
        a = math.radians(180 + 180 * (i / 10.0))
        ca, sa = math.cos(a), math.sin(a)
        major = (i % 5 == 0)
        r2 = r - 18 if major else r - 11
        d.line((cx + r * ca, cy + r * sa, cx + r2 * ca, cy + r2 * sa),
               fill=0, width=3 if major else 2)

    # scale end labels
    d.text((cx - r, cy + 8), "0", font=F_SMALL, fill=0, anchor="ma")
    d.text((cx + r, cy + 8), f"{config.SPEED_MAX:.0f}", font=F_SMALL, fill=0, anchor="ma")

    # needle
    val = 0.0 if speed is None else clamp(speed, 0, config.SPEED_MAX)
    a = math.radians(180 + 180 * (val / config.SPEED_MAX))
    ca, sa = math.cos(a), math.sin(a)
    rn = r - 26
    d.line((cx, cy, cx + rn * ca, cy + rn * sa), fill=0, width=5)
    d.ellipse((cx - 9, cy - 9, cx + 9, cy + 9), fill=0)

    # large numeric readout
    num = "--" if speed is None else f"{speed:.0f}"
    d.text((cx, cy + 70), num, font=F_SPEED, fill=0, anchor="mm")
    d.text((cx, cy + 116), unit, font=F_UNIT, fill=0, anchor="mm")

    if stale:
        # mark next to the SPEED title - the value isn't being updated
        w = d.textlength("SPEED", font=F_LABEL)
        draw_warn_mark(d, cx + w / 2 + 18, 72, 22)


def draw_battery(d, soc, voltage, vunit, stale_soc=False, stale_v=False):
    x = layout.BATT_X
    d.text((x, 60), "BATTERY", font=F_LABEL, fill=0, anchor="la")

    bx0, by0 = x, layout.BATT_BOX_Y0
    bx1, by1 = x + layout.BATT_BOX_W, layout.BATT_BOX_Y1
    d.rectangle((bx0, by0, bx1, by1), outline=0, width=3)
    nub_h = 30
    midy = (by0 + by1) // 2
    d.rectangle((bx1, midy - nub_h // 2, bx1 + 13, midy + nub_h // 2), fill=0)

    pad = 8
    if soc is not None:
        inner_w = (bx1 - bx0) - 2 * pad
        fw = int(inner_w * clamp(soc, 0, 100) / 100.0)
        if fw > 0:
            d.rectangle((bx0 + pad, by0 + pad, bx0 + pad + fw, by1 - pad), fill=0)

    soc_txt = "--" if soc is None else f"{soc:.0f}%"
    d.text((x, layout.BATT_SOC_Y), soc_txt, font=F_SOC, fill=0, anchor="lm")
    if stale_soc:
        w = d.textlength(soc_txt, font=F_SOC)
        draw_warn_mark(d, x + w + 22, layout.BATT_SOC_Y, 26)
    if voltage is not None:
        vtxt = f"{voltage:.1f} {vunit or 'V'}"
        d.text((W - 20, layout.BATT_VOLT_Y), vtxt, font=F_SMALL, fill=0, anchor="rm")
        if stale_v:
            w = d.textlength(vtxt, font=F_SMALL)
            draw_warn_mark(d, W - 20 - w - 16, layout.BATT_VOLT_Y, 20)
    elif stale_v:
        draw_warn_mark(d, W - 30, layout.BATT_VOLT_Y, 20)


def draw_temps(d, temps, stale):
    d.text((DIV_X + 22, 270), "TEMPERATURES", font=F_LABEL, fill=0, anchor="la")

    items = [
        ("MOTOR", "t_motor"),
        ("EZK",   "t_ezk"),
        ("BATT",  "t_batt"),
        ("PI",    "t_pi"),
    ]
    area_x0, area_x1 = DIV_X + 10, W - 12
    slot = (area_x1 - area_x0) / len(items)
    half = layout.TEMP_HALF
    base_y, top_y = layout.TEMP_BASE_Y, layout.TEMP_TOP_Y
    bar_h = base_y - top_y

    for i, (lbl, key) in enumerate(items):
        val_c = temps.get(key)
        val = to_display_temp(val_c)              # convert C -> display unit
        cx = area_x0 + slot * i + slot / 2
        d.rectangle((cx - half, top_y, cx + half, base_y), outline=0, width=2)
        if val is not None:
            fh = bar_h * clamp(val, 0, config.TEMP_MAX) / config.TEMP_MAX
            if fh > 0:
                d.rectangle((cx - half, base_y - fh, cx + half, base_y), fill=0)
        vtxt = "--" if val is None else f"{val:.0f}°{config.TEMP_UNIT}"
        d.text((cx, top_y - 6), vtxt, font=F_TEMP, fill=0, anchor="md")
        d.text((cx, base_y + 7), lbl, font=F_SMALL, fill=0, anchor="ma")
        if stale.get(key):
            draw_warn_mark(d, cx + half + 9, top_y + 12, 17)


def draw_notify(d, warnings):
    """Draw the bottom notification toast: a small centred box showing the most
    important active warning, with a count badge if more than one is active.
    Draws nothing when there are no active warnings."""
    if not warnings:
        return
    top = warnings[0]
    count = len(warnings)

    cy = layout.NOTIFY_CY
    box_h = layout.NOTIFY_H
    pad = 14
    icon_sz = 22
    gap = 9
    badge_d = 26 if count > 1 else 0
    badge_gap = 9 if count > 1 else 0

    max_box_w = W - 80
    max_text_w = max_box_w - (pad * 2 + icon_sz + gap + badge_gap + badge_d)
    text = _ellipsize(d, top["text"], F_NOTIFY, max_text_w)
    tw = d.textlength(text, font=F_NOTIFY)

    box_w = int(pad * 2 + icon_sz + gap + tw + badge_gap + badge_d)
    bx0 = (W - box_w) // 2
    bx1 = bx0 + box_w
    by0 = cy - box_h // 2
    by1 = cy + box_h // 2

    # white fill clears whatever was beneath, then a rounded outline = a chip
    d.rounded_rectangle((bx0, by0, bx1, by1), radius=9, fill=255, outline=0, width=2)

    ix = bx0 + pad + icon_sz // 2
    if top.get("icon") == "info":
        draw_info_mark(d, ix, cy, icon_sz)
    else:
        draw_warn_mark(d, ix, cy, icon_sz)

    tx = bx0 + pad + icon_sz + gap
    d.text((tx, cy), text, font=F_NOTIFY, fill=0, anchor="lm")

    if count > 1:
        r = badge_d // 2
        bcx = bx1 - pad - r
        d.ellipse((bcx - r, cy - r, bcx + r, cy + r), fill=0)
        d.text((bcx, cy - 1), str(count), font=F_BADGE, fill=255, anchor="mm")


def render(speed, speed_unit, temps, soc, voltage, voltage_unit, warnings, stale, clock_str):
    """speed/speed_unit are passed through from the HA entity untouched.
    warnings is the visible (non-hidden) ordered warning list. stale maps
    value keys -> bool."""
    img = Image.new('1', (W, H), 255)
    d = ImageDraw.Draw(img)

    d.rectangle((1, 1, W - 2, H - 2), outline=0, width=2)
    d.line((2, HEAD_H, W - 3, HEAD_H), fill=0, width=2)
    d.line((DIV_X, HEAD_H, DIV_X, CONTENT_BOT), fill=0, width=2)
    d.line((DIV_X, BAT_DIV_Y, W - 3, BAT_DIV_Y), fill=0, width=2)

    # header: logo + title + clock
    tx = 16
    if layout.LOGO is not None:
        img.paste(layout.LOGO, (14, 5))
        tx = 14 + layout.LOGO.width + 12
    d.text((tx, 9), config.TITLE, font=F_TITLE, fill=0, anchor="la")
    d.text((W - 18, 9), clock_str, font=F_TITLE, fill=0, anchor="ra")

    draw_speedometer(d, speed, speed_unit, stale.get("speed", False))
    draw_battery(d, soc, voltage, voltage_unit,
                 stale.get("soc", False), stale.get("voltage", False))
    draw_temps(d, temps, stale)
    draw_notify(d, warnings)
    return img
