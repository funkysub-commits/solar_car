"""Telemetry CSV log + export server for the Solar Car CANbus add-on.

Two independent pieces, both pure stdlib so they are unit-testable on a PC:

  TelemetryLog   -- appends one wide CSV row per tick (both devices, every
                    sensor field, plus the health columns) to a daily file
                    under LOG_DIR (/share/solarcar_telemetry on the Pi, so the
                    Samba / SSH add-ons can see it too). Files are
                    line-flushed so a power cut loses at most one row.

  export server  -- tiny HTTP server (default port 8099, host network) with a
                    one-page UI to pick a time window and download it as a
                    single CSV. Reachable directly at http://<pi-ip>:8099/ and
                    through HA ingress (sidebar panel), so every link in the
                    page is RELATIVE -- ingress rewrites the path prefix.

Timestamps are UTC ISO-8601 with milliseconds and a trailing Z, fixed width,
so range filtering is a plain string comparison. The Pi has no RTC: the wall
clock can step when NTP syncs after the hotspot connects, so every row also
carries uptime_s (time.monotonic) to spot the step.
"""
import os
import io
import csv
import json
import time
import html
import logging
import threading
import urllib.parse
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG_DIR = os.environ.get("TELEMETRY_LOG_DIR", "/share/solarcar_telemetry")
FILE_PREFIX = "telemetry-"
FILE_EXT = ".csv"
META_COLUMNS = ["time_utc", "uptime_s", "interval_s",
                "canadapter_status", "ezkontrol_status", "bestgo_status"]


