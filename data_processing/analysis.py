"""Analyses on a processed telemetry day (run telemetry.py first).

    python analysis.py                 every day under processed/
    python analysis.py 2026-10-03      one day

Writes processed/<date>/analysis/:
    overloads.csv          each controller error: what led up to it, how it cleared
    thermal_model.json     fitted motor heating/cooling model + warning rule
    thermal_fit.csv        measured vs modelled motor temp, per second
    efficiency_vs_speed.csv  Wh/mi in steady-speed cruising, by speed bin
    solar_break_even.json  road-load fit, solar estimate, break-even speed
    capacity.json          usable pack capacity from Ah counted vs SOC drop
    pack_health.json       pack/cell resistance, cell imbalance at rest/under load
    dashboard.json         downsampled series + tables for the HTML dashboard
and figures/: one overview PNG per drive, plus the analysis charts, and
dashboard.html: interactive page (timeline, break-even and motor-heat tools).
Also appends a row per day to processed/health_history.csv.
"""
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.optimize import least_squares

from telemetry import LOW_SPEED_MPH, PROCESSED

# Chart palette (validated reference palette, light mode).
INK, INK2, MUTED = "#0b0b0b", "#52514e", "#898781"
GRID, SURFACE = "#e1e0d9", "#fcfcfb"
S1, S2, S3, S4 = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
CRITICAL = "#d03b3b"

LAUNCH_MPH = 3.0          # "pulling away" = was below this in the lookback
LOOKBACK = pd.Timedelta(seconds=20)
MOTOR_TEMP_LIMIT_C = 130  # hottest seen when it stalled; treat as the ceiling


def controller_active(w):
    """Seconds the motor controller was on the bus (falls back to 'has rpm data'
    when the status sensor wasn't exported)."""
    if w["mc_online"].notna().any():
        return w["mc_online"] == 1
    return w["motor_rpm"].notna()


# --- 1. Controller errors ------------------------------------------------------

def overloads(w, ev):
    err = ev[(ev["channel"] == "ezkontrol_errors") & ev["state"].notna()
             & ~ev["state"].isin(["", "unavailable", "unknown"])]
    cleared = ev[(ev["channel"] == "ezkontrol_errors") & ev["state"].isna()]
    contactor_off = ev[(ev["channel"] == "ezkontrol_dc_contactor") & (ev["state"] == "Off")]
    rows = []
    for t, state in zip(err["time"], err["state"]):
        sec = t.floor("s")
        pre = w.loc[sec - LOOKBACK:sec + pd.Timedelta(seconds=2)]
        later = cleared[cleared["time"] > t]["time"]
        t_clear = later.iloc[0] if len(later) else pd.NaT
        cycled = ((contactor_off["time"] >= t - pd.Timedelta(seconds=2))
                  & (contactor_off["time"] <= (t_clear if pd.notna(t_clear) else t))).any()
        before = w.loc[sec - LOOKBACK:sec - pd.Timedelta(seconds=3), "speed_mph"]
        if "Stall" in state:
            context = "stalled under load"
        elif (before.abs() < LAUNCH_MPH).any():
            context = "pulling away"
        else:
            context = "under way"
        rows.append({
            "time": t, "error": state,
            "context": context,
            # Speed/rpm/current readings below LOW_SPEED_MPH are jumpy - the
            # motor struggles there - so treat them as rough.
            "speed_mph": w["speed_mph"].asof(sec),
            "low_speed_reading": bool(abs(w["speed_mph"].asof(sec)) < LOW_SPEED_MPH),
            "max_throttle_pct": pre["throttle_pct"].max(),
            "secs_at_full_throttle": int((pre["throttle_pct"] >= 99).sum()),
            "max_phase_current_a": pre["mc_phase_current_a"].max(),
            "max_bus_current_a": pre["mc_bus_current_a"].max(),
            "motor_temp_c": w["motor_temp_c"].asof(sec),
            "mc_temp_c": w["mc_temp_c"].asof(sec),
            "cleared_after_s": round((t_clear - t).total_seconds()) if pd.notna(t_clear) else np.nan,
            "cleared_by": "power cycle" if cycled else "self-cleared",
        })
    return pd.DataFrame(rows)


# --- 2. Motor thermal model -----------------------------------------------------
# dT/dt = a*(I/100)^2 + b*(rpm/1000) - (T - T_amb)/tau     (per second)
# I^2 is copper loss, rpm term stands in for iron/friction loss. Motor temp is
# reported in 5 C steps, so this fits the trend, not the staircase.

def _simulate(p, i2, rpm, t0, mask_reset=None):
    a, b, tau, tamb = p
    T = np.empty(len(i2))
    T[0] = t0
    for k in range(1, len(i2)):
        T[k] = T[k - 1] + a * i2[k - 1] + b * rpm[k - 1] - (T[k - 1] - tamb) / tau
    return T


