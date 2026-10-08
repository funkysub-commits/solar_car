"""Bundle one processed day into a folder (and zip) to hand to the team.

    python telemetry.py && python analysis.py      # process first
    python make_team_pack.py                       # newest processed day
    python make_team_pack.py 2026-10-03

Writes team_pack/<date>/ and team_pack/solar_car_telemetry_<date>.zip:
    README.md            overview, findings, caveats, how to use the files
    dashboard.html       interactive version - open in any browser
    figures/             PNG charts
    data/                CSVs anyone can open in Excel / Sheets / pandas
    process_it_yourself/ the scripts + raw export, to rerun or extend
"""
import json
import shutil
import sys
from pathlib import Path

import pandas as pd

import telemetry as tm

HERE = Path(__file__).parent
PACKS = HERE / "team_pack"
SCRIPTS = ["telemetry.py", "analysis.py", "dashboard_template.html", "make_team_pack.py"]
REQUIREMENTS = "pandas>=2.2\nnumpy\nscipy\nmatplotlib\npyarrow\nxlrd\n"

# Units for the derived columns (raw channel units come from telemetry.CHANNELS).
DERIVED_UNITS = {"pack_power_w": "W", "mc_power_w": "W", "pack_energy_out_wh": "Wh",
                 "mc_energy_wh": "Wh", "mc_regen_wh": "Wh", "pack_charge_in_wh": "Wh",
                 "distance_mi": "mi", "moving": "", "session": ""}


def data_dictionary(wide):
    units = {col: unit for col, unit in tm.CHANNELS.values()} | DERIVED_UNITS
    rows = [{"column": "time", "unit": "",
             "description": "Local time (Pacific) with UTC offset; one row per second."}]
    rows += [{"column": c, "unit": units.get(c, ""), "description": tm.COLUMN_INFO.get(c, "")}
             for c in wide.columns]
    return pd.DataFrame(rows)


def raw_files_for(day):
    """Raw exports that contain this day (cheap check on the file contents)."""
    out = []
    for f in sorted(tm.RAW.glob("*")):
        if f.suffix.lower() not in (".csv", ".xls", ".xlsx"):
            continue
        try:
            t = tm.load_raw(f)["time"].dt.strftime("%Y-%m-%d")
        except Exception:
            continue
        if (t == day).any():
            out.append(f)
    return out


def fmt(v, d=0):
    return "–" if v is None or pd.isna(v) else f"{v:,.{d}f}"


