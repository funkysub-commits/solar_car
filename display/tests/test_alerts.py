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

    def test_explicit_adapter_down(self):
        s = alerts.device_status(stale(), {"bus": False, "batt": True, "ezk": True})
        self.assertEqual(s, (True, False, False))

    def test_explicit_device_down(self):
        s = alerts.device_status(stale(), {"bus": True, "batt": False, "ezk": True})
        self.assertEqual(s, (False, True, False))

    def test_no_folding_all_three_independent(self):
        # all three explicitly down -> all three flagged (no folding now)
        s = alerts.device_status(stale(), {"bus": False, "batt": False, "ezk": False})
        self.assertEqual(s, (True, True, True))

    def test_inference_battery_only(self):
        # health sensors absent; only battery-fed values stale -> batt_down
        s = alerts.device_status(stale(*config.BATT_KEYS), UNKNOWN)
        self.assertEqual(s, (False, True, False))

    def test_inference_all_stale_is_adapter_down(self):
        # everything stale -> adapter inferred down, and the two devices too
        s = alerts.device_status(stale(*config.CAN_KEYS), UNKNOWN)
        self.assertEqual(s, (True, True, True))

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

    def test_adapter_down_marks_all_can_keys_not_pi(self):
        merged = alerts.merge_device_stale(stale(), True, False, False)
        for k in config.CAN_KEYS:
            self.assertTrue(merged[k], k)
        self.assertFalse(merged["t_pi"])


class DeviceMarks(unittest.TestCase):
    """The on-screen "!" marks ignore age-staleness entirely."""

    def test_nothing_down_marks_nothing(self):
        marks = alerts.device_marks(False, False, False)
        self.assertFalse(any(marks.values()), marks)

    def test_battery_down_marks_exactly_battery_keys(self):
        marks = alerts.device_marks(False, True, False)
        for k in config.BATT_KEYS:
            self.assertTrue(marks[k], k)
        for k in config.EZK_KEYS + ("t_pi",):
            self.assertFalse(marks[k], k)

    def test_every_stale_key_is_covered(self):
        # marks must be a total map over STALE_KEYS - render() indexes it
        self.assertEqual(set(alerts.device_marks(True, True, True)),
                         set(config.STALE_KEYS))


class BuildWarnings(unittest.TestCase):
    TEMPS = {"t_motor": 40.0, "t_ezk": 35.0, "t_batt": 30.0, "t_pi": 48.0}

    def keys(self, ws):
        return [w["key"] for w in ws]

    def test_priority_order(self):
        # bestgo down + a live high motor temp + Pi temp stale (non-CAN).
        # The stale Pi temp raises NO warning - only devices off the bus do.
        temps = {**self.TEMPS, "t_motor": 72.0}
        st = alerts.merge_device_stale(stale("t_pi"), False, True, False)
        ws = alerts.build_warnings(temps, st, (False, True, False))
        self.assertEqual(self.keys(ws), ["can_bestgo", "temp_t_motor"])

    def test_all_three_devices_separate_adapter_first(self):
        st = alerts.merge_device_stale(stale("t_pi"), True, True, True)
        ws = alerts.build_warnings(self.TEMPS, st, (True, True, True))
        self.assertEqual(self.keys(ws),
                         ["can_adapter", "can_bestgo", "can_ezk"])

    def test_unchanging_value_raises_no_warning(self):
        # a parked car: every value frozen, but nothing is reported off the bus
        st = stale(*config.STALE_KEYS)
        ws = alerts.build_warnings(self.TEMPS, st, (False, False, False))
        self.assertEqual(self.keys(ws), [])

    def test_device_warning_replaces_its_stale_warnings(self):
        st = alerts.merge_device_stale(stale(), False, True, False)
        ws = alerts.build_warnings(self.TEMPS, st, (False, True, False))
        self.assertEqual(self.keys(ws), ["can_bestgo"])

    def test_hotter_sorts_first(self):
        temps = {**self.TEMPS, "t_motor": 70.0, "t_batt": 78.0}
        ws = alerts.build_warnings(temps, stale(), (False, False, False))
        self.assertEqual(self.keys(ws), ["temp_t_batt", "temp_t_motor"])

    def test_high_temp_suppressed_when_stale(self):
        # a frozen reading raises neither a high-temp warning nor a stale one
        temps = {**self.TEMPS, "t_motor": 90.0}
        ws = alerts.build_warnings(temps, stale("t_motor"),
                                   (False, False, False))
        self.assertEqual(self.keys(ws), [])

    def test_device_warning_outranks_temp(self):
        # a live high temp is capped (<=90) so device-down warnings stay above it
        temps = {**self.TEMPS, "t_pi": 200.0}     # absurdly hot, capped priority
        ws = alerts.build_warnings(temps, stale(), (False, True, False))
        self.assertEqual(self.keys(ws)[0], "can_bestgo")

    def test_aux_down_warns_below_the_can_devices(self):
        ws = alerts.build_warnings(self.TEMPS, stale(), (False, False, True),
                                   aux_down=True)
        self.assertEqual(self.keys(ws), ["can_ezk", "aux_batt"])

    def test_aux_silent_by_default(self):
        # a disabled or placeholder aux battery never reaches build_warnings as
        # down, so it contributes nothing
        ws = alerts.build_warnings(self.TEMPS, stale(), (False, False, False))
        self.assertEqual(self.keys(ws), [])

    def test_ha_down_hides_aux_warning_too(self):
        ws = alerts.build_warnings(self.TEMPS, stale(), (False, False, False),
                                   ha_down=True, aux_down=True)
        self.assertEqual(self.keys(ws), ["ha"])

    def test_ha_down_is_only_warning(self):
        st = stale(*config.STALE_KEYS)
        ws = alerts.build_warnings({**self.TEMPS, "t_motor": 99.0}, st,
                                   (True, False, False), ha_down=True)
        self.assertEqual(self.keys(ws), ["ha"])


class Clock(unittest.TestCase):
    def test_12h_with_seconds_and_no_leading_zero(self):
        import display
        for _ in range(3):
            s = display.now_clock()
            # 1-12 hour (never "0" or "01"), :MM:SS, no AM/PM
            self.assertRegex(s, r"^(1[0-2]|[1-9]):[0-5]\d:[0-5]\d$")


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

    def test_read_health_trusts_an_old_timestamp(self):
        # The CANbus app re-pushes an unchanged "1" on a heartbeat, and HA does
        # not advance last_updated for an unchanged state+attributes. An explicit
        # reading must therefore be trusted no matter how old its timestamp
        # looks - otherwise a healthy adapter reads as "CAN adapter disconnected".
        self.fake_get(("1", {}, "2020-01-01T00:00:00Z"))
        self.assertIs(ha_client.read_health("x"), True)
        self.fake_get(("0", {}, "2020-01-01T00:00:00Z"))
        self.assertIs(ha_client.read_health("x"), False)

    def test_set_message_truncates_and_uses_entity_domain(self):
        calls = []
        orig = ha_client.ha_call_service
        ha_client.ha_call_service = lambda d, s, data: calls.append((d, s, data))
        self.addCleanup(lambda: setattr(ha_client, "ha_call_service", orig))
        ha_client.set_message("input_text.eink_message", "x" * 300)
        domain, service, data = calls[0]
        self.assertEqual((domain, service), ("input_text", "set_value"))
        self.assertEqual(data["entity_id"], "input_text.eink_message")
        self.assertEqual(len(data["value"]), 255)   # input_text's hard cap

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
