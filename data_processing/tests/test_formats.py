"""The new CSV export and the old .xls list must give the same processed data.

Builds a CSV in the add-on's export format (time_utc + one column per entity,
carried forward, blanks for unknown/unavailable) from the 2026-10-03 .xls and
checks both loaders agree.

    python -m pytest data_processing/tests -q
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import telemetry as tm  # noqa: E402

XLS = HERE / "raw" / "solarcar_raw_readings_2026-10-03.xls"
NAME_TO_ENTITY = {n: e for e, n in tm.ENTITY_TO_NAME.items()}


def to_export_csv(long, path, drop=()):
    """Write `long` the way telemetry_export.py writes HA history."""
    rows = long[~long["name"].isin(drop)].copy()
    rows["col"] = rows["name"].map(lambda n: NAME_TO_ENTITY.get(n, tm.snake(n)))
    rows["cell"] = np.where(rows["status"] != "ok", "",
                            rows["text"].fillna(rows["value"].map(
                                lambda v: "" if pd.isna(v) else f"{v:g}")))
    piv = rows.pivot_table(index="time", columns="col", values="cell", aggfunc="last")
    piv = piv.ffill().fillna("")
    piv.index = piv.index.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%S.%f").str[:-3] + "Z"
    piv.index.name = "time_utc"
    piv.to_csv(path)


@pytest.fixture(scope="module")
def xls_long():
    if not XLS.exists():
        pytest.skip("sample export not present")
    return tm.load_raw(XLS)


def test_csv_matches_xls(xls_long, tmp_path):
    csv = tmp_path / "export.csv"
    to_export_csv(xls_long, csv)
    a = tm.to_wide(xls_long)
    b = tm.to_wide(tm.load_raw(csv))
    common = a.index.intersection(b.index)
    assert len(common) > 0.99 * len(a)
    for col in ["speed_mph", "pack_current_a", "motor_temp_c", "soc_pct", "mc_power_w"]:
        x, y = a.loc[common, col], b.loc[common, col]
        both = x.notna() & y.notna()
        assert both.mean() > 0.95 * x.notna().mean(), col
        assert np.allclose(x[both], y[both], atol=1e-6), col
    da, db = tm.summarise_drives(a), tm.summarise_drives(b)
    assert list(da["distance_mi"]) == list(db["distance_mi"])


def test_events_survive(xls_long, tmp_path):
    csv = tmp_path / "export.csv"
    to_export_csv(xls_long, csv)
    ev = tm.build_events(tm.load_raw(csv))
    errs = ev[(ev["channel"] == "ezkontrol_errors") & ev["state"].notna()]
    assert errs["state"].str.contains("Overload").sum() == 9
    assert (ev["state"] == "Drive").any() and not (ev["state"] == "?(1)").any()


def test_speed_falls_back_to_rpm(xls_long, tmp_path):
    """A default export has no 'Adjusted Car Speed' - speed must still exist."""
    csv = tmp_path / "export.csv"
    to_export_csv(xls_long, csv, drop=("Adjusted Car Speed", "Car Speed", "Solar Car Speed"))
    w = tm.to_wide(tm.load_raw(csv))
    assert w["speed_mph"].notna().mean() > 0.3
    assert 25 < tm.summarise_drives(w)["distance_mi"].sum() < 32
