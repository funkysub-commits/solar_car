"""On-demand telemetry export for the Solar Car CANbus add-on.

Nothing is logged to disk. When someone asks for a time window, this pulls
the recorded history of every selected entity straight out of Home
Assistant (GET /api/history/period through the Supervisor proxy), pivots it
into one wide CSV (one row per timestamp at which anything changed, other
columns carried forward) and returns it as a download.

Served by a tiny stdlib HTTP server (port 8099, host network) with a
one-page UI, reachable directly at http://<pi-ip>:8099/ and through HA
ingress (sidebar panel) -- so every link in the page is RELATIVE.

Which entities go in the CSV is editable from the page: the default set is
the add-on's own sensors plus the configured extras; the user can untick
any of them and add any other HA entity (picked from a list of everything
HA has). The selection is saved in the add-on's /data (EntitySelection) so
it survives restarts and is shared by everyone who opens the page.

Entities that don't exist (HA restarted and the add-on hasn't pushed them
yet, a device never seen, the mph template sensor not installed...) simply
come back with no history: their column is still in the CSV, just blank.
'unknown' / 'unavailable' states are written as blanks too.

The recorder only keeps what changed, and only for its purge window
(HA default 10 days), so an export can't be finer than the update interval
that was in force at the time, nor older than the recorder's retention.
"""
import io
import os
import re
import csv
import json
import html
import logging
import threading
import urllib.parse
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

CHUNK = timedelta(hours=1)      # history is fetched in windows this long (bounded memory)
HISTORY_TIMEOUT = 120           # seconds per chunk request
SELECTION_FILE = os.environ.get("EXPORT_SELECTION_FILE", "/data/export_entities.json")
ENTITY_RE = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")


def utc_iso(dt):
    """Fixed-width UTC timestamp: 2026-10-03T14:05:09.123Z"""
    dt = dt.astimezone(timezone.utc)
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


def _parse_ha_time(t):
    return datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(timezone.utc)


def valid_entity_ids(ids):
    """De-duplicated, order-preserving list of well-formed entity ids."""
    out = []
    for e in ids or []:
        e = str(e).strip().lower()
        if ENTITY_RE.match(e) and e not in out:
            out.append(e)
    return out


class EntitySelection:
    """The list of entities an export covers, persisted as JSON.

    `default` is what the add-on proposes (its own sensors + configured
    extras). Without a saved file the selection IS the default; saving
    stores the user's full list (so unticked defaults stay out and added
    entities stay in). Thread-safe; file errors are logged, never raised."""

    def __init__(self, default, path=SELECTION_FILE):
        self.default = valid_entity_ids(default)
        self.path = path
        self._lock = threading.Lock()
        self._selected = None
        self._load()

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            sel = valid_entity_ids(data.get("selected"))
            self._selected = sel
            logging.info(f"export: loaded entity selection ({len(sel)} entities) from {self.path}")
        except FileNotFoundError:
            self._selected = None
        except (OSError, ValueError) as e:
            logging.warning(f"export: cannot read {self.path} ({e}); using defaults")
            self._selected = None

    @property
    def ids(self):
        with self._lock:
            return list(self._selected if self._selected is not None else self.default)

    @property
    def customised(self):
        with self._lock:
            return self._selected is not None

    def save(self, selected):
        sel = valid_entity_ids(selected)
        with self._lock:
            self._selected = sel
            try:
                os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
                with open(self.path, "w", encoding="utf-8") as f:
                    json.dump({"selected": sel}, f, indent=1)
            except OSError as e:
                logging.error(f"export: cannot save {self.path}: {e}")
                return False
        logging.info(f"export: entity selection saved ({len(sel)} entities)")
        return True

    def reset(self):
        with self._lock:
            self._selected = None
            try:
                os.remove(self.path)
            except FileNotFoundError:
                pass
            except OSError as e:
                logging.error(f"export: cannot remove {self.path}: {e}")
                return False
        logging.info("export: entity selection reset to defaults")
        return True


