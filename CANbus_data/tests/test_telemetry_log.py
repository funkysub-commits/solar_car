"""Unit tests for the add-on's telemetry CSV log + export server
(ha_addons/solar-car-canbus/telemetry_log.py). Pure stdlib; runs on the PC.

    python -m unittest CANbus_data/tests/test_telemetry_log.py
"""
import csv
import io
import os
import sys
import json
import tempfile
import unittest
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ADDON = Path(__file__).resolve().parent.parent / "ha_addons" / "solar-car-canbus"
sys.path.insert(0, str(ADDON))

import telemetry_log as tl  # noqa: E402

T0 = datetime(2026, 10, 3, 12, 0, 0, tzinfo=timezone.utc)
COLS = ["ezkontrol_bus_voltage", "ezkontrol_gear", "bestgo_soc"]


def _write_n(log, n, start=T0, step=1.0, **fields):
    for i in range(n):
        vals = {"ezkontrol_bus_voltage": 50 + i, "ezkontrol_gear": "D1", "bestgo_soc": 70}
        vals.update(fields)
        log.write(100.0 + i, i % 2 == 0, (1, 1, 0), vals, now=start + timedelta(seconds=step * i))


class TelemetryLogWrite(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_header_and_rows(self):
        log = tl.TelemetryLog(COLS, log_dir=self.dir)
        _write_n(log, 3)
        log.close()
        files = tl.list_files(self.dir)
        self.assertEqual([(n, d) for n, d, _ in files], [("telemetry-2026-10-03.csv", "2026-10-03")])
        with open(os.path.join(self.dir, files[0][0]), newline="") as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[0], tl.META_COLUMNS + COLS)
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[1][0], "2026-10-03T12:00:00.000Z")
        self.assertEqual(rows[1][1:6], ["100.000", "1", "1", "1", "0"])
        self.assertEqual(rows[1][6:], ["50", "D1", "70"])
        self.assertEqual(rows[2][2], "0")        # high_res False on odd rows
        self.assertEqual(log.rows_today, 3)

    def test_none_is_blank(self):
        log = tl.TelemetryLog(COLS, log_dir=self.dir)
        log.write(1.0, False, (1, 0, 0), {"bestgo_soc": None}, now=T0)
        log.close()
        with open(log.path, newline="") as f:
            row = list(csv.reader(f))[1]
        self.assertEqual(row[6:], ["", "", ""])

    def test_day_rollover(self):
        log = tl.TelemetryLog(COLS, log_dir=self.dir)
        log.write(1.0, False, (1, 1, 1), {}, now=T0.replace(hour=23, minute=59, second=59))
        log.write(2.0, False, (1, 1, 1), {}, now=T0 + timedelta(days=1))
        log.close()
        days = [d for _, d, _ in tl.list_files(self.dir)]
        self.assertEqual(days, ["2026-10-03", "2026-10-04"])
        self.assertEqual(log.rows_today, 1)

    def test_reopen_appends_and_counts(self):
        log = tl.TelemetryLog(COLS, log_dir=self.dir)
        _write_n(log, 2)
        log.close()
        log2 = tl.TelemetryLog(COLS, log_dir=self.dir)
        _write_n(log2, 1, start=T0 + timedelta(seconds=10))
        self.assertEqual(log2.rows_today, 3)
        log2.close()
        with open(log2.path, newline="") as f:
            self.assertEqual(len(list(csv.reader(f))), 4)   # one header only

    def test_header_mismatch_starts_sibling_file(self):
        log = tl.TelemetryLog(COLS, log_dir=self.dir)
        _write_n(log, 1)
        log.close()
        log2 = tl.TelemetryLog(COLS + ["bestgo_soh"], log_dir=self.dir)
        log2.write(1.0, False, (1, 1, 1), {"bestgo_soh": 99}, now=T0)
        log2.close()
        names = sorted(n for n, _, _ in tl.list_files(self.dir))
        self.assertEqual(names, ["telemetry-2026-10-03-1.csv", "telemetry-2026-10-03.csv"])

    def test_prune_old_files(self):
        old = os.path.join(self.dir, "telemetry-2020-01-01.csv")
        with open(old, "w") as f:
            f.write("x\n")
        log = tl.TelemetryLog(COLS, log_dir=self.dir, keep_days=30)
        _write_n(log, 1, start=datetime.now(timezone.utc))
        log.close()
        self.assertFalse(os.path.exists(old))

    def test_write_error_does_not_raise(self):
        bad = os.path.join(self.dir, "a-file-not-a-dir")
        open(bad, "w").close()
        log = tl.TelemetryLog(COLS, log_dir=os.path.join(bad, "sub"))
        log.write(1.0, False, (1, 1, 1), {}, now=T0)      # must not raise
        self.assertIsNotNone(log.last_error)