def thermal_model(w):
    d = w.loc[controller_active(w)]
    span = w.loc[d.index[0]:d.index[-1]]
    i2 = ((span["mc_phase_current_a"].fillna(0) / 100) ** 2).to_numpy()
    rpm = (span["motor_rpm"].abs().fillna(0) / 1000).to_numpy()
    meas = span["motor_temp_c"].to_numpy()
    ok = ~np.isnan(meas)
    t0 = meas[ok][0]

    def resid(p):
        return (_simulate(p, i2, rpm, t0) - meas)[ok]

    fit = least_squares(resid, x0=[0.05, 0.01, 600, 20],
                        bounds=([0, 0, 30, 0], [5, 5, 20000, 45]))
    a, b, tau, tamb = fit.x
    sim = _simulate(fit.x, i2, rpm, t0)
    r = sim[ok] - meas[ok]
    rmse = float(np.sqrt(np.mean(r ** 2)))
    r2 = float(1 - np.sum(r ** 2) / np.sum((meas[ok] - meas[ok].mean()) ** 2))

    def steady(i_a, rpm_v):
        return tamb + tau * (a * (i_a / 100) ** 2 + b * rpm_v / 1000)

    # Time to reach the limit from a given temp at a sustained load.
    def secs_to_limit(T, i_a, rpm_v):
        Tss = steady(i_a, rpm_v)
        if Tss <= MOTOR_TEMP_LIMIT_C:
            return None
        return float(tau * np.log((Tss - T) / (Tss - MOTOR_TEMP_LIMIT_C)))

    model = {
        "equation": "dT/dt = a*(I_phase/100)^2 + b*(rpm/1000) - (T - T_amb)/tau  [C/s]",
        "a": a, "b": b, "tau_s": tau, "t_amb_c": tamb,
        "rmse_c": rmse, "r2": r2, "points": int(ok.sum()),
        "limit_c": MOTOR_TEMP_LIMIT_C,
        "steady_state_c": {f"{i}A@{n}rpm": round(steady(i, n), 1)
                           for i, n in [(100, 1500), (150, 1500), (200, 1500), (300, 1000)]},
        "secs_to_limit_from_80c": {f"{i}A@{n}rpm": (round(s) if (s := secs_to_limit(80, i, n)) else None)
                                   for i, n in [(150, 1500), (200, 1500), (300, 1000)]},
        "max_continuous_phase_a_at_1500rpm": round(
            100 * np.sqrt(max((MOTOR_TEMP_LIMIT_C - tamb) / tau - b * 1.5, 0) / a), 0),
        "warning_rule": ("warn when the model predicts the motor reaches the limit within "
                         "60 s at the current load, i.e. T > Tss - (Tss - limit)*exp(60/tau)"),
    }
    out = pd.DataFrame({"measured_c": meas, "model_c": sim,
                        "phase_current_a": span["mc_phase_current_a"]}, index=span.index)
    return model, out


# --- 3. Efficiency vs speed --------------------------------------------------------

CAR_MASS_KG = 237  # car + driver, from the road-load fit; used for kinetic energy
BRAKE_DRAG_RATIO = 1.6  # steady W/mph this many times the day's median = brake dragging


def _steady(w, window=8, band=3.0):
    """Rolling windows where speed stays within `band` mph and above LOW_SPEED_MPH."""
    roll = w["speed_mph"].rolling(window)
    return ((roll.max() - roll.min()) < band) & (roll.min() > LOW_SPEED_MPH)


def drive_efficiency(w, drives):
    """Per-drive driving-style and efficiency numbers, plus a brake-drag flag.

    A drive whose steady-speed power per mph is far above the others' had
    something fighting the motor (on 2026-10-03 the brake was held down), so
    it is flagged and kept out of the cruise-efficiency fits.
    """
    r = w[["speed_mph", "mc_power_w"]].rolling(8).mean()
    steady = _steady(w)
    rows = []
    for _, d in drives.iterrows():
        s = w.loc[d["start"]:d["end"]]
        st = r.loc[d["start"]:d["end"]][steady.loc[d["start"]:d["end"]]]
        mv = s[s["speed_mph"] > LOW_SPEED_MPH]
        v = s["speed_mph"].clip(lower=0).rolling(3, center=True).mean() * MPS
        dke = (0.5 * CAR_MASS_KG * v ** 2).diff()
        e_in = s["mc_power_w"].clip(lower=0).sum() / 3600
        regen = -s["mc_power_w"].clip(upper=0).sum() / 3600
        accel = dke[dke > 0].sum() / 3600
        rows.append({
            "drive": int(d["drive"]), "distance_mi": d["distance_mi"],
            "wh_per_mi": d["wh_per_mi"], "avg_moving_mph": d["avg_moving_mph"],
            "steady_w_per_mph": round(float((st["mc_power_w"] / st["speed_mph"]).median()), 1)
            if len(st) else np.nan,
            "mean_throttle_pct": round(float(mv["throttle_pct"].mean()), 0) if len(mv) else np.nan,
            "full_throttle_pct": round(float(100 * (mv["throttle_pct"] >= 99).mean()), 0)
            if len(mv) else np.nan,
            "slowing_on_throttle_s": int(((s["throttle_pct"] > 50)
                                          & (s["speed_mph"].diff(3) < -3)).sum()),
            "coasting_s": int(((s["throttle_pct"] == 0) & (s["speed_mph"] > LOW_SPEED_MPH)).sum()),
            "stops": int(((s["speed_mph"] < 1) & (s["speed_mph"].shift() >= 1)).sum()),
            "energy_in_wh": round(e_in),
            "speeding_up_wh": round(accel),
            "speeding_up_pct": round(100 * accel / e_in, 1) if e_in > 0 else np.nan,
            "regen_wh": round(regen, 1),
            "regen_of_speeding_up_pct": round(100 * regen / accel, 1) if accel > 0 else np.nan,
            "mc_errors": int(d["mc_errors"]),
        })
    df = pd.DataFrame(rows)
    med = df["steady_w_per_mph"].median()
    df["brake_drag_suspected"] = df["steady_w_per_mph"] > BRAKE_DRAG_RATIO * med
    return df


def fit_mask(w, drives, exclude):
    """True everywhere except inside the excluded drives."""
    m = pd.Series(True, index=w.index)
    for _, d in drives[drives["drive"].isin(exclude)].iterrows():
        m.loc[d["start"]:d["end"]] = False
    return m