class HAHistory:
    """Thin client for HA's history + states REST API via the Supervisor proxy."""

    def __init__(self, ha_url, token):
        self.ha_url = ha_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"}
        self.session = requests.Session()

    def all_entities(self):
        """Every entity id HA currently has, sorted; None if HA can't be asked."""
        try:
            r = self.session.get(f"{self.ha_url}/api/states", headers=self.headers, timeout=15)
            if r.status_code != 200:
                return None
            return sorted(s["entity_id"] for s in r.json())
        except Exception as e:
            logging.debug(f"export: states fetch failed: {e}")
            return None

    def existing(self, entity_ids):
        """Subset of entity_ids that currently exist in HA; None if unknown."""
        have = self.all_entities()
        if have is None:
            return None
        have = set(have)
        return [e for e in entity_ids if e in have]

    def period(self, entity_ids, start, end):
        """Raw history for [start, end]: list of per-entity lists of states
        (HA omits entities with no history at all). Raises on HTTP error."""
        url = (f"{self.ha_url}/api/history/period/{urllib.parse.quote(start.isoformat())}?"
               + urllib.parse.urlencode({"end_time": end.isoformat(),
                                         "filter_entity_id": ",".join(entity_ids),
                                         "minimal_response": "", "no_attributes": ""}))
        r = self.session.get(url, headers=self.headers, timeout=HISTORY_TIMEOUT)
        if r.status_code != 200:
            raise RuntimeError(f"HA history HTTP {r.status_code}: {r.text[:200]}")
        return r.json()


def export_csv(ha, entity_ids, start, end, out, chunk=CHUNK):
    """Write one wide CSV for [start, end) to the text stream `out`.

    Columns: time_utc, then one per entity (entity id minus the domain; the
    full id is used if two entities would collide). The first row is the
    state of everything at `start` (what HA reports as in force then); after
    that one row per instant anything changed, other columns carried
    forward. Returns the number of data rows. Entities with no history
    (don't exist / never recorded) are blank.
    """
    if not entity_ids:
        csv.writer(out, lineterminator="\n").writerow(["time_utc"])
        return 0
    short = [e.split(".", 1)[1] for e in entity_ids]
    cols = [s if short.count(s) == 1 else e for s, e in zip(short, entity_ids)]
    col_of = dict(zip(entity_ids, cols))
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["time_utc"] + cols)
    cur = {c: "" for c in cols}
    n = 0
    first = True
    t = start
    end_key = utc_iso(end)
    while t < end:
        t2 = min(t + chunk, end)
        data = ha.period(entity_ids, t, t2)
        events = []        # (utc_iso, col, state)
        for series in data:
            if not series:
                continue
            col = col_of.get(series[0].get("entity_id"))
            if col is None:
                continue
            for st in series:
                ts = st.get("last_changed") or st.get("last_updated")
                if not ts:
                    continue
                events.append((utc_iso(_parse_ha_time(ts)), col, st.get("state")))
        events.sort()
        t_key = utc_iso(t)
        i = 0
        # States HA reports as "in force at chunk start" carry a timestamp
        # at/before the chunk start (a change exactly on the boundary comes
        # back in BOTH neighbouring chunks): fold them into the carried-
        # forward values without emitting a row.
        while i < len(events) and events[i][0] <= t_key:
            cur[events[i][1]] = _clean(events[i][2])
            i += 1
        if first:
            w.writerow([t_key] + [cur[c] for c in cols])
            n += 1
            first = False
        while i < len(events):
            ts = events[i][0]
            while i < len(events) and events[i][0] == ts:
                cur[events[i][1]] = _clean(events[i][2])
                i += 1
            if ts >= end_key:
                break
            w.writerow([ts] + [cur[c] for c in cols])
            n += 1
        t = t2
    return n


def _clean(state):
    return "" if state in (None, "unknown", "unavailable") else state


