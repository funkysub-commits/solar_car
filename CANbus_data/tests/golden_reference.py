"""Frozen copies of the PRE-CONSOLIDATION decoders, for equivalence testing.

When the duplicated decoders were merged into the solarcar_can package
(2026-06), the originals were deleted. These verbatim copies preserve their
exact decode behaviour so test_decoders.py can prove the shared package
produces the same values. Do not "fix" or modernise this file -- its value
is that it does NOT change.

Two lineages are preserved:

  * addon_*  -- from ha_addons/solar-car-canbus/can_reader.py (the HA add-on;
                canonical field names, rounding, full error names)
  * script_* -- from pc_files/bestgo_decode.py + pc_files/ezkontrol_decode.py
                (the PC/Pi dashboards; short field names, no rounding,
                abbreviated error names). pc_files and rp_files parsers were
                byte-identical, verified by diff before deletion.
"""
import struct

# ===========================================================================
# addon lineage -- verbatim from can_reader.py
# ===========================================================================
GEAR_MAP = {0: "None", 1: "R", 2: "N", 3: "D1", 4: "D2", 5: "D3", 6: "S", 7: "P"}

ERROR_BITS_BYTE4 = [
    "Overcurrent", "Overload", "Overvoltage", "Undervoltage",
    "Controller Overheat", "Motor Overheat", "Motor Stalled", "Motor Out of Phase",
]
ERROR_BITS_BYTE5 = [
    "Motor Sensor", "Motor AUX Sensor", "Encoder Misaligned", "Anti-Runaway",
    "Main Accelerator", "AUX Accelerator", "Pre-charge", "DC Contactor",
]
ERROR_BITS_BYTE6 = [
    "Power Valve", "Current Sensor", "Auto-tune", "RS485", "CAN", "Software",
]


def addon_ez_decode_msg1(data):
    bus_v_raw   = struct.unpack_from('<H', data, 0)[0]
    bus_i_raw   = struct.unpack_from('<H', data, 2)[0]
    phase_i_raw = struct.unpack_from('<H', data, 4)[0]
    speed_raw   = struct.unpack_from('<H', data, 6)[0]
    return {
        "bus_voltage":   round(bus_v_raw * 0.1, 1),
        "bus_current":   round(bus_i_raw * 0.1 - 3200, 1),
        "phase_current": round(phase_i_raw * 0.1 - 3200, 1),
        "motor_speed":   speed_raw - 32000,
    }


def addon_ez_decode_msg2(data):
    sb = data[3]
    errors = []
    for i, name in enumerate(ERROR_BITS_BYTE4):
        if data[4] & (1 << i): errors.append(name)
    for i, name in enumerate(ERROR_BITS_BYTE5):
        if data[5] & (1 << i): errors.append(name)
    for i, name in enumerate(ERROR_BITS_BYTE6):
        if data[6] & (1 << i): errors.append(name)
    errors_str = ", ".join(errors) if errors else "None"
    if len(errors_str) > 250:
        errors_str = errors_str[:247] + "..."
    return {
        "controller_temp": data[0] - 40,
        "motor_temp":      data[1] - 40,
        "throttle":        data[2],
        "gear":            GEAR_MAP.get(sb & 0x07, "Unknown"),
        "brake":           "On" if (sb >> 3) & 1 else "Off",
        "op_mode":         (sb >> 4) & 0x07,            # raw int in the old add-on
        "dc_contactor":    "On" if (sb >> 7) & 1 else "Off",
        "errors":          errors_str,
        "error_count":     len(errors),
    }


BG_LIMITS, BG_SOC, BG_MEAS, BG_ALARMS = 0x351, 0x355, 0x356, 0x35A
BG_MFR, BG_INFO, BG_NAME0, BG_NAME1 = 0x35E, 0x35F, 0x370, 0x371
BG_CELLEXT, BG_CAPACITY = 0x373, 0x379
BG_IDS = {BG_LIMITS, BG_SOC, BG_MEAS, BG_ALARMS, BG_MFR, BG_INFO,
          BG_NAME0, BG_NAME1, BG_CELLEXT, BG_CAPACITY}
KELVIN = 273.15

_bg_name = {}


def addon_bg_reset():
    _bg_name.clear()


def _u16(data, off):
    return int.from_bytes(data[off:off + 2], "little")


def _s16(data, off):
    return int.from_bytes(data[off:off + 2], "little", signed=True)


def _ascii(raw):
    out = []
    for b in raw:
        if b == 0:
            break
        out.append(chr(b) if 32 <= b < 127 else ".")
    return "".join(out).strip()


def _bg_battery_name():
    parts = [_ascii(_bg_name.get(0, b"")), _ascii(_bg_name.get(1, b""))]
    return " ".join(p for p in parts if p) or "?"