def power_gap(w, long):
    """Battery (BMS) vs motor controller: where the two power readings differ.

    Uses only the high-resolution stretch (both logged ~1 s) when there is
    one; 10 s battery samples are too coarse to compare second by second.
    """
    # Sample spacing of the raw battery-current readings (the 1 s table holds
    # values forward, so it can't show this).
    t = long.loc[long["channel"] == "pack_current_a", "time"].sort_values()
    gaps = t.diff().dt.total_seconds().rolling(30).median()
    fast_from = t[gaps < 2.5]
    hi = w.loc[fast_from.iloc[0].floor("s"):] if len(fast_from) else w.iloc[0:0]
    out = {"hi_res_from": hi.index[0].isoformat() if len(hi) else None}
    both = lambda s: s["pack_current_a"].notna() & s["mc_bus_current_a"].notna() & controller_active(s)
    for name, s in [("hi_res", hi), ("whole_day", w)]:
        b = both(s)
        ib, im = -s.loc[b, "pack_current_a"], s.loc[b, "mc_bus_current_a"]
        out[name] = {"seconds": int(b.sum()),
                     "battery_ah": round(float(ib.sum() / 3600), 2),
                     "controller_ah": round(float(im.sum() / 3600), 2),
                     "battery_wh": round(float(s.loc[b, "pack_power_w"].sum() / 3600)),
                     "controller_wh": round(float(s.loc[b, "mc_power_w"].sum() / 3600))}
    src = hi if len(hi) > 300 else w
    x, y = -src["pack_current_a"], src["mc_bus_current_a"]
    lags = range(0, 8)
    corr = [x.shift(-l)[(x.shift(-l).notna() & y.notna() & src["moving"])].corr(
        y[(x.shift(-l).notna() & y.notna() & src["moving"])]) for l in lags]
    lag = int(np.nanargmax(corr))
    xs, ys = x.shift(-lag).rolling(10).mean(), y.rolling(10).mean()
    ok = xs.notna() & ys.notna() & (ys > 5)
    slope, offset = np.polyfit(ys[ok], xs[ok], 1)
    idle = controller_active(w) & (w["speed_mph"].abs() < 0.3) & (w["throttle_pct"] == 0)
    load = w["mc_bus_current_a"] > 5
    dv = (w["pack_voltage_v"] - w["mc_bus_voltage_v"])
    r_mohm = 1000 * np.polyfit(w.loc[load & dv.notna(), "mc_bus_current_a"], dv[load & dv.notna()], 1)[0]
    return out | {
        "battery_lag_s": lag,
        "fit_battery_a_vs_controller_a": {"slope": round(float(slope), 3),
                                          "offset_a": round(float(offset), 2),
                                          "corr": round(float(xs[ok].corr(ys[ok])), 3)},
        "extra_power_w_at_pack_v": round(float(offset * w["pack_voltage_v"].median())),
        "idle_battery_a": float((-w.loc[idle, "pack_current_a"]).median()),
        "idle_voltage_offset_v": round(float(dv[idle].median()), 2),
        "wiring_resistance_mohm": round(float(r_mohm), 1),
        "points": [[round(float(a), 1), round(float(b), 1)]
                   for a, b in zip(ys[ok].iloc[::5], xs[ok].iloc[::5])],
        "note": ("Battery current tracks controller current almost exactly in scale, but "
                 "reads a steady offset higher whenever the motor runs (zero when parked): "
                 "a small load that is only on while driving, or a ~1 A offset in the "
                 "controller's current sensor. The battery also reads slightly higher "
                 "voltage at rest (calibration) and the cabling/contactor/fuse drop adds "
                 "a few milliohms. Before the high-resolution switch the battery current "
                 "was logged only every ~10 s, so per-drive battery energy is noisy."),
    }


def efficiency_vs_speed(w, window=8, mask=None):
    """Wh/mi in steady cruising (speed within a 3 mph band over `window` s).

    Only above LOW_SPEED_MPH: below that the motor jumps around and the
    readings aren't trustworthy. `mask` drops excluded stretches (brake drag).
    """
    steady = _steady(w, window) & w["mc_power_w"].rolling(window).count().eq(window)
    if mask is not None:
        steady &= mask
    roll = w["speed_mph"].rolling(window)
    p = w["mc_power_w"].rolling(window).mean()
    v = roll.mean()
    pts = pd.DataFrame({"speed": v[steady], "power_w": p[steady]})
    pts = pts.iloc[::window]  # non-overlapping windows
    pts["wh_per_mi"] = pts["power_w"] / pts["speed"]
    bins = [LOW_SPEED_MPH] + list(range(10, 45, 5))
    pts["bin"] = pd.cut(pts["speed"], bins)
    tbl = pts.groupby("bin", observed=True).agg(
        n=("wh_per_mi", "size"), speed_mph=("speed", "mean"),
        power_w_p25=("power_w", lambda x: x.quantile(.25)),
        power_w_p75=("power_w", lambda x: x.quantile(.75)),
        power_w=("power_w", "median"), wh_per_mi=("wh_per_mi", "median"),
        wh_per_mi_p25=("wh_per_mi", lambda x: x.quantile(.25)),
        wh_per_mi_p75=("wh_per_mi", lambda x: x.quantile(.75))).reset_index()
    tbl["bin"] = tbl["bin"].astype(str)
    return tbl[tbl["n"] >= 3].round(1), pts


# --- 3b. Solar input and break-even speed --------------------------------------------
# No solar sensor is logged, so solar is read off the pack current while parked
# with the controller idle: there, pack current = solar in. (Accessories run
# off their own battery, not the main pack.)
# (Uses the current's sign, not the BESTGO "charging" flag.)

MPS = 0.44704  # m/s per mph


