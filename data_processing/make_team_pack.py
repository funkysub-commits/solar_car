"""Bundle one processed day into a folder (and zip) to hand to the team.

    python telemetry.py && python analysis.py      # process first
    python make_team_pack.py                       # newest processed day
    python make_team_pack.py 2026-10-03

Writes team_pack/<date>/ and team_pack/solar_car_telemetry_<date>.zip:
    dashboard.html       start here - interactive overview, opens in any browser
    details/             everything else
        README.md        what we did, what we learned, how to use the files
        figures/         PNG charts
        data/            CSVs anyone can open in Excel / Sheets / pandas
        raw/             the original export(s)
The "what we did" story comes from notes/<date>.md.
"""
import json
import re
import shutil
import sys
from pathlib import Path

import pandas as pd

import telemetry as tm

HERE = Path(__file__).parent
PACKS = HERE / "team_pack"
NOTES = HERE / "notes"

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


def day_story(day):
    """notes/<date>.md, with its headings pushed one level down to sit under ours."""
    f = NOTES / f"{day}.md"
    if not f.exists():
        return ""
    return re.sub(r"^(#+)", r"#\1", f.read_text(encoding="utf-8"), flags=re.M).strip()


def readme(day, drives, ov, de, model, be, cap, health, gap, raw_names):
    dist = drives["distance_mi"].sum()
    wh = drives["mc_energy_wh"].sum()
    whmi = wh / dist if dist else float("nan")
    launches = ov[ov["context"] == "pulling away"]
    stall = ov[ov["context"] == "stalled under load"]
    sol = be["solar"]["solar_w"]
    be_row = next((r for r in be["break_even"] if r["solar_w"] == sol), be["break_even"][0])
    cc = {c["mph"]: c for c in be["cruise_curve"]}
    soc = drives[["soc_start", "soc_end"]].dropna()
    date_txt = pd.Timestamp(day).strftime("%A %#d %B %Y" if sys.platform == "win32"
                                          else "%A %-d %B %Y")
    first = pd.Timestamp(drives["start"].min()).strftime("%H:%M")
    last = pd.Timestamp(drives["end"].max()).strftime("%H:%M")
    brake = de[de["brake_drag_suspected"]]
    normal = de[~de["brake_drag_suspected"]]
    real = normal[normal["distance_mi"] >= 0.5]
    best = real.loc[real["wh_per_mi"].idxmin()] if len(real) else None
    fit = gap.get("fit_battery_a_vs_controller_a") if gap else None

    findings = []
    if len(ov):
        findings.append(
            f"**Controller overloads mostly happen pulling away from a stop.** "
            f"{len(launches)} of the {len(ov)} controller errors came within a few seconds of "
            f"starting from standstill, almost always at 100% throttle. Easing into the throttle "
            f"from a stop, or lowering the controller's launch current limit, should remove most "
            f"of them.")
    if len(brake):
        b = brake.iloc[0]
        findings.append(
            f"**Drive {', '.join(str(x) for x in brake['drive'])} was driven with the brake held "
            f"down, and that's what overheated the motor.** At steady speeds it needed "
            f"{fmt(b['steady_w_per_mph'])} W per mph, against about "
            f"{fmt(normal['steady_w_per_mph'].median())} for the other drives, and it spent "
            f"{fmt(b['full_throttle_pct'])}% of its time at full throttle. It used "
            f"{fmt(b['wh_per_mi'])} Wh/mile, roughly double the others, and the motor climbed to "
            f"{fmt(drives['max_motor_temp_c'].max())} °C"
            + (f" and stalled at {pd.Timestamp(stall.iloc[0]['time']).strftime('%H:%M')}"
               if len(stall) else "") + ". It's left out of the efficiency fits below.")
    elif len(stall):
        findings.append(
            f"**The motor overheated and stalled at "
            f"{pd.Timestamp(stall.iloc[0]['time']).strftime('%H:%M')}** after a long stretch near "
            f"full power.")
    findings.append(
        f"**The motor can run about {fmt(model['max_continuous_phase_a_at_1500rpm'])} A of phase "
        f"current at 1500 rpm indefinitely.** It heats and cools slowly (about "
        f"{fmt(model['tau_s'] / 60)} minutes to settle), so short bursts are fine but sustained "
        f"high current gets it to the stall temperature. See `figures/motor_thermal_model.png`.")
    if best is not None:
        findings.append(
            f"**Driving style matters a lot.** The most efficient drive was drive "
            f"{int(best['drive'])} at {fmt(best['wh_per_mi'])} Wh/mile (average "
            f"{fmt(best['avg_moving_mph'], 1)} mph, full throttle {fmt(best['full_throttle_pct'])}% "
            f"of the time). Speeding the car up took {fmt(de['speeding_up_pct'].min())}–"
            f"{fmt(de['speeding_up_pct'].max())}% of each drive's energy, and regen got back at "
            f"most {fmt(de['regen_of_speeding_up_pct'].max())}% of that. Fewer stops and gentler "
            f"starts are the cheapest wins.")
    findings.append(
        f"**Steady cruising on the flat costs about {fmt(cc[15]['wh_per_mi'])} Wh/mile at "
        f"15 mph, {fmt(cc[25]['wh_per_mi'])} at 25 and {fmt(cc[35]['wh_per_mi'])} at 35.** "
        f"The day's overall {fmt(whmi)} Wh/mile is higher because of starts, stops and the "
        f"brake-held drive.")
    findings.append(
        f"**Solar break-even is about {fmt(be_row['break_even_mph'], 1)} mph** (likely "
        f"{fmt(be_row['p10_mph'], 1)}–{fmt(be_row['p90_mph'], 1)} mph) with {sol or 725} W of sun. "
        f"Below that speed the panels supply everything the motor uses, so the car could keep "
        f"going as long as the sun holds. The solar figure comes from the charging stop just "
        f"before 2 pm, so it's the weakest number here; measuring the array properly would firm "
        f"it up.")
    if fit:
        findings.append(
            f"**The battery supplies about {fmt(gap['extra_power_w_at_pack_v'])} W more than the "
            f"motor controller reports.** Once both were logging fast, the battery read "
            f"{fmt(fit['offset_a'], 1)} A more than the controller at every load while the motor "
            f"ran, and the same when parked; otherwise the two agree within "
            f"{fmt(abs(1 - fit['slope']) * 100, 0)}%. Accessories have their own battery, so "
            f"either something draws power only while driving or the controller's current sensor "
            f"reads low. Cables, fuse and contactor lose a little more (about "
            f"{fmt(gap['wiring_resistance_mohm'], 1)} mΩ). See `figures/battery_vs_controller.png`.")
    if cap:
        findings.append(
            f"**Battery: about {fmt(cap['usable_capacity_ah'], 1)} Ah per 100% state of charge, "
            f"against a {cap['rated_capacity_ah']} Ah rating.** The pack delivered "
            f"{fmt(cap['ah_counted'], 1)} Ah while charge fell {fmt(cap['soc_from'])}% → "
            f"{fmt(cap['soc_to'])}%. Cells stayed well balanced: "
            f"{fmt(health['cell_delta_at_rest_mv_median'])} mV apart at rest and "
            f"{fmt(health['cell_delta_over_50a_mv_median'], 1)} mV under load.")

    caveats = [
        f"**Low-speed readings are rough.** Below about {tm.LOW_SPEED_MPH:.0f} mph the motor "
        f"struggles and speed, rpm and current jump around (the shudder; speed can even read "
        f"negative). The data is left in, but the efficiency and break-even numbers only use "
        f"driving above {tm.LOW_SPEED_MPH:.0f} mph.",
        "**Logging got faster at about 2:45 pm.** Before that the controller was logged every "
        "2 s and the battery current only every ~10 s; after, every 0.5 s and ~1 s. Earlier "
        "battery numbers are coarse, so per-drive energy uses the controller's figures.",
        "**Speed and distance aren't calibrated yet**, so treat miles and mph as close but "
        "not exact.",
        "**Motor temperature is reported in 5 °C steps**, so it looks like a staircase.",
        f"**Energy for speeding up assumes the car plus driver weigh about "
        f"{CAR_MASS_KG} kg.**",
    ]
    if soc.empty or drives["soc_start"].isna().any():
        caveats.append("**The first drive has no battery data**: the battery wasn't sending on "
                       "the CAN bus yet.")

    rows = []
    for d in drives.itertuples():
        e = de[de["drive"] == d.drive].iloc[0]
        rows.append(
            f"| {int(d.drive)}{' (brake held)' if e['brake_drag_suspected'] else ''} | "
            f"{pd.Timestamp(d.start).strftime('%H:%M')} | {d.distance_mi:.2f} | "
            f"{fmt(d.avg_moving_mph, 1)} | {fmt(d.max_mph, 1)} | {fmt(d.wh_per_mi)} | "
            f"{fmt(e['full_throttle_pct'])}% | {fmt(e['speeding_up_pct'])}% | "
            f"{'–' if pd.isna(d.soc_start) else f'{d.soc_start:.0f} → {d.soc_end:.0f}%'} | "
            f"{fmt(d.max_motor_temp_c)} | {int(d.mc_errors)} |")
    drive_figs = " · ".join(f"[Drive {int(d)}](figures/drive_{int(d):02d}.png)"
                            for d in drives["drive"])
    story = day_story(day)

    return f"""# Solar car telemetry: {date_txt}

What we did on the {date_txt} test day, and what the car's data says. The quickest way in
is **`../dashboard.html`** (open it in a web browser); this page has the same story in
more depth, plus the charts and the data behind it.

![Whole day](figures/day_overview.png)

{story}

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

## Efficiency in pictures

**Energy per mile, drive by drive.** Lower is better.

![Energy per mile by drive](figures/efficiency_by_drive.png)

**Where each drive's energy went.** Yellow is energy spent speeding the car up; blue is
cruising, drag and losses.

![Energy breakdown](figures/energy_breakdown.png)

**Power needed at a steady speed**, and where solar alone could keep the car going.

![Solar break-even](figures/solar_break_even.png)

**Battery vs motor controller current.** Dots above the grey "equal" line mean the battery
supplied more than the controller reported.

![Battery vs controller](figures/battery_vs_controller.png)

## Keep in mind

{chr(10).join(f"- {c}" for c in caveats)}

## Drives

| Drive | Start | Miles | Avg mph | Max mph | Wh/mi | Full throttle | Speeding up | Battery | Motor max °C | Errors |
|---|---|---|---|---|---|---|---|---|---|---|
{chr(10).join(rows)}

"Speeding up" is the share of the drive's energy that went into accelerating the car.
Charts for each drive: {drive_figs}

## What's in this folder

| Path | What it is |
|---|---|
| `../dashboard.html` | Interactive version: hover the timeline, zoom to a drive, try the break-even and motor-heat calculators. |
| `figures/` | The charts as images, for slides and chat. |
| `data/telemetry_1s.csv` | **The main data.** One row per second, one column per measurement. Opens in Excel or Google Sheets. |
| `data/data_dictionary.csv` | What every column in `telemetry_1s.csv` means, with units. |
| `data/drives.csv` | One row per drive with totals and peaks. |
| `data/drive_efficiency.csv` | Per-drive driving style and energy: throttle use, speeding-up energy, regen, brake flag. |
| `data/controller_errors.csv` | Every controller error and what the car was doing in the 20 s before it. |
| `data/events.csv` | Log of state changes: errors, drive mode, regen, sensors dropping out. |
| `data/efficiency_vs_speed.csv` | Power and Wh/mile at steady speeds, by speed band. |
| `data/models.json` | Fitted numbers: motor heat, road load / break-even, battery capacity and health, battery-vs-controller gap. |
| `raw/` | The original export from Home Assistant ({", ".join(f"`{n}`" for n in raw_names)}). |

## Look at the data yourself

**Spreadsheet:** open `data/telemetry_1s.csv`. Each row is one second. Columns are
described in `data/data_dictionary.csv`. Filter `moving` = TRUE for driving only.

**Python:**

```python
import pandas as pd
df = pd.read_csv("data/telemetry_1s.csv", parse_dates=["time"], index_col="time")
df.loc[df.moving, ["speed_mph", "mc_power_w"]].plot(subplots=True)
```

## Words used here

- **Phase current**: current in the motor windings. It's what heats the motor, and it's
  higher than the battery current at low speed.
- **Bus current / controller power**: what the motor controller draws from the battery.
- **SOC**: state of charge, the battery's percentage.
- **Wh/mile**: energy per mile; lower is better.
- **Break-even speed**: the fastest steady speed at which the solar panels supply all the
  power the motor needs.
- **Regen**: energy recovered into the battery when slowing down.
"""


