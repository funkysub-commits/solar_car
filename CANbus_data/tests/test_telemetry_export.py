"""Unit tests for the add-on's on-demand telemetry export
(ha_addons/solar-car-canbus/telemetry_export.py) against a mock HA history API.

    python -m unittest CANbus_data/tests/test_telemetry_export.py
"""
import csv
import io
import sys
import json
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ADDON = Path(__file__).resolve().parent.parent / "ha_addons" / "solar-car-canbus"
sys.path.insert(0, str(ADDON))

import telemetry_export as te  # noqa: E402

T0 = datetime(2026, 10, 3, 12, 0, 0, tzinfo=timezone.utc)
ENTITIES = ["sensor.ezkontrol_status", "sensor.ezkontrol_bus_voltage",
            "sensor.bestgo_soc", "sensor.solar_car_speed"]


def iso(dt):
    return dt.isoformat()


class MockHA:
    """Serves /api/states and /api/history/period from an in-memory list of
    (entity_id, time, state). Mimics HA: for each requested entity with any
    history it returns the state in force at `start` (timestamped before
    start) followed by the changes inside the window; entities with no
    recorded states at all are omitted; minimal_response strips entity_id
    from all but the first element."""

    def __init__(self):
        self.states = []          # (entity_id, datetime, state)
        self.existing = set()
        self.requests = []
        self.fail = False
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def _send(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code); self.send_header("Content-Length", str(len(body)))
                self.end_headers(); self.wfile.write(body)

            def do_GET(self):
                outer.requests.append(self.path)
                if outer.fail:
                    return self._send(500, {"message": "boom"})
                if self.headers.get("Authorization") != "Bearer tok":
                    return self._send(401, {"message": "unauthorized"})
                url = urllib.parse.urlsplit(self.path)
                if url.path == "/api/states":
                    return self._send(200, [{"entity_id": e, "state": "x"} for e in outer.existing])
                if url.path.startswith("/api/history/period/"):
                    start = datetime.fromisoformat(urllib.parse.unquote(url.path.rsplit("/", 1)[1]))
                    q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
                    end = datetime.fromisoformat(q["end_time"])
                    out = []
                    for e in q["filter_entity_id"].split(","):
                        hist = sorted((t, st) for (eid, t, st) in outer.states if eid == e)
                        if not hist:
                            continue
                        before = [(t, st) for t, st in hist if t < start]
                        inside = [(t, st) for t, st in hist if start <= t <= end]
                        series = []
                        if before:
                            series.append(before[-1])
                        series += inside
                        if not series:
                            continue
                        out.append([{"entity_id": e, "last_changed": iso(series[0][0]), "state": series[0][1]}]
                                   + [{"last_changed": iso(t), "state": st} for t, st in series[1:]])
                    return self._send(200, out)
                self._send(404, {"message": "nope"})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def add(self, entity, offset_s, state):
        self.states.append((entity, T0 + timedelta(seconds=offset_s), state))

    def close(self):
        self.srv.shutdown(); self.srv.server_close()