def solar_estimate(w, min_secs=60):
    b = w[w["pack_current_a"].notna()]
    parked = (b["speed_mph"].abs() < 0.5) & (b["mc_bus_current_a"].fillna(0).abs() < 0.5)
    charging = parked & (b["pack_current_a"] > 1)
    runs = charging.ne(charging.shift()).cumsum()
    wins = []
    for _, g in b[charging].groupby(runs[charging]):
        if len(g) >= min_secs:
            wins.append({"start": g.index[0].isoformat(), "end": g.index[-1].isoformat(),
                         "secs": len(g),
                         "median_w": round(float(-g["pack_power_w"].median()))})
    return {
        "windows": wins,
        "solar_w": round(float(np.median([x["median_w"] for x in wins]))) if wins else None,
        "parked_secs": int(parked.sum()),
        "parked_secs_reading_zero": int((parked & (b["pack_current_a"] == 0)).sum()),
        "note": ("Net solar = pack charge power while parked with the controller idle. "
                 "Most parked time reads exactly 0 A, so the array was only feeding the "
                 "pack (or in sun) during the windows listed - treat as provisional."),
    }


def road_load(w, n_boot=200, seed=0, max_accel=0.15, mask=None):
    """Fit P_controller = c0 + c1*v + c3*v^3 + m*a*v (all >= 0) on 5 s smoothed data.

    c0 ~ controller/motor no-load loss, c1*v ~ rolling resistance plus
    drivetrain/motor losses (the route was flat), c3*v^3 ~ aero drag,
    m ~ effective mass (kg). v in m/s. Only near-steady samples
    (|a| < max_accel) are used, with a robust loss so launches and the odd
    spike don't drag the cruise curve up; `mask` drops excluded drives.
    """
    r = w[["speed_mph", "mc_power_w"]].rolling(5, center=True).mean()
    v = r["speed_mph"] * MPS
    a = (v.shift(-2) - v.shift(2)) / 4
    m = ((r["speed_mph"] > LOW_SPEED_MPH) & r["mc_power_w"].notna() & a.notna()
         & controller_active(w) & (a.abs() < max_accel))
    if mask is not None:
        m &= mask
    X = np.column_stack([np.ones(m.sum()), v[m], v[m] ** 3, (a * v)[m]])
    y = r["mc_power_w"][m].to_numpy()

    def fit(Xs, ys):
        return least_squares(lambda c: Xs @ c - ys, x0=[50, 100, 0.1, 200],
                             bounds=([0, 0, 0, 0], [2000, 2000, 50, 2000]),
                             loss="soft_l1", f_scale=200).x

    c = fit(X, y)
    pred = X @ c
    r2 = float(1 - ((y - pred) ** 2).sum() / ((y - y.mean()) ** 2).sum())
    # Block bootstrap (60 s blocks) for an uncertainty band.
    rng = np.random.default_rng(seed)
    blocks = np.arange(len(y)) // 60
    groups = [np.flatnonzero(blocks == k) for k in np.unique(blocks)]
    boots = []
    for _ in range(n_boot):
        pick = np.concatenate([groups[k] for k in rng.integers(0, len(groups), len(groups))])
        boots.append(fit(X[pick], y[pick]))
    return c, np.array(boots), r2, int(m.sum())


def cruise_power(c, mph):
    v = np.asarray(mph) * MPS
    return c[0] + c[1] * v + c[2] * v ** 3


def break_even(c, solar_w, aux_w=0):
    """Highest steady speed whose cruise power (+ any other main-battery load) <= solar."""
    grid = np.arange(0, 60.01, 0.1)
    ok = grid[cruise_power(c, grid) + aux_w <= solar_w]
    return float(ok.max()) if len(ok) else 0.0


def solar_break_even(w, aux_w=0, mask=None, excluded=()):
    # aux_w: other loads on the main battery. Accessories have their own
    # battery, so it is 0 on this car.
    c, boots, r2, n = road_load(w, mask=mask)
    sol = solar_estimate(w)
    levels = sorted({300, 500, 1000, 1200, sol["solar_w"] or 720})
    table = []
    for s_w in levels:
        be = [break_even(b, s_w, aux_w) for b in boots]
        table.append({"solar_w": s_w, "break_even_mph": round(break_even(c, s_w, aux_w), 1),
                      "p10_mph": round(float(np.percentile(be, 10)), 1),
                      "p90_mph": round(float(np.percentile(be, 90)), 1)})
    curve = []
    for mph in range(5, 41, 5):
        bp = [cruise_power(b, mph) for b in boots]
        curve.append({"mph": mph, "power_w": round(float(cruise_power(c, mph))),
                      "p10_w": round(float(np.percentile(bp, 10))),
                      "p90_w": round(float(np.percentile(bp, 90))),
                      "wh_per_mi": round(float(cruise_power(c, mph)) / mph, 1)})
    return {
        "model": "P = c0 + c1*v + c3*v^3 + m*a*v  (v m/s, a m/s^2, P controller input W)",
        "c0_w": c[0], "c1_w_per_mps": c[1], "c3_w_per_mps3": c[2], "mass_kg": c[3],
        # Same terms in physical units (includes motor/controller losses).
        "rolling_and_drivetrain_force_n": round(c[1], 1),
        "drag_area_cda_m2": round(c[2] / (0.5 * 1.2), 2),
        "r2": r2, "points": n, "aux_load_w_assumed": aux_w,
        "excluded_drives": [int(x) for x in excluded],
        "solar": sol, "break_even": table, "cruise_curve": curve,
        "note": ("Break-even = fastest steady speed where cruise power <= solar, "
                 "i.e. range limited only by daylight. Flat route; fitted above 8 mph only, "
                 "leaving out drives with brake drag. p10/p90 from a 60 s block bootstrap."),
    }


# --- 4. Pack capacity ----------------------------------------------------------------