class TelemetryExport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        log = tl.TelemetryLog(COLS, log_dir=self.dir)
        # 10 rows at 12:00:00 .. 12:00:09, then 2 rows the next day
        _write_n(log, 10)
        _write_n(log, 2, start=T0 + timedelta(days=1))
        log.close()

    def tearDown(self):
        self.tmp.cleanup()

    def _export(self, start, end):
        buf = io.StringIO()
        n = tl.export_csv(start, end, buf, self.dir)
        return n, list(csv.reader(io.StringIO(buf.getvalue())))

    def test_range_is_half_open(self):
        n, rows = self._export(T0 + timedelta(seconds=2), T0 + timedelta(seconds=5))
        self.assertEqual(n, 3)
        self.assertEqual([r[0] for r in rows[1:]],
                         ["2026-10-03T12:00:02.000Z", "2026-10-03T12:00:03.000Z", "2026-10-03T12:00:04.000Z"])

    def test_unbounded_spans_files_with_single_header(self):
        n, rows = self._export(None, None)
        self.assertEqual(n, 12)
        self.assertEqual(sum(1 for r in rows if r[0] == "time_utc"), 1)

    def test_empty_range_has_header(self):
        n, rows = self._export(T0 + timedelta(days=5), None)
        self.assertEqual(n, 0)
        self.assertEqual(rows, [tl.META_COLUMNS])

    def test_parse_time(self):
        self.assertEqual(tl.parse_time("2026-10-03T12:00:00Z"), T0)
        self.assertEqual(tl.parse_time("2026-10-03T12:00:00"), T0)          # naive = UTC
        self.assertEqual(tl.parse_time("2026-10-03T14:00:00+02:00"), T0)
        self.assertEqual(tl.parse_time(str(int(T0.timestamp()))), T0)
        self.assertEqual(tl.parse_time("2026-10-03"), T0.replace(hour=0))
        self.assertIsNone(tl.parse_time(""))
        self.assertIsNone(tl.parse_time("yesterday"))

    def test_http_server(self):
        state = {"high_res": True, "interval": 0.5, "rows_today": 12}
        srv = tl.start_export_server(0, lambda: state, self.dir)
        self.assertIsNotNone(srv)
        port = srv.server_address[1]
        base = f"http://127.0.0.1:{port}"
        try:
            page = urllib.request.urlopen(base + "/").read().decode()
            self.assertIn("High-resolution mode: <b>ON</b>", page)
            self.assertIn('href="export?hours=1"', page)          # relative (ingress-safe)
            self.assertNotIn('href="/export', page)

            st = json.loads(urllib.request.urlopen(base + "/status").read())
            self.assertEqual(st["files"], 2)
            self.assertTrue(st["high_res"])

            r = urllib.request.urlopen(
                base + "/export?start=2026-10-03T12:00:03Z&end=2026-10-03T12:00:06Z")
            self.assertEqual(r.headers["X-Rows"], "3")
            self.assertIn("attachment", r.headers["Content-Disposition"])
            body = r.read().decode()
            self.assertEqual(body.count("\n"), 4)

            r = urllib.request.urlopen(base + "/export?hours=1&end=2026-10-03T12:30:00Z")
            self.assertEqual(r.headers["X-Rows"], "10")

            r = urllib.request.urlopen(base + "/files/telemetry-2026-10-03.csv")
            self.assertEqual(r.read().decode().count("\n"), 11)

            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(base + "/files/..%2Fsecret.csv")
            self.assertEqual(cm.exception.code, 404)
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(base + "/export?start=2026-10-03T12:00:06Z&end=2026-10-03T12:00:03Z")
            self.assertEqual(cm.exception.code, 400)
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