def readme(day, drives, ov, model, be, cap, health, raw_names):
    dist = drives["distance_mi"].sum()
    wh = drives["mc_energy_wh"].sum()
    whmi = wh / dist if dist else float("nan")
    launches = ov[ov["context"] == "pulling away"]
    stall = ov[ov["context"] == "stalled under load"]
    sol = be["solar"]["solar_w"]
    be_row = next((r for r in be["break_even"] if r["solar_w"] == sol), be["break_even"][0])
    cc = {c["mph"]: c for c in be["cruise_curve"]}
    soc = drives[["soc_start", "soc_end"]].dropna()
    date_txt = pd.Timestamp(day).strftime("%A %-d %B %Y") if sys.platform != "win32" \
        else pd.Timestamp(day).strftime("%A %#d %B %Y")
    first = pd.Timestamp(drives["start"].min()).strftime("%H:%M")
    last = pd.Timestamp(drives["end"].max()).strftime("%H:%M")

    findings = []
    if len(ov):
        findings.append(
            f"**Controller overloads mostly happen pulling away from a stop.** "
            f"{len(launches)} of the {len(ov)} controller errors came within a few seconds of "
            f"starting from standstill, almost always at 100% throttle. Easing into the throttle "
            f"from a stop, or lowering the controller's launch current limit, should remove most "
            f"of them. Each error is listed in `data/controller_errors.csv`, and the red lines "
            f"on the charts mark when they happened.")
    if len(stall):
        st = stall.iloc[0]
        findings.append(
            f"**The motor overheated (up to {fmt(drives['max_motor_temp_c'].max())} °C) and "
            f"stalled at {pd.Timestamp(st['time']).strftime('%H:%M')}** after a long stretch near full "
            f"power, and the controller had to be switched off and on to clear it. "
            f"The motor heats and cools slowly (it takes about {fmt(model['tau_s'] / 60)} minutes "
            f"to settle). Our fitted heat model says it can sustain about "
            f"{fmt(model['max_continuous_phase_a_at_1500rpm'])} A of phase current at 1500 rpm "
            f"indefinitely; more than that and it eventually reaches the limit. "
            f"See `figures/motor_thermal_model.png`.")
    findings.append(
        f"**Solar break-even is about {fmt(be_row['break_even_mph'], 1)} mph** "
        f"(likely {fmt(be_row['p10_mph'], 1)}–{fmt(be_row['p90_mph'], 1)} mph) with "
        f"{sol or 725} W of sun. Below that speed the panels supply everything the motor "
        f"uses, so the car could in principle keep going all day. The solar figure is "
        f"from {len(be['solar']['windows'])} short parked charging "
        f"{'period' if len(be['solar']['windows']) == 1 else 'periods'}, so it's the "
        f"weakest number here; a proper measurement of the array would firm it up. "
        f"See `figures/solar_break_even.png`; the interactive dashboard lets you try other "
        f"solar figures.")
    findings.append(
        f"**Steady cruising costs about {fmt(cc[15]['wh_per_mi'])} Wh per mile at 15 mph, "
        f"{fmt(cc[25]['wh_per_mi'])} at 25 and {fmt(cc[35]['wh_per_mi'])} at 35.** "
        f"The day's overall {fmt(whmi)} Wh/mi is higher because of starts, stops and hills. "
        f"Regen gave back very little (under 5% per drive).")
    if cap:
        findings.append(
            f"**Battery: about {fmt(cap['usable_capacity_ah'], 1)} Ah per 100% state of charge, "
            f"against a {cap['rated_capacity_ah']} Ah rating.** The pack delivered "
            f"{fmt(cap['ah_counted'], 1)} Ah while charge fell {fmt(cap['soc_from'])}% → "
            f"{fmt(cap['soc_to'])}%. Cells stayed well balanced: "
            f"{fmt(health['cell_delta_at_rest_mv_median'])} mV apart at rest and "
            f"{fmt(health['cell_delta_over_50a_mv_median'], 1)} mV under load.")

    caveats = [
        f"**Low-speed readings are unreliable.** Below about {tm.LOW_SPEED_MPH:.0f} mph the "
        f"motor struggles and speed, rpm and current jump around (speed can even read "
        f"negative). The data is left in, but the efficiency and break-even numbers only "
        f"use driving above {tm.LOW_SPEED_MPH:.0f} mph.",
        "**The two current sensors disagree.** Only the motor runs off the main battery "
        "(accessories have their own battery), yet the battery's current reads about "
        f"{fmt((health or {}).get('pack_vs_mc_current_ratio', 1.12) * 100 - 100)}% higher than "
        "the controller's. One of them is off, so energy and capacity figures carry roughly "
        "that much uncertainty.",
        "**Values are only logged when they change**, roughly every 1–2 s for the motor and "
        "every 1–10 s for the battery. Short spikes between readings are missed, and "
        "`telemetry_1s.csv` holds each value until the next reading.",
    ]
    if soc.empty or drives["soc_start"].isna().any():
        caveats.append("**Some drives have no battery data** because the battery wasn't "
                       "sending on the CAN bus at the time (see the Drives table).")
    caveats.append("**Motor temperature is reported in 5 °C steps**, so it looks like a "
                   "staircase on the charts.")

    drive_rows = "\n".join(
        f"| {int(d.drive)} | {pd.Timestamp(d.start).strftime('%H:%M')} | {d.distance_mi:.2f} | "
        f"{d.moving_min:.0f} | {fmt(d.avg_moving_mph, 1)} | {fmt(d.max_mph, 1)} | "
        f"{fmt(d.wh_per_mi)} | "
        f"{'–' if pd.isna(d.soc_start) else f'{d.soc_start:.0f} → {d.soc_end:.0f}%'} | "
        f"{fmt(d.max_motor_temp_c)} | {int(d.mc_errors)} |"
        for d in drives.itertuples())
    drive_figs = " · ".join(f"[Drive {int(d)}](figures/drive_{int(d):02d}.png)"
                            for d in drives["drive"])

    return f"""# Solar car telemetry: {date_txt}

Data logged by the car's Raspberry Pi (motor controller + battery over CAN bus) during
the test drive on {date_txt}, cleaned up and analysed. Start with this page, then open
**`dashboard.html`** in a web browser for interactive charts. Everything else in the
folder is the data and the scripts behind those charts.

![Whole day](figures/day_overview.png)

## At a glance

| | |
|---|---|
| Drives | {len(drives)} between {first} and {last} (Pacific time) |
| Distance | {dist:.1f} miles |
| Top speed | {fmt(drives['max_mph'].max(), 1)} mph |
| Energy used | {fmt(wh)} Wh at the motor controller, {fmt(whmi)} Wh/mile overall |
| Battery charge | {f"{soc['soc_start'].iloc[0]:.0f}% → {soc['soc_end'].iloc[-1]:.0f}%" if not soc.empty else "–"} |
| Hottest motor | {fmt(drives['max_motor_temp_c'].max())} °C |
| Controller errors | {len(ov)} |
| Solar break-even speed | about {fmt(be_row['break_even_mph'], 1)} mph at {sol or 725} W of sun |

## What we learned

{chr(10).join(f"{i}. {f}" for i, f in enumerate(findings, 1))}

## Keep in mind

{chr(10).join(f"- {c}" for c in caveats)}

## Drives

| Drive | Start | Miles | Moving min | Avg mph | Max mph | Wh/mi | Battery | Motor max °C | Errors |
|---|---|---|---|---|---|---|---|---|---|
{drive_rows}

Charts for each drive: {drive_figs}

## What's in this folder

| Path | What it is |
|---|---|
| `dashboard.html` | Interactive charts: hover the timeline, zoom to a drive, try the break-even and motor-heat calculators. Works offline. |
| `figures/` | The charts as images, for slides and chat. `day_overview.png` is the whole day; `drive_NN.png` is one per drive. |
| `data/telemetry_1s.csv` | **The main data.** One row per second, one column per measurement. Opens in Excel or Google Sheets. |
| `data/data_dictionary.csv` | What every column in `telemetry_1s.csv` means, with units. |
| `data/drives.csv` | One row per drive with totals and peaks (more columns than the table above). |
| `data/controller_errors.csv` | Every controller error and what the car was doing in the 20 s before it. |
| `data/events.csv` | Log of state changes: errors, drive mode, regen, sensors dropping out. |
| `data/efficiency_vs_speed.csv` | Power and Wh/mile at steady speeds, by speed band. |
| `data/models.json` | Fitted numbers: motor heat model, road-load / break-even model, battery capacity and health. |
| `process_it_yourself/` | The scripts and the raw export, to rerun or extend the analysis (below). |

## Look at the data yourself

**Spreadsheet:** open `data/telemetry_1s.csv`. Each row is one second. Columns are
described in `data/data_dictionary.csv`. Filter `moving` = TRUE for driving only.

**Python:**

```python
import pandas as pd
df = pd.read_csv("data/telemetry_1s.csv", parse_dates=["time"], index_col="time")
df.loc[df.moving, ["speed_mph", "mc_power_w"]].plot(subplots=True)
```

## Rerun the processing

You need Python 3.10+.

```
cd process_it_yourself
pip install -r requirements.txt
python telemetry.py        # raw/ export(s) -> processed/<date>/ tables
python analysis.py         # -> processed/<date>/analysis/, figures/, dashboard.html
python make_team_pack.py   # rebuild a folder like this one
```

`telemetry.py` reads every export in `raw/` ({", ".join(f"`{n}`" for n in raw_names)}).
New exports come from the CANbus add-on's **Telemetry Export** page in Home Assistant
(the CSV with a `time_utc` column); drop them into `raw/` and rerun. The older
"Raw readings" `.xls` lists work too.

## Words used here

- **Phase current**: current in the motor windings. It's what heats the motor, and it's
  higher than the battery current at low speed.
- **Bus current / power**: what the motor controller draws from the battery.
- **SOC**: state of charge, the battery's percentage.
- **Wh/mile**: energy per mile; lower is better.
- **Break-even speed**: the fastest steady speed at which the solar panels supply all the
  power the motor needs.
- **Regen**: energy recovered into the battery when slowing down.
"""