def capacity(w):
    b = w[w["pack_current_a"].notna()]
    if b.empty or b["soc_pct"].nunique() < 5:
        return None
    ah = (-b["pack_current_a"] / 3600).cumsum()
    wh = (b["pack_power_w"] / 3600).cumsum()
    chg = b["soc_pct"].ne(b["soc_pct"].shift())
    soc = b.loc[chg, "soc_pct"]
    ah_pct = -np.polyfit(soc, ah[chg], 1)[0]
    wh_pct = -np.polyfit(soc, wh[chg], 1)[0]
    nominal_v = float(b["pack_voltage_v"].median())
    return {
        "soc_from": float(soc.iloc[0]), "soc_to": float(soc.iloc[-1]),
        "ah_counted": round(float(ah.iloc[-1]), 1), "wh_counted": round(float(wh.iloc[-1])),
        "usable_capacity_ah": round(100 * ah_pct, 1),
        "usable_capacity_wh": round(100 * wh_pct),
        "rated_capacity_ah": 100, "median_pack_v": nominal_v,
        "note": ("Capacity = Ah counted per 1% of BMS SOC drop x 100, vs the pack's "
                 "100 Ah rating (BWP-FE51100). Lower = capacity loss, an optimistic BMS "
                 "SOC, or pack-current calibration; confirm with a full charge/discharge."),
    }


# --- 5. Pack / cell health -----------------------------------------------------------

def pack_health(long, w):
    def pair(a, b, tol="500ms"):
        x = long[long["channel"] == a][["time", "value"]].dropna().rename(columns={"value": a})
        y = long[long["channel"] == b][["time", "value"]].dropna().rename(columns={"value": b})
        return pd.merge_asof(x.sort_values("time"), y.sort_values("time"), on="time",
                             direction="nearest", tolerance=pd.Timedelta(tol)).dropna()

    pv = pair("pack_voltage_v", "pack_current_a")
    if len(pv) < 50:
        return None
    soc = w["soc_pct"].reindex(pv["time"].dt.floor("s")).to_numpy()
    X = np.column_stack([np.ones(len(pv)), soc, pv["pack_current_a"]])
    good = ~np.isnan(soc)
    coef = np.linalg.lstsq(X[good], pv["pack_voltage_v"].to_numpy()[good], rcond=None)[0]
    r_pack = coef[2] * 1000  # V/A -> mOhm (current negative on discharge)

    cv = pair("cell_v_min_mv", "pack_current_a")
    r_cell = np.polyfit(cv["pack_current_a"], cv["cell_v_min_mv"], 1)[0]  # mV/A = mOhm

    dl = pair("cell_v_delta_mv", "pack_current_a")
    rest = dl[dl["pack_current_a"].abs() < 2]["cell_v_delta_mv"]
    load = dl[dl["pack_current_a"] < -50]["cell_v_delta_mv"]
    return {
        "pack_resistance_mohm": round(r_pack, 1),
        "weakest_cell_resistance_mohm": round(r_cell, 2),
        "cell_delta_at_rest_mv_median": float(rest.median()) if len(rest) else None,
        "cell_delta_over_50a_mv_median": float(load.median()) if len(load) else None,
        "cell_delta_max_mv": float(dl["cell_v_delta_mv"].max()),
        "min_cell_mv": float(w["cell_v_min_mv"].min()),
        "max_cell_temp_c": float(w["cell_temp_max_c"].max()),
        "samples": int(len(pv)),
        "pack_vs_mc_current_ratio": round(float(
            (-w["pack_current_a"]).where(w["moving"]).sum()
            / w["mc_bus_current_a"].where(w["moving"] & w["pack_current_a"].notna()).sum()), 3),
        "note": ("Resistances from voltage sag vs current (SOC as covariate). Track these "
                 "day to day: rising resistance or rest-delta = ageing / a weak cell. "
                 "Nothing else runs off the main battery, so pack_vs_mc_current_ratio "
                 "should be ~1; the gap is calibration between the two current sensors."),
    }


# --- Charts --------------------------------------------------------------------------

