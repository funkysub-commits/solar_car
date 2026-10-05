"""Golden-master tests: solarcar_can vs the frozen pre-consolidation decoders.

Replays the real captures in tests/fixtures/ through the new shared decoders
and asserts the output matches BOTH pre-consolidation lineages
(see golden_reference.py), modulo the documented intentional changes:

  * script short field names -> canonical names (sensor.<prefix>_<field>)
  * unrounded script floats  -> rounded to the canonical decimals
  * gear 0: "NO" -> "None"; brake/contactor "ON"/"off" -> "On"/"Off"
  * op_mode: raw int (old add-on) -> name string; and the old name table
    {0: "Normal", 2..4} missed the "0: Stop / 1: Drive" half of the enum that
    sits on the other side of a page break in the PDF, so 0 is now "Stop"
    (was "Normal") and 1 is "Drive" (was "?(1)")
  * errors: abbreviated names, ","-joined -> full names, ", "-joined
  * battery name: the old scripts concatenated the two name-frame payloads
    BEFORE the stop-at-first-NUL scan, so a NUL-padded first half swallowed
    the second ("Lithium" instead of "Lithium Valley"). The add-on behaviour
    (clean each half, join with a space) is the keeper; not compared against
    the script lineage.

Run:  python tests/test_decoders.py   (or pytest tests/)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # CANbus_data/
sys.path.insert(0, str(Path(__file__).resolve().parent))         # tests/

import can

import golden_reference as gold
from solarcar_can import bestgo, ezkontrol
from solarcar_can.bestgo import BestgoDecoder
from solarcar_can.ezkontrol import EzkontrolDecoder, OP_MODE_NAMES

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Decimals the canonical fields are rounded to (None = exact / non-float)
BG_SCRIPT_MAP = [
    # (script key, canonical key, round digits)
    ("cvl",       "charge_voltage_limit",    1),
    ("ccl",       "charge_current_limit",    1),
    ("dcl",       "discharge_current_limit", 1),
    ("dvl",       "discharge_voltage_limit", 1),
    ("soc",       "soc",                     None),
    ("soh",       "soh",                     None),
    ("soc_hi",    "soc_hi",                  2),
    ("pack_v",    "pack_voltage",            2),
    ("pack_i",    "pack_current",            1),
    ("pack_t",    "pack_temp",               1),
    ("alarms",    "alarms",                  None),
    ("warnings",  "warnings",                None),
    ("chem",      "chemistry",               None),
    ("fw",        "firmware",                None),
    ("cap_nom",   "nominal_capacity",        None),
    ("cell_vmin", "cell_voltage_min",        None),
    ("cell_vmax", "cell_voltage_max",        None),
    ("cell_tmin", "cell_temp_min",           1),
    ("cell_tmax", "cell_temp_max",           1),
    ("cap_inst",  "installed_capacity",      None),
]

EZ_SCRIPT_MAP = [
    ("v",         "bus_voltage",     1),
    ("ibus",      "bus_current",     1),
    ("iphase",    "phase_current",   1),
    ("rpm",       "motor_speed",     None),
    ("tctrl",     "controller_temp", None),
    ("tmot",      "motor_temp",      None),
    ("accel",     "throttle",        None),
    ("life",      "life",            None),
]

SCRIPT_MODE_RENAMED = {"Normal": "Stop", "?(1)": "Drive"}

ERR_SHORT2FULL = dict(
    list(zip(gold.ERRORS_A, gold.ERROR_BITS_BYTE4))
    + list(zip(gold.ERRORS_B, gold.ERROR_BITS_BYTE5))
    + list(zip(gold.ERRORS_C, gold.ERROR_BITS_BYTE6)))


def read_frames(name):
    path = FIXTURES / name
    return [(m.arbitration_id, bytes(m.data)) for m in can.ASCReader(path)]


def script_bg_decode(arb, data):
    """The old pc_files/bestgo_decode.py main-loop dispatch, replicated."""
    if arb == gold.BG_LIMITS and len(data) >= 8:
        return gold.script_parse_limits(data)
    if arb == gold.BG_SOC and len(data) >= 6:
        return gold.script_parse_soc(data)
    if arb == gold.BG_MEAS and len(data) >= 6:
        return gold.script_parse_meas(data)
    if arb == gold.BG_ALARMS and len(data) >= 8:
        return gold.script_parse_alarms(data)
    if arb == gold.BG_INFO and len(data) >= 6:
        return gold.script_parse_info(data)
    if arb == gold.BG_MFR:
        return {"mfr": gold._ascii(data)}
    if arb == gold.BG_CELLEXT and len(data) >= 8:
        return gold.script_parse_cellext(data)
    if arb == gold.BG_CAPACITY and len(data) >= 2:
        return {"cap_inst": gold._u16(data, 0)}
    return None


def assert_mapped(new, old, mapping, ctx):
    for old_key, new_key, nd in mapping:
        if old_key not in old:
            continue
        assert new_key in new, f"{ctx}: {new_key} missing (script has {old_key})"
        want = old[old_key] if nd is None else round(old[old_key], nd)
        got = new[new_key]
        assert got == want, f"{ctx}: {new_key}={got!r}, script lineage gives {want!r}"


# ---------------------------------------------------------------------------
def test_bestgo_fixture_against_addon_lineage():
    dec = BestgoDecoder()
    gold.addon_bg_reset()
    frames = decoded = 0
    for arb, data in read_frames("bestgo-capture.asc"):
        frames += 1
        new = dec.decode(arb, data)
        old = gold.addon_bg_decode(arb, data)
        assert (new is None) == (old is None), f"0x{arb:X}: claim mismatch"
        if new is None:
            continue
        decoded += 1
        for k, v in old.items():
            assert new[k] == v, f"0x{arb:X} {k}: new={new[k]!r} old={v!r}"
        extras = set(new) - set(old)
        assert extras <= {"soc_hi", "chemistry", "life"}, \
            f"0x{arb:X}: unexpected new fields {extras}"
    assert decoded >= 40, f"fixture exercised only {decoded} frames of {frames}"


def test_bestgo_fixture_against_script_lineage():
    dec = BestgoDecoder()
    checked = 0
    for arb, data in read_frames("bestgo-capture.asc"):
        new = dec.decode(arb, data)
        old = script_bg_decode(arb, data)
        if new is None or old is None:
            continue
        assert_mapped(new, old, BG_SCRIPT_MAP, f"0x{arb:X}")
        checked += 1
    assert checked >= 40, f"only {checked} frames compared"


def test_ezkontrol_fixture_against_addon_lineage():
    dec = EzkontrolDecoder()
    decoded = 0
    for arb, data in read_frames("ezkontrol-capture.asc"):
        new = dec.decode(arb, data)
        if arb == ezkontrol.MSG1_ID:
            old = gold.addon_ez_decode_msg1(data)
        elif arb == ezkontrol.MSG2_ID:
            old = gold.addon_ez_decode_msg2(data)
        else:
            assert new is None, f"0x{arb:X}: claimed a foreign frame"
            continue
        decoded += 1
        for k, v in old.items():
            if k == "op_mode":   # intentional: raw int -> name
                assert new[k] == OP_MODE_NAMES.get(v, f"?({v})"), \
                    f"op_mode: new={new[k]!r} old raw={v}"
            else:
                assert new[k] == v, f"0x{arb:X} {k}: new={new[k]!r} old={v!r}"
        extras = set(new) - set(old)
        assert extras <= {"life"}, f"0x{arb:X}: unexpected new fields {extras}"
    assert decoded >= 100, f"fixture exercised only {decoded} EZ frames"


def test_ezkontrol_fixture_against_script_lineage():
    dec = EzkontrolDecoder()
    checked = 0
    for arb, data in read_frames("ezkontrol-capture.asc"):
        new = dec.decode(arb, data)
        if arb == ezkontrol.MSG1_ID:
            old = gold.script_parse_msg_i(data)
        elif arb == ezkontrol.MSG2_ID:
            old = gold.script_parse_msg_ii(data)
        else:
            continue
        assert_mapped(new, old, EZ_SCRIPT_MAP, f"0x{arb:X}")
        if "mode" in old:   # intentional: 0 "Normal" -> "Stop", 1 "?(1)" -> "Drive"
            assert new["op_mode"] == SCRIPT_MODE_RENAMED.get(old["mode"], old["mode"])
        if "gear" in old:
            want = "None" if old["gear"] == "NO" else old["gear"]
            assert new["gear"] == want
            assert new["brake"] == old["brake"].capitalize()
            assert new["dc_contactor"] == old["contactor"].capitalize()
            if old["errors"] == "OK":
                assert new["errors"] == "None"
            else:
                shorts = old["errors"].split(",")
                assert new["errors"] == ", ".join(ERR_SHORT2FULL[s] for s in shorts)
        checked += 1
    assert checked >= 100, f"only {checked} frames compared"


def test_battery_name_assembly():
    dec = BestgoDecoder()
    f0 = dec.decode(bestgo.ID_NAME0, b"Lithium\x00")
    assert f0["battery_name"] == "Lithium"
    f1 = dec.decode(bestgo.ID_NAME1, b"Valley\x00\x00")
    assert f1["battery_name"] == "Lithium Valley"


def test_vendored_addon_package_in_sync():
    """The HA add-on carries a vendored copy of solarcar_can; fail if stale."""
    import sync_addon
    src_files = sorted(sync_addon.SRC.glob("*.py"))
    assert src_files, "package sources missing"
    for f in src_files:
        dst = sync_addon.DST / f.name
        assert dst.exists(), f"{dst} missing -- run sync_addon.py"
        assert dst.read_text(encoding="utf-8") == sync_addon.vendored(f), \
            f"{dst.name} is stale -- run sync_addon.py"


def test_op_mode_names_full_enum():
    """Byte 3 bits 6-4: 0 Stop, 1 Drive, 2 Cruise, 3 EBS, 4 Hold (spec p.3-4)."""
    dec = EzkontrolDecoder()
    want = {0: "Stop", 1: "Drive", 2: "Cruise", 3: "EBS", 4: "Hold",
            5: "?(5)", 6: "?(6)", 7: "?(7)"}
    for mode, name in want.items():
        data = bytes([60, 60, 0, (mode << 4) | 0x84, 0, 0, 0, 0])  # D2, contactor on
        f = dec.decode(ezkontrol.MSG2_ID, data)
        assert f["op_mode"] == name, f"mode {mode}: {f['op_mode']!r}"
        assert f["gear"] == "D2" and f["dc_contactor"] == "On" and f["brake"] == "Off"


def test_op_mode_seen_in_fixture():
    """The bench capture has both mode 0 and mode 1 frames; neither may decode
    to an unknown '?(n)' placeholder."""
    modes = {EzkontrolDecoder().decode(arb, data)["op_mode"]
             for arb, data in read_frames("ezkontrol-capture.asc")
             if arb == ezkontrol.MSG2_ID}
    assert {"Stop", "Drive"} <= modes, modes
    assert not any(m.startswith("?") for m in modes), modes


def test_bestgo_capacity_fields_track_soc_in_fixture():
    """Documents real BMS behaviour (not a decoder bug): 0x35F bytes 4-5 and
    0x379 bytes 0-1 carry remaining Ah, which on this 100 Ah pack equals SOC %.
    Each comes from its own frame -- the decoder must not copy SOC into them."""
    dec = BestgoDecoder()
    soc = nom = inst = None
    for arb, data in read_frames("bestgo-capture.asc"):
        f = dec.decode(arb, data) or {}
        if arb == bestgo.ID_SOC:
            soc = f["soc"]
            assert "nominal_capacity" not in f and "installed_capacity" not in f
        elif arb == bestgo.ID_INFO:
            nom = f["nominal_capacity"]
            assert set(f) == {"chemistry", "firmware", "nominal_capacity"}
        elif arb == bestgo.ID_CAPACITY:
            inst = f["installed_capacity"]
            assert set(f) == {"installed_capacity"}
    assert soc == nom == inst == 56, (soc, nom, inst)
    # and they really are independent bytes: a 0x35F with a different value
    f = dec.decode(bestgo.ID_INFO, bytes([0, 0, 1, 1, 100, 0, 0, 0]))
    assert f["nominal_capacity"] == 100
    f = dec.decode(bestgo.ID_CAPACITY, bytes([0x2C, 0x01, 0, 0, 0, 0, 0, 0]))
    assert f["installed_capacity"] == 300


def test_foreign_and_short_frames():
    bg, ez = BestgoDecoder(), EzkontrolDecoder()
    assert bg.decode(0x180117EF, bytes(8)) is None
    assert ez.decode(0x351, bytes(8)) is None
    assert bg.decode(0x123, bytes(8)) is None
    assert ez.decode(0x123, bytes(8)) is None
    # recognised IDs with short payloads are claimed (dict), not dropped (None)
    assert bg.decode(bestgo.ID_LIMITS, b"\x01") == {}
    assert ez.decode(ezkontrol.MSG1_ID, b"\x01") == {}


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS  {name}")
            except AssertionError as e:
                failures += 1
                print(f"FAIL  {name}: {e}")
    sys.exit(1 if failures else 0)
