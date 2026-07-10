"""Frame composition: pure-PIL drawing of the dashboard. No hardware, no
network - fully testable on a PC (the golden-image harness drives render())."""
from PIL import Image, ImageDraw

import config
import layout
from layout import (W, H, HEAD_H, DIV_X, BAT_DIV_Y, MSG_DIV_Y, CONTENT_BOT,
                    F_TITLE, F_HEAD_LABEL, F_HEAD_NET, F_LABEL, F_SPEED, F_UNIT,
                    F_SOC, F_TEMP, F_SMALL, F_MSG, F_WARN, F_BADGE,
                    F_SPLASH, F_SPLASH_SUB)
from units import clamp, to_display_temp


def draw_warn_mark(d, cx, cy, h=18):
    """A small warning triangle with an exclamation - drawn next to any value
    whose data has stopped arriving, and used as a warning chip's icon."""
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


def _ellipsize(d, text, font, max_w):
    """Trim text with a trailing ellipsis so it fits within max_w pixels."""
    if d.textlength(text, font=font) <= max_w:
        return text
    ell = "…"
    while text and d.textlength(text + ell, font=font) > max_w:
        text = text[:-1]
    return (text + ell) if text else ell


def _wrap(d, text, font, max_w, max_lines):
    """Word-wrap text to max_w pixels and at most max_lines lines; the last
    line is ellipsized if the text doesn't fit."""
    words = text.split()
    lines, cur = [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if not cur or d.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = w
            if len(lines) == max_lines:
                break
    if len(lines) < max_lines and cur:
        lines.append(cur)
    if len(lines) == max_lines:
        # if anything was dropped, mark the final line with an ellipsis
        joined = " ".join(lines)
        if joined != text:
            lines[-1] = _ellipsize(d, lines[-1] + " …", font, max_w)
    return lines[:max_lines]


def draw_speedometer(d, speed, unit, stale=False):
    """speed and its unit label are shown exactly as Home Assistant reports
    them - the add-on does no unit conversion."""
    cx, cy, r = layout.SPEED_CX, layout.SPEED_CY, layout.SPEED_R

    d.text((cx, 54), "SPEED", font=F_LABEL, fill=0, anchor="ma")
    d.arc((cx - r, cy - r, cx + r, cy + r), 180, 360, fill=0, width=4)

    import math
    for i in range(11):
        a = math.radians(180 + 180 * (i / 10.0))
        ca, sa = math.cos(a), math.sin(a)
        major = (i % 5 == 0)
        r2 = r - 15 if major else r - 9
        d.line((cx + r * ca, cy + r * sa, cx + r2 * ca, cy + r2 * sa),
               fill=0, width=3 if major else 2)

    d.text((cx - r, cy + 4), "0", font=F_SMALL, fill=0, anchor="ma")
    d.text((cx + r, cy + 4), f"{config.SPEED_MAX:.0f}", font=F_SMALL, fill=0, anchor="ma")

    val = 0.0 if speed is None else clamp(speed, 0, config.SPEED_MAX)
    a = math.radians(180 + 180 * (val / config.SPEED_MAX))
    ca, sa = math.cos(a), math.sin(a)
    rn = r - 22
    d.line((cx, cy, cx + rn * ca, cy + rn * sa), fill=0, width=5)
    d.ellipse((cx - 8, cy - 8, cx + 8, cy + 8), fill=0)

    num = "--" if speed is None else f"{speed:.0f}"
    d.text((cx, cy + 56), num, font=F_SPEED, fill=0, anchor="mm")
    d.text((cx, cy + 94), unit, font=F_UNIT, fill=0, anchor="mm")

    if stale:
        w = d.textlength("SPEED", font=F_LABEL)
        draw_warn_mark(d, cx + w / 2 + 18, 62, 22)


def draw_header_net(d, lines):
    """The header connection block - an "IP:" heading and up to two stacked rows
    (e.g. "Router: 192.168.1.50:8123" / "Hotspot: 203.0.113.7:8123"), or a lone
    "Pi Offline" - drawn in the gap between the title and the clock. lines is the
    list of (label, value) tuples from ha_client.connection_lines(). It has no
    partial-refresh region of its own, so it repaints only as part of a
    full-screen refresh (see display.py), not on regular per-region updates."""
    if not lines:
        return
    # A lone label-less row is the offline / single-message fallback: centre it
    # on its own, with no "IP:" heading in front.
    if len(lines) == 1 and not lines[0][0]:
        d.text((layout.HEAD_NET_CX, layout.HEAD_NET_CY), lines[0][1],
               font=F_HEAD_LABEL, fill=0, anchor="mm")
        return

    head = "IP:"
    head_w = d.textlength(head, font=F_HEAD_LABEL)
    gap = 6
    avail = layout.HEAD_NET_MAXW - head_w - gap
    rows = [f"{lab}: {val}" if lab else val for lab, val in lines[:2]]
    rows = [_ellipsize(d, t, F_HEAD_NET, avail) for t in rows]
    rows_w = max(d.textlength(t, font=F_HEAD_NET) for t in rows)

    # Centre the heading + rows block as a whole within the header gap.
    left = layout.HEAD_NET_CX - (head_w + gap + rows_w) / 2
    d.text((left, layout.HEAD_NET_CY), head, font=F_HEAD_LABEL, fill=0, anchor="lm")
    rx = left + head_w + gap
    if len(rows) == 1:
        d.text((rx, layout.HEAD_NET_CY), rows[0], font=F_HEAD_NET, fill=0, anchor="lm")
    else:
        h = layout.HEAD_NET_ROW_H
        for t, dy in zip(rows, (-h / 2, h / 2)):
            d.text((rx, layout.HEAD_NET_CY + dy), t, font=F_HEAD_NET, fill=0, anchor="lm")


def draw_messages(d, ha_msg):
    """The MESSAGE box (left-bottom): the user's free-text message from Home
    Assistant, wrapped. Warnings do NOT appear here - they have their own bar."""
    d.text((layout.MSG_X, layout.MSG_LABEL_Y), "MESSAGE", font=F_LABEL, fill=0, anchor="la")
    y = layout.MSG_FIRST_LINE_Y
    if not ha_msg:
        d.text((layout.MSG_X, y), "- no message -", font=F_MSG, fill=0, anchor="la")
        return
    max_w = 448 - layout.MSG_X - 10
    n_lines = (CONTENT_BOT - y) // layout.MSG_LINE_H
    for line in _wrap(d, ha_msg, F_MSG, max_w, max(1, n_lines)):
        d.text((layout.MSG_X, y), line, font=F_MSG, fill=0, anchor="la")
        y += layout.MSG_LINE_H


def draw_charge_bolt(d, cx, cy, h):
    """A lightning bolt centred at (cx, cy), h pixels tall - drawn on the battery
    icon while the pack is charging. Filled white with a black outline so it
    stays visible over both the black SoC fill and the white empty area."""
    w = h * 0.58
    pts = [(0.12, -0.50), (-0.34, 0.08), (-0.05, 0.08),
           (-0.16, 0.50), (0.34, -0.08), (0.05, -0.08)]
    poly = [(cx + px * w, cy + py * h) for px, py in pts]
    d.polygon(poly, fill=255)
    d.line(poly + [poly[0]], fill=0, width=3, joint="curve")


def draw_battery(d, soc, voltage, vunit, stale_soc=False, stale_v=False,
                 charging=False, aux_soc=None, aux_on=True, aux_stale=False):
    x = layout.BATT_X
    d.text((x, 60), "BATTERY", font=F_LABEL, fill=0, anchor="la")

    # Aux (12V) battery percentage, small, on the heading row at the far right.
    # Disabled -> nothing is drawn at all, so the row reads as it did before the
    # aux battery existed.
    if aux_on:
        aux_txt = "AUX --" if aux_soc is None else f"AUX {aux_soc:.0f}%"
        d.text((layout.AUX_X, layout.AUX_Y), aux_txt, font=F_SMALL, fill=0, anchor="ra")
        if aux_stale:
            w = d.textlength(aux_txt, font=F_SMALL)
            draw_warn_mark(d, layout.AUX_X - w - 14, layout.AUX_Y + 8, 17)

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

    if charging:
        draw_charge_bolt(d, (bx0 + bx1) / 2, (by0 + by1) / 2, (by1 - by0) * 0.82)

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

    items = [("MOTOR", "t_motor"), ("EZK", "t_ezk"), ("BATT", "t_batt"), ("PI", "t_pi")]
    area_x0, area_x1 = DIV_X + 10, W - 12
    slot = (area_x1 - area_x0) / len(items)
    half = layout.TEMP_HALF
    base_y, top_y = layout.TEMP_BASE_Y, layout.TEMP_TOP_Y
    bar_h = base_y - top_y

    for i, (lbl, key) in enumerate(items):
        val = to_display_temp(temps.get(key))
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
            # inside the bar, near its top - the mark is drawn white-on-black
            # outline, so it reads whether or not the fill reaches up to it
            draw_warn_mark(d, cx, top_y + 13, 17)


# Warning-bar chip metrics
_PAD = 10
_ICON = 18
_GAP_ICON = 7
_GAP_CHIP = 8


def _chip_w(d, text):
    return _PAD * 2 + _ICON + _GAP_ICON + int(d.textlength(text, font=F_WARN))


def _draw_chip(d, x, w, text):
    cy, h = layout.WARN_CY, layout.WARN_CHIP_H
    d.rounded_rectangle((x, cy - h // 2, x + w, cy + h // 2), radius=8,
                        fill=255, outline=0, width=2)
    draw_warn_mark(d, x + _PAD + _ICON // 2, cy, _ICON)
    d.text((x + _PAD + _ICON + _GAP_ICON, cy), text, font=F_WARN, fill=0, anchor="lm")


def _draw_overflow(d, n):
    """Filled pill at the bar's right end: '+N' warnings that didn't fit."""
    cy, h = layout.WARN_CY, layout.WARN_CHIP_H
    txt = f"+{n}"
    tw = int(d.textlength(txt, font=F_BADGE))
    w = tw + 18
    x1 = layout.WARN_X1
    x0 = x1 - w
    d.rounded_rectangle((x0, cy - h // 2, x1, cy + h // 2), radius=8, fill=0)
    d.text(((x0 + x1) // 2, cy - 1), txt, font=F_BADGE, fill=255, anchor="mm")
    return x0


def draw_warnings_bar(d, warnings):
    """Fill the bottom bar left->right with warning chips (highest priority
    leftmost). If they don't all fit, show a '+N' pill at the right for the
    overflow. Draws nothing when there are no warnings."""
    if not warnings:
        return
    n = len(warnings)
    x = layout.WARN_X0
    shown = 0
    for i, w in enumerate(warnings):
        cw = _chip_w(d, w["text"])
        gap = _GAP_CHIP if shown else 0
        remaining_after = n - (shown + 1)
        # reserve room for the '+N' pill only if something will overflow
        reserve = 44 if remaining_after > 0 else 0
        if shown and x + gap + cw + reserve > layout.WARN_X1:
            break
        if not shown and cw > layout.WARN_X1 - layout.WARN_X0:
            # a single very long warning: ellipsize it to the whole bar
            text = _ellipsize(d, w["text"], F_WARN,
                              layout.WARN_X1 - layout.WARN_X0 - (_PAD * 2 + _ICON + _GAP_ICON))
            cw = _chip_w(d, text)
            _draw_chip(d, x, cw, text)
            shown = 1
            break
        _draw_chip(d, x + gap, cw, w["text"])
        x += gap + cw
        shown += 1
    if shown < n:
        _draw_overflow(d, n - shown)


def render(speed, speed_unit, temps, soc, voltage, voltage_unit,
           warnings, stale, ha_msg, clock_str, header_lines=None, charging=False,
           aux_soc=None, aux_on=True, aux_stale=False):
    """speed passes through from the HA entity untouched; speed_unit is the label
    to print under it (config.SPEED_UNIT overrides the entity's own unit).
    warnings is the visible (non-hidden) ordered warning list; ha_msg is the
    user's free-text message (shown in the MESSAGE box, not the warning bar).
    stale maps value keys -> bool and now marks ONLY values fed by a CAN device
    that is off the bus (alerts.device_marks), never a merely-unchanging value.
    header_lines is the (label, value) connection-row list for the header block
    (ha_client.connection_lines()). aux_soc is the 12V battery percentage, drawn
    only when aux_on; aux_stale marks it when its status sensor reads down."""
    img = Image.new('1', (W, H), 255)
    d = ImageDraw.Draw(img)

    d.rectangle((1, 1, W - 2, H - 2), outline=0, width=2)
    d.line((2, HEAD_H, W - 3, HEAD_H), fill=0, width=2)
    d.line((DIV_X, HEAD_H, DIV_X, CONTENT_BOT), fill=0, width=2)
    d.line((DIV_X, BAT_DIV_Y, W - 3, BAT_DIV_Y), fill=0, width=2)
    d.line((8, MSG_DIV_Y, 444, MSG_DIV_Y), fill=0, width=2)

    tx = 16
    if layout.LOGO is not None:
        img.paste(layout.LOGO, (14, 5))
        tx = 14 + layout.LOGO.width + 12
    d.text((tx, 9), config.TITLE, font=F_TITLE, fill=0, anchor="la")
    draw_header_net(d, header_lines)
    d.text((W - 18, 9), clock_str, font=F_TITLE, fill=0, anchor="ra")

    draw_speedometer(d, speed, speed_unit, stale.get("speed", False))
    draw_messages(d, ha_msg)
    draw_battery(d, soc, voltage, voltage_unit,
                 stale.get("soc", False), stale.get("voltage", False), charging,
                 aux_soc, aux_on, aux_stale)
    draw_temps(d, temps, stale)
    draw_warnings_bar(d, warnings)
    return img


def render_splash(title=None, subtitle="Safe to unplug"):
    """A clean full-screen 'powered off' frame drawn once when the add-on stops,
    after which the panel is deep-slept so it holds this image with no power (and
    so it isn't left mid-refresh when the Pi cuts power). Routed through the same
    panel push as the dashboard, so the 180-degree flip applies here too."""
    title = config.TITLE if title is None else title
    img = Image.new('1', (W, H), 255)
    d = ImageDraw.Draw(img)
    d.rectangle((1, 1, W - 2, H - 2), outline=0, width=2)

    cx = W // 2
    y = 96
    if layout.SPLASH_LOGO is not None:
        lg = layout.SPLASH_LOGO
        img.paste(lg, (cx - lg.width // 2, y))
        y += lg.height + 18
    else:
        y += 40
    d.text((cx, y), title, font=F_TITLE, fill=0, anchor="ma")
    y += 58
    d.text((cx, y), "POWERED OFF", font=F_SPLASH, fill=0, anchor="ma")
    y += 78
    d.text((cx, y), subtitle, font=F_SPLASH_SUB, fill=0, anchor="ma")
    return img