def utc_now_iso(now=None):
    """Fixed-width UTC timestamp: 2026-10-03T14:05:09.123Z"""
    dt = now or datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def parse_time(s):
    """Accept epoch seconds, or ISO-8601 (naive = UTC, 'Z' or offset ok, a
    bare date means midnight UTC). Returns an aware UTC datetime or None."""
    if s is None or s == "":
        return None
    s = s.strip()
    try:
        return datetime.fromtimestamp(float(s), tz=timezone.utc)
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class TelemetryLog:
    """Append-only daily CSV writer.

    columns: ordered list of data columns AFTER the META_COLUMNS (e.g.
    'ezkontrol_bus_voltage', ...). If an existing day file has a different
    header (sensor table changed between versions) a new file with a numeric
    suffix is started rather than corrupting the old one.
    """

    def __init__(self, columns, log_dir=LOG_DIR, keep_days=30):
        self.columns = list(columns)
        self.header = META_COLUMNS + self.columns
        self.log_dir = log_dir
        self.keep_days = keep_days
        self._fh = None
        self._path = None
        self._day = None
        self.rows_today = 0
        self._lock = threading.Lock()
        self.last_error = None

    # -- file management ----------------------------------------------------
    def _open_for_day(self, day):
        os.makedirs(self.log_dir, exist_ok=True)
        base = os.path.join(self.log_dir, f"{FILE_PREFIX}{day}")
        n = 0
        while True:
            path = base + (f"-{n}" if n else "") + FILE_EXT
            if not os.path.exists(path):
                fh = open(path, "a", newline="", buffering=1, encoding="utf-8")
                csv.writer(fh).writerow(self.header)
                self.rows_today = 0
                break
            with open(path, "r", newline="", encoding="utf-8") as f:
                first = f.readline().rstrip("\r\n")
                if first == ",".join(self.header):
                    self.rows_today = max(0, sum(1 for _ in f))
                    fh = open(path, "a", newline="", buffering=1, encoding="utf-8")
                    break
            n += 1       # header mismatch: start a sibling file
        self._fh, self._path, self._day = fh, path, day
        logging.info(f"telemetry log: writing {path} ({self.rows_today} rows so far)")

    def close(self):
        with self._lock:
            if self._fh:
                try:
                    self._fh.close()
                except OSError:
                    pass
            self._fh = None

    @property
    def path(self):
        return self._path

    # -- writing -------------------------------------------------------------
    def write(self, uptime_s, interval_s, statuses, values, now=None):
        """Append one row. `interval_s` = the update interval in force;
        `statuses` = (adapter, ezkontrol, bestgo) 1/0;
        `values` maps column name -> value (missing -> blank). Errors (SD
        card full / share unmounted) are logged once and never raised."""
        now = now or datetime.now(timezone.utc)
        day = now.strftime("%Y-%m-%d")
        row = [utc_now_iso(now), f"{uptime_s:.3f}", f"{interval_s:g}",
               *[int(s) for s in statuses]]
        for c in self.columns:
            v = values.get(c)
            row.append("" if v is None else v)
        with self._lock:
            try:
                if self._fh is None or day != self._day:
                    if self._fh:
                        self._fh.close()
                    self._open_for_day(day)
                    self.prune()
                csv.writer(self._fh).writerow(row)
                self.rows_today += 1
                if self.last_error:
                    logging.info("telemetry log: writing again")
                    self.last_error = None
            except OSError as e:
                if str(e) != self.last_error:
                    logging.error(f"telemetry log: write failed: {e}")
                    self.last_error = str(e)
                self._fh = None

    def prune(self):
        """Delete day files older than keep_days (by the date in the name)."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.keep_days)).strftime("%Y-%m-%d")
        for name, day, _ in list_files(self.log_dir):
            if day < cutoff:
                try:
                    os.remove(os.path.join(self.log_dir, name))
                    logging.info(f"telemetry log: pruned {name}")
                except OSError as e:
                    logging.warning(f"telemetry log: prune {name}: {e}")


# ---------------------------------------------------------------------------
# Reading / exporting
# ---------------------------------------------------------------------------
def list_files(log_dir=LOG_DIR):
    """[(name, 'YYYY-MM-DD', size_bytes)] sorted oldest first."""
    out = []
    try:
        names = os.listdir(log_dir)
    except OSError:
        return out
    for name in names:
        if not (name.startswith(FILE_PREFIX) and name.endswith(FILE_EXT)):
            continue
        day = name[len(FILE_PREFIX):len(FILE_PREFIX) + 10]
        try:
            datetime.strptime(day, "%Y-%m-%d")
            size = os.path.getsize(os.path.join(log_dir, name))
        except (ValueError, OSError):
            continue
        out.append((name, day, size))
    out.sort(key=lambda t: (t[1], t[0]))
    return out


def iter_rows(start, end, log_dir=LOG_DIR):
    """Yield (header, row) for every logged row with start <= time < end
    (aware datetimes; None = unbounded). The header is yielded with each row
    so callers can notice a column-set change between files."""
    s_key = utc_now_iso(start) if start else ""
    e_key = utc_now_iso(end) if end else "~"          # '~' sorts after digits
    s_day = s_key[:10] if start else ""
    e_day = e_key[:10] if end else "~"
    for name, day, _ in list_files(log_dir):
        if day < s_day or day > e_day:
            continue
        try:
            with open(os.path.join(log_dir, name), newline="", encoding="utf-8") as f:
                rd = csv.reader(f)
                header = next(rd, None)
                if not header:
                    continue
                for row in rd:
                    if not row:
                        continue
                    t = row[0]
                    if t < s_key or t >= e_key:
                        continue
                    yield header, row
        except OSError as e:
            logging.warning(f"telemetry export: cannot read {name}: {e}")


def export_csv(start, end, out, log_dir=LOG_DIR):
    """Write one merged CSV for [start, end) to the text stream `out`.
    Returns the number of data rows written. If the column set changes
    between files, a fresh header line is emitted before the first row of
    the new shape (so nothing is silently misaligned)."""
    w = csv.writer(out, lineterminator="\n")
    cur_header = None
    n = 0
    for header, row in iter_rows(start, end, log_dir):
        if header != cur_header:
            w.writerow(header)
            cur_header = header
        w.writerow(row)
        n += 1
    if cur_header is None:
        w.writerow(META_COLUMNS)       # empty export still has a header
    return n


# ---------------------------------------------------------------------------
# HTTP export server
# ---------------------------------------------------------------------------
PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Solar Car telemetry export</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
 body{font-family:system-ui,sans-serif;margin:16px;max-width:720px;color:#222}
 h1{font-size:1.3em} h2{font-size:1.05em;margin-top:1.4em}
 .mode{padding:6px 10px;border-radius:6px;display:inline-block;background:#eee}
 .quick a{display:inline-block;margin:4px 6px 4px 0;padding:6px 10px;border:1px solid #888;
          border-radius:6px;text-decoration:none;color:#124}
 table{border-collapse:collapse;width:100%%} td,th{padding:4px 8px;border-bottom:1px solid #ddd;text-align:left}
 label{display:block;margin:6px 0} input[type=datetime-local]{font-size:1em}
 button{padding:6px 14px;font-size:1em}
 small{color:#666}
</style></head><body>
<h1>Solar Car telemetry export</h1>
<p><span class="mode">Update interval: <b>%(interval)s s</b> <small>(%(source)s)</small></span>
 &nbsp; <small>%(rows_today)s rows today &middot; %(n_files)s day file(s), %(total_mb).1f MB</small></p>

<h2>Download the last&hellip;</h2>
<p class="quick">
 <a href="export?minutes=15">15 min</a><a href="export?hours=1">1 h</a><a href="export?hours=2">2 h</a>
 <a href="export?hours=6">6 h</a><a href="export?hours=12">12 h</a><a href="export?hours=24">24 h</a>
 <a href="export?days=7">7 days</a><a href="export">everything</a>
</p>

<h2>Or pick a window</h2>
<form id="f" action="export" method="get" onsubmit="return go()">
 <label>From <input type="datetime-local" id="s" step="1"></label>
 <label>To &nbsp;&nbsp;<input type="datetime-local" id="e" step="1"> <small>(blank = now)</small></label>
 <input type="hidden" name="start" id="hs"><input type="hidden" name="end" id="he">
 <button type="submit">Download CSV</button>
 <small>Times are your browser's local time; the CSV itself is in UTC.</small>
</form>
<script>
function go(){var s=document.getElementById('s').value,e=document.getElementById('e').value;
 document.getElementById('hs').value=s?new Date(s).toISOString():'';
 document.getElementById('he').value=e?new Date(e).toISOString():'';return true;}
</script>

<h2>Day files</h2>
<table><tr><th>Day (UTC)</th><th>Size</th><th></th></tr>%(files)s</table>
<p><small>Logged by the Solar Car CANbus add-on to <code>%(log_dir)s</code>. One row per log tick with
every EZkontrol and BESTGO sensor; <code>*_status</code> columns are 1 when that device was alive;
<code>interval_s</code> is the update interval that was in force. Lower <b>CANbus Update Interval</b>
in Home Assistant to log (and push) faster.</small></p>
</body></html>
"""