def addon_bg_decode(arb_id, data):
    if arb_id not in BG_IDS:
        return None
    f = {}
    if arb_id == BG_LIMITS and len(data) >= 8:
        f["charge_voltage_limit"]    = round(_u16(data, 0) * 0.1, 1)
        f["charge_current_limit"]    = round(_s16(data, 2) * 0.1, 1)
        f["discharge_current_limit"] = round(_s16(data, 4) * 0.1, 1)
        f["discharge_voltage_limit"] = round(_u16(data, 6) * 0.1, 1)
    elif arb_id == BG_SOC and len(data) >= 4:
        f["soc"] = _u16(data, 0)
        f["soh"] = _u16(data, 2)
    elif arb_id == BG_MEAS and len(data) >= 6:
        f["pack_voltage"] = round(_s16(data, 0) * 0.01, 2)
        f["pack_current"] = round(_s16(data, 2) * 0.1, 1)
        f["pack_temp"]    = round(_s16(data, 4) * 0.1, 1)
    elif arb_id == BG_ALARMS and len(data) >= 8:
        alarm, warn = data[0:4], data[4:8]
        f["alarms"]   = "OK" if not any(alarm) else alarm.hex()
        f["warnings"] = "OK" if not any(warn) else warn.hex()
    elif arb_id == BG_MFR:
        f["manufacturer"] = _ascii(data)
    elif arb_id == BG_INFO and len(data) >= 6:
        ver = _u16(data, 2)
        f["firmware"]         = f"v{ver >> 8}.{ver & 0xFF}"
        f["nominal_capacity"] = _u16(data, 4)
    elif arb_id == BG_NAME0:
        _bg_name[0] = bytes(data)
        f["battery_name"] = _bg_battery_name()
    elif arb_id == BG_NAME1:
        _bg_name[1] = bytes(data)
        f["battery_name"] = _bg_battery_name()
    elif arb_id == BG_CELLEXT and len(data) >= 8:
        vmin, vmax = _u16(data, 0), _u16(data, 2)
        f["cell_voltage_min"]   = vmin
        f["cell_voltage_max"]   = vmax
        f["cell_voltage_delta"] = vmax - vmin
        tmin, tmax = _u16(data, 4), _u16(data, 6)
        if tmin:
            f["cell_temp_min"] = round(tmin - KELVIN, 1)
        if tmax:
            f["cell_temp_max"] = round(tmax - KELVIN, 1)
    elif arb_id == BG_CAPACITY and len(data) >= 2:
        f["installed_capacity"] = _u16(data, 0)
    return f


# ===========================================================================
# script lineage -- verbatim from pc_files/ (identical to rp_files/)
# ===========================================================================
SCRIPT_GEAR_NAMES = {0: "NO", 1: "R", 2: "N", 3: "D1", 4: "D2", 5: "D3", 6: "S", 7: "P"}
SCRIPT_OP_MODE_NAMES = {0: "Normal", 2: "Cruise", 3: "EBS", 4: "Hold"}
ERRORS_A = ["Overcurrent", "Overload", "Overvolt", "Undervolt",
            "CtrlOT", "MotorOT", "Stalled", "OutOfPhase"]
ERRORS_B = ["MotorSens", "MotorAUX", "EncMis", "AntiRunaway",
            "MainAccel", "AuxAccel", "PreCharge", "DCCont"]
ERRORS_C = ["PowerValve", "CurrSens", "AutoTune", "RS485", "CAN", "Software"]


def script_parse_msg_i(data):
    return {
        "v":      int.from_bytes(data[0:2], "little") * 0.1,
        "ibus":   int.from_bytes(data[2:4], "little") * 0.1 - 3200,
        "iphase": int.from_bytes(data[4:6], "little") * 0.1 - 3200,
        "rpm":    int.from_bytes(data[6:8], "little") - 32000,
    }


def script_parse_msg_ii(data):
    sb = data[3]
    errs = []
    for bit, name in enumerate(ERRORS_A):
        if data[4] & (1 << bit): errs.append(name)
    for bit, name in enumerate(ERRORS_B):
        if data[5] & (1 << bit): errs.append(name)
    for bit, name in enumerate(ERRORS_C):
        if data[6] & (1 << bit): errs.append(name)
    return {
        "tctrl":     data[0] - 40,
        "tmot":      data[1] - 40,
        "accel":     data[2],
        "gear":      SCRIPT_GEAR_NAMES.get(sb & 0x07, str(sb & 0x07)),
        "brake":     "ON" if (sb >> 3) & 1 else "off",
        "mode":      SCRIPT_OP_MODE_NAMES.get((sb >> 4) & 7, f"?({(sb>>4)&7})"),
        "contactor": "ON" if (sb >> 7) & 1 else "off",
        "errors":    ",".join(errs) if errs else "OK",
        "life":      data[7] >> 4,
    }


def script_parse_limits(data):
    return {
        "cvl": _u16(data, 0) * 0.1,
        "ccl": _s16(data, 2) * 0.1,
        "dcl": _s16(data, 4) * 0.1,
        "dvl": _u16(data, 6) * 0.1,
    }


def script_parse_soc(data):
    return {
        "soc":     _u16(data, 0),
        "soh":     _u16(data, 2),
        "soc_hi":  _u16(data, 4) * 0.01,
    }


def script_parse_meas(data):
    return {
        "pack_v": _s16(data, 0) * 0.01,
        "pack_i": _s16(data, 2) * 0.1,
        "pack_t": _s16(data, 4) * 0.1,
    }


def script_parse_alarms(data):
    alarm = data[0:4]
    warn = data[4:8]
    return {
        "alarms":   "OK" if not any(alarm) else alarm.hex(),
        "warnings": "OK" if not any(warn) else warn.hex(),
    }


def script_parse_info(data):
    ver = _u16(data, 2)
    return {
        "chem":    f"0x{_u16(data, 0):04X}",
        "fw":      f"v{ver >> 8}.{ver & 0xFF}",
        "cap_nom": _u16(data, 4),
    }


def script_parse_cellext(data):
    out = {
        "cell_vmin": _u16(data, 0),
        "cell_vmax": _u16(data, 2),
    }
    tmin, tmax = _u16(data, 4), _u16(data, 6)
    if tmin:
        out["cell_tmin"] = tmin - KELVIN
    if tmax:
        out["cell_tmax"] = tmax - KELVIN
    return out
