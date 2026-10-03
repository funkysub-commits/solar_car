#!/usr/bin/env python3
"""Download solar-car telemetry for a time window as one CSV, from the PC.

Two sources, picked with --source (default: try `addon`, fall back to `history`):

  addon    The CANbus add-on's export endpoint (0.12.0+), http://<pi>:8099/export.
           The add-on reads HA's recorder history itself (through the
           Supervisor) and returns one wide CSV: first row = state of every
           entity at the window start, then one row per change with the other
           columns carried forward. Needs NO token from the PC. Entities that
           don't exist (yet) are blank columns.

  history  The same thing done from the PC straight against HA's REST API
           (GET /api/history/period) -- for when the add-on isn't running.
           Needs an HA long-lived token in the HA_TOKEN environment variable
           (Profile -> Security -> Long-lived access tokens); never write the
           token into the repo.

Either way the data is only as fine as the update interval was at the time
and only as old as HA's recorder keeps (default purge is 10 days).

Usage:
    python CANbus_data/tools/export_telemetry.py --hours 2
    python CANbus_data/tools/export_telemetry.py --start 2026-10-03T13:00 --end 2026-10-03T15:30 -o run.csv
    python CANbus_data/tools/export_telemetry.py --source history --hours 6
    python CANbus_data/tools/export_telemetry.py --host 192.168.0.47 --minutes 30

Times are LOCAL time unless you add a Z / offset; the CSV is written in UTC
(time_utc column) to match the add-on's files. The Pi address defaults to
the host in status.json's Pi.IP (--host overrides).
"""
import argparse
import csv
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "CANbus_data"))
from solarcar_can.bestgo import BG_SENSORS      # noqa: E402
from solarcar_can.ezkontrol import EZ_SENSORS   # noqa: E402

EXPORT_PORT = 8099
HA_PORT = 8123
HEALTH = ["canadapter_status", "ezkontrol_status", "bestgo_status"]
COLUMNS = (HEALTH + [f"ezkontrol_{k}" for k in EZ_SENSORS]
           + [f"bestgo_{k}" for k in BG_SENSORS])


def default_host():
    try:
        with open(os.path.join(ROOT, "status.json")) as f:
            return re.sub(r"^https?://|:\d+$", "", json.load(f)["Pi"]["IP"])
    except (OSError, KeyError, ValueError):
        return None


def parse_local(s):
    """ISO string -> aware UTC datetime. Naive input is LOCAL time here (the
    person typing it is looking at their own clock)."""
    if not s:
        return None
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.astimezone()            # attach the PC's local zone
    return dt.astimezone(timezone.utc)


def window(args):
    now = datetime.now(timezone.utc)
    start, end = parse_local(args.start), parse_local(args.end)
    span = None
    if args.minutes:
        span = timedelta(minutes=args.minutes)
    if args.hours:
        span = timedelta(hours=args.hours)
    if args.days:
        span = timedelta(days=args.days)
    if span is not None:
        end = end or now
        start = end - span
    if start is None:
        sys.exit("give --start or one of --minutes/--hours/--days")
    end = end or now
    if end <= start:
        sys.exit("end must be after start")
    return start, end


def iso_z(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


# ---------------------------------------------------------------------------
def from_addon(host, start, end, out):
    url = (f"http://{host}:{EXPORT_PORT}/export?"
           + urllib.parse.urlencode({"start": iso_z(start), "end": iso_z(end)}))
    with urllib.request.urlopen(url, timeout=60) as r:
        rows = r.headers.get("X-Rows")
        with open(out, "wb") as f:
            f.write(r.read())
    return int(rows) if rows else None


# ---------------------------------------------------------------------------
def from_history(host, start, end, out, token):
    if not token:
        sys.exit("--source history needs an HA long-lived token in the HA_TOKEN environment variable")
    entities = ",".join(f"sensor.{c}" for c in COLUMNS)
    url = (f"http://{host}:{HA_PORT}/api/history/period/{urllib.parse.quote(start.isoformat())}?"
           + urllib.parse.urlencode({"end_time": end.isoformat(),
                                     "filter_entity_id": entities,
                                     "minimal_response": "", "no_attributes": ""}))
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.load(r)

    # data = [[states of entity A...], [states of entity B...], ...]
    # minimal_response drops entity_id from all but the first state of each list.
    events = []     # (time_utc_iso, column, state)
    for series in data:
        if not series:
            continue
        col = series[0]["entity_id"].split(".", 1)[1]
        for st in series:
            t = st.get("last_changed") or st.get("last_updated")
            dt = datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(timezone.utc)
            events.append((iso_z(dt), col, st["state"]))
    events.sort()

    # Pivot: one row per distinct timestamp, carrying the latest value of every
    # column forward. The first row of each column may be a pre-window state
    # (HA returns the value in force at `start`) -- that's wanted.
    cur = {c: "" for c in COLUMNS}
    n = 0
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["time_utc", "source"] + COLUMNS)
        i = 0
        while i < len(events):
            t = events[i][0]
            while i < len(events) and events[i][0] == t:
                _, col, state = events[i]
                cur[col] = "" if state in ("unknown", "unavailable") else state
                i += 1
            w.writerow([t, "ha_history"] + [cur[c] for c in COLUMNS])
            n += 1
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=default_host(), help="Pi address (default: status.json)")
    ap.add_argument("--source", choices=["auto", "addon", "history"], default="auto")
    ap.add_argument("--start"), ap.add_argument("--end")
    ap.add_argument("--minutes", type=float), ap.add_argument("--hours", type=float), ap.add_argument("--days", type=float)
    ap.add_argument("-o", "--out", help="output CSV (default: solarcar_telemetry_<start>_<end>.csv)")
    args = ap.parse_args()
    if not args.host:
        sys.exit("no Pi address: pass --host or fill status.json")

    start, end = window(args)
    tag = lambda d: d.strftime("%Y%m%d-%H%M%SZ")
    out = args.out or f"solarcar_telemetry_{tag(start)}_{tag(end)}.csv"
    print(f"window (UTC): {iso_z(start)} -> {iso_z(end)}   pi={args.host}")

    if args.source in ("auto", "addon"):
        try:
            n = from_addon(args.host, start, end, out)
            print(f"add-on log: {n} rows -> {out}")
            if n == 0 and args.source == "auto":
                print("add-on log is empty for this window (before 0.10.0 / pruned?); trying HA history")
            else:
                return
        except Exception as e:
            if args.source == "addon":
                sys.exit(f"add-on export failed: {e}")
            print(f"add-on export unavailable ({e}); trying HA history")

    n = from_history(args.host, start, end, out, os.environ.get("HA_TOKEN"))
    print(f"HA history: {n} change-rows -> {out}")


if __name__ == "__main__":
    main()
