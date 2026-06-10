"""Shared console-dashboard scaffolding for the CAN CLI tools.

Box-drawing helpers, per-field change highlighting, and the two device
panel renderers used by monitor.py / bestgo_decode.py / ezkontrol_decode.py.
Field names are the canonical solarcar_can ones.
"""
import os
import sys
import time

ANSI_HOME = "\x1b[H"
ANSI_CLEAR = "\x1b[2J"
ANSI_CLEAR_DOWN = "\x1b[J"
ANSI_HIDE_CURSOR = "\x1b[?25l"
ANSI_SHOW_CURSOR = "\x1b[?25h"

HIGHLIGHT_SEC = 0.5


def enable_vt():
    """Enable VT (ANSI) escape processing on Windows consoles."""
    if os.name == "nt":
        os.system("")


def screen_begin():
    sys.stdout.write(ANSI_CLEAR + ANSI_HOME + ANSI_HIDE_CURSOR)
    sys.stdout.flush()


def screen_update(text):
    sys.stdout.write(ANSI_HOME + text + ANSI_CLEAR_DOWN)
    sys.stdout.flush()


def screen_end():
    sys.stdout.write(ANSI_SHOW_CURSOR + "\n")
    sys.stdout.flush()


class Panel:
    """Decoded field store with per-field change highlighting."""

    def __init__(self, no_highlight=()):
        self.state = {}
        self.last_change = {}
        self.no_highlight = set(no_highlight)

    def update(self, fields):
        now = time.monotonic()
        for name, value in fields.items():
            old = self.state.get(name)
            self.state[name] = value
            if name not in self.no_highlight and old is not None and old != value:
                self.last_change[name] = now

    def fresh(self, name):
        return (time.monotonic() - self.last_change.get(name, 0)) < HIGHLIGHT_SEC


class Geometry:
    """Row formatters for one fixed-width bordered panel."""

    def __init__(self, total_w=58, label_w=12):
        self.content_w = total_w - 4   # area between '| ' and ' |'
        self.border_w = total_w - 2    # dashes between '+' and '+'
        self.slot_w = (self.content_w - 2) // 2
        self.label_w = label_w

    def slot(self, panel, label, value, name):
        mark = "*" if panel.fresh(name) else " "
        return f"{label:<{self.label_w}}{value:>{self.slot_w - self.label_w - 2}} {mark}"

    def row2(self, slot_a, slot_b):
        return f"| {slot_a}  {slot_b} |"

    def row_wide(self, panel, label, value, name):
        mark = "*" if panel.fresh(name) else " "
        inner = f"{label:<{self.label_w}}{value} {mark}"
        return f"| {inner:<{self.content_w}} |"

    def text_row(self, text):
        return f"| {text:<{self.content_w}} |"

    def empty_row(self):
        return "| " + " " * self.content_w + " |"

    def title_bar(self, title):
        core = f" {title} "
        return "+--" + core + "-" * (self.border_w - 2 - len(core)) + "+"

    def plain_bar(self):
        return "+" + "-" * self.border_w + "+"


