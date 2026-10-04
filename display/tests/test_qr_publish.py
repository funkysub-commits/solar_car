"""The 'Connect to Pi' QR sensors: publish_ip_sensors() must write a QR PNG
into HA's www folder and publish its /local path, publish 'unavailable' for a
down link, and re-create a file that went missing.

    python -m unittest display/tests/test_qr_publish.py
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

ADDON = Path(__file__).resolve().parent.parent / "addon"
sys.path.insert(0, str(ADDON))

_tmp = tempfile.TemporaryDirectory()
os.environ["QR_DIR"] = os.path.join(_tmp.name, "www", "solarcar")   # read by config at import
os.environ["QR_URL_BASE"] = "/local/solarcar"

import config      # noqa: E402
import ha_client   # noqa: E402

try:
    import qrcode  # noqa: F401
    HAVE_QRCODE = True
except Exception:
    HAVE_QRCODE = False


class QRPublish(unittest.TestCase):
    def setUp(self):
        self.posted = []
        self._orig_post = ha_client.ha_post_state
        ha_client.ha_post_state = lambda e, s, a: self.posted.append((e, s, dict(a)))
        ha_client._ip_pub.clear()
        ha_client._ip_pub_time = 0.0
        ha_client._router_ip = "192.168.0.47"
        ha_client._wifi_ip = None
        for f in Path(config.QR_DIR).glob("*.png") if os.path.isdir(config.QR_DIR) else []:
            f.unlink()

    def tearDown(self):
        ha_client.ha_post_state = self._orig_post

    def _by_entity(self):
        return {e: (s, a) for e, s, a in self.posted}

    @unittest.skipUnless(HAVE_QRCODE, "qrcode not installed on this PC")
    def test_writes_png_and_publishes_local_path(self):
        ha_client.publish_ip_sensors()
        pub = self._by_entity()
        state, attrs = pub[config.ENT_PI_ROUTER_IP]
        self.assertEqual(state, "192.168.0.47")
        self.assertEqual(attrs["url"], "http://192.168.0.47:8123")
        self.assertEqual(attrs["qr_path"], "/local/solarcar/qr_router.png")
        self.assertNotIn("qr", attrs)                      # no data: URI any more
        png = Path(config.QR_DIR) / "qr_router.png"
        self.assertTrue(png.exists())
        self.assertEqual(png.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        self.assertFalse((Path(config.QR_DIR) / "qr_router.png.tmp").exists())
        # hotspot link down -> unavailable, no file
        state, attrs = pub[config.ENT_PI_HOTSPOT_IP]
        self.assertEqual(state, "unavailable")
        self.assertFalse(attrs["connected"])
        self.assertFalse((Path(config.QR_DIR) / "qr_hotspot.png").exists())

    @unittest.skipUnless(HAVE_QRCODE, "qrcode not installed on this PC")
    def test_unchanged_link_not_republished_until_heartbeat_and_missing_file_rewritten(self):
        ha_client.publish_ip_sensors()
        n = len(self.posted)
        ha_client.publish_ip_sensors()                    # same IPs, not due
        self.assertEqual(len(self.posted), n)
        (Path(config.QR_DIR) / "qr_router.png").unlink()  # someone wiped www/
        ha_client.publish_ip_sensors()
        self.assertTrue((Path(config.QR_DIR) / "qr_router.png").exists())
        self.assertEqual(len(self.posted), n + 1)

    @unittest.skipUnless(HAVE_QRCODE, "qrcode not installed on this PC")
    def test_ip_change_rewrites_qr(self):
        ha_client.publish_ip_sensors()
        before = (Path(config.QR_DIR) / "qr_router.png").read_bytes()
        ha_client._router_ip = "10.89.191.211"
        ha_client.publish_ip_sensors()
        after = (Path(config.QR_DIR) / "qr_router.png").read_bytes()
        self.assertNotEqual(before, after)
        self.assertEqual(self._by_entity()[config.ENT_PI_ROUTER_IP][0], "10.89.191.211")

    def test_unwritable_dir_degrades_to_url_only(self):
        blocker = os.path.join(_tmp.name, "blocker")
        open(blocker, "w").close()
        orig = config.QR_DIR
        config.QR_DIR = os.path.join(blocker, "www")       # a file in the way
        try:
            ha_client.publish_ip_sensors()
            state, attrs = self._by_entity()[config.ENT_PI_ROUTER_IP]
            self.assertEqual(state, "192.168.0.47")
            self.assertEqual(attrs["url"], "http://192.168.0.47:8123")
            self.assertNotIn("qr_path", attrs)
        finally:
            config.QR_DIR = orig


if __name__ == "__main__":
    unittest.main()
