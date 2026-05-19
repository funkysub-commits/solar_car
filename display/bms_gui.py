import threading
import sys
from typing import Iterable, Optional

import dearpygui.dearpygui as dpg

from battery_data.smart_bms import BMSInfo, ProtectionStatus


ALL_SECTIONS = (
    "options",       # temperature-unit toggle bar
    "summary",       # top status header (voltage / current / power / SoC)
    "pack",          # detailed pack-level tab
    "cells",         # per-cell voltage matrix tab
    "temperatures",  # NTC, MOS, env temperatures tab
    "mos",           # MOSFET + balance state tab
    "protection",    # protection flag grid tab
    "alarms",        # raw alarm strings tab
    "settings",      # configured protection thresholds tab
    "identity",      # SN, firmware versions, comms tab
    "heating",       # heating + force-start tab
)

_PROTECTION_FLAGS = (
    (ProtectionStatus.CELL_OVP,      "Cell OVP"),
    (ProtectionStatus.CELL_UVP,      "Cell UVP"),
    (ProtectionStatus.PACK_OVP,      "Pack OVP"),
    (ProtectionStatus.PACK_UVP,      "Pack UVP"),
    (ProtectionStatus.CHARGE_OTP,    "Chg OTP"),
    (ProtectionStatus.CHARGE_UTP,    "Chg UTP"),
    (ProtectionStatus.DISCHARGE_OTP, "Dsg OTP"),
    (ProtectionStatus.DISCHARGE_UTP, "Dsg UTP"),
    (ProtectionStatus.CHARGE_OCP,    "Chg OCP"),
    (ProtectionStatus.DISCHARGE_OCP, "Dsg OCP"),
    (ProtectionStatus.SHORT_CIRCUIT, "Short Circuit"),
    (ProtectionStatus.IC_ERROR,      "IC Error"),
    (ProtectionStatus.MOS_LOCK,      "MOS Lock"),
)

_LABEL_COLOR = (150, 152, 170)

_lock = threading.Lock()
_info: BMSInfo = BMSInfo()
_is_fahrenheit = False
_num_cells = 16
_sections: set = set(ALL_SECTIONS)


# -------------------------------------------------------------------
# Public write API
# -------------------------------------------------------------------

def update(info: BMSInfo) -> None:
    """Push a fresh BMSInfo snapshot to the dashboard. Thread-safe."""
    global _info
    with _lock:
        _info = info


def update_screen() -> None:
    """Kept for backwards compatibility. The render loop redraws every frame."""
    pass


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

def _temp_str(celsius: float) -> str:
    if _is_fahrenheit:
        return f"{celsius * 9 / 5 + 32:.10g} °F"
    return f"{celsius:.10g} °C"


def _toggle_units(_sender, app_data):
    global _is_fahrenheit
    _is_fahrenheit = bool(app_data)


def _cell_theme(volt: float) -> str:
    if volt < 3.0:
        return "critical_theme"
    if volt < 3.3:
        return "warning_theme"
    return "nominal_theme"


def _cell_label(volt: float) -> str:
    if volt < 3.0:
        return "CRITICAL"
    if volt < 3.3:
        return "Low"
    return "Nominal"


def _set(tag: str, value) -> None:
    if dpg.does_item_exist(tag):
        dpg.set_value(tag, value)


def _theme(tag: str, theme: str) -> None:
    if dpg.does_item_exist(tag):
        dpg.bind_item_theme(tag, theme)


# -------------------------------------------------------------------
# Theme + global style
# -------------------------------------------------------------------

def _build_text_themes() -> None:
    palette = {
        "nominal_theme":  (110, 230, 140),
        "warning_theme":  (255, 200,  80),
        "critical_theme": (255,  90,  90),
        "info_theme":     (110, 200, 255),
        "accent_theme":   (200, 140, 255),
        "muted_theme":    (130, 132, 145),
        "label_theme":    _LABEL_COLOR,
    }
    for tag, color in palette.items():
        with dpg.theme(tag=tag):
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_Text, color)