# ---------------------------------------------------------------------------
# HTTP export server
# ---------------------------------------------------------------------------
PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Solar Car telemetry export</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
 body{font-family:system-ui,sans-serif;margin:16px;max-width:760px;color:#222}
 h1{font-size:1.3em} h2{font-size:1.05em;margin-top:1.4em}
 .mode{padding:6px 10px;border-radius:6px;display:inline-block;background:#eee}
 .quick a{display:inline-block;margin:4px 6px 4px 0;padding:6px 10px;border:1px solid #888;
          border-radius:6px;text-decoration:none;color:#124}
 label{display:block;margin:6px 0} input[type=datetime-local],input[list]{font-size:1em}
 button{padding:6px 14px;font-size:1em;margin-right:6px}
 small{color:#666} .miss{color:#a40} .x{color:#a00;cursor:pointer;border:none;background:none;padding:0 4px;font-size:1em}
 #ents{columns:2;font-size:.9em;margin:4px 0} #ents label{display:block;margin:2px 0;break-inside:avoid}
 #ents label.added{font-style:italic}
 .bar{margin:10px 0} #msg{margin-left:8px;color:#064}
</style></head><body>
<h1>Solar Car telemetry export</h1>
<p><span class="mode">Update interval: <b>%(interval)s s</b> <small>(%(source)s)</small></span>
 &nbsp; <small id="exist"></small></p>

<h2>Download the last&hellip;</h2>
<p class="quick">
 <a href="export?minutes=15">15 min</a><a href="export?hours=1">1 h</a><a href="export?hours=2">2 h</a>
 <a href="export?hours=6">6 h</a><a href="export?hours=12">12 h</a><a href="export?hours=24">24 h</a>
 <a href="export?days=7">7 days</a>
</p>

<h2>Or pick a window</h2>
<form id="f" action="export" method="get" onsubmit="return go()">
 <label>From <input type="datetime-local" id="s" step="1" required></label>
 <label>To &nbsp;&nbsp;<input type="datetime-local" id="e" step="1"> <small>(blank = now)</small></label>
 <input type="hidden" name="start" id="hs"><input type="hidden" name="end" id="he">
 <button type="submit">Download CSV</button>
 <small>Times are your browser's local time; the CSV itself is in UTC.</small>
</form>

<h2>What goes in the CSV</h2>
<p><small>Tick the entities to include. Add anything else Home Assistant has in the box below
(start typing to search). <b>Save</b> keeps the list on the Pi for everyone; <b>Reset</b> goes
back to the add-on's own sensors. Entities marked <span class="miss">missing</span> don't exist in
Home Assistant right now (e.g. not pushed yet since a restart) -- they stay in the file as
blank columns.</small></p>
<div id="ents"><small>loading&hellip;</small></div>
<p class="bar"><input list="all" id="add" placeholder="sensor.something" size="32"><datalist id="all"></datalist>
 <button type="button" onclick="addEnt()">Add</button></p>
<p class="bar"><button type="button" onclick="save()">Save</button>
 <button type="button" onclick="resetSel()">Reset to defaults</button><span id="msg"></span></p>

<p><small>One CSV built on the spot from Home Assistant's recorded history: a <code>time_utc</code>
column plus one column per entity. The first row is the state of everything at the start of the
window, then one row every time any value changed, with the other columns carried forward.
Blank cells mean the entity had no value (unknown / unavailable / didn't exist yet). History only
exists as finely as the update interval was set at the time, and only as far back as Home
Assistant's recorder keeps (10 days by default).</small></p>

<script>
var S={selected:[],default:[],existing:null,all:[]};
function go(){var s=document.getElementById('s').value,e=document.getElementById('e').value;
 document.getElementById('hs').value=s?new Date(s).toISOString():'';
 document.getElementById('he').value=e?new Date(e).toISOString():'';return true;}
function esc(t){return t.replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
function render(){
 var box=document.getElementById('ents'),h='';
 var ids=S.default.slice();S.selected.forEach(function(e){if(ids.indexOf(e)<0)ids.push(e);});
 ids.forEach(function(e){
  var on=S.selected.indexOf(e)>=0,miss=S.existing&&S.existing.indexOf(e)<0,added=S.default.indexOf(e)<0;
  h+='<label class="'+(added?'added':'')+'"><input type="checkbox" data-e="'+esc(e)+'"'+(on?' checked':'')+'> '+esc(e)
    +(miss?' <span class="miss">(missing)</span>':'')
    +(added?' <button class="x" title="remove" onclick="rm(\\''+esc(e)+'\\')">&#10005;</button>':'')+'</label>';});
 box.innerHTML=h||'<small>no entities</small>';
 var n=S.existing?S.selected.filter(function(e){return S.existing.indexOf(e)>=0;}).length:'?';
 document.getElementById('exist').textContent=S.selected.length+' entities selected, '+n+' of them exist in Home Assistant right now'+(S.customised?' (custom list)':'');
 var dl=document.getElementById('all');dl.innerHTML=S.all.map(function(e){return '<option value="'+esc(e)+'">';}).join('');
}
function current(){var out=[];document.querySelectorAll('#ents input[type=checkbox]').forEach(function(c){if(c.checked)out.push(c.getAttribute('data-e'));});return out;}
function addEnt(){var v=document.getElementById('add').value.trim().toLowerCase();if(!v)return;
 S.selected=current();if(S.selected.indexOf(v)<0)S.selected.push(v);document.getElementById('add').value='';render();msg('added - press Save');}
function rm(e){S.selected=current().filter(function(x){return x!=e;});render();msg('removed - press Save');}
function msg(t){document.getElementById('msg').textContent=t;}
function load(){fetch('entities').then(function(r){return r.json();}).then(function(d){S=d;render();})
 .catch(function(e){document.getElementById('ents').innerHTML='<small class="miss">could not load entity list: '+esc(String(e))+'</small>';});}
function save(){S.selected=current();
 fetch('entities',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({selected:S.selected})})
 .then(function(r){return r.json();}).then(function(d){S=d;render();msg('saved');}).catch(function(e){msg('save failed: '+e);});}
function resetSel(){fetch('entities/reset',{method:'POST'}).then(function(r){return r.json();}).then(function(d){S=d;render();msg('reset to defaults');});}
load();
</script>
</body></html>
"""


def make_handler(ha, selection, state):
    """Request handler bound to an HAHistory client, an EntitySelection and a
    callable returning {interval, interval_source} for the page header."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "SolarCarTelemetry/3"

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

        def _json(self, code, obj):
            self._send(code, json.dumps(obj), "application/json")

        def _route(self):
            url = urllib.parse.urlsplit(self.path)
            path = url.path.rstrip("/") or "/"
            q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
            return path, q

        def do_GET(self):
            path, q = self._route()
            try:
                if path == "/":
                    return self._index()
                if path == "/export":
                    return self._export(q)
                if path == "/status":
                    return self._json(200, self._status())
                if path == "/entities":
                    return self._json(200, self._entities())
                self._send(404, "not found", "text/plain")
            except Exception as e:          # never let one request kill the server
                logging.exception("export http: request failed")
                try:
                    self._send(500, f"error: {e}", "text/plain")
                except OSError:
                    pass

        def do_POST(self):
            path, q = self._route()
            try:
                if path == "/entities":
                    n = int(self.headers.get("Content-Length") or 0)
                    try:
                        body = json.loads(self.rfile.read(n) or b"{}")
                        selected = body["selected"]
                        if not isinstance(selected, list):
                            raise ValueError
                    except (ValueError, KeyError, TypeError):
                        return self._send(400, 'expected JSON {"selected": [entity ids]}', "text/plain")
                    ok = selection.save(selected)
                    return self._json(200 if ok else 500, self._entities())
                if path == "/entities/reset":
                    ok = selection.reset()
                    return self._json(200 if ok else 500, self._entities())
                self._send(404, "not found", "text/plain")
            except Exception as e:
                logging.exception("export http: request failed")
                try:
                    self._send(500, f"error: {e}", "text/plain")
                except OSError:
                    pass

        def _entities(self):
            ids = selection.ids
            all_ids = ha.all_entities()
            return {"selected": ids,
                    "default": selection.default,
                    "customised": selection.customised,
                    "existing": None if all_ids is None else [e for e in ids if e in set(all_ids)],
                    "missing": None if all_ids is None else [e for e in ids if e not in set(all_ids)],
                    "all": all_ids or []}

        def _status(self):
            st = dict(state())
            ent = self._entities()
            st.update(entities=ent["selected"], existing=ent["existing"], missing=ent["missing"],
                      customised=ent["customised"])
            return st

        def _index(self):
            st = dict(state())
            body = PAGE % dict(
                interval=f"{st['interval']:g}" if isinstance(st.get("interval"), (int, float)) else "?",
                source=html.escape(str(st.get("interval_source", "add-on options"))))
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
            if start is None:
                return self._send(400, "give start (ISO / epoch) or minutes/hours/days", "text/plain")
            end = min(end or now, now)
            if end <= start:
                return self._send(400, "end must be after start", "text/plain")
            # ?entities=a,b,c overrides the saved selection for this one download
            ids = valid_entity_ids(q["entities"].split(",")) if q.get("entities") else selection.ids
            # Build the whole file before committing to a 200, so an
            # unreachable HA gives a readable error instead of a broken file.
            buf = io.StringIO()
            try:
                n = export_csv(ha, ids, start, end, buf)
            except Exception as e:
                logging.warning(f"export: history fetch failed: {e}")
                return self._send(502, f"could not read history from Home Assistant: {e}", "text/plain")
            tag = lambda d: d.strftime("%Y%m%d-%H%M%SZ")
            fname = f"solarcar_telemetry_{tag(start)}_{tag(end)}.csv"
            self._send(200, buf.getvalue(), "text/csv; charset=utf-8",
                       {"Content-Disposition": f'attachment; filename="{fname}"',
                        "X-Rows": str(n)})

    return Handler


def start_export_server(port, ha, selection, state):
    """Start the export HTTP server on a daemon thread. Returns the server,
    or None if the port can't be bound (logged, never fatal)."""
    try:
        srv = ThreadingHTTPServer(("0.0.0.0", port), make_handler(ha, selection, state))
    except OSError as e:
        logging.error(f"telemetry export server: cannot bind port {port}: {e}")
        return None
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, name="telemetry-export", daemon=True).start()
    logging.info(f"telemetry export server listening on :{port} (and via HA ingress); "
                 f"{len(selection.ids)} entities selected"
                 + (" (custom list)" if selection.customised else ""))
    return srv
