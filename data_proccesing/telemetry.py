"""Convert a raw Home Assistant history export into analysis-ready tables.

Input : the "Raw readings" .xls written by the CANbus add-on's history export
        (one row per state change: Time (Pacific) | Name | Value | Unit | Status).
Output: processed/<date>/
          readings_long.parquet  every reading, cleaned (tidy / long format)
          wide_1s.parquet/.csv   one row per second, one column per channel,
                                 sample-and-hold, plus derived power/energy/distance
          events.csv             text/state channels: errors, op mode, contactor...
          sessions.csv           one row per logging session with headline stats
          drives.csv             one row per drive (moving, stops < 2 min merged)
          channels.csv           data dictionary: raw name -> column, unit, counts
        processed/all_drives.csv every day's drives stacked, for cross-day trends

Needs: pip install pandas xlrd pyarrow
Usage:  python telemetry.py              (every *.xls in this folder)
        python telemetry.py solarcar_raw_readings_2026-10-03.xls
Load later with:  pd.read_parquet("processed/2026-10-03/wide_1s.parquet")
"""
import argparse
import re
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
PROCESSED = HERE / "processed"
TZ = "America/Los_Angeles"
SESSION_GAP = pd.Timedelta(minutes=5)   # no data for this long -> new session
MOVING_MPH = 1.0

# Raw HA name -> (column, unit). Units are normalised here ("mphh" is HA's
# Riemann-sum unit for mph integrated over hours, i.e. miles; the export also
# mangles the degree sign).
CHANNELS = {
    "Adjusted Car Speed":                   ("speed_mph", "mph"),
    "Car Speed":                            ("speed_raw_mph", "mph"),
    "Solar Car Speed":                      ("speed_solar_mph", "mph"),
    "Odometer":                             ("odometer_mi", "mi"),
    "EZkontrol Bus Voltage":                ("mc_bus_voltage_v", "V"),
    "EZkontrol Bus Current":                ("mc_bus_current_a", "A"),
    "EZkontrol Phase Current":              ("mc_phase_current_a", "A"),
    "EZkontrol Motor Speed":                ("motor_rpm", "rpm"),
    "EZkontrol Throttle":                   ("throttle_pct", "%"),
    "EZkontrol Controller Temp":            ("mc_temp_c", "°C"),
    "EZkontrol Motor Temp":                 ("motor_temp_c", "°C"),
    "EZkontrol Error Count":                ("mc_error_count", ""),
    "EZkontrol Status":                     ("mc_online", ""),
    "BESTGO Pack Voltage":                  ("pack_voltage_v", "V"),
    "BESTGO Pack Current":                  ("pack_current_a", "A"),
    "BESTGO Soc":                           ("soc_pct", "%"),
    "BESTGO Soh":                           ("soh_pct", "%"),
    "BESTGO Cell Voltage Min":              ("cell_v_min_mv", "mV"),
    "BESTGO Cell Voltage Max":              ("cell_v_max_mv", "mV"),
    "BESTGO Cell Voltage Delta":            ("cell_v_delta_mv", "mV"),
    "BESTGO Cell Temp Min":                 ("cell_temp_min_c", "°C"),
    "BESTGO Cell Temp Max":                 ("cell_temp_max_c", "°C"),
    "BESTGO Pack Temp":                     ("pack_temp_c", "°C"),
    "BESTGO Nominal Capacity":              ("nominal_capacity_ah", "Ah"),
    "BESTGO Installed Capacity":            ("installed_capacity_ah", "Ah"),
    "BESTGO Charge Voltage Limit":          ("charge_v_limit_v", "V"),
    "BESTGO Charge Current Limit":          ("charge_i_limit_a", "A"),
    "BESTGO Discharge Voltage Limit":       ("discharge_v_limit_v", "V"),
    "BESTGO Discharge Current Limit":       ("discharge_i_limit_a", "A"),
    "BESTGO Status":                        ("bms_online", ""),
    "CAN Adapter Status":                   ("can_adapter_up", ""),
    "System Monitor Processor temperature": ("pi_cpu_temp_f", "°F"),
}