def _apply_global_style() -> None:
    with dpg.theme() as global_theme:
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_color(dpg.mvThemeCol_WindowBg,         (22, 24, 31))
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg,          (28, 30, 38))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg,          (38, 42, 52))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered,   (50, 56, 70))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive,    (60, 70, 90))
            dpg.add_theme_color(dpg.mvThemeCol_Tab,              (38, 42, 52))
            dpg.add_theme_color(dpg.mvThemeCol_TabHovered,       (90, 110, 160))
            dpg.add_theme_color(dpg.mvThemeCol_TabActive,        (70, 105, 180))
            dpg.add_theme_color(dpg.mvThemeCol_Header,           (50, 60, 80))
            dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered,    (60, 80, 110))
            dpg.add_theme_color(dpg.mvThemeCol_HeaderActive,     (70, 100, 140))
            dpg.add_theme_color(dpg.mvThemeCol_TableHeaderBg,    (44, 50, 65))
            dpg.add_theme_color(dpg.mvThemeCol_TableBorderStrong,(60, 70, 90))
            dpg.add_theme_color(dpg.mvThemeCol_TableBorderLight, (50, 55, 70))
            dpg.add_theme_color(dpg.mvThemeCol_Separator,        (60, 65, 80))
            dpg.add_theme_color(dpg.mvThemeCol_PlotHistogram,    (110, 200, 255))
            dpg.add_theme_color(dpg.mvThemeCol_Text,             (222, 224, 235))
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 4)
            dpg.add_theme_style(dpg.mvStyleVar_TabRounding,   4)
            dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 16, 14)
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding,   8,  5)
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing,    8,  6)
            dpg.add_theme_style(dpg.mvStyleVar_CellPadding,    8,  4)
    dpg.bind_theme(global_theme)


# -------------------------------------------------------------------
# Layout — small reusable blocks
# -------------------------------------------------------------------

def _kv(label: str, tag: str, default: str = "—",
        value_theme: Optional[str] = None) -> None:
    dpg.add_text(label.upper(), color=_LABEL_COLOR)
    dpg.add_text(default, tag=tag)
    if value_theme:
        dpg.bind_item_theme(tag, value_theme)
    dpg.add_spacer(height=4)


def _kv_table(rows) -> None:
    with dpg.table(header_row=True,
                   borders_innerH=True, borders_outerH=True,
                   borders_innerV=True, borders_outerV=True,
                   policy=dpg.mvTable_SizingStretchProp):
        dpg.add_table_column(label="Parameter")
        dpg.add_table_column(label="Value")
        for label, tag in rows:
            with dpg.table_row():
                dpg.add_text(label)
                dpg.add_text("—", tag=tag)


# -------------------------------------------------------------------
# Layout — header + tabs
# -------------------------------------------------------------------

def _build_options_bar() -> None:
    with dpg.group(horizontal=True, horizontal_spacing=20):
        dpg.add_checkbox(label="Display temperature in Fahrenheit (°F)",
                         callback=_toggle_units)


def _build_summary_bar() -> None:
    with dpg.group(horizontal=True, horizontal_spacing=32):
        with dpg.group():
            dpg.add_text("VOLTAGE", color=_LABEL_COLOR)
            dpg.add_text("0.00 V", tag="hdr_voltage")
            dpg.bind_item_theme("hdr_voltage", "info_theme")
        with dpg.group():
            dpg.add_text("CURRENT", color=_LABEL_COLOR)
            dpg.add_text("0.00 A", tag="hdr_current")
        with dpg.group():
            dpg.add_text("POWER", color=_LABEL_COLOR)
            dpg.add_text("0 W", tag="hdr_power")
            dpg.bind_item_theme("hdr_power", "accent_theme")
        with dpg.group():
            dpg.add_text("STATE OF CHARGE", color=_LABEL_COLOR)
            dpg.add_progress_bar(tag="hdr_soc", default_value=0.0,
                                 width=340, height=22, overlay="SoC  0.0%")


def _build_pack_tab() -> None:
    with dpg.tab(label="Pack"):
        dpg.add_spacer(height=6)
        with dpg.group(horizontal=True, horizontal_spacing=80):
            with dpg.group():
                _kv("Total voltage",   "pack_total_v",   "0.00 V",   "info_theme")
                _kv("Current",         "pack_current",   "0.00 A")
                _kv("Power",           "pack_power",     "0.0 W",    "accent_theme")
                _kv("State of charge", "pack_soc",       "0.0 %",    "nominal_theme")
                _kv("State of health", "pack_soh",       "0 %",      "nominal_theme")
            with dpg.group():
                _kv("Remaining capacity", "pack_remaining",  "0.00 Ah")
                _kv("Nominal capacity",   "pack_nominal",    "0.00 Ah")
                _kv("Cycle count",        "pack_cycles",     "0")
                _kv("Cycle throughput",   "pack_cycle_cap",  "0.00 Ah")