def _style(ax):
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color("#c3c2b7")
    ax.grid(True, color=GRID, lw=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(colors=MUTED, labelsize=8)
    ax.yaxis.label.set_color(INK2)
    ax.yaxis.label.set_size(9)


def drive_figure(w, drive, ov, path, title=None, smooth=None):
    s = w.loc[pd.Timestamp(drive["start"]) - pd.Timedelta("30s"):
              pd.Timestamp(drive["end"]) + pd.Timedelta("30s")]
    if smooth:  # whole-day view: average so the lines stay readable
        s = s.select_dtypes("number").rolling(smooth, min_periods=1).mean()
    t = s.index.tz_localize(None)
    fig, axes = plt.subplots(4, 1, figsize=(11, 8.5), sharex=True,
                             gridspec_kw={"hspace": 0.18}, facecolor=SURFACE)
    panels = [
        ("Speed (mph)", [("speed_mph", S1, None)]),
        ("Power (W)", [("pack_power_w", S1, "battery"), ("mc_power_w", S2, "controller")]),
        ("Temperature (°C)", [("motor_temp_c", S2, "motor"), ("mc_temp_c", S1, "controller"),
                              ("cell_temp_max_c", S3, "battery cells")]),
        ("State of charge (%)", [("soc_pct", S1, None)]),
    ]
    for ax, (label, series) in zip(axes, panels):
        _style(ax)
        for col, color, name in series:
            ax.plot(t, s[col], color=color, lw=1.4, label=name, solid_capstyle="round")
        ax.set_ylabel(label)
        if len(series) > 1:
            ax.legend(loc="upper left", fontsize=8, frameon=False, ncol=len(series),
                      labelcolor=INK2)
    axes[1].axhline(0, color="#c3c2b7", lw=0.8)
    errs = ov[(ov["time"] >= s.index[0]) & (ov["time"] <= s.index[-1])]
    for et in errs["time"]:
        for ax in axes:
            ax.axvline(et.tz_localize(None), color=CRITICAL, lw=1, alpha=.8)
    if len(errs):
        axes[0].text(errs["time"].iloc[0].tz_localize(None), axes[0].get_ylim()[1],
                     " ⚠ controller error", color=INK2, fontsize=8, va="top")
    axes[-1].xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%H:%M"))
    fig.suptitle(title or (f"Drive {drive['drive']} · {pd.Timestamp(drive['start']):%d %b %H:%M} · "
                           f"{drive['distance_mi']} mi · {drive['wh_per_mi']} Wh/mi · "
                           f"{len(errs)} controller error(s)"),
                 x=0.07, ha="left", color=INK, fontsize=11)
    fig.savefig(path, dpi=110, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)


def break_even_figure(be, eff_tbl, figdir):
    curve = pd.DataFrame(be["cruise_curve"])
    mph = np.arange(0, 40.5, 0.5)
    c = [be["c0_w"], be["c1_w_per_mps"], be["c3_w_per_mps3"]]
    aux = be["aux_load_w_assumed"]
    sol = be["solar"]["solar_w"]
    fig, ax = plt.subplots(figsize=(8, 4.5), facecolor=SURFACE)
    _style(ax)
    ax.fill_between(curve["mph"], curve["p10_w"] + aux, curve["p90_w"] + aux,
                    color=S1, alpha=.10, lw=0)
    ax.plot(mph, cruise_power(c, mph) + aux, color=S1, lw=2)
    ax.scatter(eff_tbl["speed_mph"], eff_tbl["power_w"], color=S1, s=36, zorder=3,
               edgecolor=SURFACE, lw=2)
    for row in be["break_even"]:
        main = row["solar_w"] == sol
        ax.axhline(row["solar_w"], color=S4 if main else GRID, lw=1.4 if main else 0.8)
        ax.text(40.5, row["solar_w"], f"{row['solar_w']} W → {row['break_even_mph']} mph",
                fontsize=8, color=INK if main else MUTED, va="center")
    ax.axvspan(0, LOW_SPEED_MPH, color=GRID, alpha=.5, lw=0)
    ax.text(LOW_SPEED_MPH / 2, 2900, "low speed:\nnot fitted", ha="center", va="top",
            fontsize=8, color=MUTED)
    ax.set_xlim(0, 40)
    ax.set_ylim(0, 3000)
    ax.set_xlabel("Steady speed (mph)", color=INK2, fontsize=9)
    ax.set_ylabel("Power needed (W)")
    ax.set_title("Solar break-even: line = road-load fit, dots = steady-speed medians, "
                 "band = p10-p90", loc="left", fontsize=9.5, color=INK)
    fig.savefig(figdir / "solar_break_even.png", dpi=110, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)


def analysis_figures(w, eff_tbl, eff_pts, thermal, model, ov, figdir):
    # Efficiency vs speed
    fig, ax = plt.subplots(figsize=(7, 4), facecolor=SURFACE)
    _style(ax)
    ax.scatter(eff_pts["speed"], eff_pts["wh_per_mi"], s=10, color=S1, alpha=.25, lw=0)
    ax.plot(eff_tbl["speed_mph"], eff_tbl["wh_per_mi"], color=S1, lw=2, marker="o", ms=4)
    ax.set_ylim(0, min(eff_pts["wh_per_mi"].quantile(.98) * 1.1, 400))
    ax.set_xlabel("Steady cruising speed (mph)", color=INK2, fontsize=9)
    ax.set_ylabel("Wh per mile (controller input)")
    ax.set_title("Energy use vs speed — median per 2.5 mph bin, dots are 10 s windows",
                 loc="left", fontsize=10, color=INK)
    fig.savefig(figdir / "efficiency_vs_speed.png", dpi=110, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)

    # Thermal model fit
    fig, axes = plt.subplots(2, 1, figsize=(11, 5.5), sharex=True, facecolor=SURFACE,
                             gridspec_kw={"height_ratios": [2, 1]})
    t = thermal.index.tz_localize(None)
    _style(axes[0]); _style(axes[1])
    axes[0].plot(t, thermal["measured_c"], color=S2, lw=1.4, label="measured")
    axes[0].plot(t, thermal["model_c"], color=S1, lw=1.4, label="model")
    axes[0].axhline(MOTOR_TEMP_LIMIT_C, color=CRITICAL, lw=1)
    axes[0].text(t[0], MOTOR_TEMP_LIMIT_C, f" stall at {MOTOR_TEMP_LIMIT_C} °C", color=INK2,
                 fontsize=8, va="bottom")
    axes[0].set_ylabel("Motor temp (°C)")
    axes[0].legend(loc="lower right", frameon=False, fontsize=8, ncol=2, labelcolor=INK2)
    axes[1].plot(t, thermal["phase_current_a"].clip(lower=0), color=S1, lw=0.8)
    axes[1].set_ylabel("Phase current (A)")
    axes[1].xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%H:%M"))
    fig.suptitle(f"Motor thermal model — τ = {model['tau_s'] / 60:.0f} min, "
                 f"RMSE {model['rmse_c']:.1f} °C, R² {model['r2']:.2f}",
                 x=0.07, ha="left", fontsize=11, color=INK)
    fig.savefig(figdir / "motor_thermal_model.png", dpi=110, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)


def efficiency_figures(de, gap, figdir):
    """Per-drive Wh/mi, where each drive's energy went, battery vs controller current."""
    brake = de["brake_drag_suspected"]
    labels = [f"Drive {d}" for d in de["drive"]]

    fig, ax = plt.subplots(figsize=(8, 4), facecolor=SURFACE)
    _style(ax)
    colors = [S2 if b else S1 for b in brake]
    bars = ax.bar(labels, de["wh_per_mi"], color=colors, width=0.6)
    for b, v, flag, thr in zip(bars, de["wh_per_mi"], brake, de["full_throttle_pct"]):
        ax.text(b.get_x() + b.get_width() / 2, v + 2, f"{v:.0f}", ha="center", fontsize=9, color=INK)
        if flag:
            ax.text(b.get_x() + b.get_width() / 2, v + 14, "brake held", ha="center",
                    fontsize=8, color=INK2)
    ax.set_ylabel("Wh per mile (controller input)")
    ax.set_title("Energy per mile by drive (lower is better)", loc="left", fontsize=10, color=INK)
    ax.set_ylim(0, de["wh_per_mi"].max() * 1.25)
    fig.savefig(figdir / "efficiency_by_drive.png", dpi=110, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)

    # Where the energy went: speeding up vs everything else (cruising + losses).
    fig, ax = plt.subplots(figsize=(8, 4), facecolor=SURFACE)
    _style(ax)
    acc = de["speeding_up_wh"].clip(upper=de["energy_in_wh"])
    rest = de["energy_in_wh"] - acc
    ax.bar(labels, rest, color=S1, width=0.6, label="cruising, losses and drag")
    ax.bar(labels, acc, bottom=rest, color=S4, width=0.6, label="speeding up (kinetic energy)",
           edgecolor=SURFACE, linewidth=2)
    for i, (r, a, g) in enumerate(zip(rest, acc, de["regen_wh"])):
        ax.text(i, r + a + 12, f"regen {abs(g):.0f} Wh", ha="center", fontsize=8, color=INK2)
    ax.set_ylabel("Energy (Wh)")
    ax.set_ylim(0, (rest + acc).max() * 1.15)
    ax.legend(loc="upper left", frameon=False, fontsize=8, labelcolor=INK2)
    ax.set_title(f"Where each drive's energy went (car + driver ≈ {CAR_MASS_KG} kg)",
                 loc="left", fontsize=10, color=INK)
    fig.savefig(figdir / "energy_breakdown.png", dpi=110, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)

    # Battery vs controller current (10 s averages, high-res period).
    pts = np.array(gap["points"]) if gap["points"] else np.empty((0, 2))
    fig, ax = plt.subplots(figsize=(6, 5), facecolor=SURFACE)
    _style(ax)
    if len(pts):
        ax.scatter(pts[:, 0], pts[:, 1], s=14, color=S1, alpha=.5, lw=0)
        hi = max(pts.max(), 10)
        ax.plot([0, hi], [0, hi], color=MUTED, lw=1)
        f = gap["fit_battery_a_vs_controller_a"]
        ax.plot([0, hi], [f["offset_a"], f["slope"] * hi + f["offset_a"]], color=S2, lw=2)
        ax.text(hi * 0.04, hi * 0.92,
                f"battery = {f['slope']:.2f} × controller + {f['offset_a']:.1f} A\n"
                f"(≈ {gap['extra_power_w_at_pack_v']} W extra while driving)",
                fontsize=9, color=INK, va="top")
        ax.text(hi * 0.98, hi * 0.9, "equal", fontsize=8, color=MUTED, ha="right")
    ax.set_xlabel("Controller input current (A, 10 s average)", color=INK2, fontsize=9)
    ax.set_ylabel("Battery output current (A)")
    ax.set_title("Battery vs controller current", loc="left", fontsize=10, color=INK)
    fig.savefig(figdir / "battery_vs_controller.png", dpi=110, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)


# --- Dashboard payload ------------------------------------------------------------

def md_to_html(md):
    """Tiny Markdown subset for the day notes: #/## headings, - bullets, **bold**, paragraphs."""
    import html
    import re
    out, para, items = [], [], []

    def inline(t):
        return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html.escape(t))

    def flush():
        if para:
            out.append(f"<p>{inline(' '.join(para))}</p>"); para.clear()
        if items:
            out.append("<ul>" + "".join(f"<li>{inline(i)}</li>" for i in items) + "</ul>"); items.clear()
    for line in md.splitlines():
        t = line.strip()
        if not t:
            flush()
        elif t.startswith("#"):
            flush()
            lvl = min(len(t) - len(t.lstrip("#")) + 1, 4)
            out.append(f"<h{lvl}>{inline(t.lstrip('#').strip())}</h{lvl}>")
        elif t.startswith(("- ", "* ")):
            if para:
                flush()
            items.append(t[2:])
        elif items and line.startswith("  "):
            items[-1] += " " + t
        else:
            para.append(t)
    flush()
    return "\n".join(out)