# Columns in wide_1s, in order. Speed duplicates, static BMS limits and the
# capacity channels stay in the long table. (The BMS reports *remaining* Ah in
# both capacity fields; on this 100 Ah pack that equals SOC %.)
WIDE_COLUMNS = [
    "speed_mph", "odometer_mi", "throttle_pct", "motor_rpm",
    "mc_bus_voltage_v", "mc_bus_current_a", "mc_phase_current_a",
    "pack_voltage_v", "pack_current_a", "soc_pct",
    "cell_v_min_mv", "cell_v_max_mv", "cell_v_delta_mv",
    "mc_temp_c", "motor_temp_c", "cell_temp_min_c", "cell_temp_max_c",
    "pack_temp_c", "pi_cpu_temp_f",
    "mc_online", "bms_online", "can_adapter_up", "mc_error_count",
]


def snake(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def load_raw(path):
    """Read the export into a tidy long table (one row per reading)."""
    raw = pd.read_excel(path, sheet_name=0)
    raw.columns = ["time", "name", "value", "unit", "status"]
    df = pd.DataFrame({
        "time": pd.to_datetime(raw["time"]).dt.tz_localize(TZ, ambiguous="infer"),
        "name": raw["name"],
    })
    df["channel"] = df["name"].map(lambda n: CHANNELS.get(n, (snake(n), None))[0])
    df["unit"] = [CHANNELS.get(n, (None, u))[1] for n, u in zip(raw["name"], raw["unit"])]
    df["unit"] = df["unit"].fillna("")
    df["value"] = pd.to_numeric(raw["value"], errors="coerce")
    is_text = df["value"].isna() & raw["value"].notna()
    df["text"] = raw["value"].where(is_text).astype("string")
    # HA writes "unavailable"/"unknown" when the source drops out. A blank
    # value with no status is a text sensor cleared to "" (e.g. errors gone).
    df["status"] = raw["status"].fillna("ok").astype("string")
    # Add-on < 0.12.1 mis-decoded op mode 0/1; map old history to the fixed names.
    om = df["channel"] == "ezkontrol_op_mode"
    df.loc[om, "text"] = df.loc[om, "text"].replace({"Normal": "Stop", "?(1)": "Drive"})
    return df.sort_values("time", kind="stable").reset_index(drop=True)


def to_wide(long, freq="1s"):
    """One row per `freq`, one column per numeric channel, sample-and-hold.

    HA only logs on change, so the last value holds until the next reading.
    An 'unavailable' reading holds NaN until the channel comes back.
    """
    num = long[long["channel"].isin(WIDE_COLUMNS) & (long["text"].isna())]
    marker = -1e300  # stands in for "unavailable" so ffill doesn't paper over it
    vals = num["value"].where(num["status"] == "ok", marker)
    piv = (pd.DataFrame({"time": num["time"], "channel": num["channel"], "v": vals})
           .pivot_table(index="time", columns="channel", values="v", aggfunc="last"))
    start, end = piv.index.min().floor(freq), piv.index.max().ceil(freq)
    wide = (piv.resample(freq).last()
               .reindex(pd.date_range(start, end, freq=freq, name="time"))
               .ffill()
               .replace(marker, np.nan))
    wide = wide.reindex(columns=[c for c in WIDE_COLUMNS if c in wide.columns])
    return add_derived(wide, long)


def add_derived(wide, long):
    dt_h = pd.Timedelta(wide.index.freq).total_seconds() / 3600
    w = wide
    # BESTGO pack current is negative when discharging; flip so + = power out.
    w["pack_power_w"] = -(w["pack_voltage_v"] * w["pack_current_a"])
    w["mc_power_w"] = w["mc_bus_voltage_v"] * w["mc_bus_current_a"]
    w["pack_energy_out_wh"] = (w["pack_power_w"].fillna(0) * dt_h).cumsum()
    w["mc_energy_wh"] = (w["mc_power_w"].fillna(0) * dt_h).cumsum()
    # Regen: energy flowing back through the controller / into the pack.
    w["mc_regen_wh"] = (-w["mc_power_w"].clip(upper=0).fillna(0) * dt_h).cumsum()
    w["pack_charge_in_wh"] = (-w["pack_power_w"].clip(upper=0).fillna(0) * dt_h).cumsum()
    w["distance_mi"] = (w["speed_mph"].clip(lower=0).fillna(0) * dt_h).cumsum()
    w["moving"] = w["speed_mph"].abs() > MOVING_MPH
    # Sessions: split wherever the logger went quiet for SESSION_GAP.
    t = long["time"].drop_duplicates()
    starts = t[t.diff() > SESSION_GAP]
    w["session"] = np.searchsorted(starts.values, w.index.values, side="right") + 1
    return w


def build_events(long):
    """State/text channels and availability changes, as a readable log."""
    state = {"ezkontrol_errors", "ezkontrol_op_mode", "ezkontrol_dc_contactor",
             "ezkontrol_gear", "ezkontrol_brake", "bestgo_charging",
             "bestgo_alarms", "bestgo_warnings", "can_adapter_up", "mc_online",
             "bms_online", "mc_error_count"}
    ev = long[long["channel"].isin(state) | (long["status"] != "ok")].copy()
    ev["state"] = ev["text"].fillna(ev["value"].map(lambda v: "" if pd.isna(v) else f"{v:g}"))
    ev.loc[ev["status"] != "ok", "state"] = ev["status"]
    ev = ev[["time", "channel", "state"]]
    # keep only actual changes per channel
    ev = ev[ev.groupby("channel")["state"].transform(lambda s: s.ne(s.shift()))]
    ev["note"] = ""
    contactor = ev["channel"] == "ezkontrol_dc_contactor"
    # The driver keys the controller off to clear a latched error - not a cutout.
    ev.loc[contactor & (ev["state"] == "Off"), "note"] = "controller switched off (power cycle to clear errors)"
    ev.loc[contactor & (ev["state"] == "On"), "note"] = "controller back on"
    ev.loc[(ev["channel"] == "bestgo_charging") & (ev["state"] == "on"), "note"] = "regen"
    return ev.reset_index(drop=True)


def _stats(s):
    mv = s[s["moving"]]
    dist = s["distance_mi"].iloc[-1] - s["distance_mi"].iloc[0]
    mc_wh = s["mc_energy_wh"].iloc[-1] - s["mc_energy_wh"].iloc[0]
    regen_wh = s["mc_regen_wh"].iloc[-1] - s["mc_regen_wh"].iloc[0]
    pack_wh = s["pack_energy_out_wh"].iloc[-1] - s["pack_energy_out_wh"].iloc[0]
    soc = s["soc_pct"].dropna()
    return {
        "start": s.index[0], "end": s.index[-1],
        "duration_min": round(len(s) / 60, 1),
        "moving_min": round(len(mv) / 60, 1),
        "distance_mi": round(dist, 2),
        "avg_moving_mph": round(mv["speed_mph"].mean(), 1) if len(mv) else np.nan,
        "max_mph": s["speed_mph"].max(),
        # Motor-controller energy covers every drive; the BMS was not always
        # reporting, so pack energy can undercount.
        "mc_energy_wh": round(mc_wh),
        "wh_per_mi": round(mc_wh / dist, 1) if dist > 0.1 else np.nan,
        "regen_wh": round(regen_wh),
        "regen_pct": round(100 * regen_wh / (mc_wh + regen_wh), 1) if mc_wh > 0 else np.nan,
        "pack_energy_out_wh": round(pack_wh),
        "bms_coverage_pct": round(100 * s["pack_current_a"].notna().mean()),
        "soc_start": soc.iloc[0] if len(soc) else np.nan,
        "soc_end": soc.iloc[-1] if len(soc) else np.nan,
        "peak_mc_power_w": s["mc_power_w"].max().round(),
        "max_motor_temp_c": s["motor_temp_c"].max(),
        "max_mc_temp_c": s["mc_temp_c"].max(),
        "max_cell_delta_mv": s["cell_v_delta_mv"].max(),
        "min_cell_mv": s["cell_v_min_mv"].min(),
        "mc_errors": int((s["mc_error_count"].diff() > 0).sum()),
    }


def summarise_sessions(wide):
    """One row per logging session (split on gaps in the logger)."""
    return pd.DataFrame([{"session": k, **_stats(s)} for k, s in wide.groupby("session")])


def summarise_drives(wide, max_stop=pd.Timedelta(minutes=2)):
    """One row per drive: moving stretches, merged across stops < max_stop."""
    mv = wide["moving"]
    edges = mv.ne(mv.shift()).cumsum()
    runs = [(g.index[0], g.index[-1]) for _, g in wide[mv].groupby(edges[mv])]
    merged = []
    for a, b in runs:
        if merged and a - merged[-1][1] < max_stop:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    df = pd.DataFrame([_stats(wide.loc[a:b]) for a, b in merged])
    if len(df):
        df = df[df["distance_mi"] >= 0.1].reset_index(drop=True)
    df.insert(0, "drive", range(1, len(df) + 1))
    return df


def channel_table(long):
    g = long.groupby(["name", "channel", "unit"], dropna=False)
    t = g.agg(readings=("time", "size"),
              unavailable=("status", lambda s: (s != "ok").sum()),
              min=("value", "min"), max=("value", "max"), mean=("value", "mean"),
              first=("time", "min"), last=("time", "max")).reset_index()
    t["in_wide"] = t["channel"].isin(WIDE_COLUMNS)
    return t.sort_values("readings", ascending=False)


def process(xls, freq="1s", out=None, quiet=False):
    """Convert one export; returns the output directory."""
    long = load_raw(xls)
    day = long["time"].iloc[0].strftime("%Y-%m-%d")
    out = out or PROCESSED / day
    out.mkdir(parents=True, exist_ok=True)

    wide = to_wide(long, freq)
    events = build_events(long)
    sessions = summarise_sessions(wide)
    drives = summarise_drives(wide)

    long.to_parquet(out / "readings_long.parquet", index=False)
    wide.to_parquet(out / "wide_1s.parquet")
    wide.round(3).to_csv(out / "wide_1s.csv")
    events.to_csv(out / "events.csv", index=False)
    sessions.to_csv(out / "sessions.csv", index=False)
    drives.to_csv(out / "drives.csv", index=False)
    channel_table(long).to_csv(out / "channels.csv", index=False, float_format="%.4g")

    if not quiet:
        print(f"{xls.name}: {len(long)} readings, {long['channel'].nunique()} channels -> {out}")
        print(f"wide: {wide.shape[0]} rows x {wide.shape[1]} cols, {len(events)} events")
        with pd.option_context("display.width", 250, "display.max_columns", 40):
            print(drives.drop(columns=["end"]).to_string(index=False))
    return out


def build_index():
    """Stack every processed day's drives into processed/all_drives.csv."""
    frames = []
    for f in sorted(PROCESSED.glob("*/drives.csv")):
        d = pd.read_csv(f)
        d.insert(0, "date", f.parent.name)
        frames.append(d)
    if frames:
        alld = pd.concat(frames, ignore_index=True)
        alld.to_csv(PROCESSED / "all_drives.csv", index=False)
        print(f"all_drives.csv: {len(alld)} drives over {len(frames)} day(s)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("xls", type=Path, nargs="*",
                    help="export(s) to convert (default: every *.xls next to this script)")
    ap.add_argument("--freq", default="1s", help="wide-table resolution (default 1s)")
    ap.add_argument("--out", type=Path, help="output dir (single file only; default processed/<date>)")
    a = ap.parse_args()

    files = a.xls or sorted(HERE.glob("*.xls"))
    if a.out and len(files) > 1:
        ap.error("--out only works with a single file")
    for f in files:
        process(f, a.freq, a.out)
    build_index()


if __name__ == "__main__":
    main()