def render_ezkontrol(panel, geo, title):
    """EZkontrol panel rows (no trailing stats line)."""
    s = panel.state
    L = [geo.title_bar(title)]

    if "bus_voltage" in s:
        L.append(geo.row2(
            geo.slot(panel, "Battery", f"{s['bus_voltage']:.1f} V",     "bus_voltage"),
            geo.slot(panel, "Bus I",   f"{s['bus_current']:+.1f} A",    "bus_current")))
        L.append(geo.row2(
            geo.slot(panel, "Phase I", f"{s['phase_current']:+.1f} A",  "phase_current"),
            geo.slot(panel, "Speed",   f"{s['motor_speed']:+d} rpm",    "motor_speed")))
    else:
        L.append(geo.text_row("(waiting for MCU frames 0x180117EF/0x180217EF...)"))
        L.append(geo.empty_row())

    if "controller_temp" in s:
        L.append(geo.row2(
            geo.slot(panel, "Ctrl temp", f"{s['controller_temp']:+d} C", "controller_temp"),
            geo.slot(panel, "Motor T",   f"{s['motor_temp']:+d} C",      "motor_temp")))
        L.append(geo.row2(
            geo.slot(panel, "Accel",     f"{s['throttle']} %",           "throttle"),
            geo.slot(panel, "Gear",      s["gear"],                      "gear")))
        L.append(geo.row2(
            geo.slot(panel, "Brake",     s["brake"],                     "brake"),
            geo.slot(panel, "Mode",      s["op_mode"],                   "op_mode")))
        L.append(geo.row2(
            geo.slot(panel, "Contactor", s["dc_contactor"],              "dc_contactor"),
            geo.slot(panel, "Life",      f"0x{s['life']:X}",             "life")))
        err = s["errors"]
        err_max = geo.content_w - geo.label_w - 2
        if len(err) > err_max:
            err = err[:err_max - 3] + "..."
        L.append(geo.row_wide(panel, "Errors", err, "errors"))
    else:
        for _ in range(5):
            L.append(geo.empty_row())

    L.append(geo.plain_bar())
    return L


def render_bestgo(panel, geo, title):
    """BESTGO panel rows (no trailing stats line)."""
    s = panel.state
    L = [geo.title_bar(title)]

    if "battery_name" in s or "manufacturer" in s:
        ident = s.get("battery_name", "?")
        if s.get("manufacturer"):
            ident = f"{ident}  ({s['manufacturer']})"
        L.append(geo.row_wide(panel, "Battery", ident, "battery_name"))
    else:
        L.append(geo.text_row("(waiting for BMS frames 0x351/0x355/0x356...)"))

    if "soc" in s:
        L.append(geo.row2(geo.slot(panel, "SOC", f"{s['soc']} %", "soc"),
                          geo.slot(panel, "SOH", f"{s['soh']} %", "soh")))
    else:
        L.append(geo.empty_row())

    if "pack_voltage" in s:
        L.append(geo.row2(
            geo.slot(panel, "Pack V", f"{s['pack_voltage']:.2f} V",  "pack_voltage"),
            geo.slot(panel, "Pack I", f"{s['pack_current']:+.1f} A", "pack_current")))
        cap = f"{s['nominal_capacity']} Ah" if "nominal_capacity" in s else "--"
        L.append(geo.row2(
            geo.slot(panel, "Pack T", f"{s['pack_temp']:.1f} C", "pack_temp"),
            geo.slot(panel, "Capacity", cap,                     "nominal_capacity")))
    else:
        L.append(geo.empty_row())
        L.append(geo.empty_row())

    if "charge_voltage_limit" in s:
        L.append(geo.row_wide(
            panel, "Charge lim",
            f"{s['charge_voltage_limit']:.1f} V   {s['charge_current_limit']:.1f} A",
            "charge_voltage_limit"))
        L.append(geo.row_wide(
            panel, "Dischg lim",
            f"{s['discharge_voltage_limit']:.1f} V   {s['discharge_current_limit']:.1f} A",
            "discharge_voltage_limit"))
    else:
        L.append(geo.empty_row())
        L.append(geo.empty_row())

    if "cell_voltage_min" in s:
        L.append(geo.row_wide(
            panel, "Cell V",
            f"{s['cell_voltage_min']}-{s['cell_voltage_max']} mV"
            f"  (d={s['cell_voltage_delta']} mV)",
            "cell_voltage_max"))
    else:
        L.append(geo.empty_row())

    if "cell_temp_min" in s and "cell_temp_max" in s:
        ct = f"{s['cell_temp_min']:.1f}-{s['cell_temp_max']:.1f} C"
    else:
        ct = "--"
    L.append(geo.row2(geo.slot(panel, "Cell T", ct, "cell_temp_max"),
                      geo.slot(panel, "Firmware", s.get("firmware", "--"), "firmware")))

    L.append(geo.row_wide(panel, "Alarms",   s.get("alarms",   "--"), "alarms"))
    L.append(geo.row_wide(panel, "Warnings", s.get("warnings", "--"), "warnings"))
    L.append(geo.plain_bar())
    return L