class ExportCSV(unittest.TestCase):
    def setUp(self):
        self.ha = MockHA()
        self.client = te.HAHistory(self.ha.url, "tok")
        # ezkontrol_status + bus_voltage have history; soc appears mid-window
        # (entity created after an HA restart); solar_car_speed never exists.
        self.ha.add("sensor.ezkontrol_status", -100, "1")
        self.ha.add("sensor.ezkontrol_bus_voltage", -50, "52.0")
        self.ha.add("sensor.ezkontrol_bus_voltage", 2, "52.1")
        self.ha.add("sensor.ezkontrol_bus_voltage", 4, "unavailable")
        self.ha.add("sensor.ezkontrol_bus_voltage", 6, "52.3")
        self.ha.add("sensor.bestgo_soc", 4, "70")
        self.ha.add("sensor.bestgo_soc", 9, "69")
        self.ha.existing = {"sensor.ezkontrol_status", "sensor.ezkontrol_bus_voltage", "sensor.bestgo_soc"}

    def tearDown(self):
        self.ha.close()

    def _export(self, start, end, **kw):
        buf = io.StringIO()
        n = te.export_csv(self.client, ENTITIES, start, end, buf, **kw)
        return n, list(csv.reader(io.StringIO(buf.getvalue())))

    def test_pivot_carry_forward_and_blanks(self):
        n, rows = self._export(T0, T0 + timedelta(seconds=10))
        self.assertEqual(rows[0], ["time_utc", "ezkontrol_status", "ezkontrol_bus_voltage", "bestgo_soc", "solar_car_speed"])
        self.assertEqual(n, 5)
        # initial row at window start: states in force (soc not yet existing, speed never)
        self.assertEqual(rows[1], ["2026-10-03T12:00:00.000Z", "1", "52.0", "", ""])
        self.assertEqual(rows[2], ["2026-10-03T12:00:02.000Z", "1", "52.1", "", ""])
        self.assertEqual(rows[3], ["2026-10-03T12:00:04.000Z", "1", "", "70", ""])   # unavailable -> blank, soc appears
        self.assertEqual(rows[4], ["2026-10-03T12:00:06.000Z", "1", "52.3", "70", ""])
        self.assertEqual(rows[5], ["2026-10-03T12:00:09.000Z", "1", "52.3", "69", ""])
        self.assertTrue(all(r[4] == "" for r in rows[1:]))   # missing entity: blank column

    def test_no_history_at_all_gives_header_and_blank_initial_row(self):
        self.ha.states.clear()
        n, rows = self._export(T0, T0 + timedelta(seconds=10))
        self.assertEqual(n, 1)
        self.assertEqual(rows[1], ["2026-10-03T12:00:00.000Z", "", "", "", ""])

    def test_chunking_is_seamless(self):
        # 10 s window fetched in 3 s chunks -> 4 history calls, identical rows
        n1, rows1 = self._export(T0, T0 + timedelta(seconds=10))
        self.ha.requests.clear()
        n2, rows2 = self._export(T0, T0 + timedelta(seconds=10), chunk=timedelta(seconds=3))
        self.assertEqual(sum(1 for r in self.ha.requests if "/api/history/" in r), 4)
        self.assertEqual(rows1, rows2)

    def test_end_is_exclusive(self):
        n, rows = self._export(T0, T0 + timedelta(seconds=9))
        self.assertEqual(rows[-1][0], "2026-10-03T12:00:06.000Z")

    def test_http_error_raises(self):
        self.ha.fail = True
        with self.assertRaises(RuntimeError):
            self._export(T0, T0 + timedelta(seconds=10))

    def test_existing(self):
        self.assertEqual(self.client.existing(ENTITIES),
                         ["sensor.ezkontrol_status", "sensor.ezkontrol_bus_voltage", "sensor.bestgo_soc"])
        self.ha.fail = True
        self.assertIsNone(self.client.existing(ENTITIES))

    def test_parse_time(self):
        self.assertEqual(te.parse_time("2026-10-03T12:00:00Z"), T0)
        self.assertEqual(te.parse_time("2026-10-03T12:00:00"), T0)          # naive = UTC
        self.assertEqual(te.parse_time("2026-10-03T14:00:00+02:00"), T0)
        self.assertEqual(te.parse_time(str(int(T0.timestamp()))), T0)
        self.assertEqual(te.parse_time("2026-10-03"), T0.replace(hour=0))
        self.assertIsNone(te.parse_time(""))
        self.assertIsNone(te.parse_time("yesterday"))

    def test_duplicate_short_names_use_full_ids(self):
        ids = ["sensor.ezkontrol_status", "binary_sensor.ezkontrol_status"]
        buf = io.StringIO()
        te.export_csv(self.client, ids, T0, T0 + timedelta(seconds=1), buf)
        self.assertEqual(buf.getvalue().splitlines()[0],
                         "time_utc,sensor.ezkontrol_status,binary_sensor.ezkontrol_status")

    def test_http_server(self):
        # history for "now"-relative links: put a change 30 s ago
        now = datetime.now(timezone.utc)
        self.ha.states.append(("sensor.bestgo_soc", now - timedelta(seconds=30), "55"))
        self.ha.existing |= {"sensor.other_thing", "light.kitchen"}
        state = {"interval": 0.5, "interval_source": "input_number.canbus_update_interval"}
        tmp = tempfile.TemporaryDirectory()
        sel = te.EntitySelection(ENTITIES, path=Path(tmp.name) / "sel.json")
        srv = te.start_export_server(0, self.client, sel, lambda: state)
        self.assertIsNotNone(srv)
        base = f"http://127.0.0.1:{srv.server_address[1]}"

        def post(path, body=None):
            req = urllib.request.Request(base + path, method="POST",
                                         data=None if body is None else json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            return json.loads(urllib.request.urlopen(req).read())

        try:
            page = urllib.request.urlopen(base + "/").read().decode()
            self.assertIn("Update interval: <b>0.5 s</b>", page)
            self.assertIn('href="export?hours=1"', page)          # relative (ingress-safe)
            self.assertNotIn('href="/export', page)
            self.assertIn("fetch('entities'", page)                # relative API calls too

            ent = json.loads(urllib.request.urlopen(base + "/entities").read())
            self.assertEqual(ent["selected"], ENTITIES)
            self.assertEqual(ent["default"], ENTITIES)
            self.assertFalse(ent["customised"])
            self.assertEqual(ent["missing"], ["sensor.solar_car_speed"])
            self.assertIn("light.kitchen", ent["all"])

            st = json.loads(urllib.request.urlopen(base + "/status").read())
            self.assertEqual(st["missing"], ["sensor.solar_car_speed"])

            r = urllib.request.urlopen(base + "/export?start=2026-10-03T12:00:00Z&end=2026-10-03T12:00:10Z")
            self.assertEqual(r.headers["X-Rows"], "5")
            self.assertIn("attachment", r.headers["Content-Disposition"])
            self.assertEqual(r.read().decode().count("\n"), 6)

            r = urllib.request.urlopen(base + "/export?minutes=5")
            body = r.read().decode()
            self.assertEqual(r.headers["X-Rows"], "2")                 # initial row + the 30 s-ago change
            last = body.splitlines()[-1].split(",")
            self.assertEqual(last[1:], ["1", "52.3", "55", ""])          # carried forward, soc changed, speed missing

            # --- editable selection: drop status + speed, add another entity ---
            new = ["sensor.ezkontrol_bus_voltage", "sensor.bestgo_soc", "sensor.other_thing", "Bad Id!", "sensor.bestgo_soc"]
            ent = post("/entities", {"selected": new})
            self.assertEqual(ent["selected"], ["sensor.ezkontrol_bus_voltage", "sensor.bestgo_soc", "sensor.other_thing"])
            self.assertTrue(ent["customised"])
            self.assertEqual(ent["missing"], [])
            r = urllib.request.urlopen(base + "/export?start=2026-10-03T12:00:00Z&end=2026-10-03T12:00:10Z")
            lines = r.read().decode().splitlines()
            self.assertEqual(lines[0], "time_utc,ezkontrol_bus_voltage,bestgo_soc,other_thing")
            self.assertEqual(lines[1], "2026-10-03T12:00:00.000Z,52.0,,")
            # persisted: a fresh EntitySelection on the same file sees it
            self.assertEqual(te.EntitySelection(ENTITIES, path=Path(tmp.name) / "sel.json").ids, ent["selected"])
            # one-off override via ?entities=
            r = urllib.request.urlopen(base + "/export?start=2026-10-03T12:00:00Z&end=2026-10-03T12:00:10Z&entities=sensor.bestgo_soc")
            self.assertEqual(r.read().decode().splitlines()[0], "time_utc,bestgo_soc")
            # reset
            ent = post("/entities/reset")
            self.assertEqual(ent["selected"], ENTITIES)
            self.assertFalse(ent["customised"])
            self.assertFalse((Path(tmp.name) / "sel.json").exists())
            with self.assertRaises(urllib.error.HTTPError) as cm:
                post("/entities", {"nope": 1})
            self.assertEqual(cm.exception.code, 400)

            for bad in ("/export", "/export?start=2026-10-03T12:00:06Z&end=2026-10-03T12:00:03Z",
                        "/export?hours=abc"):
                with self.assertRaises(urllib.error.HTTPError) as cm:
                    urllib.request.urlopen(base + bad)
                self.assertEqual(cm.exception.code, 400, bad)

            self.ha.fail = True
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(base + "/export?hours=1")
            self.assertEqual(cm.exception.code, 502)
        finally:
            srv.shutdown()
            srv.server_close()
            tmp.cleanup()


class SelectionFile(unittest.TestCase):
    def test_defaults_save_reset_and_bad_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sub" / "sel.json"
            s = te.EntitySelection(["sensor.a", "sensor.b", "junk", "sensor.a"], path=p)
            self.assertEqual(s.ids, ["sensor.a", "sensor.b"])
            self.assertTrue(s.save(["sensor.b", "light.x"]))
            self.assertEqual(te.EntitySelection(["sensor.a"], path=p).ids, ["sensor.b", "light.x"])
            p.write_text("{not json")
            s2 = te.EntitySelection(["sensor.a"], path=p)
            self.assertEqual(s2.ids, ["sensor.a"])                 # corrupt file -> defaults
            self.assertTrue(s2.reset())
            self.assertFalse(p.exists())


if __name__ == "__main__":
    unittest.main()