def _build_cells_tab(num_cells: int) -> None:
    with dpg.tab(label="Cells"):
        dpg.add_spacer(height=6)
        with dpg.group(horizontal=True, horizontal_spacing=40):
            dpg.add_text("Min:   —", tag="cells_summary_min")
            dpg.bind_item_theme("cells_summary_min", "info_theme")
            dpg.add_text("Max:   —", tag="cells_summary_max")
            dpg.bind_item_theme("cells_summary_max", "info_theme")
            dpg.add_text("Avg:   —", tag="cells_summary_avg")
            dpg.add_text("Δ:     —", tag="cells_summary_delta")
            dpg.bind_item_theme("cells_summary_delta", "accent_theme")
        dpg.add_spacer(height=6)
        dpg.add_separator()
        dpg.add_spacer(height=6)
        with dpg.table(header_row=True,
                       borders_innerH=True, borders_outerH=True,
                       borders_innerV=True, borders_outerV=True,
                       policy=dpg.mvTable_SizingStretchProp,
                       scrollY=True, height=380):
            dpg.add_table_column(label="Cell")
            dpg.add_table_column(label="Voltage")
            dpg.add_table_column(label="Status")
            dpg.add_table_column(label="Balancing")
            for i in range(1, num_cells + 1):
                with dpg.table_row():
                    dpg.add_text(f"#{i:02d}")
                    dpg.add_text("—", tag=f"cell_v_{i}")
                    dpg.add_text("—", tag=f"cell_status_{i}")
                    dpg.bind_item_theme(f"cell_status_{i}", "muted_theme")
                    dpg.add_text("", tag=f"cell_bal_{i}")
                    dpg.bind_item_theme(f"cell_bal_{i}", "info_theme")


def _build_temperatures_tab() -> None:
    with dpg.tab(label="Temps"):
        dpg.add_spacer(height=6)
        with dpg.group(horizontal=True, horizontal_spacing=60):
            with dpg.group():
                dpg.add_text("MOS", color=_LABEL_COLOR)
                dpg.add_text("—", tag="temp_mos")
                dpg.bind_item_theme("temp_mos", "warning_theme")
            with dpg.group():
                dpg.add_text("ENVIRONMENT", color=_LABEL_COLOR)
                dpg.add_text("—", tag="temp_env")
                dpg.bind_item_theme("temp_env", "info_theme")
        dpg.add_spacer(height=10)
        dpg.add_separator()
        dpg.add_spacer(height=6)
        dpg.add_text("NTC SENSORS", color=_LABEL_COLOR)
        dpg.add_spacer(height=4)
        with dpg.table(header_row=True,
                       borders_innerH=True, borders_outerH=True,
                       borders_innerV=True, borders_outerV=True,
                       policy=dpg.mvTable_SizingStretchProp):
            dpg.add_table_column(label="Sensor")
            dpg.add_table_column(label="Reading")
            for i in range(1, 9):
                with dpg.table_row():
                    dpg.add_text(f"NTC #{i}")
                    dpg.add_text("—", tag=f"temp_ntc_{i}")


def _build_mos_tab() -> None:
    with dpg.tab(label="MOS"):
        dpg.add_spacer(height=6)
        with dpg.group(horizontal=True, horizontal_spacing=60):
            with dpg.group():
                dpg.add_text("CHARGE MOSFET", color=_LABEL_COLOR)
                dpg.add_text("OFF", tag="mos_charge")
                dpg.bind_item_theme("mos_charge", "muted_theme")
            with dpg.group():
                dpg.add_text("DISCHARGE MOSFET", color=_LABEL_COLOR)
                dpg.add_text("OFF", tag="mos_discharge")
                dpg.bind_item_theme("mos_discharge", "muted_theme")
            with dpg.group():
                dpg.add_text("BALANCING", color=_LABEL_COLOR)
                dpg.add_text("Idle", tag="mos_balance")
                dpg.bind_item_theme("mos_balance", "muted_theme")


def _build_protection_tab() -> None:
    with dpg.tab(label="Protection"):
        dpg.add_spacer(height=6)
        dpg.add_text("Active flags are shown in red; inactive in gray.",
                     color=_LABEL_COLOR)
        dpg.add_spacer(height=10)
        cols = 4
        for row_start in range(0, len(_PROTECTION_FLAGS), cols):
            with dpg.group(horizontal=True, horizontal_spacing=24):
                for flag, label in _PROTECTION_FLAGS[row_start:row_start + cols]:
                    tag = f"prot_{flag.name}"
                    dpg.add_text(f"⬤  {label}", tag=tag)
                    dpg.bind_item_theme(tag, "muted_theme")
            dpg.add_spacer(height=4)


