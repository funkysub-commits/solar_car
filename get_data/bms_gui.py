import threading
import time
import sys
import dearpygui.dearpygui as dpg

# Internal thread-safe cache memory array
_bms_data = {
    "soc": 1.0,
    "temp_c": 25.0,
    "cells": [3.7 for _ in range(16)],
    "is_f": False,
    "array_w": 0.0,
    "bus_a": 0.0,
    "mppt_eff": 0.0
}
_lock = threading.Lock()
_num_cells = 16

def _get_temp_text():
    """Formats temperature string depending on unit setting."""
    if _bms_data["is_f"]:
        temp_f = (_bms_data["temp_c"] * 9/5) + 32
        return f"{temp_f:.1f} °F"
    return f"{_bms_data['temp_c']:.1f} °C"

def _toggle_units(sender, app_data):
    """Callback triggered directly inside the UI framework context."""
    with _lock:
        _bms_data["is_f"] = app_data

def _pull_data_into_widgets():
    """Safely reads the shared cache memory to update local layout widgets."""
    with _lock:
        soc_val = _bms_data["soc"]
        temp_str = _get_temp_text()
        cell_snapshot = list(_bms_data["cells"])
        array_w = _bms_data["array_w"]
        bus_a = _bms_data["bus_a"]
        mppt_eff = _bms_data["mppt_eff"]

    # Modify layout text values safely within the main thread loop
    dpg.set_value("array_w_display", f"{array_w:.1f} W")
    dpg.set_value("bus_a_display", f"{bus_a:.2f} A")
    dpg.set_value("mppt_eff_display", f"{mppt_eff:.1f} %")

    dpg.set_value("soc_gauge", soc_val)
    dpg.configure_item("soc_gauge", overlay=f"{soc_val * 100:.1f}%")
    dpg.set_value("temp_text_display", temp_str)

    # Update cell matrix
    for idx in range(min(_num_cells, len(cell_snapshot))):
        cell_id = idx + 1
        volt = cell_snapshot[idx]
        dpg.set_value(f"cell_v_{cell_id}", f"{volt:.3f} V")
        
        if volt < 3.0:
            dpg.set_value(f"cell_status_{cell_id}", "CRITICAL LOW")
            dpg.bind_item_theme(f"cell_status_{cell_id}", "critical_theme")
        elif volt < 3.5:
            dpg.set_value(f"cell_status_{cell_id}", "Low Balance")
            dpg.bind_item_theme(f"cell_status_{cell_id}", "warning_theme")
        else:
            dpg.set_value(f"cell_status_{cell_id}", "Nominal")
            dpg.bind_item_theme(f"cell_status_{cell_id}", "nominal_theme")

def start_dashboard(num_cells=16, worker_callback=None):
    """
    Launches the telemetry environment dashboard on the MAIN thread.
    Spawns your telemetry data loop inside a background worker thread.
    """
    global _num_cells
    _num_cells = num_cells
    _bms_data["cells"] = [3.7 for _ in range(num_cells)]
    
    # 1. Initialize Context
    dpg.create_context()
    
    # Define highlight themes
    with dpg.theme(tag="nominal_theme"):
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_color(dpg.mvThemeCol_Text, (100, 250, 100))
    with dpg.theme(tag="warning_theme"):
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_color(dpg.mvThemeCol_Text, (250, 180, 50))
    with dpg.theme(tag="critical_theme"):
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_color(dpg.mvThemeCol_Text, (250, 50, 50))
    with dpg.theme(tag="solar_theme"):
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_color(dpg.mvThemeCol_Text, (100, 200, 255))

    dpg.create_viewport(title='Solar Car Telemetry Studio', width=880, height=720)

    # 2. Build Dashboard Layout
    with dpg.window(label="Race Dashboard", width=850, height=680, no_collapse=True, no_move=True, no_resize=True):
        with dpg.collapsing_header(label="SYSTEM OPTIONS", default_open=True):
            dpg.add_checkbox(label="Display Temperature in Fahrenheit (°F)", callback=_toggle_units)
            
        with dpg.collapsing_header(label="SOLAR ARRAY & POWERTRAIN PERFORMANCE", default_open=True):
            with dpg.group(horizontal=True, horizontal_spacing=50):
                with dpg.group():
                    dpg.add_text("SOLAR ARRAY INPUT:")
                    dpg.add_text("0.0 W", tag="array_w_display")
                    dpg.bind_item_theme("array_w_display", "solar_theme")
                with dpg.group():
                    dpg.add_text("ACTIVE BUS CURRENT:")
                    dpg.add_text("0.00 A", tag="bus_a_display")
                    dpg.bind_item_theme("bus_a_display", "solar_theme")
                with dpg.group():
                    dpg.add_text("MPPT EFFICIENCY:")
                    dpg.add_text("0.0 %", tag="mppt_eff_display")
                    dpg.bind_item_theme("mppt_eff_display", "solar_theme")

        with dpg.collapsing_header(label="BATTERY PACK STATUS", default_open=True):
            with dpg.group(horizontal=True, horizontal_spacing=40):
                with dpg.group():
                    dpg.add_text("PACK CAPACITY (SoC):")
                    dpg.add_progress_bar(tag="soc_gauge", default_value=1.0, width=350, height=35, overlay="100.0%")
                with dpg.group():
                    dpg.add_text("PACK CORE TEMPERATURE:")
                    dpg.add_text("25.0 °C", tag="temp_text_display")
                    
        dpg.add_spacer(height=15)
        
        with dpg.collapsing_header(label="INDIVIDUAL CELL TELEMETRY MATRIX", default_open=True):
            with dpg.table(header_row=True, borders_innerH=True, borders_outerH=True, borders_innerV=True, borders_outerV=True):
                dpg.add_table_column(label="Cell Index")
                dpg.add_table_column(label="Live Voltage")
                dpg.add_table_column(label="Balance Status")
                
                for i in range(1, num_cells + 1):
                    with dpg.table_row():
                        dpg.add_text(f"Cell Node #{i:02d}")
                        dpg.add_text("3.700 V", tag=f"cell_v_{i}")
                        dpg.add_text("Nominal", tag=f"cell_status_{i}")
                        dpg.bind_item_theme(f"cell_status_{i}", "nominal_theme")

    dpg.setup_dearpygui()
    dpg.show_viewport()
    
    # 3. Spawn your custom background loop function if provided
    if worker_callback:
        t = threading.Thread(target=worker_callback, daemon=True)
        t.start()
    
    # 4. Execute UI render frames continuously on the OS Main Thread
    while dpg.is_dearpygui_running():
        _pull_data_into_widgets()
        dpg.render_dearpygui_frame()
        
    dpg.destroy_context()
    sys.exit()

# -------------------------------------------------------------
# PUBLIC WRITE FUNCTIONS (Call these from your background loop)
# -------------------------------------------------------------

def battery_soc(percentage_decimal):
    with _lock: _bms_data["soc"] = max(0.0, min(1.0, float(percentage_decimal)))

def battery_temp(celsius_value):
    with _lock: _bms_data["temp_c"] = float(celsius_value)

def battery_cells(voltage_list):
    with _lock:
        limit = min(len(voltage_list), len(_bms_data["cells"]))
        for i in range(limit):
            _bms_data["cells"][i] = float(voltage_list[i])

def solar_performance(array_watts, bus_amps, mppt_efficiency_pct):
    with _lock:
        _bms_data["array_w"] = float(array_watts)
        _bms_data["bus_a"] = float(bus_amps)
        _bms_data["mppt_eff"] = float(mppt_efficiency_pct)

def update_screen():
    pass