CAR_MASS_KG = 237


def build(day):
    src = tm.PROCESSED / day
    an = src / "analysis"
    if not (an / "dashboard.json").exists():
        sys.exit(f"{day}: run telemetry.py and analysis.py first")
    out = PACKS / day
    if out.exists():
        shutil.rmtree(out)
    det = out / "details"
    (det / "data").mkdir(parents=True)

    wide = pd.read_parquet(src / "wide_1s.parquet")
    drives = pd.read_csv(src / "drives.csv", parse_dates=["start", "end"])
    ov = pd.read_csv(an / "overloads.csv")
    de = pd.read_csv(an / "drive_efficiency.csv")
    gap = json.loads((an / "power_gap.json").read_text())
    models = {k: json.loads((an / f"{f}.json").read_text()) for k, f in
              [("motor_heat", "thermal_model"), ("road_load_break_even", "solar_break_even"),
               ("battery_capacity", "capacity"), ("battery_health", "pack_health")]}
    models["battery_vs_controller"] = {k: v for k, v in gap.items() if k != "points"}

    wide.round(3).to_csv(det / "data" / "telemetry_1s.csv")
    data_dictionary(wide).to_csv(det / "data" / "data_dictionary.csv", index=False)
    drives.to_csv(det / "data" / "drives.csv", index=False)
    de.to_csv(det / "data" / "drive_efficiency.csv", index=False)
    ov.to_csv(det / "data" / "controller_errors.csv", index=False)
    shutil.copy(src / "events.csv", det / "data" / "events.csv")
    shutil.copy(an / "efficiency_vs_speed.csv", det / "data" / "efficiency_vs_speed.csv")
    (det / "data" / "models.json").write_text(json.dumps(models, indent=2))
    shutil.copytree(src / "figures", det / "figures")
    shutil.copy(src / "dashboard.html", out / "dashboard.html")

    (det / "raw").mkdir()
    raws = raw_files_for(day)
    for f in raws:
        shutil.copy(f, det / "raw" / f.name)

    text = readme(day, drives, ov, de, models["motor_heat"], models["road_load_break_even"],
                  models["battery_capacity"], models["battery_health"], gap,
                  [f.name for f in raws])
    (det / "README.md").write_text(text, encoding="utf-8")

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