def _build_alarms_tab() -> None:
    with dpg.tab(label="Alarms"):
        dpg.add_spacer(height=6)
        dpg.add_text("No active alarms", tag="alarm_list")


def _build_settings_tab() -> None:
    with dpg.tab(label="Settings"):
        dpg.add_spacer(height=6)
        _kv_table([
            ("Cell OVP",            "set_cell_ovp"),
            ("Cell OVP recovery",   "set_cell_ovp_recovery"),
            ("Cell UVP",            "set_cell_uvp"),
            ("Cell UVP recovery",   "set_cell_uvp_recovery"),
            ("Pack OVP",            "set_pack_ovp"),
            ("Pack UVP",            "set_pack_uvp"),
            ("Charge OCP",          "set_charge_ocp"),
            ("Discharge OCP",       "set_discharge_ocp"),
            ("Charge OTP",          "set_charge_otp"),
            ("Charge UTP",          "set_charge_utp"),
            ("Discharge OTP",       "set_discharge_otp"),
            ("Discharge UTP",       "set_discharge_utp"),
            ("Balance start",       "set_balance_start"),
            ("Balance delta",       "set_balance_delta"),
            ("Short-circuit delay", "set_sc_delay"),
            ("OCP delay",           "set_ocp_delay"),
        ])


def _build_identity_tab() -> None:
    with dpg.tab(label="Info"):
        dpg.add_spacer(height=6)
        _kv_table([
            ("Serial number",    "id_sn"),
            ("Production date",  "id_production"),
            ("MCU version",      "id_mcu"),
            ("BLE version",      "id_ble"),
            ("Machine version",  "id_machine"),
            ("Protocol type",    "id_protocol"),
            ("Comm mode",        "id_mode"),
        ])


def _build_heating_tab() -> None:
    with dpg.tab(label="Heating"):
        dpg.add_spacer(height=6)
        with dpg.group(horizontal=True, horizontal_spacing=60):
            with dpg.group():
                dpg.add_text("HEATING", color=_LABEL_COLOR)
                dpg.add_text("OFF", tag="heat_state")
                dpg.bind_item_theme("heat_state", "muted_theme")
            with dpg.group():
                dpg.add_text("FORCE START", color=_LABEL_COLOR)
                dpg.add_text("OFF", tag="heat_force_start")
                dpg.bind_item_theme("heat_force_start", "muted_theme")
            with dpg.group():
                dpg.add_text("START TEMP", color=_LABEL_COLOR)
                dpg.add_text("—", tag="heat_start")
            with dpg.group():
                dpg.add_text("STOP TEMP", color=_LABEL_COLOR)
                dpg.add_text("—", tag="heat_stop")


# -------------------------------------------------------------------
# Per-frame widget updates
# -------------------------------------------------------------------

def _update_summary(info: BMSInfo) -> None:
    _set("hdr_voltage", f"{info.total_voltage:.10g} V")
    if dpg.does_item_exist("hdr_current"):
        dpg.set_value("hdr_current", f"{info.current:+.10g} A")
        dpg.bind_item_theme(
            "hdr_current",
            "nominal_theme" if info.current > 0 else
            "info_theme"    if info.current == 0 else
            "accent_theme",
        )
    _set("hdr_power", f"{info.power:.10g} W")
    if dpg.does_item_exist("hdr_soc"):
        soc01 = max(0.0, min(1.0, info.soc / 100.0))
        dpg.set_value("hdr_soc", soc01)
        dpg.configure_item("hdr_soc", overlay=f"SoC  {info.soc:.10g}%")


def _update_pack(info: BMSInfo) -> None:
    _set("pack_total_v",  f"{info.total_voltage:.10g} V")
    _set("pack_current",  f"{info.current:+.10g} A")
    _set("pack_power",    f"{info.power:.10g} W")
    _set("pack_soc",      f"{info.soc:.10g} %")
    _set("pack_soh",      f"{info.soh} %")
    _set("pack_remaining",f"{info.remaining_capacity:.10g} Ah")
    _set("pack_nominal",  f"{info.nominal_capacity:.10g} Ah")
    _set("pack_cycles",   str(info.cycle_count))
    _set("pack_cycle_cap",f"{info.cycle_capacity:.10g} Ah")


