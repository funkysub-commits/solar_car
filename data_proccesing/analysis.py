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

from telemetry import PROCESSED

# Chart palette (validated reference palette, light mode).
INK, INK2, MUTED = "#0b0b0b", "#52514e", "#898781"
GRID, SURFACE = "#e1e0d9", "#fcfcfb"
S1, S2, S3, S4 = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
CRITICAL = "#d03b3b"

LAUNCH_MPH = 3.0          # "pulling away" = was below this in the lookback
LOOKBACK = pd.Timedelta(seconds=20)
MOTOR_TEMP_LIMIT_C = 130  # hottest seen when it stalled; treat as the ceiling


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
        reversals = int((np.sign(pre["motor_rpm"].replace(0, np.nan).dropna()).diff() != 0).sum() - 1)
        rows.append({
            "time": t, "error": state,
            "context": context,
            "speed_mph": w["speed_mph"].asof(sec),
            "max_throttle_pct": pre["throttle_pct"].max(),
            "secs_at_full_throttle": int((pre["throttle_pct"] >= 99).sum()),
            "max_phase_current_a": pre["mc_phase_current_a"].max(),
            "max_bus_current_a": pre["mc_bus_current_a"].max(),
            "rpm_reversals": max(reversals, 0),   # wheel rocking back and forth
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
    d = w.loc[w["mc_online"] == 1].copy() if w["mc_online"].notna().any() else w.copy()
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

def efficiency_vs_speed(w, window=8):
    """Wh/mi in steady cruising (speed within a 3 mph band over `window` s)."""
    sp = w["speed_mph"]
    roll = sp.rolling(window)
    steady = ((roll.max() - roll.min()) < 3.0) & (roll.min() > 3) \
        & w["mc_power_w"].rolling(window).count().eq(window)
    p = w["mc_power_w"].rolling(window).mean()
    v = roll.mean()
    pts = pd.DataFrame({"speed": v[steady], "power_w": p[steady]})
    pts = pts.iloc[::window]  # non-overlapping windows
    pts["wh_per_mi"] = pts["power_w"] / pts["speed"]
    bins = np.arange(0, 45, 5)
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
# with the controller idle: there, pack current = solar in - accessory load.
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


def road_load(w, n_boot=200, seed=0, max_accel=0.15):
    """Fit P_controller = c0 + c1*v + c3*v^3 + m*a*v (all >= 0) on 5 s smoothed data.

    c0 ~ controller/motor no-load loss, c1*v ~ rolling resistance (+ average
    grade), c3*v^3 ~ aero drag, m ~ effective mass (kg). v in m/s.
    Only near-steady samples (|a| < max_accel) are used, with a robust loss so
    hill climbs and launches don't drag the cruise curve up.
    """
    r = w[["speed_mph", "mc_power_w"]].rolling(5, center=True).mean()
    v = r["speed_mph"] * MPS
    a = (v.shift(-2) - v.shift(2)) / 4
    m = ((r["speed_mph"] > 5) & r["mc_power_w"].notna() & a.notna()
         & (w["mc_online"] == 1) & (a.abs() < max_accel))
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


def break_even(c, solar_w, aux_w):
    """Highest steady speed whose cruise power + aux load <= solar."""
    grid = np.arange(0, 60.01, 0.1)
    ok = grid[cruise_power(c, grid) + aux_w <= solar_w]
    return float(ok.max()) if len(ok) else 0.0


def solar_break_even(w, aux_w=20):
    c, boots, r2, n = road_load(w)
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
        "rolling_plus_grade_force_n": round(c[1], 1),
        "drag_area_cda_m2": round(c[2] / (0.5 * 1.2), 2),
        "r2": r2, "points": n, "aux_load_w_assumed": aux_w,
        "solar": sol, "break_even": table, "cruise_curve": curve,
        "note": ("Break-even = fastest steady speed where cruise power + aux load <= solar, "
                 "i.e. range limited only by daylight. Average for the route driven "
                 "(grade is folded into c1). p10/p90 from a 60 s block bootstrap."),
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
                 "pack_vs_mc_current_ratio > 1 is aux load + sensor calibration difference."),
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


def drive_figure(w, drive, ov, path):
    s = w.loc[pd.Timestamp(drive["start"]) - pd.Timedelta("30s"):
              pd.Timestamp(drive["end"]) + pd.Timedelta("30s")]
    t = s.index.tz_localize(None)
    fig, axes = plt.subplots(4, 1, figsize=(11, 8.5), sharex=True,
                             gridspec_kw={"hspace": 0.18}, facecolor=SURFACE)
    panels = [
        ("Speed (mph)", [("speed_mph", S1, None)]),
        ("Power (W)", [("mc_power_w", S2, "controller"), ("pack_power_w", S1, "battery")]),
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
    fig.suptitle(f"Drive {drive['drive']} · {pd.Timestamp(drive['start']):%d %b %H:%M} · "
                 f"{drive['distance_mi']} mi · {drive['wh_per_mi']} Wh/mi · "
                 f"{len(errs)} controller error(s)", x=0.07, ha="left", color=INK, fontsize=11)
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
    ax.set_xlim(0, 40)
    ax.set_ylim(0, 3000)
    ax.set_xlabel("Steady speed (mph)", color=INK2, fontsize=9)
    ax.set_ylabel("Power needed (W)")
    ax.set_title("Solar break-even: line = road-load fit + aux, dots = steady-speed medians, "
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


# --- Dashboard payload ------------------------------------------------------------

def dashboard_payload(day, w, drives, ov, eff_tbl, model, cap, health, be):
    cols = ["speed_mph", "throttle_pct", "mc_power_w", "pack_power_w", "motor_temp_c",
            "mc_temp_c", "cell_temp_max_c", "soc_pct", "mc_phase_current_a",
            "cell_v_delta_mv", "pack_voltage_v"]
    active = w[w["mc_online"] == 1]
    s = w.loc[active.index[0]:active.index[-1], cols].resample("5s").agg(
        {c: ("max" if c in ("mc_phase_current_a", "cell_v_delta_mv") else "mean") for c in cols})
    series = {"t": [int(x.timestamp()) for x in s.index]}
    for c in cols:
        series[c] = [None if pd.isna(v) else round(float(v), 1) for v in s[c]]

    def rec(df):
        return json.loads(df.to_json(orient="records", date_format="iso"))

    return {"date": day, "series": series, "drives": rec(drives), "overloads": rec(ov),
            "efficiency": rec(eff_tbl), "thermal": model, "capacity": cap, "health": health,
            "break_even": be}


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
    eff_tbl, eff_pts = efficiency_vs_speed(w)
    eff_tbl.to_csv(out / "efficiency_vs_speed.csv", index=False)
    be = solar_break_even(w)
    (out / "solar_break_even.json").write_text(json.dumps(be, indent=2))
    break_even_figure(be, eff_tbl, figdir)
    cap = capacity(w)
    (out / "capacity.json").write_text(json.dumps(cap, indent=2))
    health = pack_health(long, w)
    (out / "pack_health.json").write_text(json.dumps(health, indent=2))

    for _, d in drives.iterrows():
        drive_figure(w, d, ov, figdir / f"drive_{d['drive']:02d}.png")
    analysis_figures(w, eff_tbl, eff_pts, thermal, model, ov, figdir)

    payload = dashboard_payload(day, w, drives, ov, eff_tbl, model, cap, health, be)
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
    for name, obj in [("thermal", model), ("capacity", cap), ("health", health),
                      ("break_even", be)]:
        print(name, json.dumps(obj, indent=1, default=str))


def main():
    days = sys.argv[1:] or sorted(p.name for p in PROCESSED.iterdir()
                                  if (p / "wide_1s.parquet").exists())
    for d in days:
        analyse(PROCESSED / d)


if __name__ == "__main__":
    main()