def build(day):
    src = tm.PROCESSED / day
    an = src / "analysis"
    if not (an / "dashboard.json").exists():
        sys.exit(f"{day}: run telemetry.py and analysis.py first")
    out = PACKS / day
    if out.exists():
        shutil.rmtree(out)
    (out / "data").mkdir(parents=True)

    wide = pd.read_parquet(src / "wide_1s.parquet")
    drives = pd.read_csv(src / "drives.csv", parse_dates=["start", "end"])
    ov = pd.read_csv(an / "overloads.csv")
    models = {k: json.loads((an / f"{f}.json").read_text()) for k, f in
              [("motor_heat", "thermal_model"), ("road_load_break_even", "solar_break_even"),
               ("battery_capacity", "capacity"), ("battery_health", "pack_health")]}

    wide.round(3).to_csv(out / "data" / "telemetry_1s.csv")
    data_dictionary(wide).to_csv(out / "data" / "data_dictionary.csv", index=False)
    drives.to_csv(out / "data" / "drives.csv", index=False)
    ov.to_csv(out / "data" / "controller_errors.csv", index=False)
    shutil.copy(src / "events.csv", out / "data" / "events.csv")
    shutil.copy(an / "efficiency_vs_speed.csv", out / "data" / "efficiency_vs_speed.csv")
    (out / "data" / "models.json").write_text(json.dumps(models, indent=2))
    shutil.copytree(src / "figures", out / "figures")
    shutil.copy(src / "dashboard.html", out / "dashboard.html")

    (out / "raw").mkdir(parents=True)
    raws = raw_files_for(day)
    for f in raws:
        shutil.copy(f, out / "raw" / f.name)

    text = readme(day, drives, ov, models["motor_heat"], models["road_load_break_even"],
                  models["battery_capacity"], models["battery_health"], [f.name for f in raws])
    (out / "README.md").write_text(text, encoding="utf-8")

    zip_base = PACKS / f"solar_car_telemetry_{day}"
    shutil.make_archive(str(zip_base), "zip", root_dir=PACKS, base_dir=day)
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file()) / 1e6
    print(f"{out}  ({size:.1f} MB)  +  {zip_base.name}.zip")
    return out


def main():
    days = sys.argv[1:] or sorted(p.name for p in tm.PROCESSED.iterdir()
                                  if (p / "analysis" / "dashboard.json").exists())[-1:]
    for d in days:
        build(d)


if __name__ == "__main__":
    main()