def make_handler(state, log_dir):
    """Build a request handler bound to `state` -- a callable returning a dict
    with keys interval, interval_source, rows_today (so the page shows live info
    without the server importing can_reader)."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "SolarCarTelemetry/1"

        def log_message(self, fmt, *args):     # route to logging, quietly
            logging.debug("export http: " + fmt, *args)

        def _send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = urllib.parse.urlsplit(self.path)
            path = url.path.rstrip("/") or "/"
            q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
            try:
                if path == "/":
                    return self._index()
                if path == "/export":
                    return self._export(q)
                if path == "/status":
                    return self._send(200, json.dumps(self._status()), "application/json")
                if path.startswith("/files/"):
                    return self._file(path[len("/files/"):])
                self._send(404, "not found", "text/plain")
            except Exception as e:          # never let one request kill the server
                logging.exception("export http: request failed")
                try:
                    self._send(500, f"error: {e}", "text/plain")
                except OSError:
                    pass

        def _status(self):
            st = dict(state())
            files = list_files(log_dir)
            st.update(log_dir=log_dir, files=len(files),
                      bytes=sum(s for _, _, s in files))
            return st

        def _index(self):
            st = self._status()
            files = list_files(log_dir)
            rows = "".join(
                f"<tr><td>{html.escape(day)}</td><td>{size/1e6:.2f} MB</td>"
                f"<td><a href=\"files/{urllib.parse.quote(name)}\">{html.escape(name)}</a></td></tr>"
                for name, day, size in reversed(files)) or "<tr><td colspan=3>no log files yet</td></tr>"
            body = PAGE % dict(
                interval=f"{st['interval']:g}" if isinstance(st.get("interval"), (int, float)) else "?",
                source=html.escape(str(st.get("interval_source", "add-on options"))),
                rows_today=st.get("rows_today", 0),
                n_files=len(files), total_mb=st["bytes"] / 1e6,
                files=rows, log_dir=html.escape(log_dir))
            self._send(200, body)

        def _export(self, q):
            now = datetime.now(timezone.utc)
            start = parse_time(q.get("start"))
            end = parse_time(q.get("end"))
            span = None
            for key, unit in (("minutes", 60), ("hours", 3600), ("days", 86400)):
                if q.get(key):
                    try:
                        span = timedelta(seconds=float(q[key]) * unit)
                    except ValueError:
                        return self._send(400, f"bad {key}", "text/plain")
            if span is not None:
                end = end or now
                start = end - span
            if start and end and end <= start:
                return self._send(400, "end must be after start", "text/plain")
            buf = io.StringIO()
            n = export_csv(start, end, buf, log_dir)
            tag = lambda d: d.strftime("%Y%m%d-%H%M%SZ") if d else "all"
            fname = f"solarcar_telemetry_{tag(start)}_{tag(end or now)}.csv"
            self._send(200, buf.getvalue(), "text/csv; charset=utf-8",
                       {"Content-Disposition": f'attachment; filename="{fname}"',
                        "X-Rows": str(n)})

        def _file(self, name):
            name = urllib.parse.unquote(name)
            if "/" in name or "\\" in name or not (name.startswith(FILE_PREFIX) and name.endswith(FILE_EXT)):
                return self._send(404, "not found", "text/plain")
            full = os.path.join(log_dir, name)
            try:
                with open(full, "rb") as f:
                    data = f.read()
            except OSError:
                return self._send(404, "not found", "text/plain")
            self._send(200, data, "text/csv; charset=utf-8",
                       {"Content-Disposition": f'attachment; filename="{name}"'})

    return Handler


def start_export_server(port, state, log_dir=LOG_DIR):
    """Start the export HTTP server on a daemon thread. Returns the server,
    or None if the port can't be bound (logged, never fatal)."""
    try:
        srv = ThreadingHTTPServer(("0.0.0.0", port), make_handler(state, log_dir))
    except OSError as e:
        logging.error(f"telemetry export server: cannot bind port {port}: {e}")
        return None
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, name="telemetry-export", daemon=True).start()
    logging.info(f"telemetry export server listening on :{port} (and via HA ingress)")
    return srv