def _update_cells(info: BMSInfo) -> None:
    cells = info.cell_voltages
    for idx in range(_num_cells):
        i = idx + 1
        v_tag = f"cell_v_{i}"
        s_tag = f"cell_status_{i}"
        b_tag = f"cell_bal_{i}"
        if not dpg.does_item_exist(v_tag):
            continue
        if idx < len(cells):
            volt = cells[idx]
            marker = ""
            if i == info.min_cell_number:
                marker = "  ◄MIN"
            elif i == info.max_cell_number:
                marker = "  ◄MAX"
            dpg.set_value(v_tag, f"{volt:.10g} V{marker}")
            dpg.set_value(s_tag, _cell_label(volt))
            dpg.bind_item_theme(s_tag, _cell_theme(volt))
            balancing = bool(info.balance_status & (1 << idx))
            dpg.set_value(b_tag, "●" if balancing else "")
        else:
            dpg.set_value(v_tag, "—")
            dpg.set_value(s_tag, "—")
            dpg.bind_item_theme(s_tag, "muted_theme")
            dpg.set_value(b_tag, "")

    if info.cell_voltages:
        _set("cells_summary_min",
             f"Min:   {info.min_cell_voltage:.10g} V  (cell {info.min_cell_number})")
        _set("cells_summary_max",
             f"Max:   {info.max_cell_voltage:.10g} V  (cell {info.max_cell_number})")
        _set("cells_summary_avg", f"Avg:   {info.avg_cell_voltage:.10g} V")
        _set("cells_summary_delta",
             f"Δ:     {info.delta_cell_voltage * 1000:.10g} mV")


def _update_temperatures(info: BMSInfo) -> None:
    _set("temp_mos", _temp_str(info.mos_temperature))
    _set("temp_env", _temp_str(info.env_temperature))
    for i in range(8):
        tag = f"temp_ntc_{i + 1}"
        if i < len(info.temperatures):
            _set(tag, _temp_str(info.temperatures[i]))
        else:
            _set(tag, "—")


def _update_mos(info: BMSInfo) -> None:
    _set("mos_charge",    "ON" if info.charge_mos_on else "OFF")
    _theme("mos_charge",  "nominal_theme" if info.charge_mos_on else "muted_theme")
    _set("mos_discharge", "ON" if info.discharge_mos_on else "OFF")
    _theme("mos_discharge", "nominal_theme" if info.discharge_mos_on else "muted_theme")
    _set("mos_balance",   "ACTIVE" if info.balance_active else "Idle")
    _theme("mos_balance", "info_theme" if info.balance_active else "muted_theme")


def _update_protection(info: BMSInfo) -> None:
    for flag, _label in _PROTECTION_FLAGS:
        tag = f"prot_{flag.name}"
        active = bool(info.protection_status & flag)
        _theme(tag, "critical_theme" if active else "muted_theme")


def _update_alarms(info: BMSInfo) -> None:
    text = "\n".join(info.alarm_info) if info.alarm_info else "No active alarms"
    _set("alarm_list", text)


def _update_settings(info: BMSInfo) -> None:
    _set("set_cell_ovp",          f"{info.cell_ovp:.10g} V")
    _set("set_cell_ovp_recovery", f"{info.cell_ovp_recovery:.10g} V")
    _set("set_cell_uvp",          f"{info.cell_uvp:.10g} V")
    _set("set_cell_uvp_recovery", f"{info.cell_uvp_recovery:.10g} V")
    _set("set_pack_ovp",          f"{info.pack_ovp:.10g} V")
    _set("set_pack_uvp",          f"{info.pack_uvp:.10g} V")
    _set("set_charge_ocp",        f"{info.charge_ocp:.10g} A")
    _set("set_discharge_ocp",     f"{info.discharge_ocp:.10g} A")
    _set("set_charge_otp",        _temp_str(info.charge_otp))
    _set("set_charge_utp",        _temp_str(info.charge_utp))
    _set("set_discharge_otp",     _temp_str(info.discharge_otp))
    _set("set_discharge_utp",     _temp_str(info.discharge_utp))
    _set("set_balance_start",     f"{info.balance_start_voltage:.10g} V")
    _set("set_balance_delta",     f"{info.balance_delta * 1000:.10g} mV")
    _set("set_sc_delay",          f"{info.short_circuit_delay} µs")
    _set("set_ocp_delay",         f"{info.ocp_delay} ms")