def day_notes(day):
    """The team's own notes for the day (notes/<date>.md), if written."""
    f = Path(__file__).parent / "notes" / f"{day}.md"
    return f.read_text(encoding="utf-8") if f.exists() else ""


def dashboard_payload(day, w, drives, ov, eff_tbl, model, cap, health, be,
                      drive_eff=None, gap=None, notes=""):
    cols = ["speed_mph", "throttle_pct", "mc_power_w", "pack_power_w", "motor_temp_c",
            "mc_temp_c", "cell_temp_max_c", "soc_pct", "mc_phase_current_a",
            "cell_v_delta_mv", "pack_voltage_v"]
    active = w[controller_active(w)]
    s = w.loc[active.index[0]:active.index[-1], cols].resample("5s").agg(
        {c: ("max" if c in ("mc_phase_current_a", "cell_v_delta_mv") else "mean") for c in cols})
    series = {"t": [int(x.timestamp()) for x in s.index]}
    for c in cols:
        series[c] = [None if pd.isna(v) else round(float(v), 1) for v in s[c]]

    def rec(df):
        return json.loads(df.to_json(orient="records", date_format="iso"))

    return {"date": day, "series": series, "drives": rec(drives), "overloads": rec(ov),
            "efficiency": rec(eff_tbl), "thermal": model, "capacity": cap, "health": health,
            "break_even": be,
            "drive_eff": rec(drive_eff) if drive_eff is not None else [],
            "power_gap": gap, "notes_html": md_to_html(notes) if notes else "",
            "car_mass_kg": CAR_MASS_KG}


