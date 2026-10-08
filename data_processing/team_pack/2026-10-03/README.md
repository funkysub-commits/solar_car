# Solar car telemetry: Saturday 3 October 2026

Data logged by the car's Raspberry Pi (motor controller + battery over CAN bus) during
the test drive on Saturday 3 October 2026, cleaned up and analysed. Start with this page, then open
**`dashboard.html`** in a web browser for interactive charts. Everything else in the
folder is the data and the scripts behind those charts.

![Whole day](figures/day_overview.png)

## At a glance

| | |
|---|---|
| Drives | 7 between 13:02 and 15:08 (Pacific time) |
| Distance | 28.3 miles |
| Top speed | 39.4 mph |
| Energy used | 2,677 Wh at the motor controller, 95 Wh/mile overall |
| Battery charge | 99% → 35% |
| Hottest motor | 130 °C |
| Controller errors | 9 |
| Solar break-even speed | about 13.1 mph at 725 W of sun |

## What we learned

1. **Controller overloads mostly happen pulling away from a stop.** 7 of the 9 controller errors came within a few seconds of starting from standstill, almost always at 100% throttle. Easing into the throttle from a stop, or lowering the controller's launch current limit, should remove most of them. Each error is listed in `data/controller_errors.csv`, and the red lines on the charts mark when they happened.
2. **The motor overheated (up to 130 °C) and stalled at 14:14** after a long stretch near full power, and the controller had to be switched off and on to clear it. The motor heats and cools slowly (it takes about 13 minutes to settle). Our fitted heat model says it can sustain about 214 A of phase current at 1500 rpm indefinitely; more than that and it eventually reaches the limit. See `figures/motor_thermal_model.png`.
3. **Solar break-even is about 13.1 mph** (likely 11.5–14.3 mph) with 725 W of sun. Below that speed the panels supply everything the motor uses, so the car could in principle keep going all day. The solar figure is from 1 short parked charging period, so it's the weakest number here; a proper measurement of the array would firm it up. See `figures/solar_break_even.png`; the interactive dashboard lets you try other solar figures.
4. **Steady cruising costs about 57 Wh per mile at 15 mph, 71 at 25 and 92 at 35.** The day's overall 95 Wh/mi is higher because of starts, stops and hills. Regen gave back very little (under 5% per drive).
5. **Battery: about 88.5 Ah per 100% state of charge, against a 100 Ah rating.** The pack delivered 57.5 Ah while charge fell 99% → 35%. Cells stayed well balanced: 4 mV apart at rest and 11.5 mV under load.

## Keep in mind

- **Low-speed readings are unreliable.** Below about 8 mph the motor struggles and speed, rpm and current jump around (speed can even read negative). The data is left in, but the efficiency and break-even numbers only use driving above 8 mph.
- **The two current sensors disagree.** Only the motor runs off the main battery (accessories have their own battery), yet the battery's current reads about 12% higher than the controller's. One of them is off, so energy and capacity figures carry roughly that much uncertainty.
- **Values are only logged when they change**, roughly every 1–2 s for the motor and every 1–10 s for the battery. Short spikes between readings are missed, and `telemetry_1s.csv` holds each value until the next reading.
- **Some drives have no battery data** because the battery wasn't sending on the CAN bus at the time (see the Drives table).
- **Motor temperature is reported in 5 °C steps**, so it looks like a staircase on the charts.

## Drives

| Drive | Start | Miles | Moving min | Avg mph | Max mph | Wh/mi | Battery | Motor max °C | Errors |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 13:02 | 0.33 | 2 | 9.7 | 21.6 | 72 | – | 35 | 1 |
| 2 | 13:11 | 4.47 | 17 | 15.7 | 29.5 | 79 | 99 → 93% | 70 | 2 |
| 3 | 13:37 | 5.04 | 15 | 20.5 | 35.6 | 89 | 93 → 82% | 100 | 1 |
| 4 | 13:59 | 6.04 | 16 | 23.1 | 37.2 | 145 | 83 → 64% | 130 | 2 |
| 5 | 14:22 | 9.60 | 20 | 28.4 | 39.4 | 79 | 62 → 41% | 110 | 1 |
| 6 | 14:53 | 0.88 | 4 | 12.4 | 16.9 | 47 | 41 → 40% | 55 | 0 |
| 7 | 15:01 | 1.93 | 6 | 17.5 | 32.4 | 92 | 40 → 35% | 70 | 1 |

Charts for each drive: [Drive 1](figures/drive_01.png) · [Drive 2](figures/drive_02.png) · [Drive 3](figures/drive_03.png) · [Drive 4](figures/drive_04.png) · [Drive 5](figures/drive_05.png) · [Drive 6](figures/drive_06.png) · [Drive 7](figures/drive_07.png)

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

`telemetry.py` reads every export in `raw/` (`solarcar_raw_readings_2026-10-03.xls`).
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