def _update_identity(info: BMSInfo) -> None:
    _set("id_sn",         info.sn_code or "—")
    _set("id_production", info.production_date or "—")
    _set("id_mcu",        info.mcu_version or "—")
    _set("id_ble",        info.ble_version or "—")
    _set("id_machine",    info.machine_version or "—")
    _set("id_protocol",   str(info.comm_protocol_type))
    _set("id_mode",       str(info.comm_mode))


def _update_heating(info: BMSInfo) -> None:
    _set("heat_state",        "ON" if info.heating_on else "OFF")
    _theme("heat_state",      "warning_theme" if info.heating_on else "muted_theme")
    _set("heat_force_start",  "ON" if info.force_start_on else "OFF")
    _theme("heat_force_start","warning_theme" if info.force_start_on else "muted_theme")
    _set("heat_start", _temp_str(info.heating_start_temp))
    _set("heat_stop",  _temp_str(info.heating_stop_temp))


_SECTION_UPDATERS = {
    "summary":      _update_summary,
    "pack":         _update_pack,
    "cells":        _update_cells,
    "temperatures": _update_temperatures,
    "mos":          _update_mos,
    "protection":   _update_protection,
    "alarms":       _update_alarms,
    "settings":     _update_settings,
    "identity":     _update_identity,
    "heating":      _update_heating,
}


def _pull_data_into_widgets() -> None:
    with _lock:
        info = _info
    for name, updater in _SECTION_UPDATERS.items():
        if name in _sections:
            updater(info)


# -------------------------------------------------------------------
# Dashboard entry point
# -------------------------------------------------------------------

def start_dashboard(num_cells: int = 16,
                    worker_callback=None,
                    sections: Optional[Iterable[str]] = None) -> None:
    """Launch the telemetry dashboard on the main thread.

    Parameters
    ----------
    num_cells : number of cells to render in the cell matrix.
    worker_callback : zero-arg function started in a daemon thread before the
        render loop. Use it to call ``bms_gui.update(info)`` with fresh data.
    sections : iterable of section names to include. ``None`` includes all.
        Valid names are listed in :data:`ALL_SECTIONS`.
    """
    global _num_cells, _sections
    _num_cells = num_cells
    if sections is None:
        _sections = set(ALL_SECTIONS)
    else:
        requested = set(sections)
        invalid = requested - set(ALL_SECTIONS)
        if invalid:
            raise ValueError(
                f"Unknown section(s): {sorted(invalid)}. "
                f"Valid sections: {ALL_SECTIONS}"
            )
        _sections = requested

    dpg.create_context()
    _build_text_themes()
    _apply_global_style()
    dpg.create_viewport(title="Solar Car Telemetry Studio",
                        width=960, height=720)

    with dpg.window(tag="main_window",
                    no_title_bar=True, no_resize=True,
                    no_move=True, no_collapse=True):
        dpg.add_text("SOLAR CAR TELEMETRY", color=(225, 227, 240))
        dpg.add_separator()
        dpg.add_spacer(height=8)

        if "options" in _sections:
            _build_options_bar()
            dpg.add_spacer(height=8)
            dpg.add_separator()
            dpg.add_spacer(height=8)

        if "summary" in _sections:
            _build_summary_bar()
            dpg.add_spacer(height=10)
            dpg.add_separator()
            dpg.add_spacer(height=6)

        tab_builders = [
            ("pack",         _build_pack_tab),
            ("cells",        lambda: _build_cells_tab(num_cells)),
            ("temperatures", _build_temperatures_tab),
            ("mos",          _build_mos_tab),
            ("protection",   _build_protection_tab),
            ("alarms",       _build_alarms_tab),
            ("settings",     _build_settings_tab),
            ("identity",     _build_identity_tab),
            ("heating",      _build_heating_tab),
        ]
        active_tabs = [(n, b) for n, b in tab_builders if n in _sections]
        if active_tabs:
            with dpg.tab_bar():
                for _name, builder in active_tabs:
                    builder()

    dpg.setup_dearpygui()
    dpg.set_primary_window("main_window", True)
    dpg.show_viewport()

    if worker_callback:
        threading.Thread(target=worker_callback, daemon=True).start()

    while dpg.is_dearpygui_running():
        _pull_data_into_widgets()
        dpg.render_dearpygui_frame()

    dpg.destroy_context()
    sys.exit()