# --- Driver --------------------------------------------------------------------------

def analyse(day_dir):
    day = day_dir.name
    w = pd.read_parquet(day_dir / "wide_1s.parquet")
    long = pd.read_parquet(day_dir / "readings_long.parquet")
    ev = pd.read_csv(day_dir / "events.csv")
    ev["time"] = pd.to_datetime(ev["time"], utc=True).dt.tz_convert(w.index.tz)
    drives = pd.read_csv(day_dir / "drives.csv", parse_dates=["start", "end"])
    out = day_dir / "analysis"
    figdir = day_dir / "figures"
    out.mkdir(exist_ok=True)
    figdir.mkdir(exist_ok=True)

    ov = overloads(w, ev)
    ov.to_csv(out / "overloads.csv", index=False)
    model, thermal = thermal_model(w)
    thermal.round(2).to_csv(out / "thermal_fit.csv")
    (out / "thermal_model.json").write_text(json.dumps(model, indent=2))
    drive_eff = drive_efficiency(w, drives)
    drive_eff.to_csv(out / "drive_efficiency.csv", index=False)
    excluded = drive_eff.loc[drive_eff["brake_drag_suspected"], "drive"].tolist()
    mask = fit_mask(w, drives, excluded)
    eff_tbl, eff_pts = efficiency_vs_speed(w, mask=mask)
    eff_tbl.to_csv(out / "efficiency_vs_speed.csv", index=False)
    gap = power_gap(w, long)
    (out / "power_gap.json").write_text(json.dumps(gap, indent=2))
    be = solar_break_even(w, mask=mask, excluded=excluded)
    (out / "solar_break_even.json").write_text(json.dumps(be, indent=2))
    break_even_figure(be, eff_tbl, figdir)
    cap = capacity(w)
    (out / "capacity.json").write_text(json.dumps(cap, indent=2))
    health = pack_health(long, w)
    (out / "pack_health.json").write_text(json.dumps(health, indent=2))

    for _, d in drives.iterrows():
        drive_figure(w, d, ov, figdir / f"drive_{d['drive']:02d}.png")
    if len(drives):
        span = {"start": drives["start"].min() - pd.Timedelta("5min"),
                "end": drives["end"].max() + pd.Timedelta("5min")}
        drive_figure(w, span, ov, figdir / "day_overview.png",
                     title=f"{day} · {len(drives)} drives · {drives['distance_mi'].sum():.1f} mi · "
                           f"{len(ov)} controller errors (red lines) · 15 s averages",
                     smooth=15)
    analysis_figures(w, eff_tbl, eff_pts, thermal, model, ov, figdir)
    efficiency_figures(drive_eff, gap, figdir)

    notes = day_notes(day)
    payload = dashboard_payload(day, w, drives, ov, eff_tbl, model, cap, health, be,
                                drive_eff, gap, notes)
    data = json.dumps(payload, separators=(",", ":"))
    (out / "dashboard.json").write_text(data)
    tpl = (Path(__file__).parent / "dashboard_template.html").read_text(encoding="utf-8")
    (day_dir / "dashboard.html").write_text(tpl.replace("__DATA__", data.replace("</", r"<\/")),
                                            encoding="utf-8")

    row = {"date": day, "distance_mi": drives["distance_mi"].sum(),
           "wh_per_mi": round(drives["mc_energy_wh"].sum() / drives["distance_mi"].sum(), 1),
           "controller_errors": len(ov), "max_motor_temp_c": w["motor_temp_c"].max(),
           "thermal_tau_min": round(model["tau_s"] / 60, 1),
           "solar_w": be["solar"]["solar_w"],
           "break_even_mph": next((r["break_even_mph"] for r in be["break_even"]
                                   if r["solar_w"] == be["solar"]["solar_w"]), None),
           **({"usable_capacity_ah": cap["usable_capacity_ah"]} if cap else {}),
           **({k: health[k] for k in ("pack_resistance_mohm", "weakest_cell_resistance_mohm",
                                      "cell_delta_at_rest_mv_median",
                                      "cell_delta_over_50a_mv_median")} if health else {})}
    hist_path = PROCESSED / "health_history.csv"
    hist = pd.read_csv(hist_path) if hist_path.exists() else pd.DataFrame()
    hist = pd.concat([hist[hist.get("date", pd.Series(dtype=str)) != day] if len(hist) else hist,
                      pd.DataFrame([row])], ignore_index=True).sort_values("date")
    hist.to_csv(hist_path, index=False)

    print(f"== {day}")
    with pd.option_context("display.width", 250, "display.max_columns", 30):
        print(ov.to_string(index=False))
        print(eff_tbl.to_string(index=False))
        print(drive_eff.to_string(index=False))
    for name, obj in [("thermal", model), ("capacity", cap), ("health", health),
                      ("break_even", be), ("power_gap", {k: v for k, v in gap.items() if k != "points"})]:
        print(name, json.dumps(obj, indent=1, default=str))


def main():
    days = sys.argv[1:] or sorted(p.name for p in PROCESSED.iterdir()
                                  if (p / "wide_1s.parquet").exists())
    for d in days:
        analyse(PROCESSED / d)


if __name__ == "__main__":
    main()
