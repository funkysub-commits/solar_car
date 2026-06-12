#!/usr/bin/env python3
"""Unit tests for the warning/staleness/hide logic (alerts.py) and the
ha_client readers. Pure stdlib: python -m unittest discover display/tests
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "addon"))

import alerts          # noqa: E402
import config          # noqa: E402
import ha_client       # noqa: E402

OK = {"bus": True, "batt": True, "ezk": True}
UNKNOWN = {"bus": None, "batt": None, "ezk": None}


def stale(*keys):
    return {k: (k in keys) for k in config.STALE_KEYS}


class DeviceStatus(unittest.TestCase):
    def test_all_healthy(self):
        self.assertEqual(alerts.device_status(stale(), OK), (False, False, False))

    def test_explicit_bus_down_wins(self):
        s = alerts.device_status(stale(), {"bus": False, "batt": True, "ezk": True})
        self.assertEqual(s, (True, False, False))

    def test_explicit_device_down(self):
        s = alerts.device_status(stale(), {"bus": True, "batt": False, "ezk": True})
        self.assertEqual(s, (False, True, False))

    def test_bus_down_folds_devices(self):
        s = alerts.device_status(stale(), {"bus": False, "batt": False, "ezk": False})
        self.assertEqual(s, (True, False, False))

    def test_inference_battery_only(self):
        # health sensors absent; only battery-fed values stale -> batt_down
        s = alerts.device_status(stale(*config.BATT_KEYS), UNKNOWN)
        self.assertEqual(s, (False, True, False))

    def test_inference_all_stale_is_bus_down(self):
        s = alerts.device_status(stale(*config.CAN_KEYS), UNKNOWN)
        self.assertEqual(s, (True, False, False))

    def test_explicit_true_blocks_inference(self):
        # health says battery is fine even though its values look stale
        s = alerts.device_status(stale(*config.BATT_KEYS), OK)
        self.assertEqual(s, (False, False, False))

    def test_pi_never_infers_can(self):
        s = alerts.device_status(stale("t_pi"), UNKNOWN)
        self.assertEqual(s, (False, False, False))


class MergeDeviceStale(unittest.TestCase):
    def test_battery_down_marks_exactly_battery_keys(self):
        merged = alerts.merge_device_stale(stale(), False, True, False)
        for k in config.BATT_KEYS:
            self.assertTrue(merged[k], k)
        for k in config.EZK_KEYS + ("t_pi",):
            self.assertFalse(merged[k], k)

    def test_bus_down_marks_all_can_keys_not_pi(self):
        merged = alerts.merge_device_stale(stale(), True, False, False)
        for k in config.CAN_KEYS:
            self.assertTrue(merged[k], k)
        self.assertFalse(merged["t_pi"])


class BuildWarnings(unittest.TestCase):
    TEMPS = {"t_motor": 40.0, "t_ezk": 35.0, "t_batt": 30.0, "t_pi": 48.0}

    def keys(self, ws):
        return [w["key"] for w in ws]

    def test_priority_order(self):
        temps = {**self.TEMPS, "t_motor": 72.0}
        st = alerts.merge_device_stale(stale("t_pi"), False, True, False)
        ws = alerts.build_warnings(temps, st, (False, True, False), "hi")
        self.assertEqual(self.keys(ws),
                         ["can_batt", "temp_t_motor", "stale_t_pi", "user"])

    def test_bus_down_explains_can_keys_keeps_pi(self):
        st = alerts.merge_device_stale(stale("t_pi"), True, False, False)
        ws = alerts.build_warnings(self.TEMPS, st, (True, False, False), "")
        self.assertEqual(self.keys(ws), ["can", "stale_t_pi"])

    def test_device_warning_replaces_its_stale_warnings(self):
        st = alerts.merge_device_stale(stale(), False, True, False)
        ws = alerts.build_warnings(self.TEMPS, st, (False, True, False), "")
        self.assertEqual(self.keys(ws), ["can_batt"])

    def test_hotter_sorts_first(self):
        temps = {**self.TEMPS, "t_motor": 70.0, "t_batt": 78.0}
        ws = alerts.build_warnings(temps, stale(), (False, False, False), "")
        self.assertEqual(self.keys(ws), ["temp_t_batt", "temp_t_motor"])

    def test_high_temp_suppressed_when_stale(self):
        temps = {**self.TEMPS, "t_motor": 90.0}
        ws = alerts.build_warnings(temps, stale("t_motor"),
                                   (False, False, False), "")
        self.assertEqual(self.keys(ws), ["stale_t_motor"])

    def test_ha_down_replaces_everything_but_user(self):
        st = stale(*config.STALE_KEYS)
        ws = alerts.build_warnings({**self.TEMPS, "t_motor": 99.0}, st,
                                   (True, False, False), "msg", ha_down=True)
        self.assertEqual(self.keys(ws), ["ha", "user"])


class FitHidden(unittest.TestCase):
    def test_fits_untouched(self):
        ws = [{"key": "a", "priority": 9}, {"key": "b", "priority": 1}]
        keep, dropped = alerts.fit_hidden({"a", "b"}, ws)
        self.assertEqual((keep, dropped), ({"a", "b"}, set()))

    def test_drops_lowest_priority_first(self):
        ws = [{"key": f"k{i:02d}" + "x" * 28, "priority": i} for i in range(12)]
        keys = {w["key"] for w in ws}                 # 12 keys x 31 chars >> 255
        keep, dropped = alerts.fit_hidden(keys, ws)
        self.assertLessEqual(len(",".join(sorted(keep))), 255)
        self.assertTrue(keep)
        # every kept key outranks every dropped key
        prio = {w["key"]: w["priority"] for w in ws}
        self.assertGreater(min(prio[k] for k in keep),
                           max(prio[k] for k in dropped))


class ReaderParsing(unittest.TestCase):
    def fake_get(self, ret):
        self._orig = ha_client.ha_get
        ha_client.ha_get = lambda entity: ret
        self.addCleanup(lambda: setattr(ha_client, "ha_get", self._orig))

    def test_read_health_states(self):
        from datetime import datetime, timezone
        fresh = datetime.now(timezone.utc).isoformat()
        for state, want in [("on", True), ("Connected", True), ("ok", True),
                            ("1", True), ("0", False),
                            ("off", False), ("disconnected", False),
                            ("error", False), ("weird", None),
                            ("unavailable", None), (None, None)]:
            self.fake_get((state, {} if state else {}, fresh if state else None))
            self.assertEqual(ha_client.read_health("x"), want, state)

    def test_read_health_stale_signal_is_unknown(self):
        # a health sensor frozen at "1" but not updated for ages reads unknown,
        # so the caller falls back to data-staleness inference
        self.fake_get(("1", {}, "2020-01-01T00:00:00Z"))
        self.assertIsNone(ha_client.read_health("x"))

    def test_read_message_tristate(self):
        self.fake_get((None, {}, None))              # request failed
        self.assertIsNone(ha_client.read_message("x"))
        self.fake_get(("", {}, "2026-01-01T00:00:00Z"))
        self.assertEqual(ha_client.read_message("x"), "")
        self.fake_get((" hi ", {}, "2026-01-01T00:00:00Z"))
        self.assertEqual(ha_client.read_message("x"), "hi")

    def test_entity_age(self):
        self.assertEqual(ha_client.entity_age_seconds(None), float("inf"))
        self.assertEqual(ha_client.entity_age_seconds("garbage"), float("inf"))
        self.assertGreater(ha_client.entity_age_seconds("2020-01-01T00:00:00Z"),
                           1e8)


if __name__ == "__main__":
    unittest.main()
